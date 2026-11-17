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
    """§2.3's conversion of detections.tsv's rows to COCO results, as the JSON text of a list."""
    out = []
    for image_id, _, k, score, x1, y1, x2, y2 in dets:
        m = manifest[int(image_id)]
        r, left, top, w, h = float(m["r"]), int(m["left"]), int(m["top"]), int(m["w"]), int(m["h"])
        bx1 = min(max((float(x1) - left) / r, 0.0), w)
        by1 = min(max((float(y1) - top) / r, 0.0), h)
        bx2 = min(max((float(x2) - left) / r, 0.0), w)
        by2 = min(max((float(y2) - top) / r, 0.0), h)
        box = ",".join(mr.g9(v) for v in (bx1, by1, bx2 - bx1, by2 - by1))
        out.append(f'{{"image_id":{int(image_id)},"category_id":{category_ids[int(k)]},"bbox":[{box}],'
                   f'"score":{mr.g9(float(score))}}}')
    return "[" + ",\n".join(out) + "]\n"


def evaluate(gt, results_json, subsets):
    """COCOeval's 12 stats (bbox, default parameters) over each list of image ids of subsets, with one
    loadRes of the results and one COCOeval at a time: for a pass, the results take about 1.4 GB and a
    COCOeval about 3 GB. COCOeval changes neither the results nor the ground truth (its _prepare sets
    each ground truth's ignore from iscrowd alone), so the subsets do not depend on each other."""
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
        ids = [int(r[0]) for r in images]
        if not ids or ids != list(manifest)[:len(ids)] or {int(d[0]) for d in dets} - set(ids):
            raise InputError("the pass's images are not the manifest's first images in its order")
        gt = load_gt(a.annotations)
    except (InputError, OSError, KeyError, ValueError, zipfile.BadZipFile) as e:
        print(f"m4_coco: {e}", file=sys.stderr)
        return 2
    category_ids = sorted(gt.getCatIds())
    results = dets_dir / "coco_results.json"
    results.write_text(to_coco(dets, manifest, category_ids), encoding="utf-8", newline="\n")
    n_dets = len(dets)
    del dets  # about 1 GB for a pass, freed before COCOeval
    gc.collect()
    quant = set(mp.quantization_images(QUANTIZATION.read_text(encoding="utf-8")))
    without = [i for i in ids if manifest[i]["file_name"] not in quant]
    rows = [("images", len(ids)), ("images_without_quantization", len(without)), ("detections", n_dets),
            ("detections_per_image_mean", f"{n_dets / len(ids):.2f}"),
            ("images_at_300", sum(int(r[2]) == mr.MAX_DET for r in images)),
            ("images_tied_at_300", sum(r[3] == "1" for r in images)),
            ("sha256_detections_tsv", mp.file_sha256(dets_dir / "detections.tsv")),
            ("sha256_images_tsv", mp.file_sha256(dets_dir / "images.tsv")),
            ("sha256_coco_results_json", mp.file_sha256(results))]
    for name, stats in zip(("all", "without_quantization"), evaluate(gt, results, (ids, without))):
        rows += [(f"{name}_{k}", f"{v:.6f}") for k, v in zip(STATS, stats)]
    header = {k: pc[k] for k in mp.PC_FIELDS}
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
