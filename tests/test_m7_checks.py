"""Unit tests for tools/m7_checks.py: the rules of prereg/M7.md §8 step 3, on synthetic records.
Run: python3 -m unittest tests.test_m7_checks   (or pytest)
"""
import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import m7_checks as mc  # noqa: E402

EVENTS = ("cpu_cycles", "inst_retired", "l2d_cache_refill", "l3d_cache_refill", "stall_backend")
SHA = "a" * 64


def event_rows(names=EVENTS, found="1"):
    return [{"name": n, "found": found, "pmu_type": "7", "config": "0x11"} for n in names]


def counter(tid, comm, run_s, cycles=None, **over):
    """A counters.csv row of a thread that ran run_s, with the cycles of 2208 MHz unless given."""
    row = {"tid": str(tid), "comm": comm, "opened": "1", "open_errno": "0", "failed_event": "", "read_ok": "1",
           "enable_ok": "1", "disable_ok": "1",
           "time_enabled_ns": str(int(run_s * 1e9)), "time_running_ns": str(int(run_s * 1e9)),
           "run_ns_enable": "1000", "run_ns_disable": str(1000 + int(run_s * 1e9)),
           "cpu_cycles": str(int(run_s * mc.A76_HZ) if cycles is None else cycles), "inst_retired": "5",
           "l2d_cache_refill": "4", "l3d_cache_refill": "3", "stall_backend": "2"}
    row.update(over)
    return row


def run_rows():
    counted = [counter(1, "bench_main", 0.01), counter(2, "dec-1", 0.05), counter(3, "mpp_dec_parser", 0.03),
               counter(4, "pre-0", 0.3), counter(5, "npu-0", 0.1), counter(6, "metrics", 0.02)]
    threads = [{"tid": r["tid"], "comm": r["comm"], "affinity": "4-7", "cpu_s_in_window": "0.1"} for r in counted]
    return threads, counted


class Unchanged(unittest.TestCase):
    def setUp(self):
        self.objects = [{"source": s, "sha256_m7": SHA, "sha256_freeze_m6": SHA} for s in mc.UNCHANGED_SOURCES]
        self.dump = {"frames": "100"}
        for arm in mc.ARMS:
            for ext in (".u8", ".i8", ".detections"):
                self.dump[f"sha256_{arm}{ext}"] = arm + ext

    def test_equal_builds_pass(self):
        self.assertEqual(mc.unchanged_problems(self.objects, "f", "f", self.dump, dict(self.dump)), [])

    def test_the_list_is_step_2s(self):
        self.assertEqual(len(mc.UNCHANGED_SOURCES), 15)
        self.assertEqual(sum(s.startswith("src/preprocess/") for s in mc.UNCHANGED_SOURCES), 7)
        problems = mc.unchanged_problems(self.objects[1:], "f", "f", self.dump, self.dump)
        self.assertTrue(any("not the 15" in p for p in problems))

    def test_a_changed_object_fails(self):
        self.objects[3]["sha256_m7"] = "b" * 64
        problems = mc.unchanged_problems(self.objects, "f", "f", self.dump, self.dump)
        self.assertEqual(len(problems), 1)
        self.assertIn(mc.UNCHANGED_SOURCES[3], problems[0])

    def test_flags_frames_and_data(self):
        self.assertTrue(mc.unchanged_problems(self.objects, "f", "g", self.dump, self.dump))
        self.assertTrue(mc.unchanged_problems(self.objects, " ", " ", self.dump, self.dump))
        short = dict(self.dump, frames="99")
        self.assertTrue(any("99 frames" in p for p in mc.unchanged_problems(self.objects, "f", "f", short, self.dump)))
        for arm in mc.ARMS:
            other = dict(self.dump, **{f"sha256_{arm}.u8": "x"})
            problems = mc.unchanged_problems(self.objects, "f", "f", other, self.dump)
            self.assertEqual(problems, [f"{arm}: the input tensors differ from freeze-m6's build's"])
        missing = {k: v for k, v in self.dump.items() if k != "sha256_rc.detections"}
        self.assertEqual(len(mc.unchanged_problems(self.objects, "f", "f", missing, missing)), 1)


class Cpu0(unittest.TestCase):
    def record(self, idle, loop, **over):
        r = {"span_s": "10", "loop_s": "2", "clock_hz": "1800000000", "idle_cycles": str(idle),
             "loop_cycles": str(loop), "idle_read_ok": "1", "loop_read_ok": "1", "idle_time_enabled_ns": "9",
             "idle_time_running_ns": "9", "loop_time_enabled_ns": "9", "loop_time_running_ns": "9", "passed": "1"}
        r.update(over)
        return r

    def test_passes(self):
        self.assertEqual(mc.cpu0_problems(self.record(93877222, 3671023983)), [])

    def test_idle_limit(self):
        self.assertEqual(mc.cpu0_problems(self.record(899999999, 899999999 + 3600000000)), [])
        self.assertTrue(mc.cpu0_problems(self.record(900000000, 900000000 + 3600000000)))

    def test_difference_within_5_percent(self):
        self.assertEqual(mc.cpu0_problems(self.record(0, 3780000000)), [])
        self.assertEqual(mc.cpu0_problems(self.record(0, 3420000000)), [])
        self.assertTrue(mc.cpu0_problems(self.record(0, 3780000001)))
        self.assertTrue(mc.cpu0_problems(self.record(0, 3419999999)))

    def test_other_failures(self):
        self.assertTrue(mc.cpu0_problems(self.record(1, 3600000000, loop_read_ok="0")))
        self.assertTrue(mc.cpu0_problems(self.record(1, 3600000000, clock_hz="2208000000")))
        self.assertTrue(mc.cpu0_problems(self.record(1, 3600000000, passed="0")))
        self.assertTrue(mc.cpu0_problems({"idle_cycles": "1"}))


