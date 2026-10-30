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
- Change: PROTOCOL §5.2 makes B's `bind` a call to `rknn_inputs_set`. Instead, B's `bind` copies the slot with `memcpy` into an input buffer from `rknn_create_mem`, one per context, bound once as UINT8 NHWC with `rknn_set_io_mem`. A uses B's `bind` and changes the same way. The description of B in HYPOTHESES.md ("What is compared") changes with it: B copies into the NPU input with `memcpy`, not `rknn_inputs_set`.
- Reason: with librknnrt 2.3.2, a context whose input comes from `rknn_inputs_set` never writes the outputs bound with `rknn_set_io_mem`. B shares that output binding with C (§5.1, §5.3).
  - Measured with `tools/inputs_set_probe` on the first frame of video ① ([output](../docs/notes/preprocess/inputs_set_probe.txt)). The bound outputs were zeroed before each run.
    - After `rknn_inputs_set` and `rknn_run`, they were still zero, and post-processing found 370 boxes in them. `rknn_outputs_get` on the same input gave 17.
    - Binding the outputs again after `rknn_inputs_set` changed nothing.
    - After a `memcpy` into an input buffer bound with `rknn_set_io_mem`, they equalled `rknn_outputs_get`.
  - Reading B's outputs with `rknn_outputs_get` would give B an output copy that C does not make.
  - The `memcpy` keeps B's defining CPU copy into the NPU input, and an output binding identical to C's. `tools/compare_prepare` checks B's bound outputs against `rknn_outputs_get` on the first and the last of its 1,000 frames: equal.
- Affected data: none. No B run of `bench_main` had been made.
- Release: none (before `freeze-m1`).

## A4 — 2026-10-30 — Scaling: RGA3's kernel and OpenCV's interpolation
- Change: fixes RGA's scaling settings (PROTOCOL §14), and adds a third OpenCV choice to the two of §8.
  - RGA: `im_opt_t.interp` stays `IM_INTERP_DEFAULT`. RGA3 ignores it. On a noise frame, linear, cubic and average gave output byte-identical to the default, both as bare values and as `IM_INTERP(h, v)`, which sets librga's apply flags (`tools/rga_kernel`).
  - RGA3's kernel for 1920×1080 → 640×360, measured with impulses (`tools/rga_kernel`):
    - Output pixel (c, r) is the mean of input columns 3c+1…3c+3 and rows 3r+1…3r+3, for Y and for the chroma repeated to full resolution. That is a 3×3 box one pixel off OpenCV's `INTER_AREA` box (3c…3c+2).
    - The last output column and row give the frame's last column or row a weight of about 3/9.
    - RGA3 does not read the decoder's padding rows below the frame.
  - OpenCV for B and A: §8 allows `INTER_LINEAR` or `INTER_AREA`. This amendment adds a third choice and picks it: `INTER_AREA` with the luma box moved by one pixel to match RGA3, at exactly 1/3 scale.
    - That is `INTER_AREA` on the frame shifted by one pixel. The last row and column repeat the frame's edge, which gives the edge a weight of 2/9 instead of RGA3's about 3/9.
    - Chroma uses `INTER_AREA` at chroma resolution, unshifted.
    - The order stays: resize, then convert (BT.601 limited range).
- Reason: §8 fixes RGA's settings first, then picks OpenCV's interpolation by the lower §11.1 MAE against C. The one-pixel shift is a measured property of RGA3's fixed scaler, not a tuned parameter.
  - On the 1,000 frames of video ① (see A5), C against B had these MAEs: 4.50 with `INTER_LINEAR` (`tools/compare_prepare`), 3.78 with `INTER_AREA` (`tools/rga_probe`), 2.41 with the shifted `INTER_AREA` (`tools/compare_prepare`).
  - `tools/rga_probe` also tried other OpenCV interpolations, converting before resizing, and full models of RGA3 ([output](../docs/notes/preprocess/rga_probe_1000.txt)). These are diagnostics outside §8's choices. The closest, with 4:4:4 chroma, a custom color conversion and truncated averages, reached an MAE of 2.04, still above the registered 2. It is not used because it needs code outside OpenCV.
- Affected data: none.
- Release: none (before `freeze-m1`).

## A5 — 2026-10-30 — Equivalence relative to B's own admissible variants
- Change: the pass conditions of PROTOCOL §11.1 and §11.2 are replaced. These stay:
  - the inputs;
  - the border check, which is still a pass condition: every border element of every B and C tensor is 114;
  - the matching of §11.2;
  - the histograms.
- The new conditions compare C against a reference pair R.
  - **R:** B's preprocessing with plain `INTER_LINEAR` (`cv::resize` on both planes, no shift; §8's other choice), against B as used (A4), on the same frames.
  - In R, the `INTER_LINEAR` side takes C's place: its boxes are matched to B's and counted against B's.
  - **Tensor level:** the MAE of C against B is at most R's, and the minimum per-frame PSNR of C against B is at least R's.
  - **Detection level:**
    - (a) the share of matched pairs with IoU ≥ 0.95 and confidence difference ≤ 0.02 is at least R's;
    - (b) the share of frames whose counted boxes agree in every class is at least R's;
    - (c) the total number of boxes differs from B's by at most 2%: |N_C − N_B| / N_B ≤ 0.02. N counts every box post-processing returns (confidence ≥ 0.25 after NMS, every class, summed over the frames), not only the counted boxes of (b).
  - The registered §11.1 and §11.2 results are still computed and reported, and so is the box count per class.
