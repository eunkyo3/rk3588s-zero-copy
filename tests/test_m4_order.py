#!/usr/bin/env python3
"""Tests for tools/m4_order.py: M4's accuracy passes as their records show them, the order of prereg/M4.md
§8 step 5, the wiring check of §8 step 3, and the calib_npu session's gate.

Usage: python3 tests/test_m4_order.py
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import m4_order as mo  # noqa: E402
import stream_session as ss  # noqa: E402

F4 = "f4" * 20
BOARD_PATHS = {name: path for path, name in mo.BOARD_MODELS.items()}


def utc(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def text(t):
    return f"{t:%Y-%m-%dT%H:%M:%SZ}"


def pass_csv(d, start, kind, freeze, model, precision, seconds, valid):
    """pass.csv as tools/m4_accuracy.cpp and tools/m4_fp32.py write the fields m4_order reads; it starts a
    second after the run."""
    (d / "pass.csv").write_text(
        f"# start_time_utc: {text(utc(start) + timedelta(seconds=1))}\n# run_kind: {kind}\n# freeze_m4_commit: {freeze}\n"
        f"# amendment: A14\n# model: {model}\n# precision: {precision}\nkey,value\nimages,5000\nseconds,{seconds}\n"
        f"valid,{valid}\nreason,{'none' if valid else 'an input, its bytes'}\n", encoding="utf-8", newline="\n")


def board_pass(root, name, model="yolov8n", start="2026-11-20T01:00:00Z", seconds=300, valid=1, kind="m4",
               freeze=F4, mode="pass", exit_code="auto", with_pass_csv=True, pid="4242", board_pass_txt=None):
    """A record of tools/m4_board_pass.py: run.txt, and once the tool ended exit_code and pass.csv."""
    d = Path(root, name)
    d.mkdir(parents=True)
    (d / "run.txt").write_text(
        f"time_utc: {start}\ncommand: build/m4_accuracy {mode} --model {BOARD_PATHS[model]} --manifest m.tsv "
        f"--inputs i --out o --run-kind {kind} --freeze-m4-commit {freeze} --dump 50 --chunk 500 "
        f"--compare-dump results/check/x/dump\npid: {pid}\nboot_id: b-1\n", encoding="utf-8", newline="\n")
    if with_pass_csv:
        pass_csv(d, start, kind, freeze, model, "int8", seconds, valid)
    if exit_code == "auto":
        exit_code = (0 if valid else 1) if with_pass_csv else None
    if exit_code is not None:
        (d / "exit_code").write_text(f"{exit_code}\n", encoding="utf-8")
    if board_pass_txt:
        (d / "board_pass.txt").write_text(f"time_utc: {start}\noutcome: x\nseconds: {board_pass_txt}\n",
                                          encoding="utf-8")
    return d


def fp32_pass(root, name, model="yolov8n", start="2026-11-20T02:00:00Z", seconds=5000, valid=1, freeze=F4,
              tool="tools/m4_fp32.py", exit_code="auto", with_pass_csv=True, ended=True, kind="m4"):
    """A record of tools/eval/pc_run.py running tools/m4_fp32.py: pc.json, pass.csv and exit.txt."""
    d = Path(root, name)
    d.mkdir(parents=True)
    pc = {"start_time_utc": start, "run_kind": kind, "freeze_m4_commit": freeze, "amendment": "A14", "record": name,
          "tool": [tool, "--model", model, "--manifest", "m.tsv", "--inputs", "/data/inputs2"]}
    (d / "pc.json").write_text(json.dumps(pc, indent=1) + "\n", encoding="utf-8", newline="\n")
    if with_pass_csv:
        pass_csv(d, start, kind, freeze, model, "fp32", seconds, valid)
    if exit_code == "auto":
        exit_code = (0 if valid else 1) if with_pass_csv else "none"
    if ended:
        end = utc(start) + timedelta(seconds=seconds + 10)
        (d / "exit.txt").write_text(f"exit_code: {exit_code}\nend_time_utc: {text(end)}\n", encoding="utf-8")
    return d


def all_passes(root, freeze=F4):
    """The four passes, valid, one after another from 2026-11-20T01:00:00Z; the last ends 04:00:01."""
    board_pass(root, "20261120T010000Z_int8_yolov8n", "yolov8n", "2026-11-20T01:00:00Z", freeze=freeze)
    board_pass(root, "20261120T011000Z_int8_yolov10n", "yolov10n", "2026-11-20T01:10:00Z", freeze=freeze)
    fp32_pass(root, "20261120T012000Z_fp32_yolov8n", "yolov8n", "2026-11-20T01:20:00Z", 5000, freeze=freeze)
    fp32_pass(root, "20261120T030000Z_fp32_yolov10n", "yolov10n", "2026-11-20T03:00:00Z", 3600, freeze=freeze)
    return utc("2026-11-20T04:00:01Z")


def wiring_check(root, name, model, objects, frames=10, differ=(), model_sha=None, tensors=mo.TENSORS_SHA256,
                 cut=False):
    """A record of tools/m4_accuracy.cpp wiring: wiring.csv with its header and a row per frame."""
    d = Path(root, name)
    d.mkdir(parents=True)
    sha = model_sha or {name_: s for _, s, name_ in ss.M4_MODELS.values()}[model]
    rows = "".join(f"{k},5,5,{0 if k in differ else 1}\n" for k in range(frames)) + ("10,5," if cut else "")
    (d / "wiring.csv").write_text(
        f"# start_time_utc: 2026-11-18T00:00:00Z\n# run_kind: check\n# model: {model}\n# model_sha256: {sha}\n"
        f"# tensors_sha256: {tensors}\n# post_objects_sha256: {json.dumps(objects)}\n"
        "frame,shared_detections,direct_detections,equal\n" + rows, encoding="utf-8", newline="\n")
    return d


def build_objects(data=b"o"):
    """Writes the build's four post-processing objects; returns {path: SHA-256}, as the checks record them."""
    import hashlib
    out = {}
    for i, path in enumerate(ss.M4_POST_OBJECTS.values()):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(data + bytes([i]))
        out[path] = hashlib.sha256(data + bytes([i])).hexdigest()
    return out


