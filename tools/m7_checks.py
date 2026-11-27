#!/usr/bin/env python3
"""The checks before freeze-m7 (prereg/M7.md §8 step 3), judged from their records in results/check/.

Records, each the latest of its name (<UTC>_<name>):
  m7_cpu0_check       tools/m7_cpu0_check. "cpu0's counter": the idle count below 5% of 10 s x 1800 MHz, and the
                      difference of the two counts within 5% of 2.0 s x 1800 MHz. Its outcome decides whether
                      the report gives the counter's value (M7 §3, §5); it is not a failure of the checks.
  m7_check_counters   tools/stream_session.py m7-check --part counters. "The counters": one run of Ck with a 10 s
                      window, in which every pipeline thread of threads.csv has its group and time_running equals
                      time_enabled for each; for the decoder's group, the NPU workers' group and any other group
                      that ran at least 0.2 s, the group's cycles divided by 2208 MHz are within 5% of the
                      group's run time read with the counters; and every event counts above zero on some thread
                      of the decoder's group. Its outcome decides whether Bk, Bdk, Rck and Ck are run (M7 §1,
                      §5); it is not a failure of the checks. The arms the session tool leaves out (M7_NOT_RUN)
                      must agree with it. An event other than cpu_cycles and inst_retired that cannot be
                      opened, or counts zero on every thread of the decoder's group, is left out (M7 §1): the
                      check is judged on the events that remain, and the checks fail until the event is
                      removed from the group (core/perf_counters.h) and the counters' check has run again.
                      The metrics thread, which runs on cpu0 at 1800 MHz, is not in the cycles' comparison.
  m7_unchanged        tools/m7_unchanged.sh. "Unchanged arms": the objects M7 §8 step 2 lists as not changed have
                      the SHA-256 of freeze-m6's build's, compiled with the same flags; for B, Bd, Rc, C and Cp
                      the input tensors, the NPU's outputs and the detections on 100 frames of video ① equal
                      those of freeze-m6's build.
  m7_check_rest       tools/stream_session.py m7-check --part rest. The default path: one run of C at 2 channels
                      whose threads, affinities and queues equal those of M1's 32_main_C_2ch_r1. "The
                      snapshots": in one run of C with --snapshot on, both snapshots exist and parse, no thread
                      of bench_main is among the other tasks, the first is finished before the window's start,
                      and the columns of frames.csv, channels.csv and threads.csv and the keys of summary.csv
                      are those of the default run. Reported, not judged: whether cpu0's counter was read. "Cpt": as many timed calls as inferences in the window,
                      every one positive; its outcome decides whether Cpt is run (M7 §5). One valid run of each
                      arm that is run, at its point, with its process CPU and latency.
  m7_probe_smoke      tools/m7_probe.py run --only-config-1-one-frame. The probe's tool starts: configuration 1
                      on one frame (M7 §8 step 3, last line).
Every record must come from one build: the same commit in each, a full commit and not "-dirty", whose code
(src, tools, tests, CMakeLists.txt) equals the checkout's that this tool runs in. The two records of the counters'
checks may come from an earlier build: M7 §8 step 3 has the events' set committed after them, and M7 §5 the arms
that are not run. That build is one full commit too, and what differs between it and the later one is only where
those two outcomes are written (AFTER_COUNTERS) and tests/.
The order M7 §8 step 3 gives is checked on the records' start times: m7_cpu0_check and m7_check_counters before
m7_unchanged, m7_check_rest and m7_probe_smoke; and m7_check_rest, which ends with the arms' 10 s runs, last.
That the counters' outcome was committed before any other run of a counted arm is shown by the commits' times:
the records of m7_cpu0_check and m7_check_counters must be in a git commit whose time is before the start of
m7_check_rest.

Usage, from the repository: python3 tools/m7_checks.py [--results DIR] [--out FILE]
  --results  where the records are (default results/check)
  --out      also write the rows there, as a CSV with a header (PROTOCOL §1.4, run kind check)
Prints key,value rows. Exit 0 when every check passed, 1 when one did not, 2 when a record is missing or
cannot be read.
"""
import argparse
import csv
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cpu_breakdown  # noqa: E402  (the thread groups)
import m2_checks  # noqa: E402  (the default path's comparison with M1's run)
import m6_checks  # noqa: E402  (the checks M7 takes over as they are)
import stream_session as ss  # noqa: E402

