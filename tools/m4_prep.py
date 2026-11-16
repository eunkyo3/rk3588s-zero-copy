#!/usr/bin/env python3
"""M4's inputs (prereg/M4.md §2.1, §2.2) and the PC's data checks of §8 step 3.

Runs in the evaluation image (tools/eval/Dockerfile) through tools/eval/pc_run.py, which records the PC's
clock, CPU and image in <record>/pc.json first:

  python tools/eval/pc_run.py --record results/check/<UTC>_m4_inputs -- tools/m4_prep.py \
      --coco /data/coco --out /data/inputs

Steps:
  1. val2017.zip and annotations_trainval2017.zip in --coco are checked against tools/eval/coco.sha256.
  2. instances_val2017.json is read from the annotations zip. Each JPEG of val2017.zip must be one of its
     images, and each of its images must have one JPEG; they are written to <coco>/val2017/.
  3. Each image, by image id ascending (§2.2):
       - decoded with cv2.imread(path, IMREAD_COLOR | IMREAD_IGNORE_ORIENTATION), BGR; its size must equal
         the annotations' width and height;
       - scaled by r = 640 / max(w, h) to w' = floor(w·r + 0.5), h' = floor(h·r + 0.5) in float64, with
         INTER_LINEAR, unless (w', h') = (w, h);
       - placed on a 640×640 canvas filled with 114 at left = (640 − w') // 2, top = (640 − h') // 2;
       - written to <out>/<image id, 12 digits>.png, then read back: its pixels must be the canvas's.
  4. The 20 quantization images (models/coco_subset_20.sha256) must be among the 5,000, each JPEG with
     that SHA-256.
Writes to the record directory:
  - manifest.tsv: per image, by id: image_id, file_name, w, h, r (Python's repr), w_scaled, h_scaled, left,
    top, rgb_sha256 (the SHA-256 of the 640×640×3 RGB bytes in HWC order, as bound), png_sha256;
  - m4_inputs.csv: the checks and counts, the SHA-256 of manifest.tsv, and passed 1 when every check holds.
Both have a header of "# key: value" lines: pc.json's start time, CPU, clocks and image, M4's commits.
Exit code 0 when every check passes, 1 when one does not, 2 for unusable input (nothing then goes to --out).
"""
import argparse
import csv
import hashlib
import io
import json
import math
import re
import sys
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))  # python3 -I leaves the script's directory out
import stream_session as ss  # noqa: E402

SIZE, FILL = 640, 114  # §2.2: the model's input, and the letterbox's fill (PROTOCOL §5.2, Ultralytics)
IMAGES = 5000  # §2.1: all of val2017
QUANTIZATION_IMAGES = 20  # both models' calibration set, among the 5,000 (M4 Disclosure)
VAL_ZIP, ANN_ZIP = "val2017.zip", "annotations_trainval2017.zip"
ANNOTATIONS = "annotations/instances_val2017.json"
MEMBER = re.compile(r"val2017/(\d{12}\.jpg)")  # every image member of val2017.zip
MANIFEST_COLUMNS = ["image_id", "file_name", "w", "h", "r", "w_scaled", "h_scaled", "left", "top", "rgb_sha256",
                    "png_sha256"]


class InputError(Exception):
    """The inputs cannot be used."""


def geometry(w, h):
    """§2.2's scaling and letterbox of a w×h image: (r, w', h', left, top), in float64."""
    r = SIZE / max(w, h)
    ws, hs = math.floor(w * r + 0.5), math.floor(h * r + 0.5)
    return r, ws, hs, (SIZE - ws) // 2, (SIZE - hs) // 2


def read_sha256_file(text):
    """{name: SHA-256} of sha256sum's output ("<hex>  <name>", or with '*' for binary mode)."""
    out = {}
    for line in text.splitlines():
        m = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line.strip())
        if not m:
            raise InputError(f"not a sha256sum line: {line!r}")
        out[m.group(2)] = m.group(1)
    return out


