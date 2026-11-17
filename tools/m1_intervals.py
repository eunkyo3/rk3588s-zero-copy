#!/usr/bin/env python3
"""M1's paired differences of B against C, and the spread between sessions (exploratory, after the fact).

Issue #67, computed after every run of M1, M1.1 and M2 had been seen. No verdict changes. From the runs that
count (tools/m1_report.py), valid runs only:
  - paired differences: at every point of M1's main and auxiliary sweeps, B_r against C_r of the same
    repetition r, which share a session. Process CPU as H1's saving, (B_r - C_r) / B_r; p50 and p99 latency
    as B_r - C_r in ms, whose mean is the difference of the means that H2 compares. Each with a descriptive
    95% t-interval of the mean, as M2 reported its pairs (tools/m2_report.py's t_interval);
  - the spread between sessions: B's and C's process CPU and latency at 8 channels, main condition, and at
    5 channels x 30 fps, in M1, M1.1 and M2, with each session's code commit and sampler from the runs'
    headers;
  - repetitions: from the coefficients of variation of latency, the fewest runs whose 95% t-interval has a
    given half-width (guidance for later pre-registrations, not a registration);
  - reproduction: M1's and M2's published values that this tool's code path computes too.

Usage, from the repository: python3 tools/m1_intervals.py [--out FILE]   (Markdown)
"""
import argparse
import functools
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m1_report as rep  # noqa: E402
import m2_report as mr  # noqa: E402
import stream_session as ss  # noqa: E402

M1_SWEEPS = ("main", "aux")  # M1's sweeps of B and C with 3 repetitions
METRICS = (  # (key, label, relative): relative as H1's saving, (B_r - C_r) / B_r; else B_r - C_r
    ("process_cpu_s", "process CPU", True),
    ("latency_p50_ms", "p50 latency", False),
    ("latency_p99_ms", "p99 latency", False),
)
SESSIONS = (  # (point, channels, [(milestone, sweep)]): the sessions with B or C at the point, in time order
    ("8 channels, main condition", 8, (("M1", "main"), ("M1.1", "k4"), ("M1.1", "bdirect_main"), ("M2", "m2_main"))),
    ("5 channels, 30 fps", 5, (("M1", "aux"), ("M1.1", "bdirect_aux"), ("M2", "m2_aux"))),
)
HALF_WIDTHS = (0.02, 0.05, 0.10)  # of the mean
MOST_RUNS = 100


def pairs(a, b, key, relative):
    """{r: a_r - b_r, or (a_r - b_r) / a_r if relative}, over the repetitions both arms have a valid run of.
    a, b: {repetition: metrics}."""
    return {r: (a[r][key] - b[r][key]) / (a[r][key] if relative else 1.0) for r in sorted(a.keys() & b.keys())}


def side(lo, hi):
    """Where a descriptive interval lies: "above 0", "below 0", "spans 0", or "–" without one."""
    if math.isnan(lo) or math.isnan(hi):
        return "–"
    return "above 0" if lo > 0 else "below 0" if hi < 0 else "spans 0"


def spread(means):
    """(highest - lowest) / lowest."""
    return (max(means) - min(means)) / min(means)


