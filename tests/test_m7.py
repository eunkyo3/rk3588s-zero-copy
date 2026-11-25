#!/usr/bin/env python3
"""Tests for M7's parts (prereg/M7.md) in tools/stream_session.py and tools/check_prereg_order.py: the sweeps m7_main
and m7_aux and m7-check (§1, §4, §8 step 3), rule 5 and the record of the snapshots (§4), the idle pair of snapshots
and the probe in a session (§3, §4), the session's gates (§8, §9), and the order check (§8).

Usage: python3 tests/test_m7.py
"""
import datetime as dt
import io
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import stream_session as ss  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_stream_session as tss  # noqa: E402  (its session scaffolding; a module: its tests are not collected here)
from test_check_prereg_order import AFTER, Case  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UTC = dt.timezone.utc
F7 = "f7" * 20
MAIN = ("B", "Bd", "Rc", "C", "Bk", "Bdk", "Rck", "Ck")
AUX = ("B", "Bd", "Rc", "C", "Ct", "Cpt")


class InTmp(unittest.TestCase):
    """A test that runs in a temporary working directory."""

    def setUp(self):
        self.here = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.here)
        self.tmp.cleanup()


def session(d, **attrs):
    """A Session without __init__, as tests/test_stream_session.py makes one, with M7's attributes."""
    s = tss.OperatorStop.session(None, d)
    s.freeze_m3 = s.freeze_m6 = "none"
    s.freeze_m7, s.snapshot_proc, s.idle_snapshot = F7, None, "not taken"
    for k, v in attrs.items():
        setattr(s, k, v)
    return s


def close(s):
    for f in (s.runs, s.started, s.log):
        f.close()


class Sweeps(InTmp):
    def test_the_sweeps(self):
        self.assertEqual(ss.SWEEPS["m7_main"], ((8,), "main", MAIN, "m7"))  # M7 §1
        self.assertEqual(ss.SWEEPS["m7_aux"], ((5,), "aux", AUX, "m7"))
        self.assertEqual([n for n in ss.SWEEPS if ss.is_m7(n)], ["m7_main", "m7_aux"])
        self.assertFalse(ss.is_m7("m6") or ss.is_m7(None) or ss.is_m7("m7_check"))
        for name in ("m7_main", "m7_aux"):
            self.assertNotIn(name, ss.ORDER)  # outside the others' order, as M6's sweep
            self.assertEqual(ss.session_kind(name), "m7")
        self.assertTrue(ss.unsaturated(8, "main", ss.LIMIT) and ss.unsaturated(5, "aux", ss.LIMIT))  # rule 3 applies

    def test_the_sessions_of_each_sweep_are_told_apart(self):
        for name in ("20261128T000000Z_m7_main", "20261128T030000Z_m7_aux", "20261128T050000Z_m7_main"):
            Path("results/m7", name).mkdir(parents=True)
        Path("results/check/20261127T000000Z_m7_main_smoke").mkdir(parents=True)
        self.assertEqual([p.name for p in ss.sweep_sessions("m7_main")],
                         ["20261128T000000Z_m7_main", "20261128T050000Z_m7_main"])
        self.assertEqual([p.name for p in ss.sweep_sessions("m7_aux")], ["20261128T030000Z_m7_aux"])
        with mock.patch.object(ss, "M7_SUPERSEDED", ("20261128T000000Z_m7_main",)):  # M7 §5: kept, not counted
            self.assertEqual([p.name for p in ss.sweep_sessions("m7_main")], ["20261128T050000Z_m7_main"])
            self.assertEqual(len(ss.sweep_sessions("m7_aux")), 1)

    def test_orders_as_registered(self):
        # M7 §4, as registered.
        self.assertEqual(ss.M7_ORDERS["m7_main"], {1: MAIN, 2: ("Ck", "Rck", "Bdk", "Bk", "C", "Rc", "Bd", "B"),
                                                   3: ("Bdk", "Bd", "C", "Ck", "Rc", "Rck", "B", "Bk")})
        self.assertEqual(ss.M7_ORDERS["m7_aux"], {1: AUX, 2: ("Cpt", "Ct", "C", "Rc", "Bd", "B"),
                                                  3: ("Rc", "C", "B", "Bd", "Cpt", "Ct")})
        for sweep, orders in ss.M7_ORDERS.items():
            self.assertEqual(orders[2], orders[1][::-1])  # the second is the first reversed
            for order in orders.values():
                self.assertEqual(sorted(order), sorted(ss.SWEEPS[sweep][2]))
        with mock.patch.object(ss, "M7_NOT_RUN", ()):
            for sweep, (channels, _) in ss.M7_POINTS.items():
                self.assertEqual(ss.sweep_plan(sweep), [(channels, r, ss.M7_ORDERS[sweep][r]) for r in (1, 2, 3)])
            self.assertEqual(sum(len(o) for _, _, o in ss.sweep_plan("m7_main")), 24)  # M7 §4
            self.assertEqual(sum(len(o) for _, _, o in ss.sweep_plan("m7_aux")), 18)

    def test_arms_that_are_not_run_are_left_out_and_the_order_stands(self):
        with mock.patch.object(ss, "M7_NOT_RUN", ss.M7_COUNTED):  # M7 §5: the counters' check failed
            self.assertEqual(ss.sweep_variants("m7_main"), ("B", "Bd", "Rc", "C"))
            self.assertEqual([o for _, _, o in ss.sweep_plan("m7_main")],
                             [("B", "Bd", "Rc", "C"), ("C", "Rc", "Bd", "B"), ("Bd", "C", "Rc", "B")])
            self.assertEqual(ss.sweep_variants("m7_aux"), AUX)
        with mock.patch.object(ss, "M7_NOT_RUN", ("Cpt",)):  # M7 §5: Cpt's check failed
            self.assertEqual([o for _, _, o in ss.sweep_plan("m7_aux")],
                             [("B", "Bd", "Rc", "C", "Ct"), ("Ct", "C", "Rc", "Bd", "B"), ("Rc", "C", "B", "Bd", "Ct")])
            self.assertEqual(ss.sweep_variants("m7_main"), MAIN)
        self.assertEqual(ss.sweep_variants("m6"), ss.m6_arms())  # the others as before
        self.assertEqual(ss.sweep_variants("m3_after"), ("B", "C"))

    def test_bench_options(self):
        snap = ["--snapshot", "on"]
        want = {"B": ["--variant", "B"], "Bd": ["--variant", "Bd"], "Rc": ["--variant", "Rc"], "C": ["--variant", "C"],
                "Bk": ["--variant", "B", "--thread-counters", "on"],
                "Bdk": ["--variant", "Bd", "--thread-counters", "on"],
                "Rck": ["--variant", "Rc", "--thread-counters", "on"],
                "Ck": ["--variant", "C", "--thread-counters", "on"],
                "Ct": ["--variant", "C", "--time-rknn", "on"],
                "Cpt": ["--variant", "C", "--post", "neon", "--time-rknn", "on"]}
        for sweep in ("m7_main", "m7_aux"):
            for arm in ss.SWEEPS[sweep][2]:
                self.assertEqual(ss.bench_args(arm, sweep), want[arm] + snap, (sweep, arm))  # M7 §1
                self.assertEqual(ss.bench_args(arm, "m7_check"), want[arm] + snap, arm)
        self.assertEqual(ss.bench_args("Ck", "m7_counters"), want["Ck"] + snap)
        self.assertEqual(ss.bench_args("C", "m7_snapshot"), ["--variant", "C"] + snap)
        self.assertEqual(ss.bench_args("Cpt", "m7_cpt"), want["Cpt"] + snap)
        self.assertEqual(ss.bench_args("C", "m7_default"), ["--variant", "C"])  # every option at its default
        for phase in ("m6", "m6_check"):  # M6's arms as before: no snapshots
            self.assertEqual(ss.bench_args("Ct", phase), ["--variant", "C", "--time-rknn", "on"])
            self.assertEqual(ss.bench_args("B", phase), ["--variant", "B"])

    def test_rule_3_applies(self):
        for phase, channels, condition in (("m7_main", 8, "main"), ("m7_aux", 5, "aux"), ("m7_counters", 8, "main"),
                                           ("m7_default", 2, "main"), ("m7_snapshot", 2, "main"),
                                           ("m7_cpt", 5, "aux"), ("m7_check", 5, "aux")):
            self.assertTrue(ss.rule3_applies(phase, "C", channels, condition, ss.LIMIT, {}), phase)

    def test_sessions(self):
        ns = types.SimpleNamespace
        self.assertTrue(ss.m7_session(ns(command="sweep", sweep="m7_main")))
        self.assertTrue(ss.m7_session(ns(command="sweep", sweep="m7_aux")))
        self.assertTrue(ss.m7_session(ns(command="m7-check", sweep=None)))
        self.assertFalse(ss.m7_session(ns(command="sweep", sweep="m6")))
        self.assertFalse(ss.m6_session(ns(command="sweep", sweep="m7_main")))
        self.assertFalse(ss.m6_session(ns(command="m7-check", sweep=None)))

    def runs_tsv(self, d, runs):
        d.mkdir(parents=True, exist_ok=True)
        rows = "".join(f"{i}\t{i:02d}_{d.name[17:]}_{arm}_{ch}ch_r{r}\t{d.name[17:]}\t{arm}\t{ch}\t{r}\t1\t1\t\n"
                       for i, (ch, r, arm) in enumerate(runs, 1))
        (d / "runs.tsv").write_text("seq\tname\tphase\tvariant\tchannels\trepetition\tattempt\tvalid\tpassed\n" + rows,
                                    encoding="utf-8")

    def test_progress(self):
        self.assertEqual(ss.progress("m7_main"), "new")
        main = [(8, r, a) for _, r, order in ss.sweep_plan("m7_main") for a in order]
        self.runs_tsv(Path("results/m7/20261128T000000Z_m7_main"), main[:-1])
        self.assertEqual(ss.progress("m7_main"), "started")
        self.runs_tsv(Path("results/m7/20261128T000000Z_m7_main"), main)
        self.assertEqual(ss.progress("m7_main"), "complete")
        self.assertEqual(ss.progress("m7_aux"), "new")
        with mock.patch.object(ss, "M7_NOT_RUN", ss.M7_COUNTED):  # arms that are not run are not waited for
            self.runs_tsv(Path("results/m7/20261128T000000Z_m7_main"), [x for x in main if x[2] not in ss.M7_COUNTED])
            self.assertEqual(ss.progress("m7_main"), "complete")


