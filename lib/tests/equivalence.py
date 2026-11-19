#!/usr/bin/env python3
"""Compare the library's detections with the benchmark's own tool on the same file.

    equivalence.py DEMO.jsonl LIB.jsonl

DEMO.jsonl comes from the registered tools/demo_detect (variant C's prepare, rknn_run and M1's
post-processing, one frame at a time), LIB.jsonl from rkzc-jsonl --post m1 on the same video, model and
stride. demo_detect gives boxes in the model input's coordinates with its letterbox band; the library
gives them in the source frame. For every frame of DEMO the library must have the same detections: the
same classes in the same order, the same scores, and boxes that agree after mapping through the band.

Exit status 0 if they agree and there was something to compare, 1 otherwise.
"""
import json
import sys

SCORE_TOL = 1e-4  # both files print scores with 4 decimals
BOX_TOL = 0.05    # pixels of the source frame; both print 2 decimals, and the band scales by 3 at 1080p


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def compare(demo_lines, lib_lines):
    """Returns (problems, frames compared, detections compared)."""
    header = demo_lines[0]["header"]
    bx, by, bw, bh = header["band"]
    lib = {}
    for line in lib_lines:
        if line["channel"] == 0:
            lib.setdefault(line["seq"], line)  # a looped file would repeat; the first pass counts
    problems, frames, detections = [], 0, 0
    for d in demo_lines[1:]:
        frames += 1
        got = lib.get(d["frame"])
        if got is None:
            problems.append(f"frame {d['frame']}: missing from the library's output")
            continue
        sx, sy = got["width"] / bw, got["height"] / bh
        want = []
        for cls, score, x1, y1, x2, y2 in d["detections"]:
            clamp_x = lambda v: min(max((v - bx) * sx, 0.0), got["width"])
            clamp_y = lambda v: min(max((v - by) * sy, 0.0), got["height"])
            want.append((cls, score, clamp_x(x1), clamp_y(y1), clamp_x(x2) - clamp_x(x1), clamp_y(y2) - clamp_y(y1)))
        if len(want) != len(got["detections"]):
            problems.append(f"frame {d['frame']}: {len(want)} detections against {len(got['detections'])}")
            continue
        for i, (w, g) in enumerate(zip(want, got["detections"])):
            detections += 1
            if w[0] != g[0] or abs(w[1] - g[1]) > SCORE_TOL:
                problems.append(f"frame {d['frame']} detection {i}: class/score {w[:2]} against {g[:2]}")
            # The demo's 2-decimal rounding is scaled by sx, sy with the box.
            elif max(abs(a - b) for a, b in zip(w[2:], g[2:])) > BOX_TOL:
                problems.append(f"frame {d['frame']} detection {i}: box {w[2:]} against {g[2:]}")
    return problems, frames, detections


def main(argv):
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    problems, frames, detections = compare(load(argv[1]), load(argv[2]))
    for p in problems[:20]:
        print("FAIL", p)
    print(f"{frames} frames, {detections} detections compared, {len(problems)} problems")
    if frames == 0 or detections == 0:
        print("FAIL nothing to compare: the video needs objects the model detects")
        return 1
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
