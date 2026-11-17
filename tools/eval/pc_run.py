#!/usr/bin/env python3
"""Runs one of M4's tools on the PC in the evaluation image, after the checks of prereg/M4.md §4.

Before the container starts, on the host (Windows):
  - the code: the repository's HEAD commit. The tool must be tracked by git; a tracked file that differs
    from HEAD, or an untracked file under tools/ or tests/ (which the tool could import), refuses the run.
    --allow-dirty lets a check run from such a tree (development only; recorded), never a run of kind m4;
  - the evaluation image: its ID must be tools/eval/image_id.txt's, and the container runs by that ID;
  - the CPU model (Win32_Processor's Name);
  - the host clock's offset from NTP: `w32tm /stripchart /computer:time.windows.com /samples:3 /dataonly`.
    The samples that answered count, and the others are recorded. The run does not start with no answer,
    or with an offset above 1 s;
  - the clock's sync state, `w32tm /query /status`, recorded as it prints, its error too (the Windows
    Time service may not run);
  - the container clock's offset from the host's: 5 exchanges over one container, and the one with the
    shortest round trip. Above 1 s, the run does not start.
Then the start time from the host clock goes to <record>/pc.json with the rest, the run kind and M4's
stage commits (PROTOCOL §1.4, M4 §4), and the tool runs with `python3 -I`, since its inputs are downloaded
data:
  - the repository read-only at /repo (the working directory), the record directory at /record, and
    --data (default D:/m4data) at /data;
  - no network, no new privileges, and memory limited to 2 GB (4 GB with swap), as for the conversion:
    other projects' containers share the Docker VM. COCOeval of a pass (tools/m4_coco.py, about 1.5
    million detections) needs about 4.9 GB: that tool gets 6 GB without swap, which fails at once rather
    than thrash the disk, and it does not start while another container runs or in a Docker VM with less
    than 1 GB more than that.
Its container is named m4-<record name> (pc.json's container). Its output goes to the console and to
<record>/<tool>.log, and its exit code and end time to <record>/exit.txt (exit_code none when this script
was stopped first; when it could not write it at all, tools/m4_send_record.py --ended does, once no
container of the record runs). With --checks-only, no tool runs:
pc.json records the checks alone (the PC's clock, say).

Usage, on the PC from the repository (from Git Bash, with MSYS_NO_PATHCONV=1, which it checks: Git Bash
would turn the container's /data/... into Windows paths):
  python tools/eval/pc_run.py --kind check --name m4_inputs [--data D:/m4data] -- tools/m4_prep.py ARGS...
  python tools/eval/pc_run.py --kind check --name pc_clock --checks-only
The record directory is results/<kind>/<UTC>_<name>. Exit: the tool's code (0 with --checks-only), or 2
when a check stops the run before it starts.
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
sys.path.append(str(REPO / "tools"))
import stream_session as ss  # noqa: E402  (M4's commits and env.md's Registration table)

IMAGE = "rk3588s-zero-copy/m4-eval:1"
IMAGE_ID_FILE = REPO / "tools/eval/image_id.txt"  # the evaluation image's ID, which runs must use (§8)
NTP_SERVER = "time.windows.com"
MAX_OFFSET_S = 1.0  # M4 §4, for the host's offset from NTP and the container's from the host
CONTAINER = ["--network", "none", "--security-opt", "no-new-privileges"]
MEMORY = ["--memory=2g", "--memory-swap=4g"]
LARGE_MEMORY_GB = {"tools/m4_coco.py": 6}  # without swap
KINDS = ("check", "m4")
SAMPLE = re.compile(r"^\s*[0-9]{1,2}:[0-9]{2}:[0-9]{2},\s*(.*?)\s*$")  # w32tm /dataonly: "hh:mm:ss, <value>"
OFFSET = re.compile(r"([+-][0-9]+\.[0-9]+)s")
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


def large_memory_gb(tool):
    """The tool's large memory limit in GB, or None: by its path in the repository, however it is written."""
    if not tool:
        return None
    try:
        key = (REPO / tool).resolve().relative_to(REPO.resolve()).as_posix()
    except (OSError, ValueError):
        return None
    return LARGE_MEMORY_GB.get(key)


def memory(tool):
    """The tool's container memory: (docker run's flags, the record's text)."""
    gb = large_memory_gb(tool)
    if gb is None:
        return MEMORY, "2g, 4g with swap"
    return [f"--memory={gb}g", f"--memory-swap={gb}g"], f"{gb}g, no swap"


