#!/usr/bin/env python3
"""M1's tables and verdicts from the comparison runs (PROTOCOL §7.8, §9.3, §12; HYPOTHESES).

Reads the protocol sessions of tools/stream_session.py (results/comparison/<UTC>_main, _aux, _h3 and
results/reference/<UTC>_reference). For every measurement point it takes the runs that count: of each
repetition, the runs of the session that completed it (docs/notes/comparison.md); a run there is valid
or invalid x2. Then, with the method of docs/notes/analysis.md, it reports:
  - the conditions every result is stated with (HYPOTHESES, A1, A5, A6, A8);
  - completeness: every point of every sweep, and H3 (§9.3, §10);
  - the verdicts of H1, H2 and H3, spec-literal and practical (HYPOTHESES);
  - per point and metric, the mean and the sample standard deviation over the valid runs (§7.8); a
    coefficient of variation above 10% for a hypothesis metric is flagged (§9.3);
  - every invalid x2 record by variant, with the reasons of both attempts, H3's included;
  - the attempts that do not count (repeated, replaced, or cut short), with their reasons, and the
    runs with samples at a trip point (warnings, §9.3).

Usage, from the repository: python3 tools/m1_report.py [--check] [--out FILE]
  --check   only the completeness check: exit 0 if every point of every sweep is complete and H3 is done
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

H1_THRESHOLD, H2_RATIO, H2_DROP_PP = 0.25, 0.9, 0.01  # HYPOTHESES H1, H2
FLOAT_TOLERANCE = 1e-9  # for H2's drop-rate bound, compared on values written to 5 decimals
CV_FLAG = 0.10  # §9.3
HYPOTHESIS_METRICS = ("process_cpu_s", "latency_p99_ms")  # H1, H2
LOST = "the session ended during the run"
SYSTEM_KEYS = [f"system_cpu{c}_s" for c in range(8)] + ["system_cpu0-3_s", "system_cpu4-5_s", "system_cpu6-7_s",
                                                       "system_cpu_total_s"]
METRICS = (  # (key, label), in the tables' order: every metric of §7 that summary.csv and channels.csv give
    [("process_cpu_s", "process CPU (s)"), ("process_cpu_a76_s", "process CPU, A76 (s)"),
     ("process_cpu_a55_s", "process CPU, A55 (s)"), ("process_cpu_unattributed_s", "process CPU, unattributed (s)")]
    + [(k, k[:-2].replace("_", " ") + " (s)") for k in SYSTEM_KEYS]
    + [(k[:-2] + "_minus_idle_s", k[:-2].replace("_", " ") + " minus idle (s)") for k in SYSTEM_KEYS]
    + [("latency_p50_ms", "p50 latency (ms)"), ("latency_p99_ms", "p99 latency (ms)"),
       ("latency_p99_bench_ms", "p99 latency, bench_main's (ms)"), ("mpp_hold_p50_ms", "MPP hold p50 (ms)"),
       ("mpp_hold_p99_ms", "MPP hold p99 (ms)"), ("slot_wait_p99_ms", "slot wait p99 (ms)"),
       ("inference_fps_min", "inference rate, lowest channel (fps)"),
       ("inference_fps_mean", "inference rate, channel mean (fps)"),
       ("inference_fps_max", "inference rate, highest channel (fps)"), ("drop_rate", "drop rate"),
       ("decoder_drops", "decoder drops"), ("drift_max_s", "largest drift (s)"),
       ("rss_window_max_kb", "largest RSS (kB)"), ("temp_max_mc", "highest temperature (m°C)")])
CONDITIONS = [
    "The pipeline runs on the four A76 cores (cpu4-7); the metrics thread on cpu0 (PROTOCOL §8).",
    "The decoder's buffers are CPU-cached (AMENDMENTS A1, PROTOCOL §5.1).",
    "B and C are equivalent under A5's criteria on video 1 (A5), and on the held-out videos 2, 3 and 4 too: "
    "the check of A8 passed, pooled and per video.",
    "Every C result carries this label (A6): \"input path without CPU pixel copies; §11.4 perf criterion not met "
    "because of about 1,150 small copies (about 15 KB) per inference inside the NPU runtime, as in B\".",
    "Open question (A6): the NPU's native outputs are NC1HWC2 and both variants bind them as NCHW INT8, about "
    "1.2 MB per inference; binding the native layout fails, which suggests the runtime converts the outputs on "
    "the CPU, with code other than memcpy or memmove.",
]


# ---------------------------------------------------------------- the sessions and their attempts

def sessions_of(root, name):
    kind = ss.session_kind(name)
    return sorted(d for d in (root / "results" / kind).glob(f"*_{name}")
                  if d.is_dir() and ss.re.fullmatch(ss.SESSION_NAME + name, d.name))


def runs_rows(session_dirs):
    """{(session, name): runs.tsv row} of the sessions."""
    out = {}
    for d in session_dirs:
        path = Path(d) / "runs.tsv"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                for row in csv.DictReader(f, delimiter="\t"):
                    out[(Path(d).name, row["name"])] = row
    return out


def reason(record, rows):
    if record.get("lost"):
        return LOST
    return rows.get((record["session"], record["name"]), {}).get("reasons", "")


def counted_runs(session_dirs, sweep):
    """{(channels, repetition, variant): run} for the repetitions some session completed. A run is a
    dict with status ("valid" or "invalid x2"), session, name (its last attempt) and attempts: the
    [(session/name, reason)] of its attempts, the first one included for an invalid x2 run. Also
    returns the attempts that do not count, as [(session/name, valid, reason)]."""
    records = ss.load_records(session_dirs, (sweep,))
    rows = runs_rows(session_dirs)
    variants = ss.SWEEPS[sweep][2]
    by_rep = {}
    for r in records:
        by_rep.setdefault((r["channels"], r["repetition"]), []).append(r)
    out, used = {}, set()
    for (channels, rep), recs in by_rep.items():
        for session in dict.fromkeys(r["session"] for r in recs):
            mine = [r for r in recs if r["session"] == session]
            last = {v: [r for r in mine if r["variant"] == v][-1] for v in variants
                    if any(r["variant"] == v for r in mine)}
            if len(last) != len(variants) or not all(r["valid"] or r["attempt"] == 2 for r in last.values()):
                continue
            for v, r in last.items():
                attempts = [r]
                if r["attempt"] == 2 and not r["valid"]:  # invalid x2: its first attempt too
                    at = next(i for i, x in enumerate(recs) if x is r)
                    before = [x for x in recs[:at] if x["variant"] == v and x["attempt"] == 1]
                    attempts = before[-1:] + attempts
                used.update(id(x) for x in attempts)
                out[(channels, rep, v)] = {
                    "status": "valid" if r["valid"] else "invalid x2", "session": session, "name": r["name"],
                    "attempts": [(f"{x['session']}/{x['name']}", reason(x, rows)) for x in attempts]}
            break
    others = [(f"{r['session']}/{r['name']}", r["valid"], reason(r, rows)) for r in records if id(r) not in used]
    return out, others


def trip_warnings(session_dirs):
    """[(session/name, samples)] of the runs with samples at or above a trip point (§9.3)."""
    return [(f"{s}/{n}", row["trip_samples"]) for (s, n), row in runs_rows(session_dirs).items()
            if row.get("trip_samples") not in ("", "0", None)]


# ---------------------------------------------------------------- one run

def p99(latencies_ms):
    """§7.3: numpy.percentile with its default (linear) interpolation."""
    return float(np.percentile(latencies_ms, 99)) if len(latencies_ms) else math.nan


def num(summary, key):
    v = summary.get(key, "")
    try:
        return float(v)
    except ValueError:
        return math.nan


def run_metrics(run_dir, idle):
    """The metrics of one valid run. idle: the session's idle baseline (rows of idle_baseline.csv)."""
    header, rows = ss.read_csv(run_dir / "summary.csv")
    s = {r["key"]: r["value"] for r in rows}
    channels = ss.read_csv(run_dir / "channels.csv")[1]
    frames = ss.read_csv(run_dir / "frames.csv")[1]
    warmup_ns, window_ns = int(header["warmup_s"]) * 10**9, int(s["window_s"]) * 10**9
    lat = [(int(f["t4_ns"]) - int(f["t0_ns"])) / 1e6 for f in frames
           if warmup_ns <= int(f["t4_ns"]) < warmup_ns + window_ns]
    m = {k: num(s, k) for k, _ in METRICS}
    m["latency_p99_ms"] = p99(lat)
    m["latency_p99_bench_ms"] = num(s, "latency_p99_ms")
    fps = [float(c["inference_fps"]) for c in channels]
    m["inference_fps_min"], m["inference_fps_max"] = min(fps), max(fps)
    m["inference_fps_mean"] = sum(fps) / len(fps)
    for k in SYSTEM_KEYS:
        m[k[:-2] + "_minus_idle_s"] = m[k] - idle_share(idle, k, float(s["window_s"]))
    return m


