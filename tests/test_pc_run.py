#!/usr/bin/env python3
"""Tests for tools/eval/pc_run.py: the checks of prereg/M4.md §4 before a run on the PC.

Usage: python3 tests/test_pc_run.py
"""
import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.append(str(Path(__file__).resolve().parent.parent / "tools" / "eval"))
import pc_run as pr  # noqa: E402

# The PC's own output (its Korean text decoded), with two samples that timed out.
STRIPCHART = ("time.windows.com[52.231.114.183:123] 추적 중\n3개 샘플 수집 중\n"
              "현재 시간은 2026-11-16 오후 2:30:28입니다.\n14:30:28, 오류: 0x800705B4\n"
              "14:30:31, +00.7527771s\n14:30:33, 오류: 0x800705B4\n")
IMAGE_ID = pr.IMAGE_ID_FILE.read_text(encoding="utf-8").strip()


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

    def test_the_image_id_file(self):
        self.assertRegex(IMAGE_ID, r"^sha256:[0-9a-f]{64}$")


class FakeContainer:
    """docker run -i of the clock loop: it answers each line with clock() and ends at the end of input."""

    def __init__(self, clock):
        self.clock, self.closed, self.killed = clock, False, False
        self.stdout = self
        self.stdin = mock.MagicMock()
        self.lines = []
        self.stdin.write.side_effect = lambda s: self.lines.append(repr(self.clock()) + "\n")
        self.stdin.close.side_effect = lambda: setattr(self, "closed", True)

    def __iter__(self):
        while not self.closed:
            if self.lines:
                yield self.lines.pop(0)

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


