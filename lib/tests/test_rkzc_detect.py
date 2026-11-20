#!/usr/bin/env python3
"""rkzc-detect on the board: its exit statuses, its lines on stdout and on the Unix socket, and its stop.

    test_rkzc_detect.py RKZC_DETECT MODEL.rknn VIDEO.mp4 [OBJECTS.mp4]

VIDEO: a short H.264 file (a few seconds). OBJECTS: a video with objects the model detects; with it, a
client that does not read is seen to be disconnected (that needs lines long enough to fill its buffer).
Exit status 0 if every check passed.
"""
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time

failures = []


def check(ok, what):
    print(("ok:   " if ok else "FAIL: ") + what, flush=True)
    if not ok:
        failures.append(what)


def write_config(directory, name, model, sources, extra_top="", sections=""):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'model = "{model}"\nsources = [{", ".join(json.dumps(s) for s in sources)}]\n{extra_top}\n{sections}\n')
    return path


def run(binary, *args, timeout=60):
    r = subprocess.run([binary, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    log = "\n".join(l for l in r.stderr.splitlines() if l.startswith("rkzc-detect"))
    return r.returncode, r.stdout, log


def wait_for(predicate, seconds):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def read_lines(sock, count, seconds):
    """Up to `count` whole lines from a socket within `seconds`."""
    sock.settimeout(0.5)
    data, until = b"", time.monotonic() + seconds
    while data.count(b"\n") < count and time.monotonic() < until:
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            continue
        if not chunk:
            break
        data += chunk
    return [l for l in data.decode().split("\n")[:-1]][:count]


def frame_ok(obj):
    return (isinstance(obj.get("channel"), int) and isinstance(obj.get("seq"), int)
            and isinstance(obj.get("pts_us"), int) and obj.get("time_ms", 0) > 1.7e12 and obj.get("width", 0) > 0
            and obj.get("height", 0) > 0 and isinstance(obj.get("detections"), list)
            and all(set(d) == {"class", "name", "score", "x", "y", "w", "h"} and 0 <= d["class"] < 80
                    and isinstance(d["name"], str) and 0.25 <= d["score"] <= 1 and d["w"] > 0 and d["h"] > 0
                    for d in obj["detections"]))


def statuses(binary, model, video, tmp):
    code, out, log = run(binary)
    check(code == 2 and "usage" in log + out + "usage", f"no arguments: status {code}")
    code, out, _ = run(binary, "--version")
    check(code == 0 and out.startswith("rkzc-detect "), f"--version: {out.strip()!r}")
    code, _, log = run(binary, "--config", os.path.join(tmp, "nonexistent.toml"))
    check(code == 2 and "cannot read" in log, f"a missing configuration: status {code}, {log!r}")
    bad = write_config(tmp, "bad.toml", model, [video], extra_top="every_nth_frmae = 3")
    code, _, log = run(binary, "--config", bad)
    check(code == 2 and "bad.toml: line 3: unknown key every_nth_frmae" in log,
          f"a misspelt key: status {code}, {log!r}")
    nomodel = write_config(tmp, "nomodel.toml", os.path.join(tmp, "nonexistent.rknn"), [video])
    code, _, log = run(binary, "--config", nomodel)
    check(code == 2 and "nonexistent.rknn" in log, f"a missing model: status {code}, {log!r}")
    nodir = write_config(tmp, "nodir.toml", model, [video], sections='[output]\nto = "unix:/nonexistent-dir/x.sock"')
    code, _, log = run(binary, "--config", nodir)
    check(code == 2 and "cannot listen" in log, f"a socket that cannot be made: status {code}, {log!r}")

    good = write_config(tmp, "good.toml", model, [video])
    code, out, _ = run(binary, "--config", good, "--check")
    check(code == 0 and "model  ok" in out and "source ok" in out, f"--check: status {code}")
    missing = write_config(tmp, "missing.toml", model, [video, os.path.join(tmp, "nonexistent.mp4")])
    code, out, _ = run(binary, "--config", missing, "--check")
    check(code == 1 and "source ok" in out and "source FAILED" in out, f"--check with a missing source: status {code}")


def on_stdout(binary, model, video, tmp):
    """A file played once: every line is JSON, the program exits 0 when the file has ended."""
    cfg = write_config(tmp, "stdout.toml", model, [video])
    code, out, log = run(binary, "--config", cfg)
    lines = [json.loads(l) for l in out.splitlines()]
    frames = [l for l in lines if "event" not in l]
    events = [l["event"] for l in lines if "event" in l]
    check(code == 0 and "every source has ended" in log, f"a file that ends: status {code}")
    check(len(frames) >= 5 and all(frame_ok(f) for f in frames), f"{len(frames)} frame lines, every one well formed")
    check(sorted(f["seq"] for f in frames) == [6 * i for i in range(len(frames))], "seq: 0, 6, 12, ...")
    check(events == ["source_opened", "source_ended"], f"event lines: {events}")
    check(f"inferred {len(frames)}" in log, "the final log line counts the same frames")

    quiet = write_config(tmp, "quiet.toml", model, [video], sections="[output]\nevents = false\nempty_frames = false")
    code, out, _ = run(binary, "--config", quiet)
    lines = [json.loads(l) for l in out.splitlines()]
    check(code == 0 and all("event" not in l and l["detections"] for l in lines),
          f"events and empty frames turned off: {len(lines)} lines left")

    # The reader goes away: the program ends with status 1 instead of running on unheard.
    loop = write_config(tmp, "loop.toml", model, [video], extra_top="loop_files = true")
    p = subprocess.Popen([binary, "--config", loop], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p.stdout.readline()
    p.stdout.close()
    try:
        code = p.wait(15)
    except subprocess.TimeoutExpired:
        p.kill()
        code = None
    log = p.stderr.read().decode()
    check(code == 1 and "reader is gone" in log, f"stdout closed by its reader: status {code}")


def on_socket(binary, model, video, objects, tmp):
    path = os.path.join(tmp, "detect.sock")
    # A file left by a program that is gone must not stop the start.
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(path)
    stale.close()
    check(os.path.exists(path), "a stale socket file is in the way")

    source = objects or video
    cfg = write_config(tmp, "socket.toml", model, [source, source],
                       extra_top="loop_files = true\nevery_nth_frame = 1" if objects else "loop_files = true",
                       sections=f'[output]\nto = "unix:{path}"\nsocket_mode = "0600"\n[log]\nstats_every_s = 2')
    # The log goes to a file: a pipe nobody reads would fill and hold the program.
    log_path = os.path.join(tmp, "socket.log")
    log_file = open(log_path, "wb")
    p = subprocess.Popen([binary, "--config", cfg], stdout=subprocess.DEVNULL, stderr=log_file)

    def log_text():
        with open(log_path, "rb") as f:
            return f.read().decode(errors="replace")

    try:
        def connect():
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.connect(path)
                return s
            except OSError:
                s.close()
                return None

        first = []
        check(wait_for(lambda: first.append(connect()) or first[-1] is not None, 10), "the socket accepts a client")
        a = first[-1]
        check(a is not None and (os.stat(path).st_mode & 0o777) == 0o600, "the socket file has the mode asked for")
        if a is None:
            return
        b = connect()
        la = [json.loads(l) for l in read_lines(a, 20, 10)]
        lb = [json.loads(l) for l in read_lines(b, 20, 10)]
        fa = [l for l in la if "event" not in l]
        check(len(la) == 20 and len(lb) == 20, f"two clients each got 20 lines ({len(la)}, {len(lb)})")
        check(all(frame_ok(f) for f in fa) and {f["channel"] for f in fa} == {0, 1},
              "the frame lines are well formed and come from both channels")
        if objects:
            check(sum(len(f["detections"]) for f in fa) > 0, "there are detections in them")

        # A second program on the same socket is refused and leaves the first one's socket alone.
        code, _, log = run(binary, "--config", cfg)
        check(code == 2 and "in use" in log and os.path.exists(path), f"a second program: status {code}, {log!r}")

        if objects:
            # A client that connects and never reads: dropped when its buffers are full, while the
            # reading clients go on.
            idle = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            idle.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            idle.connect(path)
            got = 0
            until = time.monotonic() + 60
            while time.monotonic() < until and "1 disconnected for not reading" not in log_text():
                got += len(read_lines(a, 50, 1)) + len(read_lines(b, 50, 1))
            dropped = "1 disconnected for not reading" in log_text()
            check(dropped and got > 100, f"the idle client was disconnected; the others read {got} lines meanwhile")
            idle.close()

        a.close()  # a client that leaves is not an error
        more = read_lines(b, 10, 10)
        check(len(more) == 10, "after one client left, the other still gets lines")

        t0 = time.monotonic()
        p.send_signal(signal.SIGTERM)
        try:
            code = p.wait(15)
        except subprocess.TimeoutExpired:
            code = None
        took = time.monotonic() - t0
        log = log_text()
        check(code == 0 and took < 10 and "SIGTERM, stopping" in log, f"SIGTERM: status {code} after {took:.2f} s")
        check(not os.path.exists(path), "the socket file is removed")
        check("channel 1: connected" in log and "output: " in log, "the counters were logged")
        b.close()
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
        log_file.close()


def main():
    if len(sys.argv) not in (4, 5):
        print(__doc__, file=sys.stderr)
        return 2
    binary, model, video = sys.argv[1:4]
    objects = sys.argv[4] if len(sys.argv) == 5 else None
    with tempfile.TemporaryDirectory(prefix="rkzc-test-") as tmp:
        statuses(binary, model, video, tmp)
        on_stdout(binary, model, video, tmp)
        on_socket(binary, model, video, objects, tmp)
    print(f"{len(failures)} failed" if failures else "all passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
