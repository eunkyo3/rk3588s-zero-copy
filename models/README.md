[한국어](README.ko.md)

# Models

Model files are not committed. The benchmark's files are kept in the release [`data-model-yolov8n_i8-04a76611`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/data-model-yolov8n_i8-04a76611). Download them with:

```sh
cd models
for f in yolov8n_i8.rknn yolov8n.onnx SHA256SUMS; do
    curl -fLO https://github.com/eunkyo3/rk3588s-zero-copy/releases/download/data-model-yolov8n_i8-04a76611/$f
done
sha256sum -c SHA256SUMS && rm SHA256SUMS
```

The conversion is repeated with the command below, but it gives a different `.rknn` file (see below):

```sh
tools/model/convert_yolov8n.sh
# Windows (Git Bash + Docker Desktop):
MSYS_NO_PATHCONV=1 ROOT="$(pwd -W)" tools/model/convert_yolov8n.sh
```

This builds a Docker image with rknn-toolkit2 (the same version as the board's librknnrt, 2.3.2) and converts YOLOv8n into `models/yolov8n_i8.rknn`. The base image is pinned by digest and the Python packages by `tools/model/constraints.txt`.

- **Source:** the RKNN-friendly YOLOv8n ONNX export distributed by [rknn_model_zoo](https://github.com/airockchip/rknn_model_zoo) `v2.3.2` (`examples/yolov8`). Its outputs stop before box decoding, so the post-processing runs on the CPU.
- **Conversion:** `examples/yolov8/python/convert.py` from the same tag, target `rk3588`, INT8. `mean 0, std 255` is built into the model, so the input is uint8 RGB NHWC 640×640.
- **Calibration set:** the 20 COCO images that rknn_model_zoo `datasets/COCO/coco_subset_20.txt` lists, at commit `bad6c73` (tag `v2.3.2`). [coco_subset_20.sha256](coco_subset_20.sha256) gives the SHA-256 of the list and of each image, relative to `datasets/COCO` (AMENDMENTS A10).
- **Checksums:** the downloaded ONNX is checked against its pinned SHA-256 before conversion. `models/yolov8n.sha256` is written by the conversion.

| File | SHA-256 |
|---|---|
| `yolov8n.onnx` | `0c8716701f471067932b797eeb67c8e5db47c693c2557c881d7679ec12e21bc5` |
| `yolov8n_i8.rknn` | `04a76611a785802b08703192455994e07bbfe4531821ae7ed35faf78fe1ec1ee` |

**The conversion is not bit-reproducible.** Two conversions in the same pinned image gave two different `.rknn` files (about 1,200 bytes differ), while the ONNX input was identical. The benchmark uses the file with the SHA-256 above. A new conversion gives a different file and must not replace it.

YOLOv8 is published by Ultralytics under AGPL-3.0. Both files are derived from it and are distributed under the same license; the release holds its text.
