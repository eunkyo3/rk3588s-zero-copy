#!/usr/bin/env python3
"""Tests for tools/m1_report.py: the verdict rules of HYPOTHESES, the statistics and the runs that count.

Usage: python3 tests/test_m1_report.py
"""
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import m1_report as rep  # noqa: E402

SMOKE = ROOT / "results" / "check" / "20261103T070702Z_main_smoke"


def run(cpu=10.0, system=12.0, p99=50.0, drop=0.0, fps=5.0):
    return {"process_cpu_s": cpu, "system_cpu_idle_sub_s": system, "latency_p99_ms": p99, "drop_rate": drop,
            "inference_fps_min": fps}


class Stats(unittest.TestCase):
    def test_mean_sd_cv(self):
        n, mean, sd, cv = rep.stats([1.0, 2.0, 3.0])
        self.assertEqual((n, mean, sd), (3, 2.0, 1.0))  # the sample standard deviation
        self.assertAlmostEqual(cv, 0.5)
        self.assertEqual(rep.stats([4.0])[:2], (1, 4.0))
        self.assertTrue(math.isnan(rep.stats([4.0])[2]))
        self.assertEqual(rep.stats([])[0], 0)

    def test_p99_is_numpys_linear_interpolation(self):
        values = [float(x) for x in range(1, 101)]  # (n - 1) * 0.99 = 98.01 -> 99 + 0.01
        self.assertAlmostEqual(rep.p99(values), 99.01)


class H1(unittest.TestCase):
    def test_adopted_and_rejected(self):
        b, c = [run(cpu=10)] * 3, [run(cpu=7.5)] * 3
        self.assertEqual((rep.h1(b, c)["spec"], rep.h1(b, c)["practical"]), ("adopted", "adopted"))
        c = [run(cpu=7.6)] * 3  # a 24% saving
        self.assertEqual((rep.h1(b, c)["spec"], rep.h1(b, c)["practical"]), ("rejected", "rejected"))

    def test_fewer_than_3_valid_runs(self):
        v = rep.h1([run(cpu=10)] * 2, [run(cpu=5)] * 3)
        self.assertEqual((v["spec"], v["flagged"], v["practical"]), ("adopted", True, "indeterminate"))
        v = rep.h1([], [run()] * 3)
        self.assertEqual((v["spec"], v["practical"]), ("indeterminate", "indeterminate"))

    def test_a_slow_channel_makes_the_practical_verdict_indeterminate(self):
        v = rep.h1([run(cpu=10)] * 3, [run(cpu=5), run(cpu=5), run(cpu=5, fps=4.74)])
        self.assertEqual((v["spec"], v["practical"]), ("adopted", "indeterminate"))

    def test_mixed(self):
        self.assertTrue(rep.h1([run(cpu=10, system=10)] * 3, [run(cpu=5, system=11)] * 3)["mixed"])
        self.assertFalse(rep.h1([run(cpu=10, system=10)] * 3, [run(cpu=5, system=9)] * 3)["mixed"])


class H2(unittest.TestCase):
    def reps(self, *p99s, drop=0.0):
        return {r: run(p99=p, drop=drop) for r, p in enumerate(p99s, 1)}

    def test_adopted_needs_every_repetition(self):
        b = self.reps(100, 100, 100)
        self.assertEqual(rep.h2(b, self.reps(90, 90, 90))["practical"], "adopted")
        v = rep.h2(b, self.reps(80, 80, 91))  # mean lower, but repetition 3 is not 10% lower
        self.assertEqual((v["spec"], v["practical"]), ("adopted", "indeterminate"))

    def test_the_drop_guard(self):
        b = self.reps(100, 100, 100, drop=0.010)
        self.assertEqual(rep.h2(b, self.reps(80, 80, 80, drop=0.020))["practical"], "adopted")  # 1 point: allowed
        self.assertEqual(rep.h2(b, self.reps(80, 80, 80, drop=0.0201))["practical"], "indeterminate")

    def test_rejected(self):
        v = rep.h2(self.reps(100, 100, 100), self.reps(100, 100, 100))
        self.assertEqual((v["spec"], v["practical"]), ("rejected", "rejected"))

    def test_fewer_than_3_valid_runs(self):
        v = rep.h2(self.reps(100, 100), self.reps(50, 50, 50))
        self.assertEqual((v["spec"], v["flagged"], v["practical"]), ("adopted", True, "indeterminate"))


