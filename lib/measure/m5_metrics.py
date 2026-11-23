"""prereg/M5.md §3: the service's metrics, from the files a run of lib/measure/m5_session.py leaves.

Everything here is a function of its arguments or of a run's directory: no clock, no /proc, no systemd.
lib/measure/test_m5.py tests it on the PC; m5_session.py calls it on the board after a run, and
m5_summary.py calls it again from the stored files.

The files of a service run (arm P or P4), in its directory:
  lines.jsonl          every line read from the socket, as received, in the order of arrival
  marks.csv            key,value: t_svc_ns, t_start_ns, t_w_ns, t_e_ns, the two timed reads of
                       /proc/<MainPID>/stat with their times, and the tool's own CPU at both
  proc_stat_w.txt, proc_stat_e.txt, schedstat_w.txt, schedstat_e.txt   /proc/stat and /proc/schedstat
  affinity_w.tsv, affinity_e.tsv    tid, name, the CPUs the thread is allowed
  samples.csv          one row per second from t_start: clocks, temperatures, NPU load, VmRSS
  thread_samples.csv   one row per second and thread: utime + stime in ticks, the CPU it was last on
  journal.jsonl        journalctl -o json of the unit, from the run's start to after its stop
  unit.txt             systemctl show of the unit, at the start and after the stop

Times named *_ns are CLOCK_MONOTONIC in nanoseconds, the clock of the lines' capture_ns and out_ns.
"""
import csv
import json
import math
import re
from pathlib import Path

CHANNELS = 8
EVERY = 6            # the shipped every_nth_frame (M5 §1)
FPS = 30             # the streams' frame rate
SLOT_MS = 200.0      # 1 frame in 6 at 30 fps: the circle of the arrival phases (M2 §3.1)
LATE_READ_NS = 50_000_000   # §3: a timed read more than 50 ms off makes the run invalid
FIRST_LINES_S = 60   # §4 rule 2: every channel has a frame line by t_svc + 60 s
MIN_FPS = 4.75       # §2: the practical verdict's inference rate
A55, A76_SLOW, A76_FAST = range(0, 4), range(4, 6), range(6, 8)
CLUSTERS = (("0-3", A55), ("4-5", A76_SLOW), ("6-7", A76_FAST))
DOMAINS = ("policy0_khz", "policy4_khz", "policy6_khz", "dmc_hz", "npu_hz")
SOURCE_EVENTS = ("source_opened", "source_lost", "source_ended", "source_error")
FRAME_FIELDS = ("channel", "seq", "pts_us", "capture_ns", "out_ns", "decode_errors")


# ---------------------------------------------------------------- the lines

def parse_lines(text):
    """(frames, events, problems) of the socket's text. frames keep the order of arrival, each with its
    index in `arrival`. A last line without a newline is dropped, as the package's documentation says a
    reader should; any other line that is not a JSON object, or a frame line without an integer in every
    field of FRAME_FIELDS, is a problem."""
    frames, events, problems = [], [], []
    lines = text.split("\n")
    if lines and lines[-1] != "":
        lines[-1] = ""  # cut short by the reader's close
    for i, line in enumerate(lines):
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            problems.append(f"line {i + 1} is not JSON")
            continue
        if not isinstance(obj, dict):
            problems.append(f"line {i + 1} is not a JSON object")
        elif "event" in obj:
            events.append(obj)
        elif all(isinstance(obj.get(k), int) and not isinstance(obj.get(k), bool) for k in FRAME_FIELDS):
            obj["arrival"] = len(frames)
            frames.append(obj)
        else:
            problems.append(f"line {i + 1} is a frame line without every field of {', '.join(FRAME_FIELDS)}")
    return frames, events, problems


def by_channel(frames):
    """{channel: its frame lines in the order of seq} (§3: three NPU workers can finish out of order)."""
    out = {}
    for f in frames:
        out.setdefault(f["channel"], []).append(f)
    for lines in out.values():
        lines.sort(key=lambda f: f["seq"])
    return out


