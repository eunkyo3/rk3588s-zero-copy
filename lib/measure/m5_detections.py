#!/usr/bin/env python3
"""prereg/M5.md §8 step 3, "Detections": the installed service's lines against rkzc-jsonl's, with one file
as the only source of each.

    m5_detections.py --deb rkzc_<v>_arm64.deb --jsonl PATH/rkzc-jsonl --video FILE.mp4 [--out DIR]

rkzc-jsonl is the library's example program, built from the package's commit; it is not in the package.
Both read the same file once, with the shipped sampling (1 frame in 6) and post-processing ("auto").
For every frame of rkzc-jsonl the service must have a line with the same detections: the same classes in
the same order, and the same scores and boxes as printed (4 and 2 decimals; both print the library's
numbers). The service's lines must also pass the field checks of step 3.

The service reads the file from /var/lib/rkzc (its user cannot read a home directory); the copy is
removed afterwards, and /etc/rkzc/detect.toml is left as the shipped file. Run as root on the board, from a
clean tree of the tool's commit, with the package installed and the service stopped. As for a session, the
.deb must be the file lib/measure/package.txt records and every file it holds must equal the installed one.

The record goes to lib/measure/results/<UTC>_m5_detections: m5_detections.csv with M5's header (run kind
check) and the result, both programs' lines, and the configuration used. Exit status 0 if they agree, 1 if
not, 2 if the check could not be made.
"""
import argparse
import datetime as dt
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import m5_metrics as mm  # noqa: E402
import m5_session as m5  # noqa: E402  (the unit's name, the paths, the shipped configuration)

COPY = Path("/var/lib/rkzc/m5_detections_check.mp4")


def compare(reference, service):
    """(problems, frames compared, detections compared). reference: rkzc-jsonl's objects; service: the
    service's frame lines."""
    got = {f["seq"]: f for f in service if f["channel"] == 0}
    problems, detections = [], 0
    for r in reference:
        s = got.get(r["seq"])
        if s is None:
            problems.append(f"seq {r['seq']}: no line from the service")
            continue
        want = [tuple(d) for d in r["detections"]]
        have = [(d["class"], d["score"], d["x"], d["y"], d["w"], d["h"]) for d in s["detections"]]
        detections += len(want)
        if (r["pts_us"], r["width"], r["height"]) != (s["pts_us"], s["width"], s["height"]):
            problems.append(f"seq {r['seq']}: pts or size differs")
        elif want != have:
            problems.append(f"seq {r['seq']}: detections differ: {want[:2]} against {have[:2]}")
    extra = sorted(set(got) - {r["seq"] for r in reference})
    if extra:
        problems.append(f"{len(extra)} lines of the service for frames rkzc-jsonl did not give (first seq {extra[0]})")
    return problems, len(reference), detections


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ("deb", "jsonl", "video"):
        ap.add_argument("--" + name, required=True)
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    args.deb, args.jsonl, args.video = (str(Path(p).resolve()) for p in (args.deb, args.jsonl, args.video))
    os.chdir(m5.ROOT)
    start = dt.datetime.now(dt.timezone.utc)
    try:
        if os.geteuid() != 0:
            raise m5.ss.Stop("run as root")
        tool_commit, freeze_m5 = m5.tool_state()
        version, sha = m5.recorded_package(args.deb)
        same = m5.installed_as_packaged(args.deb, version)
        if m5.service_state()[0] != "inactive" or m5.processes({"bench_main", "rkzc-detect"}):
            raise m5.ss.Stop("the service is not stopped, or a bench process exists")
        shipped = m5.shipped_config(args.deb)
    except (m5.ss.Stop, OSError) as e:
        print(f"m5_detections: not starting: {e}", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else m5.RESULTS / f"{start:%Y%m%dT%H%M%SZ}_m5_detections"
    out.mkdir(parents=True, exist_ok=True)
    header = m5.m5_header(start, "check", freeze_m5, tool_commit, version, sha)

    def record(rows):
        mm.write_keyed(out / "m5_detections.csv", header, [("package_files_identical", same)] + rows)
    r = subprocess.run([args.jsonl, "--model", str(m5.MODEL), "--every", str(mm.EVERY), args.video],
                       capture_output=True, text=True, timeout=300)
    (out / "rkzc_jsonl.jsonl").write_text(r.stdout)
    (out / "rkzc_jsonl.log").write_text(r.stderr)
    reference = [json.loads(l) for l in r.stdout.splitlines() if l.strip()]
    if r.returncode != 0 or not reference:
        print(f"m5_detections: rkzc-jsonl gave status {r.returncode} and {len(reference)} frames", file=sys.stderr)
        record([("result", f"not made: rkzc-jsonl gave status {r.returncode} and {len(reference)} frames")])
        return 2
    shutil.copyfile(args.video, COPY)
    os.chmod(COPY, 0o644)
    data = b""
    try:
        m5.CONFIG.write_text(m5.config_for(shipped, [str(COPY)]), encoding="utf-8")
        (out / "detect.toml").write_text(m5.CONFIG.read_text(encoding="utf-8"))
        m5.must(["systemctl", "start", m5.UNIT], timeout=90)
        # The socket opens before the source does: connect at once, so that the first frame is not missed.
        sock, until = None, time.monotonic() + 30
        while sock is None and time.monotonic() < until:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.connect(m5.SOCKET)
                sock = s
            except OSError:
                s.close()
                time.sleep(0.002)
        if sock is None:
            print("m5_detections: the service's socket did not open", file=sys.stderr)
            record([("result", "not made: the service's socket did not open")])
            return 2
        sock.settimeout(120)
        while True:  # until the file has ended and the program has closed its socket
            chunk = sock.recv(1 << 16)
            if not chunk:
                break
            data += chunk
        sock.close()
    finally:
        m5.out_of(["systemctl", "stop", m5.UNIT], timeout=90)
        m5.out_of(["systemctl", "reset-failed", m5.UNIT])
        COPY.unlink(missing_ok=True)
        m5.CONFIG.write_text(shipped, encoding="utf-8")
    (out / "lines.jsonl").write_bytes(data)
    text = data.decode("utf-8", "replace")
    frames, events, bad = mm.parse_lines(text if text.endswith("\n") else text + "\n")
    problems, n, detections = compare(reference, frames)
    problems += [f"line: {b}" for b in bad] + [f"fields: {p}" for p in mm.field_problems(frames)]
    passed = not problems and detections > 0
    rows = [("result", "passed" if passed else "failed"), ("video_sha256", m5.sha256(args.video)),
            ("frames_of_rkzc_jsonl", n), ("frame_lines_of_the_service", len(frames)),
            ("detections_compared", detections), ("events", " ".join(e["event"] for e in events) or "none"),
            ("problems", len(problems))] + [(f"problem_{i:02d}", p) for i, p in enumerate(problems[:20])]
    record(rows)
    print("".join(f"{k}: {v}\n" for k, v in rows), end="")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
