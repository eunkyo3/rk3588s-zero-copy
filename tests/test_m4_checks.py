#!/usr/bin/env python3
"""Tests for tools/m4_checks.py: the verdicts of prereg/M4.md §8 step 3 on the real check records of
2026-11-17, and on copies of them with one thing wrong at a time.

Usage: python3 tests/test_m4_checks.py
"""
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import m4_checks as m4c  # noqa: E402

CHECK = ROOT / "results/check"
RECORDS = {  # §8 step 3's records at the build of 63febdf (the inputs from ①)
    "inputs": "20261116T064917Z_m4_inputs", "decode": "20261117T081135Z_m4_decode",
    "rules_v8": "20261117T081427Z_rules_yolov8n", "rules_v10": "20261117T081441Z_rules_yolov10n",
    "check_v8": "20261117T081555Z_rules_check_yolov8n", "check_v10": "20261117T081622Z_rules_check_yolov10n",
    "wiring_v8": "20261117T081456Z_wiring_yolov8n", "wiring_v10": "20261117T081504Z_wiring_yolov10n",
    "default": "20261117T081656Z_m4_default_c", "arms": "20261117T082036Z_m4_check",
}
C8_RUN, C10_RUN = "01_m4_check_C8_8ch", "02_m4_check_C10_8ch"
SECTIONS = 10
DATA, DECODING, DEFAULT = "Data (§2.1, §2.2)", "The board tool's decoding (§8 step 3)", "Default path (§8 step 3)"
RULES_V8, RULES_V10 = "Rules, yolov8n (§2.3)", "Rules, yolov10n (§2.3)"
WIRING_V8, WIRING_V10 = "Wiring, C8 (yolov8n)", "Wiring, C10 (yolov10n)"
ARMS, BUILD, OBJECTS = "Arms (§8 step 3)", "Build (§8 step 3)", "Objects (§8 step 3)"
PIPELINE_POST = "build/CMakeFiles/core.dir/src/core/pipeline_post.cpp.o"


def post(**values):
    """A change to an env.json's post-processing."""
    return lambda e: e["post"].update(values)


