#!/usr/bin/env python3
"""Tests for tools/m4_rules.py (prereg/M4.md §2.3), with numpy.

The test against the C++ yolov10_post needs the board's dumps of the exploration
(results/check/20261110T073848Z_m4_probe/*.i8, not committed); without them it is skipped.

Usage: python3 tests/test_m4_rules.py
"""
import csv
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).resolve().parent.parent / "tools"))
import m4_rules as mr  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PROBE = REPO / "results/check/20261110T073848Z_m4_probe"
GRIDS = (80, 40, 20)


def outputs(per_branch, scores=None, bins=None):
    """A model's float32 outputs: zero class scores and flat DFL bins unless scores[(b, cls, cell)] or
    bins[(b, cell)] (a (4, 16) array) say otherwise."""
    out = []
    for b, g in enumerate(GRIDS):
        box = np.zeros((1, 64, g, g), np.float32)
        cls = np.zeros((1, 80, g, g), np.float32)
        for (bb, c, cell), s in (scores or {}).items():
            if bb == b:
                cls[0, c].flat[cell] = s
        for (bb, cell), v in (bins or {}).items():
            if bb == b:
                box[0].reshape(64, -1)[:, cell] = np.asarray(v, np.float32).reshape(64)
        out += [box, cls] + ([np.zeros((1, 1, g, g), np.float32)] if per_branch == 3 else [])
    return out


def peaked(left, top, right, bottom):
    """DFL bins whose expectations are (about) these distances: a sharp peak at each."""
    v = np.full((4, 16), -30.0, np.float32)
    for side, d in enumerate((left, top, right, bottom)):
        v[side, d] = 30.0
    return v


class Basics(unittest.TestCase):
    def test_dequantize_is_the_cpp_expression(self):
        q = np.array([-128, -1, 0, 5, 127], np.int8)
        want = (q.astype(np.float32) - np.float32(-38)) * np.float32(0.114573859)
        np.testing.assert_array_equal(mr.dequantize(q, -38, 0.114573859), want)
        self.assertEqual(mr.dequantize(q, -38, 0.114573859).dtype, np.float32)

    def test_decode_cells(self):
        rng = np.random.default_rng(1)
        box = rng.normal(0, 3, (1, 64, 20, 20)).astype(np.float32)
        got = mr.decode_cells(box)
        bins = box[0].reshape(4, 16, 400).astype(np.float64)
        e = np.exp(bins - bins.max(axis=1, keepdims=True))
        d = (e * np.arange(16)[None, :, None]).sum(1) / e.sum(1)  # (4, cells)
        cell = np.arange(400)
        x, y = cell % 20 + 0.5, cell // 20 + 0.5
        want = np.stack([(x - d[0]) * 32, (y - d[1]) * 32, (x + d[2]) * 32, (y + d[3]) * 32], 1)
        np.testing.assert_allclose(got, want, atol=1e-3)
        self.assertEqual(got.dtype, np.float32)
        one = mr.decode_cells(outputs(2, bins={(0, 81): peaked(1, 2, 3, 4)})[0])[81]  # cell (1, 1) on stride 8
        np.testing.assert_allclose(one, [(1.5 - 1) * 8, (1.5 - 2) * 8, (1.5 + 3) * 8, (1.5 + 4) * 8], atol=1e-3)

    def test_g9_prints_float32_as_the_board_does(self):
        self.assertEqual(mr.g9(0.1), "0.100000001")  # float32's 0.1
        self.assertEqual(mr.g9(np.float32(306.725403)), "306.725403")
        self.assertEqual(mr.g9(640.0), "640")

    def test_layouts_are_checked(self):
        with self.assertRaisesRegex(ValueError, "expected 9 outputs"):
            mr.yolov8_rule(outputs(2))
        with self.assertRaisesRegex(ValueError, "expected 6 outputs"):
            mr.yolov10_rule(outputs(3))
        bad = outputs(2)
        bad[1] = bad[1][:, :79]
        with self.assertRaisesRegex(ValueError, "channels"):
            mr.yolov10_rule(bad)


