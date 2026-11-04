#!/usr/bin/env python3
"""Tests for tools/m1_phases.py: a channel's phase, the spread of a run's phases, and one run.

Usage: python3 tests/test_m1_phases.py
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import m1_phases as phases  # noqa: E402


class Phase(unittest.TestCase):
    def test_the_circular_mean(self):
        ms = 10**6
        self.assertAlmostEqual(phases.phase([k * 200 * ms + 30 * ms for k in range(50)], 200 * ms) / ms, 30.0)
        # across the period's end: 199 and 1 ms average to 0, not to 100
        p = phases.phase([1000 * ms + 199 * ms, 2000 * ms + 1 * ms], 200 * ms) / ms
        self.assertAlmostEqual(min(p, 200 - p), 0.0, places=6)

    def test_the_spread(self):
        self.assertAlmostEqual(phases.spread([10, 20, 190], 200), 30)  # 190 to 10 across the end, then 20
        self.assertAlmostEqual(phases.spread([100, 10, 20], 200), 90)
        self.assertEqual(phases.spread([5, 5], 200), 0)  # together
        self.assertEqual(phases.spread([42], 200), 0)

    def test_the_drift(self):
        ms = 10**6
        steady = [k * 200 * ms + 30 * ms for k in range(60)]
        self.assertAlmostEqual(phases.drift(steady, 200 * ms), 0.0)
        # the last 10 frames 5 ms later; across the period's end too
        self.assertAlmostEqual(phases.drift(steady[:50] + [t + 5 * ms for t in steady[50:]], 200 * ms) / ms, 5.0)
        late = [k * 200 * ms + 198 * ms for k in range(50)] + [k * 200 * ms + 3 * ms for k in range(50, 60)]
        self.assertAlmostEqual(phases.drift(late, 200 * ms) / ms, 5.0)


class RunPhases(unittest.TestCase):
    def test_one_run(self):
        # 5 fps a channel, window [20 s, 140 s) by t4: channel 1 at 10 ms in each 200 ms, channel 2 at 40 ms,
        # 20 ms from t0 to t4 with 3 ms in Q2; a frame before the window at another phase is left out.
        ms = 10**6
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / "summary.csv").write_text("# warmup_s: 20\nkey,value\nwindow_s,120\n", encoding="utf-8")
            rows = [(1, 19 * 10**9 + 150 * ms)]
            rows += [(ch, 30 * 10**9 + k * 200 * ms + off * ms) for ch, off in ((1, 10), (2, 40)) for k in range(100)]
            (d / "frames.csv").write_text("channel,t0_ns,t2a_ns,t2b_ns,t4_ns\n" + "".join(
                f"{ch},{t0},{t0 + 5 * ms},{t0 + 8 * ms},{t0 + 20 * ms}\n" for ch, t0 in rows), encoding="utf-8")
            s, dr, p50, p99, q2 = phases.run_phases(d, 5)
            self.assertAlmostEqual(s, 30.0, places=6)
            self.assertAlmostEqual(dr, 0.0, places=6)
            self.assertAlmostEqual(p50, 20.0)
            self.assertAlmostEqual(p99, 20.0)
            self.assertAlmostEqual(q2, 3.0)


if __name__ == "__main__":
    unittest.main()
