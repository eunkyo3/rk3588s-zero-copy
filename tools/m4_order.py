#!/usr/bin/env python3
"""M4's order of runs and its wiring check, as their records show them (prereg/M4.md §8 steps 3 and 5).

  - Order (§8 step 5): the four accuracy passes (§2.5: each model in INT8 on the board and in FP32 on
    the PC), then the calib_npu session (§3.1), then the pipeline sessions (§3.2, §3.3).
  - A pass is done when it is valid, or once it has been invalid twice: it is then reported as missing
    (§5). An invalid pass is repeated once.
  - Wiring (§8 step 3): a check of the build's post-processing objects for both settings, C8 and C10.
    §5 repeats it with a build whose objects differ.

The records:
  - A board pass: results/m4/<UTC>_<name>/ from tools/m4_board_pass.py. run.txt (tools/m4_board_run.sh)
    gives the command and the start. Once the tool ends, exit_code and pass.csv (tools/m4_accuracy.cpp)
    follow, and on the PC board_pass.txt.
  - An FP32 pass: results/m4/<UTC>_<name>/ from tools/eval/pc_run.py. pc.json names tools/m4_fp32.py,
    exit.txt follows when the tool ends, and pass.csv too. tools/m4_send_record.py copies the record
    to the board, whose sessions check the order.
  - A wiring check: results/check/<UTC>_<name>/wiring.csv (tools/m4_accuracy.cpp wiring).
A pass that did not start (exit code 2 without pass.csv: refused, or a failed setup; on the PC also
docker's 125 to 127) is no attempt. One that ended without pass.csv any other way (a board reset, a
killed run) is an invalid attempt.

Used by tools/stream_session.py and, through the gate below, tools/m4_calib.sh on the board, and by
tools/check_prereg_order.py.

  python3 tools/m4_order.py gate <freeze-m4's commit>
The gate of M4's calib_npu session (tools/m4_calib.sh, run kind m4): freeze-m4's commit from
docs/notes/env.md, the four passes done, and the wiring check of this build's objects
(tools/stream_session.py m4_gates_problem, which the pipeline sessions pass too). Exit 0 when the
session may start, 1 with the reason when not, 2 when the records cannot be read.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

PASSES = (("yolov8n", "int8"), ("yolov10n", "int8"), ("yolov8n", "fp32"), ("yolov10n", "fp32"))  # §2.5
BOARD_MODELS = {"models/yolov8n_i8.rknn": "yolov8n", "models/yolov10n_i8.rknn": "yolov10n"}  # m4_board_pass.py
FP32_TOOL = "tools/m4_fp32.py"
NOT_STARTED = {"board": {"2"}, "pc": {"2", "125", "126", "127"}}  # exit codes without pass.csv
RECORD = re.compile(r"\d{8}T\d{6}Z_[A-Za-z0-9_-]+")
SETTINGS = {"yolov8n": "C8", "yolov10n": "C10"}  # §3.2: the arm of each model
WIRING_FRAMES = 10  # §8 step 3
TENSORS_SHA256 = "051d6f12662e0b8ef9b72f00f74f498683913c39f197deb42c34ac1a54340c67"  # M1-3's tensor set (A7)


@dataclass(frozen=True)
class Attempt:
    record: Path
    model: str
    precision: str  # int8 (board) or fp32 (PC)
    start: datetime
    end: datetime | None  # None when no record gives it
    outcome: str  # valid, invalid or running


def parse_utc(s):
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def read(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def keyed(path):
    """{key: value} of a file of "key: value" lines; {} if it cannot be read."""
    out = {}
    for line in (read(path) or "").splitlines():
        k, sep, v = line.partition(": ")
        if sep:
            out[k] = v.strip()
    return out


def header_and_lines(path):
    """(the "# key: value" lines at the top of a results CSV, the lines after them); None if unreadable."""
    text = read(path)
    if text is None:
        return None
    header, lines = {}, text.splitlines()
    while lines and lines[0].startswith("# "):
        k, _, v = lines.pop(0)[2:].partition(": ")
        header[k] = v
    return header, lines


def key_values(path):
    """(header, {key: value}) of a key,value results CSV, as pass.csv; None if unreadable."""
    hl = header_and_lines(path)
    if hl is None:
        return None
    header, lines = hl
    rows = dict(line.split(",", 1) for line in lines[1:] if "," in line)
    return header, rows


def arg(words, flag):
    """The value after flag in a command's words, or None."""
    return words[words.index(flag) + 1] if flag in words[:-1] else None


