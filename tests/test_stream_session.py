#!/usr/bin/env python3
"""Tests for tools/stream_session.py: its pure parts, and how a run reacts to the operator's signal.

Usage: python3 tests/test_stream_session.py
"""
import datetime as dt
import hashlib
import io
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import stream_session as ss  # noqa: E402

UTC = dt.timezone.utc


class Trials:
    """A fake trial: passed(count, phase, k) for the k-th trial at that count and phase."""

    def __init__(self, passed):
        self.passed = passed
        self.calls = []

    def __call__(self, count, phase):
        k = sum(1 for c, p in self.calls if (c, p) == (count, phase))
        self.calls.append((count, phase))
        return self.passed(count, phase, k)


class LimitSearch(unittest.TestCase):
    def test_threshold(self):
        for limit in range(0, 33):
            with self.subTest(limit=limit):
                t = Trials(lambda c, p, k, limit=limit: c <= limit)
                self.assertEqual(ss.limit_search(t), limit)

    def test_order_from_above_the_start(self):
        t = Trials(lambda c, p, k: c <= 10)
        self.assertEqual(ss.limit_search(t), 10)
        self.assertEqual(t.calls, [(8, "search"), (9, "search"), (10, "search"), (11, "search")]
                         + [(10, "confirm")] * 3 + [(11, "confirm")] * 3)

    def test_order_from_below_the_start(self):
        t = Trials(lambda c, p, k: c <= 6)
        self.assertEqual(ss.limit_search(t), 6)
        self.assertEqual(t.calls[:3], [(8, "search"), (7, "search"), (6, "search")])

    def test_cap(self):
        t = Trials(lambda c, p, k: True)
        self.assertEqual(ss.limit_search(t), 32)
        self.assertNotIn(33, [c for c, _ in t.calls])  # no runs above the cap
        self.assertEqual(t.calls.count((32, "confirm")), 3)

    def test_none_passes(self):
        t = Trials(lambda c, p, k: False)
        self.assertEqual(ss.limit_search(t), 0)
        self.assertEqual(t.calls.count((1, "confirm")), 3)  # no runs at 0

    def test_up_reuses_the_runs_it_has(self):
        # The search stops at 10 (11 fails once), but 11 then passes 2 of 3 and 12 fails.
        t = Trials(lambda c, p, k: c <= 10 or (c == 11 and (p == "confirm" and k != 1)))
        self.assertEqual(ss.limit_search(t), 11)
        self.assertEqual(t.calls.count((11, "confirm")), 3)  # not repeated after moving up
        self.assertEqual(t.calls.count((12, "confirm")), 3)

    def test_down(self):
        # 10 passes the search but only 1 of 3 confirmations; 9 passes.
        t = Trials(lambda c, p, k: c <= 9 or (c == 10 and (p == "search" or k == 0)))
        self.assertEqual(ss.limit_search(t), 9)
        self.assertEqual(t.calls.count((10, "confirm")), 3)  # kept as the new n + 1
        self.assertEqual(t.calls.count((9, "confirm")), 3)

    def test_two_of_three_at_n_holds(self):
        t = Trials(lambda c, p, k: c <= 9 or (c == 10 and k != 2))
        self.assertEqual(ss.limit_search(t), 10)

    def test_main_set(self):
        self.assertEqual(ss.main_set(32), [1, 2, 4, 8, 12, 16, 32])
        self.assertEqual(ss.main_set(16), [1, 2, 4, 8, 12, 16])
        self.assertEqual(ss.main_set(10), [1, 2, 4, 8, 10])
        self.assertEqual(ss.main_set(0), [])


def log(*lines):
    return "\n".join(f"2026/11/02 {line}" for line in lines) + "\n"


# A normal run of one channel as mediamtx logs it, in KST (UTC+9). The bench process starts at
# 08:59:58.5 UTC, its warm-up at 09:00:01.2, and its window ends at 09:00:05.2 (ServerEvents below).
NORMAL = [
    "17:59:59 INF [RTSP] [conn 10.88.0.2:4000] opened",
    "17:59:59 INF [path ch1] runOnDemand command started",
    "17:59:59 INF [RTSP] [session aa] created by 127.0.0.1:5000",
    "17:59:59 INF [RTSP] [session aa] is publishing to path 'ch1'",
    "18:00:00 INF [RTSP] [session bb] created by 10.88.0.2:4000",
    "18:00:00 INF [RTSP] [session bb] is reading from path 'ch1', with TCP, 1 track (H264)",
    "18:00:05 INF [RTSP] [conn 10.88.0.2:4000] closed: read: connection reset by peer",
    "18:00:05 INF [RTSP] [session bb] destroyed: not in use",
    "18:00:06 INF [path ch1] runOnDemand command stopped: not needed by anyone",
    "18:00:06 INF [RTSP] [session aa] destroyed: torn down by 127.0.0.1:5000",
]


