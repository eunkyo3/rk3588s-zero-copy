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
PREREG_M2, BETWEEN, FREEZE_M2 = "2026-11-05T04:45:34Z", "2026-11-06T00:00:00Z", "2026-11-10T00:00:00Z"
M2_ROWS = [f"| `prereg-m2` | `5d9d` | {PREREG_M2} |", f"| `freeze-m2` | `f2f2` | {FREEZE_M2} |"]
PREREG_M4, BETWEEN_M4, FREEZE_M4 = "2026-11-11T00:04:33Z", "2026-11-15T00:00:00Z", "2026-11-18T00:00:00Z"
M4_ROWS = [f"| `prereg-m4` | `8638` | {PREREG_M4} |", f"| `freeze-m4` | `f4f4` | {FREEZE_M4} |"]


def env_md(freeze="", amend=None, extra_rows=()):
    rows = [f"| [`prereg-v1`](https://example/prereg-v1) | `87fe` | {PREREG} |",
            f"| `freeze-m1` | {'`abc`' if freeze else 'not created yet'} | {freeze} |"]
    if amend:
        rows.append(f"| `amend-A9` | `def` | {amend} |")
    rows += list(extra_rows)
    return ("# Environment\n\n## Registration\n\n| Stage | Tag commit | Release `created_at` |\n|---|---|---|\n"
            + "\n".join(rows) + "\n\n## Board baseline\n\n| `prereg-v1` | not | a registration row |\n")


def amendments_md(released=()):
    out = "# Amendments\n\n"
    for n in (6, 7, 9):
        release = f"`amend-A{n}`" if f"A{n}" in released else "none (before `freeze-m1`)"
        out += f"## A{n} — 2026-11-02 — title\n- Change: x\n- Release: {release}\n\n"
    return out


