#!/usr/bin/env python3
"""M1-3 NPU capacity (PROTOCOL §6.2) from a tools/calib_npu.sh session.

For each measurement (P_cap on 3 cores; the raw NPU limit on 1, 2 and 3 cores), the run of a
repetition is its last attempt: a repeat replaces an invalid run (§9.3). A repetition is
`invalid_x2` when its repeat is invalid too, and `missing` when the session stopped before it had a
valid run or a repeat. The value is the median of the valid repetitions' completed inferences per
second. A measurement is complete when each repetition is valid or invalid ×2 (§9.3).

Derived from P_cap (§6.2), exactly (P_cap = completed / window, as a fraction):
  - H2 auxiliary point: floor(0.8 × P_cap / 30) channels;
  - the P_cap part of the unsaturated condition, channels × fps ≤ 0.8 × P_cap: at most
    floor(0.8 × P_cap / 5) channels at the main condition and floor(0.8 × P_cap / 30) at the
    auxiliary one. The condition also needs channels ≤ L (§6.1), measured separately.

Usage: python3 tools/calib_summary.py <session_dir>
Writes <session_dir>/calibration.csv and prints it. Exit code 0 when every measurement is complete
and the session used the protocol's settings (§6.2, §9.2), 1 otherwise, 2 for unreadable input.
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
SHARED = ["prereg_v1_commit", "freeze_m1_commit", "run_kind", "model_sha256", "decoder", "variant", "channels",
          "code_commit", "amendment", "warmup_s", "duration_s", "tensors", "tensor_count", "tensor_heap"]
# What the protocol fixes for these runs: §6.2 (1,000 tensors, 20 s warm-up, 120 s) and §9.2 (10 min idle).
PROTOCOL = {"warmup_s": "20", "duration_s": "120", "tensor_count": "1000", "idle_s": "600"}
CLOCKS = ["policy0_khz", "policy4_khz", "policy6_khz", "dmc_hz", "npu_hz"]


class InputError(Exception):
    pass


def read_keyed(path):
    """The "key: value" lines of a text file (perf_mode.txt, baseline.txt)."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        k, sep, v = line.partition(": ")
        if sep:
            out[k] = v
    return out


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


def derived(p_cap):
    budget = Fraction(4, 5) * p_cap
    return {"h2_aux_point": math.floor(budget / 30),
            "unsaturated_main_max_channels_by_p_cap": math.floor(budget / 5),
            "unsaturated_aux_max_channels_by_p_cap": math.floor(budget / 30)}


def summarize(session):
    """Per measurement: (name, [per repetition: (state, rate, completed)], median); the session's
    shared header; and the values summed over the valid runs."""
    with open(session / "runs.tsv", encoding="utf-8") as f:
        runs = list(csv.DictReader(f, delimiter="\t"))
    last = {}  # (measurement, cores, repetition) -> its last attempt
    for r in runs:
        key = (r["measurement"], int(r["cores"]), int(r["repetition"]))
        if key not in last or int(r["attempt"]) > int(last[key]["attempt"]):
            last[key] = r

    out, shared, starts, ntp = [], None, [], set()
    totals = {"samples_at_or_above_trip0": 0, "clock_samples_below_fixed": 0}
    for measurement, cores in MEASUREMENTS:
        reps = []
        for rep in range(1, REPETITIONS + 1):
            r = last.get((measurement, cores, rep))
            if r is None or r["exit"] != "0":
                state = "invalid_x2" if r is not None and r["attempt"] == "2" else "missing"
                reps.append((state, None, None))
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
                diff = [k for k in SHARED if fields[k] != shared[k]]
                raise InputError(f"{r['dir']}: {', '.join(diff)} differ from the session's first run")
            starts.append(header["start_time_utc"])
            ntp.add(header["ntp_synchronized"])
            for k in totals:
                totals[k] += int(values[k])
            completed = int(values["completed_in_window"])
            reps.append(("valid", Fraction(completed, int(header["duration_s"])), completed))
        rates = [x for _, x, _ in reps if x is not None]
        name = "p_cap" if measurement == "p_cap" else f"npu_raw_{cores}c"
        out.append((name, reps, statistics.median(rates) if rates else None))
    if shared is None:
        raise InputError("no valid run")
    header = dict(shared, start_time_utc=min(starts), repetition="1-3 (median)",
                  ntp_synchronized="yes" if ntp == {"yes"} else "not in every run")
    return out, header, totals


def fmt(x):
    return "none" if x is None else f"{float(x):.4f}"


def main(argv):
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    session = Path(argv[1])
    try:
        measurements, header, totals = summarize(session)
        clocks = read_keyed(session / "perf_mode.txt")
        idle = read_keyed(session / "baseline.txt")
        tensors_sha256 = (session / "tensors.sha256").read_text(encoding="utf-8").split()[0]
        fixed = [(f"fixed_{k}", clocks[k]) for k in CLOCKS]
        baseline = idle["soc_thermal_baseline_mc"]
        settings = dict(header, idle_s=idle["idle_s"])
    except (InputError, OSError, KeyError, ValueError, IndexError) as e:
        print(f"calib_summary: {e}", file=sys.stderr)
        return 2

    rows, complete = [("key", "value")], True
    for name, reps, median in measurements:
        rows.append((f"{name}_inf_per_s", fmt(median)))
        rows.append((f"{name}_valid_runs", sum(state == "valid" for state, _, _ in reps)))
        for rep, (state, rate, completed) in enumerate(reps, 1):
            rows.append((f"{name}_r{rep}", fmt(rate) if state == "valid" else state))
            rows.append((f"{name}_r{rep}_completed", completed if state == "valid" else state))
        complete &= all(state != "missing" for state, _, _ in reps)
    p_cap = measurements[0][2]
    if p_cap is not None:
        rows += list(derived(p_cap).items())
    off = [f"{k} {settings[k]} (protocol {v})" for k, v in PROTOCOL.items() if settings[k] != v]
    rows += fixed
    rows += [("tensors_sha256", tensors_sha256), ("idle_s", idle["idle_s"]), ("soc_thermal_baseline_mc", baseline)]
    rows += list(totals.items())
    rows += [("complete", int(complete)), ("protocol_settings", "yes" if not off else "no: " + "; ".join(off))]

    text = "".join(f"# {k}: {v}\n" for k, v in header.items())
    text += "".join(f"{k},{v}\n" for k, v in rows)
    (session / "calibration.csv").write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")
    if not complete:
        print("calib_summary: the session stopped before every repetition had a valid run or a repeat",
              file=sys.stderr)
    if off:
        print("calib_summary: not the protocol's settings: " + "; ".join(off), file=sys.stderr)
    return 0 if complete and not off else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
