#!/usr/bin/env python3
"""prereg/M5.md §2 and §6 from M5's records: every run's values, the runs that count, the two hypotheses'
verdicts, the headline values and the paired differences.

  m5_summary.py SESSION...            the runs of these sessions, one row each (a check session too)
  m5_summary.py --sweep [SESSION...]  also §2's verdicts and §6's values, from the sweep's sessions
                                      (default: every lib/measure/results/<UTC>_m5)

It needs numpy: §3 computes the latency percentiles with numpy.percentile and its default, and the board,
where m5_session.py runs, has none. A service run's other values are recomputed here from its stored
files by lib/measure/m5_metrics.py, the functions the session used, and compared with what the session
wrote.

What each column is:
  process CPU       P, P4: Δ(utime + stime) of the service's process between the two timed reads (§3).
                    B, C: bench_main's process_cpu_s; "less metrics thread" subtracts its process_cpu_a55_s.
  system CPU        both measures over the window minus the session's idle baseline scaled to the window's
                    length (M2 §3.2 for /proc/schedstat, PROTOCOL §7.2 for /proc/stat), total and per cluster.
  latency           p50 and p99 in ms. B, C: bench_main's own. P, P4: out_ns - capture_ns.
  inference rate    the lowest channel's, in fps.

The runs that count (§4): per repetition and arm, the valid run of the session that completed the
repetition, as tools/stream_session.py's bookkeeping gives them.
"""
import argparse
import csv
import statistics
import sys
from pathlib import Path

import numpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(HERE))
import stream_session as ss  # noqa: E402
import m5_metrics as mm  # noqa: E402

ARMS = ("B", "C", "P", "P4")
PHASE, CHANNELS = "m5", 8
THRESHOLD = 0.25  # §2: H1's
CV_FLAG = 0.10  # PROTOCOL §9.3, on process CPU (M5 §4)
T975 = {1: 12.706, 2: 4.303, 3: 3.182}  # the t quantile of a two-sided 95% interval, by degrees of freedom
CLUSTERS = ("0-3", "4-5", "6-7")
# (key, title, unit, digits) of the values reported per run and compared in pairs (§6).
METRICS = (("process_cpu_s", "Process CPU", "s", 3),
           ("schedstat_total_s", "System CPU, /proc/schedstat, less idle", "s", 3),
           ("system_total_s", "System CPU, /proc/stat, less idle", "s", 3),
           ("latency_p50_ms", "Latency p50", "ms", 3), ("latency_p99_ms", "Latency p99", "ms", 3),
           ("inference_fps_min", "Inference rate, lowest channel", "fps", 3),
           ("drop_rate", "Drop rate", "", 5), ("temp_max_mc", "Highest temperature", "m°C", 0),
           ("rss_max_kb", "VmRSS, highest", "kB", 0))


def keyed(path):
    """(header, {key: value}) of a "# key: value" + key,value file."""
    header, rows = ss.read_csv(path)
    return header, {r["key"]: r["value"] for r in rows}


