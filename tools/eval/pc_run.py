#!/usr/bin/env python3
"""Runs one of M4's tools on the PC in the evaluation image, after the checks of prereg/M4.md §4.

Before the container starts, on the host (Windows):
  - the code: the repository's HEAD commit and whether a tracked file differs from it. A tree that
    differs is refused unless --allow-dirty (development only; recorded either way);
  - the evaluation image's ID (tools/eval/build.sh builds and checks the image);
  - the CPU model (Win32_Processor's Name);
  - the host clock's offset from NTP: `w32tm /stripchart /computer:time.windows.com /samples:3 /dataonly`.
    The samples that answered count, and the others are recorded. The run does not start with no answer,
    or with an offset above 1 s;
  - the clock's sync state, `w32tm /query /status`, recorded as it prints, its error too (the Windows
    Time service may not run);
  - the container clock's offset from the host's: 5 exchanges over one `docker run -i` of the image, and
    the one with the shortest round trip. Above 1 s, the run does not start.
Then the start time from the host clock goes with the rest to <record>/pc.json, and the tool runs with
`python3 -I`, since its inputs are downloaded data:
  - the repository read-only at /repo (the working directory), the record directory at /record, and
    --data (default D:/m4data) at /data;
  - memory limited to 2 GB (4 GB with swap), as for the conversion: other projects' containers share the
    Docker VM.
Its output goes to the console and to <record>/<tool>.log.

Usage, on the PC from the repository (from Git Bash, with MSYS_NO_PATHCONV=1, which it checks: Git Bash
would turn the container's /data/... into Windows paths):
  python tools/eval/pc_run.py --kind check --name m4_inputs [--data D:/m4data] -- tools/m4_prep.py ARGS...
The record directory is results/<kind>/<UTC>_<name>. Exit: the tool's code, or 2 when a check stops the
run before the tool starts.
"""
import argparse
import datetime as dt
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
IMAGE = "rk3588s-zero-copy/m4-eval:1"
NTP_SERVER = "time.windows.com"
MAX_OFFSET_S = 1.0  # M4 §4, for the host's offset from NTP and the container's from the host
MEMORY = ["--memory=2g", "--memory-swap=4g"]
KINDS = ("check", "m4")
SAMPLE = re.compile(r"^\s*\d{1,2}:\d{2}:\d{2},\s*(.*?)\s*$")  # w32tm /stripchart /dataonly: "hh:mm:ss, <value>"
OFFSET = re.compile(r"([+-]\d+\.\d+)s")
CLOCK = "import sys, time\nfor line in sys.stdin:\n    print(repr(time.time()), flush=True)\n"


class Refused(Exception):
    """A check of §4 stops the run before the tool starts."""


def decode(data):
    """Console output of a Windows tool, in the console's code page (w32tm prints localized text)."""
    return data.decode("oem" if sys.platform == "win32" else "utf-8", errors="replace")


def parse_stripchart(text):
    """(offsets in s of the samples that answered, how many did not) from w32tm /stripchart /dataonly."""
    offsets, failed = [], 0
    for line in text.splitlines():
        m = SAMPLE.fullmatch(line)
        if m:
            o = OFFSET.fullmatch(m.group(1))
            if o:
                offsets.append(float(o.group(1)))
            else:
                failed += 1
    return offsets, failed


def ntp_problem(offsets):
    """Why the host clock's samples stop the run (§4), or None."""
    if not offsets:
        return f"no sample of {NTP_SERVER} answered: the host clock's offset is unknown"
    worst = max(offsets, key=abs)
    if abs(worst) > MAX_OFFSET_S:
        return f"the host clock is {worst:+.3f} s off {NTP_SERVER} (at most {MAX_OFFSET_S} s, M4 §4)"
    return None


def best_exchange(exchanges):
    """(offset, round trip) of the exchange with the shortest round trip; each is (host time sent, remote
    time, host time answered), and the offset is remote minus host at the round trip's midpoint."""
    t0, remote, t1 = min(exchanges, key=lambda e: e[2] - e[0])
    return remote - (t0 + t1) / 2, t1 - t0


def one_line(text):
    return " | ".join(line.strip() for line in text.splitlines() if line.strip())


def run(cmd, timeout=120):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Refused(f"{cmd[0]}: {e}") from None
    return r.returncode, decode(r.stdout) + decode(r.stderr)


