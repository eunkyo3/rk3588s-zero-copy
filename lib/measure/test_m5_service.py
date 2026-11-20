#!/usr/bin/env python3
"""A test of m5_session.py's service run without the board: the real measure() and m5_metrics.evaluate()
against a stand-in for the service and for systemd.

A script named rkzc-detect serves frame lines of 8 channels on a Unix socket, as the program does, and logs
as it does; stand-ins for systemctl and journalctl on the PATH start it, stop it and answer the questions
measure() asks. Linux only (it needs /proc, AF_UNIX and sched_setaffinity), with a short warm-up and window.

Usage: python3 lib/measure/test_m5_service.py
"""
import argparse
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

LINUX = sys.platform.startswith("linux")
if LINUX:
    import m5_metrics as mm
    import m5_session as m5

SERVICE = r'''#!/usr/bin/env python3
# A stand-in for the program: 8 channels, 1 frame in 6 of 30 fps, lines as rkzc-detect 0.1.1 writes them.
import json, os, signal, socket, sys, time
sock_path, log_path, mode = sys.argv[1:4]
open("/proc/self/comm", "w").write("rkzc-detect")  # the name measure() expects of the main process
def log(msg):
    with open(log_path, "a") as f:
        f.write(json.dumps({"__REALTIME_TIMESTAMP": str(time.time_ns() // 1000), "MESSAGE": msg}) + "\n")
stop = []
signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(sock_path)
srv.listen(4)
srv.setblocking(False)
clients, seq, t_next, began = [], 0, time.monotonic(), time.monotonic()
for c in range(8):
    log(f"rkzc-detect: channel {c}: source_opened: rtsp://10.88.0.1:8554/ch{c + 1}")
while not stop:
    try:
        clients.append(srv.accept()[0])
    except BlockingIOError:
        pass
    now = time.monotonic()
    if now >= t_next:
        for c in range(8):
            t0 = time.clock_gettime_ns(time.CLOCK_MONOTONIC) - 30_000_000 - c * 1_000_000
            line = ('{"channel":%d,"seq":%d,"pts_us":%d,"time_ms":%d,"capture_ns":%d,"out_ns":%d,"decode_errors":0,'
                    '"width":1920,"height":1080,"detections":[]}\n' % (c, seq, seq * 1000000 // 30, time.time() * 1000,
                                                                       t0, t0 + 30_000_000 + c * 1_000_000))
            for s in list(clients):
                try:
                    s.sendall(line.encode())
                except OSError:
                    clients.remove(s)
        seq += 6
        t_next += 0.2
        sum(i * i for i in range(20000))  # some CPU time to measure
    if mode == "exit" and now - began > 2.0:
        sys.exit(3)
    if mode == "close" and now - began > 2.0 and clients:
        clients.pop().close()
        mode = "run"
    time.sleep(0.005)
log("rkzc-detect: SIGTERM, stopping")
for c in range(8):
    log(f"rkzc-detect: channel {c}: connected, decoded {seq}, inferred {seq // 6}, dropped 0, errors 0, "
        f"decode errors 0, reconnects 0")
log("rkzc-detect: output: 0 clients, 0 disconnected for not reading, 0 refused as too many")
os.unlink(sock_path)
'''

SYSTEMCTL = r'''#!/usr/bin/env python3
# A stand-in for systemctl: start, stop, show, is-active, is-enabled, daemon-reload, reset-failed.
import datetime, os, signal, subprocess, sys, time
d = os.environ["M5_FAKE_DIR"]
pidfile = os.path.join(d, "pid")
def pid():
    try:
        p = int(open(pidfile).read().split()[0])
        os.kill(p, 0)
        return p if open(f"/proc/{p}/stat").read().split(")")[-1].split()[0] != "Z" else 0
    except (OSError, ValueError):
        return 0
cmd = sys.argv[1]
if cmd == "start":
    os.sched_setaffinity(0, range(os.cpu_count()))  # systemd's services do not inherit the tool's cpu0
    p = subprocess.Popen([os.path.join(d, "rkzc-detect"), os.environ["M5_FAKE_SOCKET"], os.path.join(d, "journal"),
                          os.environ.get("M5_FAKE_MODE", "run")], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    now = datetime.datetime.now(datetime.timezone.utc)
    open(pidfile, "w").write(f"{p.pid} {time.clock_gettime_ns(time.CLOCK_MONOTONIC) // 1000} "
                             f"{now:%a %Y-%m-%d %H:%M:%S.%f UTC}")
elif cmd == "stop":
    p = pid()
    if p:
        os.kill(p, signal.SIGTERM)
        for _ in range(200):
            if not pid():
                break
            time.sleep(0.02)
elif cmd == "show":
    f = open(pidfile).read().split(" ", 2) if os.path.exists(pidfile) else ["0", "0", ""]
    values = {"MainPID": f[0] if pid() else "0", "ExecMainStartTimestampMonotonic": f[1],
              "ExecMainStartTimestamp": f[2], "NRestarts": "0", "InvocationID": "x",
              "ActiveState": "active" if pid() else "inactive", "Result": "success", "ExecMainStatus": "0",
              "ExecMainCode": "1", "ExecMainExitTimestamp": "", "DropInPaths": "", "CPUAffinity": ""}
    for k in sys.argv[sys.argv.index("-p") + 1].split(","):
        print(f"{k}={values[k]}")
elif cmd == "is-active":
    print("active" if pid() else "inactive")
elif cmd == "is-enabled":
    print("disabled")
'''