def field_problems(frames):
    """M5 §8 step 3, "Fields": out_ns - capture_ns is positive for every line, capture_ns increases with seq
    within a channel, and a channel's decode_errors never decreases in the order its lines arrive."""
    problems = []
    bad = [f for f in frames if f["out_ns"] - f["capture_ns"] <= 0]
    if bad:
        problems.append(f"{len(bad)} lines with out_ns - capture_ns <= 0 (first: channel {bad[0]['channel']} "
                        f"seq {bad[0]['seq']})")
    for ch, lines in sorted(by_channel(frames).items()):
        for a, b in zip(lines, lines[1:]):
            if b["seq"] == a["seq"]:
                problems.append(f"channel {ch}: seq {a['seq']} twice")
                break
            if b["capture_ns"] <= a["capture_ns"]:
                problems.append(f"channel {ch}: capture_ns does not increase from seq {a['seq']} to {b['seq']}")
                break
        arrived = sorted(lines, key=lambda f: f["arrival"])
        for a, b in zip(arrived, arrived[1:]):
            if b["decode_errors"] < a["decode_errors"]:
                problems.append(f"channel {ch}: decode_errors goes down, {a['decode_errors']} to "
                                f"{b['decode_errors']} (seq {a['seq']} then {b['seq']} in arrival order)")
                break
    return problems


def first_complete(frames, channels=CHANNELS):
    """The index in `frames` (arrival order) of the line with which every channel 0..channels-1 has one, or
    None. The tool takes t_start when it has read that line (§3)."""
    seen = set()
    for i, f in enumerate(frames):
        if 0 <= f["channel"] < channels:
            seen.add(f["channel"])
        if len(seen) == channels:
            return i
    return None


def latencies_ns(frames, t_w, t_e):
    """§3, latency: out_ns - capture_ns of the lines of all channels with out_ns inside the window."""
    return [f["out_ns"] - f["capture_ns"] for f in frames if t_w <= f["out_ns"] < t_e]


def inference_rates(frames, t_w, t_e, channels=CHANNELS):
    """§3, inference rate: per channel, the lines with out_ns inside the window over the window's length."""
    n = [0] * channels
    for f in frames:
        if t_w <= f["out_ns"] < t_e and 0 <= f["channel"] < channels:
            n[f["channel"]] += 1
    return [c / ((t_e - t_w) / 1e9) for c in n]


def drop_counts(frames, t_w, t_e, channels=CHANNELS, every=EVERY):
    """§3, drop rate: [(missing, expected)] per channel. Of a channel's lines with capture_ns inside the
    window, the sampled frames expected are the multiples of `every` from the smallest to the largest seq;
    those without a line are missing."""
    out = []
    lines = by_channel(frames)
    for ch in range(channels):
        seqs = {f["seq"] for f in lines.get(ch, []) if t_w <= f["capture_ns"] < t_e}
        if not seqs:
            out.append((0, 0))
            continue
        lo, hi = min(seqs), max(seqs)
        expected = {s for s in range(lo - lo % every + (every if lo % every else 0), hi + 1, every)}
        out.append((len(expected - seqs), len(expected)))
    return out


def decoder_drops(frames, t_w, t_e, channels=CHANNELS, fps=FPS):
    """§3, decoder drops: per channel, over its lines with pts_us >= 0 in the order of seq, from the last
    one with out_ns before t_w to the last one with out_ns before t_e. A step with Δpts <= 0 counts 1; any
    other step max(0, round(Δpts / period) - Δseq). The difference of decode_errors between those two lines
    is added. A channel with no line of pts_us >= 0 in the window counts 1."""
    out = []
    lines = by_channel(frames)
    for ch in range(channels):
        mine = [f for f in lines.get(ch, []) if f["pts_us"] >= 0]
        if not any(t_w <= f["out_ns"] < t_e for f in mine):
            out.append(1)
            continue
        before_w = [i for i, f in enumerate(mine) if f["out_ns"] < t_w]
        before_e = [i for i, f in enumerate(mine) if f["out_ns"] < t_e]
        first, last = (before_w[-1] if before_w else 0), before_e[-1]
        n = 0
        for a, b in zip(mine[first:last], mine[first + 1:last + 1]):
            dpts, dseq = b["pts_us"] - a["pts_us"], b["seq"] - a["seq"]
            n += 1 if dpts <= 0 else max(0, round(dpts * fps / 1e6) - dseq)
        if last > first:
            n += max(0, mine[last]["decode_errors"] - mine[first]["decode_errors"])
        out.append(n)
    return out


