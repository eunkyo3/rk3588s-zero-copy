#!/usr/bin/env python3
"""Tests for tools/equivalence_check.py. The end-to-end tests need numpy, like the tool.

Usage: python3 tests/test_equivalence_check.py
"""
import io
import itertools
import json
import random
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import equivalence_check as eq  # noqa: E402


def box(cls, score, x1, x2, y1=0.0, y2=1.0):
    return [cls, score, x1, y1, x2, y2]


def next_f32_up(x):
    """The next float32 above a positive float32 x."""
    return struct.unpack("<f", struct.pack("<I", struct.unpack("<I", struct.pack("<f", x))[0] + 1))[0]


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

    def test_hungarian_differs_from_greedy(self):
        # Greedy takes s0-b0 (0.90) first and leaves s1-b1 (0.61), 1.51 in all; the Hungarian
        # matching takes s0-b1 (0.80) and s1-b0 (0.85), 1.65.
        side = [box(0, 0.9, 0, 10), box(0, 0.9, 1.35, 9)]
        base = [box(0, 0.9, 0, 9), box(0, 0.9, -2.5, 10)]
        self.assertAlmostEqual(eq.iou(side[0], base[0]), 0.9)
        self.assertAlmostEqual(eq.iou(side[0], base[1]), 0.8)
        self.assertAlmostEqual(eq.iou(side[1], base[0]), 0.85)
        self.assertAlmostEqual(eq.iou(side[1], base[1]), 0.612)
        self.assertEqual(sorted((i, j) for i, j, _ in eq.match(side, base)), [(0, 1), (1, 0)])


class FrameAgreement(unittest.TestCase):
    def agrees(self, side, base):
        return eq.frame_agrees(side, base, eq.match(side, base))

    def test_near_threshold_side_box_and_its_partner_are_not_counted(self):
        side = [box(0, 0.26, 0, 10), box(1, 0.9, 20, 30)]
        base = [box(0, 0.30, 0, 10), box(1, 0.9, 20, 30), box(1, 0.24, 40, 50)]
        self.assertTrue(self.agrees(side, base))

    def test_near_threshold_base_box_and_its_partner_are_not_counted(self):
        side = [box(0, 0.30, 0, 10)]
        base = [box(0, 0.25, 0, 10)]
        self.assertTrue(self.agrees(side, base))

    def test_partner_below_half_iou_is_still_counted(self):
        # The near box has no partner: IoU 0.33 forms no pair, so the side box counts alone.
        side = [box(0, 0.9, 0, 10)]
        base = [box(0, 0.25, 5, 15)]
        self.assertEqual(eq.match(side, base), [])
        self.assertFalse(self.agrees(side, base))

    def test_extra_box_disagrees(self):
        side = [box(0, 0.9, 0, 10)]
        base = [box(0, 0.9, 0, 10), box(0, 0.5, 40, 50)]
        self.assertFalse(self.agrees(side, base))

    def test_band_edges_after_the_json_round_trip(self):
        # The dump writes scores with %.9g; the band must hold for float32 0.23 and 0.27 as read back.
        scores = {"low": eq.f32(0.23), "high": eq.f32(0.27), "above": next_f32_up(eq.f32(0.27))}
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "b.jsonl"
            dets = ", ".join("[0, %.9g, 0, 0, 1, 1]" % s for s in scores.values())
            path.write_text(json.dumps({"header": {"variant": "B", "run_kind": "equivalence"}}) +
                            '\n{"frame": 0, "pts": 0, "detections": [%s]}\n' % dets, encoding="utf-8")
            _, frames = eq.load(path, "B")
        near = [eq.near_threshold(d) for d in frames[0][2]]
        self.assertEqual(near, [True, True, False])


