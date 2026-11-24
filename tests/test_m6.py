#!/usr/bin/env python3
"""Tests for M6's parts (prereg/M6.md): the m6 sweep and m6-check of tools/stream_session.py (§1, §4), the order
checks of tools/check_prereg_order.py (§8), and tools/m6_checks.py (§8 step 3). tools/m6_report.py has its own
tests.

Usage: python3 tests/test_m6.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import m6_checks as m6c  # noqa: E402
import stream_session as ss  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_check_prereg_order import AFTER, Case  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PREREG_M6, BETWEEN, FREEZE_M6, LATER = ("2026-11-23T23:47:43Z", "2026-11-24T06:00:00Z", "2026-11-26T00:00:00Z",
                                        "2026-11-26T03:00:00Z")
M6_ROWS = [f"| `prereg-m6` | `5a17` | {PREREG_M6} |", f"| `freeze-m6` | `f6f6` | {FREEZE_M6} |"]


class Sweep(unittest.TestCase):
    def test_the_sweep(self):
        self.assertEqual(ss.SWEEPS["m6"], ((8,), "main", ss.M6_ARMS, "m6"))
        self.assertEqual(ss.M6_ARMS, ("B", "Bd", "Rc", "C", "By", "Cp", "Cpf", "Ct"))  # M6 §1
        self.assertTrue(ss.is_m6("m6"))
        self.assertFalse(ss.is_m6("m3_after"))
        self.assertNotIn("m6", ss.ORDER)  # it stands outside the others' order, as M3's sweeps
        self.assertEqual(ss.session_kind("m6"), "m6")

    def test_orders(self):
        # M6 §4, as registered.
        self.assertEqual(ss.M6_ORDERS[1], ("B", "Bd", "Rc", "C", "By", "Cp", "Cpf", "Ct"))
        self.assertEqual(ss.M6_ORDERS[2], ("Ct", "Cpf", "Cp", "By", "C", "Rc", "Bd", "B"))
        self.assertEqual(ss.M6_ORDERS[3], ("By", "B", "Rc", "C", "Bd", "Cpf", "Cp", "Ct"))
        for order in ss.M6_ORDERS.values():
            self.assertEqual(sorted(order), sorted(ss.M6_ARMS))
        with mock.patch.object(ss, "M6_NOT_RUN", ()):
            self.assertEqual(ss.sweep_plan("m6"), [(8, r, ss.M6_ORDERS[r]) for r in (1, 2, 3)])
            self.assertEqual(sum(len(order) for _, _, order in ss.sweep_plan("m6")), 24)

    def test_an_arm_that_is_not_run_is_left_out_and_the_order_stands(self):
        with mock.patch.object(ss, "M6_NOT_RUN", ("By",)):
            self.assertEqual(ss.m6_arms(), ("B", "Bd", "Rc", "C", "Cp", "Cpf", "Ct"))
            self.assertEqual(ss.sweep_variants("m6"), ss.m6_arms())
            self.assertEqual([order for _, _, order in ss.sweep_plan("m6")],
                             [("B", "Bd", "Rc", "C", "Cp", "Cpf", "Ct"), ("Ct", "Cpf", "Cp", "C", "Rc", "Bd", "B"),
                              ("B", "Rc", "C", "Bd", "Cpf", "Cp", "Ct")])
        self.assertEqual(ss.sweep_variants("m3_after"), ("B", "C"))

    def test_bench_options(self):
        want = {"B": ["--variant", "B"], "Bd": ["--variant", "Bd"], "Rc": ["--variant", "Rc"],
                "C": ["--variant", "C"], "By": ["--variant", "By"], "Cp": ["--variant", "C", "--post", "neon"],
                "Cpf": ["--variant", "C", "--post", "neon", "--cores", "all"],
                "Ct": ["--variant", "C", "--time-rknn", "on"]}
        for phase in ("m6", "m6_check"):
            for arm in ss.M6_ARMS:
                self.assertEqual(ss.bench_args(arm, phase), want[arm], arm)  # unstaggered: no sweep option
        self.assertEqual(ss.bench_args("C", "m6_default"), ["--variant", "C"])  # every option at its default

    def test_rule_3_applies(self):
        for phase, channels in (("m6", 8), ("m6_check", 8), ("m6_default", 2)):
            self.assertTrue(ss.rule3_applies(phase, "C", channels, "main", ss.LIMIT, {}), phase)

    def test_sessions(self):
        ns = lambda **kw: type("Args", (), kw)()  # noqa: E731
        self.assertTrue(ss.m6_session(ns(command="sweep", sweep="m6")))
        self.assertTrue(ss.m6_session(ns(command="m6-check", sweep=None)))
        self.assertFalse(ss.m6_session(ns(command="sweep", sweep="m3_after")))
        self.assertFalse(ss.m3_session(ns(command="sweep", sweep="m6")))

    def test_a_repetition_cut_short_is_redone_whole(self):
        arms = ss.M6_ARMS
        rec = lambda s, i, arm, valid=True: {"session": s, "name": f"{i:02d}_m6_{arm}_8ch_r1", "channels": 8,  # noqa: E731
                                             "repetition": 1, "variant": arm, "attempt": 1, "valid": valid}
        first = [rec("s1", i, a) for i, a in enumerate(arms[:5], 1)]  # the session ended inside repetition 1
        complete, pending = ss.sweep_state(first, arms)
        self.assertEqual(complete, set())
        self.assertEqual(pending[(8, 1)][0], dict.fromkeys(arms, 1))  # every arm again, from attempt 1
        self.assertEqual(len(pending[(8, 1)][1]), 5)  # the runs it replaces are kept and named
        complete, pending = ss.sweep_state(first + [rec("s2", i, a) for i, a in enumerate(arms, 1)], arms)
        self.assertEqual((complete, pending), ({(8, 1)}, {}))


class CpfRule(unittest.TestCase):
    """M6 §4's rule for Cpf: every pipeline thread allowed cpu0-7, the metrics thread cpu0 only."""

    def threads(self, **changes):
        t = {"bench_main": "0-7", "dec-1": "0-7", "pre-0": "0-7", "npu-0": "0-7", "mpp_dec": "0-7", "metrics": "0"}
        t.update(changes)
        return [{"comm": k, "affinity": v} for k, v in t.items() if v is not None]

    def test_as_registered(self):
        self.assertEqual(ss.cpf_affinity_problems(self.threads(), self.threads()), [])

    def test_a_pinned_pipeline_thread(self):
        for start, end, when in ((self.threads(**{"npu-0": "4-7"}), self.threads(), "start"),
                                 (self.threads(), self.threads(**{"dec-1": "0-3"}), "end")):
            problems = ss.cpf_affinity_problems(start, end)
            self.assertEqual(len(problems), 1, problems)
            self.assertIn(f"window's {when}", problems[0])

    def test_the_metrics_thread(self):
        self.assertTrue(ss.cpf_affinity_problems(self.threads(metrics="0-7"), self.threads()))
        self.assertTrue(ss.cpf_affinity_problems(self.threads(), self.threads(metrics="4")))
        self.assertTrue(ss.cpf_affinity_problems(self.threads(metrics=None), self.threads()))

    def test_no_threads_is_a_problem(self):
        self.assertTrue(ss.cpf_affinity_problems([], self.threads()))

    def test_cp_as_pinned_would_fail(self):
        pinned = [{"comm": c, "affinity": "0" if c == "metrics" else "4-7"} for c in ("bench_main", "npu-0", "metrics")]
        self.assertTrue(ss.cpf_affinity_problems(pinned, pinned))