def wiring_both(objects, root="results/check"):
    wiring_check(root, "20261118T000000Z_wiring_yolov8n", "yolov8n", objects)
    wiring_check(root, "20261118T000100Z_wiring_yolov10n", "yolov10n", objects)


class InTemp(unittest.TestCase):
    def setUp(self):
        self.here = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.here)
        self.tmp.cleanup()


class Attempts(InTemp):
    def test_the_records_of_both_sides(self):
        r = Path("results/m4")
        board_pass(r, "20261120T010000Z_int8_yolov8n")  # valid
        board_pass(r, "20261120T020000Z_int8_yolov10n", "yolov10n", "2026-11-20T02:00:00Z", valid=0)  # invalid
        board_pass(r, "20261120T030000Z_refused", "yolov10n", "2026-11-20T03:00:00Z", with_pass_csv=False,
                   exit_code=2)  # refused at its start, or set_perf_mode failed: no attempt
        board_pass(r, "20261120T040000Z_reset", "yolov10n", "2026-11-20T04:00:00Z", with_pass_csv=False,
                   board_pass_txt="95")  # a board reset: invalid, ended when the driver stopped waiting
        board_pass(r, "20261120T050000Z_died", "yolov10n", "2026-11-20T05:00:00Z", with_pass_csv=False,
                   exit_code=1)  # NpuContexts' die: invalid
        board_pass(r, "20261120T060000Z_running", "yolov8n", "2026-11-20T06:00:00Z", with_pass_csv=False, pid="7")
        board_pass(r, "20261120T070000Z_check", kind="check", freeze="none")  # not a pass of kind m4
        board_pass(r, "20261120T080000Z_other_freeze", freeze="e4" * 20)
        board_pass(r, "20261120T090000Z_decode", mode="decode")
        fp32_pass(r, "20261120T100000Z_fp32_yolov8n", start="2026-11-20T10:00:00Z")  # valid
        fp32_pass(r, "20261120T110000Z_fp32_refused", start="2026-11-20T11:00:00Z", with_pass_csv=False, exit_code=2)
        fp32_pass(r, "20261120T120000Z_fp32_docker", start="2026-11-20T12:00:00Z", with_pass_csv=False, exit_code=125)
        fp32_pass(r, "20261120T130000Z_fp32_killed", "yolov10n", "2026-11-20T13:00:00Z", 100,
                  with_pass_csv=False)  # pc_run stopped: exit_code none, an invalid attempt
        fp32_pass(r, "20261120T140000Z_coco", tool="tools/m4_coco.py", start="2026-11-20T14:00:00Z")
        fp32_pass(r, "20261120T150000Z_fp32_check", start="2026-11-20T15:00:00Z", kind="check")
        board_pass(r, "x20261120T160000Z_int8_yolov8n", start="2026-11-20T16:00:00Z")  # not a record's name
        (r / "20261120T170000Z_empty").mkdir()
        (r / "20261120T180000Z_bad_json").mkdir()
        (r / "20261120T180000Z_bad_json" / "pc.json").write_text("[1, 2]", encoding="utf-8")

        alive = lambda run: run.get("pid") == "7"  # noqa: E731
        got = [(a.record.name, a.model, a.precision, a.outcome, a.end and text(a.end))
               for a in mo.pass_attempts(r, F4, alive)]
        self.assertEqual(got, [
            ("20261120T010000Z_int8_yolov8n", "yolov8n", "int8", "valid", "2026-11-20T01:05:01Z"),
            ("20261120T020000Z_int8_yolov10n", "yolov10n", "int8", "invalid", "2026-11-20T02:05:01Z"),
            ("20261120T040000Z_reset", "yolov10n", "int8", "invalid", "2026-11-20T04:01:35Z"),
            ("20261120T050000Z_died", "yolov10n", "int8", "invalid", None),
            ("20261120T060000Z_running", "yolov8n", "int8", "running", None),
            ("20261120T100000Z_fp32_yolov8n", "yolov8n", "fp32", "valid", "2026-11-20T11:23:21Z"),
            ("20261120T130000Z_fp32_killed", "yolov10n", "fp32", "invalid", "2026-11-20T13:01:50Z")])
        # Without a way to tell (the PC), a run without its exit code is an invalid one.
        self.assertEqual([a.outcome for a in mo.pass_attempts(r, F4) if a.record.name.endswith("_running")],
                         ["invalid"])
        self.assertEqual(mo.pass_attempts(Path("nothing"), F4), [])

    def test_when_the_passes_are_done(self):
        r = Path("results/m4")
        self.assertEqual(mo.passes_status([]), (None, [
            "the INT8 pass of yolov8n has no record", "the INT8 pass of yolov10n has no record",
            "the FP32 pass of yolov8n has no record", "the FP32 pass of yolov10n has no record"]))
        last = all_passes(r)
        self.assertEqual(mo.passes_status(mo.pass_attempts(r, F4)), (last, []))
        # Only the attempts that started before a time count, and the time is the last end among them.
        done, problems = mo.passes_status(mo.pass_attempts(r, F4), before=utc("2026-11-20T02:00:00Z"))
        self.assertEqual((done, problems), (None, ["the FP32 pass of yolov10n has no record"]))

    def test_an_invalid_pass_is_repeated_once(self):
        r = Path("results/m4")
        all_passes(r)
        import shutil
        shutil.rmtree(r / "20261120T011000Z_int8_yolov10n")
        board_pass(r, "20261120T050000Z_int8_yolov10n", "yolov10n", "2026-11-20T05:00:00Z", valid=0)
        self.assertEqual(mo.passes_status(mo.pass_attempts(r, F4))[1], [
            "the INT8 pass of yolov10n was invalid (20261120T050000Z_int8_yolov10n) and is repeated once (§5)"])
        # Invalid twice: done, reported as missing (§5), when the second ended (or, without its end, started).
        board_pass(r, "20261120T060000Z_int8_yolov10n", "yolov10n", "2026-11-20T06:00:00Z", with_pass_csv=False,
                   exit_code=1)
        self.assertEqual(mo.passes_status(mo.pass_attempts(r, F4)), (utc("2026-11-20T06:00:00Z"), []))
        # A valid repeat is done when it ended.
        shutil.rmtree(r / "20261120T060000Z_int8_yolov10n")
        board_pass(r, "20261120T060000Z_int8_yolov10n", "yolov10n", "2026-11-20T06:00:00Z", seconds=400)
        self.assertEqual(mo.passes_status(mo.pass_attempts(r, F4)), (utc("2026-11-20T06:06:41Z"), []))
        # A run that still runs on the board keeps the passes from being done.
        board_pass(r, "20261120T070000Z_int8_yolov8n", start="2026-11-20T07:00:00Z", with_pass_csv=False)
        attempts = mo.pass_attempts(r, F4, alive=lambda run: True)
        self.assertEqual(mo.passes_status(attempts)[1],
                         ["the INT8 pass of yolov8n is running (20261120T070000Z_int8_yolov8n)"])

    def test_board_alive_without_proc(self):
        if Path("/proc/sys/kernel/random/boot_id").exists():
            self.skipTest("this host has /proc")
        self.assertFalse(mo.board_alive({"pid": "1", "boot_id": "b"}))


