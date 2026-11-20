#!/usr/bin/env python3
"""M5's measurement sessions (prereg/M5.md), run on the board: B and C with the registered bench_main, and
the installed package's service rkzc-detect as P and P4.

  sweep   M5 §4's sweep, run kind m5: 4 arms × 3 repetitions at H1's point, in the order
            1: B, C, P, P4     2: P4, P, C, B     3: C, B, P4, P
          It spans as many sessions as it needs, each in lib/measure/results/<UTC>_m5: a session first
          reads the earlier ones' runs.tsv and redoes the repetitions they left incomplete, whole and in
          their order (§4). It starts only once freeze-m5 has its row in docs/notes/env.md, with the
          protocol's settings and a clean tree.
  check   the checks of M5 §8 step 3, run kind check, in lib/measure/results/<UTC>_m5_check: one run of
          each arm named by --arms, in that order. --duration 10 gives step 3's short runs. --cpu-check
          also reads bench_main's CPU time from /proc during every C run and compares it with bench_main's
          own value (step 3, "The tool against bench_main").

What comes from the registered tools/stream_session.py, imported and used unchanged (M5 §8 step 2): the
session's start (inventory by M3 §4's pinned keys, fixed clocks, environment, the stream server, the idle
period with the soc-thermal baseline), the cooldown gate, the clock exchange with the stream PC, the idle
baseline of system CPU, every B and C run with its judgement by PROTOCOL §9.3 rules 1 to 4, the repeat of
an invalid run, runs.tsv and started.tsv, the parsing of the server's log with rule 4's events, and the
bookkeeping of repetitions across sessions. For that, this tool adds M5's sweep to the imported table
SWEEPS with M3's kind: its B and C runs are M3's (M3 §1's options, M3 §3's validity, M3 §4's inventory
rule), and they pass freeze-m3's commit as M3's did. No file of the registered paths changes.

What this tool implements itself: the package's checks, the service's run (start, the socket's reader, the
timed reads and one-second samples of M5 §3, stop), its judgement by M5 §4's five rules (the server-log
half follows stream_session.py's judge line by line), M5's header beside every run, and the order of the
arms.

A service run, as M5 §3 has it:
  - the drop-in with CPUAffinity=4-7 is in place for P4 and absent for P, with a daemon-reload after
    every change;
  - systemctl start; t_svc is ExecMainStartTimestampMonotonic. The tool, pinned to cpu0 for the run,
    connects to the socket and reads every line. t_start is CLOCK_MONOTONIC when it has read a frame line
    of every channel; t_w = t_start + warm-up, t_e = t_w + window;
  - once per second from t_start it samples the clocks, the temperatures, the NPU load, the service's
    VmRSS and every thread's CPU time and last CPU. At t_w and t_e it first reads /proc/<MainPID>/stat,
    with the time before and after the read, then /proc/stat, /proc/schedstat, its own CPU time and every
    thread's affinity;
  - it reads on until t_e + 2 s, closes its connection, then stops the service, and keeps the unit's
    journal. No other process is started between t_start and that close.
The lines are stored as received; lib/measure/m5_metrics.py computes §3's values from the stored files.
The latency percentiles need numpy, which the board does not have: lib/measure/m5_summary.py computes
them from the same files.

Usage, on the board as root from the repository, detached from the login session (setsid nohup), with the
package installed, its model fetched, and the service stopped and disabled:
  COOLING=<text> AMBIENT_C=<°C> python3 lib/measure/m5_session.py sweep --deb rkzc_<v>_arm64.deb
  COOLING=... AMBIENT_C=... python3 lib/measure/m5_session.py check --deb FILE [--arms B C P P4]
                                    [--duration S] [--warmup S] [--idle-s S] [--gate-s S] [--cpu-check]
The .deb is the installed package's file: its SHA-256 must be the one lib/measure/package.txt records,
every file it holds must equal the installed one, and /etc/rkzc/detect.toml is written from its shipped
copy with only `sources` changed (M5 §1).
Exit code 0 when the session completed (whatever it found), 1 when it stopped early, 2 when it did not start.
"""
import argparse
import csv
import datetime as dt
import hashlib
import os
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(HERE))
import stream_session as ss  # noqa: E402  (the registered session tool, unchanged)
import m5_metrics as mm  # noqa: E402

ARMS = ("B", "C", "P", "P4")
SERVICE_ARMS = ("P", "P4")
ORDER = {1: ("B", "C", "P", "P4"), 2: ("P4", "P", "C", "B"), 3: ("C", "B", "P4", "P")}  # M5 §4
CHANNELS, CONDITION, PHASE = 8, "main", "m5"
# M5's sweep in the registered tool's table, with M3's kind: see the text above.
ss.SWEEPS[PHASE] = ((CHANNELS,), CONDITION, ARMS, "m3")

PREREG_M5_COMMIT = "9ac3ec137504b65751c1232ad9add69f3d15f091"  # docs/notes/env.md
RESULTS = Path("lib", "measure", "results")
PACKAGE_TXT = Path("lib", "measure", "package.txt")  # version and SHA-256 of the .deb (M5 §8 step 2)
REGISTERED = ("src", "tools", "scripts", "tests", "CMakeLists.txt")  # M5 §1: equal to freeze-m3's
UNIT = "rkzc-detect.service"
DROPIN = Path("/etc/systemd/system/rkzc-detect.service.d/affinity.conf")
DROPIN_TEXT = "[Service]\nCPUAffinity=4-7\n"  # M5 §1, P4
CONFIG = Path("/etc/rkzc/detect.toml")
SOCKET = "/run/rkzc/detect.sock"
MODEL = Path("/var/lib/rkzc/yolov8n_i8.rknn")
MODEL_SHA256 = ss.M4_MODELS["C8"][1]  # 04a76611…, M1's model
SOURCES = [f"rtsp://{ss.SERVER}:{ss.RTSP_PORT}/ch{n}" for n in range(1, CHANNELS + 1)]
LINGER_S = 2  # §3: the tool reads until t_e + 2 s
THERMAL = Path("/sys/class/thermal")
FREQ_FILES = {"policy0_khz": "/sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq",
              "policy4_khz": "/sys/devices/system/cpu/cpufreq/policy4/scaling_cur_freq",
              "policy6_khz": "/sys/devices/system/cpu/cpufreq/policy6/scaling_cur_freq",
              "dmc_hz": "/sys/class/devfreq/dmc/cur_freq",
              "npu_hz": "/sys/class/devfreq/fdab0000.npu/cur_freq"}  # the files bench_main samples
