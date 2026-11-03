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
  sweep        a sweep of §12 (M1-6), run kind comparison or reference, with the method of
               docs/notes/comparison.md: main (B and C at the main set), aux (B and C at the
               auxiliary set) or reference (A at the A set). Channel counts in the order of §9.1's
               seeded shuffle; per count, 3 repetitions, B->C, C->B, B->C. A sweep spans as many
               sessions as it needs: each session reads the earlier sessions' runs.tsv of its sweep,
               first redoes the repetitions they left incomplete (§9.1, §9.3), then goes on.
  h3           the H3 search and confirmation of §10 for B and C (M1-6), run kind comparison, with the
               method of docs/notes/comparison.md. Like a sweep, it spans as many sessions as it needs:
               each session replays the earlier sessions' runs through the same schedule and goes on.

Each session:
  - first records the board's software, build and model (tools/inventory.sh), and stops if they differ
    from docs/notes/inventory_m1.txt, writing inventory.diff, or if that file is not the one AMENDMENTS
    A10 fixes;
  - fixes the clocks (tools/set_perf_mode.sh) and records the environment (tools/collect_env.sh);
  - checks the SHA-256 of the stream PC's mediamtx and ffmpeg against A10, then starts
    stream-server/publish.sh there with the M1 assignment, with its log in UTC and
    under systemd-inhibit so the PC does not sleep, and stops it at the end, keeping its logs; records
    the PC's stream-server commit, the hashes of its configuration and the clock offset to the PC;
  - idles first for the soc-thermal baseline, then puts every run behind the cooldown gate (§9.2), and
    stops if 30 minutes pass without a run (§2); a sweep also takes the idle baseline of system CPU
    (§7.2) over the idle period's last 120 s;
  - records the environment before every run (§9.1), and the server's log lines and publisher output
    with every run (§3);
  - judges every run by §9.3 rules 1 to 4 where they apply, and repeats an invalid run once.

Which rules apply:
  - limit runs: rules 1, 2 and 4 (§6.1). A run passes when every channel keeps receive drift <= 1 s and
    has no decoder drops (§6.1); an invalid x2 run counts as not passing (§10.3).
  - loop-check runs: rules 1, 2 and 4. Zero decoder drops is the check's criterion, not rule 3.
  - thermal runs: rules 2, 3 (both runs are at unsaturated conditions when L <= 31) and 4. Rule 1 is
    the check's own criterion, so it does not make a thermal run invalid.
  - sweep runs: rules 1, 2 and 4, and rule 3 at unsaturated conditions (§6.2 with L = 32, A9): every
    count but 32 at the main condition, every auxiliary and every A count.
  - h3 runs: rules 1, 2 and 4 (§10). A run sustains as HYPOTHESES H3 defines it; an invalid x2 run
    counts as not sustained.
External failures (§9.3, last paragraph): an input that ends early, a server that is down before or
after a run, or a log that cannot be read make the run invalid with the reason given; a log that cannot
be read, a server that is
down, or a PC clock more than 5 ms behind or 100 ms ahead of the board's, stops the session. SIGTERM
or SIGHUP (the operator) stops the session too. In limit, loop-check and thermal sessions a run it cuts
short is recorded with that reason and is neither repeated nor counted; a stopped limit search is kept
and flagged, and the search is run again from §10 step 1 in a new session. In sweep and h3 sessions,
a run cut short by the operator, by an external failure or by the session's death (a reboot) is an
invalid attempt (§9.3), and the next session goes on as docs/notes/comparison.md says.

Sweep and h3 sessions follow the order main, aux, h3, reference: a session starts only when the
earlier ones are complete and its own is not. Each attempt is written to started.tsv before it runs,
just before its bench starts, so that one the session's death cuts short is still known to the next
session. While a sweep or h3 has a repetition to redo, no other session starts, smoke tests included.

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
  COOLING=... AMBIENT_C=... python3 tools/stream_session.py sweep {main,aux,reference}
  COOLING=... AMBIENT_C=... python3 tools/stream_session.py h3