- Reason: the registered conditions can only be met by pixel-identical tensors.
  - On 1,000 frames of video ① (Pexels 36330861, the only benchmark video with at least 1,000 frames), measured with `tools/compare_prepare` (greedy matching; [output](../docs/notes/preprocess/compare_v1_1000.txt)):

    | | C vs B | R |
    |---|---|---|
    | Tensor MAE | 2.41 | 4.54 |
    | Minimum PSNR | 34.36 dB | 28.52 dB |
    | Pairs within both thresholds | 41.8% | 28.4% |
    | Frames with equal counts | 52.4% | 34.7% |
    | All boxes, against B | +0.44% | −1.04% |

  - Under input differences this small, the INT8 model's confidences move by up to 0.26 between C and B, and by up to 0.34 between the two OpenCV variants.
  - The amended conditions require two things:
    - C differs from B no more than B's own admissible variants differ from each other.
    - The post-processing workload, measured as the total box count, matches.
  - **Per class:** R also misses 2% in 4 of the 8 classes with at least 100 boxes in B (person −2.02%, truck −3.98%, handbag +2.06%, stop sign −93.4%). The project owner chose the total over all classes. C's largest per-class deviations, bus +14.65% and stop sign +72.64%, are reported.
  - **Not an independent test:**
    - These criteria were chosen after C's and R's results on these frames were known, and after A4 had moved B's luma box toward C.
    - M1-4's check runs on video ① too, the only video with 1,000 frames.
    - R's tensor difference is close to that of C against `INTER_LINEAR` B (4.54 and 4.50) almost by construction, since B now approximates C.
- Affected data: none. Recorded before any equivalence run of M1-4.
- Release: none (before `freeze-m1`).

## A6 — 2026-10-30 — Zero-copy evidence: small copies inside the NPU runtime
- Change: none to the protocol. This records a criterion of §11.4 that was not met, and its cause, as §11.4 requires.
- Result, on video ① with default governors:
  - **Tools:**
    - `tools/zero_copy_evidence.sh` and `.py`: perf, 10 s per run ([output](../docs/notes/preprocess/zero_copy_evidence.txt)).
    - `tools/copy_sizes.sh` and `.py`: a uprobe on `__memcpy_generic` that records every call's length, 4 s of one channel, 20 inferences ([output](../docs/notes/preprocess/copy_sizes.txt)).
  - **Build:** all code is built with `-fno-omit-frame-pointer -fno-optimize-sibling-calls`, so a copy's caller stays on the stack. A first run without the second flag could not attribute B's `bind` copy, which was a tail call.
  - **perf, C:** copy samples with `prepare`, `bind` or `rknn_run` on the stack are 1.04% of all samples at 1 channel and 1.43% at 4 channels, above 0.5%. By the innermost of the three, at 1 / 4 channels:
    - `prepare`: 0 / 1. The 1 is the kernel copying an `fstat` result (the per-buffer cache key).
    - `bind`: 0 / 2. One is a copy of the `rknn_tensor_attr` struct in `bind` itself; the other is a string copy in libstdc++ inside `rknn_set_io_mem`.
    - `rknn_run`: 39 / 171. These are `__memcpy_generic` called from `librknnrt.so`, and kernel user-copies in the runtime's `ioctl` calls.
  - **What the copies move,** at 1 channel over 20 inferences:
    - **C, under `rknn_run`:** 6,056 calls, 84,790 bytes. That is about 300 calls and 4.2 KB per inference, none longer than 128 bytes.
    - **C, under `prepare` and `bind`:** 680 calls, 30,120 bytes, none longer than 512 bytes.
    - **B, under `bind`:** 20 calls of 1,228,800 bytes, one input tensor per frame.
    - **B, under `rknn_run`:** 5,576 calls, 78,030 bytes, the same small copies as C.
  - **Positive control, B (perf):**
    - `bind`: 78 / 473 samples, its input copy.
    - `rknn_run`: 29 / 113, the same runtime copies as C.
  - **C − none, total copy samples:** +33 at 1 channel, +75 at 4 channels. As copy shares, −0.96 and −1.61 points: the runs have different sample totals.
  - **Cause:** the criterion counts samples, not bytes. Inside `rknn_run`, the runtime makes about 300 small copies per inference, in B as in C. They move metadata, not pixel or tensor data, and together they exceed 0.5% of samples.
  - **Not covered by this criterion:** the NPU's native outputs are NC1HWC2, and the pipeline binds them as NCHW INT8, about 1.2 MB per inference.
    - Binding the native NC1HWC2 layout fails in `rknn_run` ("unsupported tensor dtype … OutputOperator … fallback cpu failed"), also with `pass_through`. This suggests that the runtime converts the outputs on the CPU.
    - If it does, it uses code other than `memcpy`. `librknnrt.so` has no symbols, and 15.7% / 22.4% of C's samples end unresolved in it.
    - B binds its outputs the same way.
  - **strace:** C made no dma-buf `mmap` call. B's preprocessing threads made two `DMA_BUF_IOCTL_SYNC` calls on the same fd per frame, 50 frames in 10 s.
  - **Cache maintenance** (`dcache_*_poc`), at 1 / 4 channels: B 8.6% / 9.5%, C 28.4% / 26.2%, none 43.9% / 43.4%.
    - MPP itself syncs every decoded frame.
    - Under default governors, the CPU clock differs between runs, which distorts shares across runs.
- Consequence (§11.4):
  - The report states that C's zero-copy property was not met under the §11.4 perf criterion.
  - Every C result carries this label: "input path without CPU pixel copies; §11.4 perf criterion not met because of about 300 small copies (about 4 KB) per inference inside the NPU runtime, as in B".
  - The report also states the open question of the output conversion.
- Affected data: none.
- Release: none (before `freeze-m1`).