class Counters(unittest.TestCase):
    def test_passes(self):
        threads, counted = run_rows()
        self.assertEqual(mc.counters_problems(threads, counted, event_rows()), [])

    def test_groups(self):
        _, counted = run_rows()
        g = mc.counter_groups(counted, EVENTS)
        self.assertEqual(g["dec"]["threads"], 2)
        self.assertAlmostEqual(g["dec"]["run_s"], 0.08)
        self.assertEqual(g["dec"]["inst_retired"], 10)
        self.assertEqual(sorted(g), ["dec", "metrics", "npu", "pre", "rest"])
        self.assertEqual(g["dec"]["run_ns"], 80000000)

    def test_a_thread_without_a_group(self):
        threads, counted = run_rows()
        counted[3]["opened"] = "0"
        self.assertTrue(any("without a group" in p for p in mc.counters_problems(threads, counted, event_rows())))
        threads, counted = run_rows()
        self.assertTrue(mc.counters_problems(threads, counted[:-2] + counted[-1:], event_rows()))

    def test_multiplexed(self):
        threads, counted = run_rows()
        counted[1]["time_running_ns"] = "1"
        self.assertTrue(mc.counters_problems(threads, counted, event_rows()))

    def test_cycles_against_run_time(self):
        for comm, tid in (("dec-1", 2), ("npu-0", 5), ("pre-0", 4)):  # the two named groups, and one above 0.2 s
            threads, counted = run_rows()
            row = next(r for r in counted if r["tid"] == str(tid))
            row["cpu_cycles"] = str(int(int(row["cpu_cycles"]) * 1.2))
            problems = mc.counters_problems(threads, counted, event_rows())
            self.assertEqual(len(problems), 1, comm)
            self.assertIn("not within 5%", problems[0])
        threads, counted = run_rows()  # a group below 0.2 s that is not one of the two is not judged
        counted[0]["cpu_cycles"] = "1"
        self.assertEqual(mc.counters_problems(threads, counted, event_rows()), [])

    def test_a_group_at_exactly_0_2_s_is_judged(self):
        threads, counted = run_rows()
        counted[0].update(run_ns_disable=str(1000 + 200000000), cpu_cycles="1")  # bench_main: the rest group
        self.assertEqual(len(mc.counters_problems(threads, counted, event_rows())), 1)
        counted[0]["run_ns_disable"] = str(1000 + 199999999)
        self.assertEqual(mc.counters_problems(threads, counted, event_rows()), [])

    def test_the_metrics_thread_is_not_compared(self):
        threads, counted = run_rows()  # it runs on cpu0 at 1800 MHz, whatever it ran
        counted[5].update(run_ns_disable=str(1000 + 10**9), cpu_cycles=str(1800 * 10**6))
        self.assertEqual(mc.counters_problems(threads, counted, event_rows()), [])

    def test_the_tolerance_is_5_percent(self):
        threads, counted = run_rows()  # npu-0 ran 0.1 s: 220,800,000 cycles, and 5% of that is 11,040,000
        for cycles, ok in ((231840000, True), (231840001, False), (209760000, True), (209759999, False)):
            counted[4]["cpu_cycles"] = str(cycles)
            self.assertEqual(mc.counters_problems(threads, counted, event_rows()) == [], ok, cycles)

    def test_an_event_that_counts_nothing_on_the_decoder(self):
        threads, counted = run_rows()  # M7 §1: it is left out, and the check is judged on the rest
        counted[1]["l3d_cache_refill"] = counted[2]["l3d_cache_refill"] = "0"
        self.assertEqual(mc.events_to_leave_out(counted, event_rows()), ["l3d_cache_refill"])
        self.assertEqual(mc.counters_problems(threads, counted, event_rows()), [])
        counted[2]["l3d_cache_refill"] = "1"  # one thread of the group is enough
        self.assertEqual(mc.events_to_leave_out(counted, event_rows()), [])

    def test_a_needed_event_that_counts_nothing_fails(self):
        threads, counted = run_rows()
        counted[1]["inst_retired"] = counted[2]["inst_retired"] = "0"
        self.assertEqual(mc.events_to_leave_out(counted, event_rows()), [])
        self.assertEqual(mc.counters_problems(threads, counted, event_rows()),
                         ["inst_retired counts zero on every thread of the decoder's group"])

    def test_an_event_that_cannot_be_opened(self):
        threads, counted = run_rows()
        for r in counted:
            r.update(opened="0", open_errno="2", failed_event="stall_backend", read_ok="0")
        self.assertEqual(mc.events_to_leave_out(counted, event_rows()), ["stall_backend"])
        self.assertTrue(mc.counters_problems(threads, counted, event_rows()))  # no groups: run it again
        missing = event_rows()
        missing[2]["found"] = "0"
        self.assertEqual(mc.events_to_leave_out(run_rows()[1], missing), ["l2d_cache_refill"])
        missing[0]["found"] = "0"  # cpu_cycles is never left out
        self.assertEqual(mc.events_to_leave_out(run_rows()[1], missing), ["l2d_cache_refill"])

    def test_the_needed_events(self):
        threads, counted = run_rows()
        self.assertTrue(mc.counters_problems(threads, counted, event_rows(EVENTS[1:])))
        self.assertTrue(mc.counters_problems(threads, counted, event_rows(found="0")))
        fewer = event_rows(EVENTS[:4])  # an event left out (M7 §1): the group has the events that remain
        self.assertEqual(mc.counters_problems(threads, counted, fewer), [])

    def test_no_run_time(self):
        threads, counted = run_rows()
        counted[2]["run_ns_disable"] = "-1"
        self.assertTrue(any("no run time" in p for p in mc.counters_problems(threads, counted, event_rows())))

    def test_no_decoder_or_npu_group(self):
        threads, counted = run_rows()
        keep = [r for r in counted if not r["comm"].startswith("npu")]
        problems = mc.counters_problems([t for t in threads if not t["comm"].startswith("npu")], keep, event_rows())
        self.assertTrue(any("npu group" in p for p in problems))


