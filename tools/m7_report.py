#!/usr/bin/env python3
"""M7's tables and verdicts (prereg/M7.md §2, §3, §4, §5 and §6) from the run records of its two sweeps: m7_main
(8 channels, main condition) and m7_aux (5 channels, auxiliary condition).

The runs that count (§4) are, per sweep, repetition and arm, the valid run of the session that completed the
repetition (an invalid ×2 run counts as missing), as tools/m6_report.py takes them, over the arms the sweep runs:
§1's, without stream_session.M7_NOT_RUN. A session that stream_session.M7_SUPERSEDED names (§5, §9) never counts:
its runs are listed in a section of their own. Both constants, and the orders (M7_ORDERS), are read at run time.
  - the two hypotheses of §2, from the means of the runs that count, decided in exact arithmetic
    (fractions.Fraction) on the records' decimal values, so that a threshold met exactly counts as met (A17):
      M7-H1  (B − Rc) / (B − C) ≥ 0.90 on process CPU in m7_aux; mixed if the same ratio on /proc/schedstat's
             system CPU less the idle baseline (M2 §3.2) is on the other side of 0.90
      M7-H2  D(C) − D(Bd) ≥ 0.50 s in m7_main, D the decoder group's CPU in threads.csv; mixed if the same
             difference from the snapshots' run times of those threads, scaled from the snapshots' span to 120 s,
             is on the other side of 0.50 s; no label if a run that counts of Bd or C has no complete snapshots
    each with its spec-literal and practical verdict and its flags (§2, "For both"). The practical verdict is the
    spec-literal one, except indeterminate when a run that counts of a named arm has a channel below 4.75 fps
    (main) or 28.5 fps (aux), when the mean drop rates of two named arms differ by more than 1 percentage point,
    or when a named arm has fewer than 3 runs that count (the spec-literal verdict then uses the runs that exist,
    flagged). With no run that counts of a named arm, or a named arm not run, both are indeterminate. Flagged
    too: §4's CV above 10%, of process CPU for B, Rc and C in m7_aux and of D for Bd and C in m7_main;
  - §3's values from the snapshots (snapshot_*.csv and snapshot<k>_*.txt), per run, as the second snapshot less
    the first over their span (the first's start to the second's): kernel threads' CPU in all and by name group;
    other processes' CPU in all and the five largest by name; threads of one snapshot only; interrupts per line
    of the NPU, RGA and rkvdec, per CPU, and of all lines; soft interrupts per kind; /proc/stat's irq and softirq
    columns; cpu0's busy time by its counter next to its /proc/schedstat value (only with --cpu0-check passed);
    bench_main's threads by group: timeslices, context switches and time waiting to run. Where the session's idle
    pair (<session>/idle_snapshot) has a value, the report also gives the run's less the idle pair's scaled from
    its span to the run's (§4);
  - from the counters (counters.csv) of Bk, Bdk, Rck and Ck, per thread group: each event's count, instructions
    per cycle, cycles / 2208 MHz next to the group's run time read with the counters, and their ratio;
  - §6's tables: the four cells at each point, also by thread group and per inference, next to M6's and M1.1's
    values (other sessions, from prereg/M7.md's Disclosure); the decoder group's D, Rc's and Rck's values by
    position in the session; the counted arms' paired differences and §6's description rule; Xk − X and §5's 2%
    rule; the kernel's side; Ct's and Cpt's rknn_run values and the probe's table (<session>/probe/probe.csv);
    the paired differences of §6 with a descriptive 95% t interval (df = pairs − 1, none below 2 pairs); every
    arm descriptively; B and C next to the earlier milestones at each point; every run, valid or not; and the
    runs whose snapshots or counters are missing, incomplete or unreadable (§4: they stay valid).
The thread groups are tools/cpu_breakdown.py's (as tools/m6_report.py restates them): the decoder group is dec-*
with MPP's mpp_dec_parser and mpp_dec_hal. Standard library only, so that it also runs on the board.
Usage, from the repository:
  python3 tools/m7_report.py [--cpu0-check passed|failed] [--out FILE]       every M7 session, results/m7/<UTC>_<sweep>
  python3 tools/m7_report.py --session DIR [--session DIR] [...]             an m7_main or m7_aux session with the
                                                                             earlier sessions of its sweep, or a
                                                                             smoke test (<UTC>_<sweep>_smoke) alone
  --cpu0-check  the outcome of §8 step 3's check of cpu0's counter, as the amendment records it (default failed:
                no value of the counter is reported, §3)
Exit 0, or 2 when a session, or a run that counts, cannot be read.
"""
import argparse
import csv
import re
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m3_report as m3r  # noqa: E402  (counting_runs, mean_sd, cv, M1_SESSION)
import m6_report as m6r  # noqa: E402  (run values, the runs' bookkeeping, paired, cells, the tables' helpers)
import stream_session as ss  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
KIND = "m7"  # results/m7/<UTC>_<sweep> (M7 §8 step 2)
SWEEPS = {sweep: (*ss.M7_POINTS[sweep], ss.M7_ORDERS[sweep][1])  # M7 §1: channels, condition, arms in the first
          for sweep in ("m7_main", "m7_aux")}                      # repetition's order (M7 §4), as the session tool's
REPETITIONS = 3  # M7 §4
WINDOW_S = 120  # PROTOCOL §7.1; M7-H2's snapshot values are scaled to it (§2)
MIN_FPS = {"main": Fraction(475, 100), "aux": Fraction(285, 10)}  # M7 §2, "For both"
DROP_PP = Fraction(1, 100)  # M7 §2: mean drop rates more than 1 percentage point apart
CV_LIMIT = Fraction(1, 10)  # M7 §4
COST_LIMIT = Fraction(2, 100)  # M7 §5: Xk − X above 2% of X's process CPU
A55_HZ, A76_HZ = 1_800_000_000, 2_208_000_000  # A7's fixed clocks: cpu0 (M7 §3) and the A76 (M7 §3)
USER_HZ = 100  # /proc/stat's tick (sysconf(_SC_CLK_TCK) on the board, as src/core/metrics.cpp reads it)
HYPOTHESES = {  # M7 §2: sweep, named arms, threshold, value, metric, the mixed label's metric
    "M7-H1": ("m7_aux", ("B", "Rc", "C"), Fraction(9, 10), "(B − Rc) / (B − C)", "process_cpu_s",
              "schedstat_total_s"),
    "M7-H2": ("m7_main", ("Bd", "C"), Fraction(1, 2), "D(C) − D(Bd)", "threads_dec_s", "snap_dec_120_s"),
}
CV_KEYS = {"M7-H1": ("process_cpu_s", "process CPU"), "M7-H2": ("threads_dec_s", "decoder group's CPU")}  # M7 §4
NOTES = {  # M7 §2, in its words
    "M7-H1": ("M7-H1 is the expected result, as M6-H1 was. It tests the one cell the auxiliary condition has never "
              "had, and compares bundles, not a pure \"copy\".",),
    "M7-H2": ("M7-H2 is about a difference of about one second between thread groups whose sd over three runs has "
              "been between 0.09 and 0.75 s. Three runs can show a difference of that size and cannot bound it "
              "closely.",
              "What an adoption says: that on this board C's decoder threads use more CPU than Bd's, in three "
              "sessions. It does not say why; question 2's counters describe it (§6).",
              "What a rejection says: that the difference of M1.1 and M6 did not repeat at this size. The earlier "
              "values stand as they were reported."),
}
BOTH_NOTE = ("Neither says anything about M6-H1. `m7_main` runs B, Rc and C again, and their ratio is reported next "
             "to M6's without a verdict (§6). One board, one image, one model, two points, file-backed RTSP streams "
             "at 1080p.")
COUNTED = {"Bk": "B", "Bdk": "Bd", "Rck": "Rc", "Ck": "C"}  # M7 §1: the counted arm and its cell
PAIRS = {  # M7 §6, "Paired differences"
    "m7_main": (("B", "Rc"), ("Bd", "C"), ("B", "Bd"), ("Rc", "C"), ("B", "C"),
                ("Bk", "B"), ("Bdk", "Bd"), ("Rck", "Rc"), ("Ck", "C")),
    "m7_aux": (("B", "Rc"), ("Bd", "C"), ("B", "Bd"), ("Rc", "C"), ("B", "C"), ("Ct", "C"), ("Ct", "Cpt")),
}
PAIRED_METRICS = (("process_cpu_s", "Process CPU", "s"),
                  ("schedstat_total_s", "System CPU, /proc/schedstat, less idle", "s"),
                  ("threads_dec_s", "D, the decoder group", "s"),
                  ("latency_p50_ms", "Latency p50", "ms"), ("latency_p99_ms", "Latency p99", "ms"))
COUNTER_PAIRS = (("Ck", "Bdk"), ("Rck", "Ck"), ("Bk", "Bdk"), ("Rck", "Bk"))  # M7 §6
KERNEL_PAIRS = (("C", "B"), ("Rc", "B"), ("C", "Bd"))  # M7 §6, the kernel's side
KERNEL_GROUPS = (("mpp_worker", "mpp_worker_*"), ("rga3", "irq/*-rga3"), ("npu", "rknpu or npu in the name"),
                 ("kworker", "kworker/*"), ("ksoftirqd", "ksoftirqd/*"), ("rest", "the rest"))  # M7 §3
IRQ_NAMES = ("npu", "rga", "rkvdec")  # M7 §3: the interrupt lines whose name holds one of these
OWN_STATS = (("timeslices", "Timeslices", 0), ("voluntary_cs", "Voluntary context switches", 0),
             ("involuntary_cs", "Involuntary context switches", 0), ("wait_s", "Time waiting to run (s)", 3),
             ("run_s", "Run time (s)", 3))  # M7 §3: per thread group of bench_main, from the snapshots
IDLE_PREFIXES = ("kernel", "others_s", "tasks_s", "irq", "softirq_", "stat_", "sched_s")  # values the idle pair has
COUNTER_COLUMNS = ("tid", "comm", "opened", "open_errno", "failed_event", "enable_ok", "disable_ok", "read_ok",
                   "time_enabled_ns", "time_running_ns", "run_ns_enable", "run_ns_disable", "t_enable_ns",
                   "t_disable_ns")  # counters.csv's columns before the events (core/perf_counters.h)
COUNTED_OK = ("opened", "enable_ok", "disable_ok", "read_ok")  # a thread's group counts only if all four are 1
# Earlier sessions' values (prereg/M7.md, Disclosure), process CPU per 120 s window in s: other sessions, labelled so.
M6_CELLS = {"B": Fraction("44.010"), "Bd": Fraction("42.543"), "Rc": Fraction("20.903"), "C": Fraction("19.303")}
M11_AUX = {"B": Fraction("130.95"), "Bd": Fraction("129.85"), "C": Fraction("37.45")}  # M1.1 at the auxiliary point
EARLIER_D = (  # the decoder group, s: (session, point, {arm: value})
    ("M1", "main", {"B": "10.47 ± 0.18", "C": "10.84 ± 0.33"}),
    ("M1.1", "main", {"B": "10.87 ± 0.20", "Bd": "10.30 ± 0.42", "C": "11.34 ± 0.16"}),
    ("M6", "main", {"B": "10.60 ± 0.36", "Bd": "10.05 ± 0.19", "Rc": "11.75 ± 0.75", "C": "11.18 ± 0.09"}),
    ("M1", "aux", {"B": "8.34", "C": "8.44"}),
    ("M1.1", "aux", {"B": "8.24", "Bd": "8.01", "C": "8.42"}))