NPU_LOAD = "/sys/kernel/debug/rknpu/load"
TICK_HZ = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
GIT = ["git", "-c", "safe.directory=*"]  # root, in the user's repository


def mono_ns():
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC)


def utc(ns):
    return dt.datetime.fromtimestamp(ns / 1e9, dt.timezone.utc)


def stamp(t, ms=False):
    return f"{t:%Y-%m-%dT%H:%M:%S}.{t.microsecond // 1000:03d}Z" if ms else f"{t:%Y-%m-%dT%H:%M:%SZ}"


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def out_of(cmd, timeout=60):
    """(exit code, stdout, stderr) of a command."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, "", str(e)


def must(cmd, timeout=60):
    code, out, err = out_of(cmd, timeout)
    if code != 0:
        raise ss.Stop(f"`{' '.join(cmd)[:80]}` failed: {(err or out).strip()[-300:] or code}")
    return out


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def processes(names):
    """[(pid, name)] of the running processes with one of these names."""
    found = []
    for p in Path("/proc").iterdir():
        if p.name.isdigit():
            try:
                name = (p / "comm").read_text().strip()
            except OSError:
                continue
            if name in names:
                found.append((int(p.name), name))
    return found


def unit_show(*properties):
    """{property: value} of the unit, times in µs of UTC where systemd formats them."""
    out = must(["systemctl", "show", UNIT, "--timestamp=us+utc", "-p", ",".join(properties)])
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def shipped_config(deb):
    """The package's own /etc/rkzc/detect.toml, from the .deb."""
    p = subprocess.run(f"dpkg-deb --fsys-tarfile '{deb}' | tar -xO ./etc/rkzc/detect.toml", shell=True,
                       capture_output=True, text=True)
    if p.returncode != 0 or "sources" not in p.stdout:
        raise ss.Stop(f"cannot read etc/rkzc/detect.toml from {deb}: {p.stderr.strip()[-200:]}")
    return p.stdout


def config_for(shipped, sources):
    """M5 §1, "as installed": the shipped configuration with only the value of `sources` changed."""
    block = "sources = [\n" + "".join(f'    "{s}",\n' for s in sources) + "]"
    text, n = re.subn(r"^sources = \[\n(?:.*\n)*?\]", lambda _m: block, shipped, count=1, flags=re.M)
    if n != 1:
        raise ss.Stop("the shipped detect.toml has no `sources = [ ... ]` block to replace")
    return text


def package_record(text):
    """(version, SHA-256) of lib/measure/package.txt."""
    kv = ss.keyed(text)
    if not re.fullmatch(r"[0-9a-f]{64}", kv.get("sha256", "")) or not kv.get("version"):
        raise ss.Stop(f"{PACKAGE_TXT} needs `version: ` and `sha256: ` lines")
    return kv["version"], kv["sha256"]


def latest_amendment():
    found = re.findall(r"^## (A\d+) ", (ROOT / "prereg" / "AMENDMENTS.md").read_text(encoding="utf-8"), re.M)
    return found[-1] if found else "none"


def tool_sha256():
    """One SHA-256 over the tool's files: every .py of lib/measure/ and package.txt, by name. The sweep's
    sessions are those of one tool and one package; a commit that changes neither (a record, a document)
    leaves this value as it was, where the commit's own hash would not."""
    h = hashlib.sha256()
    for p in sorted(list(HERE.glob("*.py")) + [HERE / "package.txt"]):
        data = p.read_bytes().replace(b"\r\n", b"\n") if p.exists() else b""
        h.update(f"{p.name}\0{len(data)}\0".encode() + data)
    return h.hexdigest()


def session_key(d):
    """(the tool's SHA-256, the package's SHA-256) a session ran with, from its record, which it writes
    before its idle period, or from one of its runs'."""
    for f in [Path(d) / "m5_session.csv"] + sorted(Path(d).glob("*/m5_run.csv")):
        if f.exists():
            h = ss.read_header(f)
            return h.get("tool_sha256"), h.get("package_sha256")
    return None, None


def m5_sessions(key, current=None):
    """The sweep's sessions that ran this tool and package: lib/measure/results/<UTC>_m5, by name
    (UTC), not the current one. After a change to either, the sweep is rerun whole (M5 §5, §9); the sessions
    of the earlier ones stay where they are and are not read."""
    return sorted(d for d in RESULTS.glob("*_m5") if d.is_dir() and re.fullmatch(ss.SESSION_NAME + "m5", d.name)
                  and (current is None or d.resolve() != Path(current).resolve()) and session_key(d) == key)


def m5_header(start, run_kind, freeze_m5, tool_commit, package_version, package_sha256):
    """M5's header (§8 step 2), the fields every record has."""
    return {"start_time_utc": stamp(start), "run_kind": run_kind, "prereg_m5_commit": PREREG_M5_COMMIT,
            "freeze_m5_commit": freeze_m5, "tool_commit": tool_commit, "tool_sha256": tool_sha256(),
            "amendment": latest_amendment(),
            "package_version": package_version, "package_sha256": package_sha256}