Options for smoke tests only (recorded, and flagged in the result): --idle-s, --warmup, --duration,
--gate-s, for limit and h3 --start and --cap, and for sweep --counts. A sweep or h3 session with any of
them is a smoke test: its runs are of kind check, it goes to results/check/<UTC>_<sweep or h3>_smoke, and
it neither reads nor counts toward the protocol sessions.
Writes <out>/runs.tsv, one directory per run with the server's log lines, server/ (the whole server
log), result.csv; prints progress. Exit code 0 when the session completed (whatever it found), 1 when
it stopped early, 2 for bad arguments.
"""
import argparse
import csv
import datetime as dt
import difflib
import hashlib
import math
import os
import random
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
INVENTORY = "docs/notes/inventory_m1.txt"  # the board's software, build and model that freeze-m1 freezes (A10)
INVENTORY_SHA256 = "f6e5a78a15e5fecbae5ab5c132d866dcddd8eaec9562c48d6413d83f97f934dd"  # of INVENTORY as A10 fixes it
PC_BINARIES = {  # the stream PC's, as A10 fixes them: name -> SHA-256
    "mediamtx": "0ffb7f18210d08ee38cb16668c4f3a2c96622e69d401119c0026d078a2b89523",
    "ffmpeg": "e7e7fb30477f717e6f55f9180a70386c62677ef8a4d4d1a5d948f4098aa3eb99",
}
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
PREREG_V1_COMMIT = "87fe874b4f196dafd48f6a175b2d7604625aedba"  # docs/notes/env.md
FREEZE_M1_COMMIT = "2eec964d27545e081a44bc5ead01bbfcd2905a54"  # docs/notes/env.md; every run carries it
LIMIT = 32  # L (A9)
SEED, REPETITIONS = 20261030, 3  # §9.1, §8
SWEEPS = {  # §12 with §6.3 and A9: counts, condition, variants, run kind, results/<kind>/<UTC>_<sweep>
    "main": ((1, 2, 4, 8, 12, 16, 32), "main", ("B", "C"), "comparison"),
    "aux": ((1, 2, 3, 4, 5), "aux", ("B", "C"), "comparison"),
    "reference": ((1, 2), "main", ("A",), "reference"),
}
IDLE_BASELINE_S = 120  # §7.2
H3_START, SUSTAIN_FPS = 8, 4.75  # §10 (min(8, L)), HYPOTHESES H3
H3_PHASES = ("h3_search", "h3_confirm")
ORDER = ("main", "aux", "h3", "reference")  # docs/notes/comparison.md
SESSION_NAME = r"\d{8}T\d{6}Z_"  # results/<kind>/<UTC>_<sweep or h3>

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


def channel_order(counts):
    """§9.1: the sorted counts, shuffled by a fresh random.Random(20261030)."""
    order = sorted(counts)
    random.Random(SEED).shuffle(order)
    return order


def variant_order(variants, repetition):
    """§9.1: B->C in repetitions 1 and 3, C->B in repetition 2."""
    return tuple(variants) if repetition % 2 else tuple(reversed(variants))


def sweep_plan(sweep, counts=None):
    """[(channels, repetition, variants in run order)] of a sweep, in run order."""
    all_counts, _, variants, _ = SWEEPS[sweep]
    return [(c, r, variant_order(variants, r)) for c in channel_order(counts or all_counts)
            for r in range(1, REPETITIONS + 1)]


def sweep_state(records, variants):
    """Which repetitions earlier sessions completed, and what the others need (§9.1, §9.3).

    records: the attempts of the sweep's earlier sessions, in order, as dicts with session, name,
    channels, repetition, variant, attempt (1, or 2 for the repeat of an invalid run) and valid.
    A run is final when it is valid or its repeat is invalid too (invalid x2). A repetition is complete
    when all its runs are final within one session. An incomplete one is redone at the start of the
    next session, all of its runs: a run whose last attempt, in any earlier session, was an invalid
    first one continues with its repeat (attempt 2); every other run starts again at attempt 1. Its
    earlier runs are kept, and are replaced. Returns (complete, pending): complete is a set of
    (channels, repetition); pending maps (channels, repetition) to ({variant: first attempt},
    [names of the runs it replaces]).
    """
    by_rep = {}
    for r in records:
        by_rep.setdefault((r["channels"], r["repetition"]), []).append(r)
    complete, pending = set(), {}
    for key, recs in by_rep.items():
        sessions = list(dict.fromkeys(r["session"] for r in recs))
        if any(all(any(r["variant"] == v and (r["valid"] or r["attempt"] == 2)
                       for r in recs if r["session"] == s) for v in variants) for s in sessions):
            complete.add(key)
            continue
        first = {}
        for v in variants:
            mine = [r for r in recs if r["variant"] == v]
            first[v] = 2 if mine and not mine[-1]["valid"] and mine[-1]["attempt"] == 1 else 1
        pending[key] = (first, [f"{r['session']}/{r['name']}" for r in recs])
    return complete, pending


def load_records(session_dirs, phases):
    """The attempts of a sweep's or of h3's sessions, in order, from their runs.tsv (see sweep_state
    and h3_trials). phases: the runs.tsv phases to read. An attempt in started.tsv without a row in
    runs.tsv was cut short by the session's death (a reboot): it counts as invalid (§9.3), with
    lost = True."""
    out = []
    for d in session_dirs:
        rows, started = [], []
        for name, target in (("runs.tsv", rows), ("started.tsv", started)):
            path = Path(d) / name
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    target.extend(r for r in csv.DictReader(f, delimiter="\t") if r["phase"] in phases)
        done = {r["name"] for r in rows}
        rows += [dict(r, valid="0", passed="", lost="1") for r in started if r["name"] not in done]
        rows.sort(key=lambda r: (int(r["seq"]), int(r["attempt"])))
        for row in rows:
            out.append({"session": Path(d).name, "name": row["name"], "phase": row["phase"],
                        "channels": int(row["channels"]), "repetition": int(row["repetition"]),
                        "variant": row["variant"], "attempt": int(row["attempt"]),
                        "valid": row["valid"] == "1", "passed": row["passed"] == "1",
                        "lost": row.get("lost") == "1"})
    return out


def sustained(rows):
    """HYPOTHESES H3: every channel at >= 4.75 fps inference, with drift <= 1 s and no decoder drops."""
    return bool(rows) and all(float(r["inference_fps"]) >= SUSTAIN_FPS and float(r["drift_max_s"]) <= 1.0
                              and int(r["decoder_drops"]) == 0 for r in rows)


def h3_schedule(limit=LIMIT, start=H3_START):
    """§10 for B and C as one schedule: a generator that yields each run as (variant, phase, channels,
    repetition, pair) and is sent whether it sustained. It returns {variant: confirmed n}.

    - Search (step 1): one run per variant and step, B->C at odd steps and C->B at even ones while both
      search (step 4); up from min(8, L) while runs sustain, or down from it until one does.
    - Confirmation (step 2), in rounds: each variant still confirming needs 3 runs at n (if n >= 1)
      and 3 at n + 1 (if n < L) that it does not have yet. A round runs the counts in ascending order;
      at a count both need, the 6 runs are interleaved B->C, C->B, B->C, and each such pair is marked
      with pair = (channels, i), i = 1, 2, 3 (docs/notes/comparison.md: a pair is not split over
      sessions). Then each variant goes up, down or is confirmed, "up" first.
    The repetition is 1 for a search run and 1 to 3 for a confirmation run at its count.
    """
    start = min(start, limit)
    st = {v: {"next": start, "up": True, "n": 0, "done": False} for v in ("B", "C")}
    step = 1
    while not all(x["done"] for x in st.values()):
        for v in (("B", "C") if step % 2 else ("C", "B")):
            x = st[v]
            if x["done"]:
                continue
            c = x["next"]
            ok = yield (v, "h3_search", c, 1, None)
            if ok:
                x["n"] = c
                x["done"] = not x["up"] or c >= limit
                x["next"] = c + 1
            elif x["up"] and c == start and c > 1:  # the start does not sustain: go down
                x["up"], x["next"] = False, c - 1
            elif not x["up"] and c > 1:
                x["next"] = c - 1
            else:
                x["done"] = True
        step += 1

    n = {v: st[v]["n"] for v in ("B", "C")}
    runs = {"B": {}, "C": {}}  # variant -> channels -> outcomes of its confirmation runs there
    confirming = ["B", "C"]
    while confirming:
        need = {}
        for v in confirming:
            for c in (n[v], n[v] + 1):
                if 1 <= c <= limit and c not in runs[v]:
                    need.setdefault(c, []).append(v)
        for c in sorted(need):
            if len(need[c]) == 2:
                for i in (1, 2, 3):
                    for v in (("B", "C") if i % 2 else ("C", "B")):
                        runs[v].setdefault(c, []).append((yield (v, "h3_confirm", c, i, (c, i))))
            else:
                v = need[c][0]
                for i in (1, 2, 3):
                    runs[v].setdefault(c, []).append((yield (v, "h3_confirm", c, i, None)))
        still = []
        for v in confirming:
            at_n = sum(runs[v][n[v]]) if n[v] >= 1 else None
            above = sum(runs[v][n[v] + 1]) if n[v] < limit else None
            if above is not None and above >= 2:
                n[v] += 1
                still.append(v)
            elif at_n is not None and at_n <= 1:
                n[v] -= 1
                still.append(v)
        confirming = still
    return n


def h3_trials(records):
    """The h3 runs of earlier sessions as trials: consecutive attempts of one run within a session.
    Each trial is a dict with session, names, variant, phase, channels, repetition, final (valid, or
    invalid x2) and sustained (False unless valid and passed)."""
    out = []
    for r in records:
        last = out[-1] if out else None
        key = (r["session"], r["variant"], r["phase"], r["channels"], r["repetition"])
        if last and not last["final"] and key == (last["session"], last["variant"], last["phase"],
                                                  last["channels"], last["repetition"]) and r["attempt"] == 2:
            t = last
        else:
            t = {"session": r["session"], "names": [], "variant": r["variant"], "phase": r["phase"],
                 "channels": r["channels"], "repetition": r["repetition"]}
            out.append(t)
        t["names"].append(f"{r['session']}/{r['name']}")
        t["final"] = r["valid"] or r["attempt"] == 2
        t["sustained"] = r["valid"] and r["passed"]
    return out


def h3_replay(trials, limit=LIMIT, start=H3_START):
    """Replays earlier sessions' trials through h3_schedule. Returns (schedule, next run or None, n or
    None, continued, replaced): continued maps a run (variant, phase, channels, repetition) to 2 when
    its next attempt is the repeat of an earlier session's invalid first attempt; replaced lists, per
    such run, the earlier runs it replaces. A pair's first run whose partner did not finish in the same
    session is replaced; an unfinished partner continues with its repeat (§9.1, §9.3)."""
    gen = h3_schedule(limit, start)
    nxt, n = next(gen), None
    continued, replaced = {}, {}
    i = 0
    while i < len(trials):
        t = trials[i]
        if nxt is None or (t["variant"], t["phase"], t["channels"], t["repetition"]) != nxt[:4]:
            raise Stop(f"h3 records out of §10's order at {t['names'][0]}: expected {nxt and nxt[:4]}")
        key = nxt[:4]
        if not t["final"]:  # the session ended before the repeat: the next session repeats it
            continued[key] = 2
            replaced.setdefault(key, []).extend(t["names"])
            i += 1
            continue
        pair = nxt[4]
        if pair is not None and pair_first(nxt):
            partner = trials[i + 1] if i + 1 < len(trials) else None
            if partner is None or partner["session"] != t["session"] or not partner["final"]:
                replaced.setdefault(key, []).extend(t["names"])
                continued.pop(key, None)
                if partner is not None and partner["session"] == t["session"]:  # unfinished partner
                    pkey = (partner["variant"], partner["phase"], partner["channels"], partner["repetition"])
                    continued[pkey] = 2
                    replaced.setdefault(pkey, []).extend(partner["names"])
                    i += 2
                else:
                    i += 1
                continue
        continued.pop(key, None)  # this run is done: nothing of it is left to repeat or to replace
        replaced.pop(key, None)
        t["counted"] = True  # for tools/m1_report.py: the trials whose outcome the schedule took
        try:
            nxt = gen.send(t["sustained"])
        except StopIteration as e:
            nxt, n = None, e.value
        i += 1
    return gen, nxt, n, continued, replaced


def pair_first(run):
    """Whether run, a confirmation run with a pair mark, is the first of its pair: B's at i = 1 and 3,
    C's at i = 2."""
    return run[0] == ("B" if run[3] % 2 else "C")


