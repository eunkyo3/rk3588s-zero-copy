#!/usr/bin/env python3
"""§8 step 3's check of M4's two implementations of a rule (prereg/M4.md §2.3), in the evaluation image:
the Python rule (tools/m4_rules.py) on the board's dumped INT8 outputs of the manifest's first 50 images,
against the C++ rule's detections of the same run (tools/m4_accuracy.cpp pass, --dets-in-record).

  MSYS_NO_PATHCONV=1 python tools/eval/pc_run.py --kind check --name rules_check_yolov8n -- \\
      tools/m4_rules_check.py --board-record results/check/<UTC>_rules_yolov8n

For each dumped image, the two lists must have the same count and order, the same class, the score within
1e-6 and the box within 1e-3 px; the candidates' count and the tie flag must also agree. No pair is
excepted, except a difference caused by an IoU within 1e-6 of 0.7, which is reported. Caused means: the
Python NMS compared such an IoU in that image, and moving the threshold by 1e-6 one way or the other
makes the Python rule give the C++ list and tie. A difference in the candidates' count, which comes before
the NMS, is never excepted.
Writes <record>/rules_check.csv: the counts, the largest differences, how many values are equal bit for
bit, the IoUs near 0.7, and valid. Exit 0 when valid, 1 when not, 2 for unusable input.
"""
import argparse
import csv
import io
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO / "tools"))
import m4_coco as mc  # noqa: E402
import m4_prep as mp  # noqa: E402
import m4_rules as mr  # noqa: E402

MANIFEST = REPO / "results/check/20261116T064917Z_m4_inputs/manifest.tsv"
IMAGES = 50  # §2.3, §8 step 3
SCORE_TOL, BOX_TOL = 1e-6, 1e-3
NEAR = 1e-6  # §2.3: an IoU within 1e-6 of 0.7


class InputError(Exception):
    pass


def read_csv(path):
    """(header, rows as dicts) of a "# key: value" headed CSV."""
    text = Path(path).read_text(encoding="utf-8")
    header = {k: v for k, _, v in (l[2:].partition(": ") for l in text.splitlines() if l.startswith("# "))}
    rows = list(csv.DictReader(l for l in text.splitlines() if not l.startswith("# ")))
    return header, rows


def outputs_of(dump, image_id, attrs):
    """The image's dumped outputs, dequantized as the C++ does."""
    out = []
    for k, a in enumerate(attrs):
        q = np.fromfile(dump / f"{image_id:012d}_out{k}.i8", np.int8)
        dims = tuple(int(x) for x in a["dims"].split("x"))
        if q.size != np.prod(dims):
            raise InputError(f"{image_id:012d}_out{k}.i8 has {q.size} values, not {np.prod(dims)}")
        out.append(mr.dequantize(q.reshape(dims), int(a["zp"]), np.float32(a["scale"])))
    return out


def compare(result, rows):
    """(same list within §2.3's tolerances, largest score and box differences, values equal bit for bit,
    values) of one image: a Python Result against the C++ rows."""
    if len(rows) != len(result.cls) or any(int(r[2]) != c for r, c in zip(rows, result.cls.tolist())):
        return False, float("inf"), float("inf"), 0, 0
    want = np.array([[r[3], r[4], r[5], r[6], r[7]] for r in rows], np.float32).reshape(-1, 5)
    got = np.concatenate([result.score[:, None], result.box], axis=1).astype(np.float32).reshape(-1, 5)
    score_diff = float(np.abs(got[:, 0] - want[:, 0]).max()) if len(rows) else 0.0
    box_diff = float(np.abs(got[:, 1:] - want[:, 1:]).max()) if len(rows) else 0.0
    ok = score_diff <= SCORE_TOL and box_diff <= BOX_TOL
    return ok, score_diff, box_diff, int((got == want).sum()), int(want.size)


