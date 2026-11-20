#!/usr/bin/env python3
"""The Python module on the board.

    RKZC_LIBRARY=.../librkzc.so.0 PYTHONPATH=lib/python \\
        python3 test_python.py MODEL.rknn VIDEO.mp4 [OBJECTS.mp4 RKZC_JSONL]

VIDEO: a short H.264 file (a few seconds). With OBJECTS (a video with objects the model detects) and
RKZC_JSONL (the C++ example), the Python example's lines are compared with the C++ example's.
Exit status 0 if every check passed.
"""
import contextlib
import gc
import io
import os
import subprocess
import sys
import threading
import time

import rkzc

failures = []


def check(ok, what):
    print(("ok:   " if ok else "FAIL: ") + what, flush=True)
    if not ok:
        failures.append(what)


def raises(fn, what, needle=""):
    try:
        fn()
    except rkzc.Error as e:
        check(needle in str(e) and str(e) != "", f"{what}: Error({str(e)[:70]!r})")
    else:
        check(False, f"{what}: no Error")


def open_fds():
    return len(os.listdir("/proc/self/fd"))


def basics(model, video):
    check(rkzc.version() != "", f"version {rkzc.version()}")
    check(rkzc.class_name(0) == "person" and rkzc.class_name(999) == "?", "class_name")
    mi = rkzc.check_model(model)
    check(mi.width > 0 and mi.height > 0 and mi.outputs > 0 and mi.runtime != "", f"check_model: {mi}")
    si = rkzc.check_source(video)
    check(si.width > 0 and si.height > 0, f"check_source: {si}")
    raises(lambda: rkzc.check_model("/nonexistent.rknn"), "check_model on a missing file")
    raises(lambda: rkzc.check_source("/nonexistent.mp4"), "check_source on a missing file")
    raises(lambda: rkzc.Detector([], model), "no source")
    raises(lambda: rkzc.Detector([video], "/nonexistent.rknn"), "a missing model")
    raises(lambda: rkzc.Detector([video], model, post="nonsense"), "an unknown post")
    raises(lambda: rkzc.Detector([video], model, every_nth_frame=0), "every_nth_frame 0")


def single_play(model, video):
    frames, events = [], []
    seen_threads = set()

    def on_frame(f):
        seen_threads.add(threading.get_ident())
        frames.append(f)

    with rkzc.Detector(video, model, on_frame=on_frame, on_event=events.append) as det:  # a str, not a list
        check(det.model_size[0] > 0, f"model_size {det.model_size}")
        det.start()
        raises(det.start, "a second start")
        ended = det.wait(30)
        det.stop()
        st = det.stats()
    check(ended, "wait() returned True when the file ended")
    check(len(st) == 1 and st[0].inferred == len(frames) and len(frames) > 0,
          f"{len(frames)} frames delivered, stats say {st}")
    if len(st) != 1:
        return
    check(st[0].sampled == st[0].inferred + st[0].dropped and st[0].errors == 0, f"stats: {st[0]}")
    check(all(f.channel == 0 and f.seq % 6 == 0 and f.width > 0 and f.capture_ns > 0 for f in frames),
          "every frame: channel 0, seq a multiple of 6, a size and a capture time")
    check(len({f.seq for f in frames}) == len(frames), "no seq twice")
    check(all(isinstance(d, rkzc.Detection) and isinstance(d.cls, int) and isinstance(d.name, str)
              for f in frames for d in f.detections), "detections are Detection tuples")
    kinds = [e.kind for e in events]
    check(rkzc.EventKind.SOURCE_OPENED in kinds and rkzc.EventKind.SOURCE_ENDED in kinds,
          f"events: {[k.name for k in kinds]}")
    check(threading.get_ident() not in seen_threads, "the callbacks came from the library's threads")
    raises(det.stats, "stats() after close", "closed")
    det.close()  # again: nothing
    det.stop()   # after close: nothing
    check(det.wait(1) is True, "wait() after close returns True")


