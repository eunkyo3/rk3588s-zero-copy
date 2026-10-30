[한국어](preprocess.ko.md)

# Preprocessing: variants B and A, alignment with C (M1-2)

M1-2 adds the CPU-copy variant B and the software-decode reference A next to C (M1-1). It also aligns B's output with C's, and collects the zero-copy evidence of PROTOCOL §11.4. These findings changed the registered design, and each change is recorded in AMENDMENTS:

| Finding | Amendment |
|---|---|
| The OpenCV build is fixed before B's first run | A2 |
| `rknn_inputs_set`, the registered bind of B, leaves the outputs bound with `rknn_set_io_mem` unwritten | A3 |
| RGA3's 3:1 scaler is a fixed 3×3 box one pixel off OpenCV's, and B's luma box now matches it | A4 |
| The registered equivalence criteria need pixel-identical tensors; the criteria are now relative to B's own admissible variants | A5 |
| C's input path makes no CPU pixel copy, but small copies inside the NPU runtime exceed the §11.4 perf threshold | A6 |

All measurements were made on the board on 2026-10-30, with default governors, on video ① below. The outputs in [preprocess/](preprocess/) come from the code of this change.

## Variants

| | Decoder | `prepare` | `bind` |
|---|---|---|---|
| **B** | `h264_rkmpp`, DRM PRIME | `mmap` of the decoder buffer (cached per buffer), `DMA_BUF_IOCTL_SYNC` START/END(READ), OpenCV: resize, then BT.601 limited-range NV12 → RGB into a CPU slot | `memcpy` of the slot into the context's input buffer (A3) |
| **A** | libavcodec `h264` (software) | the same OpenCV code on I420 | as B |
| **C** | `h264_rkmpp`, DRM PRIME | RGA `improcess` into a dma-heap slot | `rknn_set_io_mem` |

All three share the output binding (`rknn_create_mem` + `rknn_set_io_mem`, NCHW INT8) and the rest of the pipeline.

`tools/inputs_set_probe` shows why B does not use `rknn_inputs_set` ([preprocess/inputs_set_probe.txt](preprocess/inputs_set_probe.txt)). After `rknn_inputs_set`, the bound outputs stay zero, also when they are bound again. Post-processing then finds 370 boxes in the zeros, against 17 from `rknn_outputs_get`.

## RGA3's scaler, measured

`tools/rga_kernel` puts single bright pixels (impulses) into a black NV12 frame and scales it the way C does ([preprocess/rga_kernel.txt](preprocess/rga_kernel.txt)).

- **Luma:** output pixel (c, r) is the mean of input columns 3c+1…3c+3 and rows 3r+1…3r+3, with weight 1/9. OpenCV's `INTER_AREA` averages 3c…3c+2: the same box, one pixel off.
- **Chroma:** each chroma sample is repeated to full resolution, then gets the same box.
- **Last row and column:**
  - The last output column and row give the frame's last column or row a weight of about 3/9. B repeats the edge, which gives 2/9. Each is one line of 640 or 360.
  - Decoder frames have 1,088 rows in memory for 1,080. RGA3 does not read the padding rows.
- **`im_opt_t.interp`:** RGA3 ignores it. On a noise frame, linear, cubic and average gave output byte-identical to the default. This holds for bare values and for `IM_INTERP(h, v)`, which sets librga's apply flags.
- **Color conversion:** on flat frames, RGA's BT.601 limited-range conversion matches OpenCV's within ±1 (mean −0.18).

## Alignment

C against B over the 640×360 band, on the 1,000 frames of video ① (`tools/compare_prepare` and `tools/rga_probe`, [preprocess/rga_probe_1000.txt](preprocess/rga_probe_1000.txt)):

| B's luma scaling | MAE | Minimum PSNR |
|---|---|---|
| `INTER_LINEAR` | 4.50 | 28.7 dB |
| `INTER_AREA` | 3.78 | — |
| **`INTER_AREA` with the box moved by one pixel (A4)** | **2.41** | **34.4 dB** |
| A full model of RGA3, with 4:4:4 chroma, a custom conversion and truncated averages (diagnostic only) | 2.04 | 35.3 dB |

- **What B uses:** only the one-pixel move. It is the same OpenCV call on a shifted region, so B's CPU cost does not change.
- **The full model:** it needs code outside OpenCV, and it still misses the registered MAE of 2.
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
- **Not an independent test:** the A5 criteria were chosen after these results were known, and M1-4 checks the same video.
- **Per class:** the box counts against B, for the 8 classes with at least 100 boxes in B:

  | | person | car | bus | truck | traffic light | stop sign | backpack | handbag |
  |---|---|---|---|---|---|---|---|---|
  | C | −0.51% | −0.01% | **+14.65%** | 0.00% | −0.73% | **+72.64%** | −1.36% | −1.03% |
  | R | −2.02% | +0.60% | −1.27% | −3.98% | −0.39% | −93.40% | −0.85% | +2.06% |

  - R misses 2% in 4 of these 8 classes too.
  - Stop sign flickers around the 0.25 threshold in every variant.
  - Bus differs more for C than for R.

Full output: [preprocess/compare_v1_1000.txt](preprocess/compare_v1_1000.txt).

## M1-2 completion check

One channel of video ①, `file_source`, a 30 s window ([preprocess/check_summary.txt](preprocess/check_summary.txt)):

