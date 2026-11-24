#!/usr/bin/env python3
"""M6's tables and verdicts (prereg/M6.md §2, §3, §4 and §6) from the run records of M6's sweep.

The runs that count (§4) are, per repetition and arm, the valid run of the session that completed the
repetition (an invalid ×2 run counts as missing), as tools/stream_session.py's sweep_state completes them
(tools/m3_report.py's counting_runs), over the arms the sweep runs: stream_session.m6_arms(), M6_ARMS without
M6_NOT_RUN. Everything is computed from the records' values and rounded only where it is printed:
  - the three hypotheses of §2, on process CPU (PROTOCOL §7.1), from the means of the runs that count:
      M6-H1  (B − Rc) / (B − C) ≥ 0.90   mixed if the same ratio on /proc/schedstat is on the other side of 0.90
      M6-H2  (Cpf − Cp) / Cp ≥ 0.20      mixed if Cpf − Cp has the other sign on /proc/schedstat
      M6-H3  (B − By) / B ≥ 0.10         mixed if B − By has the other sign on /proc/schedstat
    each with its spec-literal and practical verdict and its flags. The practical verdict is the spec-literal
    one, except indeterminate when a run that counts of an arm the hypothesis names has a channel below 4.75 fps
    (PROTOCOL §7.4), when the mean drop rates (§7.5) of two arms it names differ by more than 1 percentage point
    either way, or when an arm it names has fewer than 3 runs that count (the spec-literal verdict then uses the
    runs that exist, flagged). With no run that counts of a named arm, or a named arm not run, both verdicts are
    indeterminate. Flagged too: a CV of process CPU above 10% for every named arm, and above 5% for B and By (§4);
  - §6's tables: the four cells of question 1 in seconds and as shares of B − C, also by thread group (the
    groups of tools/cpu_breakdown.py); the CPU path (question 2); the package's excess (question 3), with Cp and
    Cpf next to M5's P4 and P, across sessions; Ct's rknn_run values (§3); the paired differences of §6 with a
    descriptive 95% t interval (df = pairs − 1, none below 2 pairs); every arm's values; B and C next to M1's,
    M3's and M5's at this point; and every run's values, valid or not, with its position in its session.
System CPU from /proc/schedstat (M2 §3.2) and from /proc/stat (PROTOCOL §7.2) is the window's sum minus the
session's idle baseline (idle_baseline.csv) scaled to the window's length. Latency p50 and p99 (§7.3) and the
stage times (tools/m1_stages.py's stages) are linear percentiles over the frames whose t4 is in the window; the
arrival phases are tools/m1_phases.py's spread and drift. The inference rate of a channel is its frames
processed in the window over the window's length; the drop rate is Q1 drops over sampled frames (summary.csv).

Standard library only, so that it also runs on the board: the thread groups, stages, phases and percentiles of
the tools named above are restated here, since those tools import numpy or matplotlib.
Usage, from the repository:
  python3 tools/m6_report.py [--out FILE]                  every M6 session, results/m6/<UTC>_m6, as Markdown
  python3 tools/m6_report.py --session DIR [--out FILE]    an m6 session with the earlier sessions of its sweep,
                                                           or a smoke test (results/check/<UTC>_m6_smoke) alone
Exit 0, or 2 when a session cannot be read.
"""
import argparse
import csv
import itertools
import math
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m3_report as m3r  # noqa: E402  (counting_runs, run_values, mean_sd, cv, fmt, pct, M1_SESSION, m3_sessions)
import stream_session as ss  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PHASE, CHANNELS = "m6", 8  # M6 §1: H1's point, 8 channels in the main condition
M5_SESSION = Path("lib/measure/results/20261123T001204Z_m5")  # M5's sweep (docs/report/M5_summary.txt)
M5_ARMS = ("B", "C", "P", "P4")
REPETITIONS = 3  # M6 §4: 8 arms × 3 repetitions
MIN_FPS = 4.75  # M6 §2, PROTOCOL §7.4
DROP_PP = 0.01  # M6 §2: mean drop rates more than 1 percentage point apart, in either direction
FLOAT_TOLERANCE = 1e-9  # tools/m1_report.py's, for the drop-rate bound
CV_LIMITS = {"B": (0.10, 0.05), "By": (0.10, 0.05)}  # M6 §4; every other arm a hypothesis names: 0.10
T975 = {1: 12.706204736174698, 2: 4.302652729911275, 3: 3.182446305284263}  # t, 0.975, by df (tools/m2_report.py)
CLUSTERS = ("0-3", "4-5", "6-7")
HYPOTHESES = {  # M6 §2: the arms each names, its threshold, its value
    "M6-H1": (("B", "Rc", "C"), 0.90, "(B − Rc) / (B − C)"),
    "M6-H2": (("Cp", "Cpf"), 0.20, "(Cpf − Cp) / Cp"),
    "M6-H3": (("B", "By"), 0.10, "(B − By) / B"),
}
NOTES = {  # M6 §2, "What the hypotheses can and cannot tell", reported with each hypothesis (§6)
    "M6-H1": "Close to what M1.1 already showed: it tests the one cell M1.1 could not fill, and an adoption is the "
             "expected result. Its ratio compares bundles (the copy, its sync pair and a cached destination for "
             "RGA3), not a pure copy (§1).",
    "M6-H2": "It tests the scheduler on this board, under this load and on this kernel, as M5-H1 did; Cpf's value "
             "can move with anything else that runs on the board. An adoption says that the placement alone adds at "
             "least 20% to bench_main's process CPU; how much of P's 48.5% it reproduces is (Cpf − Cp) / Cp next "
             "to 0.485, across sessions, without a verdict. Cpf is not the package (§7).",
    "M6-H3": "It is about one library called one way, with B's filter and B's thread count. A rejection does not "
             "show that no CPU path is much faster than B's; an adoption does not show that By is the fastest. B "
             "stays the registered reference; By is a further arm next to it.",
}
CELLS = (("B − Rc", "B", "Rc"), ("Bd − C", "Bd", "C"), ("B − Bd", "B", "Bd"), ("Rc − C", "Rc", "C"))  # §6, question 1
PAIRS = (("B", "Rc"), ("Bd", "C"), ("B", "Bd"), ("Rc", "C"), ("B", "By"), ("By", "C"), ("Cp", "C"), ("Cpf", "Cp"),
         ("Ct", "C"), ("B", "C"))  # §6, the paired differences
