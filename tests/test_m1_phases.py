#!/usr/bin/env python3
"""Tests for tools/m1_phases.py: a channel's phase, the spread and drift of a run's phases, one run, and
the report.

Usage: python3 tests/test_m1_phases.py
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import m1_phases as phases  # noqa: E402
import test_m1_report as tr  # noqa: E402

MS = 10**6


def run_dir(d, offsets_ms, period_ns=200 * MS, start_ns=30 * 10**9, n=100):
    """A run with one channel per offset: n frames each, every period_ns from start_ns + offset, 20 ms from
    t0 to t4 with 3 ms in Q2, and one frame before the window at another phase."""
    d.mkdir(parents=True, exist_ok=True)
    (d / "summary.csv").write_text("# warmup_s: 20\nkey,value\nwindow_s,120\n", encoding="utf-8")
    rows = [(1, 19 * 10**9 + 150 * MS)]
    rows += [(ch, int(round(start_ns + k * period_ns + off * MS))) for ch, off in enumerate(offsets_ms, 1)
             for k in range(n)]
    (d / "frames.csv").write_text("channel,t0_ns,t2a_ns,t2b_ns,t4_ns\n" + "".join(
        f"{ch},{t0},{t0 + 5 * MS},{t0 + 8 * MS},{t0 + 20 * MS}\n" for ch, t0 in rows), encoding="utf-8")
    return d


class Phase(unittest.TestCase):
    def test_the_circular_mean(self):
        self.assertAlmostEqual(phases.phase([k * 200 * MS + 30 * MS for k in range(50)], 200 * MS) / MS, 30.0)
        # across the period's end: 199 and 1 ms average to 0, not to 100
        p = phases.phase([1000 * MS + 199 * MS, 2000 * MS + 1 * MS], 200 * MS) / MS
        self.assertAlmostEqual(min(p, 200 - p), 0.0, places=6)
        self.assertLess(phases.phase([200 * MS - 1, 400 * MS + 1], 200 * MS), 200 * MS)  # in [0, period)

    def test_the_spread(self):
        self.assertAlmostEqual(phases.spread([10, 20, 190], 200), 30)  # 190 to 10 across the end, then 20
        self.assertAlmostEqual(phases.spread([100, 10, 20], 200), 90)
        self.assertEqual(phases.spread([5, 5], 200), 0)  # together
        self.assertEqual(phases.spread([42], 200), 0)

    def test_the_drift(self):
        steady = [k * 200 * MS + 30 * MS for k in range(60)]
        self.assertAlmostEqual(phases.drift(steady, 200 * MS), 0.0)
        # the last 10 frames 5 ms later; across the period's end too
        self.assertAlmostEqual(phases.drift(steady[:50] + [t + 5 * MS for t in steady[50:]], 200 * MS) / MS, 5.0)
        late = [k * 200 * MS + 198 * MS for k in range(50)] + [k * 200 * MS + 3 * MS for k in range(50, 60)]
        self.assertAlmostEqual(phases.drift(late, 200 * MS) / MS, 5.0)
        # a middle sixth 4 ms later, the last back in place; in any order
        middle = steady[:20] + [t + 4 * MS for t in steady[20:30]] + steady[30:]
        self.assertAlmostEqual(phases.drift(middle[::-1], 200 * MS) / MS, 4.0)


class RunPhases(unittest.TestCase):
    def test_one_run(self):
        with tempfile.TemporaryDirectory() as t:
            s, dr, p50, p99, q2 = phases.run_phases(run_dir(Path(t), (10, 40)), 5, 2)
            self.assertAlmostEqual(s, 30.0, places=6)
            self.assertAlmostEqual(dr, 0.0, places=6)
            self.assertAlmostEqual(p50, 20.0)
            self.assertAlmostEqual(p99, 20.0)
            self.assertAlmostEqual(q2, 3.0)

    def test_the_auxiliary_period_late_in_a_run(self):
        # 30 fps: a period of 33.3 ms that is not a whole number of ns, with t0 near the window's end
        with tempfile.TemporaryDirectory() as t:
            d = run_dir(Path(t), (10, 20, 12), period_ns=1e9 / 30, start_ns=130 * 10**9, n=200)
            s, dr, *_ = phases.run_phases(d, 30, 3)
            self.assertAlmostEqual(s, 10.0, places=4)
            self.assertAlmostEqual(dr, 0.0, places=4)

    def test_a_channel_without_frames_in_the_window(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ValueError):
                phases.run_phases(run_dir(Path(t), (10, 40)), 5, 3)


class Report(unittest.TestCase):
    def test_the_runs_it_lists(self):
        # main: 1-channel runs are left out, and so is C at 2 channels, invalid twice; other sweeps have no run
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            main = tr.session(root / "results" / "comparison", "20261103T000000Z_main",
                              [("01_main_B_1ch_r1", "B", 1, 1, 1, "1", ""), ("02_main_C_1ch_r1", "C", 1, 1, 1, "1", ""),
                               ("03_main_B_2ch_r1", "B", 2, 1, 1, "1", ""), ("04_main_C_2ch_r1", "C", 2, 1, 1, "0", "x"),
                               ("04_main_C_2ch_r1_repeat", "C", 2, 1, 2, "0", "y")])
            run_dir(main / "03_main_B_2ch_r1", (10, 40))
            text = phases.report(root)
            table = text[text.index("## main\n"):text.index("## aux\n")]
            self.assertIn("| 2 | B | 1 | 30.0 | 0.0 | 20.0 | 20.0 | 3.0 |", table)
            self.assertEqual(table.count("\n| "), 2)  # the header and that one run
            self.assertIn("## reference\n\nNo run yet.", text)
            self.assertIn("## k4 (M1.1, A12)\n\nNo run yet.", text)


if __name__ == "__main__":
    unittest.main()