class Wiring(InTemp):
    def setUp(self):
        super().setUp()
        self.objects = {path: f"{i:064x}" for i, path in enumerate(ss.M4_POST_OBJECTS.values())}
        self.models = {name: sha for _, sha, name in ss.M4_MODELS.values()}

    def problems(self):
        return mo.wiring_problems(Path("results/check"), self.objects, self.models)

    def test_a_passed_check_for_each_setting(self):
        self.assertEqual(self.problems(), [
            "no passed wiring check of C8 (yolov8n) with this build's post-processing objects is in results/check/ "
            "(M4 §8 step 3, §5)",
            "no passed wiring check of C10 (yolov10n) with this build's post-processing objects is in results/check/ "
            "(M4 §8 step 3, §5)"])
        wiring_check("results/check", "20261118T000000Z_wiring_yolov8n", "yolov8n", self.objects)
        self.assertEqual(len(self.problems()), 1)
        self.assertIn("C10", self.problems()[0])
        wiring_check("results/check", "20261118T000100Z_wiring_yolov10n", "yolov10n", self.objects)
        self.assertEqual(self.problems(), [])

    def test_what_does_not_count(self):
        other = dict(self.objects)
        other[ss.M4_POST_OBJECTS["sha256_pipeline_post_o"]] = "e" * 64  # another build of the shared function
        wiring_check("results/check", "20261118T000000Z_other_objects", "yolov8n", other)
        wiring_check("results/check", "20261118T000100Z_nine_frames", "yolov8n", self.objects, frames=9, cut=True)
        wiring_check("results/check", "20261118T000200Z_other_model", "yolov8n", self.objects, model_sha="d" * 64)
        wiring_check("results/check", "20261118T000300Z_other_tensors", "yolov8n", self.objects, tensors="c" * 64)
        wiring_check("results/check", "x20261118T000400Z_name", "yolov8n", self.objects)
        bad = wiring_check("results/check", "20261118T000500Z_bad_json", "yolov8n", self.objects)
        (bad / "wiring.csv").write_text((bad / "wiring.csv").read_text(encoding="utf-8").replace(
            "# post_objects_sha256: {", "# post_objects_sha256: ["), encoding="utf-8")
        wiring_check("results/check", "20261118T000600Z_wiring_yolov10n", "yolov10n", self.objects)
        self.assertEqual([p.split(" (")[0] for p in self.problems()], ["no passed wiring check of C8"])
        # A cut row after ten whole ones does not undo them.
        wiring_check("results/check", "20261118T000700Z_wiring_yolov8n", "yolov8n", self.objects, cut=True)
        self.assertEqual(self.problems(), [])

    def test_a_frame_that_differs_stops_these_objects(self):
        wiring_both(self.objects)
        wiring_check("results/check", "20261119T000000Z_wiring_yolov10n", "yolov10n", self.objects, frames=4,
                     differ=(3,))  # even a check that stopped early
        self.assertEqual(self.problems(), [
            "the wiring check 20261119T000000Z_wiring_yolov10n of C10 found frames that differ with these objects: "
            "the cause is found and fixed (M4 §5)"])
        self.objects[ss.M4_POST_OBJECTS["sha256_yolov10_post_o"]] = "a" * 64  # a build with other objects: again
        self.assertEqual([p.split(" (")[0] for p in self.problems()], ["no passed wiring check of C8",
                                                                         "no passed wiring check of C10"])


