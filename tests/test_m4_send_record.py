#!/usr/bin/env python3
"""Tests for tools/m4_send_record.py: which records it sends, and the copy to a fake board.

Usage: python3 tests/test_m4_send_record.py
"""
import hashlib
import io
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import m4_send_record as sr  # noqa: E402
import test_m4_order as tmo  # noqa: E402


class FakeBoard:
    """The board's files, {absolute path: bytes}, and the ssh commands the script sends."""

    def __init__(self, unpack_code=0, corrupt=False):
        self.files, self.commands, self.unpack_code, self.corrupt = {}, [], unpack_code, corrupt

    def listing(self, directory):
        inside = {p[len(directory) + 1:]: data for p, data in self.files.items() if p.startswith(directory + "/")}
        if not inside:
            return b"absent\n"
        return "".join(f"{hashlib.sha256(d).hexdigest()}  ./{p}\n" for p, d in sorted(inside.items())).encode()

    def board(self, host, command, data=None, timeout=300):
        self.commands.append((command, data))
        if command.startswith("if [ -d "):
            return 0, self.listing(command.split()[3]), ""
        if command.startswith("mkdir -p /home/pi/m4/send && cat > "):
            self.files[command.split()[-1]] = data
            return 0, b"", ""
        if command.startswith("sudo "):
            if self.unpack_code:
                return self.unpack_code, b"", "tar: error"
            inner = shlex.split(command)[-1]
            part, staged = re.search(r"tar -x --no-same-owner -C (\S+) -f (\S+)", inner).groups()
            src, dst = re.search(r"\[ ! -e \S+ \] && mv (\S+) (\S+) &&", inner).groups()
            with tarfile.open(fileobj=io.BytesIO(self.files.pop(staged))) as tar:
                for m in tar.getmembers():
                    self.modes = getattr(self, "modes", {}) | {m.name: (m.mode, m.uid, m.uname)}
                    if m.isfile():
                        data = tar.extractfile(m).read()
                        self.files[f"{part}/{m.name}"] = data + (b"x" if self.corrupt else b"")
            if any(p.startswith(dst + "/") for p in self.files):
                return 1, b"", ""  # [ ! -e target ]
            for p in [p for p in self.files if p.startswith(src + "/")]:
                self.files[dst + p[len(src):]] = self.files.pop(p)
            return 0, b"", ""
        raise AssertionError(command)