POINT_LABELS = {"main": "H1's, 8 ch", "aux": "5 ch × 30 fps"}
M6_RC = ((3, "20.19", "11.05"), (14, "20.78", "11.65"), (19, "21.74", "12.54"))  # M6's Rc: slot, process CPU, D
M6_CT = {"rknn_run_cpu_s": "5.794", "rknn_run_cpu_share": "0.291", "rknn_run_cpu_mean_ms": "1.207"}  # H1's point
M6_CT_C = "0.58 s (−0.14 to 1.30 s)"  # M6's Ct − C
AUX_REFERENCES = (("M1", Path("results/comparison/20261103T101109Z_aux"), "aux"),  # B and C at 5 ch × 30 fps
                  ("M1.1", Path("results/m1.1/20261104T065947Z_bdirect_aux"), "bdirect_aux"))
SLOT_NOTE = ("Slot: the run's place in its session's sequence (runs.tsv's seq), its position in the session; a repeat "
             "shares the slot of the run it repeats.")
LATENCY_NOTE = ("Latency in an unstaggered run depends on the channels' arrival phases, which are not controlled "
                "(M7 §3).")
SPAN_NOTE = ("The snapshots' values are over their own span, from the first snapshot's start to the second's (about "
             "121 s), not rescaled (§1).")


class Incomplete(ValueError):
    """A pair of snapshots that counts as missing: one not taken, or stopped at its deadline (M7 §1, §4)."""


# ---------------------------------------------------------------- M7's constants of the session tool

def not_run():
    """The arms that are not run (§5): stream_session.M7_NOT_RUN, read at run time."""
    return tuple(ss.M7_NOT_RUN)


def not_run_why(arm):
    """Why an arm is not run: stream_session.M7_NOT_RUN_WHY's sentence, read at run time."""
    return ss.M7_NOT_RUN_WHY.get(arm) or "stream_session.M7_NOT_RUN_WHY gives no reason"


def superseded_names():
    """The sessions an amendment supersedes (§5, §9): stream_session.M7_SUPERSEDED, read at run time."""
    return tuple(ss.M7_SUPERSEDED)


def sweep_arms(sweep):
    """The arms a sweep runs, in its first repetition's order (§1, §4), without those not run."""
    return ss.m7_arms(sweep)


def order_position(sweep, repetition, arm):
    """The arm's place in its repetition's order (§4), 1-based, without the arms not run; None outside the plan."""
    order = {r: o for _, r, o in ss.sweep_plan(sweep)}.get(repetition, ())
    return order.index(arm) + 1 if arm in order else None


# ---------------------------------------------------------------- the snapshots' files (§1, §3)

def meta_values(path):
    """{key: value} of a key,value CSV with "# key: value" header lines."""
    return {r["key"]: r["value"] for r in ss.read_csv(path)[1]}


def snapshot_problem(meta):
    """Why a pair of snapshots counts as missing (§1, §4): a snapshot not taken or incomplete; "" when both are
    complete."""
    for k in (1, 2):
        if meta.get(f"snapshot{k}_taken") not in ("1", "true"):
            return f"snapshot {k} was not taken"
        if meta.get(f"snapshot{k}_complete") not in ("1", "true"):
            return f"snapshot {k} is incomplete: it stopped at its deadline (§1)"
    return ""


def kernel_group(comm):
    """M7 §3's name group of a kernel thread: the first of KERNEL_GROUPS whose pattern its name matches."""
    if comm.startswith("mpp_worker_"):
        return "mpp_worker"
    if re.fullmatch(r"irq/.*-rga3", comm):
        return "rga3"
    if "rknpu" in comm or "npu" in comm:
        return "npu"
    if comm.startswith("kworker/"):
        return "kworker"
    if comm.startswith("ksoftirqd/"):
        return "ksoftirqd"
    return "rest"


def pair_rows(rows, key):
    """({key(row): row} of snapshot 1, the same of snapshot 2)."""
    out = ({}, {})
    for r in rows:
        if r["snapshot"] not in ("1", "2"):
            raise ValueError(f"snapshot {r['snapshot']!r} is neither 1 nor 2")
        out[int(r["snapshot"]) - 1][key(r)] = r
    return out


def other_tasks(rows):
    """§3 from snapshot_tasks.csv's rows: the run time of every other task over the span, in ns. A thread is a
    (pid, tid); its name and kind are the second snapshot's. A thread in the second snapshot only started inside the
    span and its run time counts in full; one in the first only ended inside it and is listed, its contribution
    unknown (§1). Other processes are named by their main thread's name (tid = pid) where the snapshot has it.
    Returns {"kernel_ns": {group: ns}, "others_ns": {process name: ns}, "only_first": [...], "only_second": [...]},
    each listed thread a dict with pid, tid, comm, kernel and run_ns (None when unknown)."""
    first, second = pair_rows(rows, lambda r: (int(r["pid"]), int(r["tid"])))
    names = {(k, p): r["comm"] for k, snap in enumerate((first, second)) for (p, t), r in snap.items() if p == t}
    kernel = dict.fromkeys((g for g, _ in KERNEL_GROUPS), 0)
    others, only_first, only_second = {}, [], []
    for (pid, tid), r in second.items():
        run = int(r["run_ns"]) - (int(first[(pid, tid)]["run_ns"]) if (pid, tid) in first else 0)
        is_kernel = r["kernel"] in ("1", "true")
        if (pid, tid) not in first:
            only_second.append({"pid": pid, "tid": tid, "comm": r["comm"], "kernel": is_kernel, "run_ns": run})
        if is_kernel:
            kernel[kernel_group(r["comm"])] += run
        else:
            name = names.get((1, pid), names.get((0, pid), r["comm"]))
            others[name] = others.get(name, 0) + run
    only_first = [{"pid": p, "tid": t, "comm": r["comm"], "kernel": r["kernel"] in ("1", "true"), "run_ns": None}
                  for (p, t), r in first.items() if (p, t) not in second]
    return {"kernel_ns": kernel, "others_ns": others, "only_first": only_first, "only_second": only_second}


def own_threads(rows):
    """§3 from snapshot_own.csv's rows: bench_main's threads over the span, summed by thread group (tools/
    cpu_breakdown.py's groups): run_ns, wait_ns, timeslices and the two context switches (None in a group where a
    thread's status could not be read). A thread of one snapshot only is treated as in other_tasks. Returns
    {"groups": {group: {stat: value}}, "only_first": [...], "only_second": [...]}."""
    first, second = pair_rows(rows, lambda r: int(r["tid"]))
    stats = ("run_ns", "wait_ns", "timeslices", "voluntary_cs", "involuntary_cs")
    groups = {g: dict.fromkeys(stats, 0) for g, _ in m6r.GROUPS}
    only_second = []
    for tid, r in second.items():
        before = first.get(tid)
        if before is None:
            only_second.append({"tid": tid, "comm": r["comm"], "run_ns": int(r["run_ns"])})
        g = groups[m6r.group(r["comm"])]
        for s in stats:
            now, then = int(r[s]), (int(before[s]) if before else 0)
            g[s] = None if g[s] is None or now < 0 or then < 0 else g[s] + now - then
    only_first = [{"tid": t, "comm": r["comm"], "run_ns": None} for t, r in first.items() if t not in second]
    return {"groups": groups, "only_first": only_first, "only_second": only_second}


def cpu_header(line):
    """The number of CPUs of a /proc/interrupts or /proc/softirqs header line ("CPU0 CPU1 ...")."""
    tokens = line.split()
    if not tokens or not all(re.fullmatch(r"CPU\d+", t) for t in tokens):
        raise ValueError(f"not a header of CPUs: {line!r}")
    return len(tokens)


def parse_interrupts(text):
    """{line: (name, [count per CPU])} of a /proc/interrupts text; the name is the text after the counts (chip,
    hardware irq, trigger and device). A line with fewer counts than CPUs (Err, MIS) keeps the counts it has."""
    lines = text.splitlines()
    ncpu = cpu_header(lines[0])
    out = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        irq, rest = line.split(":", 1)
        tokens = rest.split()
        counts = []
        while tokens and len(counts) < ncpu and tokens[0].isdigit():
            counts.append(int(tokens.pop(0)))
        out[irq.strip()] = (" ".join(tokens), counts)
    return out


def parse_softirqs(text):
    """{kind: [count per CPU]} of a /proc/softirqs text."""
    lines = text.splitlines()
    ncpu = cpu_header(lines[0])
    out = {}
    for line in lines[1:]:
        if ":" in line:
            kind, rest = line.split(":", 1)
            counts = [int(x) for x in rest.split()]
            if len(counts) != ncpu:
                raise ValueError(f"/proc/softirqs line {kind.strip()} has {len(counts)} counts for {ncpu} CPUs")
            out[kind.strip()] = counts
    return out


def parse_stat(text):
    """(irq, softirq) of /proc/stat's line "cpu", in ticks of USER_HZ, as the kernel gives them."""
    for line in text.splitlines():
        tokens = line.split()
        if tokens and tokens[0] == "cpu":
            return int(tokens[6]), int(tokens[7])
    raise ValueError("/proc/stat has no line cpu")


def parse_schedstat(text):
    """{cpu: run time in ns} of a /proc/schedstat text: the seventh number of each cpu<N> line, as
    src/core/metrics.cpp reads it (M2 §3.2)."""
    out = {}
    for line in text.splitlines():
        tokens = line.split()
        if tokens and re.fullmatch(r"cpu\d+", tokens[0]) and len(tokens) >= 8:
            out[int(tokens[0][3:])] = int(tokens[7])
    if not out:
        raise ValueError("/proc/schedstat has no cpu line")
    return out


def counts_delta(first, second):
    """{line: [delta per CPU]} of two parsed /proc/interrupts or /proc/softirqs: a line in the second only counts in
    full; one in the first only, or whose CPUs differ, is left out."""
    out = {}
    for k, v in second.items():
        now = v[1] if isinstance(v, tuple) else v
        then = first.get(k)
        then = None if then is None else then[1] if isinstance(then, tuple) else then
        if then is None:
            out[k] = list(now)
        elif len(then) == len(now):
            out[k] = [a - b for a, b in zip(now, then)]
    return out