class Checks(unittest.TestCase):
    def fake_run(self, status="", untracked="", tracked=True, flags="H tools/m4_prep.py\n", offsets=STRIPCHART,
                 image=True, running="", vm="8273903616\n"):
        def run(cmd, timeout=120):
            if cmd[:2] == ["docker", "ps"]:
                return 0, running
            if cmd[:2] == ["docker", "info"]:
                return 0, vm
            if cmd[0] == "git":
                if "rev-parse" in cmd:
                    return 0, "c" * 40 + "\n"
                if "--error-unmatch" in cmd:
                    return (0, cmd[-1]) if tracked else (1, "did not match")
                if "ls-files" in cmd:
                    return 0, flags
                return 0, untracked if "--untracked-files=all" in cmd else status
            if cmd[:3] == ["docker", "image", "inspect"]:
                self.assertEqual(cmd[-1], IMAGE_ID)  # inspected by the fixed ID, not the tag
                return (0, IMAGE_ID + "\n") if image else (1, "no such image")
            if cmd[0] == "powershell":
                return 0, "Intel(R) Core(TM) i7-6500U CPU @ 2.50GHz\r\n"
            if "/stripchart" in cmd:
                return 0, offsets
            if "/query" in cmd:
                return -2147023834, "서비스가 시작되지 않았습니다. (0x80070426)\n"
            raise AssertionError(cmd)
        return run

    def checks(self, container=(0.0012, 0.003), allow_dirty=False, tool="tools/m4_prep.py", **kw):
        with mock.patch.object(pr, "run", self.fake_run(**kw)), \
                mock.patch.object(pr, "container_clock", return_value=container) as clock:
            return pr.checks(pr.IMAGE, tool, allow_dirty), clock

    def test_a_run_that_starts(self):
        pc, clock = self.checks()
        clock.assert_called_once_with(IMAGE_ID)  # by the fixed ID, not the tag
        self.assertEqual((pc["code_commit"], pc["code_status"], pc["eval_image_id"]), ("c" * 40, "clean", IMAGE_ID))
        self.assertEqual({k: pc["host"][k] for k in ("cpu_model", "ntp_offset_s", "ntp_samples", "w32tm_status_exit")},
                         {"cpu_model": "Intel(R) Core(TM) i7-6500U CPU @ 2.50GHz", "ntp_offset_s": "+0.7528",
                          "ntp_samples": "1 answered, 2 did not", "w32tm_status_exit": -2147023834})
        self.assertIn("0x80070426", pc["host"]["w32tm_status"])
        self.assertEqual(pc["container_clock_offset_s"], "+0.0012")
        self.assertEqual(pc["container_memory"], "2g, 4g with swap")
        # the other containers and the VM's size do not matter at 2 GB
        pc, _ = self.checks(running="other\n", vm="4000000000\n")
        self.assertEqual(pc["container_memory"], "2g, 4g with swap")

    def test_the_evaluations_memory(self):
        self.assertEqual(pr.memory("tools/m4_coco.py"), (["--memory=6g", "--memory-swap=6g"], "6g, no swap"))
        for spelling in ("./tools/m4_coco.py", "tools/../tools/m4_coco.py", "tools\\m4_coco.py"):
            if "\\" not in spelling or sys.platform == "win32":
                self.assertEqual(pr.memory(spelling)[1], "6g, no swap", spelling)
        self.assertEqual(pr.memory("../tools/m4_coco.py")[1], "2g, 4g with swap")  # outside the repository
        self.assertEqual(pr.memory("tools/m4_fp32.py"), (["--memory=2g", "--memory-swap=4g"], "2g, 4g with swap"))
        self.assertEqual(pr.memory(None)[1], "2g, 4g with swap")
        pc, _ = self.checks(tool="tools/m4_coco.py", flags="H tools/m4_coco.py\n")
        self.assertEqual(pc["container_memory"], "6g, no swap")
        for kw, message in (({"running": "uwb-n8n\nta0q-db\n"}, "other containers run: uwb-n8n | ta0q-db"),
                            ({"vm": "6900000000\n"}, "the Docker VM has 6.4 GB (at least 7 GB)")):
            with self.subTest(kw=kw), self.assertRaisesRegex(pr.Refused, re.escape(message)):
                self.checks(tool="tools/m4_coco.py", flags="H tools/m4_coco.py\n", **kw)

    def test_what_stops_a_run(self):
        for kw, message in (({"status": " M tools/m4_prep.py\n"}, "the tree differs from HEAD: M tools/m4_prep.py"),
                            ({"untracked": "?? tools/numpy.py\n"}, "?? tools/numpy.py"),
                            ({"tracked": False}, "tools/m4_prep.py is not tracked by git"),
                            ({"flags": "H a\nh tools/m4_prep.py\n"}, "assume-unchanged or skip-worktree: h tools/m4_prep.py"),
                            ({"flags": "S tools/x.py\n"}, "skip-worktree: S tools/x.py"),
                            ({"image": False}, "docker load -i"),
                            ({"offsets": "14:30:28, 오류: 0x800705B4\n"}, "no sample"),
                            ({"offsets": "14:30:31, +01.2000000s\n"}, "+1.200 s off"),
                            ({"container": (-1.5, 0.01)}, "container's clock is -1.500 s off")):
            with self.subTest(kw=kw), self.assertRaisesRegex(pr.Refused, re.escape(message)):
                self.checks(**kw)
        pc, _ = self.checks(status=" M tools/m4_prep.py\n", untracked="?? tests/x.py\n", allow_dirty=True)
        self.assertEqual(pc["code_status"], "differs: M tools/m4_prep.py | ?? tests/x.py")  # development: recorded
        with self.assertRaisesRegex(pr.Refused, "not tracked"):  # --allow-dirty never runs an untracked tool
            self.checks(tracked=False, allow_dirty=True)

    def test_the_container_clock(self):
        times = iter([100.0, 100.4, 101.0, 101.2, 102.0, 102.02, 103.0, 103.5, 104.0, 104.1, 105.0, 105.3])
        fake = FakeContainer(lambda: 1000.0)
        with mock.patch.object(pr.subprocess, "Popen", return_value=fake) as popen, \
                mock.patch.object(pr.time, "time", side_effect=lambda: next(times)):
            offset, rtt = pr.container_clock(IMAGE_ID)
        cmd = popen.call_args[0][0]
        self.assertEqual(cmd[:3], ["docker", "run", "-i"])
        self.assertIn(IMAGE_ID, cmd)
        self.assertEqual(cmd[cmd.index("--network") + 1], "none")  # no network
        self.assertAlmostEqual(rtt, 0.02)  # the first exchange (the container's start) is left out
        self.assertAlmostEqual(offset, 1000.0 - 102.01)
        self.assertTrue(fake.closed and not fake.killed)  # its input ended, so it ended