JOURNALCTL = r'''#!/usr/bin/env python3
import os, sys
if "--sync" not in sys.argv:
    path = os.path.join(os.environ["M5_FAKE_DIR"], "journal")
    sys.stdout.write(open(path).read() if os.path.exists(path) else "")
'''


@unittest.skipUnless(LINUX, "needs Linux")
class ServiceRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        for name, text in (("rkzc-detect", SERVICE), ("systemctl", SYSTEMCTL), ("journalctl", JOURNALCTL)):
            (d / name).write_text(textwrap.dedent(text))
            (d / name).chmod(0o755 | stat.S_IXUSR)
        self.env = dict(os.environ)
        os.environ.update(PATH=f"{d}:{os.environ['PATH']}", M5_FAKE_DIR=str(d), M5_FAKE_SOCKET=str(d / "detect.sock"),
                          M5_FAKE_MODE="run")
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self.env)))
        self.saved = (m5.SOCKET, m5.FREQ_FILES, os.sched_getaffinity(0))
        m5.SOCKET = str(d / "detect.sock")
        self.fixed = {}
        m5.FREQ_FILES = {}
        for i, dom in enumerate(mm.DOMAINS):
            (d / dom).write_text(f"{1000 + i}\n")
            m5.FREQ_FILES[dom] = str(d / dom)
            self.fixed[dom] = 1000 + i
        self.addCleanup(self.restore)
        self.d = d

    def restore(self):
        m5.SOCKET, m5.FREQ_FILES, cpus = self.saved
        os.sched_setaffinity(0, cpus)
        os.system(f"{self.d}/systemctl stop x")

    def session(self, warmup=2, duration=3):
        s = object.__new__(m5.M5Session)  # measure() needs the arguments and nothing of the session's start
        s.args = argparse.Namespace(warmup=warmup, duration=duration)
        s.pinned = False
        return s

    def measured(self, mode="run", **kw):
        os.environ["M5_FAKE_MODE"] = mode
        run_dir = self.d / f"run_{mode}"
        run_dir.mkdir()
        s, o, marks = self.session(**kw), m5.ss.Outcome(), {}
        try:
            code = s.measure(run_dir, o, marks)
        finally:
            os.sched_setaffinity(0, self.saved[2])
        return code, o, marks, run_dir

    def test_a_run(self):
        code, o, marks, run_dir = self.measured()
        self.assertEqual((code, o.reasons), (0, []))
        rows, reasons = mm.evaluate(run_dir, "P", self.fixed, m5.TICK_HZ)
        v = dict(rows)
        if os.cpu_count() >= 8:
            self.assertEqual(reasons, [], v)
        else:  # fewer CPUs than the board: rule 5 has to object, and nothing else may
            self.assertTrue(len(reasons) == 2 and all("rule 5" in r for r in reasons), reasons)
        self.assertEqual(v["window_s"], "3")
        self.assertEqual((v["inference_fps_min"], v["inference_fps_max"]), ("5.000", "5.000"))
        self.assertEqual((v["sampled_in_window"], v["missing_in_window"], v["decoder_drops"]), ("120", "0", "0"))
        self.assertLess(abs(float(v["read_w_late_ms"])), 50)
        self.assertLess(abs(float(v["read_e_late_ms"])), 50)
        self.assertGreater(float(v["process_cpu_s"]), 0.0)
        self.assertLess(abs(float(v["process_cpu_s"]) - float(v["thread_cpu_s"])), 0.05)
        self.assertEqual(v["service_output"], "0 0 0")
        self.assertEqual(int(v["service_inferred"]) % 8, 0)
        self.assertIn("1 threads", v["affinity_w"])
        # What measure() left: every file evaluate reads, the lines up to t_e + 2 s, six samples (k = 0..5).
        frames, _, problems = mm.parse_lines((run_dir / "lines.jsonl").read_text())
        self.assertEqual(problems, [])
        self.assertGreater(max(f["out_ns"] for f in frames), marks["t_e_ns"] + 1_500_000_000)
        self.assertEqual([r["k"] for r in mm.read_rows(run_dir / "samples.csv")], list("012345"))
        self.assertEqual(marks["main_pid_before_stop"], str(marks["main_pid"]))
        self.assertEqual(marks["active_before_stop"], "active")
        self.assertEqual(os.sched_getaffinity(0), self.saved[2])
        ch = mm.channel_rows(run_dir)
        self.assertEqual((len(ch), ch[0]["inference_fps"]), (8, "5.000"))
        # The stand-in's start time has the format the session reads for rule 4.
        import datetime as dt
        dt.datetime.strptime(marks["svc_start"], "%a %Y-%m-%d %H:%M:%S.%f UTC")
        # t_start is when a line of every channel had been read.
        self.assertGreaterEqual(marks["t_start_ns"], marks["t_svc_ns"])
        self.assertEqual(mm.first_complete(frames) is not None, True)

    def test_the_service_exits(self):
        code, o, marks, run_dir = self.measured("exit")
        self.assertNotEqual(code, 0)
        self.assertTrue(any("exited" in r or "closed the tool's connection" in r for r in o.reasons), o.reasons)
        self.assertTrue((run_dir / "marks.csv").exists() and (run_dir / "lines.jsonl").exists())
        rows, reasons = mm.evaluate(run_dir, "P", self.fixed, m5.TICK_HZ)
        self.assertIn("did not complete", reasons[0])

    def test_the_service_closes_the_connection(self):
        code, o, _, _ = self.measured("close")
        self.assertEqual(o.reasons, ["the service closed the tool's connection (rule 2)"])
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