class Case:
    def __init__(self, d, released=(), **refs):
        self.d = d
        (d / "env.md").write_text(env_md(**refs), encoding="utf-8")
        (d / "amendments.md").write_text(amendments_md(released), encoding="utf-8")
        (d / "results").mkdir()

    def write(self, rel, text):
        p = self.d / "results" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def csv(self, rel, start, kind="calibration", amendment="A6", prereg_m2=None, prereg_m4=None, **fields):
        m2 = f"# prereg_m2_commit: {prereg_m2}\n" if prereg_m2 is not None else ""
        m4 = f"# prereg_m4_commit: {prereg_m4}\n" if prereg_m4 is not None else ""
        more = "".join(f"# {k}: {v}\n" for k, v in fields.items())
        self.write(rel, f"# start_time_utc: {start}\n# run_kind: {kind}\n# amendment: {amendment}\n{m2}{m4}{more}"
                        "key,value\n")

    def run(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cpo.main(["--results", str(self.d / "results"), "--env-md", str(self.d / "env.md"),
                             "--amendments", str(self.d / "amendments.md")])
        return code, out.getvalue() + err.getvalue()


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def case(self, **kw):
        return Case(Path(tempfile.mkdtemp(dir=self.d)), **kw)

    def test_every_header_kind_passes_after_prereg(self):
        c = self.case()
        c.csv("calibration/s/r1/summary.csv", AFTER)
        header = {"header": {"start_time_utc": AFTER, "run_kind": "equivalence", "amendment": "A6"}}
        c.write("equivalence/e/b.jsonl", json.dumps(header) + '\n{"frame": 0}\n')
        c.write("check/x/env.json", json.dumps({"header_start_time_utc": AFTER, "header_run_kind": "check"}))
        c.write("calibration/s/tensors.json", json.dumps({"start_time_utc": AFTER, "amendment": "A6"}))  # dump_tensor
        c.write("calibration/s/env.txt", f"time_utc: {AFTER}\nntp_synchronized: yes\n")
        c.write("calibration/s/runs.tsv", "repetition\n")  # inherits from the headers in its directory
        c.write("calibration/s/r1/r1.log", "log\n")  # inherits from summary.csv
        code, out = c.run()
        self.assertEqual(code, 0, out)
        self.assertIn("7 files, 0 problems", out)

    def test_a_file_before_prereg_fails(self):
        c = self.case()
        c.csv("calibration/s/summary.csv", BEFORE)
        c.write("calibration/s/check.txt", "no header\n")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertEqual(out.count("not after prereg-v1"), 2)

    def test_a_start_equal_to_the_reference_fails(self):
        c = self.case()
        c.csv("calibration/s/summary.csv", PREREG)
        self.assertEqual(c.run()[0], 1)

    def test_headerless_file_takes_the_earliest_start_in_its_directory(self):
        c = self.case()
        c.csv("equivalence/e/a.csv", AFTER, kind="equivalence")
        c.csv("equivalence/e/b.csv", BEFORE, kind="equivalence")
        c.write("equivalence/e/check.txt", "no header\n")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("check.txt: starts 2026-10-30T01:00:00Z", out)

    def test_headerless_file_searches_upwards(self):
        c = self.case()
        c.write("calibration/s/env.txt", f"time_utc: {AFTER}\n")
        c.write("calibration/s/sub/deeper/x.log", "log\n")
        self.assertEqual(c.run()[0], 0)

    def test_jsonl_without_header(self):
        c = self.case()
        c.write("equivalence/e/env.txt", f"time_utc: {AFTER}\n")
        c.write("equivalence/e/b.jsonl", '{"frame": 0}\n')
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("b.jsonl: a results JSONL without a header", out)

    def test_unreadable_release_line(self):
        c = self.case()
        p = c.d / "amendments.md"
        p.write_text(p.read_text(encoding="utf-8").replace("- Release: none", "- Release: amend A9", 1),
                     encoding="utf-8")
        c.csv("calibration/s/summary.csv", AFTER)
        code, out = c.run()
        self.assertEqual(code, 2, out)
        self.assertIn("cannot read its Release line", out)

    def test_file_without_any_start_time(self):
        c = self.case()
        c.write("equivalence/e/dump.txt", "frames: 3\n")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("no start time", out)

    def test_csv_without_header_or_without_start_time(self):
        c = self.case()
        c.write("calibration/s/env.txt", f"time_utc: {AFTER}\n")
        c.write("calibration/s/plain.csv", "key,value\n")
        c.write("calibration/s/no_start.csv", "# run_kind: calibration\nkey,value\n")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("plain.csv: a results CSV without a header", out)
        self.assertIn("no_start.csv: a header without start_time_utc", out)

    def test_comparison_needs_freeze(self):
        c = self.case()
        c.csv("comparison/r/summary.csv", AFTER, kind="comparison")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("freeze-m1 has no reference time", out)

        for start, expected in [(AFTER, 1), (LATER, 0)]:
            with self.subTest(start=start):
                c = self.case(freeze="2026-11-10T00:00:00Z")
                c.csv("comparison/r/summary.csv", start, kind="comparison")
                c.write("comparison/r/frames.log", "x\n")  # inherits the comparison kind
                self.assertEqual(c.run()[0], expected)

    def test_crashed_comparison_run_takes_its_kind_from_the_path(self):
        # bench_main writes its CSVs at the end; a crash leaves only the environment and the log.
        c = self.case(freeze="2026-11-10T00:00:00Z")
        c.write("comparison/r.env.txt", f"time_utc: {AFTER}\n")
        c.write("comparison/r.log", "crashed\n")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("a comparison run that starts", out)

    def test_header_kind_must_match_the_path(self):
        c = self.case()
        c.csv("calibration/s/summary.csv", AFTER, kind="comparison")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("its path says 'calibration'", out)

    def test_unknown_path_kind(self):
        c = self.case()
        c.csv("Comparison/r/summary.csv", AFTER, kind="Comparison")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("not under results/<run kind>/", out)

    def test_calibration_does_not_need_freeze(self):
        c = self.case()
        c.csv("calibration/s/summary.csv", AFTER, kind="calibration")
        self.assertEqual(c.run()[0], 0)

    def test_m2_needs_freeze_m2(self):
        # M2 §5.1: every m2 run starts after freeze-m2; without its reference time, every m2 file fails.
        for rows in ([M2_ROWS[0]], [M2_ROWS[0], "| `freeze-m2` | not created yet | |"]):
            with self.subTest(rows=rows):
                c = self.case(extra_rows=rows)
                c.csv("m2/s/r1/summary.csv", LATER, kind="m2")
                c.write("m2/s/r1/frames.log", "x\n")  # inherits the m2 kind
                code, out = c.run()
                self.assertEqual(code, 1, out)
                self.assertEqual(out.count("a m2 run, and freeze-m2 has no reference time"), 2, out)
        for start, expected in [(BETWEEN, 1), (FREEZE_M2, 1), (LATER, 0)]:
            with self.subTest(start=start):
                c = self.case(extra_rows=M2_ROWS)
                c.csv("m2/s/r1/summary.csv", start, kind="m2")
                code, out = c.run()
                self.assertEqual(code, expected, out)
                self.assertEqual(out.count("not after freeze-m2"), expected, out)

    def test_a_header_that_names_prereg_m2(self):
        # M2 §5.1: whatever its run kind, a file whose header names prereg-m2 starts after it.
        for start, expected in [(AFTER, 1), (PREREG_M2, 1), (BETWEEN, 0)]:
            with self.subTest(start=start):
                c = self.case(extra_rows=M2_ROWS)
                c.csv("check/x/summary.csv", start, kind="check", prereg_m2="5d9d")
                c.write("check/x/x.log", "x\n")  # inherits it from summary.csv
                code, out = c.run()
                self.assertEqual(code, expected, out)
                self.assertEqual(out.count("not after prereg-m2"), 2 * expected, out)
        c = self.case(extra_rows=M2_ROWS)
        c.write("check/y/env.json", json.dumps({"header_start_time_utc": AFTER, "header_run_kind": "check",
                                                "header_prereg_m2_commit": "5d9d"}))
        header = {"header": {"start_time_utc": AFTER, "run_kind": "check", "prereg_m2_commit": "5d9d"}}
        c.write("check/z/t.jsonl", json.dumps(header) + "\n")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertEqual(out.count("names prereg-m2 but starts 2026-11-02T07:00:00Z"), 2, out)

    def test_prereg_m2_none_or_absent_sets_no_time(self):
        c = self.case()  # no prereg-m2 row either
        c.csv("check/x/a.csv", AFTER, kind="check", prereg_m2="none")
        c.csv("check/x/b.csv", AFTER, kind="check", prereg_m2="")
        c.csv("check/x/c.csv", AFTER, kind="check")
        self.assertEqual(c.run()[0], 0)

    def test_naming_prereg_m2_needs_its_reference_time(self):
        c = self.case()
        c.csv("check/x/a.csv", BETWEEN, kind="check", prereg_m2="5d9d")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("names prereg-m2, and prereg-m2 has no reference time", out)

    def test_m4_needs_freeze_m4(self):
        # M4 §4: every m4 run or pass starts after freeze-m4; without its reference time, every m4 file fails.
        for rows in ([M4_ROWS[0]], [M4_ROWS[0], "| `freeze-m4` | not created yet | |"]):
            with self.subTest(rows=rows):
                c = self.case(extra_rows=rows)
                c.csv("m4/s/r1/summary.csv", LATER, kind="m4", prereg_m4="8638")
                c.write("m4/s/r1/frames.log", "x\n")  # inherits the m4 kind
                code, out = c.run()
                self.assertEqual(code, 1, out)
                self.assertEqual(out.count("a m4 run, and freeze-m4 has no reference time"), 2, out)
        for start, expected in [(BETWEEN_M4, 1), (FREEZE_M4, 1), (LATER, 0)]:
            with self.subTest(start=start):
                c = self.case(extra_rows=M4_ROWS)
                c.csv("m4/s/r1/summary.csv", start, kind="m4", prereg_m4="8638")
                code, out = c.run()
                self.assertEqual(code, expected, out)
                self.assertEqual(out.count("not after freeze-m4"), expected, out)
        # A pass on the PC writes a JSON header with start_time_utc (M4 §4); its directory's other files inherit it.
        c = self.case(extra_rows=M4_ROWS)
        c.write("m4/p/pass.json", json.dumps({"start_time_utc": BETWEEN_M4, "run_kind": "m4"}))
        c.write("m4/p/detections.json.gz.sha256", "x\n")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertEqual(out.count("a m4 run that starts 2026-11-15T00:00:00Z, not after freeze-m4"), 2, out)

    def test_a_header_that_names_prereg_m4(self):
        # M4 §4: whatever its run kind, a file whose header names prereg-m4 starts after it.
        for start, expected in [(BETWEEN, 1), (PREREG_M4, 1), (BETWEEN_M4, 0)]:
            with self.subTest(start=start):
                c = self.case(extra_rows=M2_ROWS + M4_ROWS)
                c.csv("check/x/summary.csv", start, kind="check", prereg_m2="5d9d", prereg_m4="8638")
                c.write("check/x/x.log", "x\n")  # inherits it from summary.csv
                code, out = c.run()
                self.assertEqual(code, expected, out)
                self.assertEqual(out.count("not after prereg-m4"), 2 * expected, out)
                self.assertEqual(out.count("not after prereg-m2"), 0, out)
        c = self.case(extra_rows=M4_ROWS)
        c.write("check/y/env.json", json.dumps({"header_start_time_utc": BETWEEN, "header_run_kind": "check",
                                                "header_prereg_m4_commit": "8638"}))
        header = {"header": {"start_time_utc": BETWEEN, "run_kind": "check", "prereg_m4_commit": "8638"}}
        c.write("check/z/t.jsonl", json.dumps(header) + "\n")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertEqual(out.count("names prereg-m4 but starts 2026-11-06T00:00:00Z"), 2, out)

    def test_prereg_m4_none_absent_or_without_its_time(self):
        c = self.case()  # no prereg-m4 row
        c.csv("check/x/a.csv", BETWEEN_M4, kind="check", prereg_m4="none")
        c.csv("check/x/b.csv", BETWEEN_M4, kind="check")
        self.assertEqual(c.run()[0], 0)
        c.csv("check/x/c.csv", BETWEEN_M4, kind="check", prereg_m4="8638")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("names prereg-m4, and prereg-m4 has no reference time", out)

    def test_m4_fields_in_other_run_kinds(self):
        # Every calib_npu and bench_main run now carries prereg_m4_commit, M1's run kinds included.
        for start, expected in [(BETWEEN, 1), (BETWEEN_M4, 0)]:
            with self.subTest(start=start):
                c = self.case(extra_rows=M4_ROWS)
                c.csv("calibration/s/r/summary.csv", start, prereg_m4="8638", freeze_m4_commit="none")
                code, out = c.run()
                self.assertEqual(code, expected, out)
                self.assertEqual(out.count("names prereg-m4 but starts"), expected, out)
        c = self.case()  # no prereg-m4 row
        c.csv("calibration/s/r/summary.csv", BETWEEN_M4, prereg_m4="8638")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("names prereg-m4, and prereg-m4 has no reference time", out)
        self.assertIn("gives a prereg-m4 commit, and prereg-m4 has no tag commit in the table", out)

    def test_an_env_json_naming_two_stages(self):
        # Only the stage it starts too early for fails.
        c = self.case(extra_rows=M2_ROWS + M4_ROWS)
        c.write("check/y/env.json", json.dumps({"header_start_time_utc": BETWEEN, "header_run_kind": "check",
                                                "header_prereg_m2_commit": "5d9d", "header_prereg_m4_commit": "8638"}))
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertEqual(out.count("names prereg-m4 but starts"), 1, out)
        self.assertNotIn("names prereg-m2 but starts", out)

    def test_m4_path_with_another_run_kind(self):
        c = self.case(extra_rows=M4_ROWS)
        c.csv("m4/s/r1/summary.csv", LATER, kind="check", prereg_m4="8638")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("header says run kind 'check', its path says 'm4'", out)

    def test_stage_commits_match_the_table(self):
        # PROTOCOL §1.4: a header's stage commits are the tags' commits in the Registration table.
        c = self.case(extra_rows=M2_ROWS + M4_ROWS)
        c.csv("check/x/a.csv", LATER, kind="check", prereg_v1_commit="87fe", prereg_m2="5d9d", prereg_m4="8638",
              freeze_m2_commit="f2f2", freeze_m4_commit="f4f4")
        self.assertEqual(c.run()[0], 0)
        c.csv("check/x/b.csv", LATER, kind="check", prereg_m2="beef", freeze_m4_commit="f2f2")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("gives beef as prereg-m2's commit, the table has 5d9d", out)
        self.assertIn("gives f2f2 as freeze-m4's commit, the table has f4f4", out)
        self.assertEqual(out.count("FAIL"), 2, out)
        # A freeze-m1 row without a commit yet ("not created yet"): no header may give one.
        c = self.case()
        c.csv("check/x/a.csv", AFTER, kind="check", freeze_m1_commit="none")
        self.assertEqual(c.run()[0], 0)
        c.csv("check/x/b.csv", AFTER, kind="check", freeze_m1_commit="abc")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("gives a freeze-m1 commit, and freeze-m1 has no tag commit in the table", out)
        # A JSON value that is not a string gives no commit.
        c = self.case()
        c.write("check/y/env.json", json.dumps({"header_start_time_utc": AFTER, "header_run_kind": "check",
                                                "header_prereg_m4_commit": 0, "header_freeze_m4_commit": False}))
        self.assertEqual(c.run()[0], 0)

    def test_a_header_that_names_freeze_m4(self):
        # Before freeze-m4 exists no run can give its commit; after, a run that gives it starts after it.
        c = self.case(extra_rows=[M4_ROWS[0]])
        c.csv("check/x/a.csv", LATER, kind="check", freeze_m4_commit="f4f4")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("names freeze-m4, and freeze-m4 has no reference time", out)
        for start, expected in [(BETWEEN_M4, 1), (LATER, 0)]:
            with self.subTest(start=start):
                c = self.case(extra_rows=M4_ROWS)
                c.csv("check/x/a.csv", start, kind="check", freeze_m4_commit="f4f4")
                code, out = c.run()
                self.assertEqual(code, expected, out)
                self.assertEqual(out.count("names freeze-m4 but starts"), expected, out)

    def test_amendment_release(self):
        for start, expected in [(AFTER, 1), (LATER, 0)]:
            with self.subTest(start=start):
                c = self.case(released=("A9",), freeze="2026-11-01T00:00:00Z", amend="2026-11-10T00:00:00Z")
                c.csv("comparison/r/summary.csv", start, kind="comparison", amendment="A9")
                self.assertEqual(c.run()[0], expected)
        # An amendment without a Release (before freeze-m1) sets no time.
        c = self.case()
        c.csv("calibration/s/summary.csv", AFTER, amendment="A7")
        self.assertEqual(c.run()[0], 0)

    def test_a_release_needs_its_reference_time(self):
        c = self.case(released=("A9",))
        c.csv("calibration/s/summary.csv", AFTER)
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("amend-A9: AMENDMENTS gives it a Release", out)

    def test_unknown_amendment(self):
        c = self.case()
        c.csv("calibration/s/summary.csv", AFTER, amendment="A42")
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("names A42, which AMENDMENTS does not have", out)

    def test_registration_table_errors(self):
        bad = {"a second row": f"| `prereg-v1` | `x` | {PREREG} |",
               "cannot read": "| `amend-A9` | `def` | 2026-11-10T00:00:00Z | extra |"}
        for message, row in bad.items():
            with self.subTest(message):
                c = self.case()
                (c.d / "env.md").write_text(env_md(extra_rows=[row]), encoding="utf-8")
                c.csv("calibration/s/summary.csv", AFTER)
                code, out = c.run()
                self.assertEqual(code, 2, out)
                self.assertIn(message, out)

    def test_rows_outside_the_registration_table_are_ignored(self):
        c = self.case()  # env_md has a prereg-v1 row under "Board baseline" too
        c.csv("calibration/s/summary.csv", AFTER)
        self.assertEqual(c.run()[0], 0)

    def test_byte_order_mark(self):
        c = self.case()
        p = c.d / "results/calibration/s/summary.csv"
        p.parent.mkdir(parents=True)
        p.write_bytes(f"﻿# start_time_utc: {BEFORE}\n# run_kind: calibration\nkey,value\n".encode("utf-8"))
        code, out = c.run()
        self.assertEqual(code, 1, out)
        self.assertIn("not after prereg-v1", out)

    def test_binary_files_are_not_read(self):
        c = self.case()
        c.csv("equivalence/e/a.csv", AFTER, kind="equivalence")
        (c.d / "results/equivalence/e/c.u8").write_bytes(b"\xff\xfe" * 1000)
        self.assertEqual(c.run()[0], 0)

    def test_env_md_without_prereg_is_an_input_error(self):
        c = self.case()
        (c.d / "env.md").write_text("# nothing\n", encoding="utf-8")
        code, out = c.run()
        self.assertEqual(code, 2, out)


if __name__ == "__main__":
    unittest.main()