class Code(unittest.TestCase):
    """M6 §9: a protocol session runs freeze-m6's code, built at the checkout's commit."""

    def git(self, head="h" * 40, diff=0, status=""):
        def run(args):
            if args[0] == "rev-parse":
                return 0, head
            if args[0] == "diff":
                self.assertEqual(args[:4], ["diff", "--quiet", "f" * 40, "HEAD"])
                self.assertEqual(tuple(args[5:]), ss.M6_CODE_PATHS)
                return diff, ""
            return 0, status
        return run

    def problem(self, built="h" * 40, **kw):
        with mock.patch.object(ss, "build_commit", lambda: built):
            return ss.m6_code_problem("f" * 40, self.git(**kw))

    def test_freeze_m6s_code_built_at_the_checkout(self):
        self.assertIsNone(self.problem())

    def test_another_build(self):
        self.assertIn("build this commit", self.problem(built="a" * 40))
        self.assertIn("build this commit", self.problem(built="h" * 40 + "-dirty"))

    def test_code_that_differs_from_freeze_m6(self):
        self.assertIn("differ from freeze-m6's", self.problem(diff=1))
        self.assertIn("differ from freeze-m6's", self.problem(diff=128))  # git cannot compare: not shown equal

    def test_local_changes(self):
        self.assertIn("local changes", self.problem(status=" M src/core/pipeline.cpp"))

    def test_the_code_paths(self):
        self.assertEqual(ss.M6_CODE_PATHS, ("src", "tools", "tests", "CMakeLists.txt"))  # M6 §9