RECORDS = ("m7_cpu0_check", "m7_check_counters", "m7_unchanged", "m7_check_rest", "m7_probe_smoke")
ARMS = ("b", "bd", "rc", "c", "cp")  # tools/m6_arms_dump's file stems for B, Bd, Rc, C and Cp
FRAMES = 100  # M7 §8 step 3
UNCHANGED_SOURCES = (  # M7 §8 step 2, "Not changed"
    "src/preprocess/cpu_common.cpp", "src/preprocess/cpu_copy.cpp", "src/preprocess/cpu_direct.cpp",
    "src/preprocess/cpu_sw.cpp", "src/preprocess/rga_copy.cpp", "src/preprocess/zero_copy_rga.cpp",
    "src/preprocess/cpu_yuv.cpp", "src/core/npu.cpp", "src/core/video_source.cpp", "src/core/slot_pool.cpp",
    "src/core/metrics.cpp", "src/core/pipeline_post.cpp", "src/core/yolov8_post.cpp",
    "src/core/yolov8_post_neon.cpp", "src/core/yolov10_post.cpp")
A76_HZ_INT = 2208 * 10**6  # A7: the A76 cores' fixed clock
A76_HZ = float(A76_HZ_INT)
GROUP_MIN_NS = 200 * 10**6  # M7 §8 step 3: any other group that ran at least 0.2 s (within 5%: counters_problems)
NEEDED_GROUPS = ("dec", "npu")  # the decoder's group and the NPU workers', whatever they ran
NEEDED_EVENTS = ("cpu_cycles", "inst_retired")  # M7 §1: without either, the counted arms are not run
# What a commit made after the counters' checks may change: the group's events (M7 §1), the arms that are not run
# (M7 §5) with this tool's reading of them, and tests.
AFTER_COUNTERS = ("src/core/perf_counters.h", "tools/stream_session.py", "tools/m7_checks.py")
EARLY_RECORDS = ("m7_cpu0_check", "m7_check_counters")
SAME_COLUMNS = ("frames.csv", "channels.csv", "threads.csv")  # with summary.csv's keys: "columns and rows"


class Missing(Exception):
    pass


def keyed_csv(path):
    """(header, {key: value}) of a "# key: value" header and a key,value table."""
    try:
        header, rows = ss.read_csv(path)
    except OSError as e:
        raise Missing(f"cannot read {path}: {e!r}") from None
    return header, {r["key"]: r["value"] for r in rows}


def unchanged_problems(objects, flags_m7, flags_ref, dump_m7, dump_ref):
    """Why the earlier arms are not shown unchanged; [] if they are. objects: rows of objects.csv (source,
    sha256_m7, sha256_freeze_m6); flags_*: the two builds' flags, with their own directory replaced; dump_*:
    tools/m6_arms_dump's rows from each build."""
    problems = []
    listed = sorted(o["source"] for o in objects)
    if listed != sorted(UNCHANGED_SOURCES):
        problems.append(f"the objects compared are not the {len(UNCHANGED_SOURCES)} of M7 §8 step 2: {listed}")
    problems += [f"object of {o['source']} differs from freeze-m6's build's" for o in objects
                 if o["sha256_m7"] != o["sha256_freeze_m6"] or len(o["sha256_m7"]) != 64]
    if flags_m7 != flags_ref or not flags_m7.strip():
        problems.append("the two builds' compile flags, defines or include paths differ")
    for name, d in (("this build", dump_m7), ("freeze-m6's build", dump_ref)):
        if d.get("frames") != str(FRAMES):
            problems.append(f"{name}'s dump has {d.get('frames')} frames, not {FRAMES}")
    for arm in ARMS:
        for ext, what in ((".u8", "input tensors"), (".i8", "outputs"), (".detections", "detections")):
            a, b = dump_m7.get(f"sha256_{arm}{ext}"), dump_ref.get(f"sha256_{arm}{ext}")
            if not a or a != b:
                problems.append(f"{arm}: the {what} differ from freeze-m6's build's")
    return problems


