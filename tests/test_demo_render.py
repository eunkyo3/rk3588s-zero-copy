#!/usr/bin/env python3
"""Tests for tools/demo_render.py's own logic: the assignment it reads, the detections file, the
mapping of a box from model input to a tile, where a box's label goes, and which detections a channel
shows for each frame. Needs numpy; runs without OpenCV (a stand-in module and a stand-in video).

Usage: python3 tests/test_demo_render.py
"""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
try:
    import cv2  # noqa: F401
except ImportError:
    sys.modules["cv2"] = types.ModuleType("cv2")
import demo_render as dr  # noqa: E402


class Render(unittest.TestCase):
    def test_the_first_8_channels_of_the_m1_assignment(self):
        a = dr.assignment()
        self.assertEqual(len(a), 8)
        self.assertEqual(a[0], ("ch1", "pexels_36330861.mp4"))
        self.assertEqual([v for _, v in a[:4]], [v for _, v in a[4:]])  # videos 1 to 4, twice

    def test_a_box_from_the_band_to_the_tile(self):
        band = [0, 140, 640, 360]  # 1920 x 1080 letterboxed into 640 x 640
        self.assertEqual(dr.to_tile([0, 0.9, 0, 140, 640, 500], band), (0, 0, dr.TILE_W, dr.TILE_H))
        self.assertEqual(dr.to_tile([0, 0.9, 320, 320, 480, 410], band), (320, 180, 480, 270))
        half = [160, 230, 320, 180]  # a band half the tile's size: the box scales by 2
        self.assertEqual(dr.to_tile([0, 0.9, 160, 230, 480, 410], half), (0, 0, dr.TILE_W, dr.TILE_H))
        self.assertEqual(dr.to_tile([0, 0.9, 240, 275, 320, 320], half), (160, 90, 320, 180))

    def test_a_box_label_goes_below_the_channel_label(self):
        channel = (100, 30)
        self.assertEqual(dr.label_top(50, 10, 8, channel), 42)  # under the channel label
        self.assertEqual(dr.label_top(150, 10, 8, channel), 12)  # beside it: on the box, inside the tile
        self.assertEqual(dr.label_top(50, 200, 8, channel), 200)  # below it: on the box

    def test_load_detections(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "v.jsonl"
            p.write_text(json.dumps({"header": {"stride": 6, "band": [0, 140, 640, 360]}}) + "\n"
                         + json.dumps({"frame": 0, "pts": 0, "detections": [[0, 0.5, 1, 2, 3, 4]]}) + "\n"
                         + json.dumps({"frame": 6, "pts": 6, "detections": []}) + "\n", encoding="utf-8")
            header, frames = dr.load_detections(p)
            self.assertEqual(header["stride"], 6)
            self.assertEqual(frames, {0: [[0, 0.5, 1, 2, 3, 4]], 6: []})


class FakeCapture:
    """A 10-frame video whose frames are their indices."""
    LENGTH = 10

    def __init__(self, path):
        self.n = 0

    def isOpened(self):
        return True

    def read(self):
        if self.n == self.LENGTH:
            return False, None
        self.n += 1
        return True, self.n - 1

    def release(self):
        pass


class ChannelFrames(unittest.TestCase):
    def test_start_hold_and_wrap(self):
        # Sampled every 3rd frame. Starting at frame 4: frames 4 and 5 show frame 3's boxes, 6 to 8
        # frame 6's, 9 frame 9's; then the video starts again at frame 0.
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "v.jsonl"
            rows = [json.dumps({"header": {"stride": 3, "band": [0, 140, 640, 360], "labels": ["a"]}})]
            rows += [json.dumps({"frame": i, "pts": i, "detections": [[0, 0.5, i, 0, 1, 1]]}) for i in (0, 3, 6, 9)]
            p.write_text("\n".join(rows) + "\n", encoding="utf-8")
            with mock.patch.object(dr.cv2, "VideoCapture", FakeCapture, create=True), \
                    mock.patch.object(dr, "draw_tile", lambda img, label, dets, labels, band: (img, dets[0][2])):
                c = dr.Channel("ch1", "v.mp4", t, t, start_frame=4)
                shown = [c.next() for _ in range(10)]
        self.assertEqual(shown, [(4, 3), (5, 3), (6, 6), (7, 6), (8, 6), (9, 9), (0, 0), (1, 0), (2, 0), (3, 3)])


if __name__ == "__main__":
    unittest.main()
