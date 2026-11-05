#!/usr/bin/env python3
"""The checks of prereg/M2.md §7 step 3, before freeze-m2: whether each passes, from the check runs'
own files. Run kind check; nothing here is a verdict.

  - equivalence (§4): tools/m2_equivalence's m2_equivalence.csv says passed 1, with every frame of
    videos ① to ④ (their frame counts, and ② to ④'s SHA-256 of AMENDMENTS A8) and 1,000,000 synthetic
    decodings, none differing;
  - default path (§7 step 2): a run of C with the defaults at 2 channels, main condition, whose
    env.json has the threads (name and affinity), the affinities and the queues of M1's run
    32_main_C_2ch_r1;
  - stagger (§3.1): in a run of each arm (C with post m1, Cp with post neon, both variant C, run kind
    check) at 8 channels, main condition, staggered, every sampled frame of channel k (k = channel - 1
    in frames.csv) has seq % 6 == k % 6. Its arrival phases and stagger error are reported; they set no
    pass or fail;
  - /proc/schedstat (§3.2): in each of those two runs, the raw window sum over all CPUs is at least the
    run's process CPU, and each of cpu0 to cpu7's is above 0;
  - the same objects (§4): every run's env.json names the post-processing objects that the equivalence
    check passed with.

The stagger error of a run is the largest circular distance, over k, between phi_k - phi_0 and
(k mod 6) x 33.3 ms on the 200 ms circle, with phi_k channel k's phase as tools/m1_phases.py defines it.
The CPU values of these runs are printed too (§7 step 3).

Usage, from the repository: python3 tools/m2_checks.py --equivalence DIR --default DIR --c DIR --cp DIR
Prints Markdown. Exit code 0 when every check passes, 1 when one does not.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m1_phases as ph  # noqa: E402
import m1_report as rep  # noqa: E402
import stream_session as ss  # noqa: E402

PERIOD_MS = 200.0  # main condition: 1 frame in 6 at 30 fps
STRIDE = 6
VIDEOS = {  # in the order m2_equivalence is given them: frames (docs/notes/preprocess.md), SHA-256 (A8)
    "pexels_36330861.mp4": (1186, None),
    "pexels_29130409.mp4": (752, "e560dd8481d5fe1c615f208fae192fc7fd583097d202e785dc026eae8fb75b41"),
    "pexels_29969146.mp4": (714, "592b6847e2e63e07393af6f4a04b691be438c23eba2c73f79b021dca877c2772"),
    "pixabay_180386.mp4": (948, "2538f54d36e0d763ec3e13f9e606bf01e2d142fff82181eb156f792576b0555b"),
}
SYNTHETIC_DECODES = 1_000_000
CPUS = 8
OBJECTS = ("build/CMakeFiles/core.dir/src/core/yolov8_post.cpp.o",  # kPostObjects of src/core/report.h
           "build/CMakeFiles/core.dir/src/core/yolov8_post_neon.cpp.o")
OBJECT_KEYS = ("sha256_yolov8_post_o", "sha256_yolov8_post_neon_o")  # their rows in m2_equivalence.csv
M1_DEFAULT_RUN = "results/comparison/20261103T073439Z_main/32_main_C_2ch_r1"


def stagger_error(phases_ms, period_ms=PERIOD_MS, stride=STRIDE):
    """The largest circular distance, over k, between phases_ms[k] - phases_ms[0] and
    (k mod stride) x period_ms / stride, in ms."""
    worst = 0.0
    for k, p in enumerate(phases_ms):
        d = abs((p - phases_ms[0] - (k % stride) * period_ms / stride) % period_ms)
        worst = max(worst, min(d, period_ms - d))
    return worst


def sampler_rule_broken(frames, stride=STRIDE):
    """The frames whose seq breaks seq % stride == (channel - 1) % stride."""
    return [f for f in frames if int(f["seq"]) % stride != (int(f["channel"]) - 1) % stride]


def schedstat_problems(summary):
    """Why a run's /proc/schedstat fails §7 step 3's check, from its summary {key: value}; [] if it passes."""
    problems = []
    if summary.get("schedstat_version", "-1") == "-1" or "schedstat_cpu_total_s" not in summary:
        return ["no /proc/schedstat values"]
    if float(summary["schedstat_cpu_total_s"]) < float(summary["process_cpu_s"]):
        problems.append(f"sum {summary['schedstat_cpu_total_s']} s below process CPU {summary['process_cpu_s']} s")
    for c in range(CPUS):
        v = summary.get(f"schedstat_cpu{c}_s")
        if v is None or float(v) <= 0:
            problems.append(f"schedstat_cpu{c}_s is {v if v is not None else 'missing'}")
    return problems


def equivalence_problems(rows):
    """Why an m2_equivalence.csv's rows {key: value} fail §4; [] if they pass."""
    problems = [] if rows.get("passed") == "1" else ["passed is not 1"]
    if rows.get("complete") != "1" or rows.get("differing_total") != "0":
        problems.append("not complete, or something differs")
    for i, (name, (frames, sha)) in enumerate(VIDEOS.items(), 1):
        k = f"video{i}"
        if not rows.get(f"{k}_path", "").endswith("/" + name):
            problems.append(f"{k} is not {name}")
        if rows.get(f"{k}_frames") != str(frames):
            problems.append(f"{k} has {rows.get(f'{k}_frames')} frames, not {frames}")
        if sha and rows.get(f"{k}_sha256") != sha:
            problems.append(f"{k}'s SHA-256 is not A8's")
        if rows.get(f"{k}_differing_frames") != "0":
            problems.append(f"{k}: {rows.get(f'{k}_differing_frames')} frames differ")
    if f"video{len(VIDEOS) + 1}_path" in rows:
        problems.append("more videos than ① to ④")
    if rows.get("synthetic_decodes") != str(SYNTHETIC_DECODES) or rows.get("synthetic_differing_sets") != "0":
        problems.append("synthetic decodings missing or differing")
    return problems


