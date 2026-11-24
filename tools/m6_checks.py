#!/usr/bin/env python3
"""The checks before freeze-m6 (prereg/M6.md §8 step 3), judged from their records in results/check/.

Records, each the latest of its name (<UTC>_<name>):
  m6_unchanged       tools/m6_unchanged.sh. "Unchanged arms": the objects M6 §8 step 2 lists as not changed
                     have the SHA-256 of freeze-m3's build's, compiled with the same flags; for B, Bd, C and Cp
                     the input tensors, the NPU's outputs and the detections on 100 frames of video ① equal
                     those of freeze-m3's build. "Rc": the input buffer the NPU read equals C's slot, with
                     equal outputs.
  m6_rc_trace        tools/m6_rc_trace.sh. "Rc": in a system-call trace of a short run, two
                     DMA_BUF_IOCTL_SYNC calls per frame, on a slot's dma-buf, from an NPU worker, and none on
                     any other dma-buf.
  m6_by_check        tools/m6_by_check. "By", 1: the functional check on synthetic frames.
  m6_by_equivalence  tools/equivalence.sh with C_PLACE=By. "By", 2: A5's criteria against B on the 1,000
                     frames of video ①. Its outcome decides whether By is run (M6 §1); it is not a failure
                     of the checks. The arms the session tool leaves out (M6_NOT_RUN) must agree with it.
  m6_by_holdout      tools/equivalence_holdout.sh with C_PLACE=By. "By", 3: reported only.
  m6_check           tools/stream_session.py m6-check. The default path: one run of C at 2 channels whose
                     threads, affinities and queues equal those of M1's 32_main_C_2ch_r1. "Cpf": every
                     pipeline thread allowed cpu0-7 and the metrics thread cpu0 only, at the window's start
                     and end. "Ct": as many timed calls as inferences in the window, every one positive. One
                     valid run of each arm at the point, with its process CPU and latency.
The order M6 §8 step 3 gives is checked on the records' start times: the m6_check session, whose runs have
measurement windows, starts after m6_unchanged, m6_by_check and m6_by_equivalence; so does m6_rc_trace.

Usage, from the repository: python3 tools/m6_checks.py [--results DIR] [--out FILE]
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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m2_checks  # noqa: E402  (the default path's comparison with M1's run)
import stream_session as ss  # noqa: E402

RECORDS = ("m6_unchanged", "m6_rc_trace", "m6_by_check", "m6_by_equivalence", "m6_by_holdout", "m6_check")
OLD_ARMS = ("b", "bd", "c", "cp")  # tools/m6_arms_dump's file stems for B, Bd, C and Cp
FRAMES = 100  # M6 §8 step 3
SLOT_HEAPS = ("system-dma32", "system")  # Rc's heap, and its fallback (M6 §1)
SYNC = re.compile(r"^(\d+)\s+(\d+\.\d+)\s+ioctl\((\d+),\s*DMA_BUF_IOCTL_SYNC")
TRIM_S = 0.5  # the trace's first and last half second are left out: strace attaches and detaches there


class Missing(Exception):
    """A record is missing or cannot be read."""


def latest(results, name):
    """The latest record <UTC>_<name> under results, or None."""
    found = sorted(d for d in Path(results).glob(f"*_{name}")
                   if d.is_dir() and re.fullmatch(ss.SESSION_NAME + re.escape(name), d.name))
    return found[-1] if found else None


def keyed_csv(path):
    """(header, {key: value}) of a "# key: value" header and a key,value table."""
    try:
        header, rows = ss.read_csv(path)
    except OSError as e:
        raise Missing(f"cannot read {path}: {e!r}") from None
    return header, {r["key"]: r["value"] for r in rows}


def unchanged_problems(objects, flags_m6, flags_ref, dump_m6, dump_ref):
    """Why the earlier arms are not shown unchanged; [] if they are. objects: rows of objects.csv
    (source, sha256_m6, sha256_freeze_m3); flags_*: the two builds' flags, with their own directory
    replaced; dump_*: tools/m6_arms_dump's rows from each build."""
    problems = []
    if not objects:
        problems.append("no objects")
    problems += [f"object of {o['source']} differs from freeze-m3's build's" for o in objects
                 if o["sha256_m6"] != o["sha256_freeze_m3"] or len(o["sha256_m6"]) != 64]
    if flags_m6 != flags_ref or not flags_m6.strip():
        problems.append("the two builds' compile flags, defines or include paths differ")
    for name, d in (("this build", dump_m6), ("freeze-m3's build", dump_ref)):
        if d.get("frames") != str(FRAMES):
            problems.append(f"{name}'s dump has {d.get('frames')} frames, not {FRAMES}")
    for arm in OLD_ARMS:
        for ext, what in ((".u8", "input tensors"), (".i8", "outputs"), (".jsonl", "detections")):
            a, b = dump_m6.get(f"sha256_{arm}{ext}"), dump_ref.get(f"sha256_{arm}{ext}")
            if not a or a != b:
                problems.append(f"{arm}: the {what} differ from freeze-m3's build's")
    return problems


