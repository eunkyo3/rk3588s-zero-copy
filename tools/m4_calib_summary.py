#!/usr/bin/env python3
"""M4's NPU capacity (prereg/M4.md §3.1) from a tools/m4_calib.sh session.

For each model (YOLOv8n, YOLOv10n) and measurement (P_cap on 3 cores with the pipeline's
post-processing; the raw limit on 1 core), the run of a repetition is its last attempt: a repeat
replaces an invalid run (PROTOCOL §9.3). A repetition is `invalid_x2` when its repeat is invalid too,
and `missing` when the session stopped before it had a valid run or a repeat.
  - The value is the median of the valid repetitions' completed inferences per second, also with
    fewer than 3 valid runs (M4 §5); with none, there is no value.
  - Also reported (§3.1), as medians over the valid repetitions: the mean rknn_run, bind and
    post-processing times per step, and the mean NPU load per core.
  - A measurement is complete when no repetition is missing.

Saturation (M4 §3.2), from each model's P_cap, exactly (P_cap = completed / window, as a fraction):
a point is unsaturated for a model only if channels × fps ≤ 0.8 × P_cap. The main point is 8 × 5, the
auxiliary one 5 × 30. Without a P_cap the label is `unknown` (M4 §5).

Checks: every run's header matches runs.tsv, its model's SHA-256 (M4 §1) and its post-processing (neon
for YOLOv8n's P_cap, v10 for YOLOv10n's, none for the raw limit); the runs share the session's other
header fields; and the runs went in §3.1's order. M4's settings are 1,000 tensors of M1-3's set
(A7), a 20 s warm-up, a 120 s window, 10 minutes idle and run kind m4.

Usage: python3 tools/m4_calib_summary.py <session_dir>
Writes <session_dir>/m4_calibration.csv and prints it. Exit code 0 when every measurement is complete,
the order is §3.1's and the session used M4's settings; 1 otherwise; 2 for unreadable input.
"""
import csv
import statistics
import sys
from fractions import Fraction
from pathlib import Path

import calib_summary as cs

MODELS = {  # M4 §1: name -> (SHA-256, P_cap's post-processing)
    "yolov8n": ("04a76611a785802b08703192455994e07bbfe4531821ae7ed35faf78fe1ec1ee", "neon"),
    "yolov10n": ("6a6442e1addbd1f08dbdb0772aeea1a992233b4f1db3bd451d20823522246f81", "v10"),
}
MEASUREMENTS = [("p_cap", 3), ("npu_raw", 1)]
REPETITIONS = 3
TENSORS_SHA256 = "051d6f12662e0b8ef9b72f00f74f498683913c39f197deb42c34ac1a54340c67"  # A7
POINTS = {"main": (8, 5), "aux": (5, 30)}  # M4 §3.2: channels, inference fps
# Header fields that every run of a session shares; model_sha256 and post are checked per model.
SHARED = ["prereg_v1_commit", "freeze_m1_commit", "prereg_m4_commit", "freeze_m4_commit", "run_kind", "decoder",
          "variant", "channels", "code_commit", "amendment", "warmup_s", "duration_s", "tensors", "tensor_count",
          "tensor_heap"]
SETTINGS = {"warmup_s": "20", "duration_s": "120", "tensor_count": "1000", "idle_s": "600", "run_kind": "m4"}
# Per-run means taken as medians over the valid repetitions (key in summary.csv -> key here).
STAGES = {"run_ms_mean": "run_ms", "bind_ms_mean": "bind_ms", "post_ms_mean": "post_ms",
          "npu_load_mean_core0": "npu_load_core0", "npu_load_mean_core1": "npu_load_core1",
          "npu_load_mean_core2": "npu_load_core2"}
InputError = cs.InputError


def planned_order():
    """[(repetition, model, measurement)] in §3.1's order."""
    out = []
    for rep in range(1, REPETITIONS + 1):
        models = ["yolov8n", "yolov10n"] if rep % 2 else ["yolov10n", "yolov8n"]
        for m in models:
            out += [(rep, m, measurement) for measurement, _ in MEASUREMENTS]
    return out


def in_order(runs):
    """Whether runs.tsv's runs, a repeat right after the run it repeats, follow planned_order()."""
    seen = []
    for r in runs:
        key = (int(r["repetition"]), r["model"], r["measurement"])
        if seen and seen[-1] == key and r["attempt"] == "2":
            continue
        if r["attempt"] != "1":
            return False
        seen.append(key)
    return seen == planned_order()[:len(seen)]


def saturation(p_cap):
    """{point: unsaturated | saturated | unknown} for a P_cap (a Fraction, or None)."""
    if p_cap is None:
        return {p: "unknown" for p in POINTS}
    return {p: "unsaturated" if ch * fps <= Fraction(4, 5) * p_cap else "saturated"
            for p, (ch, fps) in POINTS.items()}