class CheckPlan(InTmp):
    """m7-check's two parts (M7 §8 step 3)."""

    def test_the_counters_part(self):
        self.assertEqual(ss.m7_check_plan("counters"), [("m7_counters", "Ck", 8, "main")])

    def test_the_rest(self):
        plan = ss.m7_check_plan("rest")
        self.assertEqual(plan[:3], [("m7_default", "C", 2, "main"), ("m7_snapshot", "C", 2, "main"),
                                    ("m7_cpt", "Cpt", 5, "aux")])
        self.assertEqual(plan[3:], [("m7_check", a, 8, "main") for a in MAIN]
                         + [("m7_check", a, 5, "aux") for a in AUX])

    def test_arms_that_are_not_run(self):
        with mock.patch.object(ss, "M7_NOT_RUN", ss.M7_COUNTED + ("Cpt",)):
            plan = ss.m7_check_plan("rest")
            self.assertEqual([(a, c) for p, a, c, _ in plan if p == "m7_check"],
                             [(a, 8) for a in ("B", "Bd", "Rc", "C")] + [(a, 5) for a in ("B", "Bd", "Rc", "C", "Ct")])
            self.assertIn(("m7_cpt", "Cpt", 5, "aux"), plan)  # the check itself stays
            self.assertEqual(ss.m7_check_plan("counters"), [("m7_counters", "Ck", 8, "main")])

    def test_the_command_runs_the_plan(self):
        for part, stop_at in (("counters", None), ("rest", None), ("rest", 4)):
            with self.subTest(part=part, stop_at=stop_at):
                calls, results = [], []

                def run(*a, stop_at=stop_at):
                    calls.append(a)
                    if len(calls) == stop_at:
                        raise ss.Stop("the server is down")
                    valid = a[1] != "Bd"
                    return valid, Path(f"{len(calls):02d}_{a[0]}_{a[1]}_{a[2]}ch")

                s = types.SimpleNamespace(run=run, write_result=lambda rows, kind: results.append((rows, kind)),
                                          args=types.SimpleNamespace(part=part, sweep=None, command="m7-check"),
                                          out=Path("."), idle_snapshot="complete")
                with mock.patch.object(ss, "write_idle_baseline") as idle:
                    try:
                        ss.cmd_m7_check(s)
                    except ss.Stop:
                        pass
                idle.assert_called_once_with(s, "check")
                plan = ss.m7_check_plan(part)
                self.assertEqual(calls, [(p, a, ch, cond, "check") for p, a, ch, cond in plan][:stop_at])
                (rows, kind), = results
                self.assertEqual(kind, "check")
                self.assertEqual(rows[:2], [("part", part), ("session_status", "stopped" if stop_at else "completed")])
                ran = rows[2:2 + len(calls) - (1 if stop_at else 0)]
                if part == "counters":
                    self.assertEqual(ran, [("m7_counters_Ck_main_run", "01_m7_counters_Ck_8ch")])
                elif not stop_at:
                    self.assertEqual(ran[:4], [("m7_default_C_main_run", "01_m7_default_C_2ch"),
                                               ("m7_snapshot_C_main_run", "02_m7_snapshot_C_2ch"),
                                               ("m7_cpt_Cpt_aux_run", "03_m7_cpt_Cpt_5ch"),
                                               ("m7_check_B_main_run", "04_m7_check_B_8ch")])
                    self.assertEqual(dict(ran)["m7_check_Bd_main_run"], "invalid_x2")
                    self.assertEqual(dict(ran)["m7_check_Cpt_aux_run"], f"{len(plan):02d}_m7_check_Cpt_5ch")
                self.assertEqual(dict(rows)["arms_m7_main"], " ".join(MAIN))
                self.assertEqual(dict(rows)["arms_m7_aux"], " ".join(AUX))
                self.assertEqual(dict(rows)["idle_snapshot"], "complete")


def counters(*threads, **changes):
    """counters.csv's rows for threads [(tid, comm)], every group opened and read and running as long as enabled;
    changes: {tid: {column: value}}."""
    rows = []
    for tid, comm in threads:
        r = {"tid": tid, "comm": comm, "opened": "1", "open_errno": "0", "failed_event": "", "read_ok": "1",
             "enable_ok": "1", "disable_ok": "1",
             "time_enabled_ns": "10000000000", "time_running_ns": "10000000000", "cpu_cycles": "123"}
        r.update(changes.get(tid, {}))
        rows.append(r)
    return rows


THREADS = [("100", "bench_main"), ("101", "dec-1"), ("102", "npu-0"), ("103", "pre-0"), ("104", "mpp_dec_parser"),
           ("105", "metrics")]


def threads_csv(threads=THREADS):
    return [{"tid": t, "comm": c, "affinity": "0" if c == "metrics" else "4-7", "cpu_s_in_window": "1.0"}
            for t, c in threads]


