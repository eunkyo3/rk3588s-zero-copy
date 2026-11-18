#!/usr/bin/env python3
"""Tests for M3's parts (prereg/M3.md): the m3 sweeps of tools/stream_session.py, their pinned inventory keys
(§4) and session order (§3), the order checks of tools/check_prereg_order.py, and tools/m3_report.py.

Usage: python3 tests/test_m3.py
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import m3_report as m3r  # noqa: E402
import stream_session as ss  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_check_prereg_order import Case  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FREEZE_M3 = "2026-11-19T03:00:00Z"
M3_ROWS = [f"| `freeze-m3` | `f3f3` | {FREEZE_M3} |"]


class Sweeps(unittest.TestCase):
    def test_sweeps(self):
        for name in ss.M3_SWEEPS:
            self.assertEqual(ss.SWEEPS[name], ((8,), "main", ("B", "C"), "m3"))
            self.assertTrue(ss.is_m3(name))
            self.assertNotIn(name, ss.ORDER)
            self.assertEqual(ss.bench_args("B", name), ["--variant", "B"])  # M1's defaults, no other option
            self.assertEqual(ss.bench_args("C", name), ["--variant", "C"])
            self.assertTrue(ss.rule3_applies(name, "C", 8, "main", ss.LIMIT, {}))
        self.assertEqual([ss.variant_order(("B", "C"), r) for r in (1, 2, 3)], [("B", "C"), ("C", "B"), ("B", "C")])
        self.assertFalse(ss.is_m3("main"))


class Inventory(unittest.TestCase):
    def setUp(self):
        self.ref = (ROOT / ss.INVENTORY).read_text(encoding="utf-8")

    def test_pinned_keys(self):
        for key in ("kernel", "librknnrt", "rknn_header rknn_api.h", "h264_rkmpp_default_deint", "model",
                    "lib /usr/lib/librknnrt.so", "core_cxx_flags", "ffmpeg"):
            self.assertTrue(ss.pinned(key), key)
        for key in ("os", "ffmpeg_built_with", "CMAKE_CXX_COMPILER", "lib /usr/lib/aarch64-linux-gnu/libc.so.6",
                    "link_bench_main", "kernelx", "librknnrt_extra"):
            self.assertFalse(ss.pinned(key), key)
        keys = ss.inventory_keyed(self.ref)
        self.assertEqual(len(keys), len(self.ref.splitlines()))  # every line is one key
        self.assertTrue(all(any(ss.pinned(k) for k in keys if k.startswith(p.strip()) or k == p)
                            for p in ss.M3_PINNED), "every pinned entry names a key of inventory_m1.txt")

    def test_only_pinned_keys_are_problems(self):
        self.assertEqual(ss.m3_inventory_problems(self.ref, self.ref), [])
        now = self.ref.replace("os: Ubuntu 24.04.4 LTS", "os: Ubuntu 24.04.5 LTS")
        now = now.replace("libc6:arm64 2.39-0ubuntu8.9", "libc6:arm64 2.39-0ubuntu8.12")
        self.assertNotEqual(now, self.ref)
        self.assertEqual(ss.m3_inventory_problems(now, self.ref), [])
        now = now.replace("librknnrt: 2.3.2", "librknnrt: 2.3.4")
        problems = ss.m3_inventory_problems(now, self.ref)
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].startswith("librknnrt: 2.3.2"), problems)

    def test_a_missing_pinned_key(self):
        lacking = "".join(line + "\n" for line in self.ref.splitlines() if not line.startswith("model: "))
        self.assertEqual(ss.m3_inventory_problems(lacking, self.ref),
                         ["model: " + ss.inventory_keyed(self.ref)["model"] + " -> (none)"])


def write_setup(root, name, result="ok", fresh="yes", start="2026-11-19T06:00:00Z", end="2026-11-19T07:00:00Z"):
    d = root / name
    d.mkdir(parents=True)
    (d / "setup.csv").write_text(f"# start_time_utc: {start}\n# run_kind: m3\n# freeze_m3_commit: f3f3\n"
                                 f"# amendment: A6\nkey,value\nresult,{result}\nfresh,{fresh}\nend_time_utc,{end}\n",
                                 encoding="utf-8")


class SessionOrder(unittest.TestCase):
    def test_order(self):
        with tempfile.TemporaryDirectory() as t, \
                mock.patch.object(ss, "freeze_m3_commit", return_value="f" * 40), \
                mock.patch.object(ss, "progress", return_value="new"):
            root = Path(t)
            self.assertIsNone(ss.m3_order_problem("m3_before", ss.m3_setup_records(root)))
            self.assertIn("needs the latest setup record", ss.m3_order_problem("m3_after", ss.m3_setup_records(root)))
            write_setup(root, "20261119T060000Z_setup", fresh="no")
            records = ss.m3_setup_records(root)
            self.assertEqual([d.name for d, _ in records], ["20261119T060000Z_setup"])
            self.assertIn("has a setup record", ss.m3_order_problem("m3_before", records))
            self.assertIn("needs the latest setup record", ss.m3_order_problem("m3_after", records))
            write_setup(root, "20261119T080000Z_setup")
            self.assertIsNone(ss.m3_order_problem("m3_after", ss.m3_setup_records(root)))
            write_setup(root, "20261119T100000Z_setup", result="failed in step packages")  # a later reset failed
            self.assertIn("needs the latest setup record", ss.m3_order_problem("m3_after", ss.m3_setup_records(root)))
            (root / "20261119T090000Z_setup_x").mkdir()  # not a record's name
            self.assertEqual(len(ss.m3_setup_records(root)), 3)
        with mock.patch.object(ss, "freeze_m3_commit", return_value="none"):
            self.assertIn("freeze-m3 comes before", ss.m3_order_problem("m3_before", []))

    def test_complete(self):
        with mock.patch.object(ss, "freeze_m3_commit", return_value="f" * 40), \
                mock.patch.object(ss, "progress", return_value="complete"):
            self.assertIn("is complete", ss.m3_order_problem("m3_before", []))


class Order(unittest.TestCase):
    """tools/check_prereg_order.py: m3 after freeze-m3; m3_before, then the setup on a fresh board, then m3_after."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def case(self, rows=tuple(M3_ROWS)):
        return Case(Path(tempfile.mkdtemp(dir=self.d)), extra_rows=rows)

    def setup(self, c, name, start, end, result="ok", fresh="yes"):
        write_setup(c.d / "results" / "m3", name, result, fresh, start, end)

    def test_m3_needs_freeze_m3(self):
        for rows, start, expected in [((), "2026-11-20T00:00:00Z", "freeze-m3 has no reference time"),
                                      (tuple(M3_ROWS), "2026-11-19T02:00:00Z", "not after freeze-m3")]:
            with self.subTest(rows=rows, start=start):
                c = self.case(rows)
                c.csv("m3/20261119T020000Z_m3_before/01_m3_before_B_8ch_r1/summary.csv", start, kind="m3")
                code, out = c.run()
                self.assertEqual(code, 1, out)
                self.assertIn(expected, out)

    def test_before_then_setup_then_after(self):
        c = self.case()
        c.csv("m3/20261119T040000Z_m3_before/01_m3_before_B_8ch_r1/summary.csv", "2026-11-19T04:10:00Z", kind="m3")
        self.setup(c, "20261119T060000Z_setup", "2026-11-19T06:00:00Z", "2026-11-19T07:00:00Z")
        c.csv("m3/20261119T080000Z_m3_after/01_m3_after_B_8ch_r1/summary.csv", "2026-11-19T08:10:00Z", kind="m3")
        code, out = c.run()
        self.assertEqual(code, 0, out)

    def test_after_without_a_good_setup(self):
        for result, fresh in [("failed in step build", "yes"), ("ok", "no")]:
            with self.subTest(result=result, fresh=fresh):
                c = self.case()
                self.setup(c, "20261119T060000Z_setup", "2026-11-19T06:00:00Z", "2026-11-19T07:00:00Z", result, fresh)
                c.csv("m3/20261119T080000Z_m3_after/r/summary.csv", "2026-11-19T08:10:00Z", kind="m3")
                code, out = c.run()
                self.assertEqual(code, 1, out)
                self.assertIn("the latest setup record before it did not pass on a fresh board", out)

    def test_after_before_the_setup_ended(self):
        c = self.case()
        self.setup(c, "20261119T060000Z_setup", "2026-11-19T06:00:00Z", "2026-11-19T09:00:00Z")
        c.csv("m3/20261119T080000Z_m3_after/r/summary.csv", "2026-11-19T08:10:00Z", kind="m3")
        code, out = c.run()
        self.assertEqual(code, 1, out)

    def test_only_the_latest_setup_counts(self):
        c = self.case()
        self.setup(c, "20261119T060000Z_setup", "2026-11-19T06:00:00Z", "2026-11-19T07:00:00Z")
        self.setup(c, "20261119T073000Z_setup", "2026-11-19T07:30:00Z", "2026-11-19T07:40:00Z", "failed in step ffmpeg")
        c.csv("m3/20261119T080000Z_m3_after/r/summary.csv", "2026-11-19T08:10:00Z", kind="m3")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("the latest setup record before it did not pass", out)

    def test_setup_before_the_last_before_record(self):
        c = self.case()
        self.setup(c, "20261119T060000Z_setup", "2026-11-19T06:00:00Z", "2026-11-19T07:00:00Z")
        c.csv("m3/20261119T050000Z_m3_before/02_m3_before_C_8ch_r3/summary.csv", "2026-11-19T06:30:00Z", kind="m3")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("before the last record of the m3_before sessions", out)

    def test_a_header_that_names_freeze_m3(self):
        c = self.case()
        c.csv("check/20261119T020000Z_m3_before_smoke/r/summary.csv", "2026-11-19T02:00:00Z", kind="check",
              freeze_m3_commit="f3f3")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("names freeze-m3", out)


