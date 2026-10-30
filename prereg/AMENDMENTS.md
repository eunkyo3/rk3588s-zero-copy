[한국어](AMENDMENTS.ko.md)

# Amendments

Append-only record of changes to [HYPOTHESES.md](HYPOTHESES.md) and [PROTOCOL.md](PROTOCOL.md) after `prereg-v1`, and of every value PROTOCOL §14 says is fixed later. Existing entries are never edited or removed. If the English and Korean entries disagree, the English entry governs.

Each entry has this form:

```
## A<number> — YYYY-MM-DD — <short title>
- Change:
- Reason:
- Affected data: (runs or results that must be redone, or "none")
- Release: (after freeze-m1 only: amend-A<number>)
```

## A1 — 2026-10-30 — Spike results: RGA core, context path, release policy, decoder buffers
- Change: fixes these values of PROTOCOL §14.
  - RGA core: `im_opt_t.core = IM_SCHEDULER_RGA3_CORE0 | IM_SCHEDULER_RGA3_CORE1`. The spike observed that the mask is honoured: with RGA3 core 1 alone, the load moved to core 1. The mask excludes RGA2.
  - Context path: late binding (§5.1). One dma-buf registers with all three contexts, so the symmetric context fallback of §5.4 is not used.
  - Release policy: freeing an RKNN memory object in one context leaves the other contexts working, so the release fallback of §5.4 is not used. At shutdown, RKNN memory objects are still freed before their fds are closed.
  - C path fallback: none. C2 works.
  - Decoder buffer cacheability: CPU-cached. The decoder allocates from the `system-dma32` heap (below 4 GiB, cached).
- Reason: results of the M1-0 spike, [docs/notes/spike.md](../docs/notes/spike.md).
- Affected data: none. No measurement has been made.
- Release: none (before `freeze-m1`).

## A2 — 2026-10-30 — OpenCV build
- Change: fixes the OpenCV build (PROTOCOL §14, before the first B run of M1-2).
  - Ubuntu packages `libopencv-core406t64` and `libopencv-imgproc406t64` 4.6.0+dfsg-13.1ubuntu1: OpenCV 4.6.0 for aarch64, baseline NEON FP16, parallel framework TBB 2021.11, built with GCC 13.2.0.
  - `libopencv_core.so.4.6.0` SHA-256 `270377fc59116c2045fae2891c03a5abdeabb5c58982afd7183bc715cdeca6b3`; `libopencv_imgproc.so.4.6.0` SHA-256 `9f2b345b4b666bab4ce3d6db44fd5ef312a49f98d9233720f501460e64d9ebef`.
  - The full `cv::getBuildInformation()` output is [docs/notes/opencv_build.txt](../docs/notes/opencv_build.txt) (SHA-256 `f639ef8a42c044303ee94b2dff3dcba728e4ae713302421c8943620588c7dd26`).
  - `bench_main` calls `cv::setNumThreads(1)` (PROTOCOL §8).
- Reason: PROTOCOL §14 fixes the OpenCV build before B runs for the first time.
- Affected data: none. No B run has been made.
- Release: none (before `freeze-m1`).

## A3 — 2026-10-30 — B's bind copies into a bound input buffer
- Change: PROTOCOL §5.2 makes B's `bind` a call to `rknn_inputs_set`. Instead, B's `bind` copies the slot with `memcpy` into an input buffer from `rknn_create_mem`, one per context, bound once as UINT8 NHWC with `rknn_set_io_mem`. A uses B's `bind` and changes the same way.
- Reason: with librknnrt 2.3.2, a context whose input comes from `rknn_inputs_set` never writes the outputs bound with `rknn_set_io_mem`. B shares that output binding with C (§5.1, §5.3).
  - Measured with `tools/compare_prepare`: B's bound outputs differed from `rknn_outputs_get` on the same input, and post-processing found 370 boxes per frame in the unwritten (zeroed) buffers. Binding the outputs again after `rknn_inputs_set` did not help.
  - Reading B's outputs with `rknn_outputs_get` would give B an output copy that C does not make.
  - The `memcpy` keeps B's defining CPU copy into the NPU input and an output binding identical to C's. B's bound outputs now equal `rknn_outputs_get` on the same input.
- Affected data: none. No B run of `bench_main` had been made.
- Release: none (before `freeze-m1`).

## A4 — 2026-10-30 — Scaling: RGA3's kernel and OpenCV's interpolation
- Change: fixes the values of PROTOCOL §8 and §14 for scaling.
  - RGA: `im_opt_t.interp` stays `IM_INTERP_DEFAULT`. RGA3 ignores it: default, linear, cubic and average gave bit-identical tensors.
  - RGA3's kernel for 1920×1080 → 640×360, measured with impulses (`tools/rga_kernel`): output pixel (c, r) is the mean of input columns 3c+1…3c+3 and rows 3r+1…3r+3, for Y and for the chroma repeated to full resolution. That is a 3×3 box one pixel off OpenCV's `INTER_AREA` box (3c…3c+2).
  - OpenCV for B and A: `INTER_AREA`. At exactly 1/3 scale, B's luma box is moved by one pixel to match RGA3: `INTER_AREA` on the frame shifted by one pixel, with the last row and column repeated. Chroma uses `INTER_AREA` at chroma resolution, unshifted. The order stays: resize, then convert (BT.601 limited range).
