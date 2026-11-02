#!/usr/bin/env python3
"""PROTOCOL §1.5: checks the start times of everything under results/ against the registration
reference times.

  - every file starts after prereg-v1;
  - every run of kind comparison, reference or m1.1 starts after freeze-m1;
  - every run whose header names an amendment with a Release amend-A<n> starts after that Release.

The reference times are the Releases' created_at, as the Registration table of docs/notes/env.md
records them (PROTOCOL §1.3). An amendment has a Release when its entry in prereg/AMENDMENTS.md says
"Release: amend-A<n>"; each such Release needs a row in that table. File modification times are not
used.

A file's start time, run kind and amendment come from its own header:
  - a CSV must start with "# key: value" lines that include start_time_utc (PROTOCOL §1.4);
  - a JSONL whose first line is {"header": {...}};
  - a JSON object with "header_start_time_utc" (bench_main, calib_npu) or "start_time_utc";
  - a text file with a "time_utc: " line (tools/collect_env.sh).
Any other file (logs, hashes, tables, a crashed run's leftovers) takes the earliest start time of the
headers in its directory, or else in the nearest directory above it, and their run kinds and
amendments. Every file's run kind also comes from its path, results/<run kind>/..., which is where
bench_main and the other tools write; a header that names another kind is a problem.

Usage: python3 tools/check_prereg_order.py [--results DIR] [--env-md FILE] [--amendments FILE]
Exit code 0 when every file passes, 1 when one does not, 2 for unreadable input.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

KINDS = {"check", "calibration", "equivalence", "comparison", "reference", "m1.1"}  # PROTOCOL §1.4
AFTER_FREEZE = {"comparison", "reference", "m1.1"}
TAG_ROW = re.compile(r"^\|\s*\[?`([^`]+)`\]?(?:\([^)]*\))?\s*\|[^|]*\|\s*([^|]*?)\s*\|\s*$")


class InputError(Exception):
    pass


def parse_time(s, where):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise InputError(f"{where}: not a UTC time: {s!r}") from None


def reference_times(env_md):
    """{tag: created_at or None} from the Registration table of docs/notes/env.md."""
    refs, section = {}, None
    for n, line in enumerate(env_md.read_text(encoding="utf-8-sig").splitlines(), 1):
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if section != "Registration" or not line.startswith("|") or set(line) <= set("|- "):
            continue
        if line.startswith("| Stage"):
            continue
        m = TAG_ROW.match(line)
        if not m:
            raise InputError(f"{env_md}:{n}: cannot read this row of the Registration table")
        tag, value = m.group(1), m.group(2)
        if tag in refs:
            raise InputError(f"{env_md}:{n}: a second row for {tag}")
        refs[tag] = parse_time(value, f"{env_md}:{n}, {tag}") if value else None
    if refs.get("prereg-v1") is None:
        raise InputError(f"{env_md}: no created_at for prereg-v1 in the Registration table")
    return refs


def amendments(amendments_md):
    """(every amendment's name, the names with a Release amend-A<n>) from prereg/AMENDMENTS.md."""
    names, released, current = set(), set(), None
    for line in amendments_md.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^## (A\d+) ", line)
        if m:
            current = m.group(1)
            names.add(current)
        if not line.startswith("- Release:") or current is None:  # the format block comes first
            continue
        rest = line[len("- Release:"):].strip()
        m = re.search(r"\bamend-(A\d+)\b", rest)
        if m:
            if m.group(1) != current:
                raise InputError(f"{amendments_md}: entry {current} names Release amend-{m.group(1)}")
            released.add(current)
        elif not rest.startswith("none"):
            raise InputError(f"{amendments_md}: entry {current}: cannot read its Release line: {rest!r}")
    return names, released


def header_of(path):
    """(start, run_kind, amendment) from the file's own header; (None, ...) for a header without a
    start time; None if the file has no header."""
    if path.suffix not in (".csv", ".jsonl", ".json", ".txt"):  # tensors and logs are not read
        return None
    try:
        with open(path, encoding="utf-8-sig") as f:
            first = f.readline()
            if path.suffix == ".csv":
                if not first.startswith("# "):
                    return None
                h = {}
                line = first
                while line.startswith("# "):
                    k, _, v = line[2:].rstrip("\n").partition(": ")
                    h[k] = v
                    line = f.readline()
                return h.get("start_time_utc"), h.get("run_kind"), h.get("amendment")
            if path.suffix == ".jsonl" and first.startswith('{"header"'):
                h = json.loads(first)["header"]
                return h.get("start_time_utc"), h.get("run_kind"), h.get("amendment")
            if path.suffix == ".json":
                h = json.loads(first + f.read())
                if isinstance(h, dict) and ("header_start_time_utc" in h or "start_time_utc" in h):
                    return (h.get("header_start_time_utc") or h.get("start_time_utc"),
                            h.get("header_run_kind") or h.get("run_kind"),
                            h.get("header_amendment") or h.get("amendment"))
                return None
            if path.suffix == ".txt":
                for line in [first] + f.readlines():
                    if line.startswith("time_utc: "):
                        return line[len("time_utc: "):].strip(), None, None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return None


def check(results, refs, names, released):
    """Returns (number of files, list of problems)."""
    problems = []
    for tag in sorted(released):
        if refs.get(f"amend-{tag}") is None:
            problems.append(f"amend-{tag}: AMENDMENTS gives it a Release, but env.md has no created_at for it")
    files = sorted(p for p in results.rglob("*") if p.is_file())
    headers = {}
    for p in files:
        h = header_of(p)
        if h is None:
            if p.suffix in (".csv", ".jsonl"):
                problems.append(f"{p}: a results {p.suffix[1:].upper()} without a header (PROTOCOL §1.4)")
            continue
        if not h[0]:
            problems.append(f"{p}: a header without start_time_utc")
            continue
        headers[p] = (parse_time(h[0], str(p)), h[1], h[2])
    by_dir = {}
    for p, h in headers.items():
        by_dir.setdefault(p.parent, []).append(h)

    for p in files:
        rel = p.relative_to(results)
        path_kind = rel.parts[0] if len(rel.parts) > 1 else None
        if path_kind not in KINDS:
            problems.append(f"{p}: not under results/<run kind>/ ({', '.join(sorted(KINDS))})")
        if p in headers:
            own = [headers[p]]
        else:
            d = p.parent
            while d not in by_dir and d != results and results in d.parents:
                d = d.parent
            if d not in by_dir:
                problems.append(f"{p}: no start time in its directory or above")
                continue
            start = min(h[0] for h in by_dir[d])
            own = [(start, kind, amendment) for _, kind, amendment in by_dir[d]]
        for start, kind, amendment in own:
            if kind is not None and kind != path_kind:
                problems.append(f"{p}: header says run kind {kind!r}, its path says {path_kind!r}")
            if start <= refs["prereg-v1"]:
                problems.append(f"{p}: starts {start:%Y-%m-%dT%H:%M:%SZ}, not after prereg-v1")
            for k in {kind, path_kind} & AFTER_FREEZE:
                freeze = refs.get("freeze-m1")
                if freeze is None:
                    problems.append(f"{p}: a {k} run, and freeze-m1 has no reference time")
                elif start <= freeze:
                    problems.append(f"{p}: a {k} run that starts {start:%Y-%m-%dT%H:%M:%SZ}, not after freeze-m1")
            if amendment and amendment != "none":
                if amendment not in names:
                    problems.append(f"{p}: names {amendment}, which AMENDMENTS does not have")
                release = refs.get(f"amend-{amendment}")
                if amendment in released and release is not None and start <= release:
                    problems.append(f"{p}: names {amendment} but starts {start:%Y-%m-%dT%H:%M:%SZ}, "
                                    f"not after amend-{amendment}")
    return len(files), sorted(set(problems))


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    root = Path(__file__).resolve().parent.parent
    ap.add_argument("--results", type=Path, default=root / "results")
    ap.add_argument("--env-md", type=Path, default=root / "docs" / "notes" / "env.md")
    ap.add_argument("--amendments", type=Path, default=root / "prereg" / "AMENDMENTS.md")
    args = ap.parse_args(argv)
    try:
        refs = reference_times(args.env_md)
        names, released = amendments(args.amendments)
        if not args.results.is_dir():
            raise InputError(f"{args.results} is not a directory")
        n, problems = check(args.results, refs, names, released)
    except (InputError, OSError) as e:
        print(f"check_prereg_order: {e}", file=sys.stderr)
        return 2
    for tag, t in refs.items():
        print(f"{tag}: {t:%Y-%m-%dT%H:%M:%SZ}" if t else f"{tag}: no reference time yet")
    for p in problems:
        print(f"FAIL {p}")
    print(f"{n} files, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