class Report(unittest.TestCase):
    def test_m1_reference(self):
        # M1_tables.md, sweep main at 8 channels: B 43.283 ± 0.343 s and 33.117 ± 4.543 ms; C 18.560 ± 0.308 s
        # and 30.340 ± 0.961 ms.
        with mock.patch.object(m3r, "M1_SESSION", ROOT / m3r.M1_SESSION):
            runs = m3r.counting_runs([m3r.M1_SESSION], "main")
        self.assertEqual(len(runs), 6)
        pv = m3r.per_variant(runs)
        for v, cpu, p50 in (("B", (43.283, 0.343), (33.117, 4.543)), ("C", (18.560, 0.308), (30.340, 0.961))):
            for key, (mean, sd) in (("process_cpu_s", cpu), ("latency_p50_ms", p50)):
                m, s, n = m3r.mean_sd([r[key] for r in pv[v]])
                self.assertEqual((round(m, 3), round(s, 3), n), (mean, sd, 3), (v, key))

    def test_criterion(self):
        m1 = {"B": [{"process_cpu_s": 40.0, "latency_p50_ms": 30.0}], "C": [{"process_cpu_s": 20.0,
                                                                             "latency_p50_ms": 30.0}]}
        same = {"B": [{"process_cpu_s": 44.0, "latency_p50_ms": 27.0}], "C": [{"process_cpu_s": 18.0,
                                                                               "latency_p50_ms": 33.0}]}
        cells, verdict = m3r.criterion(same, m1)
        self.assertEqual(verdict, "reproduced")  # every difference is exactly ±10%: within
        self.assertTrue(all(c[5] for c in cells))
        off = {**same, "C": [{"process_cpu_s": 17.9, "latency_p50_ms": 33.0}]}
        self.assertEqual(m3r.criterion(off, m1)[1], "not reproduced")
        none = {**same, "C": []}
        self.assertEqual(m3r.criterion(none, m1)[1], "not determined")
        self.assertEqual(m3r.criterion({**off, "B": []}, m1)[1], "not reproduced")

    def test_counting_runs(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t) / "20261119T040000Z_m3_before"
            d.mkdir()
            rows = [(1, "01_m3_before_B_8ch_r1", "B", 1, 1, 1), (2, "02_m3_before_C_8ch_r1", "C", 1, 1, 0),
                    (2, "02_m3_before_C_8ch_r1_a2", "C", 1, 2, 0), (3, "03_m3_before_C_8ch_r2", "C", 2, 1, 1)]
            text = "seq\tname\tphase\tvariant\tchannels\tcondition\trepetition\tattempt\tvalid\tpassed\n"
            text += "".join(f"{s}\t{n}\tm3_before\t{v}\t8\tmain\t{r}\t{a}\t{ok}\t\n" for s, n, v, r, a, ok in rows)
            (d / "runs.tsv").write_text(text, encoding="utf-8")
            runs = m3r.counting_runs([d], "m3_before")
        # repetition 1 is complete (C invalid ×2, so missing); repetition 2 lacks B, so it does not count
        self.assertEqual(runs, {(1, "B"): d / "01_m3_before_B_8ch_r1", (1, "C"): None})

    def test_dpkg_diff(self):
        a = {"libc6": "2.39-0ubuntu8.9", "gone": "1", "same": "2"}
        b = {"libc6": "2.39-0ubuntu8.12", "new": "3", "same": "2"}
        self.assertEqual(m3r.dpkg_diff(a, b), (["new"], ["gone"], [("libc6", "2.39-0ubuntu8.9", "2.39-0ubuntu8.12")]))


if __name__ == "__main__":
    unittest.main()