PAIRED_METRICS = (("process_cpu_s", "Process CPU", "s"),
                  ("schedstat_total_s", "System CPU, /proc/schedstat, less idle", "s"),
                  ("latency_p50_ms", "Latency p50", "ms"), ("latency_p99_ms", "Latency p99", "ms"))
GROUPS = (("dec", "Decoder (dec-*, mpp_*)"), ("npu", "NPU workers (npu-*)"), ("pre", "Preprocessing (pre-*)"),
          ("rest", "Rest (bench_main, metrics)"))  # tools/cpu_breakdown.py's thread groups
GROUP_KEYS = tuple((f"threads_{g}_s", label.split(" (")[0]) for g, label in GROUPS) + (
    ("threads_sum_s", "Threads' sum"), ("threads_remainder_s", "Remainder"))
STAGES = (("q1_wait", "Q1 wait", "t1_ns", "t0_ns"), ("prepare", "prepare", "t2a_ns", "t1_ns"),
          ("q2_wait", "Q2 wait", "t2b_ns", "t2a_ns"), ("bind", "bind", "t2c_ns", "t2b_ns"),
          ("run", "run", "t3_ns", "t2c_ns"), ("post", "post", "t4_ns", "t3_ns"))  # tools/m1_stages.py's
SYSTEM_ROWS = tuple((f"{p}_{c}_s", f"System CPU, {t}, {'total' if c == 'total' else 'cpu' + c}, less idle (s)", 3)
                    for p, t in (("schedstat", "/proc/schedstat"), ("system", "/proc/stat"))
                    for c in ("total",) + CLUSTERS)
DESCRIPTIVE = (  # (key, label, digits): §6, "Descriptively, for every arm"
    (("process_cpu_s", "Process CPU (s)", 3), ("process_cpu_a76_s", "Process CPU, A76 (s)", 3),
     ("process_cpu_a55_s", "Process CPU, A55 (s)", 3),
     ("process_cpu_unattributed_s", "Process CPU, unattributed (s)", 3))
    + SYSTEM_ROWS
    + (("latency_p50_ms", "Latency p50 (ms)", 3), ("latency_p99_ms", "Latency p99 (ms)", 3))
    + tuple((f"stage_{k}_{p}_ms", f"Stage {name}, {p} (ms)", 3) for k, name, _, _ in STAGES for p in ("p50", "p99"))
    + (("arrival_spread_ms", "Arrival phases, spread (ms)", 1), ("arrival_drift_ms", "Arrival phases, drift (ms)", 1),
       ("inference_fps_min", "Inference rate, lowest channel (fps)", 3),
       ("inference_fps_mean", "Inference rate, channel mean (fps)", 3), ("drop_rate", "Drop rate", 5),
       ("temp_max_mc", "Highest temperature (m°C)", 0), ("rss_window_max_kb", "VmRSS, highest (kB)", 0))
    + tuple((k, f"Threads, {label} (s)", 3) for k, label in GROUP_KEYS))
CT_ROWS = (("rknn_run_cpu_s", "Thread CPU across rknn_run, NPU workers (s)", 3),
           ("rknn_run_cpu_share", "Its share of process CPU", 4),
           ("rknn_run_cpu_mean_ms", "Thread CPU of one rknn_run, mean (ms)", 4),
           ("rknn_run_wall_mean_ms", "Wall time of the same stage, t3 − t2c, mean (ms)", 4),
           ("rknn_run_calls", "Calls", 0))  # M6 §3
LATENCY_NOTE = ("Latency in an unstaggered run depends on the channels' arrival phases, which are not controlled at "
                "this point (M3 §2, M6 §3 and §6).")
CORES_NOTE = "Cpf's and P's process CPU count cores of two kinds (M5 §2, M6 §1 and §6)."


# ---------------------------------------------------------------- one run

def group(comm):
    """The thread group of a thread's name, as tools/cpu_breakdown.py's group (the M2 report's groups)."""
    if comm.startswith("npu-"):
        return "npu"
    if comm.startswith(("dec-", "mpp_")):
        return "dec"
    if comm.startswith("pre-"):
        return "pre"
    return "rest"