def cpu0_values(meta, sched1, sched2):
    """§3: cpu0's busy time by its counter (cycles / 1800 MHz) next to cpu0's /proc/schedstat over the same span
    (between the snapshots' reads of it), their difference with the end reads' duration in it and without it
    (cpu0_counter_read_ns − end_tick_wake_ns). /proc/schedstat's difference also holds the metrics thread's slice
    from its wake-up for the first snapshot to the counter's enable, which the counter does not count: that start
    slice (cpu0_counter_enable_ns − snapshot1_tick_wake_ns), and the difference with both corrected, are None in a
    record without snapshot1_tick_wake_ns. Returns (values, problem): values None when the counter was not enabled
    or read."""
    if "cpu0_counter_cycles" not in meta:
        return None, "no counter in snapshot_meta.csv"
    for key in ("event_found", "enabled", "read_ok"):
        if meta.get(f"cpu0_counter_{key}") != "1":
            return None, f"cpu0's counter: {key} is {meta.get(f'cpu0_counter_{key}')!r} (errno " \
                         f"{meta.get('cpu0_counter_open_errno', '?')})"
    cpu = int(meta.get("cpu0_counter_cpu", "0"))
    busy = Fraction(int(meta["cpu0_counter_cycles"]), A55_HZ)
    sched = Fraction(sched2[cpu] - sched1[cpu], 10**9)
    end = Fraction(int(meta["cpu0_counter_read_ns"]) - int(meta["end_tick_wake_ns"]), 10**9)
    start = Fraction(int(meta["cpu0_counter_enable_ns"]) - int(meta["snapshot1_tick_wake_ns"]), 10**9) \
        if meta.get("snapshot1_tick_wake_ns") else None
    return {"cpu0_busy_s": busy, "cpu0_sched_s": sched, "cpu0_diff_s": busy - sched, "cpu0_end_reads_s": end,
            "cpu0_diff_less_end_s": busy - sched - end, "cpu0_start_slice_s": start,
            "cpu0_diff_both_s": None if start is None else busy - sched - end + start,
            "cpu0_multiplexed": meta.get("cpu0_counter_time_running_ns") != meta.get("cpu0_counter_time_enabled_ns")
            }, ""


def read_snapshots(d):
    """The values of the pair of snapshots in directory d (§1, §3) over their span. Raises when a file is missing or
    cannot be parsed, or a snapshot is not complete (it then counts as missing, §4)."""
    d = Path(d)
    meta = meta_values(d / "snapshot_meta.csv")
    problem = snapshot_problem(meta)
    if problem:
        raise Incomplete(problem)
    span_ns = int(meta["snapshot2_begin_ns"]) - int(meta["snapshot1_begin_ns"])
    if span_ns <= 0:
        raise ValueError(f"the snapshots' span is {span_ns} ns")
    text = {(k, n): (d / f"snapshot{k}_{n}.txt").read_text(encoding="utf-8")
            for k in (1, 2) for n in ("schedstat", "stat", "interrupts", "softirqs")}
    out = {"span_ns": span_ns, "meta": meta}
    out.update(other_tasks(ss.read_csv(d / "snapshot_tasks.csv")[1]))
    out["own"] = own_threads(ss.read_csv(d / "snapshot_own.csv")[1])
    irq1, irq2 = parse_interrupts(text[(1, "interrupts")]), parse_interrupts(text[(2, "interrupts")])
    out["irq"] = counts_delta(irq1, irq2)
    out["irq_names"] = {k: v[0] for k, v in irq2.items()}
    out["softirq"] = counts_delta(parse_softirqs(text[(1, "softirqs")]), parse_softirqs(text[(2, "softirqs")]))
    (i1, s1), (i2, s2) = parse_stat(text[(1, "stat")]), parse_stat(text[(2, "stat")])
    out["stat_irq"], out["stat_softirq"] = i2 - i1, s2 - s1
    sched1, sched2 = parse_schedstat(text[(1, "schedstat")]), parse_schedstat(text[(2, "schedstat")])
    out["sched_ns"] = sum(sched2[c] - sched1[c] for c in sched2 if c in sched1)
    out["cpu0"], out["cpu0_problem"] = cpu0_values(meta, sched1, sched2)
    return out


def selected_irq(name):
    """Whether an interrupt line's name holds npu, rga or rkvdec (§3), in any case."""
    return any(x in name.lower() for x in IRQ_NAMES)


def snapshot_flat(s):
    """The flat values of read_snapshots' result, keys snap_<name>: Fractions of s, or counts."""
    ns = lambda x: Fraction(x, 10**9)  # noqa: E731
    out = {"snap_span_s": ns(s["span_ns"])}
    for g, _ in KERNEL_GROUPS:
        out[f"snap_kernel_{g}_s"] = ns(s["kernel_ns"][g])
    out["snap_kernel_s"] = ns(sum(s["kernel_ns"].values()))
    out["snap_others_s"] = ns(sum(s["others_ns"].values()))
    out["snap_tasks_s"] = out["snap_kernel_s"] + out["snap_others_s"]
    out["snap_only_first"] = len(s["only_first"])
    out["snap_only_second"] = len(s["only_second"])
    out["snap_irq_total"] = sum(sum(v) for v in s["irq"].values())
    for k, v in s["irq"].items():
        if selected_irq(s["irq_names"].get(k, "")):
            out[f"snap_irq_{k}"] = sum(v)
            out.update({f"snap_irqcpu_{k}_{c}": x for c, x in enumerate(v)})
    out.update({f"snap_softirq_{k}": sum(v) for k, v in s["softirq"].items()})
    out["snap_stat_irq"], out["snap_stat_softirq"] = s["stat_irq"], s["stat_softirq"]
    out["snap_sched_s"] = ns(s["sched_ns"])
    groups = s["own"]["groups"]
    for g, _ in m6r.GROUPS:
        for key, _, _ in OWN_STATS:
            raw = groups[g][key.replace("_s", "_ns") if key.endswith("_s") else key]
            out[f"own_{g}_{key}"] = None if raw is None else ns(raw) if key.endswith("_s") else raw
    out["snap_own_s"] = ns(sum(groups[g]["run_ns"] for g, _ in m6r.GROUPS))
    out["snap_dec_s"] = ns(groups["dec"]["run_ns"])
    out["snap_dec_120_s"] = out["snap_dec_s"] * WINDOW_S / out["snap_span_s"]  # M7-H2's "Mixed" (§2)
    if s.get("cpu0"):
        out.update(s["cpu0"])
    return out


def less_idle(flat, idle):
    """{less_<name>: the run's value less the idle pair's scaled from its span to the run's} for the values the idle
    pair has (§4, §6); {} without an idle pair."""
    if not idle:
        return {}
    scale = flat["snap_span_s"] / idle["snap_span_s"]
    return {"less_" + k[5:]: v - idle[k] * scale for k, v in flat.items()
            if k.startswith("snap_") and k[5:].startswith(IDLE_PREFIXES) and idle.get(k) is not None
            and v is not None}


def others_less_idle(s, idle_s, scale):
    """{process name: s} of the other processes over the span, less the idle pair's for the same name scaled
    (a name the idle pair lacks ran 0 there); {} without an idle pair."""
    if not idle_s:
        return {}
    return {k: Fraction(v, 10**9) - Fraction(idle_s["others_ns"].get(k, 0), 10**9) * scale
            for k, v in s["others_ns"].items()}


# ---------------------------------------------------------------- the counters' file (§1, §3)

def read_counters(d):
    """Per thread group (tools/cpu_breakdown.py's), from counters.csv: each event's count summed over the group's
    threads whose group was opened, enabled, disabled and read, their run time read with the counters (run_ns_disable −
    run_ns_enable), and the threads left out. Returns ({group: {event: count, "run_ns", "threads", "left_out"}},
    [events]). Raises when the file is missing or cannot be parsed."""
    d = Path(d)
    _, rows = ss.read_csv(d / "counters.csv")
    if not rows:
        raise ValueError("counters.csv has no thread")
    events = [k for k in rows[0] if k not in COUNTER_COLUMNS]
    groups = {g: dict(dict.fromkeys(events, 0), run_ns=0, threads=0, left_out=[]) for g, _ in m6r.GROUPS}
    for r in rows:
        g = groups[m6r.group(r["comm"])]
        if any(r[k] != "1" for k in COUNTED_OK) or int(r["run_ns_enable"]) < 0 or int(r["run_ns_disable"]) < 0:
            g["left_out"].append(r["comm"])
            continue
        for e in events:
            g[e] += int(r[e])
        g["run_ns"] += int(r["run_ns_disable"]) - int(r["run_ns_enable"])
        g["threads"] += 1
    return groups, events


def counters_flat(groups, events):
    """The flat values of read_counters' result, keys ctr_<group>_<name>: each event's count; and where the group has
    cycles, instructions and run time: ipc, cycles_s (cycles / 2208 MHz), run_s, ratio (cycles_s / run_s) and mhz
    (cycles / run time, the clock the counters saw)."""
    out = {"ctr_events": tuple(events)}
    for g, x in groups.items():
        out.update({f"ctr_{g}_{e}": x[e] for e in events})
        cycles, inst, run_ns = x.get("cpu_cycles"), x.get("inst_retired"), x["run_ns"]
        out[f"ctr_{g}_ipc"] = Fraction(inst, cycles) if cycles and inst is not None else None
        out[f"ctr_{g}_cycles_s"] = Fraction(cycles, A76_HZ) if cycles is not None else None
        out[f"ctr_{g}_run_s"] = Fraction(run_ns, 10**9)
        out[f"ctr_{g}_ratio"] = out[f"ctr_{g}_cycles_s"] / out[f"ctr_{g}_run_s"] if cycles is not None and run_ns \
            else None
        out[f"ctr_{g}_mhz"] = Fraction(cycles * 1000, run_ns) if cycles is not None and run_ns else None
        out[f"ctr_{g}_left_out"] = len(x["left_out"])
    return out


# ---------------------------------------------------------------- one run

def arrival(run_dir, fps):
    """tools/m1_phases.py's arrival spread and drift (ms) at a sampling period of 1 / fps: tools/m6_report.py's
    run_values computes them at the main condition's period only."""
    run_dir = Path(run_dir)
    header, rows = ss.read_csv(run_dir / "summary.csv")
    window_s = Fraction({r["key"]: r["value"] for r in rows}["window_s"])
    warmup_ns = int(header["warmup_s"]) * 10**9
    end_ns = warmup_ns + round(window_s * 10**9)
    t0 = {}
    for f in ss.read_csv(run_dir / "frames.csv")[1]:
        if warmup_ns <= int(f["t4_ns"]) < end_ns:
            t0.setdefault(f["channel"], []).append(int(f["t0_ns"]))
    period_ns = 1e9 / fps
    if not t0 or len(t0) != len(ss.read_csv(run_dir / "channels.csv")[1]):
        return {"arrival_spread_ms": None, "arrival_drift_ms": None}
    return {"arrival_spread_ms": m6r.spread([m6r.phase(v, period_ns) for v in t0.values()], period_ns) / 1e6,
            "arrival_drift_ms": max(m6r.drift(v, period_ns) for v in t0.values()) / 1e6}


def extra_values(run_dir, arm, idle_pair):
    """A run's snapshot and counter values (§3), which never make the run unreadable (§4): the flat values, with
    "snap" (read_snapshots' result or None), "snap_problem", "others_less_idle", "counters_problem" and
    "cpu0_problem" ("" when there is none)."""
    out = {"snap": None, "snap_problem": "", "others_less_idle": {}, "counters_problem": "", "cpu0_problem": ""}
    try:
        s = read_snapshots(run_dir)
    except m6r.READ_ERRORS as e:
        out["snap_problem"] = problem_text(e)
    else:
        flat = snapshot_flat(s)
        idle_flat = snapshot_flat(idle_pair) if idle_pair else None
        out.update(flat, snap=s, cpu0_problem=s["cpu0_problem"])
        out.update(less_idle(flat, idle_flat))
        out["others_less_idle"] = others_less_idle(s, idle_pair, flat["snap_span_s"] / idle_flat["snap_span_s"]
                                                   if idle_flat else 1)
    if arm in COUNTED:
        try:
            out.update(counters_flat(*read_counters(run_dir)))
        except m6r.READ_ERRORS as e:
            out["counters_problem"] = problem_text(e)
    return out


