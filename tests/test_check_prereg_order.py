#!/usr/bin/env python3
"""Tests for tools/check_prereg_order.py.

Usage: python3 tests/test_check_prereg_order.py
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import check_prereg_order as cpo  # noqa: E402

PREREG = "2026-10-30T02:01:36Z"
BEFORE, AFTER, LATER = "2026-10-30T01:00:00Z", "2026-11-02T07:00:00Z", "2026-11-20T00:00:00Z"


def env_md(freeze="", amend=None):
    rows = [f"| [`prereg-v1`](https://example/prereg-v1) | `87fe` | {PREREG} |",
            f"| `freeze-m1` | {'`abc`' if freeze else 'not created yet'} | {freeze} |"]
    if amend:
        rows.append(f"| `amend-A9` | `def` | {amend} |")
    return "# Environment\n\n| Stage | Tag commit | Release `created_at` |\n|---|---|---|\n" + "\n".join(rows) + "\n"


class Case:
    def __init__(self, d, **refs):
        self.d = d
        (d / "env.md").write_text(env_md(**refs), encoding="utf-8")
        (d / "results").mkdir()

    def write(self, rel, text):
        p = self.d / "results" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def csv(self, rel, start, kind="calibration", amendment="A6"):
        self.write(rel, f"# start_time_utc: {start}\n# run_kind: {kind}\n# amendment: {amendment}\nkey,value\n")

    def run(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cpo.main(["--results", str(self.d / "results"), "--env-md", str(self.d / "env.md")])
        return code, out.getvalue() + err.getvalue()


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_header_kind_passes_after_prereg(self):
        c = Case(self.d)
        c.csv("calibration/s/r1/summary.csv", AFTER)
        header = {"header": {"start_time_utc": AFTER, "run_kind": "equivalence", "amendment": "A6"}}
        c.write("equivalence/e/b.jsonl", json.dumps(header) + '\n{"frame": 0}\n')
        c.write("check/x/env.json", json.dumps({"header_start_time_utc": AFTER, "header_run_kind": "check"}))
        c.write("calibration/s/env.txt", f"time_utc: {AFTER}\nntp_synchronized: yes\n")
        c.write("calibration/s/runs.tsv", "repetition\n")  # inherits from env.txt in its directory
        c.write("calibration/s/r1/r1.log", "log\n")  # inherits from summary.csv
        code, out = c.run()
        self.assertEqual(code, 0, out)
        self.assertIn("6 files, 0 problems", out)

    def test_a_file_before_prereg_fails(self):
        c = Case(self.d)
        c.csv("calibration/s/summary.csv", BEFORE)
        c.write("calibration/s/check.txt", "no header\n")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertEqual(out.count("not after prereg-v1"), 2)

    def test_headerless_file_takes_the_earliest_start_in_its_directory(self):
        c = Case(self.d)
        c.csv("equivalence/e/a.csv", AFTER)
        c.csv("equivalence/e/b.csv", BEFORE)
        c.write("equivalence/e/check.txt", "no header\n")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("check.txt: starts 2026-10-30T01:00:00Z", out)

    def test_headerless_file_searches_upwards(self):
        c = Case(self.d)
        c.write("calibration/s/env.txt", f"time_utc: {AFTER}\n")
        c.write("calibration/s/sub/deeper/x.log", "log\n")
        self.assertEqual(c.run()[0], 0)

    def test_file_without_any_start_time(self):
        c = Case(self.d)
        c.write("equivalence/e/dump.txt", "frames: 3\n")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("no start time", out)

    def test_comparison_needs_freeze(self):
        c = Case(self.d)
        c.csv("comparison/r/summary.csv", AFTER, kind="comparison")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("freeze-m1 has no reference time", out)

        for start, expected in [(AFTER, 1), (LATER, 0)]:
            with self.subTest(start=start), tempfile.TemporaryDirectory() as t:
                c = Case(Path(t), freeze="2026-11-10T00:00:00Z")
                c.csv("comparison/r/summary.csv", start, kind="comparison")
                c.write("comparison/r/frames.log", "x\n")  # inherits the comparison kind
                code, out = c.run()
                self.assertEqual(code, expected, out)

    def test_calibration_does_not_need_freeze(self):
        c = Case(self.d)
        c.csv("calibration/s/summary.csv", AFTER, kind="calibration")
        self.assertEqual(c.run()[0], 0)

    def test_amendment_release(self):
        for start, expected in [(AFTER, 1), (LATER, 0)]:
            with self.subTest(start=start), tempfile.TemporaryDirectory() as t:
                c = Case(Path(t), freeze="2026-11-01T00:00:00Z", amend="2026-11-10T00:00:00Z")
                c.csv("comparison/r/summary.csv", start, kind="comparison", amendment="A9")
                code, out = c.run()
                self.assertEqual(code, expected, out)
        # An amendment without a Release (before freeze-m1) sets no time.
        c = Case(self.d)
        c.csv("calibration/s/summary.csv", AFTER, amendment="A7")
        self.assertEqual(c.run()[0], 0)

    def test_binary_files_are_not_read(self):
        c = Case(self.d)
        c.csv("equivalence/e/a.csv", AFTER)
        (self.d / "results/equivalence/e/c.u8").write_bytes(b"\xff\xfe" * 1000)
        self.assertEqual(c.run()[0], 0)

    def test_env_md_without_prereg_is_an_input_error(self):
        c = Case(self.d)
        (self.d / "env.md").write_text("# nothing\n", encoding="utf-8")
        code, out = c.run()
        self.assertEqual(code, 2, out)


if __name__ == "__main__":
    unittest.main()
