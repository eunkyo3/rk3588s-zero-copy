[한국어](preprocess.ko.md)

# Preprocessing: variants B and A, alignment with C (M1-2)

M1-2 adds the CPU-copy variant B and the software-decode reference A next to C (M1-1). It also aligns B's output with C's, and collects the zero-copy evidence of PROTOCOL §11.4. These findings changed the registered design, and each change is recorded in AMENDMENTS:

| Finding | Amendment |
|---|---|
| The OpenCV build is fixed before B's first run | A2 |
| `rknn_inputs_set`, the registered bind of B, leaves the outputs bound with `rknn_set_io_mem` unwritten | A3 |
| RGA3's 3:1 scaler is a fixed 3×3 box one pixel off OpenCV's, and B's luma box now matches it | A4 |
| The registered equivalence criteria need pixel-identical tensors; the criteria are now relative to B's own admissible variants | A5 |
| C's input path makes no CPU pixel copy, but the runtime copies the outputs on the CPU, so the §11.4 perf criterion is not met | A6 |

All measurements were made on the board on 2026-10-30, with default governors, on video ① below.

## Variants

| | Decoder | `prepare` | `bind` |
|---|---|---|---|
| **B** | `h264_rkmpp`, DRM PRIME | `mmap` of the decoder buffer (cached per buffer), `DMA_BUF_IOCTL_SYNC` START/END(READ), OpenCV: resize, then BT.601 limited-range NV12 → RGB into a CPU slot | `memcpy` of the slot into the context's input buffer (A3) |
| **A** | libavcodec `h264` (software) | the same OpenCV code on I420 | as B |
| **C** | `h264_rkmpp`, DRM PRIME | RGA `improcess` into a dma-heap slot | `rknn_set_io_mem` |

All three share the output binding (`rknn_create_mem` + `rknn_set_io_mem`, NCHW INT8) and the rest of the pipeline.

## RGA3's scaler, measured

`tools/rga_kernel` puts single bright pixels (impulses) into a black NV12 frame and scales it the way C does.

- **Luma:** output pixel (c, r) is the mean of input columns 3c+1…3c+3 and rows 3r+1…3r+3, with weight 1/9. OpenCV's `INTER_AREA` averages 3c…3c+2: the same box, one pixel off.
- **Chroma:** each chroma sample is repeated to full resolution, then gets the same box.
- **Last row and column:** treated differently, but each is one line out of 640 or 360.
- **`im_opt_t.interp`:** RGA3 ignores it. Default, linear, cubic and average gave bit-identical tensors. librga has no usage flag that enables it.
- **Color conversion:** on flat frames, RGA's BT.601 limited-range conversion matches OpenCV's within ±1 (mean −0.18).

## Alignment

C against B over the 640×360 band (`tools/compare_prepare` and `tools/rga_probe`):

| B's luma scaling | MAE | Minimum PSNR | Frames |
|---|---|---|---|
| `INTER_LINEAR` | 4.50 | 28.7 dB | 1,000 |
| `INTER_AREA` | 3.61 | — | 100 |
| **`INTER_AREA` with the box moved by one pixel (A4)** | **2.41** | **34.4 dB** | 1,000 |
| A full model of RGA3, with 4:4:4 chroma and a custom conversion (diagnostic only) | 2.09 | 35.3 dB | 100 |

- **What B uses:** only the one-pixel move. It is the same OpenCV call on a shifted region, so B's CPU cost does not change.
- **The full model:** it needs code outside OpenCV and still does not reach a pixel-identical result.
- **The remaining difference:** a mean bias of about −0.6 (C darker), which rounding alone does not explain.

## Equivalence

The registered criteria of §11.1 and §11.2 fail. C's detections against B on 1,000 frames of video ① (greedy matching; the M1-4 tool decides, with Hungarian matching):

| | C vs B | R = B with `INTER_LINEAR` vs B |
|---|---|---|
| Tensor MAE / minimum PSNR | 2.41 / 34.36 dB | 4.54 / 28.52 dB |
| Matched pairs with IoU ≥ 0.95 and confidence difference ≤ 0.02 | 41.8% | 28.4% |
| Frames with equal box counts | 52.4% | 34.7% |
| Largest confidence difference | 0.26 | 0.34 |
| All boxes, against B | +0.44% | −1.04% |

- **C is closer to B than B's two admissible OpenCV variants are to each other.** The INT8 model's confidences move by tenths under input differences this small. So the registered criteria can only be met by pixel-identical tensors.
- **A5 replaces them.** C must differ from B no more than R does, and the total box count must stay within 2%. Under A5, the estimate passes.
- **Per class:** the box counts, against B, are reported:

  | | person | car | bus | truck | traffic light | stop sign | backpack | handbag |
  |---|---|---|---|---|---|---|---|---|
  | C | −0.51% | −0.01% | **+14.7%** | 0.00% | −0.73% | **+72.6%** | −1.36% | −1.03% |
  | R | −2.02% | +0.60% | −1.27% | −3.98% | −0.39% | −93.4% | −0.85% | +2.06% |

  R misses 2% in 4 of 8 classes too. Stop sign flickers around the 0.25 threshold in every variant. Bus differs more for C than for R.

Full output: [preprocess/compare_v1_1000.txt](preprocess/compare_v1_1000.txt).

