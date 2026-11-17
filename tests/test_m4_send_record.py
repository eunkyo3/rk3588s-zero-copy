#!/usr/bin/env python3
"""Tests for tools/m4_send_record.py: which records it sends, and the copy to a fake board.

Usage: python3 tests/test_m4_send_record.py
"""
import hashlib
import io
import shlex
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
            to, staged = inner.split()[2], inner.split()[10]
            with tarfile.open(fileobj=io.BytesIO(self.files.pop(staged))) as tar:
                for m in tar.getmembers():
                    self.modes = getattr(self, "modes", {}) | {m.name: (m.mode, m.uid, m.uname)}
                    if m.isfile():
                        data = tar.extractfile(m).read()
                        self.files[f"{to}/{m.name}"] = data + (b"x" if self.corrupt else b"")
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
        self.assertIn("tar -x --no-same-owner -C /home/pi/rk3588s-zero-copy/results/m4 -f "
                      f"/home/pi/m4/send/{record.name}.tar && rm -f /home/pi/m4/send/{record.name}.tar", sudo[0])
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
        for to in ("relative/dir", "/home/pi/a b", "/x';rm"):
            code, out = self.send(record, FakeBoard(), "--to", to)
            self.assertEqual(code, 2, to)
            self.assertIn("--to is an absolute directory", out)
        code, out = self.send(record, FakeBoard(), "--to", "/home/pi/m4/trial/")
        self.assertEqual(code, 0, out)
        self.assertIn(f"/home/pi/m4/trial/{record.name}: 3 files", out)  # pc.json, pass.csv, exit.txt


if __name__ == "__main__":
    unittest.main()
