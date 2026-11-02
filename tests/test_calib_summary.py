#!/usr/bin/env python3
"""Tests for tools/calib_summary.py.

Usage: python3 tests/test_calib_summary.py
"""
import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import calib_summary as cs  # noqa: E402

HEADER = {"start_time_utc": "2026-11-02T06:00:00Z", "ntp_synchronized": "yes", "prereg_v1_commit": "p",
          "freeze_m1_commit": "none", "run_kind": "calibration", "model_sha256": "m", "code_commit": "c",
          "amendment": "A6", "warmup_s": "20", "duration_s": "120", "tensors": "t.u8"}


class Session:
    """A fake tools/calib_npu.sh session: by default 3 valid runs of each measurement."""

    def __init__(self, d):
        self.d = d
        self.rows = []

    def add(self, rep, measurement, cores, completed, attempt=1, exit_code=0, valid="1", start=None, **header):
        name = f"{measurement}_{cores}c_r{rep}" + ("_repeat" if attempt == 2 else "")
        h = dict(HEADER, measurement=measurement, cores=str(cores), repetition=str(rep), **header)
        if start:
            h["start_time_utc"] = start
        (self.d / name).mkdir()
        text = "".join(f"# {k}: {v}\n" for k, v in h.items())
        text += f"key,value\ncompleted_in_window,{completed}\nvalid,{valid}\n"
        (self.d / name / "summary.csv").write_text(text, encoding="utf-8")
        self.rows.append((rep, measurement, cores, attempt, name, exit_code))

    def drop(self, name):
        self.rows = [r for r in self.rows if r[4] != name]
        shutil.rmtree(self.d / name)

    def fill(self, completed=None):
        for rep in (1, 2, 3):
            for measurement, cores in cs.MEASUREMENTS:
                c = (completed or {}).get((measurement, cores, rep), 1200 * cores)
                self.add(rep, measurement, cores, c)

    def run(self):
        lines = ["repetition\tmeasurement\tcores\tattempt\tdir\texit\tgate_soc_mc"]
        lines += ["\t".join(map(str, r)) + "\t40000" for r in self.rows]
        (self.d / "runs.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cs.main(["calib_summary.py", str(self.d)])
        values = {}
        if (self.d / "calibration.csv").exists():
            for line in (self.d / "calibration.csv").read_text(encoding="utf-8").splitlines():
                if not line.startswith("#"):
                    k, _, v = line.partition(",")
                    values[k] = v
        return code, out.getvalue() + err.getvalue(), values


class Derived(unittest.TestCase):
    def test_h2_point_at_its_boundary(self):
        # 0.8 × P_cap / 30 = 5 exactly at P_cap = 187.5 = 22500 / 120; one inference less gives 4.
        self.assertEqual(cs.derived(Fraction(22500, 120))["h2_aux_point"], 5)
        self.assertEqual(cs.derived(Fraction(22499, 120))["h2_aux_point"], 4)

    def test_main_boundary(self):
        # 0.8 × P_cap / 5 = 30 at P_cap = 187.5.
        self.assertEqual(cs.derived(Fraction(22500, 120))["unsaturated_main_max_channels_by_p_cap"], 30)
        self.assertEqual(cs.derived(Fraction(22499, 120))["unsaturated_main_max_channels_by_p_cap"], 29)

    def test_no_auxiliary_point(self):
        self.assertEqual(cs.derived(Fraction(37, 1))["h2_aux_point"], 0)


class Summarize(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Session(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_median_and_derived(self):
        self.s.fill({("p_cap", 3, 1): 23000, ("p_cap", 3, 2): 22400, ("p_cap", 3, 3): 22600})
        code, out, v = self.s.run()
        self.assertEqual(code, 0, out)
        self.assertEqual(v["p_cap_inf_per_s"], f"{22600 / 120:.4f}")
        self.assertEqual(v["h2_aux_point"], "5")
        self.assertEqual(v["npu_raw_2c_inf_per_s"], "20.0000")

    def test_repeat_replaces_an_invalid_run(self):
        self.s.fill()
        # Repetition 2 of P_cap: the first attempt was invalid; the repeat counts.
        self.s.drop("p_cap_3c_r2")
        self.s.add(2, "p_cap", 3, 99999, valid="0", exit_code=1)
        self.s.add(2, "p_cap", 3, 3600, attempt=2)
        code, out, v = self.s.run()
        self.assertEqual(code, 0, out)
        self.assertEqual(v["p_cap_r2"], "30.0000")

    def test_invalid_twice_has_no_run(self):
        self.s.fill()
        self.s.drop("npu_raw_1c_r3")
        self.s.add(3, "npu_raw", 1, 1, valid="0", exit_code=1)
        self.s.add(3, "npu_raw", 1, 1, attempt=2, valid="0", exit_code=1)
        code, out, v = self.s.run()
        self.assertEqual(code, 1, out)
        self.assertEqual(v["npu_raw_1c_r3"], "none")
        self.assertEqual(v["npu_raw_1c_inf_per_s"], "10.0000")  # the median of the two valid runs

    def test_session_start_is_the_earliest_run(self):
        self.s.fill()
        self.s.drop("npu_raw_3c_r1")
        self.s.add(1, "npu_raw", 3, 3600, start="2026-11-02T05:00:00Z")
        code, out, _ = self.s.run()
        self.assertEqual(code, 0, out)
        self.assertIn("# start_time_utc: 2026-11-02T05:00:00Z", out)

    def test_mixed_headers_are_an_input_error(self):
        self.s.fill()
        self.s.drop("npu_raw_2c_r2")
        self.s.add(2, "npu_raw", 2, 2400, code_commit="other")
        code, out, _ = self.s.run()
        self.assertEqual(code, 2, out)
        self.assertIn("differs", out)


if __name__ == "__main__":
    unittest.main()
