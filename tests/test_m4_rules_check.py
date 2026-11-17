#!/usr/bin/env python3
"""Tests for tools/m4_rules_check.py's verdict on one image (prereg/M4.md §2.3, §8 step 3): what is equal,
what differs, and the one exception, a difference caused by an IoU within 1e-6 of 0.7.

Usage: python3 tests/test_m4_rules_check.py
"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).resolve().parent.parent / "tools"))
import m4_rules as mr  # noqa: E402
import m4_rules_check as rc  # noqa: E402


def result(classes, tied=False, candidates=10):
    n = len(classes)
    return mr.Result(np.array(classes), np.full(n, 0.5, np.float32), np.tile(np.float32([1, 2, 3, 4]), (n, 1)),
                     candidates, tied)


def rows(classes):
    return [["1", str(k), str(c), "0.5", "1", "2", "3", "4"] for k, c in enumerate(classes)]


class FakeRule:
    """A YOLOv8n rule whose list depends on the threshold: at 0.7 it gives `at`, and below or above it
    `below` or `above`; it reports `near` IoUs."""

    def __init__(self, at, below=None, above=None, near=()):
        self.at, self.below, self.above, self.near = at, below or at, above or at, list(near)

    def __call__(self, outputs, iou_thresh=mr.NMS_IOU, near=None):
        if near is not None:
            near.extend(self.near)
        if iou_thresh < mr.NMS_IOU:
            return self.below
        if iou_thresh > mr.NMS_IOU:
            return self.above
        return self.at


class Verdict(unittest.TestCase):
    def judge(self, rule, cpp, candidates=10, tied=False):
        return rc.judge(rule, None, rows(cpp), candidates, tied, nms=True)[:2]

    def test_equal(self):
        self.assertEqual(self.judge(FakeRule(result([3, 4])), [3, 4]), ("equal", ""))

    def test_a_difference_without_a_near_iou_differs(self):
        self.assertEqual(self.judge(FakeRule(result([3, 4])), [3, 5])[0], "differ")

    def test_a_difference_a_moved_threshold_reproduces_is_excepted(self):
        rule = FakeRule(result([3, 4]), below=result([3]), near=[(1, 0.7000004)])
        verdict, what = self.judge(rule, [3])
        self.assertEqual(verdict, "excepted")
        self.assertIn("reproduced at IoU 0.69", what)

    def test_a_near_iou_elsewhere_does_not_excuse_another_difference(self):
        rule = FakeRule(result([3, 4]), below=result([3]), near=[(1, 0.7000004)])
        self.assertEqual(self.judge(rule, [3, 5])[0], "differ")  # rank 1's class: no threshold gives it

    def test_the_candidates_count_is_never_excepted(self):
        rule = FakeRule(result([3, 4]), below=result([3, 4]), near=[(1, 0.7000004)])
        self.assertEqual(self.judge(rule, [3, 4], candidates=11), ("differ", "the candidates' count"))

    def test_the_tie(self):
        self.assertEqual(self.judge(FakeRule(result([3, 4])), [3, 4], tied=True), ("differ", "the tie"))
        rule = FakeRule(result([3, 4]), above=result([3, 4], tied=True), near=[(5, 0.6999996)])
        self.assertEqual(self.judge(rule, [3, 4], tied=True)[0], "excepted")

    def test_tolerances(self):
        rule = FakeRule(result([3]))
        near_box = [["1", "0", "3", "0.5", "1.0009", "2", "3", "4"]]
        self.assertEqual(rc.judge(rule, None, near_box, 10, False, nms=True)[0], "equal")
        far_box = [["1", "0", "3", "0.5", "1.002", "2", "3", "4"]]
        self.assertEqual(rc.judge(rule, None, far_box, 10, False, nms=True)[0], "differ")
        far_score = [["1", "0", "3", "0.500002", "1", "2", "3", "4"]]
        self.assertEqual(rc.judge(rule, None, far_score, 10, False, nms=True)[0], "differ")


if __name__ == "__main__":
    unittest.main()