class Main(unittest.TestCase):
    def main(self, argv, env=None, checks=None):
        env = {"MSYS_NO_PATHCONV": "1"} if env is None else env
        with mock.patch.dict(pr.os.environ, env, clear=False), \
                mock.patch.object(pr, "checks", side_effect=checks or pr.Refused("stop here")) as c, \
                redirect_stderr(io.StringIO()) as err, redirect_stdout(io.StringIO()):
            if "MSYS_NO_PATHCONV" not in env:
                pr.os.environ.pop("MSYS_NO_PATHCONV", None)
            code = pr.main(argv)
        return code, err.getvalue(), c

    def test_what_main_refuses_before_its_checks(self):
        with tempfile.TemporaryDirectory() as data:
            ok = ["--kind", "check", "--name", "x", "--data", data, "--", "tools/m4_prep.py"]
            for argv, env, message in (
                    (ok, {"MSYSTEM": "MINGW64"}, "MSYS_NO_PATHCONV=1"),
                    (["--kind", "check", "--name", "a/b", "--data", data, "--", "tools/m4_prep.py"], None, "the name"),
                    (["--kind", "check", "--name", "x", "--data", data], None, "name a tool"),
                    (ok[:6] + ["--checks-only", "--", "tools/m4_prep.py"], None, "name a tool"),
                    (ok[:6] + ["--", "tools/nothing.py"], None, "no tools/nothing.py"),
                    (["--kind", "m4", "--name", "x", "--data", data, "--allow-dirty", "--", "tools/m4_prep.py"], None,
                     "never a run of kind m4"),
                    (ok[:4] + ["--data", str(Path(data) / "no"), "--", "tools/m4_prep.py"], None, "not a directory")):
                with self.subTest(argv=argv, env=env):
                    code, err, checks = self.main(argv, env)
                    self.assertEqual(code, 2)
                    self.assertIn(message, err)
                    checks.assert_not_called()
            code, err, checks = self.main(ok)  # past main's own checks
            self.assertEqual((code, checks.call_count), (2, 1))
            self.assertIn("stop here", err)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            pr.main(["--kind", "comparison", "--name", "x", "--", "tools/m4_prep.py"])

    def test_a_git_bash_path_is_refused_on_windows(self):
        with mock.patch.object(pr.sys, "platform", "win32"):
            code, err, _ = self.main(["--kind", "check", "--name", "x", "--data", "/d/m4data", "--", "tools/m4_prep.py"])
            self.assertEqual(code, 2)
            self.assertIn("Git Bash path", err)
            # --checks-only mounts no data: it needs none
            code, err, checks = self.main(["--kind", "check", "--name", "x", "--data", "/d/nothing", "--checks-only"])
            self.assertEqual((code, checks.call_count), (2, 1))
            self.assertIn("stop here", err)

    def test_kind_m4_waits_for_freeze_m4(self):
        with tempfile.TemporaryDirectory() as data:
            argv = ["--kind", "m4", "--name", "x", "--data", data, "--", "tools/m4_prep.py"]
            with mock.patch.object(pr, "stage_fields", return_value={"run_kind": "m4", "freeze_m4_commit": "none"}):
                code, err, checks = self.main(argv)
            self.assertEqual(code, 2)
            self.assertIn("comes after freeze-m4", err)
            checks.assert_not_called()  # nothing checked, no record made
            with mock.patch.object(pr, "stage_fields", return_value={"run_kind": "m4", "freeze_m4_commit": "f4" * 20}):
                code, err, checks = self.main(argv)
            self.assertEqual((code, checks.call_count), (2, 1))

    def test_the_record_and_the_container(self):
        pc = {"code_commit": "c" * 40, "code_status": "clean", "eval_image": pr.IMAGE, "eval_image_id": IMAGE_ID,
              "host": {"ntp_offset_s": "+0.1", "ntp_samples": "3 answered, 0 did not"},
              "container_clock_offset_s": "+0.0010", "container_clock_rtt_s": "0.0020"}
        with tempfile.TemporaryDirectory() as t:
            repo = Path(t)
            (repo / "tools").mkdir()
            (repo / "tools/x.py").write_text("")
            with mock.patch.object(pr, "REPO", repo), \
                    mock.patch.object(pr, "stage_fields", side_effect=lambda kind: {"run_kind": kind, "amendment": "A13"}), \
                    mock.patch.object(pr.subprocess, "Popen") as popen:
                popen.return_value.stdout = [b"line\n"]
                popen.return_value.wait.return_value = 3
                code, _, _ = self.main(["--kind", "check", "--name", "x", "--data", t, "--", "tools/x.py", "--a", "/data"],
                                       checks=lambda *a: pc)
                self.assertEqual(code, 3)  # the tool's
                record = next((repo / "results/check").iterdir())
                written = json.loads((record / "pc.json").read_text(encoding="utf-8"))
                self.assertEqual(list(written)[:3], ["start_time_utc", "run_kind", "amendment"])
                self.assertEqual(written["tool"], ["tools/x.py", "--a", "/data"])
                self.assertEqual(written["record"], record.name)  # the tool names its outputs after it
                self.assertEqual((record / "x.log").read_bytes(), b"line\n")
                cmd = popen.call_args[0][0]
                self.assertEqual(cmd[cmd.index(IMAGE_ID):], [IMAGE_ID, "python3", "-I", "/repo/tools/x.py", "--a", "/data"])
                self.assertIn(f"type=bind,source={repo},target=/repo,readonly", cmd)
                self.assertTrue({"--network", "none", "no-new-privileges", "--memory=2g", "--memory-swap=4g"} <= set(cmd))
                # the COCO evaluation: 6 GB without swap
                (repo / "tools/m4_coco.py").write_text("")
                self.main(["--kind", "check", "--name", "e", "--data", t, "--", "tools/m4_coco.py"], checks=lambda *a: pc)
                cmd = popen.call_args[0][0]
                self.assertTrue({"--memory=6g", "--memory-swap=6g"} <= set(cmd))
                self.assertNotIn("--memory=2g", cmd)
                # --checks-only: the record, and no container
                popen.reset_mock()
                code, _, _ = self.main(["--kind", "check", "--name", "clock", "--data", t, "--checks-only"],
                                       checks=lambda *a: pc)
                self.assertEqual(code, 0)
                popen.assert_not_called()
                self.assertEqual(len(list((repo / "results/check").glob("*_clock/pc.json"))), 1)

    def test_stage_fields(self):
        f = pr.stage_fields("check")
        self.assertEqual((f["run_kind"], f["prereg_m4_commit"], f["freeze_m1_commit"]),
                         ("check", pr.ss.PREREG_M4_COMMIT, pr.ss.FREEZE_M1_COMMIT))
        self.assertRegex(f["amendment"], r"^A[0-9]+$")
        self.assertIn(f["freeze_m4_commit"], ("none",) + tuple(pr.ss.registration_commits(
            (pr.REPO / pr.ss.ENV_MD).read_text(encoding="utf-8")).values()))


if __name__ == "__main__":
    unittest.main()
