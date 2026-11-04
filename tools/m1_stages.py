#!/usr/bin/env python3
"""Where the latency goes (exploratory, not pre-registered): per stage of PROTOCOL §7.3's timeline,
from the frames.csv of the runs that count (tools/m1_report.py): M1's sweeps, then M1.1's (4K, A11; the
designs of §13, A12).

Stages of a processed frame, over the frames whose t4 falls in the window (as for §7.3's p99):
  Q1 wait  t1 - t0    decoded, waiting for a preprocessing thread
  prepare  t2a - t1   B: map, resize and convert on the CPU; C: RGA3; A: B's code on a software frame
  Q2 wait  t2b - t2a  prepared, waiting for an NPU worker
  bind     t2c - t2b
  run      t3 - t2c   rknn_run
  post     t4 - t3    post-processing
Per run, the median and the 99th percentile of each stage (numpy, linear); per point, the mean over
its valid runs. The stages' percentiles do not add up to the total's.

Usage, from the repository: python3 tools/m1_stages.py [--out FILE]   (Markdown)
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m1_report as rep  # noqa: E402
import stream_session as ss  # noqa: E402

STAGES = [("Q1 wait", "t1_ns", "t0_ns"), ("prepare", "t2a_ns", "t1_ns"), ("Q2 wait", "t2b_ns", "t2a_ns"),
          ("bind", "t2c_ns", "t2b_ns"), ("run", "t3_ns", "t2c_ns"), ("post", "t4_ns", "t3_ns"),
          ("total", "t4_ns", "t0_ns")]


def run_stages(run_dir):
    """{stage: (p50, p99) in ms} of one run, over the frames whose t4 is in the window."""
    frames = rep.window_frames(run_dir)
    out = {}
    for name, end, start in STAGES:
        v = np.array([(int(f[end]) - int(f[start])) / 1e6 for f in frames])
        out[name] = (float(np.percentile(v, 50)), float(np.percentile(v, 99))) if len(v) else (np.nan, np.nan)
    return out


def sweep_stages(root, sweep):
    """{channels: {variant: {stage: (mean p50, mean p99, n runs)}}} over the valid runs that count."""
    runs, _ = rep.counted_runs(rep.sessions_of(root, sweep), sweep)
    kind = ss.SWEEPS[sweep][3]
    per = {}
    for (ch, _, v), run in sorted(runs.items()):
        if run["status"] == "valid":
            per.setdefault(ch, {}).setdefault(v, []).append(
                run_stages(root / "results" / kind / run["session"] / run["name"]))
    return {ch: {v: {s: (float(np.mean([r[s][0] for r in rs])), float(np.mean([r[s][1] for r in rs])), len(rs))
                     for s, _, _ in STAGES} for v, rs in by_v.items()} for ch, by_v in per.items()}


def report(root):
    lines = ["# Where the latency goes (exploratory)", "",
             "Not pre-registered and not used for any verdict. Per stage of §7.3's timeline, the median and the "
             "99th percentile in ms, each the mean over the point's valid runs (n). The stages' percentiles do "
             "not add up to the total's.", ""]
    for sweep in ("main", "aux", "reference", "4k", *rep.A12_TITLES):
        data = sweep_stages(root, sweep)
        title = {"4k": "4k (M1.1, A11)"}.get(sweep, f"{sweep} (M1.1, A12)" if sweep in rep.A12_TITLES else sweep)
        lines += [f"## {title}", "",
                  "| Channels | Variant | n | " + " | ".join(f"{s} p50 / p99" for s, _, _ in STAGES) + " |",
                  "|---|---|---|" + "---|" * len(STAGES)]
        for ch in sorted(data):
            for v in sorted(data[ch]):
                st = data[ch][v]
                n = next(iter(st.values()))[2]
                lines.append(f"| {ch} | {v} | {n} | " + " | ".join(f"{p50:.1f} / {p99:.1f}" for p50, p99, _ in st.values())
                             + " |")
        lines.append("")
    return "\n".join(lines)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    text = report(Path(__file__).resolve().parent.parent)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