class Order(unittest.TestCase):
    """tools/check_prereg_order.py with run kind m6 and M6's two stages (M6 §8)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c = Case(Path(self.tmp.name), freeze=AFTER, extra_rows=M6_ROWS)

    def tearDown(self):
        self.tmp.cleanup()

    def test_an_m6_run_after_freeze_m6(self):
        self.c.csv("m6/20261126T030000Z_m6/01_m6_B_8ch_r1/summary.csv", LATER, kind="m6", prereg_m6_commit="5a17",
                   freeze_m6_commit="f6f6")
        self.assertEqual(self.c.run()[0], 0, self.c.run()[1])

    def test_an_m6_run_before_freeze_m6(self):
        self.c.csv("m6/s/summary.csv", BETWEEN, kind="m6", prereg_m6_commit="5a17", freeze_m6_commit="f6f6")
        code, out = self.c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("freeze-m6", out)

    def test_a_check_run_between_the_stages_names_prereg_m6_only(self):
        self.c.csv("check/20261124T060000Z_m6_check/summary.csv", BETWEEN, kind="check", prereg_m6_commit="5a17",
                   freeze_m6_commit="none")
        self.assertEqual(self.c.run()[0], 0, self.c.run()[1])

    def test_a_run_that_names_prereg_m6_before_it(self):
        self.c.csv("check/s/summary.csv", AFTER, kind="check", prereg_m6_commit="5a17")
        code, out = self.c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("prereg-m6", out)

    def test_a_check_run_that_names_freeze_m6_before_it(self):
        self.c.csv("check/s/summary.csv", BETWEEN, kind="check", prereg_m6_commit="5a17", freeze_m6_commit="f6f6")
        code, out = self.c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("freeze-m6", out)

    def test_another_commit_than_the_tags(self):
        self.c.csv("m6/s/summary.csv", LATER, kind="m6", prereg_m6_commit="5a17", freeze_m6_commit="0000")
        code, out = self.c.run()
        self.assertEqual(code, 1, out)

    def test_an_m6_run_without_a_freeze_m6_row(self):
        c = Case(Path(tempfile.mkdtemp(dir=self.tmp.name)), freeze=AFTER, extra_rows=M6_ROWS[:1])
        c.csv("m6/s/summary.csv", LATER, kind="m6", prereg_m6_commit="5a17")
        self.assertNotEqual(c.run()[0], 0)


HASH = "a" * 64


def dump(arms=("b", "bd", "c", "cp", "rc"), frames=100, **changed):
    rows = {"frames": str(frames)}
    for arm in arms:
        for ext in (".u8", ".i8", ".jsonl"):
            rows[f"sha256_{arm}{ext}"] = f"{'c' if arm in ('c', 'cp', 'rc') else 'b'}{ext[1]}".ljust(64, "0")
    rows.update(changed)
    return rows


class Unchanged(unittest.TestCase):
    def objects(self, **differ):
        return [{"source": s, "sha256_m6": HASH, "sha256_freeze_m3": differ.get(s, HASH)}
                for s in m6c.UNCHANGED_SOURCES]

    def problems(self, objects=None, flags=("f", "f"), mine=None, ref=None):
        return m6c.unchanged_problems(self.objects() if objects is None else objects, flags[0], flags[1],
                                      dump() if mine is None else mine, dump(m6c.OLD_ARMS) if ref is None else ref)

    def test_unchanged(self):
        self.assertEqual(self.problems(), [])

    def test_an_object_that_differs(self):
        problems = self.problems(objects=self.objects(**{"src/core/npu.cpp": "b" * 64}))
        self.assertEqual(len(problems), 1)
        self.assertIn("src/core/npu.cpp", problems[0])

    def test_the_list_is_the_registered_one(self):
        self.assertEqual(len(m6c.UNCHANGED_SOURCES), 13)  # M6 §8 step 2: ten files and the three post-processings
        self.assertTrue(self.problems(objects=self.objects()[:-1]))  # one of them missing
        script = (ROOT / "tools/m6_unchanged.sh").read_text(encoding="utf-8")
        listed = script.split('UNCHANGED="', 1)[1].split('"', 1)[0].split()
        self.assertEqual(sorted(listed), sorted(m6c.UNCHANGED_SOURCES))
        cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        for source in m6c.UNCHANGED_SOURCES:
            self.assertIn(source, cmake)

    def test_no_objects_or_flags_that_differ(self):
        self.assertTrue(self.problems(objects=[]))
        self.assertTrue(self.problems(flags=("-O3", "-O2")))
        self.assertTrue(self.problems(flags=("", "")))

    def test_data_that_differs(self):
        for key in ("sha256_b.u8", "sha256_bd.i8", "sha256_c.jsonl", "sha256_cp.jsonl"):
            problems = self.problems(mine=dump(**{key: "f" * 64}))
            self.assertEqual(len(problems), 1, key)
        self.assertTrue(self.problems(ref=dump(("b", "bd", "c"))))  # an arm missing from the reference
        self.assertTrue(self.problems(mine=dump(frames=99)))

    def test_rc_against_c(self):
        self.assertEqual(m6c.rc_data_problems(dump()), [])
        self.assertEqual(len(m6c.rc_data_problems(dump(**{"sha256_rc.u8": "f" * 64}))), 1)
        self.assertEqual(len(m6c.rc_data_problems(dump(**{"sha256_rc.i8": "f" * 64}))), 1)
        self.assertEqual(len(m6c.rc_data_problems(dump(m6c.OLD_ARMS))), 2)  # Rc was not dumped


class Trace(unittest.TestCase):
    """Rc's trace: two sync calls per frame on a slot, from an NPU worker, none elsewhere."""

    THREADS = {"100": "bench_main", "101": "npu-0", "102": "npu-1", "103": "pre-0"}
    FDS = {"20": ("system-dma32", 1228800), "21": ("system-dma32", 1228800), "30": ("system-uncached", 2093056),
           "40": ("system-dma32", 3133440)}  # 40: a decoder buffer, from the slots' heap (A1) and larger

    def trace(self, frames=40, extra=(), per_frame=2, tid="101", fd="20"):
        t0, lines, binds = 1000.0, [], []
        lines.append(f"100 {t0:.6f} ioctl(5, _IOC(_IOC_READ, 0x64, 0x41, 0x8), 0x1) = 0")
        for i in range(frames):
            t = t0 + 0.1 + 0.2 * i
            binds.append(t)
            for k in range(per_frame):
                lines.append(f"{tid} {t + 0.0001 * (k + 1):.6f} ioctl({fd}, DMA_BUF_IOCTL_SYNC, 0xffff) = 0")
            lines.append(f"{tid} {t + 0.01:.6f} ioctl(5, _IOC(_IOC_READ|_IOC_WRITE, 0x64, 0x41, 0x68), 0x2) = 0")
        lines += list(extra)
        lines.append(f"100 {t0 + 0.2 * frames + 0.1:.6f} ioctl(5, _IOC(_IOC_READ, 0x64, 0x41, 0x8), 0x1) = 0")
        return m6c.trace_summary(sorted(lines, key=lambda l: float(l.split()[1])), self.THREADS, self.FDS, binds)

    def test_as_registered(self):
        t = self.trace()
        self.assertEqual(m6c.trace_problems(t), [])
        self.assertEqual(t["sync_in_trace"], 2 * t["frames_in_trace"])
        self.assertEqual(t["sync_on_other_fds"], 0)
        self.assertGreaterEqual(t["frames_in_trace"], 30)  # the first and last half second are left out
        self.assertLess(t["frames_in_trace"], 40)

    def test_one_sync_per_frame(self):
        self.assertTrue(any("not two per frame" in p for p in m6c.trace_problems(self.trace(per_frame=1))))

    def test_four_syncs_per_frame(self):
        self.assertTrue(any("not two per frame" in p for p in m6c.trace_problems(self.trace(per_frame=4))))

    def test_a_sync_on_a_decoder_buffer(self):
        extra = ["103 1002.050000 ioctl(30, DMA_BUF_IOCTL_SYNC, 0xffff) = 0",
                 "103 1002.050100 ioctl(30, DMA_BUF_IOCTL_SYNC, 0xffff) = 0"]
        problems = m6c.trace_problems(self.trace(extra=extra))
        self.assertTrue(any("not slots" in p for p in problems), problems)
        self.assertTrue(any("other than the NPU workers" in p for p in problems), problems)

    def test_a_sync_on_an_fd_that_is_no_dma_buf(self):
        self.assertTrue(any("not slots" in p for p in m6c.trace_problems(self.trace(fd="99"))))

    def test_a_sync_on_a_decoder_buffer_of_the_slots_heap(self):
        self.assertTrue(any("not slots" in p for p in m6c.trace_problems(self.trace(fd="40"))))

    def test_a_bind_cut_by_the_traces_start_does_not_break_the_pairs(self):
        extra = ["101 1000.000500 ioctl(20, DMA_BUF_IOCTL_SYNC, 0xffff) = 0"]  # an END whose START was not traced
        self.assertEqual(m6c.trace_problems(self.trace(extra=extra)), [])

    def test_pairs_on_different_fds(self):
        extra = ["102 1003.050000 ioctl(20, DMA_BUF_IOCTL_SYNC, 0xffff) = 0",
                 "102 1003.050100 ioctl(21, DMA_BUF_IOCTL_SYNC, 0xffff) = 0"]
        self.assertTrue(any("pairs" in p for p in m6c.trace_problems(self.trace(extra=extra))))

    def test_too_short_a_trace(self):
        self.assertTrue(any("frames in the trace" in p for p in m6c.trace_problems(self.trace(frames=8))))

    def test_the_fallback_heap_counts_as_a_slot(self):
        with mock.patch.object(Trace, "FDS", {"20": ("system", 1228800)}):
            self.assertEqual(m6c.trace_problems(self.trace()), [])


