#!/usr/bin/env python3
"""Tests for tools/m1_plots.py: every figure is written, from the points m1_report collects, and a
second run writes the same bytes (M1-7: a clean clone gives the same files).

Usage: python3 tests/test_m1_plots.py
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import m1_plots as plots  # noqa: E402
from test_m1_report import fake_run, idle_file, session  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