class Checks(unittest.TestCase):
    def setUp(self):
        missing = [n for n in RECORDS.values() if not (CHECK / n).is_dir()]
        if missing:
            self.skipTest(f"no {missing[0]} in results/check/")
        self.tmp = tempfile.TemporaryDirectory()
        self.check = Path(self.tmp.name) / "results/check"
        for name in RECORDS.values():  # the dumps are not read: their list, dump.sha256, is
            shutil.copytree(CHECK / name, self.check / name,
                            ignore=shutil.ignore_patterns("*.i8", "dets", "server", "*.log"))

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, rules=("check_v8", "check_v10"), wiring=("wiring_v8", "wiring_v10")):
        r = {k: str(self.check / v) for k, v in RECORDS.items()}
        return SimpleNamespace(inputs=r["inputs"], decode=r["decode"], default=r["default"], arms=r["arms"],
                               rules_check=[r[k] for k in rules], wiring=[r[k] for k in wiring])

    def run_checks(self, **records):
        return m4c.report(self.args(**records), ROOT)

    def path(self, record, rel):
        return self.check / RECORDS[record] / rel

    def edit(self, record, rel, old, new):
        p = self.path(record, rel)
        text = p.read_text(encoding="utf-8")
        self.assertIn(old, text)
        p.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")

    def set_row(self, record, rel, key, value):
        """Sets a key,value CSV's row."""
        p = self.path(record, rel)
        lines = p.read_text(encoding="utf-8").splitlines()
        i = next(k for k, line in enumerate(lines) if line.startswith(f"{key},"))
        lines[i] = f"{key},{value}"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    def edit_json(self, record, rel, change):
        p = self.path(record, rel)
        value = json.loads(p.read_text(encoding="utf-8"))
        change(value)
        p.write_text(json.dumps(value), encoding="utf-8")

    def failed(self, section, **records):
        """The report, when only this section fails."""
        text, ok = self.run_checks(**records)
        self.assertFalse(ok)
        self.assertIn(f"## {section}: failed", text)
        self.assertEqual(text.count(": failed"), 1, text)
        return text

    def test_the_real_records_pass(self):
        text, ok = self.run_checks()
        self.assertTrue(ok, text)
        self.assertEqual(text.count(": passed"), SECTIONS, text)
        self.assertIn("(A13's)", text)
        self.assertIn("every board and PC record names build `63febdfd9c84c9f30fb4c7ef56d1a052a0eeb38b`", text)

    def test_one_thing_wrong_at_a_time(self):
        # (section, record, file, old, new, what the report says)
        cases = [
            (DATA, "inputs", "m4_inputs.csv", "png_mismatches,0", "png_mismatches,1", "png_mismatches is 1"),
            (DATA, "inputs", "m4_inputs.csv", "# prereg_m4_commit: 8638", "# prereg_m4_commit: 0000",
             "prereg-m4's commit as 0000"),
            (DECODING, "decode", "decode.csv", "images_not_the_manifests,0", "images_not_the_manifests,1",
             "images_not_the_manifests is 1"),
            (DECODING, "decode", "decode.csv", "# run_kind: check", "# run_kind: m4", "run kind m4"),
            (DECODING, "decode", "decode.csv", "# manifest_rows_sha256: 5a63", "# manifest_rows_sha256: 0000",
             "not of the inputs' manifest (manifest_rows_sha256)"),
            (DECODING, "decode", "board_pass.txt", "exited 0", "was killed",
             "board_pass.txt: the board tool was killed"),
            (RULES_V8, "check_v8", "rules_check.csv", "lists_differing,0", "lists_differing,1",
             "lists_differing is 1"),
            (RULES_V8, "check_v8", "rules_check.csv", "# eval_image_id: sha256:c676", "# eval_image_id: sha256:0000",
             "not the evaluation image"),
            (RULES_V8, "check_v8", "rules_check.csv", "# code_status: clean", "# code_status: dirty",
             "the PC's code was dirty"),
            (RULES_V8, "rules_v8", "pass.csv", "# model_sha256: 04a7", "# model_sha256: 0000",
             "is not a pass of §1's yolov8n"),
            (RULES_V8, "rules_v8", "pass.csv", "# manifest_sha256: b29a", "# manifest_sha256: 0000",
             "pass.csv is not of the inputs' manifest (manifest_sha256)"),
            (RULES_V8, "rules_v8", "pass.csv", "sha256_dump_sha256,74e4", "sha256_dump_sha256,0000",
             "dump.sha256 is not the one pass.csv names"),
            (RULES_V8, "rules_v8", "exit_code", "0", "1", "the board tool's exit code is 1"),
            (RULES_V10, "rules_v10", "pass.csv", "dumped_images,50", "dumped_images,49", "dumped_images is 49"),
            (WIRING_V8, "wiring_v8", "exit_code", "0", "139", "the board tool's exit code is 139"),
            (WIRING_V8, "wiring_v8", "wiring.csv", "\n1,17,17,1", "\n0,17,17,1", "frames are not 0 to N-1"),
            (WIRING_V8, "wiring_v8", "wiring.csv", "# tensors_sha256: 051d", "# tensors_sha256: 0000",
             "not M1-3's tensor set"),
            (WIRING_V10, "wiring_v10", "wiring.csv", "\n9,", "\n9,0,", "a malformed row: 9,0,"),
            (DEFAULT, "default", "env.json", '"Q1": 4', '"Q1": 5', "queues differ"),
            (ARMS, "arms", "runs.tsv", "\tmain\t1\t1\t0\t1\t", "\tmain\t1\t1\t0\t0\t", "C8: runs.tsv: exit 0, valid 0"),
            (ARMS, "arms", "result.csv", "session_status,completed", "session_status,stopped",
             "session_status is stopped"),
            (ARMS, "arms", "result.csv", "# session: 20261117T082036Z_m4_check", "# session: 20261117T082036Z_m2_check",
             "not this m4-check session"),
            (ARMS, "arms", f"{C8_RUN}/frames.csv", "# model_sha256: 04a7", "# model_sha256: 6a64",
             "C8: model_sha256 is 6a64"),
            (ARMS, "arms", f"{C8_RUN}/frames.csv", "# prereg_m4_commit: 8638", "# prereg_m4_commit: 0000",
             "C8: frames.csv names prereg-m4's commit as 0000"),
            (BUILD, "decode", "decode.csv", "# code_commit: 63febdf", "# code_commit: f0fcc7d", "2 builds are named"),
            (BUILD, "inputs", "m4_inputs.csv", "# code_status: clean", "# code_status: dirty",
             "m4_inputs.csv: the PC's code was dirty"),
            (BUILD, "inputs", "m4_inputs.csv", "# code_commit: 9dbf81491cc758203ec28ca85c7c83a45c8dc089",
             "# code_commit: 87fe874b4f196dafd48f6a175b2d7604625aedba", "tools/m4_prep.py at the inputs' commit"),
            (OBJECTS, "wiring_v8", "wiring.csv", "# map_rule_object_sha256: 94f5", "# map_rule_object_sha256: 0000",
             "different objects of the YOLOv8n rule"),
        ]
        for section, record, rel, old, new, says in cases:
            with self.subTest(section=section, rel=rel, new=new):
                saved = self.path(record, rel).read_bytes()
                self.edit(record, rel, old, new)
                self.assertIn(says, self.failed(section))
                self.path(record, rel).write_bytes(saved)

    def test_json_wrong_one_at_a_time(self):
        cases = [
            (RULES_V8, "check_v8", "pc.json", lambda j: j.update(prereg_m4_commit="0000"),
             "pc.json does not name prereg-m4's commit"),
            (RULES_V8, "check_v8", "pc.json", lambda j: j.update(run_kind="m4"), "pc.json is of run kind m4"),
            (DEFAULT, "default", "env.json", lambda e: e.update(header_prereg_m4_commit="0000"),
             "env.json does not name prereg-m4's commit"),
            (DEFAULT, "default", "env.json", lambda e: e.update(header_model_sha256=m4c.MODEL_SHA["yolov10n"]),
             "not M1's"),
            (DEFAULT, "default", "env.json", post(nms_thresh=0.5), "post nms_thresh is 0.5"),
            (ARMS, "arms", f"{C10_RUN}/env.json", lambda e: e.update(header_post="neon"),
             "C10: env.json does not record the arm's model and post-processing"),
            (ARMS, "arms", f"{C10_RUN}/env.json", post(max_det=100), "C10: env.json: post max_det is 100"),
            (ARMS, "arms", f"{C8_RUN}/env.json", post(conf_thresh=0.3), "C8: env.json: post conf_thresh is 0.3"),
            (BUILD, "arms", f"{C10_RUN}/env.json", lambda e: e.update(header_code_commit="eeaa97d"),
             "2 builds are named"),
            (OBJECTS, "default", "env.json", lambda e: e["post"]["objects_sha256"].pop(PIPELINE_POST),
             "does not name the four objects of kPostObjects"),
        ]
        for section, record, rel, change, says in cases:
            with self.subTest(section=section, rel=rel, says=says):
                saved = self.path(record, rel).read_bytes()
                self.edit_json(record, rel, change)
                self.assertIn(says, self.failed(section))
                self.path(record, rel).write_bytes(saved)

    def test_a_wiring_frame_that_differs(self):
        p = self.path("wiring_v8", "wiring.csv")
        lines = p.read_text(encoding="utf-8").splitlines()
        lines[-1] = lines[-1][:-1] + "0"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        self.assertIn("9 of 10 frames equal", self.failed(WIRING_V8))

    def test_a_malformed_wiring_row_is_not_dropped(self):
        p = self.path("wiring_v8", "wiring.csv")
        p.write_text(p.read_text(encoding="utf-8") + "10,17,16,0,x\n", encoding="utf-8", newline="\n")
        self.assertIn("a malformed row: 10,17,16,0,x", self.failed(WIRING_V8))

    def test_the_dump_list(self):
        p = self.path("rules_v8", "dump.sha256")
        lines = p.read_text(encoding="utf-8").splitlines()
        image = lines[-1][66:].rsplit("_out", 1)[0]
        for kept in ([l for l in lines if image not in l], [l for l in lines if "_out0." in l]):
            with self.subTest(lines=len(kept)):
                p.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
                self.set_row("rules_v8", "pass.csv", "sha256_dump_sha256", m4c.file_sha256(p))  # only the list wrong
                self.assertIn("does not list the 9 outputs of the manifest's first 50 images", self.failed(RULES_V8))

    def test_one_record_per_model(self):
        self.assertIn("a second record of yolov8n", self.failed(RULES_V8, rules=("check_v8", "check_v8")))
        self.assertIn("a second record of yolov10n", self.failed(WIRING_V10, wiring=("wiring_v10", "wiring_v10")))

    def test_yolov10n_has_no_exception(self):
        self.set_row("check_v10", "rules_check.csv", "lists_equal", "49")
        self.set_row("check_v10", "rules_check.csv", "lists_excepted_near_0.7", "1")
        self.assertIn("(no NMS: none can be excepted)", self.failed(RULES_V10))

    def test_objects(self):
        # another build of the shared function in one record: that record is named, not the others
        self.edit("wiring_v10", "wiring.csv", f'"{PIPELINE_POST}": "28a4', f'"{PIPELINE_POST}": "0000')
        text = self.failed(OBJECTS)
        self.assertIn(f"{RECORDS['wiring_v10']} names other post-processing objects than the other records: "
                      f"{PIPELINE_POST}", text)
        self.assertEqual(text.count("names other post-processing objects"), 1, text)
        self.assertIn("the same in 6 of 7 records", text)

    def test_an_object_other_than_a13s(self):
        self.edit_json("default", "env.json",
                       lambda e: e["post"]["objects_sha256"].update(dict.fromkeys(m4c.A13_OBJECTS, "0" * 64)))
        self.assertIn(f"{RECORDS['default']}: build/CMakeFiles/core.dir/src/core/yolov8_post.cpp.o is 0000",
                      self.failed(OBJECTS))

    def test_the_sampler_rule(self):
        p = self.path("arms", f"{C8_RUN}/frames.csv")
        lines = p.read_text(encoding="utf-8").splitlines()
        cols = next(l for l in lines if not l.startswith("# ")).split(",")
        i = next(k for k, l in enumerate(lines) if l and not l.startswith("# ") and l.split(",") != cols)
        row = lines[i].split(",")
        row[cols.index("seq")] = str(int(row[cols.index("seq")]) + 1)  # a frame off its channel's phase
        lines[i] = ",".join(row)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        self.assertRegex(self.failed(ARMS), r"C8: 1 of \d+ frames break the sampler's rule")

    def test_a_build_that_is_not_a_whole_commit(self):
        p, _ = m4c.build_section({"a": "63febdfd9c84c9f30fb4c7ef56d1a052a0eeb38b-dirty"}, {}, "", ROOT)
        self.assertEqual(p, ["the build 63febdfd9c84c9f30fb4c7ef56d1a052a0eeb38b-dirty is not a whole commit "
                             "(a -dirty build?)"])

    def test_a13_objects_are_the_amendments(self):
        text = (ROOT / "prereg/AMENDMENTS.md").read_text(encoding="utf-8")
        for path, sha in m4c.A13_OBJECTS.items():
            self.assertIn(sha, text, path)

    def main_exit(self):
        a = self.args()
        argv = ["--inputs", a.inputs, "--decode", a.decode, "--default", a.default, "--arms", a.arms,
                "--rules-check", *a.rules_check, "--wiring", *a.wiring]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            return m4c.main(argv), out.getvalue(), err.getvalue()

    def test_main(self):
        code, out, _ = self.main_exit()
        self.assertEqual(code, 0, out)
        self.edit("decode", "decode.csv", "images_not_the_manifests,0", "images_not_the_manifests,1")
        code, out, _ = self.main_exit()
        self.assertEqual(code, 1, out)
        self.assertIn("**All checks did not pass.**", out)

    def test_main_on_records_it_cannot_read(self):
        cases = [("default", "env.json", None), ("check_v8", "pc.json", "[]"), ("arms", f"{C8_RUN}/env.json", "[]"),
                 ("decode", "decode.csv", None), ("check_v8", "rules_check.csv", None),
                 ("wiring_v8", "wiring.csv", None), ("inputs", "manifest.tsv", None)]
        for record, rel, content in cases:
            with self.subTest(record=record, rel=rel):
                p = self.path(record, rel)
                saved = p.read_bytes()
                if content is None:
                    p.unlink()
                else:
                    p.write_text(content, encoding="utf-8")
                code, out, err = self.main_exit()
                self.assertEqual(code, 2, out)
                self.assertIn("cannot read the records", err)
                p.write_bytes(saved)


if __name__ == "__main__":
    unittest.main()
