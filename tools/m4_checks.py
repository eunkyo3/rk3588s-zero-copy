#!/usr/bin/env python3
"""The checks of prereg/M4.md §8 step 3, before freeze-m4: whether each passes, from the check runs' own
records (run kind check). Its output goes into the amendment that freeze-m4 tags (§8 step 4).

  - Data (tools/m4_prep.py through tools/eval/pc_run.py): m4_inputs.csv says passed 1, with the SHA-256
    of val2017.zip and annotations_trainval2017.zip of tools/eval/coco.sha256, 5,000 images decoded at
    their annotated sizes, none missing or unreadable, and the 20 quantization images among them by name
    and SHA-256. Its manifest.tsv has the SHA-256 it reports.
  - Decoding: the board tool's decode.csv says valid 1 for all 5,000 images of that manifest (the same
    SHA-256 of the file and of its rows), none other than the manifest's.
  - Rules (§2.3): for each model (one record each), tools/m4_rules_check.py's rules_check.csv says
    valid 1, with 50 lists equal, or for YOLOv8n excepted (YOLOv10n has no NMS), and no problem. Its
    board record is a pass of §1's model on that manifest, valid for the first 50 images. Its dump.sha256
    is the one pass.csv names and lists every output of those 50 images, for §2.5's determinism check.
  - Wiring: for each setting (one record each), wiring.csv with M1-3's tensors (A7) and §1's model:
    well-formed rows of frames 0 to N-1, N at least 10, all equal.
  - Default path: a run of C with the defaults (M1's model and post-processing) at 2 channels, main
    condition, whose env.json has the threads, affinities and queues of M1's run 32_main_C_2ch_r1
    (tools/m2_checks.py).
  - Arms: tools/stream_session.py m4-check's session completed, with one valid run of each arm at 8
    channels, main condition, staggered, variant C, run kind check. Each run's frames.csv header and
    env.json name the arm's model SHA-256 (§1) and post-processing with its thresholds, and every frame
    follows M2's sampler rule, seq % 6 == (channel - 1) % 6. Their CPU values and stagger error are
    reported.
  - Build: every board and PC record names one build, a whole commit, and the PC's code was clean. The
    data record may be older: its tools/m4_prep.py and evaluation image must be the build's (git). What
    changed in src/, CMakeLists.txt, tools/ and tests/ between the build and HEAD is reported.
  - Objects: every record names the four post-processing objects (kPostObjects), the same in all, and
    the rules' and wiring's records the same object of the YOLOv8n rule. Those of yolov8_post.cpp and Cp's
    file must be A13's; the others are reported, for freeze-m4's amendment.
  - Every record is of run kind check and names prereg-m4's commit (§8 step 3). Every board record's tool
    exited 0. tools/check_prereg_order.py checks the starts.

Usage, from the repository:
  python3 tools/m4_checks.py --inputs DIR --decode DIR --rules-check DIR DIR --wiring DIR DIR \
      --default DIR --arms DIR
Prints Markdown. Exit code 0 when every check passes, 1 when one does not, 2 when a record cannot be read.
"""
import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m2_checks as mc  # noqa: E402  (M2's default path and sampler rule)
import m4_order as mo  # noqa: E402
import stream_session as ss  # noqa: E402

IMAGES, RULES_IMAGES, QUANTIZATION = 5000, 50, 20  # §2.1, §2.3, §2.1
A13_OBJECTS = {  # prereg/AMENDMENTS.md A13: the objects M2's equivalence check passed with
    "build/CMakeFiles/core.dir/src/core/yolov8_post.cpp.o":
        "536b9351e78e815c09ba6e8c4f643e06e2e06454028163362ed5eaeaa4adbfa2",
    "build/CMakeFiles/core.dir/src/core/yolov8_post_neon.cpp.o":
        "3956c1ba1f541bfe6ebd44b67cef4b67403eb1c1deb31cd842354d034bae7555",
}
MODEL_SHA = {name: sha for _, sha, name in ss.M4_MODELS.values()}
ARM = {name: arm for arm, (_, _, name) in ss.M4_MODELS.items()}  # yolov8n -> C8
ARM_POST = {"C8": {"name": "neon", "conf_thresh": 0.25, "nms_thresh": 0.45},  # Cp's (M2)
            "C10": {"name": "v10", "conf_thresh": 0.25, "max_det": 300}}  # §8 step 2
