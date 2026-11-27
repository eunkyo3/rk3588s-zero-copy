#!/usr/bin/env python3
"""The probe of the NPU's output bindings (prereg/M7.md §3 "The probe of the output bindings (#60)", §4, §5
"The probe", §8 step 3). It drives build/m7_output_probe (tools/m7_output_probe.cpp), whose header says what a
configuration binds: one process per job, so that a configuration whose process crashes is refused and the
others still run.

  1. Outputs. For each configuration 1 to 6, one process in dump mode makes 40 runs alternating the two frames
     and dumps every output after each. A non-zero exit, a signal or a timeout refuses the configuration, with
     the reason. Each other dump is compared, run by run and output by output, with configuration 1's after the
     conversion its layout and type call for (§3): "INT8 values must be equal; a permutation of the axes; for
     NC1HWC2, the channels put back in NCHW order with the padding channels dropped; for FLOAT32,
     (q − zero point) × scale, with a difference of at most half of that output's scale". Any difference
     refuses the configuration: which output, which run, the largest difference.
     Configuration 1 is the reference. If its process fails, if its attributes are not NCHW INT8, if two of its
     runs on the same frame differ, or if the two frames give the same outputs, the probe has a defect (§5):
     it stops, and the exit code is 1.
  2. Cost. 15 rounds; in round r (from 0) the process of every configuration accepted in 1 is started once in
     time mode, in the accepted configurations' order rotated to start with the (r mod n)-th, and makes 40
     untimed and 20 timed runs. A configuration whose process fails in a round is refused from then on, keeps
     its place in the rotation, and has no cost. A round's value is the mean per run of its 20 timed runs, of
     the calling thread's CPU and of the process CPU across rknn_run: tools/output_probe's statistic for a
     round (its total over the round's runs / runs, docs/notes/m2_exploration.md §1). Over the 15 rounds: the
     median and the range, and the median minus configuration 1's.

Reading a layout. Configuration 1's attributes give each output's shape (N, C, H, W). An output whose dims are
that shape (configurations 1 to 4 keep the query's dims) is dense in its fmt: NCHW as it is, NHWC permuted. An
output with other dims (the native ones) is read with the dims in its fmt's order, its rows w_stride elements
apart when w_stride is larger than W: NHWC as (N, H, W, C'), NC1HWC2 as (N, C1, H, W, C2) with channel c at
(c // C2, c % C2); channels from C on are padding and are dropped. Only INT8 and FLOAT32 have a conversion in
§3's rule; an output of another type cannot equal configuration 1's and refuses its configuration.

Usage, from the repository on the board, as root with the clocks fixed (tools/set_perf_mode.sh):
  python3 tools/m7_probe.py run [--out DIR] [--run-kind check|m7] [--freeze-m7-commit SHA]
                                [--only-config-1-one-frame] [--keep-dumps]
  --out                      the record's directory (default results/<run kind>/<UTC>_m7_probe, and
                             <UTC>_m7_probe_smoke for --only-config-1-one-frame)
  --run-kind                 check (default) or m7; m7 needs --freeze-m7-commit. The whole probe runs only with
                             m7 (M7 §8 step 3: "The probe is not run before freeze-m7, other than its
                             configuration 1 on one frame"); without --only-config-1-one-frame, check is refused
  --freeze-m7-commit         freeze-m7's commit, or none before it (default none)
  --only-config-1-one-frame  §8 step 3's smoke before freeze-m7 ("its configuration 1 on one frame, which shows
                             that the tool starts"): configuration 1 in dump mode, one run on one frame, and
                             nothing else; run kind check
  --keep-dumps               keep the outputs' bytes (about 0.7 GB); by default a configuration's are removed
                             once they are compared, configuration 1's last
Writes in DIR, each CSV with "# key: value" header lines (PROTOCOL §1.4):
  probe.csv              one row per configuration: accepted, refused, or not run; the reason; the cost's
                         statistics in ms per run
  rounds.csv             one row per round and configuration started: its position, the round's means per run
  dump/config<N>/        the dump process's attrs.csv, refused.csv when refused, process.log
  time/r<RR>_config<N>/  each time process's time.csv (the raw values per run), refused.csv, process.log
Exit code 0 when the probe ran, whatever the configurations' outcomes; 1 when configuration 1 failed, a defect
of the probe (§5); 2 for arguments it refuses.
"""
import argparse
import csv
import datetime as dt
import hashlib
import io
import math
import operator
import re
import signal
import statistics
import subprocess
import sys
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build" / "m7_output_probe"
MODEL = ROOT / "models" / "yolov8n_i8.rknn"  # the tool's default model, for the header's model_sha256
PREREG_M7_COMMIT = "c40d1a05eadb5b9bb5ed93c592e3d339ca1aa2fb"  # docs/notes/env.md; core/report.h's kPreregM7Commit
CONFIGS = {  # M7 §3, as tools/m7_output_probe.cpp binds them
    1: "NCHW INT8 from RKNN_QUERY_OUTPUT_ATTR (the pipeline's binding; the reference)",
    2: "NHWC INT8 from RKNN_QUERY_OUTPUT_ATTR",
    3: "NCHW FLOAT32 from RKNN_QUERY_OUTPUT_ATTR",
    4: "NHWC FLOAT32 from RKNN_QUERY_OUTPUT_ATTR",
    5: "RKNN_QUERY_NATIVE_OUTPUT_ATTR unchanged",
    6: "RKNN_QUERY_NATIVE_NHWC_OUTPUT_ATTR unchanged",
}
DUMP_RUNS, ROUNDS, TIMED_RUNS = 40, 15, 20  # M7 §3
UNTIMED_RUNS = 40  # tools/m7_output_probe.cpp's, before a time process's timed runs
DUMP_TIMEOUT_S, TIME_TIMEOUT_S = 600, 300  # far above a process's time: the native NHWC binding took 34 ms a run
EXIT_STAGES = {1: "a failure of the tool", 2: "rknn_init or rknn_set_core_mask failed", 3: "a query failed",
               4: "rknn_create_mem or rknn_set_io_mem failed", 5: "rknn_run failed"}  # the tool's exit codes
