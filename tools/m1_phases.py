#!/usr/bin/env python3
"""When the channels' frames reach the pipeline (exploratory, after the fact: M1.1, not pre-registered):
per run that counts, how close together the channels' arrival phases are, next to the run's latency.

A channel infers 5 frames a second in the main condition (1 in 6) and 30 in the auxiliary, so its
inferred frames come with a period of 200 or 33.3 ms. Its phase is the circular mean of their t0 modulo
that period, over the frames whose t4 falls in the window (as for §7.3's p99). The spread of a run is
the shortest arc of that period that holds every channel's phase: near 0, the channels' frames come
together, in bursts. The phases are set when a run's channels start and the protocol does not control
them. The drift of a run is the largest change of a channel's phase, from the first sixth of its frames
in time to any later sixth: near 0, the phases held for the whole window.

Usage, from the repository: python3 tools/m1_phases.py [--out FILE]   (Markdown)
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m1_report as rep  # noqa: E402
import stream_session as ss  # noqa: E402

SWEEPS = {  # every sweep with a point of more than one channel, and where it comes from
    "main": "M1", "aux": "M1", "reference": "M1", "4k": "M1.1, A11", **{s: "M1.1, A12" for s in rep.A12_TITLES}}


def phase(t0_ns, period_ns):
    """The circular mean of t0_ns modulo period_ns, in ns, in [0, period_ns)."""
    a = 2 * np.pi * (np.asarray(t0_ns, dtype=np.float64) % period_ns) / period_ns
    return float(np.arctan2(np.sin(a).mean(), np.cos(a).mean()) % (2 * np.pi) / (2 * np.pi) * period_ns) % period_ns


def drift(t0_ns, period_ns):
    """The largest change of the phase of t0_ns from the first sixth, in time, to any later sixth, in ns."""
    sixths = [s for s in np.array_split(np.sort(np.asarray(t0_ns, dtype=np.float64)), 6) if len(s)]
    first = phase(sixths[0], period_ns)
    d = [abs(phase(s, period_ns) - first) for s in sixths[1:]]
    return max((min(x, period_ns - x) for x in d), default=0.0)


def spread(phases, period):
    """The shortest arc of a circle of length period that holds every phase."""
    p = sorted(phases)
    gaps = [b - a for a, b in zip(p, p[1:])] + [p[0] + period - p[-1]]
    return period - max(gaps)


def run_phases(run_dir, fps, channels):
    """(spread, drift, latency p50, p99, Q2 wait p50), in ms, of one run of channels channels, over the
    frames in its window. Every channel must have frames there."""
    frames = rep.window_frames(run_dir)
    period_ns = 1e9 / fps
    t0 = {}
    for f in frames:
        t0.setdefault(f["channel"], []).append(int(f["t0_ns"]))
    if len(t0) != channels:
        raise ValueError(f"{run_dir}: {len(t0)} of {channels} channels have frames in the window")
    total = [(int(f["t4_ns"]) - int(f["t0_ns"])) / 1e6 for f in frames]
    q2 = [(int(f["t2b_ns"]) - int(f["t2a_ns"])) / 1e6 for f in frames]
    return (spread([phase(v, period_ns) for v in t0.values()], period_ns) / 1e6,
            max(drift(v, period_ns) for v in t0.values()) / 1e6,
            float(np.percentile(total, 50)), float(np.percentile(total, 99)), float(np.percentile(q2, 50)))


def report(root):
    lines = ["# When the channels' frames arrive (exploratory)", "",
             "After the fact (M1.1), not pre-registered and not used for any verdict. Per valid run that counts, "
             "with more than one channel: the spread of the channels' arrival phases, the shortest arc of the "
             "frame period (200 ms in the main condition, 33.3 ms in the auxiliary) that holds every channel's "
             "phase, next to the run's latency and its median wait for an NPU worker (Q2), in ms. A small spread "
             "means the channels' frames come together. The drift is the largest change of a channel's phase from "
             "the first sixth of its frames to any later sixth. Method: `tools/m1_phases.py`.", ""]
    for sweep, source in SWEEPS.items():
        _, condition, _, kind = ss.SWEEPS[sweep]
        runs, _ = rep.counted_runs(rep.sessions_of(root, sweep), sweep)
        rows = sorted(((ch, v, r, run) for (ch, r, v), run in runs.items() if ch > 1 and run["status"] == "valid"),
                      key=lambda x: x[:3])
        lines += [f"## {sweep}" if source == "M1" else f"## {sweep} ({source})", ""]
        if not rows:
            lines += ["No run yet.", ""]
            continue
        lines += ["| Channels | Variant | Repetition | Spread | Drift | p50 | p99 | Q2 wait p50 |",
                  "|---|---|---|---|---|---|---|---|"]
        for ch, v, r, run in rows:
            s, d, p50, p99, q2 = run_phases(root / "results" / kind / run["session"] / run["name"],
                                            ss.FPS[condition], ch)
            lines.append(f"| {ch} | {v} | {r} | {s:.1f} | {d:.1f} | {p50:.1f} | {p99:.1f} | {q2:.1f} |")
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
