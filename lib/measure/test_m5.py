#!/usr/bin/env python3
"""Tests for M5's tool: lib/measure/m5_metrics.py, and the pure parts of m5_session.py, m5_order.py,
m5_summary.py and m5_detections.py. They need no board; m5_summary's need numpy.

Usage: python3 lib/measure/test_m5.py
"""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import m5_metrics as mm  # noqa: E402
import m5_order  # noqa: E402
import m5_session as m5  # noqa: E402

try:
    import m5_summary
except ImportError:  # no numpy
    m5_summary = None

S = 10**9
PERIOD = S // 30


def frame(ch, seq, t0, latency_ms=30, pts=None, errors=0):
    return {"channel": ch, "seq": seq, "pts_us": seq * 1000000 // 30 if pts is None else pts, "time_ms": 1,
            "capture_ns": t0, "out_ns": t0 + latency_ms * 10**6, "decode_errors": errors, "width": 1920,
            "height": 1080, "detections": []}


def stream(ch, t_first, seconds, skip=(), **kw):
    """A channel's frame lines: 1 in 6 of a 30 fps stream from t_first, without the seqs in skip."""
    return [frame(ch, seq, t_first + seq * PERIOD, **kw) for seq in range(0, seconds * 30, 6) if seq not in skip]


def arrive(frames):
    """The frames in the order of out_ns, with their arrival index, as parse_lines gives them."""
    frames = sorted(frames, key=lambda f: f["out_ns"])
    for i, f in enumerate(frames):
        f["arrival"] = i
    return frames


def text_of(frames):
    return "".join(json.dumps({k: v for k, v in f.items() if k != "arrival"}, separators=(",", ":")) + "\n"
                   for f in frames)


