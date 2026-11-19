#!/usr/bin/env python3
"""M3's comparison (prereg/M3.md §2 and §6): M1's B and C at H1's point, reproduced before and after the board's
reset, against M1's own runs.

The runs that count are, per repetition and variant, the valid run of the session that completed the repetition
(an invalid ×2 run counts as missing), as tools/stream_session.py's sweep_state completes them:
  - M1: sweep main, results/comparison/20261103T073439Z_main, at 8 channels;
  - M3: the m3_before and m3_after sessions in results/m3/ (or the one given with --session).
Per variant, the mean and sample sd of process CPU (PROTOCOL §7.1) and of the runs' p50 latency (§7.3), and
M3 §2's criterion: (M3 − M1) / M1 within ±10% for both metrics of both variants, from the unrounded means.
"reproduced" when all four hold, "not reproduced" when one does not, "not determined" when a variant has no
valid run. The other metrics of §6 are listed without a criterion.

With the sessions, it lists each session's inventory diff from docs/notes/inventory_m1.txt and the keys that
M3 §4 pins (none differ in a session that ran), and the packages whose version differs between the board
before and after its reset (each m3 session's dpkg.tsv).

Standard library only, so that it also runs on a freshly set-up board (scripts/reproduce_m1.sh).
Usage, from the repository:
  python3 tools/m3_report.py [--out FILE]        every M3 session, as Markdown
  python3 tools/m3_report.py --session DIR       one session (an m3 sweep, or its smoke test), as text
Exit 0, or 2 when a session cannot be read.
"""
import argparse
import csv
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stream_session as ss  # noqa: E402

M1_SESSION = Path("results/comparison/20261103T073439Z_main")  # sweep main of M1-6, which holds H1's point
CHANNELS, VARIANTS = 8, ("B", "C")
THRESHOLD = 0.10  # M3 §2
CRITERION = (("process_cpu_s", "Process CPU", "s", 3), ("latency_p50_ms", "Latency p50", "ms", 3))
OTHERS = (("system_cpu_total_s", "System CPU, /proc/stat", "s", 3),
          ("schedstat_cpu_total_s", "System CPU, /proc/schedstat", "s", 3),
          ("latency_p99_ms", "Latency p99", "ms", 3),
          ("mpp_hold_p50_ms", "MPP hold p50", "ms", 3),
          ("drop_rate", "Drop rate", "", 4),
          ("decoder_drops", "Decoder drops", "", 0),
          ("temp_max_mc", "Highest temperature", "m°C", 0))


def counting_runs(session_dirs, phase, channels=CHANNELS, variants=VARIANTS):
    """{(repetition, variant): run directory or None} for the complete repetitions at channels: the valid run
    of the session that completed the repetition, None for an invalid ×2 run."""
    records = [r for r in ss.load_records(session_dirs, (phase,)) if r["channels"] == channels]
    complete, _ = ss.sweep_state(records, variants)
    by_name = {Path(d).name: Path(d) for d in session_dirs}
    out = {}
    for ch, rep in sorted(complete):
        recs = [r for r in records if r["repetition"] == rep]
        for s in dict.fromkeys(r["session"] for r in recs):
            mine = [r for r in recs if r["session"] == s]
            if all(any(r["variant"] == v and (r["valid"] or r["attempt"] == 2) for r in mine) for v in variants):
                for v in variants:
                    valid = [r for r in mine if r["variant"] == v and r["valid"]]
                    out[(rep, v)] = by_name[s] / valid[-1]["name"] if valid else None
                break
    return out


def run_values(run_dir):
    """{key: float} of a run's summary.csv (its key,value table)."""
    _, rows = ss.read_csv(Path(run_dir) / "summary.csv")
    out = {}
    for r in rows:
        try:
            out[r["key"]] = float(r["value"])
        except ValueError:
            pass
    return out


def per_variant(runs):
    """{variant: [summary values of each counting run]} of counting_runs' result."""
    out = {v: [] for v in VARIANTS}
    for (_, v), d in sorted(runs.items()):
        if d is not None:
            out[v].append(run_values(d))
    return out


def mean_sd(values):
    """(mean, sample sd or None, n) of a list of floats; (None, None, 0) for none."""
    if not values:
        return None, None, 0
    return statistics.fmean(values), (statistics.stdev(values) if len(values) > 1 else None), len(values)


def relative(m3, m1):
    return None if m3 is None or m1 in (None, 0) else (m3 - m1) / m1


def complete_repetitions(runs):
    """How many repetitions counting_runs' result has (complete ones only)."""
    return len({rep for rep, _ in runs})


def cv(values):
    """The coefficient of variation (sample sd / mean) of PROTOCOL §9.3's flag, or None below 2 values."""
    mean, sd, _ = mean_sd(values)
    return None if sd is None or not mean else sd / mean