def problem_text(e):
    """Why a run's snapshots or counters are missing, from the error reading them raised."""
    if isinstance(e, FileNotFoundError):
        return f"missing: {Path(e.filename).name}"
    return str(e) if isinstance(e, Incomplete) else f"{type(e).__name__}: {e}"


def run_values(run_dir, idle, sweep, arm, idle_pair):
    """{key: value} of one run: tools/m6_report.py's run_values (PROTOCOL §7, M2 §3.2, M6 §3's rknn_run values for Ct
    and Cpt), the arrival phases at the sweep's sampling period, and the run's snapshot and counter values. The
    window values and the left-over ones of §6: left_window_s (/proc/schedstat less idle − process CPU − the other
    tasks less the idle pair) and left_span_s (the same from the snapshots alone, over their span), and stat_excess_s
    (/proc/stat less idle − /proc/schedstat less idle)."""
    out = m6r.run_values(run_dir, idle)
    condition = SWEEPS[sweep][1]
    if condition != "main":
        out.update(arrival(run_dir, ss.FPS[condition]))
    out.update(extra_values(run_dir, arm, idle_pair))
    sched, stat, tasks = out.get("schedstat_total_s"), out.get("system_total_s"), out.get("less_tasks_s")
    out["left_window_s"] = None if None in (sched, tasks) else sched - out["process_cpu_s"] - tasks
    out["left_span_s"] = None if None in (out.get("less_sched_s"), tasks) else \
        out["less_sched_s"] - out["snap_own_s"] - tasks
    out["stat_excess_s"] = None if None in (sched, stat) else stat - sched
    if out.get("rknn_run_calls"):  # §6, question 4: the NPU workers' CPU per inference, across rknn_run and the rest
        out["npu_per_inference_ms"] = out["threads_npu_s"] * 1000 / out["rknn_run_calls"]
        out["npu_rest_ms"] = out["npu_per_inference_ms"] - out["rknn_run_cpu_mean_ms"]
    return out


def read_idle_pair(session_dir):
    """read_snapshots' result for a session's idle pair (§4), <session>/idle_snapshot; None without it or when it
    cannot be read (problem in the second value)."""
    d = Path(session_dir) / "idle_snapshot"
    if not d.is_dir():
        return None, "no idle_snapshot"
    try:
        return read_snapshots(d), ""
    except m6r.READ_ERRORS as e:
        return None, f"idle pair unreadable ({problem_text(e)})"


# ---------------------------------------------------------------- the sweeps' runs

def split_superseded(session_dirs):
    """(the sessions whose runs can count, the superseded ones), by stream_session.M7_SUPERSEDED."""
    dirs, names = [Path(d) for d in session_dirs], superseded_names()
    return [d for d in dirs if d.name not in names], [d for d in dirs if d.name in names]


def attempts(session_dirs, sweep, counted):
    """Every attempt of a sweep's sessions, in order, as tools/m6_report.py's attempts lists them, with M7's run
    values. An attempt whose record cannot be read gets {} and the problem, unless it counts: then Unreadable."""
    dirs = {Path(d).name: Path(d) for d in session_dirs}
    rows = m6r.runs_rows(dirs.values())
    idle = {name: m6r.read_idle(d) for name, d in dirs.items()}
    pairs = {name: read_idle_pair(d)[0] for name, d in dirs.items()}
    out = []
    for r in ss.load_records(list(dirs.values()), (sweep,)):
        key = (r["session"], r["name"])
        row, run_dir, problem = rows.get(key, {}), dirs[r["session"]] / r["name"], ""
        try:
            values = m6r.read_values(lambda d, i: run_values(d, i, sweep, r["variant"], pairs[r["session"]]),
                                     run_dir, idle[r["session"]])
        except m6r.Unreadable as e:
            if key in counted:
                raise m6r.Unreadable(f"a run that counts cannot be read: {e}") from None
            values, problem = {}, ("no run directory" if not run_dir.is_dir() else f"record unreadable ({e})")
        values.update(run=f"{r['session']}/{r['name']}", seq=row.get("seq", ""), arm=r["variant"])
        out.append(dict(r, values=values, counts=key in counted, seq=row.get("seq", ""), problem=problem,
                        position=order_position(sweep, r["repetition"], r["variant"]),
                        trip_samples=row.get("trip_samples", ""),
                        reasons="the session ended during the run" if r["lost"] else row.get("reasons", "")))
    return out


def collect(session_dirs, sweep):
    """One sweep's runs, as tools/m6_report.py's collect gives M6's: {"sweep", "sessions", "superseded", "arms",
    "complete", "counting": {(repetition, arm): values or None}, "per_arm": {arm: [values]}, "every",
    "superseded_runs"}."""
    sessions, superseded = split_superseded(session_dirs)
    channels = SWEEPS[sweep][0]
    arms = sweep_arms(sweep)
    counting = m3r.counting_runs(sessions, sweep, channels, arms)
    counted = {(d.parent.name, d.name) for d in counting.values() if d is not None}
    complete = sorted({rep for rep, _ in counting})
    completed_in = {rep: d.parent.name for (rep, _), d in counting.items() if d is not None}
    replaced_by = {old: s for (s, _), row in m6r.runs_rows(sessions).items()
                   for old in (row.get("replaces") or "").split()}
    every = attempts(sessions, sweep, counted)
    for e in every:
        e["why"] = "" if e["counts"] else m6r.why_not(e, complete, completed_in, replaced_by)
    values_of = {(e["session"], e["name"]): e["values"] for e in every}
    by_rep = {k: (values_of[(d.parent.name, d.name)] if d is not None else None) for k, d in counting.items()}
    per_arm = {a: [v for (_, arm), v in sorted(by_rep.items()) if arm == a and v is not None] for a in arms}
    old = attempts(superseded, sweep, set())
    for e in old:
        e["why"] = "superseded (M7 §5, §9)" + (f"; {e['problem']}" if e["problem"] and not e["lost"] else "")
    return {"sweep": sweep, "sessions": sessions, "superseded": superseded, "arms": arms, "complete": complete,
            "counting": by_rep, "per_arm": per_arm, "every": every, "superseded_runs": old}


# ---------------------------------------------------------------- the hypotheses (§2)

def value(name, m):
    """§2: a hypothesis's value from {arm: mean}; None where M7-H1's denominator is 0."""
    if name == "M7-H1":
        den = m["B"] - m["C"]
        return (m["B"] - m["Rc"]) / den if den else None
    return m["C"] - m["Bd"]


def cv_flags(name, runs):
    """§4's CV flag (PROTOCOL §9.3), above 10%: process CPU of B, Rc and C for M7-H1, D of Bd and C for M7-H2."""
    key, label = CV_KEYS[name]
    out = []
    for a in HYPOTHESES[name][1]:
        x = [v[key] for v in runs.get(a, []) if v.get(key) is not None]
        if m6r.cv_above(x, CV_LIMIT):
            out.append(f"CV of {a}'s {label} {100 * m3r.cv([float(y) for y in x]):.2f}%, above 10% (§4)")
    return out


def hypothesis(name, runs):
    """§2's verdicts of one hypothesis, in exact arithmetic. runs: {arm: [values of its runs that count]} of its
    sweep. Returns a dict: the arms, their numbers of runs ("n") and means, the value, "spec" and "practical", the
    secondary means and value, "mixed" (True, False, or None when it cannot be told) and the flags."""
    sweep, arms, threshold, formula, key, second = HYPOTHESES[name]
    condition = SWEEPS[sweep][1]
    n = {a: len(runs.get(a, [])) for a in arms}
    out = {"name": name, "arms": arms, "threshold": threshold, "formula": formula, "n": n, "means": {}, "value": None,
           "spec": "indeterminate", "practical": "indeterminate", "secondary": {}, "secondary_value": None,
           "mixed": None, "flags": cv_flags(name, runs)}
    flags = out["flags"]
    absent = [a for a in arms if a in not_run()]
    if absent:
        flags += [f"{a} is not run: {not_run_why(a)}; both verdicts are indeterminate (§2, §5)" for a in absent]
        return out
    empty = [a for a in arms if not n[a]]
    if empty:
        flags.append(f"no run that counts of {', '.join(empty)}: both verdicts are indeterminate (§2)")
        return out
    m = out["means"] = {a: m6r.exact_mean(v[key] for v in runs[a]) for a in arms}
    out["value"] = value(name, m)
    if out["value"] is None:
        flags.append(f"{formula} is undefined: its denominator is 0")
        return out
    out["spec"] = out["practical"] = "adopted" if out["value"] >= threshold else "rejected"
    few = [f"{a} {n[a]}" for a in arms if n[a] < REPETITIONS]
    if few:
        flags.append(f"fewer than 3 runs that count ({', '.join(few)}): the spec-literal verdict uses the runs that "
                     "exist (§2)")
        out["practical"] = "indeterminate"
    slow = [v["run"] for a in arms for v in runs[a]
            if v.get("inference_fps_min") is None or v["inference_fps_min"] < MIN_FPS[condition]]
    if slow:
        flags.append(f"a channel below {float(MIN_FPS[condition]):g} fps in {', '.join(slow)} (§2)")
        out["practical"] = "indeterminate"
    no_drop = [v["run"] for a in arms for v in runs[a] if v.get("drop_rate") is None]
    if no_drop:
        flags.append(f"no drop rate in {', '.join(no_drop)} (§2)")
        out["practical"] = "indeterminate"
    drops = {a: m6r.exact_mean(v["drop_rate"] for v in runs[a] if v.get("drop_rate") is not None) for a in arms}
    for i, a in enumerate(arms):
        for b in arms[i + 1:]:
            if None not in (drops[a], drops[b]) and abs(drops[a] - drops[b]) > DROP_PP:
                flags.append(f"the mean drop rates of {a} and {b} differ by "
                             f"{100 * float(abs(drops[a] - drops[b])):.3f} percentage points, more than 1 (§2)")
                out["practical"] = "indeterminate"
    missing = {a: [v["run"] for v in runs[a] if v.get(second) is None] for a in arms}
    if name == "M7-H2" and any(missing.values()):  # §2: no label, and the report says so
        flags.append("no complete snapshots in " + ", ".join(r for a in arms for r in missing[a])
                     + ": no mixed label (§2)")
        return out
    partial = [f"{a} {len(missing[a])} of {n[a]}" for a in arms if 0 < len(missing[a]) < n[a]]
    if partial:
        flags.append(f"no /proc/schedstat value less idle in some runs that count ({', '.join(partial)}): the "
                     "secondary means use the others")
    s = {a: m6r.exact_mean(v[second] for v in runs[a] if v.get(second) is not None) for a in arms}
    if None in s.values():
        flags.append(f"the secondary metric is missing for {', '.join(a for a in arms if s[a] is None)}: "
                     "no mixed label")
        return out
    out["secondary"], out["secondary_value"] = s, value(name, s)
    if out["secondary_value"] is None:
        flags.append(f"{formula} is undefined on /proc/schedstat: no mixed label")
        return out
    out["mixed"] = (out["value"] >= threshold) != (out["secondary_value"] >= threshold)
    return out


# ---------------------------------------------------------------- §6's other values

def interval(by_rep, a, b, key):
    """tools/m6_report.py's paired: ({repetition: a − b}, mean, half-width of the descriptive 95% t interval)."""
    return m6r.paired(by_rep, a, b, key)


