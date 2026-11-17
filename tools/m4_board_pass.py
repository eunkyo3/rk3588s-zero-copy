#!/usr/bin/env python3
"""Runs M4's board tool (tools/m4_accuracy.cpp) from the PC: it sends the inputs to the board in chunks
while the tool runs, as prereg/M4.md §2.2 has it, then fetches the records (§2.5, §8 step 3).

  python tools/m4_board_pass.py pass --model yolov8n --kind check --name rules_yolov8n --limit 50 --dets-in-record
  python tools/m4_board_pass.py decode --kind check --name m4_decode
  python tools/m4_board_pass.py wiring --model yolov10n --kind check --name wiring_yolov10n
  python tools/m4_board_pass.py pass --model yolov8n --kind m4 --freeze-m4-commit <sha> \\
      --compare-dump results/check/<UTC>_rules_yolov8n/dump

  - The board: the repository at ~/rk3588s-zero-copy, built by tools/deploy_board.sh. Its build's
    commit must be the PC's HEAD for a run of kind m4, whose tree is clean and whose code (src, tools,
    tests, CMakeLists.txt) is freeze-m4's; a check records the build's commit.
  - The run: tools/m4_board_run.sh, as root, detached from the ssh session (sudo gets
    BOARD_SUDO_PASSWORD on its standard input, or runs with -n without it). Its record is
    results/<kind>/<UTC>_<name> on the board.
  - Inputs (pass, decode): the manifest goes to ~/m4/inputs/<record>/manifest.tsv before the tool
    starts (the board's tree has no results/), then its PNGs (--inputs, default D:/m4data/inputs2) in
    chunks of --chunk images, each sent as a tar to ~/m4/inputs/<record>/chunk_<k>.part and renamed
    chunk_<k> when whole. At most --ahead chunks are on the board at once; the tool deletes each after
    its images, and this script deletes ~/m4/inputs/<record> at the end (the chunks of a run that
    stopped early).
  - Every ssh call is retried with a back-off (a dropped connection must not cost a pass), and the run
    is watched by its PID and the board's boot id (run.txt): a board reset, a killed run or
    --timeout-min end the wait, as an invalid run (§5).
  - Afterwards, whatever happened: the record goes to results/<kind>/<record>/ here, with
    board_pass.txt (this script's account of the run), and a pass's detections (unless
    --dets-in-record, for checks) from ~/m4/passes/<record>/ to D:/m4data/passes/<record>/. The
    record's dumps (*.i8) are not committed (.gitignore); their SHA-256 are, in dump.sha256.
Environment: SSH (default "ssh") with options that log in without a prompt; BOARD_SUDO_PASSWORD.
Exit: the board tool's exit code (0 valid), 1 for a run that ended without one, 2 when it cannot start.
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
RULES_IMAGES = 50  # §2.5, §8 step 3
CODE = ["src", "tools", "tests", "CMakeLists.txt"]


class Refused(Exception):
    pass


class Lost(Exception):
    """The board did not answer after the retries."""


def ssh_base():
    return shlex.split(os.environ.get("SSH", "ssh"), posix=os.name != "nt")


def board(host, command, data=None, timeout=300):
    """(exit code, stdout bytes, stderr text) of one ssh call."""
    r = subprocess.run(ssh_base() + [host, command], input=data, capture_output=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr.decode("utf-8", "replace")


def retried(host, command, data=None, timeout=300, tries=6):
    """board() until it exits 0, with a back-off of 2, 4, 8, ... s; Lost after the last try."""
    last = ""
    for t in range(tries):
        try:
            code, out, err = board(host, command, data, timeout)
            if code == 0:
                return out
            last = f"exit {code}: {err.strip()[-300:]}"
        except (subprocess.TimeoutExpired, OSError) as e:
            last = repr(e)
        time.sleep(2 ** (t + 1))
    raise Lost(f"{command[:80]}: {last}")


def git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout.strip()


def board_commit(host):
    out = retried(host, f"grep -h '^CODE_COMMIT:' {BOARD_REPO}/build/CMakeCache.txt", tries=2).decode()
    m = re.search(r"=(\S+)", out)
    if not m:
        raise Refused(f"no build on the board ({out.strip()}): run tools/deploy_board.sh")
    return m.group(1)


def send_chunk(host, inputs, k, names, local_dir):
    """Sends the chunk's PNGs as one tar; the chunk appears on the board only when whole. A retry after a
    send whose answer was lost finds the chunk in place, or already run and deleted by the tool."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name in names:
            tar.add(local_dir / name, arcname=name)
    part, whole = f"{inputs}/chunk_{k:04d}.part", f"{inputs}/chunk_{k:04d}"
    retried(host, f"[ -d {whole} ] || {{ rm -rf {part} && mkdir -p {part} && tar -x -C {part} && mv {part} {whole}; }}",
            data=buf.getvalue(), timeout=1800)