class Rule5(unittest.TestCase):
    """M7 §4's rule 5 for the counted arms."""

    def test_every_pipeline_thread_has_its_group(self):
        self.assertEqual(ss.counter_problems(threads_csv(), counters(*THREADS)), [])

    def test_a_thread_without_a_group(self):
        problems = ss.counter_problems(threads_csv(), counters(*THREADS[:2], *THREADS[3:]))
        self.assertEqual(len(problems), 1)
        self.assertIn("npu-0 (102)", problems[0])

    def test_a_group_that_did_not_open_or_was_not_read(self):
        for change in ({"opened": "0", "open_errno": "13", "failed_event": "l3d_cache_refill"}, {"read_ok": "0"},
                       {"enable_ok": "0"}, {"disable_ok": "0"}):
            problems = ss.counter_problems(threads_csv(), counters(*THREADS, **{"101": change}))
            self.assertEqual(len(problems), 1, change)
            self.assertIn("dec-1 (101)", problems[0])

    def test_a_group_that_was_multiplexed(self):
        problems = ss.counter_problems(threads_csv(), counters(*THREADS, **{"103": {"time_running_ns": "9999999000"}}))
        self.assertEqual(len(problems), 1)
        self.assertIn("pre-0 (103) ran 9999999000 ns of the 10000000000 ns", problems[0])

    def test_the_metrics_thread_is_not_a_pipeline_thread(self):
        self.assertEqual(ss.counter_problems(threads_csv(), counters(*THREADS[:-1])), [])
        # but a group of its own that opened must still run as long as it was enabled ("for some group")
        problems = ss.counter_problems(threads_csv(), counters(*THREADS, **{"105": {"time_running_ns": "1"}}))
        self.assertEqual(len(problems), 1)

    def test_groups_of_threads_that_ended_are_not_needed(self):
        self.assertEqual(ss.counter_problems(threads_csv(), counters(*THREADS, ("106", "dec-9"))), [])

    def test_nothing_to_judge(self):
        self.assertEqual(len(ss.counter_problems(threads_csv(), None)), 1)  # no readable counters.csv
        self.assertEqual(len(ss.counter_problems([], counters(*THREADS))), 1)  # no pipeline thread
        self.assertEqual(len(ss.counter_problems(threads_csv([("105", "metrics")]), counters(*THREADS))), 1)

    def test_a_group_that_never_counted(self):
        zero = {"time_enabled_ns": "0", "time_running_ns": "0"}
        problems = ss.counter_problems(threads_csv(), counters(*THREADS, **{"102": zero}))
        self.assertEqual(len(problems), 1)  # the thread used 1.0 s in the window
        self.assertIn("enabled for 0 ns", problems[0])
        idle = threads_csv()
        idle[2]["cpu_s_in_window"] = "0.000"  # a thread that did not run has nothing to count
        self.assertEqual(ss.counter_problems(idle, counters(*THREADS, **{"102": zero})), [])

    def test_unreadable_times(self):
        problems = ss.counter_problems(threads_csv(), counters(*THREADS, **{"104": {"time_enabled_ns": ""}}))
        self.assertEqual(len(problems), 1)
        self.assertIn("no readable times", problems[0])


def write_snapshots(d, taken=("1", "1"), complete=("1", "1"), drop=(), tasks_columns=None):
    """The files of core/snapshot.h in d, as write_snapshots writes them."""
    d.mkdir(parents=True, exist_ok=True)
    head = f"# start_time_utc: 2026-11-28T00:00:00Z\n# prereg_m7_commit: {ss.PREREG_M7_COMMIT}\n# run_kind: m7\n"
    meta = "".join(f"snapshot{k}_taken,{taken[k - 1]}\nsnapshot{k}_complete,{complete[k - 1]}\n" for k in (1, 2))
    files = {"snapshot_meta.csv": head + "key,value\n" + meta,
             "snapshot_own.csv": head + ss.SNAPSHOT_COLUMNS["snapshot_own.csv"] + "\n1,100,bench_main,1,2,3,4,5\n",
             "snapshot_tasks.csv": head + (tasks_columns or ss.SNAPSHOT_COLUMNS["snapshot_tasks.csv"]) + "\n"
                                   "1,7,7,1,\"kworker/0:1, x\",100\n"}
    for k in (1, 2):
        for name in ("schedstat", "stat", "interrupts", "softirqs"):
            files[f"snapshot{k}_{name}.txt"] = f"{name} text\n"
    for name, text in files.items():
        if name not in drop:
            (d / name).write_text(text, encoding="utf-8")


class Snapshots(unittest.TestCase):
    """M7 §4: whether a run's snapshots are there and complete. They never decide validity."""

    META = {"snapshot1_taken": "1", "snapshot1_complete": "1", "snapshot2_taken": "1", "snapshot2_complete": "1"}

    def test_both_complete(self):
        self.assertEqual(ss.snapshot_problems(self.META, set(ss.SNAPSHOT_FILES)), [])

    def test_incomplete_or_not_taken(self):
        problems = ss.snapshot_problems({**self.META, "snapshot1_complete": "0"}, set(ss.SNAPSHOT_FILES))
        self.assertEqual(problems, ["snapshot 1 is incomplete"])
        problems = ss.snapshot_problems({**self.META, "snapshot2_taken": "0"}, set(ss.SNAPSHOT_FILES))
        self.assertEqual(problems, ["snapshot 2 was not taken"])

    def test_missing_files(self):
        self.assertEqual(ss.snapshot_problems(None, set()), ["no readable snapshot_meta.csv"])
        problems = ss.snapshot_problems(self.META, set(ss.SNAPSHOT_FILES) - {"snapshot2_interrupts.txt"})
        self.assertEqual(problems, ["no readable snapshot2_interrupts.txt"])

    def test_the_files_of_core_snapshot_h(self):
        self.assertEqual(len(ss.SNAPSHOT_FILES), 11)
        header = (ROOT / "src/core/snapshot.h").read_text(encoding="utf-8")
        for name in ("snapshot_meta.csv", "snapshot_own.csv", "snapshot_tasks.csv", "_schedstat.txt", "_stat.txt",
                     "_interrupts.txt", "_softirqs.txt"):
            self.assertIn(name, header)
        for name, columns in ss.SNAPSHOT_COLUMNS.items():
            self.assertIn(f"{name:<21}{columns}", header)

    def test_state_from_the_files(self):
        with tempfile.TemporaryDirectory() as t:
            for case, kw, want in (
                    ("ok", {}, "complete"),
                    ("first", {"complete": ("0", "1")}, "missing: snapshot 1 is incomplete"),
                    ("meta", {"drop": ("snapshot_meta.csv",)}, "missing: no readable snapshot_meta.csv"),
                    ("txt", {"drop": ("snapshot1_stat.txt",)}, "missing: no readable snapshot1_stat.txt"),
                    ("columns", {"tasks_columns": "snapshot,pid,tid,comm,run_ns"},
                     "missing: no readable snapshot_tasks.csv")):
                d = Path(t) / case
                write_snapshots(d, **kw)
                self.assertEqual(ss.snapshots_state(d), want, case)
            self.assertEqual(ss.snapshots_state(Path(t) / "none"), "missing: no readable snapshot_meta.csv")


class RunOfAnArm(unittest.TestCase):
    """What the session's judge takes from a finished M7 run (m7_run_problems)."""

    def env(self, arm):
        return {"m6": {"arm": ""}, "m7": {"arm": arm, "snapshot": arm != "", "thread_counters": arm in ss.M7_COUNTED}}

    def test_a_run_of_its_arm(self):
        for sweep in ("m7_main", "m7_aux"):
            for arm in ss.SWEEPS[sweep][2]:
                rows = counters(*THREADS) if arm in ss.M7_COUNTED else None
                self.assertEqual(ss.m7_run_problems(sweep, arm, self.env(arm), threads_csv(), rows), ([], None), arm)

    def test_the_default_run_names_no_arm(self):
        self.assertEqual(ss.m7_run_problems("m7_default", "C", self.env(""), [], None), ([], None))
        reasons, external = ss.m7_run_problems("m7_default", "C", self.env("C"), [], None)  # with the snapshots
        self.assertTrue(reasons and external)
        self.assertEqual(ss.m7_run_problems("m7_snapshot", "C", self.env("C"), [], None), ([], None))

    def test_another_arm_stops_the_session(self):
        for variant, env in (("Ck", self.env("C")), ("C", self.env("Ck")), ("Cpt", self.env("Ct")),
                             ("B", {"m6": {"arm": "B"}}),  # a binary without M7's key
                             ("B", None)):  # an unreadable env.json
            reasons, external = ss.m7_run_problems("m7_main", variant, env, threads_csv(), counters(*THREADS))
            self.assertEqual(len(reasons), 1, variant)
            self.assertEqual(external, "a run's options are not its arm's")

    def test_rule_5_is_the_counted_arms_only(self):
        missing = counters(*THREADS[1:])
        reasons, external = ss.m7_run_problems("m7_main", "Bk", self.env("Bk"), threads_csv(), missing)
        self.assertEqual((len(reasons), external), (1, None))  # an invalid run, repeated; not a wrong setup
        self.assertEqual(ss.m7_run_problems("m7_main", "B", self.env("B"), threads_csv(), missing), ([], None))
        reasons, _ = ss.m7_run_problems("m7_counters", "Ck", self.env("Ck"), threads_csv(), None)
        self.assertEqual(len(reasons), 1)


