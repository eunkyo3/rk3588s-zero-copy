"""Exploration before M4's plan, not a run that counts: YOLOv10n's ONNX FP32 reference and its
comparison with the board's INT8 outputs (tools/m4_probe.cpp).

Runs in the converter image (tools/model/Dockerfile), with rknn_model_zoo cloned at v2.3.2 into a
directory named rknn_model_zoo (the zoo's yolov10.py looks for that name in its path):

  reference ZOO ONNX WORK
      The zoo's bus.jpg and its 20 calibration images (datasets/COCO/coco_subset_20.txt), letterboxed
      to 640x640 with pad 0 as the zoo's yolov10.py does, are written as WORK/<image>.png (the board's
      inputs). ONNX FP32 runs on RGB/255 of the same pixels; its outputs go to WORK/<image>.npz.
  compare ZOO WORK PROBE_DIR
      Against PROBE_DIR (m4_probe's m4_probe.csv, detections.csv and *.i8):
        1. tensors: the largest |INT8 - FP32| per output, and the INT8 values at +127 (saturated);
        2. detections at conf 0.25, the zoo's post_process_yolov10 on dequantized INT8 against the same on
           FP32: matched (same class, IoU >= 0.5), only in one, IoU and score differences;
        3. same-class pairs with IoU > 0.45 (what an NMS at 0.45 would remove), and pairs with identical
           boxes (two classes at one cell);
        4. yolov10_post (C++, detections.csv) against post_process_yolov10 on the same INT8 outputs, at
           conf 0.25 and 0.001: matched within 1e-6 in score and 1e-3 px, else, where a side kept 300,
           whether the pair is tied at the 300th score.
      Writes PROBE_DIR/m4_explore.json and prints a summary.
"""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ZOO_COMMIT = "bad6c7334531becaf90a561988519b7bec34d0ab"  # v2.3.2
ONNX_SHA256 = "4a925bcb01b261ab97955bfe6c0dd5ad2e638f6db13198d4b6602750870603f6"


def zoo_post(zoo):
    sys.path.insert(0, str(Path(zoo) / "examples/yolov10/python"))
    import yolov10  # noqa: E402

    return yolov10


def images(zoo):
    zoo = Path(zoo)
    names = (zoo / "datasets/COCO/coco_subset_20.txt").read_text().split()
    return [zoo / "examples/yolov10/model/bus.jpg"] + [zoo / "datasets/COCO" / n for n in names]


