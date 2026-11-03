#!/usr/bin/env python3
"""Measurement sessions that read the RTSP stream server (PROTOCOL §3), run on the board.

Subcommands:
  limit        the decode-only limit L (§6.1): --variant none runs, searched from 8 channels up to
               the cap of 32 and confirmed with the procedure of §10, "passes" in place of "sustains"
  loop-check   the loop check of §3: for each video of the assignment, a 1-channel --variant none run
               with at least two of its loop points in the window, which passes with zero decoder drops
  thermal      the thermal check of §12: B at the largest auxiliary count (5, A7) and at the largest
               main count (L), 140 s each. It passes when no clock sample falls below its fixed value
               and every temperature stays below its zone's trip_point_0_temp.

Each session:
  - fixes the clocks (tools/set_perf_mode.sh) and records the environment (tools/collect_env.sh);
  - starts stream-server/publish.sh on the stream PC with the M1 assignment, with its log in UTC and
    under systemd-inhibit so the PC does not sleep, and stops it at the end, keeping its logs; records
    the PC's stream-server commit, the hashes of its configuration and the clock offset to the PC;
  - idles first for the soc-thermal baseline, then puts every run behind the cooldown gate (§9.2), and
    stops if 30 minutes pass without a run (§2);
  - records the environment before every run (§9.1), and the server's log lines and publisher output
    with every run (§3);
  - judges every run by §9.3 rules 1 to 4 where they apply, and repeats an invalid run once.

Which rules apply:
  - limit runs: rules 1, 2 and 4 (§6.1). A run passes when every channel keeps receive drift <= 1 s and
    has no decoder drops (§6.1); an invalid x2 run counts as not passing (§10.3).
  - loop-check runs: rules 1, 2 and 4. Zero decoder drops is the check's criterion, not rule 3.
  - thermal runs: rules 2, 3 (both runs are at unsaturated conditions when L <= 31) and 4. Rule 1 is
    the check's own criterion, so it does not make a thermal run invalid.
External failures (§9.3, last paragraph): an input that ends early, a server that is down before or
after a run, or a log that cannot be read make the run invalid with the reason given; a log that cannot
be read, a server that is
down, or a PC clock more than 5 ms behind or 100 ms ahead of the board's, stops the session. SIGTERM
or SIGHUP (the operator) stops the session too: a run it cuts short is recorded with that reason and is
neither repeated nor counted. A stopped limit search is kept and flagged, and the search is run again
from §10 step 1 in a new session.

Rule 4 reads the server's log, whose lines have one-second resolution. An event counts when its second
lies in [warm-up start, window end], both rounded down to the second. In the window end's own second
the board's disconnect lines are exempt, since the bench disconnects right after the window; the
publishers stop at least 1 s later (runOnDemandCloseAfter). Events: an ERR line; a closed connection or
session of the board; a publisher that stops or exits; a publisher start after the first one of its
channel in this run; a session of another host that closes. WAR lines and publisher output (ffmpeg)
are recorded as warnings.

Usage, on the board as root from the repository (bench_main built by tools/deploy_board.sh), detached
from the login session (setsid nohup):
  COOLING=<text> AMBIENT_C=<°C> python3 tools/stream_session.py limit [--out DIR]
  COOLING=... AMBIENT_C=... python3 tools/stream_session.py loop-check [--out DIR]
  COOLING=... AMBIENT_C=... python3 tools/stream_session.py thermal --limit L [--out DIR]
Options for smoke tests only (recorded, and flagged in the result): --idle-s, --warmup, --duration,
--gate-s, and for limit --start and --cap.
Writes <out>/runs.tsv, one directory per run with the server's log lines, server/ (the whole server
log), result.csv; prints progress. Exit code 0 when the session completed (whatever it found), 1 when
it stopped early, 2 for bad arguments.
"""
import argparse
import csv
import datetime as dt
import math
import os
import re
import select
import signal
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path