def percentile(values, q):
    """PROTOCOL §7.3: numpy.percentile's default (linear) interpolation; None without values."""
    if not values:
        return None
    v = sorted(values)
    pos = q / 100 * (len(v) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def phase(t0_ns, period_ns):
    """tools/m1_phases.py's phase: the circular mean of t0_ns modulo period_ns, in ns, in [0, period_ns)."""
    a = [2 * math.pi * (float(t) % period_ns) / period_ns for t in t0_ns]
    mean = math.atan2(math.fsum(map(math.sin, a)) / len(a), math.fsum(map(math.cos, a)) / len(a))
    return mean % (2 * math.pi) / (2 * math.pi) * period_ns % period_ns


def split(values, parts):
    """numpy.array_split's parts of a list: the first len % parts of them one longer."""
    size, longer = divmod(len(values), parts)
    out, i = [], 0
    for p in range(parts):
        n = size + (p < longer)
        out.append(values[i:i + n])
        i += n
    return out


def drift(t0_ns, period_ns):
    """tools/m1_phases.py's drift: the largest change of the phase of t0_ns from the first sixth, in time, to any
    later sixth, in ns."""
    sixths = [s for s in split(sorted(float(t) for t in t0_ns), 6) if s]
    first = phase(sixths[0], period_ns)
    d = [abs(phase(s, period_ns) - first) for s in sixths[1:]]
    return max((min(x, period_ns - x) for x in d), default=0.0)


def spread(phases, period):
    """tools/m1_phases.py's spread: the shortest arc of a circle of length period that holds every phase."""
    p = sorted(phases)
    gaps = [b - a for a, b in zip(p, p[1:])] + [p[0] + period - p[-1]]
    return period - max(gaps)


def read_idle(session_dir):
    """{key: value} of a session's idle_baseline.csv (PROTOCOL §7.2, M2 §3.2); {} without one."""
    path = Path(session_dir) / "idle_baseline.csv"
    return {r["key"]: r["value"] for r in ss.read_csv(path)[1]} if path.exists() else {}


def less_idle(values, idle, window_s):
    """{schedstat_<total or cluster>_s, system_<total or cluster>_s}: the window's sum of each system CPU measure
    minus the idle baseline scaled from its own length to the window's (PROTOCOL §7.2, M2 §3.2); None where the
    run or the baseline lacks it. values: {summary key: float}."""
    out = {}
    for prefix in ("schedstat", "system"):
        for name in ("total",) + CLUSTERS:
            key = f"{prefix}_cpu_total_s" if name == "total" else f"{prefix}_cpu{name}_s"
            have = values.get(key) is not None and key in idle and "window_s" in idle
            out[f"{prefix}_{name}_s"] = (values[key] - float(idle[key]) * window_s / float(idle["window_s"])
                                         if have else None)
    return out


def run_values(run_dir, idle):
    """{key: value} of one run of bench_main, from its summary.csv, channels.csv, threads.csv and frames.csv: the
    values §3 and §6 report, unrounded, None where a record lacks one. idle: its session's idle baseline."""
    run_dir = Path(run_dir)
    s = m3r.run_values(run_dir)
    warmup_ns = int(ss.read_header(run_dir / "summary.csv")["warmup_s"]) * 10**9
    window_s = s["window_s"]
    out = {k: s.get(k) for k in ("process_cpu_s", "process_cpu_a76_s", "process_cpu_a55_s",
                                 "process_cpu_unattributed_s", "temp_max_mc", "rss_window_max_kb")}
    out["drop_rate"] = s["q1_drops_in_window"] / s["sampled_in_window"] if s.get("sampled_in_window") else None
    out.update(less_idle(s, idle, window_s))
    fps = [int(r["processed_in_window"]) / window_s for r in ss.read_csv(run_dir / "channels.csv")[1]]
    out["inference_fps_min"] = min(fps) if fps else None
    out["inference_fps_mean"] = statistics.fmean(fps) if fps else None
    groups = dict.fromkeys((g for g, _ in GROUPS), 0.0)
    for t in ss.read_csv(run_dir / "threads.csv")[1]:
        groups[group(t["comm"])] += float(t["cpu_s_in_window"])
    out.update({f"threads_{g}_s": v for g, v in groups.items()})
    out["threads_sum_s"] = sum(groups.values())
    out["threads_remainder_s"] = out["process_cpu_s"] - out["threads_sum_s"]
    end_ns = warmup_ns + round(window_s * 10**9)
    frames = [f for f in ss.read_csv(run_dir / "frames.csv")[1] if warmup_ns <= int(f["t4_ns"]) < end_ns]
    latency = [(int(f["t4_ns"]) - int(f["t0_ns"])) / 1e6 for f in frames]
    out["latency_p50_ms"], out["latency_p99_ms"] = percentile(latency, 50), percentile(latency, 99)
    for key, _, end, start in STAGES:
        v = [(int(f[end]) - int(f[start])) / 1e6 for f in frames]
        out[f"stage_{key}_p50_ms"], out[f"stage_{key}_p99_ms"] = percentile(v, 50), percentile(v, 99)
    t0 = {}
    for f in frames:
        t0.setdefault(f["channel"], []).append(int(f["t0_ns"]))
    period_ns = 1e9 / ss.FPS["main"]
    if t0 and len(t0) == len(fps):  # tools/m1_phases.py: every channel has frames in the window
        out["arrival_spread_ms"] = spread([phase(v, period_ns) for v in t0.values()], period_ns) / 1e6
        out["arrival_drift_ms"] = max(drift(v, period_ns) for v in t0.values()) / 1e6
    else:
        out["arrival_spread_ms"] = out["arrival_drift_ms"] = None
    if frames and "rknn_cpu_ns" in frames[0]:  # M6 §3, arm Ct: the frames of report.cpp's rknn_run_* rows
        cpu_ns = sum(int(f["rknn_cpu_ns"]) for f in frames)
        wall_ns = sum(int(f["t3_ns"]) - int(f["t2c_ns"]) for f in frames)
        out.update(rknn_run_calls=len(frames), rknn_run_cpu_s=cpu_ns / 1e9,
                   rknn_run_cpu_share=cpu_ns / 1e9 / out["process_cpu_s"] if out["process_cpu_s"] else None,
                   rknn_run_cpu_mean_ms=cpu_ns / 1e6 / len(frames), rknn_run_wall_mean_ms=wall_ns / 1e6 / len(frames))
    return out


def service_values(run_dir, idle):
    """M5's P or P4, as lib/measure/m5_summary.py reads them: the service's process CPU (m5_run.csv) and both system
    CPU measures over the window of marks.csv, minus the idle baseline."""
    _, rows = ss.summary_values(Path(run_dir) / "m5_run.csv")
    _, marks = ss.summary_values(Path(run_dir) / "marks.csv")
    window_s = (int(marks["t_e_ns"]) - int(marks["t_w_ns"])) / 1e9
    values = {k: float(v) for k, v in rows.items()
              if k.startswith(("schedstat_cpu", "system_cpu")) and k.endswith("_s")}
    return {"process_cpu_s": float(rows["process_cpu_s"]), **less_idle(values, idle, window_s)}


# ---------------------------------------------------------------- the sweep's runs

def runs_rows(session_dirs):
    """{(session, name): row} of the sessions' runs.tsv, and of started.tsv for an attempt without a runs.tsv row."""
    out = {}
    for d in session_dirs:
        for name in ("started.tsv", "runs.tsv"):  # runs.tsv's row replaces started.tsv's
            path = Path(d) / name
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    out.update(((Path(d).name, r["name"]), r) for r in csv.DictReader(f, delimiter="\t"))
    return out


def order_position(repetition, arm):
    """The arm's place in its repetition's order (M6 §4), 1-based, without the arms that are not run; None outside
    the plan."""
    order = {r: o for _, r, o in ss.sweep_plan(PHASE)}.get(repetition, ())
    return order.index(arm) + 1 if arm in order else None


def collect(session_dirs):
    """The sweep's runs: {"sessions", "arms", "complete": [repetitions], "counting": {(repetition, arm): values or
    None}, "per_arm": {arm: [values of its runs that count]}, "every": [every attempt with its values]}. A run's
    values carry its "run" (session/name) and "seq" (its position in its session)."""
    sessions = [Path(d) for d in session_dirs]
    arms = ss.m6_arms()
    counting = m3r.counting_runs(sessions, PHASE, CHANNELS, arms)
    counted = {(d.parent.name, d.name) for d in counting.values() if d is not None}
    idle = {d.name: read_idle(d) for d in sessions}
    by_name = {d.name: d for d in sessions}
    rows = runs_rows(sessions)
    every, values_of = [], {}
    for r in ss.load_records(sessions, (PHASE,)):
        key = (r["session"], r["name"])
        row = rows.get(key, {})
        try:
            values = run_values(by_name[r["session"]] / r["name"], idle[r["session"]])
        except (OSError, ValueError, KeyError, ZeroDivisionError, ss.Stop):
            if key in counted:  # a run that counts must be readable
                raise
            values = {}
        values.update(run=f"{r['session']}/{r['name']}", seq=row.get("seq", ""))
        values_of[key] = values
        every.append(dict(r, values=values, counts=key in counted, seq=row.get("seq", ""),
                          position=order_position(r["repetition"], r["variant"]),
                          reasons="the session ended during the run" if r["lost"] else row.get("reasons", "")))
    by_rep = {k: (values_of[(d.parent.name, d.name)] if d is not None else None) for k, d in counting.items()}
    per_arm = {a: [v for (_, arm), v in sorted(by_rep.items()) if arm == a and v is not None] for a in arms}
    return {"sessions": sessions, "arms": arms, "complete": sorted({rep for rep, _ in counting}),
            "counting": by_rep, "per_arm": per_arm, "every": every}


# ---------------------------------------------------------------- the hypotheses

def value(name, m):
    """§2: a hypothesis's value from {arm: mean}, None where its denominator is 0."""
    if name == "M6-H1":
        num, den = m["B"] - m["Rc"], m["B"] - m["C"]
    elif name == "M6-H2":
        num, den = m["Cpf"] - m["Cp"], m["Cp"]
    else:
        num, den = m["B"] - m["By"], m["B"]
    return num / den if den else None


def is_mixed(name, m, s):
    """§2's "Mixed" from the process CPU means m and the /proc/schedstat means s: for M6-H1 the same ratio on the
    other side of 0.90, for M6-H2 and M6-H3 the difference with the other sign. None when M6-H1's ratio is
    undefined on either."""
    threshold = HYPOTHESES[name][1]
    if name == "M6-H1":
        a, b = value(name, m), value(name, s)
        return None if a is None or b is None else (a >= threshold) != (b >= threshold)
    x, y = ("Cpf", "Cp") if name == "M6-H2" else ("B", "By")
    return (m[x] - m[y]) * (s[x] - s[y]) < 0


def mean_of(runs, key):
    """The mean of key over runs where it has a value, None without one."""
    return m3r.mean_sd([v[key] for v in runs if v.get(key) is not None])[0]


def cv_flags(arms, runs):
    """§4's CV flag on process CPU (PROTOCOL §9.3): above 10% for every arm a hypothesis names, and above 5% for B
    and By."""
    out = []
    for a in arms:
        c = m3r.cv([v["process_cpu_s"] for v in runs.get(a, [])])
        out += [f"CV of {a}'s process CPU {100 * c:.2f}%, above {100 * limit:.0f}% (§4)"
                for limit in CV_LIMITS.get(a, (0.10,)) if c is not None and c > limit]
    return out


def hypothesis(name, runs):
    """§2's verdicts of one hypothesis. runs: {arm: [values of its runs that count]}. Returns a dict: the arms, their
    numbers of runs ("n") and process CPU means, the value, "spec" and "practical", the /proc/schedstat means and
    value, "mixed" (True, False, or None when it cannot be told) and the flags."""
    arms, threshold, formula = HYPOTHESES[name]
    n = {a: len(runs.get(a, [])) for a in arms}
    out = {"name": name, "arms": arms, "threshold": threshold, "formula": formula, "n": n, "means": {}, "value": None,
           "spec": "indeterminate", "practical": "indeterminate", "secondary": {}, "secondary_value": None,
           "mixed": None, "flags": []}
    flags = out["flags"]
    absent = [a for a in arms if a in ss.M6_NOT_RUN]
    if absent:  # §2, §5: a hypothesis that names an arm that is not run is indeterminate
        flags += [f"{a} is not run (§1, §5): both verdicts are indeterminate"
                  + ("; the equivalence results that stopped it are in the amendment of §8 step 4" if a == "By" else "")
                  for a in absent]
        return out
    flags += cv_flags(arms, runs)
    empty = [a for a in arms if not n[a]]
    if empty:
        flags.append(f"no run that counts of {', '.join(empty)}: both verdicts are indeterminate (§2)")
        return out
    m = out["means"] = {a: mean_of(runs[a], "process_cpu_s") for a in arms}
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
            if v.get("inference_fps_min") is None or v["inference_fps_min"] < MIN_FPS]
    if slow:
        flags.append(f"a channel below {MIN_FPS} fps in {', '.join(slow)} (§2)")
        out["practical"] = "indeterminate"
    drops = {a: mean_of(runs[a], "drop_rate") for a in arms}
    if None in drops.values():
        flags.append(f"no drop rate for {', '.join(a for a in arms if drops[a] is None)} (§2)")
        out["practical"] = "indeterminate"
    for a, b in itertools.combinations(arms, 2):
        if None not in (drops[a], drops[b]) and abs(drops[a] - drops[b]) > DROP_PP + FLOAT_TOLERANCE:
            flags.append(f"the mean drop rates of {a} and {b} differ by {100 * abs(drops[a] - drops[b]):.3f} "
                         "percentage points, more than 1 (§2)")
            out["practical"] = "indeterminate"
    s = {a: mean_of(runs[a], "schedstat_total_s") for a in arms}
    if None in s.values():
        flags.append(f"the secondary metric is missing for {', '.join(a for a in arms if s[a] is None)}: "
                     "no mixed label")
        return out
    out["secondary"], out["secondary_value"] = s, value(name, s)
    out["mixed"] = is_mixed(name, m, s)
    if out["mixed"] is None:
        flags.append(f"{formula} is undefined on /proc/schedstat: no mixed label")
    return out