def rc_data_problems(dump_m6):
    """Why Rc's input buffer or outputs are not C's; [] if they are (M6 §8 step 3, "Rc")."""
    problems = []
    for ext, what in ((".u8", "the input buffer the NPU read is not C's slot byte for byte"),
                      (".i8", "the outputs differ from C's")):
        rc, c = dump_m6.get(f"sha256_rc{ext}"), dump_m6.get(f"sha256_c{ext}")
        if not rc or rc != c:
            problems.append(f"rc: {what}")
    return problems


def trace_summary(strace_lines, threads, fd_exporters, bind_times):
    """What a trace of Rc shows (M6 §8 step 3, "Rc").

    strace_lines: lines of `strace -f -ttt -e trace=ioctl -o`: "<tid> <epoch s> ioctl(<fd>, ...". threads:
    {tid: comm}. fd_exporters: {fd: exporter name} for the process's dma-bufs. bind_times: the epoch time of
    every frame's bind (t2b), in order.
    Returns a dict: sync calls on slots (dma-bufs of a slot heap) and on other fds; the calls not made by an
    NPU worker; whether each worker's calls come in pairs on one fd; and, over the trace without its first
    and last half second, cut between two binds, the sync calls and the frames bound.
    """
    calls = []  # (time, tid, fd)
    for line in strace_lines:
        m = SYNC.match(line)
        if m:
            calls.append((float(m.group(2)), m.group(1), m.group(3)))
    times = [float(m.group(2)) for m in map(re.compile(r"^(\d+)\s+(\d+\.\d+)\s").match, strace_lines) if m]
    out = {"sync_calls": len(calls),
           "sync_on_slots": sum(1 for _, _, fd in calls if fd_exporters.get(fd) in SLOT_HEAPS),
           "sync_not_from_npu_workers": sum(1 for _, tid, _ in calls if not threads.get(tid, "").startswith("npu-")),
           "slot_heaps": " ".join(sorted({fd_exporters[fd] for _, _, fd in calls if fd in fd_exporters})) or "none"}
    out["sync_on_other_fds"] = out["sync_calls"] - out["sync_on_slots"]
    by_tid = {}
    for _, tid, fd in calls:
        by_tid.setdefault(tid, []).append(fd)
    out["paired"] = all(len(fds) % 2 == 0 and all(fds[i] == fds[i + 1] for i in range(0, len(fds), 2))
                        for fds in by_tid.values())

    def cut(target):  # the middle of the gap between two binds that is nearest to target
        mids = [(a + b) / 2 for a, b in zip(bind_times, bind_times[1:])]
        return min(mids, key=lambda m: abs(m - target)) if mids else target

    if times:
        lo, hi = cut(min(times) + TRIM_S), cut(max(times) - TRIM_S)
        out["frames_in_trace"] = sum(1 for t in bind_times if lo <= t <= hi)
        out["sync_in_trace"] = sum(1 for t, _, _ in calls if lo <= t <= hi)
    else:
        out["frames_in_trace"] = out["sync_in_trace"] = 0
    return out


def trace_problems(t):
    """Why a trace_summary is not Rc's registered behaviour; [] if it is."""
    problems = []
    if t["frames_in_trace"] < 10:
        problems.append(f"only {t['frames_in_trace']} frames in the trace")
    if t["sync_in_trace"] != 2 * t["frames_in_trace"]:
        problems.append(f"{t['sync_in_trace']} sync calls for {t['frames_in_trace']} frames, not two per frame")
    if t["sync_on_other_fds"]:
        problems.append(f"{t['sync_on_other_fds']} sync calls on fds that are not slots")
    if t["sync_not_from_npu_workers"]:
        problems.append(f"{t['sync_not_from_npu_workers']} sync calls from threads other than the NPU workers")
    if not t["paired"]:
        problems.append("a worker's sync calls do not come in pairs on one fd")
    return problems