SERVER, BOARD_IP, RTSP_PORT = "10.88.0.1", "10.88.0.2", 8554
SSH = ["ssh", "-i", "/home/pi/.ssh/bench_stream", "-o", "UserKnownHostsFile=/home/pi/.ssh/known_hosts",
       "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"eunkyo@{SERVER}"]
PC_REPO, PC_VIDEOS = "rk3588s-zero-copy", "media/bench"  # relative to the PC user's home
ASSIGNMENT = "stream-server/assignment_m1.txt"
MANIFEST = "/home/pi/media/bench/manifest.tsv"
LIMIT_START, LIMIT_CAP = 8, 32  # §6.1
MAIN_SET = (1, 2, 4, 8, 12, 16)  # §6.1
P_CAP_BUDGET = Fraction(4661, 30)  # 0.8 × P_cap, inferences per second (A7)
AUX_MAX = 5  # the largest auxiliary channel count (A7)
FPS = {"main": 5, "aux": 30}
PROTOCOL = {"idle_s": 600, "warmup": 20, "duration": 120, "gate_s": 60, "start": LIMIT_START, "cap": LIMIT_CAP}
DOMAINS = ["policy0_khz", "policy4_khz", "policy6_khz", "dmc_hz", "npu_hz"]
GATE_MC, SESSION_END_S = 3000, 30 * 60 - 60  # a minute's margin for collect_env and setup
MAX_PC_AHEAD_S, MAX_PC_BEHIND_S = 0.1, 0.005

LOG_LINE = re.compile(r"^(\d{4}/\d\d/\d\d \d\d:\d\d:\d\d) (\w+) (.*)$")


class Stop(Exception):
    """The session cannot go on."""


class Interrupted(Stop):
    """The operator stopped the session (SIGTERM or SIGHUP). Never taken as a run's outcome."""


def clock_problem(offset):
    """Why the PC's clock offset (PC minus board, s) makes rule 4 untrustworthy, or None. The bench
    disconnects within milliseconds of the window's end: a PC that lags by more could stamp that
    disconnect into the window's last full second, where it would count."""
    if offset < -MAX_PC_BEHIND_S or offset > MAX_PC_AHEAD_S:
        return f"the PC's clock is {offset * 1000:.1f} ms off the board's (allowed -{MAX_PC_BEHIND_S * 1000:.0f} " \
               f"to +{MAX_PC_AHEAD_S * 1000:.0f} ms)"
    return None


# ---------------------------------------------------------------- pure parts (tests/test_stream_session.py)

def parse_utc(s):
    """ISO 8601 UTC with optional fraction, e.g. 2026-11-02T08:30:01.123Z."""
    return dt.datetime.strptime(s.replace("Z", "+0000"),
                                "%Y-%m-%dT%H:%M:%S.%f%z" if "." in s else "%Y-%m-%dT%H:%M:%S%z")


def parse_server_log(text, utc_offset="+0000"):
    """[(utc time, level, message, raw line)] of a mediamtx log in local time with the given offset."""
    sign = -1 if utc_offset.startswith("-") else 1
    offset = dt.timedelta(hours=int(utc_offset[1:3]), minutes=int(utc_offset[3:5])) * sign
    out = []
    for line in text.splitlines():
        m = LOG_LINE.match(line)
        if m:
            local = dt.datetime.strptime(m.group(1), "%Y/%m/%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
            out.append((local - offset, m.group(2), m.group(3), line))
    return out


def server_events(lines, run_start, warmup_start, window_end, board_ip=BOARD_IP):
    """(events, warnings) of §9.3 rule 4 for a run whose bench process started at run_start."""
    lo, hi = warmup_start.replace(microsecond=0), window_end.replace(microsecond=0)
    track_from = run_start.replace(microsecond=0) - dt.timedelta(seconds=30)  # runs are >= 60 s apart
    creators, started, events, warnings = {}, set(), [], []
    for t, level, msg, _ in lines:
        m = re.search(r"\[session (\w+)\] created by ([\d.]+):\d+", msg)
        if m:
            creators[m.group(1)] = m.group(2)
        if t < track_from:
            continue
        path = re.search(r"\[path (ch\d+)\] runOnDemand command (started|stopped|exited)", msg)
        first_start = path is not None and path.group(2) == "started" and path.group(1) not in started
        if path and path.group(2) == "started":
            started.add(path.group(1))
        if not lo <= t <= hi:
            continue
        stamp = f"{t:%H:%M:%SZ} {msg}"
        session = re.search(r"\[session (\w+)\] destroyed", msg)
        board_conn = re.search(r"\[conn " + re.escape(board_ip) + r":\d+\] closed", msg)
        board_session = session is not None and creators.get(session.group(1)) == board_ip
        if t == hi and (board_conn or board_session):
            continue  # the bench's own disconnect after the window
        if level == "ERR":
            events.append("error: " + stamp)
        elif level == "WAR":
            warnings.append(stamp)
        if board_conn or board_session:
            events.append("reader disconnect: " + stamp)
        elif session and creators.get(session.group(1)) == "127.0.0.1":
            events.append("publisher exit: " + stamp)
        elif session:
            events.append("session of another host closed: " + stamp)
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


def unsaturated(channels, condition, limit):
    """§6.2: channels <= L and channels × fps <= 0.8 × P_cap."""
    return channels <= limit and channels * FPS[condition] <= P_CAP_BUDGET


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
    """§6.1: every channel keeps drift <= 1 s and has no decoder drops."""
    return bool(rows) and all(float(r["drift_max_s"]) <= 1.0 and int(r["decoder_drops"]) == 0 for r in rows)


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


def trip_samples(samples, trips):
    """Samples at or above a zone's trip point 0: warnings for any run (§9.3)."""
    return sum(1 for s in samples for zone, trip in trips.items() if int(s[f"temp_mc_{zone}"]) >= trip)


def loops_in_window(duration_s, warmup, window):
    """Loop points of a video of duration_s that fall inside the window."""
    return math.floor((warmup + window) / duration_s) - math.floor(warmup / duration_s)


# ---------------------------------------------------------------- the session

def keyed(text):
    return {k: v for k, _, v in (line.partition(": ") for line in text.splitlines()) if v}


class Outcome:
    def __init__(self):
        self.reasons, self.warnings = [], []
        self.external = None  # an external failure that stops the session
        self.code = None
        self.trip_samples = ""
        self.offset_ms = ""


class Session:
    def __init__(self, args):
        self.args = args
        self.out = Path(args.out)
        if self.out.exists() and any(self.out.iterdir()):
            raise Stop(f"{self.out} is not empty: every earlier run is kept (PROTOCOL §9.3)")
        for k in ("COOLING", "AMBIENT_C"):
            if not os.environ.get(k):
                raise Stop(f"set {k} (PROTOCOL §2)")
        for f in ("build/bench_main", "build/CMakeCache.txt", ASSIGNMENT, MANIFEST):
            if not Path(f).exists():
                raise Stop(f"no {f}")
        self.out.mkdir(parents=True, exist_ok=True)
        self.log = open(self.out / "session.log", "a", encoding="utf-8")
        self.seq = 0
        self.server_dir = None
        self.server_launched = False
        self.launcher_pid = None
        self.server_started = False
        self.last_end = None
        self.invalid_x2 = []
        self.run_header = None  # the first run's header, for result.csv
        self.runs = open(self.out / "runs.tsv", "w", encoding="utf-8")
        self.runs.write("seq\tname\tphase\tvariant\tchannels\tcondition\trepetition\tattempt\texit\tvalid\t"
                        "passed\treasons\twarnings\ttrip_samples\tgate_soc_mc\tclock_offset_ms\tstart_utc\tend_utc\n")
        self.runs.flush()

    def say(self, msg):
        line = f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ} {msg}"
        try:
            print(line, flush=True)
        except OSError:  # no terminal any more; session.log keeps the line
            pass
        self.log.write(line + "\n")
        self.log.flush()

    def sh(self, cmd, out_file, timeout=120):
        """Runs cmd with its output in out_file."""
        try:
            with open(out_file, "w", encoding="utf-8") as f:
                return subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, text=True, timeout=timeout).returncode
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Stop(f"{cmd[0]}: {e}") from None

    def pc(self, command, stdin=None, timeout=60, tries=3):
        """Runs a command on the stream PC; read-only commands are tried up to 3 times."""
        error = ""
        for k in range(tries):
            try:
                r = subprocess.run(SSH + [command], input=stdin, capture_output=True, text=True, timeout=timeout)
                if r.returncode == 0:
                    return r.stdout
                error = r.stderr.strip()[-300:] or f"exit {r.returncode}"
            except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as e:
                error = str(e)
            if k + 1 < tries:
                time.sleep(5 * (k + 1))
        raise Stop(f"stream PC: `{command[:80]}` failed: {error}")

    def soc(self):
        for z in Path("/sys/class/thermal").glob("thermal_zone*"):
            if (z / "type").read_text().strip() == "soc-thermal":
                v = (z / "temp").read_text().strip()
                if not v.isdigit():
                    raise Stop(f"cannot read {z}/temp")
                return int(v)
        raise Stop("no soc-thermal zone")

    def clock_offset(self):
        """(PC clock minus board clock, round trip) in seconds: of 5 exchanges over one open SSH
        connection, the one with the shortest round trip, whose midpoint is then closest to the
        moment the PC read its clock. The connection's setup is not timed."""
        p = None
        try:
            p = subprocess.Popen(SSH + ["while read -r x; do date -u +%s.%N; done"], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
            best = None
            for k in range(6):
                t0 = time.time()
                p.stdin.write("x\n")
                p.stdin.flush()
                if not select.select([p.stdout], [], [], 20)[0]:
                    raise Stop("the PC did not answer the clock exchange")
                line = p.stdout.readline()
                t1 = time.time()
                if not line.strip():
                    raise Stop("the clock exchange with the PC ended")
                pc_now = float(line)  # also for the first answer: anything else would shift every pair
                if k == 0:
                    continue  # the first answer waits for the connection's setup
                offset, rtt = pc_now - (t0 + t1) / 2, t1 - t0
                if best is None or rtt < best[1]:
                    best = (offset, rtt)
            return best
        except (OSError, ValueError) as e:
            raise Stop(f"clock exchange with the PC: {e}") from None
        finally:
            if p:
                p.kill()
                p.wait()

    def server_alive(self):
        """The server process runs and listens on its RTSP port. The SSH call itself goes over the
        wired link, so it checks the link too. (A TCP probe from the board would show in the
        server's log as a reader connection.)"""
        try:
            self.pc(f"kill -0 \"$(cat ~/{PC_REPO}/stream-server/mediamtx.pid)\" && ss -ltn | grep -q ':{RTSP_PORT} '",
                    tries=2)
            return True
        except Interrupted:
            raise
        except Stop:
            return False

    # -- start and end

    def start(self):
        a = self.args
        if self.sh(["tools/set_perf_mode.sh"], self.out / "perf_mode.txt") != 0:
            raise Stop("set_perf_mode.sh failed: " + (self.out / "perf_mode.txt").read_text())
        self.fixed = {k: int(v) for k, v in keyed((self.out / "perf_mode.txt").read_text()).items()}
        if self.sh(["tools/collect_env.sh"], self.out / "env.txt") != 0:
            raise Stop("collect_env.sh failed")
        env = (self.out / "env.txt").read_text()
        self.trips = {m.group(1): int(m.group(2)) for m in
                      re.finditer(r"^thermal_(\S+): temp_mc=-?\d+ trip0_mc=(\d+)", env, re.M)}
        if "soc-thermal" not in self.trips:
            raise Stop("env.txt has no trip point for soc-thermal")
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
        self.last_end = time.monotonic() - a.gate_s  # no bench ran during the idle period

    def start_server(self):
        self.say("starting the stream server")
        pid = f"~/{PC_REPO}/stream-server/mediamtx.pid"
        if self.pc(f"test -f {pid} && kill -0 \"$(cat {pid})\" 2>/dev/null && echo running || true").strip():
            raise Stop("a stream server is already running on the PC; stop it first")
        self.pc(f"cat > ~/{PC_REPO}/stream-server/session_assignment.txt",
                stdin=Path(ASSIGNMENT).read_text(encoding="utf-8"), tries=1)
        # setsid makes the launcher the leader of its own process group, so end() can stop everything
        # it started, even a server that never reported its log directory.
        out = self.pc(f"cd ~/{PC_VIDEOS}; TZ=UTC setsid nohup systemd-inhibit --what=sleep:idle:handle-lid-switch "
                      f"--who=rk3588s-bench --why='RTSP server for a measurement session' --mode=block "
                      f"~/{PC_REPO}/stream-server/publish.sh ~/{PC_REPO}/stream-server/session_assignment.txt "
                      f"> ~/{PC_REPO}/stream-server/publish.out 2>&1 < /dev/null & echo $!", tries=1)
        self.server_launched = True
        self.launcher_pid = int(out.strip())
        for _ in range(90):
            time.sleep(1)
            out = self.pc(f"cat ~/{PC_REPO}/stream-server/publish.out")
            m = re.search(r"channels, logs in (\S+)", out)
            if m:
                self.server_started = True
                if "listeners on :8554" in self.pc(f"cat {m.group(1)}/mediamtx.log 2>/dev/null || true"):
                    self.server_dir = m.group(1)
                    break
            elif out.strip():
                raise Stop("publish.sh: " + out.strip()[-300:])
        else:
            raise Stop("the stream server did not start")
        offset, rtt = self.clock_offset()
        info = {
            "log_dir": self.server_dir,
            "log_time_zone": "UTC (TZ=UTC)",
            "clock_offset_s": f"{offset:.4f} (PC minus board; round trip of the exchange {rtt:.4f} s)",
            "pc_ntp_synchronized": self.pc("timedatectl show -p NTPSynchronized --value").strip(),
            "pc_repo_commit": self.pc(f"git -C ~/{PC_REPO} rev-parse HEAD").strip(),
            "pc_repo_status": self.pc(f"git -C ~/{PC_REPO} status --porcelain -- stream-server | grep -v "
                                      f"'session_assignment.txt\\|publish.out' || true").strip() or "clean",
            "pc_sha256": " ".join(self.pc(f"cd ~/{PC_REPO}/stream-server && sha256sum mediamtx.yml publish.sh "
                                          f"mediamtx").split()),
            "pc_versions": " | ".join(self.pc(f"cat {self.server_dir}/versions.txt").splitlines()),
        }
        (self.out / "server.txt").write_text("".join(f"{k}: {v}\n" for k, v in info.items()))
        problem = clock_problem(offset)
        if problem:
            raise Stop(problem)
        self.say(f"stream server up, logs in {self.server_dir}, clock offset {offset * 1000:.1f} ms")

    def end(self):
        # A second signal must not cut the cleanup short.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        if self.server_launched:
            self.say("stopping the stream server")
            steps = []
            if self.server_started:  # mediamtx first, so that it stops its publishers itself
                steps.append(("stop", lambda: self.pc(
                    f"kill \"$(cat ~/{PC_REPO}/stream-server/mediamtx.pid)\"; sleep 2; "
                    f"! kill -0 \"$(cat ~/{PC_REPO}/stream-server/mediamtx.pid)\" 2>/dev/null", tries=1)))
            # Then whatever is left of the launcher's process group (a server still starting, the inhibitor).
            steps.append(("stop the launcher's process group", lambda: self.pc(
                f"kill -- -{self.launcher_pid} 2>/dev/null; sleep 2; ! kill -0 -- -{self.launcher_pid} 2>/dev/null",
                tries=1)))
            if self.server_dir:
                (self.out / "server").mkdir(exist_ok=True)
                for name in ("mediamtx.log", "assignment.tsv", "versions.txt", "console.log"):
                    steps.append((name, lambda n=name: (self.out / "server" / n).write_text(
                        self.pc(f"cat {self.server_dir}/{n}"))))
            steps.append(("publish.out", lambda: (self.out / "publish.out").write_text(
                self.pc(f"cat ~/{PC_REPO}/stream-server/publish.out"))))
            for what, step in steps:
                try:
                    step()
                except Exception as e:  # keep going: every step that can still work should
                    self.say(f"could not {what}: {e}")
        try:
            self.sh(["tools/collect_env.sh"], self.out / "env_end.txt")
        except Stop as e:
            self.say(f"collect_env.sh at the end: {e}")

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

    def console_size(self):
        return int(self.pc(f"stat -c %s {self.server_dir}/console.log").strip())

    def attempt(self, name, phase, variant, channels, condition, run_kind, repetition, source):
        """One bench run, judged. Returns (Outcome, run directory, gate temperature, start, end)."""
        a, o = self.args, Outcome()
        temp = self.gate()
        if not self.server_alive():
            raise Stop("the stream server is down (external failure, PROTOCOL §9.3)")
        if self.sh(["tools/collect_env.sh"], self.out / f"{name}.env.txt") != 0:
            raise Stop("collect_env.sh failed")
        console_before = self.console_size()
        run_dir = self.out / name
        cmd = ["./build/bench_main", "--variant", variant, "--channels", str(channels), "--source", source,
               "--condition", condition, "--warmup", str(a.warmup), "--duration", str(a.duration),
               "--run-kind", run_kind, "--repetition", str(repetition), "--out", str(run_dir)]
        start = dt.datetime.now(dt.timezone.utc)
        self.say(f"{name}: {variant}, {channels} ch, {condition}")
        try:
            o.code = self.sh(cmd, self.out / f"{name}.log", timeout=a.warmup + a.duration + 180)
        except Interrupted as e:  # subprocess.run has killed the bench
            o.code = "interrupted"
            o.reasons.append(f"stopped by the operator ({e})")
            o.external = "stopped by the operator"
            self.last_end = time.monotonic()
            return o, run_dir, temp, start, dt.datetime.now(dt.timezone.utc)
        except Stop as e:
            o.code = f"failed: {e}"
        self.last_end = time.monotonic()
        end = dt.datetime.now(dt.timezone.utc)
        try:
            self.judge(o, run_dir, phase, channels, condition, start, end, console_before)
        except Interrupted as e:
            o.reasons.append(f"stopped by the operator before the run was judged ({e})")
            o.external = "stopped by the operator"
        return o, run_dir, temp, start, end

    def judge(self, o, run_dir, phase, channels, condition, start, end, console_before):
        """Fills o for a finished bench run (§9.3)."""
        a = self.args
        # The server's side: alive, its log lines and its publishers' output, kept with every run.
        alive = self.server_alive()
        lines = None
        try:
            offset, _ = self.clock_offset()
            o.offset_ms = f"{offset * 1000:.1f}"
            problem = clock_problem(offset)
            if problem:
                o.reasons.append(problem + " (external failure)")
                o.external = problem
            lines = parse_server_log(self.pc(f"cat {self.server_dir}/mediamtx.log"))
            run_dir.mkdir(exist_ok=True)
            lo, hi = start - dt.timedelta(seconds=2), end + dt.timedelta(seconds=2)
            (run_dir / "server.log").write_text("".join(raw + "\n" for t, _, _, raw in lines if lo <= t <= hi))
            added = self.pc(f"tail -c +{console_before + 1} {self.server_dir}/console.log")
            published = [l for l in added.splitlines() if not LOG_LINE.match(l)]  # mediamtx's own lines repeat its log
            if published:
                (run_dir / "server_console.txt").write_text("\n".join(published) + "\n")
                o.warnings.append(f"{len(published)} lines of publisher output")
        except Interrupted:
            raise
        except Stop as e:  # infrastructure, not an outcome: stop rather than count a repeat
            o.reasons.append(f"server log unavailable: {e} (external failure)")
            o.external = o.external or "the server's log could not be read"
        if not alive:
            o.reasons.append("stream server down after the run (external failure)")
            o.external = o.external or "the stream server is down"

        summary_path, channels_path = run_dir / "summary.csv", run_dir / "channels.csv"
        if o.code != 0 or not summary_path.exists() or not channels_path.exists():  # rule 2
            o.reasons.append(f"bench exit {o.code}")
        else:
            header, rows = read_csv(summary_path)
            self.run_header = self.run_header or header
            summary = {r["key"]: r["value"] for r in rows}
            ch_rows = read_csv(channels_path)[1]
            samples = read_csv(run_dir / "samples.csv")[1]
            missing = [z for z in self.trips if samples and f"temp_mc_{z}" not in samples[0]]
            if missing:
                o.reasons.append(f"samples.csv has no temperature for {', '.join(missing)}")
                o.external = "the samples lack trip-point zones"
                return
            o.trip_samples = trip_samples(samples, self.trips)
            if phase != "thermal":  # rule 1; for the thermal check it is the criterion
                o.reasons += [f"clock below fixed: {d}" for d in clocks_below(summary, self.fixed)]
            if phase == "thermal" and unsaturated(channels, condition, a.limit):  # rule 3
                drops = sum(int(r["decoder_drops"]) for r in ch_rows)
                if drops:
                    o.reasons.append(f"{drops} decoder drops at an unsaturated condition (rule 3)")
            ended = [r["channel"] for r in ch_rows if r["ended_early"] != "0"]
            if ended:
                o.reasons.append(f"input ended early on channel {', '.join(ended)} (external failure)")
            if lines is not None:
                events, warnings = server_events(lines, start, parse_utc(summary["warmup_start_utc"]),
                                                 parse_utc(summary["window_end_utc"]))
                o.reasons += events  # rule 4
                o.warnings += warnings

    def run(self, phase, variant, channels, condition, run_kind, repetition=1, source=None):
        """A run, repeated once if invalid (§9.3). Returns (valid, run directory of the last attempt)."""
        source = source or f"rtsp://{SERVER}:{RTSP_PORT}/ch{{n}}"
        self.seq += 1
        base = f"{self.seq:02d}_{phase}_{variant}_{channels}ch"
        for attempt in (1, 2):
            name = base if attempt == 1 else base + "_repeat"
            o, run_dir, temp, start, end = self.attempt(name, phase, variant, channels, condition, run_kind,
                                                        repetition, source)
            valid = not o.reasons
            passed = ""
            if valid and phase in ("search", "confirm"):
                passed = "1" if channels_pass(read_csv(run_dir / "channels.csv")[1]) else "0"
            self.runs.write("\t".join(map(str, [
                self.seq, name, phase, variant, channels, condition, repetition, attempt, o.code, int(valid), passed,
                "; ".join(o.reasons), "; ".join(o.warnings), o.trip_samples, temp, o.offset_ms,
                f"{start:%Y-%m-%dT%H:%M:%SZ}", f"{end:%Y-%m-%dT%H:%M:%SZ}"])) + "\n")
            self.runs.flush()
            self.say(f"{name}: " + ("valid" if valid else "invalid: " + "; ".join(o.reasons))
                     + (f", passed {passed}" if passed else ""))
            if o.external:
                raise Stop(f"{name}: {o.external}; the session stops (PROTOCOL §9.3)")
            if valid:
                return True, run_dir
        self.say(f"{base}: invalid x2")
        self.invalid_x2.append(base)
        return False, run_dir

    def write_result(self, rows, run_kind):
        a = self.args
        off = [f"{k} {getattr(a, k)} (protocol {v})" for k, v in PROTOCOL.items() if getattr(a, k) != v]
        with open(self.out / "runs.tsv", encoding="utf-8") as f:
            starts = [r["start_utc"] for r in csv.DictReader(f, delimiter="\t")]
        h = self.run_header or {}
        header = {"start_time_utc": min(starts) if starts else "", "ntp_synchronized": h.get("ntp_synchronized", ""),
                  "prereg_v1_commit": h.get("prereg_v1_commit", ""), "freeze_m1_commit": h.get("freeze_m1_commit", ""),
                  "run_kind": run_kind, "model_sha256": h.get("model_sha256", ""), "decoder": h.get("decoder", ""),
                  "code_commit": self.code_commit, "amendment": h.get("amendment", ""), "session": self.out.name,
                  "cooling": os.environ["COOLING"], "ambient_c": os.environ["AMBIENT_C"]}
        rows = rows + [("invalid_x2_runs", " ".join(self.invalid_x2) or "none"),
                       ("protocol_settings", "yes" if not off else "no: " + "; ".join(off))]
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
    budget_main = math.floor(P_CAP_BUDGET / FPS["main"])
    rows = [("L", limit), ("L_at_cap", int(limit == s.args.cap)), ("main_set", " ".join(map(str, main_set(limit)))),
            ("unsaturated_main_max_channels", min(limit, budget_main)),
            ("unsaturated_aux_max_channels", min(limit, math.floor(P_CAP_BUDGET / FPS["aux"])))]
    s.write_result(rows, "calibration")


def loop_channels():
    """[(channel, video, duration in s)] for the first channel of each video in the assignment."""
    durations = {}
    for line in Path(MANIFEST).read_text(encoding="utf-8").splitlines()[1:]:
        f = line.split("\t")
        durations[f[0] + ".mp4"] = float(f[11])
    out = []
    for line in Path(ASSIGNMENT).read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            ch, video = line.split()
            if video not in durations:
                raise Stop(f"{video} is not in {MANIFEST}")
            if video not in [v for _, v, _ in out]:
                out.append((ch, video, durations[video]))
    return out


def cmd_loop_check(s):
    rows = []
    for ch, video, duration in s.loops:
        loops = loops_in_window(duration, s.args.warmup, s.args.duration)
        valid, run_dir = s.run("loop", "none", 1, "main", "check", source=f"rtsp://{SERVER}:{RTSP_PORT}/{ch}")
        drops = int(read_csv(run_dir / "channels.csv")[1][0]["decoder_drops"]) if valid else None
        ok = valid and loops >= 2 and drops == 0
        rows += [(f"{video}_channel", ch), (f"{video}_loop_points_in_window", loops),
                 (f"{video}_decoder_drops", drops if drops is not None else "invalid_x2"),
                 (f"{video}_passed", int(ok))]
    rows.append(("passed", int(all(v == 1 for k, v in rows if k.endswith("_passed")))))
    s.write_result(rows, "check")


def cmd_thermal(s):
    rows, passed = [], True
    for condition, channels in (("aux", AUX_MAX), ("main", max(main_set(s.args.limit)))):
        valid, run_dir = s.run("thermal", "B", channels, condition, "calibration")
        if valid:
            samples = read_csv(run_dir / "samples.csv")[1]  # a valid run has every trip zone (judge)
            problems = thermal_problems(samples, s.fixed, s.trips)
            (run_dir / "thermal_check.txt").write_text("\n".join(problems or ["pass"]) + "\n")
            state = "passed" if not problems else f"failed ({len(problems)} samples)"
        else:
            problems, state = ["invalid x2"], "invalid_x2"
        passed &= not problems
        rows.append((f"{condition}_{channels}ch", state))
    rows.append(("passed", int(passed)))
    s.write_result(rows, "calibration")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["limit", "loop-check", "thermal"])
    ap.add_argument("--out")
    ap.add_argument("--limit", type=int, help="thermal: L, which gives the largest main count")
    ap.add_argument("--idle-s", type=int, default=PROTOCOL["idle_s"])
    ap.add_argument("--warmup", type=int, default=PROTOCOL["warmup"])
    ap.add_argument("--duration", type=int, default=PROTOCOL["duration"])
    ap.add_argument("--gate-s", type=int, default=PROTOCOL["gate_s"])
    ap.add_argument("--start", type=int, default=PROTOCOL["start"])
    ap.add_argument("--cap", type=int, default=PROTOCOL["cap"])
    args = ap.parse_args(argv)
    if args.command == "thermal" and not (args.limit and 1 <= args.limit <= 32):
        ap.error("thermal needs --limit L (1..32)")
    if args.idle_s < 60:
        ap.error("--idle-s must be at least 60")
    if not 1 <= args.start <= args.cap <= 32:
        ap.error("need 1 <= --start <= --cap <= 32 (the assignment has 32 channels)")
    kind = {"limit": "limit", "loop-check": "loop", "thermal": "thermal"}[args.command]
    args.out = args.out or f"results/{'check' if kind == 'loop' else 'calibration'}/" \
                           f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}_{kind}"

    def on_signal(signum, _frame):
        raise Interrupted(f"signal {signal.Signals(signum).name}")

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGHUP, on_signal)
    try:
        s = Session(args)
        if args.command == "loop-check":
            s.loops = loop_channels()  # before the idle period, so a bad input fails at once
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