class Judge(InTmp):
    """Session.judge on a finished M7 run, with its files."""

    def judged(self, phase="m7_main", arm="Ck", env_arm=None, code=0, made=True, counter_rows=None,
               snapshots=None):
        d = Path(tempfile.mkdtemp(dir="."))
        s = session(d)
        s.clock_offset = lambda: (0.0, 0.001)
        s.pc = lambda command, **kw: ""
        s.trips, s.fixed = {"soc-thermal": 85000}, dict.fromkeys(ss.DOMAINS, 0)
        run_dir = d / "run"
        start, end = dt.datetime(2026, 11, 28, 0, 0, 0, tzinfo=UTC), dt.datetime(2026, 11, 28, 0, 2, 31, tzinfo=UTC)
        o = ss.Outcome()
        o.code = code
        if made:
            run_dir.mkdir()
            (run_dir / "summary.csv").write_text(
                "key,value\n" + "".join(f"freq_min_{k},1\n" for k in ss.DOMAINS)
                + "warmup_start_utc,2026-11-28T00:00:10Z\nwindow_end_utc,2026-11-28T00:02:30Z\n", encoding="utf-8")
            (run_dir / "channels.csv").write_text("channel,decoder_drops,ended_early\n1,0,0\n", encoding="utf-8")
            (run_dir / "samples.csv").write_text("t_s,temp_mc_soc-thermal\n1,40000\n", encoding="utf-8")
            (run_dir / "env.json").write_text(json.dumps({"m7": {"arm": arm if env_arm is None else env_arm}}),
                                              encoding="utf-8")
            (run_dir / "threads.csv").write_text("tid,comm,affinity,cpu_s_in_window\n" + "".join(
                f"{t},{c},\"4-7\",1.0\n" for t, c in THREADS), encoding="utf-8")
            rows = counters(*THREADS) if counter_rows is None else counter_rows
            (run_dir / "counters.csv").write_text(
                "# run_kind: m7\n" + ",".join(rows[0]) + "\n" + "".join(",".join(r.values()) + "\n" for r in rows),
                encoding="utf-8")
            if phase != "m7_default":
                write_snapshots(run_dir, **(snapshots or {}))
        s.judge(o, run_dir, phase, arm, 8, "main", start, end, 0)
        close(s)
        return o

    def test_a_valid_counted_run_with_its_snapshots(self):
        o = self.judged()
        self.assertEqual((o.reasons, o.external, o.snapshots), ([], None, "complete"))

    def test_rule_5_makes_it_invalid(self):
        o = self.judged(counter_rows=counters(*THREADS, **{"102": {"read_ok": "0"}}))
        self.assertEqual(len(o.reasons), 1)
        self.assertIn("rule 5", o.reasons[0])
        self.assertIsNone(o.external)

    def test_snapshots_never_decide_validity(self):
        o = self.judged(arm="C", snapshots={"complete": ("0", "1")})
        self.assertEqual((o.reasons, o.external, o.snapshots), ([], None, "missing: snapshot 1 is incomplete"))
        o = self.judged(arm="B", snapshots={"drop": ("snapshot_meta.csv",)})
        self.assertEqual((o.reasons, o.snapshots), ([], "missing: no readable snapshot_meta.csv"))

    def test_the_default_run_has_none(self):
        o = self.judged(phase="m7_default", arm="C", env_arm="")
        self.assertEqual((o.reasons, o.snapshots), ([], "off"))

    def test_a_run_that_is_not_its_arms_stops_the_session(self):
        o = self.judged(arm="Ck", env_arm="C")
        self.assertEqual(o.external, "a run's options are not its arm's")

    def test_a_refused_run_stops_the_session(self):
        o = self.judged(phase="m7_aux", arm="Cpt", code=1, made=False)
        self.assertEqual(o.external, "bench_main refused an M7 run's options")
        self.assertTrue(o.snapshots.startswith("missing: "))  # recorded with every attempt
        o = self.judged(phase="m7_aux", arm="Cpt", code=1)  # it failed later: an invalid run, repeated
        self.assertEqual((o.reasons, o.external), (["bench exit 1"], None))


class Commands(InTmp):
    """bench_main's command line for M7's runs, and runs.tsv's snapshots column (Session.attempt, Session.run)."""

    def bench_command(self, phase, arm, channels, condition, run_kind, freeze=F7):
        d = Path(tempfile.mkdtemp(dir="."))
        s = session(d, freeze_m7=freeze)
        cmds = []

        def sh(cmd, out_file, timeout=120):
            cmds.append(cmd)
            if cmd[0] == "./build/bench_main":
                raise ss.Interrupted("signal SIGTERM")
            return 0

        s.sh = sh
        with redirect_stdout(io.StringIO()), self.assertRaises(ss.Stop):
            s.run(phase, arm, channels, condition, run_kind)
        close(s)
        return [c for c in cmds if c[0] == "./build/bench_main"][0], d

    def test_the_commands(self):
        source = ["--source", "rtsp://10.88.0.1:8554/ch{n}"]
        times = ["--warmup", "1", "--duration", "1"]
        cmd, d = self.bench_command("m7_main", "Ck", 8, "main", "m7")
        self.assertEqual(cmd, ["./build/bench_main", "--variant", "C", "--thread-counters", "on", "--snapshot", "on",
                               "--channels", "8", *source, "--condition", "main", *times, "--run-kind", "m7",
                               "--repetition", "1", "--freeze-commit", ss.FREEZE_M1_COMMIT, "--freeze-m7-commit", F7,
                               "--out", str(d / "01_m7_main_Ck_8ch_r1")])
        cmd, d = self.bench_command("m7_aux", "Cpt", 5, "aux", "m7")
        self.assertEqual(cmd[1:8], ["--variant", "C", "--post", "neon", "--time-rknn", "on", "--snapshot"])
        self.assertEqual(cmd[-4:], ["--freeze-m7-commit", F7, "--out", str(d / "01_m7_aux_Cpt_5ch_r1")])
        for phase, arm, ch, args in (("m7_default", "C", 2, ["--variant", "C"]),
                                     ("m7_snapshot", "C", 2, ["--variant", "C", "--snapshot", "on"]),
                                     ("m7_counters", "Ck", 8,
                                      ["--variant", "C", "--thread-counters", "on", "--snapshot", "on"])):
            cmd, d = self.bench_command(phase, arm, ch, "main", "check", freeze="none")
            self.assertEqual(cmd, ["./build/bench_main", *args, "--channels", str(ch), *source, "--condition", "main",
                                   *times, "--run-kind", "check", "--repetition", "1", "--freeze-commit",
                                   ss.FREEZE_M1_COMMIT, "--freeze-m7-commit", "none",
                                   "--out", str(d / f"01_{phase}_{arm}_{ch}ch")])
            self.assertNotIn("--freeze-m6-commit", cmd)  # bench_main refuses another milestone's freeze commit
        cmd, _ = self.bench_command("m6", "Ct", 8, "main", "m6")  # M6's as before
        self.assertNotIn("--freeze-m7-commit", cmd)
        self.assertNotIn("--snapshot", cmd)

    def test_runs_tsv_has_the_snapshots_column_in_m7_sessions(self):
        for phase, column in (("m7_aux", True), ("m6", False)):
            d = Path(tempfile.mkdtemp(dir="."))
            s = session(d)

            def attempt(name, *args):
                o = ss.Outcome()
                o.code, o.snapshots = 0, "missing: snapshot 2 is incomplete"
                now = dt.datetime(2026, 11, 28, tzinfo=UTC)
                return o, d / name, 40000, now, now

            s.attempt = attempt
            with redirect_stdout(io.StringIO()):
                self.assertEqual(s.run(phase, "C", 5, "aux", "m7", 1)[0], True)
            close(s)
            row = (d / "runs.tsv").read_text(encoding="utf-8").rstrip("\n").split("\t")
            self.assertEqual(len(row), 20 if column else 19, phase)
            if column:
                self.assertEqual(row[-1], "missing: snapshot 2 is incomplete")

    def test_the_header_of_runs_tsv(self):
        d = Path(tempfile.mkdtemp(dir="."))
        with mock.patch.object(ss, "INVENTORY_SHA256", ss.hashlib.sha256(b"x").hexdigest()), \
                mock.patch.dict(os.environ, {"COOLING": "fan", "AMBIENT_C": "24"}), \
                mock.patch.object(ss, "MANIFEST", "manifest.tsv"):  # the board's is outside the working directory
            for f in ("build/bench_main", "build/CMakeCache.txt", ss.ASSIGNMENT, ss.MANIFEST, ss.INVENTORY):
                Path(f).parent.mkdir(parents=True, exist_ok=True)
                Path(f).write_text("x", encoding="utf-8")
            for name, command, sweep, column in (("a", "sweep", "m7_main", True), ("b", "m7-check", None, True),
                                                 ("c", "sweep", "m6", False)):
                s = ss.Session(types.SimpleNamespace(out=str(d / name), command=command, sweep=sweep))
                for f in (s.runs, s.started, s.log):
                    f.close()
                header = (d / name / "runs.tsv").read_text(encoding="utf-8").rstrip("\n").split("\t")
                self.assertEqual(header[-2:], ["replaces", "snapshots"] if column else ["end_utc", "replaces"], name)
                self.assertEqual((s.freeze_m7, s.snapshot_proc, s.idle_snapshot), ("none", None, "not taken"))


