#!/usr/bin/env python3
"""Runs M4's board tool (tools/m4_accuracy.cpp) from the PC: it sends the inputs to the board in chunks
while the tool runs, as prereg/M4.md §2.2 has it, then fetches the records (§2.5, §8 step 3).

  python tools/m4_board_pass.py pass --model yolov8n --kind check --name rules_yolov8n --limit 50 --dets-in-record
  python tools/m4_board_pass.py decode --kind check --name m4_decode
  python tools/m4_board_pass.py wiring --model yolov10n --kind check --name wiring_yolov10n
  python tools/m4_board_pass.py pass --model yolov8n --kind m4 --freeze-m4-commit <sha> \\
      --compare-dump results/check/<UTC>_rules_yolov8n/dump

  - The board: the repository at ~/rk3588s-zero-copy, built by tools/deploy_board.sh. Its build's
    commit must be the PC's HEAD, without -dirty, for a run of kind m4; a check records it.
  - The run: tools/m4_board_run.sh, as root, in the background (sudo gets BOARD_SUDO_PASSWORD on its
    standard input, or runs with -n without it). Its record is results/<kind>/<UTC>_<name> on the board.
  - Inputs (pass, decode): the manifest's PNGs (--inputs, default D:/m4data/inputs2) in chunks of
    --chunk images, each sent as a tar to ~/m4/inputs/<record>/chunk_<k>.part and renamed chunk_<k> when
    whole. At most --ahead chunks are on the board at once; the tool deletes each after its images.
  - Afterwards: the record goes to results/<kind>/<record>/ here, and a pass's detections (unless
    --dets-in-record) from ~/m4/passes/<record>/ to D:/m4data/passes/<record>/. The record's dumps
    (*.i8) are not committed (.gitignore).
Environment: SSH (default "ssh") with options that log in without a prompt; BOARD_SUDO_PASSWORD.
Exit: the board tool's exit code (0 valid), or 2 when the run cannot start.
"""
import argparse
import datetime as dt
import io
import os
import re
import shlex
import subprocess
import sys
import tarfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO / "tools"))
import m4_coco as mc  # noqa: E402  (the manifest)

MANIFEST = "results/check/20261116T064917Z_m4_inputs/manifest.tsv"  # the inputs M4 uses
MODELS = {"yolov8n": "models/yolov8n_i8.rknn", "yolov10n": "models/yolov10n_i8.rknn"}
TENSORS = "/home/pi/calib/tensors_c.u8"  # M1-3's tensor set (A7), for the wiring check
BOARD_REPO = "rk3588s-zero-copy"


class Refused(Exception):
    pass


def ssh_base():
    return shlex.split(os.environ.get("SSH", "ssh"))


def board(host, command, data=None, timeout=300, binary=False):
    """(exit code, output) of command on the board, with data on its standard input."""
    r = subprocess.run(ssh_base() + [host, command], input=data, capture_output=True, timeout=timeout)
    out = r.stdout if binary else r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
    return r.returncode, out


def git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout.strip()


def board_commit(host):
    code, out = board(host, f"grep -h '^CODE_COMMIT:' {BOARD_REPO}/build/CMakeCache.txt")
    m = re.search(r"=(\S+)", out)
    if code or not m:
        raise Refused(f"no build on the board ({out.strip()}): run tools/deploy_board.sh")
    return m.group(1)


