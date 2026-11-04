#!/usr/bin/env python3
"""M1's figures (M1-7) from the comparison runs, as SVG, with the method of tools/m1_report.py.

  - cpu_<sweep>.svg: process CPU (total, A76, A55) and system CPU minus idle (total, cpu0-3, cpu4-7);
  - latency_<sweep>.svg: p50 and p99 latency, drop rate;
  - decoder_<sweep>.svg: MPP hold time (p50, p99) and slot wait (p99);
  - h3.svg: the H3 runs that count (search and confirmation): the lowest channel's inference rate,
    process CPU and p99 latency, one valid run per point; invalid x2 runs are not drawn.
for the main and auxiliary sweeps; A's runs (the reference sweep) are drawn with the main sweep. In
the sweeps' figures a point is the mean over its valid runs, with the sample standard deviation as an
error bar (none for one valid run); a point without a valid run breaks the line. The vertical lines
are the measured limits: the NPU's, P_cap / fps (A7), and the decoder's, L = 32, the search's cap (A9).

The files carry no date or version and have LF line ends, so a clean clone writes the same bytes with
the same matplotlib.

Usage, from the repository: python3 tools/m1_plots.py [--out DIR]   (default docs/report/figures)
"""
import argparse
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("svg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m1_report as rep  # noqa: E402
import stream_session as ss  # noqa: E402

P_CAP = float(ss.P_CAP_BUDGET) / 0.8  # inferences per second (A7): 194.2083
SERIES = {"B": "#2a78d6", "C": "#eb6834", "A": "#1baf7a"}  # categorical slots 1-3, fixed per variant
MARKERS = {"B": "o", "C": "s", "A": "^"}  # identity is never colour alone
LABELS = {"B": "B", "C": "C (A6 label)", "A": "A (reference)"}
SURFACE, INK, INK_2, RULE = "#fcfcfb", "#0b0b0b", "#52514e", "#d9d8d4"
WINDOW_S = 120  # §9.1

CPU_PANELS = [
    ("process_cpu_s", "Process CPU, total"), ("process_cpu_a76_s", "Process CPU on the A76"),
    ("process_cpu_a55_s", "Process CPU on the A55"), ("system_cpu_total_minus_idle_s", "System CPU minus idle, total"),
    ("system_cpu0-3_minus_idle_s", "System CPU minus idle, cpu0-3 (A55)"),
    (lambda m: m["system_cpu4-5_minus_idle_s"] + m["system_cpu6-7_minus_idle_s"],
     "System CPU minus idle, cpu4-7 (A76)"),
]
LATENCY_PANELS = [("latency_p50_ms", "p50 latency (ms)"), ("latency_p99_ms", "p99 latency (ms)"),
                  ("drop_rate", "Drop rate")]
DECODER_PANELS = [("mpp_hold_p50_ms", "MPP hold, p50 (ms)"), ("mpp_hold_p99_ms", "MPP hold, p99 (ms)"),
                  ("slot_wait_p99_ms", "Slot wait, p99 (ms)")]
H3_PANELS = [("inference_fps_min", "Inference rate, lowest channel (fps)"), ("process_cpu_s", "Process CPU (s)"),
             ("latency_p99_ms", "p99 latency (ms)")]


def style():
    plt.rcParams.update({
        "svg.hashsalt": "m1", "svg.fonttype": "none", "font.family": "sans-serif",
        "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica"], "font.size": 9,
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": RULE, "axes.labelcolor": INK_2, "axes.titlecolor": INK, "axes.titlesize": 9.5,
        "axes.titleweight": "bold", "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "axes.grid.axis": "y", "grid.color": RULE, "grid.linewidth": 0.6,
        "xtick.color": INK_2, "ytick.color": INK_2, "xtick.major.size": 0, "ytick.major.size": 0,
        "legend.frameon": False, "lines.linewidth": 1.5, "lines.markersize": 5.5,
    })


def value(metric, m):
    return metric(m) if callable(metric) else m[metric]


def series(points, variant, metric):
    """[(channels, mean, sd)] of a sweep's points for one variant: nan where a point has no valid run,
    so the line breaks there; sd 0 (no error bar) for a point with one valid run."""
    out = []
    for ch in sorted(points):
        runs = points[ch].get(variant, {"valid": {}})["valid"].values()
        n, mean, sd, _ = rep.stats([value(metric, m) for m in runs])
        out.append((ch, mean, 0.0 if math.isnan(sd) else sd))
    return out if any(not math.isnan(y) for _, y, _ in out) else []