# ---------------------------------------------------------------- §6's other values

def means(per_arm, key):
    """{arm: mean of key over its runs that count, or None}."""
    return {a: mean_of(runs, key) for a, runs in per_arm.items()}


def diff(m, a, b):
    """m[a] − m[b], None where either is missing."""
    return None if m.get(a) is None or m.get(b) is None else m[a] - m[b]


def ratio(x, y):
    """x / y, None where either is missing or y is 0."""
    return None if x is None or not y else x / y


def cells(m):
    """§6, question 1: [(label, value)] of the four cells and of (B − Bd) − (Rc − C), from {arm: mean}."""
    out = [(label, diff(m, a, b)) for label, a, b in CELLS]
    bundles = dict(out)
    out.append(("(B − Bd) − (Rc − C)", None if None in (bundles["B − Bd"], bundles["Rc − C"])
                else bundles["B − Bd"] - bundles["Rc − C"]))
    return out


def paired(by_rep, a, b, key):
    """§6: ({repetition: a_r − b_r}, their mean, the half-width of a descriptive 95% t interval) over the
    repetitions with a run that counts of both arms; df = pairs − 1, no interval below 2 pairs."""
    d = {}
    for rep in sorted({r for r, _ in by_rep}):
        va, vb = by_rep.get((rep, a)), by_rep.get((rep, b))
        if va and vb and va.get(key) is not None and vb.get(key) is not None:
            d[rep] = va[key] - vb[key]
    if not d:
        return d, None, None
    v = list(d.values())
    half = T975[len(v) - 1] * statistics.stdev(v) / math.sqrt(len(v)) if len(v) >= 2 else None
    return d, statistics.fmean(v), half