def board_alive(run):
    """Whether the run of run.txt's fields still runs: the same boot, and its PID is
    tools/m4_board_run.sh. False where /proc cannot tell (on the PC)."""
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        cmdline = Path("/proc", run.get("pid", "none"), "cmdline").read_bytes()
    except OSError:
        return False
    return boot == run.get("boot_id") and b"m4_board_run.sh" in cmdline


def attempt(d, model, precision, start, freeze_m4, exit_code, end, running, side):
    """The attempt of a pass record whose run started: from its pass.csv if it has one."""
    kv = key_values(d / "pass.csv")
    if kv is not None:
        header, rows = kv
        if (header.get("run_kind"), header.get("freeze_m4_commit"), header.get("model"), header.get("precision")) \
                != ("m4", freeze_m4, model, precision):
            return None
        t0 = parse_utc(header.get("start_time_utc", ""))
        try:
            end = t0 + timedelta(seconds=float(rows["seconds"])) if t0 else end
        except (KeyError, ValueError):
            pass
        return Attempt(d, model, precision, start, end, "valid" if rows.get("valid") == "1" else "invalid")
    if exit_code in NOT_STARTED[side]:
        return None
    if exit_code is None and running:
        return Attempt(d, model, precision, start, None, "running")
    return Attempt(d, model, precision, start, end, "invalid")


def board_attempt(d, freeze_m4, alive):
    run = keyed(d / "run.txt")
    words = run.get("command", "").split()
    if words[1:2] != ["pass"] or arg(words, "--run-kind") != "m4" or arg(words, "--freeze-m4-commit") != freeze_m4:
        return None
    model, start = BOARD_MODELS.get(arg(words, "--model") or ""), parse_utc(run.get("time_utc", ""))
    if model is None or start is None:
        return None
    code = (read(d / "exit_code") or "").strip() or None
    log = keyed(d / "board_pass.txt")  # the PC's copy: when m4_board_pass.py stopped waiting
    t, seconds = parse_utc(log.get("time_utc", "")), log.get("seconds", "")
    end = t + timedelta(seconds=float(seconds)) if t and seconds.isdigit() else None
    return attempt(d, model, "int8", start, freeze_m4, code, end, alive is not None and alive(run), "board")


def pc_attempt(d, freeze_m4):
    try:
        pc = json.loads(read(d / "pc.json") or "")
    except ValueError:
        return None
    if not isinstance(pc, dict):
        return None
    tool = pc.get("tool") or []
    if tool[:1] != [FP32_TOOL] or pc.get("run_kind") != "m4" or pc.get("freeze_m4_commit") != freeze_m4:
        return None
    model, start = arg(tool, "--model"), parse_utc(pc.get("start_time_utc", ""))
    if model not in SETTINGS or start is None:
        return None
    ended = keyed(d / "exit.txt")
    return attempt(d, model, "fp32", start, freeze_m4, ended.get("exit_code"),
                   parse_utc(ended.get("end_time_utc", "")), False, "pc")


def pass_attempts(results_m4, freeze_m4, alive=None):
    """The attempts at M4's accuracy passes in results_m4 with freeze-m4's commit, in UTC order. alive
    tells a board run that still runs (board_alive, on the board); without it, none does."""
    d0 = Path(results_m4)
    out = []
    for d in sorted(d0.iterdir()) if d0.is_dir() else []:
        if d.is_dir() and RECORD.fullmatch(d.name):
            a = board_attempt(d, freeze_m4, alive) if (d / "run.txt").exists() else \
                pc_attempt(d, freeze_m4) if (d / "pc.json").exists() else None
            if a is not None:
                out.append(a)
    return out