def memory_problem(tool):
    """Why a tool with a large memory limit cannot start now, or None: another container runs, or the
    Docker VM has less than 1 GB more than the limit."""
    gb = large_memory_gb(tool)
    if gb is None:
        return None
    code, running = run(["docker", "ps", "--format", "{{.Names}}"])
    if code:
        return "docker ps failed: " + one_line(running)
    if running.strip():
        return f"{tool} takes {gb} GB of the Docker VM, and other containers run: {one_line(running)}"
    code, total = run(["docker", "info", "--format", "{{.MemTotal}}"])
    if code or not total.strip().isdigit():
        return "docker info failed: " + one_line(total)
    if int(total) < (gb + 1) * 2**30:
        return f"{tool} takes {gb} GB, and the Docker VM has {int(total) / 2**30:.1f} GB (at least {gb + 1} GB)"
    return None


def container_clock(image_id):
    """(offset, round trip) of the container clock against the host's (best_exchange)."""
    p = subprocess.Popen(["docker", "run", "-i", "--rm", *CONTAINER, *MEMORY, image_id, "python3", "-u", "-c",
                          CLOCK],
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
        try:  # end of input ends the loop, and the container with it
            p.stdin.close()
            p.wait(timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            p.kill()
            p.wait()
    return best_exchange(exchanges)


def stage_fields(kind):
    """The run kind, M4's stage commits from docs/notes/env.md's Registration table, and the amendment in
    force, for pc.json's header (PROTOCOL §1.4, M4 §4)."""
    env = (REPO / ss.ENV_MD).read_text(encoding="utf-8")
    amendments = re.findall(r"^## (A[0-9]+) ", (REPO / "prereg/AMENDMENTS.md").read_text(encoding="utf-8"), re.M)
    return {"run_kind": kind, "prereg_v1_commit": ss.PREREG_V1_COMMIT, "freeze_m1_commit": ss.FREEZE_M1_COMMIT,
            "prereg_m4_commit": ss.PREREG_M4_COMMIT,
            "freeze_m4_commit": ss.registration_rows(env).get("freeze-m4", ("none", None))[0],
            "amendment": amendments[-1] if amendments else "none"}


def code_state(tool, allow_dirty):
    """(HEAD, its status): refuses an untracked tool, a tracked file that differs, or an untracked file
    under tools/ or tests/, unless allow_dirty."""
    code, commit = run(["git", "-C", str(REPO), "rev-parse", "HEAD"])
    code2, status = run(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no"])
    code3, untracked = run(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=all", "--",
                            "tools", "tests"])
    if code or code2 or code3:
        raise Refused("git failed: " + one_line(commit + status + untracked))
    if tool is not None:
        code, out = run(["git", "-C", str(REPO), "ls-files", "--error-unmatch", "--", tool])
        if code:
            raise Refused(f"{tool} is not tracked by git: commit it first")
    code, flags = run(["git", "-C", str(REPO), "ls-files", "-v"])  # files git status does not show
    hidden = [line for line in flags.splitlines() if line[:1].islower() or line[:1] == "S"]
    if code or hidden:
        raise Refused("tracked files marked assume-unchanged or skip-worktree: " + one_line("\n".join(hidden[:5])))
    untracked = "\n".join(line for line in untracked.splitlines() if line.startswith("??"))
    differs = one_line(status + "\n" + untracked)
    if differs and not allow_dirty:
        raise Refused("the tree differs from HEAD: " + differs)
    return commit.strip(), "clean" if not differs else "differs: " + differs


def checks(image, tool, allow_dirty):
    """§4's checks and records, as pc.json's fields without its start time and stage fields."""
    commit, status = code_state(tool, allow_dirty)
    expected = IMAGE_ID_FILE.read_text(encoding="utf-8").strip()
    code, image_id = run(["docker", "image", "inspect", "--format", "{{.Id}}", expected])  # by ID, not the tag
    if code or image_id.strip() != expected:
        raise Refused(f"no image {expected} ({IMAGE_ID_FILE.name}): docker load -i D:/m4data/images/m4-eval_<ID>.tar "
                      "(tools/eval/save_image.sh)")
    problem = memory_problem(tool)
    if problem:
        raise Refused(problem)
    _, cpu = run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"])
    _, chart = run(["w32tm", "/stripchart", f"/computer:{NTP_SERVER}", "/samples:3", "/dataonly"])
    offsets, failed = parse_stripchart(chart)
    status_code, w32tm_status = run(["w32tm", "/query", "/status"])
    problem = ntp_problem(offsets)
    if problem:
        raise Refused(problem)
    container_offset, rtt = container_clock(expected)
    if abs(container_offset) > MAX_OFFSET_S:
        raise Refused(f"the container's clock is {container_offset:+.3f} s off the host's (at most {MAX_OFFSET_S} s, "
                      "M4 §4)")
    return {
        "code_commit": commit, "code_status": status, "eval_image": image, "eval_image_id": expected,
        "host": {"cpu_model": one_line(cpu) or "unknown", "ntp_server": NTP_SERVER,
                 "ntp_offset_s": f"{max(offsets, key=abs):+.4f}",
                 "ntp_samples": f"{len(offsets)} answered, {failed} did not",
                 "ntp_stripchart": one_line(chart), "w32tm_status": one_line(w32tm_status) or "(no output)",
                 "w32tm_status_exit": status_code},
        "container_clock_offset_s": f"{container_offset:+.4f}", "container_clock_rtt_s": f"{rtt:.4f}",
        "container_memory": memory(tool)[1],
    }


def container_name(record_name):
    """The tool's container, named after its record: tools/m4_send_record.py --ended looks for it."""
    return f"m4-{record_name}"


def docker_command(image_id, record, data, tool):
    return ["docker", "run", "--rm", "--name", container_name(record.name), *CONTAINER, *memory(tool[0])[0],
            "--mount", f"type=bind,source={REPO},target=/repo,readonly",
            "--mount", f"type=bind,source={record},target=/record",
            "--mount", f"type=bind,source={data},target=/data",
            "-w", "/repo", image_id, "python3", "-I", f"/repo/{tool[0]}", *tool[1:]]


def main(argv):
    split = argv.index("--") if "--" in argv else len(argv)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kind", required=True, choices=KINDS)
    ap.add_argument("--name", required=True, help="the record's name: results/<kind>/<UTC>_<name>")
    ap.add_argument("--data", default="D:/m4data")
    ap.add_argument("--image", default=IMAGE)
    ap.add_argument("--checks-only", action="store_true", help="record the checks; run no tool")
    ap.add_argument("--allow-dirty", action="store_true", help="checks only: run from a tree that differs")
    a = ap.parse_args(argv[:split])
    tool = argv[split + 1:]
    problem = None
    if os.environ.get("MSYSTEM") and os.environ.get("MSYS_NO_PATHCONV") != "1":
        problem = "from Git Bash, run with MSYS_NO_PATHCONV=1: it would turn /data/... into a Windows path"
    elif not re.fullmatch(r"[A-Za-z0-9_-]+", a.name):
        problem = "the name is letters, digits, _ and -"
    elif a.checks_only != (not tool):
        problem = "name a tool of the repository after --, e.g. -- tools/m4_prep.py, or give --checks-only alone"
    elif tool and not (REPO / tool[0]).is_file():
        problem = f"no {tool[0]} in the repository"
    elif a.allow_dirty and a.kind == "m4":
        problem = "--allow-dirty is for development checks, never a run of kind m4"
    elif tool and sys.platform == "win32" and a.data.startswith("/"):
        problem = f"--data {a.data} is a Git Bash path; give D:/... instead"
    elif tool and not Path(a.data).is_dir():
        problem = f"--data {a.data} is not a directory"
    if problem:
        print(f"pc_run: {problem}", file=sys.stderr)
        return 2
    try:
        stage = stage_fields(a.kind)
        if a.kind == "m4" and stage["freeze_m4_commit"] == "none":
            raise Refused("run kind m4 comes after freeze-m4, which has no row in docs/notes/env.md yet (M4 §4)")
        pc = checks(a.image, tool[0] if tool else None, a.allow_dirty)
    except (Refused, OSError, ss.Stop) as e:
        print(f"pc_run: not starting: {e}", file=sys.stderr)
        return 2
    now = dt.datetime.now(dt.timezone.utc)
    record = REPO / "results" / a.kind / f"{now:%Y%m%dT%H%M%SZ}_{a.name}"
    record.mkdir(parents=True)
    pc = {"start_time_utc": f"{now:%Y-%m-%dT%H:%M:%SZ}", **stage, "record": record.name, **pc, "data": a.data,
          "tool": tool, **({"container": container_name(record.name)} if tool else {})}
    (record / "pc.json").write_text(json.dumps(pc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                                    newline="\n")
    print(f"pc_run: {record.relative_to(REPO)}, host clock {pc['host']['ntp_offset_s']} s off NTP "
          f"({pc['host']['ntp_samples']}), container {pc['container_clock_offset_s']} s", flush=True)
    if a.checks_only:
        return 0
    cmd = docker_command(pc["eval_image_id"], record, Path(a.data).resolve(), tool)
    code = None
    try:
        with open(record / f"{Path(tool[0]).stem}.log", "w", encoding="utf-8", newline="") as log:  # LF, as the tool's
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            for raw in p.stdout:
                line = raw.decode("utf-8", errors="replace")
                sys.stdout.write(line)
                log.write(line)
            code = p.wait()
    finally:
        # How and when the tool ended, also when this script is stopped (exit_code none): tools/m4_order.py
        # tells a pass that did not start from one that ended without its pass.csv (M4 §5).
        end = dt.datetime.now(dt.timezone.utc)
        (record / "exit.txt").write_text(f"exit_code: {'none' if code is None else code}\n"
                                         f"end_time_utc: {end:%Y-%m-%dT%H:%M:%SZ}\n", encoding="utf-8", newline="\n")
    return code


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")  # the tool's UTF-8 output on a console with another code page
    sys.exit(main(sys.argv[1:]))