def judge(rule, outputs, rows, candidates, tied, nms):
    """One image: (verdict "equal", "excepted" or "differ", what differs, score and box differences, values
    equal bit for bit, values, the near-0.7 IoUs). rule(outputs, **kw) is the Python rule; rows are the C++
    list; candidates and tied are the C++ counts; nms says whether the rule has an NMS (YOLOv8n)."""
    near = [] if nms else None
    result = rule(outputs, near=near) if nms else rule(outputs)
    ok, s, b, bits, n = compare(result, rows)
    if candidates != result.candidates:
        return "differ", "the candidates' count", s, b, bits, n, near or []
    if ok and tied == result.tied:
        return "equal", "", s, b, bits, n, near or []
    what = "the lists" if not ok else "the tie"
    if near:
        for thresh in (mr.NMS_IOU - np.float32(NEAR), mr.NMS_IOU + np.float32(NEAR)):
            moved = rule(outputs, iou_thresh=np.float32(thresh))
            if compare(moved, rows)[0] and moved.tied == tied:
                return "excepted", what + f", reproduced at IoU {float(thresh):.7f}", s, b, bits, n, near
    return "differ", what, s, b, bits, n, near or []


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--board-record", required=True, help="the board's check record, fetched to results/check/")
    ap.add_argument("--record", default="/record")
    a = ap.parse_args(argv)
    board, record = REPO / a.board_record, Path(a.record)
    try:
        pc = json.loads((record / "pc.json").read_text(encoding="utf-8"))
        pass_header, pass_rows = read_csv(board / "pass.csv")
        if {r["key"]: r["value"] for r in pass_rows}.get("valid") != "1":
            raise InputError(f"{board.name}'s pass.csv does not say valid,1")
        _, attrs = read_csv(board / "outputs.csv")
        det_header, dets = mc.read_table(board / "dets/map/detections.tsv", mc.DET_COLUMNS)
        _, images = mc.read_table(board / "dets/map/images.tsv", mc.IMAGE_COLUMNS)
        model = det_header["model"]
        rule = mr.RULES[model]
        manifest = list(mc.read_manifest(MANIFEST))
        if det_header.get("manifest_rows_sha256") != mp.rows_sha256(MANIFEST.read_text(encoding="utf-8")):
            raise InputError("the board's run used another manifest")
        dumped = sorted(int(p.name[:12]) for p in (board / "dump").glob("*_out0.i8"))
        if dumped != sorted(manifest[:IMAGES]):
            raise InputError(f"the dump has {len(dumped)} images, not the manifest's first {IMAGES}")
        per_image = {int(r[0]): r for r in images}
        missing = [i for i in manifest[:IMAGES] if i not in per_image]
        if missing:
            raise InputError(f"images.tsv has no row for {missing[0]:012d}")
    except (InputError, OSError, KeyError, ValueError) as e:
        print(f"m4_rules_check: {e}", file=sys.stderr)
        return 2
    by_image = {}
    for r in dets:
        by_image.setdefault(int(r[0]), []).append(r)

    counts = {"equal": 0, "excepted": 0, "differ": 0}
    bits = values = 0
    worst_score = worst_box = 0.0
    near_all, problems = [], []
    for image_id in manifest[:IMAGES]:
        cpp = per_image[image_id]
        verdict, what, s, b, eq_bits, n, near = judge(
            rule, outputs_of(board / "dump", image_id, attrs), by_image.get(image_id, []), int(cpp[1]),
            cpp[3] == "1", model == "yolov8n")
        counts[verdict] += 1
        if verdict != "equal":
            problems.append(f"{image_id:012d}: {what} differ" + (" (excepted: §2.3)" if verdict == "excepted" else ""))
        near_all += [(image_id, k, v) for k, v in near]
        worst_score, worst_box = max(worst_score, s), max(worst_box, b)
        bits, values = bits + eq_bits, values + n

    valid = counts["differ"] == 0
    rows = [("board_record", board.name), ("model", model), ("images", IMAGES), ("lists_equal", counts["equal"]),
            ("lists_differing", counts["differ"]), ("lists_excepted_near_0.7", counts["excepted"]),
            ("largest_score_difference", f"{worst_score:.3g}"), ("largest_box_difference_px", f"{worst_box:.3g}"),
            ("values_equal_bit_for_bit", bits), ("values", values), ("ious_within_1e-6_of_0.7", len(near_all)),
            ("ious_within_1e-6_of_0.7_list", "; ".join(f"{i:012d} rank {k} IoU {v:.9g}" for i, k, v in near_all) or "none"),
            ("problems", "; ".join(problems) or "none"), ("valid", int(valid))]
    header = {**{k: pc[k] for k in mp.PC_FIELDS}, "board_record": board.name,
              "board_code_commit": pass_header.get("code_commit", ""), "model": model,
              "model_sha256": det_header.get("model_sha256", ""), "rule_python": "tools/m4_rules.py",
              "rule_cpp": det_header.get("rule", ""), "map_rule_object_sha256": pass_header.get("map_rule_object_sha256", "")}
    table = io.StringIO()
    csv.writer(table, lineterminator="\n").writerows([("key", "value")] + rows)
    text = "".join(f"# {k}: {v}\n" for k, v in header.items()) + table.getvalue()
    (record / "rules_check.csv").write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")
    return 0 if valid else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
