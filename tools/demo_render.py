#!/usr/bin/env python3
"""M1-7 demo, step 2: the 8-channel grid video, drawn from C's detections (tools/demo_detect).

Channels 1 to 8 take their videos from the M1 assignment (stream-server/assignment_m1.txt: videos 1 to
4, twice). Each tile shows a channel's video with the boxes of its latest sampled frame: demo_detect
ran C on 1 frame in 6 (5 fps), so a box stays up until the next inference, as on a live screen. Like
the stream server, a video starts again at its end. Channels 5 to 8 start --offset seconds into their
video, so that no two tiles show the same frames. The ninth tile says what the video is.

Rendered offline from the detections: the video says nothing about timing, which is the benchmark's
business (docs/notes/comparison.md).

Needs python3-opencv and numpy, and ffmpeg with libx264 (the board's Ubuntu packages).

Usage, on the board from the repository:
  python3 tools/demo_render.py --media DIR --detections DIR --out demo.mp4 [--seconds 20] [--offset 10]
    [--poster poster.jpg]
  --media       the prepared videos (/home/pi/media/bench)
  --detections  <video stem>.jsonl from tools/demo_detect, one per video
  --poster      also save one frame, from the middle of the video, as a JPEG
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

ASSIGNMENT = Path(__file__).resolve().parent.parent / "stream-server" / "assignment_m1.txt"
CHANNELS, FPS = 8, 30
TILE_W, TILE_H, COLS = 640, 360, 3  # 3 x 3 tiles: 1920 x 1080
SURFACE, INK, INK_2 = (251, 252, 252), (11, 11, 11), (78, 81, 82)  # BGR
# Fixed colours per class (BGR), from the categorical palette of the report's figures; others grey.
CLASS_COLORS = {"person": (214, 120, 42), "car": (52, 104, 235), "bus": (122, 175, 27), "truck": (0, 161, 237),
                "motorcycle": (164, 123, 232), "bicycle": (0, 131, 0), "traffic light": (214, 80, 98)}
OTHER = (150, 150, 150)
INFO = ["RK3588S zero-copy pipeline: detection demo",
        "",
        "Variant C: RGA3 letterbox into the NPU's",
        "input buffer, no CPU pixel copies (A6)",
        "YOLOv8n INT8 on the NPU",
        "",
        "8 channels, 1080p, detections at 5 fps each",
        "(1 frame in 6); boxes stay until the next",
        "",
        "Rendered offline from C's detections:",
        "not a timing measurement",
        "Channels 5-8 start {offset} s into their video",
        "Videos: Pexels and Pixabay, credits in README"]


def assignment(path=ASSIGNMENT):
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            ch, video = line.split()
            out.append((ch, video))
    return out[:CHANNELS]


def load_detections(path):
    """(header, {frame: [[cls, score, x1, y1, x2, y2], ...]}) of a demo_detect file."""
    with open(path, encoding="utf-8") as f:
        header = json.loads(f.readline())["header"]
        frames = {}
        for line in f:
            row = json.loads(line)
            frames[row["frame"]] = row["detections"]
    return header, frames


def to_tile(det, band):
    """A detection's box in tile pixels: model input -> frame (through the letterbox band) -> tile."""
    bx, by, bw, bh = band  # the band is the whole frame, scaled
    sx, sy = TILE_W / bw, TILE_H / bh
    _, _, x1, y1, x2, y2 = det
    return (round((x1 - bx) * sx), round((y1 - by) * sy), round((x2 - bx) * sx), round((y2 - by) * sy))


def draw_tile(img, label, dets, labels, band):
    tile = cv2.resize(img, (TILE_W, TILE_H), interpolation=cv2.INTER_AREA)
    for det in dets:
        name = labels[det[0]]
        color = CLASS_COLORS.get(name, OTHER)
        x1, y1, x2, y2 = to_tile(det, band)
        cv2.rectangle(tile, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)
        text = f"{name} {det[1]:.2f}"
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.38, 1)
        ty = max(y1, th + 4)
        cv2.rectangle(tile, (x1, ty - th - 4), (x1 + tw + 4, ty), color, -1)
        cv2.putText(tile, text, (x1 + 2, ty - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1, cv2.LINE_AA)
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    cv2.rectangle(tile, (0, 0), (tw + 12, th + 12), SURFACE, -1)
    cv2.putText(tile, label, (6, th + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, INK, 1, cv2.LINE_AA)
    return tile


def info_tile(offset):
    tile = np.full((TILE_H, TILE_W, 3), SURFACE, np.uint8)
    for i, line in enumerate(INFO):
        bold = i == 0
        cv2.putText(tile, line.format(offset=offset), (24, 40 + 24 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55 if bold else 0.5,
                    INK if bold else INK_2, 2 if bold else 1, cv2.LINE_AA)
    return tile


class Channel:
    def __init__(self, ch, video, media, detections, start_frame):
        self.label = f"{ch}  {Path(video).stem}"
        self.path = str(Path(media) / video)
        self.header, self.dets = load_detections(Path(detections) / f"{Path(video).stem}.jsonl")
        self.stride = int(self.header["stride"])
        self.open()
        for _ in range(start_frame):  # decoded, not seeked: the frame index must match demo_detect's
            self.read()

    def open(self):
        self.cap = cv2.VideoCapture(self.path)
        if not self.cap.isOpened():
            sys.exit(f"demo_render: cannot open {self.path}")
        self.index = 0

    def read(self):
        """The next frame and its index in the file; at the end the video starts again, as the stream
        server's does."""
        ok, img = self.cap.read()
        if not ok:
            self.cap.release()
            self.open()
            ok, img = self.cap.read()
            if not ok:
                sys.exit(f"demo_render: cannot read {self.path}")
        self.index += 1
        return self.index - 1, img

    def next(self):
        index, img = self.read()
        sampled = index - index % self.stride
        dets = self.dets.get(sampled)
        if dets is None:
            sys.exit(f"demo_render: no detections for frame {sampled} of {self.path}")
        return draw_tile(img, self.label, dets, self.header["labels"], self.header["band"])


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--media", required=True)
    ap.add_argument("--detections", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--offset", type=int, default=10)
    ap.add_argument("--poster")
    args = ap.parse_args(argv)
    channels = [Channel(ch, video, args.media, args.detections, (i // 4) * args.offset * FPS)
                for i, (ch, video) in enumerate(assignment())]
    info = info_tile(args.offset)
    rows = -(-(CHANNELS + 1) // COLS)
    enc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                            "-s", f"{TILE_W * COLS}x{TILE_H * rows}", "-r", str(FPS), "-i", "-", "-c:v", "libx264",
                            "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                            args.out], stdin=subprocess.PIPE)
    total = args.seconds * FPS
    for n in range(total):
        tiles = [c.next() for c in channels] + [info]
        grid = np.vstack([np.hstack(tiles[r * COLS:(r + 1) * COLS]) for r in range(rows)])
        enc.stdin.write(grid.tobytes())
        if args.poster and n == total // 2:
            cv2.imwrite(args.poster, grid, [cv2.IMWRITE_JPEG_QUALITY, 85])
    enc.stdin.close()
    if enc.wait() != 0:
        sys.exit("demo_render: ffmpeg failed")
    print(f"{total} frames, {args.seconds} s, in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
