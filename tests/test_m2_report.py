import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import m2_report as mr  # noqa: E402


def run(cpu, fps=5.0, drop=0.0, sched=None):
    return {"process_cpu_s": cpu, "inference_fps_min": fps, "drop_rate": drop,
            "schedstat_total_minus_idle_s": cpu + 3 if sched is None else sched}


def arms(c, cp, **kw):
    return {r: run(x, **kw) for r, x in enumerate(c, 1)}, {r: run(x, **kw) for r, x in enumerate(cp, 1)}


class Verdict(unittest.TestCase):
    def test_adopted_at_the_threshold(self):
        c, cp = arms([20.0, 20.0, 20.0], [19.4, 19.4, 19.4])  # exactly 3%
        v = mr.verdict("m2_main", c, cp)
        self.assertAlmostEqual(v["saving"], 0.03)
        self.assertEqual((v["spec"], v["practical"]), ("adopted", "adopted"))

    def test_rejected_below_it(self):
        v = mr.verdict("m2_main", *arms([20.0, 20.0, 20.0], [19.5, 19.5, 19.5]))
        self.assertEqual((v["spec"], v["practical"]), ("rejected", "rejected"))

    def test_aux_threshold_and_fps_guard(self):
        c, cp = arms([40.0, 40.0, 40.0], [37.0, 37.0, 37.0], fps=30.0)  # 7.5%
        self.assertEqual(mr.verdict("m2_aux", c, cp)["practical"], "adopted")
        cp[2]["inference_fps_min"] = 28.4
        v = mr.verdict("m2_aux", c, cp)
        self.assertEqual((v["spec"], v["practical"]), ("adopted", "indeterminate"))
        self.assertIn("below 28.5 fps", v["reason"])

    def test_drop_guard_is_one_sided(self):
        c, cp = arms([20.0] * 3, [19.0] * 3)
        for r in cp:
            cp[r]["drop_rate"] = 0.011
        self.assertEqual(mr.verdict("m2_main", c, cp)["practical"], "indeterminate")
        for r in cp:
            cp[r]["drop_rate"], c[r]["drop_rate"] = 0.0, 0.05  # C dropping more does not stop Cp
        self.assertEqual(mr.verdict("m2_main", c, cp)["practical"], "adopted")

    def test_fewer_than_three_runs(self):
        c, cp = arms([20.0, 20.0, 20.0], [19.0, 19.0])
        v = mr.verdict("m2_main", c, cp)
        self.assertEqual((v["spec"], v["practical"], v["reason"]), ("adopted", "indeterminate", "fewer than 3 valid runs"))
        self.assertEqual(mr.verdict("m2_main", c, {})["spec"], "indeterminate")

    def test_mixed_and_pairs(self):
        c, cp = arms([20.0, 21.0, 19.0], [19.0, 20.5, 18.0])
        for r in cp:
            cp[r]["schedstat_total_minus_idle_s"] = c[r]["schedstat_total_minus_idle_s"] + 1
        v = mr.verdict("m2_main", c, cp)
        self.assertTrue(v["mixed"])
        self.assertAlmostEqual(v["pairs"][2], 0.5 / 21.0)
        mean, lo, hi = v["interval"]
        self.assertTrue(lo < mean < hi)

    def test_cv_flag(self):
        v = mr.verdict("m2_main", *arms([18.0, 20.0, 22.0], [19.0, 19.0, 19.0]))  # C's CV 10%
        self.assertEqual(v["cv_flags"], ["C"])


class Interval(unittest.TestCase):
    def test_three_values(self):
        mean, lo, hi = mr.t_interval([0.01, 0.02, 0.03])
        self.assertAlmostEqual(mean, 0.02)
        self.assertAlmostEqual(hi - mean, 4.302652729911275 * 0.01 / math.sqrt(3))

    def test_one_value(self):
        mean, lo, hi = mr.t_interval([0.02])
        self.assertTrue(math.isnan(lo) and math.isnan(hi))


class Detections(unittest.TestCase):
    def test_video_frame(self):
        self.assertEqual(mr.video_frame(1, 95985), 32)  # ① has 1,186 frames; 95985 is frame 32, 15 ticks early
        self.assertEqual(mr.video_frame(1, (1186 + 5) * 3000), 5)  # the next loop
        self.assertEqual(mr.video_frame(6, (752 + 5) * 3000), 5)  # channel 6 plays ②

    def test_compare(self):
        def frame(ch, k, n):
            return {"channel": str(ch), "pts": str(k * 3000), "detections": str(n)}
        c = [frame(1, 0, 3), frame(1, 6, 4), frame(1, 1186 + 6, 4), frame(2, 1, 2)]
        cp = [frame(1, 6, 4), frame(1, 12, 9), frame(2, 1, 1)]
        self.assertEqual(mr.compare_detections(c, cp), (2, 1, 0))
        c.append(frame(1, 1186 * 2 + 6, 5))  # the same video frame, another count, within a run
        self.assertEqual(mr.compare_detections(c, cp), (2, 2, 1))


class Aligned(unittest.TestCase):
    def test_shift_found(self):
        counts = [(k * 7) % 5 for k in range(1186)]  # a count per video frame of ①
        c = [{"channel": "1", "pts": str(k * 3000), "detections": str(counts[k])} for k in range(0, 1186, 2)]
        # Cp's pts start 4 frames later: its frame k is C's frame k + 4
        cp = [{"channel": "1", "pts": str(k * 3000), "detections": str(counts[(k + 4) % 1186])} for k in range(0, 1186, 2)]
        shift, agree, n, nxt = mr.aligned_agreement(c, cp, 1)
        self.assertEqual((shift, agree), (4, 1.0))
        self.assertGreater(n, 500)
        self.assertLess(nxt, 1.0)

    def test_too_few_frames(self):
        c = [{"channel": "1", "pts": "0", "detections": "1"}]
        self.assertIsNone(mr.aligned_agreement(c, c, 1))


if __name__ == "__main__":
    unittest.main()
