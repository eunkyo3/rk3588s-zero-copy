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
        # Window [20 s, 140 s) by t4. In it: 1 ms in Q1, 2 preparing, 3 in Q2, 0 binding, 10 to 19 running,
        # 1 post, the first frame ending exactly at the window's start. Outside it, frames that ran 1 s:
        # one ending just before the window, one at its end, one after it.
        def frame(t4, run_ms):
            d = [1, 2, 3, 0, run_ms, 1]  # ms per stage, t0 to t4
            t = [t4 - sum(d) * 10**6]
            for ms in d:
                t.append(t[-1] + ms * 10**6)
            return t

        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / "summary.csv").write_text("# warmup_s: 20\nkey,value\nwindow_s,120\n", encoding="utf-8")
            cols = ["t0_ns", "t1_ns", "t2a_ns", "t2b_ns", "t2c_ns", "t3_ns", "t4_ns"]
            rows = [frame(20 * 10**9, 10)] + [frame(30 * 10**9 + k * 10**8, 10 + k) for k in range(1, 10)]
            rows += [frame(20 * 10**9 - 1, 1000), frame(140 * 10**9, 1000), frame(150 * 10**9, 1000)]
            (d / "frames.csv").write_text(",".join(cols) + "\n" + "".join(",".join(map(str, r)) + "\n" for r in rows),
                                          encoding="utf-8")
            s = stages.run_stages(d)
            # numpy's linear percentiles of 10, 11, ..., 19: p50 14.5, p99 18.91
            self.assertEqual({k: (round(v[0], 6), round(v[1], 6)) for k, v in s.items()},
                             {"Q1 wait": (1.0, 1.0), "prepare": (2.0, 2.0), "Q2 wait": (3.0, 3.0), "bind": (0.0, 0.0),
                              "run": (14.5, 18.91), "post": (1.0, 1.0), "total": (21.5, 25.91)})

    @unittest.skipUnless(SMOKE.exists(), "needs the smoke session in results/check")
    def test_the_total_is_m1_reports_latency(self):
        import m1_report as rep
        idle = rep.read_idle(SMOKE)
        for name in ("01_main_B_1ch_r1", "02_main_C_1ch_r1"):
            self.assertAlmostEqual(stages.run_stages(SMOKE / name)["total"][1],
                                   rep.run_metrics(SMOKE / name, idle)["latency_p99_ms"], places=6)


if __name__ == "__main__":
    unittest.main()