def passes_status(attempts, before=None):
    """(when the four passes were done, or None; what keeps them from being done). With before, only the
    attempts that started before it count: the passes as they stood then."""
    done, problems = [], []
    for model, precision in PASSES:
        own = sorted((a for a in attempts if (a.model, a.precision) == (model, precision)
                      and (before is None or a.start < before)), key=lambda a: a.start)
        name = f"the {precision.upper()} pass of {model}"
        valid = [a for a in own if a.outcome == "valid"]
        running = [a for a in own if a.outcome == "running"]
        if running:
            problems.append(f"{name} is running ({running[0].record.name})")
        elif valid:
            done.append(valid[0].end or valid[0].start)
        elif len(own) >= 2:  # invalid twice: reported as missing (§5)
            done.append(own[1].end or own[1].start)
        elif own:
            problems.append(f"{name} was invalid ({own[0].record.name}) and is repeated once (§5)")
        else:
            problems.append(f"{name} has no record")
    return (max(done) if not problems else None), problems


def wiring_rows(path):
    """(header, [the equal column of each whole row]) of a wiring.csv; None if unreadable. A row cut short
    by a crash is left out."""
    hl = header_and_lines(path)
    if hl is None or not hl[1]:
        return None
    header, lines = hl
    equal = []
    for row in csv.reader(lines[1:]):
        if len(row) == 4 and row[3] in ("0", "1"):
            equal.append(row[3])
    return header, equal


def wiring_problems(results_check, objects, models):
    """What keeps a passed wiring check (§8 step 3) from covering these post-processing objects, for both
    settings: [] when one covers them. objects: {path: SHA-256} of the build's, as the check records them
    (post_objects_sha256); models: {name: SHA-256} of §1's models.
      - Passed: a check of the model with M1-3's tensors and these objects, whose frames (at least 10)
        were all equal.
      - A check of these objects in which a frame differed stops them for good: the outputs and the code
        are deterministic, so a later pass would not undo it. The cause is found and fixed (§5)."""
    root, problems = Path(results_check), []
    for model, setting in SETTINGS.items():
        passed = failed = None
        for d in sorted(root.iterdir()) if root.is_dir() else []:
            got = wiring_rows(d / "wiring.csv") if RECORD.fullmatch(d.name) else None
            if got is None:
                continue
            header, equal = got
            try:
                recorded = json.loads(header.get("post_objects_sha256", ""))
            except ValueError:
                continue
            if (header.get("model"), header.get("model_sha256"), header.get("tensors_sha256")) \
                    != (model, models[model], TENSORS_SHA256) or not isinstance(recorded, dict) \
                    or any(recorded.get(p) != sha for p, sha in objects.items()):
                continue
            if "0" in equal:
                failed = failed or d.name
            elif len(equal) >= WIRING_FRAMES:
                passed = passed or d.name
        if failed:
            problems.append(f"the wiring check {failed} of {setting} found frames that differ with these objects: "
                            "the cause is found and fixed (M4 §5)")
        elif not passed:
            problems.append(f"no passed wiring check of {setting} ({model}) with this build's post-processing "
                            "objects is in results/check/ (M4 §8 step 3, §5)")
    return problems


def main(argv):
    if len(argv) != 2 or argv[0] != "gate":
        print("usage: python3 tools/m4_order.py gate <freeze-m4's commit>", file=sys.stderr)
        return 2
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import stream_session as ss

    try:
        freeze, _ = ss.freeze_m4_registration()
        if freeze == "none":
            problem = f"freeze-m4 has no row in {ss.ENV_MD} yet (M4 §4)"
        elif argv[1] != freeze:
            problem = f"{argv[1]} is not freeze-m4's commit, {freeze} in {ss.ENV_MD}"
        else:
            problem = ss.m4_gates_problem(freeze)
    except ss.Stop as e:
        print(f"m4_order: {e}", file=sys.stderr)
        return 2
    if problem:
        print(f"m4_order: not starting: {problem}", file=sys.stderr)
        return 1
    print("m4_order: the four accuracy passes are done, and a wiring check covers this build's objects")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
