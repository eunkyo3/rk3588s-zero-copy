#!/usr/bin/env python3
"""M1-3 NPU capacity (PROTOCOL §6.2) from a tools/calib_npu.sh session.

For each measurement (P_cap on 3 cores; the raw NPU limit on 1, 2 and 3 cores), the run of a
repetition is its last attempt: a repeat replaces an invalid run (§9.3). The value is the median of
the repetitions' completed inferences per second. A repetition that is invalid twice has no run.

Derived from P_cap (§6.2), exactly (P_cap = completed / window, as a fraction):
  - H2 auxiliary point: floor(0.8 × P_cap / 30) channels;
  - the P_cap part of the unsaturated condition, channels × fps ≤ 0.8 × P_cap: at most
    floor(0.8 × P_cap / 5) channels at the main condition and floor(0.8 × P_cap / 30) at the
    auxiliary one. The condition also needs channels ≤ L (§6.1), measured separately.

Usage: python3 tools/calib_summary.py <session_dir>
Writes <session_dir>/calibration.csv and prints it. Exit code 0 when every measurement has 3 valid
runs, 1 otherwise, 2 for unreadable input.
"""
import csv
import math
import statistics
import sys
from fractions import Fraction
from pathlib import Path

MEASUREMENTS = [("p_cap", 3), ("npu_raw", 1), ("npu_raw", 2), ("npu_raw", 3)]
REPETITIONS = 3
# Header fields that every run of a session shares (PROTOCOL §1).
SHARED = ["ntp_synchronized", "prereg_v1_commit", "freeze_m1_commit", "run_kind", "model_sha256", "code_commit",
          "amendment", "warmup_s", "duration_s", "tensors"]


class InputError(Exception):
    pass


def read_summary(path):
    """(header, values) of a calib_npu summary.csv."""
    header, rows = {}, []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("# "):
                k, _, v = line[2:].rstrip("\n").partition(": ")
                header[k] = v
            else:
                rows.append(line)
    values = {r["key"]: r["value"] for r in csv.DictReader(rows)}
    return header, values


def rate(header, values):
    """Completed inferences per second in the window, exactly."""
    return Fraction(int(values["completed_in_window"]), int(header["duration_s"]))


def derived(p_cap):
    budget = Fraction(4, 5) * p_cap
    return {"h2_aux_point": math.floor(budget / 30),
            "unsaturated_main_max_channels_by_p_cap": math.floor(budget / 5),
            "unsaturated_aux_max_channels_by_p_cap": math.floor(budget / 30)}


def summarize(session):
    with open(session / "runs.tsv", encoding="utf-8") as f:
        runs = list(csv.DictReader(f, delimiter="\t"))
    last = {}  # (measurement, cores, repetition) -> its last attempt
    for r in runs:
        key = (r["measurement"], int(r["cores"]), int(r["repetition"]))
        if key not in last or int(r["attempt"]) > int(last[key]["attempt"]):
            last[key] = r

    rows, complete, shared, starts = [], True, None, []
    for measurement, cores in MEASUREMENTS:
        rates = []
        for rep in range(1, REPETITIONS + 1):
            r = last.get((measurement, cores, rep))
            if r is None or r["exit"] != "0":
                rates.append(None)
                continue
            header, values = read_summary(session / r["dir"] / "summary.csv")
            if (header["measurement"], int(header["cores"]), int(header["repetition"])) != (measurement, cores, rep):
                raise InputError(f"{r['dir']}: header does not match runs.tsv")
            if values["valid"] != "1":
                raise InputError(f"{r['dir']}: exit 0 but valid is {values['valid']}")
            fields = {k: header[k] for k in SHARED}
            if shared is None:
                shared = fields
            elif fields != shared:
                raise InputError(f"{r['dir']}: header differs from the session's first run")
            starts.append(header["start_time_utc"])
            rates.append(rate(header, values))
        valid = [x for x in rates if x is not None]
        complete &= len(valid) == REPETITIONS
        rows.append((measurement, cores, rates, statistics.median(valid) if valid else None))
    if shared is None:
        raise InputError("no valid run")
    return rows, complete, dict(shared, start_time_utc=min(starts))


def fmt(x):
    return "none" if x is None else f"{float(x):.4f}"


def main(argv):
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    session = Path(argv[1])
    try:
        rows, complete, header = summarize(session)
    except (InputError, OSError, KeyError, ValueError) as e:
        print(f"calib_summary: {e}", file=sys.stderr)
        return 2

    out = [("key", "value")]
    for measurement, cores, rates, median in rows:
        name = "p_cap" if measurement == "p_cap" else f"npu_raw_{cores}c"
        out.append((f"{name}_inf_per_s", fmt(median)))
        for rep, x in enumerate(rates, 1):
            out.append((f"{name}_r{rep}", fmt(x)))
    p_cap = rows[0][3]
    if p_cap is not None:
        out += list(derived(p_cap).items())
    text = "".join(f"# {k}: {v}\n" for k, v in header.items())
    text += "".join(f"{k},{v}\n" for k, v in out)
    (session / "calibration.csv").write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")
    if not complete:
        print("calib_summary: a measurement has fewer than 3 valid runs", file=sys.stderr)
    return 0 if complete else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
