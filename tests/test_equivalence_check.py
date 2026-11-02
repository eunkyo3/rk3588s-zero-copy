#!/usr/bin/env python3
"""Tests for tools/equivalence_check.py. Needs numpy. Usage: python3 tests/test_equivalence_check.py"""
import io
import itertools
import json
import random
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import equivalence_check as eq  # noqa: E402

import numpy as np  # noqa: E402


def box(cls, score, x1, x2, y1=0.0, y2=1.0):
    return [cls, score, x1, y1, x2, y2]


def brute_force(weights):
    n, m = len(weights), len(weights[0])
    if n <= m:
        return max(sum(weights[i][c] for i, c in enumerate(cols)) for cols in itertools.permutations(range(m), n))
    return max(sum(weights[r][j] for j, r in enumerate(rows)) for rows in itertools.permutations(range(n), m))


class Assignment(unittest.TestCase):
    def test_matches_brute_force(self):
        rng = random.Random(1)
        for _ in range(500):
            n, m = rng.randint(1, 5), rng.randint(1, 5)
            w = [[rng.choice([0.0, rng.random()]) for _ in range(m)] for _ in range(n)]
            a = eq.assignment(w)
            self.assertEqual(len(a), min(n, m))
            self.assertEqual(len(set(a.values())), len(a))
            self.assertAlmostEqual(sum(w[i][j] for i, j in a.items()), brute_force(w), places=9)

    def test_empty(self):
        self.assertEqual(eq.assignment([]), {})
        self.assertEqual(eq.assignment([[]]), {})


class Match(unittest.TestCase):
    def test_iou_below_half_counts_as_zero(self):
        # IoU(s0, b0) = 0.6; s0-b1 and s1-b0 are 0.449. Summing raw IoU would prefer the two 0.449
        # pairs and then drop both; with IoU < 0.5 at 0, s0-b0 is matched.
        side = [box(0, 0.9, 0, 10), box(0, 0.9, 6.3, 16.3)]
        base = [box(0, 0.9, 2.5, 12.5), box(0, 0.9, -3.8, 6.2)]
        self.assertAlmostEqual(eq.iou(side[0], base[0]), 0.6)
        self.assertLess(eq.iou(side[0], base[1]), 0.5)
        self.assertLess(eq.iou(side[1], base[0]), 0.5)
        pairs = eq.match(side, base)
        self.assertEqual([(i, j) for i, j, _ in pairs], [(0, 0)])

    def test_per_class(self):
        side = [box(1, 0.9, 0, 10)]
        base = [box(2, 0.9, 0, 10)]
        self.assertEqual(eq.match(side, base), [])

    def test_maximises_summed_iou(self):
        side = [box(0, 0.9, 0, 10), box(0, 0.9, 1, 11)]
        base = [box(0, 0.9, 1, 11), box(0, 0.9, 0, 10)]
        self.assertEqual(sorted((i, j) for i, j, _ in eq.match(side, base)), [(0, 1), (1, 0)])


class FrameAgreement(unittest.TestCase):
    def test_near_threshold_boxes_and_their_partners_are_not_counted(self):
        side = [box(0, 0.26, 0, 10), box(1, 0.9, 20, 30)]
        base = [box(0, 0.30, 0, 10), box(1, 0.9, 20, 30), box(1, 0.24, 40, 50)]
        self.assertTrue(eq.frame_agrees(side, base, eq.match(side, base)))

    def test_extra_box_disagrees(self):
        side = [box(0, 0.9, 0, 10)]
        base = [box(0, 0.9, 0, 10), box(0, 0.5, 40, 50)]
        self.assertFalse(eq.frame_agrees(side, base, eq.match(side, base)))

    def test_band_edges_as_float32(self):
        self.assertTrue(eq.near_threshold(box(0, eq.f32(0.27), 0, 1)))
        self.assertTrue(eq.near_threshold(box(0, eq.f32(0.23), 0, 1)))
        above = np.nextafter(np.float32(eq.f32(0.27)), np.float32(1)).item()
        self.assertFalse(eq.near_threshold(box(0, above, 0, 1)))