def chunks_on_board(host, inputs):
    out = retried(host, f"ls -d {inputs}/chunk_* 2>/dev/null | grep -v '\\.part$' | wc -l").decode()
    return int(out.strip())


def state(host, record, pid, boot_id):
    """The run's exit code, or None while it runs; Lost with the reason if the board reset or the run is
    gone without one."""
    out = retried(host, f"cat {BOARD_REPO}/{record}/exit_code 2>/dev/null; echo; "
                        f"[ -d /proc/{pid} ] && echo alive; echo boot $(cat /proc/sys/kernel/random/boot_id)").decode()
    first = out.splitlines()[0].strip() if out else ""
    if first.lstrip("-").isdigit():
        return int(first)
    if f"boot {boot_id}" not in out:
        raise Lost("the board reset during the run (its boot id changed)")
    if "alive" not in out:
        # It may have written exit_code just after the cat: one more look.
        again = retried(host, f"cat {BOARD_REPO}/{record}/exit_code 2>/dev/null").decode().strip()
        if again.lstrip("-").isdigit():
            return int(again)
        raise Lost("the run is gone without an exit code (killed)")
    return None


def fetch(host, board_dir, name, local_parent):
    """Copies the board's board_dir/name into local_parent/name."""
    data = retried(host, f"tar -C {board_dir} -cf - {name}", timeout=3600)
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
    ap.add_argument("--dump", type=int, default=RULES_IMAGES)
    ap.add_argument("--compare-dump", help="a record's dump directory, as the board has it (results/check/...)")
    ap.add_argument("--dets-in-record", action="store_true", help="a check's detections in its record")
    ap.add_argument("--chunk", type=int, default=500)
    ap.add_argument("--ahead", type=int, default=3)
    ap.add_argument("--timeout-min", type=int, default=240, help="how long to wait for the run")
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
        if a.chunk < 1 or a.ahead < 1 or a.dump < 0 or a.timeout_min < 1:
            raise Refused("--chunk, --ahead and --timeout-min are at least 1, --dump at least 0")
        if a.compare_dump and not re.fullmatch(r"results/check/[A-Za-z0-9_-]+/dump", a.compare_dump):
            raise Refused("--compare-dump is a check record's dump: results/check/<record>/dump")
        head = git("rev-parse", "HEAD")
        dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
        if a.kind == "m4":
            if a.dets_in_record or not a.compare_dump or a.dump < RULES_IMAGES:
                raise Refused("a pass of kind m4 writes its detections outside results/ (§2.5), and compares its "
                              f"first {RULES_IMAGES} images with §8 step 3's dump: --compare-dump, --dump >= 50")
            if dirty or subprocess.run(["git", "-C", str(REPO), "diff", "--quiet", a.freeze_m4_commit, "HEAD", "--",
                                        *CODE]).returncode != 0:
                raise Refused(f"a run of kind m4 runs freeze-m4's code ({a.freeze_m4_commit}) from a clean tree")
        built = board_commit(host)
        if a.kind == "m4" and built != head:
            raise Refused(f"a run of kind m4 needs the board built from the PC's HEAD {head}; it has {built}")
        if built != head:
            print(f"m4_board_pass: the board's build is {built}, the PC's HEAD {head} (recorded)", flush=True)
        ids = list(mc.read_manifest(REPO / MANIFEST))[:a.limit]
        local = Path(a.inputs)
        missing = [i for i in ids if not (local / f"{i:012d}.png").is_file()] if a.mode != "wiring" else []
        if missing:
            raise Refused(f"{len(missing)} PNGs are missing from {local}, the first {missing[0]:012d}.png")
    except (Refused, Lost, subprocess.CalledProcessError, OSError) as e:
        print(f"m4_board_pass: not starting: {e}", file=sys.stderr)
        return 2

    now = dt.datetime.now(dt.timezone.utc)
    name = f"{now:%Y%m%dT%H%M%SZ}_{a.name}"
    record = f"results/{a.kind}/{name}"
    inputs = f"/home/pi/m4/inputs/{name}"
    manifest = f"{inputs}/manifest.tsv"
    out = f"{record}/dets" if a.dets_in_record else f"/home/pi/m4/passes/{name}"
    args = [a.mode]
    if a.mode == "pass":
        args += ["--model", MODELS[a.model], "--manifest", manifest, "--inputs", inputs, "--out", out,
                 "--run-kind", a.kind, "--freeze-m4-commit", a.freeze_m4_commit, "--dump", str(a.dump),
                 "--chunk", str(a.chunk)]
        args += ["--limit", str(a.limit)] if a.limit else []
        args += ["--compare-dump", a.compare_dump] if a.compare_dump else []
    elif a.mode == "decode":
        args += ["--manifest", manifest, "--inputs", inputs, "--chunk", str(a.chunk)]
        args += ["--limit", str(a.limit)] if a.limit else []
    else:
        args += ["--model", MODELS[a.model], "--tensors", TENSORS]
    log = [f"time_utc: {now:%Y-%m-%dT%H:%M:%SZ}", f"script: tools/m4_board_pass.py {shlex.join(argv)}",
           f"pc_head: {head}{' (tree differs)' if dirty else ''}", f"board_build: {built}"]
    password = os.environ.get("BOARD_SUDO_PASSWORD")
    sudo = "sudo -S -p ''" if password else "sudo -n"
    inner = f"setsid nohup sh tools/m4_board_run.sh {record} {shlex.join(args)} > /dev/null 2>&1 < /dev/null &"
    try:
        if a.mode != "wiring":
            retried(host, f"mkdir -p {inputs} && cat > {manifest}", data=(REPO / MANIFEST).read_bytes())
        # Not retried: a second start would be a second run.
        code, _, err = board(host, f"cd {BOARD_REPO} && {sudo} sh -c {shlex.quote(inner)}",
                             data=(password + "\n").encode() if password else None, timeout=120)
        if code:
            raise Refused(err.strip())
    except (Refused, Lost, subprocess.TimeoutExpired, OSError) as e:
        print(f"m4_board_pass: not starting: {e}", file=sys.stderr)
        retried(host, f"rm -rf {inputs}", tries=2) if a.mode != "wiring" else None
        return 2
    print(f"m4_board_pass: {record} started on the board (its build {built})", flush=True)

    started = time.time()
    code, outcome = None, "running"
    try:
        run_txt = ""
        for _ in range(30):  # run.txt is m4_board_run.sh's first act
            run_txt = retried(host, f"cat {BOARD_REPO}/{record}/run.txt 2>/dev/null; true").decode()
            if "boot_id: " in run_txt:
                break
            time.sleep(2)
        fields = dict(l.split(": ", 1) for l in run_txt.splitlines() if ": " in l)
        if "pid" not in fields or "boot_id" not in fields:
            raise Lost("the run did not start (no run.txt on the board)")
        pid, boot_id = fields["pid"].strip(), fields["boot_id"].strip()
        deadline = started + 60 * a.timeout_min
        if a.mode != "wiring":
            groups = [ids[i:i + a.chunk] for i in range(0, len(ids), a.chunk)]
            for k, group in enumerate(groups):
                while chunks_on_board(host, inputs) >= a.ahead:
                    if (code := state(host, record, pid, boot_id)) is not None or time.time() > deadline:
                        break
                    time.sleep(2)
                if code is not None or (code := state(host, record, pid, boot_id)) is not None:
                    break  # the tool stopped early (an invalid run): its record says why
                send_chunk(host, inputs, k, [f"{i:012d}.png" for i in group], local)
                print(f"m4_board_pass: chunk {k + 1} of {len(groups)} sent, {time.time() - started:.0f} s", flush=True)
        while code is None:
            if time.time() > deadline:
                raise Lost(f"no exit code after {a.timeout_min} minutes; the run may still be going on the board")
            time.sleep(5)
            code = state(host, record, pid, boot_id)
        outcome = f"the board tool exited {code}"
    except Lost as e:
        code, outcome = 1, f"ended without the tool's exit code: {e}"
    finally:
        print(f"m4_board_pass: {outcome} after {time.time() - started:.0f} s", flush=True)
        log += [f"outcome: {outcome}", f"seconds: {time.time() - started:.0f}"]
        local_record = REPO / record
        for what, step in (("the record", lambda: fetch(host, f"{BOARD_REPO}/results/{a.kind}", name, REPO / "results" / a.kind)),
                           ("the detections", lambda: fetch(host, "/home/pi/m4/passes", name, Path(a.passes))
                            if a.mode == "pass" and not a.dets_in_record else None),
                           ("the inputs' removal", lambda: retried(host, f"rm -rf {inputs}") if a.mode != "wiring" else None)):
            try:
                step()
            except (Lost, OSError, tarfile.TarError) as e:
                log.append(f"failed: {what}: {e}")
                print(f"m4_board_pass: {what} failed: {e}", file=sys.stderr)
        local_record.mkdir(parents=True, exist_ok=True)
        (local_record / "board_pass.txt").write_text("\n".join(log) + "\n", encoding="utf-8", newline="\n")
    summary = REPO / record / {"pass": "pass.csv", "decode": "decode.csv", "wiring": "wiring.csv"}[a.mode]
    if summary.exists():
        print(summary.read_text(encoding="utf-8"), end="")
    print(f"m4_board_pass: {record}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