def criterion(m3_values, m1_values):
    """M3 §2: ([(variant, key, M1 mean, M3 mean, relative difference, within)], verdict)."""
    cells, verdict = [], "reproduced"
    for v in VARIANTS:
        for key, *_ in CRITERION:
            m1 = mean_sd([r[key] for r in m1_values[v] if key in r])[0]
            m3 = mean_sd([r[key] for r in m3_values[v] if key in r])[0]
            rel = relative(m3, m1)
            within = None if rel is None else abs(rel) <= THRESHOLD
            cells.append((v, key, m1, m3, rel, within))
            if within is None:
                verdict = "not determined" if verdict != "not reproduced" else verdict
            elif not within:
                verdict = "not reproduced"
    return cells, verdict


def fmt(x, digits=3, unit=""):
    if x is None:
        return "–"
    return f"{x:.{digits}f}" + (f" {unit}" if unit else "")


def pct(x):
    return "–" if x is None else f"{100 * x:+.2f}%"


def m3_sessions(root=Path("results/m3")):
    """{sweep: [session directories]} of the protocol sessions in results/m3/."""
    return {s: ss.sweep_sessions(s) if root == Path("results/m3") else
            sorted(d for d in root.glob(f"*_{s}") if d.is_dir()) for s in ss.M3_SWEEPS}


