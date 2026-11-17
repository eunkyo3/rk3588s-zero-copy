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
  sweep 4k     the exploratory 4K design of AMENDMENTS A11 (M1.1): B and C at 1, 2, 4 and 8 channels of
               the 4K set, run kind m1.1, after every sweep of §12 and after the 4K loop check
               (loop-check --set 4k). The session checks the 4K videos' SHA-256 against A11.
  sweep k4, a55, bdirect_main, bdirect_aux
               the optional M1.1 designs of §13 with AMENDMENTS A12, run kind m1.1, after the 4K sweep,
               each with its controls rerun in the same session: B with K = 4 (Bk4) against B at 8
               channels; B and C on the A55 cores at 8 channels, one repetition; B-direct (Bd) against B
               and C at 8 channels main and at 5 channels aux, once tools/bdirect_check has passed.
               With three arms the order rotates.
  sweep m2_main, m2_aux
               M2's comparison of prereg/M2.md §5.2, run kind m2, after the M1.1 designs: C against Cp
               (C with the NEON post-processing, --post neon) at 8 channels main with the staggered
               sampler (--stagger on, both arms), then at 5 channels aux. Each run passes freeze-m2's
               commit from docs/notes/env.md. A session starts only once freeze-m2 has its row there and
               M2 §4's equivalence check, in results/check/<UTC>_m2_equivalence, has passed with the
               SHA-256 of this build's post-processing objects.
  m2-check     the check runs of prereg/M2.md §7 step 3, run kind check, before freeze-m2: one run of each
               arm, C then Cp, at 8 channels main with the staggered sampler, once M2 §4's equivalence
               check has passed with this build's post-processing objects. Its runs pass no freeze-m2
               commit (bench_main's default, none).
  sweep m4_main, m4_aux
               M4's pipeline comparison of prereg/M4.md §3.2, run kind m4, after M2's sweeps: C8 (YOLOv8n with
               Cp's post-processing, --post neon) against C10 (YOLOv10n with yolov10_post, --post v10), both
               variant C, at 8 channels main with the staggered sampler (--stagger on, both arms), then at 5
               channels aux. Each run passes freeze-m4's commit from docs/notes/env.md. A session starts only
               once freeze-m4 has its row there, M2 §4's equivalence check has passed with this build's two
               objects of A13 (M4 §8 step 3), the four accuracy passes are done and a passed wiring check
               covers this build's post-processing objects (m4_gates_problem, M4 §8 steps 3 and 5), and M4's
               calib_npu session (tools/m4_calib.sh, in results/m4/<UTC>_m4_calib) decides
               (m4_calib_choice). That session gives each arm's saturation label at the sweep's point (M4
               §3.2). Every M4 pipeline session writes the session it took to saturation.txt, and none starts
               with another one than the earlier ones took.
  m4-check     the arms check of prereg/M4.md §8 step 3, run kind check: one run of each arm, C8 then C10,
               at 8 channels main with the staggered sampler, once M2 §4's equivalence check has passed with
               this build's two objects of A13. Its runs pass freeze-m4's commit, none before freeze-m4
               (M4 §4). With --calib DIR (a tools/m4_calib.sh session), that session's labels decide rule 3;
               without it, rule 3 does not judge its runs, as for an unknown label (M4 §5).
               Both M4 commands first check the two models' SHA-256 against M4 §1, and every M4 run's header
               against its arm's model and post-processing.
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
    stops if 30 minutes pass without a run (§2); a sweep or m2-check also takes the idle baseline of
    system CPU (§7.2, and from /proc/schedstat for M2 §3.2) over the idle period's last 120 s;
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
  - m2-check runs: rules 1, 2, 3 and 4 (8 channels main is unsaturated, M2 §5.2).
  - m4 sweep and m4-check runs: rules 1, 2 and 4, and rule 3 only for an arm whose label at the point is
    unsaturated (M4 §3.2). A saturated or unknown label leaves rule 3 out, and the runs are still made. A run
    whose header is not its arm's, or that bench_main refuses before it makes the run's directory, stops the
    session: the setup is wrong, and a repeat would fail the same way.
  - h3 runs: rules 1, 2 and 4 (§10). A run sustains as HYPOTHESES H3 defines it; an invalid x2 run
    counts as not sustained.
External failures (§9.3, last paragraph): an input that ends early, a server that is down before or
after a run, or a log that cannot be read make the run invalid with the reason given; a log that cannot
be read, a server that is
down, or a PC clock more than 5 ms behind or 100 ms ahead of the board's, stops the session. SIGTERM
or SIGHUP (the operator) stops the session too. In limit, loop-check, thermal and m2-check sessions a run
it cuts short is recorded with that reason and is neither repeated nor counted; a stopped limit search is kept
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
  COOLING=... AMBIENT_C=... python3 tools/stream_session.py loop-check [--set 4k] [--out DIR]
  COOLING=... AMBIENT_C=... python3 tools/stream_session.py thermal --limit L [--out DIR]
  COOLING=... AMBIENT_C=... python3 tools/stream_session.py sweep {main,aux,reference,4k,k4,a55,bdirect_main,
                                                                   bdirect_aux,m2_main,m2_aux,m4_main,m4_aux}
  COOLING=... AMBIENT_C=... python3 tools/stream_session.py m2-check [--out DIR]
  COOLING=... AMBIENT_C=... python3 tools/stream_session.py m4-check [--calib DIR] [--out DIR]
  COOLING=... AMBIENT_C=... python3 tools/stream_session.py h3
Options for smoke tests only (recorded, and flagged in the result): --idle-s, --warmup, --duration,
--gate-s, for limit and h3 --start and --cap, for sweep --counts, and for an m4 sweep --calib DIR, the
tools/m4_calib.sh session whose labels it takes (without it, a smoke test takes the protocol's session if
there is one, and otherwise the label unknown). A sweep or h3 session with any of
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m4_order  # noqa: E402  (M4 §8 steps 3 and 5: the accuracy passes and the wiring check)

SERVER, BOARD_IP, RTSP_PORT = "10.88.0.1", "10.88.0.2", 8554
SSH = ["ssh", "-i", "/home/pi/.ssh/bench_stream", "-o", "UserKnownHostsFile=/home/pi/.ssh/known_hosts",
       "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"eunkyo@{SERVER}"]
PC_REPO = "rk3588s-zero-copy"  # relative to the PC user's home
VIDEO_SETS = {  # assignment, the PC's video directory, the board's manifest, the videos' SHA-256 to check
    "m1": ("stream-server/assignment_m1.txt", "media/bench", "/home/pi/media/bench/manifest.tsv", None),  # A9
    "4k": ("stream-server/assignment_4k.txt", "media/bench4k", "/home/pi/media/bench4k/manifest.tsv", {  # A11
        "pexels_36330861.mp4": "dc02ec11ee5774588b90233e5e364bac37f2b4e8e0be771fd023e06539a59a8e",
        "pexels_29130409.mp4": "0cd2060610d94766a27bc109d646348801b4abb75276537dc4557631bc08e7e8",
        "pixabay_180386.mp4": "f886a60d179264fa6683188fd1a5b6ac2178d311a10c05f7380a246ad88d6a2b"}),
}
ASSIGNMENT, PC_VIDEOS, MANIFEST, VIDEO_SHA256 = VIDEO_SETS["m1"]  # main() selects the set of the session
INVENTORY = "docs/notes/inventory_m1.txt"  # the board's software, build and model that freeze-m1 freezes (A10)
INVENTORY_SHA256 = "f6e5a78a15e5fecbae5ab5c132d866dcddd8eaec9562c48d6413d83f97f934dd"  # of INVENTORY as A10 fixes it
PC_BINARIES = {  # the stream PC's, as A10 fixes them: name -> SHA-256
    "mediamtx": "0ffb7f18210d08ee38cb16668c4f3a2c96622e69d401119c0026d078a2b89523",
    "ffmpeg": "e7e7fb30477f717e6f55f9180a70386c62677ef8a4d4d1a5d948f4098aa3eb99",
}
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
PREREG_M2_COMMIT = "5d9d6d04e50f17d52980dc5b958c7071d7107176"  # docs/notes/env.md; M2's headers carry it (M2 §5.1)
PREREG_M4_COMMIT = "8638685d9ef806a85e5042d7cc10febdd5ae93a9"  # docs/notes/env.md; M4's headers carry it (M4 §4)
ENV_MD = "docs/notes/env.md"  # its Registration table gives freeze-m2's and freeze-m4's commits
POST_OBJECTS = {  # M2 §4: the post-processing objects of libcore, by their key in m2_equivalence.csv
    "sha256_yolov8_post_o": "build/CMakeFiles/core.dir/src/core/yolov8_post.cpp.o",
    "sha256_yolov8_post_neon_o": "build/CMakeFiles/core.dir/src/core/yolov8_post_neon.cpp.o",
}
M4_POST_OBJECTS = {  # M4 §8 step 3: also yolov10_post and the shared function (kPostObjects of src/core/report.h)
    **POST_OBJECTS,
    "sha256_yolov10_post_o": "build/CMakeFiles/core.dir/src/core/yolov10_post.cpp.o",
    "sha256_pipeline_post_o": "build/CMakeFiles/core.dir/src/core/pipeline_post.cpp.o",
}
M4_MODELS = {  # M4 §1: arm -> (model, its SHA-256, its name in tools/m4_calib_summary.py)
    "C8": ("models/yolov8n_i8.rknn", "04a76611a785802b08703192455994e07bbfe4531821ae7ed35faf78fe1ec1ee", "yolov8n"),
    "C10": ("models/yolov10n_i8.rknn", "6a6442e1addbd1f08dbdb0772aeea1a992233b4f1db3bd451d20823522246f81", "yolov10n"),
}
M4_CALIB = "m4_calib"  # results/m4/<UTC>_m4_calib: a session of tools/m4_calib.sh (M4 §3.1)
M4_CALIB_SETTINGS = {"run_kind": "m4", "warmup_s": "20", "duration_s": "120"}  # its session.txt (M4 §3.1)
LABELS = ("unsaturated", "saturated", "unknown")  # M4 §3.2 and §5, as tools/m4_calib_summary.py writes them
LIMIT = 32  # L (A9)
SEED, REPETITIONS = 20261030, 3  # §9.1, §8
SWEEPS = {  # §12 with §6.3 and A9: counts, condition, variants, run kind, results/<kind>/<UTC>_<sweep>
    "main": ((1, 2, 4, 8, 12, 16, 32), "main", ("B", "C"), "comparison"),
    "aux": ((1, 2, 3, 4, 5), "aux", ("B", "C"), "comparison"),
    "reference": ((1, 2), "main", ("A",), "reference"),
    "4k": ((1, 2, 4, 8), "main", ("B", "C"), "m1.1"),  # A11, with the 4K video set
    # AMENDMENTS A12: §13's optional designs, each with its controls; the arms are named in ARMS
    "k4": ((8,), "main", ("B", "Bk4"), "m1.1"),
    "a55": ((8,), "main", ("B", "C"), "m1.1"),
    "bdirect_main": ((8,), "main", ("B", "C", "Bd"), "m1.1"),
    "bdirect_aux": ((5,), "aux", ("B", "C", "Bd"), "m1.1"),
    # prereg/M2.md §5.2: C against Cp, C with the NEON post-processing (§1)
    "m2_main": ((8,), "main", ("C", "Cp"), "m2"),
    "m2_aux": ((5,), "aux", ("C", "Cp"), "m2"),
    # prereg/M4.md §3.2: C8 (YOLOv8n, Cp's post-processing) against C10 (YOLOv10n, yolov10_post)
    "m4_main": ((8,), "main", ("C8", "C10"), "m4"),
    "m4_aux": ((5,), "aux", ("C8", "C10"), "m4"),
}
ARMS = {"Bd": ["--variant", "Bd"], "Bk4": ["--variant", "B", "--prep-threads", "4"],  # A12
        "Cp": ["--variant", "C", "--post", "neon"],  # M2 §1
        "C8": ["--variant", "C", "--post", "neon", "--model", M4_MODELS["C8"][0]],  # M4 §3.2
        "C10": ["--variant", "C", "--post", "v10", "--model", M4_MODELS["C10"][0]]}  # else --variant <arm>
SWEEP_OPTIONS = {"a55": ["--cores", "a55"],  # A12; on every run of the sweep or phase
                 "m2_main": ["--stagger", "on"], "m2_check": ["--stagger", "on"],  # M2 §3.1, both arms
                 "m4_main": ["--stagger", "on"], "m4_check": ["--stagger", "on"]}  # M4 §3.2, both arms
M2_CHECK = (8, "main", ("C", "Cp"))  # M2 §7 step 3: channels, condition, arms in run order, one run each
M4_CHECK = (8, "main", ("C8", "C10"))  # M4 §8 step 3, "Arms": the same
M4_POINTS = {"m4_main": "main", "m4_aux": "aux", "m4_check": "main"}  # M4 §3.2: the point of each M4 phase
SWEEP_REPETITIONS = {"a55": 1}  # A12 (§13: × 1); the other sweeps have REPETITIONS
IDLE_BASELINE_S = 120  # §7.2
H3_START, SUSTAIN_FPS = 8, 4.75  # §10 (min(8, L)), HYPOTHESES H3
H3_PHASES = ("h3_search", "h3_confirm")
ORDER = ("main", "aux", "h3", "reference", "4k", "k4", "a55", "bdirect_main", "bdirect_aux",  # comparison.md
         "m2_main", "m2_aux",  # M2 §5.3: main first
         "m4_main", "m4_aux")  # M4 §3.3: main first
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
    """§9.1: B->C in repetitions 1 and 3, C->B in repetition 2. Three arms rotate (A12): B->C->Bd,
    C->Bd->B, Bd->B->C."""
    k = (repetition - 1) % len(variants)
    return tuple(variants[k:]) + tuple(variants[:k])


def bench_args(arm, phase):
    """bench_main's options for an arm in a phase: --variant, then K, the post-processing, the cores or the
    sampler where A12 or M2 change them."""
    return ARMS.get(arm, ["--variant", arm]) + SWEEP_OPTIONS.get(phase, [])


def is_m2(name):
    """Whether name is a sweep of M2 (run kind m2, M2 §5.1)."""
    return name in SWEEPS and SWEEPS[name][3] == "m2"


def m2_session(args):
    """Whether a session is M2's, an m2 sweep or m2-check: its headers carry M2's commits (M2 §5.1)."""
    return is_m2(getattr(args, "sweep", None)) or getattr(args, "command", None) == "m2-check"


def is_m4(name):
    """Whether name is a sweep of M4 (run kind m4, M4 §4)."""
    return name in SWEEPS and SWEEPS[name][3] == "m4"


def m4_phase(phase):
    """Whether a run's phase is M4's, an m4 sweep or m4-check: its runs carry freeze-m4's commit, and its
    arms' saturation labels decide rule 3 (M4 §3.2, §4)."""
    return is_m4(phase) or phase == "m4_check"


def m4_session(args):
    """Whether a session is M4's, an m4 sweep or m4-check: its headers carry M4's commits (M4 §4)."""
    return is_m4(getattr(args, "sweep", None)) or getattr(args, "command", None) == "m4-check"


def rule3_applies(phase, variant, channels, condition, limit, labels):
    """Whether §9.3 rule 3 judges a run, which it does at unsaturated conditions. For M4's runs that is the
    arm's saturation label at the point (M4 §3.2): only "unsaturated" applies it. For the others, §6.2."""
    if m4_phase(phase):
        return labels.get(variant) == "unsaturated"
    return phase in ("thermal", "m2_check") + tuple(SWEEPS) and unsaturated(channels, condition, limit)


def m4_labels(calibration_csv, point):
    """{arm: saturation label} at a point (main or aux), from tools/m4_calib_summary.py's
    m4_calibration.csv (M4 §3.2). Stop for any other label: "incomplete" means that session stopped, and
    M4 §3.1 then has a new one run in full."""
    rows = summary_values(calibration_csv)[1]
    out = {}
    for arm, (_, _, model) in M4_MODELS.items():
        label = rows.get(f"{model}_{point}_point")
        if label not in LABELS:
            raise Stop(f"{calibration_csv}: {model}_{point}_point is {label!r}, not one of {', '.join(LABELS)}")
        out[arm] = label
    return out


def summary_values(path):
    """(header, {key: value}) of a results CSV with a key,value table. Stop if it cannot be read."""
    try:
        header, rows = read_csv(path)
        return header, {r["key"]: r["value"] for r in rows}
    except (OSError, KeyError, UnicodeDecodeError, csv.Error) as e:
        raise Stop(f"cannot read {path}: {e!r}") from None


def read_text(path):
    """The text of path. Stop if it cannot be read."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise Stop(f"cannot read {path}: {e!r}") from None


def read_header(path):
    """The "# key: value" lines at the top of a results CSV, without its table (a crashed run can leave a
    table cut short). Stop if it cannot be read."""
    header = {}
    for line in read_text(path).splitlines():
        if not line.startswith("# "):
            break
        k, _, v = line[2:].partition(": ")
        header[k] = v
    return header


def has_rows(path):
    """Whether a TSV with a header line has a row after it."""
    return Path(path).exists() and len(read_text(path).splitlines()) > 1


def calib_amendment(d):
    """The amendment of a calib_npu session: its m4_calibration.csv's header, else the first of its runs'
    summary.csv headers; None when it has neither (it stopped before a run wrote one)."""
    for path in [d / "m4_calibration.csv"] + sorted(d.glob("*/summary.csv")):
        if path.exists():
            return read_header(path).get("amendment") or None
    return None


def amendment_number(amendment):
    m = re.fullmatch(r"A(\d+)", amendment or "")
    return int(m[1]) if m else None


def calib_candidate(d, freeze_m4):
    """Whether d can be M4's calib_npu session: a tools/m4_calib.sh session (results/m4/<UTC>_m4_calib) with
    M4's settings and freeze-m4's commit in its session.txt, 10 minutes idle in its baseline.txt once that
    exists, and at least one attempt in runs.tsv. Smoke tests and sessions that ran nothing do not count."""
    if not re.fullmatch(SESSION_NAME + M4_CALIB, d.name) or not (d / "session.txt").exists():
        return False
    s = keyed(read_text(d / "session.txt"))
    if any(s.get(k) != v for k, v in M4_CALIB_SETTINGS.items()) or s.get("freeze_m4_commit") != freeze_m4:
        return False
    if (d / "baseline.txt").exists() and keyed(read_text(d / "baseline.txt")).get("idle_s") != "600":
        return False
    return has_rows(d / "runs.tsv")


def calib_start(d, header):
    """When a calib_npu session started: the earliest of its summary's start_time_utc and env.txt's
    time_utc, the earliest record in it (tools/check_prereg_order.py takes the directory's earliest)."""
    times = [header.get("start_time_utc", "")]
    if (d / "env.txt").exists():
        times.append(keyed(read_text(d / "env.txt")).get("time_utc", ""))
    parsed = [parse_utc(t) for t in times if t]
    return min(parsed) if parsed else None


def m4_calib_choice(freeze_m4, freeze_created):
    """M4 §3.2: the calib_npu session whose medians decide, as (directory, None), or (None, why none does).
    The candidates are the sessions of calib_candidate, in UTC order.
      - Their amendment is the latest candidate's: a session rerun whole after an amendment (§9) is made with
        a later one, and replaces those of earlier ones. None decides if the latest candidate stopped before
        a run wrote its summary (a new one is run in full, §3.1), or was made with an older amendment than an
        earlier one.
      - Of that amendment, the first session whose m4_calibration.csv says complete 1 and m4_settings yes,
        with freeze-m4's commit, and which started after freeze-m4's created_at and once the four accuracy
        passes were done (§8 step 5, tools/m4_order.py), decides. §3.1 has one session, so a later one
        does not replace it."""
    sessions = [d for d in sorted(Path("results", "m4").glob("*_" + M4_CALIB)) if calib_candidate(d, freeze_m4)]
    if not sessions:
        return None, "there is no calib_npu session with M4's settings and freeze-m4's commit in results/m4/"
    amendments = {d: calib_amendment(d) for d in sessions}
    latest = sessions[-1]
    amendment = amendments[latest]
    if amendment_number(amendment) is None:
        return None, (f"the latest calib_npu session, {latest.name}, stopped before any run wrote its summary "
                      f"(amendment {amendment}): a new one is run in full (M4 §3.1)")
    newest = max(n for n in map(amendment_number, amendments.values()) if n is not None)
    if amendment_number(amendment) < newest:
        return None, (f"the latest calib_npu session, {latest.name}, was made with {amendment}, after one made with "
                      f"A{newest}")
    attempts = m4_order.pass_attempts(Path("results", "m4"), freeze_m4)
    for d in sessions:
        summary = d / "m4_calibration.csv"
        if amendments[d] != amendment or not summary.exists():
            continue
        header, v = summary_values(summary)
        if v.get("complete") != "1" or v.get("m4_settings") != "yes" or header.get("freeze_m4_commit") != freeze_m4:
            continue
        start = calib_start(d, header)
        if freeze_created and not (start and start > parse_utc(freeze_created)):
            continue  # not §8 step 5's session; tools/check_prereg_order.py reports it
        done, _ = m4_order.passes_status(attempts, before=start)
        if not (start and done and done < start):
            continue  # before the accuracy passes were done (§8 step 5): the same
        return d, None
    return None, (f"no calib_npu session of {amendment}, the latest session's amendment, is complete with M4's "
                  "settings and freeze-m4's commit, after freeze-m4's created_at and the accuracy passes")


def m4_pins():
    """[(session name, its saturation.txt)] of M4's protocol pipeline sessions that made an attempt (in
    started.tsv); one without saturation.txt gives {}. A session that stopped before its first run took
    nothing."""
    out = []
    for sweep in ("m4_main", "m4_aux"):
        for d in sweep_sessions(sweep):
            if has_rows(d / "started.tsv"):
                f = d / "saturation.txt"
                out.append((d.name, keyed(read_text(f)) if f.exists() else {}))
    return out


def file_sha256(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as e:
        raise Stop(f"cannot read {path}: {e!r}") from None


def m4_gates_problem(freeze_m4):
    """Why M4's calib_npu session (tools/m4_order.py gate, for tools/m4_calib.sh) or a protocol pipeline
    session cannot start yet, or None (tools/m4_order.py):
      - §8 step 5: the four accuracy passes of freeze-m4's code in results/m4/ come first. Each is valid,
        or was invalid twice (§5), and none is running. The FP32 passes' records come from the PC
        (tools/m4_send_record.py). No build/m4_accuracy runs at all, whatever its record says.
      - §8 step 3 and §5: a passed wiring check in results/check/ covers this build's four post-processing
        objects (yolov10_post's and the shared function's among them), for C8 and for C10."""
    running = m4_order.tools_running()
    if running:
        return (f"build/m4_accuracy runs (PID {', '.join(running)}): M4's sessions do not overlap a pass or a "
                "check of the board tool (M4 §8 step 5)")
    _, problems = m4_order.passes_status(m4_order.pass_attempts(Path("results", "m4"), freeze_m4,
                                                                alive=m4_order.board_alive))
    if problems:
        return ("M4's accuracy passes come before its calib_npu and pipeline sessions (M4 §8 step 5): "
                + "; ".join(problems))
    objects = {M4_POST_OBJECTS[k]: sha for k, sha in post_object_hashes(M4_POST_OBJECTS).items()}
    missing = [p for p in M4_POST_OBJECTS.values() if p not in objects]
    if missing:
        return f"this build has no {', '.join(missing)} (M4 §8 step 3)"
    problems = m4_order.wiring_problems(Path("results", "check"), objects,
                                        {name: sha for _, sha, name in M4_MODELS.values()})
    return "; ".join(problems) if problems else None


def m4_model_problems():
    """The models of M4's arms whose SHA-256 is not M4 §1's."""
    out = []
    for arm, (path, sha, model) in M4_MODELS.items():
        got = hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).exists() else "none (no file)"
        if got != sha:
            out.append(f"{path} ({arm}) is not M4's {model}: SHA-256 {got}, M4 §1 fixes {sha}")
    return out


def arm_post(arm):
    """The post-processing an arm passes to bench_main (--post), or m1, bench_main's default."""
    args = ARMS.get(arm, [])
    return args[args.index("--post") + 1] if "--post" in args else "m1"


def sweep_plan(sweep, counts=None):
    """[(channels, repetition, variants in run order)] of a sweep, in run order."""
    all_counts, _, variants, _ = SWEEPS[sweep]
    return [(c, r, variant_order(variants, r)) for c in channel_order(counts or all_counts)
            for r in range(1, SWEEP_REPETITIONS.get(sweep, REPETITIONS) + 1)]


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


def schedstat_run_ns(text):
    """(version, {cpu: rq_cpu_time in ns}) from /proc/schedstat: the 7th number after the label of each
    cpu<N> line, as bench_main reads it (M2 §3.2). Domain lines and the others are skipped."""
    version, out = "unknown", {}
    for line in text.splitlines():
        f = line.split()
        if len(f) == 2 and f[0] == "version":
            version = f[1]
        elif len(f) >= 8 and re.fullmatch(r"cpu\d+", f[0]):
            out[int(f[0][3:])] = int(f[7])
    return version, out


def idle_rows(before, after, tick_s, prefix="system"):
    """§7.2's idle baseline as rows named like bench_main's summary: per CPU, per cluster and total.
    tick_s: seconds per count; prefix "schedstat" for /proc/schedstat's ns (M2 §3.2)."""
    d = {c: (after[c] - before[c]) * tick_s for c in sorted(after) if c in before}
    rows = [(f"{prefix}_cpu{c}_s", f"{v:.3f}") for c, v in d.items()]
    for name, cpus in (("0-3", range(0, 4)), ("4-5", range(4, 6)), ("6-7", range(6, 8))):
        rows.append((f"{prefix}_cpu{name}_s", f"{sum(d.get(c, 0.0) for c in cpus):.3f}"))
    rows.append((f"{prefix}_cpu_total_s", f"{sum(d.values()):.3f}"))
    return rows


def schedstat_rows(before, after):
    """M2 §3.2's idle baseline from /proc/schedstat's text at the two moments: the version, then rows as
    idle_rows gives them. None for either text (no /proc/schedstat) gives no rows."""
    if before is None or after is None:
        return []
    (version, b), (_, a) = schedstat_run_ns(before), schedstat_run_ns(after)
    return [("schedstat_version", version)] + idle_rows(b, a, 1e-9, "schedstat")


def registration_rows(text):
    """{stage: (tag commit, Release created_at)} from the Registration table of docs/notes/env.md, for the
    rows that have both."""
    out, section = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        m = re.match(r"^\|\s*\[?`([^`]+)`\]?(?:\([^)]*\))?\s*\|\s*`([0-9a-f]{40})`\s*\|"
                     r"\s*(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\s*\|\s*$", line)
        if section == "Registration" and m:
            out[m.group(1)] = (m.group(2), m.group(3))
    return out


def registration_commits(text):
    """{stage: tag commit} of registration_rows."""
    return {k: commit for k, (commit, _) in registration_rows(text).items()}


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


def video_problems(assignment_tsv, expected):
    """The server's assignment.tsv (channel, video, SHA-256) against the expected {video name: SHA-256}."""
    problems = []
    for line in assignment_tsv.splitlines():
        f = line.split("\t")
        if len(f) != 3:
            problems.append(f"bad line in the server's assignment.tsv: {line!r}")
            continue
        name = Path(f[1]).name
        if name not in expected:
            problems.append(f"{f[0]} serves {name}, which is not in the set")
        elif f[2] != expected[name]:
            problems.append(f"{f[0]} serves {name} with SHA-256 {f[2]}, the set has {expected[name]}")
    if not assignment_tsv.strip():
        return ["the server's assignment.tsv is empty"]
    served = {Path(line.split("\t")[1]).name for line in assignment_tsv.splitlines() if line.count("\t") == 2}
    return problems + [f"no channel serves {name}" for name in sorted(set(expected) - served)]


def keyed(text):
    return {k: v for k, _, v in (line.partition(": ") for line in text.splitlines()) if v}


def read_optional(path):
    """The text of path, or None if it cannot be read (/proc/schedstat needs CONFIG_SCHED_INFO, M2 §3.2)."""
    try:
        return Path(path).read_text()
    except OSError:
        return None


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
        self.freeze_m2 = self.freeze_m4 = "none"  # start() reads them (session_freezes)
        self.labels, self.calib = {}, None  # M4 §3.2: {arm: saturation label}, and the session they come from
        self.arm_headers = {}  # M4: each arm's first run header, for result.csv
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
        self.code_commit = build_commit()
        self.freeze_m2, self.freeze_m4 = session_freezes(a)
        self.start_server()
        self.say(f"idle for {a.idle_s} s")
        temps = []
        from_s = max(0, a.idle_s - IDLE_BASELINE_S)  # §7.2's idle baseline: the idle period's last 120 s
        for i in range(a.idle_s):
            if i == from_s:
                self.idle = [dt.datetime.now(dt.timezone.utc), time.monotonic(), Path("/proc/stat").read_text(),
                             read_optional("/proc/schedstat")]  # M2 §3.2
            time.sleep(1)
            if i >= a.idle_s - 60:
                temps.append(self.soc())
        self.idle += [time.monotonic(), Path("/proc/stat").read_text(), read_optional("/proc/schedstat")]
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
        if VIDEO_SHA256:
            problems = video_problems(self.pc(f"cat {self.server_dir}/assignment.tsv"), VIDEO_SHA256)
            if problems:
                raise Stop("; ".join(problems[:5]))
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
        cmd = ["./build/bench_main", *bench_args(variant, phase), "--channels", str(channels), "--source", source,
               "--condition", condition, "--warmup", str(a.warmup), "--duration", str(a.duration),
               "--run-kind", run_kind, "--repetition", str(repetition), "--freeze-commit", FREEZE_M1_COMMIT,
               *(["--freeze-m2-commit", self.freeze_m2] if is_m2(phase) else []),  # M2 §5.1; M1's as before
               *(["--freeze-m4-commit", self.freeze_m4] if m4_phase(phase) else []),  # M4 §4
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
            self.judge(o, run_dir, phase, variant, channels, condition, start, end, console_before)
        except Interrupted as e:
            o.reasons.append(f"stopped by the operator before the run was judged ({e})")
            o.external = "stopped by the operator"
        return o, run_dir, temp, start, end

    def judge(self, o, run_dir, phase, variant, channels, condition, start, end, console_before):
        """Fills o for a finished bench run (§9.3)."""
        a = self.args
        made = run_dir.exists()  # bench_main makes it once its options pass (M4: m4_rule_problem)
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
            if m4_phase(phase) and not made and o.code != 0:  # it refused the run: the setup, not the run
                o.reasons[-1] += " before it made its directory: it refused the run's options (see its log)"
                o.external = o.external or "bench_main refused an M4 run's options"
        else:
            header, rows = read_csv(summary_path)
            self.run_header = self.run_header or header
            if m4_phase(phase):  # M4 §1, §3.2: the run is its arm's; anything else is the setup's fault
                want = (M4_MODELS[variant][1], arm_post(variant))
                got = (header.get("model_sha256"), header.get("post"))
                if got != want:
                    o.reasons.append(f"the run has model {got[0]} with post-processing {got[1]}, not arm {variant}'s "
                                     f"{want[0]} with {want[1]} (M4 §1)")
                    o.external = "a run's model or post-processing is not its arm's"
                    return
                self.arm_headers.setdefault(variant, header)
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
            if rule3_applies(phase, variant, channels, condition, limit, self.labels):
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
        if getattr(a, "calib", None) and getattr(a, "command", None) == "sweep":
            off.append(f"calib {a.calib} (protocol: the latest complete M4 calib_npu session in results/m4/)")
        with open(self.out / "runs.tsv", encoding="utf-8") as f:
            starts = [r["start_utc"] for r in csv.DictReader(f, delimiter="\t")]
        h = self.run_header or {}
        m2 = ("prereg_m2_commit", "freeze_m2_commit") if m2_session(a) else ()
        if m4_session(a):  # M4 §4: its commits, and each arm's model and post-processing, from its first run
            m4 = {"prereg_m4_commit": PREREG_M4_COMMIT, "freeze_m4_commit": self.freeze_m4}
            models = {f"{k}_{arm}": self.arm_headers.get(arm, {}).get(k, "")
                      for arm in M4_MODELS for k in ("model_sha256", "post")}
        else:
            m4, models = {}, {"model_sha256": h.get("model_sha256", "")}
        header = {"start_time_utc": min(starts) if starts else "", "ntp_synchronized": h.get("ntp_synchronized", ""),
                  "prereg_v1_commit": h.get("prereg_v1_commit", ""), "freeze_m1_commit": h.get("freeze_m1_commit", ""),
                  **{k: h.get(k, "") for k in m2},  # M2 §5.1, from the first run's header
                  **m4, "run_kind": run_kind, **models, "decoder": h.get("decoder", ""),
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


def loop_check_done(video_set):
    """Whether a loop check of the video set has completed with the protocol's settings:
    results/check/<UTC>_loop_<set> whose result.csv says protocol_settings yes. Whether its videos
    passed does not matter here (A11: a video that fails is reported, and the runs go ahead)."""
    for d in Path("results", "check").glob(f"*_loop_{video_set}"):
        result = d / "result.csv"
        if re.fullmatch(SESSION_NAME + "loop_" + re.escape(video_set), d.name) and result.exists():
            rows = {r["key"]: r["value"] for r in read_csv(result)[1]}
            if rows.get("protocol_settings") == "yes":
                return True
    return False


def build_commit():
    """The commit build/ was built from (tools/deploy_board.sh sets CODE_COMMIT), or "unknown"."""
    cache = Path("build/CMakeCache.txt")
    commit = re.search(r"^CODE_COMMIT:STRING=(.*)$", cache.read_text(), re.M) if cache.exists() else None
    return commit.group(1) if commit else "unknown"


def bdirect_check_done(commit):
    """Whether tools/bdirect_check has passed with the binary of the runs (A12): results/check/
    <UTC>_bdirect_check whose check.csv says passed 1, built from commit with amendment A12 or later."""
    for d in Path("results", "check").glob("*_bdirect_check"):
        check = d / "check.csv"
        if re.fullmatch(SESSION_NAME + "bdirect_check", d.name) and check.exists():
            header, rows = read_csv(check)
            amendment = re.fullmatch(r"A(\d+)", header.get("amendment", ""))
            if (amendment and int(amendment[1]) >= 12 and header.get("code_commit") == commit
                    and {r["key"]: r["value"] for r in rows}.get("passed") == "1"):
                return True
    return False


def freeze_m2_commit():
    """freeze-m2's tag commit from the Registration table of docs/notes/env.md, or "none" before it
    (M2 §5.1)."""
    return registration_commits(Path(ENV_MD).read_text(encoding="utf-8")).get("freeze-m2", "none")


def freeze_m4_registration():
    """(freeze-m4's tag commit, its Release created_at) from the Registration table of docs/notes/env.md, or
    ("none", None) before it (M4 §4)."""
    try:
        text = Path(ENV_MD).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise Stop(f"cannot read {ENV_MD}: {e!r}") from None
    return registration_rows(text).get("freeze-m4", ("none", None))


def freeze_m4_commit():
    return freeze_m4_registration()[0]


def session_freezes(args):
    """(freeze-m2's commit, freeze-m4's commit) that a session's runs pass. M2's sweeps take freeze-m2's;
    m2-check runs before it and passes none (M2 §7). M4's sessions, m4-check too, take freeze-m4's, which is
    none before it (M4 §4): a check that §5 repeats after freeze-m4 names it."""
    return (freeze_m2_commit() if is_m2(args.sweep) else "none",
            freeze_m4_commit() if m4_session(args) else "none")


def post_object_hashes(objects=POST_OBJECTS):
    """{key: SHA-256} of this build's post-processing objects (POST_OBJECTS, or M4_POST_OBJECTS); a missing
    one is left out."""
    return {k: hashlib.sha256(Path(p).read_bytes()).hexdigest() for k, p in objects.items() if Path(p).exists()}


def m2_equivalence_done(hashes):
    """Whether M2 §4's equivalence check has passed with these post-processing objects: results/check/
    <UTC>_m2_equivalence whose m2_equivalence.csv says passed 1, with the SHA-256 of both objects equal
    to hashes (post_object_hashes). Objects that differ need the check again with their build (M2 §4).
    A check of the same objects in which a frame or a set differed stops Cp for good: the decoding is
    deterministic, so a later pass of the same objects does not undo it (M2 §4, "On failure"). A check
    that is incomplete (the wrong videos, say) neither passes nor stops them."""
    passed = False
    for d in Path("results", "check").glob("*_m2_equivalence"):
        check = d / "m2_equivalence.csv"
        if re.fullmatch(SESSION_NAME + "m2_equivalence", d.name) and check.exists():
            rows = {r["key"]: r["value"] for r in read_csv(check)[1]}
            if all(k in hashes and rows.get(k) == hashes[k] for k in POST_OBJECTS):
                if rows.get("differing_total", "0") != "0":
                    return False
                passed |= rows.get("passed") == "1" and rows.get("complete") == "1"
    return passed


def equivalence_problem():
    """Why M2's or M4's runs and checks cannot start yet for want of M2 §4's equivalence check of this
    build's two objects of A13 (m2_equivalence_done), or None. M4 §8 step 3: they equal A13's, which M2's
    check passed with, or the check is repeated with M4's build."""
    if m2_equivalence_done(post_object_hashes()):
        return None
    return ("a passed equivalence check of this build's post-processing objects (their SHA-256), in "
            "results/check/<UTC>_m2_equivalence, comes before M2's and M4's runs and checks (M2 §4, §7 step 3; "
            "M4 §8 step 3)")


def order_problem(name):
    """Why a protocol session of name cannot start now (docs/notes/comparison.md), or None."""
    for earlier in ORDER[:ORDER.index(name)]:
        if progress(earlier) != "complete":
            return f"{earlier} comes before {name} and is not complete"
    if name == "4k" and not loop_check_done("4k"):
        return "the loop check of the 4K set (loop-check --set 4k) comes before the first 4K session (A11)"
    if name.startswith("bdirect_") and not bdirect_check_done(build_commit()):
        return ("a passed tools/bdirect_check of this build, in results/check/<UTC>_bdirect_check, comes before "
                "B-direct (A12)")
    if is_m2(name) and freeze_m2_commit() == "none":
        return f"freeze-m2 comes before M2's runs, and {ENV_MD} has no row for it with its commit and created_at " \
               "(M2 §5.1)"
    if is_m4(name) and freeze_m4_commit() == "none":
        return f"freeze-m4 comes before M4's runs, and {ENV_MD} has no row for it with its commit and created_at " \
               "(M4 §4)"
    problem = equivalence_problem() if is_m2(name) or is_m4(name) else None
    if problem:
        return problem
    if is_m4(name):
        problem = m4_gates_problem(freeze_m4_commit())  # the passes and the wiring check (§8 steps 3, 5)
        if problem:
            return problem
        calib, why = m4_calib_choice(*freeze_m4_registration())
        if calib is None:
            return f"M4's calib_npu session (tools/m4_calib.sh) comes before its pipeline sessions, and {why} " \
                   "(M4 §3.2, §8 step 5)"
        sha = file_sha256(calib / "m4_calibration.csv")
        for session, pin in m4_pins():
            took = (Path(pin.get("calib_npu_session", "unknown")).name, pin.get("calib_npu_csv_sha256", "unknown"))
            if took != (calib.name, sha):
                return (f"M4's pipeline session {session} took its labels from {took[0]} (m4_calibration.csv "
                        f"SHA-256 {took[1]}), and the calib_npu session that decides now is {calib.name} ({sha}): one "
                        "session decides (M4 §3.2). A rerun under an amendment (§9) needs that amendment's own "
                        "procedure")
    if progress(name) == "complete":
        return f"{name} is complete"
    return None


def write_idle_baseline(s, run_kind):
    """§7.2: system CPU over the idle period's last 120 s, from /proc/stat and, where it can be read,
    /proc/schedstat (M2 §3.2), with a results header (§1.4)."""
    start, t0, stat0, sched0, t1, stat1, sched1 = s.idle
    rows = [("window_s", f"{t1 - t0:.1f}")] + idle_rows(proc_stat_nonidle(stat0), proc_stat_nonidle(stat1),
                                                       1.0 / os.sysconf("SC_CLK_TCK"))
    if m2_session(s.args) or m4_session(s.args):  # M2 §3.2, M4 §3.3; none without /proc/schedstat.
        rows += schedstat_rows(sched0, sched1)  # M1's sessions keep their rows.
    amendments = re.findall(r"^## (A\d+) ", Path("prereg/AMENDMENTS.md").read_text(encoding="utf-8"), re.M)
    ntp = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], capture_output=True,
                         text=True).stdout.strip()
    m2 = {"prereg_m2_commit": PREREG_M2_COMMIT, "freeze_m2_commit": s.freeze_m2} if m2_session(s.args) else {}
    m4 = {"prereg_m4_commit": PREREG_M4_COMMIT, "freeze_m4_commit": s.freeze_m4} if m4_session(s.args) else {}
    header = {"start_time_utc": f"{start:%Y-%m-%dT%H:%M:%SZ}", "ntp_synchronized": ntp,
              "prereg_v1_commit": PREREG_V1_COMMIT, "freeze_m1_commit": FREEZE_M1_COMMIT, **m2, **m4,
              "run_kind": run_kind,
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
                        ("repetitions_left", " ".join(left) or "none")]
                       + (list(post_object_hashes().items()) if is_m2(a.sweep) else [])  # M2 §4
                       + (m4_rows(s) if is_m4(a.sweep) else []), s.run_kind)


def m4_rows(s):
    """M4's rows of result.csv: the calib_npu session that gave the labels, each arm's label at the point
    (M4 §3.2), and the SHA-256 of the build's four post-processing objects (M4 §8 step 3)."""
    return ([("calib_npu_session", str(s.calib) if s.calib else "none")]
            + [(f"saturation_{arm}", s.labels[arm]) for arm in M4_MODELS]
            + list(post_object_hashes(M4_POST_OBJECTS).items()))


def cmd_m2_check(s):
    """M2 §7 step 3: one run of each arm at 8 channels, main condition, staggered, before freeze-m2."""
    channels, condition, arms = M2_CHECK
    write_idle_baseline(s, "check")
    rows, status = [], "stopped"
    try:
        for arm in arms:
            valid, run_dir = s.run("m2_check", arm, channels, condition, "check")
            rows.append((f"{arm}_run", run_dir.name if valid else "invalid_x2"))
        status = "completed"
    finally:  # also when the session stops: what it ran, and the objects it ran with (M2 §4)
        s.write_result([("session_status", status)] + rows + list(post_object_hashes().items()), "check")


def cmd_m4_check(s):
    """M4 §8 step 3, "Arms": one run of each arm, C8 then C10, at 8 channels, main condition, staggered,
    before freeze-m4."""
    channels, condition, arms = M4_CHECK
    write_idle_baseline(s, "check")
    rows, status = [], "stopped"
    try:
        for arm in arms:
            valid, run_dir = s.run("m4_check", arm, channels, condition, "check")
            rows.append((f"{arm}_run", run_dir.name if valid else "invalid_x2"))
        status = "completed"
    finally:  # also when the session stops: what it ran, its labels and its objects
        s.write_result([("session_status", status)] + rows + m4_rows(s), "check")


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
    ap.add_argument("command", choices=["limit", "loop-check", "thermal", "sweep", "h3", "m2-check", "m4-check"])
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
    ap.add_argument("--set", choices=list(VIDEO_SETS), help="loop-check: the video set (default m1)")
    ap.add_argument("--calib", help="m4-check, and an m4 sweep as a smoke test: the tools/m4_calib.sh session whose "
                                    "saturation labels decide rule 3 (M4 §3.2)")
    args = ap.parse_args(argv)
    if (args.command == "sweep") != (args.sweep is not None):
        ap.error(f"sweep needs one of {', '.join(SWEEPS)}; the other commands take none")
    if args.counts and (args.command != "sweep" or not set(args.counts) <= set(SWEEPS[args.sweep][0])
                        or len(set(args.counts)) != len(args.counts)):
        ap.error("--counts is for sweep, with distinct counts of that sweep")
    if args.limit is not None and args.command != "thermal":
        ap.error("--limit is for thermal")
    if args.set and args.command != "loop-check":
        ap.error("--set is for loop-check; sweep 4k uses the 4K set by itself")
    m4 = args.command == "m4-check" or is_m4(args.sweep)
    if args.calib and not m4:
        ap.error("--calib is for m4-check and the m4 sweeps")
    if args.calib and not (Path(args.calib) / "m4_calibration.csv").exists():
        ap.error(f"{args.calib} has no m4_calibration.csv (tools/m4_calib_summary.py)")
    video_set = args.set or ("4k" if args.sweep == "4k" else "m1")
    global ASSIGNMENT, PC_VIDEOS, MANIFEST, VIDEO_SHA256
    ASSIGNMENT, PC_VIDEOS, MANIFEST, VIDEO_SHA256 = VIDEO_SETS[video_set]
    if args.command == "thermal" and not (args.limit and 1 <= args.limit <= 32):
        ap.error("thermal needs --limit L (1..32)")
    if args.idle_s < 60:
        ap.error("--idle-s must be at least 60")
    if not 1 <= args.start <= args.cap <= 32:
        ap.error("need 1 <= --start <= --cap <= 32 (the assignment has 32 channels)")
    now = f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}"
    smoke = any(getattr(args, k) != v for k, v in PROTOCOL.items()) or bool(args.counts) \
        or (bool(args.calib) and args.command == "sweep")
    if args.command in ("sweep", "h3"):
        name = args.sweep or "h3"
        run_kind = "check" if smoke else session_kind(name)
        if args.out and Path(args.out).parent != Path("results", run_kind):
            ap.error(f"this session goes to results/{run_kind}/" + ("" if smoke else "<UTC>_" + name))
        if args.out and not smoke and not re.fullmatch(SESSION_NAME + re.escape(name), Path(args.out).name):
            ap.error(f"a protocol session is named <UTC>_{name}, where the next one finds it")
        args.out = args.out or f"results/{run_kind}/{now}_{name}" + ("_smoke" if smoke else "")
    else:
        kind = {"limit": "limit", "loop-check": "loop", "thermal": "thermal", "m2-check": "m2_check",
                "m4-check": "m4_check"}[args.command]
        run_kind = "check" if kind in ("loop", "m2_check", "m4_check") else "calibration"
        args.out = args.out or f"results/{run_kind}/{now}_{kind}" + (f"_{video_set}" if video_set != "m1" else "")

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
        # Not freeze-m2 or freeze-m4: the checks come before them.
        problem = equivalence_problem() if args.command in ("m2-check", "m4-check") else None
        if problem:
            raise Stop(f"not starting: {problem}")
        if m4:
            problems = m4_model_problems()
            if problems:
                raise Stop("not starting: " + "; ".join(problems))
            # M4 §3.2: the labels at the point. order_problem has checked a protocol sweep's calib_npu session.
            point = M4_POINTS[args.sweep or "m4_check"]
            calib, why = (Path(args.calib), None) if args.calib else \
                m4_calib_choice(*freeze_m4_registration()) if args.command == "sweep" else (None, "m4-check")
            if calib is None and args.command == "sweep" and not smoke:
                raise Stop(f"not starting: {why}")
            labels = m4_labels(calib / "m4_calibration.csv", point) if calib else dict.fromkeys(M4_MODELS, "unknown")
            calib_sha = file_sha256(calib / "m4_calibration.csv") if calib else "none"
        s = Session(args)
        s.smoke, s.run_kind = smoke, run_kind
        if m4:
            s.labels, s.calib = labels, calib
            with open(s.out / "saturation.txt", "w", encoding="utf-8") as f:  # before any run (m4_pinned_calib)
                f.write(f"calib_npu_session: {calib or 'none'}\ncalib_npu_csv_sha256: {calib_sha}\npoint: {point}\n"
                        + "".join(f"label_{k}: {v}\n" for k, v in labels.items()))
                durable(f)
            s.say(f"saturation at the {point} point: " + ", ".join(f"{k} {v}" for k, v in labels.items())
                  + f" (from {calib or 'no calib_npu session'}; rule 3 only where unsaturated, M4 §3.2)")
        if args.command == "loop-check":
            s.loops = loop_channels()  # before the idle period, so a bad input fails at once
    except Stop as e:
        print(f"stream_session: {e}", file=sys.stderr)
        return 2
    try:
        s.start()
        {"limit": cmd_limit, "loop-check": cmd_loop_check, "thermal": cmd_thermal, "sweep": cmd_sweep,
         "h3": cmd_h3, "m2-check": cmd_m2_check, "m4-check": cmd_m4_check}[args.command](s)
        status = 0
    except Stop as e:
        s.say(f"stopped: {e}")
        status = 1
    finally:
        s.end()
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