def idle_share(idle, key, window_s):
    """The idle baseline's value of key over a window of window_s (§7.2), scaled from the baseline's
    own length to the run's."""
    return float(idle[key]) * window_s / float(idle["window_s"])


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
        return dict(out, spec="indeterminate", practical="indeterminate", reason="a variant has no valid run")
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
    sb = stats([r["system_cpu_total_minus_idle_s"] for r in b])[1]
    sc = stats([r["system_cpu_total_minus_idle_s"] for r in c])[1]
    out.update(system_B=sb, system_C=sc, mixed=(mb - mc) * (sb - sc) < 0)
    return out


def h2(b, c):
    """HYPOTHESES H2 at the auxiliary condition, 5 channels. b, c: {repetition: valid run's metrics}."""
    out = {"n_B": len(b), "n_C": len(c)}
    if not b or not c:
        return dict(out, spec="indeterminate", practical="indeterminate", reason="a variant has no valid run")
    mb = stats([r["latency_p99_ms"] for r in b.values()])[1]
    mc = stats([r["latency_p99_ms"] for r in c.values()])[1]
    db = stats([r["drop_rate"] for r in b.values()])[1]
    dc = stats([r["drop_rate"] for r in c.values()])[1]
    out.update(B=mb, C=mc, drop_B=db, drop_C=dc, flagged=len(b) < 3 or len(c) < 3)
    out["spec"] = "adopted" if mc < mb else "rejected"
    if out["flagged"]:
        out["practical"], out["reason"] = "indeterminate", "fewer than 3 valid runs"
    elif (all(c[r]["latency_p99_ms"] <= H2_RATIO * b[r]["latency_p99_ms"] for r in (1, 2, 3))
          and dc <= db + H2_DROP_PP + FLOAT_TOLERANCE):
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
    """Everything the report needs, from the protocol sessions under root/results."""
    c = {"data": {}, "missing": [], "invalid_x2": [], "others": [], "warnings": [], "h3_n": None, "h3_problem": None}
    for sweep in ("main", "aux", "reference"):
        sessions = sessions_of(root, sweep)
        idle = {d.name: read_idle(d) for d in sessions if (d / "idle_baseline.csv").exists()}
        runs, others = counted_runs(sessions, sweep)
        c["others"] += [(sweep,) + o for o in others]
        c["warnings"] += [(sweep,) + w for w in trip_warnings(sessions)]
        counts, _, variants, kind = ss.SWEEPS[sweep]
        c["data"][sweep] = {}
        for ch in counts:
            for v in variants:
                point = c["data"][sweep].setdefault(ch, {}).setdefault(v, {"valid": {}})
                for rep in range(1, ss.REPETITIONS + 1):
                    run = runs.get((ch, rep, v))
                    if run is None:
                        c["missing"].append(f"{sweep} {ch} ch {v} r{rep}")
                    elif run["status"] == "valid":
                        d = root / "results" / kind / run["session"] / run["name"]
                        point["valid"][rep] = run_metrics(d, idle[run["session"]])
                    else:
                        c["invalid_x2"].append((f"{sweep} {ch} ch {v} r{rep}", run["attempts"]))
    sessions = sessions_of(root, "h3")
    records = ss.load_records(sessions, ss.H3_PHASES)
    rows = runs_rows(sessions)
    trials = ss.h3_trials(records)
    try:
        _, _, c["h3_n"], _, _ = ss.h3_replay(trials)  # marks the trials it counts
    except ss.Stop as e:
        c["h3_problem"] = str(e)
    if c["h3_n"] is None:
        c["missing"].append("h3: " + (c["h3_problem"] or "not done"))
    c["invalid_x2"] += h3_invalid_x2(records, trials, rows)
    listed = {n for _, attempts in c["invalid_x2"] for n, _ in attempts}
    c["others"] += [("h3", f"{r['session']}/{r['name']}", r["valid"], reason(r, rows)) for r in h3_not_counted(records, trials)
                    if f"{r['session']}/{r['name']}" not in listed]
    c["warnings"] += [("h3",) + w for w in trip_warnings(sessions)]
    return c