def tool_state():
    """(the tool's commit, freeze-m5's commit or "none"). Stop unless the working tree is that commit's, the
    build is of the registered code, and that code equals freeze-m3's (M5 §1, §8 step 2)."""
    commit = must(GIT + ["rev-parse", "HEAD"]).strip()
    dirty = [l for l in must(GIT + ["status", "--porcelain"]).splitlines()
             if not re.search(r"\s(lib/measure/results/|results/|build/|models/)", l)]
    if dirty:
        raise ss.Stop("the working tree is not clean: " + "; ".join(l.strip() for l in dirty[:5])
                      + ": the tool is the tool of a commit (M5 §8 step 2)")
    rows = ss.registration_rows((ROOT / ss.ENV_MD).read_text(encoding="utf-8"))
    if rows.get("prereg-m5", ("",))[0] != PREREG_M5_COMMIT:
        raise ss.Stop(f"{ss.ENV_MD} has no row for prereg-m5 with commit {PREREG_M5_COMMIT}")
    freeze_m3 = rows.get("freeze-m3", ("none",))[0]
    for what, commit_of in (("freeze-m3", freeze_m3), ("the build", ss.build_commit())):
        code, _, err = out_of(GIT + ["diff", "--quiet", commit_of, "HEAD", "--", *REGISTERED])
        if code != 0:
            raise ss.Stop(f"the registered paths ({', '.join(REGISTERED)}) of this commit are not those of {what} "
                          f"({commit_of}) (M5 §1): {err.strip()[-200:]}")
    return commit, rows.get("freeze-m5", ("none", None))[0]


def recorded_package(deb):
    """(version, SHA-256) of lib/measure/package.txt. Stop unless deb is that file."""
    try:
        version, want = package_record(read(PACKAGE_TXT))
    except OSError as e:
        raise ss.Stop(f"cannot read {PACKAGE_TXT}: {e}") from None
    got = sha256(deb)
    if got != want:
        raise ss.Stop(f"{deb} has SHA-256 {got}; {PACKAGE_TXT} records {want}")
    return version, want


def installed_as_packaged(deb, version):
    """How many files of the .deb equal the installed ones. Stop unless the package is installed in this
    version and every file but the configuration is the package's."""
    installed = must(["dpkg-query", "-W", "-f", "${Version} ${db:Status-Abbrev}", "rkzc"]).strip()
    if installed.split() != [version, "ii"]:
        raise ss.Stop(f"the installed rkzc is `{installed}`, not {version} installed")
    with tempfile.TemporaryDirectory(prefix="m5-deb-") as tmp:
        must(["dpkg-deb", "-x", str(deb), tmp])
        same, differ = 0, []
        for p in sorted(Path(tmp).rglob("*")):
            rel = p.relative_to(tmp)
            there = Path("/") / rel
            if p.is_symlink():
                ok = there.is_symlink() and os.readlink(p) == os.readlink(there)
            elif p.is_file():
                ok = rel == CONFIG.relative_to("/") or (there.is_file() and not there.is_symlink()
                                                         and p.read_bytes() == there.read_bytes())
            else:
                continue
            same += ok
            if not ok:
                differ.append(str(there))
    if differ:
        raise ss.Stop(f"{len(differ)} installed files are not the package's: " + ", ".join(differ[:5]))
    if not MODEL.is_file() or sha256(MODEL) != MODEL_SHA256:
        raise ss.Stop(f"{MODEL} is not M1's model ({MODEL_SHA256}); rkzc-fetch-model yolov8n fetches it")
    return same


def service_state():
    return [out_of(["systemctl", c, UNIT])[1].strip() for c in ("is-active", "is-enabled")]


def plan():
    return [(CHANNELS, r, ORDER[r]) for r in sorted(ORDER)]