def counted_values(session_dirs, phase_name, arms):
    """{arm: [values of its runs that count]} of an earlier sweep at H1's point: run_values for bench_main's runs,
    service_values for M5's P and P4."""
    runs = m3r.counting_runs([Path(d) for d in session_dirs], phase_name, CHANNELS, arms)
    out = {a: [] for a in arms}
    for (_, arm), d in sorted(runs.items()):
        if d is not None:
            out[arm].append((service_values if arm in ("P", "P4") else run_values)(d, read_idle(d.parent)))
    return out


def references(root=ROOT):
    """§6: B and C at H1's point in M1 (sweep main), M3 (m3_before and m3_after) and M5, and M5's P and P4, read from
    their records: {label: {arm: [values]}}."""
    out = {"M1": counted_values([root / m3r.M1_SESSION], "main", ("B", "C"))}
    for sweep, dirs in m3r.m3_sessions(root / "results" / "m3").items():
        out[f"M3 {sweep}"] = counted_values(dirs, sweep, ("B", "C"))
    out["M5"] = counted_values([root / M5_SESSION], "m5", M5_ARMS)
    return out


# ---------------------------------------------------------------- Markdown

def fmt(x, digits=3):
    return m3r.fmt(x, digits)


def pct(x):
    return m3r.pct(x)