class ServerEvents(unittest.TestCase):
    run_start = dt.datetime(2026, 11, 2, 8, 59, 58, 500000, tzinfo=UTC)
    warmup_start = dt.datetime(2026, 11, 2, 9, 0, 1, 200000, tzinfo=UTC)
    window_end = dt.datetime(2026, 11, 2, 9, 0, 5, 200000, tzinfo=UTC)

    def events(self, lines):
        parsed = ss.parse_server_log(log(*lines), "+0900")
        return ss.server_events(parsed, self.run_start, self.warmup_start, self.window_end)

    def test_parse_offset(self):
        (t, level, msg, raw), = ss.parse_server_log(log(NORMAL[0]), "+0900")
        self.assertEqual(t, dt.datetime(2026, 11, 2, 8, 59, 59, tzinfo=UTC))
        self.assertEqual(level, "INF")
        self.assertEqual(raw, "2026/11/02 " + NORMAL[0])
        (t, _, _, _), = ss.parse_server_log(log(NORMAL[0]), "-0130")
        self.assertEqual(t, dt.datetime(2026, 11, 2, 19, 29, 59, tzinfo=UTC))

    def test_normal_run_has_no_events(self):
        # The bench's own disconnect at 18:00:05 KST is in the window's last second, 09:00:05 UTC.
        self.assertEqual(self.events(NORMAL), ([], []))

    def test_reader_disconnect_in_the_window(self):
        lines = NORMAL[:6] + ["18:00:03 INF [RTSP] [conn 10.88.0.2:4000] closed: EOF",
                              "18:00:03 INF [RTSP] [session bb] destroyed: not in use"] + NORMAL[6:]
        events, _ = self.events(lines)
        self.assertEqual([e.split(":")[0] for e in events], ["reader disconnect", "reader disconnect"])

    def test_publisher_exit_and_restart(self):
        lines = NORMAL[:6] + ["18:00:02 INF [path ch1] runOnDemand command exited: exit status 1",
                              "18:00:02 INF [RTSP] [session aa] destroyed: terminated",
                              "18:00:03 INF [path ch1] runOnDemand command started"] + NORMAL[6:]
        events, _ = self.events(lines)
        self.assertEqual([e.split(":")[0] for e in events], ["publisher exit", "publisher exit", "publisher restart"])

    def test_first_start_inside_the_interval_is_not_an_event(self):
        # The publisher's first start happens to be stamped in the warm-up's first second.
        lines = ["18:00:01 INF [path ch1] runOnDemand command started"]
        self.assertEqual(self.events(lines), ([], []))

    def test_errors_count_and_warnings_are_kept(self):
        events, warnings = self.events(NORMAL[:6] + ["18:00:02 ERR [path ch1] something failed",
                                                     "18:00:03 WAR [RTSP] [session bb] 3 RTP packets lost"])
        self.assertEqual(len(events), 1)
        self.assertIn("something failed", events[0])
        self.assertEqual(len(warnings), 1)

    def test_events_before_the_warmup_or_after_the_window_do_not_count(self):
        lines = ["18:00:00 ERR before the warm-up", "18:00:06 ERR after the window"]
        self.assertEqual(self.events(lines), ([], []))

    def test_the_window_ends_second_counts_except_the_benchs_disconnect(self):
        # 09:00:05 UTC holds the window's end (.2): a publisher exit there counts, the bench's
        # disconnect does not.
        lines = NORMAL[:6] + ["18:00:05 INF [path ch1] runOnDemand command exited: exit status 1"] + NORMAL[6:8]
        events, _ = self.events(lines)
        self.assertEqual([e.split(":")[0] for e in events], ["publisher exit"])

    def test_earlier_runs_do_not_count(self):
        lines = ["17:59:00 INF [path ch1] runOnDemand command started"] + NORMAL
        self.assertEqual(self.events(lines), ([], []))

    def test_first_start_is_tracked_from_shortly_before_the_run(self):
        # The PC's clock lags: the first start is stamped before the bench process started.
        lines = ["17:59:57 INF [path ch1] runOnDemand command started",
                 "18:00:03 INF [path ch1] runOnDemand command started"]
        events, _ = self.events(lines)
        self.assertEqual([e.split(":")[0] for e in events], ["publisher restart"])

    def test_session_of_another_host(self):
        lines = ["17:59:59 INF [RTSP] [session cc] created by 192.168.123.5:6000",
                 "18:00:02 INF [RTSP] [session cc] destroyed: not in use"]
        events, _ = self.events(lines)
        self.assertEqual([e.split(":")[0] for e in events], ["session of another host closed"])