class EndToEnd(unittest.TestCase):
    H, W, BAND = 8, 8, [0, 2, 8, 4]
    N = 4
    A = box(0, 0.9, 0, 4, 2, 6)
    X = box(1, 0.8, 5, 7, 2, 4)

    def write(self, d, variant, frames, values, **header):
        import numpy as np

        h = {"variant": variant, "run_kind": "equivalence", "model_sha256": "m", "source": "v.mp4",
             "code_commit": "c", "amendment": "A6", "model_w": self.W, "model_h": self.H, "band": self.BAND,
             "labels": ["a", "b"], "preprocessing": variant, "tensors": f"{variant.lower()}.u8"}
        h.update(header)
        lines = [json.dumps({"header": h})]
        lines += [json.dumps({"frame": i, "pts": 3000 * i, "detections": det}) for i, det in enumerate(frames)]
        (d / f"{variant.lower()}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        tensors = []
        for v in values:
            t = np.full((self.H, self.W, 3), 114, np.uint8)
            x, y, w, hh = self.BAND
            t[y:y + hh, x:x + w] = v
            tensors.append(t)
        np.stack(tensors).tofile(d / f"{variant.lower()}.u8")

    def check(self, b=None, c=None, r=None, b_t=None, c_t=None, r_t=None, frames=None):
        """Writes a case (by default C = R = B in detections; B 100, C 101, R 103 in pixels) and runs
        the check. Returns (exit code, output, A5 conditions or None)."""
        base = [[self.A] for _ in range(self.N)]
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            self.write(d, "B", b or base, b_t or [100] * self.N)
            self.write(d, "C", c or base, c_t or [101] * self.N)
            self.write(d, "R", r or base, r_t or [103] * self.N)
            return self.run_check(d, frames or self.N)

    def run_check(self, d, frames):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = eq.main(["--b", str(d / "b.jsonl"), "--c", str(d / "c.jsonl"), "--r", str(d / "r.jsonl"),
                            "--frames", str(frames), "--json", str(d / "out.json")])
        summary = json.loads((d / "out.json").read_text()) if (d / "out.json").exists() else None
        return code, out.getvalue() + err.getvalue(), summary

    def assert_only_fails(self, result, condition):
        code, out, summary = result
        self.assertEqual(code, 1, out)
        self.assertEqual([k for k, v in summary["a5"].items() if not v], [condition], out)

    def test_pass_when_c_is_closer_to_b_than_r(self):
        code, out, summary = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("verdict (A5): PASS, not on the registered input", out)
        self.assertFalse(summary["registered_input"])
        self.assertAlmostEqual(summary["tensor"]["C"]["mae"], 1.0)
        self.assertAlmostEqual(summary["tensor"]["R"]["mae"], 3.0)

    def test_mae_alone(self):
        # C off by 2 everywhere; R off by 7 in one frame: R has the lower MAE, C the higher minimum PSNR.
        self.assert_only_fails(self.check(c_t=[102] * 4, r_t=[100, 100, 100, 107]), "tensor_mae")

    def test_min_psnr_alone(self):
        self.assert_only_fails(self.check(c_t=[100, 100, 100, 107], r_t=[102] * 4), "tensor_min_psnr")

    def test_border_alone(self):
        import numpy as np

        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            base = [[self.A] for _ in range(self.N)]
            self.write(d, "B", base, [100] * self.N)
            self.write(d, "C", base, [101] * self.N)
            self.write(d, "R", base, [103] * self.N)
            c = np.fromfile(d / "c.u8", np.uint8)
            c[0] = 0  # the first border element of the first frame
            c.tofile(d / "c.u8")
            self.assert_only_fails(self.run_check(d, self.N), "border_114_b_and_c")

    def test_pairs_alone(self):
        worse = box(0, 0.85, 0, 4, 2, 6)  # confidence difference 0.05
        self.assert_only_fails(self.check(c=[[worse] for _ in range(self.N)]), "a_pairs_within_both_thresholds")

    def test_frames_alone(self):
        # C moves box X from frame 1 to frame 0: two frames disagree, the totals and the pairs do not change.
        b = [[self.A], [self.A, self.X], [self.A], [self.A]]
        c = [[self.A, self.X], [self.A], [self.A], [self.A]]
        self.assert_only_fails(self.check(b=b, c=c, r=b), "b_frames_agreeing")

    def test_total_boxes_alone(self):
        # An extra C box near the threshold is left out of the counts, so frames still agree.
        c = [[self.A, box(1, 0.26, 5, 7, 2, 4)]] + [[self.A] for _ in range(self.N - 1)]
        self.assert_only_fails(self.check(c=c), "c_total_boxes")

    def test_inconsistent_inputs(self):
        code, out, _ = self.check(frames=self.N + 1)
        self.assertEqual(code, 2, out)
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            base = [[self.A] for _ in range(self.N)]
            self.write(d, "B", base, [100] * self.N)
            self.write(d, "C", base, [101] * self.N)
            self.write(d, "R", base, [103] * self.N, model_sha256="other")
            code, out, _ = self.run_check(d, self.N)
            self.assertEqual(code, 2, out)
            self.assertIn("model_sha256", out)

    def test_crash_is_not_a_fail(self):
        with mock.patch.object(eq, "tensor_stats", side_effect=RuntimeError("boom")):
            code, out, _ = self.check()
        self.assertEqual(code, 2, out)
        self.assertIn("RuntimeError: boom", out)


if __name__ == "__main__":
    unittest.main()