class Code(InTmp):
    """M7 §9: a protocol session runs freeze-m7's code, built at the checkout's commit; m7_main comes first."""

    def git(self, head="h" * 40, diff=0, status=""):
        def run(args):
            if args[0] == "rev-parse":
                return 0, head
            if args[0] == "diff":
                self.assertEqual(args[:4], ["diff", "--quiet", F7, "HEAD"])
                self.assertEqual(tuple(args[5:]), ss.M7_CODE_PATHS)
                return diff, ""
            return 0, status
        return run

    def problem(self, built="h" * 40, **kw):
        with mock.patch.object(ss, "build_commit", lambda: built):
            return ss.m7_code_problem(F7, self.git(**kw))

    def test_freeze_m7s_code_built_at_the_checkout(self):
        self.assertIsNone(self.problem())

    def test_another_build_other_code_or_local_changes(self):
        self.assertIn("build this commit (M7 §9)", self.problem(built="a" * 40))
        self.assertIn("differ from freeze-m7's", self.problem(diff=1))
        self.assertIn("local changes", self.problem(status=" M tools/stream_session.py"))
        self.assertIn("an m7 session runs from a git checkout", ss.m7_code_problem(F7, lambda args: (128, "")))

    def test_the_code_paths_and_tag(self):
        self.assertEqual(ss.M7_CODE_PATHS, ("src", "tools", "tests", "CMakeLists.txt"))  # M7 §9
        self.assertEqual(ss.M7_CODE_TAG, "freeze-m7")

    def test_the_gate(self):
        seen, done = [], {"m7_main": "new", "m7_aux": "new"}
        registered = {"freeze-m7": F7}
        with mock.patch.object(ss, "registered_commit", lambda tag: registered.get(tag, "none")), \
                mock.patch.object(ss, "m7_code_problem", lambda commit: seen.append(commit)), \
                mock.patch.object(ss, "progress", lambda name: done[name]):
            self.assertIsNone(ss.m7_order_problem("m7_main"))
            self.assertEqual(seen, [F7])
            self.assertIn("m7_main comes before m7_aux and is not complete (M7 §4)", ss.m7_order_problem("m7_aux"))
            done["m7_main"] = "started"
            self.assertIn("m7_main comes before m7_aux", ss.m7_order_problem("m7_aux"))
            done["m7_main"] = "complete"
            self.assertIsNone(ss.m7_order_problem("m7_aux"))
            self.assertEqual(ss.m7_order_problem("m7_main"), "m7_main is complete")
            done["m7_aux"] = "complete"
            self.assertEqual(ss.m7_order_problem("m7_aux"), "m7_aux is complete")
            with mock.patch.object(ss, "M7_CODE_TAG", "amend-A99"):  # a rerun whose amendment has no row yet
                self.assertIn("no row for amend-A99, whose code M7's runs use (M7 §9)", ss.m7_order_problem("m7_main"))
            registered.clear()
            self.assertIn("freeze-m7 comes before M7's runs", ss.m7_order_problem("m7_main"))
            self.assertIn("(M7 §8)", ss.m7_order_problem("m7_aux"))

    def test_the_gate_from_env_md(self):
        Path("docs/notes").mkdir(parents=True)
        Path(ss.ENV_MD).write_text(tss.env_md(), encoding="utf-8")
        self.assertIn("freeze-m7 comes before", ss.m7_order_problem("m7_main"))
        Path(ss.ENV_MD).write_text(tss.env_md(f"| `freeze-m7` | `{F7}` | 2026-11-28T00:00:00Z |"), encoding="utf-8")
        with mock.patch.object(ss, "m7_code_problem", lambda commit: f"code {commit}"):
            self.assertEqual(ss.m7_order_problem("m7_main"), f"code {F7}")


class FakeProcess:
    def __init__(self, code=0, on_poll=None):
        self.code, self.on_poll, self.killed = code, on_poll, False

    def poll(self):
        if self.on_poll:
            self.on_poll()
        return None if self.code is None and not self.killed else self.code

    def kill(self):
        self.killed = True

    def wait(self):
        return -9 if self.killed else self.code