def cpu0_problems(check):
    """Why cpu0's counter did not pass its check; [] if it did. check: check.csv's {key: value}. The two
    criteria are computed again from the counts."""
    try:
        idle, loop = int(check["idle_cycles"]), int(check["loop_cycles"])
        span, loop_s, hz = float(check["span_s"]), float(check["loop_s"]), float(check["clock_hz"])
    except (KeyError, ValueError) as e:
        return [f"the record cannot be read: {e!r}"]
    problems = []
    if (span, loop_s, hz) != (10.0, 2.0, 1800e6):
        problems.append(f"the spans or the clock are not M7 §8 step 3's: {span} s, {loop_s} s, {hz} Hz")
    for name in ("idle", "loop"):
        if check.get(f"{name}_read_ok") != "1":
            problems.append(f"the {name} span's counter was not read")
    if not idle < 0.05 * span * hz:
        problems.append(f"the idle count is {idle}, not below 5% of {span:g} s x {hz / 1e6:g} MHz")
    if abs((loop - idle) - loop_s * hz) > 0.05 * loop_s * hz:
        problems.append(f"the difference of the counts is {loop - idle}, not within 5% of {loop_s * hz:.0f}")
    if not problems and check.get("passed") != "1":
        problems.append("the tool's own verdict is not passed")
    return problems


def counter_groups(counter_rows, events):
    """{group: {"run_ns", "run_s", "threads", event: count, ...}} of counters.csv's rows that opened and were
    read, by tools/cpu_breakdown's thread groups, with the metrics thread in a group of its own (it runs on cpu0,
    at another clock). run_ns: the run time read with the counters (M7 §1)."""
    out = {}
    for r in counter_rows:
        if r["opened"] != "1" or r["read_ok"] != "1":
            continue
        name = "metrics" if r["comm"] == "metrics" else cpu_breakdown.group(r["comm"])
        g = out.setdefault(name, dict.fromkeys(events, 0) | {"run_ns": 0, "run_s": 0.0, "threads": 0})
        g["run_ns"] += int(r["run_ns_disable"]) - int(r["run_ns_enable"])
        g["run_s"] += (int(r["run_ns_disable"]) - int(r["run_ns_enable"])) / 1e9
        g["threads"] += 1
        for e in events:
            g[e] += int(r[e])
    return out


def events_to_leave_out(counter_rows, event_rows):
    """The events M7 §1 leaves out, in the group's order: those that could not be opened on this kernel (not in
    sysfs, or named by a group as the event that failed to open), and those that count zero on every thread of the
    decoder's group. cpu_cycles and inst_retired are never left out: without either the counted arms are not run."""
    not_opened = {e["name"] for e in event_rows if e["found"] != "1"}
    not_opened |= {r["failed_event"] for r in counter_rows if r["opened"] != "1" and r.get("failed_event")}
    decoder = [r for r in counter_rows if cpu_breakdown.group(r["comm"]) == "dec" and r["opened"] == "1"
               and r["read_ok"] == "1"]
    zero = {e["name"] for e in event_rows if decoder and all(int(r[e["name"]] or 0) <= 0 for r in decoder)}
    return [e["name"] for e in event_rows if e["name"] in not_opened | zero and e["name"] not in NEEDED_EVENTS]