def h3_invalid_x2(records, trials, rows):
    """[(what, [(session/name, reason)])] of the H3 runs the schedule counted as invalid x2: their repeat
    and the first attempt before it, which may be in an earlier session."""
    out, by_name = [], {f"{r['session']}/{r['name']}": r for r in records}
    for t in trials:
        last = by_name[t["names"][-1]]
        if not t.get("counted") or last["valid"] or last["attempt"] != 2:
            continue
        at = next(i for i, x in enumerate(records) if x is last)
        first = [x for x in records[:at] if x["attempt"] == 1 and (x["variant"], x["phase"], x["channels"],
                 x["repetition"]) == (last["variant"], last["phase"], last["channels"], last["repetition"])]
        out.append((f"h3 {t['phase']} {t['channels']} ch {t['variant']} r{t['repetition']}",
                    [(f"{x['session']}/{x['name']}", reason(x, rows)) for x in first[-1:] + [last]]))
    return out


def h3_not_counted(records, trials):
    """The H3 attempts that do not count: those of trials the schedule did not take (redone, or cut
    short), and the invalid first attempts of runs that were repeated."""
    counted = {n for t in trials if t.get("counted") for n in t["names"][-1:]}
    return [r for r in records if f"{r['session']}/{r['name']}" not in counted]