class IdleSnapshot(InTmp):
    """M7 §4: the idle pair of snapshots in the idle period, before the idle baseline begins."""

    def test_when(self):
        at = ss.idle_snapshot_second(600)
        self.assertEqual(at, 329)
        self.assertEqual(at + ss.IDLE_SNAPSHOT_SPAN_S + ss.IDLE_SNAPSHOT_MARGIN_S, 600 - ss.IDLE_BASELINE_S)
        self.assertEqual(ss.IDLE_SNAPSHOT_SPAN_S, 121)  # M7 §4
        self.assertEqual(ss.idle_snapshot_second(271), 0)
        for idle_s in (270, 120, 60):
            self.assertIsNone(ss.idle_snapshot_second(idle_s), idle_s)

    def test_the_command(self):
        self.assertEqual(ss.idle_snapshot_command(Path("s/idle_snapshot"), "m7", F7),
                         ["build/m7_idle_snapshot", str(Path("s/idle_snapshot")), "--span", "121", "--run-kind", "m7",
                          "--freeze-m7-commit", F7])
        self.assertEqual(ss.idle_snapshot_command(Path("x"), "check", "none"),
                         ["build/m7_idle_snapshot", "x", "--span", "121", "--run-kind", "check"])

    def started(self, process=None, error=None, run_kind="m7", freeze=F7):
        d = Path(tempfile.mkdtemp(dir="."))
        s = session(d, run_kind=run_kind, freeze_m7=freeze)
        calls = []

        def popen(cmd, **kw):
            calls.append((cmd, kw))
            if error:
                raise error
            return process

        with mock.patch.object(ss.subprocess, "Popen", popen), redirect_stdout(io.StringIO()):
            s.start_idle_snapshot()
            s.finish_idle_snapshot()
        close(s)
        return s, calls, d

    def test_a_pair_that_completes(self):
        d = Path(tempfile.mkdtemp(dir="."))
        process = FakeProcess(0, on_poll=lambda: write_snapshots(d / "idle_snapshot"))  # the tool wrote its files
        s = session(d, run_kind="m7")
        with mock.patch.object(ss.subprocess, "Popen", lambda cmd, **kw: process), redirect_stdout(io.StringIO()):
            s.start_idle_snapshot()
            s.finish_idle_snapshot()
        close(s)
        self.assertEqual(s.idle_snapshot, "complete")
        self.assertIsNone(s.snapshot_proc)
        self.assertTrue((d / "idle_snapshot.log").exists())

    def test_the_tools_command_line(self):
        s, calls, d = self.started(FakeProcess(0), run_kind="check", freeze="none")
        (cmd, kw), = calls
        self.assertEqual(cmd, ss.idle_snapshot_command(d / "idle_snapshot", "check", "none"))
        self.assertEqual(kw["stderr"], ss.subprocess.STDOUT)
        self.assertEqual(s.idle_snapshot, "missing: no readable snapshot_meta.csv")  # it exited 0 and wrote nothing

    def test_a_pair_that_fails_is_recorded_and_the_session_goes_on(self):
        s, _, _ = self.started(FakeProcess(1))
        self.assertEqual(s.idle_snapshot, "failed: exit 1 (idle_snapshot.log)")
        s, _, _ = self.started(error=OSError("no build/m7_idle_snapshot"))
        self.assertEqual(s.idle_snapshot, "failed: no build/m7_idle_snapshot")

    def test_a_pair_that_still_runs_when_the_baseline_begins_is_stopped(self):
        process = FakeProcess(None)
        s, _, _ = self.started(process)
        self.assertTrue(process.killed)
        self.assertIn("still ran when the idle baseline began", s.idle_snapshot)

    def test_the_session_stops_it_at_its_end(self):
        d = Path(tempfile.mkdtemp(dir="."))
        s = session(d, server_launched=False)
        s.snapshot_proc = process = FakeProcess(None)
        s.sh = lambda cmd, out_file, timeout=120: 0
        with mock.patch.object(ss.signal, "signal"), mock.patch.object(ss.signal, "SIGHUP", 1, create=True), \
                redirect_stdout(io.StringIO()):
            s.end()
        close(s)
        self.assertTrue(process.killed)

    def idle_period(self, command, sweep, idle_s=600):
        """Session.start up to the end of its idle period: [(event, seconds slept so far)]."""
        Path("docs/notes").mkdir(parents=True, exist_ok=True)
        Path(ss.INVENTORY).write_text("kernel: 6.1\n", encoding="utf-8")
        Path(ss.ENV_MD).write_text(tss.env_md(f"| `freeze-m7` | `{F7}` | 2026-11-28T00:00:00Z |"), encoding="utf-8")
        d = Path(tempfile.mkdtemp(dir="."))
        s = session(d, args=types.SimpleNamespace(command=command, sweep=sweep, idle_s=idle_s, gate_s=60),
                    run_kind="m7")
        slept, events = [0], []

        def sh(cmd, out_file, timeout=120, err_file=None):
            text = {"tools/inventory.sh": "kernel: 6.1\n", "tools/set_perf_mode.sh": "policy0_khz: 1\n",
                    "tools/collect_env.sh": "thermal_soc-thermal: temp_mc=40000 trip0_mc=85000\n"}[cmd[0]]
            Path(out_file).write_text(text, encoding="utf-8")
            return 0

        def start_snapshot():
            events.append(("start", slept[0]))
            s.snapshot_proc = FakeProcess(0, on_poll=lambda: events.append(("finish", slept[0])))

        read_text = pathlib.Path.read_text

        def proc_read_text(path, *a, **kw):
            if path.as_posix() in ("/proc/stat", "/proc/schedstat"):
                events.append(("baseline", slept[0]))
                return "cpu0 1 0 0 0 0 0 0 0 0 0\n"
            return read_text(path, *a, **kw)

        s.sh, s.soc, s.start_server, s.start_idle_snapshot = sh, (lambda: 40000), (lambda: None), start_snapshot
        with mock.patch.object(ss.time, "sleep", lambda t: slept.__setitem__(0, slept[0] + t)), \
                mock.patch.object(pathlib.Path, "read_text", proc_read_text), redirect_stdout(io.StringIO()):
            s.start()
        close(s)
        return events, s

    def test_inside_the_idle_period_before_the_idle_baseline(self):
        events, s = self.idle_period("sweep", "m7_main")
        self.assertEqual(events[:3], [("start", 329), ("finish", 480), ("baseline", 480)])  # 480: the last 120 s
        self.assertEqual(events[-1], ("baseline", 600))  # and the period is not lengthened
        self.assertEqual(s.freeze_m7, F7)  # M7 §8: the sweep's runs pass freeze-m7's commit
        events, s = self.idle_period("m7-check", None)
        self.assertEqual(events[0], ("start", 329))
        self.assertEqual(s.freeze_m7, "none")  # m7-check's runs pass none

    def test_a_short_idle_period_cannot_hold_it(self):
        events, s = self.idle_period("sweep", "m7_aux", idle_s=200)
        self.assertNotIn("start", [e for e, _ in events])
        self.assertEqual(s.idle_snapshot, "not taken: an idle period of 200 s cannot hold it before the idle baseline")

    def test_no_pair_in_other_sessions(self):
        events, s = self.idle_period("sweep", "m6")
        self.assertEqual([e for e, _ in events], ["baseline", "baseline", "baseline", "baseline"])
        self.assertEqual(s.freeze_m7, "none")


class Probe(InTmp):
    """M7 §3, §4: the probe once, after m7_aux's last run, in the same session."""

    def test_the_command(self):
        self.assertEqual(ss.m7_probe_command(Path("s/probe"), "m7", F7),
                         [sys.executable, "tools/m7_probe.py", "run", "--out", str(Path("s/probe")), "--run-kind", "m7",
                          "--freeze-m7-commit", F7])

    def probed(self, sh, smoke=False, complete=True):
        calls = []

        def record(cmd, out_file, timeout=120):
            calls.append((cmd, out_file, timeout))
            return sh(cmd)

        s = types.SimpleNamespace(smoke=smoke, out=Path("s"), run_kind="m7", freeze_m7=F7, sh=record,
                                  say=lambda msg: None)
        return ss.m7_probe(s, complete), calls

    def test_exit_codes_are_recorded(self):
        result, calls = self.probed(lambda cmd: 0)
        self.assertEqual(result, "exit 0")
        self.assertEqual(calls, [(ss.m7_probe_command(Path("s/probe"), "m7", F7), Path("s/probe.log"),
                                  ss.M7_PROBE_TIMEOUT_S)])
        result, _ = self.probed(lambda cmd: 3)
        self.assertEqual(result, "failed: exit 3, see probe.log (M7 §5: the sweep's runs stand)")

    def test_a_probe_that_cannot_run_does_not_stop_the_session(self):
        def fail(cmd):
            raise ss.Stop("python3: timed out")
        result, _ = self.probed(fail)
        self.assertTrue(result.startswith("failed: python3: timed out"))

    def test_the_operator_stops_the_session(self):
        def interrupt(cmd):
            raise ss.Interrupted("signal SIGTERM")
        with self.assertRaises(ss.Interrupted):
            self.probed(interrupt)

    def test_not_in_a_smoke_test_or_before_the_sweep_is_complete(self):
        result, calls = self.probed(lambda cmd: 0, smoke=True)
        self.assertEqual((result, calls), ("not run: a smoke test runs no probe (M7 §8 step 3)", []))
        result, calls = self.probed(lambda cmd: 0, complete=False)
        self.assertEqual((result, calls), ("not run: m7_aux is not complete", []))

    def swept(self, sweep, stop_at=None, earlier=()):
        """cmd_sweep in a protocol session of sweep, whose runs are all valid: (calls in order, result rows)."""
        out = Path("results/m7", f"20261128T060000Z_{sweep}")
        out.mkdir(parents=True)
        for name, reps in earlier:
            d = Path("results/m7", name)
            d.mkdir(parents=True)
            rows = [(r, a) for c, r, order in ss.sweep_plan(sweep) if r in reps for a in order]
            (d / "runs.tsv").write_text(
                "seq\tname\tphase\tvariant\tchannels\trepetition\tattempt\tvalid\tpassed\n" + "".join(
                    f"{i}\t{i:02d}_{sweep}_{a}_r{r}\t{sweep}\t{a}\t{ss.M7_POINTS[sweep][0]}\t{r}\t1\t1\t\n"
                    for i, (r, a) in enumerate(rows, 1)), encoding="utf-8")
        (out / "runs.tsv").write_text("seq\tname\tphase\tvariant\tchannels\trepetition\tattempt\tvalid\tpassed\n",
                                      encoding="utf-8")
        calls, results = [], []

        def run(phase, variant, channels, condition, run_kind, repetition, first_attempt=1, replaces=""):
            calls.append(("run", variant, repetition))
            if len(calls) == stop_at:
                raise ss.Stop("the server is down")
            with open(out / "runs.tsv", "a", encoding="utf-8") as f:
                f.write(f"{len(calls)}\tx{len(calls)}\t{phase}\t{variant}\t{channels}\t{repetition}\t1\t1\t\n")
            return True, out / f"x{len(calls)}"

        def sh(cmd, out_file, timeout=120):
            calls.append(("probe", cmd[1], out_file))
            return 0

        s = types.SimpleNamespace(args=types.SimpleNamespace(sweep=sweep, counts=None, command="sweep"), smoke=False,
                                  run_kind="m7", out=out, freeze_m7=F7, idle_snapshot="complete", run=run, sh=sh,
                                  say=lambda msg: None, write_result=lambda rows, kind: results.append(rows))
        with mock.patch.object(ss, "write_idle_baseline"):
            try:
                ss.cmd_sweep(s)
            except ss.Stop:
                pass
        return calls, dict(results[0])

    def test_after_m7_auxs_last_run(self):
        calls, rows = self.swept("m7_aux")
        self.assertEqual(len(calls), 19)
        self.assertEqual(calls[-1], ("probe", "tools/m7_probe.py",
                                     Path("results/m7/20261128T060000Z_m7_aux/probe.log")))
        self.assertEqual([c[0] for c in calls[:-1]], ["run"] * 18)
        self.assertEqual((rows["probe"], rows["session_status"], rows["repetitions_left"]),
                         ("exit 0", "completed", "none"))
        self.assertEqual(rows["order_r3"], "Rc C B Bd Cpt Ct")
        self.assertEqual(rows["idle_snapshot"], "complete")

    def test_in_the_session_that_completes_m7_aux(self):
        calls, rows = self.swept("m7_aux", earlier=[("20261127T000000Z_m7_aux", (1, 2))])
        self.assertEqual([c[2] for c in calls[:-1]], [3] * 6)  # repetition 3, then the probe
        self.assertEqual(calls[-1][0], "probe")
        self.assertEqual(rows["probe"], "exit 0")

    def test_not_when_the_session_stops(self):
        calls, rows = self.swept("m7_aux", stop_at=7)
        self.assertNotIn("probe", [c[0] for c in calls])
        self.assertEqual(rows["probe"], "not run: the session stopped before m7_aux's last run")
        self.assertEqual(rows["session_status"], "stopped")

    def test_not_after_m7_main(self):
        calls, rows = self.swept("m7_main")
        self.assertEqual([c[0] for c in calls], ["run"] * 24)
        self.assertNotIn("probe", rows)
        self.assertEqual(rows["arms"], " ".join(MAIN))