SHA = re.compile(r"[0-9a-f]{40}")
STATISTIC = ("per round, the mean per run over its timed runs; over the rounds, the median and the range "
             "(tools/output_probe's)")


class Mismatch(Exception):
    """An output cannot equal configuration 1's: the configuration is refused with this reason."""


class Defect(Exception):
    """Configuration 1 cannot be the reference: the probe has a defect (M7 §5)."""


# ---- reading the tool's files ----

def read_csv(text):
    """(header, rows): the "# key: value" lines at the start, then the rows as dicts."""
    header, body = {}, []
    for line in text.splitlines():
        if not body and line.startswith("# "):
            key, _, value = line[2:].partition(": ")
            header[key] = value
        else:
            body.append(line)
    return header, list(csv.DictReader(body))


def f32(x):
    """x rounded to the nearest float32, as the tool's float was before it was printed."""
    return array("f", [x])[0]


def parse_attrs(text):
    """attrs.csv's rows as dicts, one per output in order, with numbers for the numbers."""
    attrs = []
    for k, row in enumerate(read_csv(text)[1]):
        if int(row["output"]) != k:
            raise ValueError(f"attrs.csv: row {k} is output {row['output']}")
        attrs.append({
            "dims": [int(d) for d in row["dims"].split("x")], "n_elems": int(row["n_elems"]),
            "w_stride": int(row["w_stride"]), "fmt": row["fmt"], "type": row["type"], "zp": int(row["zp"]),
            "scale": f32(float(row["scale"])), "bytes": int(row["bytes"]), "ext": row["ext"]})
    if not attrs:
        raise ValueError("attrs.csv has no output")
    return attrs


def dump_name(run, output, attr):
    return f"run{run:02d}_out{output}.{attr['ext']}"


# ---- the conversion and the comparison (M7 §3) ----

def logical_shape(ref_attr):
    """(N, C, H, W) of an output, from configuration 1's attributes."""
    dims = ref_attr["dims"]
    if ref_attr["fmt"] != "NCHW" or ref_attr["type"] != "INT8" or len(dims) != 4:
        raise Defect(f"configuration 1's output is {ref_attr['fmt']} {ref_attr['type']} with dims {dims}, "
                     "not NCHW INT8")
    return tuple(dims)