def draw(ax, xs, ys, err, variant, connect=True):
    ax.errorbar(xs, ys, yerr=err, color=SERIES[variant], marker=MARKERS[variant], linestyle="-" if connect else "none",
                markeredgecolor=SURFACE, markeredgewidth=0.8, elinewidth=1, capsize=0, label=LABELS[variant],
                zorder=3)


def limits(ax, fps):
    """The measured limits as thin vertical rules, tagged at the bottom (the figure's note names them):
    the NPU's, P_cap / fps, and the decoder's L."""
    lo, hi = ax.get_xlim()
    for x, tag, side in ((P_CAP / fps, " NPU", "left"), (ss.LIMIT, "L ", "right")):
        if lo <= x <= hi:
            ax.axvline(x, color=INK_2, linewidth=0.8, zorder=1)
            ax.text(x, 0.02, tag, transform=ax.get_xaxis_transform(), ha=side, va="bottom", fontsize=7, color=INK_2)


def limits_note(fps, x_max):
    shown = [f"NPU = P_cap / {fps} fps = {P_CAP / fps:.1f} channels (A7)"]
    if ss.LIMIT <= x_max:
        shown.append(f"L = {ss.LIMIT}, the decoder limit at the search's cap (A9)")
    return "Vertical rules, measured limits: " + "; ".join(shown) + ". Error bars: sample sd."


def x_axis(ax, sweep, counts, lowest=8):
    if sweep == "aux":  # room right of each rule for its tag
        ax.set_xlim(0.5, P_CAP / 30 + 1)
    elif sweep == "h3":  # lowest: the lowest count the H3 search reached
        ax.set_xlim(min(lowest, ss.H3_START) - 1, P_CAP / 5 + 4.5)
    else:
        ax.set_xscale("log", base=2)
        ax.set_xlim(0.8, P_CAP / 5 * 1.35)
    ax.set_xticks(counts)
    ax.set_xticklabels([str(c) for c in counts])
    ax.minorticks_off()
    ax.set_xlabel("channels")
    limits(ax, ss.FPS["aux" if sweep == "aux" else "main"])


