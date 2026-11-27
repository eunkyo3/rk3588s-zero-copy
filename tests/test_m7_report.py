#!/usr/bin/env python3
"""Tests for tools/m7_report.py (prereg/M7.md §2, §3, §4, §5 and §6): the two hypotheses' verdicts in exact
arithmetic, their mixed labels (M7-H2's from the snapshots, and no label without them), the practical-verdict
exceptions at each point, missing runs, arms not run and CV flags; the snapshots' values (other tasks by name group,
threads of one snapshot only, interrupts, soft interrupts, /proc/stat, /proc/schedstat, cpu0's counter, bench_main's
threads) and the idle pair scaled to a run's span; the counters by thread group; §6's description rule and §5's 2%
rule; the runs that count across sessions, superseded sessions, incomplete snapshots, and the report. Fixtures are
written to a temporary directory.

Usage: python3 tests/test_m7_report.py
"""
import io
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import m7_report as m7r  # noqa: E402
import stream_session as ss  # noqa: E402
from test_m6_report import IDLE, keyed, table  # noqa: E402

WARMUP, WINDOW = 20, 120
CPU = {"B": 40.0, "Bd": 39.0, "Rc": 22.0, "C": 20.0, "Bk": 40.4, "Bdk": 39.3, "Rck": 22.2, "Ck": 20.2,
       "Ct": 20.5, "Cpt": 19.0}
AUX_CPU = {"B": 130.0, "Bd": 129.0, "Rc": 39.0, "C": 37.0, "Ct": 37.5, "Cpt": 32.0}
DEC = {"B": 10.5, "Bd": 10.0, "Rc": 11.6, "C": 11.0}  # D in threads.csv, s; the counted arms take their cell's
SPAN_NS = 121_001_000_000  # the snapshots' span, the first's start to the second's
EVENTS = ("cpu_cycles", "inst_retired", "l2d_cache_refill", "l3d_cache_refill", "stall_backend")


def d(x):
    """A decimal as a record writes it, as the Fraction the tool reads."""
    return Fraction(str(x))


# ---------------------------------------------------------------- the snapshots' files

def interrupts(m, extra=""):
    """A /proc/interrupts text of two CPUs; m scales every count."""
    return ("           CPU0       CPU1\n"
            f" 11: {1000 * m:10d} {1000 * m:10d}     GICv3  27 Level     arch_timer\n"
            f" 37: {100 * m:10d} {0:10d}     GICv3 141 Level     fdab0000.npu\n"
            f" 38: {50 * m:10d} {0:10d}     GICv3 142 Level     fdb60000.rga\n"
            f" 40: {30 * m:10d} {10 * m:10d}     GICv3 150 Level     fdc38100.rkvdec\n"
            f"IPI0: {20 * m:9d} {30 * m:10d}       Rescheduling interrupts\n" + extra +
            "Err:          0\n")


def softirqs(m):
    return ("                    CPU0       CPU1\n"
            f"          HI: {0:10d} {0:10d}\n       TIMER: {100 * m:10d} {200 * m:10d}\n"
            f"      NET_RX: {5 * m:10d} {0:10d}\n       SCHED: {40 * m:10d} {60 * m:10d}\n")


def stat(m):
    return (f"cpu  {100 * m} 0 {50 * m} 1000 0 0 {3 * m} 0 0 0\ncpu0 {50 * m} 0 {25 * m} 500 0 0 {3 * m} 0 0 0\n"
            f"cpu1 {50 * m} 0 {25 * m} 500 0 0 0 0 0 0\nintr 1 2 3\nctxt 5\n")


def schedstat(cpu0_ns, cpu1_ns):
    return (f"version 15\ntimestamp 4294\ncpu0 0 0 0 0 0 0 {cpu0_ns} 7 8\ndomain0 03 0 0 0 0\n"
            f"cpu1 0 0 0 0 0 0 {cpu1_ns} 7 8\n")


# (pid, tid, kernel, name in snapshot 1, name in snapshot 2, run in snapshot 1, its increase per unit), ns
TASKS = ((10, 10, 1, "mpp_worker_0", "mpp_worker_0", 5 * 10**9, 300_000_000),
         (11, 11, 1, "irq/37-rga3", "irq/37-rga3", 10**9, 50_000_000),
         (12, 12, 1, "rknpu_power_off", "rknpu_power_off", 0, 10_000_000),
         (13, 13, 1, "kworker/0:1-events", "kworker/0:1-rknpu", 10**9, 20_000_000),  # its second name holds npu
         (14, 14, 1, "kworker/1:0-mm_percpu_wq", "kworker/1:0-events", 10**9, 40_000_000),
         (15, 15, 1, "ksoftirqd/0", "ksoftirqd/0", 10**9, 30_000_000),
         (16, 16, 1, "rcu_preempt", "rcu_preempt", 10**9, 60_000_000),
         (100, 100, 0, "sshd", "sshd", 2 * 10**9, 100_000_000),
         (200, 200, 0, "python3", "python3", 3 * 10**9, 200_000_000),
         (200, 201, 0, "worker,1", "worker,1", 10**9, 50_000_000))  # a name that is quoted in the CSV
ONLY_FIRST = ((300, 300, 0, "short", 500_000_000),)
ONLY_SECOND = ((400, 400, 0, "new", 70_000_000), (17, 17, 1, "kworker/0:2", 10_000_000))  # run per unit
# bench_main's threads: (tid, name, run increase in ns); dec-1's is dec_ns − 0.9 s
OWN = ((1001, "dec-1", None), (1002, "mpp_dec_parser", 700_000_000), (1003, "mpp_dec_hal", 200_000_000),
       (1004, "npu-0", 5 * 10**9), (1005, "pre-0", 3 * 10**9), (1006, "metrics", 100_000_000))


def quoted(s):
    return f'"{s}"' if "," in s else s


