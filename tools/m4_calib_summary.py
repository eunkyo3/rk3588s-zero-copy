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
auxiliary one 5 × 30. Without a P_cap the label is `unknown` (M4 §5); while P_cap has a missing
repetition, `incomplete`.

Checks, on every attempt that wrote a summary.csv, valid or not:
  - its header matches runs.tsv, its model's SHA-256 (M4 §1) and its post-processing (neon for
    YOLOv8n's P_cap, v10 for YOLOv10n's, none for the raw limit), and it shares the session's other
    header fields;
  - runs.tsv has one attempt per run and at most one repeat, only of an invalid run (PROTOCOL §9.3).
M4's settings, which exit code 1 reports when they are not met: §3.1's order; 1,000 tensors of
M1-3's set (A7) in system-uncached-dma32 buffers; a 20 s warm-up and a 120 s window; 10 minutes idle;
run kind m4; freeze-m1's commit (PROTOCOL §1.4); and session.txt's run kind and freeze-m4 commit
equal to the runs'.

Usage: python3 tools/m4_calib_summary.py <session_dir>
Writes <session_dir>/m4_calibration.csv and prints it. Exit code 0 when every measurement is complete
and the session used M4's settings; 1 otherwise; 2 for unreadable input.
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
FREEZE_M1_COMMIT = "2eec964d27545e081a44bc5ead01bbfcd2905a54"  # docs/notes/env.md, Registration
HEAP = "system-uncached-dma32"  # C's slots' heap, as M1-3 (A7)
POINTS = {"main": (8, 5), "aux": (5, 30)}  # M4 §3.2: channels, inference fps
# Header fields that every run of a session shares; model_sha256 and post are checked per model, and
# tensor_heap per run.
SHARED = ["prereg_v1_commit", "freeze_m1_commit", "prereg_m4_commit", "freeze_m4_commit", "run_kind", "decoder",
          "variant", "channels", "code_commit", "amendment", "warmup_s", "duration_s", "tensors", "tensor_count"]
SETTINGS = {"warmup_s": "20", "duration_s": "120", "tensor_count": "1000", "idle_s": "600", "run_kind": "m4",
            "freeze_m1_commit": FREEZE_M1_COMMIT}
# Per-run means taken as medians over the valid repetitions (key in summary.csv -> key here).
STAGES = {"run_ms_mean": "run_ms", "bind_ms_mean": "bind_ms", "post_ms_mean": "post_ms",
          "npu_load_mean_core0": "npu_load_core0", "npu_load_mean_core1": "npu_load_core1",
          "npu_load_mean_core2": "npu_load_core2"}
COLUMNS = ["repetition", "model", "measurement", "cores", "attempt", "dir", "exit"]
InputError = cs.InputError


def planned_order():
    """[(repetition, model, measurement)] in §3.1's order."""
    out = []
    for rep in range(1, REPETITIONS + 1):
        models = ["yolov8n", "yolov10n"] if rep % 2 else ["yolov10n", "yolov8n"]
        for m in models:
            out += [(rep, m, measurement) for measurement, _ in MEASUREMENTS]
    return out


def key_of(r):
    return r["model"], r["measurement"], int(r["cores"]), int(r["repetition"])


def read_runs(session):
    """runs.tsv's rows, each complete, with one attempt per run and at most one repeat, of an invalid
    run (PROTOCOL §9.3)."""
    with open(session / "runs.tsv", encoding="utf-8") as f:
        runs = list(csv.DictReader(f, delimiter="\t"))
    attempts = {}
    for n, r in enumerate(runs, 2):
        if None in r or any(r.get(c) in (None, "") for c in COLUMNS):
            raise InputError(f"runs.tsv:{n}: an incomplete or malformed row")
        if r["model"] not in MODELS or (r["measurement"], int(r["cores"])) not in MEASUREMENTS:
            raise InputError(f"runs.tsv:{n}: not one of M4's runs: {r['model']} {r['measurement']} {r['cores']}c")
        attempts.setdefault(key_of(r), []).append(r)
    for rs in attempts.values():
        if [r["attempt"] for r in rs] not in (["1"], ["1", "2"]):
            raise InputError(f"{rs[0]['dir']}: attempts {[r['attempt'] for r in rs]}, not one run and at most "
                             "one repeat (PROTOCOL §9.3)")
        if len(rs) == 2 and rs[0]["exit"] == "0":
            raise InputError(f"{rs[1]['dir']}: a repeat of a valid run (PROTOCOL §9.3)")
    return runs


def in_order(runs):
    """Whether runs.tsv's runs, each repeat right after the run it repeats, follow planned_order()."""
    seen, prev = [], None
    for r in runs:
        key = (int(r["repetition"]), r["model"], r["measurement"])
        if r["attempt"] == "2":
            if prev != (key, "1"):
                return False
        else:
            seen.append(key)
        prev = (key, r["attempt"])
    return seen == planned_order()[:len(seen)]


def saturation(p_cap):
    """{point: unsaturated | saturated | unknown} for a P_cap (a Fraction, or None)."""
    if p_cap is None:
        return {p: "unknown" for p in POINTS}
    return {p: "unsaturated" if ch * fps <= Fraction(4, 5) * p_cap else "saturated"
            for p, (ch, fps) in POINTS.items()}


