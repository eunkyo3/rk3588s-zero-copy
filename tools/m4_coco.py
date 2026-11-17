#!/usr/bin/env python3
"""M4's detections files, their COCO conversion and their evaluation (prereg/M4.md §2.3 "To COCO" and
"Evaluation", §6), in the evaluation image.

A pass (the board's INT8 pass or the PC's FP32 pass, §2.5) writes, in a directory of its own:
  - detections.tsv: "# key: value" header lines, then the columns image_id, rank, class, score, x1, y1, x2,
    y2: one row per detection, the images in the manifest's order, each image's detections in the rule's
    order (rank 0 first). class is the model's index (0-79). score and the box, in model input
    coordinates, are float32 values printed with 9 significant digits (%.9g);
  - images.tsv: the same header, then image_id, candidates, detections, tied: per image, how many
    (cell, class) pairs were above conf, how many detections the rule kept, and 1 if the rule's next
    detection had the last one's score (§6, "how often the 300th score was tied").

Conversion (one function for both precisions): each box goes back to the original image with the
manifest's r, left and top: minus left (x) or top (y), divided by r, clipped to [0, w] × [0, h], as
[x, y, width, height]. It is computed in float64 from the printed values and written as float32 with 9
significant digits, as the score is. Class k is the k-th smallest category id of instances_val2017.json.
Each image's detections keep their order, which pycocotools' stable sort keeps among equal scores.

Evaluation: pycocotools' COCOeval, iouType bbox, default parameters, over the images the detections cover
(all 5,000 for a pass), and also without the 20 quantization images (§6). Writes coco_eval.csv in the
record directory, and the COCO json next to the detections.

What it evaluates:
  - the pass's manifest_rows_sha256 must be --manifest's;
  - a model's detections (a header that names model_sha256) only in a run of kind m4, after freeze-m4
    (§8 step 5), and only a pass of kind m4 that its own record (results/m4/<record>/pass.csv) calls
    valid, whose detections.tsv and images.tsv have the SHA-256 that record gives, over the manifest's
    5,000 images, 20 of them the quantization images;
  - other detections (synthetic ones, for checks of the tool) only in a run of kind check.

Usage, in the evaluation image (tools/eval/pc_run.py):
  python3 -I tools/m4_coco.py --dets /data/passes/<pass> --manifest <manifest.tsv> \
      --annotations /data/coco/annotations_trainval2017.zip [--record /record]
Exit 0, or 2 for unusable input.
"""
import argparse
import csv
import gc
import hashlib
import io
import json
import math
import re
import sys
import zipfile
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO / "tools"))
import m4_prep as mp  # noqa: E402  (the manifest's columns and the quantization images)
import m4_rules as mr  # noqa: E402

DET_COLUMNS = ["image_id", "rank", "class", "score", "x1", "y1", "x2", "y2"]
IMAGE_COLUMNS = ["image_id", "candidates", "detections", "tied"]
COCO_SHA256 = REPO / "tools/eval/coco.sha256"
QUANTIZATION = REPO / "models/coco_subset_20.sha256"
STATS = ["ap", "ap50", "ap75", "ap_small", "ap_medium", "ap_large", "ar1", "ar10", "ar100", "ar_small",
         "ar_medium", "ar_large"]  # COCOeval.stats, in its order
IMAGES = 5000  # §2.1: val2017
QUANTIZATION_IMAGES = 20  # §2.4


class InputError(Exception):
    pass


def write_table(path, header, columns, rows):
    """A "# key: value" header, then a tab-separated table."""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(f"# {k}: {v}\n" for k, v in header.items()))
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(columns)
        w.writerows(rows)


def read_table(path, columns):
    """(header, rows as lists of strings) of write_table's file, with these columns."""
    header, body = {}, []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("# ") and not body:
                k, _, v = line[2:].rstrip("\n").partition(": ")
                header[k] = v
            else:
                body.append(line)
    rows = list(csv.reader(body, delimiter="\t"))
    if not rows or rows[0] != columns:
        raise InputError(f"{path}: expected the columns {', '.join(columns)}")
    return header, rows[1:]


def detection_rows(image_id, result):
    """detections.tsv's rows of one image's Result (m4_rules)."""
    return [[image_id, rank, int(c), mr.g9(s), *(mr.g9(v) for v in box)]
            for rank, (c, s, box) in enumerate(zip(result.cls, result.score, result.box))]


def read_manifest(path):
    """{image id: manifest row as a dict} of a manifest.tsv (m4_prep), in its order."""
    _, rows = read_table(path, mp.MANIFEST_COLUMNS)
    return {int(r[0]): dict(zip(mp.MANIFEST_COLUMNS, r)) for r in rows}