def write_snapshots(d_, m=2, dec_ns=10 * 10**9, span_ns=SPAN_NS, problem=None, cpu0=True, cycles=2_700_000_000):
    """The snapshot files of a run (or of the idle pair, cpu0=False), as src/core/snapshot.cpp writes them; m scales
    the other tasks' run time, the interrupts and soft interrupts. problem: "incomplete" (the first stopped at its
    deadline), "missing" (no snapshot_tasks.csv) or "garbled" (an interrupts file without its header)."""
    d_.mkdir(parents=True, exist_ok=True)
    header = {"run_kind": "m7"}
    meta = {"snapshot1_taken": 1, "snapshot1_complete": 0 if problem == "incomplete" else 1,
            "snapshot1_begin_ns": 19 * 10**9, "snapshot1_schedstat_read_ns": 19 * 10**9 + 1000,
            "snapshot1_end_ns": 19 * 10**9 + 50_000_000, "snapshot1_own_threads": len(OWN),
            "snapshot1_other_threads": 11, "snapshot1_ended_while_read": 0,
            "snapshot2_taken": 1, "snapshot2_complete": 1, "snapshot2_begin_ns": 19 * 10**9 + span_ns,
            "snapshot2_schedstat_read_ns": 19 * 10**9 + span_ns + 1000,
            "snapshot2_end_ns": 19 * 10**9 + span_ns + 60_000_000,
            "snapshot2_own_threads": len(OWN), "snapshot2_other_threads": 12, "snapshot2_ended_while_read": 0}
    if cpu0:
        meta.update(window_start_ns=20 * 10**9, window_end_ns=140 * 10**9, end_tick_wake_ns=140 * 10**9 + 100_000,
                    cpu0_counter_cpu=0, cpu0_counter_event_found=1, cpu0_counter_pmu_type=8, cpu0_counter_config="0x11",
                    cpu0_counter_open_errno=0, cpu0_counter_enabled=1, cpu0_counter_read_ok=1,
                    snapshot1_tick_wake_ns=19 * 10**9 - 3_000_000,  # the wake-up for the first snapshot's tick
                    cpu0_counter_enable_ns=19 * 10**9 + 1000, cpu0_counter_read_ns=140 * 10**9 + 2_100_000,
                    cpu0_counter_cycles=cycles, cpu0_counter_time_enabled_ns=span_ns,
                    cpu0_counter_time_running_ns=span_ns)
    else:
        meta["span_s"] = round(span_ns / 1e9)
    keyed(d_ / "snapshot_meta.csv", header, meta)
    rows = []
    for k in (1, 2):
        rows += [(k, p, t, kern, quoted(n1 if k == 1 else n2), r + (k - 1) * inc * m)
                 for p, t, kern, n1, n2, r, inc in TASKS]
        rows += [(1, p, t, kern, n, r) for p, t, kern, n, r in ONLY_FIRST] if k == 1 else \
            [(2, p, t, kern, n, r * m) for p, t, kern, n, r in ONLY_SECOND]
    if problem != "missing":
        table(d_ / "snapshot_tasks.csv", header, ["snapshot", "pid", "tid", "kernel", "comm", "run_ns"], rows)
    own = []
    for k in (1, 2):
        for tid, name, inc in OWN:
            inc = dec_ns - 900_000_000 if inc is None else inc
            own.append((k, tid, name, 10**9 + (k - 1) * inc, 10**6 + (k - 1) * 10_000_000, 50 + (k - 1) * 1000,
                        40 + (k - 1) * 900, 10 + (k - 1) * 100))
    table(d_ / "snapshot_own.csv", header, ["snapshot", "tid", "comm", "run_ns", "wait_ns", "timeslices",
                                            "voluntary_cs", "involuntary_cs"], own)
    for k in (1, 2):
        text = interrupts(m * (k - 1) + 1)
        if problem == "garbled" and k == 2:
            text = text.split("\n", 1)[1]  # without its header of CPUs
        (d_ / f"snapshot{k}_interrupts.txt").write_text(text, encoding="utf-8")
        (d_ / f"snapshot{k}_softirqs.txt").write_text(softirqs(m * (k - 1) + 1), encoding="utf-8")
        (d_ / f"snapshot{k}_stat.txt").write_text(stat(m * (k - 1) + 1), encoding="utf-8")
        (d_ / f"snapshot{k}_schedstat.txt").write_text(
            schedstat(10**9 + (k - 1) * 1_400_000_000, 3 * 10**9 + (k - 1) * 2 * 10**9 * m), encoding="utf-8")


