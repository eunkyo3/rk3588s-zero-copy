#!/usr/bin/env python3
"""Tests for tools/m1_report.py: the verdict rules of HYPOTHESES, the statistics, the runs that count
and the report.

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
COLS = ["seq", "name", "phase", "variant", "channels", "condition", "repetition", "attempt", "exit", "valid",
        "passed", "reasons", "warnings", "trip_samples", "gate_soc_mc", "clock_offset_ms", "start_utc", "end_utc",
        "replaces"]


def run(cpu=10.0, system=12.0, p99=50.0, drop=0.0, fps=5.0):
    return {"process_cpu_s": cpu, "system_cpu_total_minus_idle_s": system, "latency_p99_ms": p99, "drop_rate": drop,
            "inference_fps_min": fps}


def session(root, name, rows, started=()):
    """A session directory with runs.tsv rows (name, variant, channels, repetition, attempt, valid, reasons
    [, trip_samples]) and started.tsv rows (name, variant, channels, repetition, attempt) of lost attempts."""
    d = Path(root) / name
    d.mkdir(parents=True)
    lines = ["\t".join(COLS)]
    phase = name.split("_", 1)[1]
    for seq, r in enumerate(rows, 1):
        row = dict.fromkeys(COLS, "")
        row.update(seq=str(seq), name=r[0], phase=phase, variant=r[1], channels=str(r[2]), repetition=str(r[3]),
                   attempt=str(r[4]), valid=r[5], reasons=r[6], trip_samples=r[7] if len(r) > 7 else "0")
        lines.append("\t".join(row[c] for c in COLS))
    (d / "runs.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    s_cols = ["seq", "name", "phase", "variant", "channels", "condition", "repetition", "attempt", "start_utc"]
    s_lines = ["\t".join(s_cols)]
    for k, (n, v, ch, r, a) in enumerate(started, len(rows) + 1):
        s_lines.append("\t".join([str(k), n, phase, v, str(ch), "main", str(r), str(a), ""]))
    (d / "started.tsv").write_text("\n".join(s_lines) + "\n", encoding="utf-8")
    return d


def fake_run(d, cpu, latencies, fps=5.0, system=20.0):
    """A run directory with the files run_metrics reads: 20 s warm-up, 120 s window."""
    d.mkdir(parents=True)
    keys = {"window_s": "120", "process_cpu_s": cpu, "system_cpu_total_s": system, "latency_p50_ms": "10",
            "latency_p99_ms": "0", "drop_rate": "0.00000", "decoder_drops": "0", "drift_max_s": "0.000",
            "temp_max_mc": "50000"}
    keys.update({k: "1.0" for k in rep.SYSTEM_KEYS if k != "system_cpu_total_s"})
    (d / "summary.csv").write_text("# warmup_s: 20\nkey,value\n" + "".join(f"{k},{v}\n" for k, v in keys.items()),
                                   encoding="utf-8")
    (d / "channels.csv").write_text(f"channel,inference_fps\n1,{fps}\n2,5.0\n", encoding="utf-8")
    t0 = 25 * 10**9
    frames = "".join(f"1,{t0 + k * 10**9},{t0 + k * 10**9 + int(ms * 1e6)}\n" for k, ms in enumerate(latencies))
    (d / "frames.csv").write_text("channel,t0_ns,t4_ns\n" + frames, encoding="utf-8")


def idle_file(d, total=2.4):
    keys = {"window_s": "120.0"}
    keys.update({k: "0.1" for k in rep.SYSTEM_KEYS})
    keys["system_cpu_total_s"] = str(total)
    (d / "idle_baseline.csv").write_text("key,value\n" + "".join(f"{k},{v}\n" for k, v in keys.items()),
                                         encoding="utf-8")


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
        self.assertFalse(rep.h1([run(cpu=10, system=10)] * 3, [run(cpu=5, system=10)] * 3)["mixed"])  # zero: no sign


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
        self.assertEqual(rep.h2(b, self.reps(80, 80, 80, drop=0.02001))["practical"], "indeterminate")

    def test_rejected(self):
        v = rep.h2(self.reps(100, 100, 100), self.reps(100, 100, 100))
        self.assertEqual((v["spec"], v["practical"]), ("rejected", "rejected"))

    def test_fewer_than_3_or_no_valid_runs(self):
        v = rep.h2(self.reps(100, 100), self.reps(50, 50, 50))
        self.assertEqual((v["spec"], v["flagged"], v["practical"]), ("adopted", True, "indeterminate"))
        v = rep.h2({}, self.reps(50, 50, 50))
        self.assertEqual((v["spec"], v["practical"]), ("indeterminate", "indeterminate"))


class H3(unittest.TestCase):
    def test_verdicts(self):
        self.assertEqual(rep.h3({"B": 10, "C": 12}), {"n_B": 10, "n_C": 12, "spec": "adopted", "practical": "adopted"})
        self.assertEqual(rep.h3({"B": 12, "C": 10})["practical"], "rejected")
        v = rep.h3({"B": 31, "C": 32})
        self.assertEqual((v["spec"], v["practical"]), ("adopted", "indeterminate"))
        self.assertEqual(rep.h3({"B": 30, "C": 32})["practical"], "adopted")  # only C at the ceiling
        self.assertEqual(rep.h3(None)["spec"], "not done")


class CountedRuns(unittest.TestCase):
    def test_the_session_that_completed_a_repetition(self):
        with tempfile.TemporaryDirectory() as t:
            s1 = session(t, "20261104T000000Z_main", [("01_main_B_8ch_r1", "B", 8, 1, 1, "1", "")])
            s2 = session(t, "20261104T040000Z_main", [
                ("01_main_B_8ch_r1", "B", 8, 1, 1, "1", ""),
                ("02_main_C_8ch_r1", "C", 8, 1, 1, "0", "clock below fixed: npu_hz"),
                ("02_main_C_8ch_r1_repeat", "C", 8, 1, 2, "0", "bench exit -11")])
            runs, others = rep.counted_runs([s1, s2], "main")
            self.assertEqual(runs[(8, 1, "B")]["session"], "20261104T040000Z_main")
            self.assertEqual((runs[(8, 1, "C")]["status"], runs[(8, 1, "C")]["attempts"]),
                             ("invalid x2", [("20261104T040000Z_main/02_main_C_8ch_r1", "clock below fixed: npu_hz"),
                                             ("20261104T040000Z_main/02_main_C_8ch_r1_repeat", "bench exit -11")]))
            self.assertEqual(others, [("20261104T000000Z_main/01_main_B_8ch_r1", True, "")])  # replaced by s2

    def test_an_incomplete_repetition_and_a_lost_attempt(self):
        with tempfile.TemporaryDirectory() as t:
            s1 = session(t, "20261104T000000Z_main", [("01_main_B_8ch_r1", "B", 8, 1, 1, "1", "")],
                         started=[("02_main_C_8ch_r1", "C", 8, 1, 1)])
            runs, others = rep.counted_runs([s1], "main")
            self.assertEqual(runs, {})
            self.assertEqual(others, [("20261104T000000Z_main/01_main_B_8ch_r1", True, ""),
                                      ("20261104T000000Z_main/02_main_C_8ch_r1", False, rep.LOST)])


class OneRun(unittest.TestCase):
    @unittest.skipUnless(SMOKE.exists(), "needs the smoke session in results/check")
    def test_p99_from_frames_equals_the_benchs(self):
        idle = rep.read_idle(SMOKE)
        for name in ("01_main_B_1ch_r1", "02_main_C_1ch_r1"):
            m = rep.run_metrics(SMOKE / name, idle)
            self.assertAlmostEqual(m["latency_p99_ms"], m["latency_p99_bench_ms"], places=3)
            # 5 s window; the idle baseline is 60 s here, scaled down to 5 s.
            expected = m["system_cpu_total_s"] - float(idle["system_cpu_total_s"]) * 5 / float(idle["window_s"])
            self.assertAlmostEqual(m["system_cpu_total_minus_idle_s"], expected)


class Report(unittest.TestCase):
    def test_nothing_run_is_not_complete(self):
        with tempfile.TemporaryDirectory() as t:
            c = rep.collect(Path(t))
            self.assertEqual(len(c["missing"]), 42 + 30 + 6 + 1)  # every run of every sweep, and H3
            self.assertEqual(c["missing"][-1], "h3: not done")
            text = rep.report(c)
            self.assertIn("| H1, CPU at 8 channels | indeterminate | indeterminate |", text)
            self.assertIn("| H3, maximum channels | not done | not done |", text)
            self.assertIn("input path without CPU pixel copies", text)

    def test_a_report_from_sessions(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            rows, k = [], 0
            for r in (1, 2, 3):
                for v in (("B", "C") if r % 2 else ("C", "B")):
                    k += 1
                    rows.append((f"{k:02d}_main_{v}_8ch_r{r}", v, 8, r, 1, "1", "", "2" if k == 1 else "0"))
            rows.append(("07_main_B_1ch_r1", "B", 1, 1, 1, "0", "bench exit -11"))
            rows.append(("07_main_B_1ch_r1_repeat", "B", 1, 1, 2, "0", "clock below fixed: dmc_hz"))
            rows.append(("08_main_C_1ch_r1", "C", 1, 1, 1, "1", ""))
            s = session(root / "results" / "comparison", "20261104T000000Z_main", rows)
            idle_file(s)
            cpu = {"B": [10.0, 10.0, 10.0], "C": [5.0, 6.0, 10.0]}  # C's CV is above 10%
            for name, v, ch, r, *_ in rows:
                if not name.endswith("_repeat") and name != "07_main_B_1ch_r1":
                    fake_run(s / name, cpu[v][r - 1] if ch == 8 else 1.0, [10.0] * 99 + [20.0])
            c = rep.collect(root)
            self.assertNotIn("main 8 ch B r1", c["missing"])
            self.assertIn("main 1 ch B r2", c["missing"])
            m = c["data"]["main"][8]["C"]["valid"][1]
            self.assertAlmostEqual(m["latency_p99_ms"], 10.1)  # index 98.01 of 99 x 10 and one 20: 10 + 0.01 * 10
            self.assertAlmostEqual(m["system_cpu_total_minus_idle_s"], 20.0 - 2.4)
            text = rep.report(c)
            self.assertIn("B 10.000 s, C 7.000 s, saving 30.0%", text)
            self.assertIn("main 8 ch C: process CPU (s) CV", text)
            self.assertIn("- main 1 ch B r1: 20261104T000000Z_main/07_main_B_1ch_r1: bench exit -11; "
                          "20261104T000000Z_main/07_main_B_1ch_r1_repeat: clock below fixed: dmc_hz", text)
            self.assertIn("- main: 20261104T000000Z_main/01_main_B_8ch_r1: 2 samples", text)
            self.assertIn("## M1.1: 4K input (exploratory, A11)", text)
            self.assertTrue(text.endswith("No 4K session yet.\n"))

    def test_the_4k_section(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            main = session(root / "results" / "comparison", "20261104T000000Z_main",
                           [("01_main_B_2ch_r1", "B", 2, 1, 1, "1", ""), ("02_main_C_2ch_r1", "C", 2, 1, 1, "1", "")])
            idle_file(main)
            fake_run(main / "01_main_B_2ch_r1", 3.0, [10.0] * 100)
            fake_run(main / "02_main_C_2ch_r1", 1.5, [10.0] * 100)
            rows = [("01_4k_B_2ch_r1", "B", 2, 1, 1, "1", ""), ("02_4k_C_2ch_r1", "C", 2, 1, 1, "1", ""),
                    ("03_4k_C_2ch_r2", "C", 2, 2, 1, "1", ""), ("04_4k_B_2ch_r2", "B", 2, 2, 1, "0", "bench exit -11"),
                    ("04_4k_B_2ch_r2_repeat", "B", 2, 2, 2, "0", "decoder drops")]
            k4 = session(root / "results" / "m1.1", "20261104T050000Z_4k", rows)
            idle_file(k4)
            for name, v, _, r, *_ in rows[:3]:
                fake_run(k4 / name, {"B": 9.0, "C": 2.0 * r}[v], [10.0] * 100)
            c = rep.collect(root)
            self.assertFalse(any(m.startswith("4k") for m in c["missing"]))  # not part of M1's completeness
            self.assertIn("4k 1 ch B r1", c["4k"]["missing"])
            text = rep.report(c)
            section = text[text.index("## M1.1: 4K input"):]
            self.assertIn("| 2 | process CPU (s) | 9.000 ± – (1) | 3.000 ± 1.414 (2) | 3.000 ± – (1) | 1.500 ± – (1) |",
                          section)
            self.assertIn("- 4k 2 ch C: process CPU (s) CV 47.1%", section)  # in the 4K section's flags only
            self.assertNotIn("4k 2 ch C", text[:text.index("## M1.1: 4K input")])
            self.assertIn("### Invalid x2", section)
            self.assertIn("- 4k 2 ch B r2: 20261104T050000Z_4k/04_4k_B_2ch_r2: bench exit -11; "
                          "20261104T050000Z_4k/04_4k_B_2ch_r2_repeat: decoder drops", section)
            self.assertIn("Not complete: 4k 1 ch B r1", section)


if __name__ == "__main__":
    unittest.main()