def counters_problems(threads_rows, counter_rows, event_rows):
    """Why the counters' check of M7 §8 step 3 fails, in its three parts, on the events that remain after
    events_to_leave_out; [] if it passes. threads_rows: the rows of the run's threads.csv; counter_rows: of its
    counters.csv; event_rows: of its counter_events.csv. If an event that is left out kept the groups from
    opening, the run has no groups and the first part fails: the check is run again without the event."""
    left_out = events_to_leave_out(counter_rows, event_rows)
    event_rows = [e for e in event_rows if e["name"] not in left_out]
    events = [e["name"] for e in event_rows]
    problems = [f"the group has no {e}, which the counted arms need (M7 §1)" for e in NEEDED_EVENTS if e not in events]
    problems += [f"{e['name']} is not an event of the PMU" for e in event_rows if e["found"] != "1"]
    if problems:
        return problems
    # 1. every pipeline thread has its group, and no group was multiplexed
    problems += ss.counter_problems(threads_rows, counter_rows)
    unreadable = [r["comm"] for r in counter_rows
                  if r["opened"] == "1" and r["read_ok"] == "1" and "-1" in (r["run_ns_enable"], r["run_ns_disable"])]
    if unreadable:
        problems.append(f"no run time read with the counters for: {', '.join(sorted(unreadable))}")
        return problems
    # 2. the cycles are the run time at the fixed clock
    groups = counter_groups(counter_rows, events)
    for name in NEEDED_GROUPS:
        if name not in groups:
            problems.append(f"no thread of the {name} group has a group of counters")
    for name, g in sorted(groups.items()):
        if name == "metrics" or (name not in NEEDED_GROUPS and g["run_ns"] < GROUP_MIN_NS):
            continue
        as_s = g["cpu_cycles"] / A76_HZ
        # in whole numbers: |cycles x 10^9 - run_ns x Hz| <= 5% of run_ns x Hz
        if g["run_ns"] <= 0 or 20 * abs(g["cpu_cycles"] * 10**9 - g["run_ns"] * A76_HZ_INT) > g["run_ns"] * A76_HZ_INT:
            problems.append(f"{name}: {g['cpu_cycles']} cycles are {as_s:.4f} s at 2208 MHz, not within 5% of the "
                            f"run time {g['run_s']:.4f} s")
    # 3. every event counts on some thread of the decoder's group
    problems += [f"{e} counts zero on every thread of the decoder's group" for e in events
                 if groups.get("dec", {}).get(e, 0) <= 0]
    return problems


def snapshot_check_problems(run, default):
    """Why the snapshots' check of M7 §8 step 3 fails; [] if it passes. run: the directory of a run of C with
    --snapshot on; default: of the run of C with every option at its default, at the same point."""
    state = ss.snapshots_state(run)
    if state != "complete":
        return [f"the snapshots are not both there and complete: {state}"]
    problems = []
    _, meta = keyed_csv(run / "snapshot_meta.csv")
    own = ss.read_csv(run / "snapshot_own.csv")[1]
    tasks = ss.read_csv(run / "snapshot_tasks.csv")[1]
    env = json.loads((run / "env.json").read_text(encoding="utf-8"))
    bench = {str(t["tid"]) for t in env["threads_at_window_start"]}
    for k in ("1", "2"):
        mine = {r["tid"] for r in own if r["snapshot"] == k}
        others = [r for r in tasks if r["snapshot"] == k]
        if not mine or not others:
            problems.append(f"snapshot {k} has no thread of bench_main, or no other task")
        if len(mine) != int(meta[f"snapshot{k}_own_threads"]) or len(others) != int(meta[f"snapshot{k}_other_threads"]):
            problems.append(f"snapshot {k}'s tables do not have the rows its meta file counts")
        among = sorted(r["tid"] for r in others if r["tid"] in mine | bench or r["pid"] in mine | bench)
        if among:
            problems.append(f"snapshot {k}: threads of bench_main among the other tasks: {', '.join(among)}")
        if not any(r["kernel"] == "1" for r in others) or not any(r["kernel"] == "0" for r in others):
            problems.append(f"snapshot {k} has no kernel thread, or no other process")
        for name in ("schedstat", "stat", "interrupts", "softirqs"):
            if not (run / f"snapshot{k}_{name}.txt").read_text(encoding="utf-8", errors="replace").strip():
                problems.append(f"snapshot {k}'s /proc/{name} is empty")
    if not int(meta["snapshot1_end_ns"]) < int(meta["window_start_ns"]):
        problems.append("the first snapshot is not finished before the window's start")
    if not int(meta["snapshot2_begin_ns"]) >= int(meta["window_end_ns"]):
        problems.append("the second snapshot begins before the window's end")
    for name in SAME_COLUMNS:
        a, b = (list(ss.read_csv(d / name)[1][0]) for d in (run, default))
        if a != b:
            problems.append(f"{name}'s columns differ from the default run's")
    a, b = (list(keyed_csv(d / "summary.csv")[1]) for d in (run, default))
    if a != b:
        problems.append("summary.csv's keys differ from the default run's")
    if len(ss.read_csv(run / "channels.csv")[1]) != len(ss.read_csv(default / "channels.csv")[1]):
        problems.append("channels.csv's rows differ from the default run's")
    return problems