class EndToEnd(unittest.TestCase):
    H, W, BAND = 8, 8, [0, 2, 8, 4]

    def write(self, d, variant, frames, tensors, **header):
        h = {"variant": variant, "run_kind": "equivalence", "model_sha256": "m", "source": "v.mp4",
             "code_commit": "c", "amendment": "A6", "model_w": self.W, "model_h": self.H, "band": self.BAND,
             "labels": ["a", "b"], "preprocessing": variant, "tensors": f"{variant.lower()}.u8"}
        h.update(header)
        lines = [json.dumps({"header": h})]
        lines += [json.dumps({"frame": i, "pts": 3000 * i, "detections": det}) for i, det in enumerate(frames)]
        (d / f"{variant.lower()}.jsonl").write_text("\n".join(lines) + "\n")
        np.stack(tensors).astype(np.uint8).tofile(d / f"{variant.lower()}.u8")

    def tensor(self, value):
        t = np.full((self.H, self.W, 3), 114, np.uint8)
        x, y, w, h = self.BAND
        t[y:y + h, x:x + w] = value
        return t

    def run_check(self, d, n):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = eq.main(["--b", str(d / "b.jsonl"), "--c", str(d / "c.jsonl"), "--r", str(d / "r.jsonl"),
                            "--frames", str(n), "--json", str(d / "out.json")])
        return code, out.getvalue() + err.getvalue()

    def setup_case(self, d, c_value, r_value, c_extra=False):
        n = 4
        dets = [[box(0, 0.9, 0, 4, 2, 6)] for _ in range(n)]
        c_dets = [list(x) for x in dets]
        if c_extra:
            c_dets[0] = c_dets[0] + [box(1, 0.8, 5, 7, 2, 4)]
        self.write(d, "B", dets, [self.tensor(100)] * n)
        self.write(d, "C", c_dets, [self.tensor(c_value)] * n)
        self.write(d, "R", dets, [self.tensor(r_value)] * n)
        return n

    def test_pass_when_c_is_closer_to_b_than_r(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            n = self.setup_case(d, 101, 103)
            code, out = self.run_check(d, n)
            self.assertEqual(code, 0, out)
            self.assertIn("verdict (A5): PASS", out)
            self.assertIn("registered criteria", out)
            s = json.loads((d / "out.json").read_text())
            self.assertAlmostEqual(s["tensor"]["C"]["mae"], 1.0)
            self.assertAlmostEqual(s["tensor"]["R"]["mae"], 3.0)

    def test_fail_when_r_is_closer(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            n = self.setup_case(d, 103, 101)
            code, out = self.run_check(d, n)
            self.assertEqual(code, 1, out)
            self.assertIn("tensor MAE, C 3.0000 <= R 1.0000: FAIL", out)

    def test_fail_on_total_boxes(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            n = self.setup_case(d, 101, 103, c_extra=True)  # 5 boxes against 4
            code, out = self.run_check(d, n)
            self.assertEqual(code, 1, out)
            self.assertIn("(c) total boxes", out)

    def test_border_must_be_114(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            n = self.setup_case(d, 101, 103)
            bad = self.tensor(101)
            bad[0, 0, 0] = 0
            self.write(d, "C", [[box(0, 0.9, 0, 4, 2, 6)]] * n, [bad] * n)
            code, out = self.run_check(d, n)
            self.assertEqual(code, 1, out)
            self.assertIn("border 114 in every B and C tensor: FAIL", out)

    def test_inconsistent_inputs(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            n = self.setup_case(d, 101, 103)
            code, out = self.run_check(d, n + 1)
            self.assertEqual(code, 2, out)
            self.write(d, "R", [[]] * n, [self.tensor(103)] * n, model_sha256="other")
            code, out = self.run_check(d, n)
            self.assertEqual(code, 2, out)
            self.assertIn("model_sha256", out)


if __name__ == "__main__":
    unittest.main()