class Order(unittest.TestCase):
    def times(self, **minutes):
        base = dt.datetime(2026, 11, 26, tzinfo=dt.timezone.utc)
        order = {"m7_cpu0_check": 0, "m7_check_counters": 1, "m7_unchanged": 2, "m7_probe_smoke": 3,
                 "m7_check_rest": 4}
        order.update(minutes)
        return {k: base + dt.timedelta(minutes=v) for k, v in order.items()}

    def test_the_registered_order(self):
        self.assertEqual(mc.order_problems(self.times()), [])
        self.assertEqual(mc.order_problems(self.times(m7_cpu0_check=1, m7_check_counters=0)), [])

    def test_counters_first(self):
        self.assertTrue(mc.order_problems(self.times(m7_unchanged=0.5)))
        self.assertTrue(mc.order_problems(self.times(m7_probe_smoke=1)))

    def test_the_arms_runs_last(self):
        self.assertTrue(mc.order_problems(self.times(m7_check_rest=2.5)))

    def test_committed_before_the_rest(self):
        t = self.times()
        ok = {"m7_cpu0_check": t["m7_unchanged"], "m7_check_counters": t["m7_unchanged"]}
        self.assertEqual(mc.committed_problems(ok, t["m7_check_rest"]), [])
        late = dict(ok, m7_check_counters=t["m7_check_rest"])
        self.assertEqual(len(mc.committed_problems(late, t["m7_check_rest"])), 1)
        self.assertEqual(len(mc.committed_problems(dict(ok, m7_cpu0_check=None), t["m7_check_rest"])), 1)


class Builds(unittest.TestCase):
    A, B = "a" * 40, "b" * 40

    def commits(self, early, late):
        return {"m7_cpu0_check": early, "m7_check_counters session": early, "m7_check_counters Ck": early,
                "m7_unchanged dump": late, "m7_check_rest session": late, "m7_probe_smoke": late}

    def judge(self, commits, files=()):
        return mc.build_problems(commits, same_code=lambda c: c == self.B, changed=lambda a, b: list(files))

    def test_one_build(self):
        self.assertEqual(self.judge(self.commits(self.B, self.B)), [])
        self.assertTrue(self.judge(self.commits(self.A, self.A)))  # not the checkout's code
        self.assertTrue(self.judge(self.commits(self.B, self.B + "-dirty")))

    def test_the_counters_checks_from_an_earlier_build(self):
        allowed = ["tools/stream_session.py", "tools/m7_checks.py", "src/core/perf_counters.h", "tests/test_m7.py"]
        self.assertEqual(self.judge(self.commits(self.A, self.B), allowed), [])
        problems = self.judge(self.commits(self.A, self.B), allowed + ["src/core/pipeline.cpp"])
        self.assertEqual(len(problems), 1)
        self.assertIn("src/core/pipeline.cpp", problems[0])

    def test_what_is_not_allowed(self):
        mixed = dict(self.commits(self.A, self.B), m7_cpu0_check="c" * 40)
        self.assertTrue(self.judge(mixed))  # the two counters' checks from two builds
        self.assertTrue(self.judge(self.commits(self.A + "-dirty", self.B)))
        late = dict(self.commits(self.A, self.B), m7_probe_smoke=self.A)
        self.assertTrue(self.judge(late))  # a later record from the earlier build
        self.assertTrue(mc.build_problems(self.commits(self.A, self.B), same_code=lambda c: True,
                                          changed=lambda a, b: None))  # git cannot compare


if __name__ == "__main__":
    unittest.main()
