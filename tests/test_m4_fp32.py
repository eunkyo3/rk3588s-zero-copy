#!/usr/bin/env python3
"""Tests for tools/m4_fp32.py (prereg/M4.md §2.2, §2.5), with a fake onnxruntime session. The tests that
decode PNGs need OpenCV and run in the evaluation image; elsewhere they are skipped.

Usage: python3 tests/test_m4_fp32.py
"""
import hashlib
import io
import json
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.append(str(Path(__file__).resolve().parent.parent / "tools"))
import m4_coco as mc  # noqa: E402
import m4_fp32 as mf  # noqa: E402
import m4_prep as mp  # noqa: E402

try:
    import cv2
except ImportError:
    cv2 = None

PC = {"start_time_utc": "2026-11-20T00:00:00Z", "run_kind": "check", "prereg_v1_commit": "p" * 40,
      "freeze_m1_commit": "f" * 40, "prereg_m4_commit": "8" * 40, "freeze_m4_commit": "none", "amendment": "A13",
      "code_commit": "c" * 40, "code_status": "clean", "eval_image": "m4-eval:1", "eval_image_id": "sha256:e",
      "record": "20261120T000000Z_fp32"}


class FakeSession:
    """YOLOv10n's six outputs: one class-5 score of 0.9 at cell 0 of stride 8, and how often it ran."""

    def __init__(self):
        self.inputs = []

    def get_inputs(self):
        return [types.SimpleNamespace(name="images")]

    def run(self, names, feed):
        self.inputs.append(feed["images"])
        out = []
        for g in (80, 40, 20):
            cls = np.zeros((1, 80, g, g), np.float32)
            if g == 80:
                cls[0, 5, 0, 0] = 0.9
            out += [np.zeros((1, 64, g, g), np.float32), cls]
        return out