def send_chunk(host, inputs, k, names, local_dir):
    """Sends the chunk's PNGs as one tar; the chunk appears on the board only when whole."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name in names:
            tar.add(local_dir / name, arcname=name)
    part, whole = f"{inputs}/chunk_{k:04d}.part", f"{inputs}/chunk_{k:04d}"
    code, out = board(host, f"mkdir -p {part} && tar -x -C {part} && mv {part} {whole}", data=buf.getvalue(), timeout=1800)
    if code:
        raise RuntimeError(f"sending chunk {k} failed: {out.strip()}")


def chunks_on_board(host, inputs):
    code, out = board(host, f"ls -d {inputs}/chunk_* 2>/dev/null | grep -vc '\\.part$'")
    return int(out.strip() or 0)


def finished(host, record):
    code, out = board(host, f"cat {BOARD_REPO}/{record}/exit_code 2>/dev/null")
    return int(out.strip()) if code == 0 and out.strip().lstrip("-").isdigit() else None


def fetch(host, board_dir, name, local_parent):
    """Copies the board's board_dir/name into local_parent/name."""
    code, data = board(host, f"tar -C {board_dir} -cf - {name}", timeout=3600, binary=True)
    if code:
        raise RuntimeError(f"fetching {board_dir}/{name} failed")
    local_parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
        tar.extractall(local_parent, filter="data")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["pass", "decode", "wiring"])
    ap.add_argument("--kind", required=True, choices=["check", "m4"])
    ap.add_argument("--name", required=True, help="the record's name: results/<kind>/<UTC>_<name>")
    ap.add_argument("--model", choices=list(MODELS))
    ap.add_argument("--freeze-m4-commit", default="none")
    ap.add_argument("--limit", type=int, help="the first N images (check only)")
    ap.add_argument("--dump", type=int, default=50)
    ap.add_argument("--compare-dump", help="a record's dump directory, as the board has it (results/check/...)")
    ap.add_argument("--dets-in-record", action="store_true", help="a check's detections in its record")
    ap.add_argument("--chunk", type=int, default=500)
    ap.add_argument("--ahead", type=int, default=3)
    ap.add_argument("--inputs", default="D:/m4data/inputs2")
    ap.add_argument("--passes", default="D:/m4data/passes")
    ap.add_argument("--host", default="pi@192.168.123.90")
    a = ap.parse_args(argv)
    host = a.host
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", a.name):
            raise Refused("the name is letters, digits, _ and -")
        if a.mode in ("pass", "wiring") and not a.model:
            raise Refused(f"{a.mode} needs --model")
        if a.mode != "pass" and a.kind != "check":
            raise Refused(f"{a.mode} is a check of §8 step 3: --kind check")
        if a.limit is not None and (a.limit < 1 or a.kind != "check"):
            raise Refused("--limit is at least 1, for a check")
        head = git("rev-parse", "HEAD")
        dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
        built = board_commit(host)
        if a.kind == "m4" and (built != head or dirty):
            raise Refused(f"a run of kind m4 needs the board built from the PC's HEAD {head}, clean; it has {built}")
        if built != head:
            print(f"m4_board_pass: the board's build is {built}, the PC's HEAD {head} (recorded)", flush=True)
        ids = list(mc.read_manifest(REPO / MANIFEST))[:a.limit]
        local = Path(a.inputs)
        missing = [i for i in ids if not (local / f"{i:012d}.png").is_file()] if a.mode != "wiring" else []
        if missing:
            raise Refused(f"{len(missing)} PNGs are missing from {local}, the first {missing[0]:012d}.png")
    except (Refused, subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as e:
        print(f"m4_board_pass: not starting: {e}", file=sys.stderr)
        return 2

    now = dt.datetime.now(dt.timezone.utc)
    name = f"{now:%Y%m%dT%H%M%SZ}_{a.name}"
    record = f"results/{a.kind}/{name}"
    inputs = f"/home/pi/m4/inputs/{name}"
    out = f"{record}/dets" if a.dets_in_record else f"/home/pi/m4/passes/{name}"
    args = [a.mode]
    if a.mode == "pass":
        args += ["--model", MODELS[a.model], "--manifest", MANIFEST, "--inputs", inputs, "--out", out,
                 "--run-kind", a.kind, "--freeze-m4-commit", a.freeze_m4_commit, "--dump", str(a.dump),
                 "--chunk", str(a.chunk)]
        args += ["--limit", str(a.limit)] if a.limit else []
        args += ["--compare-dump", a.compare_dump] if a.compare_dump else []
    elif a.mode == "decode":
        args += ["--manifest", MANIFEST, "--inputs", inputs, "--chunk", str(a.chunk)]
        args += ["--limit", str(a.limit)] if a.limit else []
    else:
        args += ["--model", MODELS[a.model], "--tensors", TENSORS]
    password = os.environ.get("BOARD_SUDO_PASSWORD")
    sudo = "sudo -S -p ''" if password else "sudo -n"
    inner = f"setsid nohup sh tools/m4_board_run.sh {record} {shlex.join(args)} > /dev/null 2>&1 < /dev/null &"
    code, outp = board(host, f"cd {BOARD_REPO} && mkdir -p {inputs} && {sudo} sh -c {shlex.quote(inner)}",
                       data=(password + "\n").encode() if password else None)
    if code:
        print(f"m4_board_pass: not starting: {outp.strip()}", file=sys.stderr)
        return 2
    print(f"m4_board_pass: {record} started on the board (its build {built})", flush=True)

    started = time.time()
    if a.mode != "wiring":
        groups = [ids[i:i + a.chunk] for i in range(0, len(ids), a.chunk)]
        for k, group in enumerate(groups):
            while chunks_on_board(host, inputs) >= a.ahead and finished(host, record) is None:
                time.sleep(2)
            if finished(host, record) is not None:
                break  # the tool stopped early (an invalid pass): its record says why
            send_chunk(host, inputs, k, [f"{i:012d}.png" for i in group], local)
            print(f"m4_board_pass: chunk {k + 1} of {len(groups)} sent, {time.time() - started:.0f} s", flush=True)
    while (code := finished(host, record)) is None:
        time.sleep(5)
    print(f"m4_board_pass: the board tool exited {code} after {time.time() - started:.0f} s", flush=True)

    fetch(host, f"{BOARD_REPO}/results/{a.kind}", name, REPO / "results" / a.kind)
    if a.mode == "pass" and not a.dets_in_record:
        fetch(host, "/home/pi/m4/passes", name, Path(a.passes))
    board(host, f"rmdir {inputs} 2>/dev/null; true")
    summary = REPO / record / {"pass": "pass.csv", "decode": "decode.csv", "wiring": "wiring.csv"}[a.mode]
    if summary.exists():
        print(summary.read_text(encoding="utf-8"), end="")
    print(f"m4_board_pass: {record}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