class Ct(unittest.TestCase):
    """Ct's check: as many timed calls as inferences in the window, every one positive."""

    def run_(self, n=50, calls=None, processed=None, cpu=700000, warmup=20, duration=10):
        frames = [{"t4_ns": str(warmup * 10**9 + i * 10**8), "rknn_cpu_ns": str(cpu)} for i in range(n)]
        frames += [{"t4_ns": str(5 * 10**9), "rknn_cpu_ns": str(cpu)},  # warm-up
                   {"t4_ns": str((warmup + duration) * 10**9), "rknn_cpu_ns": str(cpu)}]  # at the window's end: out
        summary = {"rknn_run_calls": str(n if calls is None else calls),
                   "processed_in_window": str(n if processed is None else processed)}
        return summary, frames, warmup, duration

    def test_as_registered(self):
        self.assertEqual(m6c.ct_problems(*self.run_()), [])

    def test_counts_that_differ(self):
        self.assertTrue(m6c.ct_problems(*self.run_(calls=49)))
        self.assertTrue(m6c.ct_problems(*self.run_(processed=51)))

    def test_a_call_that_is_not_positive(self):
        summary, frames, w, d = self.run_()
        frames[3]["rknn_cpu_ns"] = "0"
        self.assertTrue(any("not positive" in p for p in m6c.ct_problems(summary, frames, w, d)))
        frames[3]["rknn_cpu_ns"] = "-5"
        self.assertTrue(any("not positive" in p for p in m6c.ct_problems(summary, frames, w, d)))

    def test_a_run_without_timing(self):
        summary, frames, w, d = self.run_()
        self.assertEqual(m6c.ct_problems({"processed_in_window": "50"}, frames, w, d), ["the run has no rknn_run timing"])
        self.assertEqual(m6c.ct_problems(summary, [{"t4_ns": f["t4_ns"]} for f in frames], w, d),
                         ["the run has no rknn_run timing"])

    def test_no_inference(self):
        self.assertTrue(m6c.ct_problems(*self.run_(n=0)))


