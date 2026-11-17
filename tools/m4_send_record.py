#!/usr/bin/env python3
"""Copies an FP32 pass's record from the PC to the board's results/m4/, where the gates of M4's calib_npu
session and pipeline sessions look for the four accuracy passes (prereg/M4.md §8 step 5;
tools/m4_order.py).

  python tools/m4_send_record.py results/m4/<UTC>_fp32_yolov8n

  - The record: a pass that ended. Its pc.json names tools/m4_fp32.py and run kind m4, and it has
    exit.txt (tools/eval/pc_run.py writes it when the tool ends). An invalid pass is sent too: a second
    invalid one makes a missing pass (§5).
  - The copy: the record's files go to the board as one tar, which root unpacks into
    ~/rk3588s-zero-copy/results/m4/<record>/ (--to: another directory, for a trial). Sessions write
    results/ as root. A record already there is left alone if its files are the same, and refused if
    not.
  - The check: afterwards the board's SHA-256 of every file must be the PC's.
Environment: SSH and BOARD_SUDO_PASSWORD, as for tools/m4_board_pass.py.
Exit 0 when the board has the record, 1 when the copy failed, 2 when it cannot start.
"""
import argparse
import hashlib
import io
import json
import os
import re
import shlex
import sys
import tarfile
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
import m4_board_pass as bp  # noqa: E402  (ssh with retries)
import m4_order as mo  # noqa: E402

BOARD_RESULTS = f"/home/pi/{bp.BOARD_REPO}/results/m4"
STAGING = "/home/pi/m4/send"  # the tar, until root unpacks it


class Refused(Exception):
    pass


def local_files(record):
    """{relative path: SHA-256} of the record's files."""
    return {p.relative_to(record).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(record.rglob("*")) if p.is_file()}


def check_record(record):
    """Refused unless record is an FP32 pass of run kind m4 that ended."""
    if not mo.RECORD.fullmatch(record.name):
        raise Refused(f"{record} is not a record directory, <UTC>_<name>")
    try:
        pc = json.loads((record / "pc.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Refused(f"{record}/pc.json cannot be read: {e}") from None
    tool = (pc.get("tool") or []) if isinstance(pc, dict) else []
    if tool[:1] != [mo.FP32_TOOL] or pc.get("run_kind") != "m4":
        raise Refused(f"{record} is not an FP32 pass of run kind m4 ({mo.FP32_TOOL} through tools/eval/pc_run.py)")
    if not (record / "exit.txt").is_file():
        raise Refused(f"{record} has no exit.txt: the pass has not ended")


def board_files(host, directory):
    """{relative path: SHA-256} of the board's directory, or None if it has none."""
    out = bp.retried(host, f"if [ -d {directory} ]; then cd {directory} && find . -type f -print0 | "
                           "xargs -0 -r sha256sum; else echo absent; fi").decode()
    if out.strip() == "absent":
        return None
    files = {}
    for line in out.splitlines():
        sha, _, path = line.partition("  ")
        files[path[2:] if path.startswith("./") else path] = sha
    return files


def tar_of(record):
    """The record as a tar: files 0644 and directories 0755, owned by root."""
    def normal(info):
        info.mode = 0o755 if info.isdir() else 0o644
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        return info

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.add(record, arcname=record.name, filter=normal)
    return buf.getvalue()


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("record", type=Path, help="results/m4/<UTC>_<name>, an FP32 pass's record")
    ap.add_argument("--to", default=BOARD_RESULTS, help="the board's directory (default: its results/m4)")
    ap.add_argument("--host", default="pi@192.168.123.90")
    a = ap.parse_args(argv)
    record, host, to = a.record.resolve(), a.host, a.to.rstrip("/")
    name = record.name
    target = f"{to}/{name}"
    try:
        if re.match(r"[A-Za-z]:[/\\]", to):
            raise Refused(f"--to {to} is a Windows path: from Git Bash, run with MSYS_NO_PATHCONV=1")
        if not to.startswith("/") or any(c in to for c in " '\"$`\\;&|<>*?"):
            raise Refused("--to is an absolute directory of the board, without spaces, quotes or shell characters")
        check_record(record)
        mine = local_files(record)
        theirs = board_files(host, target)
        if theirs is not None:
            if theirs == mine:
                print(f"m4_send_record: {target} is already on the board, the same")
                return 0
            raise Refused(f"{target} is on the board with other files: nothing is overwritten")
    except (Refused, bp.Lost, OSError) as e:
        print(f"m4_send_record: not sending: {e}", file=sys.stderr)
        return 2
    password = os.environ.get("BOARD_SUDO_PASSWORD")
    sudo = "sudo -S -p ''" if password else "sudo -n"
    staged = f"{STAGING}/{name}.tar"
    unpack = f"mkdir -p {to} && tar -x --no-same-owner -C {to} -f {staged} && rm -f {staged}"  # root's files
    try:
        bp.retried(host, f"mkdir -p {STAGING} && cat > {staged}", data=tar_of(record), timeout=600)
        code, _, err = bp.board(host, f"{sudo} sh -c {shlex.quote(unpack)}",
                                data=(password + "\n").encode() if password else None, timeout=300)
        if code:
            raise bp.Lost(f"unpacking as root: exit {code}: {err.strip()[-300:]}")
        theirs = board_files(host, target)
    except (bp.Lost, OSError) as e:
        print(f"m4_send_record: the copy failed: {e}", file=sys.stderr)
        return 1
    if theirs != mine:
        print(f"m4_send_record: {target} on the board does not have the PC's files", file=sys.stderr)
        return 1
    print(f"m4_send_record: {target}: {len(mine)} files, the same SHA-256 as the PC's")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