def read_trace(d):
    """trace_summary of a tools/m6_rc_trace.sh record."""
    try:
        threads = dict(l.split(" ", 1) for l in (d / "threads.txt").read_text().splitlines() if " " in l)
        exporters = {}
        for line in (d / "fds.tsv").read_text().splitlines():
            fd, _, exp = (line.split("\t") + ["", ""])[:3]
            if exp:
                exporters[fd] = exp
        header, summary = keyed_csv(d / "run" / "summary.csv")
        t0 = ss.parse_utc(summary["warmup_start_utc"]).timestamp()
        binds = sorted(t0 + int(f["t2b_ns"]) / 1e9 for f in ss.read_csv(d / "run" / "frames.csv")[1])
        lines = (d / "strace.txt").read_text().splitlines()
    except (OSError, KeyError, ValueError) as e:
        raise Missing(f"cannot read the trace in {d}: {e!r}") from None
    if header.get("variant") != "Rc":
        raise Missing(f"{d}: the traced run is variant {header.get('variant')}, not Rc")
    return header, trace_summary(lines, threads, exporters, binds)


def ct_problems(summary, frames, warmup_s, duration_s):
    """Why a run of arm Ct does not time every inference of the window; [] if it does (M6 §8 step 3, "Ct").
    summary: its summary.csv rows; frames: its frames.csv rows."""
    lo, hi = warmup_s * 10**9, (warmup_s + duration_s) * 10**9
    window = [f for f in frames if lo <= int(f["t4_ns"]) < hi]
    problems = []
    if any("rknn_cpu_ns" not in f for f in frames) or "rknn_run_calls" not in summary:
        return ["the run has no rknn_run timing"]
    if not window:
        problems.append("no inference in the window")
    if summary["rknn_run_calls"] != str(len(window)) or summary.get("processed_in_window") != str(len(window)):
        problems.append(f"{summary['rknn_run_calls']} timed calls, {summary.get('processed_in_window')} inferences "
                        f"in the summary and {len(window)} frames in the window")
    not_positive = sum(1 for f in window if int(f["rknn_cpu_ns"]) <= 0)
    if not_positive:
        problems.append(f"{not_positive} timed calls with a CPU time that is not positive")
    return problems


def start_of(d, name):
    """A record's start time, from its own files."""
    if name in ("m6_by_equivalence", "m6_by_holdout"):
        first = next(iter(sorted(d.glob("**/b.jsonl"))), None)
        if first is None:
            raise Missing(f"{d}: no b.jsonl")
        with open(first, encoding="utf-8") as f:
            return ss.parse_utc(json.loads(f.readline())["header"]["start_time_utc"])
    if name == "m6_unchanged":
        text = (d / "reference.txt").read_text()
        return ss.parse_utc(re.search(r"^start_time_utc: (\S+)", text, re.M).group(1))
    path = {"m6_by_check": d / "check.csv", "m6_rc_trace": d / "run" / "summary.csv", "m6_check": d / "result.csv"}[name]
    return ss.parse_utc(ss.read_csv(path)[0]["start_time_utc"])


