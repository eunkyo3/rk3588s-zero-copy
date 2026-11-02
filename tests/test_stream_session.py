#!/usr/bin/env python3
"""Tests for the pure parts of tools/stream_session.py.

Usage: python3 tests/test_stream_session.py
"""
import datetime as dt
import sys
import unittest
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
        (t, level, msg), = ss.parse_server_log(log(NORMAL[0]), "+0900")
        self.assertEqual(t, dt.datetime(2026, 11, 2, 8, 59, 59, tzinfo=UTC))
        self.assertEqual(level, "INF")
        (t, _, _), = ss.parse_server_log(log(NORMAL[0]), "-0130")
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

    def test_earlier_runs_do_not_count(self):
        lines = ["17:59:00 INF [path ch1] runOnDemand command started"] + NORMAL
        self.assertEqual(self.events(lines), ([], []))


class Criteria(unittest.TestCase):
    def test_channels_pass(self):
        row = {"drift_max_s": "0.000", "decoder_drops": "0", "ended_early": "0"}
        self.assertTrue(ss.channels_pass([row, dict(row, drift_max_s="1.000")]))
        self.assertFalse(ss.channels_pass([row, dict(row, drift_max_s="1.033")]))
        self.assertFalse(ss.channels_pass([row, dict(row, decoder_drops="1")]))
        self.assertFalse(ss.channels_pass([dict(row, ended_early="1")]))
        self.assertFalse(ss.channels_pass([]))

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


if __name__ == "__main__":
    unittest.main()
