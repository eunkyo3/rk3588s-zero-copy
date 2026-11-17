#!/usr/bin/env python3
"""Where the CPU goes from B to Cp (exploratory, computed after the fact: not pre-registered, no verdict).

Each step against the control of its own session, at 8 channels in the main condition and at 5 channels
× 30 fps: C against B in M1 (sweeps main and aux), Bd and C against B in M1.1 (A12: bdirect_main and
bdirect_aux), Cp against C in M2 (m2_main and m2_aux). Per arm, over the valid runs that count (taken as
tools/m1_report.py takes them), the mean ± sample sd of:
  - process CPU in the 120 s window (summary.csv), and the ratio of its mean to the control's mean;
  - CPU by thread group, from threads.csv, with the groups of the M2 report: NPU workers (npu-*), decoder
    (dec-* and MPP's mpp_* threads), preprocessing (pre-*), and the rest (bench_main, metrics);
  - the remainder: process CPU minus the sum of the threads (threads.csv has the threads alive at the
    window's end).
With them: each session's code commit, from its runs' env.json; the published values these numbers overlap
with, and whether they match at the published digits (exit 1 if one does not); and the figure. The sessions,
and their binaries, differ, so the tables and the figure compare values within a session only.

Usage, from the repository: python3 tools/cpu_breakdown.py [--out FILE] [--svg FILE]
  --out   write the tables there (Markdown) instead of printing them
  --svg   where to write the figure (default docs/notes/cpu_breakdown/cpu_breakdown.svg)
"""
import argparse
import json
import math
import sys
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("svg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m1_plots as plots  # noqa: E402
import m1_report as rep  # noqa: E402
import stream_session as ss  # noqa: E402

STUDIES = (  # (study, {point: sweep}, arms; the first is the session's control)
    ("M1", {"main": "main", "aux": "aux"}, ("B", "C")),
    ("M1.1", {"main": "bdirect_main", "aux": "bdirect_aux"}, ("B", "Bd", "C")),
    ("M2", {"main": "m2_main", "aux": "m2_aux"}, ("C", "Cp")),
)
POINTS = {"main": (8, "8 channels, main condition (5 fps)"), "aux": (5, "5 channels × 30 fps")}
GROUPS = (("dec", "Decoder (dec-*, mpp_*)"), ("npu", "NPU workers (npu-*)"), ("pre", "Preprocessing (pre-*)"),
          ("rest", "Rest (bench_main, metrics)"))
KEYS = ("process",) + tuple(g for g, _ in GROUPS) + ("threads", "remainder")
PUBLISHED = {  # (study, point, arm): {key: value as published}; process CPU from the reports' tables, the
    # groups from docs/report/M2.md, "Where the saving comes from"
    ("M1", "main", "B"): {"process": "43.283 ± 0.343"},
    ("M1", "main", "C"): {"process": "18.560 ± 0.308"},
    ("M1", "aux", "B"): {"process": "129.587 ± 1.236"},
    ("M1", "aux", "C"): {"process": "35.680 ± 0.486"},
    ("M1.1", "main", "B"): {"process": "44.037 ± 0.268"},
    ("M1.1", "main", "Bd"): {"process": "42.787 ± 0.673"},
    ("M1.1", "main", "C"): {"process": "19.487 ± 0.257"},
    ("M1.1", "aux", "B"): {"process": "130.953 ± 0.556"},
    ("M1.1", "aux", "Bd"): {"process": "129.853 ± 0.655"},
    ("M1.1", "aux", "C"): {"process": "37.447 ± 1.408"},
    ("M2", "main", "C"): {"process": "18.737 ± 0.170", "npu": "6.83 ± 0.08", "dec": "11.33 ± 0.15", "pre": "0.45"},
    ("M2", "main", "Cp"): {"process": "17.600 ± 0.560", "npu": "5.82 ± 0.01", "dec": "11.24 ± 0.53", "pre": "0.43"},
    ("M2", "aux", "C"): {"process": "37.280 ± 0.528", "npu": "27.10 ± 0.46", "dec": "8.74 ± 0.25", "pre": "1.31"},
    ("M2", "aux", "Cp"): {"process": "31.790 ± 0.401", "npu": "22.15 ± 0.18", "dec": "8.29 ± 0.29", "pre": "1.21"},
}
SOURCES = {"M1": "M1_tables.md", "M1.1": "M1_tables.md (A12)", "M2": "M2_tables.md; groups: M2.md"}
COLOURS = dict(zip((g for g, _ in GROUPS), ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")))  # categorical slots 1-4


# ---------------------------------------------------------------- thread groups

def group(comm):
    """The thread group of a thread's name, as in the M2 report."""
    if comm.startswith("npu-"):
        return "npu"
    if comm.startswith(("dec-", "mpp_")):
        return "dec"
    if comm.startswith("pre-"):
        return "pre"
    return "rest"


def by_group(threads):
    """{group: CPU s in the window} of threads.csv's rows, every group present."""
    out = dict.fromkeys((g for g, _ in GROUPS), 0.0)
    for t in threads:
        out[group(t["comm"])] += float(t["cpu_s_in_window"])
    return out


def run_cpu(run_dir):
    """A run's process CPU, its CPU by thread group, their sum and the remainder, and its code commit
    (env.json)."""
    s = {r["key"]: r["value"] for r in ss.read_csv(run_dir / "summary.csv")[1]}
    g = by_group(ss.read_csv(run_dir / "threads.csv")[1])
    process = rep.num(s, "process_cpu_s")
    threads = sum(g.values())
    commit = json.loads((run_dir / "env.json").read_text(encoding="utf-8")).get("header_code_commit", "?")
    return dict(g, process=process, threads=threads, remainder=process - threads, commit=commit)


# ---------------------------------------------------------------- the runs that count

def collect(root):
    """{(study, point): {"sweep", "sessions", "commits", "arms": {arm: {repetition: run_cpu}}}}, from the
    valid runs that count of each study's sweeps under root/results."""
    out = {}
    for study, sweeps, arms in STUDIES:
        for point, sweep in sweeps.items():
            channels = POINTS[point][0]
            runs, _ = rep.counted_runs(rep.sessions_of(root, sweep), sweep)
            kind = ss.SWEEPS[sweep][3]
            data, sessions = {arm: {} for arm in arms}, set()
            for (ch, r, arm), run in runs.items():
                if ch == channels and arm in data and run["status"] == "valid":
                    data[arm][r] = run_cpu(root / "results" / kind / run["session"] / run["name"])
                    sessions.add(run["session"])
            commits = sorted({m["commit"] for runs_ in data.values() for m in runs_.values()})
            out[(study, point)] = {"sweep": sweep, "sessions": sorted(sessions), "commits": commits, "arms": data}
    return out


def summary(runs):
    """{key: (n, mean, sd)} over the runs of KEYS."""
    return {k: rep.stats([m[k] for m in runs.values()])[:3] for k in KEYS}


def ratio(arm, control):
    """The ratio of an arm's mean to its control's mean, nan without both."""
    return arm / control if control and not math.isnan(arm) and not math.isnan(control) else math.nan


def largest(s):
    """The thread group with the largest mean in a summary, None without runs."""
    return max((g for g, _ in GROUPS), key=lambda g: s[g][1]) if s["process"][0] else None


def spread(values):
    """(max - min) / min of the values, nan below 2."""
    v = [x for x in values if not math.isnan(x)]
    return (max(v) - min(v)) / min(v) if len(v) > 1 and min(v) else math.nan


def as_published(published, n_mean_sd):
    """The tool's (n, mean, sd) written as the published value is: the same decimals, with "± sd" where the
    published value has one."""
    _, mean, sd = n_mean_sd
    value, _, sd_text = (x.strip() for x in published.partition("±"))
    digits = len(value.split(".")[1]) if "." in value else 0
    out = f"{mean:.{digits}f}"
    return out + (f" ± {sd:.{len(sd_text.split('.')[1]) if '.' in sd_text else 0}f}" if sd_text else "")


# ---------------------------------------------------------------- the tables

def f(x, digits=2):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{digits}f}"


def mean_sd(s, digits=2):
    n, mean, sd = s
    return "–" if n == 0 else f(mean, digits) + ("" if math.isnan(sd) else f" ± {f(sd, digits)}")


def report(c):
    lines = ["# Where the CPU goes from B to Cp (exploratory)", "",
             "Computed after the fact, not pre-registered, no verdict. Values are the mean ± sample sd over the "
             "valid runs that count, per 120 s window, in s. Method: `tools/cpu_breakdown.py`. Each session's "
             "values are compared with its own control only: the sessions and binaries differ.", "",
             "## Sessions", "", "| Study | Point | Sweep | Session | Code | Runs (arm: n) |",
             "|---|---|---|---|---|---|"]
    for (study, point), d in c.items():
        n = ", ".join(f"{a}: {len(r)}" for a, r in d["arms"].items())
        sessions = ", ".join(f"`{s}`" for s in d["sessions"])
        lines.append(f"| {study} | {POINTS[point][1]} | `{d['sweep']}` | {sessions} | "
                     f"{', '.join(f'`{x[:7]}`' for x in d['commits'])} | {n} |")
    for point, (_, title) in POINTS.items():
        lines += ["", f"## {title}", "", "| Study | Arm | Process CPU | ÷ control | Decoder | NPU workers | "
                  "Preprocessing | Rest | Threads' sum | Remainder | Decoder's share | Largest group (share) |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        names = dict(GROUPS)
        for study, _, arms in STUDIES:
            d = c[(study, point)]
            control = summary(d["arms"][arms[0]])["process"][1]
            for arm in arms:
                s = summary(d["arms"][arm])
                r = "control" if arm == arms[0] else f(ratio(s["process"][1], control), 3)
                top = largest(s)
                top = f"{names[top].split(' (')[0]} ({f(100 * ratio(s[top][1], s['process'][1]), 1)}%)" if top else "–"
                lines.append(f"| {study} | {arm} | {mean_sd(s['process'])} | {r} | "
                             + " | ".join(mean_sd(s[g]) for g, _ in GROUPS)
                             + f" | {mean_sd(s['threads'])} | {mean_sd(s['remainder'])} | "
                             f"{f(100 * ratio(s['dec'][1], s['process'][1]), 1)}% | {top} |")
        lines += ["", "Change against the session's control, mean − mean (s):", "",
                  "| Study | Step | Process CPU | Decoder | NPU workers | Preprocessing | Rest | Remainder |",
                  "|---|---|---|---|---|---|---|---|"]
        for study, _, arms in STUDIES:
            d = c[(study, point)]
            base = summary(d["arms"][arms[0]])
            for arm in arms[1:]:
                s = summary(d["arms"][arm])
                lines.append(f"| {study} | {arms[0]} → {arm} | "
                             + " | ".join(f"{s[k][1] - base[k][1]:+.2f}" if s[k][0] and base[k][0] else "–"
                                          for k in ("process",) + tuple(g for g, _ in GROUPS) + ("remainder",)) + " |")
        cs = [summary(c[(study, point)]["arms"]["C"])["process"][1] for study, _, _ in STUDIES]
        lines += ["", "C's process CPU in the three sessions (not one scale): "
                  + ", ".join(f"{study} {f(x)} s" for (study, _, _), x in zip(STUDIES, cs))
                  + f"; (max − min) / min = {f(100 * spread(cs), 1)}%."]
        bs = [summary(c[(study, point)]["arms"]["B"])["process"][1] for study, _, arms in STUDIES if "B" in arms]
        lines.append(f"B's in M1 and M1.1: {', '.join(f(x) + ' s' for x in bs)}; (max − min) / min = "
                     f"{f(100 * spread(bs), 1)}%.")
    lines += ["", "## Against the published values", "",
              "| Study | Point | Arm | Value | Published | This tool | Match | Source |",
              "|---|---|---|---|---|---|---|---|"]
    labels = dict(GROUPS, process="Process CPU")
    for study, point, arm, k, published, mine in checks(c):
        lines.append(f"| {study} | {POINTS[point][0]} ch | {arm} | {labels[k]} | {published} | {mine} | "
                     f"{'yes' if mine == published else 'NO'} | {SOURCES[study]} |")
    return "\n".join(lines) + "\n"


def checks(c):
    """[(study, point, arm, key, published, this tool's value written as the published one is)]."""
    out = []
    for (study, point, arm), values in PUBLISHED.items():
        s = summary(c[(study, point)]["arms"][arm])
        out += [(study, point, arm, k, published, as_published(published, s[k]) if s[k][0] else "–")
                for k, published in values.items()]
    return out


# ---------------------------------------------------------------- the figure

FIG_W, FIG_H, AXES_H = 9.6, 8.9, 2.55  # inches: the figure, and each row's panels
LINE_IN = 7.5 / 72 * 1.25  # one line of the segments' 7.5 pt text


def figure(c, path):
    """Rows: the two points; columns: the three sessions, each its own panel, sharing the row's scale. A bar
    is an arm's threads by group, its value in s and its share of process CPU inside each segment that fits;
    above it, its process CPU (mean ± sd) and the ratio of that mean to the control's. The file carries no
    date or version, as tools/m1_plots.py writes its figures."""
    plots.style()
    fig, axes = plt.subplots(len(POINTS), len(STUDIES), figsize=(FIG_W, FIG_H), sharey="row", squeeze=False,
                             gridspec_kw={"width_ratios": [len(arms) for _, _, arms in STUDIES]})
    fig.subplots_adjust(left=0.075, right=0.99, top=1 - 1.5 / FIG_H, bottom=1 - 7.95 / FIG_H, wspace=0.1,
                        hspace=1.35 / AXES_H)
    for row, (point, (_, title)) in enumerate(POINTS.items()):
        sums = {(study, arm): summary(c[(study, point)]["arms"][arm]) for study, _, arms in STUDIES for arm in arms}
        y_max = 1.24 * max([s["process"][1] for s in sums.values() if s["process"][0]], default=1.0)
        for col, (study, _, arms) in enumerate(STUDIES):
            ax, d = axes[row][col], c[(study, point)]
            control = sums[(study, arms[0])]["process"][1]
            for x, arm in enumerate(arms):
                s, bottom = sums[(study, arm)], 0.0
                if not s["process"][0]:
                    continue
                for g, label in GROUPS:
                    h = s[g][1]
                    ax.bar(x, h, bottom=bottom, width=0.64, color=COLOURS[g], edgecolor=plots.SURFACE, linewidth=1.5,
                           label=label if x == 0 else None, zorder=3)
                    lines = int(h / y_max * AXES_H / LINE_IN)  # lines of text the segment has room for
                    if lines >= 1:
                        share = f"\n({100 * h / s['process'][1]:.0f}%)" if lines >= 2 else ""
                        ax.text(x, bottom + h / 2, f"{h:.2f}{share}", ha="center", va="center", fontsize=7.5,
                                color=plots.INK if g == "rest" else "#ffffff", zorder=4)
                    bottom += h
                tag = "control" if x == 0 else f"{ratio(s['process'][1], control):.3f} × {arms[0]}"
                ax.text(x, bottom + 0.012 * y_max, f"{mean_sd(s['process'])} s\n{tag}", ha="center", va="bottom",
                        fontsize=7.5, color=plots.INK, zorder=4)
            ax.set_xticks(range(len(arms)))
            ax.set_xticklabels(arms, fontsize=9, color=plots.INK)
            ax.set_xlim(-0.6, len(arms) - 0.4)
            ax.set_ylim(0, y_max)
            ax.set_title(f"{study}, session {', '.join(x[:16] for x in d['sessions'])}\ncode "
                         + ", ".join(x[:7] for x in d["commits"]), loc="left", fontsize=8.5)
        axes[row][0].set_ylabel("CPU s in the 120 s window")
        fig.text(0.01, axes[row][0].get_position().y1 + 0.5 / FIG_H, title, ha="left", va="bottom", fontsize=10,
                 fontweight="bold", color=plots.INK)
    cs = {p: [summary(c[(study, p)]["arms"]["C"])["process"][1] for study, _, _ in STUDIES] for p in POINTS}
    remainder = max(abs(summary(r)["remainder"][1]) for d in c.values() for r in d["arms"].values() if r)
    note = ("Exploratory, computed after the fact. Each panel is one session with its own binary (its code commit in "
            "its title): compare bars within a panel only. C's process CPU in M1 / M1.1 / M2: "
            + "; ".join(f"{POINTS[p][0]} channels {' / '.join(f'{x:.2f}' for x in v)} s ({100 * spread(v):.1f}% apart)"
                        for p, v in cs.items())
            + ". M2's main condition staggers the channels' sampling. A bar is the sum of the threads in threads.csv "
            f"(those alive at the window's end); it differs from the process CPU above it by at most {remainder:.2f} s "
            "on average. In a segment: CPU s (share of process CPU). Mean ± sample sd of 3 runs; ratios of the means.")
    fig.suptitle("Where the CPU goes, from B to Cp, by thread group", x=0.01, y=1 - 0.1 / FIG_H, ha="left",
                 va="top", fontsize=10.5, fontweight="bold", color=plots.INK)
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper left", bbox_to_anchor=(0.005, 1 - 0.4 / FIG_H), ncol=len(labels),
               fontsize=8.5, labelcolor=plots.INK)
    fig.text(0.01, 0.08 / FIG_H, textwrap.fill(note, 170), ha="left", va="bottom", fontsize=7.5, color=plots.INK_2)
    with open(path, "w", encoding="utf-8", newline="\n") as out:  # the same bytes on every OS
        fig.savefig(out, format="svg", metadata={"Date": None, "Creator": None})
    plt.close(fig)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out")
    ap.add_argument("--svg", default="docs/notes/cpu_breakdown/cpu_breakdown.svg")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    c = collect(Path(__file__).resolve().parent.parent)
    text = report(c)
    for p in filter(None, (args.out, args.svg)):
        Path(p).parent.mkdir(parents=True, exist_ok=True)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        print(text, end="")
    figure(c, Path(args.svg))
    bad = [x for x in checks(c) if x[4] != x[5]]
    for study, point, arm, k, published, mine in bad:
        print(f"{study} {point} {arm} {k}: published {published}, this tool {mine}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