def container_clock(image):
    """(offset, round trip) of the container clock against the host's (best_exchange)."""
    p = subprocess.Popen(["docker", "run", "-i", "--rm", *MEMORY, image, "python3", "-u", "-c", CLOCK],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    answers = queue.Queue()
    threading.Thread(target=lambda: [answers.put(line) for line in p.stdout], daemon=True).start()
    exchanges = []
    try:
        for k in range(6):
            t0 = time.time()
            p.stdin.write("x\n")
            p.stdin.flush()
            line = answers.get(timeout=120 if k == 0 else 10)  # the first waits for the container to start
            t1 = time.time()
            if k:
                exchanges.append((t0, float(line), t1))
    except (queue.Empty, ValueError, OSError) as e:
        raise Refused(f"the container's clock did not answer: {e!r}") from None
    finally:
        p.kill()
        p.wait()
    return best_exchange(exchanges)


def checks(image, allow_dirty):
    """§4's checks and records, as pc.json's fields without its start time."""
    code, commit = run(["git", "-C", str(REPO), "rev-parse", "HEAD"])
    code2, status = run(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no"])
    if code or code2:
        raise Refused("git failed: " + one_line(commit + status))
    if status.strip() and not allow_dirty:
        raise Refused("tracked files differ from HEAD: " + one_line(status))
    code, image_id = run(["docker", "image", "inspect", "--format", "{{.Id}}", image])
    if code:
        raise Refused(f"no {image}: run tools/eval/build.sh")
    _, cpu = run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"])
    _, chart = run(["w32tm", "/stripchart", f"/computer:{NTP_SERVER}", "/samples:3", "/dataonly"])
    offsets, failed = parse_stripchart(chart)
    status_code, w32tm_status = run(["w32tm", "/query", "/status"])
    problem = ntp_problem(offsets)
    if problem:
        raise Refused(problem)
    container_offset, rtt = container_clock(image)
    if abs(container_offset) > MAX_OFFSET_S:
        raise Refused(f"the container's clock is {container_offset:+.3f} s off the host's (at most {MAX_OFFSET_S} s, "
                      "M4 §4)")
    return {
        "code_commit": commit.strip(), "code_status": "clean" if not status.strip() else "differs: " + one_line(status),
        "eval_image": image, "eval_image_id": image_id.strip(),
        "host": {"cpu_model": one_line(cpu) or "unknown", "ntp_server": NTP_SERVER,
                 "ntp_offset_s": f"{max(offsets, key=abs):+.4f}",
                 "ntp_samples": f"{len(offsets)} answered, {failed} did not",
                 "ntp_stripchart": one_line(chart), "w32tm_status": one_line(w32tm_status) or "(no output)",
                 "w32tm_status_exit": status_code},
        "container_clock_offset_s": f"{container_offset:+.4f}", "container_clock_rtt_s": f"{rtt:.4f}",
    }


def main(argv):
    if "--" not in argv:
        print("pc_run: usage: pc_run.py --kind KIND --name NAME [--data DIR] -- TOOL [ARGS...]", file=sys.stderr)
        return 2
    split = argv.index("--")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kind", required=True, choices=KINDS)
    ap.add_argument("--name", required=True, help="the record's name: results/<kind>/<UTC>_<name>")
    ap.add_argument("--data", default="D:/m4data")
    ap.add_argument("--image", default=IMAGE)
    ap.add_argument("--allow-dirty", action="store_true", help="development only: run from a tree that differs")
    a = ap.parse_args(argv[:split])
    tool = argv[split + 1:]
    if os.environ.get("MSYSTEM") and os.environ.get("MSYS_NO_PATHCONV") != "1":
        print("pc_run: from Git Bash, run with MSYS_NO_PATHCONV=1: it would turn /data/... into a Windows path",
              file=sys.stderr)
        return 2
    if not tool or not re.fullmatch(r"[\w-]+", a.name) or not (REPO / tool[0]).is_file():
        print("pc_run: name a tool of the repository, e.g. tools/m4_prep.py, and a name of word characters",
              file=sys.stderr)
        return 2
    try:
        pc = checks(a.image, a.allow_dirty)
    except Refused as e:
        print(f"pc_run: not starting: {e}", file=sys.stderr)
        return 2
    now = dt.datetime.now(dt.timezone.utc)
    record = REPO / "results" / a.kind / f"{now:%Y%m%dT%H%M%SZ}_{a.name}"
    record.mkdir(parents=True)
    pc = {"start_time_utc": f"{now:%Y-%m-%dT%H:%M:%SZ}", **pc, "data": a.data, "tool": tool}
    (record / "pc.json").write_text(json.dumps(pc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                                    newline="\n")
    print(f"pc_run: {record.relative_to(REPO)}, host clock {pc['host']['ntp_offset_s']} s off NTP "
          f"({pc['host']['ntp_samples']}), container {pc['container_clock_offset_s']} s", flush=True)
    cmd = ["docker", "run", "--rm", *MEMORY,
           "--mount", f"type=bind,source={REPO},target=/repo,readonly",
           "--mount", f"type=bind,source={record},target=/record",
           "--mount", f"type=bind,source={Path(a.data).resolve()},target=/data",
           "-w", "/repo", a.image, "python3", "-I", f"/repo/{tool[0]}", *tool[1:]]
    with open(record / f"{Path(tool[0]).stem}.log", "w", encoding="utf-8", newline="") as log:  # LF, as the tool's
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for raw in p.stdout:
            line = raw.decode("utf-8", errors="replace")
            sys.stdout.write(line)
            log.write(line)
        return p.wait()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
