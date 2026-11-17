#!/usr/bin/env python3
"""Tests for tools/m4_coco.py (prereg/M4.md §2.3 "To COCO" and "Evaluation"). The tests that evaluate need
pycocotools and run in the evaluation image; elsewhere they are skipped.

Usage: python3 tests/test_m4_coco.py
  In the evaluation image, with a record:
  MSYS_NO_PATHCONV=1 python tools/eval/pc_run.py --kind check --name m4_coco_tests -- tests/test_m4_coco.py
"""
import io
import json
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.append(str(Path(__file__).resolve().parent.parent / "tools"))
import m4_coco as mc  # noqa: E402
import m4_prep as mp  # noqa: E402
import m4_rules as mr  # noqa: E402

try:
    import pycocotools  # noqa: F401
except ImportError:
    pycocotools = None

PC = {"start_time_utc": "2026-11-20T00:00:00Z", "run_kind": "check", "prereg_v1_commit": "p" * 40,
      "freeze_m1_commit": "f" * 40, "prereg_m4_commit": "8" * 40, "freeze_m4_commit": "none", "amendment": "A13",
      "code_commit": "c" * 40, "code_status": "clean", "eval_image": "m4-eval:1", "eval_image_id": "sha256:e",
      "record": "20261120T000000Z_x"}
# image 1: 500×375, scaled by 1.28, top 80; image 2: 640×480, unscaled, top 80
MANIFEST = {1: {"image_id": "1", "file_name": "000000000001.jpg", "w": "500", "h": "375", "r": "1.28",
                "w_scaled": "640", "h_scaled": "480", "left": "0", "top": "80"},
            2: {"image_id": "2", "file_name": "000000000002.jpg", "w": "640", "h": "480", "r": "1.0",
                "w_scaled": "640", "h_scaled": "480", "left": "0", "top": "80"}}


