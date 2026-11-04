#!/usr/bin/env python3
"""Tests for tools/m1_stages.py: the stages of one run, over the frames in the window.

Usage: python3 tests/test_m1_stages.py
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import m1_stages as stages  # noqa: E402

SMOKE = ROOT / "results" / "check" / "20261103T070702Z_main_smoke"


class RunStages(unittest.TestCase):
    def test_the_frames_in_the_window(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / "summary.csv").write_text("# warmup_s: 20\nkey,value\nwindow_s,120\n", encoding="utf-8")
            cols = ["t0_ns", "t1_ns", "t2a_ns", "t2b_ns", "t2c_ns", "t3_ns", "t4_ns"]
            rows = []
            for k in range(100):  # in the window: 1 ms in Q1, 2 preparing, 3 in Q2, 0 binding, 15 running, 1 post
                t0 = 30 * 10**9 + k * 10**8
                rows.append([t0, t0 + 10**6, t0 + 3 * 10**6, t0 + 6 * 10**6, t0 + 6 * 10**6, t0 + 21 * 10**6,
                             t0 + 22 * 10**6])
            rows.append([0, 10**9, 2 * 10**9, 3 * 10**9, 3 * 10**9, 4 * 10**9, 5 * 10**9])  # t4 in the warm-up
            (d / "frames.csv").write_text(",".join(cols) + "\n" + "".join(",".join(map(str, r)) + "\n" for r in rows),
                                          encoding="utf-8")
            s = stages.run_stages(d)
            self.assertEqual({k: round(v[1], 6) for k, v in s.items()},
                             {"Q1 wait": 1.0, "prepare": 2.0, "Q2 wait": 3.0, "bind": 0.0, "run": 15.0, "post": 1.0,
                              "total": 22.0})

    @unittest.skipUnless(SMOKE.exists(), "needs the smoke session in results/check")
    def test_the_total_is_m1_reports_latency(self):
        import m1_report as rep
        idle = rep.read_idle(SMOKE)
        for name in ("01_main_B_1ch_r1", "02_main_C_1ch_r1"):
            self.assertAlmostEqual(stages.run_stages(SMOKE / name)["total"][1],
                                   rep.run_metrics(SMOKE / name, idle)["latency_p99_ms"], places=6)


if __name__ == "__main__":
    unittest.main()