class H3(unittest.TestCase):
    def test_verdicts(self):
        self.assertEqual(rep.h3({"B": 10, "C": 12}), {"n_B": 10, "n_C": 12, "spec": "adopted", "practical": "adopted"})
        self.assertEqual(rep.h3({"B": 12, "C": 10})["practical"], "rejected")
        v = rep.h3({"B": 31, "C": 32})
        self.assertEqual((v["spec"], v["practical"]), ("adopted", "indeterminate"))
        self.assertEqual(rep.h3({"B": 30, "C": 32})["practical"], "adopted")  # only C at the ceiling
        self.assertEqual(rep.h3(None)["spec"], "not done")


class CountedRuns(unittest.TestCase):
    COLS = ["seq", "name", "phase", "variant", "channels", "condition", "repetition", "attempt", "exit", "valid",
            "passed", "reasons", "warnings", "trip_samples", "gate_soc_mc", "clock_offset_ms", "start_utc",
            "end_utc", "replaces"]

    def session(self, root, name, rows):
        d = Path(root) / name
        d.mkdir()
        lines = ["\t".join(self.COLS)]
        for seq, (rname, variant, channels, repetition, attempt, valid, reasons) in enumerate(rows, 1):
            row = dict.fromkeys(self.COLS, "")
            row.update(seq=str(seq), name=rname, phase="main", variant=variant, channels=str(channels),
                       repetition=str(repetition), attempt=str(attempt), valid=valid, reasons=reasons)
            lines.append("\t".join(row[c] for c in self.COLS))
        (d / "runs.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return d

    def test_the_session_that_completed_a_repetition(self):
        with tempfile.TemporaryDirectory() as t:
            s1 = self.session(t, "20261104T000000Z_main", [("01_main_B_8ch_r1", "B", 8, 1, 1, "1", "")])
            s2 = self.session(t, "20261104T040000Z_main", [
                ("01_main_B_8ch_r1", "B", 8, 1, 1, "1", ""),
                ("02_main_C_8ch_r1", "C", 8, 1, 1, "0", "clock below fixed: npu_hz"),
                ("02_main_C_8ch_r1_repeat", "C", 8, 1, 2, "0", "clock below fixed: npu_hz")])
            runs = rep.counted_runs([s1, s2], "main")
            self.assertEqual(runs[(8, 1, "B")]["session"], "20261104T040000Z_main")  # s1's B is replaced
            self.assertEqual((runs[(8, 1, "C")]["status"], runs[(8, 1, "C")]["name"], runs[(8, 1, "C")]["reasons"]),
                             ("invalid x2", "02_main_C_8ch_r1_repeat", "clock below fixed: npu_hz"))
            self.assertEqual(len(runs), 2)

    def test_an_incomplete_repetition_counts_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            s1 = self.session(t, "20261104T000000Z_main", [("01_main_B_8ch_r1", "B", 8, 1, 1, "1", "")])
            self.assertEqual(rep.counted_runs([s1], "main"), {})


class OneRun(unittest.TestCase):
    @unittest.skipUnless(SMOKE.exists(), "needs the smoke session in results/check")
    def test_p99_from_frames_equals_the_benchs(self):
        idle = rep.read_idle(SMOKE)
        for name in ("01_main_B_1ch_r1", "02_main_C_1ch_r1"):
            m = rep.run_metrics(SMOKE / name, idle)
            self.assertAlmostEqual(m["latency_p99_ms"], m["latency_p99_bench_ms"], places=3)
            # 5 s window; the idle baseline is 60 s here, scaled down to 5 s.
            expected = m["system_cpu_total_s"] - float(idle["system_cpu_total_s"]) * 5 / float(idle["window_s"])
            self.assertAlmostEqual(m["system_cpu_idle_sub_s"], expected)


if __name__ == "__main__":
    unittest.main()