@functools.lru_cache(maxsize=None)
def t_975(df):
    """Student's t, 0.975 quantile, for an integer df >= 1: the t with P(|T| <= t) = 0.95, from the closed
    forms of Abramowitz and Stegun 26.7.3 (odd df) and 26.7.4 (even df), by bisection."""
    def central(t):
        th = math.atan(t / math.sqrt(df))
        c2 = math.cos(th) ** 2
        if df % 2:  # (2/pi) (th + sin th cos th (1 + 2/3 cos^2 + 2*4/(3*5) cos^4 + ...))
            term, total = 1.0, 1.0 if df > 1 else 0.0
            for k in range(1, (df - 1) // 2):
                term *= 2 * k / (2 * k + 1) * c2
                total += term
            return 2 / math.pi * (th + math.sin(th) * math.cos(th) * total)
        term = total = 1.0  # sin th (1 + 1/2 cos^2 + 1*3/(2*4) cos^4 + ...)
        for k in range(1, df // 2):
            term *= (2 * k - 1) / (2 * k) * c2
            total += term
        return math.sin(th) * total
    lo, hi = 0.0, 1000.0
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if central(mid) < 0.95 else (lo, mid)
    return (lo + hi) / 2


def repetitions(sd, half_width, most=MOST_RUNS):
    """The fewest runs n >= 2 whose 95% t-interval of a mean, +/- t_975(n - 1) * sd / sqrt(n), is at most
    half_width on either side, sd and half_width in one unit (as shares of the mean: the CV); None above most."""
    for n in range(2, most + 1):
        if t_975(n - 1) * sd / math.sqrt(n) <= half_width:
            return n
    return None


def session_heads(root, sweep, channels):
    """[(session, code commits, stagger values)] of a sweep's sessions, from the headers of its runs at the
    count; a header without stagger, from before the option, gives "–"."""
    out = []
    for d in rep.sessions_of(root, sweep):
        heads = [ss.read_csv(d / name / "summary.csv")[0] for (_, name), row in rep.runs_rows([d]).items()
                 if int(row["channels"]) == channels and (d / name / "summary.csv").exists()]
        out.append((d.name, sorted({h.get("code_commit", "")[:7] for h in heads}),
                    sorted({h.get("stagger", "–") for h in heads})))
    return out


def collect(root):
    """{sweep: rep.collect_sweep, with "heads": session_heads at the sweep's point} for M1's sweeps and the
    sessions of SESSIONS."""
    out = {s: rep.collect_sweep(root, s) for s in M1_SWEEPS}
    for _, ch, rows in SESSIONS:
        for _, sweep in rows:
            if sweep not in out:
                out[sweep] = rep.collect_sweep(root, sweep)
            out[sweep]["heads"] = session_heads(root, sweep, ch)
    return out


def point(c, sweep, ch, arm):
    """{repetition: metrics} of an arm's valid runs at a point; {} if the sweep has no such arm."""
    return c[sweep]["data"].get(ch, {}).get(arm, {}).get("valid", {})


def stat(runs, key):
    return rep.stats([r[key] for r in runs.values()])


def reproduction(c):
    """[(value, published, source, this tool's)] for the published values this tool's code path gives."""
    def ms(runs, key, digits):
        _, mean, sd, _ = stat(runs, key)
        return f"{mr.f(mean, digits)} ± {mr.f(sd, digits)}"

    def each(values, fmt):
        return ", ".join(fmt(x) for x in values)

    b8, c8 = point(c, "main", 8, "B"), point(c, "main", 8, "C")
    b5, c5 = point(c, "aux", 5, "B"), point(c, "aux", 5, "C")
    mb, mc = stat(b8, "process_cpu_s")[1], stat(c8, "process_cpu_s")[1]
    out = [("M1, 8 ch main, B process CPU (s)", "43.283 ± 0.343", "M1.md", ms(b8, "process_cpu_s", 3)),
           ("M1, 8 ch main, C process CPU (s)", "18.560 ± 0.308", "M1.md", ms(c8, "process_cpu_s", 3)),
           ("M1, H1's saving (B − C) / B, from the means", "57.1%", "M1.md", mr.pct((mb - mc) / mb, 1)),
           ("M1, 5 ch × 30 fps, B p99 per repetition (ms)", "28.3, 23.2, 22.1", "M1.md",
            each([b5[r]["latency_p99_ms"] for r in sorted(b5)], lambda x: mr.f(x, 1))),
           ("M1, 5 ch × 30 fps, C p99 per repetition (ms)", "23.7, 23.4, 30.5", "M1.md",
            each([c5[r]["latency_p99_ms"] for r in sorted(c5)], lambda x: mr.f(x, 1))),
           ("M1.1 (bdirect_main), 8 ch main, C process CPU (s)", "19.49 ± 0.26", "M1.1.md",
            ms(point(c, "bdirect_main", 8, "C"), "process_cpu_s", 2)),
           ("M2 (m2_main), 8 ch main, C process CPU (s)", "18.737 ± 0.170", "M2.md",
            ms(point(c, "m2_main", 8, "C"), "process_cpu_s", 3)),
           ("M1, 5 ch × 30 fps, C process CPU (s)", "35.68 ± 0.49", "M2.md", ms(c5, "process_cpu_s", 2)),
           ("M1.1 (bdirect_aux), 5 ch × 30 fps, C process CPU (s)", "37.45 ± 1.41", "M1.1.md",
            ms(point(c, "bdirect_aux", 5, "C"), "process_cpu_s", 2)),
           ("M2 (m2_aux), 5 ch × 30 fps, C process CPU (s)", "37.280 ± 0.528", "M2.md",
            ms(point(c, "m2_aux", 5, "C"), "process_cpu_s", 3))]
    for hyp, sweep, ch, saving, per_rep, interval in (
            ("M2-H1", "m2_main", 8, "6.07%", "8.76%, 6.23%, 3.21%", "-0.83% to 12.97%"),
            ("M2-H2", "m2_aux", 5, "14.73%", "13.57%, 15.36%, 15.23%", "12.24% to 17.20%")):
        arm_c, arm_cp = point(c, sweep, ch, "C"), point(c, sweep, ch, "Cp")
        m_c, m_cp = stat(arm_c, "process_cpu_s")[1], stat(arm_cp, "process_cpu_s")[1]
        p = pairs(arm_c, arm_cp, "process_cpu_s", True)
        _, lo, hi = mr.t_interval(list(p.values()))
        out += [(f"{hyp}, (C − Cp) / C, from the means", saving, "M2.md", mr.pct((m_c - m_cp) / m_c, 2)),
                (f"{hyp}, (C_r − Cp_r) / C_r", per_rep, "M2.md", each(p.values(), lambda x: mr.pct(x, 2))),
                (f"{hyp}, 95% t-interval", interval, "M2.md", f"{mr.pct(lo, 2)} to {mr.pct(hi, 2)}")]
    return out


def paired_section(c):
    lines = ["## Paired differences of B against C", "",
             "B_r against C_r of the same repetition r. Both ran in the same session, in alternating order (B→C, "
             "C→B, B→C). Process CPU as H1's saving, (B_r − C_r) / B_r. Latency as B_r − C_r in ms, positive where "
             "C is lower; its mean is the difference of the means that H2 compares. Each with a descriptive 95% "
             f"t-interval of the mean of the differences (`tools/m2_report.py`'s `t_interval`; t = {mr.T_975[2]:.3f} "
             "for 3 pairs). Reported, not tested.",
             ""]
    for sweep in M1_SWEEPS:
        data = c[sweep]["data"]
        for key, label, relative in METRICS:
            unit, digits = ("s", 3) if relative else ("ms", 1)
            show = (lambda x: mr.pct(x, 1)) if relative else (lambda x: mr.f(x, 1))  # noqa: E731
            lines += [f"### {sweep}: {label}", "",
                      f"| Channels | B ({unit}) | C ({unit}) | "
                      + ("(B − C) / B, from the means | (B_r − C_r) / B_r" if relative else "B_r − C_r (ms)")
                      + ": r1, r2, r3 | Mean | 95% t-interval | Interval |",
                      "|---|---|---|" + ("---|" if relative else "") + "---|---|---|---|"]
            sides = {}
            for ch in sorted(data):
                b, cc = data[ch]["B"]["valid"], data[ch]["C"]["valid"]
                p = pairs(b, cc, key, relative)
                mean, lo, hi = mr.t_interval(list(p.values()))
                sides.setdefault(side(lo, hi), []).append(ch)
                means = ""
                if relative:
                    mb, mc = stat(b, key)[1], stat(cc, key)[1]
                    means = f" {mr.pct((mb - mc) / mb, 1)} |"
                lines.append(f"| {ch} | {mr.mean_sd(b, key, digits)} | {mr.mean_sd(cc, key, digits)} |{means} "
                             f"{', '.join(show(x) for x in p.values())} | {show(mean)} | {show(lo)} to {show(hi)} | "
                             f"{side(lo, hi)} |")
            lines += ["", "Intervals: " + "; ".join(f"{s} at {', '.join(map(str, chs))} ch"
                                                    for s, chs in sorted(sides.items())) + ".", ""]
    lines += ["**H3:** its search ran one run per count and variant, so it has no interval. Its confirmation ran "
              "32 channels three times per variant to check that the count is sustained; the main sweep covers "
              "32 channels with 3 repetitions, above.", ""]
    return lines


def spread_section(c):
    lines = ["## The spread between sessions", "",
             "B and C at the same point in different sessions. Mean ± sample sd (n). The sessions differ in their "
             "binary (code commit), and M2's main session in its sampler (stagger on). M2 ran C and Cp, no B; "
             "k4 ran B and Bk4, no C. Latency spreads leave out sessions with the stagger on.", ""]
    for title, ch, rows in SESSIONS:
        lines += [f"### {title}", "",
                  "| Milestone | Session | Code | Stagger | B process CPU (s) | C process CPU (s) | B p50 (ms) | "
                  "C p50 (ms) | B p99 (ms) | C p99 (ms) |", "|---|---|---|---|---|---|---|---|---|---|"]
        for milestone, sweep in rows:
            heads = c[sweep]["heads"]
            cells = []
            for key, digits in (("process_cpu_s", 3), ("latency_p50_ms", 1), ("latency_p99_ms", 1)):
                for arm in ("B", "C"):
                    runs = point(c, sweep, ch, arm)
                    cells.append(mr.mean_sd(runs, key, digits) if runs else "–")
            lines.append(f"| {milestone} | {', '.join(f'`{h[0]}`' for h in heads)} | "
                         f"{', '.join(x for h in heads for x in h[1])} | {', '.join(x for h in heads for x in h[2])} | "
                         + " | ".join(cells) + " |")
        lines.append("")
        for key, label, digits in (("process_cpu_s", "process CPU", 3), ("latency_p50_ms", "p50 latency", 1),
                                   ("latency_p99_ms", "p99 latency", 1)):
            for arm in ("B", "C"):
                found = [(milestone, sweep, stat(point(c, sweep, ch, arm), key)) for milestone, sweep in rows
                         if point(c, sweep, ch, arm)
                         and (key == "process_cpu_s" or all(h[2] != ["on"] for h in c[sweep]["heads"]))]
                if len(found) < 2:
                    continue
                means = [s[1] for _, _, s in found]
                lines.append(f"- **{arm}, {label}:** "
                             + ", ".join(f"{mr.f(s[1], digits)} ({m} `{sw}`)" for m, sw, s in found)
                             + f". The highest mean is {mr.pct(spread(means), 1)} above the lowest. Within these "
                             f"sessions, the CV is at most {mr.pct(max(s[3] for _, _, s in found), 1)}.")
        lines.append("")
    return lines


def repetitions_section(c):
    lines = ["## Repetitions for a given interval (guidance)", "",
             f"The fewest runs n whose 95% t-interval of a mean, ± t(n − 1) × CV / √n, is at most h of the mean, "
             f"with the CV observed here taken as the true one; more than {MOST_RUNS} is shown as \"> {MOST_RUNS}\". "
             "For the difference of two arms with that CV each, paired runs and independent errors, the sd of the "
             "difference is about √2 × CV of one arm's mean. Each CV comes from 3 runs, so it is rough itself.", "",
             "### Coefficients of variation in M1", "",
             "| Sweep | Channels | Process CPU, B | Process CPU, C | p50, B | p50, C | p99, B | p99, C |",
             "|---|---|---|---|---|---|---|---|"]
    cv = {key: [] for key, _, _ in METRICS}
    for sweep in M1_SWEEPS:
        for ch in sorted(c[sweep]["data"]):
            cells = []
            for key, _, _ in METRICS:
                for arm in ("B", "C"):
                    x = stat(point(c, sweep, ch, arm), key)[3]
                    cv[key].append(x)
                    cells.append(mr.pct(x, 1))
            lines.append(f"| {sweep} | {ch} | " + " | ".join(cells) + " |")
    m2 = {sweep: [stat(point(c, sweep, ch, arm), "latency_p99_ms")[3] for arm in ("C", "Cp")]
          for sweep, ch in (("m2_main", 8), ("m2_aux", 5))}
    rows = [("p99 latency, median over M1's points and arms", statistics.median(cv["latency_p99_ms"])),
            ("p99 latency, largest in M1", max(cv["latency_p99_ms"])),
            ("p50 latency, median over M1's points and arms", statistics.median(cv["latency_p50_ms"])),
            ("p50 latency, largest in M1", max(cv["latency_p50_ms"])),
            ("p99 latency, M2's 8 ch main (stagger on), larger of C and Cp", max(m2["m2_main"])),
            ("p99 latency, M2's 5 ch × 30 fps, larger of C and Cp", max(m2["m2_aux"])),
            ("process CPU, largest in M1 (for comparison)", max(cv["process_cpu_s"]))]
    heads = [f"{mr.pct(h, 0)}" for h in HALF_WIDTHS]
    above = sum(x > rep.CV_FLAG for x in cv["latency_p99_ms"])
    lines += ["", f"M1 has {len(cv['latency_p99_ms'])} points and arms (main and aux sweeps, B and C). p99 latency's "
              f"CV is above {mr.pct(rep.CV_FLAG, 0)} at {above} of them.", "",
              "### Runs for a half-width h of the mean", "",
              "| Variation | CV | One arm, h = " + ", ".join(heads)
              + " | Difference (√2 × CV), h = " + ", ".join(heads) + " |",
              "|---|---|---|---|"]

    def runs(sd):
        return ", ".join(f"> {MOST_RUNS}" if n is None else str(n)
                         for n in (repetitions(sd, h) for h in HALF_WIDTHS))
    for label, x in rows:
        lines.append(f"| {label} | {mr.pct(x, 1)} | {runs(x)} | {runs(math.sqrt(2) * x)} |")
    return lines + [""]


def report(c):
    lines = ["# M1: paired differences and the spread between sessions (exploratory)", "",
             "Computed after the fact (issue #67), once every run of M1, M1.1 and M2 had been seen. Nothing here "
             "changes a verdict. Valid runs only, per 120 s window. Method: `tools/m1_intervals.py`. The note is "
             "[m1_intervals.md](m1_intervals.md).", "",
             "## Reproduction", "",
             "Published values that this tool's code path computes too.", "",
             "| Value | Published | Source | This tool | Same |", "|---|---|---|---|---|"]
    for what, published, source, mine in reproduction(c):
        lines.append(f"| {what} | {published} | [{source}](../report/{source}) | {mine} | "
                     f"{'yes' if mine == published else 'NO'} |")
    lines.append("")
    lines += paired_section(c) + spread_section(c) + repetitions_section(c)
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