def number(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def baseline(session):
    """({key: seconds per second of idle}) of a session's idle_baseline.csv."""
    _, rows = keyed(Path(session) / "idle_baseline.csv")
    w = float(rows["window_s"])
    return {k: float(v) / w for k, v in rows.items() if k.endswith("_s") and k != "window_s"}


def less_idle(values, prefix, base, window_s):
    """{total and per cluster: the measure over the window minus the idle baseline scaled to it}."""
    out = {}
    for name in ("total",) + CLUSTERS:
        key = f"{prefix}_cpu_total_s" if name == "total" else f"{prefix}_cpu{name}_s"
        if key in values and key in base and number(values[key]) is not None:
            out[f"{prefix}_{name}_s"] = float(values[key]) - base[key] * window_s
    return out


def run_values(run_dir):
    """{key: value} of one run, any arm: the header's fields, validity, and §6's values as floats."""
    run_dir = Path(run_dir)
    header, rows = keyed(run_dir / "m5_run.csv")
    arm = header["arm"]
    out = {"run": run_dir.name, "session": run_dir.parent.name, "arm": arm, "repetition": header["repetition"],
           "attempt": header["attempt"], "run_kind": header["run_kind"], "valid": rows.get("valid") == "1",
           "reasons": rows.get("reasons", ""), "start_time_utc": header["start_time_utc"]}
    try:
        base = baseline(run_dir.parent)
    except (OSError, KeyError, ValueError):
        base = {}
    if arm in ("B", "C"):
        if not (run_dir / "summary.csv").exists():
            return out
        _, s = keyed(run_dir / "summary.csv")
        window_s = float(s["window_s"])
        ch = ss.read_csv(run_dir / "channels.csv")[1]
        out.update(process_cpu_s=float(s["process_cpu_s"]),
                   process_cpu_less_metrics_s=float(s["process_cpu_s"]) - float(s["process_cpu_a55_s"]),
                   latency_p50_ms=number(s.get("latency_p50_ms")), latency_p99_ms=number(s.get("latency_p99_ms")),
                   inference_fps_min=min(float(r["inference_fps"]) for r in ch), drop_rate=number(s.get("drop_rate")),
                   temp_max_mc=number(s.get("temp_max_mc")), rss_max_kb=number(s.get("rss_window_max_kb")),
                   decoder_drops=number(s.get("decoder_drops")))
        out.update(less_idle(s, "schedstat", base, window_s))
        out.update(less_idle(s, "system", base, window_s))
        for k in ("cpu_check", "cpu_check_difference_s"):
            if k in rows:
                out[k] = rows[k]
        return out
    if not (run_dir / "marks.csv").exists() or "process_cpu_s" not in rows:
        return out
    marks = mm.read_keyed_csv(run_dir / "marks.csv")
    t_w, t_e = int(marks["t_w_ns"]), int(marks["t_e_ns"])
    window_s = (t_e - t_w) / 1e9
    frames, _, _ = mm.parse_lines((run_dir / "lines.jsonl").read_text(encoding="utf-8", errors="replace"))
    lat = numpy.array(mm.latencies_ns(frames, t_w, t_e), dtype=float) / 1e6
    out.update(process_cpu_s=float(rows["process_cpu_s"]),
               latency_p50_ms=float(numpy.percentile(lat, 50)) if len(lat) else None,
               latency_p99_ms=float(numpy.percentile(lat, 99)) if len(lat) else None,
               inference_fps_min=float(rows["inference_fps_min"]), drop_rate=number(rows["drop_rate"]),
               temp_max_mc=number(rows["temp_max_mc"]), rss_max_kb=number(rows["rss_window_max_kb"]),
               decoder_drops=number(rows["decoder_drops"]), a55_share=number(rows["a55_share"]),
               tool_cpu_s=float(rows["tool_cpu_s"]), first_lines_s=float(rows["first_lines_s"]),
               service_dropped=number(rows.get("service_dropped")), service_errors=number(rows.get("service_errors")),
               field_problems="; ".join(mm.field_problems(frames)) or "none",
               affinity=rows.get("affinity_w", "") + " / " + rows.get("affinity_e", ""))
    out.update(less_idle(rows, "schedstat", base, window_s))
    out.update(less_idle(rows, "system", base, window_s))
    # The session's own rows against a fresh evaluation of the stored files.
    fixed = {d: 0 for d in mm.DOMAINS}  # rule 1 is the session's; only the values are compared here
    again = dict(mm.evaluate(run_dir, arm, fixed)[0])
    differ = [k for k, v in again.items() if k in rows and rows[k] != v]
    out["recomputed"] = "same" if not differ else "differs: " + " ".join(differ)
    return out


def session_runs(session):
    """The runs of a session, in the order of runs.tsv."""
    with open(Path(session) / "runs.tsv", encoding="utf-8") as f:
        names = [r["name"] for r in csv.DictReader(f, delimiter="\t") if r["phase"] == PHASE]
    return [run_values(Path(session) / n) for n in names if (Path(session) / n / "m5_run.csv").exists()]


def current_sessions(session_dirs):
    """The sessions that ran the package and the tool of the latest one (M5 §5, §9: after a change to
    either, the sweep is rerun whole, and the superseded runs stay where they are)."""
    def key(d):
        h = ss.read_header(Path(d) / "m5_session.csv") if (Path(d) / "m5_session.csv").exists() else {}
        return h.get("tool_commit"), h.get("package_sha256")
    dirs = sorted(session_dirs, key=lambda d: Path(d).name)
    return [d for d in dirs if key(d) == key(dirs[-1])] if dirs else []


def counting_runs(session_dirs):
    """{(repetition, arm): run directory or None} for the complete repetitions (§4): the valid run of the
    session that completed the repetition, None for an invalid ×2 run."""
    records = ss.load_records(session_dirs, (PHASE,))
    complete, _ = ss.sweep_state(records, ARMS)
    by_name = {Path(d).name: Path(d) for d in session_dirs}
    out = {}
    for _, rep in sorted(complete):
        recs = [r for r in records if r["repetition"] == rep]
        for s in dict.fromkeys(r["session"] for r in recs):
            mine = [r for r in recs if r["session"] == s]
            if all(any(r["variant"] == a and (r["valid"] or r["attempt"] == 2) for r in mine) for a in ARMS):
                for a in ARMS:
                    valid = [r for r in mine if r["variant"] == a and r["valid"]]
                    out[(rep, a)] = by_name[s] / valid[-1]["name"] if valid else None
                break
    return out


def mean(values):
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None


def verdicts(counting, arm, secondary="schedstat_total_s"):
    """§2's two verdicts for an arm (P for M5-H1, P4 for M5-H2). counting: {(repetition, arm): values or
    None}. Returns a dict with the means, the relative difference, both verdicts and the flags."""
    b = [v for (r, a), v in sorted(counting.items()) if a == "B" and v is not None]
    x = [v for (r, a), v in sorted(counting.items()) if a == arm and v is not None]
    out = {"arm": arm, "runs_B": len(b), f"runs_{arm}": len(x), "flags": []}
    if not b or not x:
        return {**out, "spec_literal": "indeterminate", "practical": "indeterminate",
                "flags": ["B or the arm has no run that counts"]}
    mb, mx = mean(r["process_cpu_s"] for r in b), mean(r["process_cpu_s"] for r in x)
    rel = (mb - mx) / mb
    out.update(mean_B=mb, mean_arm=mx, relative=rel, spec_literal="adopted" if rel >= THRESHOLD else "rejected")
    out["practical"] = out["spec_literal"]
    if len(b) < 3 or len(x) < 3:
        out["flags"].append("fewer than 3 runs that count")
        out["practical"] = "indeterminate"
    slow = [f"{r['session']}/{r['run']}" for r in b + x if r["inference_fps_min"] < mm.MIN_FPS]
    if slow:
        out["flags"].append("a channel below 4.75 fps in " + ", ".join(slow))
        out["practical"] = "indeterminate"
    sb, sx = mean(r.get(secondary) for r in b), mean(r.get(secondary) for r in x)
    if sb is None or sx is None:
        out["flags"].append("the secondary metric is missing")
    else:
        out.update(secondary_B=sb, secondary_arm=sx)
        if (sb - sx) * (mb - mx) < 0:
            out["flags"].append("mixed")
    cvs = {name: statistics.stdev(v) / statistics.fmean(v) for name, v in
           (("B", [r["process_cpu_s"] for r in b]), (arm, [r["process_cpu_s"] for r in x])) if len(v) > 1}
    out["cv"] = cvs
    out["flags"] += [f"CV of {name} above 10%" for name, c in cvs.items() if c > CV_FLAG]  # PROTOCOL §9.3
    return out


def headline(counting, arm):
    """§6: ((arm - C) / C, (B - arm) / (B - C)) from the means of the runs that count, None where one is
    missing."""
    m = {a: mean(v["process_cpu_s"] for (r, a2), v in counting.items() if a2 == a and v is not None)
         for a in ("B", "C", arm)}
    if None in m.values() or m["C"] == 0 or m["B"] == m["C"]:
        return None, None
    return (m[arm] - m["C"]) / m["C"], (m["B"] - m[arm]) / (m["B"] - m["C"])


def paired(counting, a, b, key, relative=False):
    """§6: the differences a_r - b_r (or (a_r - b_r) / b_r) over the repetitions that have both runs, their
    mean and the half-width of a descriptive 95% t interval (None with fewer than 2 pairs)."""
    d = []
    for rep in sorted({r for r, _ in counting}):
        va, vb = counting.get((rep, a)), counting.get((rep, b))
        if va and vb and va.get(key) is not None and vb.get(key) is not None:
            d.append((va[key] - vb[key]) / vb[key] if relative else va[key] - vb[key])
    if not d:
        return d, None, None
    half = T975[len(d) - 1] * statistics.stdev(d) / len(d) ** 0.5 if len(d) >= 2 else None
    return d, statistics.fmean(d), half


def fmt(x, digits=3):
    return "–" if x is None else f"{x:.{digits}f}"


def runs_table(runs):
    cols = ["session", "run", "arm", "repetition", "attempt", "valid"] + [k for k, *_ in METRICS] + \
           ["process_cpu_less_metrics_s", "a55_share", "tool_cpu_s", "schedstat_0-3_s", "schedstat_4-5_s",
            "schedstat_6-7_s", "decoder_drops", "recomputed", "cpu_check", "cpu_check_difference_s", "reasons"]
    lines = ["\t".join(cols)]
    for r in runs:
        lines.append("\t".join(fmt(r.get(c), 5 if c == "drop_rate" else 3) if isinstance(r.get(c), float)
                               else str(r.get(c, "")) for c in cols))
    return "\n".join(lines)


def sweep_text(all_dirs):
    session_dirs = current_sessions(all_dirs)
    counting_dirs = counting_runs(session_dirs)
    counting = {k: (run_values(d) if d is not None else None) for k, d in counting_dirs.items()}
    out = [f"sessions: {' '.join(Path(d).name for d in session_dirs) or 'none'}",
           "superseded sessions (another tool commit or package): "
           + (" ".join(Path(d).name for d in all_dirs if d not in session_dirs) or "none"),
           f"complete repetitions: {sorted({r for r, _ in counting})}",
           "runs that count: " + ", ".join(f"r{r} {a}: {d.parent.name + '/' + d.name if d else 'invalid x2'}"
                                           for (r, a), d in sorted(counting_dirs.items()))]
    for name, arm in (("M5-H1", "P"), ("M5-H2", "P4")):
        v = verdicts(counting, arm)
        out.append(f"{name} ({arm}): B {fmt(v.get('mean_B'))} s, {arm} {fmt(v.get('mean_arm'))} s, "
                   f"(B - {arm}) / B = {fmt(v.get('relative'), 4)}; spec-literal {v['spec_literal']}, practical "
                   f"{v['practical']}; secondary B {fmt(v.get('secondary_B'))} s, {arm} {fmt(v.get('secondary_arm'))} s"
                   f"; CV {', '.join(f'{k} {c:.4f}' for k, c in v.get('cv', {}).items()) or '–'}"
                   f"; flags: {'; '.join(v['flags']) or 'none'}")
        more, kept = headline(counting, arm)
        out.append(f"  headline: ({arm} - C) / C = {fmt(more, 4)}; (B - {arm}) / (B - C) = {fmt(kept, 4)}")
    for a, b in (("P", "C"), ("P4", "C"), ("P4", "P")):
        for key, title, unit, digits in METRICS:
            d, m, half = paired(counting, a, b, key)
            out.append(f"  {a} - {b}, {title}: " + " ".join(fmt(x, digits) for x in d) + f"; mean {fmt(m, digits)}"
                       + (f" ± {fmt(half, digits)}" if half is not None else "") + f" {unit}")
            if key in ("process_cpu_s", "schedstat_total_s", "system_total_s") and b == "C":
                d, m, _ = paired(counting, a, b, key, relative=True)
                out.append(f"  ({a} - C) / C per repetition, {title}: " + " ".join(fmt(x, 4) for x in d)
                           + f"; mean {fmt(m, 4)}")
    return "\n".join(out)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sessions", nargs="*", type=Path)
    ap.add_argument("--sweep", action="store_true", help="§2's verdicts and §6's values from the sweep's sessions")
    args = ap.parse_args(argv)
    sessions = args.sessions or (sorted(d for d in (ROOT / "lib" / "measure" / "results").glob("*_m5") if d.is_dir())
                                 if args.sweep else [])
    if not sessions:
        ap.error("no session")
    print(runs_table([r for s in sessions for r in session_runs(s)]))
    if args.sweep:
        print(sweep_text(sessions))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