def proc_stat_nonidle(text):
    """{cpu: non-idle time in ticks} from /proc/stat: user + nice + system + irq + softirq + steal,
    as bench_main counts it (§7.2)."""
    out = {}
    for line in text.splitlines():
        f = line.split()
        if f and f[0].startswith("cpu") and f[0] != "cpu":
            v = [int(x) for x in f[1:9]]
            out[int(f[0][3:])] = v[0] + v[1] + v[2] + v[5] + v[6] + v[7]
    return out


def idle_rows(before, after, tick_s):
    """§7.2's idle baseline as rows named like bench_main's summary: per CPU, per cluster and total."""
    d = {c: (after[c] - before[c]) * tick_s for c in sorted(after) if c in before}
    rows = [(f"system_cpu{c}_s", f"{v:.3f}") for c, v in d.items()]
    for name, cpus in (("0-3", range(0, 4)), ("4-5", range(4, 6)), ("6-7", range(6, 8))):
        rows.append((f"system_cpu{name}_s", f"{sum(d.get(c, 0.0) for c in cpus):.3f}"))
    rows.append(("system_cpu_total_s", f"{sum(d.values()):.3f}"))
    return rows


def loops_in_window(duration_s, warmup, window):
    """Loop points of a video of duration_s that fall inside the window."""
    return math.floor((warmup + window) / duration_s) - math.floor(warmup / duration_s)