def excludes_zero(mean, half):
    """Whether a descriptive 95% interval excludes zero; False without an interval (fewer than 2 pairs)."""
    return half is not None and (mean - half > 0 or mean + half < 0)


def description(by_rep, a, b, group="dec"):
    """§6's rule for a pair of counted arms and a thread group: "with a difference in instructions" if the interval of
    the paired difference of inst_retired excludes zero, "in instructions per cycle" if that of instructions per
    cycle does, both if both do, each with its sign, and otherwise "not shown". A description, not a verdict."""
    found = []
    for key, what in ((f"ctr_{group}_inst_retired", "in instructions"), (f"ctr_{group}_ipc",
                                                                         "in instructions per cycle")):
        _, mean, half = interval(by_rep, a, b, key)
        if excludes_zero(mean, half):
            found.append(f"{what} ({'+' if mean > 0 else '−'})")
    return "not shown" if not found else "with a difference " + " and ".join(found)


def counter_cost(by_rep, per_arm, counted):
    """§5's rule for Xk − X: (the mean of the paired differences of process CPU, exactly; 2% of X's mean process CPU;
    whether the mean is above it). None for the first two without a pair or without X's runs."""
    cell = COUNTED[counted]
    d, _, _ = interval(by_rep, counted, cell, "process_cpu_s")
    x = per_arm.get(cell, [])
    if not d or not x:
        return None, None, None
    mean = m6r.exact_mean(d.values())
    limit = COST_LIMIT * m6r.exact_mean(v["process_cpu_s"] for v in x)
    return mean, limit, mean > limit


def per_inference_ms(diff_s, sweep):
    """A difference per window as ms per inference, over the point's nominal inferences per window (channels ×
    inference rate × 120 s: 4,800 and 18,000), as §2's reason computes M6's Rc − C."""
    channels, condition, _ = SWEEPS[sweep]
    return None if diff_s is None else float(diff_s) * 1000 / (channels * ss.FPS[condition] * WINDOW_S)


def counted_values(session_dirs, phase, channels, arms):
    """{arm: [values of its runs that count]} of an earlier sweep (tools/m6_report.py's run_values)."""
    runs = m3r.counting_runs([Path(d) for d in session_dirs], phase, channels, arms)
    out = {a: [] for a in arms}
    for (_, arm), d in sorted(runs.items()):
        if d is not None:
            out[arm].append(m6r.read_values(m6r.run_values, d, m6r.read_idle(d.parent)))
    return out


def references(root=ROOT):
    """§6: B and C at each point in the earlier milestones, read from their records: {point: {label: {arm:
    [values]}}}: at H1's point M1, M3, M5 (tools/m6_report.py's references) and M6; at the auxiliary point M1 and
    M1.1."""
    main = {k: {a: v for a, v in runs.items() if a in ("B", "C")} for k, runs in m6r.references(root).items()}
    main["M6"] = {a: r for a, r in m6r.collect(m6r.m6_sessions(root))["per_arm"].items() if a in ("B", "C")}
    aux = {label: counted_values([root / path], phase, SWEEPS["m7_aux"][0], ("B", "C"))
           for label, path, phase in AUX_REFERENCES}
    return {"main": main, "aux": aux}


# ---------------------------------------------------------------- Markdown

fmt, pct, cell, text = m6r.fmt, m6r.pct, m6r.cell, m6r.text


mean_of, means = m6r.mean_of, m6r.means


def mega(x):
    """A count in millions, for the counters' tables."""
    return None if x is None else float(x) / 1e6


def sweep_title(sweep):
    channels, condition, _ = SWEEPS[sweep]
    return f"`{sweep}`, {channels} channels, {'main' if condition == 'main' else 'auxiliary'} condition"


def status_line(cs, smoke):
    if smoke:
        return ("**A smoke test:** its windows are not the protocol's, and it counts for nothing. The verdicts below "
                "are §2's arithmetic on its runs, not M7's outcome.")
    done = {s: len(c["complete"]) for s, c in cs.items()}
    if any(n < REPETITIONS for n in done.values()):
        return ("**No outcome yet:** " + ", ".join(f"{n} of {REPETITIONS} repetitions of `{s}`"
                                                   for s, n in done.items())
                + " are complete. The verdicts below are §2's arithmetic on the runs that count so far.")
    return "Both sweeps are complete: the verdicts below are M7's (§2)."