def receive_drifts(frames, t_w, t_e, channels=CHANNELS, fps=FPS):
    """§3, receive drift: per channel, D = (30 × Δt0 - Δseq) / 30 seconds between its first and its last
    line in the window, in the order of seq. In the window: out_ns inside it, as for the latency and the
    rate. None for a channel with fewer than two such lines."""
    out = []
    lines = by_channel(frames)
    for ch in range(channels):
        mine = [f for f in lines.get(ch, []) if t_w <= f["out_ns"] < t_e]
        if len(mine) < 2:
            out.append(None)
            continue
        dt0 = (mine[-1]["capture_ns"] - mine[0]["capture_ns"]) / 1e9
        out.append((fps * dt0 - (mine[-1]["seq"] - mine[0]["seq"])) / fps)
    return out


def phase(t0_ns, period_ns):
    """The circular mean of t0_ns modulo period_ns, in ns, in [0, period_ns): tools/m1_phases.py's phase."""
    a = [2 * math.pi * (t % period_ns) / period_ns for t in t0_ns]
    mean = math.atan2(sum(map(math.sin, a)) / len(a), sum(map(math.cos, a)) / len(a))
    return (mean % (2 * math.pi)) / (2 * math.pi) * period_ns % period_ns


def phase_drift(t0_ns, period_ns):
    """The largest change of the phase from the first sixth of t0_ns, in time, to any later sixth, in ns:
    tools/m1_phases.py's drift, with numpy.array_split's sixths."""
    t = sorted(t0_ns)
    size, extra = divmod(len(t), 6)
    sixths, at = [], 0
    for i in range(6):
        n = size + (1 if i < extra else 0)
        if n:
            sixths.append(t[at:at + n])
        at += n
    first = phase(sixths[0], period_ns)
    d = [abs(phase(s, period_ns) - first) for s in sixths[1:]]
    return max((min(x, period_ns - x) for x in d), default=0.0)


def phase_spread(phases, period):
    """The shortest arc of a circle of length period that holds every phase: tools/m1_phases.py's spread."""
    p = sorted(phases)
    gaps = [b - a for a, b in zip(p, p[1:])] + [p[0] + period - p[-1]]
    return period - max(gaps)


def arrival_phases(frames, t_w, t_e, channels=CHANNELS):
    """§3, arrival phases, as M2 §3.1 has them from tools/m1_phases.py, with capture_ns as t0 and out_ns as
    t4: per channel, (phase, drift) in ms over its lines with out_ns inside the window, None without one;
    and the run's spread in ms, None unless every channel has a phase."""
    period = SLOT_MS * 1e6
    out = []
    lines = by_channel(frames)
    for ch in range(channels):
        t0 = [f["capture_ns"] for f in lines.get(ch, []) if t_w <= f["out_ns"] < t_e]
        out.append((phase(t0, period) / 1e6, phase_drift(t0, period) / 1e6) if t0 else None)
    spread = phase_spread([p[0] * 1e6 for p in out], period) / 1e6 if all(p is not None for p in out) else None
    return out, spread


# ---------------------------------------------------------------- /proc

def stat_fields(text):
    """(name, utime + stime in ticks, the CPU last run on) of a /proc/<pid>/stat or .../task/<tid>/stat
    line. The name may hold spaces and parentheses: the fields are counted from the last ')'."""
    end = text.rindex(")")
    rest = text[end + 1:].split()
    return text[text.index("(") + 1:end], int(rest[11]) + int(rest[12]), int(rest[36])


def cpu_list(text):
    """The set of CPUs of a kernel list such as "0-3,6"."""
    out = set()
    for part in text.strip().split(","):
        if part:
            lo, _, hi = part.partition("-")
            out.update(range(int(lo), int(hi or lo) + 1))
    return out


def affinity_problems(arm, rows):
    """§4 rule 5 at one moment. rows: [(tid, name, set of allowed CPUs)]. A thread of a P run must be allowed
    every CPU of 0 to 7; a thread of a P4 run no CPU outside 4 to 7."""
    if not rows:
        return ["no thread's affinity could be read"]
    if arm == "P":
        bad = [(t, n, c) for t, n, c in rows if not set(range(8)) <= c]
        what = "is not allowed every CPU of 0 to 7"
    else:
        bad = [(t, n, c) for t, n, c in rows if not c or not c <= set(range(4, 8))]
        what = "is allowed a CPU outside 4 to 7"
    return [f"{len(bad)} of {len(rows)} threads: e.g. {bad[0][1]} ({bad[0][0]}) {what}: "
            f"{','.join(map(str, sorted(bad[0][2])))}"] if bad else []


