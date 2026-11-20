#!/usr/bin/env python3
"""The Python module's example: detections as JSON lines on stdout, events on stderr.

    detect_jsonl.py --model FILE.rknn [--post auto|m1|neon|v10] [--every N] [--loop] [--seconds S]
                    [--retry-max MS] [--stall MS] SOURCE...

The same options and the same lines as the C++ example (lib/examples/detect_jsonl.cpp); the test compares
the two. It runs until every source has ended (files, without --loop), for S seconds, or until Ctrl-C.
"""
import argparse
import sys

import rkzc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--post")
    ap.add_argument("--every", type=int)
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--seconds", type=float)
    ap.add_argument("--retry-max", type=int)
    ap.add_argument("--stall", type=int)
    ap.add_argument("sources", nargs="+")
    args = ap.parse_args()

    out = sys.stdout

    def on_frame(f):
        boxes = ", ".join("[%d, %.4f, %.2f, %.2f, %.2f, %.2f]" % (d.cls, d.score, d.x, d.y, d.w, d.h)
                          for d in f.detections)
        out.write('{"channel": %d, "seq": %d, "pts_us": %d, "width": %d, "height": %d, "detections": [%s]}\n'
                  % (f.channel, f.seq, f.pts_us, f.width, f.height, boxes))

    def on_event(e):
        print(f"detect_jsonl.py: channel {e.channel}: {e.kind.name.lower().replace('_', ' ')}: {e.message}",
              file=sys.stderr)

    try:
        det = rkzc.Detector(args.sources, args.model, post=args.post, every_nth_frame=args.every,
                            loop_files=args.loop, reconnect_max_ms=args.retry_max, stall_timeout_ms=args.stall,
                            on_frame=on_frame, on_event=on_event)
    except rkzc.Error as e:
        print(f"detect_jsonl.py: {e}", file=sys.stderr)
        return 1
    with det:
        det.start()
        try:
            det.wait(args.seconds)
        except KeyboardInterrupt:
            pass
        det.stop()
        out.flush()
        for channel, s in enumerate(det.stats()):
            print(f"detect_jsonl.py: channel {channel}: decoded {s.decoded}, sampled {s.sampled}, "
                  f"inferred {s.inferred}, dropped {s.dropped}, errors {s.errors}, reconnects {s.reconnects}",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