def hypotheses_section(cs, smoke):
    out = ["## Hypotheses (§2)", "", status_line(cs, smoke), "",
           "| Hypothesis | Sweep | Means (s) | Value | Threshold | Spec-literal | Practical | "
           "Mixed label's means (s) | Value on it | Mixed | Flags |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    notes = []
    for name, (sweep, *_rest) in HYPOTHESES.items():
        h = hypothesis(name, cs[sweep]["per_arm"])
        m = ", ".join(f"{a} {fmt(h['means'][a])} ({h['n'][a]})" for a in h["arms"] if a in h["means"]) or "–"
        s = ", ".join(f"{a} {fmt(h['secondary'][a])}" for a in h["arms"] if a in h["secondary"]) or "–"
        mixed = "–" if h["mixed"] is None else "mixed" if h["mixed"] else "none"
        threshold = f"{float(h['threshold']):.2f}" + (" s" if name == "M7-H2" else "")
        out.append(f"| {name}: {h['formula']} ≥ {threshold} | `{sweep}` | {m} | {fmt(h['value'], 4)} | {threshold} | "
                   f"{h['spec']} | {h['practical']} | {s} | {fmt(h['secondary_value'], 4)} | {mixed} | "
                   f"{text('; '.join(h['flags']) or 'none')} |")
        notes.append(f"- **{name}:** " + " ".join(NOTES[name]))
    return out + [""] + notes + [f"- **Both:** {BOTH_NOTE}", "",
                                 "Means over the runs that count (n): M7-H1 on process CPU, M7-H2 on D, the decoder "
                                 "group's CPU in threads.csv. The verdicts and the mixed labels are decided exactly "
                                 "on the records' decimal values; the table rounds them. Mixed label: M7-H1 from "
                                 "/proc/schedstat's system CPU less the idle baseline (M2 §3.2); M7-H2 from the "
                                 "snapshots' run times of the decoder group's threads (snapshot 2 less snapshot 1), "
                                 "scaled from the snapshots' span to 120 s (§2)."]


def cells_section(cs):
    out = ["## The four cells (question 1, §6)", ""]
    for sweep, c in cs.items():
        pa = c["per_arm"]
        m = means(pa, "process_cpu_s")
        bc = m6r.diff(m, "B", "C")
        out += [f"### {sweep_title(sweep)}", "",
                f"From the means of the runs that count. B − C = {fmt(bc)} s; each value also as a share of it.", "",
                "| Difference | Process CPU (s) | Share of B − C |", "|---|---|---|"]
        out += [f"| {label} | {fmt(v)} | {pct(m6r.ratio(v, bc))} |" for label, v in m6r.cells(m)]
        out += ["", "By thread group (tools/cpu_breakdown.py's groups, from threads.csv, the threads alive at the "
                "window's end; the remainder is process CPU less the threads' sum), in s, with the share of process "
                "CPU's B − C:", "", "| Difference | " + " | ".join(label for _, label in m6r.GROUP_KEYS) + " |",
                "|---|" + "---|" * len(m6r.GROUP_KEYS)]
        by_group = {k: m6r.cells(means(pa, k)) for k, _ in m6r.GROUP_KEYS}
        for i, (label, _) in enumerate(m6r.cells(m)):
            out.append(f"| {label} | " + " | ".join(
                f"{fmt(by_group[k][i][1])} ({pct(m6r.ratio(by_group[k][i][1], bc))})" for k, _ in m6r.GROUP_KEYS)
                + " |")
        out.append("")
    main = means(cs["m7_main"]["per_arm"], "process_cpu_s")
    aux = means(cs["m7_aux"]["per_arm"], "process_cpu_s")
    m6 = {a: float(x) for a, x in M6_CELLS.items()}
    m11 = {a: float(x) for a, x in M11_AUX.items()}
    columns = (("`m7_main`", main, "m7_main"), ("M6, H1's point (other session)", m6, "m7_main"),
               ("`m7_aux`", aux, "m7_aux"), ("M1.1, auxiliary point (other session)", m11, "m7_aux"))
    out += ["### Next to the earlier sessions", "",
            "Across sessions, so labelled: M6's means at H1's point and M1.1's at the auxiliary point, from "
            "prereg/M7.md's Disclosure (Rc never ran at the auxiliary point before M7). Seconds per window, and the "
            "share of the same column's B − C.", "",
            "| Difference | " + " | ".join(label for label, _, _ in columns) + " |", "|---|" + "---|" * len(columns)]
    for i, (label, _) in enumerate(m6r.cells(main)):
        row = []
        for _, m, _ in columns:
            v = m6r.cells(m)[i][1]
            row.append(f"{fmt(v)} ({pct(m6r.ratio(v, m6r.diff(m, 'B', 'C')))})")
        out.append(f"| {label} | " + " | ".join(row) + " |")
    out += ["", "Per inference (ms), over the point's nominal inferences per window (8 × 5 × 120 = 4,800; 5 × 30 × 120 "
            "= 18,000), as §2's reason computes M6's Rc − C:", "",
            "| Difference | " + " | ".join(label for label, _, _ in columns) + " |", "|---|" + "---|" * len(columns)]
    for label, a, b in (("Rc − C", "Rc", "C"), ("B − Bd", "B", "Bd")):
        out.append(f"| {label} | " + " | ".join(fmt(per_inference_ms(m6r.diff(m, a, b), s), 4)
                                               for _, m, s in columns) + " |")
    return out


def decoder_section(cs):
    main, aux = cs["m7_main"], cs["m7_aux"]
    out = ["## The decoder's threads (question 2, §6)", "",
           "D: the decoder group's CPU in the window, from threads.csv (dec-*, mpp_dec_parser, mpp_dec_hal). From the "
           "snapshots: the same threads' run time over the snapshots' span (snapshot 2 less snapshot 1), and scaled to "
           "120 s as M7-H2's mixed label takes it. Mean ± sample sd (n) over the runs that count.", "",
           "| Sweep | Arm | D (s) | Snapshots, over their span (s) | Snapshots, scaled to 120 s (s) |",
           "|---|---|---|---|---|"]
    for sweep, c in cs.items():
        for a in c["arms"]:
            runs = c["per_arm"][a]
            out.append(f"| `{sweep}` | {a} | {cell(v.get('threads_dec_s') for v in runs)} | "
                       f"{cell(v.get('snap_dec_s') for v in runs)} | {cell(v.get('snap_dec_120_s') for v in runs)} |")
    out += ["", "Earlier sessions (prereg/M7.md, Disclosure; other sessions, so labelled), D in s:", "",
            "| Session | Point | B | Bd | Rc | C |", "|---|---|---|---|---|---|"]
    out += [f"| {s} | {POINT_LABELS[p]} | " + " | ".join(v.get(a) or "–" for a in ("B", "Bd", "Rc", "C")) + " |"
            for s, p, v in EARLIER_D]
    out += ["", "Its paired differences between the four cells are in the paired differences below (D).", "",
            "### Rc and Rck by position in the session", "",
            "Process CPU and D of every run that counts against its slot. " + SLOT_NOTE, "",
            "| Arm | Run | Repetition | Slot | Process CPU (s) | D (s) |", "|---|---|---|---|---|---|"]
    for a in ("Rc", "Rck"):
        for (rep, arm), v in sorted(main["counting"].items()):
            if arm == a and v is not None:
                out.append(f"| {a} | `{v['run']}` | {rep} | {text(v['seq'])} | {fmt(v['process_cpu_s'])} | "
                           f"{fmt(v['threads_dec_s'])} |")
    out += [f"| Rc, M6 (other session) | – | {i + 1} | {slot} | {cpu} | {dec} |" for i, (slot, cpu, dec)
            in enumerate(M6_RC)]
    out += [""] + counters_section(main) + [""] + cost_section(main)
    return out


def counters_section(c):
    arms = [a for a in COUNTED if a in c["arms"]]
    out = ["### The counters (counted arms, `m7_main`)", ""]
    if not arms:
        reasons = "; ".join(f"{a} ({not_run_why(a)})" for a in COUNTED if a in not_run())
        return out + [f"The counted arms are not run (§5): {reasons or 'none in this sweep'}. Question 2's "
                      "description by counters is missing; M7-H2 is not affected."]
    pa = c["per_arm"]
    events = []
    for a in arms:
        for v in pa[a]:
            events += [e for e in v.get("ctr_events", ()) if e not in events]
    out += ["Per thread group, over the window (§1), mean ± sample sd (n) over the runs that count: each event's "
            "count in millions; instructions per cycle; cycles / 2208 MHz next to the group's run time read with the "
            "counters (run_ns_disable − run_ns_enable), their ratio, and cycles / run time, the clock the counters saw "
            "(MHz). From the snapshots: the group's timeslices, context switches and time waiting to run (over their "
            "span). Threads left out: whose group was not opened, enabled, disabled or read.", ""]
    for g, label in m6r.GROUPS:
        rows = ([(f"ctr_{g}_{e}", f"{e} (10⁶)", 3, mega) for e in events]
                + [(f"ctr_{g}_ipc", "Instructions per cycle", 4, None),
                   (f"ctr_{g}_cycles_s", "Cycles / 2208 MHz (s)", 3, None),
                   (f"ctr_{g}_run_s", "Run time read with the counters (s)", 3, None),
                   (f"ctr_{g}_ratio", "Their ratio", 4, None), (f"ctr_{g}_mhz", "Cycles / run time (MHz)", 1, None),
                   (f"ctr_{g}_left_out", "Threads left out", 0, None)]
                + [(f"own_{g}_{k}", f"Snapshots: {name.lower() if k != 'run_s' else 'run time (s)'}", d, None)
                   for k, name, d in OWN_STATS])
        out += [f"**{label}**", "", "| Value | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
        for key, name, digits, scale in rows:
            out.append(f"| {name} | " + " | ".join(
                cell(((scale or (lambda x: x))(v.get(key)) for v in pa[a]), digits) for a in arms) + " |")
        out.append("")
    out += ["**The description (§6):** for each pair and the decoder's group, by one rule: *with a difference in "
            "instructions* if the descriptive 95% interval of the paired difference of inst_retired excludes zero, "
            "*in instructions per cycle* if that of instructions per cycle does, both if both do, each with its sign "
            "(of the mean paired difference), and otherwise *not shown*. A description, not a verdict.", "",
            "| Pair | Decoder group's D, mean paired difference (s) | Description |", "|---|---|---|"]
    for a, b in COUNTER_PAIRS:
        if a in c["arms"] and b in c["arms"]:
            _, mean, _ = interval(c["counting"], a, b, "threads_dec_s")
            out.append(f"| {a} − {b} | {fmt(mean)} | {description(c['counting'], a, b)} |")
        else:
            out.append(f"| {a} − {b} | – | not run |")
    out += ["", "Paired differences of the counters, per thread group (each repetition's difference, their mean and a "
            "descriptive 95% t interval; counts in millions):", "",
            "| Pair | Group | Value | r1 | r2 | r3 | Mean | 95% t interval |", "|---|---|---|---|---|---|---|---|"]
    for a, b in COUNTER_PAIRS:
        if a not in c["arms"] or b not in c["arms"]:
            continue
        for g, label in m6r.GROUPS:
            for key, name, scale, digits in ([(f"ctr_{g}_{e}", f"{e} (10⁶)", 1e6, 3) for e in events]
                                             + [(f"ctr_{g}_ipc", "Instructions per cycle", 1, 4)]
                                             + [(f"own_{g}_{k}", f"Snapshots: {n.lower()}", 1, d)
                                                for k, n, d in OWN_STATS[:3]]):
                out.append(paired_row(f"{a} − {b}", label.split(" (")[0], name, c["counting"], a, b, key, digits,
                                      scale))
    return out


def paired_row(pair, group, name, by_rep, a, b, key, digits=3, scale=1, unit=""):
    d, mean, half = interval(by_rep, a, b, key)
    f = lambda x: None if x is None else x / scale  # noqa: E731
    span = "–" if half is None else f"{fmt(f(mean - half), digits)} to {fmt(f(mean + half), digits)}{unit}"
    return (f"| {pair} | {group} | {name} | " + " | ".join(fmt(f(d.get(r)), digits) for r in range(1, 4))
            + f" | {fmt(f(mean), digits)} | {span} |")


def cost_section(c):
    out = ["### Xk − X, the counters' cost (§5, §6)", "",
           "The mean of the paired differences of process CPU, Xk − X, against 2% of X's mean process CPU over its "
           "runs that count. Above it, §5 says that the counted arm is not the cell with counters added but an arm "
           "of its own, and question 2's counters are read with that said. Their paired differences on every metric "
           "are below.", "", "| Cell | Xk − X, mean (s) | 2% of X (s) | Reading |", "|---|---|---|---|"]
    for counted, x in COUNTED.items():
        if counted not in c["arms"] or x not in c["arms"]:
            out.append(f"| {counted} − {x} | – | – | not run |")
            continue
        mean, limit, above = counter_cost(c["counting"], c["per_arm"], counted)
        reading = "–" if above is None else (f"above 2%: {counted} is not {x} with counters added but an arm of its "
                                             "own (§5)") if above else "not above 2%"
        out.append(f"| {counted} − {x} | {fmt(mean)} | {fmt(limit)} | {reading} |")
    return out


def kernel_rows(c):
    """The rows of the kernel side's per-arm table: (key, label, digits), with the interrupt lines of the NPU, RGA and
    rkvdec that some run of the sweep has, by their line and name."""
    rows = [("snap_span_s", "Snapshots' span (s)", 3), ("kernel_s", "Kernel threads, all (s)", 3)]
    rows += [(f"kernel_{g}_s", f"Kernel threads, {label} (s)", 3) for g, label in KERNEL_GROUPS]
    rows += [("others_s", "Other processes, all (s)", 3), ("tasks_s", "All other tasks (s)", 3),
             ("snap_only_first", "Threads in the first snapshot only (n)", 1),
             ("snap_only_second", "Threads in the second snapshot only (n)", 1),
             ("irq_total", "Interrupts, all lines", 0)]
    names = irq_names(c)
    rows += [(f"irq_{k}", f"Interrupts, line {k} ({names[k]})", 0) for k in names]
    kinds = []
    for v in values_of(c):
        kinds += [k[len("snap_softirq_"):] for k in v if k.startswith("snap_softirq_")
                  and k[len("snap_softirq_"):] not in kinds]
    rows += [(f"softirq_{k}", f"Soft interrupts, {k}", 0) for k in kinds]
    rows += [("stat_irq", "/proc/stat irq (ticks of 10 ms)", 1), ("stat_softirq", "/proc/stat softirq (ticks of 10 ms)",
                                                                   1)]
    return rows


def values_of(c):
    return [v for runs in c["per_arm"].values() for v in runs]


def irq_names(c):
    """{line: name} of the NPU's, RGA's and rkvdec's interrupt lines in the runs that count, by line."""
    out = {}
    for v in values_of(c):
        if v.get("snap"):
            out.update({k: n for k, n in v["snap"]["irq_names"].items() if selected_irq(n) and k in v["snap"]["irq"]})
    return dict(sorted(out.items(), key=lambda x: (not x[0].isdigit(), int(x[0]) if x[0].isdigit() else 0, x[0])))


def kernel_section(cs, pairs, cpu0_ok):
    out = ["## The kernel's side (question 3, §6)", "",
           SPAN_NOTE + " Each value as the run's own, then less the session's idle pair scaled from its span to the "
           "run's (§4), where the idle pair has the value. Mean ± sample sd (n) over the runs that count with complete "
           "snapshots. Kernel threads: their process is pid 2 or has pid 2 as its parent; a thread goes to the first "
           "name group it matches (§3's order), by its name in the second snapshot. A kworker's name carries its "
           "current work item, so a kworker running an rknpu item at the second snapshot counts in the npu group. "
           "A thread of the second snapshot only counts in "
           "full; one of the first only is counted and listed, its contribution unknown (§1). Interrupts: the lines "
           "whose name holds npu, rga or rkvdec, in any case; a line of the second snapshot only counts in full.",
           "",
           "As M2 §3.2 has it: the time of an interrupt that arrives while a task runs is inside that task's value, "
           "and the time of one that arrives while its CPU is idle is in no task's. For the second part M7 has the "
           "interrupt counts and, if its check passed, cpu0's counter against cpu0's /proc/schedstat value (§3). It "
           "has nothing that separates the first part from the task it falls in (§6).", ""]
    for sweep, c in cs.items():
        arms, pa = c["arms"], c["per_arm"]
        out += [f"### {sweep_title(sweep)}", "", "| Value | " + " | ".join(f"{a} | {a}, less idle" for a in arms)
                + " |", "|---|" + "---|---|" * len(arms)]
        for key, label, digits in kernel_rows(c):
            raw = key if key.startswith("snap_") else "snap_" + key
            less = None if key.startswith("snap_") else "less_" + key
            out.append(f"| {label} | " + " | ".join(
                f"{cell((v.get(raw) for v in pa[a]), digits)} | "
                f"{cell((v.get(less) for v in pa[a]), digits) if less else ''}" for a in arms) + " |")
        out += ["", "The five largest other processes by name (their main thread's name), over the span, mean over the "
                "runs that count (s), less the idle pair's for the same name:", "",
                "| Arm | Processes |", "|---|---|"]
        for a in arms:
            out.append(f"| {a} | {text(top_five(pa[a]))} |")
        names = irq_names(c)
        if names:
            ncpu = max(len(v["snap"]["irq"][k]) for v in values_of(c) if v.get("snap") for k in names
                       if k in v["snap"]["irq"])
            out += ["", "Interrupts of these lines per CPU, mean over the runs that count, less the idle pair's:", "",
                    "| Arm | Line | " + " | ".join(f"CPU{i}" for i in range(ncpu)) + " |",
                    "|---|---|" + "---|" * ncpu]
            for a in arms:
                for k in names:
                    out.append(f"| {a} | {k} ({text(names[k])}) | " + " | ".join(
                        fmt(mean_of(pa[a], f"less_irqcpu_{k}_{i}"), 0) for i in range(ncpu)) + " |")
        out += ["", "System CPU next to the tasks (s): /proc/schedstat's and /proc/stat's system CPU less the idle "
                "baseline and the process CPU are over the window (120 s); the other tasks are over the snapshots' "
                "span less the idle pair. Left, window: /proc/schedstat − process CPU − the other tasks. Left, span: "
                "the same from the snapshots alone (/proc/schedstat over the span less the idle pair's − bench_main's "
                "threads − the other tasks). /proc/stat − /proc/schedstat: its excess (or, below 0, its shortfall).",
                "", "| Value | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
        for key, label in (("schedstat_total_s", "/proc/schedstat, less idle (window)"),
                           ("process_cpu_s", "Process CPU (window)"),
                           ("less_tasks_s", "All other tasks, less the idle pair (span)"),
                           ("left_window_s", "Left, window"),
                           ("less_sched_s", "/proc/schedstat, less the idle pair (span)"),
                           ("snap_own_s", "bench_main's threads (span)"), ("left_span_s", "Left, span"),
                           ("system_total_s", "/proc/stat, less idle (window)"),
                           ("stat_excess_s", "/proc/stat − /proc/schedstat (window)")):
            out.append(f"| {label} | " + " | ".join(cell(v.get(key) for v in pa[a]) for a in arms) + " |")
        out += ["", "Whether a task's CPU accounts for /proc/stat's excess or shortfall, per run that counts: the "
                "other processes (less the idle pair, over the span) whose CPU is at least the excess's size, and the "
                "largest one.", "", "| Run | Arm | /proc/stat − /proc/schedstat (s) | Largest other process (s) | "
                "Processes at least its size |", "|---|---|---|---|---|"]
        for a in arms:
            for v in pa[a]:
                out.append(excess_row(v))
        out += [""] + cpu0_section(c, cpu0_ok) + [""] + own_section(c)
        out += ["", "Paired differences of the kernel's side (less the idle pair):", "",
                "| Pair | Group | Value | r1 | r2 | r3 | Mean | 95% t interval |", "|---|---|---|---|---|---|---|---|"]
        for a, b in pairs:
            if a not in arms or b not in arms:
                out.append(f"| {a} − {b} | – | not run | | | | | |")
                continue
            for key, label, digits in (("less_kernel_s", "Kernel threads, all (s)", 3),
                                       *((f"less_kernel_{g}_s", f"Kernel threads, {n} (s)", 3)
                                         for g, n in KERNEL_GROUPS),
                                       ("less_irq_total", "Interrupts, all lines", 0),
                                       *((f"less_irq_{k}", f"Interrupts, line {k}", 0) for k in names)):
                out.append(paired_row(f"{a} − {b}", "kernel's side", label, c["counting"], a, b, key, digits))
        out += ["", "The idle pair of each session (raw, over its own span):", "", idle_table(c), ""]
    return out


def top_five(runs):
    """The five largest other processes by name, from the mean over the runs with snapshots (a name absent from a
    run ran 0 there), each as its mean over the span and less the idle pair's."""
    runs = [v for v in runs if v.get("snap")]
    if not runs:
        return "–"
    total, less = {}, {}
    for v in runs:
        for k, x in v["snap"]["others_ns"].items():
            total[k] = total.get(k, 0) + Fraction(x, 10**9) / len(runs)
        for k, x in v["others_less_idle"].items():
            less[k] = less.get(k, 0) + x / len(runs)
    top = sorted(total, key=lambda k: (-total[k], k))[:5]
    return "; ".join(f"{k} {fmt(total[k])} ({fmt(less.get(k))} less idle)" for k in top)


def excess_row(v):
    excess, others = v.get("stat_excess_s"), v.get("others_less_idle") or {}
    largest = max(others, key=lambda k: others[k], default=None)
    big = [k for k, x in sorted(others.items(), key=lambda x: -x[1]) if excess is not None and x >= abs(excess)]
    return (f"| `{v['run']}` | {v.get('arm', '')} | {fmt(excess)} | "
            f"{'–' if largest is None else text(f'{largest} {fmt(others[largest])}')} | "
            f"{'–' if excess is None or not others else text(', '.join(big) or 'none')} |")


def cpu0_section(c, cpu0_ok):
    out = ["cpu0's busy time by its counter (§3): cycles / 1800 MHz, next to cpu0's /proc/schedstat over the same span "
           "(between the two snapshots' reads of it). Their difference is the time cpu0 was active outside every "
           "task, plus the metrics thread's end slice, less its start slice. The end slice: the reads at the window's "
           "end and the second snapshot's, from the end tick's wake-up to the counter's read; the counter counts it, "
           "and /proc/schedstat adds it only when the thread is switched out (§3). The start slice: from the wake-up "
           "for the first snapshot's tick to the counter's enable; /proc/schedstat holds it and the counter does not. "
           "Rows: the counter; /proc/schedstat; their difference (with the end slice, §3's \"with it\"); the end "
           "slice; the difference without it (§3's \"without it\"); the start slice; and the difference with both "
           "corrected (counter − /proc/schedstat − end slice + start slice). A record without "
           "snapshot1_tick_wake_ns has no start slice, and its last two cells are empty (–)."]
    if not cpu0_ok:
        return out + ["", "**cpu0's counter failed its check** (§8 step 3, as the amendment records it; "
                      "`--cpu0-check failed`): the report gives no value of it (§3, §5)."]
    arms, pa = c["arms"], c["per_arm"]
    out += ["", "| Value | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
    for key, label in (("cpu0_busy_s", "Busy by the counter (s)"), ("cpu0_sched_s", "cpu0's /proc/schedstat (s)"),
                       ("cpu0_diff_s", "Difference, with the end reads (s)"),
                       ("cpu0_end_reads_s", "The end reads' duration, the end slice (s)"),
                       ("cpu0_diff_less_end_s", "Difference, without them (s)"),
                       ("cpu0_start_slice_s", "The start slice (s)"),
                       ("cpu0_diff_both_s", "Difference, both slices corrected (s)")):
        out.append(f"| {label} | " + " | ".join(cell((v.get(key) for v in pa[a]), 4) for a in arms) + " |")
    multiplexed = [v["run"] for v in values_of(c) if v.get("cpu0_multiplexed")]
    if multiplexed:
        out += ["", f"The counter's time_running differs from its time_enabled in: {', '.join(multiplexed)}."]
    return out


def own_section(c):
    arms, pa = c["arms"], c["per_arm"]
    out = ["bench_main's threads by group, from the snapshots (over their span, mean ± sd (n)):", "",
           "| Group | Value | " + " | ".join(arms) + " |", "|---|---|" + "---|" * len(arms)]
    for g, label in m6r.GROUPS:
        for k, name, digits in OWN_STATS:
            out.append(f"| {label.split(' (')[0]} | {name} | " + " | ".join(
                cell((v.get(f"own_{g}_{k}") for v in pa[a]), digits) for a in arms) + " |")
    return out


def idle_table(c):
    rows = ["| Session | Idle pair | Span (s) | Kernel threads (s) | Other processes (s) | Interrupts, all lines |",
            "|---|---|---|---|---|---|"]
    for d in c["sessions"]:
        pair, problem = read_idle_pair(d)
        if pair is None:
            rows.append(f"| `{d.name}` | {text(problem)} | – | – | – | – |")
            continue
        f = snapshot_flat(pair)
        rows.append(f"| `{d.name}` | yes | {fmt(f['snap_span_s'])} | {fmt(f['snap_kernel_s'])} | "
                    f"{fmt(f['snap_others_s'])} | {fmt(f['snap_irq_total'], 0)} |")
    return "\n".join(rows)


def rknn_section(cs):
    c = cs["m7_aux"]
    pa = c["per_arm"]
    arms = [a for a in ("Ct", "Cpt") if a in c["arms"]]
    rows = tuple((k, label, d, M6_CT.get(k, "–")) for k, label, d in m6r.CT_ROWS) + (
        ("npu_per_inference_ms", "NPU workers' CPU per inference (ms)", 4, "–"),
        ("rknn_run_cpu_mean_ms", "NPU workers' CPU per inference, across rknn_run (ms)", 4, "–"),
        ("npu_rest_ms", "NPU workers' CPU per inference, the rest (ms)", 4, "–"))
    out = ["## rknn_run (question 4, §6)", "",
           "Ct and Cpt at the auxiliary condition (M6 §3's values; mean ± sample sd (n) over the runs that count), "
           "next to M6's Ct at H1's point (other session; prereg/M7.md's Disclosure). Per inference: over the calls, "
           "which are the inferences whose t4 is in the window.", "",
           "| Value | " + " | ".join(arms) + " | Ct, M6 at H1's point (other session) |",
           "|---|" + "---|" * len(arms) + "---|"]
    for key, label, digits, m6 in rows:
        out.append(f"| {label} | " + " | ".join(cell((v.get(key) for v in pa.get(a, [])), digits) for a in arms)
                   + f" | {m6} |")
    m = means(pa, "process_cpu_s")
    out += ["", f"Ct − C, process CPU, from the means: {fmt(m6r.diff(m, 'Ct', 'C'))} s (M6's at H1's point: "
            f"{M6_CT_C}); its paired differences and their interval are in the paired differences below. The stage's "
            "wall time (t3 − t2c) is the arm's own and includes the two clock reads around each rknn_run."]
    return out + [""] + probe_section(cs)


def read_probe(path):
    """(header lines, columns, rows) of the probe's table, read generically: "# key: value" lines, a header row and
    rows."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    head = [x[2:] for x in lines if x.startswith("# ")]
    table = list(csv.reader(x for x in lines if not x.startswith("# ") and x.strip()))
    if not table:
        raise ValueError(f"{path} has no table")
    return head, table[0], table[1:]


def probe_section(cs):
    out = ["### The probe of the output bindings (§3)", "",
           "Outside the pipeline: one context that runs alone, not the pipeline, and no hypothesis rests on it (§3). "
           "Its table as the probe wrote it (`<session>/probe/probe.csv`)."]
    found = False
    for c in cs.values():
        for d in c["sessions"]:
            path = d / "probe" / "probe.csv"
            if not path.exists():
                continue
            found = True
            try:
                head, columns, rows = read_probe(path)
            except (OSError, ValueError, csv.Error) as e:
                out += ["", f"`{d.name}/probe/probe.csv` cannot be read: {text(e)}"]
                continue
            out += ["", f"`{d.name}/probe/probe.csv`" + (f" ({text('; '.join(head))})" if head else "") + ":", "",
                    "| " + " | ".join(text(x) for x in columns) + " |", "|---" * len(columns) + "|"]
            out += ["| " + " | ".join(text(x) for x in r) + " |" for r in rows]
    return out + ([] if found else ["", "No session has the probe's table yet."])


def paired_section(cs):
    out = ["## Paired differences (§6)", "",
           "Over the repetitions with a run that counts of both arms: each repetition's difference, their mean and a "
           "descriptive 95% t interval (df = pairs − 1; none with fewer than 2 pairs). " + LATENCY_NOTE, "",
           "| Sweep | Difference | Metric | r1 | r2 | r3 | Mean | 95% t interval |",
           "|---|---|---|---|---|---|---|---|"]
    for sweep, c in cs.items():
        for a, b in PAIRS[sweep]:
            if a not in c["arms"] or b not in c["arms"]:
                absent = ", ".join(x for x in (a, b) if x not in c["arms"])
                out.append(f"| `{sweep}` | {a} − {b} | – | not run: {absent} | | | | |")
                continue
            for key, label, unit in PAIRED_METRICS:
                out.append(paired_row(f"`{sweep}`", f"{a} − {b}", f"{label} ({unit})", c["counting"], a, b, key,
                                      unit=f" {unit}"))
    return out


def descriptive_section(cs):
    out = ["## Every arm, descriptively (§6)", "",
           "Mean ± sample sd (n) over the runs that count. " + LATENCY_NOTE + " " + SLOT_NOTE
           + " Arrival phases at the condition's sampling period.", ""]
    for sweep, c in cs.items():
        arms, pa = c["arms"], c["per_arm"]
        out += [f"### {sweep_title(sweep)}", "", "| Metric | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
        for key, label, digits in m6r.DESCRIPTIVE:  # its "Threads, Decoder" row is D
            out.append(f"| {label} | " + " | ".join(cell((v.get(key) for v in pa[a]), digits) for a in arms) + " |")
        out.append("| CV of process CPU | " + " | ".join(
            pct(m3r.cv([float(v["process_cpu_s"]) for v in pa[a]])).lstrip("+") for a in arms) + " |")
        out.append("| Slot in the session, r1 / r2 / r3 | " + " | ".join(
            " / ".join(text(c["counting"][(r, a)]["seq"]) if c["counting"].get((r, a)) else "–"
                       for r in range(1, REPETITIONS + 1)) for a in arms) + " |")
        out.append("")
    return out


def milestones_section(cs, refs):
    out = ["## B and C next to the earlier milestones (§6)", "",
           "Across sessions, so descriptive: each value is of its own sessions and binary, read from their records. "
           "Latency is recomputed here from each run's frames.csv (PROTOCOL §7.3), so its last digit can differ from "
           "the published tables.", ""]
    for sweep, point in (("m7_main", "main"), ("m7_aux", "aux")):
        columns = list(refs.get(point, {}).items()) + [("M7", cs[sweep]["per_arm"])]
        out += [f"### {sweep_title(sweep)}", "", "| Value | " + " | ".join(k for k, _ in columns) + " |",
                "|---|" + "---|" * len(columns)]
        for arm in ("B", "C"):
            for key, label in (("process_cpu_s", "process CPU (s)"), ("latency_p50_ms", "latency p50 (ms)")):
                out.append(f"| {arm}, {label} | " + " | ".join(cell(v.get(key) for v in runs.get(arm, []))
                                                             for _, runs in columns) + " |")
        out.append("| (B − C) / B, process CPU | " + " | ".join(
            fmt(m6r.ratio(m6r.diff(means({a: runs.get(a, []) for a in ("B", "C")}, "process_cpu_s"), "B", "C"),
                          mean_of(runs.get("B", []), "process_cpu_s")), 4) for _, runs in columns) + " |")
        out.append("")
    return out


def missing_section(cs):
    out = ["## Runs whose snapshots or counters are missing (§4)", "",
           "A snapshot that is missing, incomplete or cannot be parsed leaves the run valid and its snapshot values "
           "missing (§4). Every attempt of the sessions that can count.", "",
           "| Run | Arm | Counts | Snapshots | Counters | cpu0's counter |", "|---|---|---|---|---|---|"]
    n = len(out)
    for c in cs.values():
        for e in c["every"]:
            v = e["values"]
            if not v:
                continue
            snap, ctr, cpu0 = v.get("snap_problem"), v.get("counters_problem"), v.get("cpu0_problem")
            if snap or ctr or cpu0:
                ctr = ctr or ("ok" if e["variant"] in COUNTED else "not a counted arm")
                out.append(f"| `{v['run']}` | {e['variant']} | {'yes' if e['counts'] else 'no'} | "
                           f"{text(snap or 'ok')} | {text(ctr)} | {text(cpu0 or 'ok')} |")
    if len(out) == n:
        out.append("| none | | | | | |")
    out += ["", "Threads of one snapshot only, per run that counts (other tasks and bench_main's own; §1):", "",
            "| Run | Arm | In the first only (contribution unknown) | In the second only (run time counted, s) |",
            "|---|---|---|---|"]
    for c in cs.values():
        for a in c["arms"]:
            for v in c["per_arm"][a]:
                s = v.get("snap")
                if not s:
                    continue
                first = s["only_first"] + s["own"]["only_first"]
                second = s["only_second"] + s["own"]["only_second"]
                if first or second:
                    out.append(f"| `{v['run']}` | {a} | {text(', '.join(t['comm'] for t in first) or 'none')} | "
                               + text(", ".join(f"{t['comm']} {fmt(Fraction(t['run_ns'], 10**9))}" for t in second)
                                      or "none") + " |")
    return out


def every_run_section(cs):
    out = ["## Every run (§6)", "",
           "Every attempt of the sessions that can count, valid or not, in session order. " + SLOT_NOTE + " Order: its "
           "place in its repetition's order (§4). Counts: whether it is a run that counts, and why not.", ""]
    for sweep, c in cs.items():
        out += [f"### {sweep_title(sweep)}", ""] + m6r.run_table(c["every"]) + [""]
    return out


def superseded_section(cs):
    out = ["## Superseded runs (§5, §9)", ""]
    old = [c for c in cs.values() if c["superseded"]]
    if not old:
        return out + ["No session is superseded (stream_session.M7_SUPERSEDED)."]
    for c in old:
        out += [f"### {sweep_title(c['sweep'])}", "", "Superseded by an amendment (stream_session.M7_SUPERSEDED): "
                + ", ".join(f"`{d.name}`" for d in c["superseded"]) + ". Superseded runs stay under their records and "
                "are reported (§5); none of them counts.", ""] + m6r.run_table(c["superseded_runs"]) + [""]
    return out


def sessions_section(cs):
    out = ["## Sessions", "", "| Session | Sweep | Attempts | Status | Ambient (°C) | Idle baseline | Idle pair | "
           "Probe | Superseded |", "|---|---|---|---|---|---|---|---|---|"]
    for sweep, c in cs.items():
        attempts_in = {}
        for e in c["every"] + c["superseded_runs"]:
            attempts_in[e["session"]] = attempts_in.get(e["session"], 0) + 1
        for d in sorted(c["sessions"] + c["superseded"], key=lambda x: x.name):
            result = d / "result.csv"
            header, rows = ss.summary_values(result) if result.exists() else ({}, {})
            out.append(f"| `{d.name}` | `{sweep}` | {attempts_in.get(d.name, 0)} | "
                       f"{text(rows.get('session_status', 'no result.csv'))} | {text(header.get('ambient_c', ''))} | "
                       f"{'yes' if (d / 'idle_baseline.csv').exists() else 'no'} | "
                       f"{text(rows.get('idle_snapshot') or ('yes' if (d / 'idle_snapshot').is_dir() else 'no'))} | "
                       f"{text(rows.get('probe') or ('yes' if (d / 'probe' / 'probe.csv').exists() else 'no'))} | "
                       f"{'yes' if d in c['superseded'] else 'no'} |")
    if len(out) == 4:
        out.append("| none | | | | | | | | |")
    not_run_text = "; ".join(f"{a} ({not_run_why(a)})" for a in not_run()) or "none"
    out += ["", f"Arms not run (§5): {not_run_text}."]
    for sweep, c in cs.items():
        out.append(f"`{sweep}`: arms run {', '.join(c['arms'])}; repetitions complete {len(c['complete'])} of "
                   f"{REPETITIONS}.")
    return out


def counting_section(cs):
    out = ["## Runs that count (§4)", ""]
    for sweep, c in cs.items():
        out += [f"### {sweep_title(sweep)}", ""] + m6r.counting_section(c)[2:] + [""]
    return out


def report(sessions, smoke=False, refs=None, cpu0_ok=False):
    """The Markdown tables of M7 from its sessions, {sweep: [session directories]} (§2, §3, §6). refs: references(),
    read when None. cpu0_ok: whether §8 step 3's check of cpu0's counter passed."""
    cs = {sweep: collect(sessions.get(sweep, []), sweep) for sweep in SWEEPS}
    refs = references() if refs is None else refs
    out = ["# M7 tables", "",
           "Generated by `tools/m7_report.py` from the run records of M7's two sweeps "
           "([prereg/M7.md](../../prereg/M7.md)); nothing here is edited by hand. Every value is computed from the "
           "records' values and rounded only here; process CPU and system CPU are per window, the snapshots' values "
           "over their own span.", ""]
    for section in (sessions_section(cs), counting_section(cs), hypotheses_section(cs, smoke), cells_section(cs),
                    decoder_section(cs), kernel_section(cs, KERNEL_PAIRS, cpu0_ok), rknn_section(cs),
                    paired_section(cs), descriptive_section(cs), milestones_section(cs, refs), missing_section(cs),
                    every_run_section(cs), superseded_section(cs)):
        out += section + [""]
    return "\n".join(out).rstrip("\n") + "\n"


# ---------------------------------------------------------------- the sessions

def m7_sessions(root=ROOT):
    """{sweep: every protocol session of it, results/m7/<UTC>_<sweep>, by name}, superseded ones included."""
    base = root / "results" / KIND
    return {sweep: sorted(d for d in base.glob(f"*_{sweep}") if d.is_dir()
                          and re.fullmatch(ss.SESSION_NAME + sweep, d.name)) for sweep in SWEEPS}


def session_dirs(sessions):
    """({sweep: [sessions]}, smoke) that --session DIR ... reads: a smoke test (<UTC>_<sweep>_smoke) alone; a protocol
    session with the earlier sessions of its sweep, since a sweep spans sessions (PROTOCOL §9.3)."""
    out, smoke = {sweep: [] for sweep in SWEEPS}, False
    for session in map(Path, sessions):
        if not session.is_dir():
            raise ValueError(f"{session} is not a directory")
        for sweep in SWEEPS:
            if re.fullmatch(ss.SESSION_NAME + sweep + "_smoke", session.name):
                out[sweep], smoke = [session], True
                break
            if re.fullmatch(ss.SESSION_NAME + sweep, session.name):
                out[sweep] = sorted(d for d in session.parent.glob(f"*_{sweep}") if d.is_dir()
                                    and d.name <= session.name and re.fullmatch(ss.SESSION_NAME + sweep, d.name))
                break
        else:
            raise ValueError(f"{session} is not an m7_main or m7_aux session")
    return out, smoke


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--session", action="append", default=[],
                    help="an m7_main or m7_aux session (read with the earlier ones of its sweep), or a smoke test "
                         "alone; may be given once per sweep")
    ap.add_argument("--cpu0-check", choices=("passed", "failed"), default="failed",
                    help="the outcome of §8 step 3's check of cpu0's counter (default failed: no value is reported)")
    ap.add_argument("--out", help="write the Markdown tables there")
    a = ap.parse_args(argv)
    try:
        sessions, smoke = session_dirs(a.session) if a.session else (m7_sessions(), False)
        out = report(sessions, smoke, cpu0_ok=a.cpu0_check == "passed")
    except (OSError, ValueError, KeyError, ss.Stop) as e:
        print(f"m7_report: {e}", file=sys.stderr)
        return 2
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(out)
    else:
        if hasattr(sys.stdout, "reconfigure"):  # the tables' signs (−, ±, ×) on a console of another code page
            sys.stdout.reconfigure(encoding="utf-8")
        print(out, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