def a55_share(thread_rows, k_from, k_to):
    """§3, where the work ran: (ticks on cpu0-3, all ticks) over the samples k_from < k <= k_to. thread_rows:
    [(k, tid, ticks, cpu)], one per second and thread. Each thread's Δticks since its last sample goes to the
    CPU it was last on at the later one; a thread first seen at a sample counts from zero."""
    last, on_a55, total = {}, 0, 0
    for k, tid, ticks, cpu in sorted(thread_rows):
        d = ticks - last.get(tid, 0 if k > k_from else ticks)
        last[tid] = ticks
        if k_from < k <= k_to and d > 0:
            total += d
            if cpu in A55:
                on_a55 += d
    return on_a55, total


def thread_deltas(thread_rows, names, k_from, k_to):
    """[(name, tid, ticks over the window)] by descending ticks: each thread's utime + stime between the
    samples k_from and k_to; a thread that started inside counts from zero."""
    at = {}
    for k, tid, ticks, _ in thread_rows:
        if k in (k_from, k_to):
            at.setdefault(tid, {})[k] = ticks
    out = [(names.get(tid, "?"), tid, v[k_to] - v.get(k_from, 0)) for tid, v in at.items() if k_to in v]
    return sorted(out, key=lambda r: (-r[2], r[1]))


def interpolate(samples, t):
    """The value at t of [(time, value)] sorted by time, linear between the two around it; None outside."""
    for (t0, v0), (t1, v1) in zip(samples, samples[1:]):
        if t0 <= t <= t1:
            return v0 + (v1 - v0) * ((t - t0) / (t1 - t0)) if t1 > t0 else v0
    return None


# ---------------------------------------------------------------- the journal

def journal_entries(text):
    """[(realtime in µs, message)] of journalctl -o json output: the program's own time of writing where the
    journal has it, else the journal's time of receipt."""
    out = []
    for line in text.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        msg = e.get("MESSAGE")
        if isinstance(msg, list):  # journald gives bytes that are not UTF-8 as numbers
            msg = bytes(msg).decode("utf-8", "replace")
        if isinstance(msg, str):
            out.append((int(e.get("_SOURCE_REALTIME_TIMESTAMP") or e["__REALTIME_TIMESTAMP"]), msg))
    return out


def journal_events(entries):
    """[(realtime µs, channel, event name)] of the program's event lines in the journal."""
    out = []
    for t, msg in entries:
        m = re.match(r"rkzc-detect: channel (\d+): (\w+): ", msg)
        if m and (m.group(2) in SOURCE_EVENTS or m.group(2) == "frame_error"):
            out.append((t, int(m.group(1)), m.group(2)))
    return out


def stop_log(entries, channels=CHANNELS):
    """The program's counters at its stop: ({channel: {name: count}}, (clients, disconnected for not reading,
    refused as too many) or None). The program also logs them every stats_every_s seconds; only the lines
    after its last "stopping" (or "every source has ended") are its stop log."""
    counters, output = {}, None
    stops = [i for i, (_, msg) in enumerate(entries)
             if re.match(r"rkzc-detect: .*(, stopping$|every source has ended$)", msg)]
    for _, msg in entries[stops[-1] + 1:] if stops else []:
        m = re.match(r"rkzc-detect: channel (\d+): (?:not )?connected, decoded (\d+), inferred (\d+), dropped (\d+), "
                     r"errors (\d+), decode errors (\d+), reconnects (\d+)", msg)
        if m:
            counters[int(m.group(1))] = dict(zip(("decoded", "inferred", "dropped", "errors", "decode_errors",
                                                  "reconnects"), map(int, m.groups()[1:])))
        m = re.match(r"rkzc-detect: output: (\d+) clients, (\d+) disconnected for not reading, (\d+) refused", msg)
        if m:
            output = tuple(map(int, m.groups()))
    return {c: v for c, v in counters.items() if c < channels}, output


