#!/usr/bin/env python3
"""Tests for tools/m6_report.py (prereg/M6.md §2, §3, §4, §5 and §6): the three hypotheses' verdicts in exact
arithmetic, mixed labels, practical-verdict exceptions (§2, and §5's for Cpf), missing runs, arms not run and CV
flags; the runs that count across sessions, superseded sessions and why a run does not count; partial records;
the paired intervals; Ct's values; and the earlier milestones' values read from their records. Fixtures are
written to a temporary directory.

Usage: python3 tests/test_m6_report.py
"""
import io
import math
import statistics
import sys
import tempfile
import unittest
from fractions import Fraction
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


def d(x):
    """A decimal as a record writes it, as the Fraction the tool reads."""
    return Fraction(str(x))


def v(cpu, sched=None, fps=5.0, drop=0.0, run="s/r"):
    """A run's values as hypothesis() reads them."""
    return {"process_cpu_s": d(cpu), "schedstat_total_s": d(cpu + 2.0 if sched is None else sched),
            "inference_fps_min": d(fps), "drop_rate": d(drop), "q1_drops_in_window": 0, "run": run}


def arms(**cpu):
    """{arm: three runs at the given process CPU}."""
    return {a: [v(x, run=f"s/{a}_r{r}") for r in (1, 2, 3)] for a, x in cpu.items()}


def keyed(path, header, rows):
    path.write_text("".join(f"# {k}: {x}\n" for k, x in header.items()) + "key,value\n"
                    + "".join(f"{k},{x}\n" for k, x in rows.items()), encoding="utf-8")


def table(path, header, columns, rows):
    path.write_text("".join(f"# {k}: {x}\n" for k, x in header.items()) + ",".join(columns) + "\n"
                    + "".join(",".join(str(x) for x in r) + "\n" for r in rows), encoding="utf-8")


