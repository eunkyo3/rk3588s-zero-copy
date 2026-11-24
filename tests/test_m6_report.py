#!/usr/bin/env python3
"""Tests for tools/m6_report.py (prereg/M6.md §2, §3, §4 and §6): the three hypotheses' verdicts, mixed labels,
practical-verdict exceptions, missing runs and CV flags; the runs that count across sessions; the paired
intervals; Ct's values; and the earlier milestones' values read from their records. Fixtures are written to a
temporary directory.

Usage: python3 tests/test_m6_report.py
"""
import math
import statistics
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import m6_report as m6r  # noqa: E402
import stream_session as ss  # noqa: E402

WARMUP, WINDOW = 20, 120
IDLE = {"window_s": "120.0", "schedstat_cpu_total_s": "1.200", "schedstat_cpu0-3_s": "0.600",
        "schedstat_cpu4-5_s": "0.300", "schedstat_cpu6-7_s": "0.300", "system_cpu_total_s": "1.000",
        "system_cpu0-3_s": "0.500", "system_cpu4-5_s": "0.250", "system_cpu6-7_s": "0.250"}
CPU = {"B": 40.0, "Bd": 39.0, "Rc": 22.0, "C": 20.0, "By": 34.0, "Cp": 19.0, "Cpf": 24.0, "Ct": 20.1}


def v(cpu, sched=None, fps=5.0, drop=0.0, run="s/r"):
    """A run's values as hypothesis() reads them."""
    return {"process_cpu_s": cpu, "schedstat_total_s": cpu + 2.0 if sched is None else sched,
            "inference_fps_min": fps, "drop_rate": drop, "run": run}


def arms(**cpu):
    """{arm: three runs at the given process CPU}."""
    return {a: [v(x, run=f"s/{a}_r{r}") for r in (1, 2, 3)] for a, x in cpu.items()}


def keyed(path, header, rows):
    path.write_text("".join(f"# {k}: {x}\n" for k, x in header.items()) + "key,value\n"
                    + "".join(f"{k},{x}\n" for k, x in rows.items()), encoding="utf-8")


def table(path, header, columns, rows):
    path.write_text("".join(f"# {k}: {x}\n" for k, x in header.items()) + ",".join(columns) + "\n"
                    + "".join(",".join(str(x) for x in r) + "\n" for r in rows), encoding="utf-8")


