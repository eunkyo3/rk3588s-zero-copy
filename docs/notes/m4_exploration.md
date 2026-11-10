[한국어](m4_exploration.ko.md)

# Exploration before M4's plan

These runs are not registered and are not part of M4's results. They were made on 2026-11-10, before M4's plan, which has no hypotheses. The plan's post-processing rule and its mAP settings were chosen after seeing them, and this note is the record of what was seen.

| Question | How | Result | For M4 |
|---|---|---|---|
| Does YOLOv10n convert with the pinned toolkit? | `tools/model/convert_yolov10n.sh` | Yes. The INT8 model is `6a6442e1…`. The toolkit warned about outlier weights in the class head. | M4's model ([`data-model-yolov10n_i8-6a6442e1`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/data-model-yolov10n_i8-6a6442e1)) |
| What does the INT8 model output? | `m4_probe` | Six int8 outputs. The class scores top out at 0.51, 0.89 and 0.97 for strides 8, 16 and 32. | Recorded. The mAP will show what it costs. |
| How close are INT8 and FP32? | `m4_probe`, `m4_explore.py compare` | On 21 images at conf 0.25: 195 detections matched, 13 only in FP32, 11 only in INT8. | Recorded. 21 images do not estimate accuracy. |
| Does it need NMS? | the same | Among 208 FP32 detections, 11 same-class pairs overlap with IoU > 0.45. | No NMS, as YOLOv10 is meant to run |
| Which post-processing? | the zoo's code, `yolov10_post` | The zoo's C++ demo misreads boxes when one cell has two classes. YOLOv10's own rule, ported, equals the zoo's Python. | YOLOv10's rule, ties in (branch, cell, class) order |
| What does it cost? | `m4_probe` | 0.084 ms of CPU per call at conf 0.25, against 0.361 ms for M1's `yolov8_post` | Recorded |

`m4_probe` ran as root on the board with the clocks fixed (`tools/set_perf_mode.sh`), on NPU core 0 with M1's bindings, with librknnrt 2.3.2. The runs are listed [at the end](#runs).

## 1. Conversion

`tools/model/convert_yolov10n.sh` ran on 2026-11-10 on this project's Windows PC. The container's memory was limited to 2 GB, and samples every 2–4 s saw at most about 0.5 GB.

