#!/usr/bin/env python3
"""rkzc-detect on the board: its exit statuses, its lines on stdout and on the Unix socket, and its stop.

    test_rkzc_detect.py RKZC_DETECT MODEL.rknn VIDEO.mp4 [OBJECTS.mp4]

VIDEO: a short H.264 file (a few seconds). OBJECTS: a video with objects the model detects; with it, a
client that does not read is seen to be disconnected (that needs lines long enough to fill its buffer).
Exit status 0 if every check passed.
"""
import grp
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
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
    """Returns (status, stdout, the program's own log lines, all of stderr)."""
    r = subprocess.run([binary, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    log = "\n".join(l for l in r.stderr.splitlines() if l.startswith("rkzc-detect"))
    return r.returncode, r.stdout, log, r.stderr


def wait_for(predicate, seconds):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class Reader(threading.Thread):
    """Reads a socket's lines in the background, so that the client never falls behind."""

    def __init__(self, sock):
        super().__init__(daemon=True)
        self.sock, self.data, self.closed_by_server, self.lock = sock, b"", False, threading.Lock()
        sock.settimeout(0.2)
        self.stopping = False
        self.start()

    def run(self):
        while not self.stopping:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                return
            if not chunk:
                self.closed_by_server = True
                return
            with self.lock:
                self.data += chunk

    def lines(self):
        with self.lock:
            return self.data.decode().split("\n")[:-1]  # the last piece is not a whole line yet

    def close(self):
        self.stopping = True
        self.join(2)
        self.sock.close()


def frame_ok(obj):
    return (isinstance(obj.get("channel"), int) and isinstance(obj.get("seq"), int)
            and isinstance(obj.get("pts_us"), int) and obj.get("time_ms", 0) > 1.7e12 and obj.get("width", 0) > 0
            and obj.get("height", 0) > 0 and isinstance(obj.get("detections"), list)
            and all(set(d) == {"class", "name", "score", "x", "y", "w", "h"} and 0 <= d["class"] < 80
                    and isinstance(d["name"], str) and 0.25 <= d["score"] <= 1 and d["w"] > 0 and d["h"] > 0
                    for d in obj["detections"]))


def statuses(binary, model, video, tmp):
    code, _, _, err = run(binary)
    check(code == 2 and "usage: rkzc-detect" in err, f"no arguments: status {code}, the usage is printed")
    code, out, _, _ = run(binary, "--version")
    check(code == 0 and out.startswith("rkzc-detect "), f"--version: {out.strip()!r}")
    code, _, log, _ = run(binary, "--config", os.path.join(tmp, "nonexistent.toml"))
    check(code == 2 and "cannot read" in log, f"a missing configuration: status {code}, {log!r}")
    bad = write_config(tmp, "bad.toml", model, [video], extra_top="every_nth_frmae = 3")
    code, _, log, _ = run(binary, "--config", bad)
    check(code == 2 and "bad.toml: line 3: unknown key every_nth_frmae" in log,
          f"a misspelt key: status {code}, {log!r}")
    nomodel = write_config(tmp, "nomodel.toml", os.path.join(tmp, "nonexistent.rknn"), [video])
    code, _, log, _ = run(binary, "--config", nomodel)
    check(code == 2 and "nonexistent.rknn" in log, f"a missing model: status {code}, {log!r}")
    nodir = write_config(tmp, "nodir.toml", model, [video], sections='[output]\nto = "unix:/nonexistent-dir/x.sock"')
    code, _, log, _ = run(binary, "--config", nodir)
    check(code == 2 and "cannot listen" in log, f"a socket that cannot be made: status {code}, {log!r}")
    nogroup = write_config(tmp, "nogroup.toml", model, [video],
                           sections=f'[output]\nto = "unix:{tmp}/g.sock"\nsocket_group = "no-such-group-rkzc"')
    code, _, log, _ = run(binary, "--config", nogroup)
    check(code == 2 and "there is no group" in log and not os.path.exists(f"{tmp}/g.sock"),
          f"a socket group that does not exist: status {code}, {log!r}")

    good = write_config(tmp, "good.toml", model, [video])
    code, out, _, _ = run(binary, "--config", good, "--check")
    check(code == 0 and "model  ok" in out and "detector ok" in out and "source ok" in out, f"--check: status {code}")
    missing = write_config(tmp, "missing.toml", model, [video, os.path.join(tmp, "nonexistent.mp4")])
    code, out, _, _ = run(binary, "--config", missing, "--check")
    check(code == 1 and "source ok" in out and "source FAILED" in out, f"--check with a missing source: status {code}")
    # A model the chosen post-processing cannot read: a run refuses it, so --check must too.
    wrong = write_config(tmp, "wrongpost.toml", model, [video], extra_top='post = "v10"')
    code, out, _, _ = run(binary, "--config", wrong, "--check")
    run_code, _, _, _ = run(binary, "--config", wrong)
    check(code == 1 and "detector FAILED" in out and run_code == 2,
          f"--check with a post-processing that does not fit the model: status {code}; a run: status {run_code}")


def on_stdout(binary, model, video, tmp):
    """A file played once: every line is JSON, the program exits 0 when the file has ended."""
    cfg = write_config(tmp, "stdout.toml", model, [video])
    code, out, log, _ = run(binary, "--config", cfg)
    lines = [json.loads(l) for l in out.splitlines()]
    frames = [l for l in lines if "event" not in l]
    events = [l["event"] for l in lines if "event" in l]
    check(code == 0 and "every source has ended" in log, f"a file that ends: status {code}")
    check(len(frames) >= 5 and all(frame_ok(f) for f in frames), f"{len(frames)} frame lines, every one well formed")
    seqs = [f["seq"] for f in frames]
    check(len(set(seqs)) == len(seqs) and all(s % 6 == 0 for s in seqs) and 0 in seqs,
          "seq: multiples of 6 from 0, none twice")
    check(events == ["source_opened", "source_ended"], f"event lines: {events}")
    check(f"inferred {len(frames)}," in log, "the final log line counts the same frames")

    with_detections = sum(1 for f in frames if f["detections"])
    quiet = write_config(tmp, "quiet.toml", model, [video], sections="[output]\nevents = false\nempty_frames = false")
    code, out, _, _ = run(binary, "--config", quiet)
    lines = [json.loads(l) for l in out.splitlines()]
    check(code == 0 and len(lines) == with_detections and with_detections < len(frames)
          and all("event" not in l and l["detections"] for l in lines),
          f"events and empty frames turned off: {len(lines)} lines, the {with_detections} frames with a detection"
          + ("" if with_detections else " (the video has none: the lines' presence is not shown)"))

    # A password in a source's URL reaches neither the lines nor the program's log.
    secret = write_config(tmp, "secret.toml", model, [video, "rtsp://someone:hunter2@127.0.0.1:9/cam"],
                          sections="[reconnect]\ninitial_ms = 200\nmax_ms = 400")
    p = subprocess.Popen([binary, "--config", secret], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(4)
    p.send_signal(signal.SIGTERM)
    out, err = p.communicate(timeout=20)
    own_log = "\n".join(l for l in err.splitlines() if l.startswith("rkzc-detect"))
    errors = [json.loads(l) for l in out.splitlines() if '"source_error"' in l]
    check(len(errors) >= 2 and all("rtsp://***@127.0.0.1:9/cam" in e["message"] for e in errors)
          and "hunter2" not in out and "hunter2" not in own_log and "***@127.0.0.1" in own_log,
          f"{len(errors)} error lines for a URL with a password: the password is in no line and not in the log")
    if "hunter2" in err:
        print("      note: the libraries underneath printed the URL to stderr themselves")

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

    # A group this user is in, other than its own, for socket_group.
    other_groups = [g for g in os.getgroups() if g != os.getgid()]
    group = grp.getgrgid(other_groups[0]).gr_name if other_groups else grp.getgrgid(os.getgid()).gr_name
    source = objects or video
    cfg = write_config(tmp, "socket.toml", model, [source, source],
                       extra_top="loop_files = true\nevery_nth_frame = 1" if objects else "loop_files = true",
                       sections=f'[output]\nto = "unix:{path}"\nsocket_mode = "0660"\nsocket_group = "{group}"\n'
                                f'max_clients = 3\n[log]\nstats_every_s = 1')
    # The log goes to a file: a pipe nobody reads would fill and hold the program.
    log_path = os.path.join(tmp, "socket.log")
    log_file = open(log_path, "wb")
    p = subprocess.Popen([binary, "--config", cfg], stdout=subprocess.DEVNULL, stderr=log_file)

    def log_text():
        with open(log_path, "rb") as f:
            return f.read().decode(errors="replace")

    def counter(name):
        found = re.findall(r"(\d+) " + name, log_text())
        return int(found[-1]) if found else 0

    def connect():
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(path)
            return s
        except OSError:
            s.close()
            return None

    readers = []
    try:
        def listening():
            s = connect()
            if s:
                s.close()
            return s is not None

        check(wait_for(listening, 10), "the socket accepts a client")
        st = os.stat(path)
        check((st.st_mode & 0o777) == 0o660 and grp.getgrgid(st.st_gid).gr_name == group,
              f"the socket file has the mode and the group asked for (0660, {group})")

        # A second program on the same socket is refused and leaves the first one's socket alone. Before
        # the clients connect: nobody then falls behind while the second program loads the model.
        code, _, log, _ = run(binary, "--config", cfg)
        check(code == 2 and "in use" in log and os.path.exists(path), f"a second program: status {code}, {log!r}")

        a, b = Reader(connect()), Reader(connect())
        readers += [a, b]
        check(wait_for(lambda: len(a.lines()) >= 20 and len(b.lines()) >= 20, 15),
              f"two clients each got 20 lines ({len(a.lines())}, {len(b.lines())})")
        for name, reader in (("first", a), ("second", b)):
            frames = [l for l in map(json.loads, reader.lines()) if "event" not in l]
            check(len(frames) > 0 and all(frame_ok(f) for f in frames) and {f["channel"] for f in frames} == {0, 1},
                  f"the {name} client's frame lines are well formed and come from both channels")
            if objects:
                check(sum(len(f["detections"]) for f in frames) > 0, f"the {name} client's lines hold detections")

        # max_clients = 3: the third is served, the fourth is closed at once.
        c = Reader(connect())
        readers.append(c)
        check(wait_for(lambda: len(c.lines()) >= 3, 10), "a third client is served")
        d = Reader(connect())
        readers.append(d)
        check(wait_for(lambda: d.closed_by_server, 5) and not d.lines() and not c.closed_by_server,
              "a fourth client, beyond max_clients, is closed without a line")
        check(wait_for(lambda: counter("refused as too many") == 1, 5), "the log counts it as refused")
        c.close()

        if objects:
            # A client that connects and never reads is dropped when its buffer is full, while the
            # reading clients go on.
            before = len(a.lines()) + len(b.lines())
            wait_for(lambda: counter("clients") == 2, 5)  # the third client's leaving has been noticed
            idle = connect()
            dropped = wait_for(lambda: counter("disconnected for not reading") >= 1, 60)
            got = len(a.lines()) + len(b.lines()) - before
            check(dropped and counter("disconnected for not reading") == 1 and got > 100
                  and not a.closed_by_server and not b.closed_by_server,
                  f"the idle client was disconnected, and only it; the others read {got} lines meanwhile")
            idle.close()

        n = len(b.lines())
        a.close()  # a client that leaves is not an error
        check(wait_for(lambda: len(b.lines()) >= n + 10, 10), "after one client left, the other still gets lines")

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
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
        log_file.close()
        for r in readers:
            r.close()


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