class Lines(unittest.TestCase):
    def test_parse_drops_a_cut_line_and_reports_bad_ones(self):
        good = text_of([frame(0, 0, 100)])
        frames, events, problems = mm.parse_lines(good + '{"event":"source_lost","channel":1}\n' + 'x\n'
                                                  + '{"channel":0,"seq":6}\n' + '{"channel":0,"se')
        self.assertEqual((len(frames), len(events)), (1, 1))
        self.assertEqual(len(problems), 2)
        self.assertIn("line 3 is not JSON", problems[0])
        self.assertIn("line 4", problems[1])
        self.assertEqual(mm.parse_lines("")[0], [])

    def test_first_complete(self):
        frames = arrive([frame(c, 0, 100 + c) for c in (0, 1, 1, 0, 2)])
        self.assertEqual(mm.first_complete(frames, 3), 4)
        self.assertIsNone(mm.first_complete(frames, 4))

    def test_field_problems(self):
        ok = arrive(stream(0, S, 2) + stream(1, S + 5, 2))
        self.assertEqual(mm.field_problems(ok), [])
        bad = arrive(stream(0, S, 1))
        bad[2]["out_ns"] = bad[2]["capture_ns"]
        self.assertIn("out_ns - capture_ns <= 0", mm.field_problems(bad)[0])
        back = arrive(stream(0, S, 1))
        back[3]["capture_ns"] = back[2]["capture_ns"] - 1
        self.assertTrue(any("capture_ns does not increase" in p for p in mm.field_problems(back)))
        down = arrive(stream(0, S, 1, errors=2))
        down[4]["decode_errors"] = 1
        self.assertTrue(any("decode_errors goes down" in p for p in mm.field_problems(down)))
        # Out of order in arrival but increasing with seq is no problem.
        swapped = arrive(stream(0, S, 1))
        swapped[1]["arrival"], swapped[2]["arrival"] = 2, 1
        self.assertEqual(mm.field_problems(swapped), [])

    def test_latency_and_rate_take_out_ns_in_the_window(self):
        frames = arrive(stream(0, 0, 10) + stream(1, 0, 10, latency_ms=50))
        t_w, t_e = 2 * S, 6 * S
        lat = mm.latencies_ns(frames, t_w, t_e)
        self.assertEqual(sorted(set(lat)), [30 * 10**6, 50 * 10**6])
        self.assertEqual(len(lat), 40)
        self.assertEqual(mm.inference_rates(frames, t_w, t_e, 2), [5.0, 5.0])
        # A line captured inside but out after t_e is not in the window's count.
        late = arrive([frame(0, 0, t_e - 1, latency_ms=1)])
        self.assertEqual(mm.latencies_ns(late, t_w, t_e), [])

    def test_drop_counts(self):
        frames = arrive(stream(0, 0, 10, skip={60, 66}) + stream(1, 0, 10))
        self.assertEqual(mm.drop_counts(frames, 0, 10 * S, 3), [(2, 50), (0, 50), (0, 0)])
        # Missing at the window's edge is outside the span of the lines seen, so it is not counted.
        edge = arrive(stream(0, 0, 10, skip={0, 294}))
        self.assertEqual(mm.drop_counts(edge, 0, 10 * S, 1), [(0, 48)])

    def test_decoder_drops(self):
        t_w, t_e = 2 * S, 8 * S
        self.assertEqual(mm.decoder_drops(arrive(stream(0, 0, 10)), t_w, t_e, 1), [0])
        # A frame the decoder never returned: pts steps by 7 frames while seq steps by 6.
        jump = stream(0, 0, 10)
        for f in jump:
            if f["seq"] >= 120:
                f["pts_us"] += 1000000 // 30
        self.assertEqual(mm.decoder_drops(arrive(jump), t_w, t_e, 1), [1])
        # Before the window's span it does not count.
        early = stream(0, 0, 10)
        for f in early:
            if f["seq"] >= 12:
                f["pts_us"] += 3 * 1000000 // 30
        self.assertEqual(mm.decoder_drops(arrive(early), t_w, t_e, 1), [0])
        # A step back in pts (a loop point that restarts the timestamps) counts 1.
        back = stream(0, 0, 10)
        for f in back:
            if f["seq"] >= 150:
                f["pts_us"] -= 5000000
        self.assertEqual(mm.decoder_drops(arrive(back), t_w, t_e, 1), [1])
        # The decoder's error returns over the span are added.
        errors = stream(0, 0, 10)
        for f in errors:
            f["decode_errors"] = 1 if f["seq"] < 90 else 3
        self.assertEqual(mm.decoder_drops(arrive(errors), t_w, t_e, 1), [2])
        # No line with a pts in the window counts 1, and so does a channel without lines.
        nopts = arrive(stream(0, 0, 10, pts=-1))
        self.assertEqual(mm.decoder_drops(nopts, t_w, t_e, 2), [1, 1])
        # Lines without pts are left out of the steps, not counted as drops.
        some = stream(0, 0, 10)
        some[20]["pts_us"] = -1
        self.assertEqual(mm.decoder_drops(arrive(some), t_w, t_e, 1), [0])

    def test_drift_and_phases(self):
        frames = arrive(stream(0, 7 * 10**6, 10))
        d = mm.receive_drifts(frames, 0, 10 * S, 2)
        self.assertAlmostEqual(d[0], 0.0, places=6)
        self.assertIsNone(d[1])
        slow = arrive([frame(0, seq, seq * PERIOD * 101 // 100) for seq in range(0, 300, 6)])
        self.assertAlmostEqual(mm.receive_drifts(slow, 0, 20 * S, 1)[0], 294 / 30 * 0.01, places=4)
        ph, spread = mm.arrival_phases(frames, 0, 10 * S, 2)
        self.assertAlmostEqual(ph[0][0], 7.0, places=3)
        self.assertLess(ph[0][1], 0.001)
        self.assertIsNone(ph[1])
        self.assertIsNone(spread)  # a channel without a phase
        # The mean is circular: 199 ms and 1 ms average to 0, not to 100.
        wrap = arrive([frame(0, 0, 199 * 10**6), frame(0, 6, 401 * 10**6)])
        m = mm.arrival_phases(wrap, 0, S, 1)[0][0][0]
        self.assertTrue(m < 0.5 or m > 199.5)
        # The run's spread: the shortest arc that holds every channel's phase, across the circle's end too.
        three = arrive(stream(0, 190 * 10**6, 10) + stream(1, 5 * 10**6, 10) + stream(2, 20 * 10**6, 10))
        self.assertAlmostEqual(mm.arrival_phases(three, 0, 10 * S, 3)[1], 30.0, places=3)
        # A line belongs to the window by its out_ns, as its latency does.
        late = arrive([frame(0, 0, S - 1, latency_ms=5), frame(0, 6, S + 200 * 10**6, latency_ms=5)])
        self.assertEqual(len(mm.latencies_ns(late, S, 2 * S)), 2)
        self.assertIsNotNone(mm.receive_drifts(late, S, 2 * S, 1)[0])

    def test_phases_are_m1_phases(self):
        try:
            sys.path.insert(0, str(HERE.parents[1] / "tools"))
            import m1_phases
        except ImportError:
            self.skipTest("needs numpy")
        import random
        rng = random.Random(5)
        period = 200e6
        for n in (1, 5, 6, 7, 600):
            t0 = [int(k * period + rng.gauss(150e6, 30e6)) + (k * 40000 if n > 6 else 0) for k in range(n)]
            self.assertAlmostEqual(mm.phase(t0, period), m1_phases.phase(t0, period), delta=1e-3)
            self.assertAlmostEqual(mm.phase_drift(t0, period), m1_phases.drift(t0, period), delta=1e-3)
        ph = [rng.uniform(0, period) for _ in range(8)]
        self.assertAlmostEqual(mm.phase_spread(ph, period), m1_phases.spread(ph, period))


class Proc(unittest.TestCase):
    def test_stat_fields(self):
        rest = "S 1 2 3 4 5 6 7 8 9 10 111 222 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 5 37"
        self.assertEqual(mm.stat_fields(f"123 (rkzc-detect) {rest}"), ("rkzc-detect", 333, 5))
        self.assertEqual(mm.stat_fields(f"123 (a (b) c) {rest}"), ("a (b) c", 333, 5))

    def test_affinity(self):
        self.assertEqual(mm.cpu_list("0-3,6\n"), {0, 1, 2, 3, 6})
        every, a76 = set(range(8)), {4, 5, 6, 7}
        self.assertEqual(mm.affinity_problems("P", [(1, "a", every)]), [])
        self.assertEqual(mm.affinity_problems("P4", [(1, "a", a76), (2, "b", {4})]), [])
        self.assertIn("not allowed every CPU", mm.affinity_problems("P", [(1, "a", every), (2, "b", a76)])[0])
        self.assertIn("outside 4 to 7", mm.affinity_problems("P4", [(1, "a", every)])[0])
        self.assertTrue(mm.affinity_problems("P", []))

    def test_a55_share_and_thread_deltas(self):
        rows = [(0, 1, 100, 4), (0, 2, 50, 0),
                (1, 1, 110, 4), (1, 2, 55, 1),
                (2, 1, 130, 2), (2, 2, 55, 1), (2, 3, 7, 5)]  # thread 3 starts inside
        self.assertEqual(mm.a55_share(rows, 0, 2), (5 + 20, 10 + 5 + 20 + 7))
        self.assertEqual(mm.a55_share(rows, 1, 2), (20, 27))
        self.assertEqual(mm.thread_deltas(rows, {1: "a", 2: "b", 3: "c"}, 0, 2), [("a", 1, 30), ("c", 3, 7), ("b", 2, 5)])

    def test_interpolate(self):
        self.assertEqual(mm.interpolate([(0, 0), (10, 100), (20, 100)], 5), 50)
        self.assertEqual(mm.interpolate([(0, 0), (10, 100)], 10), 100)
        self.assertIsNone(mm.interpolate([(0, 0), (10, 100)], 11))


def journal(entries):
    return "".join(json.dumps({"__REALTIME_TIMESTAMP": str(t), "MESSAGE": m}) + "\n" for t, m in entries)


def stats_lines(t, channels=8, disconnected=0, refused=0, inferred=700):
    return [(t, f"rkzc-detect: channel {c}: connected, decoded 4200, inferred {inferred}, dropped 0, errors 0, "
                f"decode errors 0, reconnects 0") for c in range(channels)] + \
           [(t, f"rkzc-detect: output: 1 clients, {disconnected} disconnected for not reading, {refused} refused as too many")]


def stop_lines(t, channels=8, disconnected=0, refused=0):
    return [(t, "rkzc-detect: SIGTERM, stopping")] + \
           [(t, f"rkzc-detect: channel {c}: connected, decoded 4200, inferred 700, dropped 0, errors 0, "
                f"decode errors 0, reconnects 0") for c in range(channels)] + \
           [(t, f"rkzc-detect: output: 0 clients, {disconnected} disconnected for not reading, {refused} refused as too many")]


class Journal(unittest.TestCase):
    def test_events_and_stop_log(self):
        opened = [(100 + c, f"rkzc-detect: channel {c}: source_opened: rtsp://x/ch{c + 1}") for c in range(8)]
        e = mm.journal_entries(journal(opened + stop_lines(900)))
        self.assertEqual(mm.journal_problems(e, 500), ([], []))
        counters, output = mm.stop_log(e)
        self.assertEqual((counters[7]["inferred"], output), (700, (0, 0, 0)))
        # A source lost and opened again before t_e: rule 3, twice. After t_e: not counted.
        again = opened + [(300, "rkzc-detect: channel 2: source_lost: no frame"),
                          (310, "rkzc-detect: channel 2: source_opened: rtsp://x/ch3"),
                          (600, "rkzc-detect: channel 5: source_lost: stopping")] + stop_lines(900)
        r2, r3 = mm.journal_problems(mm.journal_entries(journal(again)), 500)
        self.assertEqual((r2, r3), ([], ["source_lost on channel 2", "source_opened on channel 2 again"]))
        # A frame that failed: rule 2. So are the two client counters, whenever they happened.
        err = opened + [(200, "rkzc-detect: channel 1: frame_error: rknn_run failed")] + stop_lines(900, disconnected=1)
        r2, r3 = mm.journal_problems(mm.journal_entries(journal(err)), 500)
        self.assertEqual(r3, [])
        self.assertEqual(len(r2), 2)
        self.assertIn("frame_error on channel 1", r2[0])
        # No stop log at all (the program was killed): rule 2. The counters it logs every minute are not one.
        self.assertIn("no stop log", mm.journal_problems(mm.journal_entries(journal(opened)), 500)[0][0])
        periodic = mm.journal_entries(journal(opened + stats_lines(400, disconnected=1)))
        self.assertEqual(mm.journal_problems(periodic, 500)[0], ["the service's journal has no stop log"])
        self.assertEqual(mm.stop_log(periodic), ({}, None))
        both = mm.journal_entries(journal(opened + stats_lines(400, inferred=300) + stop_lines(900)))
        self.assertEqual(mm.stop_log(both)[0][0]["inferred"], 700)
        ended = mm.journal_entries(journal(opened + [(800, "rkzc-detect: every source has ended")] + stop_lines(900)[1:]))
        self.assertEqual(mm.stop_log(ended)[1], (0, 0, 0))
        # journald gives a message that is not UTF-8 as a list of bytes.
        raw = json.dumps({"__REALTIME_TIMESTAMP": "5", "MESSAGE": list(b"rkzc-detect: channel 0: source_lost: \xff")})
        self.assertEqual(mm.journal_events(mm.journal_entries(raw)), [(5, 0, "source_lost")])


FIXED = {"policy0_khz": 1800000, "policy4_khz": 2208000, "policy6_khz": 2208000, "dmc_hz": 2112000000,
         "npu_hz": 1000000000}


def make_run(root, arm="P", warmup=2, duration=5, **change):
    """A service run's directory as m5_session.py writes it, for a run with nothing wrong."""
    d = Path(root) / f"run_{arm}_{len(list(Path(root).iterdir()))}"
    d.mkdir()
    t_svc, t_start = 1000 * S, 1003 * S
    t_w, t_e = t_start + warmup * S, t_start + (warmup + duration) * S
    frames = []
    for c in range(8):
        frames += stream(c, t_start - S + c * 10**6, warmup + duration + 3, **change.get("stream", {}))
    for f in frames:
        f["time_ms"] = f["out_ns"] // 10**6
    frames = change.get("frames", lambda x: x)(arrive(frames))
    (d / "lines.jsonl").write_text(text_of(frames) + '{"channel":0,"se')
    cpus = "0,1,2,3,4,5,6,7" if arm == "P" else "4,5,6,7"
    marks = {"t_svc_ns": t_svc, "main_pid": 77, "t_start_ns": t_start, "t_w_ns": t_w, "t_e_ns": t_e,
             "warmup_k": warmup, "end_k": warmup + duration,
             "read_w_due_ns": t_w, "read_w_before_ns": t_w + 10**5, "read_w_after_ns": t_w + 2 * 10**5,
             "read_e_due_ns": t_e, "read_e_before_ns": t_e + 10**5, "read_e_after_ns": t_e + 2 * 10**5,
             "realtime_w_ns": 5 * S, "realtime_e_ns": (5 + duration) * S, "proc_ticks_w": 1000,
             "proc_ticks_e": 1000 + 16 * duration, "tool_ticks_w": 10, "tool_ticks_e": 12}
    marks.update(change.get("marks", {}))
    for k in change.get("drop_marks", ()):
        del marks[k]
    with open(d / "marks.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["key", "value"])
        w.writerows(marks.items())
    stat = "cpu  1 1 1 1 1 1 1 1 0 0\n" + "".join(f"cpu{c} {{u}} 0 {{s}} 900 0 0 0 0 0 0\n" for c in range(8))
    (d / "proc_stat_w.txt").write_text(stat.format(u=100, s=50))
    (d / "proc_stat_e.txt").write_text(stat.format(u=100 + 30 * duration, s=50))
    sched = "version 15\n" + "".join(f"cpu{c} 0 0 0 0 0 0 {{t}} 0 0\ndomain0 ff 0\n" for c in range(8))
    (d / "schedstat_w.txt").write_text(sched.format(t=10 * S))
    (d / "schedstat_e.txt").write_text(sched.format(t=10 * S + duration * S // 4))
    for which in "we":
        (d / f"affinity_{which}.tsv").write_text("tid\tname\tcpus\n" + "".join(
            f"{t}\t{n}\t{change.get('cpus', cpus)}\n" for t, n in ((77, "rkzc-detect"), (78, "npu-0"))))
    with open(d / "samples.csv", "w", newline="") as f:
        keys = ["k", "mono_ns"] + [f"freq_{x}" for x in FIXED] + ["temp_mc_soc-thermal", "npu_core0_pct", "rss_kb"]
        w = csv.DictWriter(f, keys)
        w.writeheader()
        for k in range(warmup + duration + 1):
            row = {"k": k, "mono_ns": t_start + k * S, **{f"freq_{x}": v for x, v in FIXED.items()},
                   "temp_mc_soc-thermal": 40000 + 100 * k, "npu_core0_pct": 20, "rss_kb": 200000 + k}
            row.update(change.get("sample", {}).get(k, {}))
            w.writerow(row)
    with open(d / "thread_samples.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["k", "tid", "name", "ticks", "cpu"])
        for k in range(warmup + duration + 1):
            w.writerow([k, 77, "rkzc-detect", 100 + k, 0])
            w.writerow([k, 78, "npu-0", 900 + 15 * k, 4 + k % 2])
    opened = [(4 * 10**6 + c, f"rkzc-detect: channel {c}: source_opened: rtsp://10.88.0.1:8554/ch{c + 1}")
              for c in range(8)]
    (d / "journal.jsonl").write_text(journal(opened + change.get("journal", []) + stop_lines(20 * 10**6)))
    return d


class Evaluate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_of(self, arm="P", **change):
        d = make_run(self.tmp.name, arm, **change)
        rows, reasons = mm.evaluate(d, arm, FIXED)
        return dict(rows), reasons, d

    def test_a_good_run(self):
        v, reasons, d = self.run_of()
        self.assertEqual(reasons, [])
        self.assertEqual(v["process_cpu_s"], "0.800")
        self.assertEqual(v["window_s"], "5")
        self.assertEqual(v["tool_cpu_s"], "0.020")
        self.assertEqual(v["first_lines_s"], "3.000")
        self.assertEqual((v["inference_fps_min"], v["inference_fps_max"]), ("5.000", "5.000"))
        self.assertEqual((v["sampled_in_window"], v["missing_in_window"], v["drop_rate"]), ("200", "0", "0.00000"))
        self.assertEqual(v["decoder_drops"], "0")
        self.assertEqual(v["processed_in_window"], "200")
        self.assertEqual(v["system_cpu_total_s"], "12.000")  # 8 CPUs × 30 ticks × 5 s
        self.assertEqual(v["schedstat_cpu4-5_s"], "2.500")
        self.assertEqual((v["thread_cpu_s"], v["thread_cpu_a55_s"], v["a55_share"]), ("0.800", "0.050", "0.0625"))
        self.assertEqual(v["thread_00"], "npu-0 78 0.75")
        self.assertEqual(v["freq_min_npu_hz"], "1000000000")
        self.assertEqual((v["temp_max_mc"], v["rss_window_max_kb"]), ("40700", "200007"))  # k = 2..7
        self.assertEqual((v["arrival_phase_spread_ms"], v["arrival_phase_drift_ms"]), ("7.0", "0.0"))
        self.assertEqual((v["service_inferred"], v["service_output"]), ("5600", "0 0 0"))
        self.assertIn("2 threads: 0,1,2,3,4,5,6,7", v["affinity_w"])
        ch = mm.channel_rows(d)
        self.assertEqual((len(ch), ch[3]["inference_fps"], ch[3]["arrival_phase_ms"]), (8, "5.000", "3.0"))

    def test_p4(self):
        self.assertEqual(self.run_of("P4")[1], [])
        _, reasons, _ = self.run_of("P4", cpus="0,1,2,3,4,5,6,7")
        self.assertEqual(len(reasons), 2)  # at t_w and at t_e
        self.assertIn("rule 5", reasons[0])

    def test_p_with_a_restricted_thread(self):
        _, reasons, _ = self.run_of("P", cpus="4,5,6,7")
        self.assertTrue(all("rule 5" in r for r in reasons) and len(reasons) == 2)

    def test_a_late_read(self):
        w = 1003 * S + 2 * S
        _, reasons, _ = self.run_of(marks={"read_w_after_ns": w + 51 * 10**6})
        self.assertEqual(len(reasons), 1)
        self.assertIn("51.0 ms off", reasons[0])
        self.assertEqual(self.run_of(marks={"read_w_after_ns": w + 50 * 10**6})[1], [])

    def test_a_clock_sample_below_fixed(self):
        _, reasons, _ = self.run_of(sample={4: {"freq_policy4_khz": 1800000}})
        self.assertEqual(reasons, ["clock below fixed, or not read: policy4_khz (rule 1)"])
        # The samples at t_w and at t_e are the window's, as bench_main's are; the one before is not.
        self.assertEqual(len(self.run_of(sample={2: {"freq_dmc_hz": 1}})[1]), 1)
        self.assertEqual(len(self.run_of(sample={7: {"freq_dmc_hz": -1}})[1]), 1)
        self.assertEqual(self.run_of(sample={1: {"freq_policy4_khz": 1}})[1], [])

    def test_decoder_drops_and_events(self):
        def with_gap(frames):
            for f in frames:
                if f["channel"] == 3 and f["seq"] >= 120:
                    f["pts_us"] += 1000000 // 30
            return frames
        v, reasons, _ = self.run_of(frames=with_gap)
        self.assertEqual(v["decoder_drops"], "1")
        self.assertEqual(reasons, ["1 decoder drops (rule 3): per channel 0 0 0 1 0 0 0 0"])
        _, reasons, _ = self.run_of(journal=[(7 * 10**6, "rkzc-detect: channel 0: source_lost: timeout")])
        self.assertEqual(reasons, ["source_lost on channel 0 (rule 3)"])
        # After t_e (realtime_e_ns is 10 s): not this run's window.
        self.assertEqual(self.run_of(journal=[(11 * 10**6, "rkzc-detect: channel 0: source_lost: x")])[1], [])

    def test_slow_start_and_incomplete(self):
        _, reasons, _ = self.run_of(marks={"t_svc_ns": 1003 * S - 61 * S})
        self.assertIn("t_svc + 60 s", reasons[0])
        rows, reasons = mm.evaluate(make_run(self.tmp.name, "P4", drop_marks=("read_e_after_ns",)), "P4", FIXED)
        self.assertEqual(rows, [])
        self.assertIn("did not complete", reasons[0])

    def test_missing_frames_are_drops_not_invalid(self):
        v, reasons, _ = self.run_of(stream={"skip": {120, 126, 132}})
        self.assertEqual(reasons, [])
        self.assertEqual((v["missing_in_window"], v["drop_rate"]), ("24", "0.12000"))
        self.assertEqual(v["inference_fps_min"], "4.400")


class Session(unittest.TestCase):
    def test_order_and_plan(self):
        self.assertEqual(m5.plan(), [(8, 1, ("B", "C", "P", "P4")), (8, 2, ("P4", "P", "C", "B")),
                                     (8, 3, ("C", "B", "P4", "P"))])
        self.assertEqual(m5.ss.SWEEPS["m5"][2], m5.ARMS)
        self.assertTrue(m5.ss.is_m3("m5"))  # M3's inventory rule, validity and freeze-m3 commit
        self.assertTrue(m5.ss.rule3_applies("m5", "B", 8, "main", m5.ss.LIMIT, {}))
        self.assertEqual(m5.ss.bench_args("C", "m5"), ["--variant", "C"])

    def test_config_changes_only_sources(self):
        shipped = (HERE.parent / "dist" / "detect.toml").read_text(encoding="utf-8")
        text = m5.config_for(shipped, m5.SOURCES)
        a, b = shipped.splitlines(), text.splitlines()
        self.assertEqual(len(b) - len(a), 7)
        i = a.index("sources = [")
        self.assertEqual(a[:i + 1], b[:i + 1])
        self.assertEqual(b[i + 1:i + 9], [f'    "rtsp://10.88.0.1:8554/ch{n}",' for n in range(1, 9)])
        self.assertEqual(a[i + 2:], b[i + 9:])
        with self.assertRaises(m5.ss.Stop):
            m5.config_for("model = 1\n", m5.SOURCES)

    def test_package_record(self):
        self.assertEqual(m5.package_record("version: 0.1.1-1\nsha256: " + "ab" * 32 + "\n"), ("0.1.1-1", "ab" * 32))
        with self.assertRaises(m5.ss.Stop):
            m5.package_record("version: 0.1.1-1\nsha256: xyz\n")

    def test_cpu_check(self):
        t0 = 1795000000 * S
        rows = [(t0 + k * S, 1000 + 16 * k) for k in range(40)]
        summary = {"window_start_utc": m5.stamp(m5.utc(t0 + 5 * S + S // 2), True),
                   "window_end_utc": m5.stamp(m5.utc(t0 + 25 * S + S // 2), True), "process_cpu_s": "3.250"}
        v = dict(m5.cpu_check_rows(rows, summary))
        self.assertEqual((v["cpu_check_tool_s"], v["cpu_check"]), ("3.200", "passed"))
        summary["process_cpu_s"] = "3.050"
        self.assertIn("failed", dict(m5.cpu_check_rows(rows, summary))["cpu_check"])
        self.assertIn("do not cover", m5.cpu_check_rows(rows[:10], summary)[0][1])


ENV_MD = """# Environment
## Registration
| Stage | Tag commit | Release `created_at` |
|---|---|---|
| [`prereg-v1`](x) | `{v1}` | 2026-10-30T02:01:36Z |
| [`prereg-m5`](x) | `{p}` | 2026-11-20T04:09:11Z |
| [`amend-A16`](x) | `{f}` | 2026-11-21T00:00:00Z |
| [`freeze-m5`](x) | `{f}` | 2026-11-21T00:00:00Z |
## Board
"""
AMENDMENTS = "## A15 — x\n- Release: `amend-A15`\n## A16 — x\n- Release: `amend-A16`\n## A17 — x\n- Release: none yet\n"


class Order(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.p, self.f = m5.PREREG_M5_COMMIT, "f" * 40
        (self.root / "env.md").write_text(ENV_MD.format(v1="1" * 40, p=self.p, f=self.f))
        (self.root / "AMENDMENTS.md").write_text(AMENDMENTS, encoding="utf-8")
        self.results = self.root / "results"

    def record(self, session, run, start, kind, amendment="A16", prereg=None, freeze=None, name="m5_run.csv"):
        d = self.results / session / run if run else self.results / session
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(f"# start_time_utc: {start}\n# run_kind: {kind}\n# amendment: {amendment}\n"
                              f"# prereg_m5_commit: {prereg or self.p}\n# freeze_m5_commit: {freeze or self.f}\n"
                              "key,value\nvalid,1\n")

    def problems(self):
        refs, commits = m5_order.cpo.reference_times(self.root / "env.md")
        # amend-A15 has no row in this table; the check reports that, and the tests below look past it.
        names, released = m5_order.cpo.amendments(self.root / "AMENDMENTS.md")
        return [p for p in m5_order.check(self.results, refs, commits, names, released)[0] if "amend-A15" not in p]

    def test_in_order(self):
        self.record("20261121T010000Z_m5", "01_m5_B_8ch_r1", "2026-11-21T01:10:00Z", "m5")
        self.record("20261121T010000Z_m5", None, "2026-11-21T01:00:00Z", "m5", name="m5_session.csv")
        self.record("20261120T050000Z_m5_check", "01_m5_P_8ch_r1", "2026-11-20T05:00:00Z", "check", "A15", freeze="none")
        self.assertEqual(self.problems(), [])

    def test_out_of_order(self):
        self.record("20261120T230000Z_m5", "01_m5_B_8ch_r1", "2026-11-20T23:10:00Z", "m5")
        p = self.problems()
        self.assertEqual(len(p), 2)  # before freeze-m5, and before the Release of the amendment it names
        self.assertTrue(any("not after freeze-m5" in x for x in p) and any("not after amend-A16" in x for x in p))

    def test_check_before_prereg_and_wrong_commit(self):
        self.record("20261120T030000Z_m5_check", "01_m5_C_8ch_r1", "2026-11-20T03:00:00Z", "check", "A15", freeze="none")
        self.assertIn("not after prereg-m5", self.problems()[0])
        self.record("20261122T030000Z_m5_check", "01_m5_C_8ch_r1", "2026-11-22T03:00:00Z", "check", freeze="e" * 40)
        self.assertTrue(any("freeze-m5's tag commit" in x for x in self.problems()))

    def test_kind_and_place(self):
        self.record("20261122T010000Z_m5", "01_m5_B_8ch_r1", "2026-11-22T01:10:00Z", "check")
        self.record("20261122T020000Z_m5_check", "01_m5_B_8ch_r1", "2026-11-22T02:10:00Z", "m5")
        self.record("20261122T030000Z_m5", "01_m5_B_8ch_r1", "2026-11-22T03:10:00Z", "m5", freeze="none")
        self.record("20261122T040000Z_m5", "01_m5_B_8ch_r1", "2026-11-22T04:10:00Z", "m5", amendment="A99")
        (self.results / "20261122T050000Z_m5" / "01_m5_B_8ch_r1").mkdir(parents=True)
        p = self.problems()
        self.assertEqual(sum("holds kind m5 only" in x for x in p), 2)
        self.assertTrue(any("freeze_m5_commit is none" in x for x in p))
        # The detections check's record is one with M5's header.
        self.record("20261122T060000Z_m5_detections", None, "2026-11-20T03:00:00Z", "check", "A15", freeze="none",
                    name="m5_detections.csv")
        self.assertTrue(any("m5_detections.csv: starts 2026-11-20T03:00:00Z, not after prereg-m5" in x
                            for x in self.problems()))
        self.assertTrue(any("AMENDMENTS does not have" in x for x in p))
        self.assertTrue(any("20261122T050000Z_m5: a session without any record" in x for x in p))


@unittest.skipIf(m5_summary is None, "needs numpy")
class Summary(unittest.TestCase):
    def counting(self, b, c, p, fps=5.0, sched=None):
        sched = sched or {"B": 46.0, "C": 22.0, "P": 25.0}
        out = {}
        for arm, values in (("B", b), ("C", c), ("P", p)):
            for r, v in enumerate(values, 1):
                out[(r, arm)] = None if v is None else {
                    "process_cpu_s": v, "inference_fps_min": fps if arm == "P" else 5.0,
                    "schedstat_total_s": sched[arm], "session": "s", "run": f"{arm}{r}"}
        return out

    def test_verdicts(self):
        v = m5_summary.verdicts(self.counting([44, 44, 44], [19, 19, 19], [33, 33, 33]), "P")
        self.assertEqual((v["spec_literal"], v["practical"], v["flags"]), ("adopted", "adopted", []))
        self.assertAlmostEqual(v["relative"], 0.25)
        v = m5_summary.verdicts(self.counting([44, 44, 44], [19, 19, 19], [33.1, 33, 33]), "P")
        self.assertEqual((v["spec_literal"], v["practical"]), ("rejected", "rejected"))
        # A channel below 4.75 fps: the practical verdict only.
        v = m5_summary.verdicts(self.counting([44, 44, 44], [19, 19, 19], [20, 20, 20], fps=4.7), "P")
        self.assertEqual((v["spec_literal"], v["practical"]), ("adopted", "indeterminate"))
        # A missing run: the runs that exist, flagged; none: indeterminate.
        v = m5_summary.verdicts(self.counting([44, 44, 44], [19, 19, 19], [20, None, 20]), "P")
        self.assertEqual((v["spec_literal"], v["practical"], v["runs_P"]), ("adopted", "indeterminate", 2))
        v = m5_summary.verdicts(self.counting([44, 44, 44], [19, 19, 19], [None, None, None]), "P")
        self.assertEqual((v["spec_literal"], v["practical"]), ("indeterminate", "indeterminate"))
        # The secondary metric with the opposite sign: mixed.
        v = m5_summary.verdicts(self.counting([44, 44, 44], [19, 19, 19], [20, 20, 20],
                                              sched={"B": 46.0, "C": 22.0, "P": 47.0}), "P")
        self.assertEqual(v["flags"], ["mixed"])
        # PROTOCOL §9.3's flag: a CV of process CPU above 10%.
        v = m5_summary.verdicts(self.counting([44, 44, 44], [19, 19, 19], [20, 26, 20]), "P")
        self.assertEqual(v["flags"], ["CV of P above 10%"])
        self.assertEqual(m5_summary.verdicts(self.counting([44, 44, 44], [19] * 3, [20, 23, 20]), "P")["flags"], [])

    def test_sessions_of_the_latest_tool_and_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            dirs = []
            for name, tool, sha in (("20261121T010000Z_m5", "a", "x"), ("20261121T030000Z_m5", "b", "x"),
                                    ("20261121T050000Z_m5", "b", "x"), ("20261121T020000Z_m5", "b", "y")):
                d = Path(tmp) / name
                d.mkdir()
                mm.write_keyed(d / "m5_session.csv", {"tool_sha256": tool, "package_sha256": sha}, [])
                dirs.append(d)
            self.assertEqual([d.name for d in m5_summary.current_sessions(dirs)],
                             ["20261121T030000Z_m5", "20261121T050000Z_m5"])
            self.assertEqual(m5.session_key(dirs[0]), ("a", "x"))
            (dirs[0] / "m5_session.csv").unlink()  # a session that died: one of its runs tells
            (dirs[0] / "01_m5_B_8ch_r1").mkdir()
            mm.write_keyed(dirs[0] / "01_m5_B_8ch_r1" / "m5_run.csv", {"tool_sha256": "c", "package_sha256": "z"}, [])
            self.assertEqual(m5.session_key(dirs[0]), ("c", "z"))
            # The summary reads a died session's key as the session tool does.
            mm.write_keyed(dirs[0] / "01_m5_B_8ch_r1" / "m5_run.csv", {"tool_sha256": "b", "package_sha256": "x"}, [])
            self.assertEqual(len(m5_summary.current_sessions(dirs)), 3)
            self.assertRegex(m5.tool_sha256(), "^[0-9a-f]{64}$")
            self.assertEqual(m5.m5_header(m5.utc(0), "check", "none", "c", "v", "s")["tool_sha256"], m5.tool_sha256())

    def test_headline_and_pairs(self):
        c = self.counting([44, 44, 44], [19, 19, 19], [24, 24, 24])
        more, kept = m5_summary.headline(c, "P")
        self.assertAlmostEqual(more, 5 / 19)
        self.assertAlmostEqual(kept, 20 / 25)
        d, m, half = m5_summary.paired(self.counting([44] * 3, [19, 20, 21], [24, 24, 23]), "P", "C", "process_cpu_s")
        self.assertEqual(d, [5, 4, 2])
        self.assertAlmostEqual(m, 11 / 3)
        self.assertAlmostEqual(half, 4.303 * (7 / 3) ** 0.5 / 3 ** 0.5, places=6)
        d, m, half = m5_summary.paired(self.counting([44] * 3, [19, None, None], [24, 24, 23]), "P", "C", "process_cpu_s")
        self.assertEqual((d, half), ([5], None))
        d, _, _ = m5_summary.paired(self.counting([44] * 3, [20, 20, 20], [25, 24, 22]), "P", "C", "process_cpu_s", True)
        self.assertEqual(d, [0.25, 0.2, 0.1])

    def test_run_values_of_a_service_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_run(tmp, "P")
            rows, reasons = mm.evaluate(d, "P", FIXED)
            mm.write_keyed(d / "m5_run.csv", {"start_time_utc": "2026-11-21T01:10:00Z", "run_kind": "m5", "arm": "P",
                                              "repetition": 1, "attempt": 1},
                           [("valid", "1"), ("reasons", "a, b")] + rows)
            (Path(tmp) / "idle_baseline.csv").write_text("# run_kind: m5\nkey,value\nwindow_s,120.0\n"
                                                         "schedstat_cpu_total_s,1.200\nschedstat_cpu0-3_s,0.600\n"
                                                         "system_cpu_total_s,2.400\n")
            v = m5_summary.run_values(d)
            self.assertEqual((v["valid"], v["process_cpu_s"], v["recomputed"], v["field_problems"], v["reasons"]),
                             (True, 0.8, "same", "none", "a, b"))
            self.assertAlmostEqual(v["latency_p50_ms"], 30.0)
            self.assertAlmostEqual(v["schedstat_total_s"], 10.0 - 1.2 * 5 / 120)
            self.assertAlmostEqual(v["system_total_s"], 12.0 - 2.4 * 5 / 120)
            self.assertAlmostEqual(v["schedstat_0-3_s"], 5.0 - 0.6 * 5 / 120)


class Detections(unittest.TestCase):
    def test_compare(self):
        import m5_detections as md
        ref = [{"channel": 0, "seq": s, "pts_us": s * 33333, "width": 1920, "height": 1080,
                "detections": [[2, 0.8411, 716.4, 451.7, 202.38, 134.08]]} for s in (0, 6, 12)]
        svc = [{"channel": 0, "seq": s, "pts_us": s * 33333, "width": 1920, "height": 1080, "detections": [
            {"class": 2, "name": "car", "score": 0.8411, "x": 716.40, "y": 451.70, "w": 202.38, "h": 134.08}]}
            for s in (0, 6, 12)]
        self.assertEqual(md.compare(ref, svc), ([], 3, 3))
        svc[1]["detections"][0]["score"] = 0.8412
        del svc[2]
        problems, _, _ = md.compare(ref, svc)
        self.assertEqual(len(problems), 2)
        self.assertIn("seq 6: detections differ", problems[0])
        self.assertIn("seq 12: no line", problems[1])
        self.assertIn("did not give", md.compare(ref[:1], svc)[0][0])


if __name__ == "__main__":
    unittest.main()