- **Checks before converting:** all four passed. The host is x86_64, the image's `pip freeze` equals `tools/model/constraints.txt`, the 20 calibration images match `models/coco_subset_20.sha256`, and the ONNX file is `4a925bcb…`.
- **Warning:** the toolkit reported outlier weights in two layers of the class head, `model.23.one2one_cv3.{0,1}.1.1.conv.weight`. They are about −11, against an absolute mean of 0.5–0.6, and may cost quantization accuracy.
- **Files:** in the release [`data-model-yolov10n_i8-6a6442e1`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/data-model-yolov10n_i8-6a6442e1), with the checksums in [models/README.md](../../models/README.md#yolov10n-m4).

## 2. The model's outputs

From `m4_probe.csv`. Each output is int8, NCHW.

| Output | Stride | Content | zp | Scale | Largest value |
|---|---|---|---|---|---|
| 0 | 8 | box, 4 sides × 16 DFL bins | −38 | 0.114573859 | 18.90 |
| 1 | 8 | 80 class scores | −128 | 0.0020011724 | 0.510 |
| 2 | 16 | box | −57 | 0.095044136 | 17.49 |
| 3 | 16 | class scores | −128 | 0.00350535568 | 0.894 |
| 4 | 32 | box | −58 | 0.0612528063 | 11.33 |
| 5 | 32 | class scores | −128 | 0.00379228638 | 0.967 |

- **There is no score-sum output,** unlike the YOLOv8n export. Nothing lets the post-processing skip a cell without reading its 80 scores.
- **The class scores clip.** A small object, detected at stride 8, cannot score above 0.510, and nothing scores above 0.967. The ranges come from the 20 calibration images. On the 21 images below, 6, 2 and 1 class scores sat at the top value (+127) for strides 8, 16 and 32. Clipping does not change which scores pass 0.25, but it changes their order, which mAP uses.
- **The smallest score above zero** is one int8 step: 0.0020, 0.0035 and 0.0038. At mAP's conf 0.001 every such score is a candidate.

## 3. INT8 against FP32

**Method.**
- **Images:** the zoo's `bus.jpg` and the 20 calibration images. The calibration images are the ones the quantization was fitted to, so they favour INT8. They are also COCO val2017 images (all 20 are at `images.cocodataset.org/val2017/`), so they are among the images M4's mAP evaluates.
- **Inputs:** each image letterboxed to 640×640 with pad 0, as the zoo's `yolov10.py` does, and written as PNG. The board ran on these pixels, and ONNX FP32 on RGB/255 of the same pixels (onnxruntime 1.23.2 in the converter image).
- **Post-processing:** the zoo's `post_process_yolov10` at conf 0.25 on both, the INT8 outputs dequantized with their exact scales.
- **Matching:** a detection is matched when the other side has one of the same class with IoU ≥ 0.5.

| | Value |
|---|---|
| Detections, FP32 / INT8 | 208 / 206 |
| Matched | 195 |
| Only in FP32 / only in INT8 | 13 / 11 |
| IoU of the matched, minimum / median | 0.652 / 0.986 |
| Score difference INT8 − FP32, median (range) | −0.014 (−0.347 to +0.340) |
| Largest \|INT8 − FP32\| per output | 4.82, 0.141, 4.65, 0.537, 2.70, 0.595 |

Of the 24 detections on one side only, 22 score between 0.25 and 0.36 (`one_side_scores` in `m4_explore.json`), so they cross the threshold. The score differences reach ±0.35, against scores that go up to 0.5–0.97.

**NMS.** YOLOv10's one-to-one head is trained to give one box per object.
- **Overlaps:** among the 208 FP32 detections, 11 same-class pairs have IoU > 0.45 (9 pairs among the 206 INT8 ones). An NMS at 0.45 would remove one of each. Whether they are duplicates or separate objects close together was not checked.
- **Two classes at one cell:** 2 pairs of detections have identical boxes, which happens when they come from one cell (1 pair in INT8).

## 4. Post-processing

**The zoo's two versions** (rknn_model_zoo v2.3.2, `examples/yolov10`):
- **C++ demo** (`cpp/postprocess.cc`): keeps every (cell, class) pair above 0.25, sorts by score and keeps 128, without NMS. Its `process_i8` advances the box offset inside the class loop ([lines 323–334](https://github.com/airockchip/rknn_model_zoo/blob/v2.3.2/examples/yolov10/cpp/postprocess.cc#L323-L334)). A second class above the threshold at the same cell therefore decodes the wrong bins. Section 3 found such cells, so the bug shows in real outputs.
- **Python demo** (`python/yolov10.py`, `post_process_yolov10`): YOLOv10's own rule. It takes the 300 cells with the highest best score, then the 300 highest (cell, class) pairs among them, then those above the threshold.

**Chosen on 2026-11-10** (the user, from these options):
- **The rule:** YOLOv10's own.
- **The port:** `src/core/yolov10_post` (PR #43). It keeps every pair above the threshold and the 300 best by score, each with its own cell's box. Equal scores are ordered by branch, then cell, then class. Pairs strictly above the 300th score are the same as the zoo's Python.
- **mAP settings:** Ultralytics' val defaults (conf 0.001, at most 300 detections, and class-wise NMS at IoU 0.7 for YOLOv8n). The pipeline keeps conf 0.25, as in M1.

**Check of the port.** `yolov10_post` (the probe's `detections.csv`) was compared with `post_process_yolov10` on the same INT8 outputs. A detection matches when its class is equal, its score is within 1e-6 and its box within 1e-3 px.

| conf | Detections | Matched | Differing, all tied at the 300th score | Other differences | Largest box difference |
|---|---|---|---|---|---|
| 0.25 | 206 | 206 | 0 | 0 | 6.2e-5 px |
| 0.001 | 6,300 | 3,154 | 3,146 | 0 | 9.2e-5 px |

At conf 0.001 every image fills its 300. The 300th score is among the smallest an image has, from 0.0035 to 0.022, and about half of the kept pairs are tied with it. Which of them are kept depends only on the order of ties: here (branch, cell, class), and in the zoo's Python, torch's `topk`. Above that score, from 20 to 286 pairs per image (median 121), the two are the same. pycocotools limits the detections per image and category (100 by default), not per image, so nearly all of the 300 kept pairs enter the evaluation, the tied ones included. Which tied pairs survive the cut can therefore move the mAP slightly, and the plan fixes the order.

**Unit tests.** `tests/test_core.cpp` checks the rule against a plain implementation of it on 60 random cases, including ties and the model's three class scales, and the edge cases (`max_det` ≤ 0, two classes at one cell, the strict threshold).

## 5. Cost

From `m4_probe.csv`: 5 runs per image and conf, `yolov10_post` timed in thread CPU right after each `rknn_run`, on 21 images.

| conf | Median | p90 | Max |
|---|---|---|---|
| 0.25 (pipeline) | 0.084 ms | 0.099 ms | 0.110 ms |
| 0.001 (mAP) | 14.2 ms | 14.5 ms | 14.7 ms |

- **Against YOLOv8n:** M1's `yolov8_post` took 0.361 ms per call on video frames ([m2_exploration.md](m2_exploration.md#3-faster-post-processing-post_probe)), and M2's NEON version 0.129 ms.
- **What the time covers:** at conf 0.25 it includes the per-cell maximum over the 80 class planes, which lets the scan skip cells without a candidate. The parts were not timed separately.
- **The inputs differ** (still images here, video frames there), so this compares orders of magnitude only.

## Runs

The run is in `results/check/`, with run kind `check`. Its raw outputs (`*.i8`, 25 MB) are not committed; `m4_probe` writes them again.

| Run | What | Code |
|---|---|---|
| `20261110T073848Z_m4_probe` | the 21 images: outputs, checks, `yolov10_post` detections and times; `m4_explore.json` is the comparison, written by `tools/m4_explore.py` as committed with this note | `d0989f6` |

- **A first pass** earlier the same day used scratch versions of these tools, with the same inputs and model. It gave the same numbers in sections 2 to 4, and the choices of section 4 were made after it. Its times, wall clock of repeated calls on the same outputs, were not kept.
- **The conversion** is recorded by `models/yolov10n.sha256`, not by a run here.

**Reproducing.**
1. In the converter image (`tools/model/Dockerfile`), with rknn_model_zoo cloned at `v2.3.2` into a directory named `rknn_model_zoo`, run `python tools/m4_explore.py reference <zoo> models/yolov10n.onnx <work>`.
2. On the board, as root with the clocks fixed, run `build/m4_probe models/yolov10n_i8.rknn <out> 5 <work>/*.png`.
3. Back in the image, run `python tools/m4_explore.py compare <zoo> <work> <out>`.