def journal_problems(entries, t_e_us, channels=CHANNELS):
    """(rule 2 reasons, rule 3 reasons) that §4 reads from the service's journal. Events count up to t_e; the
    stop log's two client counters count whenever they happened."""
    rule2, rule3 = [], []
    opened = {}
    for t, ch, name in journal_events(entries):
        if t > t_e_us:
            continue
        if name == "frame_error":
            rule2.append(f"frame_error on channel {ch}")
        elif name == "source_opened" and not opened.get(ch):
            opened[ch] = True
        else:
            rule3.append(f"{name} on channel {ch}" + (" again" if name == "source_opened" else ""))
    _, output = stop_log(entries, channels)
    if output is None:
        rule2.append("the service's journal has no stop log")
    elif output[1] or output[2]:
        rule2.append(f"the service's stop log counts {output[1]} clients disconnected for not reading and "
                     f"{output[2]} refused as too many")
    return rule2, rule3


# ---------------------------------------------------------------- a run's directory

def read_keyed_csv(path):
    """{key: value} of a key,value file."""
    with open(path, encoding="utf-8", newline="") as f:
        return {r["key"]: r["value"] for r in csv.DictReader(f)}


def write_keyed(path, header, rows):
    """A record: "# key: value" lines, then a key,value table (a value with a comma is quoted)."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("".join(f"# {k}: {v}\n" for k, v in header.items()))
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["key", "value"])
        w.writerows(rows)


def read_rows(path, delimiter=","):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter=delimiter))


def proc_stat_nonidle(text):
    """{cpu: non-idle ticks} of /proc/stat, as tools/stream_session.py and bench_main count them (§7.2)."""
    out = {}
    for line in text.splitlines():
        f = line.split()
        if f and f[0].startswith("cpu") and f[0] != "cpu":
            v = [int(x) for x in f[1:9]]
            out[int(f[0][3:])] = v[0] + v[1] + v[2] + v[5] + v[6] + v[7]
    return out


def schedstat_run_ns(text):
    """{cpu: run time in ns} of /proc/schedstat, as tools/stream_session.py and bench_main read it (M2 §3.2)."""
    out = {}
    for line in text.splitlines():
        f = line.split()
        if len(f) >= 8 and re.fullmatch(r"cpu\d+", f[0]):
            out[int(f[0][3:])] = int(f[7])
    return out


def cluster_rows(prefix, per_cpu):
    """Rows named like bench_main's summary: per CPU, per cluster and total, in seconds."""
    rows = [(f"{prefix}_cpu{c}_s", f"{v:.3f}") for c, v in sorted(per_cpu.items())]
    rows += [(f"{prefix}_cpu{name}_s", f"{sum(per_cpu.get(c, 0.0) for c in cpus):.3f}") for name, cpus in CLUSTERS]
    return rows + [(f"{prefix}_cpu_total_s", f"{sum(per_cpu.values()):.3f}")]


