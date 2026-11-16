#!/usr/bin/env python3
"""Tests for tools/eval/pc_run.py: the checks of prereg/M4.md §4 before a run on the PC.

Usage: python3 tests/test_pc_run.py
"""
import io
import re
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "eval"))
import pc_run as pr  # noqa: E402

# The PC's own output (its Korean text decoded), with two samples that timed out.
STRIPCHART = ("time.windows.com[52.231.114.183:123] 추적 중\n3개 샘플 수집 중\n"
              "현재 시간은 2026-11-16 오후 2:30:28입니다.\n14:30:28, 오류: 0x800705B4\n"
              "14:30:31, +00.7527771s\n14:30:33, 오류: 0x800705B4\n")


class Parsing(unittest.TestCase):
    def test_stripchart(self):
        self.assertEqual(pr.parse_stripchart(STRIPCHART), ([0.7527771], 2))
        self.assertEqual(pr.parse_stripchart("1:02:03, -01.2500000s\n01:02:05, +00.0000100s\n"), ([-1.25, 1e-05], 0))
        self.assertEqual(pr.parse_stripchart(""), ([], 0))

    def test_ntp_problem(self):
        self.assertIsNone(pr.ntp_problem([0.7527771]))
        self.assertIsNone(pr.ntp_problem([-1.0, 0.2]))  # at most 1 s
        self.assertIn("-1.001 s off", pr.ntp_problem([0.1, -1.001]))
        self.assertIn("no sample", pr.ntp_problem([]))

    def test_best_exchange(self):
        # The shortest round trip wins; its midpoint is the host time the remote clock is set against.
        offset, rtt = pr.best_exchange([(10.0, 10.6, 10.4), (11.0, 11.55, 11.1), (12.0, 13.0, 12.5)])
        self.assertAlmostEqual(offset, 0.5)
        self.assertAlmostEqual(rtt, 0.1)

    def test_one_line(self):
        self.assertEqual(pr.one_line("a\r\n\n  b \n"), "a | b")


class Checks(unittest.TestCase):
    def fake_run(self, status="", offsets=STRIPCHART, image="sha256:" + "e" * 64):
        def run(cmd, timeout=120):
            if cmd[:2] == ["git", "-C"] and "rev-parse" in cmd:
                return 0, "c" * 40 + "\n"
            if "status" in cmd and cmd[0] == "git":
                return 0, status
            if cmd[:3] == ["docker", "image", "inspect"]:
                return (0, image + "\n") if image else (1, "no such image")
            if cmd[0] == "powershell":
                return 0, "Intel(R) Core(TM) i7-6500U CPU @ 2.50GHz\r\n"
            if "/stripchart" in cmd:
                return 0, offsets
            if "/query" in cmd:
                return -2147023834, "서비스가 시작되지 않았습니다. (0x80070426)\n"
            raise AssertionError(cmd)
        return run

    def checks(self, container=(0.0012, 0.003), allow_dirty=False, **kw):
        with mock.patch.object(pr, "run", self.fake_run(**kw)), \
                mock.patch.object(pr, "container_clock", return_value=container) as clock:
            return pr.checks(pr.IMAGE, allow_dirty), clock

    def test_a_run_that_starts(self):
        pc, clock = self.checks()
        clock.assert_called_once_with(pr.IMAGE)
        self.assertEqual((pc["code_commit"], pc["code_status"], pc["eval_image_id"]),
                         ("c" * 40, "clean", "sha256:" + "e" * 64))
        self.assertEqual({k: pc["host"][k] for k in ("cpu_model", "ntp_offset_s", "ntp_samples", "w32tm_status_exit")},
                         {"cpu_model": "Intel(R) Core(TM) i7-6500U CPU @ 2.50GHz", "ntp_offset_s": "+0.7528",
                          "ntp_samples": "1 answered, 2 did not", "w32tm_status_exit": -2147023834})
        self.assertIn("0x80070426", pc["host"]["w32tm_status"])
        self.assertEqual(pc["container_clock_offset_s"], "+0.0012")

    def test_what_stops_a_run(self):
        for kw, message in (({"status": " M tools/m4_prep.py\n"}, "tracked files differ"),
                            ({"image": ""}, "run tools/eval/build.sh"),
                            ({"offsets": "14:30:28, 오류: 0x800705B4\n"}, "no sample"),
                            ({"offsets": "14:30:31, +01.2000000s\n"}, "+1.200 s off"),
                            ({"container": (-1.5, 0.01)}, "container's clock is -1.500 s off")):
            with self.subTest(kw=kw), self.assertRaisesRegex(pr.Refused, re.escape(message)):
                self.checks(**kw)
        pc, _ = self.checks(status=" M tools/m4_prep.py\n", allow_dirty=True)  # development: recorded
        self.assertEqual(pc["code_status"], "differs: M tools/m4_prep.py")

    def test_git_bash_needs_its_path_conversion_off(self):
        argv = ["--kind", "check", "--name", "x", "--", "tools/m4_prep.py", "--coco", "/data/coco"]
        with mock.patch.dict(pr.os.environ, {"MSYSTEM": "MINGW64"}), mock.patch.object(pr, "checks") as checks:
            pr.os.environ.pop("MSYS_NO_PATHCONV", None)
            with redirect_stderr(io.StringIO()) as err:
                self.assertEqual(pr.main(argv), 2)
            self.assertIn("MSYS_NO_PATHCONV=1", err.getvalue())
            checks.assert_not_called()
            pr.os.environ["MSYS_NO_PATHCONV"] = "1"
            checks.side_effect = pr.Refused("stop here")
            with redirect_stderr(io.StringIO()) as err:
                self.assertEqual(pr.main(argv), 2)
            self.assertIn("stop here", err.getvalue())

    def test_main_needs_a_tool_and_a_name(self):
        for argv in (["--kind", "check", "--name", "x"], ["--kind", "check", "--name", "a/b", "--", "tools/m4_prep.py"],
                     ["--kind", "check", "--name", "x", "--", "tools/nothing.py"]):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()):
                self.assertEqual(pr.main(argv), 2)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            pr.main(["--kind", "comparison", "--name", "x", "--", "tools/m4_prep.py"])


if __name__ == "__main__":
    unittest.main()