def start_of(d, name):
    """A record's start time, from its own files."""
    if name == "m7_unchanged":
        text = (d / "reference.txt").read_text()
        return ss.parse_utc(re.search(r"^start_time_utc: (\S+)", text, re.M).group(1))
    path = {"m7_cpu0_check": "check.csv", "m7_probe_smoke": "probe.csv"}.get(name, "result.csv")
    return ss.parse_utc(ss.read_csv(d / path)[0]["start_time_utc"])


def order_problems(starts):
    """Why the records' start times are not in M7 §8 step 3's order; [] if they are. starts: {record: time}."""
    first, later = ("m7_cpu0_check", "m7_check_counters"), ("m7_unchanged", "m7_check_rest", "m7_probe_smoke")
    pairs = [(a, b) for a in first for b in later]  # the counters' checks come first
    pairs += [(a, "m7_check_rest") for a in ("m7_unchanged", "m7_probe_smoke")]  # the arms' 10 s runs come last
    return [f"{b} does not start after {a}" for a, b in pairs if starts[b] <= starts[a]]


def committed_problems(commit_times, start):
    """Why the counters' outcome is not shown committed before any other run of a counted arm; [] if it is.
    commit_times: {record: the time of the first commit that holds it, or None}; start: m7_check_rest's."""
    problems = []
    for record, t in sorted(commit_times.items()):
        if t is None:
            problems.append(f"{record} is in no commit")
        elif t >= start:
            problems.append(f"{record} was committed at {t:%Y-%m-%dT%H:%M:%SZ}, not before m7_check_rest started")
    return problems


def changed_files(a, b):
    """The files of the code that differ between two commits, or None if git cannot compare them."""
    r = subprocess.run(["git", "-c", f"safe.directory={Path.cwd()}", "diff", "--name-only", a, b, "--",
                        *ss.M7_CODE_PATHS], capture_output=True, text=True)
    return r.stdout.split() if r.returncode == 0 else None


def build_problems(commits, same_code=None, changed=changed_files):
    """Why the records are not shown to come from the build under check; [] if they are. commits: {where:
    code_commit}. The records of the counters' checks (EARLY_RECORDS) may name an earlier commit, if only
    AFTER_COUNTERS and tests/ differ between it and the others' (M7 §8 step 3); the others are judged as M6's."""
    early = {w: c for w, c in commits.items() if w.split(" ")[0] in EARLY_RECORDS}
    late = {w: c for w, c in commits.items() if w not in early}
    problems = m6_checks.commit_problems(late, same_code)
    if problems or set(early.values()) <= set(late.values()):
        return problems
    values = sorted(set(early.values()))
    if len(values) != 1 or not re.fullmatch(r"[0-9a-f]{40}", values[0]):
        return ["the counters' checks do not name one full commit: " + "; ".join(f"{w} {c}" for w, c in sorted(early.items()))]
    files = changed(values[0], next(iter(late.values())))
    if files is None:
        return [f"git cannot compare the counters' checks' build ({values[0]}) with the others'"]
    other = sorted(f for f in files if f not in AFTER_COUNTERS and not f.startswith("tests/"))
    return [f"between the counters' checks' build ({values[0]}) and the others', more than the events' set and the "
            f"arms that are not run changed: {', '.join(other)}"] if other else []


def session(d, part, rows, judge):
    """An m7-check session's result.csv, judged as a session; returns its {key: value} and its header."""
    head, result = keyed_csv(d / "result.csv")
    rows.append((f"m7_check_{part}_session_status", result.get("session_status", "")))
    problems = [] if result.get("session_status") == "completed" else ["the session did not complete"]
    if result.get("part") != part:
        problems.append(f"the session's part is {result.get('part')!r}, not {part}")
    if result.get("protocol_settings") != "yes":
        problems.append(f"the session's settings are not m7-check's: {result.get('protocol_settings')}")
    with open(d / "runs.tsv", encoding="utf-8") as f:
        attempts = list(csv.DictReader(f, delimiter="\t"))
    invalid = [r["name"] for r in attempts if r["valid"] != "1"]
    rows.append((f"m7_check_{part}_invalid_attempts", " ".join(invalid) or "none"))
    judge(f"m7_check_{part}_session", problems + [f"invalid attempt: {n}" for n in invalid])
    return head, result


