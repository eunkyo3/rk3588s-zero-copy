import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import m2_checks as mc  # noqa: E402


def good_equivalence():
    rows = {"passed": "1", "complete": "1", "differing_total": "0", "synthetic_sets": "12500",
            "synthetic_decodes": "1000000", "synthetic_differing_sets": "0",
            "sha256_yolov8_post_o": "a" * 64, "sha256_yolov8_post_neon_o": "b" * 64}
    for i, (name, (frames, sha)) in enumerate(mc.VIDEOS.items(), 1):
        rows.update({f"video{i}_path": f"/home/pi/media/bench/{name}", f"video{i}_frames": str(frames),
                     f"video{i}_sha256": sha or "0" * 64, f"video{i}_differing_frames": "0"})
    return rows


class StaggerError(unittest.TestCase):
    def test_exact_slots(self):
        self.assertAlmostEqual(mc.stagger_error([10 + 200 / 6 * (k % 6) for k in range(8)]), 0.0)

    def test_largest_offset(self):
        phases = [10 + 200 / 6 * (k % 6) for k in range(8)]
        phases[3] += 7.5
        phases[6] -= 2.0
        self.assertAlmostEqual(mc.stagger_error(phases), 7.5)

    def test_across_the_circle(self):
        # channel 1 arrives 9 ms after channel 0 instead of 33.3 ms: 24.3 ms off
        self.assertAlmostEqual(mc.stagger_error([190.0, 199.0]), 200 / 6 - 9.0)

    def test_unstaggered_channels_together(self):
        self.assertAlmostEqual(mc.stagger_error([50.0, 50.0]), 200 / 6)


class SamplerRule(unittest.TestCase):
    def test_rule(self):
        frames = [{"channel": "1", "seq": "12"}, {"channel": "2", "seq": "13"}, {"channel": "8", "seq": "7"},
                  {"channel": "3", "seq": "12"}]
        self.assertEqual(mc.sampler_rule_broken(frames), [frames[3]])


class Schedstat(unittest.TestCase):
    def summary(self, **kw):
        s = {"schedstat_version": "15", "process_cpu_s": "19.0", "schedstat_cpu_total_s": "30.0"}
        s.update({f"schedstat_cpu{c}_s": "1.0" for c in range(8)})
        s.update({"schedstat_cpu0-3_s": "4.0"})
        s.update(kw)
        return s

    def test_passes(self):
        self.assertEqual(mc.schedstat_problems(self.summary()), [])

    def test_below_process_cpu(self):
        self.assertEqual(len(mc.schedstat_problems(self.summary(schedstat_cpu_total_s="18.9"))), 1)

    def test_a_cpu_at_zero(self):
        self.assertEqual(mc.schedstat_problems(self.summary(schedstat_cpu2_s="0.000")), ["schedstat_cpu2_s is 0.000"])

    def test_a_cpu_missing(self):
        s = self.summary()
        del s["schedstat_cpu7_s"]
        self.assertEqual(mc.schedstat_problems(s), ["schedstat_cpu7_s is missing"])

    def test_missing(self):
        self.assertEqual(mc.schedstat_problems({"schedstat_version": "-1", "process_cpu_s": "1"}),
                         ["no /proc/schedstat values"])


class Equivalence(unittest.TestCase):
    def test_passes(self):
        self.assertEqual(mc.equivalence_problems(good_equivalence()), [])

    def test_a_differing_frame(self):
        rows = good_equivalence()
        rows["video3_differing_frames"] = "1"
        self.assertEqual(mc.equivalence_problems(rows), ["video3: 1 frames differ"])

    def test_wrong_video_and_count(self):
        rows = good_equivalence()
        rows["video2_sha256"] = "1" * 64
        rows["video1_frames"] = "1000"
        self.assertEqual(len(mc.equivalence_problems(rows)), 2)

    def test_not_passed(self):
        rows = good_equivalence()
        rows["passed"] = "0"
        self.assertEqual(mc.equivalence_problems(rows), ["passed is not 1"])

    def test_not_complete(self):
        rows = good_equivalence()
        rows["complete"] = "0"
        self.assertEqual(mc.equivalence_problems(rows), ["not complete, or something differs"])


class ArmsAndObjects(unittest.TestCase):
    def test_arm(self):
        header = {"variant": "C", "run_kind": "check", "condition": "main", "post": "neon", "channels": "8",
                  "stagger": "on"}
        self.assertEqual(mc.arm_problems(header, "neon", 8, "on"), [])
        self.assertEqual(mc.arm_problems(header, "m1", 8, "on"), ["post is neon, not m1"])  # C's and Cp's swapped
        self.assertEqual(mc.arm_problems(dict(header, run_kind="m2"), "neon", 8, "on"), ["run_kind is m2, not check"])

    def test_objects(self):
        rows = good_equivalence()
        env = {"post": {"objects_sha256": {mc.OBJECTS[0]: "a" * 64, mc.OBJECTS[1]: "b" * 64}}}
        self.assertEqual(mc.objects_problems(env, rows), [])
        env["post"]["objects_sha256"][mc.OBJECTS[1]] = "c" * 64
        self.assertEqual(mc.objects_problems(env, rows), ["not the checked objects"])
        self.assertEqual(mc.objects_problems({}, rows), ["not the checked objects"])


class DefaultPath(unittest.TestCase):
    def env(self, **kw):
        e = {"threads_at_window_start": [{"tid": 1, "comm": "bench_main", "affinity": "4-7"},
                                         {"tid": 2, "comm": "metrics", "affinity": "0"}],
             "queues": {"K": 2, "Q1": 4, "Q2": 6, "N": 11}, "header_post": "m1", "header_stagger": "off",
             "header_variant": "C", "header_run_kind": "check", "header_condition": "main", "header_channels": "2"}
        e.update(kw)
        return e

    def test_same_layout_other_tids(self):
        m1 = self.env()
        m1["threads_at_window_start"] = [{"tid": 9, "comm": "metrics", "affinity": "0"},
                                         {"tid": 8, "comm": "bench_main", "affinity": "4-7"}]
        m1 = {k: v for k, v in m1.items() if not k.startswith("header_")}
        self.assertEqual(mc.default_problems(self.env(), m1), [])

    def test_affinity_differs(self):
        m1 = self.env()
        m1["threads_at_window_start"][1]["affinity"] = "4"
        self.assertEqual(mc.default_problems(self.env(), m1), ["threads or affinities differ from M1's"])

    def test_not_defaults(self):
        self.assertEqual(mc.default_problems(self.env(header_post="neon"), self.env()), ["post is neon, not m1"])
        self.assertEqual(mc.default_problems(self.env(header_channels="8"), self.env()), ["channels is 8, not 2"])


if __name__ == "__main__":
    unittest.main()