class Send(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, record, fake, *extra):
        def retried(host, command, data=None, timeout=300, tries=6):
            code, out, err = fake.board(host, command, data, timeout)
            if code:
                raise sr.bp.Lost(err)
            return out

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sr.bp, "board", fake.board), mock.patch.object(sr.bp, "retried", retried), \
                mock.patch.dict(sr.os.environ, {"BOARD_SUDO_PASSWORD": "pw"}), redirect_stdout(out), redirect_stderr(err):
            code = sr.main([str(record), *extra])
        return code, out.getvalue() + err.getvalue()

    def test_what_it_sends(self):
        r = self.root / "results/m4"
        cases = [
            (tmo.fp32_pass(r, "20261120T010000Z_fp32_yolov8n"), None),
            (tmo.fp32_pass(r, "20261120T020000Z_fp32_invalid", valid=0), None),  # §5: an invalid one too
            (tmo.fp32_pass(r, "20261120T030000Z_fp32_running", with_pass_csv=False, ended=False), "has no exit.txt"),
            (tmo.fp32_pass(r, "20261120T040000Z_check", kind="check"), "not an FP32 pass of run kind m4"),
            (tmo.fp32_pass(r, "20261120T050000Z_coco", tool="tools/m4_coco.py"), "not an FP32 pass"),
            (tmo.board_pass(r, "20261120T060000Z_int8_yolov8n"), "pc.json cannot be read"),
            (tmo.fp32_pass(r, "fp32_yolov8n"), "not a record directory"),
        ]
        for record, refused in cases:
            with self.subTest(record=record.name):
                if refused:
                    with self.assertRaisesRegex(sr.Refused, refused):
                        sr.check_record(record)
                else:
                    sr.check_record(record)

    def test_the_copy(self):
        record = tmo.fp32_pass(self.root / "results/m4", "20261120T010000Z_fp32_yolov8n")
        (record / "m4_fp32.log").write_bytes(b"line\n")
        fake = FakeBoard()
        code, out = self.send(record, fake)
        self.assertEqual(code, 0, out)
        target = f"{sr.BOARD_RESULTS}/{record.name}"
        self.assertEqual({p: d for p, d in fake.files.items()},
                         {f"{target}/{p.name}": p.read_bytes() for p in record.iterdir()})
        self.assertIn("4 files, the same SHA-256 as the PC's", out)
        sudo = [c for c, _ in fake.commands if c.startswith("sudo ")]
        self.assertEqual(len(sudo), 1)
        self.assertTrue(sudo[0].startswith("sudo -S -p '' sh -c "))
        # unpacked beside the target, then moved into place: a copy cut short leaves nothing there
        staging = f"/home/pi/m4/send/{record.name}"
        self.assertIn(f"tar -x --no-same-owner -C {staging}.part -f {staging}.tar && [ ! -e {target} ] && "
                      f"mv {staging}.part/{record.name} {target} && rm -rf {staging}.part {staging}.tar", sudo[0])
        self.assertEqual([d for c, d in fake.commands if c.startswith("sudo ")], [b"pw\n"])  # the password on stdin
        self.assertEqual(fake.modes[f"{record.name}/pc.json"], (0o644, 0, "root"))
        self.assertEqual(fake.modes[record.name], (0o755, 0, "root"))
        # Again: already there and the same, so nothing is sent.
        fake.commands.clear()
        code, out = self.send(record, fake)
        self.assertEqual(code, 0, out)
        self.assertIn("already on the board, the same", out)
        self.assertEqual(len(fake.commands), 1)
        # A record on the board with other files is not overwritten.
        fake.files[f"{target}/pass.csv"] = b"other"
        code, out = self.send(record, fake)
        self.assertEqual(code, 2)
        self.assertIn("on the board with other files: nothing is overwritten", out)

    def test_failures(self):
        record = tmo.fp32_pass(self.root / "results/m4", "20261120T010000Z_fp32_yolov8n")
        code, out = self.send(record, FakeBoard(unpack_code=1))
        self.assertEqual(code, 1)
        self.assertIn("the copy failed: unpacking as root: exit 1: tar: error", out)
        code, out = self.send(record, FakeBoard(corrupt=True))
        self.assertEqual(code, 1)
        self.assertIn("does not have the PC's files", out)
        for to in ("relative/dir", "/home/pi/a b", "/x';rm", "/home/pi/../etc", "/home/pi/x\nrm"):
            code, out = self.send(record, FakeBoard(), "--to", to)
            self.assertEqual(code, 2, to)
            self.assertIn("--to is an absolute directory", out)
        code, out = self.send(record, FakeBoard(), "--to", "C:/Program Files/Git/home/pi/m4")  # Git Bash's doing
        self.assertEqual(code, 2)
        self.assertIn("is a Windows path: from Git Bash, run with MSYS_NO_PATHCONV=1", out)
        code, out = self.send(record, FakeBoard(), "--to", "/home/pi/m4/trial/")
        self.assertEqual(code, 0, out)
        self.assertIn(f"/home/pi/m4/trial/{record.name}: 3 files", out)  # pc.json, pass.csv, exit.txt

    def test_a_pass_whose_pc_run_died(self):
        r = self.root / "results/m4"
        record = tmo.fp32_pass(r, "20261120T010000Z_fp32_yolov8n", ended=False)  # pass.csv, no exit.txt
        code, out = self.send(record, FakeBoard())
        self.assertEqual(code, 2)
        self.assertIn("has no exit.txt: the pass has not ended. If pc_run.py died", out)

        def docker(listing, code=0):
            return mock.patch.object(sr.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], code, stdout=listing, stderr="error during connect"))

        name = f"m4-{record.name}"
        with docker(f"other\trunning\n{name}\trunning\n"):
            code, out = self.send(record, FakeBoard(), "--ended")
        self.assertEqual(code, 2)
        self.assertIn(f"its container {name} is running: the pass has not ended", out)
        with docker("", code=1):
            code, out = self.send(record, FakeBoard(), "--ended")
        self.assertEqual(code, 2)
        self.assertIn("start Docker Desktop", out)
        self.assertFalse((record / "exit.txt").exists())
        with docker("other\trunning\n") as run:
            code, out = self.send(record, FakeBoard(), "--ended")
        self.assertEqual(code, 0, out)
        self.assertEqual(run.call_args[0][0][:3], ["docker", "ps", "-a"])
        ended = tmo.mo.keyed(record / "exit.txt")
        self.assertEqual(ended["exit_code"], "none")
        self.assertIn("written_by: tools/m4_send_record.py --ended", (record / "exit.txt").read_text(encoding="utf-8"))
        # The pass counts as its pass.csv says, and ends no earlier than the tool's own end.
        a, = tmo.mo.pass_attempts(r, tmo.F4)
        self.assertEqual(a.outcome, "valid")
        self.assertEqual(a.end, max(tmo.utc(ended["end_time_utc"]), tmo.utc("2026-11-20T03:23:21Z")))
        # A pc.json of an older pc_run.py names no container: its end cannot be checked.
        old = tmo.fp32_pass(r, "20261120T020000Z_fp32_yolov10n", "yolov10n", ended=False, container=False)
        with docker(""):
            code, out = self.send(old, FakeBoard(), "--ended")
        self.assertEqual(code, 2)
        self.assertIn("names no container", out)


if __name__ == "__main__":
    unittest.main()