def write_counters(d_, dec_inst=30 * 10**9, dec_cycles=20 * 10**9, failed=None):
    """counter_events.csv and counters.csv, as src/core/perf_counters.cpp writes them: the decoder group's threads
    share its instructions and cycles; failed: "open" (the metrics thread's group could not be opened) or "disable"
    (the ioctl that disables npu-0's group failed; its values were still read)."""
    header = {"run_kind": "m7"}
    table(d_ / "counter_events.csv", header, ["name", "found", "pmu_type", "config"],
          [(e, 1, 8, hex(i + 0x11)) for i, e in enumerate(EVENTS)])
    rows = []
    shares = {"dec-1": Fraction(7, 10), "mpp_dec_parser": Fraction(2, 10), "mpp_dec_hal": Fraction(1, 10)}
    for tid, name, _ in OWN:
        if name in shares:
            cycles, inst = dec_cycles * shares[name], dec_inst * shares[name]
            run = cycles / 2208 * 1000  # ns at 2208 MHz
        else:
            cycles, inst, run = 2208 * 10**6, 10**9, 10**9
        opened = not (failed == "open" and name == "metrics")
        disabled = opened and not (failed == "disable" and name == "npu-0")
        rows.append((tid, name, int(opened), 0 if opened else 13, "" if opened else "cpu_cycles", int(opened),
                     int(disabled), int(opened), 121 * 10**9 if opened else 0, 121 * 10**9 if opened else 0,
                     10**9 if opened else -1, int(10**9 + run) if opened else -1, 20 * 10**9 if opened else 0, 140 * 10**9 if opened else 0,
                     int(cycles) if opened else "", int(inst) if opened else "", 1000 if opened else "",
                     100 if opened else "", int(cycles) // 4 if opened else ""))
    table(d_ / "counters.csv", header, list(m7r.COUNTER_COLUMNS) + list(EVENTS), rows)


# ---------------------------------------------------------------- runs and sessions

def write_run(d_, arm, rep, cpu, channels=8, fps=5, dec=None, sched=None, drops=0, rknn=False, snap=None,
              snap_dec_ns=None, counters=None, window=WINDOW):
    """One run of bench_main: summary.csv, channels.csv, threads.csv, frames.csv (as tools/m6_report.py's tests write
    them), its snapshots and, for a counted arm, its counters. dec: D in threads.csv; snap: write_snapshots' problem,
    or "none" for no snapshot files; snap_dec_ns: the decoder group's run time over the span; counters:
    write_counters' options."""
    d_.mkdir(parents=True)
    header = {"variant": arm, "repetition": rep, "warmup_s": WARMUP, "duration_s": window}
    sched = cpu + 2.0 if sched is None else sched
    dec = DEC.get(m7r.COUNTED.get(arm, arm), 8.4) if dec is None else dec
    sampled = channels * fps * window
    keyed(d_ / "summary.csv", header, {
        "window_s": window, "process_cpu_s": f"{cpu:.3f}", "process_cpu_a76_s": f"{cpu - 0.1:.3f}",
        "process_cpu_a55_s": "0.100", "process_cpu_unattributed_s": "0.000", "system_cpu0-3_s": "1.000",
        "system_cpu4-5_s": f"{cpu / 2:.3f}", "system_cpu6-7_s": f"{cpu / 2:.3f}",
        "system_cpu_total_s": f"{cpu + 1:.3f}",
        "schedstat_version": "15", "schedstat_cpu0-3_s": "1.000", "schedstat_cpu4-5_s": f"{(sched - 1) / 2:.3f}",
        "schedstat_cpu6-7_s": f"{(sched - 1) / 2:.3f}", "schedstat_cpu_total_s": f"{sched:.3f}",
        "sampled_in_window": sampled, "q1_drops_in_window": drops, "drop_rate": f"{drops / sampled:.5f}",
        "temp_max_mc": 40000, "rss_window_max_kb": 240000})
    processed = round(fps * window)
    table(d_ / "channels.csv", header, ["channel", "decoded_in_window", "decode_fps", "drift_max_s", "decoder_drops",
                                        "sampled_in_window", "q1_drops_in_window", "processed_in_window",
                                        "inference_fps", "ended_early"],
          [(ch, 30 * window, "30.000", "0.000", 0, fps * window, 0, processed, f"{processed / window:.3f}", 0)
           for ch in range(1, channels + 1)])
    table(d_ / "threads.csv", header, ["tid", "comm", "affinity", "cpu_s_in_window"],
          [(1, "dec-1", '"4-7"', f"{dec - 1.0:.3f}"), (2, "mpp_dec_parser", '"4-7"', "0.800"),
           (3, "mpp_dec_hal", '"4-7"', "0.200"), (4, "npu-0", '"4-7"', "5.000"),
           (5, "pre-0", '"4-7"', f"{cpu - dec - 5.1:.3f}"), (6, "metrics", '"0"', "0.100")])
    frames = []
    for ch in range(1, channels + 1):
        for k, t0 in enumerate((10**9, (WARMUP + 1) * 10**9, (WARMUP + 1) * 10**9 + 2 * 10**8)):
            t0 += ch * 10**6
            t2a = t0 + 6 * 10**6
            t2c = t2a + 12 * 10**5
            t3 = t2c + 15 * 10**6 + (k == 2) * 10**6
            frames.append([ch, 6 * k, 18000 * k, t0, t0 + 10**6, t2a, t2a + 10**6, t2c, t3, t3 + 4 * 10**5, 500, 0, 0,
                           3] + ([800000 + 1000 * ch] if rknn else []))
    table(d_ / "frames.csv", header, ["channel", "seq", "pts", "t0_ns", "t1_ns", "t2a_ns", "t2b_ns", "t2c_ns", "t3_ns",
                                      "t4_ns", "slot_wait_ns", "ctx", "slot", "detections"]
          + (["rknn_cpu_ns"] if rknn else []), frames)
    if snap != "none":
        write_snapshots(d_, problem=snap, dec_ns=round(dec * 10**9) if snap_dec_ns is None else snap_dec_ns)
    if arm in m7r.COUNTED:
        write_counters(d_, **(counters or {}))


RUNS_COLUMNS = ["seq", "name", "phase", "variant", "channels", "condition", "repetition", "attempt", "exit", "valid",
                "passed", "reasons", "warnings", "trip_samples", "replaces"]
RUN_OPTIONS = ("cpu", "trip", "replaces")


def write_session(d_, sweep, runs, idle=True, pair=True, probe=None):
    """A session of an M7 sweep. runs: [(arm, repetition, attempt, valid, options)] in run order; a lost attempt
    (valid None) is in started.tsv only. options: cpu, trip, replaces, and write_run's options. probe: the rows of
    probe/probe.csv (a header row first)."""
    channels, condition, _ = m7r.SWEEPS[sweep]
    d_.mkdir(parents=True)
    if idle:
        keyed(d_ / "idle_baseline.csv", {"run_kind": "m7"}, IDLE)
    if pair:
        write_snapshots(d_ / "idle_snapshot", m=1, span_ns=121_000_500_000, cpu0=False)
    if probe:
        (d_ / "probe").mkdir()
        rows = "".join(",".join(r) + "\n" for r in probe)
        (d_ / "probe" / "probe.csv").write_text("# tool: m7_output_probe\n" + rows, encoding="utf-8")
    keyed(d_ / "result.csv", {"run_kind": "m7", "ambient_c": "24"}, {"sweep": sweep, "session_status": "completed",
                                                                     **({"idle_snapshot": "complete", "probe": "exit 0"}
                                                                        if probe else {})})
    cpu = CPU if condition == "main" else AUX_CPU
    lines, started, seq, last = ["\t".join(RUNS_COLUMNS)], ["\t".join(RUNS_COLUMNS[:8])], 0, None
    for arm, rep, attempt, valid, options in runs:
        seq += not (attempt == 2 and last == (arm, rep))
        last = (arm, rep)
        name = f"{seq:02d}_{sweep}_{arm}_{channels}ch_r{rep}" + ("_repeat" if attempt == 2 else "")
        started.append("\t".join(map(str, [seq, name, sweep, arm, channels, condition, rep, attempt])))
        if valid is None:
            continue
        write_run(d_ / name, arm, rep, options.get("cpu", cpu[arm]), channels=channels, fps=ss.FPS[condition],
                  rknn=arm in ("Ct", "Cpt"), **{k: x for k, x in options.items() if k not in RUN_OPTIONS})
        lines.append("\t".join(map(str, [seq, name, sweep, arm, channels, condition, rep, attempt, 0, int(valid), "",
                                         "" if valid else "clock below fixed: npu_hz", "", options.get("trip", 0),
                                         options.get("replaces", "")])))
    (d_ / "runs.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (d_ / "started.tsv").write_text("\n".join(started) + "\n", encoding="utf-8")
    return d_


def full_plan():
    """For a test of the registered plan: every arm of M7 §1 is run, whatever stream_session.M7_NOT_RUN now holds
    (M7 §5)."""
    return mock.patch.multiple(ss, M7_NOT_RUN=(), M7_NOT_RUN_WHY={})


def repetition(sweep, rep, **options):
    return [(a, rep, 1, True, options.get(a, {})) for a in ss.M7_ORDERS[sweep][rep] if a not in m7r.not_run()]


def sweep_runs(sweep, **per_arm):
    """The three repetitions; per_arm: {arm: [the options of r1, r2, r3]}."""
    return [x for r in (1, 2, 3) for x in repetition(sweep, r, **{a: o[r - 1] for a, o in per_arm.items()})]


def v(x, key="process_cpu_s", fps=5.0, drop=0.0, run="s/r", **more):
    """A run's values as hypothesis() reads them."""
    out = {"process_cpu_s": d(40.0), "schedstat_total_s": None, "threads_dec_s": d(10.0), "snap_dec_120_s": None,
           "inference_fps_min": d(fps), "drop_rate": d(drop), "run": run}
    out[key] = d(x)
    if key == "process_cpu_s":
        out["schedstat_total_s"] = d(x + 2.0)
    else:
        out["snap_dec_120_s"] = d(x)
    out.update(more)
    return out


class TempDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "results" / "m7"

    def tearDown(self):
        self.tmp.cleanup()


# ---------------------------------------------------------------- the snapshots' values

class Parsers(unittest.TestCase):
    def test_interrupts(self):
        lines = m7r.parse_interrupts(interrupts(1))
        self.assertEqual(lines["37"], ("GICv3 141 Level fdab0000.npu", [100, 0]))
        self.assertEqual(lines["IPI0"], ("Rescheduling interrupts", [20, 30]))
        self.assertEqual(lines["Err"], ("", [0]))  # fewer counts than CPUs
        with self.assertRaises(ValueError):
            m7r.parse_interrupts(" 37: 1 2 GICv3\n")
        self.assertTrue(m7r.selected_irq("GICv3 141 Level fdab0000.NPU"))
        self.assertTrue(m7r.selected_irq("GICv3 150 Level fdc38100.rkvdec"))
        self.assertFalse(m7r.selected_irq("GICv3 27 Level arch_timer"))

    def test_softirqs_stat_schedstat(self):
        self.assertEqual(m7r.parse_softirqs(softirqs(2))["SCHED"], [80, 120])
        with self.assertRaises(ValueError):
            m7r.parse_softirqs("   CPU0 CPU1\n  HI: 1\n")
        self.assertEqual(m7r.parse_stat(stat(2)), (0, 6))  # the line "cpu": irq and softirq, in ticks
        self.assertEqual(m7r.parse_schedstat(schedstat(5, 6)), {0: 5, 1: 6})  # the seventh number of each cpu line
        with self.assertRaises(ValueError):
            m7r.parse_stat("intr 1\n")

    def test_counts_delta(self):
        first = m7r.parse_interrupts(interrupts(1))
        second = m7r.parse_interrupts(interrupts(3, extra=" 99:         7          1     GICv3 9 Level   new\n"))
        delta = m7r.counts_delta(first, second)
        self.assertEqual(delta["37"], [200, 0])
        self.assertEqual(delta["99"], [7, 1])  # in the second only: in full
        del second["11"]
        self.assertNotIn("11", m7r.counts_delta(first, second))  # in the first only: left out

    def test_kernel_groups_first_match(self):
        groups = {n: m7r.kernel_group(n) for n in (
            "mpp_worker_3", "irq/38-rga3", "irq/38-rga2", "rknpu_power_off", "npu_sched", "kworker/u8:2-rknpu",
            "kworker/0:1H", "ksoftirqd/7", "rcu_preempt", "mpp_workerX")}
        self.assertEqual(groups, {"mpp_worker_3": "mpp_worker", "irq/38-rga3": "rga3", "irq/38-rga2": "rest",
                                  "rknpu_power_off": "npu", "npu_sched": "npu", "kworker/u8:2-rknpu": "npu",
                                  "kworker/0:1H": "kworker", "ksoftirqd/7": "ksoftirqd", "rcu_preempt": "rest",
                                  "mpp_workerX": "rest"})

    def test_snapshot_problem(self):
        ok = {"snapshot1_taken": "1", "snapshot1_complete": "1", "snapshot2_taken": "1", "snapshot2_complete": "1"}
        self.assertEqual(m7r.snapshot_problem(ok), "")
        self.assertIn("snapshot 1 is incomplete", m7r.snapshot_problem(dict(ok, snapshot1_complete="0")))
        self.assertEqual(m7r.snapshot_problem(dict(ok, snapshot2_taken="0")), "snapshot 2 was not taken")


class Snapshots(TempDir):
    def test_other_tasks_and_own_threads(self):
        write_snapshots(self.root, m=2, dec_ns=10_500_000_000)
        s = m7r.read_snapshots(self.root)
        self.assertEqual(s["span_ns"], SPAN_NS)
        self.assertEqual(s["kernel_ns"], {"mpp_worker": 600_000_000, "rga3": 100_000_000,
                                          "npu": 20_000_000 + 40_000_000,  # rknpu_power_off and kworker/0:1-rknpu
                                          "kworker": 80_000_000 + 20_000_000,  # with kworker/0:2, second only
                                          "ksoftirqd": 60_000_000, "rest": 120_000_000})
        self.assertEqual(s["others_ns"], {"sshd": 200_000_000, "python3": 500_000_000, "new": 140_000_000})
        self.assertEqual([(t["comm"], t["run_ns"]) for t in s["only_first"]], [("short", None)])
        self.assertEqual(sorted((t["comm"], t["run_ns"]) for t in s["only_second"]),
                         [("kworker/0:2", 20_000_000), ("new", 140_000_000)])
        dec = s["own"]["groups"]["dec"]
        self.assertEqual((dec["run_ns"], dec["wait_ns"], dec["timeslices"], dec["voluntary_cs"],
                          dec["involuntary_cs"]), (10_500_000_000, 30_000_000, 3000, 2700, 300))
        self.assertEqual(s["own"]["groups"]["rest"]["run_ns"], 100_000_000)  # metrics
        self.assertEqual(s["irq"]["38"], [100, 0])
        self.assertEqual(s["softirq"]["TIMER"], [200, 400])
        self.assertEqual((s["stat_irq"], s["stat_softirq"]), (0, 6))
        self.assertEqual(s["sched_ns"], 1_400_000_000 + 4 * 10**9)

    def test_flat_values(self):
        write_snapshots(self.root, m=2, dec_ns=10_500_000_000)
        f = m7r.snapshot_flat(m7r.read_snapshots(self.root))
        self.assertEqual(f["snap_span_s"], Fraction(SPAN_NS, 10**9))
        self.assertEqual(f["snap_kernel_s"], d(1.04))
        self.assertEqual(f["snap_kernel_npu_s"], d(0.06))
        self.assertEqual(f["snap_others_s"], d(0.84))
        self.assertEqual(f["snap_tasks_s"], d(1.88))
        self.assertEqual((f["snap_only_first"], f["snap_only_second"]), (1, 2))
        self.assertEqual(f["snap_irq_total"], 2 * (2000 + 100 + 50 + 40 + 50))  # every line
        self.assertEqual((f["snap_irq_37"], f["snap_irq_38"], f["snap_irq_40"]), (200, 100, 80))
        self.assertNotIn("snap_irq_11", f)  # arch_timer: not the NPU's, RGA's or rkvdec's
        self.assertEqual((f["snap_irqcpu_40_0"], f["snap_irqcpu_40_1"]), (60, 20))
        self.assertEqual(f["snap_softirq_SCHED"], 200)
        self.assertEqual(f["own_dec_timeslices"], 3000)
        self.assertEqual(f["own_dec_wait_s"], d(0.03))
        self.assertEqual(f["snap_dec_s"], d(10.5))
        self.assertEqual(f["snap_dec_120_s"], d(10.5) * 120 / Fraction(SPAN_NS, 10**9))  # M7-H2's mixed label (§2)
        self.assertEqual(f["snap_own_s"], d(10.5) + 5 + 3 + d(0.1))

    def test_cpu0(self):
        write_snapshots(self.root, cycles=2_700_000_000)
        f = m7r.snapshot_flat(m7r.read_snapshots(self.root))
        self.assertEqual(f["cpu0_busy_s"], d(1.5))  # 2.7e9 cycles at 1800 MHz
        self.assertEqual(f["cpu0_sched_s"], d(1.4))  # between the two snapshots' reads of /proc/schedstat
        self.assertEqual(f["cpu0_diff_s"], d(0.1))
        self.assertEqual(f["cpu0_end_reads_s"], d(0.002))  # cpu0_counter_read_ns − end_tick_wake_ns
        self.assertEqual(f["cpu0_diff_less_end_s"], d(0.098))
        self.assertEqual(f["cpu0_start_slice_s"], d(0.003001))  # cpu0_counter_enable_ns − snapshot1_tick_wake_ns
        self.assertEqual(f["cpu0_diff_both_s"], d(0.1) - d(0.002) + d(0.003001))
        self.assertFalse(f["cpu0_multiplexed"])
        meta = m7r.meta_values(self.root / "snapshot_meta.csv")
        older = {k: x for k, x in meta.items() if k != "snapshot1_tick_wake_ns"}  # a record without the key
        x = m7r.cpu0_values(older, {0: 0}, {0: 1_400_000_000})[0]
        self.assertEqual((x["cpu0_diff_less_end_s"], x["cpu0_start_slice_s"], x["cpu0_diff_both_s"]),
                         (d(0.098), None, None))
        self.assertEqual(m7r.cpu0_values(dict(meta, cpu0_counter_read_ok="0"), {0: 0}, {0: 1})[0], None)
        self.assertIn("read_ok", m7r.cpu0_values(dict(meta, cpu0_counter_read_ok="0"), {0: 0}, {0: 1})[1])

    def test_less_idle_scaled_to_the_run_span(self):
        write_snapshots(self.root / "run", m=2)
        write_snapshots(self.root / "idle", m=1, span_ns=121 * 10**9, cpu0=False)
        run = m7r.snapshot_flat(m7r.read_snapshots(self.root / "run"))
        idle = m7r.snapshot_flat(m7r.read_snapshots(self.root / "idle"))
        less = m7r.less_idle(run, idle)
        scale = Fraction(SPAN_NS, 121 * 10**9)
        self.assertEqual(less["less_kernel_s"], d(1.04) - d(0.52) * scale)
        self.assertEqual(less["less_irq_37"], 200 - 100 * scale)
        self.assertEqual(less["less_stat_softirq"], 6 - 3 * scale)
        self.assertEqual(less["less_sched_s"], run["snap_sched_s"] - idle["snap_sched_s"] * scale)
        self.assertNotIn("less_dec_s", less)  # bench_main's threads have no idle value
        self.assertNotIn("less_span_s", less)
        self.assertEqual(m7r.less_idle(run, None), {})

    def test_unreadable_snapshots_leave_the_run_valid(self):
        for problem, expected in (("incomplete", "snapshot 1 is incomplete"),
                                  ("missing", "missing: snapshot_tasks.csv"), ("garbled", "ValueError")):
            with self.subTest(problem=problem):
                d_ = self.root / problem
                write_run(d_, "C", 1, 20.0, snap=problem)
                x = m7r.run_values(d_, IDLE, "m7_main", "C", None)
                self.assertEqual(x["process_cpu_s"], 20)
                self.assertIsNone(x["snap"])
                self.assertIn(expected, x["snap_problem"])
                self.assertIsNone(x.get("snap_dec_120_s"))


class Counters(TempDir):
    def test_groups(self):
        self.root.mkdir(parents=True)
        write_counters(self.root, dec_inst=30 * 10**9, dec_cycles=22_080_000_000, failed="open")
        groups, events = m7r.read_counters(self.root)
        self.assertEqual(events, list(EVENTS))
        self.assertEqual((groups["dec"]["cpu_cycles"], groups["dec"]["inst_retired"]), (22_080_000_000, 30 * 10**9))
        self.assertEqual(groups["rest"]["left_out"], ["metrics"])
        f = m7r.counters_flat(groups, events)
        self.assertEqual(f["ctr_dec_ipc"], Fraction(30, Fraction("22.08")))
        self.assertEqual(f["ctr_dec_cycles_s"], 10)  # cycles / 2208 MHz
        self.assertEqual(f["ctr_dec_run_s"], 10)  # run_ns_disable − run_ns_enable, summed over the group's threads
        self.assertEqual((f["ctr_dec_ratio"], f["ctr_dec_mhz"]), (1, 2208))
        self.assertEqual(f["ctr_rest_left_out"], 1)
        self.assertEqual(f["ctr_npu_l2d_cache_refill"], 1000)

    def test_a_failed_disable_leaves_the_group_out(self):
        self.root.mkdir(parents=True)
        write_counters(self.root, failed="disable")  # opened, enabled and read, but not disabled
        groups, _ = m7r.read_counters(self.root)
        self.assertEqual((groups["npu"]["left_out"], groups["npu"]["threads"], groups["npu"]["cpu_cycles"]),
                         (["npu-0"], 0, 0))
        self.assertEqual(groups["dec"]["threads"], 3)
        rows = (self.root / "counters.csv").read_text(encoding="utf-8")
        for column in ("opened", "enable_ok", "read_ok"):  # each of the four flags alone leaves the group out
            with self.subTest(column=column):
                i = m7r.COUNTER_COLUMNS.index(column)
                lines = [x.split(",") for x in rows.splitlines()]
                for x in lines:
                    if x[1:2] == ["dec-1"]:
                        x[i] = "0"
                (self.root / "counters.csv").write_text("\n".join(",".join(x) for x in lines) + "\n",
                                                        encoding="utf-8")
                self.assertEqual(m7r.read_counters(self.root)[0]["dec"]["left_out"], ["dec-1"])


# ---------------------------------------------------------------- the hypotheses

def h1_runs(**cpu):
    return {a: [v(x, run=f"s/{a}_r{r}", fps=30.0) for r in (1, 2, 3)] for a, x in cpu.items()}


def h2_runs(**dec):
    return {a: [v(x, key="threads_dec_s", run=f"s/{a}_r{r}") for r in (1, 2, 3)] for a, x in dec.items()}


class Hypotheses(unittest.TestCase):
    def check(self, name, runs, expected):
        h = m7r.hypothesis(name, runs)
        self.assertEqual((h["spec"], h["practical"]), (expected, expected), h["flags"])
        return h

    def test_h1_threshold_exactly(self):
        # (130.95 − 37.45 − 9.35) / 93.5 = 0.9 exactly: floating point would put it below
        h = self.check("M7-H1", h1_runs(B=130.95, Rc=46.8, C=37.45), "adopted")
        self.assertEqual(h["value"], Fraction(9, 10))
        self.check("M7-H1", h1_runs(B=130.95, Rc=46.81, C=37.45), "rejected")
        self.assertEqual(self.check("M7-H1", h1_runs(B=130.0, Rc=40.0, C=37.0), "adopted")["value"],
                         Fraction(90, 93))

    def test_h2_threshold_exactly(self):
        h = self.check("M7-H2", h2_runs(Bd=10.05, C=10.55), "adopted")
        self.assertEqual(h["value"], Fraction(1, 2))
        self.assertEqual(self.check("M7-H2", h2_runs(Bd=10.05, C=10.549), "rejected")["value"], d(0.499))
        self.check("M7-H2", h2_runs(Bd=11.0, C=10.0), "rejected")  # C below Bd

    def test_h1_mixed(self):
        runs = h1_runs(B=130.0, Rc=47.0, C=37.0)  # 83 / 93: rejected
        self.assertIs(m7r.hypothesis("M7-H1", runs)["mixed"], False)  # schedstat: the same differences
        for r in runs["Rc"]:
            r["schedstat_total_s"] = d(40.0)  # (132 − 40) / 93 on schedstat: above 0.90
        h = m7r.hypothesis("M7-H1", runs)
        self.assertEqual((h["spec"], h["mixed"]), ("rejected", True))

    def test_h2_mixed_from_the_snapshots(self):
        runs = h2_runs(Bd=10.0, C=11.0)
        self.assertIs(m7r.hypothesis("M7-H2", runs)["mixed"], False)
        for r in runs["C"]:
            r["snap_dec_120_s"] = d(10.4999)
        h = m7r.hypothesis("M7-H2", runs)
        self.assertEqual((h["spec"], h["mixed"], h["secondary_value"]), ("adopted", True, d(0.4999)))
        for r in runs["C"]:
            r["snap_dec_120_s"] = d(10.5)  # exactly 0.50 s on the snapshots: met, so not mixed
        self.assertIs(m7r.hypothesis("M7-H2", runs)["mixed"], False)

    def test_h2_no_label_without_snapshots(self):
        runs = h2_runs(Bd=10.0, C=11.0)
        runs["Bd"][1]["snap_dec_120_s"] = None
        h = m7r.hypothesis("M7-H2", runs)
        self.assertEqual((h["spec"], h["practical"], h["mixed"]), ("adopted", "adopted", None))
        self.assertIn("no complete snapshots in s/Bd_r2: no mixed label (§2)", h["flags"])

    def test_h1_partial_schedstat(self):
        runs = h1_runs(B=130.0, Rc=40.0, C=37.0)
        runs["B"][0]["schedstat_total_s"] = None
        h = m7r.hypothesis("M7-H1", runs)
        self.assertIs(h["mixed"], False)
        self.assertIn("no /proc/schedstat value less idle in some runs that count (B 1 of 3)", "; ".join(h["flags"]))

    def test_slow_channel_by_point(self):
        runs = h1_runs(B=130.0, Rc=40.0, C=37.0)
        runs["Rc"][2]["inference_fps_min"] = d(28.49)
        h = m7r.hypothesis("M7-H1", runs)
        self.assertEqual((h["spec"], h["practical"]), ("adopted", "indeterminate"))
        self.assertIn("a channel below 28.5 fps in s/Rc_r3 (§2)", h["flags"])
        runs["Rc"][2]["inference_fps_min"] = Fraction(3420, 120)  # 28.5 exactly: not below
        self.assertEqual(m7r.hypothesis("M7-H1", runs)["practical"], "adopted")
        runs = h2_runs(Bd=10.0, C=11.0)
        runs["Bd"][0]["inference_fps_min"] = d(4.74)
        self.assertIn("a channel below 4.75 fps in s/Bd_r1 (§2)", m7r.hypothesis("M7-H2", runs)["flags"])
        runs["Bd"][0]["inference_fps_min"] = d(4.75)
        self.assertEqual(m7r.hypothesis("M7-H2", runs)["practical"], "adopted")

    def test_drop_rates(self):
        runs = h2_runs(Bd=10.0, C=11.0)
        for r in runs["Bd"]:
            r["drop_rate"] = d(0.0101)
        h = m7r.hypothesis("M7-H2", runs)
        self.assertEqual(h["practical"], "indeterminate")
        self.assertIn("the mean drop rates of Bd and C differ by 1.010 percentage points, more than 1 (§2)",
                      h["flags"])
        for r in runs["Bd"]:
            r["drop_rate"] = d(0.01)  # exactly 1 point: not more
        self.assertEqual(m7r.hypothesis("M7-H2", runs)["practical"], "adopted")

    def test_missing_runs(self):
        runs = h1_runs(B=130.0, Rc=40.0, C=37.0)
        runs["Rc"] = runs["Rc"][:2]
        h = m7r.hypothesis("M7-H1", runs)
        self.assertEqual((h["spec"], h["practical"]), ("adopted", "indeterminate"))
        self.assertIn("fewer than 3 runs that count (Rc 2)", "; ".join(h["flags"]))
        runs["Rc"] = []
        h = m7r.hypothesis("M7-H1", runs)
        self.assertEqual((h["spec"], h["practical"], h["value"]), ("indeterminate", "indeterminate", None))

    def test_not_run(self):
        with mock.patch.object(ss, "M7_NOT_RUN", ("Rc",)), \
                mock.patch.object(ss, "M7_NOT_RUN_WHY", {"Rc": "its check failed (results/check/x)"}):
            h = m7r.hypothesis("M7-H1", h1_runs(B=130.0, Rc=40.0, C=37.0))
            self.assertEqual((h["spec"], h["practical"]), ("indeterminate", "indeterminate"))
            self.assertEqual(h["flags"], ["Rc is not run: its check failed (results/check/x); both verdicts are "
                                          "indeterminate (§2, §5)"])
            self.assertEqual(m7r.sweep_arms("m7_aux"), ("B", "Bd", "C", "Ct", "Cpt"))
            self.assertEqual(m7r.order_position("m7_aux", 3, "C"), 1)  # §4: the order of the others stands

    def test_cv_flags(self):
        runs = h1_runs(B=130.0, C=37.0)
        runs["Rc"] = [v(x, fps=30.0) for x in (36.0, 40.0, 44.0)]  # CV 10% exactly: not above
        self.assertEqual([f for f in m7r.hypothesis("M7-H1", runs)["flags"] if f.startswith("CV")], [])
        runs["Rc"] = [v(x, fps=30.0) for x in (35.9, 40.0, 44.1)]
        self.assertEqual([f for f in m7r.hypothesis("M7-H1", runs)["flags"] if f.startswith("CV")],
                         ["CV of Rc's process CPU 10.25%, above 10% (§4)"])
        runs = h2_runs(Bd=10.0)
        runs["C"] = [v(x, key="threads_dec_s") for x in (9.0, 11.0, 13.0)]
        self.assertEqual([f for f in m7r.hypothesis("M7-H2", runs)["flags"] if f.startswith("CV")],
                         ["CV of C's decoder group's CPU 18.18%, above 10% (§4)"])


# ---------------------------------------------------------------- §5's and §6's rules

class Rules(unittest.TestCase):
    def by_rep(self, inst, ipc):
        """Three repetitions of Ck and Bdk whose decoder group differs by inst (×10⁹) and ipc per repetition."""
        out = {}
        for r in (1, 2, 3):
            out[(r, "Bdk")] = {"ctr_dec_inst_retired": 30 * 10**9, "ctr_dec_ipc": Fraction(3, 2)}
            out[(r, "Ck")] = {"ctr_dec_inst_retired": 30 * 10**9 + inst[r - 1] * 10**9,
                              "ctr_dec_ipc": Fraction(3, 2) + ipc[r - 1]}
        return out

    def test_description(self):
        self.assertEqual(m7r.description(self.by_rep((1, 1.1, 0.9), (0, 0, 0)), "Ck", "Bdk"),
                         "with a difference in instructions (+)")
        self.assertEqual(m7r.description(self.by_rep((0, 0, 0), (d(-0.1), d(-0.12), d(-0.08))), "Ck", "Bdk"),
                         "with a difference in instructions per cycle (−)")
        self.assertEqual(m7r.description(self.by_rep((1, 1.1, 0.9), (d(-0.1), d(-0.12), d(-0.08))), "Ck", "Bdk"),
                         "with a difference in instructions (+) and in instructions per cycle (−)")
        self.assertEqual(m7r.description(self.by_rep((1, -1, 0.5), (0, 0, 0)), "Ck", "Bdk"), "not shown")
        by_rep = self.by_rep((1, 1.1, 0.9), (0, 0, 0))
        del by_rep[(2, "Ck")], by_rep[(3, "Ck")]
        self.assertEqual(m7r.description(by_rep, "Ck", "Bdk"), "not shown")  # one pair: no interval

    def test_excludes_zero(self):
        self.assertTrue(m7r.excludes_zero(1.0, 0.5))
        self.assertTrue(m7r.excludes_zero(-1.0, 0.5))
        self.assertFalse(m7r.excludes_zero(0.5, 0.5))  # an end at zero: not excluded
        self.assertFalse(m7r.excludes_zero(1.0, None))

    def test_counter_cost(self):
        per_arm = {"C": [{"process_cpu_s": d(20.0)}] * 3}
        by_rep = {(r, a): {"process_cpu_s": d(x)} for r in (1, 2, 3) for a, x in (("C", 20.0), ("Ck", 20.4))}
        self.assertEqual(m7r.counter_cost(by_rep, per_arm, "Ck"), (d(0.4), d(0.4), False))  # exactly 2%: not above
        by_rep[(1, "Ck")] = {"process_cpu_s": d(20.403)}
        self.assertEqual(m7r.counter_cost(by_rep, per_arm, "Ck")[2], True)
        self.assertEqual(m7r.counter_cost({}, per_arm, "Ck"), (None, None, None))

    def test_per_inference(self):
        self.assertAlmostEqual(m7r.per_inference_ms(Fraction(8, 5), "m7_main"), 1.6 / 4.8)  # M6's 0.33 ms
        self.assertAlmostEqual(m7r.per_inference_ms(6.0, "m7_aux"), 6.0 / 18)
        self.assertIsNone(m7r.per_inference_ms(None, "m7_aux"))

    @full_plan()
    def test_orders(self):
        self.assertEqual(ss.M7_ORDERS["m7_main"][2], ("Ck", "Rck", "Bdk", "Bk", "C", "Rc", "Bd", "B"))
        self.assertEqual(ss.M7_ORDERS["m7_aux"][2], ("Cpt", "Ct", "C", "Rc", "Bd", "B"))
        self.assertEqual(m7r.order_position("m7_main", 3, "Bk"), 8)
        self.assertIsNone(m7r.order_position("m7_aux", 4, "B"))


# ---------------------------------------------------------------- the sweeps and the report

class Sweeps(TempDir):
    def sessions(self, main_runs=None, aux_runs=None, **options):
        main = write_session(self.root / "20261201T000000Z_m7_main", "m7_main", main_runs or sweep_runs("m7_main"),
                             **options)
        aux = write_session(self.root / "20261201T020000Z_m7_aux", "m7_aux", aux_runs or sweep_runs("m7_aux"),
                            probe=[("config", "layout", "type", "status", "cpu_median_ms"),
                                   ("1", "NCHW", "INT8", "accepted", "0.844"),
                                   ("5", "native", "INT8", "refused: rknn_run failed", "")])
        return main, aux

    @full_plan()
    def test_collect_and_values(self):
        main, aux = self.sessions()
        c = m7r.collect([main], "m7_main")
        self.assertEqual(c["complete"], [1, 2, 3])
        self.assertEqual([c["counting"][(r, "Rck")]["seq"] for r in (1, 2, 3)], ["7", "10", "22"])
        self.assertEqual([e["position"] for e in c["every"] if e["variant"] == "B"], [1, 8, 7])
        x = c["counting"][(1, "C")]
        self.assertEqual(x["threads_dec_s"], d(11.0))
        self.assertEqual(x["snap_dec_s"], d(11.0))
        scale = Fraction(SPAN_NS, 121_000_500_000)
        self.assertEqual(x["less_kernel_s"], d(1.04) - d(0.52) * scale)  # m = 2 against the idle pair's m = 1
        self.assertEqual(x["left_window_s"], d(22.0) - d(1.2) - 20 - x["less_tasks_s"])
        self.assertEqual(x["stat_excess_s"], (d(21.0) - d(1.0)) - (d(22.0) - d(1.2)))
        self.assertNotIn("ctr_dec_inst_retired", x)  # C: no counters
        k = c["counting"][(1, "Ck")]
        self.assertEqual(k["ctr_dec_inst_retired"], 30 * 10**9)
        self.assertEqual(k["counters_problem"], "")
        a = m7r.collect([aux], "m7_aux")
        ct = a["counting"][(1, "Ct")]
        self.assertEqual(ct["rknn_run_calls"], 10)  # two frames of each of 5 channels in the window
        self.assertEqual(ct["npu_per_inference_ms"], Fraction(5000, 10))
        self.assertEqual(ct["npu_rest_ms"], ct["npu_per_inference_ms"] - ct["rknn_run_cpu_mean_ms"])
        h = m7r.hypothesis("M7-H1", a["per_arm"])  # (130 − 39) / (130 − 37)
        self.assertEqual((h["value"], h["spec"], h["practical"]), (Fraction(91, 93), "adopted", "adopted"))
        h = m7r.hypothesis("M7-H2", c["per_arm"])
        self.assertEqual((h["value"], h["spec"], h["practical"], h["mixed"]), (1, "adopted", "adopted", False))

    def test_arrival_at_the_aux_period(self):
        d_ = self.root / "r"
        write_run(d_, "C", 1, 37.0, channels=5, fps=30)
        frames = (d_ / "frames.csv").read_text(encoding="utf-8").splitlines()
        out = []
        for line in frames:
            if line[0].isdigit():
                f = line.split(",")
                ch = int(f[0])
                shift = (ch - 1) * 10**7 - ch * 10**6  # channels at 0, 10, 20, 30 and 40 ms
                f[3] = str(int(f[3]) + shift)
                line = ",".join(f)
            out.append(line)
        (d_ / "frames.csv").write_text("\n".join(out) + "\n", encoding="utf-8")
        x = m7r.arrival(d_, 30)
        # phases 0, 6.67 (40 ms less a period), 10, 20 and 30 ms of a 33.3 ms period: the largest gap is 10 ms
        self.assertAlmostEqual(x["arrival_spread_ms"], 1000 / 30 - 10, 6)
        self.assertAlmostEqual(x["arrival_drift_ms"], 0.0, 6)

    @full_plan()
    def test_snapshots_missing_in_a_run_that_counts(self):
        runs = sweep_runs("m7_main", C=[{}, {"snap": "incomplete"}, {}])
        main = write_session(self.root / "20261201T000000Z_m7_main", "m7_main", runs)
        c = m7r.collect([main], "m7_main")
        self.assertEqual(c["counting"][(2, "C")]["process_cpu_s"], 20)  # the run stays valid and counts (§4)
        h = m7r.hypothesis("M7-H2", c["per_arm"])
        self.assertEqual((h["spec"], h["mixed"]), ("adopted", None))
        self.assertTrue(any(f.startswith("no complete snapshots in 20261201T000000Z_m7_main/13_m7_main_C_8ch_r2")
                            for f in h["flags"]), h["flags"])
        section = "\n".join(m7r.missing_section({"m7_main": c, "m7_aux": m7r.collect([], "m7_aux")}))
        self.assertIn("| `20261201T000000Z_m7_main/13_m7_main_C_8ch_r2` | C | yes | snapshot 1 is incomplete: it "
                      "stopped at its deadline (§1) |", section)

    @full_plan()
    def test_cost_and_description_from_records(self):
        ck = [{"cpu": 20.5, "counters": {"dec_inst": i}} for i in (31 * 10**9, 31_100_000_000, 30_900_000_000)]
        main = write_session(self.root / "20261201T000000Z_m7_main", "m7_main", sweep_runs("m7_main", Ck=ck))
        c = m7r.collect([main], "m7_main")
        self.assertEqual(m7r.description(c["counting"], "Ck", "Bdk"),
                         "with a difference in instructions (+) and in instructions per cycle (+)")
        mean, limit, above = m7r.counter_cost(c["counting"], c["per_arm"], "Ck")
        self.assertEqual((mean, limit, above), (d(0.5), d(0.4), True))
        section = "\n".join(m7r.cost_section(c))
        self.assertIn("| Ck − C | 0.500 | 0.400 | above 2%: Ck is not C with counters added but an arm of its own "
                      "(§5) |", section)
        self.assertIn("| Bk − B | 0.400 | 0.800 | not above 2% |", section)

    @full_plan()
    def test_report(self):
        main, aux = self.sessions()
        out = Path(self.tmp.name) / "M7_tables.md"
        with mock.patch.object(m7r, "references", return_value={}):
            self.assertEqual(m7r.main(["--session", str(main), "--session", str(aux), "--out", str(out)]), 0)
            text = out.read_text(encoding="utf-8")
            self.assertEqual(m7r.main(["--session", str(main), "--session", str(aux), "--out", str(out),
                                       "--cpu0-check", "passed"]), 0)
            passed = out.read_text(encoding="utf-8")
        self.assertIn("Both sweeps are complete: the verdicts below are M7's (§2).", text)
        self.assertIn("| M7-H1: (B − Rc) / (B − C) ≥ 0.90 | `m7_aux` | B 130.000 (3), Rc 39.000 (3), C 37.000 (3) | "
                      "0.9785 | 0.90 | adopted | adopted |", text)
        self.assertIn("| M7-H2: D(C) − D(Bd) ≥ 0.50 s | `m7_main` | Bd 10.000 (3), C 11.000 (3) | 1.0000 | 0.50 s | "
                      "adopted | adopted | Bd 9.917, C 10.909 | 0.9917 | none | none |", text)
        self.assertIn("What a rejection says: that the difference of M1.1 and M6 did not repeat at this size.", text)
        self.assertIn("| B − Rc | 18.000 | +90.00% |", text)  # m7_main
        self.assertIn("| B − Rc | 18.000 (+90.00%) | 23.107 (+93.52%) | 91.000 (+97.85%) | – (–) |", text)
        self.assertIn("| Rc − C | 0.4167 | 0.3333 | 0.1111 | – |", text)  # per inference: 2/4800, 1.6/4800, 2/18000
        self.assertIn("| Rc, M6 (other session) | – | 1 | 3 | 20.19 | 11.05 |", text)
        self.assertIn("| Rck | `20261201T000000Z_m7_main/10_m7_main_Rck_8ch_r2` | 2 | 10 | 22.200 | 11.600 |", text)
        self.assertIn("| Ck − Bdk | 1.000 | not shown |", text)  # equal counters in every repetition
        self.assertIn("**cpu0's counter failed its check**", text)
        self.assertNotIn("Busy by the counter", text)
        self.assertIn("| Busy by the counter (s) | 1.5000 ± 0.0000 (3) |", passed)
        self.assertIn("| Difference, without them (s) | 0.0980 ± 0.0000 (3) |", passed)
        self.assertIn("| The start slice (s) | 0.0030 ± 0.0000 (3) |", passed)
        self.assertIn("| Difference, both slices corrected (s) | 0.1010 ± 0.0000 (3) |", passed)
        self.assertIn("(counter − /proc/schedstat − end slice + start slice)", passed)
        self.assertIn("| 1 | NCHW | INT8 | accepted | 0.844 |", text)  # the probe's table
        self.assertIn("| 5 | native | INT8 | refused: rknn_run failed | – |", text)
        self.assertIn("| `m7_aux` | Ct − C | Process CPU (s) | 0.500 | 0.500 | 0.500 | 0.500 | 0.500 to 0.500 s |",
                      text)
        self.assertIn("| `m7_main` | Ck − C | Process CPU (s) | 0.200 | 0.200 | 0.200 | 0.200 | 0.200 to 0.200 s |",
                      text)
        self.assertIn("| Thread CPU across rknn_run, NPU workers (s) | 0.008 ± 0.000 (3) | 0.008 ± 0.000 (3) | 5.794 |",
                      text)
        self.assertIn("| Kernel threads, rknpu or npu in the name (s) |", text)
        self.assertIn("A kworker's name carries its current work item, so a kworker running an rknpu item at the "
                      "second snapshot counts in the npu group.", text)
        self.assertIn("| C − B | kernel's side | Kernel threads, all (s) | 0.000 | 0.000 | 0.000 | 0.000 |", text)
        self.assertIn("| Interrupts, line 40 (GICv3 150 Level fdc38100.rkvdec) |", text)
        self.assertIn("python3 0.500", text)  # the largest other process by name
        self.assertIn(f"| `{main.name}` | `m7_main` | 24 | completed | 24 | yes | yes | no | no |", text)
        self.assertIn(f"| `{aux.name}` | `m7_aux` | 18 | completed | 24 | yes | complete | exit 0 | no |", text)
        self.assertIn("| none | | | | | |", text)  # no run lacks its snapshots or counters
        self.assertIn("| `20261201T000000Z_m7_main/01_m7_main_B_8ch_r1` | B | short | new 0.140, kworker/0:2 0.020 |",
                      text)
        self.assertIn("No session is superseded", text)

    def test_counted_arms_not_run(self):
        why = "the check of the counters failed (results/check/x)"
        with mock.patch.object(ss, "M7_NOT_RUN", tuple(m7r.COUNTED)), \
                mock.patch.object(ss, "M7_NOT_RUN_WHY", dict.fromkeys(m7r.COUNTED, why)):
            main = write_session(self.root / "20261201T000000Z_m7_main", "m7_main", sweep_runs("m7_main"))
            self.assertEqual(len((main / "runs.tsv").read_text(encoding="utf-8").splitlines()), 13)
            c = m7r.collect([main], "m7_main")
            self.assertEqual(c["complete"], [1, 2, 3])
            text = "\n".join(m7r.counters_section(c) + m7r.cost_section(c))
            self.assertEqual(m7r.hypothesis("M7-H2", c["per_arm"])["spec"], "adopted")  # §5: H2 is not affected
        self.assertIn(f"The counted arms are not run (§5): Bk ({why})", text)
        self.assertIn("| Ck − C | – | – | not run |", text)

    def test_redone_repetition_and_superseded(self):
        first = write_session(self.root / "20261201T000000Z_m7_aux", "m7_aux", repetition("m7_aux", 1) + [
            ("Cpt", 2, 1, True, {"cpu": 50.0}), ("Ct", 2, 1, None, {})])
        replaced = f"{first.name}/07_m7_aux_Cpt_5ch_r2 {first.name}/08_m7_aux_Ct_5ch_r2"
        second = write_session(self.root / "20261202T000000Z_m7_aux", "m7_aux",
                               [(a, 2, 1, True, {"replaces": replaced}) for a in ss.M7_ORDERS["m7_aux"][2]]
                               + repetition("m7_aux", 3))
        c = m7r.collect([first, second], "m7_aux")
        self.assertEqual(c["complete"], [1, 2, 3])
        self.assertEqual(c["counting"][(2, "Cpt")]["process_cpu_s"], 32)
        old = next(e for e in c["every"] if e["name"] == "07_m7_aux_Cpt_5ch_r2")
        self.assertEqual(old["why"], f"its repetition was rerun in {second.name} (runs.tsv replaces)")
        self.assertEqual(m7r.session_dirs([second]), ({"m7_main": [], "m7_aux": [first, second]}, False))
        with mock.patch.object(ss, "M7_SUPERSEDED", (first.name,)):
            c = m7r.collect([first, second], "m7_aux")
            self.assertEqual((c["sessions"], c["superseded"]), ([second], [first]))
            self.assertTrue(all(e["why"].startswith("superseded (M7 §5, §9)") for e in c["superseded_runs"]))
            self.assertEqual(c["complete"], [2, 3])

    def test_sessions_and_errors(self):
        main, aux = self.sessions()
        self.assertEqual(m7r.m7_sessions(Path(self.tmp.name)), {"m7_main": [main], "m7_aux": [aux]})
        smoke = write_session(Path(self.tmp.name) / "results" / "check" / "20261201T000000Z_m7_aux_smoke", "m7_aux",
                              repetition("m7_aux", 1))
        self.assertEqual(m7r.session_dirs([smoke]), ({"m7_main": [], "m7_aux": [smoke]}, True))
        out = Path(self.tmp.name) / "smoke.md"
        with mock.patch.object(m7r, "references", return_value={}), mock.patch("sys.stderr", new=io.StringIO()) as err:
            self.assertEqual(m7r.main(["--session", str(smoke), "--out", str(out)]), 0)
            self.assertIn("**A smoke test:**", out.read_text(encoding="utf-8"))
            other = self.root / "20261201T000000Z_m6"
            other.mkdir(parents=True)
            self.assertEqual(m7r.main(["--session", str(other)]), 2)
            self.assertIn("is not an m7_main or m7_aux session", err.getvalue())
            self.assertEqual(m7r.main(["--session", str(self.root / "nothing")]), 2)
            partial = write_session(self.root / "20261203T000000Z_m7_main", "m7_main", repetition("m7_main", 1))
            text = m7r.report(m7r.session_dirs([partial])[0], refs={})
        self.assertIn("**No outcome yet:** 3 of 3 repetitions of `m7_main`, 0 of 3 repetitions of `m7_aux` are "
                      "complete.", text)

    def test_no_idle_pair(self):
        main = write_session(self.root / "20261201T000000Z_m7_main", "m7_main", sweep_runs("m7_main"), pair=False)
        c = m7r.collect([main], "m7_main")
        x = c["counting"][(1, "B")]
        self.assertEqual(x["snap_kernel_s"], d(1.04))  # the run's own values stay
        self.assertEqual((x.get("less_kernel_s"), x["others_less_idle"], x["left_window_s"]), (None, {}, None))
        self.assertEqual(m7r.top_five(c["per_arm"]["B"]), "python3 0.500 (– less idle); sshd 0.200 (– less idle); "
                                                           "new 0.140 (– less idle)")
        self.assertIn("| `20261201T000000Z_m7_main` | no idle_snapshot | – | – | – | – |", m7r.idle_table(c))

    def test_unreadable_run_that_counts(self):
        main = write_session(self.root / "20261201T000000Z_m7_main", "m7_main", sweep_runs("m7_main"))
        (main / "01_m7_main_B_8ch_r1" / "threads.csv").unlink()
        with mock.patch.object(m7r, "references", return_value={}), \
                mock.patch("sys.stderr", new=io.StringIO()) as err:
            self.assertEqual(m7r.main(["--session", str(main)]), 2)
        self.assertIn("m7_report: a run that counts cannot be read: 20261201T000000Z_m7_main/01_m7_main_B_8ch_r1",
                      err.getvalue())


class References(unittest.TestCase):
    def test_earlier_milestones_from_their_records(self):
        # docs/notes/cpu_breakdown.md's M1 and M1.1 at 5 channels × 30 fps; prereg/M7.md's M6 at H1's point
        refs = m7r.references()
        cpu = {p: {k: {a: round(m7r.mean_of(runs, "process_cpu_s"), 3) for a, runs in arms.items()}
                   for k, arms in labels.items()} for p, labels in refs.items()}
        self.assertEqual(cpu["aux"], {"M1": {"B": 129.587, "C": 35.680}, "M1.1": {"B": 130.953, "C": 37.447}})
        self.assertEqual(cpu["main"]["M6"], {"B": float(m7r.M6_CELLS["B"]), "C": float(m7r.M6_CELLS["C"])})
        self.assertEqual(cpu["main"]["M1"], {"B": 43.283, "C": 18.560})


if __name__ == "__main__":
    unittest.main()