def callback_failures(model, video):
    """An exception in a callback is counted and the detector goes on; start() and stop() from a callback
    are refused."""
    refused = []
    holder = {}

    def on_frame(f):
        for call in (holder["det"].stop, holder["det"].start, holder["det"].close):
            try:
                call()
            except rkzc.Error as e:
                refused.append(str(e))
        raise ValueError("the test's own exception")

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        det = rkzc.Detector([video], model, loop_files=True, on_frame=on_frame)
        holder["det"] = det
        det.start()
        time.sleep(1.5)
        det.stop()
        st = det.stats()[0]
        det.close()
    check(det.callback_errors == st.inferred and st.inferred >= 3,
          f"{det.callback_errors} callback exceptions counted, {st.inferred} frames delivered")
    check(len(refused) == 3 * st.inferred and all("callback" in r for r in refused),
          f"stop(), start() and close() from a callback refused: {refused[:1]}")
    check("the test's own exception" in err.getvalue(), "the exception was printed")


def stats_during_stop(model, video):
    """A callback calls stats() while the main thread is inside stop(): stop() waits for that callback,
    so the two must not need the same lock."""
    waiting, in_stop = threading.Event(), threading.Event()
    result = []
    holder = {}

    def on_frame(f):
        if result or waiting.is_set():
            return
        waiting.set()
        in_stop.wait(10)
        time.sleep(0.3)  # the main thread is inside stop() by now
        try:
            result.append(holder["det"].stats()[0].decoded)
        except BaseException as e:
            result.append(e)

    det = rkzc.Detector([video], model, loop_files=True, on_frame=on_frame)
    holder["det"] = det
    det.start()
    got = waiting.wait(10)
    in_stop.set()
    t0 = time.monotonic()
    det.stop()
    stop_s = time.monotonic() - t0
    det.close()
    check(got and len(result) == 1 and isinstance(result[0], int) and result[0] > 0,
          f"stats() from a callback during stop() returned {result}")
    check(0.3 <= stop_s < 5, f"stop() waited for that callback: {stop_s:.2f} s")


def concurrent_close(model, video):
    """close() from two threads: the second returns only when the detector has stopped."""
    delivered_after = []
    closed = threading.Event()

    def on_frame(f):
        if closed.is_set():
            delivered_after.append(f.seq)
        time.sleep(0.2)  # so that the first close() takes a while

    det = rkzc.Detector([video], model, loop_files=True, on_frame=on_frame)
    det.start()
    time.sleep(1.0)
    first = threading.Thread(target=det.close)
    first.start()
    time.sleep(0.05)
    det.close()  # the second call
    closed.set()
    time.sleep(0.5)
    first.join(10)
    check(not first.is_alive() and not delivered_after,
          f"no frame after the second close() returned ({len(delivered_after)} came)")
    before = open_fds()
    for _ in range(3):  # a detector that is dropped unclosed is freed at once: nothing refers to it
        rkzc.Detector([video], model)
    check(open_fds() <= before and len(rkzc._registry) == 0,
          f"three dropped detectors: file descriptors {before} before, {open_fds()} after; "
          f"{len(rkzc._registry)} left in the table")


def missing_source(model, video):
    events = []
    with rkzc.Detector(["/nonexistent.mp4", video], model, loop_files=True, reconnect_initial_ms=100,
                       reconnect_max_ms=400, on_event=events.append) as det:
        det.start()
        check(det.wait(3) is False, "wait(3) timed out: a looped file never ends")
        st = det.stats()
    errors = [e for e in events if e.kind == rkzc.EventKind.SOURCE_ERROR and e.channel == 0]
    check(len(errors) >= 3 and errors[0].message != "", f"{len(errors)} source errors on the missing channel")
    check(st[0].inferred == 0 and not st[0].connected and st[1].inferred > 0,
          f"the other channel played: {st[1].inferred} frames")


def abandoned(model, video):
    """A running detector whose last reference is dropped stays alive until it is closed, and closing
    gives every file descriptor back."""
    before = open_fds()
    count = [0]

    def on_frame(_):
        count[0] += 1

    det = rkzc.Detector([video], model, loop_files=True, on_frame=on_frame)
    det.start()
    del det
    gc.collect()
    time.sleep(1.0)
    n1 = count[0]
    gc.collect()
    time.sleep(1.0)
    check(count[0] > n1 > 0, f"still delivering after the reference was dropped ({n1}, then {count[0]})")
    rkzc._close_all()  # what runs at exit; at_exit() below lets it run at a real exit
    n2 = count[0]
    time.sleep(0.5)
    check(count[0] == n2, "no callback after _close_all()")
    gc.collect()
    check(open_fds() <= before, f"file descriptors: {before} before, {open_fds()} after")