class Order(unittest.TestCase):
    def test_conf_is_strict_and_ties_go_branch_cell_class(self):
        scores = {(2, 5, 3): 0.5, (0, 7, 10): 0.5, (0, 2, 10): 0.5, (1, 0, 0): 0.5, (0, 9, 4): 0.9,
                  (0, 1, 1): np.float32(0.001), (0, 1, 2): np.nextafter(np.float32(0.001), np.float32(1))}
        r = mr.yolov10_rule(outputs(2, scores))
        self.assertEqual(r.candidates, 6)  # 0.001 itself is not above conf
        self.assertEqual(r.cls.tolist(), [9, 2, 7, 0, 5, 1])  # 0.9; then 0.5 by branch, cell, class; then 0.001+
        self.assertFalse(r.tied)

    def test_max_det_and_its_tie(self):
        scores = {(0, c % 80, c // 80): 0.5 for c in range(301)}
        r = mr.yolov10_rule(outputs(2, scores))
        self.assertEqual((len(r.cls), r.candidates, r.tied), (300, 301, True))
        scores[(1, 0, 0)] = 0.4  # the 301st is now lower
        del scores[(0, 300 % 80, 300 // 80)]
        r = mr.yolov10_rule(outputs(2, scores))
        self.assertEqual((len(r.cls), r.tied), (300, False))
        self.assertEqual(len(mr.yolov10_rule(outputs(2, {(0, 0, 0): 0.5})).cls), 1)


class Yolov8(unittest.TestCase):
    def test_class_wise_greedy_nms(self):
        # Cells 0 and 1 on stride 8 have nearly the same box; cell 300 is far away.
        same = peaked(4, 4, 4, 4)
        bins = {(0, 0): same, (0, 1): same, (0, 300): same}
        scores = {(0, 3, 0): 0.9, (0, 3, 1): 0.8, (0, 4, 1): 0.7, (0, 3, 300): 0.6}
        r = mr.yolov8_rule(outputs(3, scores, bins))
        # Cell 1's class 3 overlaps cell 0's class 3 (IoU > 0.7): dropped. Its class 4 is another class: kept.
        self.assertEqual(list(zip(r.cls.tolist(), r.score.tolist())),
                         [(3, np.float32(0.9)), (4, np.float32(0.7)), (3, np.float32(0.6))])
        self.assertEqual(r.candidates, 4)

    def test_iou_is_yolov8_posts(self):
        kept = np.array([[0, 0, 10, 10], [5, 5, 15, 15], [20, 20, 21, 21]], np.float32)
        np.testing.assert_allclose(mr.iou_with(kept, np.array([0, 0, 10, 10], np.float32)),
                                   [1.0, 25 / 175, 0.0], rtol=1e-6)
        zero = np.zeros((1, 4), np.float32)
        self.assertEqual(mr.iou_with(zero, np.zeros(4, np.float32)).tolist(), [0.0])  # no area: 0, as the C++

    def test_the_30000_cap_and_300_kept(self):
        # 400 candidates of one class, each on its own far-apart cell of stride 8: none overlaps.
        bins = {(0, cell): peaked(0, 0, 0, 0) for cell in range(0, 6400, 16)}
        scores = {(0, 0, cell): np.float32(1 - cell / 1e5) for cell in range(0, 6400, 16)}
        r = mr.yolov8_rule(outputs(3, scores, bins))
        self.assertEqual((len(r.cls), r.candidates, r.tied), (300, 400, False))
        r = mr.yolov8_rule(outputs(3, scores, bins), max_nms=250)  # only the first 250 go to the NMS
        self.assertEqual(len(r.cls), 250)
        tied = {k: np.float32(0.5) for k in scores}
        self.assertTrue(mr.yolov8_rule(outputs(3, tied, bins)).tied)

    def test_score_sums_are_not_used(self):
        out = outputs(3, {(0, 1, 0): 0.5})
        out[2][...] = 0.0  # zero score sums would make yolov8_post drop the cell
        self.assertEqual(mr.yolov8_rule(out).cls.tolist(), [1])


@unittest.skipUnless((PROBE / "000000005001_out0.i8").exists(), "needs the exploration's board dumps (not committed)")
class AgainstTheBoard(unittest.TestCase):
    """yolov10_rule on the board's INT8 outputs of the exploration's 21 images, against C++ yolov10_post's
    detections.csv at conf 0.25 and 0.001: §2.3's tolerances, score within 1e-6 and box within 1e-3 px."""

    def test_yolov10_rule_equals_yolov10_post(self):
        attrs = dict(line.split(",", 1) for line in (PROBE / "m4_probe.csv").read_text().splitlines()
                     if not line.startswith("#") and "," in line)
        dims = [tuple(int(x) for x in attrs[f"output{k}_dims"].split("x")) for k in range(6)]
        cpp = {}
        with open(PROBE / "detections.csv") as f:
            for row in csv.DictReader(line for line in f if not line.startswith("#")):
                cpp.setdefault((row["image"], row["conf"]), []).append(row)
        self.assertEqual(len(cpp), 42)
        for (image, conf), rows in sorted(cpp.items()):
            outs = [mr.dequantize(np.fromfile(PROBE / f"{image}_out{k}.i8", np.int8).reshape(dims[k]),
                                  int(attrs[f"output{k}_zp"]), np.float32(attrs[f"output{k}_scale"]))
                    for k in range(6)]
            r = mr.yolov10_rule(outs, conf=np.float32(conf))
            self.assertEqual(len(r.cls), len(rows), (image, conf))
            self.assertEqual(r.cls.tolist(), [int(row["cls"]) for row in rows], (image, conf))
            np.testing.assert_allclose(r.score, [float(row["score"]) for row in rows], atol=1e-6, rtol=0)
            np.testing.assert_allclose(r.box, [[float(row[k]) for k in ("x1", "y1", "x2", "y2")] for row in rows],
                                       atol=1e-3, rtol=0)
            for row in rows:  # and the C++'s printed values are what g9 prints
                self.assertEqual(mr.g9(np.float32(row["score"])), row["score"])


if __name__ == "__main__":
    unittest.main()