class Records(InTmp):
    """result.csv's header and the idle baseline's in M7's sessions (M7 §8)."""

    def test_result_header(self):
        d = Path(tempfile.mkdtemp(dir="."))
        for command, sweep, duration, settings in (("sweep", "m7_main", 120, "yes"), ("m7-check", None, 10, "yes"),
                                                   ("m7-check", None, 120, "no")):
            with self.subTest(command=command, duration=duration):
                s = session(d, freeze_m7=F7 if sweep else "none")
                s.args = types.SimpleNamespace(command=command, sweep=sweep, **{**ss.PROTOCOL, "duration": duration})
                s.code_commit = "abc"
                s.run_header = {"prereg_v1_commit": ss.PREREG_V1_COMMIT, "freeze_m1_commit": ss.FREEZE_M1_COMMIT}
                close(s)
                with mock.patch.dict(os.environ, {"COOLING": "fan", "AMBIENT_C": "24"}), redirect_stdout(io.StringIO()):
                    s.write_result([("x", "1")], "m7" if sweep else "check")
                header, rows = ss.read_csv(d / "result.csv")
                self.assertEqual(list(header)[4:7], ["prereg_m7_commit", "freeze_m7_commit", "run_kind"])
                self.assertEqual((header["prereg_m7_commit"], header["freeze_m7_commit"]),
                                 (ss.PREREG_M7_COMMIT, F7 if sweep else "none"))
                self.assertNotIn("prereg_m6_commit", header)
                self.assertTrue({r["key"]: r["value"] for r in rows}["protocol_settings"].startswith(settings))

    def test_idle_baseline(self):
        Path("prereg").mkdir()
        Path("prereg/AMENDMENTS.md").write_text("## A17 — 2026-11-24 — title\n", encoding="utf-8")
        stat = "cpu0 1 0 0 0 0 0 0 0 0 0\n"
        before, after = tss.SCHEDSTAT.format(cpu0=0, cpu4=0), tss.SCHEDSTAT.format(cpu0=2_000_000_000, cpu4=0)
        start = dt.datetime(2026, 11, 28, 1, 0, 0, tzinfo=UTC)
        for command, sweep, freeze in (("sweep", "m7_aux", F7), ("m7-check", None, "none")):
            s = types.SimpleNamespace(args=types.SimpleNamespace(command=command, sweep=sweep), out=Path("."),
                                      code_commit="abc", freeze_m7=freeze,
                                      idle=[start, 0.0, stat, before, 120.0, stat, after])
            with mock.patch.object(ss.os, "sysconf", return_value=100, create=True), \
                    mock.patch.object(ss.subprocess, "run", return_value=types.SimpleNamespace(stdout="yes\n")):
                ss.write_idle_baseline(s, "m7" if sweep else "check")
            header, rows = ss.read_csv(Path("idle_baseline.csv"))
            self.assertEqual(list(header)[3:6], ["freeze_m1_commit", "prereg_m7_commit", "freeze_m7_commit"])
            self.assertEqual(header["freeze_m7_commit"], freeze)
            self.assertEqual({r["key"]: r["value"] for r in rows}["schedstat_cpu_total_s"], "2.000")  # M2 §3.2

    def test_m7_rows(self):
        d = Path(tempfile.mkdtemp(dir="."))
        (d / "inventory.diff").write_text("--- a\n+++ b\n-x: 1\n+x: 2\n", encoding="utf-8")
        s = types.SimpleNamespace(args=types.SimpleNamespace(sweep="m7_main"), out=d, idle_snapshot="failed: exit 1")
        with mock.patch.object(ss, "M7_NOT_RUN", ss.M7_COUNTED):
            rows = ss.m7_rows(s)
        self.assertEqual(rows, [("arms", "B Bd Rc C"), ("order_r1", "B Bd Rc C"), ("order_r2", "C Rc Bd B"),
                                ("order_r3", "Bd C Rc B"), ("arms_not_run", "Bk Bdk Rck Ck"),
                                ("inventory_same_as_m1", "no"), ("inventory_changed_lines", "2"),
                                ("idle_snapshot", "failed: exit 1")])


