#!/usr/bin/env python3
"""Tests for tools/cpu_breakdown.py: the thread groups, one run's CPU, a session's saturation labels, the ratios
and spreads, the published values written at their digits, and the published values the committed sessions
give back.

Usage: python3 tests/test_cpu_breakdown.py
"""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import cpu_breakdown as cb  # noqa: E402

SESSIONS = [ROOT / "results" / "comparison" / "20261103T073439Z_main",
            ROOT / "results" / "m2" / "20261105T074458Z_m2_aux",
            ROOT / "results" / "m4" / "20261118T053535Z_m4_aux"]


def thread(comm, s):
    return {"tid": "1", "comm": comm, "affinity": "4-7", "cpu_s_in_window": str(s)}


class Groups(unittest.TestCase):
    def test_the_names_in_the_runs(self):
        for comm, g in (("npu-0", "npu"), ("dec-8", "dec"), ("mpp_dec_parser", "dec"), ("mpp_dec_hal", "dec"),
                        ("pre-1", "pre"), ("metrics", "rest"), ("bench_main", "rest"), ("npu", "rest")):
            self.assertEqual(cb.group(comm), g, comm)

    def test_by_group_sums_and_has_every_group(self):
        g = cb.by_group([thread("dec-1", 0.9), thread("mpp_dec_parser", 0.4), thread("npu-0", 2.25)])
        self.assertEqual(set(g), {"dec", "npu", "pre", "rest"})
        self.assertAlmostEqual(g["dec"], 1.3)
        self.assertEqual((g["npu"], g["pre"], g["rest"]), (2.25, 0.0, 0.0))


class OneRun(unittest.TestCase):
    def test_remainder_and_commit(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / "summary.csv").write_text("# warmup_s: 20\nkey,value\nprocess_cpu_s,3.500\n", encoding="utf-8")
            (d / "threads.csv").write_text("# code_commit: abc\ntid,comm,affinity,cpu_s_in_window\n"
                                           '1,npu-0,"4-7",2.000\n2,dec-1,"4-7",1.000\n3,metrics,"0",0.100\n',
                                           encoding="utf-8")
            (d / "env.json").write_text(json.dumps({"header_code_commit": "88206ab9"}), encoding="utf-8")
            m = cb.run_cpu(d)
        self.assertAlmostEqual(m["threads"], 3.1)
        self.assertAlmostEqual(m["remainder"], 0.4)
        self.assertEqual((m["npu"], m["dec"], m["pre"], m["rest"], m["commit"]), (2.0, 1.0, 0.0, 0.1, "88206ab9"))

    def test_saturation_labels(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            self.assertEqual(cb.saturated(d), [])  # no saturation.txt: M1's, M1.1's and M2's sessions
            (d / "saturation.txt").write_text("calib_npu_session: results/m4/x\npoint: aux\nlabel_C8: unsaturated\n"
                                              "label_C10: saturated\n", encoding="utf-8")
            self.assertEqual(cb.saturated(d), ["C10"])


class Numbers(unittest.TestCase):
    def test_summary_largest_ratio_spread(self):
        runs = {r: dict(process=p, dec=d, npu=1.0, pre=0.5, rest=0.1, threads=0.0, remainder=0.0)
                for r, p, d in ((1, 18.0, 11.0), (2, 19.0, 12.0))}
        s = cb.summary(runs)
        self.assertEqual(s["process"][:2], (2, 18.5))
        self.assertEqual(cb.largest(s), "dec")
        self.assertIsNone(cb.largest(cb.summary({})))
        self.assertAlmostEqual(cb.ratio(17.6, 18.74), 0.93917, places=5)
        self.assertTrue(math.isnan(cb.ratio(17.6, math.nan)))
        self.assertTrue(math.isnan(cb.ratio(17.6, 0.0)))
        self.assertAlmostEqual(cb.spread([18.56, 19.49, 18.74]), 0.93 / 18.56)
        self.assertTrue(math.isnan(cb.spread([18.56])))

    def test_written_at_the_published_digits(self):
        self.assertEqual(cb.as_published("6.83 ± 0.08", (3, 6.8333, 0.0808)), "6.83 ± 0.08")
        self.assertEqual(cb.as_published("0.45", (3, 0.4467, 0.0115)), "0.45")
        self.assertEqual(cb.as_published("18.737 ± 0.170", (3, 18.73667, 0.17010)), "18.737 ± 0.170")
        self.assertNotEqual(cb.as_published("11.33 ± 0.15", (3, 11.3366, 0.15)), "11.33 ± 0.15")


@unittest.skipUnless(all(d.exists() for d in SESSIONS), "needs the committed sessions in results/")
class Published(unittest.TestCase):
    def test_every_published_value_is_given_back(self):
        c = cb.collect(ROOT)
        self.assertEqual([x for x in cb.checks(c) if x[4] != x[5]], [])
        self.assertEqual(len(cb.checks(c)), sum(len(v) for v in cb.PUBLISHED.values()))
        self.assertEqual({k: d["commits"] for k, d in c.items() if k[1] == "main"},
                         {("M1", "main"): ["86587a29975c28c1c49f202ba8f6023ed4074861"],
                          ("M1.1", "main"): ["cdb309e4bbe9bf17646526a722e0a1fccb5e9d56"],
                          ("M2", "main"): ["88206ab978e318eaaf1db3d4a5448b580a84f8ac"],
                          ("M4", "main"): ["de78dbd1ffe3046e807e6c17ddc9fa48accd6bff"]})
        self.assertEqual({k: d["saturated"] for k, d in c.items() if d["saturated"]}, {("M4", "aux"): ["C10"]})

    def test_m4_has_its_step_and_stays_out_of_the_c_lines(self):
        text = cb.report(cb.collect(ROOT))
        self.assertIn("| M4 | C8 → C10 | +0.27 |", text)
        self.assertIn("| M4 | C8 → C10 | +1.79 |", text)
        self.assertIn("C's process CPU in the three sessions (not one scale): M1 18.56 s, M1.1 19.49 s, M2 18.74 s;",
                      text)
        self.assertIn("compares unequal loads: M4 C10.", text)

    def test_the_figure_twice_gives_the_same_bytes(self):
        c = cb.collect(ROOT)
        with tempfile.TemporaryDirectory() as t:
            a, b = Path(t) / "a.svg", Path(t) / "b.svg"
            cb.figure(c, a)
            cb.figure(c, b)
            self.assertEqual(a.read_bytes(), b.read_bytes())
            self.assertNotIn(b"<dc:date>", a.read_bytes())


if __name__ == "__main__":
    unittest.main()