def dpkg_versions(path):
    """{package: version} of a dpkg.tsv (Package, Version, Status) for installed packages."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.reader(f, delimiter="\t"):
            if len(row) >= 3 and row[2].startswith("ii"):
                out[row[0]] = row[1]
    return out


def dpkg_diff(before, after):
    """(added, removed, changed) between two {package: version}: sorted lists."""
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted((p, before[p], after[p]) for p in set(before) & set(after) if before[p] != after[p])
    return added, removed, changed


def changed_lines(diff_path):
    if not diff_path.exists():
        return 0
    return sum(1 for line in diff_path.read_text(encoding="utf-8").splitlines()
               if line[:1] in "+-" and line[:3] not in ("+++", "---"))


def session_text(session):
    """One session against M1, as text (scripts/reproduce_m1.sh prints it)."""
    session = Path(session)
    phase = next((s for s in ss.M3_SWEEPS if session.name.endswith(s) or session.name.endswith(s + "_smoke")), None)
    if phase is None:
        raise ValueError(f"{session} is not an m3 session")
    smoke = session.name.endswith("_smoke")
    # a protocol sweep can span sessions (PROTOCOL §9.3): read every session of it up to this one
    sessions = [session] if smoke else sorted(
        d for d in session.parent.glob(f"*_{phase}")
        if d.is_dir() and d.name <= session.name and ss.re.fullmatch(ss.SESSION_NAME + phase, d.name))
    m1 = per_variant(counting_runs([M1_SESSION], "main"))
    runs = counting_runs(sessions, phase)
    m3 = per_variant(runs)
    cells, verdict = criterion(m3, m1)
    done = complete_repetitions(runs)
    lines = [f"M3 {phase}: {', '.join(str(d) for d in sessions)}",
             f"repetitions complete: {done} of {ss.REPETITIONS}; runs that count: B {len(m3['B'])}, C {len(m3['C'])}"
             f" (M1: B {len(m1['B'])}, C {len(m1['C'])})"]
    if smoke:  # its windows are not M1's: no comparison
        for v in VARIANTS:
            for key, lab, unit, _ in CRITERION:
                mean, sd, n = mean_sd([r[key] for r in m3[v] if key in r])
                lines.append(f"  {v} {lab}: {fmt(mean, 3, unit)} (sd {fmt(sd, 3)}, n {n})")
        lines.append("smoke test: its windows are not M1's, so it is not compared, and it counts for nothing")
        lines.append(f"inventory: {changed_lines(session / 'inventory.diff')} changed lines against inventory_m1.txt")
        return "\n".join(lines)
    for v, key, a, b, rel, within in cells:
        label = dict((k, (lab, unit)) for k, lab, unit, _ in CRITERION)[key]
        lines.append(f"  {v} {label[0]}: M1 {fmt(a, 3, label[1])}, here {fmt(b, 3, label[1])}, {pct(rel)}"
                     f" ({'within' if within else 'outside' if within is not None else 'no value'} ±10%)")
    lines.append(outcome_text(phase, verdict, done))
    lines.append(f"inventory: {changed_lines(session / 'inventory.diff')} changed lines against inventory_m1.txt")
    return "\n".join(lines)


def outcome_text(phase, verdict, done):
    """M3 §2 judges m3_after only, once its sweep is complete; m3_before is descriptive (§6)."""
    if phase == "m3_before":
        return f"m3_before is descriptive (M3 §6): it is not judged; by §2's arithmetic it would be: {verdict}"
    if done < ss.REPETITIONS:
        return f"M3 §2: no outcome yet ({done} of {ss.REPETITIONS} repetitions complete)"
    return f"M3 §2: {verdict}"


def report():
    """Every M3 session, as Markdown: the criterion, the other metrics, per-run values, the inventories and the
    packages."""
    sessions = m3_sessions()
    m1 = per_variant(counting_runs([M1_SESSION], "main"))
    runs = {s: counting_runs(d, s) if d else {} for s, d in sessions.items()}
    m3 = {s: per_variant(r) for s, r in runs.items()}
    out = ["# M3 tables", "",
           "Generated by `tools/m3_report.py` from `results/m3/` and M1's sweep main; nothing here is edited by hand.",
           "", "## Sessions", "", "| Sweep | Sessions |", "|---|---|"]
    out += [f"| {s} | {', '.join(f'`{d.name}`' for d in ds) or 'none'} |" for s, ds in sessions.items()]
    for s in ss.M3_SWEEPS:
        cells, verdict = criterion(m3[s], m1)
        title = "M3 §2" if s == "m3_after" else "Descriptive (M3 §6)"
        out += ["", f"## {title}: {s} against M1", "", "| Variant | Metric | M1 | M3 | (M3 − M1) / M1 | Within ±10% |",
                "|---|---|---|---|---|---|"]
        for v, key, a, b, rel, within in cells:
            lab, unit = next((lab, unit) for k, lab, unit, _ in CRITERION if k == key)
            out.append(f"| {v} | {lab} | {fmt(a, 3, unit)} | {fmt(b, 3, unit)} | {pct(rel)} | "
                       f"{'yes' if within else 'no' if within is not None else '–'} |")
        out += ["", f"**{outcome_text(s, verdict, complete_repetitions(runs[s]))}.**"]
    out += ["", "## CV of process CPU (PROTOCOL §9.3's flag above 10%)", "", "| Variant | M1 | m3_before | m3_after |",
            "|---|---|---|---|"]
    for v in VARIANTS:
        out.append(f"| {v} | " + " | ".join(pct(cv([r['process_cpu_s'] for r in vals[v]])).lstrip("+")
                                          for vals in (m1, m3["m3_before"], m3["m3_after"])) + " |")
    out += ["", "## Every metric, mean ± sd (n)", "", "| Metric | Variant | M1 | m3_before | m3_after |",
            "|---|---|---|---|---|"]
    for key, lab, unit, digits in CRITERION + OTHERS:
        for v in VARIANTS:
            cols = []
            for vals in (m1[v], m3["m3_before"][v], m3["m3_after"][v]):
                mean, sd, n = mean_sd([r[key] for r in vals if key in r])
                cols.append("–" if mean is None else f"{fmt(mean, digits)} ± {fmt(sd, digits)} ({n})")
            out.append(f"| {lab}{' (' + unit + ')' if unit else ''} | {v} | " + " | ".join(cols) + " |")
    out += ["", "## Runs", "", "| Sweep | Run | Process CPU (s) | Latency p50 (ms) | Latency p99 (ms) |", "|---|---|---|---|---|"]
    for s, dirs in [("M1 main", [M1_SESSION])] + [(s, sessions[s]) for s in ss.M3_SWEEPS]:
        phase = "main" if s == "M1 main" else s
        for (rep, v), d in sorted(counting_runs(dirs, phase).items()) if dirs else []:
            r = run_values(d) if d else {}
            out.append(f"| {s} | {d.name if d else f'r{rep} {v}: invalid ×2'} | {fmt(r.get('process_cpu_s'))} | "
                       f"{fmt(r.get('latency_p50_ms'))} | {fmt(r.get('latency_p99_ms'))} |")
    out += ["", "## Inventories (M3 §4)", "", "| Session | Lines changed against `inventory_m1.txt` | Pinned keys that differ |",
            "|---|---|---|"]
    for s in ss.M3_SWEEPS:
        for d in sessions[s]:
            pinned = d / "inventory_pinned.txt"
            out.append(f"| `{d.name}` | {changed_lines(d / 'inventory.diff')} | "
                       f"{pinned.read_text(encoding='utf-8').strip() if pinned.exists() else 'none'} |")
    before = [d / "dpkg.tsv" for d in sessions["m3_before"] if (d / "dpkg.tsv").exists()]
    after = [d / "dpkg.tsv" for d in sessions["m3_after"] if (d / "dpkg.tsv").exists()]
    if before and after:
        added, removed, changed = dpkg_diff(dpkg_versions(before[-1]), dpkg_versions(after[-1]))
        out += ["", "## Packages, before against after (M3 §6)", "",
                f"{len(changed)} changed, {len(added)} only after, {len(removed)} only before.", "",
                "| Package | Before | After |", "|---|---|---|"]
        out += [f"| {p} | {a} | {b} |" for p, a, b in changed]
    return "\n".join(out) + "\n"


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--session", help="one m3 session (or its smoke test): print it against M1")
    ap.add_argument("--out", help="write the Markdown tables there")
    a = ap.parse_args(argv)
    try:
        if a.session:
            print(session_text(a.session))
            return 0
        text = report()
    except (OSError, ValueError, KeyError, ss.Stop) as e:
        print(f"m3_report: {e}", file=sys.stderr)
        return 2
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