class CpuWatch(threading.Thread):
    """M5 §8 step 3, "The tool against bench_main": once per second, bench_main's utime + stime from /proc
    with the CLOCK_REALTIME time of the read. On cpu0, where bench_main's own metrics thread is."""

    def __init__(self):
        super().__init__(daemon=True)
        self.rows, self.done = [], threading.Event()

    def run(self):
        try:
            os.sched_setaffinity(threading.get_native_id(), {0})
        except OSError:
            pass
        pid = None
        while not self.done.wait(1.0):
            if pid is None:
                found = processes({"bench_main"})
                pid = found[0][0] if found else None
            if pid is None:
                continue
            try:
                before = time.time_ns()
                text = read(f"/proc/{pid}/stat")
                after = time.time_ns()
            except OSError:
                continue  # it has ended
            self.rows.append(((before + after) // 2, mm.stat_fields(text)[1]))

    def stop(self):
        self.done.set()
        self.join(5)


def cpu_check_rows(rows, summary):
    """The check's result: bench_main's process CPU as the tool reads it from outside, interpolated at
    bench_main's own window, against bench_main's value. Passes within 0.1 s."""
    edges = [int(ss.parse_utc(summary[k]).timestamp() * 1e9) for k in ("window_start_utc", "window_end_utc")]
    at = [mm.interpolate(rows, t) for t in edges]
    if None in at:
        return [("cpu_check", "failed: the samples do not cover bench_main's window")]
    mine, its = (at[1] - at[0]) / TICK_HZ, float(summary["process_cpu_s"])
    return [("cpu_check_tool_s", f"{mine:.3f}"), ("cpu_check_bench_main_s", f"{its:.3f}"),
            ("cpu_check_difference_s", f"{mine - its:+.3f}"), ("cpu_check_samples", str(len(rows))),
            ("cpu_check", "passed" if abs(mine - its) <= 0.1 else "failed: more than 0.1 s apart")]


class M5Session(ss.Session):
    def __init__(self, args):
        super().__init__(args)
        self.package = {}
        self.pinned = False
        self.recorded = False  # the session's final record is written

    # -- the package, before the idle period (M5 §4)

    def check_tree(self):
        """The tool's commit, and that the build's code is the registered one."""
        a = self.args
        self.tool_commit, self.freeze_m5 = tool_state()
        if a.command == "sweep" and self.freeze_m5 == "none":
            raise ss.Stop(f"freeze-m5 comes before M5's runs, and {ss.ENV_MD} has no row for it (M5 §8 step 4)")

    def check_package(self):
        """M5 §1 and §4: the installed package is the recorded file, the model is M1's, the configuration is
        the shipped one with M5's sources, and the service is stopped and disabled with no drop-in."""
        deb = Path(self.args.deb)
        version, want = recorded_package(deb)
        same = installed_as_packaged(deb, version)
        state = service_state()
        if state != ["inactive", "disabled"]:
            raise ss.Stop(f"the service is {state[0]} and {state[1]}; M5 §4 has it stopped and disabled")
        self.no_bench_process()
        shipped = shipped_config(deb)
        config = config_for(shipped, SOURCES)
        CONFIG.write_text(config, encoding="utf-8")
        (self.out / "detect.toml").write_text(config, encoding="utf-8")
        (self.out / "detect.toml.shipped").write_text(shipped, encoding="utf-8")
        if DROPIN.exists():
            self.say(f"removing {DROPIN}, left by an earlier session")
        self.set_dropin(False)
        libs = sorted(p for p in Path("/opt/rkzc/lib").iterdir() if p.is_file() and not p.is_symlink())
        self.package = {"package_version": version, "package_sha256": want, "package_files_identical": str(same),
                        "model_sha256": MODEL_SHA256,
                        **{f"lib {p}": sha256(p) for p in libs}}
        (self.out / "package.txt").write_text("".join(f"{k}: {v}\n" for k, v in self.package.items()))
        self.say(f"package rkzc {version} ({want[:12]}…): {same} files as packaged, {len(libs)} libraries hashed")

    def set_dropin(self, on):
        """P4's drop-in in place or absent, with a daemon-reload, and checked through systemd."""
        if on:
            DROPIN.parent.mkdir(parents=True, exist_ok=True)
            DROPIN.write_text(DROPIN_TEXT)
        else:
            DROPIN.unlink(missing_ok=True)
            if DROPIN.parent.is_dir() and not any(DROPIN.parent.iterdir()):
                DROPIN.parent.rmdir()
        must(["systemctl", "daemon-reload"])
        show = unit_show("DropInPaths", "CPUAffinity")
        got = (set(show.get("DropInPaths", "").split()), show.get("CPUAffinity", ""))
        if got != (({str(DROPIN)}, "4-7") if on else (set(), "")):  # nothing else differs (M5 §1)
            raise ss.Stop(f"the unit has drop-ins `{show.get('DropInPaths', '')}` and CPUAffinity `{got[1]}`; "
                          + ("P4 has this tool's drop-in alone, and 4-7" if on else "P has neither"))

    def no_bench_process(self):
        """M5 §4, the gate: no bench_main and no rkzc-detect process exists."""
        found = processes({"bench_main", "rkzc-detect"})
        if found:
            raise ss.Stop("a bench process exists before a run: " + ", ".join(f"{n} ({p})" for p, n in found))

    def gate(self):
        """The registered gate, then M5 §4's check, right before the run starts."""
        temp = super().gate()
        self.no_bench_process()
        return temp

    def end(self):
        # A second signal must not cut the cleanup short.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        try:
            self.stop_service()
            self.set_dropin(False)
        except Exception as e:  # keep going: the stream server must still be stopped
            self.say(f"could not put the service back: {e}")
        super().end()

    # -- runs

    def header(self, start, arm, repetition, attempt):
        return {**m5_header(start, self.run_kind, self.freeze_m5, self.tool_commit,
                            self.package.get("package_version", "unknown"),
                            self.package.get("package_sha256", "unknown")),
                "arm": arm, "repetition": repetition, "attempt": attempt, "session": self.out.name, "cooling": os.environ["COOLING"], "ambient_c": os.environ["AMBIENT_C"],
                "warmup_s": self.args.warmup, "duration_s": self.args.duration}

    def attempt(self, name, phase, variant, channels, condition, run_kind, repetition, source, attempt_no=1):
        """One run of an arm, judged; stream_session.py's run() calls it. B and C go to the registered
        attempt(); P and P4 are the service's. Either way M5's header is written beside the run."""
        rows = []
        if variant in SERVICE_ARMS:
            o, run_dir, temp, start, end, rows = self.service_attempt(name, variant, repetition, attempt_no)
        else:
            watch = CpuWatch() if self.args.cpu_check and variant == "C" else None
            if watch:
                watch.start()
            try:
                o, run_dir, temp, start, end = super().attempt(name, phase, variant, channels, condition, run_kind,
                                                               repetition, source, attempt_no)
            finally:
                if watch:
                    watch.stop()
            if watch and (run_dir / "summary.csv").exists():
                (run_dir / "cpu_check.tsv").write_text("realtime_ns\tticks\n" + "".join(
                    f"{t}\t{v}\n" for t, v in watch.rows))
                rows = cpu_check_rows(watch.rows, ss.summary_values(run_dir / "summary.csv")[1])
                self.say(f"{name}: " + ", ".join(f"{k} {v}" for k, v in rows))
        run_dir.mkdir(exist_ok=True)
        rows = [("valid", str(int(not o.reasons))), ("reasons", "; ".join(o.reasons) or "none"),
                ("warnings", "; ".join(o.warnings) or "none"), ("gate_soc_mc", str(temp))] + rows
        mm.write_keyed(run_dir / "m5_run.csv", self.header(start, variant, repetition, attempt_no), rows)
        return o, run_dir, temp, start, end

    def stop_service(self):
        if out_of(["systemctl", "is-active", UNIT])[1].strip() not in ("inactive", "failed"):
            out_of(["systemctl", "stop", UNIT], timeout=90)
        out_of(["systemctl", "reset-failed", UNIT])

    def service_attempt(self, name, arm, repetition, attempt_no):
        """One run of the service. Returns (Outcome, run directory, gate temperature, start, end, rows)."""
        o = ss.Outcome()
        temp = self.gate()
        if not self.server_alive():
            raise ss.Stop("the stream server is down (external failure, PROTOCOL §9.3)")
        if self.sh(["tools/collect_env.sh"], self.out / f"{name}.env.txt") != 0:
            raise ss.Stop("collect_env.sh failed")
        console_before = self.console_size()
        run_dir = self.out / name
        run_dir.mkdir()
        self.set_dropin(arm == "P4")
        start = dt.datetime.now(dt.timezone.utc)
        self.started.write("\t".join(map(str, [self.seq, name, PHASE, arm, CHANNELS, CONDITION, repetition,
                                               attempt_no, stamp(start)])) + "\n")
        ss.durable(self.started)
        self.say(f"{name}: {arm}, {CHANNELS} ch, {CONDITION}")
        marks = {}
        try:
            o.code = self.measure(run_dir, o, marks)
        except ss.Interrupted as e:
            o.code = "interrupted"
            o.reasons.append(f"stopped by the operator ({e})")
            o.external = "stopped by the operator"
        except ss.Stop as e:
            o.code = f"failed: {e}"
            o.reasons.append(f"the tool did not complete the run: {e} (rule 2)")
        except Exception as e:  # the tool's own crash is an invalid run (§4 rule 2), and the session goes on
            o.code = f"failed: {e!r}"
            o.reasons.append(f"the tool did not complete the run: {e!r} (rule 2)")
        finally:
            if self.pinned:
                os.sched_setaffinity(0, self.all_cpus)
                self.pinned = False
            self.stop_service()
            self.last_end = time.monotonic()
            self.set_dropin(False)
        end = dt.datetime.now(dt.timezone.utc)
        rows = []
        if o.code != "interrupted":
            try:
                rows = self.judge_service(o, run_dir, arm, marks, start, end, console_before)
            except ss.Interrupted as e:
                o.reasons.append(f"stopped by the operator before the run was judged ({e})")
                o.external = "stopped by the operator"
        return o, run_dir, temp, start, end, rows

    def measure(self, run_dir, o, marks):
        """Starts the service, reads and samples as M5 §3 says, stops it, and writes the run's files. Returns
        0 when the run went to its end; otherwise a text, with the reason in o."""
        a = self.args
        self.all_cpus = os.sched_getaffinity(0)
        os.sched_setaffinity(0, {0})  # §3: the tool is pinned to cpu0. Only for this run: bench_main inherits.
        self.pinned = True
        zones = sorted(((z / "type").read_text().strip(), z / "temp") for z in THERMAL.glob("thermal_zone*"))
        code, _, err = out_of(["systemctl", "start", UNIT], timeout=90)
        if code != 0:
            o.reasons.append(f"the service did not start: {err.strip()[-200:]} (rule 2)")
            (run_dir / "unit.txt").write_text(f"start failed: {err.strip()[-500:]}\n")
            self.stop_service_recorded(run_dir, marks)
            return "failed: systemctl start"
        show = unit_show("MainPID", "ExecMainStartTimestampMonotonic", "ExecMainStartTimestamp", "NRestarts",
                         "InvocationID", "DropInPaths", "CPUAffinity")
        (run_dir / "unit.txt").write_text("".join(f"start {k}={v}\n" for k, v in show.items()))
        pid, t_svc = int(show["MainPID"]), int(show["ExecMainStartTimestampMonotonic"]) * 1000
        marks.update(t_svc_ns=t_svc, main_pid=pid, svc_start=show.get("ExecMainStartTimestamp", ""),
                     restarts_at_start=show.get("NRestarts", ""))
        task = Path(f"/proc/{pid}/task")
        chunks, failure = [], None
        samples, thread_rows = [], []

        def stat_of(path):
            try:
                return read(path)
            except OSError:
                return None

        def number_of(path):
            try:
                return int(read(path).strip())
            except (OSError, ValueError):
                return -1  # m5_metrics takes a clock of -1 as below its fixed value (rule 1)

        def sample(k):
            now = mono_ns()
            try:
                tids = os.listdir(task)
            except OSError:  # the process has gone; the loop's own check says so
                tids = []
            for tid in tids:
                text = stat_of(task / tid / "stat")
                if text:
                    name, ticks, cpu = mm.stat_fields(text)
                    thread_rows.append((k, int(tid), name, ticks, cpu))
            status = stat_of(f"/proc/{pid}/status") or ""
            rss = re.search(r"^VmRSS:\s+(\d+)", status, re.M)
            load = re.findall(r"Core(\d+):\s*(\d+)%", stat_of(NPU_LOAD) or "")
            samples.append({"k": k, "mono_ns": now,
                            **{f"freq_{d}": number_of(f) for d, f in FREQ_FILES.items()},
                            **{f"temp_mc_{z}": number_of(f) for z, f in zones},
                            **{f"npu_core{c}_pct": v for c, v in load}, "rss_kb": rss.group(1) if rss else -1})

        def timed_read(which, due):
            before = mono_ns()
            text = stat_of(f"/proc/{pid}/stat")
            after, real = mono_ns(), time.time_ns()
            if text is None or mm.stat_fields(text)[0] != "rkzc-detect":
                return False
            marks.update({f"read_{which}_due_ns": due, f"read_{which}_before_ns": before,
                          f"read_{which}_after_ns": after, f"realtime_{which}_ns": real,
                          f"proc_ticks_{which}": mm.stat_fields(text)[1],
                          f"tool_ticks_{which}": mm.stat_fields(read("/proc/self/stat"))[1]})
            (run_dir / f"proc_stat_{which}.txt").write_text(read("/proc/stat"))
            sched = ss.read_optional("/proc/schedstat")
            if sched is not None:
                (run_dir / f"schedstat_{which}.txt").write_text(sched)
            rows = []
            for tid in os.listdir(task):
                text = stat_of(task / tid / "stat")
                try:
                    cpus = sorted(os.sched_getaffinity(int(tid)))
                except OSError:
                    continue
                if text:
                    rows.append((tid, mm.stat_fields(text)[0], ",".join(map(str, cpus))))
            (run_dir / f"affinity_{which}.tsv").write_text("tid\tname\tcpus\n" + "".join("\t".join(r) + "\n" for r in rows))
            return True

        sock = None
        try:
            # The socket appears once the model is loaded; every channel has a line by t_svc + 60 s (rule 2).
            first_by = t_svc + mm.FIRST_LINES_S * 10**9
            while sock is None and mono_ns() < first_by:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    s.connect(SOCKET)
                    sock = s
                except OSError:
                    s.close()
                    if not task.exists():
                        failure = "the service's main process exited before its socket opened"
                        break
                    time.sleep(0.02)
            if sock is None:
                failure = failure or f"no socket by t_svc + {mm.FIRST_LINES_S} s"
            t_start, seen, pending = None, set(), b""
            while failure is None and t_start is None:
                if mono_ns() >= first_by:
                    failure = f"some channel had no frame line by t_svc + {mm.FIRST_LINES_S} s"
                    break
                if not select.select([sock], [], [], 0.2)[0]:
                    if not task.exists():
                        failure = "the service's main process exited during its start"
                    continue
                data = sock.recv(1 << 16)
                if not data:
                    failure = "the service closed the tool's connection"
                    break
                chunks.append(data)
                pending += data
                *whole, pending = pending.split(b"\n")
                for line in whole:
                    m = re.match(rb'\{"channel":(\d+),', line)
                    if m:
                        seen.add(int(m.group(1)))
                if seen >= set(range(CHANNELS)):
                    t_start = mono_ns()
            if failure is None:
                t_w = t_start + a.warmup * 10**9
                t_e = t_w + a.duration * 10**9
                marks.update(t_start_ns=t_start, t_w_ns=t_w, t_e_ns=t_e, warmup_k=a.warmup,
                             end_k=a.warmup + a.duration, realtime_start_ns=time.time_ns() - (mono_ns() - t_start))
                k, last_k = 0, a.warmup + a.duration
                close_at = t_e + LINGER_S * 10**9
                while failure is None:
                    now = mono_ns()
                    due = t_start + k * 10**9 if k <= last_k else close_at
                    if now >= due:
                        if k > last_k:
                            break
                        # The timed read first, then this second's samples.
                        if k == a.warmup and not timed_read("w", due) or k == last_k and not timed_read("e", due):
                            failure = "the service's main process exited"
                            break
                        if not task.exists():
                            failure = "the service's main process exited"
                            break
                        sample(k)
                        k += 1
                        continue
                    if select.select([sock], [], [], (due - now) / 1e9)[0]:
                        data = sock.recv(1 << 16)
                        if not data:
                            failure = "the service closed the tool's connection"
                            break
                        chunks.append(data)
        finally:
            if sock is not None:
                sock.close()  # before the stop (§3)
            (run_dir / "lines.jsonl").write_bytes(b"".join(chunks))
            keys = list(dict.fromkeys(key for s in samples for key in s))
            with open(run_dir / "samples.csv", "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, keys, restval="")
                w.writeheader()
                w.writerows(samples)
            with open(run_dir / "thread_samples.csv", "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["k", "tid", "name", "ticks", "cpu"])
                w.writerows(thread_rows)
            try:
                self.stop_service_recorded(run_dir, marks)
            finally:
                with open(run_dir / "marks.csv", "w", encoding="utf-8", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["key", "value"])
                    w.writerows(marks.items())
        if failure:
            o.reasons.append(f"{failure} (rule 2)")
            return f"failed: {failure}"
        return 0

    def stop_service_recorded(self, run_dir, marks):
        """Stops the service and keeps what systemd and the journal say of the run."""
        before = unit_show("MainPID", "NRestarts", "ActiveState")
        out_of(["systemctl", "stop", UNIT], timeout=90)
        after = unit_show("NRestarts", "Result", "ExecMainStatus", "ExecMainCode", "ActiveState",
                          "ExecMainExitTimestamp")
        with open(run_dir / "unit.txt", "a", encoding="utf-8") as f:
            f.write("".join(f"before_stop {k}={v}\n" for k, v in before.items())
                    + "".join(f"after_stop {k}={v}\n" for k, v in after.items()))
        marks.update(main_pid_before_stop=before.get("MainPID", ""), active_before_stop=before.get("ActiveState", ""),
                     restarts_after_stop=after.get("NRestarts", ""), result=after.get("Result", ""),
                     exec_main_status=after.get("ExecMainStatus", ""))
        out_of(["journalctl", "--sync"])
        since = int(marks["t_svc_ns"] / 1e9 - (time.clock_gettime(time.CLOCK_MONOTONIC) - time.time())) - 2 \
            if "t_svc_ns" in marks else int(time.time()) - 600
        for _ in range(6):  # the program's last lines can reach the journal a moment after its exit
            code, out, err = out_of(["journalctl", "-u", UNIT, "-o", "json", "--no-pager", "--since", f"@{since}"],
                                    timeout=120)
            if code != 0 or mm.stop_log(mm.journal_entries(out))[1] is not None:
                break
            time.sleep(0.5)
            out_of(["journalctl", "--sync"])
        (run_dir / "journal.jsonl").write_text(out if code == 0 else "", encoding="utf-8")
        if code != 0:
            raise ss.Stop(f"journalctl: {err.strip()[-200:]}")

    def judge_service(self, o, run_dir, arm, marks, start, end, console_before):
        """M5 §4's rules for a service run. The server's side is stream_session.py's judge, line for line;
        rule 4 runs from t_svc to t_e."""
        alive = self.server_alive()
        lines = None
        try:
            offset, _ = self.clock_offset()
            o.offset_ms = f"{offset * 1000:.1f}"
            problem = ss.clock_problem(offset)
            if problem:
                o.reasons.append(problem + " (external failure)")
                o.external = problem
            lines = ss.parse_server_log(self.pc(f"cat {self.server_dir}/mediamtx.log"))
            lo, hi = start - dt.timedelta(seconds=2), end + dt.timedelta(seconds=2)
            (run_dir / "server.log").write_text("".join(raw + "\n" for t, _, _, raw in lines if lo <= t <= hi))
            added = self.pc(f"tail -c +{console_before + 1} {self.server_dir}/console.log")
            published = [l for l in added.splitlines() if not ss.LOG_LINE.match(l)]
            if published:
                (run_dir / "server_console.txt").write_text("\n".join(published) + "\n")
                o.warnings.append(f"{len(published)} lines of publisher output")
        except ss.Interrupted:
            raise
        except ss.Stop as e:
            o.reasons.append(f"server log unavailable: {e} (external failure)")
            o.external = o.external or "the server's log could not be read"
        if not alive:
            o.reasons.append("stream server down after the run (external failure)")
            o.external = o.external or "the stream server is down"

        rows = []
        if "t_svc_ns" not in marks:
            return rows
        # Rule 2, as systemd saw the run.
        if str(marks.get("main_pid_before_stop")) != str(marks["main_pid"]) or marks.get("active_before_stop") != "active":
            o.reasons.append(f"the service's main process was {marks.get('main_pid_before_stop')} and "
                             f"{marks.get('active_before_stop')} before its stop, not {marks['main_pid']} and active "
                             "(rule 2)")
        if marks.get("restarts_after_stop") != marks.get("restarts_at_start"):
            o.reasons.append("systemd restarted the service (rule 2)")
        if marks.get("result") != "success":
            o.warnings.append(f"the service's result after its stop: {marks.get('result')}, status "
                              f"{marks.get('exec_main_status')}")
        try:
            more, reasons = mm.evaluate(run_dir, arm, self.fixed, TICK_HZ)
            rows += more
            o.reasons += reasons
            if "realtime_e_ns" in marks:
                rows += [("warmup_start_utc", stamp(utc(marks["realtime_start_ns"]), True)),
                         ("window_start_utc", stamp(utc(marks["realtime_w_ns"]), True)),
                         ("window_end_utc", stamp(utc(marks["realtime_e_ns"]), True))]
                ch = mm.channel_rows(run_dir)
                with open(run_dir / "channels.csv", "w", encoding="utf-8", newline="") as f:
                    w = csv.DictWriter(f, list(ch[0]))
                    w.writeheader()
                    w.writerows(ch)
            samples = mm.read_rows(run_dir / "samples.csv")
            o.trip_samples = sum(1 for s in samples for zone, trip in self.trips.items()
                                 if s.get(f"temp_mc_{zone}", "") != "" and int(s[f"temp_mc_{zone}"]) >= trip)
        except Exception as e:  # an invalid run (rule 2: the tool does not complete it), not the session's end
            o.reasons.append(f"the run's files cannot be evaluated: {e!r} (rule 2)")
        # Rule 4, from t_svc to t_e (or to the run's end, if it did not reach t_e).
        try:
            t_svc_utc = dt.datetime.strptime(marks["svc_start"], "%a %Y-%m-%d %H:%M:%S.%f UTC").replace(
                tzinfo=dt.timezone.utc)
        except ValueError:
            t_svc_utc = start
            o.warnings.append("ExecMainStartTimestamp could not be read; rule 4 runs from the run's start")
        rows.append(("svc_start_utc", stamp(t_svc_utc, True)))
        if lines is not None:
            window_end = utc(marks["realtime_e_ns"]) if "realtime_e_ns" in marks else end
            events, warnings = ss.server_events(lines, start, t_svc_utc, window_end)
            o.reasons += [f"{e} (rule 4)" for e in events]
            o.warnings += warnings
        return rows

    # -- the session's record

    def key(self):
        return tool_sha256(), self.package["package_sha256"]

    def write_session(self, rows):
        a = self.args
        off = [f"{k} {getattr(a, k)} (protocol {v})" for k, v in ss.PROTOCOL.items() if getattr(a, k) != v]
        if a.command == "check":
            off.append(f"check: arms {' '.join(a.arms)}" + (", with the CPU check" if a.cpu_check else ""))
        header = self.header(self.session_start, "all", "all", "all")
        del header["arm"], header["repetition"], header["attempt"]
        rows = rows + [("invalid_x2_runs", " ".join(self.invalid_x2) or "none"),
                       ("protocol_settings", "yes" if not off else "no: " + "; ".join(off)),
                       ("freeze_m3_commit", self.freeze_m3), ("build_commit", getattr(self, "code_commit", "unknown")),
                       *[(k, v) for k, v in self.package.items() if k.startswith("package_")]]
        mm.write_keyed(self.out / "m5_session.csv", header, rows)
        self.say("session record: " + "; ".join(f"{k} {v}" for k, v in rows[:4]))


def cmd_sweep(s):
    earlier = m5_sessions(s.key(), s.out)
    complete, pending = ss.sweep_state(ss.load_records(earlier, (PHASE,)), ARMS)
    todo = [p for p in plan() if (p[0], p[1]) in pending] + \
           [p for p in plan() if (p[0], p[1]) not in pending and (p[0], p[1]) not in complete]
    s.say(f"m5: {len(complete)} of {len(plan())} repetitions complete in earlier sessions, {len(pending)} to redo, "
          f"{len(todo)} to run")
    done_here, status = [], "stopped"
    try:
        for channels, repetition, order in todo:
            first, replaced = pending.get((channels, repetition), ({}, []))
            for arm in order:
                s.run(PHASE, arm, channels, CONDITION, "check", repetition, first_attempt=first.get(arm, 1),
                      replaces=" ".join(replaced))
            done_here.append(f"r{repetition}")
        status = "completed"
    finally:
        complete, _ = ss.sweep_state(ss.load_records(earlier + [s.out], (PHASE,)), ARMS)
        left = [f"r{r}" for c, r, _ in plan() if (c, r) not in complete]
        s.write_session([("session_status", status), ("earlier_sessions", " ".join(d.name for d in earlier) or "none"),
                         ("repetitions_run_here", " ".join(done_here) or "none"),
                         ("repetitions_left", " ".join(left) or "none")])
        s.recorded = True


def cmd_check(s):
    status = "stopped"
    try:
        for arm in s.args.arms:
            s.run(PHASE, arm, CHANNELS, CONDITION, "check", 1)
        status = "completed"
    finally:
        s.write_session([("session_status", status), ("arms", " ".join(s.args.arms))])
        s.recorded = True


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["sweep", "check"])
    ap.add_argument("--deb", required=True, help="the installed package's .deb")
    ap.add_argument("--arms", nargs="+", choices=ARMS, default=list(ARMS), help="check: the arms, in run order")
    ap.add_argument("--cpu-check", action="store_true", help="check: the tool against bench_main, on C runs")
    ap.add_argument("--out")
    for k in ("idle_s", "warmup", "duration", "gate_s"):
        ap.add_argument("--" + k.replace("_", "-"), type=int, default=ss.PROTOCOL[k])
    args = ap.parse_args(argv)
    if not Path(args.deb).is_file():
        ap.error(f"no file {args.deb}")
    args.deb = str(Path(args.deb).resolve())
    os.chdir(ROOT)
    protocol = all(getattr(args, k) == ss.PROTOCOL[k] for k in ("idle_s", "warmup", "duration", "gate_s"))
    if args.command == "sweep" and (not protocol or args.out or args.cpu_check or args.arms != list(ARMS)):
        ap.error("a sweep takes the protocol's settings and its own place; use check for anything else")
    if args.idle_s < 60 or args.warmup < 1 or args.duration < 1:
        ap.error("--idle-s must be at least 60, --warmup and --duration at least 1")
    # What stream_session.py's Session reads from its arguments: an M3-kind sweep named m5.
    args.sweep, args.counts, args.calib, args.limit = PHASE, None, None, None
    args.start, args.cap = ss.PROTOCOL["start"], ss.PROTOCOL["cap"]
    now = f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}"
    args.out = args.out or str(RESULTS / (f"{now}_m5" if args.command == "sweep" else f"{now}_m5_check"))
    if args.command == "check" and re.fullmatch(ss.SESSION_NAME + "m5", Path(args.out).name):
        ap.error("a check session is not named <UTC>_m5: the sweep's sessions read those")

    def on_signal(signum, _frame):
        raise ss.Interrupted(f"signal {signal.Signals(signum).name}")

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGHUP, on_signal)
    s = None
    try:
        if os.geteuid() != 0:
            raise ss.Stop("run as root: the service, its drop-in and /proc/schedstat need it")
        s = M5Session(args)
        s.run_kind = "m5" if args.command == "sweep" else "check"
        s.session_start = dt.datetime.now(dt.timezone.utc)
        s.check_tree()
        s.check_package()
        if args.command == "sweep":
            complete, _ = ss.sweep_state(ss.load_records(m5_sessions(s.key(), s.out), (PHASE,)), ARMS)
            if all((c, r) in complete for c, r, _ in plan()):
                raise ss.Stop("the sweep is complete for this tool and package")
        # Now, so that a session that dies still says which tool and package it ran (session_key).
        s.write_session([("session_status", "running, or it died")])
    except (ss.Stop, OSError) as e:
        print(f"m5_session: not starting: {e}", file=sys.stderr)
        if s is not None:  # nothing ran: leave no session behind for the next one to read
            for f in (s.log, s.runs, s.started):
                f.close()
            shutil.rmtree(s.out)
        return 2
    try:
        s.start()
        ss.write_idle_baseline(s, s.run_kind)
        {"sweep": cmd_sweep, "check": cmd_check}[args.command](s)
        status = 0
    except ss.Stop as e:
        s.say(f"stopped: {e}")
        status = 1
    finally:
        try:
            if not s.recorded:
                s.write_session([("session_status", "stopped before its first run")])
        finally:
            s.end()
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