def fmt(x, digits=3):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{digits}f}"


def verdict_rows(c):
    main8, aux5 = c["data"]["main"][8], c["data"]["aux"][5]
    v1 = h1(list(main8["B"]["valid"].values()), list(main8["C"]["valid"].values()))
    v2 = h2(aux5["B"]["valid"], aux5["C"]["valid"])
    v3 = h3(c["h3_n"])
    flag = lambda v: " (flagged)" if v.get("flagged") else ""  # noqa: E731
    rows = [
        f"| H1, CPU at 8 channels | {v1['spec']}{flag(v1)} | {v1['practical']} | B {fmt(v1.get('B'))} s, "
        f"C {fmt(v1.get('C'))} s, saving {fmt(v1.get('saving', math.nan) * 100, 1)}%"
        f"{'; mixed (system CPU minus idle: B ' + fmt(v1['system_B']) + ' s, C ' + fmt(v1['system_C']) + ' s)' if v1.get('mixed') else ''} |",
        f"| H2, p99 at 5 channels, 30 fps | {v2['spec']}{flag(v2)} | {v2['practical']} | B {fmt(v2.get('B'), 1)} ms, "
        f"C {fmt(v2.get('C'), 1)} ms; drop rate B {fmt(v2.get('drop_B'), 5)}, C {fmt(v2.get('drop_C'), 5)} |",
        f"| H3, maximum channels | {v3['spec']} | {v3['practical']} | "
        + (f"n_B {v3['n_B']}, n_C {v3['n_C']} |" if "n_B" in v3 else "– |"),
    ]
    notes = [f"- {label}: {v['reason']}." for v, label in ((v1, "H1"), (v2, "H2"), (v3, "H3")) if v.get("reason")]
    return rows, notes


def report(c):
    lines = ["# M1 results", "", "## Conditions", ""] + [f"- {x}" for x in CONDITIONS] + [""]
    lines += ["## Completeness", ""]
    lines += ([f"Not complete: {', '.join(c['missing'])}."] if c["missing"] else ["Every point is complete, and H3 is done."])
    rows, notes = verdict_rows(c)
    lines += ["", "## Verdicts", "", "| Hypothesis | Spec-literal | Practical | Numbers |", "|---|---|---|---|"] + rows
    lines += ["", "The H2 auxiliary point is 5 channels (A7), so H2's secondary verdicts are the ones above.", ""] + notes
    if c["h3_problem"]:
        lines.append(f"- H3's records could not be replayed: {c['h3_problem']}")
    lines.append("")

    flags = []
    for sweep, points in c["data"].items():
        variants = ss.SWEEPS[sweep][2]
        lines += [f"## Sweep: {sweep}", "", "| Channels | Metric | " + " | ".join(f"{v}: mean ± sd (n)" for v in variants)
                  + " |", "|---|---|" + "---|" * len(variants)]
        for ch in sorted(points):
            for key, label in METRICS:
                cells = []
                for v in variants:
                    k, mean, sd, cv = stats([r[key] for r in points[ch][v]["valid"].values()])
                    cells.append(f"{fmt(mean)} ± {fmt(sd)} ({k})")
                    if key in HYPOTHESIS_METRICS and not math.isnan(cv) and cv > CV_FLAG:
                        flags.append(f"{sweep} {ch} ch {v}: {label} CV {cv * 100:.1f}%")
                lines.append(f"| {ch} | {label} | " + " | ".join(cells) + " |")
        lines.append("")
    lines += ["## Flags", ""]
    lines += [f"- {f}" for f in flags] or ["No hypothesis metric has a coefficient of variation above 10%."]
    lines += ["", "## Invalid x2", ""]
    lines += [f"- {what}: " + "; ".join(f"{name}: {why or 'no reason recorded'}" for name, why in attempts)
              for what, attempts in c["invalid_x2"]] or ["None."]
    lines += ["", "## Attempts that do not count", "",
              "Repeated invalid attempts, runs that a later session redid, and attempts cut short (§9.1, §9.3)."]
    lines += [f"- {sweep}: {name}: {'valid' if valid else 'invalid'}" + (f": {why}" if why else "")
              for sweep, name, valid, why in c["others"]] or ["None."]
    lines += ["", "## Warnings: samples at a trip point", ""]
    lines += [f"- {sweep}: {name}: {n} samples" for sweep, name, n in c["warnings"]] or ["None."]
    return "\n".join(lines) + "\n"


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    c = collect(Path(__file__).resolve().parent.parent)
    if args.check:
        print("\n".join(c["missing"]) if c["missing"] else "complete")
        return 1 if c["missing"] else 0
    text = report(c)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