EXIT_RUNNING = """
import sys, time, rkzc
n = [0]
def on_frame(f):
    n[0] += 1
det = rkzc.Detector([sys.argv[2]], sys.argv[1], loop_files=True, on_frame=on_frame)
det.start()
del det
time.sleep(1.5)
print("frames", n[0])
"""

# A daemon thread is still inside close() (slow callbacks) when the main thread returns.
EXIT_CLOSING = """
import sys, threading, time, rkzc
def on_frame(f):
    time.sleep(0.3)
def owner():
    with rkzc.Detector([sys.argv[2]], sys.argv[1], loop_files=True, on_frame=on_frame) as det:
        det.start()
        time.sleep(1.5)
threading.Thread(target=owner, daemon=True).start()
time.sleep(1.55 + float(sys.argv[3]))
print("main returns")
"""


def at_exit(model, video):
    """A program that returns with a detector running, or being closed by another thread, exits cleanly."""
    def run(code, *extra):
        try:
            r = subprocess.run([sys.executable, "-c", code, model, video, *extra], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return None, "", "did not exit in 60 s"
        bad = [l for l in r.stderr.splitlines() if "atal" in l or "Traceback" in l or "terminate" in l]
        return r.returncode, r.stdout.strip(), "; ".join(bad)

    code, out, bad = run(EXIT_RUNNING)
    check(code == 0 and out.startswith("frames") and out != "frames 0" and not bad,
          f"exit with a detector running: status {code}, {out!r} {bad}")
    for delay in ("0.0", "0.1", "0.3"):
        code, out, bad = run(EXIT_CLOSING, delay)
        check(code == 0 and out == "main returns" and not bad,
              f"exit {delay} s into another thread's close(): status {code} {bad}")


def detections_sane(model, objects):
    """Every field of a detection in its range: a field in the wrong place would not be."""
    frames = []
    with rkzc.Detector([objects], model, on_frame=frames.append) as det:
        det.start()
        det.wait(3)
    dets = [(f, d) for f in frames for d in f.detections]
    sane = all(0 <= d.cls < 80 and 0.25 <= d.score <= 1.0 and d.w > 0 and d.h > 0 and d.x > -1 and d.y > -1
               and d.x + d.w < f.width + 1 and d.y + d.h < f.height + 1 for f, d in dets)
    check(len(dets) > 20 and sane, f"{len(dets)} detections in {len(frames)} frames: class, score and box in range")
    check(any(d.name == "person" for _, d in dets) or len({d.cls for _, d in dets}) > 1,
          f"classes seen: {sorted({d.name for _, d in dets})[:6]}")


def same_as_cpp(model, objects, rkzc_jsonl):
    """The Python example's lines against the C++ example's, on the frames both saw."""
    example = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python", "examples", "detect_jsonl.py")
    common = ["--model", model, "--every", "6", "--seconds", "9", objects]
    cpp = subprocess.run([rkzc_jsonl] + common, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    py = subprocess.run([sys.executable, example] + common, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        text=True)
    check(cpp.returncode == 0 and py.returncode == 0, f"both examples exited 0 ({cpp.returncode}, {py.returncode})")

    def by_seq(text):
        import json
        lines = {}
        for line in text.splitlines():
            lines.setdefault(json.loads(line)["seq"], line)
        return lines

    a, b = by_seq(cpp.stdout), by_seq(py.stdout)
    shared = sorted(s for s in a if s in b and s < 200)
    different = [s for s in shared if a[s] != b[s]]
    detections = sum(a[s].count("[") - 1 for s in shared)
    check(len(shared) >= 30 and detections > 0 and not different,
          f"{len(shared)} frames with {detections} detections: the same lines"
          + (f"; first difference at seq {different[0]}:\n  {a[different[0]]}\n  {b[different[0]]}" if different else ""))


def main():
    if len(sys.argv) not in (3, 5):
        print(__doc__, file=sys.stderr)
        return 2
    model, video = sys.argv[1], sys.argv[2]
    basics(model, video)
    single_play(model, video)
    callback_failures(model, video)
    stats_during_stop(model, video)
    concurrent_close(model, video)
    missing_source(model, video)
    abandoned(model, video)
    at_exit(model, video)
    if len(sys.argv) == 5:
        detections_sane(model, sys.argv[3])
        same_as_cpp(model, sys.argv[3], sys.argv[4])
    print(f"{len(failures)} failed" if failures else "all passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
