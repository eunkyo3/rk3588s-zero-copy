#!/usr/bin/env python3
"""M4's FP32 pass on the PC (prereg/M4.md §2.2, §2.3, §2.5), in the evaluation image, through
tools/eval/pc_run.py (which records the PC, its clocks and the run kind in <record>/pc.json):

  MSYS_NO_PATHCONV=1 python tools/eval/pc_run.py --kind m4 --name fp32_yolov8n -- tools/m4_fp32.py \
      --model yolov8n --manifest results/check/<UTC>_m4_inputs/manifest.tsv --inputs /data/inputs2

  - The model's SHA-256 must be §1's (models/<model>.sha256) before anything runs.
  - onnxruntime's CPU execution provider, one intra-op and one inter-op thread, its default graph
    optimization.
  - The images run in the manifest's order. Each PNG is decoded (cv2.imread, IMREAD_COLOR |
    IMREAD_IGNORE_ORIENTATION), made RGB, and its bytes must be the manifest's (rgb_sha256): otherwise the
    pass is invalid (§5) and stops. The model sees RGB / 255 in float32, NCHW.
  - The model's rule (tools/m4_rules.py: yolov8_rule or yolov10_rule, conf 0.001, at most 300) gives
    each image's detections.
  - A pass is one uninterrupted run over all 5,000 images (§2.5). --limit N runs the first N, for a check
    only (run kind check).
Writes detections.tsv and images.tsv (tools/m4_coco.py) to --out (default /data/passes/<record name>), a
new directory outside the repository, and pass.csv to the record directory: the header, the counts, the
SHA-256 of both files, and valid 1, or valid 0 with the reason.
Exit 0 for a valid pass, 1 for an invalid one, 2 when it cannot start.
"""
import argparse
import csv
import hashlib
import io
import json
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO / "tools"))
import m4_coco as mc  # noqa: E402
import m4_prep as mp  # noqa: E402
import m4_rules as mr  # noqa: E402

MODELS = {"yolov8n": ("models/yolov8n.onnx", "models/yolov8n.sha256", "0c871670"),  # §1, and its prefix there
          "yolov10n": ("models/yolov10n.onnx", "models/yolov10n.sha256", "4a925bcb")}
RULE_TEXT = {"yolov8n": "yolov8_rule: conf 0.001, first 30000, class-wise greedy NMS at IoU 0.7, max 300",
             "yolov10n": "yolov10_rule: conf 0.001, max 300, no NMS"}


class InputError(Exception):
    pass


def model_sha256(model):
    """The model file's path and its SHA-256, checked against models/<model>.sha256 and §1's prefix."""
    path, sha_file, prefix = MODELS[model]
    recorded = mp.read_sha256_file("\n".join(l for l in (REPO / sha_file).read_text(encoding="utf-8").splitlines()
                                             if l.endswith(".onnx")))
    want = recorded[Path(path).name]
    got = mp.file_sha256(REPO / path)
    if got != want or not want.startswith(prefix):
        raise InputError(f"{path} has SHA-256 {got}, not {want} (M4 §1)")
    return REPO / path, got


def session(path):
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1  # graph optimization: onnxruntime's default
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"]), ort.__version__


