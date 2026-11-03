#!/usr/bin/env python3
"""M1's tables and verdicts from the comparison runs (PROTOCOL §7.8, §9.3, §12; HYPOTHESES).

Reads the protocol sessions of tools/stream_session.py (results/comparison/<UTC>_main, _aux, _h3 and
results/reference/<UTC>_reference). For every measurement point it takes the runs that count: of each
repetition, the runs of the session that completed it (docs/notes/comparison.md); a run there is valid
or invalid x2. Then, with the method of docs/notes/analysis.md:
  - per point and metric, the mean and the sample standard deviation over the valid runs (§7.8); a
    coefficient of variation above 10% for a hypothesis metric is flagged (§9.3);
  - the verdicts of H1, H2 and H3, spec-literal and practical (HYPOTHESES);
  - the points that are not complete: fewer than 3 valid runs without invalid x2 records for the rest.

Usage, from the repository: python3 tools/m1_report.py [--check] [--out FILE]
  --check   only the completeness check: exit 0 if every point of the sessions found is complete
  --out     write the report there (Markdown) instead of printing it
"""
import argparse
import csv
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stream_session as ss  # noqa: E402

H1_POINT, H2_POINT = ("main", 8), ("aux", 5)  # HYPOTHESES H1, H2
H1_THRESHOLD, H2_RATIO, H2_DROP_PP = 0.25, 0.9, 0.01
CV_FLAG = 0.10  # §9.3
HYPOTHESIS_METRICS = ("process_cpu_s", "latency_p99_ms")  # H1, H2
METRICS = [  # (key, label), in the tables' order
    ("process_cpu_s", "process CPU (s)"),
    ("process_cpu_a76_s", "process CPU, A76 (s)"),
    ("process_cpu_a55_s", "process CPU, A55 (s)"),
    ("system_cpu_total_s", "system CPU (s)"),
    ("system_cpu_idle_sub_s", "system CPU minus idle (s)"),
    ("latency_p50_ms", "p50 latency (ms)"),
    ("latency_p99_ms", "p99 latency (ms)"),
    ("inference_fps_min", "lowest channel inference rate (fps)"),
    ("drop_rate", "drop rate"),
    ("decoder_drops", "decoder drops"),
    ("drift_max_s", "largest drift (s)"),
    ("temp_max_mc", "highest temperature (m°C)"),
]


# ---------------------------------------------------------------- the runs that count

def counted_runs(session_dirs, sweep):
    """{(channels, repetition, variant): run} for the repetitions some session completed. A run is a
    dict with status ("valid" or "invalid x2"), session, name (its last attempt) and reasons."""
    records = ss.load_records(session_dirs, (sweep,))
    variants = ss.SWEEPS[sweep][2]
    reasons = {}
    for d in session_dirs:
        path = Path(d) / "runs.tsv"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                for row in csv.DictReader(f, delimiter="\t"):
                    reasons[(Path(d).name, row["name"])] = row["reasons"]
    by_rep = {}
    for r in records:
        by_rep.setdefault((r["channels"], r["repetition"]), []).append(r)
    out = {}
    for (channels, rep), recs in by_rep.items():
        for session in dict.fromkeys(r["session"] for r in recs):
            mine = [r for r in recs if r["session"] == session]
            last = {v: [r for r in mine if r["variant"] == v][-1] for v in variants
                    if any(r["variant"] == v for r in mine)}
            if len(last) == len(variants) and all(r["valid"] or r["attempt"] == 2 for r in last.values()):
                for v, r in last.items():
                    out[(channels, rep, v)] = {"status": "valid" if r["valid"] else "invalid x2",
                                               "session": session, "name": r["name"],
                                               "reasons": reasons.get((session, r["name"]), "")}
                break
    return out


# ---------------------------------------------------------------- one run

def p99(latencies_ms):
    """§7.3: numpy.percentile with its default (linear) interpolation."""
    return float(np.percentile(latencies_ms, 99)) if len(latencies_ms) else math.nan


def run_metrics(run_dir, idle):
    """The metrics of one valid run. idle: the session's idle baseline (rows of idle_baseline.csv)."""
    header, rows = ss.read_csv(run_dir / "summary.csv")
    s = {r["key"]: r["value"] for r in rows}
    channels = ss.read_csv(run_dir / "channels.csv")[1]
    frames = ss.read_csv(run_dir / "frames.csv")[1]
    warmup_ns, window_ns = int(header["warmup_s"]) * 10**9, int(s["window_s"]) * 10**9
    lat = [(int(f["t4_ns"]) - int(f["t0_ns"])) / 1e6 for f in frames
           if warmup_ns <= int(f["t4_ns"]) < warmup_ns + window_ns]
    m = {k: float(s[k]) for k in ("process_cpu_s", "process_cpu_a76_s", "process_cpu_a55_s", "system_cpu_total_s",
                                  "latency_p50_ms", "drop_rate", "decoder_drops", "drift_max_s", "temp_max_mc")}
    m["latency_p99_ms"] = p99(lat)
    m["latency_p99_bench_ms"] = float(s["latency_p99_ms"])
    m["inference_fps_min"] = min(float(c["inference_fps"]) for c in channels)
    m["system_cpu_idle_sub_s"] = m["system_cpu_total_s"] - idle_share(idle, float(s["window_s"]))
    return m