def to_coco(dets, manifest, category_ids):
    """§2.3's conversion of detections.tsv's rows to COCO results, as the JSON text of a list. InputError for
    a class out of range or a value that is not finite."""
    out = []
    for image_id, _, k, score, x1, y1, x2, y2 in dets:
        m = manifest[int(image_id)]
        k, score, x1, y1, x2, y2 = int(k), float(score), float(x1), float(y1), float(x2), float(y2)
        if not 0 <= k < len(category_ids) or not all(map(math.isfinite, (score, x1, y1, x2, y2))):
            raise InputError(f"image {image_id}: class {k} or a value is out of range")
        r, left, top, w, h = float(m["r"]), int(m["left"]), int(m["top"]), int(m["w"]), int(m["h"])
        bx1 = min(max((x1 - left) / r, 0.0), w)
        by1 = min(max((y1 - top) / r, 0.0), h)
        bx2 = min(max((x2 - left) / r, 0.0), w)
        by2 = min(max((y2 - top) / r, 0.0), h)
        box = ",".join(mr.g9(v) for v in (bx1, by1, bx2 - bx1, by2 - by1))
        out.append(f'{{"image_id":{int(image_id)},"category_id":{category_ids[k]},"bbox":[{box}],'
                   f'"score":{mr.g9(score)}}}')
    return "[" + ",\n".join(out) + "]\n"


def pass_record(det_header):
    """The key,value rows of the pass's own record, results/<run kind>/<record>/pass.csv, or {} without one."""
    kind, name = det_header.get("run_kind", ""), det_header.get("record", "")
    path = REPO / "results" / kind / name / "pass.csv"
    if kind not in ("check", "m4") or not re.fullmatch(r"[A-Za-z0-9_-]+", name) or not path.is_file():
        return {}
    rows = csv.reader(l for l in path.read_text(encoding="utf-8").splitlines() if not l.startswith("# "))
    return {r[0]: r[1] for r in rows if len(r) == 2}


def recorded_sha256(det_header):
    """The keys under which a pass's record gives its detections files' SHA-256: the board's INT8 pass
    has two pairs (map/ and pipeline/), the PC's FP32 pass one."""
    if det_header.get("pass", "").endswith("_pipeline"):
        return "sha256_pipeline_detections_tsv", "sha256_pipeline_images_tsv"
    if det_header.get("precision") == "int8":
        return "sha256_map_detections_tsv", "sha256_map_images_tsv"
    return "sha256_detections_tsv", "sha256_images_tsv"


def what_may_run(pc, det_header, ids, manifest, gt_ids, without, record, files_sha256):
    """InputError unless this run may evaluate this pass (the module's docstring). record is pass_record's;
    files_sha256 the SHA-256 of the detections.tsv and images.tsv to evaluate."""
    if det_header.get("model_sha256"):
        if pc["run_kind"] != "m4" or det_header.get("run_kind") != "m4":
            raise InputError("a model's detections are evaluated in a run of kind m4 only, of a pass of kind m4, "
                             "after freeze-m4 (M4 §8 step 5)")
        if record.get("valid") != "1":
            raise InputError(f"the pass's record results/m4/{det_header.get('record')}/pass.csv does not say valid,1")
        if tuple(record.get(k) for k in recorded_sha256(det_header)) != tuple(files_sha256):
            raise InputError("the detections are not the files whose SHA-256 the pass's record gives")
        if ids != list(manifest) or len(ids) != IMAGES or len(gt_ids) != IMAGES \
                or len(ids) - len(without) != QUANTIZATION_IMAGES:
            raise InputError(f"a pass covers the manifest's {IMAGES} images, {QUANTIZATION_IMAGES} of them the "
                             "quantization images")
    elif pc["run_kind"] != "check":
        raise InputError("a run of kind m4 evaluates a model's pass, whose header names model_sha256")


def evaluate(gt, results_json, subsets):
    """COCOeval's 12 stats (bbox, default parameters) over each list of image ids of subsets, with one
    loadRes of the results and one COCOeval at a time: for a pass, the results take about 1.4 GB and a
    COCOeval about 3 GB, about 4.9 GB in all (docs/notes/m4_accuracy.md). COCOeval changes neither the
    results nor the ground truth (its _prepare sets each ground truth's ignore from iscrowd alone), so the
    subsets do not depend on each other."""
    from contextlib import redirect_stdout

    from pycocotools.cocoeval import COCOeval

    stats = []
    with redirect_stdout(io.StringIO()):
        dt = gt.loadRes(str(results_json))
        for image_ids in subsets:
            e = COCOeval(gt, dt, "bbox")
            e.params.imgIds = sorted(image_ids)
            e.evaluate()
            e.accumulate()
            e.summarize()
            stats.append([float(v) for v in e.stats])
            del e
            gc.collect()
    return stats


