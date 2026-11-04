#!/usr/bin/env python3
"""Tests for tools/demo_render.py's own logic: the assignment it reads, the detections file and the
mapping of a box from model input to a tile. Runs without OpenCV (a stand-in module).

Usage: python3 tests/test_demo_render.py
"""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

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

    def test_load_detections(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "v.jsonl"
            p.write_text(json.dumps({"header": {"stride": 6, "band": [0, 140, 640, 360]}}) + "\n"
                         + json.dumps({"frame": 0, "pts": 0, "detections": [[0, 0.5, 1, 2, 3, 4]]}) + "\n"
                         + json.dumps({"frame": 6, "pts": 6, "detections": []}) + "\n", encoding="utf-8")
            header, frames = dr.load_detections(p)
            self.assertEqual(header["stride"], 6)
            self.assertEqual(frames, {0: [[0, 0.5, 1, 2, 3, 4]], 6: []})


if __name__ == "__main__":
    unittest.main()