class OneBuild(unittest.TestCase):
    C = "c" * 40

    def test_one_clean_build_of_this_code(self):
        self.assertEqual(m6c.commit_problems({"a": self.C, "b": self.C}, lambda c: True), [])

    def test_two_builds(self):
        self.assertTrue(m6c.commit_problems({"a": self.C, "b": "d" * 40}, lambda c: True))

    def test_a_dirty_or_unknown_build(self):
        for commit in (self.C + "-dirty", "unknown", ""):
            self.assertTrue(m6c.commit_problems({"a": commit, "b": commit}, lambda c: True), commit)

    def test_code_that_is_not_the_checkouts(self):
        self.assertTrue(m6c.commit_problems({"a": self.C}, lambda c: False))


def by_header(variant):
    return {"variant": variant, "run_kind": "check", "source": "/home/pi/media/bench/pexels_36330861.mp4"}


def holdout_header(name, variant="By"):
    return {"B": {"source": f"/m/{name}"}, "C": {"variant": variant, "run_kind": "check"}}


class ByInput(unittest.TestCase):
    def check_json(self, **changes):
        j = {"frames": 1000, "registered_input": True, "pass": False,
             "headers": {"B": by_header("B"), "C": by_header("By"), "R": by_header("R")}}
        j.update(changes)
        return j

    def test_bys_check_on_the_registered_input(self):
        self.assertEqual(m6c.by_input_problems(self.check_json(), m6c.VIDEO_1[1]), [])
        self.assertEqual(m6c.by_input_problems(self.check_json(**{"pass": True}), m6c.VIDEO_1[1]), [])

    def test_cs_check_is_not_bys(self):
        j = self.check_json()
        j["headers"]["C"]["variant"] = "C"
        self.assertTrue(m6c.by_input_problems(j, m6c.VIDEO_1[1]))

    def test_another_input(self):
        self.assertTrue(m6c.by_input_problems(self.check_json(frames=200, registered_input=False), m6c.VIDEO_1[1]))
        self.assertTrue(m6c.by_input_problems(self.check_json(), "0" * 64))  # another file under video 1's name
        self.assertTrue(m6c.by_input_problems(self.check_json(), ""))
        j = self.check_json()
        for k in "BCR":
            j["headers"][k]["source"] = "/x/other.mp4"
        self.assertTrue(m6c.by_input_problems(j, m6c.VIDEO_1[1]))

    def test_another_run_kind_or_no_verdict(self):
        j = self.check_json()
        j["headers"]["B"]["run_kind"] = "equivalence"
        self.assertTrue(m6c.by_input_problems(j, m6c.VIDEO_1[1]))
        self.assertTrue(m6c.by_input_problems(self.check_json(**{"pass": None}), m6c.VIDEO_1[1]))
        self.assertTrue(m6c.by_input_problems(self.check_json(headers=[]), m6c.VIDEO_1[1]))  # pooled

    def test_the_holdout(self):
        self.assertEqual(m6c.holdout_input_problems({"headers": [holdout_header(n) for n in m6c.HOLDOUT]}), [])
        self.assertTrue(m6c.holdout_input_problems({"headers": [holdout_header(n) for n in m6c.HOLDOUT[:2]]}))
        self.assertTrue(m6c.holdout_input_problems({"headers": [holdout_header(n, "C") for n in m6c.HOLDOUT]}))
        self.assertTrue(m6c.holdout_input_problems({"headers": {}}))

    def test_the_holdout_videos_are_a8s(self):
        script = (ROOT / "tools/equivalence_holdout.sh").read_text(encoding="utf-8")
        for name in m6c.HOLDOUT:
            self.assertIn(name[:-len(".mp4")], script)