def evaluate(run_dir, arm, fixed, tick_hz=100, channels=CHANNELS):
    """(rows, reasons) of a service run's directory: §3's values as key,value rows, and why §4's rules 1, 2,
    3 and 5 make the run invalid, as far as these files show it. Rule 4 (the stream server's log) and what
    only the tool saw while it ran (a connection the service closed, a process that went away) are the
    session's to add. fixed: {clock domain: its fixed value} (rule 1)."""
    run_dir = Path(run_dir)
    marks = read_keyed_csv(run_dir / "marks.csv")
    reasons, rows = [], []
    need = ("t_svc_ns", "t_start_ns", "t_w_ns", "t_e_ns", "read_w_after_ns", "read_e_after_ns")
    if any(k not in marks for k in need):
        return rows, ["the tool did not complete the run: marks.csv lacks "
                      + ", ".join(k for k in need if k not in marks) + " (rule 2)"]
    t_svc, t_start, t_w, t_e = (int(marks[k]) for k in need[:4])
    window_s = (t_e - t_w) / 1e9
    warmup_k, end_k = int(marks["warmup_k"]), int(marks["end_k"])
    frames, _, problems = parse_lines((run_dir / "lines.jsonl").read_text(encoding="utf-8", errors="replace"))
    reasons += [f"lines.jsonl: {p} (rule 2: the tool cannot read every line)" for p in problems[:3]]

    # §3, process CPU: the two timed reads.
    for which, due in (("w", t_w), ("e", t_e)):
        before, after = int(marks[f"read_{which}_before_ns"]), int(marks[f"read_{which}_after_ns"])
        off = max(abs(before - due), abs(after - due))
        rows.append((f"read_{which}_late_ms", f"{(after - due) / 1e6:.3f}"))
        if off > LATE_READ_NS:
            reasons.append(f"the process CPU read at t_{which} was {off / 1e6:.1f} ms off (rule 2: more than 50 ms)")
    process_s = (int(marks["proc_ticks_e"]) - int(marks["proc_ticks_w"])) / tick_hz
    rows.insert(0, ("process_cpu_s", f"{process_s:.3f}"))
    rows.insert(0, ("window_s", f"{window_s:g}"))
    rows.append(("tool_cpu_s", f"{(int(marks['tool_ticks_e']) - int(marks['tool_ticks_w'])) / tick_hz:.3f}"))
    rows.append(("first_lines_s", f"{(t_start - t_svc) / 1e9:.3f}"))  # t_svc to a line of every channel
    if t_start - t_svc > FIRST_LINES_S * 1e9:
        reasons.append(f"some channel had no frame line by t_svc + {FIRST_LINES_S} s (rule 2)")

    # §3, where the work ran.
    trows = [(int(r["k"]), int(r["tid"]), int(r["ticks"]), int(r["cpu"])) for r in read_rows(run_dir / "thread_samples.csv")]
    names = {int(r["tid"]): r["name"] for r in read_rows(run_dir / "thread_samples.csv")}
    on_a55, total = a55_share(trows, warmup_k, end_k)
    rows.append(("thread_cpu_s", f"{total / tick_hz:.3f}"))  # the per-second samples' sum, next to process_cpu_s
    rows.append(("thread_cpu_a55_s", f"{on_a55 / tick_hz:.3f}"))
    rows.append(("a55_share", f"{on_a55 / total:.4f}" if total else "nan"))
    for i, (name, tid, ticks) in enumerate(thread_deltas(trows, names, warmup_k, end_k)[:12]):
        rows.append((f"thread_{i:02d}", f"{name} {tid} {ticks / tick_hz:.2f}"))

    # §3, system CPU, both measures, raw: the idle baselines are the session's (idle_baseline.csv).
    stat_w, stat_e = (proc_stat_nonidle((run_dir / f"proc_stat_{w}.txt").read_text()) for w in "we")
    rows += cluster_rows("system", {c: (stat_e[c] - stat_w[c]) / tick_hz for c in sorted(stat_e) if c in stat_w})
    sched = [run_dir / f"schedstat_{w}.txt" for w in "we"]
    if all(p.exists() for p in sched):
        sw, se = (schedstat_run_ns(p.read_text()) for p in sched)
        rows += cluster_rows("schedstat", {c: (se[c] - sw[c]) / 1e9 for c in sorted(se) if c in sw})

    # §3, the lines.
    rates = inference_rates(frames, t_w, t_e, channels)
    rows += [("inference_fps_min", f"{min(rates):.3f}"), ("inference_fps_max", f"{max(rates):.3f}"),
             ("inference_fps_below_4.75", " ".join(str(c) for c, r in enumerate(rates) if r < MIN_FPS) or "none")]
    drops = drop_counts(frames, t_w, t_e, channels)
    missing, expected = sum(m for m, _ in drops), sum(e for _, e in drops)
    rows += [("sampled_in_window", str(expected)), ("missing_in_window", str(missing)),
             ("drop_rate", f"{missing / expected:.5f}" if expected else "nan")]
    dd = decoder_drops(frames, t_w, t_e, channels)
    rows.append(("decoder_drops", str(sum(dd))))
    if sum(dd):
        reasons.append(f"{sum(dd)} decoder drops (rule 3): per channel {' '.join(map(str, dd))}")
    drifts = receive_drifts(frames, t_w, t_e, channels)
    known = [d for d in drifts if d is not None]
    rows.append(("drift_end_max_s", f"{max(known, key=abs):.4f}" if known else "nan"))
    lat = latencies_ns(frames, t_w, t_e)
    rows.append(("processed_in_window", str(len(lat))))
    phases, spread = arrival_phases(frames, t_w, t_e, channels)
    rows.append(("arrival_phase_ms", " ".join("nan" if p is None else f"{p[0]:.1f}" for p in phases)))
    rows.append(("arrival_phase_spread_ms", "nan" if spread is None else f"{spread:.1f}"))
    rows.append(("arrival_phase_drift_ms", f"{max(p[1] for p in phases if p is not None):.1f}"
                 if any(p is not None for p in phases) else "nan"))

    # §3, the one-second samples; rule 1 over those of the window, both ends included, which is how
    # bench_main takes its own (src/core/report.cpp).
    samples = [s for s in read_rows(run_dir / "samples.csv") if warmup_k <= int(s["k"]) <= end_k]
    for d in DOMAINS:
        values = [int(s[f"freq_{d}"]) for s in samples]  # -1: the tool could not read it
        rows += [(f"freq_min_{d}", str(min(values)) if values else "nan"),
                 (f"freq_max_{d}", str(max(values)) if values else "nan")]
        if not values or min(values) < fixed[d]:
            reasons.append(f"clock below fixed, or not read: {d} (rule 1)")
    temps = [int(v) for s in samples for k, v in s.items() if k.startswith("temp_mc_") and v not in ("", "-1")]
    rss = [int(s["rss_kb"]) for s in samples]
    rows += [("temp_max_mc", str(max(temps)) if temps else "nan"),
             ("rss_window_start_kb", str(rss[0]) if rss else "nan"), ("rss_window_end_kb", str(rss[-1]) if rss else "nan"),
             ("rss_window_max_kb", str(max(rss)) if rss else "nan")]

    # §4 rules 2 and 3, from the journal; the events count up to t_e.
    entries = journal_entries((run_dir / "journal.jsonl").read_text(encoding="utf-8", errors="replace"))
    rule2, rule3 = journal_problems(entries, int(marks["realtime_e_ns"]) // 1000, channels)
    reasons += [f"{r} (rule 2)" for r in rule2] + [f"{r} (rule 3)" for r in rule3]
    counters, output = stop_log(entries, channels)
    for name in ("decoded", "inferred", "dropped", "errors", "decode_errors", "reconnects"):
        rows.append((f"service_{name}", str(sum(c[name] for c in counters.values())) if len(counters) == channels
                     else "nan"))
    rows.append(("service_output", " ".join(map(str, output)) if output else "nan"))

    # §4 rule 5.
    for which in "we":
        aff = [(int(r["tid"]), r["name"], cpu_list(r["cpus"])) for r in read_rows(run_dir / f"affinity_{which}.tsv", "\t")]
        allowed = sorted({",".join(map(str, sorted(c))) for _, _, c in aff})
        rows.append((f"affinity_{which}", f"{len(aff)} threads: " + " | ".join(allowed)))
        reasons += [f"at t_{which}: {p} (rule 5)" for p in affinity_problems(arm, aff)]
    return rows, reasons


def channel_rows(run_dir, channels=CHANNELS):
    """Per channel, for channels.csv: its lines in the window, rate, drops, drift, phase, and the service's
    own counters at its stop."""
    run_dir = Path(run_dir)
    marks = read_keyed_csv(run_dir / "marks.csv")
    t_w, t_e = int(marks["t_w_ns"]), int(marks["t_e_ns"])
    frames, _, _ = parse_lines((run_dir / "lines.jsonl").read_text(encoding="utf-8", errors="replace"))
    rates, drops = inference_rates(frames, t_w, t_e, channels), drop_counts(frames, t_w, t_e, channels)
    dd, drifts = decoder_drops(frames, t_w, t_e, channels), receive_drifts(frames, t_w, t_e, channels)
    phases, _ = arrival_phases(frames, t_w, t_e, channels)
    counters, _ = stop_log(journal_entries((run_dir / "journal.jsonl").read_text(encoding="utf-8", errors="replace")),
                           channels)
    out = []
    for ch in range(channels):
        c = counters.get(ch, {})
        out.append({"channel": ch, "inference_fps": f"{rates[ch]:.3f}", "sampled": drops[ch][1],
                    "missing": drops[ch][0], "decoder_drops": dd[ch],
                    "drift_end_s": "nan" if drifts[ch] is None else f"{drifts[ch]:.4f}",
                    "arrival_phase_ms": "nan" if phases[ch] is None else f"{phases[ch][0]:.1f}",
                    "arrival_phase_drift_ms": "nan" if phases[ch] is None else f"{phases[ch][1]:.1f}",
                    "service_decoded": c.get("decoded", ""), "service_inferred": c.get("inferred", ""),
                    "service_dropped": c.get("dropped", ""), "service_errors": c.get("errors", ""),
                    "service_decode_errors": c.get("decode_errors", ""), "service_reconnects": c.get("reconnects", "")})
    return out