def cell(values, digits=3):
    """"mean ± sd (n)" of the values that exist, "–" without one."""
    mean, sd, n = m3r.mean_sd([x for x in values if x is not None])
    return "–" if mean is None else f"{fmt(mean, digits)} ± {fmt(sd, digits)} ({n})"


def text(x):
    """A table cell's text, its pipes escaped."""
    return str(x).replace("|", "\\|") or "–"


def status_line(c, smoke):
    if smoke:
        return ("**A smoke test:** its windows are not the protocol's, and it counts for nothing. The verdicts below "
                "are §2's arithmetic on its runs, not M6's outcome.")
    if len(c["complete"]) < REPETITIONS:
        return (f"**No outcome yet:** {len(c['complete'])} of {REPETITIONS} repetitions are complete. The verdicts "
                "below are §2's arithmetic on the runs that count so far.")
    return "The sweep is complete: the verdicts below are M6's (§2)."


def hypotheses_section(c, smoke):
    out = ["## Hypotheses (§2)", "", status_line(c, smoke), "",
           "| Hypothesis | Means, process CPU (s) | Value | Threshold | Spec-literal | Practical | "
           "Means, /proc/schedstat less idle (s) | Value on it | Mixed | Flags |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    notes = []
    for name in HYPOTHESES:
        h = hypothesis(name, c["per_arm"])
        m = ", ".join(f"{a} {fmt(h['means'][a])} ({h['n'][a]})" for a in h["arms"] if a in h["means"]) or "–"
        s = ", ".join(f"{a} {fmt(h['secondary'][a])}" for a in h["arms"] if a in h["secondary"]) or "–"
        mixed = "–" if h["mixed"] is None else "mixed" if h["mixed"] else "none"
        out.append(f"| {name}: {h['formula']} ≥ {h['threshold']:.2f} | {m} | {fmt(h['value'], 4)} | "
                   f"{h['threshold']:.2f} | {h['spec']} | {h['practical']} | {s} | {fmt(h['secondary_value'], 4)} | "
                   f"{mixed} | {text('; '.join(h['flags']) or 'none')} |")
        notes.append(f"- **{name}:** {NOTES[name]}")
    return out + [""] + notes + ["", "Mean of each arm over its runs that count (n). Mixed: by §2's rule, from "
                                 "/proc/schedstat's system CPU less the idle baseline (M2 §3.2)."]


def cells_section(c):
    pa = c["per_arm"]
    m = means(pa, "process_cpu_s")
    bc = diff(m, "B", "C")
    out = ["## The four cells (question 1, §6)", "",
           f"From the means of the runs that count. B − C = {fmt(bc)} s; each value also as a share of it.", "",
           "| Difference | Process CPU (s) | Share of B − C |", "|---|---|---|"]
    out += [f"| {label} | {fmt(v)} | {pct(ratio(v, bc))} |" for label, v in cells(m)]
    out += ["", "(B − Bd) − (Rc − C) mixes any interaction with the two bundles' different cache maintenance (§1): "
            "per frame on the slot, B has no sync and Bd a START/END(WRITE) pair; Rc has a START/END(READ) pair "
            "and C none.", "",
            "By thread group (tools/cpu_breakdown.py's groups, from threads.csv, the threads alive at the window's "
            "end; the remainder is process CPU less the threads' sum), in s, with the share of process CPU's B − C:",
            "", "| Difference | " + " | ".join(label for _, label in GROUP_KEYS) + " |",
            "|---|" + "---|" * len(GROUP_KEYS)]
    by_group = {k: cells(means(pa, k)) for k, _ in GROUP_KEYS}
    for i, (label, _) in enumerate(cells(m)):
        out.append(f"| {label} | " + " | ".join(f"{fmt(by_group[k][i][1])} ({pct(ratio(by_group[k][i][1], bc))})"
                                                 for k, _ in GROUP_KEYS) + " |")
    return out


def cpu_path_section(c):
    pa = c["per_arm"]
    m = means(pa, "process_cpu_s")
    pre = means(pa, "threads_pre_s")
    return ["## The CPU path (question 2, §6)", "",
            f"- (B − By) / B = {fmt(ratio(diff(m, 'B', 'By'), m.get('B')), 4)}",
            f"- Preprocessing threads (pre-*): B {cell(v['threads_pre_s'] for v in pa.get('B', []))} s, "
            f"{pct(ratio(pre.get('B'), m.get('B'))).lstrip('+')} of its process CPU; By "
            f"{cell(v['threads_pre_s'] for v in pa.get('By', []))} s, "
            f"{pct(ratio(pre.get('By'), m.get('By'))).lstrip('+')} of its process CPU; "
            f"By − B {fmt(diff(pre, 'By', 'B'))} s",
            f"- C's saving against By, (By − C) / By = {fmt(ratio(diff(m, 'By', 'C'), m.get('By')), 4)}, next to "
            f"its saving against B, (B − C) / B = {fmt(ratio(diff(m, 'B', 'C'), m.get('B')), 4)}"]


def package_section(c, refs):
    pa = c["per_arm"]
    m = means(pa, "process_cpu_s")
    m5 = refs.get("M5", {})
    p, p4 = mean_of(m5.get("P", []), "process_cpu_s"), mean_of(m5.get("P4", []), "process_cpu_s")
    rows = (("process_cpu_s", "Process CPU (s)", 3), ("process_cpu_a76_s", "Process CPU, A76 (s), §7.1's split", 3),
            ("process_cpu_a55_s", "Process CPU, A55 (s), §7.1's split", 3),
            ("process_cpu_unattributed_s", "Process CPU, unattributed (s), §7.1's split", 3),
            *(r for r in SYSTEM_ROWS if r[0].startswith("schedstat")),
            *((k, f"Threads, {label} (s)", 3) for k, label in GROUP_KEYS))
    out = ["## The package's excess (question 3, §6)", "",
           f"- (Cp − C) / C = {fmt(ratio(diff(m, 'Cp', 'C'), m.get('C')), 4)}",
           f"- (Cpf − Cp) / Cp = {fmt(ratio(diff(m, 'Cpf', 'Cp'), m.get('Cp')), 4)}, next to M5's (P − P4) / P4 = "
           f"{fmt(ratio(None if None in (p, p4) else p - p4, p4), 4)} (§2: 0.485), across sessions, without a verdict",
           "", "Where Cpf's work ran (§3): /proc/schedstat per cluster, which is the whole system's, and its threads' "
           "CPU. PROTOCOL §7.1's split by affinity is reported as it is computed and means nothing for Cpf, whose "
           "pipeline threads are allowed cpu0–7.", "", "| Value | Cp | Cpf |", "|---|---|---|"]
    out += [f"| {label} | {cell((v.get(k) for v in pa.get('Cp', [])), d)} | "
            f"{cell((v.get(k) for v in pa.get('Cpf', [])), d)} |" for k, label, d in rows]
    out += ["", "**Across sessions** (M6's Cp and Cpf, M5's P4 and P from "
            f"`{M5_SESSION.as_posix()}`): descriptive, no verdict (§7).", "",
            "| Value | Cp (M6) | Cpf (M6) | P4 (M5) | P (M5) |", "|---|---|---|---|---|"]
    for k, label, d in rows[:1] + tuple(r for r in SYSTEM_ROWS if r[0].startswith("schedstat")):
        out.append(f"| {label} | " + " | ".join(cell((v.get(k) for v in runs), d) for runs in (
            pa.get("Cp", []), pa.get("Cpf", []), m5.get("P4", []), m5.get("P", []))) + " |")
    return out + ["", "P's and P4's /proc/schedstat values hold M5's measuring tool, about 1.45 s per window on cpu0; "
                  "Cp's and Cpf's hold only the metrics thread, about 0.1 s (§6). " + CORES_NOTE]


def rknn_section(c):
    pa = c["per_arm"]
    m = means(pa, "process_cpu_s")
    out = ["## rknn_run (Ct, §3; a description, no hypothesis)", "", "| Value | Ct |", "|---|---|"]
    out += [f"| {label} | {cell((v.get(k) for v in pa.get('Ct', [])), d)} |" for k, label, d in CT_ROWS]
    return out + ["", f"Ct − C, process CPU, from the means: {fmt(diff(m, 'Ct', 'C'))} s; its paired differences "
                  "and their interval are below."]


def paired_section(c):
    out = ["## Paired differences (§6)", "",
           "Over the repetitions with a run that counts of both arms: each repetition's difference, their mean and a "
           "descriptive 95% t interval (df = pairs − 1; none with fewer than 2 pairs). " + LATENCY_NOTE, "",
           "| Difference | Metric | r1 | r2 | r3 | Mean | 95% t interval |", "|---|---|---|---|---|---|---|"]
    for a, b in PAIRS:
        if a not in c["arms"] or b not in c["arms"]:
            out.append(f"| {a} − {b} | – | not run: {', '.join(x for x in (a, b) if x not in c['arms'])} | | | | |")
            continue
        for key, label, unit in PAIRED_METRICS:
            d, mean, half = paired(c["counting"], a, b, key)
            interval = "–" if half is None else f"{fmt(mean - half)} to {fmt(mean + half)} {unit}"
            out.append(f"| {a} − {b} | {label} ({unit}) | " + " | ".join(fmt(d.get(r)) for r in range(1, 4))
                       + f" | {fmt(mean)} | {interval} |")
    return out


def counting_section(c):
    arms = c["arms"]
    out = ["## Runs that count (§4)", "", "| Repetition | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
    for rep in range(1, REPETITIONS + 1):
        if rep not in c["complete"]:
            out.append(f"| r{rep} | " + " | ".join("not complete" for _ in arms) + " |")
            continue
        row = []
        for a in arms:
            v = c["counting"].get((rep, a))
            row.append("invalid ×2" if v is None else f"`{v['run']}`")
        out.append(f"| r{rep} | " + " | ".join(row) + " |")
    return out


def descriptive_section(c):
    arms, pa = c["arms"], c["per_arm"]
    out = ["## Every arm, descriptively (§6)", "",
           "Mean ± sample sd (n) over the runs that count. " + LATENCY_NOTE + " " + CORES_NOTE, "",
           "| Metric | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
    for key, label, digits in DESCRIPTIVE:
        out.append(f"| {label} | " + " | ".join(cell((v.get(key) for v in pa[a]), digits) for a in arms) + " |")
    out.append("| CV of process CPU | " + " | ".join(
        pct(m3r.cv([v["process_cpu_s"] for v in pa[a]])).lstrip("+") for a in arms) + " |")
    out.append("| Position in the session, r1 / r2 / r3 | " + " | ".join(
        " / ".join(text(c["counting"][(r, a)]["seq"]) if c["counting"].get((r, a)) else "–"
                   for r in range(1, REPETITIONS + 1)) for a in arms) + " |")
    return out


def milestones_section(c, refs):
    labels = [k for k in refs if k.startswith(("M1", "M3", "M5"))]
    columns = [(k, refs[k]) for k in labels] + [("M6", c["per_arm"])]
    out = ["## B and C at H1's point, next to M1, M3 and M5 (§6)", "",
           "Across sessions, so descriptive: each value is of its own sessions and binary.", "",
           "| Value | " + " | ".join(k for k, _ in columns) + " |", "|---|" + "---|" * len(columns)]
    for arm in ("B", "C"):
        for key, label in (("process_cpu_s", "process CPU (s)"), ("latency_p50_ms", "latency p50 (ms)")):
            out.append(f"| {arm}, {label} | " + " | ".join(cell(v.get(key) for v in runs.get(arm, []))
                                                         for _, runs in columns) + " |")
    out.append("| (B − C) / B, process CPU | " + " | ".join(
        fmt(ratio(diff(means({a: runs.get(a, []) for a in ('B', 'C')}, 'process_cpu_s'), 'B', 'C'),
                  mean_of(runs.get('B', []), 'process_cpu_s')), 4) for _, runs in columns) + " |")
    return out


def every_run_section(c):
    out = ["## Every run (§6)", "",
           "Every attempt of the sessions, valid or not, in session order. Seq: the run's position in its session; "
           "Order: its place in its repetition's order (§4).", "",
           "| Run | Arm | Repetition | Attempt | Seq | Order | Valid | Counts | Process CPU (s) | "
           "/proc/schedstat less idle (s) | /proc/stat less idle (s) | Latency p50 (ms) | Latency p99 (ms) | "
           "Lowest channel (fps) | Drop rate | Arrival spread (ms) | Highest temperature (m°C) | VmRSS (kB) | "
           "Reasons |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    columns = (("process_cpu_s", 3), ("schedstat_total_s", 3), ("system_total_s", 3), ("latency_p50_ms", 3),
               ("latency_p99_ms", 3), ("inference_fps_min", 3), ("drop_rate", 5), ("arrival_spread_ms", 1),
               ("temp_max_mc", 0), ("rss_window_max_kb", 0))
    for e in c["every"]:
        v = e["values"]
        out.append(f"| `{e['session']}/{e['name']}` | {e['variant']} | {e['repetition']} | {e['attempt']} | "
                   f"{text(e['seq'])} | {text(e['position'] or '')} | {'yes' if e['valid'] else 'no'} | "
                   f"{'yes' if e['counts'] else 'no'} | " + " | ".join(fmt(v.get(k), d) for k, d in columns)
                   + f" | {text(e['reasons'])} |")
    return out


def report(session_dirs, smoke=False, refs=None):
    """The Markdown tables of M6's sweep from its sessions (§2, §3, §6). refs: references(), read when None."""
    c = collect(session_dirs)
    refs = references() if refs is None else refs
    out = ["# M6 tables", "",
           "Generated by `tools/m6_report.py` from the run records of M6's sweep ([prereg/M6.md](../../prereg/M6.md)); "
           "nothing here is edited by hand. Every value is computed from the records' values and rounded only here; "
           "process CPU and system CPU are per window.", "", "## Sessions", "",
           "| Session | Attempts | Status | Idle baseline |", "|---|---|---|---|"]
    for d in c["sessions"]:
        result = d / "result.csv"
        status = ss.summary_values(result)[1].get("session_status", "–") if result.exists() else "no result.csv"
        out.append(f"| `{d.name}` | {sum(e['session'] == d.name for e in c['every'])} | {text(status)} | "
                   f"{'yes' if (d / 'idle_baseline.csv').exists() else 'no'} |")
    if not c["sessions"]:
        out.append("| none | | | |")
    out += ["", f"Arms run: {', '.join(c['arms'])}. Arms not run (§5): {', '.join(ss.M6_NOT_RUN) or 'none'}. "
            f"Repetitions complete: {len(c['complete'])} of {REPETITIONS}.", ""]
    for section in (counting_section(c), hypotheses_section(c, smoke), cells_section(c), cpu_path_section(c),
                    package_section(c, refs), rknn_section(c), paired_section(c), descriptive_section(c),
                    milestones_section(c, refs), every_run_section(c)):
        out += section + [""]
    return "\n".join(out).rstrip("\n") + "\n"


# ---------------------------------------------------------------- the sessions

def m6_sessions(root=ROOT):
    """The protocol sessions of M6's sweep, results/m6/<UTC>_m6, by name (UTC)."""
    return sorted(d for d in (root / "results" / ss.SWEEPS[PHASE][3]).glob(f"*_{PHASE}")
                  if d.is_dir() and re.fullmatch(ss.SESSION_NAME + PHASE, d.name))


def session_dirs(session):
    """(sessions, smoke) that --session DIR reads: a smoke test (<UTC>_m6_smoke) alone; a protocol session with the
    earlier sessions of its sweep, since a sweep spans sessions (PROTOCOL §9.3)."""
    session = Path(session)
    if not session.is_dir():
        raise ValueError(f"{session} is not a directory")
    if re.fullmatch(ss.SESSION_NAME + PHASE + "_smoke", session.name):
        return [session], True
    if not re.fullmatch(ss.SESSION_NAME + PHASE, session.name):
        raise ValueError(f"{session} is not an m6 session")
    return sorted(d for d in session.parent.glob(f"*_{PHASE}")
                  if d.is_dir() and d.name <= session.name and re.fullmatch(ss.SESSION_NAME + PHASE, d.name)), False


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--session", help="an m6 session (read with the earlier ones of its sweep), or a smoke test alone")
    ap.add_argument("--out", help="write the Markdown tables there")
    a = ap.parse_args(argv)
    try:
        sessions, smoke = session_dirs(a.session) if a.session else (m6_sessions(), False)
        out = report(sessions, smoke)
    except (OSError, ValueError, KeyError, ss.Stop) as e:
        print(f"m6_report: {e}", file=sys.stderr)
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
