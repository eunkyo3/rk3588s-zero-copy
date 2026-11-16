#!/usr/bin/env python3
"""Tests for tools/m4_prep.py (prereg/M4.md §2.2). The tests that decode or write images need OpenCV and
run in the evaluation image; elsewhere they are skipped.

Usage: python3 tests/test_m4_prep.py
  In the evaluation image, with a record (pc.json and the log):
  MSYS_NO_PATHCONV=1 python tools/eval/pc_run.py --kind check --name m4_prep_tests -- tests/test_m4_prep.py
"""
import hashlib
import io
import json
import random
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.append(str(Path(__file__).resolve().parent.parent / "tools"))
import m4_prep as mp  # noqa: E402

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None

REPO = Path(__file__).resolve().parent.parent
PC = {"start_time_utc": "2026-11-20T00:00:00Z", "run_kind": "check", "prereg_v1_commit": "p" * 40,
      "freeze_m1_commit": "f" * 40, "prereg_m4_commit": "8" * 40, "freeze_m4_commit": "none", "amendment": "A13",
      "code_commit": "c" * 40, "code_status": "clean", "eval_image": "rk3588s-zero-copy/m4-eval:1",
      "eval_image_id": "sha256:" + "e" * 64,
      "host": {"cpu_model": "Intel(R) Core(TM) i7-6500U CPU @ 2.50GHz", "ntp_offset_s": "+0.7528",
               "ntp_samples": "1 answered, 2 did not", "w32tm_status": "exit -2147023834"},
      "container_clock_offset_s": "+0.0012"}


