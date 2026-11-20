#!/usr/bin/env python3
"""prereg/M5.md §8 step 2, "Order check": the start times of M5's records against the registration's
reference times.

  - every run of kind m5 starts after freeze-m5;
  - every run that names prereg-m5 (prereg_m5_commit other than none) starts after prereg-m5, and every run
    that names freeze-m5 starts after freeze-m5;
  - every run whose header names an amendment with a Release amend-A<n> starts after that Release;
  - the commit a header gives for prereg-m5 or freeze-m5 is that stage's tag commit;
  - a session directory <UTC>_m5 holds runs of kind m5 only, and no other directory holds one.

A run is a directory with an m5_run.csv, whose "# key: value" header gives its start time, run kind,
amendment and commits (lib/measure/m5_session.py). A session's m5_session.csv and idle_baseline.csv, and
the m5_detections.csv of lib/measure/m5_detections.py, are checked the same way. bench_main's own files of a B or C run have its own header, of run kind check; they
are the run's, and take the run's start.

The reference times are the Releases' created_at, and the tag commits the Releases' commits, as the
Registration table of docs/notes/env.md records them. An amendment has a Release when its entry in
prereg/AMENDMENTS.md says "Release: amend-A<n>". The registered tools/check_prereg_order.py reads both
files; this script uses its two readers and its time parser, imported and unchanged, and nothing else of it.

Usage: python3 lib/measure/m5_order.py [--results DIR] [--env-md FILE] [--amendments FILE]
Exit code 0 when every record passes, 1 when one does not, 2 for unreadable input.
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import check_prereg_order as cpo  # noqa: E402  (reference_times, amendments, parse_time)

KINDS = {"m5", "check"}
STAGES = {"prereg_m5_commit": "prereg-m5", "freeze_m5_commit": "freeze-m5"}
SWEEP_SESSION = re.compile(r"\d{8}T\d{6}Z_m5")


def header_of(path):
    """{key: value} of the "# key: value" lines at the top of a file."""
    out = {}
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            if not line.startswith("# "):
                break
            k, sep, v = line[2:].rstrip("\n").partition(": ")
            if sep:
                out[k] = v
    return out


def check(results, refs, commits, names, released):
    """The problems of every record under results."""
    problems = []
    for tag in sorted(released):
        if refs.get(f"amend-{tag}") is None:
            problems.append(f"amend-{tag}: AMENDMENTS gives it a Release, but env.md has no created_at for it")
    files = sorted(p for p in results.rglob("*.csv")
                   if p.name in ("m5_run.csv", "m5_session.csv", "m5_detections.csv", "idle_baseline.csv"))
    for p in files:
        h = header_of(p)
        where = p.relative_to(results).as_posix()
        kind = h.get("run_kind")
        if "start_time_utc" not in h or kind not in KINDS:
            problems.append(f"{where}: no start_time_utc, or a run kind other than {', '.join(sorted(KINDS))}: {kind!r}")
            continue
        start = cpo.parse_time(h["start_time_utc"], where)
        session = p.relative_to(results).parts[0]
        if bool(SWEEP_SESSION.fullmatch(session)) != (kind == "m5"):
            problems.append(f"{where}: run kind {kind} in the session {session} (a sweep's session is <UTC>_m5, and "
                            "holds kind m5 only)")
        after = set()
        if kind == "m5":
            after.add("freeze-m5")
        if p.name != "idle_baseline.csv":  # the registered tool writes that file's header, without M5's commits
            for field, stage in STAGES.items():
                v = h.get(field)
                if v is None:
                    problems.append(f"{where}: no {field} in its header")
                elif v != "none":
                    after.add(stage)
                    if commits.get(stage) != v:
                        problems.append(f"{where}: {field} is {v}, and {stage}'s tag commit is {commits.get(stage)}")
                elif kind == "m5" or stage == "prereg-m5":
                    problems.append(f"{where}: {field} is none")
        for stage in sorted(after):
            if refs.get(stage) is None:
                problems.append(f"{where}: starts after {stage}, which has no created_at in env.md")
            elif start <= refs[stage]:
                problems.append(f"{where}: starts {h['start_time_utc']}, not after {stage}")
        amendment = h.get("amendment")
        if amendment and amendment != "none":
            if amendment not in names:
                problems.append(f"{where}: names {amendment}, which AMENDMENTS does not have")
            release = refs.get(f"amend-{amendment}")
            if amendment in released and release is not None and start <= release:
                problems.append(f"{where}: names {amendment} but starts {h['start_time_utc']}, not after "
                                f"amend-{amendment}")
    # A session with no header at all would be checked by nothing. (A run directory without m5_run.csv is
    # what a session's death leaves; its session's other records carry the times.)
    for d in sorted(p for p in results.iterdir() if p.is_dir()):
        if not any(f.parts[0] == d.name for f in (p.relative_to(results) for p in files)):
            problems.append(f"{d.name}: a session without any record that has M5's header")
    return problems, len(files)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", type=Path, default=ROOT / "lib" / "measure" / "results")
    ap.add_argument("--env-md", type=Path, default=ROOT / "docs" / "notes" / "env.md")
    ap.add_argument("--amendments", type=Path, default=ROOT / "prereg" / "AMENDMENTS.md")
    args = ap.parse_args(argv)
    try:
        refs, commits = cpo.reference_times(args.env_md)
        names, released = cpo.amendments(args.amendments)
        if not args.results.is_dir():
            print(f"m5_order: no records yet ({args.results})")
            return 0
        problems, n = check(args.results, refs, commits, names, released)
    except (cpo.InputError, OSError, UnicodeDecodeError) as e:
        print(f"m5_order: {e}", file=sys.stderr)
        return 2
    for p in problems:
        print(p)
    print(f"m5_order: {n} records, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