class Criteria(unittest.TestCase):
    def test_channels_pass(self):
        row = {"drift_max_s": "0.000", "decoder_drops": "0", "ended_early": "0"}
        self.assertTrue(ss.channels_pass([row, dict(row, drift_max_s="1.000")]))
        self.assertFalse(ss.channels_pass([row, dict(row, drift_max_s="1.033")]))
        self.assertFalse(ss.channels_pass([row, dict(row, decoder_drops="1")]))
        self.assertFalse(ss.channels_pass([]))

    def test_unsaturated(self):
        # 0.8 × P_cap = 4661/30 ≈ 155.37 (A7)
        self.assertTrue(ss.unsaturated(5, "aux", 32))     # 150
        self.assertFalse(ss.unsaturated(6, "aux", 32))    # 180
        self.assertTrue(ss.unsaturated(31, "main", 32))   # 155
        self.assertFalse(ss.unsaturated(32, "main", 32))  # 160
        self.assertFalse(ss.unsaturated(5, "aux", 4))     # above L

    def test_trip_samples(self):
        samples = [{"temp_mc_soc-thermal": "74999"}, {"temp_mc_soc-thermal": "75000"}]
        self.assertEqual(ss.trip_samples(samples, {"soc-thermal": 75000}), 1)

    def test_clocks_below(self):
        fixed = {"policy0_khz": 1800000, "policy4_khz": 2208000, "policy6_khz": 2208000, "dmc_hz": 2112000000,
                 "npu_hz": 1000000000}
        summary = {f"freq_min_{k}": str(v) for k, v in fixed.items()}
        self.assertEqual(ss.clocks_below(summary, fixed), [])
        summary["freq_min_npu_hz"] = "800000000"
        self.assertEqual(ss.clocks_below(summary, fixed), ["npu_hz"])

    def test_thermal_problems(self):
        fixed = {"policy0_khz": 1800000, "policy4_khz": 2208000, "policy6_khz": 2208000, "dmc_hz": 2112000000,
                 "npu_hz": 1000000000}
        sample = {f"freq_{k}": str(v) for k, v in fixed.items()}
        sample.update({"t_s": "1.000", "temp_mc_soc-thermal": "60000", "temp_mc_npu-thermal": "50000"})
        trips = {"soc-thermal": 75000, "npu-thermal": 115000}
        self.assertEqual(ss.thermal_problems([sample], fixed, trips), [])
        hot = dict(sample, **{"temp_mc_soc-thermal": "75000"})
        slow = dict(sample, freq_policy4_khz="2016000")
        self.assertEqual(len(ss.thermal_problems([sample, hot, slow], fixed, trips)), 2)

    def test_loops_in_window(self):
        self.assertEqual(ss.loops_in_window(39.533333, 20, 120), 3)  # video ①
        self.assertEqual(ss.loops_in_window(23.8, 20, 120), 5)       # video ③: 23.8 ... 119
        self.assertEqual(ss.loops_in_window(150, 20, 120), 0)

    def test_parse_utc(self):
        self.assertEqual(ss.parse_utc("2026-11-02T08:30:01.123Z"),
                         dt.datetime(2026, 11, 2, 8, 30, 1, 123000, tzinfo=UTC))
        self.assertEqual(ss.parse_utc("2026-11-02T08:30:01Z"), dt.datetime(2026, 11, 2, 8, 30, 1, tzinfo=UTC))


class ClockProblem(unittest.TestCase):
    def test_bounds(self):
        self.assertIsNone(ss.clock_problem(0.015))
        self.assertIsNone(ss.clock_problem(-0.004))
        self.assertIsNone(ss.clock_problem(0.1))
        self.assertIsNotNone(ss.clock_problem(-0.006))  # the bench's disconnect could move into the window
        self.assertIsNotNone(ss.clock_problem(0.101))


class Inventory(unittest.TestCase):
    """The checks of A10: the board's inventory against the reference, and the stream PC's binaries."""

    def test_reference_is_the_one_the_session_expects(self):
        reference = Path(__file__).resolve().parent.parent / ss.INVENTORY
        self.assertEqual(hashlib.sha256(reference.read_bytes()).hexdigest(), ss.INVENTORY_SHA256)

    def test_same_inventory(self):
        self.assertIsNone(ss.inventory_diff("os: x\nlib a: 1\n", "os: x\nlib a: 1\n"))

    def test_different_inventory(self):
        diff = ss.inventory_diff("os: x\nlib a: 2\n", "os: x\nlib a: 1\n")
        self.assertIn("-lib a: 1\n", diff)
        self.assertIn("+lib a: 2\n", diff)

    def test_pc_binaries(self):
        out = "".join(f"{h}  /home/u/{name}\n" for name, h in ss.PC_BINARIES.items())
        self.assertEqual(ss.pc_binary_problems(out), [])
        changed = out.replace(ss.PC_BINARIES["ffmpeg"], "0" * 64)
        self.assertEqual(len(ss.pc_binary_problems(changed)), 1)
        self.assertIn("ffmpeg", ss.pc_binary_problems(changed)[0])
        self.assertIn("cannot hash", ss.pc_binary_problems(out.splitlines(True)[0])[0])


def rec(session, name, channels, repetition, variant, attempt, valid):
    return {"session": session, "name": name, "channels": channels, "repetition": repetition,
            "variant": variant, "attempt": attempt, "valid": valid}