def summarize(session):
    """Per model and measurement: (name, [per repetition: (state, rate, completed)], median, stages); the
    session's shared header; the warnings summed over every attempt; and whether the order is §3.1's."""
    with open(session / "runs.tsv", encoding="utf-8") as f:
        runs = list(csv.DictReader(f, delimiter="\t"))
    last = {}  # (model, measurement, cores, repetition) -> its last attempt
    for r in runs:
        if r["model"] not in MODELS:
            raise InputError(f"runs.tsv: unknown model {r['model']!r}")
        key = (r["model"], r["measurement"], int(r["cores"]), int(r["repetition"]))
        if key not in last or int(r["attempt"]) > int(last[key]["attempt"]):
            last[key] = r

    totals = {"samples_at_or_above_trip0_all_attempts": 0, "clock_samples_below_fixed_all_attempts": 0}
    for r in runs:  # a crashed run may have no summary
        path = session / r["dir"] / "summary.csv"
        if path.exists():
            _, values = cs.read_summary(path)
            for k in totals:
                totals[k] += int(values[k.removesuffix("_all_attempts")])

    out, shared, starts, ntp = [], None, [], set()
    for model, (sha, cap_post) in MODELS.items():
        for measurement, cores in MEASUREMENTS:
            reps, stage_values = [], {k: [] for k in STAGES.values()}
            for rep in range(1, REPETITIONS + 1):
                r = last.get((model, measurement, cores, rep))
                if r is None or r["exit"] != "0":
                    state = "invalid_x2" if r is not None and r["attempt"] == "2" else "missing"
                    reps.append((state, None, None))
                    continue
                header, values = cs.read_summary(session / r["dir"] / "summary.csv")
                got = (header["measurement"], int(header["cores"]), int(header["repetition"]))
                if got != (measurement, cores, rep):
                    raise InputError(f"{r['dir']}: header does not match runs.tsv")
                if header["model_sha256"] != sha:
                    raise InputError(f"{r['dir']}: model_sha256 is not {model}'s (M4 §1)")
                want_post = cap_post if measurement == "p_cap" else "none"
                if header["post"] != want_post:
                    raise InputError(f"{r['dir']}: post {header['post']}, not {want_post} (M4 §3.1)")
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
                completed = int(values["completed_in_window"])
                reps.append(("valid", Fraction(completed, int(header["duration_s"])), completed))
                for k, name in STAGES.items():
                    if k in values:
                        stage_values[name].append(float(values[k]))
            rates = [x for _, x, _ in reps if x is not None]
            name = f"{model}_{measurement}" if measurement == "p_cap" else f"{model}_{measurement}_{cores}c"
            if measurement != "p_cap":  # the raw limit runs no post-processing
                del stage_values["post_ms"]
            stages = {k: statistics.median(v) if v else None for k, v in stage_values.items()}
            out.append((name, reps, statistics.median(rates) if rates else None, stages))
    if shared is None:
        raise InputError("no valid run")
    header = dict(shared, start_time_utc=min(starts), repetition="1-3 (median)",
                  ntp_synchronized="yes" if ntp == {"yes"} else "not in every run")
    return out, header, totals, in_order(runs)


def fmt(x, digits=4):
    return "none" if x is None else f"{float(x):.{digits}f}"


def main(argv):
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    session = Path(argv[1])
    try:
        measurements, header, totals, order_ok = summarize(session)
        clocks = cs.read_keyed(session / "perf_mode.txt")
        idle = cs.read_keyed(session / "baseline.txt")
        info = cs.read_keyed(session / "session.txt")
        tensors_sha256 = (session / "tensors.sha256").read_text(encoding="utf-8").split()[0]
        fixed = [(f"fixed_{k}", clocks[k]) for k in cs.CLOCKS]
        settings = dict(header, idle_s=idle["idle_s"])
        cooling, ambient = info["cooling"], info["ambient_c"]
    except (InputError, OSError, KeyError, ValueError, IndexError) as e:
        print(f"m4_calib_summary: {e}", file=sys.stderr)
        return 2

    rows, complete = [("key", "value")], True
    p_caps = {}
    for name, reps, median, stages in measurements:
        rows.append((f"{name}_inf_per_s", fmt(median)))
        rows.append((f"{name}_valid_runs", sum(state == "valid" for state, _, _ in reps)))
        for rep, (state, rate, completed) in enumerate(reps, 1):
            rows.append((f"{name}_r{rep}", fmt(rate) if state == "valid" else state))
            rows.append((f"{name}_r{rep}_completed", completed if state == "valid" else state))
        for k, v in stages.items():
            rows.append((f"{name}_{k}", fmt(v, 3 if k.endswith("_ms") else 1)))
        complete &= all(state != "missing" for state, _, _ in reps)
        if name.endswith("_p_cap"):
            p_caps[name.removesuffix("_p_cap")] = median
    for model, p_cap in p_caps.items():
        for point, label in saturation(p_cap).items():
            rows.append((f"{model}_{point}_point", label))
    off = [f"{k} {settings[k]} (M4 {v})" for k, v in SETTINGS.items() if settings[k] != v]
    if tensors_sha256 != TENSORS_SHA256:
        off.append(f"tensors_sha256 {tensors_sha256} (A7 {TENSORS_SHA256})")
    if not order_ok:
        off.append("the runs are not in §3.1's order")
    rows += fixed
    rows += [("tensors_sha256", tensors_sha256), ("idle_s", idle["idle_s"]),
             ("soc_thermal_baseline_mc", idle["soc_thermal_baseline_mc"]), ("cooling", cooling),
             ("ambient_c", ambient)]
    rows += list(totals.items())
    rows += [("order_as_planned", int(order_ok)), ("complete", int(complete)),
             ("m4_settings", "yes" if not off else "no: " + "; ".join(off))]

    text = "".join(f"# {k}: {v}\n" for k, v in header.items())
    text += "".join(f"{k},{v}\n" for k, v in rows)
    (session / "m4_calibration.csv").write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")
    if not complete:
        print("m4_calib_summary: a measurement has a missing repetition", file=sys.stderr)
    if off:
        print("m4_calib_summary: not M4's settings: " + "; ".join(off), file=sys.stderr)
    return 0 if complete and not off else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