# ---------------------------------------------------------------- the session

def inventory_diff(now, reference):
    """None if the inventory equals the reference, else a unified diff from the reference to it."""
    if now == reference:
        return None
    return "".join(difflib.unified_diff(reference.splitlines(True), now.splitlines(True),
                                        INVENTORY, "inventory.txt"))


def pc_binary_problems(sha256sum_output):
    """The problems with sha256sum's output for the PC's binaries, in the order of PC_BINARIES."""
    hashes = [line.split()[0] for line in sha256sum_output.splitlines() if line.strip()]
    if len(hashes) != len(PC_BINARIES):
        return ["cannot hash the stream PC's binaries: " + sha256sum_output.strip()[-200:]]
    return [f"the stream PC's {name} has SHA-256 {got}, A10 fixes {want}"
            for (name, want), got in zip(PC_BINARIES.items(), hashes) if got != want]


def durable(f):
    """Flushes f to the disk, so that a reboot does not lose what was written."""
    f.flush()
    os.fsync(f.fileno())


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
        for f in ("build/bench_main", "build/CMakeCache.txt", ASSIGNMENT, MANIFEST, INVENTORY):
            if not Path(f).exists():
                raise Stop(f"no {f}")
        if hashlib.sha256(Path(INVENTORY).read_bytes()).hexdigest() != INVENTORY_SHA256:
            raise Stop(f"{INVENTORY} is not the file A10 fixes (SHA-256 {INVENTORY_SHA256})")
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
                        "passed\treasons\twarnings\ttrip_samples\tgate_soc_mc\tclock_offset_ms\tstart_utc\tend_utc\t"
                        "replaces\n")
        self.started = open(self.out / "started.tsv", "w", encoding="utf-8")
        self.started.write("seq\tname\tphase\tvariant\tchannels\tcondition\trepetition\tattempt\tstart_utc\n")
        for f in (self.runs, self.started):
            durable(f)

    def say(self, msg):
        line = f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ} {msg}"
        try:
            print(line, flush=True)
        except OSError:  # no terminal any more; session.log keeps the line
            pass
        self.log.write(line + "\n")
        self.log.flush()

    def sh(self, cmd, out_file, timeout=120, err_file=None):
        """Runs cmd with its output in out_file, and its error output there too or in err_file."""
        try:
            with open(out_file, "w", encoding="utf-8") as f:
                if err_file is None:
                    return subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, text=True,
                                          timeout=timeout).returncode
                with open(err_file, "w", encoding="utf-8") as e:
                    return subprocess.run(cmd, stdout=f, stderr=e, text=True, timeout=timeout).returncode
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
        if self.sh(["tools/inventory.sh"], self.out / "inventory.txt", err_file=self.out / "inventory.err") != 0:
            raise Stop("inventory.sh failed: see inventory.err")
        diff = inventory_diff((self.out / "inventory.txt").read_text(), Path(INVENTORY).read_text())
        if diff:
            (self.out / "inventory.diff").write_text(diff)
            raise Stop(f"the board's software, build or model differs from {INVENTORY} (A10): see inventory.diff")
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
        from_s = max(0, a.idle_s - IDLE_BASELINE_S)  # §7.2's idle baseline: the idle period's last 120 s
        for i in range(a.idle_s):
            if i == from_s:
                self.idle = [dt.datetime.now(dt.timezone.utc), time.monotonic(), Path("/proc/stat").read_text()]
            time.sleep(1)
            if i >= a.idle_s - 60:
                temps.append(self.soc())
        self.idle += [time.monotonic(), Path("/proc/stat").read_text()]
        self.baseline = sum(temps) // len(temps)
        (self.out / "baseline.txt").write_text(f"idle_s: {a.idle_s}\nsoc_thermal_baseline_mc: {self.baseline}\n")
        self.say(f"soc-thermal baseline {self.baseline} m°C")
        self.last_end = time.monotonic() - a.gate_s  # no bench ran during the idle period

    def start_server(self):
        self.say("starting the stream server")
        pid = f"~/{PC_REPO}/stream-server/mediamtx.pid"
        if self.pc(f"test -f {pid} && kill -0 \"$(cat {pid})\" 2>/dev/null && echo running || true").strip():
            raise Stop("a stream server is already running on the PC; stop it first")
        # publish.sh runs the ffmpeg on the PATH of these SSH commands.
        binaries = self.pc(f"sha256sum ~/{PC_REPO}/stream-server/mediamtx \"$(command -v ffmpeg)\"")
        problems = pc_binary_problems(binaries)
        if problems:
            raise Stop("; ".join(problems))
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
            "pc_binaries_sha256": " ".join(binaries.split()),
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

    def attempt(self, name, phase, variant, channels, condition, run_kind, repetition, source, attempt_no=1):
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
               "--run-kind", run_kind, "--repetition", str(repetition), "--freeze-commit", FREEZE_M1_COMMIT,
               "--out", str(run_dir)]
        start = dt.datetime.now(dt.timezone.utc)
        # Just before the bench, after the gate and the checks: if the session dies during the run,
        # the next session still knows of it; a run that never started leaves no row.
        self.started.write("\t".join(map(str, [self.seq, name, phase, variant, channels, condition, repetition,
                                               attempt_no, f"{start:%Y-%m-%dT%H:%M:%SZ}"])) + "\n")
        durable(self.started)
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
            limit = a.limit if phase == "thermal" else LIMIT
            if phase in ("thermal",) + tuple(SWEEPS) and unsaturated(channels, condition, limit):  # rule 3
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

    def run(self, phase, variant, channels, condition, run_kind, repetition=1, source=None, first_attempt=1,
            replaces=""):
        """A run, repeated once if invalid (§9.3); first_attempt 2 when it is already the repeat of an
        earlier session's invalid run. Returns (valid, run directory of the last attempt)."""
        source = source or f"rtsp://{SERVER}:{RTSP_PORT}/ch{{n}}"
        self.seq += 1
        base = f"{self.seq:02d}_{phase}_{variant}_{channels}ch"
        if phase in SWEEPS or phase == "h3_confirm":
            base += f"_r{repetition}"
        for attempt in range(first_attempt, 3):
            name = base if attempt == 1 else base + "_repeat"
            o, run_dir, temp, start, end = self.attempt(name, phase, variant, channels, condition, run_kind,
                                                        repetition, source, attempt)
            valid = not o.reasons
            passed = ""
            if valid and phase in ("search", "confirm"):
                passed = "1" if channels_pass(read_csv(run_dir / "channels.csv")[1]) else "0"
            if valid and phase in H3_PHASES:
                passed = "1" if sustained(read_csv(run_dir / "channels.csv")[1]) else "0"
            self.runs.write("\t".join(map(str, [
                self.seq, name, phase, variant, channels, condition, repetition, attempt, o.code, int(valid), passed,
                "; ".join(o.reasons), "; ".join(o.warnings), o.trip_samples, temp, o.offset_ms,
                f"{start:%Y-%m-%dT%H:%M:%SZ}", f"{end:%Y-%m-%dT%H:%M:%SZ}", replaces])) + "\n")
            durable(self.runs)
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
        if getattr(a, "counts", None):
            off.append(f"counts {' '.join(map(str, a.counts))} (protocol: all of the sweep)")
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