def run_dir(d, result, phase, arm, channels, condition):
    """The directory of the valid run of a phase and arm in an m7-check session, or None."""
    name = result.get(f"{phase}_{arm}_{condition}_run", "")
    run = d / name if name else None
    return run if run is not None and (run / "summary.csv").exists() else None


def check(results, m1_default=m2_checks.M1_DEFAULT_RUN, commit_time=m6_checks.first_commit_time, same_code=None):
    """([(key, value)], passed) over the records under results."""
    dirs = {name: m6_checks.latest(results, name) for name in RECORDS}
    missing = [n for n, d in dirs.items() if d is None]
    if missing:
        raise Missing(f"no record in {results} for: {', '.join(missing)}")
    rows, failed, commits = [(f"record_{n}", d.name) for n, d in dirs.items()], [], {}

    def judge(key, problems):
        rows.append((key, "pass" if not problems else "FAIL: " + "; ".join(problems)))
        if problems:
            failed.append(key)

    def outcome(key, problems):  # a check whose failure M7 §5 answers: reported, and not a failure of the checks
        rows.append((key, "pass" if not problems else "FAIL: " + "; ".join(problems)))
        return not problems

    # ---- cpu0's counter ----
    head, cpu0 = keyed_csv(dirs["m7_cpu0_check"] / "check.csv")
    commits["m7_cpu0_check"] = head.get("code_commit", "")
    rows += [(f"cpu0_{k}", cpu0.get(k, "")) for k in ("idle_cycles", "loop_cycles", "idle_cycles_as_s",
                                                      "difference_as_s", "idle_cpu0_schedstat_s",
                                                      "loop_cpu0_schedstat_s", "idle_time_enabled_ns",
                                                      "idle_time_running_ns", "loop_time_enabled_ns",
                                                      "loop_time_running_ns")]
    cpu0_ok = outcome("cpu0_counter", cpu0_problems(cpu0))
    rows.append(("cpu0_counter_is_reported", "yes" if cpu0_ok else "no (M7 §3: its check failed)"))

    # ---- the counters ----
    d = dirs["m7_check_counters"]
    head, result = session(d, "counters", rows, judge)
    commits["m7_check_counters session"] = head.get("code_commit", "")
    run = run_dir(d, result, "m7_counters", "Ck", *ss.M7_POINTS["m7_main"])
    if run is None:
        counters_ok = outcome("counters", ["no valid run of Ck"])
    else:
        header, summary = keyed_csv(run / "summary.csv")
        commits["m7_check_counters Ck"] = header.get("code_commit", "")
        problems = [] if header.get("duration_s") == str(ss.M7_CHECK_DURATION) else ["the window is not 10 s"]
        try:
            threads, counted = ss.read_csv(run / "threads.csv")[1], ss.read_csv(run / "counters.csv")[1]
            events = ss.read_csv(run / "counter_events.csv")[1]
        except OSError as e:
            raise Missing(f"cannot read {run}: {e!r}") from None
        rows.append(("counter_events", " ".join(f"{e['name']}={e['config']}" for e in events)))
        rows.append(("counter_pmu_type", " ".join(sorted({e["pmu_type"] for e in events}))))
        names = [e["name"] for e in events]
        for name, g in sorted(counter_groups(counted, names).items()):
            rows.append((f"counters_{name}", f"{g['threads']} threads, run {g['run_s']:.4f} s, cycles as "
                                             f"{g['cpu_cycles'] / A76_HZ:.4f} s, "
                                             + ", ".join(f"{e} {g[e]}" for e in names)))
        left_out = events_to_leave_out(counted, events)
        rows.append(("counter_events_to_leave_out", " ".join(left_out) or "none"))
        judge("counter_event_set", [] if not left_out else
              [f"{', '.join(left_out)} must be left out (M7 §1): remove from kThreadCounterEvents "
               "(src/core/perf_counters.h) and run the counters' check again"])
        counters_ok = outcome("counters", problems + counters_problems(threads, counted, events))
    rows.append(("counted_arms_are_run", "yes" if counters_ok else "no (M7 §5: the counters' check failed)"))
    not_run = set(ss.M7_COUNTED) & set(ss.M7_NOT_RUN)
    judge("counted_arm_list", [] if not_run == (set() if counters_ok else set(ss.M7_COUNTED)) else
          [f"tools/stream_session.py's M7_NOT_RUN is {ss.M7_NOT_RUN}, and the counters' check "
           + ("passed" if counters_ok else "did not pass")])

    # ---- unchanged arms ----
    d = dirs["m7_unchanged"]
    try:
        with open(d / "objects.csv", encoding="utf-8") as f:
            lines = f.readlines()
        objects = list(csv.DictReader(l for l in lines if not l.startswith("#")))
        flags = [(d / f"flags_{b}.txt").read_text() for b in ("m7", "freeze_m6")]
    except OSError as e:
        raise Missing(f"cannot read {d}: {e!r}") from None
    head_m7, dump_m7 = keyed_csv(d / "m7" / "dump.csv")
    _, dump_ref = keyed_csv(d / "freeze_m6" / "dump.csv")
    rows += [("code_commit", head_m7.get("code_commit", "")), ("objects_compared", str(len(objects)))]
    rows += [(f"object_{o['source']}", o["sha256_m7"]) for o in objects]
    commits["m7_unchanged dump"] = head_m7.get("code_commit", "")
    commits["m7_unchanged objects"] = next((l.split(": ", 1)[1].strip() for l in lines
                                            if l.startswith("# code_commit: ")), "")
    judge("unchanged_arms", unchanged_problems(objects, flags[0], flags[1], dump_m7, dump_ref))

    # ---- the rest of m7-check ----
    d = dirs["m7_check_rest"]
    head, result = session(d, "rest", rows, judge)
    commits["m7_check_rest session"] = head.get("code_commit", "")
    default = run_dir(d, result, "m7_default", "C", 2, "main")
    problems = [] if default else ["no valid default run"]
    try:
        if default:
            env = json.loads((default / "env.json").read_text(encoding="utf-8"))
            m1_env = json.loads((Path(m1_default) / "env.json").read_text(encoding="utf-8"))
            problems = m2_checks.default_problems(env, m1_env)
            commits["m7_check_rest default"] = keyed_csv(default / "summary.csv")[0].get("code_commit", "")
    except (OSError, ValueError) as e:
        problems.append(f"cannot read the default run or M1's: {e!r}")
    judge("default_path", problems)
    snap = run_dir(d, result, "m7_snapshot", "C", 2, "main")
    if snap is None or default is None:
        judge("snapshots", ["no valid run of C with the snapshots, or no default run to compare it with"])
    else:
        _, meta = keyed_csv(snap / "snapshot_meta.csv")
        rows.append(("snapshot_first_took_ms", f"{(int(meta['snapshot1_end_ns']) - int(meta['snapshot1_begin_ns'])) / 1e6:.1f}"))
        rows.append(("snapshot_first_ended_before_window_ms",
                     f"{(int(meta['window_start_ns']) - int(meta['snapshot1_end_ns'])) / 1e6:.1f}"))
        rows.append(("snapshot_other_threads", meta["snapshot1_other_threads"]))
        rows.append(("snapshot_cpu0_counter_read", "yes" if meta.get("cpu0_counter_read_ok") == "1" else "no"))
        commits["m7_check_rest snapshot"] = keyed_csv(snap / "summary.csv")[0].get("code_commit", "")
        judge("snapshots", snapshot_check_problems(snap, default))
    cpt = run_dir(d, result, "m7_cpt", "Cpt", *ss.M7_POINTS["m7_aux"])
    if cpt is None:
        cpt_ok = outcome("cpt_timing", ["no valid run of Cpt"])
    else:
        header, summary = keyed_csv(cpt / "summary.csv")
        commits["m7_check_rest Cpt"] = header.get("code_commit", "")
        rows += [(f"cpt_{k}", v) for k, v in summary.items() if k.startswith("rknn_run_")]
        cpt_ok = outcome("cpt_timing", m6_checks.ct_problems(summary, ss.read_csv(cpt / "frames.csv")[1],
                                                             int(header["warmup_s"]), int(header["duration_s"])))
    rows.append(("cpt_is_run", "yes" if cpt_ok else "no (M7 §5: its check failed)"))
    judge("cpt_arm_list", [] if cpt_ok == ("Cpt" not in ss.M7_NOT_RUN) else
          [f"tools/stream_session.py's M7_NOT_RUN is {ss.M7_NOT_RUN}, and Cpt's check "
           + ("passed" if cpt_ok else "did not pass")])
    arm_problems = []
    rows.append(("arms_not_run", " ".join(ss.M7_NOT_RUN) or "none"))
    for sweep, (channels, condition) in ss.M7_POINTS.items():
        for arm in ss.m7_arms(sweep):
            run = run_dir(d, result, "m7_check", arm, channels, condition)
            if run is None:
                arm_problems.append(f"{arm} at {channels} channels {condition}: no valid run")
                continue
            header, summary = keyed_csv(run / "summary.csv")
            commits[f"m7_check_rest {arm} {condition}"] = header.get("code_commit", "")
            rows.append((f"run_{sweep}_{arm}", f"{run.name}: process CPU {summary['process_cpu_s']} s in "
                                               f"{summary['window_s']} s, latency p50 "
                                               f"{summary.get('latency_p50_ms')} ms, p99 "
                                               f"{summary.get('latency_p99_ms')} ms, drop rate "
                                               f"{summary['drop_rate']}, snapshots {ss.snapshots_state(run)}"))
    judge("arms_runs", arm_problems)

    # ---- the probe's tool starts ----
    d = dirs["m7_probe_smoke"]
    try:
        head, probe = ss.read_csv(d / "probe.csv")
    except OSError as e:
        raise Missing(f"cannot read {d}/probe.csv: {e!r}") from None
    commits["m7_probe_smoke"] = head.get("code_commit", "")
    ran = {r["config"]: r["outcome"] for r in probe}
    rows.append(("probe_smoke", " ".join(f"{c}:{o}" for c, o in sorted(ran.items()))))
    problems = [] if head.get("dump_runs") == "1" and head.get("rounds") == "0" else ["the record is not the smoke's"]
    if ran.get("1") not in ("accepted", "ran") or any(o != "not run" for c, o in ran.items() if c != "1"):
        problems.append(f"configuration 1 did not run alone on one frame: {ran}")
    judge("probe_starts", problems)

    # ---- the order of M7 §8 step 3, and one build ----
    starts = {name: start_of(dirs[name], name) for name in RECORDS}
    rows += [(f"start_{n}", f"{t:%Y-%m-%dT%H:%M:%SZ}") for n, t in starts.items()]
    judge("order", order_problems(starts))
    committed = {n: commit_time(dirs[n]) for n in ("m7_cpu0_check", "m7_check_counters")}
    rows += [(f"committed_{n}", f"{t:%Y-%m-%dT%H:%M:%SZ}" if t else "not committed") for n, t in committed.items()]
    judge("committed_before_the_runs", committed_problems(committed, starts["m7_check_rest"]))
    judge("one_build", build_problems(commits, same_code))
    rows.append(("passed", "0" if failed else "1"))
    return rows, not failed


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", default="results/check")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    try:
        rows, passed = check(args.results)
    except (Missing, m6_checks.Missing, KeyError) as e:
        print(f"m7_checks: {e}", file=sys.stderr)
        return 2
    text = "key,value\n" + "".join(f"{k},{v}\n" for k, v in rows)
    print(text, end="")
    if args.out:
        now = dt.datetime.now(dt.timezone.utc)
        commit = dict(rows).get("code_commit", "")
        header = {"start_time_utc": f"{now:%Y-%m-%dT%H:%M:%SZ}", "prereg_v1_commit": ss.PREREG_V1_COMMIT,
                  "prereg_m7_commit": ss.PREREG_M7_COMMIT, "run_kind": "check", "code_commit": commit,
                  "measurement": "the checks of prereg/M7.md §8 step 3, judged from their records; not a run"}
        Path(args.out).write_text("".join(f"# {k}: {v}\n" for k, v in header.items()) + text, encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
