#!/usr/bin/env python3
"""§8 step 3's check of M4's two implementations of a rule (prereg/M4.md §2.3), in the evaluation image:
the Python rule (tools/m4_rules.py) on the board's dumped INT8 outputs of the manifest's first 50 images,
against the C++ rule's detections of the same run (tools/m4_accuracy.cpp pass, --dets-in-record).

  MSYS_NO_PATHCONV=1 python tools/eval/pc_run.py --kind check --name rules_check_yolov8n -- \\
      tools/m4_rules_check.py --board-record results/check/<UTC>_rules_yolov8n

For each dumped image, the two lists must have the same count and order, the same class, the score within
1e-6 and the box within 1e-3 px; no pair is excepted, except a difference caused by an IoU within 1e-6
of 0.7, which is reported. The candidates' count and the tie flag must also agree. Writes
<record>/rules_check.csv: the counts, the largest differences, how many values are equal bit for bit,
the IoUs near 0.7, and valid. Exit 0 when valid, 1 when not, 2 for unusable input.
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
    values) of one image: the Python Result against the C++ rows."""
    if len(rows) != len(result.cls) or any(int(r[2]) != c for r, c in zip(rows, result.cls.tolist())):
        return False, float("inf"), float("inf"), 0, 0
    want = np.array([[r[3], r[4], r[5], r[6], r[7]] for r in rows], np.float32).reshape(-1, 5)
    got = np.concatenate([result.score[:, None], result.box], axis=1).astype(np.float32).reshape(-1, 5)
    score_diff = float(np.abs(got[:, 0] - want[:, 0]).max()) if len(rows) else 0.0
    box_diff = float(np.abs(got[:, 1:] - want[:, 1:]).max()) if len(rows) else 0.0
    ok = score_diff <= SCORE_TOL and box_diff <= BOX_TOL
    return ok, score_diff, box_diff, int((got == want).sum()), int(want.size)


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
    except (InputError, OSError, KeyError, ValueError) as e:
        print(f"m4_rules_check: {e}", file=sys.stderr)
        return 2
    by_image = {}
    for r in dets:
        by_image.setdefault(int(r[0]), []).append(r)
    per_image = {int(r[0]): r for r in images}

    equal = differ = excepted = bits = values = 0
    worst_score = worst_box = 0.0
    near_all, problems = [], []
    for image_id in manifest[:IMAGES]:
        near = [] if model == "yolov8n" else None
        result = rule(outputs_of(board / "dump", image_id, attrs), near=near) if near is not None \
            else rule(outputs_of(board / "dump", image_id, attrs))
        ok, s, b, eq_bits, n = compare(result, by_image.get(image_id, []))
        cpp = per_image.get(image_id)
        counts_ok = cpp is not None and int(cpp[1]) == result.candidates and int(cpp[3]) == int(result.tied)
        near_all += [(image_id, k, v) for k, v in (near or [])]
        if ok and counts_ok:
            equal += 1
        elif near:
            excepted += 1  # §2.3: reported, not a failure
            problems.append(f"{image_id:012d}: differs with {len(near)} IoU(s) within 1e-6 of 0.7 (excepted)")
        else:
            differ += 1
            problems.append(f"{image_id:012d}: " + ("the lists differ" if not ok else "the candidates or the tie differ"))
        worst_score, worst_box = max(worst_score, s), max(worst_box, b)
        bits, values = bits + eq_bits, values + n

    valid = differ == 0
    rows = [("board_record", board.name), ("model", model), ("images", IMAGES), ("lists_equal", equal),
            ("lists_differing", differ), ("lists_excepted_near_0.7", excepted),
            ("largest_score_difference", f"{worst_score:.3g}"), ("largest_box_difference_px", f"{worst_box:.3g}"),
            ("values_equal_bit_for_bit", bits), ("values", values), ("ious_within_1e-6_of_0.7", len(near_all)),
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
