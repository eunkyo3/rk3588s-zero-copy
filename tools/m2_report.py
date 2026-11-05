#!/usr/bin/env python3
"""M2's verdicts and what prereg/M2.md §6 reports, from the sessions of the sweeps m2_main and m2_aux.

  - M2-H1 (8 channels, main) and M2-H2 (5 channels, auxiliary), §2: process CPU, (C - Cp) / C on the
    3-run means against 3% and 5%; the practical verdict's guards (a channel's inference rate below
    4.75 or 28.5 fps in a valid run of either arm; Cp's mean drop rate more than 1 percentage point above
    C's); fewer than 3 valid runs as HYPOTHESES.md says; the secondary metric, /proc/schedstat's system
    CPU minus the session's idle baseline, and the label mixed when C - Cp has the other sign on it;
  - with each verdict: the three paired differences (C_r - Cp_r) / C_r, a descriptive 95% t-interval of
    their mean, and a flag on an arm whose coefficient of variation is above half the threshold;
  - per point and arm: the per-cluster split, /proc/stat's system CPU, latency, drop and inference rate,
    the post-processing stage, the arrival phases' spread and, at the main condition, the stagger error;
  - per repetition, the detection counts of the frames both runs processed, matched by channel and pts
    modulo the length of the channel's video (§4, reported with no verdict);
  - M2's C next to M1's and M1.1's C at the same points (exploratory: the sampler and sessions differ).

Usage, from the repository: python3 tools/m2_report.py [--out FILE]   (Markdown)
"""
import argparse
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m1_phases as ph  # noqa: E402
import m1_report as rep  # noqa: E402
import m2_checks as mc  # noqa: E402
import stream_session as ss  # noqa: E402

POINTS = {  # sweep: (hypothesis, channels, threshold, lowest inference rate of the practical verdict)
    "m2_main": ("M2-H1", 8, 0.03, 4.75),
    "m2_aux": ("M2-H2", 5, 0.05, 28.5),
}
DROP_PP = 0.01  # Cp's mean drop rate may be at most 1 percentage point above C's (§2)
T_975 = {1: 12.706204736174698, 2: 4.302652729911275}  # Student's t, 0.975 quantile, by degrees of freedom
PTS_PER_FRAME = 3000  # 90 kHz RTSP timestamps at 30 fps (PROTOCOL §3)
VIDEO_FRAMES = (1186, 752, 714, 948)  # videos ① to ④; channel n plays video ((n - 1) mod 4) + 1 (A9)
M1_C = {  # process CPU of C at the same points, mean ± sd over 3 runs: M1 (docs/report/M1_tables.md), M1.1 (A12)
    "m2_main": ((18.560, 0.308), (19.490, 0.260)),
    "m2_aux": ((35.680, 0.486), (37.450, 1.410)),
}


def video_frame(channel, pts):
    """The frame of the channel's video that a pts shows: pts in frames, modulo the video's length."""
    return round(pts / PTS_PER_FRAME) % VIDEO_FRAMES[(channel - 1) % 4]


def detection_counts(frames):
    """{(channel, video frame): {detection counts}} of a run's frames."""
    out = {}
    for f in frames:
        ch = int(f["channel"])
        out.setdefault((ch, video_frame(ch, int(f["pts"]))), set()).add(int(f["detections"]))
    return out


def compare_detections(c_frames, cp_frames):
    """(frames compared, frames whose counts differ, frames with more than one count within a run) for
    the video frames both runs processed."""
    c, cp = detection_counts(c_frames), detection_counts(cp_frames)
    both = c.keys() & cp.keys()
    return (len(both), sum(c[k] != cp[k] for k in both),
            sum(len(v) > 1 for v in c.values()) + sum(len(v) > 1 for v in cp.values()))