def session_kind(name):
    return "comparison" if name == "h3" else SWEEPS[name][3]


def sweep_sessions(sweep, current=None):
    """The protocol sessions of a sweep or of h3: results/<kind>/<UTC>_<name>, by name (UTC), not
    current."""
    pattern = re.compile(SESSION_NAME + re.escape(sweep) + "$")
    return sorted(d for d in Path("results", session_kind(sweep)).glob(f"*_{sweep}")
                  if d.is_dir() and pattern.match(d.name)
                  and (current is None or d.resolve() != Path(current).resolve()))


def progress(name):
    """'complete', 'started' or 'new' for a sweep or h3, from its protocol sessions."""
    sessions = sweep_sessions(name)
    if not sessions:
        return "new"
    if name == "h3":
        _, nxt, _, _, _ = h3_replay(h3_trials(load_records(sessions, H3_PHASES)))
        return "complete" if nxt is None else "started"
    complete, _ = sweep_state(load_records(sessions, (name,)), SWEEPS[name][2])
    return "complete" if all((c, r) in complete for c, r, _ in sweep_plan(name)) else "started"


def redo_pending():
    """The sweeps, or h3, whose protocol sessions left a repetition or a repeat to redo."""
    out = []
    for name in ORDER:
        sessions = sweep_sessions(name)
        if not sessions:
            continue
        if name == "h3":
            _, _, _, continued, replaced = h3_replay(h3_trials(load_records(sessions, H3_PHASES)))
            if continued or replaced:
                out.append(name)
        elif sweep_state(load_records(sessions, (name,)), SWEEPS[name][2])[1]:
            out.append(name)
    return out


