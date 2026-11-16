#!/usr/bin/env python3
"""Tests for tools/m4_calib_summary.py.

Usage: python3 tests/test_m4_calib_summary.py
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
import m4_calib_summary as ms  # noqa: E402

HEADER = {"start_time_utc": "2026-11-20T06:00:00Z", "ntp_synchronized": "yes", "prereg_v1_commit": "p",
          "freeze_m1_commit": "none", "prereg_m4_commit": "8638", "freeze_m4_commit": "f4f4", "run_kind": "m4",
          "decoder": "none (preloaded tensors)", "variant": "n/a", "channels": "n/a", "code_commit": "c",
          "amendment": "A14", "warmup_s": "20", "duration_s": "120", "tensors": "t.u8", "tensor_count": "1000",
          "tensor_heap": "system-uncached-dma32"}
CLOCKS = "policy0_khz: 1800000\npolicy4_khz: 2208000\npolicy6_khz: 2208000\ndmc_hz: 2112000000\nnpu_hz: 1000000000\n"
SHA = {m: sha for m, (sha, _) in ms.MODELS.items()}
POST = {m: post for m, (_, post) in ms.MODELS.items()}


class Session:
    """A fake tools/m4_calib.sh session."""

    def __init__(self, d, idle_s=600, tensors=ms.TENSORS_SHA256, **header):
        self.d = d
        self.rows = []
        self.header = dict(HEADER, **header)  # every run's header fields
        (d / "perf_mode.txt").write_text(CLOCKS, encoding="utf-8")
        (d / "baseline.txt").write_text(f"idle_s: {idle_s}\nsoc_thermal_baseline_mc: 37000\n", encoding="utf-8")
        (d / "tensors.sha256").write_text(f"{tensors}  t.u8\n", encoding="utf-8")
        (d / "session.txt").write_text("run_kind: m4\nfreeze_m4_commit: f4f4\ncooling: fan\nambient_c: 24\n",
                                       encoding="utf-8")

    def add(self, rep, model, measurement, completed, attempt=1, exit_code=0, valid="1", trip=0, run_ms=15.0,
            **header):
        cores = 3 if measurement == "p_cap" else 1
        name = f"{model}_{measurement}_{cores}c_r{rep}" + ("_repeat" if attempt == 2 else "")
        h = dict(self.header, measurement=measurement, cores=str(cores), repetition=str(rep), model_sha256=SHA[model],
                 post=POST[model] if measurement == "p_cap" else "none")
        h.update(header)
        (self.d / name).mkdir()
        text = "".join(f"# {k}: {v}\n" for k, v in h.items())
        text += (f"key,value\ncompleted_in_window,{completed}\nbind_ms_mean,0.020\nrun_ms_mean,{run_ms:.3f}\n"
                 + ("post_ms_mean,0.120\n" if measurement == "p_cap" else "")
                 + f"npu_load_mean_core0,90.0\nnpu_load_mean_core1,{80.0 if cores == 3 else 0.0}\n"
                 f"npu_load_mean_core2,{80.0 if cores == 3 else 0.0}\nsamples_at_or_above_trip0,{trip}\n"
                 f"clock_samples_below_fixed,{0 if valid == '1' else 3}\nvalid,{valid}\n")
        (self.d / name / "summary.csv").write_text(text, encoding="utf-8")
        self.rows.append((rep, model, measurement, cores, attempt, name, exit_code))

    def drop(self, name):
        self.rows = [r for r in self.rows if r[5] != name]
        shutil.rmtree(self.d / name)

    def fill(self, completed=None):
        """Every run of §3.1, in its order."""
        for rep, model, measurement in ms.planned_order():
            default = {"p_cap": 24000, "npu_raw": 8000}[measurement]
            self.add(rep, model, measurement, (completed or {}).get((model, measurement, rep), default))

    def run(self):
        lines = ["repetition\tmodel\tmeasurement\tcores\tattempt\tdir\texit\tgate_soc_mc\tstart_utc\tend_utc"]
        lines += ["\t".join(map(str, r)) + "\t40000\ts\te" for r in self.rows]
        (self.d / "runs.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ms.main(["m4_calib_summary.py", str(self.d)])
        values, header = {}, {}
        if (self.d / "m4_calibration.csv").exists():
            for line in (self.d / "m4_calibration.csv").read_text(encoding="utf-8").splitlines():
                if line.startswith("# "):
                    k, _, v = line[2:].partition(": ")
                    header[k] = v
                else:
                    k, _, v = line.partition(",")
                    values[k] = v
        return code, out.getvalue() + err.getvalue(), values, header


class Rules(unittest.TestCase):
    def test_order(self):
        # M4 §3.1: P_cap before the raw limit; YOLOv8n first in repetitions 1 and 3, YOLOv10n in 2.
        order = ms.planned_order()
        self.assertEqual(len(order), 12)
        self.assertEqual(order[:4], [(1, "yolov8n", "p_cap"), (1, "yolov8n", "npu_raw"),
                                     (1, "yolov10n", "p_cap"), (1, "yolov10n", "npu_raw")])
        self.assertEqual([m for r, m, x in order if x == "p_cap"],
                         ["yolov8n", "yolov10n", "yolov10n", "yolov8n", "yolov8n", "yolov10n"])

    def test_saturation_boundaries(self):
        # Auxiliary: 5 × 30 = 150 ≤ 0.8 × P_cap at P_cap = 187.5 = 22500 / 120. Main: 8 × 5 = 40 at 50.
        self.assertEqual(ms.saturation(Fraction(22500, 120)), {"main": "unsaturated", "aux": "unsaturated"})
        self.assertEqual(ms.saturation(Fraction(22499, 120)), {"main": "unsaturated", "aux": "saturated"})
        self.assertEqual(ms.saturation(Fraction(50)), {"main": "unsaturated", "aux": "saturated"})
        self.assertEqual(ms.saturation(Fraction(5999, 120)), {"main": "saturated", "aux": "saturated"})
        self.assertEqual(ms.saturation(None), {"main": "unknown", "aux": "unknown"})


class Summarize(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Session(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def replace(self, name, *runs):
        """Drops run `name` and adds `runs`, each the arguments of Session.add, in place of it in runs.tsv."""
        i = [r[5] for r in self.s.rows].index(name)
        self.s.drop(name)
        tail, self.s.rows = self.s.rows[i:], self.s.rows[:i]
        for args, kwargs in runs:
            self.s.add(*args, **kwargs)
        self.s.rows += tail

    def test_medians_labels_and_records(self):
        self.s.fill({("yolov10n", "p_cap", 1): 19800, ("yolov10n", "p_cap", 2): 19300,
                     ("yolov10n", "p_cap", 3): 19500})
        self.replace("yolov8n_npu_raw_1c_r3", ((3, "yolov8n", "npu_raw", 8000), {"run_ms": 14.0}))
        code, out, v, h = self.s.run()
        self.assertEqual(code, 0, out)
        self.assertEqual(v["yolov10n_p_cap_inf_per_s"], f"{19500 / 120:.4f}")
        self.assertEqual(v["yolov8n_p_cap_inf_per_s"], "200.0000")
        self.assertEqual(v["yolov10n_npu_raw_1c_r2_completed"], "8000")
        self.assertEqual((v["yolov8n_main_point"], v["yolov8n_aux_point"]), ("unsaturated", "unsaturated"))
        self.assertEqual((v["yolov10n_main_point"], v["yolov10n_aux_point"]), ("unsaturated", "saturated"))
        self.assertEqual((v["yolov8n_npu_raw_1c_run_ms"], v["yolov8n_p_cap_post_ms"]), ("15.000", "0.120"))
        self.assertNotIn("yolov8n_npu_raw_1c_post_ms", v)
        self.assertEqual((v["yolov10n_p_cap_npu_load_core2"], v["yolov10n_npu_raw_1c_npu_load_core1"]),
                         ("80.0", "0.0"))
        self.assertEqual((v["cooling"], v["ambient_c"], v["tensors_sha256"]), ("fan", "24", ms.TENSORS_SHA256))
        self.assertEqual((v["order_as_planned"], v["complete"], v["m4_settings"]), ("1", "1", "yes"))
        for k in ["prereg_m4_commit", "freeze_m4_commit", "run_kind", "code_commit", "amendment", "repetition"]:
            self.assertIn(k, h)
        self.assertNotIn("model_sha256", h)  # per model, not shared

    def test_repeat_replaces_an_invalid_run(self):
        self.s.fill()
        self.replace("yolov10n_p_cap_3c_r2", ((2, "yolov10n", "p_cap", 99999), {"valid": "0", "exit_code": 1}),
                     ((2, "yolov10n", "p_cap", 21000), {"attempt": 2}))
        code, out, v, _ = self.s.run()
        self.assertEqual(code, 0, out)
        self.assertEqual((v["yolov10n_p_cap_r2"], v["order_as_planned"]), ("175.0000", "1"))

    def test_invalid_twice_uses_the_valid_runs(self):
        # M4 §5: fewer than 3 valid runs give the median of the valid ones.
        self.s.fill({("yolov10n", "p_cap", 1): 22000, ("yolov10n", "p_cap", 3): 23000})
        self.replace("yolov10n_p_cap_3c_r2", ((2, "yolov10n", "p_cap", 1), {"valid": "0", "exit_code": 1}),
                     ((2, "yolov10n", "p_cap", 1), {"attempt": 2, "valid": "0", "exit_code": 1}))
        code, out, v, _ = self.s.run()
        self.assertEqual(code, 0, out)
        self.assertEqual((v["yolov10n_p_cap_r2"], v["yolov10n_p_cap_valid_runs"]), ("invalid_x2", "2"))
        self.assertEqual(v["yolov10n_p_cap_inf_per_s"], f"{22500 / 120:.4f}")
        self.assertEqual(v["yolov10n_aux_point"], "unsaturated")  # 187.5 exactly

    def test_no_valid_p_cap_is_saturation_unknown(self):
        self.s.fill()
        for rep in (1, 2, 3):
            self.replace(f"yolov10n_p_cap_3c_r{rep}",
                         ((rep, "yolov10n", "p_cap", 1), {"valid": "0", "exit_code": 1}),
                         ((rep, "yolov10n", "p_cap", 1), {"attempt": 2, "valid": "0", "exit_code": 1}))
        code, out, v, _ = self.s.run()
        self.assertEqual(code, 0, out)  # complete: every repetition is valid or invalid ×2
        self.assertEqual((v["yolov10n_p_cap_inf_per_s"], v["yolov10n_main_point"], v["yolov10n_aux_point"]),
                         ("none", "unknown", "unknown"))
        self.assertEqual(v["yolov10n_p_cap_run_ms"], "none")

    def test_stopped_session_is_incomplete(self):
        self.s.fill()
        for name in ["yolov8n_npu_raw_1c_r3", "yolov10n_p_cap_3c_r3", "yolov10n_npu_raw_1c_r3"]:
            self.s.drop(name)
        code, out, v, _ = self.s.run()
        self.assertEqual(code, 1, out)
        self.assertEqual((v["yolov10n_p_cap_r3"], v["complete"], v["order_as_planned"]), ("missing", "0", "1"))

    def test_runs_out_of_order(self):
        self.s.fill()
        self.s.rows[0], self.s.rows[1] = self.s.rows[1], self.s.rows[0]  # raw before P_cap
        code, out, v, _ = self.s.run()
        self.assertEqual(code, 1, out)
        self.assertEqual(v["order_as_planned"], "0")
        self.assertIn("not in §3.1's order", v["m4_settings"])

    def test_settings_other_than_m4s(self):
        cases = [({"idle_s": 60}, {}, "idle_s 60 (M4 600)"),
                 ({}, {"warmup_s": "1", "duration_s": "3"}, "warmup_s 1 (M4 20); duration_s 3 (M4 120)"),
                 ({}, {"run_kind": "check", "freeze_m4_commit": "none"}, "run_kind check (M4 m4)"),
                 ({"tensors": "beef"}, {}, "tensors_sha256 beef (A7")]
        for session, header, message in cases:
            with self.subTest(message), tempfile.TemporaryDirectory() as t:
                self.s = Session(Path(t), **session, **header)
                self.s.fill()
                code, out, v, _ = self.s.run()
                self.assertEqual(code, 1, out)
                self.assertIn(message, v["m4_settings"])

    def test_input_errors(self):
        cases = {
            "differ": ("yolov8n_npu_raw_1c_r1", ((1, "yolov8n", "npu_raw", 8000), {"code_commit": "other"})),
            "valid is 0": ("yolov8n_npu_raw_1c_r1", ((1, "yolov8n", "npu_raw", 8000), {"valid": "0"})),
            "does not match": ("yolov8n_npu_raw_1c_r1", ((1, "yolov8n", "npu_raw", 8000), {"repetition": "2"})),
            "not yolov8n's": ("yolov8n_p_cap_3c_r1",
                              ((1, "yolov8n", "p_cap", 24000), {"model_sha256": SHA["yolov10n"]})),
            "post m1, not neon": ("yolov8n_p_cap_3c_r1", ((1, "yolov8n", "p_cap", 24000), {"post": "m1"})),
            "post v10, not none": ("yolov10n_npu_raw_1c_r1", ((1, "yolov10n", "npu_raw", 8000), {"post": "v10"})),
        }
        for message, (name, run) in cases.items():
            with self.subTest(message), tempfile.TemporaryDirectory() as t:
                self.s = Session(Path(t))
                self.s.fill()
                self.replace(name, run)
                code, out, _, _ = self.s.run()
                self.assertEqual(code, 2, out)
                self.assertIn(message, out)

    def test_no_valid_run(self):
        self.s.add(1, "yolov8n", "p_cap", 1, valid="0", exit_code=1)
        code, out, _, _ = self.s.run()
        self.assertEqual(code, 2, out)
        self.assertIn("no valid run", out)


if __name__ == "__main__":
    unittest.main()