DEFAULT_POST = {"name": "m1", "conf_thresh": 0.25, "nms_thresh": 0.45}  # M1's
WIRING_COLUMNS = "frame,shared_detections,direct_detections,equal"
BOARD_EXITED_0 = "the board tool exited 0"  # board_pass.txt's outcome
COMMIT, SHA256 = re.compile(r"[0-9a-f]{40}"), re.compile(r"[0-9a-f]{64}")
CODE = ("src", "CMakeLists.txt", "tools", "tests")  # the code a kind-m4 run must have from freeze-m4


class Unreadable(Exception):
    """A record the checks need cannot be read (exit 2)."""


def need(value, what):
    if value is None:
        raise Unreadable(f"cannot read {what}")
    return value


def kv(path):
    """(header, {key: value}) of a key,value CSV."""
    return need(mo.key_values(path), path)


def json_dict(text, what):
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        raise Unreadable(f"{what} is not JSON") from None
    if not isinstance(value, dict):
        raise Unreadable(f"{what} is not a JSON object")
    return value


def read_json(path):
    return json_dict(need(mo.read(path), path), path)


def file_sha256(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def git(root, *args):
    """git's output, or None when git says no (an unknown commit or path)."""
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    return r.stdout.strip() if r.returncode == 0 else None


def coco_sha256(root):
    """{file: SHA-256} of tools/eval/coco.sha256."""
    out = {}
    for line in (root / "tools/eval/coco.sha256").read_text(encoding="utf-8").splitlines():
        sha, _, name = line.partition("  ")
        if name:
            out[name.strip()] = sha
    return out


def first_images(manifest, n):
    """The first n image ids of a manifest.tsv."""
    lines = [line for line in need(mo.read(manifest), manifest).splitlines() if not line.startswith("# ")]
    return [int(r["image_id"]) for r in list(csv.DictReader(lines, delimiter="\t"))[:n]]


def prereg_problem(header, what):
    got = header.get("prereg_m4_commit")
    return [] if got == ss.PREREG_M4_COMMIT else [f"{what} names prereg-m4's commit as {got}"]


def kind_problem(header, what):
    got = header.get("run_kind")
    return [] if got == "check" else [f"{what} is of run kind {got}, not check"]


def data_problems(rows, coco, manifest_sha):
    want = {"passed": "1", "problems": "none", "annotation_images": str(IMAGES), "zip_images": str(IMAGES),
            "manifest_images": str(IMAGES), "missing_jpegs": "0", "jpegs_without_annotation": "0",
            "size_mismatches": "0", "png_mismatches": "0", "unreadable": "0",
            "quantization_images": str(QUANTIZATION), "quantization_in_val2017": str(QUANTIZATION),
            "quantization_sha256_equal": str(QUANTIZATION),
            "sha256_val2017.zip": coco.get("val2017.zip"),
            "sha256_annotations_trainval2017.zip": coco.get("annotations_trainval2017.zip")}
    p = [f"{k} is {rows.get(k)}, not {v}" for k, v in want.items() if rows.get(k) != v]
    p += [f"{k} is not a SHA-256" for k in ("manifest_sha256", "manifest_rows_sha256")
          if not SHA256.fullmatch(rows.get(k) or "")]
    if manifest_sha != rows.get("manifest_sha256"):
        p.append("manifest.tsv is not the manifest m4_inputs.csv names")
    return p


def manifest_problems(header, inputs, what):
    return [f"{what} is not of the inputs' manifest ({k})" for k in ("manifest_sha256", "manifest_rows_sha256")
            if header.get(k) != inputs.get(k)]


def decode_problems(header, rows, inputs):
    want = {"valid": "1", "images": str(IMAGES), "images_planned": str(IMAGES), "images_not_the_manifests": "0"}
    p = [f"{k} is {rows.get(k)}, not {v}" for k, v in want.items() if rows.get(k) != v]
    return p + manifest_problems(header, inputs, "decode.csv")


def board_tool_problems(d):
    """Why a board record's tool did not end with exit 0 (exit_code, board_pass.txt); [] if it did."""
    p = []
    code = (mo.read(d / "exit_code") or "").strip()
    if code != "0":
        p.append(f"{d.name}: the board tool's exit code is {code or 'missing'}")
    outcome = mo.keyed(d / "board_pass.txt").get("outcome")
    if outcome != BOARD_EXITED_0:
        p.append(f"{d.name}/board_pass.txt: {outcome or 'no outcome'}")
    return p


def dump_problems(board_dir, rows, images, outputs):
    """Why a rules pass's dump.sha256 is not the one pass.csv names, listing outputs 0..outputs-1 of each
    of the images; [] if it is."""
    text = mo.read(board_dir / "dump.sha256")
    if text is None:
        return [f"{board_dir.name} has no dump.sha256"]
    p = []
    if file_sha256(board_dir / "dump.sha256") != rows.get("sha256_dump_sha256"):
        p.append(f"{board_dir.name}/dump.sha256 is not the one pass.csv names")
    listed = [line.split("  ", 1) for line in text.splitlines() if line]
    names = [x[1] for x in listed if len(x) == 2 and SHA256.fullmatch(x[0])]
    want = {f"{i:012d}_out{k}.i8" for i in images for k in range(outputs)}
    if not want or len(names) != len(listed) or len(names) != len(want) or set(names) != want:
        p.append(f"{board_dir.name}/dump.sha256 does not list the {outputs} outputs of the manifest's first "
                 f"{RULES_IMAGES} images")
    return p


def rules_problems(check_header, check_rows, board_dir, inputs, images, image_id):
    """(problems, the board record's header) of a model's rules check."""
    model = check_rows.get("model")
    want = {"valid": "1", "images": str(RULES_IMAGES), "lists_differing": "0", "problems": "none"}
    p = [f"rules_check.csv: {k} is {check_rows.get(k)}, not {v}" for k, v in want.items() if check_rows.get(k) != v]
    try:
        equal, excepted = int(check_rows.get("lists_equal")), int(check_rows.get("lists_excepted_near_0.7"))
    except (TypeError, ValueError):
        equal, excepted = -1, -1
    if equal + excepted != RULES_IMAGES or (excepted and model != "yolov8n"):
        p.append(f"rules_check.csv: {equal} lists equal and {excepted} excepted"
                 + ("" if model == "yolov8n" else " (no NMS: none can be excepted)"))
    p += kind_problem(check_header, "rules_check.csv")
    if check_header.get("code_status") != "clean":
        p.append(f"rules_check.csv: the PC's code was {check_header.get('code_status')}, not clean")
    if check_header.get("eval_image_id") != image_id:
        p.append("rules_check.csv: not the evaluation image of tools/eval/image_id.txt")
    header, rows = kv(board_dir / "pass.csv")
    want = {"valid": "1", "images": str(RULES_IMAGES), "dumped_images": str(RULES_IMAGES)}
    p += [f"{board_dir.name}/pass.csv: {k} is {rows.get(k)}, not {v}" for k, v in want.items() if rows.get(k) != v]
    if (header.get("model"), header.get("model_sha256")) != (model, MODEL_SHA.get(model)):
        p.append(f"{board_dir.name} is not a pass of §1's {model}")
    p += kind_problem(header, f"{board_dir.name}/pass.csv") + board_tool_problems(board_dir)
    p += manifest_problems(header, inputs, f"{board_dir.name}/pass.csv")
    outputs = mo.header_and_lines(board_dir / "outputs.csv")
    outputs = len(outputs[1]) - 1 if outputs and outputs[1] else 0
    p += dump_problems(board_dir, rows, images, outputs)
    return p, header


def wiring_table(path):
    """(header, [equal column], problems) of a wiring.csv: every row must be whole, frames 0 to N-1."""
    header, lines = need(mo.header_and_lines(path), path)
    p = [] if lines and lines[0] == WIRING_COLUMNS else ["wiring.csv's columns are not " + WIRING_COLUMNS]
    frames, equal = [], []
    for row in csv.reader(lines[1:]):
        if len(row) != 4 or not all(x.isdigit() for x in row) or row[3] not in ("0", "1"):
            p.append(f"a malformed row: {','.join(row)}")
            continue
        frames.append(int(row[0]))
        equal.append(row[3])
    if frames != list(range(len(frames))):
        p.append("its frames are not 0 to N-1 in order")
    return header, equal, p


def wiring_problems(header, equal):
    model = header.get("model")
    p = []
    if model not in MODEL_SHA or header.get("model_sha256") != MODEL_SHA[model]:
        p.append(f"not §1's model ({model}, {header.get('model_sha256')})")
    elif not (header.get("setting") or "").startswith(f"{ARM[model]}: "):
        p.append(f"its setting is not {ARM[model]}'s: {header.get('setting')}")
    if header.get("tensors_sha256") != mo.TENSORS_SHA256:
        p.append("not M1-3's tensor set (A7)")
    if len(equal) < mo.WIRING_FRAMES or "0" in equal:
        p.append(f"{equal.count('1')} of {len(equal)} frames equal")
    return p


def post_problems(post, want, what):
    return [f"{what}: post {k} is {post.get(k)}, not {v}" for k, v in want.items() if post.get(k) != v]


def arm_run(run_dir, arm):
    """(problems, phases in ms by channel, stagger error, spread, summary, env.json, frames.csv header) of
    an arm's run."""
    header, frames = ss.read_csv(run_dir / "frames.csv")
    env = read_json(run_dir / "env.json")
    _, sha, model = ss.M4_MODELS[arm]
    post = ARM_POST[arm]["name"]
    p = mc.arm_problems(header, post, 8, "on")
    if header.get("model_sha256") != sha:
        p.append(f"model_sha256 is {header.get('model_sha256')}, not §1's {model}")
    if (env.get("header_model_sha256"), env.get("header_post")) != (sha, post):
        p.append("env.json does not record the arm's model and post-processing")
    p += post_problems(env.get("post") or {}, ARM_POST[arm], "env.json")
    p += prereg_problem(header, "frames.csv")
    broken = mc.sampler_rule_broken(frames)
    if broken:
        p.append(f"{len(broken)} of {len(frames)} frames break the sampler's rule")
    if not frames:
        p.append("no frames")
    window = mc.rep.window_frames(run_dir)
    t0 = {}
    for f in window:
        t0.setdefault(int(f["channel"]), []).append(int(f["t0_ns"]))
    if sorted(t0) != list(range(1, 9)):
        return p + ["not every channel has frames in the window"], [], float("nan"), float("nan"), {}, env, header
    phases = [mc.ph.phase(t0[c], mc.PERIOD_MS * 1e6) / 1e6 for c in sorted(t0)]
    return (p, phases, mc.stagger_error(phases), mc.ph.spread(phases, mc.PERIOD_MS), mc.summary_of(run_dir), env,
            header)


def runs_tsv(session):
    """{run name: its row} of a session's runs.tsv."""
    lines = need(mo.read(session / "runs.tsv"), session / "runs.tsv").splitlines()
    return {r["name"]: r for r in csv.DictReader(lines, delimiter="\t")}


def build_section(builds, inputs_header, image_id, root):
    """(problems, details) of the build that every board and PC record names."""
    named = sorted(set(builds.values()), key=str)
    p = []
    if len(named) != 1:
        return [f"{len(named)} builds are named: " + "; ".join(
            f"{c} by {', '.join(k for k, v in builds.items() if v == c)}" for c in named)], []
    build = named[0]
    if not COMMIT.fullmatch(build or ""):
        return [f"the build {build} is not a whole commit (a -dirty build?)"], []
    if inputs_header.get("code_status") != "clean":
        p.append(f"m4_inputs.csv: the PC's code was {inputs_header.get('code_status')}, not clean")
    if inputs_header.get("eval_image_id") != image_id:
        p.append("m4_inputs.csv: not the evaluation image of tools/eval/image_id.txt")
    inputs_commit = inputs_header.get("code_commit")
    prep = {git(root, "rev-parse", f"{c}:tools/m4_prep.py") for c in (inputs_commit, build)}
    if None in prep or len(prep) != 1:
        p.append(f"tools/m4_prep.py at the inputs' commit {inputs_commit} is not the build's")
    if git(root, "show", f"{build}:tools/eval/image_id.txt") != image_id:
        p.append("tools/eval/image_id.txt is not the build's")
    changed = git(root, "diff", "--name-only", build, "HEAD", "--", *CODE)
    return p, [f"every board and PC record names build `{build}` ({len(builds)} places)",
               f"the data record's code `{(inputs_commit or '?')[:7]}` has the build's tools/m4_prep.py and "
               f"evaluation image `{image_id}`",
               f"changed in {', '.join(CODE)} between the build and HEAD: "
               + (", ".join(f"`{x}`" for x in changed.splitlines()) if changed else "none")]


def objects_section(objects, rule_objects):
    """(problems, details) of the post-processing objects the records name."""
    groups = {}
    for name, o in objects.items():
        groups.setdefault(json.dumps(o, sort_keys=True), []).append(name)
    common_key = max(groups, key=lambda g: len(groups[g]))
    common = json.loads(common_key)
    p = []
    for name, o in objects.items():
        if o != common:
            differ = sorted(k for k in set(o) | set(common) if o.get(k) != common.get(k))
            p.append(f"{name} names other post-processing objects than the other records: {', '.join(differ)}")
        p += [f"{name}: {path} is {o.get(path)}, not A13's {sha}" for path, sha in A13_OBJECTS.items()
              if o.get(path) != sha]
        if set(o) != set(ss.M4_POST_OBJECTS.values()):
            p.append(f"{name} does not name the four objects of kPostObjects")
    if len(set(rule_objects.values())) != 1 or None in rule_objects.values():
        p.append("the records name different objects of the YOLOv8n rule, or none: "
                 + ", ".join(f"{k} {v}" for k, v in rule_objects.items()))
    return p, ([f"`{path}`: `{sha}`" + (" (A13's)" if A13_OBJECTS.get(path) == sha else "")
                for path, sha in common.items()]
               + [f"`build/CMakeFiles/m4map.dir/src/m4/map_rules.cpp.o` (the YOLOv8n rule): "
                  f"`{next(iter(rule_objects.values()), None)}`",
                  f"the same in {len(groups[common_key])} of {len(objects)} records that name them"])


def report(a, root):
    lines, ok = ["# Checks before freeze-m4 (prereg/M4.md §8 step 3)", ""], True
    objects, rule_objects = {}, {}  # record -> its post-processing objects; record -> the YOLOv8n rule's
    builds = {}  # the file that names a build -> that build
    image_id = (root / "tools/eval/image_id.txt").read_text(encoding="utf-8").strip()

    def section(title, problems, details, extra=()):
        nonlocal ok
        ok &= not problems
        lines.extend([f"## {title}: {'passed' if not problems else 'failed'}", ""]
                     + [f"- **{x}**" for x in problems] + [f"- {x}" for x in details] + [""]
                     + (list(extra) + [""] if extra else []))

    def board_build(d):
        return mo.keyed(d / "board_pass.txt").get("board_build")

    inputs_dir = Path(a.inputs)
    inputs_header, inputs = kv(inputs_dir / "m4_inputs.csv")
    p = (data_problems(inputs, coco_sha256(root), file_sha256(inputs_dir / "manifest.tsv"))
         + kind_problem(inputs_header, "m4_inputs.csv") + prereg_problem(inputs_header, "m4_inputs.csv"))
    images = first_images(inputs_dir / "manifest.tsv", RULES_IMAGES)
    section("Data (§2.1, §2.2)", p, [
        f"record `{inputs_dir.name}`, code `{inputs_header.get('code_commit', '?')[:7]}`",
        f"COCO: val2017.zip `{inputs.get('sha256_val2017.zip')}`, annotations_trainval2017.zip "
        f"`{inputs.get('sha256_annotations_trainval2017.zip')}`",
        f"{inputs.get('manifest_images')} images ({inputs.get('resized')} resized to fit), "
        f"{inputs.get('quantization_sha256_equal')} of {QUANTIZATION} quantization images by name and SHA-256",
        f"manifest: SHA-256 `{inputs.get('manifest_sha256')}`, rows `{inputs.get('manifest_rows_sha256')}`"])

    decode_dir = Path(a.decode)
    h, rows = kv(decode_dir / "decode.csv")
    builds[f"{decode_dir.name}/decode.csv"] = h.get("code_commit")
    builds[f"{decode_dir.name}/board_pass.txt"] = board_build(decode_dir)
    p = (decode_problems(h, rows, inputs) + kind_problem(h, "decode.csv") + prereg_problem(h, "decode.csv")
         + board_tool_problems(decode_dir))
    section("The board tool's decoding (§8 step 3)", p, [
        f"record `{decode_dir.name}`, build `{h.get('code_commit', '?')[:7]}`: {rows.get('images')} images, "
        f"{rows.get('images_not_the_manifests')} other than the manifest's, {rows.get('seconds')} s"])

    seen = set()  # one record per model: the same model twice would leave the other unchecked
    for d in map(Path, a.rules_check):
        ch, check = kv(d / "rules_check.csv")
        board = d.parent / check.get("board_record", "none")
        p, bh = rules_problems(ch, check, board, inputs, images, image_id)
        if check.get("model") in seen:
            p.append(f"a second record of {check.get('model')}")
        seen.add(check.get("model"))
        pc = read_json(d / "pc.json")
        if pc.get("prereg_m4_commit") != ss.PREREG_M4_COMMIT:
            p.append("pc.json does not name prereg-m4's commit")
        p += kind_problem(pc, "pc.json") + prereg_problem(bh, f"{board.name}/pass.csv")
        builds.update({f"{d.name}/rules_check.csv": ch.get("code_commit"),
                       f"{d.name}/rules_check.csv (board_code_commit)": ch.get("board_code_commit"),
                       f"{board.name}/pass.csv": bh.get("code_commit"),
                       f"{board.name}/board_pass.txt": board_build(board)})
        objects[board.name] = json_dict(bh.get("post_objects_sha256"), f"{board.name}'s post_objects_sha256")
        rule_objects[board.name] = bh.get("map_rule_object_sha256")
        section(f"Rules, {check.get('model')} (§2.3)", p, [
            f"records `{board.name}` (build `{bh.get('code_commit', '?')[:7]}`) and `{d.name}`",
            f"{check.get('lists_equal')} of {RULES_IMAGES} lists equal, {check.get('lists_excepted_near_0.7')} "
            f"excepted; {check.get('values_equal_bit_for_bit')} of {check.get('values')} values bit for bit; "
            f"largest differences: score {check.get('largest_score_difference')}, box "
            f"{check.get('largest_box_difference_px')} px",
            f"IoUs within 1e-6 of 0.7: {check.get('ious_within_1e-6_of_0.7')}",
            f"the dump for §2.5: `{board.name}/dump`, dump.sha256 `{file_sha256(board / 'dump.sha256')}`"])

    seen = set()
    for d in map(Path, a.wiring):
        h, equal, p = wiring_table(d / "wiring.csv")
        p += wiring_problems(h, equal) + kind_problem(h, "wiring.csv") + prereg_problem(h, "wiring.csv")
        p += board_tool_problems(d)
        if h.get("model") in seen:
            p.append(f"a second record of {h.get('model')}")
        seen.add(h.get("model"))
        builds[f"{d.name}/wiring.csv"] = h.get("code_commit")
        builds[f"{d.name}/board_pass.txt"] = board_build(d)
        objects[d.name] = json_dict(h.get("post_objects_sha256"), f"{d.name}'s post_objects_sha256")
        rule_objects[d.name] = h.get("map_rule_object_sha256")
        model = h.get("model")
        section(f"Wiring, {ARM.get(model, '?')} ({model})", p, [
            f"record `{d.name}`, build `{h.get('code_commit', '?')[:7]}`: {equal.count('1')} of {len(equal)} "
            f"frames equal; {h.get('setting')}"])

    default_dir = Path(a.default)
    env = read_json(default_dir / "env.json")
    m1_env = read_json(root / mc.M1_DEFAULT_RUN / "env.json")
    p = mc.default_problems(env, m1_env)
    if env.get("header_model_sha256") != m1_env.get("header_model_sha256"):
        p.append(f"its model is {env.get('header_model_sha256')}, not M1's")
    p += post_problems(env.get("post") or {}, DEFAULT_POST, "env.json")
    if env.get("header_prereg_m4_commit") != ss.PREREG_M4_COMMIT:
        p.append("env.json does not name prereg-m4's commit")
    builds[f"{default_dir.name}/env.json"] = env.get("header_code_commit")
    objects[default_dir.name] = (env.get("post") or {}).get("objects_sha256") or {}
    section("Default path (§8 step 3)", p, [
        f"record `{default_dir.name}`, build `{env.get('header_code_commit', '?')[:7]}`: compared with "
        f"`{mc.M1_DEFAULT_RUN}` in threads, affinities and queues"])

    arms_dir = Path(a.arms)
    rh, result = ss.read_csv(arms_dir / "result.csv")
    result = {r["key"]: r["value"] for r in result}
    p = [] if result.get("session_status") == "completed" else [f"session_status is {result.get('session_status')}"]
    if not (rh.get("session") == arms_dir.name and arms_dir.name.endswith("_m4_check")):
        p.append(f"result.csv names the session {rh.get('session')}: not this m4-check session")
    p += kind_problem(rh, "result.csv") + prereg_problem(rh, "result.csv")
    builds[f"{arms_dir.name}/result.csv"] = rh.get("code_commit")
    runs = runs_tsv(arms_dir)
    table = ["| Arm | Run | Result | Process CPU (s) | Stagger error (ms) | Spread (ms) | Phases (ms) |",
             "|---|---|---|---|---|---|---|"]
    for arm in ss.M4_MODELS:
        run = result.get(f"{arm}_run", "none")
        if not (arms_dir / run / "frames.csv").exists():
            p.append(f"{arm}: no valid run ({run})")
            continue
        q, phases, err, spr, s, e, fh = arm_run(arms_dir / run, arm)
        r = runs.get(run, {})
        if (r.get("exit"), r.get("valid")) != ("0", "1"):
            q.append(f"runs.tsv: exit {r.get('exit')}, valid {r.get('valid')}")
        builds[f"{arms_dir.name}/{run}/frames.csv"] = fh.get("code_commit")
        builds[f"{arms_dir.name}/{run}/env.json"] = e.get("header_code_commit")
        objects[f"{arms_dir.name}/{run}"] = (e.get("post") or {}).get("objects_sha256") or {}
        p += [f"{arm}: {x}" for x in q]
        table.append(f"| {arm} | `{run}` | {'passed' if not q else 'failed'} | {s.get('process_cpu_s')} | {err:.1f} | "
                     f"{spr:.1f} | {', '.join(f'{x:.1f}' for x in phases)} |")
    section("Arms (§8 step 3)", p, [f"session `{arms_dir.name}`, labels C8 {result.get('saturation_C8')}, "
                                    f"C10 {result.get('saturation_C10')} (no calib_npu session of kind m4 yet)"], table)

    section("Build (§8 step 3)", *build_section(builds, inputs_header, image_id, root))
    section("Objects (§8 step 3)", *objects_section(objects, rule_objects))
    lines.append(f"**All checks {'passed' if ok else 'did not pass'}.**")
    return "\n".join(lines) + "\n", ok


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ("inputs", "decode", "default", "arms"):
        ap.add_argument(f"--{name}", required=True)
    ap.add_argument("--rules-check", nargs=2, required=True, metavar="DIR", help="one per model")
    ap.add_argument("--wiring", nargs=2, required=True, metavar="DIR", help="one per setting")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        text, ok = report(a, Path(__file__).resolve().parent.parent)
    except (OSError, ValueError, KeyError, Unreadable) as e:
        print(f"m4_checks: cannot read the records: {e}", file=sys.stderr)
        return 2
    print(text, end="")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