def order_problem(name):
    """Why a protocol session of name cannot start now (docs/notes/comparison.md), or None."""
    for earlier in ORDER[:ORDER.index(name)]:
        if progress(earlier) != "complete":
            return f"{earlier} comes before {name} and is not complete"
    if progress(name) == "complete":
        return f"{name} is complete"
    return None


def write_idle_baseline(s, run_kind):
    """§7.2: system CPU over the idle period's last 120 s, with a results header (§1.4)."""
    start, t0, stat0, t1, stat1 = s.idle
    rows = [("window_s", f"{t1 - t0:.1f}")] + idle_rows(proc_stat_nonidle(stat0), proc_stat_nonidle(stat1),
                                                       1.0 / os.sysconf("SC_CLK_TCK"))
    amendments = re.findall(r"^## (A\d+) ", Path("prereg/AMENDMENTS.md").read_text(encoding="utf-8"), re.M)
    ntp = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], capture_output=True,
                         text=True).stdout.strip()
    header = {"start_time_utc": f"{start:%Y-%m-%dT%H:%M:%SZ}", "ntp_synchronized": ntp,
              "prereg_v1_commit": PREREG_V1_COMMIT, "freeze_m1_commit": FREEZE_M1_COMMIT, "run_kind": run_kind,
              "measurement": "idle baseline of system CPU (PROTOCOL §7.2), not a run", "code_commit": s.code_commit,
              "amendment": amendments[-1] if amendments else "none"}
    text = "".join(f"# {k}: {v}\n" for k, v in header.items())
    text += "key,value\n" + "".join(f"{k},{v}\n" for k, v in rows)
    (s.out / "idle_baseline.csv").write_text(text, encoding="utf-8")


