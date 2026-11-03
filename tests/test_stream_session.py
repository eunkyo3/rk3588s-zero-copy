#!/usr/bin/env python3
"""Tests for tools/stream_session.py: its pure parts, and how a run reacts to the operator's signal.

Usage: python3 tests/test_stream_session.py
"""
import datetime as dt
import hashlib
import io
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


class OperatorStop(unittest.TestCase):
    """A signal (Interrupted) during a run stops the session; the run is recorded, not repeated."""

    def session(self, d):
        s = ss.Session.__new__(ss.Session)
        s.args = types.SimpleNamespace(warmup=1, duration=1, gate_s=0, limit=None)
        s.out, s.seq, s.invalid_x2, s.server_dir, s.run_header = d, 0, [], "/logs", None
        s.log = open(d / "session.log", "w", encoding="utf-8")
        s.runs = open(d / "runs.tsv", "w", encoding="utf-8")
        s.gate = lambda: 40000
        s.server_alive = lambda: True
        s.console_size = lambda: 0
        s.sh = lambda cmd, out_file, timeout=120: 0
        return s

    def run_and_read(self, s, d):
        with redirect_stdout(io.StringIO()), self.assertRaises(ss.Stop) as cm:
            s.run("search", "none", 8, "main", "calibration")
        s.runs.close()
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


if __name__ == "__main__":
    unittest.main()
