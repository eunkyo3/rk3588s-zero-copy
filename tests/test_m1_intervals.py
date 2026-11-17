#!/usr/bin/env python3
"""Tests for tools/m1_intervals.py: the pairs, where an interval lies, the spread, Student's t and the
repetitions for a half-width.

Usage: python3 tests/test_m1_intervals.py
"""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import m1_intervals as mi  # noqa: E402
import m2_report as mr  # noqa: E402


def arm(values, key="process_cpu_s"):
    return {r: {key: x} for r, x in enumerate(values, 1)}


class Pairs(unittest.TestCase):
    def test_relative_as_h1s_saving(self):
        p = mi.pairs(arm([40.0, 50.0]), arm([20.0, 30.0]), "process_cpu_s", True)
        self.assertEqual(list(p), [1, 2])
        self.assertAlmostEqual(p[1], 0.5)
        self.assertAlmostEqual(p[2], 0.4)

    def test_absolute(self):
        p = mi.pairs(arm([24.0, 30.0], "p99"), arm([25.5, 20.0], "p99"), "p99", False)
        self.assertAlmostEqual(p[1], -1.5)
        self.assertAlmostEqual(p[2], 10.0)

    def test_only_repetitions_both_arms_have(self):
        b, c = arm([40.0, 50.0, 60.0]), arm([20.0, 30.0, 30.0])
        del c[2]
        self.assertEqual(list(mi.pairs(b, c, "process_cpu_s", True)), [1, 3])

    def test_as_m2_paired_its_arms(self):
        c, cp = arm([20.0, 21.0, 19.0]), arm([19.0, 20.5, 18.0])  # tests/test_m2_report.py's
        self.assertAlmostEqual(mi.pairs(c, cp, "process_cpu_s", True)[2], 0.5 / 21.0)


class Side(unittest.TestCase):
    def test_sides(self):
        self.assertEqual(mi.side(0.1, 0.2), "above 0")
        self.assertEqual(mi.side(-0.2, -0.1), "below 0")
        self.assertEqual(mi.side(-0.1, 0.2), "spans 0")
        self.assertEqual(mi.side(0.0, 0.2), "spans 0")
        self.assertEqual(mi.side(math.nan, math.nan), "–")


class Spread(unittest.TestCase):
    def test_from_the_lowest(self):
        self.assertAlmostEqual(mi.spread([18.56, 19.49, 18.74]), 0.93 / 18.56)
        self.assertEqual(mi.spread([5.0]), 0.0)


class StudentT(unittest.TestCase):
    def test_m2s_constants(self):
        for df, t in mr.T_975.items():
            self.assertAlmostEqual(mi.t_975(df), t, places=9)

    def test_more_degrees_of_freedom(self):
        self.assertAlmostEqual(mi.t_975(3), 3.182446305284263, places=9)
        self.assertAlmostEqual(mi.t_975(10), 2.228138851986274, places=9)
        self.assertAlmostEqual(mi.t_975(29), 2.045229642132703, places=9)
        self.assertAlmostEqual(mi.t_975(99), 1.9842169515086827, places=9)

    def test_towards_the_normal(self):
        self.assertGreater(mi.t_975(200), 1.959963984540054)
        self.assertLess(mi.t_975(200), mi.t_975(100))


class Repetitions(unittest.TestCase):
    def test_cv_equal_to_the_half_width(self):
        # n = 6: 2.571 × 0.1 / √6 = 0.105 > 0.1; n = 7: 2.447 × 0.1 / √7 = 0.0925
        self.assertEqual(mi.repetitions(0.10, 0.10), 7)

    def test_no_variation(self):
        self.assertEqual(mi.repetitions(0.0, 0.02), 2)

    def test_three_runs_suffice(self):
        self.assertEqual(mi.repetitions(0.01, 0.05), 3)  # 4.303 × 0.01 / √3 = 0.025; n = 2 gives 0.090

    def test_beyond_most(self):
        self.assertIsNone(mi.repetitions(0.5, 0.01, most=50))

    def test_the_bound_holds_and_is_the_fewest(self):
        for cv in (0.03, 0.12, 0.24):
            for h in mi.HALF_WIDTHS:
                n = mi.repetitions(cv, h)
                if n is None:
                    continue
                self.assertLessEqual(mi.t_975(n - 1) * cv / math.sqrt(n), h)
                if n > 2:
                    self.assertGreater(mi.t_975(n - 2) * cv / math.sqrt(n - 1), h)


if __name__ == "__main__":
    unittest.main()