## M1-2 completion check

One channel of video ①, `file_source`, a 30 s window ([preprocess/check_summary.txt](preprocess/check_summary.txt)):

| | Crashes | Inference rate | Boxes in the same 160 sampled frames |
|---|---|---|---|
| A | 0 | 5.000 fps | 2,541 |
| B | 0 | 5.000 fps | 2,541 |
| C | 0 | 5.000 fps | 2,538 (−0.12%) |

- **Criteria met:** crash 0, 5 ± 0.25 fps, and A/B/C box counts within 2%.
- **A and B agree exactly:** the software decoder and MPP return bit-identical pixels.
- **Process CPU in these check runs:** A 17.5 s, B 5.05 s, C 2.05 s. These are check runs, not measurements: default governors, one channel, one video.

## Zero-copy evidence (PROTOCOL §11.4)

`tools/zero_copy_evidence.sh` collects perf (10 s per run, steady state) and strace, and `tools/zero_copy_evidence.py` summarises them ([preprocess/zero_copy_evidence.txt](preprocess/zero_copy_evidence.txt)). perf resolves `memcpy` inside libc only with `libc6-dbg` matching the running libc. Without it, the copy share reads 0.12% instead of 0.82%.

| Run | Copy samples | Under `prepare`/`bind`/`rknn_run` | Cache maintenance |
|---|---|---|---|
| none, 1 / 4 ch | 2.19% / 4.78% | 0 / 0 | 41.1% / 43.6% |
| **C, 1 / 4 ch** | 2.23% / 3.25% | **0.82% / 1.08%** | 25.6% / 28.4% |
| B, 1 / 4 ch | 2.25% / 4.11% | 0.36% / 0.70% | 4.6% / 8.2% |

- **C's copies, by function:**
  - `prepare`: none.
  - `bind`: 1 and 3 samples, copies of the `rknn_tensor_attr` struct.
  - `rknn_run`: all the rest, `__memcpy_generic` called from `librknnrt.so`, as in B (17 and 120 samples).
  - These copies come from the model's output operators, which convert the NPU's NC1HWC2 outputs to NCHW on the CPU, about 1.2 MB per inference. The native NC1HWC2 binding fails in `rknn_run`, also with `pass_through`.
  - So C's input path has no CPU pixel copy, but the §11.4 rule counts `rknn_run` and fails at 0.5%.
  - A6 records this. The report states it, and every C result carries the label.
- **C − none, total copy share:** +0.04 points (1 channel), −1.53 points (4 channels).
- **strace:**
  - C made no dma-buf `mmap` call.
  - B's preprocessing threads made exactly two `DMA_BUF_IOCTL_SYNC` calls on the same fd per frame.
  - In every variant, the decoder thread and MPP's parser also sync each decoded frame (30 calls per second each). MPP's own cache maintenance on the cached decoder buffers is shared by all variants.
- **Caveats:**
  - With default governors the CPU clock differs between runs, which distorts shares across runs. The cache-maintenance column is the most affected.
  - `librknnrt.so` and OpenCV have no symbols. B's `bind` copy is a tail call and appears without `bind` on its stack.

## Benchmark videos

The project owner chose four candidates. Three are prepared ([preprocess/videos_manifest.tsv](preprocess/videos_manifest.tsv)). The originals are not committed.

| | Source | Creator | License | Original | Prepared |
|---|---|---|---|---|---|
| ① | [Pexels 36330861](https://www.pexels.com/video/urban-crosswalk-scene-with-busy-traffic-36330861/), urban crosswalk | SHOX ART | [Pexels License](https://www.pexels.com/license/) | 3840×2160, 23.98 fps, 39.5 s, BT.709 | 1,186 frames |
| ② | [Pexels 29130409](https://www.pexels.com/video/vibrant-city-street-with-busy-traffic-29130409/), city street | Paul Bill | Pexels License | 3840×2160, 23.98 fps, 25.1 s, untagged | 752 frames |
| ③ | [Pexels 29969146](https://www.pexels.com/video/night-traffic-and-pedestrians-in-istanbul-29969146/), Istanbul at night | Sururi Ballıdağ | Pexels License | 2560×1440, 29.97 fps, 23.8 s, BT.709 | 714 frames |
| ④ | [Pixabay 180386](https://pixabay.com/videos/intersection-cars-pedestrian-night-180386/), Osaka at night | Kanenori | [Pixabay Content License](https://pixabay.com/service/license-summary/) | — | not yet: the site refuses scripted downloads |

- **Video ①** is the only one with at least 1,000 frames, so it is the `file_source` video of §11 and of the tensor set of M1-3.
- **Frame rate:** ① and ② are converted from 23.98 to 30 fps, which repeats about one frame in five.

## Tools

| Tool | Purpose |
|---|---|
| `tools/compare_prepare` | C against B (both interpolations) on N frames: tensor MAE/PSNR, border check, detection agreement, per-class counts, registered and amended verdicts |
| `tools/rga_probe` | C against OpenCV candidates per plane, and the RGA3 model |
| `tools/rga_kernel` | RGA3's impulse response and color conversion on flat frames |
| `tools/zero_copy_evidence.sh` / `.py` | perf and strace evidence of §11.4 |
