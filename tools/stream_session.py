#!/usr/bin/env python3
"""Measurement sessions that read the RTSP stream server (PROTOCOL §3), run on the board.

Subcommands:
  limit        the decode-only limit L (§6.1): --variant none runs, searched from 8 channels up to
               the cap of 32 and confirmed with the procedure of §10, "passes" in place of "sustains"
  loop-check   the loop check of §3: for each video of the assignment, a 1-channel --variant none run
               across at least two of its loop points, which passes with zero decoder drops
  thermal      the thermal check of §12: B at the largest auxiliary and the largest main channel
               count, 140 s each; it passes when no clock sample falls below its fixed value and every
               temperature stays below its zone's trip_point_0_temp

Each session:
  - fixes the clocks (tools/set_perf_mode.sh) and records the environment (tools/collect_env.sh);
  - starts stream-server/publish.sh on the stream PC with the M1 assignment, under systemd-inhibit so
    the PC does not sleep, and stops it at the end, keeping its logs;
  - idles first and takes the soc-thermal baseline, then puts every run behind the cooldown gate
    (§9.2), and stops if 30 minutes pass without a run (§2);
  - records the environment before every run (§9.1);
  - judges every run by §9.3 rules 1, 2 and 4 and repeats an invalid run once. Rule 1 is the
    criterion of the thermal check itself, so it does not make a thermal run invalid.

A run passes the limit search when every channel keeps receive drift <= 1 s and has no decoder drops
(§6.1). An invalid x2 run counts as not passing (§10.3).

Rule 4 reads the server's log, whose lines have one-second resolution: an event counts when its
second lies in [warm-up start, window end), both rounded down to the second. The bench disconnects
right after the window, so events in the window's last partial second are not seen. Events: an ERR
line, a reader session or connection of the board that closes, a publisher that stops or exits, and
a publisher start after the first one of its channel in this run. WAR lines are recorded only.

Usage, on the board as root from the repository (bench_main built by tools/deploy_board.sh):
  COOLING=<text> AMBIENT_C=<°C> python3 tools/stream_session.py limit [--out DIR]
  COOLING=... AMBIENT_C=... python3 tools/stream_session.py loop-check [--out DIR]
  COOLING=... AMBIENT_C=... python3 tools/stream_session.py thermal --main-channels N [--out DIR]
Options for smoke tests only (recorded, and flagged in the result): --idle-s, --warmup, --duration,
--gate-s, and for limit --start and --cap.
Writes <out>/runs.tsv, one directory per run with the server's log lines, server/ (the whole server
log), result.csv; prints progress. Exit code 0 when the session completed (whatever it found),
1 when it stopped early, 2 for bad arguments.
"""
import argparse
import csv
import datetime as dt
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SERVER, BOARD_IP = "10.88.0.1", "10.88.0.2"
SSH = ["ssh", "-i", "/home/pi/.ssh/bench_stream", "-o", "UserKnownHostsFile=/home/pi/.ssh/known_hosts",
       "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"eunkyo@{SERVER}"]
PC_REPO, PC_VIDEOS = "rk3588s-zero-copy", "media/bench"  # relative to the PC user's home
ASSIGNMENT = "stream-server/assignment_m1.txt"
LIMIT_START, LIMIT_CAP = 8, 32  # §6.1
MAIN_SET = (1, 2, 4, 8, 12, 16)  # §6.1
PROTOCOL = {"idle_s": 600, "warmup": 20, "duration": 120, "gate_s": 60, "start": LIMIT_START, "cap": LIMIT_CAP}
DOMAINS = ["policy0_khz", "policy4_khz", "policy6_khz", "dmc_hz", "npu_hz"]
GATE_MC, SESSION_END_S = 3000, 30 * 60 - 60  # a minute's margin for collect_env and setup

LOG_LINE = re.compile(r"^(\d{4}/\d\d/\d\d \d\d:\d\d:\d\d) (\w+) (.*)$")


class Stop(Exception):
    """The session cannot go on."""


# ---------------------------------------------------------------- pure parts (tests/test_stream_session.py)

def parse_utc(s):
    """ISO 8601 UTC with optional fraction, e.g. 2026-11-02T08:30:01.123Z."""
    return dt.datetime.strptime(s.replace("Z", "+0000"),
                                "%Y-%m-%dT%H:%M:%S.%f%z" if "." in s else "%Y-%m-%dT%H:%M:%S%z")


def parse_server_log(text, utc_offset):
    """[(utc time, level, message)] of a mediamtx log in local time with the given offset (+0900)."""
    sign = -1 if utc_offset.startswith("-") else 1
    offset = dt.timedelta(hours=int(utc_offset[1:3]), minutes=int(utc_offset[3:5])) * sign
    out = []
    for line in text.splitlines():
        m = LOG_LINE.match(line)
        if m:
            local = dt.datetime.strptime(m.group(1), "%Y/%m/%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
            out.append((local - offset, m.group(2), m.group(3)))
    return out


def server_events(lines, run_start, warmup_start, window_end, board_ip=BOARD_IP):
    """(events, warnings) of §9.3 rule 4 for a run whose bench process started at run_start."""
    lo, hi = warmup_start.replace(microsecond=0), window_end.replace(microsecond=0)
    creators, started, events, warnings = {}, set(), [], []
    for t, level, msg in lines:
        m = re.search(r"\[session (\w+)\] created by ([\d.]+):\d+", msg)
        if m:
            creators[m.group(1)] = m.group(2)
        if t < run_start.replace(microsecond=0):
            continue
        path = re.search(r"\[path (ch\d+)\] runOnDemand command (started|stopped|exited)", msg)
        first_start = path is not None and path.group(2) == "started" and path.group(1) not in started
        if path and path.group(2) == "started":
            started.add(path.group(1))
        if not lo <= t < hi:
            continue
        stamp = f"{t:%H:%M:%SZ} {msg}"
        session = re.search(r"\[session (\w+)\] destroyed", msg)
        if level == "ERR":
            events.append("error: " + stamp)
        elif level == "WAR":
            warnings.append(stamp)
        if re.search(r"\[conn " + re.escape(board_ip) + r":\d+\] closed", msg):
            events.append("reader disconnect: " + stamp)
        elif session and creators.get(session.group(1)) == board_ip:
            events.append("reader disconnect: " + stamp)
        elif session:
            events.append("publisher exit: " + stamp)
        elif path and path.group(2) != "started":
            events.append("publisher exit: " + stamp)
        elif path and not first_start:
            events.append("publisher restart: " + stamp)
    return events, warnings


def limit_search(trial, start=LIMIT_START, cap=LIMIT_CAP):
    """§10 steps 1 and 2 with the cap of §6.1. trial(count, phase) -> passed. Returns n."""
    if trial(start, "search"):
        n = start
        while n < cap and trial(n + 1, "search"):
            n += 1
    else:
        n = 0
        for c in range(start - 1, 0, -1):
            if trial(c, "search"):
                n = c
                break
    confirmations = {}  # count -> results of its confirmation runs; search runs do not count

    def confirm(count):
        runs = confirmations.setdefault(count, [])
        while len(runs) < 3:
            runs.append(trial(count, "confirm"))
        return sum(runs)

    while True:
        at_n = confirm(n) if n >= 1 else None  # counts in ascending order
        above = confirm(n + 1) if n < cap else None
        if above is not None and above >= 2:
            n += 1
        elif at_n is not None and at_n <= 1:
            n -= 1
        else:
            return n


def main_set(limit):
    return sorted({c for c in MAIN_SET if c <= limit} | ({limit} if limit >= 1 else set()))


def read_csv(path):
    """(header, rows) of a bench CSV: "# key: value" lines, then a table."""
    header, body = {}, []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("# "):
                k, _, v = line[2:].rstrip("\n").partition(": ")
                header[k] = v
            else:
                body.append(line)
    return header, list(csv.DictReader(body))


def channels_pass(rows):
    """§6.1: every channel keeps drift <= 1 s and has no decoder drops (and its input did not end)."""
    return bool(rows) and all(float(r["drift_max_s"]) <= 1.0 and int(r["decoder_drops"]) == 0
                              and r["ended_early"] == "0" for r in rows)


def clocks_below(summary, fixed):
    """Clock domains whose minimum over the window is below the fixed value (§9.3 rule 1)."""
    return [d for d in DOMAINS if int(summary[f"freq_min_{d}"]) < fixed[d]]


def thermal_problems(samples, fixed, trips):
    """§12: clock samples below the fixed values and temperatures at or above trip point 0, over
    every one-second sample of the run."""
    problems = []
    for s in samples:
        for d in DOMAINS:
            if int(s[f"freq_{d}"]) < fixed[d]:
                problems.append(f"t={s['t_s']} s: {d} {s[f'freq_{d}']}")
        for zone, trip in trips.items():
            if int(s[f"temp_mc_{zone}"]) >= trip:
                problems.append(f"t={s['t_s']} s: {zone} {s[f'temp_mc_{zone}']} m°C")
    return problems


def loops_in_window(duration_s, warmup, window):
    """Loop points of a video of duration_s that fall inside the window."""
    return math.floor((warmup + window) / duration_s) - math.floor(warmup / duration_s)


# ---------------------------------------------------------------- the session

def keyed(text):
    return {k: v for k, _, v in (line.partition(": ") for line in text.splitlines()) if v}


class Session:
    def __init__(self, args):
        self.args = args
        self.out = Path(args.out)
        if self.out.exists() and any(self.out.iterdir()):
            raise Stop(f"{self.out} is not empty: every earlier run is kept (PROTOCOL §9.3)")
        for k in ("COOLING", "AMBIENT_C"):
            if not os.environ.get(k):
                raise Stop(f"set {k} (PROTOCOL §2)")
        self.out.mkdir(parents=True, exist_ok=True)
        self.log = open(self.out / "session.log", "a", encoding="utf-8")
        self.seq = 0
        self.server_dir = None
        self.server_launched = False
        self.last_end = None
        self.runs = open(self.out / "runs.tsv", "w", encoding="utf-8")
        self.runs.write("seq\tname\tphase\tvariant\tchannels\tcondition\tattempt\texit\tvalid\tpassed\t"
                        "reasons\tgate_soc_mc\tstart_utc\tend_utc\n")
        self.runs.flush()

    def say(self, msg):
        line = f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ} {msg}"
        print(line, flush=True)
        self.log.write(line + "\n")
        self.log.flush()

    def sh(self, cmd, out_file, timeout=120):
        """Runs cmd with its output in out_file."""
        with open(out_file, "w", encoding="utf-8") as f:
            return subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, text=True, timeout=timeout)

    def pc(self, command, stdin=None, timeout=60):
        r = subprocess.run(SSH + [command], input=stdin, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise Stop(f"stream PC: `{command}` failed: {r.stderr.strip()[-300:]}")
        return r.stdout

    def soc(self):
        for z in Path("/sys/class/thermal").glob("thermal_zone*"):
            if (z / "type").read_text().strip() == "soc-thermal":
                v = (z / "temp").read_text().strip()
                if not v.isdigit():
                    raise Stop(f"cannot read {z}/temp")
                return int(v)
        raise Stop("no soc-thermal zone")

    # -- start and end

    def start(self):
        a = self.args
        if self.sh(["tools/set_perf_mode.sh"], self.out / "perf_mode.txt").returncode != 0:
            raise Stop("set_perf_mode.sh failed: " + (self.out / "perf_mode.txt").read_text())
        self.fixed = {k: int(v) for k, v in keyed((self.out / "perf_mode.txt").read_text()).items()}
        if self.sh(["tools/collect_env.sh"], self.out / "env.txt").returncode != 0:
            raise Stop("collect_env.sh failed")
        env = (self.out / "env.txt").read_text()
        self.trips = {m.group(1): int(m.group(2)) for m in
                      re.finditer(r"^thermal_(\S+): temp_mc=-?\d+ trip0_mc=(\d+)", env, re.M)}
        commit = re.search(r"^CODE_COMMIT:STRING=(.*)$", Path("build/CMakeCache.txt").read_text(), re.M)
        self.code_commit = commit.group(1) if commit else "unknown"
        self.start_server()
        self.say(f"idle for {a.idle_s} s")
        temps = []
        for i in range(a.idle_s):
            time.sleep(1)
            if i >= a.idle_s - 60:
                temps.append(self.soc())
        self.baseline = sum(temps) // len(temps)
        (self.out / "baseline.txt").write_text(f"idle_s: {a.idle_s}\nsoc_thermal_baseline_mc: {self.baseline}\n")
        self.say(f"soc-thermal baseline {self.baseline} m°C")
        self.last_end = time.monotonic()

    def start_server(self):
        self.say("starting the stream server")
        assignment = Path(ASSIGNMENT).read_text(encoding="utf-8")
        self.pc(f"cat > ~/{PC_REPO}/stream-server/session_assignment.txt", stdin=assignment)
        self.pc(f"cd ~/{PC_VIDEOS}; setsid nohup systemd-inhibit --what=sleep:idle:handle-lid-switch "
                f"--who=rk3588s-bench --why='RTSP server for a measurement session' --mode=block "
                f"~/{PC_REPO}/stream-server/publish.sh ~/{PC_REPO}/stream-server/session_assignment.txt "
                f"> ~/{PC_REPO}/stream-server/publish.out 2>&1 < /dev/null &")
        self.server_launched = True
        for _ in range(60):
            time.sleep(1)
            out = self.pc(f"cat ~/{PC_REPO}/stream-server/publish.out")
            m = re.search(r"channels, logs in (\S+)", out)
            if m and "listeners on :8554" in self.pc(f"cat {m.group(1)}/mediamtx.log 2>/dev/null || true"):
                self.server_dir = m.group(1)
                break
            if m is None and out.strip() and "channels" not in out:
                raise Stop("publish.sh: " + out.strip()[-300:])
        else:
            raise Stop("the stream server did not start")
        self.utc_offset = self.pc("date +%z").strip()
        pc_now = float(self.pc("date -u +%s.%N").strip())
        (self.out / "server.txt").write_text(
            f"log_dir: {self.server_dir}\nutc_offset: {self.utc_offset}\n"
            f"clock_difference_s: {pc_now - time.time():.3f} (PC minus board, includes one SSH round trip)\n")
        self.say(f"stream server up, logs in {self.server_dir}")

    def end(self):
        if self.server_launched:
            self.say("stopping the stream server")
            try:
                self.pc(f"kill $(cat ~/{PC_REPO}/stream-server/mediamtx.pid)")
                time.sleep(2)
                if self.server_dir:
                    (self.out / "server").mkdir(exist_ok=True)
                    for name in ("mediamtx.log", "assignment.tsv", "versions.txt", "console.log"):
                        (self.out / "server" / name).write_text(self.pc(f"cat {self.server_dir}/{name}"))
            except Stop as e:
                self.say(f"could not stop or copy the server: {e}")
        self.sh(["tools/collect_env.sh"], self.out / "env_end.txt")

    # -- runs

    def gate(self):
        while True:
            temp = self.soc()
            since = time.monotonic() - self.last_end
            if since >= SESSION_END_S:
                raise Stop("no run for 30 minutes: the session has ended (PROTOCOL §2)")
            if temp <= self.baseline + GATE_MC and since >= self.args.gate_s:
                return temp
            time.sleep(1)

    def attempt(self, name, phase, variant, channels, condition, run_kind, repetition, source):
        """One bench run. Returns (valid, reasons, run directory)."""
        a = self.args
        temp = self.gate()
        if self.sh(["tools/collect_env.sh"], self.out / f"{name}.env.txt").returncode != 0:
            raise Stop("collect_env.sh failed")
        run_dir = self.out / name
        cmd = ["./build/bench_main", "--variant", variant, "--channels", str(channels), "--source", source,
               "--condition", condition, "--warmup", str(a.warmup), "--duration", str(a.duration),
               "--run-kind", run_kind, "--repetition", str(repetition), "--out", str(run_dir)]
        start = dt.datetime.now(dt.timezone.utc)
        self.say(f"{name}: {variant}, {channels} ch, {condition}")
        try:
            code = self.sh(cmd, self.out / f"{name}.log", timeout=a.warmup + a.duration + 180).returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
        self.last_end = time.monotonic()
        end = dt.datetime.now(dt.timezone.utc)
        reasons = []
        summary_path = run_dir / "summary.csv"
        if code != 0 or not summary_path.exists():  # rule 2
            reasons.append(f"bench exit {code}")
        else:
            _, rows = read_csv(summary_path)
            summary = {r["key"]: r["value"] for r in rows}
            if phase != "thermal":  # rule 1; for the thermal check it is the criterion
                reasons += [f"clock below fixed: {d}" for d in clocks_below(summary, self.fixed)]
            log = self.pc(f"cat {self.server_dir}/mediamtx.log")
            lines = parse_server_log(log, self.utc_offset)
            events, warnings = server_events(lines, start, parse_utc(summary["warmup_start_utc"]),
                                             parse_utc(summary["window_end_utc"]))
            reasons += events  # rule 4
            lo, hi = start - dt.timedelta(seconds=2), end + dt.timedelta(seconds=2)
            (run_dir / "server.log").write_text("".join(
                line + "\n" for line, (t, _, _) in zip(
                    [l for l in log.splitlines() if LOG_LINE.match(l)], lines) if lo <= t <= hi))
            if warnings:
                (run_dir / "server_warnings.txt").write_text("\n".join(warnings) + "\n")
        return not reasons, reasons, run_dir, temp, start, end, code

    def run(self, phase, variant, channels, condition, run_kind, repetition=1, source=None):
        """A run, repeated once if invalid (§9.3). Returns (valid, run directory of the last attempt)."""
        source = source or f"rtsp://{SERVER}:8554/ch{{n}}"
        self.seq += 1
        base = f"{self.seq:02d}_{phase}_{variant}_{channels}ch"
        for attempt in (1, 2):
            name = base if attempt == 1 else base + "_repeat"
            valid, reasons, run_dir, temp, start, end, code = self.attempt(
                name, phase, variant, channels, condition, run_kind, repetition, source)
            passed = ""
            if valid and phase in ("search", "confirm"):
                passed = "1" if channels_pass(read_csv(run_dir / "channels.csv")[1]) else "0"
            self.runs.write("\t".join(map(str, [
                self.seq, name, phase, variant, channels, condition, attempt, code, int(valid), passed,
                "; ".join(reasons), temp, f"{start:%Y-%m-%dT%H:%M:%SZ}", f"{end:%Y-%m-%dT%H:%M:%SZ}"])) + "\n")
            self.runs.flush()
            self.say(f"{name}: " + ("valid" if valid else "invalid: " + "; ".join(reasons))
                     + (f", passed {passed}" if passed else ""))
            if valid:
                return True, run_dir
        self.say(f"{base}: invalid x2")
        return False, run_dir

    def write_result(self, rows, run_kind):
        a = self.args
        off = [f"{k} {getattr(a, k)} (protocol {v})" for k, v in PROTOCOL.items() if getattr(a, k) != v]
        header = {"start_time_utc": "", "session": self.out.name, "run_kind": run_kind,
                  "code_commit": self.code_commit, "cooling": os.environ["COOLING"],
                  "ambient_c": os.environ["AMBIENT_C"]}
        with open(self.out / "runs.tsv", encoding="utf-8") as f:
            starts = [r["start_utc"] for r in csv.DictReader(f, delimiter="\t")]
        header["start_time_utc"] = min(starts) if starts else ""
        rows = rows + [("protocol_settings", "yes" if not off else "no: " + "; ".join(off))]
        text = "".join(f"# {k}: {v}\n" for k, v in header.items())
        text += "key,value\n" + "".join(f"{k},{v}\n" for k, v in rows)
        (self.out / "result.csv").write_text(text, encoding="utf-8")
        print(text, end="")


def cmd_limit(s):
    confirmations = {}  # count -> confirmation runs so far, for the repetition index

    def trial(count, phase):
        repetition = 1
        if phase == "confirm":
            repetition = confirmations[count] = confirmations.get(count, 0) + 1
        valid, run_dir = s.run(phase, "none", count, "main", "calibration", repetition)
        return valid and channels_pass(read_csv(run_dir / "channels.csv")[1])

    limit = limit_search(trial, s.args.start, s.args.cap)
    ms = main_set(limit)
    rows = [("L", limit), ("main_set", " ".join(map(str, ms))),
            ("unsaturated_main_max_channels", min(limit, s.args.p_cap_main)),
            ("unsaturated_aux_max_channels", min(limit, s.args.p_cap_aux))]
    s.write_result(rows, "calibration")


def cmd_loop_check(s):
    durations = {}
    for line in Path(s.args.manifest).read_text(encoding="utf-8").splitlines()[1:]:
        f = line.split("\t")
        durations[f[0] + ".mp4"] = float(f[11])
    channels = []
    for line in Path(ASSIGNMENT).read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            ch, video = line.split()
            if video not in [v for _, v in channels]:
                channels.append((ch, video))
    rows = []
    for ch, video in channels:
        loops = loops_in_window(durations[video], s.args.warmup, s.args.duration)
        valid, run_dir = s.run("loop", "none", 1, "main", "check", source=f"rtsp://{SERVER}:8554/{ch}")
        drops = int(read_csv(run_dir / "channels.csv")[1][0]["decoder_drops"]) if valid else None
        ok = valid and loops >= 2 and drops == 0
        rows += [(f"{video}_channel", ch), (f"{video}_loop_points_in_window", loops),
                 (f"{video}_decoder_drops", drops if drops is not None else "invalid_x2"),
                 (f"{video}_passed", int(ok))]
    rows.append(("passed", int(all(v == 1 for k, v in rows if k.endswith("_passed")))))
    s.write_result(rows, "check")


def cmd_thermal(s):
    rows, passed = [], True
    for condition, channels in (("aux", s.args.aux_channels), ("main", s.args.main_channels)):
        valid, run_dir = s.run("thermal", "B", channels, condition, "calibration")
        problems = (thermal_problems(read_csv(run_dir / "samples.csv")[1], s.fixed, s.trips) if valid
                    else ["invalid x2"])
        (run_dir / "thermal_check.txt").write_text("\n".join(problems or ["pass"]) + "\n")
        passed &= not problems
        rows += [(f"{condition}_{channels}ch_problems", len(problems)), (f"{condition}_{channels}ch_passed",
                                                                          int(not problems))]
    rows.append(("passed", int(passed)))
    s.write_result(rows, "calibration")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["limit", "loop-check", "thermal"])
    ap.add_argument("--out")
    ap.add_argument("--main-channels", type=int, help="thermal: the largest main channel count")
    ap.add_argument("--aux-channels", type=int, default=5, help="thermal: the largest auxiliary count (A7: 5)")
    ap.add_argument("--p-cap-main", type=int, default=31, help="limit: P_cap's main bound (A7: 31)")
    ap.add_argument("--p-cap-aux", type=int, default=5, help="limit: P_cap's auxiliary bound (A7: 5)")
    ap.add_argument("--manifest", default="/home/pi/media/bench/manifest.tsv")
    ap.add_argument("--idle-s", type=int, default=PROTOCOL["idle_s"])
    ap.add_argument("--warmup", type=int, default=PROTOCOL["warmup"])
    ap.add_argument("--duration", type=int, default=PROTOCOL["duration"])
    ap.add_argument("--gate-s", type=int, default=PROTOCOL["gate_s"])
    ap.add_argument("--start", type=int, default=PROTOCOL["start"])
    ap.add_argument("--cap", type=int, default=PROTOCOL["cap"])
    args = ap.parse_args(argv)
    if args.command == "thermal" and not args.main_channels:
        ap.error("thermal needs --main-channels")
    if args.idle_s < 60:
        ap.error("--idle-s must be at least 60")
    if not 1 <= args.start <= args.cap <= 32:
        ap.error("need 1 <= --start <= --cap <= 32 (the assignment has 32 channels)")
    kind = {"limit": "limit", "loop-check": "loop", "thermal": "thermal"}[args.command]
    args.out = args.out or f"results/{'check' if kind == 'loop' else 'calibration'}/" \
                           f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}_{kind}"
    try:
        s = Session(args)
    except Stop as e:
        print(f"stream_session: {e}", file=sys.stderr)
        return 2
    try:
        s.start()
        {"limit": cmd_limit, "loop-check": cmd_loop_check, "thermal": cmd_thermal}[args.command](s)
        status = 0
    except Stop as e:
        s.say(f"stopped: {e}")
        status = 1
    finally:
        s.end()
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