def elements(data, type_):
    """The values of an output's bytes: bytes for INT8 (compared as bytes), floats for FLOAT32."""
    if type_ == "INT8":
        return bytes(data)
    if type_ == "FLOAT32":
        values = array("f")
        values.frombytes(bytes(data[:len(data) // 4 * 4]))
        if sys.byteorder != "little":  # the board writes little-endian floats
            values.byteswap()
        return values
    raise Mismatch(f"type {type_} has no conversion in M7 §3's rule")


def to_nchw(data, attr, shape):
    """An output's values in NCHW order with the logical shape (N, C, H, W), from its bytes as bound: bytes for
    INT8, an array of floats for FLOAT32. Raises Mismatch when its layout cannot be read as that shape."""
    n, c, h, w = shape
    values = elements(data, attr["type"])
    fmt, dims = attr["fmt"], tuple(attr["dims"])
    dense = dims == tuple(shape)
    out = bytearray() if isinstance(values, bytes) else array("f")
    if fmt == "NCHW" and dense:
        need = n * c * h * w
        rows = None
    elif fmt == "NHWC":
        if dense:
            cs, ws = c, w
        elif len(dims) == 4 and dims[:3] == (n, h, w) and dims[3] >= c:
            cs, ws = dims[3], max(w, attr["w_stride"])
        else:
            raise Mismatch(f"NHWC with dims {list(dims)} cannot be read as {list(shape)}")
        need = n * h * ws * cs
        rows = ((((b * h + y) * ws) * cs + ch, cs) for b in range(n) for ch in range(c) for y in range(h))
    elif fmt == "NC1HWC2" and not dense:
        if not (len(dims) == 5 and dims[0] == n and dims[2:4] == (h, w) and dims[1] * dims[4] >= c):
            raise Mismatch(f"NC1HWC2 with dims {list(dims)} cannot be read as {list(shape)}")
        c1n, c2n = dims[1], dims[4]
        ws = max(w, attr["w_stride"])
        need = n * c1n * h * ws * c2n
        rows = (((((b * c1n + ch // c2n) * h + y) * ws) * c2n + ch % c2n, c2n)
                for b in range(n) for ch in range(c) for y in range(h))
    else:
        raise Mismatch(f"{fmt} with dims {list(dims)} has no conversion to NCHW {list(shape)}")
    if len(values) < need:
        raise Mismatch(f"{len(values)} values, and {fmt} with dims {list(dims)} needs {need}")
    if rows is None:
        return values[:need]
    for start, step in rows:  # one row of W values of one channel
        out += values[start:start + w * step:step]
    return out


def compare_values(ref_q, zp, scale, got):
    """None when got agrees with configuration 1's INT8 values ref_q (bytes, NCHW) under M7 §3's rule, else
    (the largest difference, a description). INT8 (bytes): equal values. FLOAT32: |got − (q − zp) × scale| at
    most scale / 2 for every value; a NaN never agrees."""
    if len(got) != len(ref_q):
        return math.inf, f"{len(got)} values, configuration 1 has {len(ref_q)}"
    if isinstance(got, (bytes, bytearray)):
        if got == ref_q:
            return None
        diffs = [abs((a ^ 0x80) - (b ^ 0x80)) for a, b in zip(ref_q, got) if a != b]  # as signed bytes
        return max(diffs), f"largest difference {max(diffs)} in {len(diffs)} of {len(got)} values"
    deq = [((b ^ 0x80) - 128 - zp) * scale for b in range(256)]  # a byte as int8, dequantized
    tolerance = scale / 2
    diffs = list(map(abs, map(operator.sub, got, map(deq.__getitem__, ref_q))))
    if not any(map(math.isnan, got)) and max(diffs) <= tolerance:
        return None
    bad = [d for d in diffs if not d <= tolerance]
    largest = max(bad) if all(d == d for d in bad) else math.nan
    return largest, (f"largest difference {largest:.9g} in {len(bad)} of {len(got)} values (at most half the "
                     f"scale, {tolerance:.9g})")


def reference_problem(runs, ref_values, n_outputs):
    """Why configuration 1's dumps cannot be the reference, or None. ref_values(run, output) gives its values in
    NCHW order. Run r is on frame r % 2: runs on the same frame must give the same outputs, the frames not."""
    if runs < 2:
        return None
    for r in range(2, runs):
        for k in range(n_outputs):
            if ref_values(r, k) != ref_values(r % 2, k):
                return f"its run {r} differs from its run {r % 2} on the same frame (output {k})"
    if all(ref_values(0, k) == ref_values(1, k) for k in range(n_outputs)):
        return "the two frames give the same outputs"
    return None


def compare_dumps(ref_attrs, attrs, runs, ref_bytes, got_bytes):
    """None when every output of every run agrees with configuration 1's under M7 §3's rule, else the reason.
    ref_bytes(run, output) and got_bytes(run, output) give the dumped bytes."""
    if len(attrs) != len(ref_attrs):
        return f"{len(attrs)} outputs, configuration 1 has {len(ref_attrs)}"
    shapes = [logical_shape(a) for a in ref_attrs]
    differ, first, largest = 0, None, None
    for r in range(runs):
        for k, (ra, a) in enumerate(zip(ref_attrs, attrs)):
            ref_q = bytes(to_nchw(ref_bytes(r, k), ra, shapes[k]))
            try:
                got = to_nchw(got_bytes(r, k), a, shapes[k])
            except Mismatch as e:
                return f"output {k}: {e}"
            found = compare_values(ref_q, ra["zp"], ra["scale"], got)
            if found is None:
                continue
            differ += 1
            first = first or f"output {k} in run {r}: {found[1]}"
            if largest is None or not found[0] <= largest[0]:
                largest = (found[0], k, r)
    if not differ:
        return None
    return (f"{differ} of {runs * len(attrs)} outputs differ from configuration 1's; the first, {first}; the "
            f"largest difference, {largest[0]:.9g}, in output {largest[1]} in run {largest[2]}")


# ---- the cost (M7 §3) ----

def rotation(accepted, r):
    """Round r's order (r from 0): the accepted configurations, starting with the (r mod n)-th."""
    k = r % len(accepted)
    return list(accepted[k:]) + list(accepted[:k])


def round_means(rows):
    """(thread CPU, process CPU) in ms per run: the means over one round's timed runs (time.csv's rows)."""
    if not rows:
        raise ValueError("time.csv has no run")
    n = len(rows)
    return (sum(int(r["thread_cpu_ns"]) for r in rows) / n / 1e6,
            sum(int(r["process_cpu_ns"]) for r in rows) / n / 1e6)


def summary(values):
    """(median, min, max) over the rounds."""
    return statistics.median(values), min(values), max(values)


def cost_columns(means, reference):
    """probe.csv's cost columns of one configuration: rounds, then median, min and max of thread CPU and of
    process CPU, then each median minus configuration 1's. means: [(thread, process)] per round; reference:
    configuration 1's, or None."""
    if not means:
        return [""] * 9
    thread, process = summary([m[0] for m in means]), summary([m[1] for m in means])
    cols = [str(len(means))] + [f"{v:.4f}" for v in thread + process]
    if reference:
        ref_thread, ref_process = summary([m[0] for m in reference])[0], summary([m[1] for m in reference])[0]
        cols += [f"{thread[0] - ref_thread:.4f}", f"{process[0] - ref_process:.4f}"]
    else:
        cols += ["", ""]
    return cols


# ---- the processes ----

def failure_reason(returncode, refused_text, log_text):
    """Why a process of the tool failed, from its exit status, its refused.csv (or None) and its log."""
    if returncode < 0:
        try:
            name = signal.Signals(-returncode).name
        except ValueError:
            name = f"signal {-returncode}"
        return f"killed by {name}"
    detail = ""
    if refused_text:
        rows = {r["key"]: r["value"] for r in read_csv(refused_text)[1]}
        detail = rows.get("reason", "") + (f" in run {rows['run']}" if rows.get("run", "none") != "none" else "")
    if not detail:
        lines = [line.strip() for line in (log_text or "").splitlines() if line.strip()]
        detail = lines[-1] if lines else "no message"
    return f"exit code {returncode} ({EXIT_STAGES.get(returncode, 'an unknown failure')}): {detail}"


def start(mode, config, out, args, runs=None):
    """Runs one process of the tool into out; None when it succeeded, else why it did not."""
    out.mkdir(parents=True, exist_ok=True)
    cmd = [str(BINARY), mode, str(config), str(out), "--run-kind", args.run_kind,
           "--freeze-m7-commit", args.freeze_m7_commit] + (["--runs", str(runs)] if runs else [])
    timeout = DUMP_TIMEOUT_S if mode == "dump" else TIME_TIMEOUT_S
    log = out / "process.log"
    try:
        with open(log, "w", encoding="utf-8") as f:
            p = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout} s"
    except OSError as e:
        return f"cannot start {BINARY}: {e}"
    if p.returncode == 0:
        return None
    refused = out / "refused.csv"
    return failure_reason(p.returncode, refused.read_text(encoding="utf-8") if refused.exists() else None,
                          log.read_text(encoding="utf-8", errors="replace"))


def remove_dumps(d):
    for p in d.glob("run*_out*.*"):
        p.unlink()


# ---- the header and the record ----

def ntp_synchronized():
    try:
        return subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], capture_output=True,
                              text=True).stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def file_sha256(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return "unreadable"


def build_commit():
    """The commit build/ was built from (tools/deploy_board.sh sets CODE_COMMIT), or unknown."""
    cache = ROOT / "build" / "CMakeCache.txt"
    m = re.search(r"^CODE_COMMIT:STRING=(.*)$", cache.read_text(), re.M) if cache.exists() else None
    return m.group(1) if m else "unknown"


def latest_amendment():
    found = re.findall(r"^## (A\d+) ", (ROOT / "prereg" / "AMENDMENTS.md").read_text(encoding="utf-8"), re.M)
    return found[-1] if found else "none"


def write_csv(path, header, columns, rows):
    text = io.StringIO()
    for k, v in header.items():
        text.write(f"# {k}: {v}\n")
    w = csv.writer(text, lineterminator="\n")
    w.writerow(columns)
    w.writerows(rows)
    path.write_text(text.getvalue(), encoding="utf-8")


PROBE_COLUMNS = ["config", "binding", "outcome", "reason", "rounds", "thread_cpu_ms_median", "thread_cpu_ms_min",
                 "thread_cpu_ms_max", "process_cpu_ms_median", "process_cpu_ms_min", "process_cpu_ms_max",
                 "thread_cpu_ms_median_minus_config1", "process_cpu_ms_median_minus_config1"]
ROUND_COLUMNS = ["round", "position", "config", "thread_cpu_ms_mean", "process_cpu_ms_mean", "timed_runs", "outcome"]


def write_record(out, header, outcome, reasons, means, round_rows):
    """probe.csv and rounds.csv. outcome: {config: accepted, refused, ran or not run}; reasons: {config: why};
    means: {config: [(thread, process)] per round}."""
    rows = [[c, CONFIGS[c], outcome.get(c, "not run"), reasons.get(c, "")] +
            cost_columns(means.get(c, []) if outcome.get(c) == "accepted" else [],
                         means.get(1) if outcome.get(1) == "accepted" else None)
            for c in CONFIGS]
    write_csv(out / "probe.csv", header, PROBE_COLUMNS, rows)
    write_csv(out / "rounds.csv", header, ROUND_COLUMNS, round_rows)


# ---- the probe ----

def dump_all(out, args, configs, runs, outcome, reasons):
    """Part 1: the dumps and their comparison. Raises Defect when configuration 1 cannot be the reference."""
    def dumped(c):
        d = out / "dump" / f"config{c}"
        return d, parse_attrs((d / "attrs.csv").read_text(encoding="utf-8"))

    def reader(d, attrs):
        return lambda r, k: (d / dump_name(r, k, attrs[k])).read_bytes()

    why = start("dump", 1, out / "dump" / "config1", args, runs)
    if why:
        raise Defect(f"configuration 1: {why}")
    try:
        ref_dir, ref_attrs = dumped(1)
        shapes = [logical_shape(a) for a in ref_attrs]
        ref_bytes = reader(ref_dir, ref_attrs)

        def ref_values(r, k):
            return bytes(to_nchw(ref_bytes(r, k), ref_attrs[k], shapes[k]))

        for k in range(len(ref_attrs)):  # the first run's outputs can be read, with one run too
            ref_values(0, k)
        problem = reference_problem(runs, ref_values, len(ref_attrs))
    except (OSError, ValueError, KeyError, Mismatch) as e:
        raise Defect(f"configuration 1's dumps cannot be read: {e}") from None
    if problem:
        raise Defect(f"configuration 1: {problem}")
    outcome[1] = "accepted" if len(configs) > 1 else "ran"
    for c in configs[1:]:
        why = start("dump", c, out / "dump" / f"config{c}", args, runs)
        if why is None:
            try:
                d, attrs = dumped(c)
                why = compare_dumps(ref_attrs, attrs, runs, ref_bytes, reader(d, attrs))
            except (OSError, ValueError, KeyError) as e:
                why = f"its dumps cannot be read: {e}"
        outcome[c], reasons[c] = ("refused", why) if why else ("accepted", "")
        if not args.keep_dumps:
            remove_dumps(out / "dump" / f"config{c}")
    if not args.keep_dumps:
        remove_dumps(ref_dir)


def cost(out, args, outcome, reasons, means, round_rows):
    """Part 2: the rounds of time processes. Raises Defect when configuration 1's process fails."""
    accepted = [c for c in CONFIGS if outcome.get(c) == "accepted"]
    for r in range(ROUNDS):
        for position, c in enumerate(rotation(accepted, r)):
            if outcome[c] != "accepted":
                continue
            d = out / "time" / f"r{r:02d}_config{c}"
            why = start("time", c, d, args)
            if why is None:
                try:
                    rows = read_csv((d / "time.csv").read_text(encoding="utf-8"))[1]
                    if len(rows) != TIMED_RUNS:
                        raise ValueError(f"{len(rows)} timed runs, not {TIMED_RUNS}")
                    m = round_means(rows)
                except (OSError, ValueError, KeyError) as e:
                    why = f"its time.csv cannot be read: {e}"
            if why:
                round_rows.append([r, position, c, "", "", "", why])
                outcome[c], reasons[c] = "refused", f"round {r}: {why}"
                if c == 1:
                    raise Defect(f"configuration 1 in round {r}: {why}")
                continue
            means.setdefault(c, []).append(m)
            round_rows.append([r, position, c, f"{m[0]:.4f}", f"{m[1]:.4f}", len(rows), "ok"])


def run(args):
    smoke = args.only_config_1_one_frame
    now = dt.datetime.now(dt.timezone.utc)
    name = f"{now:%Y%m%dT%H%M%SZ}_m7_probe" + ("_smoke" if smoke else "")
    out = Path(args.out) if args.out else ROOT / "results" / args.run_kind / name
    out = out.resolve()
    if out.exists() and any(out.iterdir()):
        print(f"m7_probe: {out} is not empty", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    configs = [1] if smoke else list(CONFIGS)
    runs = 1 if smoke else DUMP_RUNS
    header = {"start_time_utc": f"{now:%Y-%m-%dT%H:%M:%SZ}", "ntp_synchronized": ntp_synchronized(),
              "prereg_m7_commit": PREREG_M7_COMMIT, "freeze_m7_commit": args.freeze_m7_commit,
              "run_kind": args.run_kind, "model_sha256": file_sha256(MODEL), "code_commit": build_commit(),
              "amendment": latest_amendment(), "tool": "m7_probe",
              "configuration": "1, one run on one frame (M7 §8 step 3)" if smoke else "1-6",
              "dump_runs": str(runs), "rounds": "0" if smoke else str(ROUNDS),
              "timed_runs_per_round": str(TIMED_RUNS), "untimed_runs_per_process": str(UNTIMED_RUNS),
              "statistic": STATISTIC, "dumps": "kept" if args.keep_dumps else "removed once compared"}
    outcome, reasons, means, round_rows = {}, {}, {}, []
    try:
        dump_all(out, args, configs, runs, outcome, reasons)
        if not smoke:
            cost(out, args, outcome, reasons, means, round_rows)
    except Defect as e:
        # The probe stopped: no configuration has a cost; rounds.csv keeps the rounds that ran.
        outcome[1], reasons[1] = "refused", f"{e} (a defect of the probe, M7 §5)"
        write_record(out, header, outcome, reasons, {}, round_rows)
        print(f"m7_probe: {reasons[1]}", file=sys.stderr)
        return 1
    write_record(out, header, outcome, reasons, means, round_rows)
    for c in configs:
        print(f"configuration {c}: {outcome.get(c, 'not run')}" + (f": {reasons[c]}" if reasons.get(c) else ""))
    print(f"written: {out / 'probe.csv'}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="run the probe")
    p.add_argument("--out")
    p.add_argument("--run-kind", choices=("check", "m7"), default="check")
    p.add_argument("--freeze-m7-commit", default="none")
    p.add_argument("--only-config-1-one-frame", action="store_true")
    p.add_argument("--keep-dumps", action="store_true")
    a = ap.parse_args(argv)
    if a.freeze_m7_commit != "none" and not SHA.fullmatch(a.freeze_m7_commit):
        print("m7_probe: --freeze-m7-commit is none or a full commit SHA", file=sys.stderr)
        return 2
    if a.run_kind == "m7" and a.freeze_m7_commit == "none":
        print("m7_probe: run kind m7 needs freeze-m7's commit (M7 §8)", file=sys.stderr)
        return 2
    if not a.only_config_1_one_frame and a.run_kind != "m7":
        print("m7_probe: the probe is not run before freeze-m7, other than --only-config-1-one-frame (M7 §8 step "
              "3): run kind m7", file=sys.stderr)
        return 2
    if a.only_config_1_one_frame and a.run_kind != "check":
        print("m7_probe: --only-config-1-one-frame is the check before freeze-m7: run kind check", file=sys.stderr)
        return 2
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