class Gate(InTemp):
    """python3 tools/m4_order.py gate <freeze-m4's commit>, as tools/m4_calib.sh runs it."""

    def gate(self, commit=F4):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = mo.main(["gate", commit])
        return code, out.getvalue() + err.getvalue()

    def env_md(self, freeze=True):
        Path("docs/notes").mkdir(parents=True, exist_ok=True)
        row = f"| [`freeze-m4`](https://x/freeze-m4) | `{F4}` | 2026-11-20T00:00:00Z |\n" if freeze else ""
        Path(ss.ENV_MD).write_text("# Environment\n\n## Registration\n\n| Stage | Tag commit | Release `created_at` |\n"
                                   f"|---|---|---|\n{row}", encoding="utf-8")

    def test_the_gate(self):
        code, out = self.gate()
        self.assertEqual(code, 2)
        self.assertIn("cannot read docs/notes/env.md", out)
        self.env_md(freeze=False)
        self.assertEqual(self.gate(), (1, "m4_order: not starting: freeze-m4 has no row in docs/notes/env.md yet "
                                          "(M4 §4)\n"))
        self.env_md()
        code, out = self.gate("e4" * 20)
        self.assertEqual(code, 1)
        self.assertIn(f"is not freeze-m4's commit, {F4} in docs/notes/env.md", out)
        code, out = self.gate()
        self.assertEqual(code, 1)
        self.assertIn("M4's accuracy passes come before its calib_npu and pipeline sessions (M4 §8 step 5): the INT8 "
                      "pass of yolov8n has no record;", out)
        all_passes(Path("results/m4"))
        code, out = self.gate()
        self.assertEqual(code, 1)
        self.assertIn("this build has no build/CMakeFiles/core.dir/src/core/yolov8_post.cpp.o", out)
        objects = build_objects()
        code, out = self.gate()
        self.assertEqual(code, 1)
        self.assertIn("no passed wiring check of C8 (yolov8n)", out)
        wiring_both(objects)
        self.assertEqual(self.gate(), (0, "m4_order: the four accuracy passes are done, and a wiring check covers "
                                          "this build's objects\n"))
        build_objects(b"rebuilt")  # §5: objects that differ need the check again
        self.assertEqual(self.gate()[0], 1)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(mo.main(["gate"]), 2)


if __name__ == "__main__":
    unittest.main()