def check(results, m1_default=m2_checks.M1_DEFAULT_RUN):
    """([(key, value)], passed) over the records under results."""
    dirs = {name: latest(results, name) for name in RECORDS}
    missing = [n for n, d in dirs.items() if d is None]
    if missing:
        raise Missing(f"no record in {results} for: {', '.join(missing)}")
    rows, failed = [(f"record_{n}", d.name) for n, d in dirs.items()], []

    def judge(key, problems):
        rows.append((key, "pass" if not problems else "FAIL: " + "; ".join(problems)))
        if problems:
            failed.append(key)

    # ---- unchanged arms, and Rc's data ----
    d = dirs["m6_unchanged"]
    try:
        with open(d / "objects.csv", encoding="utf-8") as f:
            objects = list(csv.DictReader(l for l in f if not l.startswith("#")))
        flags = [(d / f"flags_{b}.txt").read_text() for b in ("m6", "freeze_m3")]
    except OSError as e:
        raise Missing(f"cannot read {d}: {e!r}") from None
    head_m6, dump_m6 = keyed_csv(d / "m6" / "dump.csv")
    _, dump_ref = keyed_csv(d / "freeze_m3" / "dump.csv")
    rows += [("code_commit", head_m6.get("code_commit", "")), ("objects_compared", str(len(objects)))]
    judge("unchanged_arms", unchanged_problems(objects, flags[0], flags[1], dump_m6, dump_ref))
    judge("rc_equals_c", rc_data_problems(dump_m6))

    # ---- Rc's trace ----
    head, t = read_trace(dirs["m6_rc_trace"])
    rows += [(f"rc_trace_{k}", str(v)) for k, v in t.items()]
    judge("rc_trace", trace_problems(t))

    # ---- By ----
    head_by, by = keyed_csv(dirs["m6_by_check"] / "check.csv")
    rows += [("libyuv_commit", head_by.get("libyuv_commit", ""))]
    rows += [(f"by_check_{k}", v) for k, v in by.items() if k.startswith("reported_")]
    judge("by_functional", [] if by.get("passed") == "1" else ["tools/m6_by_check did not pass"])
    verdicts = {}
    for name in ("m6_by_equivalence", "m6_by_holdout"):
        try:
            j = json.loads((dirs[name] / "check.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise Missing(f"cannot read {dirs[name]}/check.json: {e!r}") from None
        verdicts[name] = j["pass"]
        rows.append((f"{name}_a5", "pass" if j["pass"] else "FAIL"))
        rows += [(f"{name}_a5_{k}", "pass" if v else "FAIL") for k, v in j["a5"].items()]
    by_runs = bool(verdicts["m6_by_equivalence"])
    rows.append(("by_is_run", "yes" if by_runs else "no (M6 §1: it did not pass A5's criteria)"))
    judge("by_arm_list", [] if by_runs == ("By" not in ss.M6_NOT_RUN) else
          [f"tools/stream_session.py's M6_NOT_RUN is {ss.M6_NOT_RUN}, and By's equivalence "
           + ("passed" if by_runs else "did not pass")])

    # ---- the m6-check session ----
    d = dirs["m6_check"]
    _, result = keyed_csv(d / "result.csv")
    rows.append(("m6_check_session_status", result.get("session_status", "")))
    problems = [] if result.get("session_status") == "completed" else ["the session did not complete"]
    default = d / result.get("default_run", "missing")
    try:
        if not problems:
            env = json.loads((default / "env.json").read_text(encoding="utf-8"))
            m1_env = json.loads((Path(m1_default) / "env.json").read_text(encoding="utf-8"))
            problems = m2_checks.default_problems(env, m1_env)
    except (OSError, ValueError) as e:
        problems.append(f"cannot read the default run or M1's: {e!r}")
    judge("default_path", problems)
    arm_problems = []
    for arm in ss.m6_arms():
        run = d / result.get(f"{arm}_run", "missing")
        if not (run / "summary.csv").exists():
            arm_problems.append(f"{arm}: no valid run")
            continue
        header, summary = keyed_csv(run / "summary.csv")
        rows.append((f"run_{arm}", f"{run.name}: process CPU {summary['process_cpu_s']} s in "
                                   f"{summary['window_s']} s, latency p50 {summary.get('latency_p50_ms')} ms, "
                                   f"p99 {summary.get('latency_p99_ms')} ms, drop rate {summary['drop_rate']}"))
        if arm == "Cpf":
            env = json.loads((run / "env.json").read_text(encoding="utf-8"))
            judge("cpf_affinities", ss.cpf_affinity_problems(env["threads_at_window_start"],
                                                             ss.read_csv(run / "threads.csv")[1]))
        if arm == "Ct":
            rows += [(f"ct_{k}", v) for k, v in summary.items() if k.startswith("rknn_run_")]
            judge("ct_timing", ct_problems(summary, ss.read_csv(run / "frames.csv")[1], int(header["warmup_s"]),
                                           int(header["duration_s"])))
    judge("arms_runs", arm_problems)

    # ---- the order of M6 §8 step 3 ----
    starts = {name: start_of(dirs[name], name) for name in RECORDS}
    rows += [(f"start_{n}", f"{t:%Y-%m-%dT%H:%M:%SZ}") for n, t in starts.items()]
    before = ("m6_unchanged", "m6_by_check", "m6_by_equivalence")
    judge("order", [f"{later} starts before {n}" for later in ("m6_rc_trace", "m6_check") for n in before
                    if starts[later] <= starts[n]])
    rows.append(("passed", "0" if failed else "1"))
    return rows, not failed


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", default="results/check")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    try:
        rows, passed = check(args.results)
    except (Missing, KeyError) as e:
        print(f"m6_checks: {e}", file=sys.stderr)
        return 2
    text = "key,value\n" + "".join(f"{k},{v}\n" for k, v in rows)
    print(text, end="")
    if args.out:
        now = dt.datetime.now(dt.timezone.utc)
        commit = dict(rows).get("code_commit", "")
        header = {"start_time_utc": f"{now:%Y-%m-%dT%H:%M:%SZ}", "prereg_v1_commit": ss.PREREG_V1_COMMIT,
                  "prereg_m6_commit": ss.PREREG_M6_COMMIT, "run_kind": "check", "code_commit": commit,
                  "measurement": "the checks of prereg/M6.md §8 step 3, judged from their records; not a run"}
        Path(args.out).write_text("".join(f"# {k}: {v}\n" for k, v in header.items()) + text, encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
