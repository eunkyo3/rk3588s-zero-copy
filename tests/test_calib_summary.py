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
          "freeze_m1_commit": "none", "run_kind": "calibration", "model_sha256": "m", "decoder": "none",
          "variant": "n/a", "channels": "n/a", "code_commit": "c", "amendment": "A6", "warmup_s": "20",
          "duration_s": "120", "tensors": "t.u8", "tensor_count": "1000", "tensor_heap": "system-uncached-dma32"}
CLOCKS = "policy0_khz: 1800000\npolicy4_khz: 2304000\npolicy6_khz: 2352000\ndmc_hz: 2112000000\nnpu_hz: 1000000000\n"


class Session:
    """A fake tools/calib_npu.sh session."""

    def __init__(self, d, idle_s=600):
        self.d = d
        self.rows = []
        (d / "perf_mode.txt").write_text(CLOCKS, encoding="utf-8")
        (d / "baseline.txt").write_text(f"idle_s: {idle_s}\nsoc_thermal_baseline_mc: 37000\n", encoding="utf-8")
        (d / "tensors.sha256").write_text("051d  t.u8\n", encoding="utf-8")

    def add(self, rep, measurement, cores, completed, attempt=1, exit_code=0, valid="1", trip=0, **header):
        name = f"{measurement}_{cores}c_r{rep}" + ("_repeat" if attempt == 2 else "")
        h = dict(HEADER, measurement=measurement, cores=str(cores), repetition=str(rep))
        h.update(header)
        (self.d / name).mkdir()
        text = "".join(f"# {k}: {v}\n" for k, v in h.items())
        text += (f"key,value\ncompleted_in_window,{completed}\nsamples_at_or_above_trip0,{trip}\n"
                 f"clock_samples_below_fixed,{0 if valid == '1' else 3}\nvalid,{valid}\n")
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
        lines = ["repetition\tmeasurement\tcores\tattempt\tdir\texit\tgate_soc_mc\tstart_utc\tend_utc"]
        lines += ["\t".join(map(str, r)) + "\t40000\ts\te" for r in self.rows]
        (self.d / "runs.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cs.main(["calib_summary.py", str(self.d)])
        values, header = {}, {}
        if (self.d / "calibration.csv").exists():
            for line in (self.d / "calibration.csv").read_text(encoding="utf-8").splitlines():
                if line.startswith("# "):
                    k, _, v = line[2:].partition(": ")
                    header[k] = v
                else:
                    k, _, v = line.partition(",")
                    values[k] = v
        return code, out.getvalue() + err.getvalue(), values, header


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

    def replace(self, name, *runs):
        """Drops run `name` and adds `runs`, each the arguments of Session.add."""
        self.s.drop(name)
        for args, kwargs in runs:
            self.s.add(*args, **kwargs)

    def test_median_derived_and_records(self):
        self.s.fill({("p_cap", 3, 1): 23000, ("p_cap", 3, 2): 22400, ("p_cap", 3, 3): 22600})
        code, out, v, h = self.s.run()
        self.assertEqual(code, 0, out)
        self.assertEqual(v["p_cap_inf_per_s"], f"{22600 / 120:.4f}")
        self.assertEqual(v["p_cap_r1_completed"], "23000")
        self.assertEqual(v["h2_aux_point"], "5")
        self.assertEqual(v["npu_raw_2c_inf_per_s"], "20.0000")
        self.assertEqual(v["fixed_npu_hz"], "1000000000")
        self.assertEqual(v["tensors_sha256"], "051d")
        self.assertEqual((v["complete"], v["protocol_settings"]), ("1", "yes"))
        for k in ["decoder", "variant", "channels", "repetition", "model_sha256", "code_commit", "amendment"]:
            self.assertIn(k, h)  # PROTOCOL §1

    def test_repeat_replaces_an_invalid_run(self):
        self.s.fill()
        self.replace("p_cap_3c_r2", ((2, "p_cap", 3, 99999), {"valid": "0", "exit_code": 1}),
                     ((2, "p_cap", 3, 3600), {"attempt": 2}))
        code, out, v, _ = self.s.run()
        self.assertEqual(code, 0, out)
        self.assertEqual(v["p_cap_r2"], "30.0000")

    def test_invalid_twice_is_complete_without_a_value(self):
        self.s.fill()
        self.replace("npu_raw_1c_r3", ((3, "npu_raw", 1, 1), {"valid": "0", "exit_code": 1}),
                     ((3, "npu_raw", 1, 1), {"attempt": 2, "valid": "0", "exit_code": 1}))
        code, out, v, _ = self.s.run()
        self.assertEqual(code, 0, out)  # §9.3: complete with an invalid ×2 record
        self.assertEqual((v["npu_raw_1c_r3"], v["npu_raw_1c_valid_runs"]), ("invalid_x2", "2"))
        self.assertEqual(v["npu_raw_1c_inf_per_s"], "10.0000")  # the median of the two valid runs

    def test_stopped_session_is_incomplete(self):
        self.s.fill()
        self.s.drop("npu_raw_3c_r3")
        self.s.drop("p_cap_3c_r3")
        self.s.add(3, "p_cap", 3, 1, valid="0", exit_code=1)  # invalid, and no repeat yet
        code, out, v, _ = self.s.run()
        self.assertEqual(code, 1, out)
        self.assertEqual((v["npu_raw_3c_r3"], v["p_cap_r3"], v["complete"]), ("missing", "missing", "0"))

    def test_settings_other_than_the_protocol(self):
        self.s = Session(self.s.d, idle_s=60)
        self.s.fill()
        code, out, v, _ = self.s.run()
        self.assertEqual(code, 1, out)
        self.assertEqual(v["protocol_settings"], "no: idle_s 60 (protocol 600)")

    def test_session_start_is_the_earliest_run(self):
        self.s.fill()
        self.replace("npu_raw_3c_r1", ((1, "npu_raw", 3, 3600), {"start_time_utc": "2026-11-02T05:00:00Z"}))
        code, out, _, h = self.s.run()
        self.assertEqual(code, 0, out)
        self.assertEqual(h["start_time_utc"], "2026-11-02T05:00:00Z")

    def test_ntp_state_is_reported_not_compared(self):
        self.s.fill()
        self.replace("npu_raw_2c_r1", ((1, "npu_raw", 2, 2400), {"ntp_synchronized": "no"}))
        code, out, _, h = self.s.run()
        self.assertEqual(code, 0, out)
        self.assertEqual(h["ntp_synchronized"], "not in every run")

    def test_trip_warnings_are_summed(self):
        self.s.fill()
        self.replace("p_cap_3c_r1", ((1, "p_cap", 3, 3600), {"trip": 4}))
        code, out, v, _ = self.s.run()
        self.assertEqual(code, 0, out)
        self.assertEqual(v["samples_at_or_above_trip0"], "4")

    def test_input_errors(self):
        cases = {
            "differ": ((1, "npu_raw", 2, 2400), {"code_commit": "other"}),
            "valid is 0": ((1, "npu_raw", 2, 2400), {"valid": "0"}),  # exit 0 in runs.tsv
            "does not match": ((1, "npu_raw", 2, 2400), {"repetition": "2"}),
        }
        for message, run in cases.items():
            with self.subTest(message), tempfile.TemporaryDirectory() as t:
                self.s = Session(Path(t))
                self.s.fill()
                self.replace("npu_raw_2c_r1", run)
                code, out, _, _ = self.s.run()
                self.assertEqual(code, 2, out)
                self.assertIn(message, out)

    def test_no_valid_run(self):
        self.s.add(1, "p_cap", 3, 1, valid="0", exit_code=1)
        code, out, _, _ = self.s.run()
        self.assertEqual(code, 2, out)
        self.assertIn("no valid run", out)


if __name__ == "__main__":
    unittest.main()