def cmd_sweep(s):
    a = s.args
    _, condition, variants, run_kind = SWEEPS[a.sweep]
    write_idle_baseline(s, s.run_kind)
    earlier = [] if s.smoke else sweep_sessions(a.sweep, s.out)
    complete, pending = sweep_state(load_records(earlier, (a.sweep,)), variants)
    plan = sweep_plan(a.sweep, a.counts)
    todo = [p for p in plan if (p[0], p[1]) in pending] + \
           [p for p in plan if (p[0], p[1]) not in pending and (p[0], p[1]) not in complete]
    s.say(f"{a.sweep}: {len(complete)} of {len(plan)} repetitions complete in earlier sessions, "
          f"{len(pending)} to redo, {len(todo)} to run")
    done_here, status = [], "stopped"
    try:
        for channels, repetition, order in todo:
            first, replaced = pending.get((channels, repetition), ({}, []))
            for variant in order:
                s.run(a.sweep, variant, channels, condition, s.run_kind, repetition,
                      first_attempt=first.get(variant, 1), replaces=" ".join(replaced))
            done_here.append(f"{channels}ch_r{repetition}")
        status = "completed"
    finally:  # also when the session stops: what it ran and what is left
        complete, _ = sweep_state(load_records(earlier + [s.out], (a.sweep,)), variants)
        left = [f"{c}ch_r{r}" for c, r, _ in plan if (c, r) not in complete]
        s.write_result([("sweep", a.sweep), ("session_status", status),
                        ("channel_order", " ".join(map(str, channel_order(a.counts or SWEEPS[a.sweep][0])))),
                        ("earlier_sessions", " ".join(d.name for d in earlier) or "none"),
                        ("repetitions_run_here", " ".join(done_here) or "none"),
                        ("repetitions_left", " ".join(left) or "none")], s.run_kind)