def load_gt(annotations_zip):
    """pycocotools' COCO of instances_val2017.json, read from the annotations zip."""
    from contextlib import redirect_stdout

    from pycocotools.coco import COCO

    with zipfile.ZipFile(annotations_zip) as zf:
        data = json.loads(zf.read(mp.ANNOTATIONS))
    gt = COCO()
    gt.dataset = data
    with redirect_stdout(io.StringIO()):
        gt.createIndex()
    return gt


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dets", required=True, help="a pass's directory: detections.tsv and images.tsv")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--annotations", required=True, help="annotations_trainval2017.zip")
    ap.add_argument("--record", default="/record")
    a = ap.parse_args(argv)
    dets_dir, record = Path(a.dets), Path(a.record)
    try:
        pc = json.loads((record / "pc.json").read_text(encoding="utf-8"))
        expected = mp.read_sha256_file(COCO_SHA256.read_text(encoding="utf-8"))
        if mp.file_sha256(a.annotations) != expected[mp.ANN_ZIP]:
            raise InputError(f"{a.annotations} is not the file tools/eval/coco.sha256 fixes")
        manifest = read_manifest(a.manifest)
        det_header, dets = read_table(dets_dir / "detections.tsv", DET_COLUMNS)
        _, images = read_table(dets_dir / "images.tsv", IMAGE_COLUMNS)
        if det_header.get("manifest_rows_sha256") != mp.rows_sha256(Path(a.manifest).read_text(encoding="utf-8")):
            raise InputError(f"{a.manifest} is not the pass's manifest (its manifest_rows_sha256)")
        ids = [int(r[0]) for r in images]
        at_300 = sum(int(r[2]) == mr.MAX_DET for r in images)
        tied_at_300 = sum(int(r[3]) == 1 for r in images)
        if not ids or ids != list(manifest)[:len(ids)] or {int(d[0]) for d in dets} - set(ids):
            raise InputError("the pass's images are not the manifest's first images in its order")
        if not dets:
            raise InputError("the pass has no detections")
        gt = load_gt(a.annotations)
        quant = set(mp.quantization_images(QUANTIZATION.read_text(encoding="utf-8")))
        without = [i for i in ids if manifest[i]["file_name"] not in quant]
        record_rows = pass_record(det_header)
        valid = record_rows.get("valid")
        what_may_run(pc, det_header, ids, manifest, gt.getImgIds(), without, record_rows,
                     (mp.file_sha256(dets_dir / "detections.tsv"), mp.file_sha256(dets_dir / "images.tsv")))
        text = to_coco(dets, manifest, sorted(gt.getCatIds()))
    except (InputError, OSError, KeyError, ValueError, IndexError, zipfile.BadZipFile) as e:
        print(f"m4_coco: {e}", file=sys.stderr)
        return 2
    results = dets_dir / "coco_results.json"
    results.write_text(text, encoding="utf-8", newline="\n")
    n_dets = len(dets)
    del dets, text  # about 1 GB for a pass, freed before COCOeval
    gc.collect()
    rows = [("pass_valid", valid or "no record"), ("images", len(ids)),
            ("images_without_quantization", len(without)), ("detections", n_dets),
            ("detections_per_image_mean", f"{n_dets / len(ids):.2f}"),
            ("images_at_300", at_300), ("images_tied_at_300", tied_at_300),
            ("sha256_detections_tsv", mp.file_sha256(dets_dir / "detections.tsv")),
            ("sha256_images_tsv", mp.file_sha256(dets_dir / "images.tsv")),
            ("sha256_coco_results_json", mp.file_sha256(results))]
    for name, stats in zip(("all", "without_quantization"), evaluate(gt, results, (ids, without))):
        rows += [(f"{name}_{k}", f"{v:.6f}") for k, v in zip(STATS, stats)]
    header = {k: pc[k] for k in mp.PC_FIELDS}
    header["pass_record"] = det_header.get("record", "")
    header.update({k: det_header.get(k, "") for k in ("pass", "model", "model_sha256", "precision", "rule",
                                                       "manifest_sha256", "manifest_rows_sha256")})
    header["dets"] = str(dets_dir)
    table = io.StringIO()
    csv.writer(table, lineterminator="\n").writerows([("key", "value")] + rows)
    text = "".join(f"# {k}: {v}\n" for k, v in header.items()) + table.getvalue()
    (record / "coco_eval.csv").write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