class Sweeps(unittest.TestCase):
    """The order of §9.1 and the session rules of §9.1 and §9.3 for the sweeps of §12."""

    def test_channel_orders(self):
        self.assertEqual(ss.channel_order(ss.SWEEPS["main"][0]), [8, 1, 16, 4, 32, 2, 12])
        self.assertEqual(ss.channel_order(ss.SWEEPS["aux"][0]), [1, 4, 3, 2, 5])
        self.assertEqual(ss.channel_order(ss.SWEEPS["reference"][0]), [2, 1])

    def test_variant_order(self):
        self.assertEqual([ss.variant_order(("B", "C"), r) for r in (1, 2, 3)], [("B", "C"), ("C", "B"), ("B", "C")])
        self.assertEqual(ss.variant_order(("A",), 2), ("A",))

    def test_plan(self):
        plan = ss.sweep_plan("main")
        self.assertEqual(len(plan), 21)
        self.assertEqual(plan[:4], [(8, 1, ("B", "C")), (8, 2, ("C", "B")), (8, 3, ("B", "C")), (1, 1, ("B", "C"))])
        self.assertEqual(ss.sweep_plan("reference"), [(2, 1, ("A",)), (2, 2, ("A",)), (2, 3, ("A",)),
                                                      (1, 1, ("A",)), (1, 2, ("A",)), (1, 3, ("A",))])

    def test_complete_in_one_session(self):
        complete, pending = ss.sweep_state([rec("s1", "01", 8, 1, "B", 1, True), rec("s1", "02", 8, 1, "C", 1, True)],
                                           ("B", "C"))
        self.assertEqual((complete, pending), ({(8, 1)}, {}))

    def test_invalid_x2_is_final(self):
        records = [rec("s1", "01", 8, 1, "B", 1, False), rec("s1", "01r", 8, 1, "B", 2, False),
                   rec("s1", "02", 8, 1, "C", 1, True)]
        self.assertEqual(ss.sweep_state(records, ("B", "C")), ({(8, 1)}, {}))

    def test_session_ended_between_the_runs(self):
        complete, pending = ss.sweep_state([rec("s1", "05", 8, 2, "C", 1, True)], ("B", "C"))
        self.assertEqual(complete, set())
        self.assertEqual(pending, {(8, 2): ({"B": 1, "C": 1}, ["s1/05"])})

    def test_session_ended_before_a_repeat(self):
        # The invalid run's next attempt is its repeat; the other run starts again.
        records = [rec("s1", "01", 8, 1, "B", 1, True), rec("s1", "02", 8, 1, "C", 1, False)]
        self.assertEqual(ss.sweep_state(records, ("B", "C"))[1], {(8, 1): ({"B": 1, "C": 2}, ["s1/01", "s1/02"])})

    def test_a_finished_run_of_an_incomplete_repetition_starts_again(self):
        records = [rec("s1", "01", 8, 1, "B", 1, False), rec("s1", "01r", 8, 1, "B", 2, False)]
        self.assertEqual(ss.sweep_state(records, ("B", "C"))[1][(8, 1)][0], {"B": 1, "C": 1})

    def test_completed_in_the_next_session(self):
        records = [rec("s1", "02", 8, 1, "C", 1, False),
                   rec("s2", "01", 8, 1, "B", 1, True), rec("s2", "02r", 8, 1, "C", 2, True)]
        self.assertEqual(ss.sweep_state(records, ("B", "C")), ({(8, 1)}, {}))

    def test_the_last_session_decides_what_is_pending(self):
        records = [rec("s1", "01", 8, 1, "B", 1, True),
                   rec("s2", "01", 8, 1, "B", 1, True), rec("s2", "02", 8, 1, "C", 1, False)]
        self.assertEqual(ss.sweep_state(records, ("B", "C"))[1],
                         {(8, 1): ({"B": 1, "C": 2}, ["s1/01", "s2/01", "s2/02"])})

    def test_runs_split_over_sessions_do_not_complete_a_repetition(self):
        records = [rec("s1", "01", 8, 1, "B", 1, True), rec("s2", "02", 8, 1, "C", 1, True)]
        self.assertEqual(ss.sweep_state(records, ("B", "C"))[1][(8, 1)][0], {"B": 1, "C": 1})

    def test_load_records(self):
        with tempfile.TemporaryDirectory() as d:
            session = Path(d) / "20261104T000000Z_main"
            session.mkdir()
            cols = ["seq", "name", "phase", "variant", "channels", "condition", "repetition", "attempt", "exit",
                    "valid", "passed", "reasons", "warnings", "trip_samples", "gate_soc_mc", "clock_offset_ms",
                    "start_utc", "end_utc", "replaces"]
            row = dict.fromkeys(cols, "")
            row.update(seq="1", name="01_main_B_8ch_r1", phase="main", variant="B", channels="8", repetition="1",
                       attempt="1", valid="1")
            other = dict(row, phase="thermal")
            (session / "runs.tsv").write_text("\t".join(cols) + "\n" + "\t".join(row[c] for c in cols) + "\n"
                                              + "\t".join(other[c] for c in cols) + "\n", encoding="utf-8")
            self.assertEqual(ss.load_records([session, Path(d) / "missing"], ("main",)),
                             [dict(rec("20261104T000000Z_main", "01_main_B_8ch_r1", 8, 1, "B", 1, True),
                                   phase="main", passed=False, lost=False)])

    def test_the_last_attempt_counts_across_sessions(self):
        # C's first attempt failed in s1; s2 redid B and ended before C: C's next attempt is its repeat.
        records = [rec("s1", "01", 8, 1, "B", 1, True), rec("s1", "02", 8, 1, "C", 1, False),
                   rec("s2", "01", 8, 1, "B", 1, True)]
        self.assertEqual(ss.sweep_state(records, ("B", "C"))[1][(8, 1)][0], {"B": 1, "C": 2})

    def test_an_attempt_the_session_died_in(self):
        cols = ["seq", "name", "phase", "variant", "channels", "condition", "repetition", "attempt", "exit",
                "valid", "passed", "reasons", "warnings", "trip_samples", "gate_soc_mc", "clock_offset_ms",
                "start_utc", "end_utc", "replaces"]
        started_cols = ["seq", "name", "phase", "variant", "channels", "condition", "repetition", "attempt",
                        "start_utc"]
        with tempfile.TemporaryDirectory() as d:
            session = Path(d) / "20261104T000000Z_main"
            session.mkdir()
            done = dict.fromkeys(cols, "")
            done.update(seq="1", name="01_main_B_8ch_r1", phase="main", variant="B", channels="8",
                        repetition="1", attempt="1", valid="1")
            (session / "runs.tsv").write_text("\t".join(cols) + "\n" + "\t".join(done[c] for c in cols) + "\n",
                                              encoding="utf-8")
            started = [dict(done, attempt="1"), dict(done, seq="2", name="02_main_C_8ch_r1", variant="C")]
            (session / "started.tsv").write_text(
                "\t".join(started_cols) + "\n" + "".join("\t".join(r[c] for c in started_cols) + "\n" for r in started),
                encoding="utf-8")
            records = ss.load_records([session], ("main",))
            self.assertEqual([(r["name"], r["valid"], r["lost"]) for r in records],
                             [("01_main_B_8ch_r1", True, False), ("02_main_C_8ch_r1", False, True)])
            # The lost attempt is C's invalid first one: the next session repeats it, after B again.
            self.assertEqual(ss.sweep_state(records, ("B", "C"))[1][(8, 1)][0], {"B": 1, "C": 2})

    def test_sessions_by_name_and_order(self):
        here = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                for name in ("comparison/20261104T000000Z_main", "comparison/20261104T010000Z_main_smoke",
                             "comparison/20261104T020000Z_aux", "comparison/x_main", "check/20261104T030000Z_main"):
                    Path("results", name).mkdir(parents=True)
                self.assertEqual([p.name for p in ss.sweep_sessions("main")], ["20261104T000000Z_main"])
                self.assertEqual(ss.sweep_sessions("main", "results/comparison/20261104T000000Z_main"), [])
                self.assertEqual(ss.progress("main"), "started")  # a session without runs completes nothing
                self.assertEqual(ss.progress("reference"), "new")
                self.assertIsNone(ss.order_problem("main"))
                self.assertIn("main comes before aux", ss.order_problem("aux"))
                self.assertIn("main comes before h3", ss.order_problem("h3"))
                self.assertEqual(ss.redo_pending(), [])
                cols = ["seq", "name", "phase", "variant", "channels", "repetition", "attempt", "valid", "passed"]
                Path("results/comparison/20261104T000000Z_main/runs.tsv").write_text(
                    "\t".join(cols) + "\n" + "\t".join(["1", "01_main_B_8ch_r1", "main", "B", "8", "1", "1", "1", ""])
                    + "\n", encoding="utf-8")
                self.assertEqual(ss.redo_pending(), ["main"])  # B ran, C did not: the next session redoes both
            finally:
                os.chdir(here)

    def test_a_repeat_started_in_an_earlier_session(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            s = OperatorStop.session(None, d)
            o = ss.Outcome()
            o.reasons, o.code = ["bench exit -11"], -11
            s.attempt = lambda *a: (o, d / "x", 40000, dt.datetime.now(UTC), dt.datetime.now(UTC))
            with redirect_stdout(io.StringIO()):
                valid, _ = s.run("main", "C", 8, "main", "comparison", 1, first_attempt=2, replaces="s1/02")
            s.runs.close()
            s.started.close()
            s.log.close()
            rows = [r.split("\t") for r in (d / "runs.tsv").read_text(encoding="utf-8").splitlines()]
            self.assertFalse(valid)
            self.assertEqual(len(rows), 1)  # only the repeat, which makes it invalid x2
            self.assertEqual((rows[0][1], rows[0][7], rows[0][9], rows[0][18]), ("01_main_C_8ch_r1_repeat", "2", "0", "s1/02"))
            self.assertEqual(s.invalid_x2, ["01_main_C_8ch_r1"])

    def test_the_4k_sweep(self):
        self.assertEqual(ss.channel_order(ss.SWEEPS["4k"][0]), [1, 8, 4, 2])
        self.assertEqual(ss.SWEEPS["4k"][3], "m1.1")
        self.assertEqual(ss.ORDER[-1], "4k")
        expected = ss.VIDEO_SETS["4k"][3]
        served = "".join(f"ch{k}\t/home/u/media/bench4k/{v}\t{expected[v]}\n"
                         for k, v in enumerate(["pexels_36330861.mp4", "pexels_29130409.mp4", "pixabay_180386.mp4"], 1))
        self.assertEqual(ss.video_problems(served, expected), [])
        wrong = served.replace(expected["pixabay_180386.mp4"], "0" * 64)
        self.assertIn("pixabay_180386.mp4 with SHA-256", ss.video_problems(wrong, expected)[0])
        self.assertIn("not in the set", ss.video_problems("ch1\tx/pexels_29969146.mp4\tab\n", expected)[0])
        self.assertEqual(ss.video_problems("", expected), ["the server's assignment.tsv is empty"])
        two = "".join(line + "\n" for line in served.splitlines()[:2])
        self.assertEqual(ss.video_problems(two, expected), ["no channel serves pixabay_180386.mp4"])

    def test_the_4k_loop_check_must_have_protocol_settings(self):
        here = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                self.assertFalse(ss.loop_check_done("4k"))
                smoke = Path("results/check/20261104T000000Z_loop_4k")
                smoke.mkdir(parents=True)
                (smoke / "result.csv").write_text("# run_kind: check\nkey,value\npassed,1\n"
                                                  "protocol_settings,no: warmup 2 (protocol 20)\n", encoding="utf-8")
                self.assertFalse(ss.loop_check_done("4k"))
                real = Path("results/check/20261104T010000Z_loop_4k")
                real.mkdir()
                (real / "result.csv").write_text("key,value\npassed,0\nprotocol_settings,yes\n", encoding="utf-8")
                self.assertTrue(ss.loop_check_done("4k"))  # even though a video failed (A11)
            finally:
                os.chdir(here)

    def test_idle_baseline(self):
        before = "cpu  1 1 1 1 1 1 1 1 0 0\ncpu0 10 0 5 100 7 1 1 0 0 0\ncpu4 0 0 0 0 0 0 0 0 0 0\nintr 5\n"
        after = "cpu  9 9 9 9 9 9 9 9 0 0\ncpu0 20 2 5 300 9 2 1 1 0 0\ncpu4 100 0 0 0 0 0 0 0 0 0\n"
        b, a = ss.proc_stat_nonidle(before), ss.proc_stat_nonidle(after)
        self.assertEqual((b, a), ({0: 17, 4: 0}, {0: 31, 4: 100}))  # iowait and idle are not counted
        rows = dict(ss.idle_rows(b, a, 0.01))
        self.assertEqual((rows["system_cpu0_s"], rows["system_cpu4_s"]), ("0.140", "1.000"))
        self.assertEqual((rows["system_cpu0-3_s"], rows["system_cpu4-5_s"], rows["system_cpu6-7_s"]),
                         ("0.140", "1.000", "0.000"))
        self.assertEqual(rows["system_cpu_total_s"], "1.140")


def drive(limit_b, limit_c, limit=32, start=8, noisy=None):
    """Runs h3_schedule against variants that sustain up to their limit; noisy maps (variant, phase,
    channels, repetition) to an outcome that overrides it. Returns (runs, n)."""
    gen, runs = ss.h3_schedule(limit, start), []
    run = next(gen)
    try:
        while True:
            runs.append(run)
            ok = (noisy or {}).get(run[:4], run[2] <= {"B": limit_b, "C": limit_c}[run[0]])
            run = gen.send(ok)
    except StopIteration as e:
        return runs, e.value


def trials_of(runs, outcomes, session="s1"):
    """Trials of one session, all final, as h3_trials would give them."""
    return [{"session": session, "names": [f"{session}/{k:02d}"], "variant": r[0], "phase": r[1],
             "channels": r[2], "repetition": r[3], "final": True, "sustained": o}
            for k, (r, o) in enumerate(zip(runs, outcomes), 1)]


class H3(unittest.TestCase):
    """§10: the search, the confirmation and their order, and resuming them across sessions."""

    def test_sustained(self):
        row = {"inference_fps": "4.750", "drift_max_s": "1.000", "decoder_drops": "0"}
        self.assertTrue(ss.sustained([row, row]))
        self.assertFalse(ss.sustained([row, dict(row, inference_fps="4.749")]))
        self.assertFalse(ss.sustained([dict(row, drift_max_s="1.001")]))
        self.assertFalse(ss.sustained([dict(row, decoder_drops="1")]))
        self.assertFalse(ss.sustained([]))

    def test_search_alternates_and_confirms(self):
        runs, n = drive(limit_b=9, limit_c=11)
        self.assertEqual(n, {"B": 9, "C": 11})
        search = [r[:3] for r in runs if r[1] == "h3_search"]
        self.assertEqual(search, [("B", "h3_search", 8), ("C", "h3_search", 8), ("C", "h3_search", 9),
                                  ("B", "h3_search", 9), ("B", "h3_search", 10), ("C", "h3_search", 10),
                                  ("C", "h3_search", 11), ("C", "h3_search", 12)])
        confirm = [(r[0], r[2], r[3], r[4]) for r in runs if r[1] == "h3_confirm"]
        # Counts ascending; B needs 9 and 10, C needs 11 and 12: no count is shared.
        self.assertEqual([c[:2] for c in confirm], [("B", 9)] * 3 + [("B", 10)] * 3 + [("C", 11)] * 3 + [("C", 12)] * 3)
        self.assertTrue(all(c[3] is None for c in confirm))

    def test_shared_counts_are_interleaved(self):
        runs, n = drive(limit_b=10, limit_c=10)
        confirm = [(r[0], r[2], r[3], r[4]) for r in runs if r[1] == "h3_confirm"]
        self.assertEqual(confirm[:6], [("B", 10, 1, (10, 1)), ("C", 10, 1, (10, 1)), ("C", 10, 2, (10, 2)),
                                       ("B", 10, 2, (10, 2)), ("B", 10, 3, (10, 3)), ("C", 10, 3, (10, 3))])
        self.assertEqual([c[1] for c in confirm[6:]], [11] * 6)
        self.assertEqual(n, {"B": 10, "C": 10})

    def test_down_from_the_start_and_zero(self):
        runs, n = drive(limit_b=5, limit_c=0)
        self.assertEqual([r[2] for r in runs if r[1] == "h3_search" and r[0] == "B"], [8, 7, 6, 5])
        self.assertEqual([r[2] for r in runs if r[1] == "h3_search" and r[0] == "C"], list(range(8, 0, -1)))
        self.assertEqual(n, {"B": 5, "C": 0})
        self.assertFalse(any(r[1] == "h3_confirm" and r[0] == "C" and r[2] == 0 for r in runs))

    def test_cap(self):
        runs, n = drive(limit_b=32, limit_c=32)
        self.assertEqual(max(r[2] for r in runs), 32)  # no run above L
        self.assertEqual(n, {"B": 32, "C": 32})

    def test_confirmation_goes_up_and_down(self):
        # C's search stops at 10, but 2 of 3 runs at 11 sustain: n goes up to 11, then 12 is checked.
        up = {("C", "h3_search", 11, 1): False}
        runs, n = drive(limit_b=6, limit_c=11, noisy=up)
        self.assertEqual(n["C"], 11)
        # B's search reaches 7, but 2 of 3 runs at 7 fail: n goes down to 6, keeping the runs at 7.
        down = {("B", "h3_confirm", 7, 1): False, ("B", "h3_confirm", 7, 2): False}
        runs, n = drive(limit_b=7, limit_c=12, noisy=down)
        self.assertEqual(n["B"], 6)
        b7 = [r for r in runs if r[0] == "B" and r[1] == "h3_confirm" and r[2] == 7]
        self.assertEqual(len(b7), 3)  # not run again as the new n + 1

    def test_replay_continues_where_the_records_end(self):
        runs, n = drive(limit_b=9, limit_c=11)
        outcomes = [r[2] <= {"B": 9, "C": 11}[r[0]] for r in runs]
        for cut in (0, 1, 5, len(runs) - 1):
            _, nxt, done, continued, replaced = ss.h3_replay(trials_of(runs[:cut], outcomes[:cut]))
            self.assertEqual((nxt, done, continued, replaced), (runs[cut], None, {}, {}))
        _, nxt, done, _, _ = ss.h3_replay(trials_of(runs, outcomes))
        self.assertEqual((nxt, done), (None, n))

    def test_replay_rejects_records_out_of_order(self):
        runs, _ = drive(limit_b=9, limit_c=11)
        swapped = [runs[1], runs[0]] + runs[2:4]
        with self.assertRaises(ss.Stop):
            ss.h3_replay(trials_of(swapped, [True] * 4))

    def test_replay_an_unfinished_repeat(self):
        runs, _ = drive(limit_b=9, limit_c=11)
        trials = trials_of(runs[:3], [True] * 3)
        trials[2]["final"] = False  # invalid first attempt, then the session ended
        _, nxt, _, continued, replaced = ss.h3_replay(trials)
        self.assertEqual(nxt, runs[2])
        self.assertEqual(continued, {runs[2][:4]: 2})
        self.assertEqual(replaced, {runs[2][:4]: ["s1/03"]})

    def test_replay_a_split_pair(self):
        runs, _ = drive(limit_b=10, limit_c=10)
        first = next(k for k, r in enumerate(runs) if r[4] is not None)
        outcomes = [r[2] <= 10 for r in runs]
        # The session ended after the pair's first run: it is redone with its partner.
        trials = trials_of(runs[:first + 1], outcomes[:first + 1])
        _, nxt, _, continued, replaced = ss.h3_replay(trials)
        self.assertEqual((nxt, continued), (runs[first], {}))
        self.assertEqual(replaced, {runs[first][:4]: [f"s1/{first + 1:02d}"]})
        # The partner's first attempt was invalid: the first run is redone, the partner continues.
        trials = trials_of(runs[:first + 2], outcomes[:first + 2])
        trials[-1]["final"] = False
        _, nxt, _, continued, replaced = ss.h3_replay(trials)
        self.assertEqual(nxt, runs[first])
        self.assertEqual(continued, {runs[first + 1][:4]: 2})
        # The same pair, finished in the next session, counts.
        later = trials_of(runs[first:first + 2], outcomes[first:first + 2], session="s2")
        later[1]["names"] = ["s2/02_repeat"]
        _, nxt, _, continued, _ = ss.h3_replay(trials + later)
        self.assertEqual((nxt, continued), (runs[first + 2], {}))

    def test_a_redone_run_leaves_nothing_pending(self):
        runs, n = drive(limit_b=10, limit_c=10)
        outcomes = [r[2] <= 10 for r in runs]
        # s1 ends on an invalid first attempt of run 3; s2 repeats it and finishes H3.
        s1 = trials_of(runs[:3], outcomes[:3])
        s1[-1]["final"] = False
        s2 = trials_of(runs[2:], outcomes[2:], session="s2")
        _, nxt, done, continued, replaced = ss.h3_replay(s1 + s2)
        self.assertEqual((nxt, done, continued, replaced), (None, n, {}, {}))
        # A split pair, redone in full in the next session, leaves nothing either.
        first = next(k for k, r in enumerate(runs) if r[4] is not None)
        s1 = trials_of(runs[:first + 1], outcomes[:first + 1])
        s2 = trials_of(runs[first:], outcomes[first:], session="s2")
        _, nxt, done, continued, replaced = ss.h3_replay(s1 + s2)
        self.assertEqual((nxt, done, continued, replaced), (None, n, {}, {}))

    def test_trials_from_records(self):
        records = [dict(rec("s1", "01", 8, 1, "B", 1, False), phase="h3_search", passed=False),
                   dict(rec("s1", "01_repeat", 8, 1, "B", 2, True), phase="h3_search", passed=True),
                   dict(rec("s1", "02", 8, 1, "C", 1, False), phase="h3_search", passed=False),
                   dict(rec("s2", "01_repeat", 8, 1, "C", 2, False), phase="h3_search", passed=False)]
        trials = ss.h3_trials(records)
        self.assertEqual([(t["names"], t["final"], t["sustained"]) for t in trials],
                         [(["s1/01", "s1/01_repeat"], True, True), (["s1/02"], False, False),
                          (["s2/01_repeat"], True, False)])


class OperatorStop(unittest.TestCase):
    """A signal (Interrupted) during a run stops the session; the run is recorded, not repeated."""

    def session(self, d):
        s = ss.Session.__new__(ss.Session)
        s.args = types.SimpleNamespace(warmup=1, duration=1, gate_s=0, limit=None)
        s.out, s.seq, s.invalid_x2, s.server_dir, s.run_header = d, 0, [], "/logs", None
        s.log = open(d / "session.log", "w", encoding="utf-8")
        s.runs = open(d / "runs.tsv", "w", encoding="utf-8")
        s.started = open(d / "started.tsv", "w", encoding="utf-8")
        s.gate = lambda: 40000
        s.server_alive = lambda: True
        s.console_size = lambda: 0
        s.sh = lambda cmd, out_file, timeout=120: 0
        return s

    def run_and_read(self, s, d):
        with redirect_stdout(io.StringIO()), self.assertRaises(ss.Stop) as cm:
            s.run("search", "none", 8, "main", "calibration")
        s.runs.close()
        s.started.close()
        s.log.close()
        return cm.exception, (d / "runs.tsv").read_text(encoding="utf-8").splitlines()

    def test_during_the_bench(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            s = self.session(d)

            def sh(cmd, out_file, timeout=120):
                if cmd[0] == "./build/bench_main":
                    raise ss.Interrupted("signal SIGTERM")
                return 0

            s.sh = sh
            error, rows = self.run_and_read(s, d)
            self.assertIn("stopped by the operator", str(error))
            self.assertEqual(len(rows), 1)  # recorded once, not repeated
            self.assertIn("stopped by the operator (signal SIGTERM)", rows[0])
            self.assertEqual(rows[0].split("\t")[9], "0")  # not valid, so never counted as a pass
            # The bench had started, so started.tsv has the attempt.
            started = (d / "started.tsv").read_text(encoding="utf-8").splitlines()
            self.assertEqual([r.split("\t")[1] for r in started], ["01_search_none_8ch"])

    def test_while_judging(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            s = self.session(d)
            calls = []

            def alive():
                calls.append(1)
                if len(calls) == 2:  # the check after the run
                    raise ss.Interrupted("signal SIGHUP")
                return True

            s.server_alive = alive
            error, rows = self.run_and_read(s, d)
            self.assertEqual(len(rows), 1)
            self.assertIn("stopped by the operator before the run was judged", rows[0])

    def test_before_the_bench_records_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            s = self.session(d)

            def gate():
                raise ss.Interrupted("signal SIGTERM")

            s.gate = gate
            error, rows = self.run_and_read(s, d)
            self.assertIsInstance(error, ss.Interrupted)
            self.assertEqual(rows, [])
            # Nothing in started.tsv either: the next session starts the run afresh, not as a repeat.
            self.assertEqual((d / "started.tsv").read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