def idle_share(idle, window_s):
    """The idle baseline's system CPU over a window of window_s (§7.2): its total, scaled from the
    baseline's own length to the run's."""
    return float(idle["system_cpu_total_s"]) * window_s / float(idle["window_s"])


def read_idle(session_dir):
    rows = ss.read_csv(Path(session_dir) / "idle_baseline.csv")[1]
    return {r["key"]: r["value"] for r in rows}


# ---------------------------------------------------------------- statistics and verdicts

def stats(values):
    """(n, mean, sample sd, coefficient of variation); sd and cv are nan below 2 values."""
    v = [x for x in values if not math.isnan(x)]
    n = len(v)
    if n == 0:
        return 0, math.nan, math.nan, math.nan
    mean = sum(v) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in v) / (n - 1)) if n > 1 else math.nan
    return n, mean, sd, (sd / mean if n > 1 and mean else math.nan)


def h1(b, c):
    """HYPOTHESES H1 at the main condition, 8 channels. b, c: the valid runs' metrics."""
    out = {"n_B": len(b), "n_C": len(c)}
    if not b or not c:
        return dict(out, spec="indeterminate", practical="indeterminate", reason="no valid run")
    mb, mc = stats([r["process_cpu_s"] for r in b])[1], stats([r["process_cpu_s"] for r in c])[1]
    saving = (mb - mc) / mb
    out.update(B=mb, C=mc, saving=saving, flagged=len(b) < 3 or len(c) < 3)
    out["spec"] = "adopted" if saving >= H1_THRESHOLD else "rejected"
    slow = [r for r in b + c if r["inference_fps_min"] < ss.SUSTAIN_FPS]
    if out["flagged"]:
        out["practical"], out["reason"] = "indeterminate", "fewer than 3 valid runs"
    elif slow:
        out["practical"], out["reason"] = "indeterminate", f"{len(slow)} valid runs with a channel below 4.75 fps"
    else:
        out["practical"] = out["spec"]
    sb, sc = stats([r["system_cpu_idle_sub_s"] for r in b])[1], stats([r["system_cpu_idle_sub_s"] for r in c])[1]
    out.update(system_B=sb, system_C=sc, mixed=(mb - mc) * (sb - sc) < 0)
    return out


def h2(b, c):
    """HYPOTHESES H2 at the auxiliary condition, 5 channels. b, c: {repetition: valid run's metrics}."""
    out = {"n_B": len(b), "n_C": len(c)}
    if not b or not c:
        return dict(out, spec="indeterminate", practical="indeterminate", reason="no valid run")
    mb = stats([r["latency_p99_ms"] for r in b.values()])[1]
    mc = stats([r["latency_p99_ms"] for r in c.values()])[1]
    db = stats([r["drop_rate"] for r in b.values()])[1]
    dc = stats([r["drop_rate"] for r in c.values()])[1]
    out.update(B=mb, C=mc, drop_B=db, drop_C=dc, flagged=len(b) < 3 or len(c) < 3)
    out["spec"] = "adopted" if mc < mb else "rejected"
    if out["flagged"]:
        out["practical"], out["reason"] = "indeterminate", "fewer than 3 valid runs"
    elif all(c[r]["latency_p99_ms"] <= H2_RATIO * b[r]["latency_p99_ms"] for r in (1, 2, 3)) and dc <= db + H2_DROP_PP:
        out["practical"] = "adopted"
    elif mc >= mb:
        out["practical"] = "rejected"
    else:
        out["practical"], out["reason"] = "indeterminate", "neither adopted nor rejected by the practical rule"
    return out


def h3(n):
    """HYPOTHESES H3. n: {variant: confirmed count}, or None if the search is not done."""
    if n is None:
        return {"spec": "not done", "practical": "not done"}
    out = {"n_B": n["B"], "n_C": n["C"], "spec": "adopted" if n["C"] >= n["B"] else "rejected"}
    if n["B"] >= ss.LIMIT - 1 and n["C"] >= ss.LIMIT - 1:
        out["practical"], out["reason"] = "indeterminate", "ceiling reached: both at least L - 1"
    else:
        out["practical"] = out["spec"]
    return out


# ---------------------------------------------------------------- the report