def write_run(d_, arm, rep, cpu, sched=None, fps=5.0, drops=0, rknn=False, window=WINDOW, broken=None):
    """One run of bench_main: summary.csv, channels.csv, threads.csv and frames.csv, as src/core/report.cpp writes
    them. Every channel has two frames in the window and one before it. broken: "summary" leaves process_cpu_s
    out, "frames" cuts the last line of frames.csv short."""
    d_.mkdir(parents=True)
    header = {"variant": arm, "repetition": rep, "warmup_s": WARMUP, "duration_s": window}
    sched = cpu + 2.0 if sched is None else sched
    sampled = 8 * 5 * window
    summary = {
        "window_s": window, "process_cpu_s": f"{cpu:.3f}", "process_cpu_a76_s": f"{cpu - 0.1:.3f}",
        "process_cpu_a55_s": "0.100", "process_cpu_unattributed_s": "0.000",
        "system_cpu0-3_s": "1.000", "system_cpu4-5_s": f"{cpu / 2:.3f}", "system_cpu6-7_s": f"{cpu / 2:.3f}",
        "system_cpu_total_s": f"{cpu + 1:.3f}", "schedstat_version": "15", "schedstat_cpu0-3_s": "1.000",
        "schedstat_cpu4-5_s": f"{(sched - 1) / 2:.3f}", "schedstat_cpu6-7_s": f"{(sched - 1) / 2:.3f}",
        "schedstat_cpu_total_s": f"{sched:.3f}", "sampled_in_window": sampled, "q1_drops_in_window": drops,
        "drop_rate": f"{drops / sampled:.5f}", "temp_max_mc": 40000, "rss_window_max_kb": 240000}
    if broken == "summary":
        del summary["process_cpu_s"]
    keyed(d_ / "summary.csv", header, summary)
    processed = round(fps * window)
    table(d_ / "channels.csv", header, ["channel", "decoded_in_window", "decode_fps", "drift_max_s", "decoder_drops",
                                        "sampled_in_window", "q1_drops_in_window", "processed_in_window",
                                        "inference_fps", "ended_early"],
          [(ch, 30 * window, "30.000", "0.000", 0, 5 * window, 0, processed, f"{processed / window:.3f}", 0)
           for ch in range(1, 9)])
    table(d_ / "threads.csv", header, ["tid", "comm", "affinity", "cpu_s_in_window"],
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
    if broken == "frames":
        frames[-1] = frames[-1][:3]
    table(d_ / "frames.csv", header, ["channel", "seq", "pts", "t0_ns", "t1_ns", "t2a_ns", "t2b_ns", "t2c_ns", "t3_ns",
                                      "t4_ns", "slot_wait_ns", "ctx", "slot", "detections"]
          + (["rknn_cpu_ns"] if rknn else []), frames)


RUNS_COLUMNS = ["seq", "name", "phase", "variant", "channels", "condition", "repetition", "attempt", "exit", "valid",
                "passed", "reasons", "warnings", "trip_samples", "replaces"]
RUN_OPTIONS = ("cpu", "trip", "replaces")  # write_session's own options; the others go to write_run


def write_session(d_, runs, cpu=None, idle=True, ambient="23"):
    """A session of sweep m6. runs: [(arm, repetition, attempt, valid, options)], in run order; a run's directory is
    written unless it is a lost attempt (valid None: in started.tsv only). options: cpu, trip (runs.tsv's
    trip_samples), replaces (runs.tsv's replaces), and write_run's options."""
    d_.mkdir(parents=True)
    if idle:
        keyed(d_ / "idle_baseline.csv", {"run_kind": "m6"}, IDLE)
    keyed(d_ / "result.csv", {"run_kind": "m6", "ambient_c": ambient}, {"sweep": "m6", "session_status": "completed"})
    lines, started, seq, last = ["\t".join(RUNS_COLUMNS)], ["\t".join(RUNS_COLUMNS[:8])], 0, None
    for arm, rep, attempt, valid, options in runs:
        seq += not (attempt == 2 and last == (arm, rep))  # a repeat right after its run keeps the run's slot
        last = (arm, rep)
        name = f"{seq:02d}_m6_{arm}_8ch_r{rep}" + ("_repeat" if attempt == 2 else "")
        started.append("\t".join(map(str, [seq, name, "m6", arm, 8, "main", rep, attempt])))
        if valid is None:
            continue
        write_run(d_ / name, arm, rep, options.get("cpu", (cpu or CPU)[arm]),
                  **{k: x for k, x in options.items() if k not in RUN_OPTIONS})
        lines.append("\t".join(map(str, [seq, name, "m6", arm, 8, "main", rep, attempt, 0, int(valid), "",
                                         "" if valid else "clock below fixed: npu_hz", "", options.get("trip", 0),
                                         options.get("replaces", "")])))
    (d_ / "runs.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (d_ / "started.tsv").write_text("\n".join(started) + "\n", encoding="utf-8")
    return d_


def repetition(rep, **options):
    """Every arm of a repetition, valid, in M6 §4's order; options: {arm: run options}."""
    return [(a, rep, 1, True, options.get(a, {})) for a in ss.M6_ORDERS[rep]]


def sweep(**per_arm):
    """The three repetitions; per_arm: {arm: [the options of r1, r2, r3]}."""
    return [x for r in (1, 2, 3) for x in repetition(r, **{a: o[r - 1] for a, o in per_arm.items()})]


class TempDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "results" / "m6"

    def tearDown(self):
        self.tmp.cleanup()


class Thresholds(unittest.TestCase):
    def check(self, name, runs, expected):
        h = m6r.hypothesis(name, runs)
        self.assertEqual((h["spec"], h["practical"]), (expected, expected), h["flags"])
        return h

    def test_h1(self):
        self.assertEqual(self.check("M6-H1", arms(B=40.0, Rc=22.0, C=20.0), "adopted")["value"], Fraction(9, 10))
        self.assertEqual(self.check("M6-H1", arms(B=40.0, Rc=21.0, C=20.0), "adopted")["value"], Fraction(19, 20))
        self.assertEqual(self.check("M6-H1", arms(B=40.0, Rc=22.2, C=20.0), "rejected")["value"], Fraction(89, 100))

    def test_h2(self):
        self.assertEqual(self.check("M6-H2", arms(Cp=20.0, Cpf=24.0), "adopted")["value"], Fraction(1, 5))
        self.check("M6-H2", arms(Cp=20.0, Cpf=25.0), "adopted")
        self.assertEqual(self.check("M6-H2", arms(Cp=20.0, Cpf=23.9), "rejected")["value"], Fraction(39, 200))

    def test_h3(self):
        self.assertEqual(self.check("M6-H3", arms(B=40.0, By=36.0), "adopted")["value"], Fraction(1, 10))
        self.check("M6-H3", arms(B=40.0, By=30.0), "adopted")
        self.assertEqual(self.check("M6-H3", arms(B=40.0, By=36.1), "rejected")["value"], Fraction(39, 400))

    def test_means_of_the_runs(self):
        runs = arms(B=40.0, C=20.0)
        runs["Rc"] = [v(21.0), v(22.0), v(23.0)]
        h = m6r.hypothesis("M6-H1", runs)
        self.assertEqual(h["means"], {"B": 40, "Rc": 22, "C": 20})
        self.assertEqual(h["n"], {"B": 3, "Rc": 3, "C": 3})


class Exact(TempDir):
    """The review's cases: a value exactly at its threshold, which floating point puts below it."""

    def verdict(self, name, runs):
        """The hypothesis from a session of these runs, read back from its records."""
        c = m6r.collect([write_session(self.root / "20261201T000000Z_m6", runs)])
        return m6r.hypothesis(name, c["per_arm"])

    def test_h1_exactly_nine_tenths(self):
        runs = sweep(B=[{"cpu": 43.044}, {"cpu": 42.482}, {"cpu": 44.029}], Rc=[{"cpu": 21.552}] * 3,
                     C=[{"cpu": 19.558}, {"cpu": 18.920}, {"cpu": 18.967}])
        h = self.verdict("M6-H1", runs)
        self.assertEqual(h["value"], Fraction(9, 10))
        self.assertEqual((h["spec"], h["practical"]), ("adopted", "adopted"), h["flags"])
        self.assertEqual(h["secondary_value"], Fraction(9, 10))  # schedstat less idle: the same differences
        self.assertIs(h["mixed"], False)

    def test_h2_exactly_one_fifth(self):
        h = self.verdict("M6-H2", sweep(Cp=[{"cpu": 17.6}] * 3, Cpf=[{"cpu": 21.12}] * 3))
        self.assertEqual((h["value"], h["spec"], h["practical"]), (Fraction(1, 5), "adopted", "adopted"))

    def test_h3_exactly_one_tenth(self):
        h = self.verdict("M6-H3", sweep(B=[{"cpu": 43.0}] * 3, By=[{"cpu": 38.7}] * 3))
        self.assertEqual((h["value"], h["spec"], h["practical"]), (Fraction(1, 10), "adopted", "adopted"))

    def test_h1_at_the_threshold_and_schedstat_just_below(self):
        runs = sweep(B=[{"sched": 42.0}] * 3, Rc=[{"sched": 24.001}] * 3, C=[{"sched": 22.0}] * 3)
        h = self.verdict("M6-H1", runs)
        self.assertEqual((h["value"], h["spec"]), (Fraction(9, 10), "adopted"))
        self.assertEqual(h["secondary_value"], Fraction(17999, 20000))
        self.assertIs(h["mixed"], True)

    def test_zero_difference_has_no_sign(self):
        runs = arms(Cp=20.0, Cpf=25.0)
        for r in runs["Cp"] + runs["Cpf"]:
            r["schedstat_total_s"] = d(22.0)
        self.assertIs(m6r.hypothesis("M6-H2", runs)["mixed"], False)
        runs = arms(B=40.0, By=40.0)  # no difference on process CPU, one on schedstat
        for r in runs["By"]:
            r["schedstat_total_s"] = d(43.5)
        h = m6r.hypothesis("M6-H3", runs)
        self.assertEqual((h["value"], h["spec"], h["mixed"]), (0, "rejected", False))


class Mixed(unittest.TestCase):
    def with_sched(self, runs, **sched):
        for a, x in sched.items():
            for r in runs[a]:
                r["schedstat_total_s"] = d(x)
        return runs

    def test_h1_ratio_on_the_other_side(self):
        runs = self.with_sched(arms(B=40.0, Rc=21.0, C=20.0), B=42.0, Rc=25.0, C=22.0)  # 0.95, schedstat 0.85
        h = m6r.hypothesis("M6-H1", runs)
        self.assertEqual((h["spec"], h["mixed"], h["secondary_value"]), ("adopted", True, Fraction(17, 20)))
        runs = self.with_sched(arms(B=40.0, Rc=21.0, C=20.0), B=42.0, Rc=24.0, C=22.0)  # schedstat exactly 0.90
        self.assertIs(m6r.hypothesis("M6-H1", runs)["mixed"], False)
        runs = self.with_sched(arms(B=40.0, Rc=23.0, C=20.0), B=42.0, Rc=24.0, C=22.0)  # 0.85, schedstat 0.90
        h = m6r.hypothesis("M6-H1", runs)
        self.assertEqual((h["spec"], h["mixed"]), ("rejected", True))

    def test_h1_undefined_on_schedstat(self):
        runs = self.with_sched(arms(B=40.0, Rc=21.0, C=20.0), B=30.0, Rc=25.0, C=30.0)
        h = m6r.hypothesis("M6-H1", runs)
        self.assertEqual((h["spec"], h["mixed"]), ("adopted", None))
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

    def test_some_runs_without_schedstat(self):
        runs = arms(B=40.0, By=35.0)
        runs["B"][1]["schedstat_total_s"] = None
        h = m6r.hypothesis("M6-H3", runs)
        self.assertIn("no /proc/schedstat value less idle in some runs that count (B 1 of 3)", "; ".join(h["flags"]))
        self.assertIs(h["mixed"], False)  # from the other two
        for r in runs["B"]:
            r["schedstat_total_s"] = None
        h = m6r.hypothesis("M6-H3", runs)
        self.assertIsNone(h["mixed"])
        self.assertTrue(any("the secondary metric is missing for B" in f for f in h["flags"]))


class Practical(unittest.TestCase):
    def test_a_slow_channel(self):
        runs = arms(B=40.0, Rc=21.0, C=20.0, Cp=19.0)
        runs["Rc"][1]["inference_fps_min"] = d(4.74)
        h = m6r.hypothesis("M6-H1", runs)
        self.assertEqual((h["spec"], h["practical"]), ("adopted", "indeterminate"))
        self.assertIn("a channel below 4.75 fps in s/Rc_r2", "; ".join(h["flags"]))
        runs["Rc"][1]["inference_fps_min"] = Fraction(570, 120)  # 4.75 exactly: not below
        self.assertEqual(m6r.hypothesis("M6-H1", runs)["practical"], "adopted")
        runs["Cp"][0]["inference_fps_min"] = d(3.0)  # an arm M6-H1 does not name
        self.assertEqual(m6r.hypothesis("M6-H1", runs)["practical"], "adopted")

    def test_drop_rates_either_way(self):
        for named in ("Rc", "B", "C"):
            with self.subTest(higher=named):
                runs = arms(B=40.0, Rc=21.0, C=20.0)
                for r in runs[named]:
                    r["drop_rate"] = d(0.011)
                h = m6r.hypothesis("M6-H1", runs)
                self.assertEqual((h["spec"], h["practical"]), ("adopted", "indeterminate"))
                self.assertTrue(any("drop rates" in f for f in h["flags"]))
        runs = arms(B=40.0, By=30.0)
        for r in runs["B"]:  # the arm with more CPU drops more: indeterminate too
            r["drop_rate"] = d(0.02)
        self.assertEqual(m6r.hypothesis("M6-H3", runs)["practical"], "indeterminate")
        for r in runs["By"]:  # exactly 1 point apart: not more than 1
            r["drop_rate"] = d(0.01)
        self.assertEqual(m6r.hypothesis("M6-H3", runs)["practical"], "adopted")


class Drops(TempDir):
    """§2's drop-rate rule on the records' counts, Q1 drops over sampled frames, exactly."""

    def h3(self, by_drops):
        runs = sweep(B=[{"drops": x} for x in (0, 0, 3)], By=[{"drops": x} for x in by_drops])
        c = m6r.collect([write_session(self.root / "20261201T000000Z_m6", runs)])
        return m6r.hypothesis("M6-H3", c["per_arm"])

    def test_exactly_one_point(self):
        # means 3/14400 and 147/14400: exactly 1 point apart (floating point makes it 0.010000000000000002)
        h = self.h3((64, 32, 51))
        self.assertEqual((h["spec"], h["practical"]), ("adopted", "adopted"), h["flags"])

    def test_just_above(self):
        h = self.h3((64, 32, 52))
        self.assertEqual((h["spec"], h["practical"]), ("adopted", "indeterminate"))
        self.assertIn("the mean drop rates of B and By differ by 1.007 percentage points", "; ".join(h["flags"]))

    def test_cpf_drops_a_frame(self):  # §5, in addition to §2
        c = m6r.collect([write_session(self.root / "20261201T000000Z_m6", sweep(Cpf=[{}, {"drops": 1}, {}]))])
        h = m6r.hypothesis("M6-H2", c["per_arm"])
        self.assertEqual((h["spec"], h["practical"]), ("adopted", "indeterminate"))
        self.assertEqual([f for f in h["flags"] if f.startswith("Cpf drops")],
                         ["Cpf drops frames, Q1 drops in the window: 20261201T000000Z_m6/10_m6_Cpf_8ch_r2 (1): the "
                          "practical verdict of M6-H2 is then indeterminate (§5)"])

    def test_cp_drops_and_cpf_none(self):  # only §2's rule: 5 of 4800 apart is below 1 point
        c = m6r.collect([write_session(self.root / "20261202T000000Z_m6", sweep(Cp=[{"drops": 5}] * 3))])
        h = m6r.hypothesis("M6-H2", c["per_arm"])
        self.assertEqual((h["spec"], h["practical"]), ("adopted", "adopted"), h["flags"])


class Missing(TempDir):
    def test_two_of_three(self):
        runs = arms(B=40.0, C=20.0)
        runs["Rc"] = [v(21.0), v(23.0)]
        h = m6r.hypothesis("M6-H1", runs)
        self.assertEqual((h["spec"], h["practical"], h["value"]), ("adopted", "indeterminate", Fraction(9, 10)))
        self.assertTrue(any("fewer than 3 runs that count (Rc 2)" in f for f in h["flags"]))

    def test_none(self):
        runs = arms(B=40.0, C=20.0)
        runs["Rc"] = []
        h = m6r.hypothesis("M6-H1", runs)
        self.assertEqual((h["spec"], h["practical"], h["value"]), ("indeterminate", "indeterminate", None))
        self.assertTrue(any("no run that counts of Rc" in f for f in h["flags"]))

    def not_run(self, arm, why):
        return mock.patch.multiple(ss, M6_NOT_RUN=(arm,), M6_NOT_RUN_WHY={arm: why})

    def test_by_not_run(self):
        why = "it did not pass A5's criteria (results/check/20261130T000000Z_m6_equivalence)"
        with self.not_run("By", why):
            runs = arms(B=40.0, Rc=21.0, C=20.0)
            h = m6r.hypothesis("M6-H3", runs)
            self.assertEqual((h["spec"], h["practical"]), ("indeterminate", "indeterminate"))
            self.assertEqual(h["flags"], [f"By is not run: {why}; both verdicts are indeterminate (§2, §5)"])
            self.assertEqual(m6r.hypothesis("M6-H1", runs)["spec"], "adopted")
            self.assertNotIn("By", ss.m6_arms())
            self.assertEqual(m6r.order_position(3, "B"), 1)  # M6 §4: the order of the others stands
            s = write_session(self.root / "20261201T000000Z_m6",
                              [(a, r, 1, True, {}) for r in (1, 2, 3) for a in ss.sweep_plan("m6")[r - 1][2]])
            c = m6r.collect([s])
            self.assertEqual(c["complete"], [1, 2, 3])
            self.assertNotIn("By", c["per_arm"])
            text = m6r.report([s], refs={})
        self.assertIn(f"Arms not run (§5): By ({why}).", text)
        self.assertIn("| B − By | – | not run: By |", text)

    def test_rc_and_cpf_not_run(self):
        with self.not_run("Rc", "its tensor is not C's byte for byte (results/check/x)"):
            runs = {"B": [v(37.0), v(40.0), v(43.0)], "C": [v(20.0)] * 3}  # B's CV 7.5%
            h = m6r.hypothesis("M6-H1", runs)
            self.assertEqual((h["spec"], h["practical"]), ("indeterminate", "indeterminate"))
            self.assertEqual(h["flags"], ["CV of B's process CPU 7.50%, above 5% (§4)",
                                          "Rc is not run: its tensor is not C's byte for byte (results/check/x); "
                                          "both verdicts are indeterminate (§2, §5)"])
        with self.not_run("Cpf", "--cores all failed its check"):
            h = m6r.hypothesis("M6-H2", {"Cp": [v(18.0), v(20.0), v(22.5)]})  # Cp's CV above 10%
            self.assertEqual((h["spec"], h["practical"]), ("indeterminate", "indeterminate"))
            self.assertTrue(h["flags"][0].startswith("CV of Cp's process CPU"), h["flags"])
            self.assertIn("Cpf is not run: --cores all failed its check", h["flags"][1])
        with mock.patch.multiple(ss, M6_NOT_RUN=("Ct",), M6_NOT_RUN_WHY={}):
            text = m6r.report([], refs={})
        self.assertIn("Arms not run (§5): Ct (stream_session.M6_NOT_RUN_WHY gives no reason).", text)


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

    def test_b_exactly_five_percent(self):
        # sd 2.2 over a mean of 44: exactly 5% (floating point makes it 0.050000000000000065), so not above it
        self.assertEqual(self.flags("M6-H3", B=[41.8, 44.0, 46.2], By=[34.0] * 3), [])
        self.assertEqual(self.flags("M6-H3", B=[41.799, 44.0, 46.201], By=[34.0] * 3),
                         ["CV of B's process CPU 5.00%, above 5% (§4)"])  # sd 2.201: just above


class Values(TempDir):
    def test_percentile(self):
        self.assertEqual(m6r.percentile([4.0, 1.0, 3.0, 2.0], 50), 2.5)
        self.assertAlmostEqual(m6r.percentile([1.0, 2.0, 3.0, 4.0], 99), 3.97)
        self.assertIsNone(m6r.percentile([], 50))

    def test_less_idle(self):
        values = {"schedstat_cpu_total_s": d(30.0), "schedstat_cpu0-3_s": d(2.0), "system_cpu_total_s": d(25.0)}
        out = m6r.less_idle(values, IDLE, Fraction(60))  # the baseline scaled from 120 s to the window's 60 s
        self.assertEqual(out["schedstat_total_s"], d(29.4))
        self.assertEqual(out["schedstat_0-3_s"], d(1.7))
        self.assertEqual(out["system_total_s"], d(24.5))
        self.assertIsNone(out["schedstat_4-5_s"])
        self.assertIsNone(m6r.less_idle(values, {}, Fraction(120))["schedstat_total_s"])

    def test_run_values(self):
        write_run(self.root / "r", "B", 1, 40.0, sched=43.0, fps=4.74, drops=24)
        x = m6r.run_values(self.root / "r", IDLE)
        self.assertEqual(x["process_cpu_s"], 40)
        self.assertEqual(x["schedstat_total_s"], d(41.8))
        self.assertEqual(x["inference_fps_min"], Fraction(569, 120))
        self.assertEqual((x["drop_rate"], x["q1_drops_in_window"]), (Fraction(24, 4800), 24))
        self.assertEqual((x["threads_pre_s"], x["threads_dec_s"], x["threads_rest_s"]), (20, d(11.9), d(0.1)))
        self.assertEqual(x["threads_remainder_s"], 0)
        # two frames per channel in the window; the one before it is left out
        self.assertAlmostEqual(x["latency_p50_ms"], 23.1)  # t4 − t0: 22.6 and 23.6 ms, 8 frames each
        self.assertAlmostEqual(x["stage_run_p50_ms"], 15.5)
        self.assertAlmostEqual(x["stage_prepare_p99_ms"], 5.0)
        self.assertNotIn("rknn_run_calls", x)
        self.assertAlmostEqual(x["arrival_spread_ms"], 7.0)  # channels 1 ms apart: 1 to 8 ms
        self.assertAlmostEqual(x["arrival_drift_ms"], 0.0)

    def test_ct(self):
        write_run(self.root / "ct", "Ct", 1, 20.0, rknn=True)
        x = m6r.run_values(self.root / "ct", IDLE)
        cpu_ns = 2 * sum(800000 + 1000 * ch for ch in range(1, 9))  # the 16 frames whose t4 is in the window
        self.assertEqual(x["rknn_run_calls"], 16)
        self.assertEqual(x["rknn_run_cpu_s"], Fraction(cpu_ns, 10**9))
        self.assertEqual(x["rknn_run_cpu_share"], Fraction(cpu_ns, 10**9) / 20)
        self.assertEqual(x["rknn_run_cpu_mean_ms"], d(0.8045))
        self.assertEqual(x["rknn_run_wall_mean_ms"], d(15.5))  # t3 − t2c: 15 and 16 ms

    def test_phases(self):
        period = 2e8
        self.assertAlmostEqual(m6r.phase([5e7, 2e8 + 5e7, 4e8 + 5e7], period), 5e7)
        self.assertAlmostEqual(m6r.spread([1e6, 1.99e8], period), 2e6)  # across the circle's end
        self.assertAlmostEqual(m6r.drift([k * period + (1e6 if k < 6 else 3e6) for k in range(12)], period), 2e6)
        self.assertEqual([len(p) for p in m6r.split(list(range(14)), 6)], [3, 3, 2, 2, 2, 2])

    def test_no_idle_baseline(self):
        s = write_session(self.root / "20261201T000000Z_m6", sweep(), idle=False)
        c = m6r.collect([s])
        self.assertIsNone(c["per_arm"]["B"][0]["schedstat_total_s"])
        h = m6r.hypothesis("M6-H1", c["per_arm"])
        self.assertEqual((h["spec"], h["practical"], h["mixed"]), ("adopted", "adopted", None))
        self.assertIn("the secondary metric is missing for B, Rc, C: no mixed label", h["flags"])
        self.assertIn(f"| `{s.name}` | 24 | completed | 23 | no | no |", m6r.report([s], refs={}))


class Sweep(TempDir):
    def test_redone_repetition(self):
        # the first session ends inside repetition 2, during its Cp run; the next one reruns the whole repetition,
        # Cp with its repeat (stream_session.sweep_state), then runs repetition 3
        first = write_session(self.root / "20261201T000000Z_m6", repetition(1) + [
            ("Ct", 2, 1, True, {"cpu": 30.0}), ("Cpf", 2, 1, True, {}), ("Cp", 2, 1, None, {})])
        replaced = " ".join(f"{first.name}/{n}" for n in ("09_m6_Ct_8ch_r2", "10_m6_Cpf_8ch_r2", "11_m6_Cp_8ch_r2"))
        second = write_session(self.root / "20261202T000000Z_m6", [
            (a, 2, 2 if a == "Cp" else 1, True, {"replaces": replaced}) for a in ss.M6_ORDERS[2]] + repetition(3))
        c = m6r.collect([first, second])
        self.assertEqual(c["complete"], [1, 2, 3])
        self.assertEqual(c["counting"][(1, "B")]["run"], "20261201T000000Z_m6/01_m6_B_8ch_r1")
        self.assertEqual(c["counting"][(2, "B")]["run"], "20261202T000000Z_m6/08_m6_B_8ch_r2")
        self.assertEqual(c["counting"][(2, "Cp")]["run"], "20261202T000000Z_m6/03_m6_Cp_8ch_r2_repeat")
        self.assertEqual(c["counting"][(2, "Ct")]["process_cpu_s"], d(20.1))  # not the first session's 30.0
        first_r2 = [e for e in c["every"] if e["session"] == first.name and e["repetition"] == 2]
        self.assertEqual([(e["variant"], e["counts"], e["why"]) for e in first_r2], [
            ("Ct", False, f"its repetition was rerun in {second.name} (runs.tsv replaces)"),
            ("Cpf", False, f"its repetition was rerun in {second.name} (runs.tsv replaces)"),
            ("Cp", False, "lost")])
        self.assertEqual(first_r2[2]["reasons"], "the session ended during the run")
        self.assertEqual(len(c["per_arm"]["B"]), 3)
        self.assertEqual(m6r.hypothesis("M6-H1", c["per_arm"])["spec"], "adopted")
        self.assertEqual(m6r.session_dirs(second), ([first, second], False))
        self.assertEqual(m6r.session_dirs(first), ([first], False))
        self.assertIn("| no: its repetition was rerun in 20261202T000000Z_m6 (runs.tsv replaces) |",
                      "\n".join(m6r.every_run_section(c)))

    def test_invalid_x2_in_one_session(self):
        r2 = [x for a, *rest in repetition(2)
              for x in ([(a, 2, 1, False, {}), (a, 2, 2, False, {"trip": 3})] if a == "Rc" else [(a, *rest)])]
        s = write_session(self.root / "20261201T000000Z_m6", repetition(1) + r2 + repetition(3))
        c = m6r.collect([s])
        self.assertEqual(c["complete"], [1, 2, 3])
        self.assertIsNone(c["counting"][(2, "Rc")])
        self.assertEqual(len(c["per_arm"]["Rc"]), 2)
        h = m6r.hypothesis("M6-H1", c["per_arm"])
        self.assertEqual((h["spec"], h["practical"]), ("adopted", "indeterminate"))
        rc = [e for e in c["every"] if e["variant"] == "Rc" and e["repetition"] == 2]
        self.assertEqual([(e["seq"], e["why"], e["values"]["process_cpu_s"]) for e in rc],
                         [("14", "invalid", 22), ("14", "invalid ×2: counts as missing", 22)])  # values reported
        rows = [x for x in m6r.every_run_section(c) if "_Rc_8ch_r2" in x]
        self.assertIn("| 14 | 6 | no | no: invalid ×2: counts as missing | 22.000 |", rows[1])
        self.assertTrue(rows[1].endswith("| 3 | clock below fixed: npu_hz |"), rows[1])  # trip samples, reasons
        r2_row = m6r.counting_section(c)[5]
        self.assertTrue(r2_row.startswith("| r2 | ") and "| invalid ×2 |" in r2_row, r2_row)

    def test_invalid_x2_across_sessions(self):
        # Rc's first attempt is the first session's last run; the next session reruns repetition 2, Rc with its
        # repeat, which is invalid too
        first = write_session(self.root / "20261201T000000Z_m6", repetition(1) + [
            (a, 2, 1, a != "Rc", {}) for a in ss.M6_ORDERS[2][:6]])
        second = write_session(self.root / "20261202T000000Z_m6", [
            (a, 2, 2 if a == "Rc" else 1, a != "Rc", {}) for a in ss.M6_ORDERS[2]] + repetition(3))
        c = m6r.collect([first, second])
        self.assertEqual(c["complete"], [1, 2, 3])
        self.assertIsNone(c["counting"][(2, "Rc")])
        r2 = [e for e in c["every"] if e["repetition"] == 2]
        self.assertEqual([(e["session"], e["attempt"], e["why"]) for e in r2 if e["variant"] == "Rc"],
                         [(first.name, 1, "invalid"), (second.name, 2, "invalid ×2: counts as missing")])
        c_first = next(e for e in r2 if e["session"] == first.name and e["variant"] == "C")
        self.assertEqual(c_first["why"], f"repetition 2 was completed in {second.name}")  # no runs.tsv replaces

    def test_valid_repeat_counts(self):
        r1 = [x for a, *rest in repetition(1)
              for x in ([(a, 1, 1, False, {}), (a, 1, 2, True, {"cpu": 21.0})] if a == "Rc" else [(a, *rest)])]
        s = write_session(self.root / "20261201T000000Z_m6", r1 + repetition(2) + repetition(3))
        c = m6r.collect([s])
        self.assertEqual(c["counting"][(1, "Rc")]["run"], f"{s.name}/03_m6_Rc_8ch_r1_repeat")
        self.assertEqual(c["counting"][(1, "Rc")]["process_cpu_s"], 21)
        first = next(e for e in c["every"] if e["name"] == "03_m6_Rc_8ch_r1")
        self.assertEqual((first["counts"], first["why"]), (False, "invalid"))

    def test_positions(self):
        s = write_session(self.root / "20261201T000000Z_m6", sweep())
        c = m6r.collect([s])
        self.assertEqual([c["counting"][(r, "B")]["seq"] for r in (1, 2, 3)], ["1", "16", "18"])
        self.assertEqual([e["position"] for e in c["every"] if e["variant"] == "B"], [1, 8, 2])

    def test_paired_interval(self):
        by_rep = {(1, "B"): {"x": 41.0}, (1, "C"): {"x": 40.0}, (2, "B"): {"x": 42.0}, (2, "C"): {"x": 40.0},
                  (3, "B"): {"x": 44.0}, (3, "C"): {"x": 40.0}}
        diffs, mean, half = m6r.paired(by_rep, "B", "C", "x")
        self.assertEqual(diffs, {1: 1.0, 2: 2.0, 3: 4.0})
        self.assertAlmostEqual(mean, 7 / 3)
        # sd = sqrt(7 / 3); half-width t(0.975, 2) × sd / sqrt(3) = 4.302653 × sqrt(7) / 3 = 3.794583
        self.assertAlmostEqual(half, 4.302652729911275 * math.sqrt(7) / 3)
        self.assertAlmostEqual(half, 3.794583, places=6)
        by_rep[(2, "C")] = None  # an invalid ×2 run: that repetition has no pair
        diffs, mean, half = m6r.paired(by_rep, "B", "C", "x")
        self.assertEqual(diffs, {1: 1.0, 3: 4.0})
        self.assertAlmostEqual(half, 12.706204736174698 * statistics.stdev([1.0, 4.0]) / math.sqrt(2))
        by_rep[(3, "C")] = None
        self.assertEqual(m6r.paired(by_rep, "B", "C", "x"), ({1: 1.0}, 1.0, None))  # one pair: no interval
        self.assertEqual(m6r.paired(by_rep, "B", "Rc", "x"), ({}, None, None))

    def test_report(self):
        cpu = dict(CPU, Ct=20.5)
        s = write_session(self.root / "20261201T000000Z_m6", sweep(Ct=[{"rknn": True}] * 3), cpu)
        out = Path(self.tmp.name) / "M6_tables.md"
        with mock.patch.object(m6r, "references", return_value={}):
            self.assertEqual(m6r.main(["--session", str(s), "--out", str(out)]), 0)
        text = out.read_text(encoding="utf-8")
        self.assertIn("The sweep is complete", text)
        self.assertIn("| M6-H1: (B − Rc) / (B − C) ≥ 0.90 | B 40.000 (3), Rc 22.000 (3), C 20.000 (3) | 0.9000 | 0.90 "
                      "| adopted | adopted |", text)
        self.assertIn("| M6-H2: (Cpf − Cp) / Cp ≥ 0.20 | Cp 19.000 (3), Cpf 24.000 (3) | 0.2632 |", text)
        self.assertIn("| M6-H3: (B − By) / B ≥ 0.10 | B 40.000 (3), By 34.000 (3) | 0.1500 |", text)
        self.assertIn("A path that filters less, such as taking single pixels, would be cheaper and is not what By "
                      "is.", text)
        self.assertIn("- **All three:** One board, one image, one model, one point, file-backed RTSP streams at "
                      "1080p.", text)
        self.assertIn("| B − Rc | 18.000 | +90.00% |", text)
        self.assertIn("| (B − Bd) − (Rc − C) | -1.000 | -5.00% |", text)
        self.assertIn("Ct − C, process CPU, from the means: 0.500 s", text)
        self.assertIn("includes the two clock reads around each rknn_run", text)
        self.assertIn("so its last digit can differ from the published tables", text)
        self.assertIn("| Calls | 16 ± 0 (3) |", text)
        self.assertIn("| Ct − C | Process CPU (s) | 0.500 | 0.500 | 0.500 | 0.500 | 0.500 to 0.500 s |", text)
        self.assertIn(f"| `{s.name}` | 24 | completed | 23 | yes | no |", text)
        self.assertIn("No session is superseded", text)

    def test_smoke_and_errors(self):
        smoke = write_session(Path(self.tmp.name) / "results" / "check" / "20261201T000000Z_m6_smoke",
                              repetition(1))
        out = Path(self.tmp.name) / "smoke.md"
        with mock.patch.object(m6r, "references", return_value={}), mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(m6r.main(["--session", str(smoke), "--out", str(out)]), 0)
            self.assertIn("**A smoke test:**", out.read_text(encoding="utf-8"))
            self.assertEqual(m6r.main(["--session", str(self.root / "20261201T000000Z_m3_after")]), 2)
            other = self.root / "20261201T000000Z_m3_after"
            other.mkdir(parents=True)
            self.assertEqual(m6r.main(["--session", str(other)]), 2)
            partial = write_session(self.root / "20261202T000000Z_m6", repetition(1))
            text = m6r.report(m6r.session_dirs(partial)[0], refs={})
        self.assertIn("**No outcome yet:** 1 of 3 repetitions are complete.", text)


class PartialRecords(TempDir):
    def test_an_unreadable_run_that_does_not_count(self):
        r1 = [x for a, *rest in repetition(1)
              for x in ([(a, 1, 1, False, {"broken": "frames"}), (a, 1, 2, True, {})] if a == "Rc"
                        else [(a, *rest)])]
        s = write_session(self.root / "20261201T000000Z_m6", r1 + repetition(2) + repetition(3))
        out = Path(self.tmp.name) / "M6_tables.md"
        with mock.patch.object(m6r, "references", return_value={}):
            self.assertEqual(m6r.main(["--session", str(s), "--out", str(out)]), 0)
        row = next(x for x in out.read_text(encoding="utf-8").splitlines() if "03_m6_Rc_8ch_r1` " in x)
        self.assertIn("| no: invalid; record unreadable (20261201T000000Z_m6/03_m6_Rc_8ch_r1: TypeError", row)

    def test_an_unreadable_run_that_counts(self):
        s = write_session(self.root / "20261201T000000Z_m6", sweep(B=[{"broken": "summary"}, {}, {}]))
        with mock.patch.object(m6r, "references", return_value={}), \
                mock.patch("sys.stderr", new=io.StringIO()) as err:
            self.assertEqual(m6r.main(["--session", str(s)]), 2)
        self.assertIn("m6_report: a run that counts cannot be read: 20261201T000000Z_m6/01_m6_B_8ch_r1: ValueError: "
                      "summary.csv has no process_cpu_s", err.getvalue())


class Superseded(TempDir):
    def sweeps(self):
        first = write_session(self.root / "20261201T000000Z_m6", sweep(By=[{"cpu": 38.0}] * 3))  # M6-H3 rejected
        second = write_session(self.root / "20261205T000000Z_m6", sweep())  # M6-H3 adopted
        return first, second

    def test_a_superseded_sweep_does_not_count(self):
        first, second = self.sweeps()
        with mock.patch.object(ss, "M6_SUPERSEDED", (first.name,)):
            c = m6r.collect([first, second])
            self.assertEqual((c["sessions"], c["superseded"]), ([second], [first]))
            self.assertEqual(m6r.hypothesis("M6-H3", c["per_arm"])["spec"], "adopted")
            self.assertTrue(all(v["run"].startswith(second.name) for runs in c["per_arm"].values() for v in runs))
            self.assertEqual(len(c["superseded_runs"]), 24)
            self.assertTrue(all(e["why"] == "superseded (M6 §5, §9)" and not e["counts"]
                                for e in c["superseded_runs"]))
            self.assertTrue(all(e["session"] == second.name for e in c["every"]))
            out = Path(self.tmp.name) / "M6_tables.md"
            with mock.patch.object(m6r, "references", return_value={}):
                self.assertEqual(m6r.main(["--session", str(second), "--out", str(out)]), 0)
                self.assertEqual(m6r.collect(m6r.m6_sessions(Path(self.tmp.name)))["sessions"], [second])
        text = out.read_text(encoding="utf-8")
        self.assertIn("| M6-H3: (B − By) / B ≥ 0.10 | B 40.000 (3), By 34.000 (3) | 0.1500 | 0.10 | adopted |", text)
        section = text[text.index("## Superseded runs"):]
        self.assertIn(f"Superseded by an amendment (stream_session.M6_SUPERSEDED): `{first.name}`.", section)
        self.assertIn(f"| `{first.name}/05_m6_By_8ch_r1` | By | 1 | 1 | 5 | 5 | yes | no: superseded (M6 §5, §9) | "
                      "38.000 |", section)
        self.assertIn(f"| `{first.name}` | 24 | completed | 23 | yes | yes |", text)

    def test_without_the_constant_one_sweep(self):
        first, second = self.sweeps()
        with mock.patch.object(ss, "M6_SUPERSEDED", ()):
            c = m6r.collect([first, second])
        self.assertEqual((c["sessions"], c["superseded"]), ([first, second], []))
        self.assertEqual(m6r.hypothesis("M6-H3", c["per_arm"])["spec"], "rejected")  # the first session completed
        later = next(e for e in c["every"] if e["session"] == second.name)
        self.assertEqual(later["why"], f"repetition 1 was completed in {first.name}")


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