def write_run(d, arm, rep, cpu, sched=None, fps=5.0, drops=0, rknn=False, window=WINDOW):
    """One run of bench_main: summary.csv, channels.csv, threads.csv and frames.csv, as src/core/report.cpp writes
    them. Every channel has two frames in the window and one before it."""
    d.mkdir(parents=True)
    header = {"variant": arm, "repetition": rep, "warmup_s": WARMUP, "duration_s": window}
    sched = cpu + 2.0 if sched is None else sched
    sampled = 8 * 5 * window
    keyed(d / "summary.csv", header, {
        "window_s": window, "process_cpu_s": f"{cpu:.3f}", "process_cpu_a76_s": f"{cpu - 0.1:.3f}",
        "process_cpu_a55_s": "0.100", "process_cpu_unattributed_s": "0.000",
        "system_cpu0-3_s": "1.000", "system_cpu4-5_s": f"{cpu / 2:.3f}", "system_cpu6-7_s": f"{cpu / 2:.3f}",
        "system_cpu_total_s": f"{cpu + 1:.3f}", "schedstat_version": "15", "schedstat_cpu0-3_s": "1.000",
        "schedstat_cpu4-5_s": f"{(sched - 1) / 2:.3f}", "schedstat_cpu6-7_s": f"{(sched - 1) / 2:.3f}",
        "schedstat_cpu_total_s": f"{sched:.3f}", "sampled_in_window": sampled, "q1_drops_in_window": drops,
        "drop_rate": f"{drops / sampled:.5f}", "temp_max_mc": 40000, "rss_window_max_kb": 240000})
    processed = round(fps * window)
    table(d / "channels.csv", header, ["channel", "decoded_in_window", "decode_fps", "drift_max_s", "decoder_drops",
                                       "sampled_in_window", "q1_drops_in_window", "processed_in_window",
                                       "inference_fps", "ended_early"],
          [(ch, 30 * window, "30.000", "0.000", 0, 5 * window, 0, processed, f"{processed / window:.3f}", 0)
           for ch in range(1, 9)])
    table(d / "threads.csv", header, ["tid", "comm", "affinity", "cpu_s_in_window"],
          [(1, "pre-0", '"4-7"', f"{cpu * 0.5:.3f}"), (2, "npu-0", '"4-7"', f"{cpu * 0.2:.3f}"),
           (3, "dec-1", '"4-7"', f"{cpu * 0.2:.3f}"), (4, "mpp_dec_parser", '"4-7"', f"{cpu * 0.1 - 0.1:.3f}"),
           (5, "metrics", '"0"', "0.100")])
    frames = []
    for ch in range(1, 9):
        for k, t0 in enumerate((10**9, (WARMUP + 1) * 10**9, (WARMUP + 1) * 10**9 + 2 * 10**8)):
            t0 += ch * 10**6
            t1, t2a = t0 + 10**6, t0 + 6 * 10**6
            t2b, t2c = t2a + 10**6, t2a + 12 * 10**5
            t3 = t2c + 15 * 10**6 + (k == 2) * 10**6
            t4 = t3 + 4 * 10**5
            row = [ch, 6 * k, 18000 * k, t0, t1, t2a, t2b, t2c, t3, t4, 500, 0, 0, 3]
            frames.append(row + ([800000 + 1000 * ch] if rknn else []))
    table(d / "frames.csv", header, ["channel", "seq", "pts", "t0_ns", "t1_ns", "t2a_ns", "t2b_ns", "t2c_ns", "t3_ns",
                                     "t4_ns", "slot_wait_ns", "ctx", "slot", "detections"]
          + (["rknn_cpu_ns"] if rknn else []), frames)


RUNS_COLUMNS = ["seq", "name", "phase", "variant", "channels", "condition", "repetition", "attempt", "exit", "valid",
                "passed", "reasons"]