def summarize(session):
    """Per model and measurement: (name, [per repetition: (state, rate, completed)], median, stages); the
    session's shared header; the warnings summed over every attempt; whether the order is §3.1's; and
    the heaps that are not HEAP."""
    runs = read_runs(session)
    last = {}  # (model, measurement, cores, repetition) -> its last attempt
    for r in runs:
        last[key_of(r)] = r  # read_runs has put any repeat after its run

    totals = {"samples_at_or_above_trip0_all_attempts": 0, "clock_samples_below_fixed_all_attempts": 0}
    shared, starts, ntp, heaps, read = None, [], set(), set(), {}
    for r in runs:  # a crashed run may have no summary
        path = session / r["dir"] / "summary.csv"
        if not path.exists():
            continue
        header, values = cs.read_summary(path)
        model, measurement, cores, rep = key_of(r)
        sha, cap_post = MODELS[model]
        if (header["measurement"], int(header["cores"]), int(header["repetition"])) != (measurement, cores, rep):
            raise InputError(f"{r['dir']}: header does not match runs.tsv")
        if header["model_sha256"] != sha:
            raise InputError(f"{r['dir']}: model_sha256 is not {model}'s (M4 §1)")
        want_post = cap_post if measurement == "p_cap" else "none"
        if header["post"] != want_post:
            raise InputError(f"{r['dir']}: post {header['post']}, not {want_post} (M4 §3.1)")
        if r["exit"] == "0" and values["valid"] != "1":
            raise InputError(f"{r['dir']}: exit 0 but valid is {values['valid']}")
        fields = {k: header[k] for k in SHARED}
        if shared is None:
            shared = fields
        elif fields != shared:
            diff = [k for k in SHARED if fields[k] != shared[k]]
            raise InputError(f"{r['dir']}: {', '.join(diff)} differ from the session's first run")
        if header["tensor_heap"] != HEAP:
            heaps.add(header["tensor_heap"])
        starts.append(header["start_time_utc"])
        ntp.add(header["ntp_synchronized"])
        for k in totals:
            totals[k] += int(values[k.removesuffix("_all_attempts")])
        read[r["dir"]] = (header, values)
    if shared is None:
        raise InputError("no run wrote a summary.csv")

    out = []
    for model in MODELS:
        for measurement, cores in MEASUREMENTS:
            reps, stage_values = [], {k: [] for k in STAGES.values()}
            for rep in range(1, REPETITIONS + 1):
                r = last.get((model, measurement, cores, rep))
                if r is None or r["exit"] != "0":
                    state = "invalid_x2" if r is not None and r["attempt"] == "2" else "missing"
                    reps.append((state, None, None))
                    continue
                if r["dir"] not in read:
                    raise InputError(f"{r['dir']}: exit 0 but no summary.csv")
                header, values = read[r["dir"]]
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
    header = dict(shared, start_time_utc=min(starts), repetition="1-3 (median)",
                  ntp_synchronized="yes" if ntp == {"yes"} else "not in every run")
    for model, (sha, cap_post) in MODELS.items():  # checked above for every run that has a summary
        header[f"model_sha256_{model}"] = sha
        header[f"post_{model}_p_cap"] = cap_post
    return out, header, totals, in_order(runs), heaps


def fmt(x, digits=4):
    return "none" if x is None else f"{float(x):.{digits}f}"


def main(argv):
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    session = Path(argv[1])
    try:
        measurements, header, totals, order_ok, heaps = summarize(session)
        clocks = cs.read_keyed(session / "perf_mode.txt")
        idle = cs.read_keyed(session / "baseline.txt")
        info = cs.read_keyed(session / "session.txt")
        tensors_sha256 = (session / "tensors.sha256").read_text(encoding="utf-8").split()[0]
        fixed = [(f"fixed_{k}", clocks[k]) for k in cs.CLOCKS]
        settings = dict(header, idle_s=idle["idle_s"])
        header.update(cooling=info["cooling"], ambient_c=info["ambient_c"])  # free text: in the header
        session_kind, session_freeze = info["run_kind"], info["freeze_m4_commit"]
    except (InputError, OSError, KeyError, ValueError, IndexError, TypeError) as e:
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
        measurement_complete = all(state != "missing" for state, _, _ in reps)
        complete &= measurement_complete
        if name.endswith("_p_cap"):
            p_caps[name.removesuffix("_p_cap")] = (median, measurement_complete)
    for model, (p_cap, done) in p_caps.items():
        for point, label in saturation(p_cap).items():
            rows.append((f"{model}_{point}_point", label if done else "incomplete"))
    off = [f"{k} {settings[k]} (M4 {v})" for k, v in SETTINGS.items() if settings[k] != v]
    if tensors_sha256 != TENSORS_SHA256:
        off.append(f"tensors_sha256 {tensors_sha256} (A7 {TENSORS_SHA256})")
    if heaps:
        off.append(f"tensor_heap {' and '.join(sorted(heaps))} in some runs ({HEAP})")
    if (session_kind, session_freeze) != (header["run_kind"], header["freeze_m4_commit"]):
        off.append(f"session.txt says run kind {session_kind} and freeze-m4 {session_freeze}; "
                   f"the runs say {header['run_kind']} and {header['freeze_m4_commit']}")
    if not order_ok:
        off.append("the runs are not in §3.1's order")
    rows += fixed
    rows += [("tensors_sha256", tensors_sha256), ("idle_s", idle["idle_s"]),
             ("soc_thermal_baseline_mc", idle["soc_thermal_baseline_mc"])]
    rows += list(totals.items())
    rows += [("order_as_planned", int(order_ok)), ("complete", int(complete)),
             ("m4_settings", "yes" if not off else "no: " + "; ".join(off))]

    bad = [k for k, v in rows if "," in str(v)]
    if bad:  # every row is key,value: free text goes in the header
        print(f"m4_calib_summary: a comma in the value of {bad}", file=sys.stderr)
        return 2
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