def run_m2_metrics(run_dir, idle, sweep):
    """rep.run_metrics, with /proc/schedstat's system CPU minus the idle baseline (§3.2), the
    post-processing stage, and the arrival phases."""
    m = rep.run_metrics(run_dir, idle)
    s = {r["key"]: r["value"] for r in ss.read_csv(run_dir / "summary.csv")[1]}
    window = float(s["window_s"])
    m["schedstat_total_s"] = rep.num(s, "schedstat_cpu_total_s")
    m["schedstat_total_minus_idle_s"] = (m["schedstat_total_s"] - rep.idle_share(idle, "schedstat_cpu_total_s", window)
                                         if "schedstat_cpu_total_s" in idle else math.nan)
    frames = rep.window_frames(run_dir)
    post = sorted((int(f["t4_ns"]) - int(f["t3_ns"])) / 1e6 for f in frames)
    m["post_p50_ms"] = float(np.percentile(post, 50)) if post else math.nan
    _, condition, _, _ = ss.SWEEPS[sweep]
    period_ms = 1000.0 / ss.FPS[condition]
    t0 = {}
    for f in frames:
        t0.setdefault(int(f["channel"]), []).append(int(f["t0_ns"]))
    phases = [ph.phase(t0[c], period_ms * 1e6) / 1e6 for c in sorted(t0)]
    m["phase_spread_ms"] = ph.spread(phases, period_ms) if phases else math.nan
    m["stagger_error_ms"] = mc.stagger_error(phases) if condition == "main" and phases else math.nan
    m["_all_frames"] = ss.read_csv(run_dir / "frames.csv")[1]
    return m


def t_interval(values):
    """(mean, low, high) of a descriptive 95% t-interval; low and high are nan below 2 values."""
    n, mean, sd, _ = rep.stats(values)
    if n < 2:
        return mean, math.nan, math.nan
    half = T_975[n - 1] * sd / math.sqrt(n) if n - 1 in T_975 else math.nan
    return mean, mean - half, mean + half


def verdict(sweep, c, cp):
    """The verdicts of a hypothesis. c, cp: {repetition: metrics of a valid run}."""
    hyp, _, threshold, min_fps = POINTS[sweep]
    out = {"hypothesis": hyp, "threshold": threshold, "n_C": len(c), "n_Cp": len(cp)}
    if not c or not cp:
        return dict(out, spec="indeterminate", practical="indeterminate", reason="an arm has no valid run")
    m_c = rep.stats([r["process_cpu_s"] for r in c.values()])
    m_cp = rep.stats([r["process_cpu_s"] for r in cp.values()])
    saving = (m_c[1] - m_cp[1]) / m_c[1]
    d_c = rep.stats([r["drop_rate"] for r in c.values()])[1]
    d_cp = rep.stats([r["drop_rate"] for r in cp.values()])[1]
    out.update(C=m_c[1], Cp=m_cp[1], saving=saving, drop_C=d_c, drop_Cp=d_cp, flagged=len(c) < 3 or len(cp) < 3)
    out["spec"] = "adopted" if saving >= threshold else "rejected"
    slow = [r for r in list(c.values()) + list(cp.values()) if r["inference_fps_min"] < min_fps]
    if out["flagged"]:
        out["practical"], out["reason"] = "indeterminate", "fewer than 3 valid runs"
    elif slow:
        out["practical"], out["reason"] = "indeterminate", f"{len(slow)} valid runs with a channel below {min_fps} fps"
    elif d_cp > d_c + DROP_PP + rep.FLOAT_TOLERANCE:
        out["practical"], out["reason"] = "indeterminate", "Cp's mean drop rate more than 1 point above C's"
    else:
        out["practical"] = out["spec"]
    s_c = rep.stats([r["schedstat_total_minus_idle_s"] for r in c.values()])[1]
    s_cp = rep.stats([r["schedstat_total_minus_idle_s"] for r in cp.values()])[1]
    out.update(system_C=s_c, system_Cp=s_cp, mixed=(m_c[1] - m_cp[1]) * (s_c - s_cp) < 0)
    pairs = {r: (c[r]["process_cpu_s"] - cp[r]["process_cpu_s"]) / c[r]["process_cpu_s"] for r in sorted(c.keys() & cp.keys())}
    out["pairs"] = pairs
    out["interval"] = t_interval(list(pairs.values()))
    out["cv_flags"] = [arm for arm, s in (("C", m_c), ("Cp", m_cp)) if s[3] > threshold / 2]
    out["cv"] = {"C": m_c[3], "Cp": m_cp[3]}
    return out


