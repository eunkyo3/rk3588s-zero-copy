#!/usr/bin/env python3
"""PROTOCOL §1.5: checks the start times of everything under results/ against the registration
reference times.

  - every file starts after prereg-v1;
  - every run of kind comparison, reference or m1.1 starts after freeze-m1;
  - every run whose header names an amendment with a Release amend-A<n> starts after that Release.

The reference times are the Releases' created_at, as docs/notes/env.md records them (PROTOCOL §1.3)
in its Registration table. File modification times are not used. A file's header comes from:
  - a CSV whose first lines are "# key: value" (start_time_utc, run_kind, amendment);
  - a JSONL whose first line is {"header": {...}};
  - a JSON object with "header_start_time_utc" (bench_main, calib_npu) or "start_time_utc";
  - a text file with a "time_utc: " line (tools/collect_env.sh).
Any other file (logs, hashes, tables) takes the earliest start time of the headers in its directory,
or else in the nearest directory above it, and the run kinds and amendments of those headers.

Usage: python3 tools/check_prereg_order.py [--results DIR] [--env-md FILE]
Exit code 0 when every file passes, 1 when one does not, 2 for unreadable input.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

AFTER_FREEZE = {"comparison", "reference", "m1.1"}
ROW = re.compile(r"^\|\s*\[?`([^`]+)`\]?(?:\([^)]*\))?\s*\|[^|]*\|\s*([^|]*?)\s*\|\s*$")


class InputError(Exception):
    pass


def parse_time(s, where):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise InputError(f"{where}: not a UTC time: {s!r}") from None


def reference_times(env_md):
    """{tag: created_at or None} from the Registration table of docs/notes/env.md."""
    refs = {}
    for line in env_md.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line)
        if m and (m.group(1) in ("prereg-v1", "freeze-m1") or m.group(1).startswith("amend-A")):
            value = m.group(2)
            refs[m.group(1)] = parse_time(value, f"{env_md}, {m.group(1)}") if value else None
    if refs.get("prereg-v1") is None:
        raise InputError(f"{env_md}: no created_at for prereg-v1")
    return refs


def header_of(path):
    """(start, run_kind, amendment) from the file's own header, or None if it has none."""
    if path.suffix not in (".csv", ".jsonl", ".json", ".txt"):  # tensors and logs are not read
        return None
    try:
        with open(path, encoding="utf-8") as f:
            first = f.readline()
            if path.suffix == ".csv" and first.startswith("# "):
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
                if isinstance(h, dict):
                    start = h.get("header_start_time_utc") or h.get("start_time_utc")
                    if start:
                        return start, h.get("header_run_kind"), h.get("header_amendment") or h.get("amendment")
                return None
            if path.suffix == ".txt":
                for line in [first] + f.readlines():
                    if line.startswith("time_utc: "):
                        return line[len("time_utc: "):].strip(), None, None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return None


def check(results, refs):
    """Returns (number of files, list of problems)."""
    files = sorted(p for p in results.rglob("*") if p.is_file())
    headers = {}
    for p in files:
        h = header_of(p)
        if h and h[0]:
            headers[p] = (parse_time(h[0], str(p)), h[1], h[2])
    by_dir = {}
    for p, h in headers.items():
        by_dir.setdefault(p.parent, []).append(h)

    problems = []
    for p in files:
        if p in headers:
            own = [headers[p]]
        else:
            d = p.parent
            while d not in by_dir and d != results and results in d.parents:
                d = d.parent
            if d not in by_dir:
                problems.append(f"{p}: no start time in its directory or above")
                continue
            hs = by_dir[d]
            start = min(h[0] for h in hs)
            own = [(start, kind, amendment) for _, kind, amendment in hs]
        for start, kind, amendment in own:
            if start <= refs["prereg-v1"]:
                problems.append(f"{p}: starts {start:%Y-%m-%dT%H:%M:%SZ}, not after prereg-v1")
            if kind in AFTER_FREEZE:
                freeze = refs.get("freeze-m1")
                if freeze is None:
                    problems.append(f"{p}: a {kind} run, and freeze-m1 has no reference time")
                elif start <= freeze:
                    problems.append(f"{p}: a {kind} run that starts {start:%Y-%m-%dT%H:%M:%SZ}, not after freeze-m1")
            release = refs.get(f"amend-{amendment}") if amendment else None
            if release is not None and start <= release:
                problems.append(f"{p}: names {amendment} but starts {start:%Y-%m-%dT%H:%M:%SZ}, "
                                f"not after amend-{amendment}")
    return len(files), sorted(set(problems))


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    root = Path(__file__).resolve().parent.parent
    ap.add_argument("--results", type=Path, default=root / "results")
    ap.add_argument("--env-md", type=Path, default=root / "docs" / "notes" / "env.md")
    args = ap.parse_args(argv)
    try:
        refs = reference_times(args.env_md)
        if not args.results.is_dir():
            raise InputError(f"{args.results} is not a directory")
        n, problems = check(args.results, refs)
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
