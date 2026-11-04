#!/usr/bin/env python3
"""Tests for tools/m1_plots.py: every figure is written, from the points m1_report collects, and a
second run writes the same bytes (M1-7: a clean clone gives the same files).

Usage: python3 tests/test_m1_plots.py
"""
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import m1_plots as plots  # noqa: E402
from test_m1_report import fake_run, idle_file, session  # noqa: E402

H3_SMOKE = ROOT / "results" / "check" / "20261103T071611Z_h3_smoke"
FIGURES = ["cpu_main.svg", "latency_main.svg", "decoder_main.svg", "cpu_aux.svg", "latency_aux.svg",
           "decoder_aux.svg", "h3.svg"]


def fixture(root):
    """A main session with two points, an aux session with one, and an A session with one."""
    for kind, name, rows in (
            ("comparison", "20261104T000000Z_main", [("01_main_B_8ch_r1", "B", 8, 1, 1, "1", ""),
                                                     ("02_main_C_8ch_r1", "C", 8, 1, 1, "1", ""),
                                                     ("03_main_B_1ch_r1", "B", 1, 1, 1, "1", ""),
                                                     ("04_main_C_1ch_r1", "C", 1, 1, 1, "1", "")]),
            ("comparison", "20261104T010000Z_aux", [("01_aux_B_5ch_r1", "B", 5, 1, 1, "1", ""),
                                                    ("02_aux_C_5ch_r1", "C", 5, 1, 1, "1", "")]),
            ("reference", "20261104T020000Z_reference", [("01_reference_A_1ch_r1", "A", 1, 1, 1, "1", "")])):
        s = session(root / "results" / kind, name, rows)
        idle_file(s)
        for run_name, v, ch, *_ in rows:
            fake_run(s / run_name, {"A": 30.0, "B": 5.0 * ch, "C": 2.0 * ch}[v], [10.0 + ch] * 100)


class Figures(unittest.TestCase):
    def test_every_figure_and_the_same_bytes_twice(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            fixture(root)
            plots.write_figures(root, root / "a")
            plots.write_figures(root, root / "b")
            for name in FIGURES:
                a, b = (root / "a" / name).read_bytes(), (root / "b" / name).read_bytes()
                self.assertEqual(a, b, name)
                self.assertNotIn(b"<dc:date>", a)
                self.assertNotIn(b"Matplotlib v", a)
            self.assertIn(b"NPU = P_cap / 5 fps = 38.8 channels", (root / "a" / "cpu_main.svg").read_bytes())
            self.assertIn(b"A (reference)", (root / "a" / "cpu_main.svg").read_bytes())
            self.assertNotIn(b"A (reference)", (root / "a" / "cpu_aux.svg").read_bytes())

    def test_no_session(self):
        with tempfile.TemporaryDirectory() as t:
            plots.write_figures(Path(t), Path(t) / "out")
            self.assertEqual(sorted(p.name for p in (Path(t) / "out").iterdir()), sorted(FIGURES))

    def test_a_point_without_a_valid_run_breaks_the_line(self):
        points = {1: {"B": {"valid": {1: {"x": 1.0}, 2: {"x": 3.0}}}}, 2: {"B": {"valid": {}}},
                  4: {"B": {"valid": {1: {"x": 5.0}}}}}
        s = plots.series(points, "B", "x")
        self.assertEqual((s[0][:2], s[2]), ((1, 2.0), (4, 5.0, 0.0)))  # one run: no error bar
        self.assertTrue(math.isnan(s[1][1]))
        self.assertEqual(plots.series(points, "C", "x"), [])


@unittest.skipUnless(H3_SMOKE.exists(), "needs the H3 smoke session in results/check")
class H3Runs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(H3_SMOKE, self.root / "results" / "comparison" / "20261103T071611Z_h3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_runs_that_count(self):
        runs, problem = plots.h3_runs(self.root, limit=2, start=1)  # the smoke session's settings
        self.assertIsNone(problem)
        for v in ("B", "C"):
            self.assertEqual(sorted((ch, phase) for ch, phase, _ in runs[v]),
                             [(1, "h3_search"), (2, "h3_confirm"), (2, "h3_confirm"), (2, "h3_confirm"),
                              (2, "h3_search")])

    def test_records_that_do_not_replay_and_a_session_without_idle_baseline(self):
        runs, problem = plots.h3_runs(self.root)  # L = 32 and a start at 8: the smoke runs do not fit
        self.assertIsNotNone(problem)
        self.assertEqual(runs, {"B": [], "C": []})
        (self.root / "results" / "comparison" / "20261104T000000Z_h3").mkdir()  # stopped before its baseline
        self.assertEqual(plots.h3_runs(self.root, limit=2, start=1)[0]["B"][0][:2], (1, "h3_search"))
        plots.h3_figure(self.root, self.root / "h3.svg")
        self.assertIn(b"could not be replayed", (self.root / "h3.svg").read_bytes())


if __name__ == "__main__":
    unittest.main()