class Main(InTmp):
    """main()'s arguments and gates for M7's commands."""

    def main_with(self, argv, err, **patches):
        """ss.main(argv) with the session and its commands mocked, and patches of stream_session's names:
        (exit code, the Session mock, the cmd_m7_check mock, the cmd_sweep mock)."""
        made = mock.MagicMock()
        made.out = Path(tempfile.mkdtemp(dir="."))
        with ExitStack() as stack:
            for cm in (mock.patch.object(ss.signal, "signal"), mock.patch.object(ss.signal, "SIGHUP", 1, create=True),
                       redirect_stderr(err), *(mock.patch.object(ss, k, v) for k, v in patches.items())):
                stack.enter_context(cm)
            session = stack.enter_context(mock.patch.object(ss, "Session", return_value=made))
            check = stack.enter_context(mock.patch.object(ss, "cmd_m7_check"))
            sweep = stack.enter_context(mock.patch.object(ss, "cmd_sweep"))
            code = ss.main(argv)
        return code, session, check, sweep

    def test_m7_check_needs_its_part(self):
        for argv in (["m7-check"], ["m6-check", "--part", "rest"], ["sweep", "m7_main", "--part", "counters"],
                     ["m7-check", "--part", "all"]):
            with self.assertRaises(SystemExit) as cm, redirect_stderr(io.StringIO()):
                ss.main(argv)
            self.assertEqual(cm.exception.code, 2, argv)

    def test_m7_checks_records(self):
        err = io.StringIO()
        for part in ss.M7_CHECK_PARTS:
            code, session, check, _ = self.main_with(["m7-check", "--part", part], err,
                                                     redo_pending=lambda: ["main"])  # outside the others' order
            self.assertEqual(code, 0, err.getvalue())
            args = session.call_args[0][0]
            self.assertRegex(args.out, rf"^results/check/\d{{8}}T\d{{6}}Z_m7_check_{part}$")
            self.assertEqual((args.duration, args.part), (10, part))  # M7 §8 step 3: windows of 10 s
            self.assertEqual(session.return_value.run_kind, "check")
            check.assert_called_once()

    def test_a_sweep_waits_for_freeze_m7(self):
        Path("docs/notes").mkdir(parents=True)
        Path(ss.ENV_MD).write_text(tss.env_md(), encoding="utf-8")
        err = io.StringIO()
        for sweep in ("m7_main", "m7_aux"):
            code, session, _, _ = self.main_with(["sweep", sweep], err, redo_pending=lambda: ["main"])
            self.assertEqual(code, 2)
            session.assert_not_called()
        self.assertIn("not starting: freeze-m7 comes before M7's runs", err.getvalue())
        self.assertNotIn("has a repetition to redo", err.getvalue())

    def test_a_protocol_sweep_and_a_smoke_test(self):
        err = io.StringIO()
        code, session, _, sweep = self.main_with(["sweep", "m7_aux"], err, m7_order_problem=lambda name: None)
        self.assertEqual(code, 0, err.getvalue())
        self.assertRegex(session.call_args[0][0].out, r"^results/m7/\d{8}T\d{6}Z_m7_aux$")
        self.assertEqual((session.return_value.run_kind, session.return_value.smoke), ("m7", False))
        code, session, _, _ = self.main_with(["sweep", "m7_main", "--idle-s", "300"], err)
        self.assertEqual(code, 0, err.getvalue())
        self.assertRegex(session.call_args[0][0].out, r"^results/check/\d{8}T\d{6}Z_m7_main_smoke$")
        self.assertEqual((session.return_value.run_kind, session.return_value.smoke), ("check", True))


PREREG_M7, BETWEEN, FREEZE_M7, LATER = ("2026-11-25T08:33:00Z", "2026-11-26T00:00:00Z", "2026-11-28T00:00:00Z",
                                        "2026-11-28T03:00:00Z")
M7_ROWS = [f"| `prereg-m7` | `c40d` | {PREREG_M7} |", f"| `freeze-m7` | `f7f7` | {FREEZE_M7} |"]


class Order(unittest.TestCase):
    """tools/check_prereg_order.py with run kind m7 and M7's two stages (M7 §8)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c = Case(Path(self.tmp.name), freeze=AFTER, extra_rows=M7_ROWS)

    def tearDown(self):
        self.tmp.cleanup()

    def session(self, base, start, kind="m7", freeze="f7f7", idle=None, probe=None):
        """An M7 session's records: a run, the idle pair of snapshots and the probe's, each with its header."""
        m7 = {"prereg_m7_commit": "c40d", "freeze_m7_commit": freeze}
        self.c.csv(f"{base}/01_m7_aux_B_5ch_r1/summary.csv", start, kind=kind, **m7)
        self.c.write(f"{base}/01_m7_aux_B_5ch_r1/snapshot1_stat.txt", "cpu 1 2 3\n")  # inherits the run's header
        self.c.csv(f"{base}/idle_snapshot/snapshot_meta.csv", idle or start, kind=kind, **m7)
        self.c.write(f"{base}/idle_snapshot/snapshot2_interrupts.txt", "0: 1\n")
        self.c.csv(f"{base}/probe/probe_costs.csv", probe or start, kind=kind, **m7)

    def test_an_m7_session_after_freeze_m7(self):
        self.session("m7/20261128T030000Z_m7_aux", LATER)
        code, out = self.c.run()
        self.assertEqual(code, 0, out)
        self.assertIn("5 files, 0 problems", out)

    def test_an_m7_run_before_freeze_m7(self):
        self.c.csv("m7/s/summary.csv", BETWEEN, kind="m7", prereg_m7_commit="c40d", freeze_m7_commit="f7f7")
        code, out = self.c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("a m7 run that starts 2026-11-26T00:00:00Z, not after freeze-m7", out)

    def test_an_idle_pair_or_a_probe_before_freeze_m7(self):
        for idle, probe, where in ((BETWEEN, None, "idle_snapshot"), (None, BETWEEN, "probe")):
            c = Case(Path(tempfile.mkdtemp(dir=self.tmp.name)), freeze=AFTER, extra_rows=M7_ROWS)
            self.c, keep = c, self.c
            self.session("m7/20261128T030000Z_m7_aux", LATER, idle=idle, probe=probe)
            code, out = c.run()
            self.c = keep
            self.assertEqual(code, 1, out)
            self.assertIn(where, out)
            self.assertIn("not after freeze-m7", out)

    def test_check_runs_between_the_stages_name_prereg_m7_only(self):
        self.session("check/20261126T000000Z_m7_check_rest", BETWEEN, kind="check", freeze="none")
        self.assertEqual(self.c.run()[0], 0, self.c.run()[1])

    def test_a_record_that_names_prereg_m7_before_it(self):
        self.c.csv("check/20261120T000000Z_m6_check/summary.csv", AFTER, kind="check", prereg_m7_commit="c40d")
        code, out = self.c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("names prereg-m7 but starts", out)

    def test_a_check_record_that_names_freeze_m7_before_it(self):
        self.session("check/s", BETWEEN, kind="check", freeze="f7f7")
        code, out = self.c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("names freeze-m7 but starts", out)

    def test_another_commit_than_the_tags(self):
        self.c.csv("m7/s/summary.csv", LATER, kind="m7", prereg_m7_commit="c40d", freeze_m7_commit="0000")
        code, out = self.c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("gives 0000 as freeze-m7's commit", out)

    def test_an_m7_run_without_a_freeze_m7_row(self):
        c = Case(Path(tempfile.mkdtemp(dir=self.tmp.name)), freeze=AFTER, extra_rows=M7_ROWS[:1])
        c.csv("m7/s/summary.csv", LATER, kind="m7", prereg_m7_commit="c40d")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("freeze-m7 has no reference time", out)

    def test_an_m7_record_outside_results_m7(self):
        self.c.csv("check/s/snapshot_meta.csv", LATER, kind="m7", prereg_m7_commit="c40d", freeze_m7_commit="f7f7")
        code, out = self.c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("header says run kind 'm7', its path says 'check'", out)


class Headers(unittest.TestCase):
    def test_the_prereg_commit_is_the_tags(self):
        rows = ss.registration_rows((ROOT / "docs/notes/env.md").read_text(encoding="utf-8"))
        self.assertEqual(rows["prereg-m7"][0], ss.PREREG_M7_COMMIT)
        self.assertIn(f'kPreregM7Commit = "{ss.PREREG_M7_COMMIT}"',
                      (ROOT / "src/core/report.h").read_text(encoding="utf-8"))

    def test_the_arms_in_the_binary_and_in_the_session_tool(self):
        rules = (ROOT / "src/core/run_rules.cpp").read_text(encoding="utf-8")
        self.assertIn("B, Bd, Rc, C, Bk, Bdk, Rck, Ck, Ct or Cpt", rules)
        self.assertEqual(sorted(set(MAIN) | set(AUX)), sorted(("B", "Bd", "Rc", "C", "Bk", "Bdk", "Rck", "Ck", "Ct",
                                                               "Cpt")))
        self.assertTrue(set(ss.M7_COUNTED) <= set(MAIN) and not set(ss.M7_COUNTED) & set(AUX))  # M7 §7: main only
        self.assertEqual(ss.M7_PHASES, ("m7_main", "m7_aux", "m7_counters", "m7_default", "m7_snapshot", "m7_cpt",
                                        "m7_check"))
        self.assertFalse(set(ss.M7_PHASES) & set(ss.M6_PHASES))  # an M7 run passes no freeze-m6 commit

    def test_the_idle_tool_and_the_probe(self):
        self.assertIn("--span S", (ROOT / "tools/m7_idle_snapshot.cpp").read_text(encoding="utf-8"))
        self.assertEqual((ss.M7_IDLE_SNAPSHOT, ss.M7_PROBE), ("build/m7_idle_snapshot", "tools/m7_probe.py"))


if __name__ == "__main__":
    unittest.main()