class StepOrder(unittest.TestCase):
    NAMES = ("m6_unchanged", "m6_by_check", "m6_by_equivalence", "m6_by_holdout", "m6_rc_trace", "m6_check")

    def starts(self, *order):
        return {name: i for i, name in enumerate(order)}

    def test_the_registered_order(self):
        self.assertEqual(m6c.order_problems(self.starts(*self.NAMES)), [])

    def test_every_swap_of_neighbours_that_the_plan_orders(self):
        for a, b in (("m6_by_check", "m6_by_equivalence"), ("m6_by_equivalence", "m6_by_holdout"),
                     ("m6_rc_trace", "m6_check")):
            order = list(self.NAMES)
            i, j = order.index(a), order.index(b)
            order[i], order[j] = order[j], order[i]
            self.assertTrue(m6c.order_problems(self.starts(*order)), (a, b))

    def test_a_run_with_a_window_before_bys_outcome(self):
        order = ("m6_unchanged", "m6_by_check", "m6_rc_trace", "m6_by_equivalence", "m6_by_holdout", "m6_check")
        self.assertTrue(m6c.order_problems(self.starts(*order)))
        order = ("m6_check", "m6_unchanged", "m6_by_check", "m6_by_equivalence", "m6_by_holdout", "m6_rc_trace")
        self.assertTrue(m6c.order_problems(self.starts(*order)))

    def test_equal_times_are_not_after(self):
        self.assertTrue(m6c.order_problems(dict.fromkeys(self.NAMES, 0)))