class Model(unittest.TestCase):
    def test_the_models_are_checked(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            (t / "models").mkdir()
            (t / "models/yolov8n.onnx").write_bytes(b"model")
            sha = hashlib.sha256(b"model").hexdigest()
            (t / "models/yolov8n.sha256").write_text(f"{sha}  yolov8n.onnx\n{'0' * 64}  yolov8n_i8.rknn\nrknn-toolkit2 2.3.2\n")
            with mock.patch.object(mf, "REPO", t), \
                    mock.patch.dict(mf.MODELS, {"yolov8n": ("models/yolov8n.onnx", "models/yolov8n.sha256", sha[:8])}):
                self.assertEqual(mf.model_sha256("yolov8n"), (t / "models/yolov8n.onnx", sha))
                (t / "models/yolov8n.onnx").write_bytes(b"other")
                with self.assertRaisesRegex(mf.InputError, "M4 §1"):
                    mf.model_sha256("yolov8n")

    def test_the_real_models_match_m4s_prefixes(self):
        for model, (path, sha_file, prefix) in mf.MODELS.items():
            text = (mf.REPO / sha_file).read_text(encoding="utf-8")
            self.assertIn(f"{prefix}", text.split(Path(path).name)[0], model)
            if (mf.REPO / path).exists():
                self.assertEqual(mf.model_sha256(model)[1][:8], prefix)


@unittest.skipIf(cv2 is None, "needs OpenCV: run in the evaluation image")
class Pass(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        rng = np.random.default_rng(3)
        (self.t / "inputs").mkdir()
        rows = []
        for image_id in (139, 285, 632):
            bgr = rng.integers(0, 256, (640, 640, 3), dtype=np.uint8)
            cv2.imwrite(str(self.t / f"inputs/{image_id:012d}.png"), bgr)
            rgb = hashlib.sha256(np.ascontiguousarray(bgr[:, :, ::-1]).tobytes()).hexdigest()
            rows.append([image_id, f"{image_id:012d}.jpg", 640, 480, "1.0", 640, 480, 0, 80, rgb, "png"])
        mc.write_table(self.t / "manifest.tsv", {"start_time_utc": "x"}, mp.MANIFEST_COLUMNS, rows)
        (self.t / "record").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, kind="check", limit=None, out="out", fake=None):
        (self.t / "record/pc.json").write_text(json.dumps(dict(PC, run_kind=kind)))
        fake = fake or FakeSession()
        argv = ["--model", "yolov10n", "--manifest", str(self.t / "manifest.tsv"), "--inputs",
                str(self.t / "inputs"), "--out", str(self.t / out), "--record", str(self.t / "record")]
        argv += ["--limit", str(limit)] if limit is not None else []
        with mock.patch.object(mf, "model_sha256", return_value=(Path("m.onnx"), "4a925bcb" + "0" * 56)), \
                mock.patch.object(mf, "session", return_value=(fake, "1.23.2")), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            code = mf.main(argv)
        return code, fake, err.getvalue()

    def test_a_pass(self):
        code, fake, err = self.run_main(limit=2)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(fake.inputs), 2)  # the first 2 of the manifest
        x = fake.inputs[0]
        self.assertEqual((x.dtype, x.shape), (np.float32, (1, 3, 640, 640)))
        bgr = cv2.imread(str(self.t / "inputs/000000000139.png"))
        np.testing.assert_array_equal(x[0, 0], bgr[:, :, 2].astype(np.float32) / np.float32(255))  # R first
        header, dets = mc.read_table(self.t / "out/detections.tsv", mc.DET_COLUMNS)
        self.assertEqual([d[:4] for d in dets], [["139", "0", "5", "0.899999976"], ["285", "0", "5", "0.899999976"]])
        self.assertEqual((header["pass"], header["precision"], header["model_sha256"][:8], header["record"]),
                         ("fp32_yolov10n", "fp32", "4a925bcb", PC["record"]))
        _, images = mc.read_table(self.t / "out/images.tsv", mc.IMAGE_COLUMNS)
        self.assertEqual(images, [["139", "1", "1", "0"], ["285", "1", "1", "0"]])
        result = (self.t / "record/pass.csv").read_text()
        self.assertIn("\nvalid,1\n", result)
        self.assertIn(f"sha256_detections_tsv,{mp.file_sha256(self.t / 'out/detections.tsv')}\n", result)

    def test_input_bytes_other_than_the_manifests_invalidate_the_pass(self):
        cv2.imwrite(str(self.t / "inputs/000000000285.png"), np.zeros((640, 640, 3), np.uint8))
        code, fake, _ = self.run_main()
        self.assertEqual((code, len(fake.inputs)), (1, 1))  # it stops at the second image
        result = (self.t / "record/pass.csv").read_text()
        self.assertIn("valid,0", result)
        self.assertIn("000000000285.png's RGB bytes are not the manifest's", result)

    def test_any_error_once_started_invalidates_the_pass_with_its_reason(self):
        class Failing(FakeSession):
            def run(self, names, feed):
                if self.inputs:
                    raise RuntimeError("onnxruntime failed")
                return super().run(names, feed)

        code, fake, _ = self.run_main(fake=Failing())
        self.assertEqual((code, len(fake.inputs)), (1, 1))
        result = (self.t / "record/pass.csv").read_text()
        self.assertIn("valid,0", result)
        self.assertIn("reason,RuntimeError: onnxruntime failed", result)

    def test_what_stops_it_from_starting(self):
        code, _, err = self.run_main(kind="m4", limit=2)
        self.assertEqual(code, 2)
        self.assertIn("--limit is for a check", err)
        for limit in (0, -1):
            code, _, err = self.run_main(limit=limit)
            self.assertEqual(code, 2)
            self.assertIn("at least 1", err)
            self.assertFalse((self.t / "out").exists())
        (self.t / "exists").mkdir()
        code, _, err = self.run_main(out="exists")
        self.assertEqual(code, 2)
        self.assertIn("exists", err)


if __name__ == "__main__":
    unittest.main()