class Tables(unittest.TestCase):
    def test_write_and_read(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "d.tsv"
            mc.write_table(p, {"pass": "fp32_yolov8n"}, mc.DET_COLUMNS, [[1, 0, 3, "0.5", "1", "2", "3", "4"]])
            self.assertEqual(p.read_bytes(), b"# pass: fp32_yolov8n\n" + "\t".join(mc.DET_COLUMNS).encode()
                             + b"\n1\t0\t3\t0.5\t1\t2\t3\t4\n")
            self.assertEqual(mc.read_table(p, mc.DET_COLUMNS),
                             ({"pass": "fp32_yolov8n"}, [["1", "0", "3", "0.5", "1", "2", "3", "4"]]))
            with self.assertRaises(mc.InputError):
                mc.read_table(p, mc.IMAGE_COLUMNS)

    def test_detection_rows(self):
        r = mr.Result(np.array([3]), np.array([0.1], np.float32), np.array([[1.5, 2, 640, 0.25]], np.float32), 7,
                      False)
        self.assertEqual(mc.detection_rows(9, r), [[9, 0, 3, "0.100000001", "1.5", "2", "640", "0.25"]])


class Conversion(unittest.TestCase):
    def convert(self, *dets):
        return json.loads(mc.to_coco([[str(v) for v in d] for d in dets], MANIFEST, list(range(1, 91, 2))))

    def test_back_to_the_image(self):
        out = self.convert((1, 0, 0, "0.75", "0", "80", "640", "560"), (2, 0, 44, "0.5", "10", "90", "20", "100"))
        self.assertEqual(out[0], {"image_id": 1, "category_id": 1, "bbox": [0, 0, 500, 375], "score": 0.75})
        self.assertEqual(out[1], {"image_id": 2, "category_id": 89, "bbox": [10, 10, 10, 10], "score": 0.5})

    def test_clip_and_scale(self):
        # x: (−12.8 − 0) / 1.28 → −10, clipped to 0; (128 − 0) / 1.28 = 100. y: (60 − 80) / 1.28 → 0; (208 − 80) / 1.28 = 100.
        out = self.convert((1, 0, 2, "0.3", "-12.8", "60", "128", "208"))[0]
        self.assertEqual(out["bbox"], [0, 0, 100, 100])
        self.assertEqual(out["category_id"], 5)  # class 2: the third smallest id

    def test_values_are_float32_with_9_digits(self):
        text = mc.to_coco([["1", "0", "0", "0.1", "1", "81", "2", "82"]], MANIFEST, list(range(80)))
        self.assertIn('"score":0.100000001', text)
        self.assertIn('"bbox":[0.78125,0.78125,0.78125,0.78125]', text)


@unittest.skipIf(pycocotools is None, "needs pycocotools: run in the evaluation image")
class Evaluation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        cats = [{"id": i, "name": str(i)} for i in range(1, 91, 2)][:80]
        anns = [{"id": 1, "image_id": 1, "category_id": 1, "bbox": [10, 10, 100, 80], "area": 8000, "iscrowd": 0},
                {"id": 2, "image_id": 2, "category_id": 3, "bbox": [200, 100, 50, 50], "area": 2500, "iscrowd": 0}]
        images = [{"id": 1, "file_name": "000000000001.jpg", "width": 500, "height": 375},
                  {"id": 2, "file_name": "000000000002.jpg", "width": 640, "height": 480}]
        self.zip = self.t / mp.ANN_ZIP
        with zipfile.ZipFile(self.zip, "w") as zf:
            zf.writestr(mp.ANNOTATIONS, json.dumps({"images": images, "annotations": anns, "categories": cats}))
        (self.t / "coco.sha256").write_text(f"{'0' * 64}  {mp.VAL_ZIP}\n{mp.file_sha256(self.zip)}  {mp.ANN_ZIP}\n")
        (self.t / "quant.sha256").write_text("0" * 64 + "  coco_subset_20.txt\n" + "1" * 64 + "  subset/000000000002.jpg\n")
        rows = [[f"{v}" for v in MANIFEST[i].values()] + ["x", "y"] for i in (1, 2)]
        mc.write_table(self.t / "manifest.tsv", {}, mp.MANIFEST_COLUMNS, rows)
        (self.t / "record").mkdir()
        (self.t / "record/pc.json").write_text(json.dumps(PC))

    def tearDown(self):
        self.tmp.cleanup()

    def pass_dir(self, dets):
        d = self.t / "pass"
        d.mkdir(exist_ok=True)
        mc.write_table(d / "detections.tsv", {"pass": "fp32_yolov8n", "model": "yolov8n"}, mc.DET_COLUMNS, dets)
        per_image = [[i, 5, sum(1 for x in dets if x[0] == i), 0] for i in (1, 2)]
        mc.write_table(d / "images.tsv", {}, mc.IMAGE_COLUMNS, per_image)
        return d

    def run_main(self, dets):
        d = self.pass_dir(dets)
        argv = ["--dets", str(d), "--manifest", str(self.t / "manifest.tsv"), "--annotations", str(self.zip),
                "--record", str(self.t / "record")]
        with mock.patch.object(mc, "COCO_SHA256", self.t / "coco.sha256"), \
                mock.patch.object(mc, "QUANTIZATION", self.t / "quant.sha256"), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            code = mc.main(argv)
        rows = {}
        if (self.t / "record/coco_eval.csv").exists():
            rows = {k: v for k, _, v in (l.partition(",") for l in
                                         (self.t / "record/coco_eval.csv").read_text().splitlines() if "," in l)}
        return code, rows, err.getvalue()

    def test_perfect_detections_give_ap_1(self):
        # The ground truth boxes, in model input coordinates: image 1 scaled by 1.28 with top 80.
        dets = [[1, 0, 0, "0.9", "12.8", "92.8", "140.8", "195.2"], [2, 0, 1, "0.8", "200", "180", "250", "230"]]
        code, rows, err = self.run_main(dets)
        self.assertEqual(code, 0, err)
        self.assertEqual((rows["all_ap"], rows["all_ap50"], rows["images"]), ("1.000000", "1.000000", "2"))
        self.assertEqual(rows["images_without_quantization"], "1")  # image 2 is a quantization image here
        self.assertEqual(rows["without_quantization_ap"], "1.000000")
        self.assertTrue((self.t / "pass/coco_results.json").exists())

    def test_a_wrong_class_gives_ap_0_where_it_counts(self):
        dets = [[1, 0, 0, "0.9", "12.8", "92.8", "140.8", "195.2"], [2, 0, 5, "0.8", "200", "180", "250", "230"]]
        code, rows, _ = self.run_main(dets)
        self.assertEqual(code, 0)
        self.assertEqual(rows["without_quantization_ap"], "1.000000")  # image 1 alone
        self.assertNotEqual(rows["all_ap"], "1.000000")

    def test_another_annotations_file_is_refused(self):
        (self.t / "coco.sha256").write_text(f"{'0' * 64}  {mp.VAL_ZIP}\n{'0' * 64}  {mp.ANN_ZIP}\n")
        code, _, err = self.run_main([[1, 0, 0, "0.9", "1", "1", "2", "2"]])
        self.assertEqual(code, 2)
        self.assertIn("is not the file", err)


if __name__ == "__main__":
    unittest.main()