def thread_layout(env):
    """env.json's threads as sorted (name, affinity), its queues and its cores."""
    threads = sorted((t["comm"], t["affinity"]) for t in env["threads_at_window_start"])
    return threads, env["queues"], env.get("cores")


def objects_problems(env, equivalence_rows):
    """Why a run's env.json does not name the post-processing objects of the equivalence check; [] if it does."""
    objects = env.get("post", {}).get("objects_sha256", {})
    want = [equivalence_rows.get(k) for k in OBJECT_KEYS]
    return [] if [objects.get(path) for path in OBJECTS] == want and all(want) else ["not the checked objects"]


def arm_problems(header, post, channels, stagger):
    """Why a run's header is not variant C, run kind check, main condition, with this post, channel count
    and stagger; [] if it is."""
    want = {"variant": "C", "run_kind": "check", "condition": "main", "post": post, "channels": str(channels),
            "stagger": stagger}
    return [f"{k} is {header.get(k)}, not {v}" for k, v in want.items() if header.get(k) != v]


def default_problems(env, m1_env):
    """Why a default run's env.json differs from M1's run in threads, affinities or queues, or is not C
    with the defaults at 2 channels, main condition; [] if not."""
    mine, theirs = thread_layout(env), thread_layout(m1_env)
    problems = arm_problems({k[len("header_"):]: v for k, v in env.items() if k.startswith("header_")}, "m1", 2,
                            "off")
    if mine[0] != theirs[0]:
        problems.append("threads or affinities differ from M1's")
    if mine[1] != theirs[1]:
        problems.append("queues differ from M1's")
    return problems


def summary_of(run_dir):
    return {r["key"]: r["value"] for r in ss.read_csv(Path(run_dir) / "summary.csv")[1]}