def cmd_h3(s):
    a = s.args
    write_idle_baseline(s, s.run_kind)
    earlier = [] if s.smoke else sweep_sessions("h3", s.out)
    gen, nxt, n, continued, replaced = h3_replay(h3_trials(load_records(earlier, H3_PHASES)), a.cap, a.start)
    s.say(f"h3: {len(earlier)} earlier sessions; " + (f"next {nxt[:4]}" if nxt else f"already done: {n}"))
    status = "stopped"
    try:
        while nxt is not None:
            variant, phase, channels, repetition, _ = nxt
            key = nxt[:4]
            valid, run_dir = s.run(phase, variant, channels, "main", s.run_kind, repetition,
                                   first_attempt=continued.pop(key, 1), replaces=" ".join(replaced.pop(key, [])))
            ok = valid and sustained(read_csv(run_dir / "channels.csv")[1])
            try:
                nxt = gen.send(ok)
            except StopIteration as e:
                nxt, n = None, e.value
        status = "completed"
    finally:  # also when the session stops: where the search is
        s.write_result([("session_status", status), ("n_B", n["B"] if n else "not yet"),
                        ("n_C", n["C"] if n else "not yet"), ("next_run", " ".join(map(str, nxt[:4])) if nxt else "none"),
                        ("limit", a.cap), ("earlier_sessions", " ".join(d.name for d in earlier) or "none")],
                       s.run_kind)


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
    ap.add_argument("command", choices=["limit", "loop-check", "thermal", "sweep", "h3"])
    ap.add_argument("sweep", nargs="?", choices=list(SWEEPS), help="sweep: which one")
    ap.add_argument("--out")
    ap.add_argument("--limit", type=int, help="thermal: L, which gives the largest main count")
    ap.add_argument("--idle-s", type=int, default=PROTOCOL["idle_s"])
    ap.add_argument("--warmup", type=int, default=PROTOCOL["warmup"])
    ap.add_argument("--duration", type=int, default=PROTOCOL["duration"])
    ap.add_argument("--gate-s", type=int, default=PROTOCOL["gate_s"])
    ap.add_argument("--start", type=int, default=PROTOCOL["start"])
    ap.add_argument("--cap", type=int, default=PROTOCOL["cap"])
    ap.add_argument("--counts", type=int, nargs="+", help="sweep, smoke tests only: these counts of the sweep")
    args = ap.parse_args(argv)
    if (args.command == "sweep") != (args.sweep is not None):
        ap.error("sweep needs main, aux or reference; the other commands take none")
    if args.counts and (args.command != "sweep" or not set(args.counts) <= set(SWEEPS[args.sweep][0])
                        or len(set(args.counts)) != len(args.counts)):
        ap.error("--counts is for sweep, with distinct counts of that sweep")
    if args.limit is not None and args.command != "thermal":
        ap.error("--limit is for thermal")
    if args.command == "thermal" and not (args.limit and 1 <= args.limit <= 32):
        ap.error("thermal needs --limit L (1..32)")
    if args.idle_s < 60:
        ap.error("--idle-s must be at least 60")
    if not 1 <= args.start <= args.cap <= 32:
        ap.error("need 1 <= --start <= --cap <= 32 (the assignment has 32 channels)")
    now = f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}"
    smoke = any(getattr(args, k) != v for k, v in PROTOCOL.items()) or bool(args.counts)
    if args.command in ("sweep", "h3"):
        name = args.sweep or "h3"
        run_kind = "check" if smoke else session_kind(name)
        if args.out and Path(args.out).parent != Path("results", run_kind):
            ap.error(f"this session goes to results/{run_kind}/" + ("" if smoke else "<UTC>_" + name))
        if args.out and not smoke and not re.fullmatch(SESSION_NAME + re.escape(name), Path(args.out).name):
            ap.error(f"a protocol session is named <UTC>_{name}, where the next one finds it")
        args.out = args.out or f"results/{run_kind}/{now}_{name}" + ("_smoke" if smoke else "")
    else:
        kind = {"limit": "limit", "loop-check": "loop", "thermal": "thermal"}[args.command]
        run_kind = "check" if kind == "loop" else "calibration"
        args.out = args.out or f"results/{run_kind}/{now}_{kind}"

    def on_signal(signum, _frame):
        raise Interrupted(f"signal {signal.Signals(signum).name}")

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGHUP, on_signal)
    try:
        this = (args.sweep or "h3") if args.command in ("sweep", "h3") and not smoke else None
        waiting = [w for w in redo_pending() if w != this]
        if waiting:
            raise Stop(f"not starting: {', '.join(waiting)} has a repetition to redo, so its next session comes "
                       "first (docs/notes/comparison.md)")
        if this:
            problem = order_problem(this)
            if problem:
                raise Stop(f"not starting: {problem} (docs/notes/comparison.md)")
        s = Session(args)
        s.smoke, s.run_kind = smoke, run_kind
        if args.command == "loop-check":
            s.loops = loop_channels()  # before the idle period, so a bad input fails at once
    except Stop as e:
        print(f"stream_session: {e}", file=sys.stderr)
        return 2
    try:
        s.start()
        {"limit": cmd_limit, "loop-check": cmd_loop_check, "thermal": cmd_thermal, "sweep": cmd_sweep,
         "h3": cmd_h3}[args.command](s)
        status = 0
    except Stop as e:
        s.say(f"stopped: {e}")
        status = 1
    finally:
        s.end()
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