def collect(root):
    """{sweep: {"sessions", "points": {"C"|"Cp": {repetition: metrics}}, "invalid_x2", "others", "warnings",
    "missing"}}."""
    out = {}
    for sweep in POINTS:
        sessions = rep.sessions_of(root, sweep)
        idle = {d.name: rep.read_idle(d) for d in sessions if (d / "idle_baseline.csv").exists()}
        runs, others = rep.counted_runs(sessions, sweep)
        _, ch = POINTS[sweep][:2]
        kind = ss.SWEEPS[sweep][3]
        point = {"C": {}, "Cp": {}}
        missing, invalid = [], []
        for r in range(1, ss.REPETITIONS + 1):
            for arm in ("C", "Cp"):
                run = runs.get((ch, r, arm))
                if run is None:
                    missing.append(f"{sweep} {arm} r{r}")
                elif run["status"] == "valid":
                    d = root / "results" / kind / run["session"] / run["name"]
                    point[arm][r] = dict(run_m2_metrics(d, idle[run["session"]], sweep), _run=f"{run['session']}/{run['name']}")
                else:
                    invalid.append((f"{sweep} {arm} r{r}", run["attempts"]))
        out[sweep] = {"sessions": len(sessions), "points": point, "missing": missing, "invalid_x2": invalid,
                      "others": others, "warnings": rep.trip_warnings(sessions)}
    return out


def f(x, digits=3):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{digits}f}"


def pct(x, digits=1):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{100 * x:.{digits}f}%"


def mean_sd(runs, key, digits=3):
    n, mean, sd, _ = rep.stats([r[key] for r in runs.values()])
    return "–" if n == 0 else f"{f(mean, digits)} ± {f(sd, digits)} ({n})"


ROWS = (  # (key, label, digits) of the per-point table
    ("process_cpu_s", "Process CPU (s)", 3),
    ("process_cpu_a76_s", "Process CPU, A76 (s)", 3),
    ("process_cpu_a55_s", "Process CPU, A55 (s)", 3),
    ("process_cpu_unattributed_s", "Process CPU, unattributed (s)", 3),
    ("schedstat_total_minus_idle_s", "System CPU, /proc/schedstat, minus idle (s)", 3),
    ("system_cpu_total_minus_idle_s", "System CPU, /proc/stat, minus idle (s)", 3),
    ("post_p50_ms", "Post-processing stage p50 (ms)", 3),
    ("latency_p50_ms", "Latency p50 (ms)", 1),
    ("latency_p99_ms", "Latency p99 (ms)", 1),
    ("drop_rate", "Drop rate", 5),
    ("inference_fps_min", "Inference rate, lowest channel (fps)", 2),
    ("phase_spread_ms", "Arrival phases' spread (ms)", 1),
    ("stagger_error_ms", "Stagger error (ms)", 1),
)