class Records(unittest.TestCase):
    def test_the_latest_record_of_a_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for name in ("20261124T010000Z_m6_check", "20261124T020000Z_m6_check", "20261124T030000Z_m6_check_smoke",
                         "20261124T040000Z_m6_by_check", "x_m6_check"):
                (d / name).mkdir()
            (d / "20261125T000000Z_m6_check").write_text("a file, not a record")
            self.assertEqual(m6c.latest(d, "m6_check").name, "20261124T020000Z_m6_check")
            self.assertIsNone(m6c.latest(d, "m6_unchanged"))
            with self.assertRaises(m6c.Missing):
                m6c.check(d)

    def test_the_records_m6_8_step_3_names(self):
        self.assertEqual(set(m6c.RECORDS), {"m6_unchanged", "m6_rc_trace", "m6_by_check", "m6_by_equivalence",
                                            "m6_by_holdout", "m6_check"})

    def test_a_missing_record_is_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(m6c.main(["--results", tmp]), 2)


class Headers(unittest.TestCase):
    def test_the_prereg_commit_is_the_tags(self):
        text = (ROOT / "docs/notes/env.md").read_text(encoding="utf-8")
        rows = ss.registration_rows(text)
        self.assertEqual(rows["prereg-m6"][0], ss.PREREG_M6_COMMIT)
        self.assertIn(f'kPreregM6Commit = "{ss.PREREG_M6_COMMIT}"',
                      (ROOT / "src/core/report.h").read_text(encoding="utf-8"))

    def test_the_arms_in_the_binary_and_in_the_session_tool(self):
        rules = (ROOT / "src/core/run_rules.cpp").read_text(encoding="utf-8")
        for arm in ss.M6_ARMS:
            self.assertIn(f'"{arm}"', rules, arm)
        self.assertEqual(json.loads(json.dumps(ss.M6_PHASES)), ["m6", "m6_check", "m6_default"])


if __name__ == "__main__":
    unittest.main()