def stagger_run(run_dir, post):
    """(problems, phases in ms by channel index, stagger error, spread, summary) of an arm's 8-channel run."""
    header, frames = ss.read_csv(Path(run_dir) / "frames.csv")
    problems = arm_problems(header, post, 8, "on")
    broken = sampler_rule_broken(frames)
    if broken:
        problems.append(f"{len(broken)} frames break the sampler's rule")
    window = rep.window_frames(Path(run_dir))
    t0 = {}
    for f in window:
        t0.setdefault(int(f["channel"]), []).append(int(f["t0_ns"]))
    if sorted(t0) != list(range(1, 9)):
        return problems + ["not every channel has frames in the window"], [], float("nan"), float("nan"), {}
    phases = [ph.phase(t0[c], PERIOD_MS * 1e6) / 1e6 for c in sorted(t0)]
    summary = summary_of(run_dir)
    problems += schedstat_problems(summary)
    return problems, phases, stagger_error(phases), ph.spread(phases, PERIOD_MS), summary


def report(equivalence_dir, default_dir, c_dir, cp_dir, root):
    lines, ok = ["# Checks before freeze-m2 (prereg/M2.md §7 step 3)", ""], True

    _, rows = ss.read_csv(Path(equivalence_dir) / "m2_equivalence.csv")
    rows = {r["key"]: r["value"] for r in rows}
    p = equivalence_problems(rows)
    ok &= not p
    lines += [f"## Equivalence (§4): {'passed' if not p else 'failed'}", ""]
    lines += [f"- {x}" for x in p] + [
        f"- {rows.get(f'video{i}_path', '?')}: {rows.get(f'video{i}_frames', '?')} frames, "
        f"{rows.get(f'video{i}_differing_frames', '?')} differing" for i in range(1, len(VIDEOS) + 1)]
    lines += [f"- synthetic: {rows.get('synthetic_decodes', '?')} decodings in {rows.get('synthetic_sets', '?')} sets, "
              f"{rows.get('synthetic_differing_sets', '?')} sets differing",
              f"- objects: yolov8_post.cpp.o {rows.get('sha256_yolov8_post_o', '?')}, "
              f"yolov8_post_neon.cpp.o {rows.get('sha256_yolov8_post_neon_o', '?')}", ""]

    env = json.loads((Path(default_dir) / "env.json").read_text(encoding="utf-8"))
    m1_env = json.loads((root / M1_DEFAULT_RUN / "env.json").read_text(encoding="utf-8"))
    p = default_problems(env, m1_env) + objects_problems(env, rows)
    ok &= not p
    lines += [f"## Default path (§7 step 2): {'passed' if not p else 'failed'}", ""]
    lines += [f"- {x}" for x in p] + [f"- compared with `{M1_DEFAULT_RUN}`: threads, affinities and queues", ""]

    lines += ["## Stagger and /proc/schedstat (§3.1, §3.2)", "",
              "| Arm | Result | Stagger error (ms) | Spread (ms) | Process CPU (s) | schedstat sum (s) | Phases (ms) |",
              "|---|---|---|---|---|---|---|"]
    notes = []
    for arm, post, d in (("C", "m1", c_dir), ("Cp", "neon", cp_dir)):
        p, phases, err, spr, s = stagger_run(d, post)
        p += objects_problems(json.loads((Path(d) / "env.json").read_text(encoding="utf-8")), rows)
        ok &= not p
        lines.append(f"| {arm} | {'passed' if not p else 'failed'} | {err:.1f} | {spr:.1f} | {s.get('process_cpu_s')} | "
                     f"{s.get('schedstat_cpu_total_s', '?')} | {', '.join(f'{x:.1f}' for x in phases)} |")
        notes += [f"- {arm}: {x}" for x in p]
    lines += [""] + notes + (["", ""] if notes else [""])
    lines.append(f"**All checks {'passed' if ok else 'did not pass'}.**")
    return "\n".join(lines) + "\n", ok


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ("equivalence", "default", "c", "cp"):
        ap.add_argument(f"--{name}", required=True)
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    text, ok = report(a.equivalence, a.default, a.c, a.cp, Path(__file__).resolve().parent.parent)
    print(text, end="")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