def write_session(d, runs, cpu=None):
    """A session of sweep m6. runs: [(arm, repetition, attempt, valid, run options)], in run order; a run's
    directory is written unless it is a lost attempt (valid None: in started.tsv only)."""
    d.mkdir(parents=True)
    keyed(d / "idle_baseline.csv", {"run_kind": "m6"}, IDLE)
    lines, started, seq, last = ["\t".join(RUNS_COLUMNS)], ["\t".join(RUNS_COLUMNS[:8])], 0, None
    for arm, rep, attempt, valid, options in runs:
        seq += not (attempt == 2 and last == (arm, rep))  # a repeat right after its run keeps the run's seq
        last = (arm, rep)
        name = f"{seq:02d}_m6_{arm}_8ch_r{rep}" + ("_repeat" if attempt == 2 else "")
        started.append("\t".join(map(str, [seq, name, "m6", arm, 8, "main", rep, attempt])))
        if valid is None:
            continue
        write_run(d / name, arm, rep, options.get("cpu", (cpu or CPU)[arm]),
                  **{k: x for k, x in options.items() if k != "cpu"})
        lines.append("\t".join(map(str, [seq, name, "m6", arm, 8, "main", rep, attempt, 0, int(valid), "",
                                         "" if valid else "clock below fixed: npu_hz"])))
    (d / "runs.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (d / "started.tsv").write_text("\n".join(started) + "\n", encoding="utf-8")
    return d


def repetition(rep, **options):
    """Every arm of a repetition, valid, in M6 §4's order; options: {arm: run options}."""
    return [(a, rep, 1, True, options.get(a, {})) for a in ss.M6_ORDERS[rep]]


class Thresholds(unittest.TestCase):
    def check(self, name, runs, expected):
        h = m6r.hypothesis(name, runs)
        self.assertEqual((h["spec"], h["practical"]), (expected, expected), h["flags"])
        return h

    def test_h1(self):
        h = self.check("M6-H1", arms(B=40.0, Rc=22.0, C=20.0), "adopted")  # exactly 0.90
        self.assertEqual(h["value"], 0.9)
        self.assertAlmostEqual(self.check("M6-H1", arms(B=40.0, Rc=21.0, C=20.0), "adopted")["value"], 0.95)
        self.assertAlmostEqual(self.check("M6-H1", arms(B=40.0, Rc=22.2, C=20.0), "rejected")["value"], 0.89)

    def test_h2(self):
        self.assertEqual(self.check("M6-H2", arms(Cp=20.0, Cpf=24.0), "adopted")["value"], 0.2)  # exactly 0.20
        self.check("M6-H2", arms(Cp=20.0, Cpf=25.0), "adopted")
        self.assertAlmostEqual(self.check("M6-H2", arms(Cp=20.0, Cpf=23.9), "rejected")["value"], 0.195)

    def test_h3(self):
        self.assertEqual(self.check("M6-H3", arms(B=40.0, By=36.0), "adopted")["value"], 0.1)  # exactly 0.10
        self.check("M6-H3", arms(B=40.0, By=30.0), "adopted")
        self.assertAlmostEqual(self.check("M6-H3", arms(B=40.0, By=36.1), "rejected")["value"], 0.0975)

    def test_means_of_the_runs(self):
        runs = arms(B=40.0, C=20.0)
        runs["Rc"] = [v(21.0), v(22.0), v(23.0)]
        h = m6r.hypothesis("M6-H1", runs)
        self.assertEqual(h["means"], {"B": 40.0, "Rc": 22.0, "C": 20.0})
        self.assertEqual(h["n"], {"B": 3, "Rc": 3, "C": 3})


class Mixed(unittest.TestCase):
    def with_sched(self, runs, **sched):
        for a, x in sched.items():
            for r in runs[a]:
                r["schedstat_total_s"] = x
        return runs

    def test_h1_ratio_on_the_other_side(self):
        runs = self.with_sched(arms(B=40.0, Rc=21.0, C=20.0), B=42.0, Rc=25.0, C=22.0)  # 0.95, schedstat 0.85
        h = m6r.hypothesis("M6-H1", runs)
        self.assertEqual((h["spec"], h["mixed"]), ("adopted", True))
        self.assertAlmostEqual(h["secondary_value"], 0.85)
        runs = self.with_sched(arms(B=40.0, Rc=21.0, C=20.0), B=42.0, Rc=24.0, C=22.0)  # schedstat exactly 0.90
        self.assertIs(m6r.hypothesis("M6-H1", runs)["mixed"], False)
        runs = self.with_sched(arms(B=40.0, Rc=23.0, C=20.0), B=42.0, Rc=24.0, C=22.0)  # 0.85, schedstat 0.90
        h = m6r.hypothesis("M6-H1", runs)
        self.assertEqual((h["spec"], h["mixed"]), ("rejected", True))

    def test_h1_undefined_on_schedstat(self):
        runs = self.with_sched(arms(B=40.0, Rc=21.0, C=20.0), B=30.0, Rc=25.0, C=30.0)
        h = m6r.hypothesis("M6-H1", runs)
        self.assertIsNone(h["mixed"])
        self.assertEqual(h["spec"], "adopted")
        self.assertTrue(any("undefined on /proc/schedstat" in f for f in h["flags"]))

    def test_h2_and_h3_sign(self):
        runs = self.with_sched(arms(Cp=20.0, Cpf=25.0), Cp=22.0, Cpf=21.5)
        self.assertIs(m6r.hypothesis("M6-H2", runs)["mixed"], True)
        runs = self.with_sched(arms(Cp=20.0, Cpf=25.0), Cp=22.0, Cpf=23.0)
        self.assertIs(m6r.hypothesis("M6-H2", runs)["mixed"], False)
        runs = self.with_sched(arms(B=40.0, By=35.0), B=42.0, By=43.0)
        self.assertIs(m6r.hypothesis("M6-H3", runs)["mixed"], True)
        runs = self.with_sched(arms(B=40.0, By=35.0), B=42.0, By=41.9)  # same sign, however small
        self.assertIs(m6r.hypothesis("M6-H3", runs)["mixed"], False)


class Practical(unittest.TestCase):
    def test_a_slow_channel(self):
        runs = arms(B=40.0, Rc=21.0, C=20.0, Cp=19.0)
        runs["Rc"][1]["inference_fps_min"] = 4.74
        h = m6r.hypothesis("M6-H1", runs)
        self.assertEqual((h["spec"], h["practical"]), ("adopted", "indeterminate"))
        self.assertIn("a channel below 4.75 fps in s/Rc_r2", "; ".join(h["flags"]))
        runs["Rc"][1]["inference_fps_min"] = 4.75  # at 4.75: not below
        self.assertEqual(m6r.hypothesis("M6-H1", runs)["practical"], "adopted")
        runs["Cp"][0]["inference_fps_min"] = 3.0  # an arm M6-H1 does not name
        self.assertEqual(m6r.hypothesis("M6-H1", runs)["practical"], "adopted")

    def test_drop_rates_either_way(self):
        for named, other in (("Rc", "B"), ("B", "Rc"), ("C", "Rc")):
            with self.subTest(higher=named):
                runs = arms(B=40.0, Rc=21.0, C=20.0)
                for r in runs[named]:
                    r["drop_rate"] = 0.011
                h = m6r.hypothesis("M6-H1", runs)
                self.assertEqual((h["spec"], h["practical"]), ("adopted", "indeterminate"))
                self.assertTrue(any("drop rates" in f for f in h["flags"]))
        runs = arms(Cp=20.0, Cpf=25.0)
        for r in runs["Cp"]:  # the arm with less CPU drops more: indeterminate too
            r["drop_rate"] = 0.02
        self.assertEqual(m6r.hypothesis("M6-H2", runs)["practical"], "indeterminate")
        for r in runs["Cpf"]:  # exactly 1 point apart: not more than 1
            r["drop_rate"] = 0.01
        self.assertEqual(m6r.hypothesis("M6-H2", runs)["practical"], "adopted")


class Missing(unittest.TestCase):
    def test_two_of_three(self):
        runs = arms(B=40.0, C=20.0)
        runs["Rc"] = [v(21.0), v(23.0)]
        h = m6r.hypothesis("M6-H1", runs)
        self.assertEqual((h["spec"], h["practical"]), ("adopted", "indeterminate"))
        self.assertEqual(h["value"], 0.9)  # from the two runs that exist
        self.assertTrue(any("fewer than 3 runs that count (Rc 2)" in f for f in h["flags"]))

    def test_none(self):
        runs = arms(B=40.0, C=20.0)
        runs["Rc"] = []
        h = m6r.hypothesis("M6-H1", runs)
        self.assertEqual((h["spec"], h["practical"], h["value"]), ("indeterminate", "indeterminate", None))
        self.assertTrue(any("no run that counts of Rc" in f for f in h["flags"]))

    def test_by_not_run(self):
        with mock.patch.object(ss, "M6_NOT_RUN", ("By",)):
            runs = arms(B=40.0, Rc=21.0, C=20.0)
            h = m6r.hypothesis("M6-H3", runs)
            self.assertEqual((h["spec"], h["practical"]), ("indeterminate", "indeterminate"))
            self.assertIn("By is not run", h["flags"][0])
            self.assertIn("equivalence results", h["flags"][0])
            self.assertEqual(m6r.hypothesis("M6-H1", runs)["spec"], "adopted")
            self.assertNotIn("By", ss.m6_arms())
            self.assertEqual(m6r.order_position(3, "B"), 1)  # M6 §4: the order of the others stands
            with tempfile.TemporaryDirectory() as t:
                d = write_session(Path(t) / "20261201T000000Z_m6",
                                  [(a, r, 1, True, {}) for r in (1, 2, 3) for a in ss.sweep_plan("m6")[r - 1][2]])
                c = m6r.collect([d])
                self.assertEqual(c["complete"], [1, 2, 3])
                self.assertNotIn("By", c["per_arm"])
                text = m6r.report([d], refs={})
            self.assertIn("Arms not run (§5): By", text)
            self.assertIn("| B − By | – | not run: By |", text)


class CvFlags(unittest.TestCase):
    def flags(self, name, **cpu):
        runs = {a: [v(x) for x in xs] for a, xs in cpu.items()}
        return [f for f in m6r.hypothesis(name, runs)["flags"] if f.startswith("CV")]

    def test_limits(self):
        self.assertEqual(self.flags("M6-H1", B=[37.0, 40.0, 43.0], Rc=[21.0] * 3, C=[20.0] * 3),
                         ["CV of B's process CPU 7.50%, above 5% (§4)"])  # B: above 5%, not 10%
        self.assertEqual(self.flags("M6-H1", B=[40.0] * 3, Rc=[20.5, 22.0, 23.5], C=[20.0] * 3), [])  # Rc 6.8%
        self.assertEqual(self.flags("M6-H1", B=[40.0] * 3, Rc=[19.0, 22.0, 25.0], C=[20.0] * 3),
                         ["CV of Rc's process CPU 13.64%, above 10% (§4)"])
        self.assertEqual(self.flags("M6-H3", B=[35.0, 40.0, 45.0], By=[33.0, 34.0, 35.0]),
                         ["CV of B's process CPU 12.50%, above 10% (§4)",
                          "CV of B's process CPU 12.50%, above 5% (§4)"])
        self.assertEqual(self.flags("M6-H3", B=[40.0] * 3, By=[32.0, 34.0, 36.0]),
                         ["CV of By's process CPU 5.88%, above 5% (§4)"])
        self.assertEqual(self.flags("M6-H2", Cp=[18.0, 20.0, 22.0], Cpf=[24.0] * 3), [])  # Cp exactly 10%


class Values(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_percentile(self):
        self.assertEqual(m6r.percentile([4.0, 1.0, 3.0, 2.0], 50), 2.5)
        self.assertAlmostEqual(m6r.percentile([1.0, 2.0, 3.0, 4.0], 99), 3.97)
        self.assertIsNone(m6r.percentile([], 50))

    def test_less_idle(self):
        values = {"schedstat_cpu_total_s": 30.0, "schedstat_cpu0-3_s": 2.0, "system_cpu_total_s": 25.0}
        out = m6r.less_idle(values, IDLE, 60.0)  # the baseline scaled from 120 s to the window's 60 s
        self.assertAlmostEqual(out["schedstat_total_s"], 30.0 - 0.6)
        self.assertAlmostEqual(out["schedstat_0-3_s"], 2.0 - 0.3)
        self.assertAlmostEqual(out["system_total_s"], 25.0 - 0.5)
        self.assertIsNone(out["schedstat_4-5_s"])
        self.assertIsNone(m6r.less_idle(values, {}, 120.0)["schedstat_total_s"])

    def test_run_values(self):
        write_run(self.d / "r", "B", 1, 40.0, sched=43.0, fps=4.74, drops=24)
        x = m6r.run_values(self.d / "r", IDLE)
        self.assertEqual(x["process_cpu_s"], 40.0)
        self.assertAlmostEqual(x["schedstat_total_s"], 43.0 - 1.2)
        self.assertAlmostEqual(x["inference_fps_min"], 569 / 120)
        self.assertEqual(x["drop_rate"], 24 / 4800)
        self.assertAlmostEqual(x["threads_pre_s"], 20.0)
        self.assertAlmostEqual(x["threads_dec_s"], 8.0 + 3.9)
        self.assertAlmostEqual(x["threads_rest_s"], 0.1)
        self.assertAlmostEqual(x["threads_remainder_s"], 0.0)
        # two frames per channel in the window; the one before it is left out
        self.assertAlmostEqual(x["latency_p50_ms"], 23.1)  # t4 − t0: 22.6 and 23.6 ms, 8 frames each
        self.assertAlmostEqual(x["stage_run_p50_ms"], 15.5)
        self.assertAlmostEqual(x["stage_prepare_p99_ms"], 5.0)
        self.assertNotIn("rknn_run_calls", x)
        self.assertAlmostEqual(x["arrival_spread_ms"], 7.0)  # channels 1 ms apart: 1 to 8 ms
        self.assertAlmostEqual(x["arrival_drift_ms"], 0.0)

    def test_ct(self):
        write_run(self.d / "ct", "Ct", 1, 20.0, rknn=True)
        x = m6r.run_values(self.d / "ct", IDLE)
        cpu_ns = 2 * sum(800000 + 1000 * ch for ch in range(1, 9))  # the 16 frames whose t4 is in the window
        self.assertEqual(x["rknn_run_calls"], 16)
        self.assertAlmostEqual(x["rknn_run_cpu_s"], cpu_ns / 1e9)
        self.assertAlmostEqual(x["rknn_run_cpu_share"], cpu_ns / 1e9 / 20.0)
        self.assertAlmostEqual(x["rknn_run_cpu_mean_ms"], 0.8045)
        self.assertAlmostEqual(x["rknn_run_wall_mean_ms"], 15.5)  # t3 − t2c: 15 and 16 ms

    def test_phases(self):
        period = 2e8
        self.assertAlmostEqual(m6r.phase([5e7, 2e8 + 5e7, 4e8 + 5e7], period), 5e7)
        self.assertAlmostEqual(m6r.spread([1e6, 1.99e8], period), 2e6)  # across the circle's end
        self.assertAlmostEqual(m6r.drift([k * period + (1e6 if k < 6 else 3e6) for k in range(12)], period), 2e6)
        self.assertEqual([len(p) for p in m6r.split(list(range(14)), 6)], [3, 3, 2, 2, 2, 2])


class Sweep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "results" / "m6"

    def tearDown(self):
        self.tmp.cleanup()

    def test_redone_repetition(self):
        # the first session ends inside repetition 2, during its Cp run; the next one reruns the whole repetition,
        # Cp with its repeat (stream_session.sweep_state), then runs repetition 3
        first = write_session(self.root / "20261201T000000Z_m6", repetition(1) + [
            ("Ct", 2, 1, True, {"cpu": 30.0}), ("Cpf", 2, 1, True, {}), ("Cp", 2, 1, None, {})])
        second = write_session(self.root / "20261202T000000Z_m6", [
            (a, 2, 2 if a == "Cp" else 1, True, {}) for a in ss.M6_ORDERS[2]] + repetition(3))
        c = m6r.collect([first, second])
        self.assertEqual(c["complete"], [1, 2, 3])
        self.assertEqual(c["counting"][(1, "B")]["run"], "20261201T000000Z_m6/01_m6_B_8ch_r1")
        self.assertEqual(c["counting"][(2, "B")]["run"], "20261202T000000Z_m6/08_m6_B_8ch_r2")
        self.assertEqual(c["counting"][(2, "Cp")]["run"], "20261202T000000Z_m6/03_m6_Cp_8ch_r2_repeat")
        self.assertEqual(c["counting"][(2, "Ct")]["process_cpu_s"], 20.1)  # not the first session's 30.0
        first_r2 = [e for e in c["every"] if e["session"] == first.name and e["repetition"] == 2]
        self.assertEqual(len(first_r2), 3)  # kept, and reported, but not counted
        self.assertFalse(any(e["counts"] for e in first_r2))
        lost = [e for e in first_r2 if e["lost"]]
        self.assertEqual([e["reasons"] for e in lost], ["the session ended during the run"])
        self.assertEqual(len(c["per_arm"]["B"]), 3)
        self.assertEqual(m6r.hypothesis("M6-H1", c["per_arm"])["spec"], "adopted")
        self.assertEqual(m6r.session_dirs(second), ([first, second], False))
        self.assertEqual(m6r.session_dirs(first), ([first], False))

    def test_invalid_x2_counts_as_missing(self):
        r2 = [x for a, *rest in repetition(2)
              for x in ([(a, 2, 1, False, {}), (a, 2, 2, False, {})] if a == "Rc" else [(a, *rest)])]
        d = write_session(self.root / "20261201T000000Z_m6", repetition(1) + r2 + repetition(3))
        c = m6r.collect([d])
        self.assertEqual(c["complete"], [1, 2, 3])
        self.assertIsNone(c["counting"][(2, "Rc")])
        self.assertEqual(len(c["per_arm"]["Rc"]), 2)
        h = m6r.hypothesis("M6-H1", c["per_arm"])
        self.assertEqual((h["spec"], h["practical"]), ("adopted", "indeterminate"))
        repeat = [e for e in c["every"] if e["name"].endswith("_Rc_8ch_r2_repeat")]
        self.assertEqual([(e["valid"], e["counts"], e["values"]["process_cpu_s"]) for e in repeat],
                         [(False, False, 22.0)])  # an invalid run's values are reported too
        r2_row = m6r.counting_section(c)[5]
        self.assertTrue(r2_row.startswith("| r2 | "), r2_row)
        self.assertIn("| invalid ×2 |", r2_row)
        self.assertEqual(repeat[0]["seq"], "14")  # the repeat keeps its run's place in the session
        rows = [x for x in m6r.every_run_section(c) if "_Rc_8ch_r2" in x]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all("| Rc | 2 |" in x and "| 14 | 6 | no | no | 22.000 |" in x
                            and x.endswith("| clock below fixed: npu_hz |") for x in rows), rows)

    def test_positions(self):
        d = write_session(self.root / "20261201T000000Z_m6", repetition(1) + repetition(2) + repetition(3))
        c = m6r.collect([d])
        self.assertEqual([c["counting"][(r, "B")]["seq"] for r in (1, 2, 3)], ["1", "16", "18"])
        self.assertEqual([e["position"] for e in c["every"] if e["variant"] == "B"], [1, 8, 2])

    def test_paired_interval(self):
        by_rep = {(1, "B"): {"x": 41.0}, (1, "C"): {"x": 40.0}, (2, "B"): {"x": 42.0}, (2, "C"): {"x": 40.0},
                  (3, "B"): {"x": 44.0}, (3, "C"): {"x": 40.0}}
        d, mean, half = m6r.paired(by_rep, "B", "C", "x")
        self.assertEqual(d, {1: 1.0, 2: 2.0, 3: 4.0})
        self.assertAlmostEqual(mean, 7 / 3)
        # sd = sqrt(7 / 3); half-width t(0.975, 2) × sd / sqrt(3) = 4.302653 × sqrt(7) / 3 = 3.794583
        self.assertAlmostEqual(half, 4.302652729911275 * math.sqrt(7) / 3)
        self.assertAlmostEqual(half, 3.794583, places=6)
        by_rep[(2, "C")] = None  # an invalid ×2 run: that repetition has no pair
        d, mean, half = m6r.paired(by_rep, "B", "C", "x")
        self.assertEqual(d, {1: 1.0, 3: 4.0})
        self.assertAlmostEqual(half, 12.706204736174698 * statistics.stdev([1.0, 4.0]) / math.sqrt(2))
        by_rep[(3, "C")] = None
        self.assertEqual(m6r.paired(by_rep, "B", "C", "x"), ({1: 1.0}, 1.0, None))  # one pair: no interval
        self.assertEqual(m6r.paired(by_rep, "B", "Rc", "x"), ({}, None, None))

    def test_report(self):
        cpu = dict(CPU, Ct=20.5)
        d = write_session(self.root / "20261201T000000Z_m6", repetition(1, Ct={"rknn": True})
                          + repetition(2, Ct={"rknn": True}) + repetition(3, Ct={"rknn": True}), cpu)
        out = Path(self.tmp.name) / "M6_tables.md"
        with mock.patch.object(m6r, "references", return_value={}):
            self.assertEqual(m6r.main(["--session", str(d), "--out", str(out)]), 0)
        text = out.read_text(encoding="utf-8")
        self.assertIn("The sweep is complete", text)
        self.assertIn("| M6-H1: (B − Rc) / (B − C) ≥ 0.90 | B 40.000 (3), Rc 22.000 (3), C 20.000 (3) | 0.9000 | 0.90 "
                      "| adopted | adopted |", text)
        self.assertIn("| M6-H2: (Cpf − Cp) / Cp ≥ 0.20 | Cp 19.000 (3), Cpf 24.000 (3) | 0.2632 |", text)
        self.assertIn("| M6-H3: (B − By) / B ≥ 0.10 | B 40.000 (3), By 34.000 (3) | 0.1500 |", text)
        self.assertIn("| B − Rc | 18.000 | +90.00% |", text)
        self.assertIn("| (B − Bd) − (Rc − C) | -1.000 | -5.00% |", text)
        self.assertIn("Ct − C, process CPU, from the means: 0.500 s", text)
        self.assertIn("| Calls | 16 ± 0 (3) |", text)
        self.assertIn("| Ct − C | Process CPU (s) | 0.500 | 0.500 | 0.500 | 0.500 | 0.500 to 0.500 s |", text)

    def test_smoke_and_errors(self):
        smoke = write_session(Path(self.tmp.name) / "results" / "check" / "20261201T000000Z_m6_smoke",
                              repetition(1))
        out = Path(self.tmp.name) / "smoke.md"
        with mock.patch.object(m6r, "references", return_value={}):
            self.assertEqual(m6r.main(["--session", str(smoke), "--out", str(out)]), 0)
            self.assertIn("**A smoke test:**", out.read_text(encoding="utf-8"))
            self.assertEqual(m6r.main(["--session", str(self.root / "20261201T000000Z_m3_after")]), 2)
            other = self.root / "20261201T000000Z_m3_after"
            other.mkdir(parents=True)
            self.assertEqual(m6r.main(["--session", str(other)]), 2)
            partial = write_session(self.root / "20261202T000000Z_m6", repetition(1))
            text = m6r.report(m6r.session_dirs(partial)[0], refs={})
        self.assertIn("**No outcome yet:** 1 of 3 repetitions are complete.", text)


class References(unittest.TestCase):
    def test_earlier_milestones_from_their_records(self):
        # M1_tables.md, M3_tables.md (m3_after) and docs/report/M5_summary.txt, at the published digits
        refs = m6r.references()
        cpu = {k: {a: round(m6r.mean_of(runs, "process_cpu_s"), 3) for a, runs in arms_.items()}
               for k, arms_ in refs.items()}
        self.assertEqual(cpu["M1"], {"B": 43.283, "C": 18.560})
        self.assertEqual(cpu["M3 m3_after"], {"B": 44.433, "C": 19.490})
        self.assertEqual(cpu["M5"], {"B": 43.733, "C": 19.250, "P": 26.907, "P4": 18.113})
        sched = {a: round(m6r.mean_of(refs["M5"][a], "schedstat_total_s"), 3) for a in ("B", "P", "P4")}
        self.assertEqual(sched, {"B": 45.671, "P": 30.134, "P4": 21.542})
        p, p4 = (m6r.mean_of(refs["M5"][a], "process_cpu_s") for a in ("P", "P4"))
        self.assertEqual(round((p - p4) / p4, 3), 0.485)  # M6 §2's 48.5%


if __name__ == "__main__":
    unittest.main()