def collect(root):
    """{sweep: {point: {variant: {"valid": {rep: metrics}, "invalid_x2": [run]}}}}, missing points, h3 n."""
    data, missing = {}, []
    for sweep in ("main", "aux", "reference"):
        sessions = sorted(d for d in (root / "results" / ss.SWEEPS[sweep][3]).glob(f"*_{sweep}")
                          if d.is_dir() and ss.re.fullmatch(ss.SESSION_NAME + sweep, d.name))
        if not sessions:
            continue
        idle = {d.name: read_idle(d) for d in sessions if (d / "idle_baseline.csv").exists()}
        runs = counted_runs(sessions, sweep)
        counts, _, variants, _ = ss.SWEEPS[sweep]
        data[sweep] = {}
        for c in counts:
            for v in variants:
                point = data[sweep].setdefault(c, {}).setdefault(v, {"valid": {}, "invalid_x2": []})
                for rep in range(1, ss.REPETITIONS + 1):
                    run = runs.get((c, rep, v))
                    if run is None:
                        missing.append(f"{sweep} {c} ch {v} r{rep}")
                    elif run["status"] == "valid":
                        d = root / "results" / ss.SWEEPS[sweep][3] / run["session"] / run["name"]
                        point["valid"][rep] = run_metrics(d, idle[run["session"]])
                    else:
                        point["invalid_x2"].append(dict(run, repetition=rep))
    h3_sessions = sorted(d for d in (root / "results" / "comparison").glob("*_h3")
                         if d.is_dir() and ss.re.fullmatch(ss.SESSION_NAME + "h3", d.name))
    n = None
    if h3_sessions:
        _, nxt, n, _, _ = ss.h3_replay(ss.h3_trials(ss.load_records(h3_sessions, ss.H3_PHASES)))
    return data, missing, n


def fmt(x, digits=3):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{digits}f}"


def report(data, missing, n):
    lines = ["# M1 results", ""]
    lines += ["## Completeness", ""]
    lines += ([f"Not complete: {', '.join(missing)}."] if missing else ["Every point found is complete."]) + [""]

    main8 = data.get("main", {}).get(8, {})
    v1 = h1([*main8.get("B", {}).get("valid", {}).values()], [*main8.get("C", {}).get("valid", {}).values()]) \
        if main8 else None
    aux5 = data.get("aux", {}).get(5, {})
    v2 = h2(aux5.get("B", {}).get("valid", {}), aux5.get("C", {}).get("valid", {})) if aux5 else None
    v3 = h3(n)
    lines += ["## Verdicts", "", "| Hypothesis | Spec-literal | Practical | Numbers |", "|---|---|---|---|"]
    if v1:
        lines.append(f"| H1, CPU at 8 channels | {v1['spec']}{' (flagged)' if v1.get('flagged') else ''} | "
                     f"{v1['practical']} | B {fmt(v1.get('B'))} s, C {fmt(v1.get('C'))} s, saving "
                     f"{fmt(v1.get('saving', math.nan) * 100, 1)}%{'; mixed (system CPU)' if v1.get('mixed') else ''} |")
    if v2:
        lines.append(f"| H2, p99 at 5 channels, 30 fps | {v2['spec']}{' (flagged)' if v2.get('flagged') else ''} | "
                     f"{v2['practical']} | B {fmt(v2.get('B'), 1)} ms, C {fmt(v2.get('C'), 1)} ms; drop rate B "
                     f"{fmt(v2.get('drop_B'), 4)}, C {fmt(v2.get('drop_C'), 4)} |")
    lines.append(f"| H3, maximum channels | {v3['spec']} | {v3['practical']} | "
                 + (f"n_B {v3['n_B']}, n_C {v3['n_C']} |" if "n_B" in v3 else "– |"))
    lines += ["", "The H2 auxiliary point is 5 channels (A7), so H2's secondary verdicts are the ones above.", ""]
    for v, label in ((v1, "H1"), (v2, "H2"), (v3, "H3")):
        if v and v.get("reason"):
            lines.append(f"- {label}: {v['reason']}.")
    lines.append("")

    flags, invalid = [], []
    for sweep, points in data.items():
        lines += [f"## {sweep}", ""]
        variants = ss.SWEEPS[sweep][2]
        lines += ["| Channels | Metric | " + " | ".join(f"{v}: mean ± sd (n)" for v in variants) + " |",
                  "|---|---|" + "---|" * len(variants)]
        for c in sorted(points):
            for key, label in METRICS:
                cells = []
                for v in variants:
                    k, mean, sd, cv = stats([r[key] for r in points[c][v]["valid"].values()])
                    cells.append(f"{fmt(mean)} ± {fmt(sd)} ({k})")
                    if key in HYPOTHESIS_METRICS and not math.isnan(cv) and cv > CV_FLAG:
                        flags.append(f"{sweep} {c} ch {v}: {label} CV {cv * 100:.1f}%")
                lines.append(f"| {c} | {label} | " + " | ".join(cells) + " |")
            for v in variants:
                invalid += [f"{sweep} {c} ch {v} r{r['repetition']}: {r['session']}/{r['name']}: {r['reasons']}"
                            for r in points[c][v]["invalid_x2"]]
        lines.append("")
    lines += ["## Flags", ""]
    lines += [f"- {f}" for f in flags] or ["No hypothesis metric has a coefficient of variation above 10%."]
    lines += ["", "## Invalid x2", ""]
    lines += [f"- {i}" for i in invalid] or ["None."]
    return "\n".join(lines) + "\n"


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    data, missing, n = collect(root)
    if args.check:
        print("\n".join(missing) if missing else "complete")
        return 1 if missing else 0
    text = report(data, missing, n)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