- Reason: §8 fixes RGA's settings first, then picks OpenCV's interpolation by the lower §11.1 MAE against C. The one-pixel shift is a property of RGA3's fixed scaler, not a tuned parameter.
  - On 1,000 frames of video ① (see A5), C against B had an MAE of 4.50 with `INTER_LINEAR` and 2.41 with the shifted `INTER_AREA`.
  - In 100 frames of `tools/rga_probe`, `INTER_AREA` without the shift gave 3.61.
- Affected data: none.
- Release: none (before `freeze-m1`).

## A5 — 2026-10-30 — Equivalence relative to B's own admissible variants
- Change: the pass conditions of PROTOCOL §11.1 and §11.2 are replaced. The inputs, the border check (every border element is 114), the matching of §11.2 and the histograms stay. The reference pair is R = B with `INTER_LINEAR` against B as used (A4), on the same frames. `INTER_LINEAR` and `INTER_AREA` are the two choices §8 allows for B.
  - Tensor level: the MAE of C against B is at most R's, and the minimum per-frame PSNR of C against B is at least R's.
  - Detection level:
    - (a) the share of matched pairs with IoU ≥ 0.95 and confidence difference ≤ 0.02 is at least R's;
    - (b) the share of frames whose counted boxes agree in every class is at least R's;
    - (c) C's total number of boxes over all classes is within 2% of B's.
  - The registered §11.1 and §11.2 results are still computed and reported, and so is the box count per class.
- Reason: the registered conditions can only be met by pixel-identical tensors.
  - On 1,000 frames of video ① (Pexels 36330861, the only benchmark video with at least 1,000 frames), measured with `tools/compare_prepare` (greedy matching):

    | | C vs B | R |
    |---|---|---|
    | Tensor MAE | 2.41 | 4.54 |
    | Minimum PSNR | 34.36 dB | 28.52 dB |
    | Pairs within both thresholds | 41.8% | 28.4% |
    | Frames with equal counts | 52.4% | 34.7% |
    | All boxes, against B | +0.44% | −1.04% |

  - Under input differences this small, the INT8 model's confidences move by up to 0.26 between C and B, and by up to 0.34 between the two OpenCV variants.
  - The amended conditions require C to differ from B no more than B's own admissible variants differ from each other, and the post-processing workload, measured as the total box count, to match.
  - Per class, R also misses 2% in 4 of 8 classes (person −2.0%, truck −4.0%, handbag +2.1%, stop sign −93%). The project owner chose the total over all classes. C's largest per-class deviations, bus +14.7% and stop sign +72.6%, are reported.
- Affected data: none. Recorded before any equivalence run of M1-4.
- Release: none (before `freeze-m1`).

## A6 — 2026-10-30 — Zero-copy evidence: the runtime copies the outputs on the CPU
- Change: none to the protocol. This records a criterion of §11.4 that was not met, and its cause, as §11.4 requires.
- Result, from `tools/zero_copy_evidence.sh` and `.py`, on video ① with default governors, 10 s per run:
  - **perf, C:** copy samples with `prepare`, `bind` or `rknn_run` on the stack are 0.82% of all samples at 1 channel and 1.08% at 4 channels, above 0.5%.
    - By function: `prepare` 0 samples; `bind` 1 and 3 samples, which are copies of the `rknn_tensor_attr` struct; `rknn_run` 24 and 139 samples, all `__memcpy_generic` called from `librknnrt.so` or kernel user-copies.
    - B shows the same `rknn_run` copies, 17 and 120 samples.
    - C − none, total copy share: +0.04 points at 1 channel, −1.53 points at 4 channels.
  - **Cause:** the model's output operators convert the NPU's native NC1HWC2 outputs into the bound NCHW INT8 buffers on the CPU, about 1.2 MB per inference.
    - Binding the native NC1HWC2 layout fails in `rknn_run` ("unsupported tensor dtype … OutputOperator … fallback cpu failed"), also with `pass_through`.
    - The native NHWC layout costs 15 ms of CPU per run instead of 2.5 ms (docs/notes/spike.md).
  - **strace:** C made no dma-buf `mmap` call. B's preprocessing threads made two `DMA_BUF_IOCTL_SYNC` calls on the same fd per frame, 50 frames in 10 s.
  - **Cache maintenance** (`dcache_*_poc`), at 1 / 4 channels: B 4.6% / 8.2%, C 25.6% / 28.4%, none 41.1% / 43.6%.
    - MPP itself syncs every decoded frame.
    - Under default governors, the CPU clock differs between runs, which distorts shares across runs.
- Consequence (§11.4): the report states that C's zero-copy property was not met under the §11.4 perf criterion. Every C result carries the label "input path without CPU pixel copies; the runtime's output operators copy about 1.2 MB per inference on the CPU, as in B".
- Affected data: none.
- Release: none (before `freeze-m1`).