def model_input(png, rgb_sha256):
    """RGB / 255 of a PNG as NCHW float32, after checking its RGB bytes against the manifest."""
    import cv2

    bgr = cv2.imread(str(png), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    if bgr is None:
        raise InputError(f"cannot decode {png}")
    rgb = np.ascontiguousarray(bgr[:, :, ::-1])
    if hashlib.sha256(rgb.tobytes()).hexdigest() != rgb_sha256:
        raise InputError(f"{png.name}'s RGB bytes are not the manifest's (M4 §5)")
    return np.ascontiguousarray((rgb.astype(np.float32) / np.float32(255)).transpose(2, 0, 1)[None])


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", required=True, choices=list(MODELS))
    ap.add_argument("--manifest", required=True, help="the inputs' manifest.tsv (tools/m4_prep.py)")
    ap.add_argument("--inputs", required=True, help="the directory of the manifest's PNGs")
    ap.add_argument("--out", help="a new directory for detections.tsv and images.tsv")
    ap.add_argument("--limit", type=int, help="run kind check only: the first N images")
    ap.add_argument("--record", default="/record")
    a = ap.parse_args(argv)
    record = Path(a.record)
    try:
        pc = json.loads((record / "pc.json").read_text(encoding="utf-8"))
        if pc["run_kind"] not in ("check", "m4") or (a.limit is not None and pc["run_kind"] != "check"):
            raise InputError(f"run kind {pc['run_kind']}: m4 runs every image; --limit is for a check")
        out = Path(a.out or f"/data/passes/{pc['record']}")
        if out.exists():
            raise InputError(f"{out} exists: a pass writes a new directory")
        model_path, model_sha = model_sha256(a.model)
        manifest = mc.read_manifest(a.manifest)
        text = Path(a.manifest).read_text(encoding="utf-8")
        images = list(manifest.values())[:a.limit] if a.limit else list(manifest.values())
        sess, ort_version = session(model_path)
    except (InputError, OSError, KeyError, ValueError) as e:
        print(f"m4_fp32: {e}", file=sys.stderr)
        return 2
    header = {**{k: pc[k] for k in mp.PC_FIELDS}, "pass": f"fp32_{a.model}", "model": a.model,
              "model_file": Path(MODELS[a.model][0]).name, "model_sha256": model_sha, "precision": "fp32",
              "rule": RULE_TEXT[a.model], "manifest": a.manifest, "manifest_sha256": mp.file_sha256(a.manifest),
              "manifest_rows_sha256": mp.rows_sha256(text), "images_planned": len(images),
              "onnxruntime": f"{ort_version}, CPUExecutionProvider, 1 intra-op and 1 inter-op thread"}
    out.mkdir(parents=True)
    rule = mr.RULES[a.model]
    name = sess.get_inputs()[0].name
    started, done, n_dets, ties, reason = time.time(), 0, 0, 0, "none"
    with open(out / "detections.tsv", "w", encoding="utf-8", newline="\n") as dets, \
            open(out / "images.tsv", "w", encoding="utf-8", newline="\n") as per_image:
        dw, iw = csv.writer(dets, delimiter="\t", lineterminator="\n"), csv.writer(per_image, delimiter="\t",
                                                                                  lineterminator="\n")
        for f, columns in ((dets, mc.DET_COLUMNS), (per_image, mc.IMAGE_COLUMNS)):
            f.write("".join(f"# {k}: {v}\n" for k, v in header.items()))
            csv.writer(f, delimiter="\t", lineterminator="\n").writerow(columns)
        try:
            for m in images:
                image_id = int(m["image_id"])
                x = model_input(Path(a.inputs) / f"{image_id:012d}.png", m["rgb_sha256"])
                result = rule(sess.run(None, {name: x}))
                dw.writerows(mc.detection_rows(image_id, result))
                iw.writerow([image_id, result.candidates, len(result.cls), int(result.tied)])
                done, n_dets, ties = done + 1, n_dets + len(result.cls), ties + int(result.tied)
                if done % 100 == 0:
                    print(f"m4_fp32: {done} of {len(images)} images, {time.time() - started:.0f} s", flush=True)
        except (InputError, OSError, ValueError) as e:  # §5: an invalid pass, kept and repeated from the start
            reason = str(e)
    rows = [("images", done), ("images_planned", len(images)), ("detections", n_dets), ("images_tied", ties),
            ("seconds", f"{time.time() - started:.0f}"), ("out", str(out)),
            ("sha256_detections_tsv", mp.file_sha256(out / "detections.tsv")),
            ("sha256_images_tsv", mp.file_sha256(out / "images.tsv")),
            ("valid", int(reason == "none" and done == len(images))), ("reason", reason)]
    table = io.StringIO()
    csv.writer(table, lineterminator="\n").writerows([("key", "value")] + rows)
    text = "".join(f"# {k}: {v}\n" for k, v in header.items()) + table.getvalue()
    (record / "pass.csv").write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")
    return 0 if reason == "none" and done == len(images) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