| | Crashes | Inference rate | Boxes in the same 160 sampled frames |
|---|---|---|---|
| A | 0 | 5.000 fps | 2,541 |
| B | 0 | 5.000 fps | 2,541 |
| C | 0 | 5.000 fps | 2,538 (−0.12%) |

- **Criteria met:** crash 0, 5 ± 0.25 fps, and A/B/C box counts within 2%.
- **A and B find the same number of boxes.** That is expected: H.264 decoding is bit-exact, so the software decoder and MPP should return the same pixels.
- **Process CPU in these check runs:** A 17.4 s, B 4.90 s, C 2.03 s. These are check runs, not measurements: default governors, one channel, one video.

## Zero-copy evidence (PROTOCOL §11.4)

`tools/zero_copy_evidence.sh` collects perf (10 s per run, steady state) and strace, and `tools/zero_copy_evidence.py` summarises them ([preprocess/zero_copy_evidence.txt](preprocess/zero_copy_evidence.txt)).

- **perf needs two things.** It resolves `memcpy` inside libc only with `libc6-dbg` matching the running libc. Without it, the copies inside libc go unnamed.
- **The build** uses `-fno-omit-frame-pointer -fno-optimize-sibling-calls`, so a copy's caller stays on the stack. Without the second flag, B's `bind` copy was a tail call and appeared without `bind` on its stack.

Copy samples with `prepare`, `bind` or `rknn_run` on the stack, by the innermost of the three, at 1 / 4 channels:

| Run | All copy samples | Under the three | `prepare` | `bind` | `rknn_run` | Cache maintenance |
|---|---|---|---|---|---|---|
| none | 85 / 344 (4.09% / 5.05%) | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 43.9% / 43.4% |
| **C** | 118 / 419 (3.13% / 3.44%) | **39 / 174 (1.04% / 1.43%)** | 0 / 1 | 0 / 2 | 39 / 171 | 28.4% / 26.2% |
| B | 140 / 740 (2.49% / 4.21%) | 107 / 587 (1.91% / 3.34%) | 0 / 1 | 78 / 473 | 29 / 113 | 8.6% / 9.5% |

- **C's copies:**
  - `prepare`, 1 sample: the kernel copying an `fstat` result, the per-buffer cache key.
  - `bind`, 2 samples: a copy of the `rknn_tensor_attr` struct in `bind` itself, and a string copy in libstdc++ inside `rknn_set_io_mem`.
  - `rknn_run`, the rest: `__memcpy_generic` called from `librknnrt.so`, and kernel user-copies in the runtime's `ioctl` calls.
- **B, the positive control:** its input copy shows under `bind`. Its `rknn_run` copies are the same as C's.

perf counts samples, not bytes. `tools/copy_sizes.sh` puts a uprobe on `__memcpy_generic` and records every call's length, over 4 s of one channel, which is 20 inferences ([preprocess/copy_sizes.txt](preprocess/copy_sizes.txt)):

| | Under `prepare` | Under `bind` | Under `rknn_run` |
|---|---|---|---|
| **C** | 40 calls, 16,320 bytes | 640 calls, 13,800 bytes | 6,056 calls, 84,790 bytes |
| B | — | 20 calls, 24,576,000 bytes | 5,576 calls, 78,030 bytes |

- **The copies inside `rknn_run`:**
  - About 300 calls and 4 KB per inference, none longer than 128 bytes, in B as in C. They move metadata, not pixel or tensor data.
  - They still add up to more than 0.5% of samples. So C's input path has no CPU pixel copy, but the §11.4 rule counts `rknn_run` and fails.
  - A6 records this. The report states it, and every C result carries the label.
- **B's input copy:** 1,228,800 bytes per frame, the whole 640×640×3 tensor.
- **Not covered by the criterion:**
  - The NPU's native outputs are NC1HWC2. The pipeline binds them as NCHW INT8, about 1.2 MB per inference.
  - The native binding fails in `rknn_run` ("… OutputOperator … fallback cpu failed"). This suggests the runtime converts the outputs on the CPU. If so, it uses code other than `memcpy`, which perf cannot name in `librknnrt.so`.
  - B binds its outputs the same way.
- **C − none, total copy samples:** +33 (1 channel), +75 (4 channels). As shares, −0.96 and −1.61 points, because the runs have different sample totals.
- **strace:**
  - C made no dma-buf `mmap` call.
  - B's preprocessing threads made exactly two `DMA_BUF_IOCTL_SYNC` calls on the same fd per frame.
  - In C and in B, the decoder thread and MPP's parser also sync each decoded frame (30 calls per second each). This is MPP's own cache maintenance on the cached decoder buffers, which every variant that decodes with MPP pays.
- **Caveats:**
  - With default governors the CPU clock differs between runs, which distorts shares across runs. The cache-maintenance column is the most affected.
  - `librknnrt.so` and OpenCV have no symbols. 15.7% / 22.4% of C's samples end unresolved in `librknnrt.so`.

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
| `tools/rga_probe` | C against OpenCV candidates per plane, and the RGA3 models |
| `tools/rga_kernel` | RGA3's impulse response, edges and padding rows, color conversion on flat frames, and the `interp` settings |
| `tools/inputs_set_probe` | `rknn_inputs_set` against bound outputs, and B's bound input (A3) |
| `tools/copy_sizes.sh` / `.py` | the bytes the copies of §11.4 move, from a uprobe on `__memcpy_generic` |
| `tools/zero_copy_evidence.sh` / `.py` | perf and strace evidence of §11.4 |