def quantization_images(text):
    """{JPEG name: SHA-256} of the 20 quantization images in models/coco_subset_20.sha256 (its subset/ lines)."""
    return {Path(name).name: sha for name, sha in read_sha256_file(text).items() if name.startswith("subset/")}


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def annotation_images(data):
    """{file name: (image id, width, height)} of instances_val2017.json's images."""
    out = {}
    for image in data["images"]:
        name = image["file_name"]
        if name in out or not re.fullmatch(r"\d{12}\.jpg", name):
            raise InputError(f"instances_val2017.json: a repeated or unexpected file name {name!r}")
        out[name] = (int(image["id"]), int(image["width"]), int(image["height"]))
    return out


def jpeg_members(zf):
    """{JPEG name: ZipInfo} of val2017.zip. Every member must be the directory val2017/ or one of its
    JPEGs by name: nothing is written outside <coco>/val2017/."""
    out = {}
    for info in zf.infolist():
        if info.filename == "val2017/" and info.is_dir():
            continue
        m = MEMBER.fullmatch(info.filename)
        if not m or info.is_dir() or m.group(1) in out:
            raise InputError(f"{VAL_ZIP}: an unexpected member {info.filename!r}")
        out[m.group(1)] = info
    return out


def letterbox(bgr, w, h):
    """§2.2 steps 2 and 3 on a decoded BGR image of w×h: the 640×640 BGR canvas, and whether it was resized."""
    import cv2
    import numpy as np

    _, ws, hs, left, top = geometry(w, h)
    resized = (ws, hs) != (w, h)
    if resized:
        bgr = cv2.resize(bgr, (ws, hs), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((SIZE, SIZE, 3), FILL, dtype=np.uint8)
    canvas[top:top + hs, left:left + ws] = bgr
    return canvas, resized


def rgb_sha256(bgr_canvas):
    """The SHA-256 of a canvas's RGB bytes in HWC order, as the board binds them."""
    import numpy as np

    return hashlib.sha256(np.ascontiguousarray(bgr_canvas[:, :, ::-1]).tobytes()).hexdigest()


def header(pc, kind="check"):
    """The record's header: pc.json's start, PC and clocks (M4 §4), M4's commits, the code."""
    amendments = re.findall(r"^## (A\d+) ", (REPO / "prereg/AMENDMENTS.md").read_text(encoding="utf-8"), re.M)
    host = pc["host"]
    return {"start_time_utc": pc["start_time_utc"], "prereg_v1_commit": ss.PREREG_V1_COMMIT,
            "prereg_m4_commit": ss.PREREG_M4_COMMIT, "freeze_m4_commit": ss.freeze_m4_commit(),
            "run_kind": kind, "code_commit": pc["code_commit"], "code_status": pc["code_status"],
            "amendment": amendments[-1] if amendments else "none", "eval_image": pc["eval_image"],
            "eval_image_id": pc["eval_image_id"], "cpu_model": host["cpu_model"],
            "ntp_offset_s": host["ntp_offset_s"], "ntp_samples": host["ntp_samples"],
            "w32tm_status": host["w32tm_status"], "container_clock_offset_s": pc["container_clock_offset_s"]}


def header_text(h):
    return "".join(f"# {k}: {v}\n" for k, v in h.items())


def prepare(coco, out, record, pc, coco_sha256=REPO / "tools/eval/coco.sha256",
            quantization=REPO / "models/coco_subset_20.sha256", images_expected=IMAGES):
    """Steps 1 to 4. Returns (rows of m4_inputs.csv, its header)."""
    import cv2
    import numpy as np

    rows, problems = [], []
    expected = read_sha256_file(Path(coco_sha256).read_text(encoding="utf-8"))
    for name in (VAL_ZIP, ANN_ZIP):
        if not (coco / name).exists():
            raise InputError(f"no {coco / name} (tools/eval/fetch_coco.sh)")
        sha = file_sha256(coco / name)
        rows.append((f"sha256_{name}", sha))
        if expected.get(name) != sha:
            problems.append(f"{name} is not the file tools/eval/coco.sha256 fixes")
    with zipfile.ZipFile(coco / ANN_ZIP) as zf:
        annotations = json.loads(zf.read(ANNOTATIONS))
    images = annotation_images(annotations)
    with zipfile.ZipFile(coco / VAL_ZIP) as zf:
        members = jpeg_members(zf)
        missing, extra = sorted(set(images) - set(members)), sorted(set(members) - set(images))
        rows += [("annotation_images", len(images)), ("zip_images", len(members)), ("missing_jpegs", len(missing)),
                 ("jpegs_without_annotation", len(extra))]
        if len(images) != images_expected or missing or extra:
            problems.append(f"{len(images)} annotated images, {len(missing)} without a JPEG, {len(extra)} JPEGs "
                            "without an annotation")
        jpegs = coco / "val2017"
        jpegs.mkdir(exist_ok=True)
        for name, info in members.items():
            with zf.open(info) as src:
                (jpegs / name).write_bytes(src.read())

    jpeg_sha = {}
    out.mkdir(parents=True)
    counts = {"size_mismatches": 0, "resized": 0, "unresized": 0, "png_mismatches": 0, "unreadable": 0}
    lines = []
    for name, (image_id, w, h) in sorted(images.items(), key=lambda kv: kv[1][0]):
        path = jpegs / name
        if not path.exists():
            continue
        jpeg_sha[name] = file_sha256(path)
        bgr = cv2.imread(str(path), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
        if bgr is None:
            counts["unreadable"] += 1
            continue
        if bgr.shape != (h, w, 3):
            counts["size_mismatches"] += 1
            continue
        canvas, resized = letterbox(bgr, w, h)
        counts["resized" if resized else "unresized"] += 1
        png = out / f"{image_id:012d}.png"
        if not cv2.imwrite(str(png), canvas):
            raise InputError(f"cannot write {png}")
        back = cv2.imread(str(png), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
        if back is None or not np.array_equal(back, canvas):
            counts["png_mismatches"] += 1
        r, ws, hs, left, top = geometry(w, h)
        lines.append([image_id, name, w, h, repr(r), ws, hs, left, top, rgb_sha256(canvas), file_sha256(png)])
    rows += list(counts.items())
    if any(counts[k] for k in ("size_mismatches", "png_mismatches", "unreadable")):
        problems.append(", ".join(f"{v} {k}" for k, v in counts.items() if v and k not in ("resized", "unresized")))

    quant = quantization_images(Path(quantization).read_text(encoding="utf-8"))
    found = [n for n in quant if n in images]
    same = [n for n in found if jpeg_sha.get(n) == quant[n]]
    rows += [("quantization_images", len(quant)), ("quantization_in_val2017", len(found)),
             ("quantization_sha256_equal", len(same))]
    if not (len(quant) == len(found) == len(same) == QUANTIZATION_IMAGES):
        problems.append(f"quantization images: {len(found)} of {len(quant)} in val2017, {len(same)} with the "
                        "recorded SHA-256")

    h = header(pc)
    text = io.StringIO()
    text.write(header_text(h))
    writer = csv.writer(text, delimiter="\t", lineterminator="\n")
    writer.writerow(MANIFEST_COLUMNS)
    writer.writerows(lines)
    (record / "manifest.tsv").write_text(text.getvalue(), encoding="utf-8")
    rows += [("manifest_images", len(lines)), ("manifest_sha256", file_sha256(record / "manifest.tsv")),
             ("problems", "; ".join(problems) or "none"),
             ("passed", int(not problems and len(lines) == images_expected))]
    return rows, h


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--coco", required=True, help="the directory of val2017.zip and annotations_trainval2017.zip")
    ap.add_argument("--out", required=True, help="a new directory for the 5,000 PNGs")
    ap.add_argument("--record", default="/record", help="the record directory, with pc_run.py's pc.json")
    a = ap.parse_args(argv)
    coco, out, record = Path(a.coco), Path(a.out), Path(a.record)
    try:
        if out.exists():
            raise InputError(f"{out} exists: the inputs are prepared once, into a new directory")
        pc = json.loads((record / "pc.json").read_text(encoding="utf-8"))
        started = time.time()
        rows, h = prepare(coco, out, record, pc)
    except (InputError, OSError, KeyError, ValueError, zipfile.BadZipFile) as e:
        print(f"m4_prep: {e}", file=sys.stderr)
        return 2
    rows.insert(0, ("seconds", f"{time.time() - started:.0f}"))
    table = io.StringIO()
    csv.writer(table, lineterminator="\n").writerows([("key", "value")] + rows)  # quotes a value with a comma
    text = header_text(h) + table.getvalue()
    (record / "m4_inputs.csv").write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if dict(rows)["passed"] == 1 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