def report(c):
    lines = ["# M2: C against Cp", "",
             "From the sessions of `sweep m2_main` and `sweep m2_aux` ([prereg/M2.md](../../prereg/M2.md)). "
             "Values are the mean ± sample sd (valid runs) per 120 s window. Method: `tools/m2_report.py`.", "",
             "## Verdicts", "",
             "| Hypothesis | C (s) | Cp (s) | (C − Cp) / C | Threshold | Spec-literal | Practical | Secondary: schedstat C / Cp (s) | Mixed |",
             "|---|---|---|---|---|---|---|---|---|"]
    verdicts = {}
    for sweep in POINTS:
        p = c[sweep]["points"]
        v = verdicts[sweep] = verdict(sweep, p["C"], p["Cp"])
        practical = v["practical"] + (f" ({v['reason']})" if v.get("reason") else "")
        lines.append(f"| {v['hypothesis']} | {f(v.get('C'))} | {f(v.get('Cp'))} | {pct(v.get('saving'), 2)} | "
                     f"{pct(v['threshold'], 0)} | {v['spec']} | {practical} | {f(v.get('system_C'))} / "
                     f"{f(v.get('system_Cp'))} | {'yes' if v.get('mixed') else 'no'} |")
    lines.append("")
    for sweep in POINTS:
        v = verdicts[sweep]
        if "pairs" not in v:
            continue
        mean, lo, hi = v["interval"]
        lines += [f"**{v['hypothesis']}:** paired differences (C_r − Cp_r) / C_r: "
                  + ", ".join(f"r{r} {pct(x, 2)}" for r, x in v["pairs"].items())
                  + f"; mean {pct(mean, 2)}, descriptive 95% t-interval {pct(lo, 2)} to {pct(hi, 2)}. "
                  f"CV of process CPU: C {pct(v['cv']['C'], 2)}, Cp {pct(v['cv']['Cp'], 2)}"
                  + (f"; flagged above {pct(v['threshold'] / 2, 1)}: {', '.join(v['cv_flags'])}." if v["cv_flags"]
                     else f"; none above {pct(v['threshold'] / 2, 1)}."), ""]
    for sweep in POINTS:
        hyp, ch = POINTS[sweep][:2]
        p = c[sweep]["points"]
        lines += [f"## {sweep}: {ch} channels, {ss.SWEEPS[sweep][1]} condition ({hyp})", "",
                  "| Metric | C | Cp |", "|---|---|---|"]
        for key, label, digits in ROWS:
            if key == "stagger_error_ms" and ss.SWEEPS[sweep][1] != "main":
                continue
            lines.append(f"| {label} | {mean_sd(p['C'], key, digits)} | {mean_sd(p['Cp'], key, digits)} |")
        lines += ["", "| Repetition | Arm | Run | Process CPU (s) | schedstat − idle (s) | Post p50 (ms) | p99 (ms) | Spread (ms) |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in range(1, ss.REPETITIONS + 1):
            for arm in ("C", "Cp"):
                m = p[arm].get(r)
                if m:
                    lines.append(f"| {r} | {arm} | `{m['_run']}` | {f(m['process_cpu_s'])} | "
                                 f"{f(m['schedstat_total_minus_idle_s'])} | {f(m['post_p50_ms'])} | "
                                 f"{f(m['latency_p99_ms'], 1)} | {f(m['phase_spread_ms'], 1)} |")
        lines += ["", "**Detection counts, C against Cp, frames both runs processed (§4, no verdict):**", ""]
        for r in range(1, ss.REPETITIONS + 1):
            if r in p["C"] and r in p["Cp"]:
                n, diff, within = compare_detections(p["C"][r]["_all_frames"], p["Cp"][r]["_all_frames"])
                lines.append(f"- r{r}: {n} video frames compared, {diff} with different counts; "
                             f"{within} with more than one count within a run.")
        (m1, m11) = M1_C[sweep]
        lines += ["", f"**M2's C next to earlier C (exploratory):** M2 {mean_sd(p['C'], 'process_cpu_s')} s; "
                  f"M1 {m1[0]:.3f} ± {m1[1]:.3f} s; M1.1 {m11[0]:.3f} ± {m11[1]:.3f} s. The main condition's sampler and "
                  "the sessions differ.", ""]
        notes = ([f"- missing: {x}" for x in c[sweep]["missing"]]
                 + [f"- invalid ×2: {k}: " + "; ".join(f"{a} ({why})" for a, why in at) for k, at in c[sweep]["invalid_x2"]]
                 + [f"- attempt that does not count: {a}, {'valid' if ok else 'invalid'} ({why})" for a, ok, why in c[sweep]["others"]]
                 + [f"- samples at a trip point: {a}: {n}" for a, n in c[sweep]["warnings"]])
        lines += notes + ([""] if notes else [])
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    text = report(collect(Path(__file__).resolve().parent.parent))
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