def reference(zoo, onnx_path, work):
    import subprocess

    import cv2
    import onnxruntime as ort

    head = subprocess.run(["git", "-C", zoo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if head != ZOO_COMMIT:
        sys.exit(f"rknn_model_zoo is at {head}, not v2.3.2 ({ZOO_COMMIT})")
    if hashlib.sha256(Path(onnx_path).read_bytes()).hexdigest() != ONNX_SHA256:
        sys.exit(f"{onnx_path} is not yolov10n.onnx {ONNX_SHA256[:8]}")
    sys.path.insert(0, str(Path(zoo) / "py_utils"))
    from coco_utils import COCO_test_helper  # noqa: E402

    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    for p in images(zoo):
        img = COCO_test_helper(enable_letter_box=True).letter_box(
            im=cv2.imread(str(p)), new_shape=(640, 640), pad_color=(0, 0, 0))
        cv2.imwrite(str(work / f"{p.stem}.png"), img)
        x = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        np.savez(work / f"{p.stem}.npz", *sess.run(None, {sess.get_inputs()[0].name: x}))
    print(f"{len(images(zoo))} inputs and ONNX outputs in {work}")


def iou(a, b):
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = w * h
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def read_probe(probe):
    rows = {}
    for line in (probe / "m4_probe.csv").read_text().splitlines():
        if not line.startswith("#") and "," in line:
            k, v = line.split(",", 1)
            rows[k] = v
    attrs = [{"dims": tuple(int(d) for d in rows[f"output{k}_dims"].split("x")), "zp": int(rows[f"output{k}_zp"]),
              "scale": np.float32(rows[f"output{k}_scale"])} for k in range(6)]
    cpp = {}
    for line in (probe / "detections.csv").read_text().splitlines():
        if line.startswith("#") or line.startswith("image,"):
            continue
        f = line.split(",")
        cpp.setdefault((f[0], f[1]), []).append((int(f[3]), float(f[4]), [float(v) for v in f[5:9]]))
    return attrs, cpp


def compare(zoo, work, probe):
    zy = zoo_post(zoo)
    work, probe = Path(work), Path(probe)
    attrs, cpp = read_probe(probe)
    stems = [p.stem for p in images(zoo)]

    worst = [0.0] * 6
    saturated = [0] * 6
    matched = only_fp32 = only_int8 = 0
    ious, score_diff, one_side = [], [], []
    overlaps = {"fp32": 0, "int8": 0}
    identical_boxes = {"fp32": 0, "int8": 0}
    totals = {"fp32": 0, "int8": 0}
    port = {c: {"detections": 0, "matched": 0, "tied_at_cut": 0, "other": 0, "max_box_diff": 0.0}
            for c in ("0.25", "0.001")}
    for stem in stems:
        fp32 = [np.load(work / f"{stem}.npz")[f"arr_{k}"] for k in range(6)]
        int8 = []
        for k, a in enumerate(attrs):
            q = np.fromfile(probe / f"{stem}_out{k}.i8", dtype=np.int8).reshape(a["dims"])
            saturated[k] += int((q == 127).sum())
            d = (q.astype(np.float32) - np.float32(a["zp"])) * a["scale"]
            worst[k] = max(worst[k], float(np.abs(d - fp32[k]).max()))
            int8.append(d)

        zy.OBJ_THRESH = 0.25
        res = {"fp32": zy.post_process_yolov10(fp32), "int8": zy.post_process_yolov10(int8)}
        for name, (b, c, s) in res.items():
            totals[name] += len(c)
            for i in range(len(c)):
                for j in range(i + 1, len(c)):
                    overlaps[name] += bool(c[i] == c[j] and iou(b[i], b[j]) > 0.45)
                    identical_boxes[name] += bool(np.array_equal(b[i], b[j]))
        (fb, fc, fs), (ib, ic, is_) = res["fp32"], res["int8"]
        used = set()
        for i in range(len(fc)):
            best, bj = 0.0, -1
            for j in range(len(ic)):
                if j not in used and ic[j] == fc[i]:
                    v = iou(fb[i], ib[j])
                    if v > best:
                        best, bj = v, j
            if bj >= 0 and best >= 0.5:
                used.add(bj)
                matched += 1
                ious.append(best)
                score_diff.append(float(is_[bj] - fs[i]))
            else:
                only_fp32 += 1
                one_side.append(float(fs[i]))
        only_int8 += len(ic) - len(used)
        one_side += [float(is_[j]) for j in range(len(ic)) if j not in used]

        for conf in ("0.25", "0.001"):
            zy.OBJ_THRESH = float(conf)
            b, c, s = zy.post_process_yolov10(int8)
            py = list(zip(c.tolist(), s.tolist(), b.tolist()))
            mine = cpp.get((stem, conf), [])
            st = port[conf]
            st["detections"] += len(mine)
            # Ties can only differ where a side was cut at its 300th detection.
            cut = 300 in (len(py), len(mine))
            low = min((t[1] for t in mine), default=0.0)
            free = [True] * len(py)
            for cc, cs, cb in mine:
                hit = next((j for j, (pc, ps, pb) in enumerate(py) if free[j] and pc == cc and abs(ps - cs) < 1e-6
                            and max(abs(x - y) for x, y in zip(pb, cb)) < 1e-3), -1)
                if hit >= 0:
                    free[hit] = False
                    st["matched"] += 1
                    st["max_box_diff"] = max(st["max_box_diff"], max(abs(x - y) for x, y in zip(py[hit][2], cb)))
                elif cut and abs(cs - low) < 1e-6:
                    st["tied_at_cut"] += 1
                else:
                    st["other"] += 1
            st["other"] += sum(1 for j, p in enumerate(py) if free[j] and not (cut and abs(p[1] - low) < 1e-6))

    out = {
        "images": len(stems),
        "tensors": {"max_abs_int8_minus_fp32": [round(v, 4) for v in worst],
                    "int8_max_value": [round(float((127 - a["zp"]) * a["scale"]), 4) for a in attrs],
                    "values_at_127": saturated},
        "detections_conf_0.25": {
            "fp32": totals["fp32"], "int8": totals["int8"], "matched": matched, "only_fp32": only_fp32,
            "only_int8": only_int8,
            "iou_min": round(min(ious), 3) if ious else None, "iou_median": round(float(np.median(ious)), 3) if ious else None,
            "score_diff_median": round(float(np.median(score_diff)), 4) if score_diff else None,
            "score_diff_min": round(min(score_diff), 4) if score_diff else None,
            "score_diff_max": round(max(score_diff), 4) if score_diff else None,
            "one_side_scores": sorted(round(v, 3) for v in one_side),
            "same_class_iou_over_0.45": overlaps, "pairs_with_identical_boxes": identical_boxes},
        "yolov10_post_vs_zoo_python": port,
    }
    (probe / "m4_explore.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "reference" and len(sys.argv) == 5:
        reference(*sys.argv[2:])
    elif cmd == "compare" and len(sys.argv) == 5:
        compare(*sys.argv[2:])
    else:
        sys.exit(__doc__)