def small_multiples(c, sweep, panels, ncols, title, path):
    points = c["data"][sweep]
    variants = ["B", "C"] + (["A"] if sweep == "main" else [])
    nrows = -(-len(panels) // ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(3.3 * ncols, 2.5 * nrows + 1.1), squeeze=False)
    for ax, (metric, label) in zip(axes.flat, panels):
        low = 0.0
        for v in variants:
            pts = series(c["data"]["reference"] if v == "A" else points, v, metric)
            if pts:
                xs, ys, err = zip(*pts)
                draw(ax, xs, ys, err, v)
                low = min([low] + [y - e for y, e in zip(ys, err) if not math.isnan(y)])
        ax.set_title(label, loc="left")
        ax.set_ylim(bottom=low)
        x_axis(ax, sweep, sorted(points))
    for ax in list(axes.flat)[len(panels):]:
        ax.set_visible(False)
    fps = ss.FPS["aux" if sweep == "aux" else "main"]
    finish(fig, axes.flat[0], title, limits_note(fps, axes.flat[0].get_xlim()[1]), path)


def finish(fig, ax, title, note, path):
    """Title, a legend under it, the note at the bottom; then the file, without a date or version."""
    height = fig.get_figheight()
    fig.suptitle(title, x=0.01, y=1 - 0.08 / height, ha="left", va="top", fontsize=10.5, fontweight="bold",
                 color=INK)
    handles, labels = ax.get_legend_handles_labels()
    order = sorted(range(len(labels)), key=lambda i: ("confirmation" in labels[i], i))
    if labels:
        fig.legend([handles[i] for i in order], [labels[i] for i in order], loc="upper left",
                   bbox_to_anchor=(0.005, 1 - 0.38 / height), ncol=len(labels), fontsize=8.5, labelcolor=INK)
    fig.text(0.01, 0.06 / height, note, ha="left", va="bottom", fontsize=7.5, color=INK_2)
    fig.tight_layout(rect=(0, 0.3 / height, 1, 1 - 0.62 / height))
    with open(path, "w", encoding="utf-8", newline="\n") as f:  # the same bytes on every OS
        fig.savefig(f, format="svg", metadata={"Date": None, "Creator": None})
    plt.close(fig)


def h3_runs(root, limit=ss.LIMIT, start=ss.H3_START):
    """({variant: [(channels, phase, metrics)]} of the H3 runs that count and are valid, or None if
    the records could not be replayed): the runs counted up to the problem stay."""
    sessions = rep.sessions_of(root, "h3")
    records = ss.load_records(sessions, ss.H3_PHASES)
    by_name = {f"{r['session']}/{r['name']}": r for r in records}
    trials = ss.h3_trials(records)
    problem = None
    try:
        ss.h3_replay(trials, limit, start)  # marks the trials it counts
    except ss.Stop as e:
        problem = str(e)
    idle = {d.name: rep.read_idle(d) for d in sessions if (d / "idle_baseline.csv").exists()}
    out = {"B": [], "C": []}
    for t in trials:
        last = by_name[t["names"][-1]]
        if t.get("counted") and last["valid"]:
            d = root / "results" / "comparison" / last["session"] / last["name"]
            out[t["variant"]].append((t["channels"], t["phase"], rep.run_metrics(d, idle[last["session"]])))
    return out, problem


def h3_figure(root, path):
    runs, problem = h3_runs(root)
    fig, axes = plt.subplots(1, len(H3_PANELS), figsize=(3.3 * len(H3_PANELS), 3.6), squeeze=False)
    counts = sorted({ch for rs in runs.values() for ch, _, _ in rs})
    for ax, (metric, label) in zip(axes.flat, H3_PANELS):
        for v, rs in runs.items():
            search = sorted((ch, m[metric]) for ch, phase, m in rs if phase == "h3_search")
            if search:
                xs, ys = zip(*search)
                draw(ax, xs, ys, None, v)
            confirm = [(ch, m[metric]) for ch, phase, m in rs if phase == "h3_confirm"]
            if confirm:
                xs, ys = zip(*confirm)
                ax.scatter(xs, ys, s=40, facecolors="none", edgecolors=SERIES[v], marker=MARKERS[v], linewidths=1,
                           zorder=4, label=f"{LABELS[v]}, confirmation")
        if metric == "inference_fps_min":
            ax.axhline(ss.SUSTAIN_FPS, color=INK_2, linewidth=0.8, zorder=1)
            lowest_fps = min([m[metric] for rs in runs.values() for _, _, m in rs], default=5.0)
            ax.text(min(counts, default=ss.H3_START), ss.SUSTAIN_FPS, f" sustained: ≥ {ss.SUSTAIN_FPS} fps",
                    va="bottom", fontsize=7, color=INK_2)
            ax.set_ylim(min(4.5, lowest_fps - 0.1), 5.2)
        else:
            ax.set_ylim(bottom=0)
        ax.set_title(label, loc="left")
        x_axis(ax, "h3", [ch for ch in counts if ch % 4 == 0 or ch < ss.H3_START],
               lowest=min(counts, default=ss.H3_START))
    note = limits_note(ss.FPS["main"], axes.flat[0].get_xlim()[1]).replace(
        "Error bars: sample sd.", "Invalid x2 runs are not drawn.")
    if problem:
        note += f" The H3 records could not be replayed past a point: {problem}"
    finish(fig, axes.flat[0], "H3: the runs that count, main condition, one run per point", note, path)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="docs/report/figures")
    args = ap.parse_args(argv)
    write_figures(Path(__file__).resolve().parent.parent, Path(args.out))
    print(f"figures in {args.out}")
    return 0


def write_figures(root, out):
    """Every figure, from the protocol sessions under root/results, into out."""
    out.mkdir(parents=True, exist_ok=True)
    style()
    c = rep.collect(root, with_m1_1=False)
    names = {"main": "main condition, 5 fps per channel", "aux": "auxiliary condition, 30 fps per channel"}
    for sweep, condition in names.items():
        small_multiples(c, sweep, CPU_PANELS, 3, f"CPU seconds in the {WINDOW_S} s window, {condition}",
                        out / f"cpu_{sweep}.svg")
        small_multiples(c, sweep, LATENCY_PANELS, 3, f"Latency and drops, {condition}", out / f"latency_{sweep}.svg")
        small_multiples(c, sweep, DECODER_PANELS, 3, f"Decoder buffers and slots, {condition}",
                        out / f"decoder_{sweep}.svg")
    h3_figure(root, out / "h3.svg")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
