import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import m4_report as mr  # noqa: E402

COMMIT = "6" * 40


def runs(values, key="process_cpu_s"):
    return {r: {key: v} for r, v in values.items()}


class Differences(unittest.TestCase):
    def test_paired_by_repetition(self):
        c8, c10 = runs({1: 17.4, 2: 17.54, 3: 18.45}), runs({1: 18.88, 2: 18.42, 3: 16.91})
        d = mr.differences(c8, c10, "process_cpu_s")
        self.assertEqual(list(d), [1, 2, 3])
        self.assertAlmostEqual(d[3], -1.54)
        rel = mr.differences(c8, c10, "process_cpu_s", relative=True)
        self.assertAlmostEqual(rel[1], 1.48 / 17.4)

    def test_a_repetition_without_both_arms_drops_out(self):
        d = mr.differences(runs({1: 1.0, 2: 1.0, 3: 1.0}), runs({1: 2.0, 3: 4.0}), "process_cpu_s")
        self.assertEqual(d, {1: 1.0, 3: 3.0})

    def test_interval_needs_two_pairs(self):
        pairs, mean = mr.interval_text({1: 0.5, 2: 1.5}, lambda x: f"{x:.2f}")
        self.assertEqual(pairs, "r1 0.50, r2 1.50")
        half = 12.706204736174698 * math.sqrt(0.5) / math.sqrt(2)  # t(0.975, 1 df) × sd / √n
        self.assertEqual(mean, f"1.00 [{1 - half:.2f}, {1 + half:.2f}]")
        self.assertEqual(mr.interval_text({2: 0.5}, lambda x: f"{x:.2f}")[1], "0.50 (no interval)")
        self.assertEqual(mr.interval_text({}, str), ("–", "–"))


class Trigger(unittest.TestCase):
    def test_rounded_then_compared(self):
        self.assertEqual(mr.trigger(0.373340, 37.3), (37.3, 0.0, False))
        self.assertEqual(mr.trigger(0.383888, 38.5), (38.4, -0.1, False))

    def test_more_than_two_points(self):
        self.assertEqual(mr.trigger(0.353, 37.3)[2], False)  # 35.3: exactly 2.0, not more
        self.assertEqual(mr.trigger(0.352, 37.3)[2], True)
        self.assertEqual(mr.trigger(0.394, 37.3)[2], True)

    def test_no_evaluation(self):
        self.assertIsNone(mr.trigger(math.nan, 37.3)[2])


def write_eval(root, name, model, precision, pass_name, run_kind="m4", exit_code="0", commit=COMMIT):
    d = root / "results/m4" / name
    d.mkdir(parents=True)
    (d / "coco_eval.csv").write_text(
        f"# run_kind: {run_kind}\n# freeze_m4_commit: {commit}\n# pass: {pass_name}\n# model: {model}\n"
        f"# precision: {precision}\nkey,value\nall_ap,0.5\n", encoding="utf-8")
    (d / "exit.txt").write_text(f"exit_code: {exit_code}\nend_time_utc: 2026-11-18T05:00:00Z\n", encoding="utf-8")


class Evaluations(unittest.TestCase):
    def test_keys_and_what_counts(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            write_eval(root, "20261118T000001Z_eval_a", "yolov8n", "int8", "int8_yolov8n")
            write_eval(root, "20261118T000002Z_eval_b", "yolov8n", "int8", "int8_yolov8n_pipeline")
            write_eval(root, "20261118T000003Z_eval_c", "yolov10n", "fp32", "fp32_yolov10n")
            write_eval(root, "20261118T000004Z_eval_d", "yolov10n", "int8", "int8_yolov10n", exit_code="2")
            write_eval(root, "20261118T000005Z_eval_e", "yolov10n", "int8", "int8_yolov10n", run_kind="check")
            write_eval(root, "20261118T000006Z_eval_f", "yolov10n", "int8", "int8_yolov10n", commit="0" * 40)
            e = mr.evaluations(root, COMMIT)
            self.assertEqual(sorted(e), [("yolov10n", "fp32", "map"), ("yolov8n", "int8", "map"),
                                         ("yolov8n", "int8", "pipeline")])
            self.assertEqual(e[("yolov8n", "int8", "pipeline")]["record"], "20261118T000002Z_eval_b")
            self.assertEqual(mr.ap(e[("yolov8n", "int8", "map")]), 0.5)
            self.assertTrue(math.isnan(mr.ap(None)))

    def test_two_evaluations_of_one_pass_stop_it(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            write_eval(root, "20261118T000001Z_eval_a", "yolov8n", "fp32", "fp32_yolov8n")
            write_eval(root, "20261118T000002Z_eval_a", "yolov8n", "fp32", "fp32_yolov8n")
            with self.assertRaises(SystemExit):
                mr.evaluations(root, COMMIT)


class KeyValues(unittest.TestCase):
    def test_header_and_table(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "x.csv"
            path.write_text("# rule: a: b, c\n# ambient_c: 25\nkey,value\nvalid,1\nreason,none\n", encoding="utf-8")
            h, r = mr.key_values(path)
            self.assertEqual(h, {"rule": "a: b, c", "ambient_c": "25"})
            self.assertEqual(r, {"key": "value", "valid": "1", "reason": "none"})


if __name__ == "__main__":
    unittest.main()