class Geometry(unittest.TestCase):
    def test_examples(self):
        self.assertEqual(mp.geometry(640, 480), (1.0, 640, 480, 0, 80))
        self.assertEqual(mp.geometry(427, 640), (1.0, 427, 640, 106, 0))
        self.assertEqual(mp.geometry(500, 375), (1.28, 640, 480, 0, 80))
        self.assertEqual(mp.geometry(333, 500), (1.28, 426, 640, 107, 0))  # 426.24 rounds down
        self.assertEqual(mp.geometry(1000, 1), (0.64, 640, 1, 0, 319))  # 0.64 + 0.5 rounds to 1
        self.assertEqual(mp.geometry(1000, 2)[2], 1)  # 1.28 + 0.5 = 1.78: floor 1

    def test_the_longer_side_is_640_and_the_image_fits(self):
        rng = random.Random(20261116)
        for _ in range(20000):
            w, h = rng.randint(1, 2000), rng.randint(1, 2000)
            r, ws, hs, left, top = mp.geometry(w, h)
            self.assertEqual(max(ws, hs), 640, (w, h))
            self.assertTrue(0 <= left and 0 <= top and left + ws <= 640 and top + hs <= 640, (w, h))
            self.assertEqual((left, top), ((640 - ws) // 2, (640 - hs) // 2))


class Inputs(unittest.TestCase):
    def test_sha256_files(self):
        self.assertEqual(mp.read_sha256_file("ab" * 32 + "  a.zip\n" + "cd" * 32 + " *b.zip\n"),
                         {"a.zip": "ab" * 32, "b.zip": "cd" * 32})
        with self.assertRaises(mp.InputError):
            mp.read_sha256_file("not a line\n")
        quant = mp.quantization_images((REPO / "models/coco_subset_20.sha256").read_text(encoding="utf-8"))
        self.assertEqual(len(quant), mp.QUANTIZATION_IMAGES)
        self.assertIn("000000005001.jpg", quant)
        coco = mp.read_sha256_file((REPO / "tools/eval/coco.sha256").read_text(encoding="utf-8"))
        self.assertEqual(sorted(coco), [mp.ANN_ZIP, mp.VAL_ZIP])

    def test_annotation_images(self):
        image = {"id": 139, "file_name": "000000000139.jpg", "width": 640, "height": 426}
        self.assertEqual(mp.annotation_images({"images": [image]}), {"000000000139.jpg": (139, 640, 426)})
        for bad in ([image, image], [image, dict(image, file_name="000000000140.jpg")],  # a name, an id repeated
                    [dict(image, file_name="000000000140.jpg")], [dict(image, file_name="../x.jpg")]):
            with self.assertRaises(mp.InputError, msg=bad):
                mp.annotation_images({"images": bad})

    def test_only_val2017_jpegs_are_written(self):
        def members(*names, size=4):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                for n in names:
                    zf.writestr(n, b"" if n.endswith("/") else b"j" * size)
            return zipfile.ZipFile(io.BytesIO(buf.getvalue()))

        self.assertEqual(sorted(mp.jpeg_members(members("val2017/", "val2017/000000000139.jpg"))),
                         ["000000000139.jpg"])
        for bad in ("../000000000139.jpg", "val2017/../../x.jpg", "val2017/a/000000000139.jpg", "val2017/x.png",
                    "/etc/000000000139.jpg", "val2017/٠٠٠٠٠٠٠٠٠١٣٩.jpg"):  # Arabic-Indic digits too
            with self.assertRaises(mp.InputError, msg=bad):
                mp.jpeg_members(members("val2017/", bad))
        with mock.patch.object(mp, "MAX_JPEG_BYTES", 3), self.assertRaisesRegex(mp.InputError, "4 bytes"):
            mp.jpeg_members(members("val2017/000000000139.jpg"))

    def test_header(self):
        h = mp.header(PC, "4.11.0 (optimized 1)")
        self.assertEqual(list(h)[:7], ["start_time_utc", "run_kind", "prereg_v1_commit", "freeze_m1_commit",
                                       "prereg_m4_commit", "freeze_m4_commit", "amendment"])
        self.assertEqual((h["start_time_utc"], h["run_kind"], h["cpu_model"], h["eval_image_id"], h["opencv"]),
                         (PC["start_time_utc"], "check", PC["host"]["cpu_model"], PC["eval_image_id"],
                          "4.11.0 (optimized 1)"))
        self.assertTrue(all("\n" not in str(v) for v in h.values()))

    def test_rows_sha256(self):
        a = "# start_time_utc: 1\n# x: y\nimage_id\tw\n139\t640\n"
        self.assertEqual(mp.rows_sha256(a), mp.rows_sha256("# start_time_utc: 2\nimage_id\tw\n139\t640\n"))
        self.assertEqual(mp.rows_sha256(a), hashlib.sha256(b"image_id\tw\n139\t640\n").hexdigest())
        self.assertNotEqual(mp.rows_sha256(a), mp.rows_sha256(a.replace("640", "641")))

    def test_main_takes_its_exit_code_from_passed_and_refuses_other_kinds(self):
        with tempfile.TemporaryDirectory() as t:
            record = Path(t)
            (record / "pc.json").write_text(json.dumps(PC), encoding="utf-8")
            fake_cv2 = mock.MagicMock(__version__="4.11.0")
            fake_cv2.useOptimized.return_value = True
            argv = ["--coco", t, "--out", str(record / "out"), "--record", t]
            for passed, code in ((1, 0), (0, 1)):
                with mock.patch.dict(sys.modules, {"cv2": fake_cv2}), \
                        mock.patch.object(mp, "prepare", return_value=[("passed", passed)]), \
                        redirect_stdout(io.StringIO()):
                    self.assertEqual(mp.main(argv), code)
                csv_text = (record / "m4_inputs.csv").read_text(encoding="utf-8")
                self.assertIn("# opencv: 4.11.0 (optimized 1)\n", csv_text)
                self.assertTrue(csv_text.endswith(f"passed,{passed}\n"))
            (record / "pc.json").write_text(json.dumps(dict(PC, run_kind="m4")), encoding="utf-8")
            with mock.patch.dict(sys.modules, {"cv2": fake_cv2}), redirect_stderr(io.StringIO()) as err:
                self.assertEqual(mp.main(argv), 2)
            self.assertIn("run kind m4", err.getvalue())


def write_coco(t, sizes, extra_jpegs=(), skip=(), garbage=(), shape=None):
    """A small COCO in t/coco: JPEGs of sizes {id: (w, h)} (an id in skip has no JPEG; one in garbage has
    bytes OpenCV cannot decode; shape {id: (w, h)} encodes another size than annotated), extra JPEGs
    without an annotation, and its coco.sha256. Returns (coco, sha file, {name: JPEG bytes})."""
    coco = t / "coco"
    coco.mkdir()
    rng = np.random.default_rng(2)
    jpegs = {}
    with zipfile.ZipFile(coco / mp.VAL_ZIP, "w") as zf:
        zf.writestr("val2017/", b"")
        for image_id, (w, h) in list(sizes.items()) + [(i, (64, 48)) for i in extra_jpegs]:
            if image_id in skip:
                continue
            ew, eh = (shape or {}).get(image_id, (w, h))
            ok, data = cv2.imencode(".jpg", rng.integers(0, 256, (eh, ew, 3), dtype=np.uint8))
            assert ok
            data = b"not a jpeg" if image_id in garbage else data.tobytes()
            jpegs[f"{image_id:012d}.jpg"] = data
            zf.writestr(f"val2017/{image_id:012d}.jpg", data)
    annotations = {"images": [{"id": i, "file_name": f"{i:012d}.jpg", "width": w, "height": h}
                              for i, (w, h) in sorted(sizes.items(), reverse=True)]}
    with zipfile.ZipFile(coco / mp.ANN_ZIP, "w") as zf:
        zf.writestr(mp.ANNOTATIONS, json.dumps(annotations))
    sha = t / "coco.sha256"
    sha.write_text("".join(f"{mp.file_sha256(coco / n)}  {n}\n" for n in (mp.VAL_ZIP, mp.ANN_ZIP)))
    return coco, sha, jpegs


@unittest.skipIf(cv2 is None, "needs OpenCV: run in the evaluation image")
class Images(unittest.TestCase):
    SIZES = {139: (640, 426), 285: (586, 640), 632: (500, 375)}

    def run_prepare(self, t, quant_sha=None, **coco):
        coco_dir, sha, jpegs = write_coco(t, self.SIZES, **coco)
        quant = t / "quant.sha256"
        quant.write_text("0" * 64 + "  coco_subset_20.txt\n"
                         + (quant_sha or hashlib.sha256(jpegs.get("000000000285.jpg", b"")).hexdigest())
                         + "  subset/000000000285.jpg\n")
        record = t / "record"
        record.mkdir(exist_ok=True)
        with mock.patch.object(mp, "QUANTIZATION_IMAGES", 1):
            rows = mp.prepare(coco_dir, t / "inputs", record, mp.header(PC, "x"), sha, quant, images_expected=3)
        return dict(rows), record

    def test_letterbox(self):
        rng = np.random.default_rng(1)
        img = rng.integers(0, 256, (375, 500, 3), dtype=np.uint8)
        canvas, resized = mp.letterbox(img, 500, 375)
        self.assertTrue(resized)
        self.assertEqual(canvas.shape, (640, 640, 3))
        self.assertTrue((canvas[:80] == 114).all() and (canvas[560:] == 114).all())
        np.testing.assert_array_equal(canvas[80:560], cv2.resize(img, (640, 480), interpolation=cv2.INTER_LINEAR))
        img = rng.integers(0, 256, (480, 640, 3), dtype=np.uint8)
        canvas, resized = mp.letterbox(img, 640, 480)
        self.assertFalse(resized)
        np.testing.assert_array_equal(canvas[80:560], img)  # not resized: the same pixels
        self.assertEqual(mp.rgb_sha256(canvas), hashlib.sha256(canvas[:, :, ::-1].copy().tobytes()).hexdigest())

    def test_prepare(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            rows, record = self.run_prepare(t)
            self.assertEqual(rows["passed"], 1, rows)
            # 500×375 is scaled; 640×426 and 586×640 already have 640 as their longer side.
            self.assertEqual((rows["resized"], rows["unresized"], rows["quantization_sha256_equal"]), (1, 2, 1))
            text = (record / "manifest.tsv").read_text()
            self.assertTrue(text.startswith("# start_time_utc: 2026-11-20T00:00:00Z\n# run_kind: check\n"))
            lines = [l for l in text.splitlines() if not l.startswith("#")]
            self.assertEqual(lines[0].split("\t"), mp.MANIFEST_COLUMNS)
            self.assertEqual([int(l.split("\t")[0]) for l in lines[1:]], [139, 285, 632])  # by image id
            row = dict(zip(mp.MANIFEST_COLUMNS, lines[2].split("\t")))
            self.assertEqual([row[k] for k in ("w", "h", "r", "w_scaled", "h_scaled", "left", "top")],
                             ["586", "640", "1.0", "586", "640", "27", "0"])
            png = cv2.imread(str(t / "inputs/000000000285.png"))
            self.assertEqual(mp.rgb_sha256(png), row["rgb_sha256"])
            self.assertEqual((rows["manifest_sha256"], rows["manifest_rows_sha256"]),
                             (mp.file_sha256(record / "manifest.tsv"), mp.rows_sha256(text)))

    def test_what_fails_the_checks(self):
        for coco, key, value in (({"shape": {632: (499, 375)}}, "size_mismatches", 1),
                                 ({"skip": (632,)}, "missing_jpegs", 1),
                                 ({"extra_jpegs": (777,)}, "jpegs_without_annotation", 1),
                                 ({"garbage": (632,)}, "unreadable", 1),
                                 ({"quant_sha": "0" * 64}, "quantization_sha256_equal", 0)):
            with self.subTest(coco=coco), tempfile.TemporaryDirectory() as t:
                rows, _ = self.run_prepare(Path(t), **coco)
                self.assertEqual((rows[key], rows["passed"]), (value, 0), rows)
                self.assertNotEqual(rows["problems"], "none")

    def test_another_zip_stops_before_anything_is_read(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            coco, sha, _ = write_coco(t, self.SIZES)
            sha.write_text("0" * 64 + f"  {mp.VAL_ZIP}\n" + mp.file_sha256(coco / mp.ANN_ZIP) + f"  {mp.ANN_ZIP}\n")
            with self.assertRaisesRegex(mp.InputError, "nothing else is read"):
                mp.prepare(coco, t / "inputs", t, mp.header(PC, "x"), sha, images_expected=3)
            self.assertFalse((coco / "val2017").exists() or (t / "inputs").exists())

    def test_main_refuses_an_existing_output(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "inputs").mkdir()
            with redirect_stderr(io.StringIO()) as err, redirect_stdout(io.StringIO()):
                self.assertEqual(mp.main(["--coco", t, "--out", str(Path(t) / "inputs"), "--record", t]), 2)
            self.assertIn("exists", err.getvalue())


if __name__ == "__main__":
    unittest.main()
