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
    - `tools/copy_sizes.sh` and `.py`: uprobes on `__memcpy_generic` and `__memmove_generic`, the variants glibc picks on this CPU, that record every call's length. 4 s of one channel, 20 or 21 inferences, no lost events ([output](../docs/notes/preprocess/copy_sizes.txt)). Kernel user-copies are not measured.
  - **Build:** all code is built with `-fno-omit-frame-pointer -fno-optimize-sibling-calls`, so a copy's caller stays on the stack. A first run without the second flag could not attribute B's `bind` copy, which was a tail call.
  - **perf, C:** copy samples with `prepare`, `bind` or `rknn_run` on the stack are 1.04% of all samples at 1 channel and 1.43% at 4 channels, above 0.5%. By the innermost of the three, at 1 / 4 channels:
    - `prepare`: 0 / 1. The 1 is the kernel copying an `fstat` result (the per-buffer cache key).
    - `bind`: 0 / 2. One is a copy of the `rknn_tensor_attr` struct in `bind` itself; the other is a stream write in libstdc++ (`basic_streambuf::xsputn`) inside `rknn_set_io_mem`.
    - `rknn_run`: 39 / 171. These are `__memcpy_generic` called from `librknnrt.so`, and kernel user-copies in the runtime's `ioctl` calls.
  - **What the copies move,** at 1 channel:
    - **C, under `rknn_run`** (21 inferences): 23,994 calls, 306,170 bytes. That is about 1,150 calls and 15 KB per inference, none longer than 512 bytes. Most are `std::vector` copies inside `librknnrt.so`, which go through `memmove`.
    - **C, under `prepare` and `bind`:** 903 calls, 33,726 bytes, none longer than 512 bytes.
    - **B, under `bind`** (20 frames): 20 calls of 1,228,800 bytes, one input tensor per frame.
    - **B, under `rknn_run`** (20 inferences): 23,408 calls, 297,640 bytes. These are the same runtime copies as C's. C also makes about 24 formatting copies per inference (`printf` and libstdc++ string writes) that B does not.
  - **Positive control, B (perf):**
    - `bind`: 78 / 473 samples, its input copy.
    - `rknn_run`: 29 / 113, the same runtime copies as C.
  - **C − none, total copy samples:** +33 at 1 channel, +75 at 4 channels. As copy shares, −0.96 and −1.61 points: the runs have different sample totals.
  - **Cause:** the criterion counts samples, not bytes. Inside `rknn_run`, the runtime makes about 1,150 small copies per inference, in B as in C. None is longer than 512 bytes, so none moves pixel or tensor data. Together they exceed 0.5% of samples.
  - **Not covered by this criterion:** the NPU's native outputs are NC1HWC2, and the pipeline binds them as NCHW INT8, about 1.2 MB per inference.
    - Binding the native NC1HWC2 layout fails in `rknn_run` ("unsupported tensor dtype … OutputOperator … fallback cpu failed"), also with `pass_through`. This suggests that the runtime converts the outputs on the CPU.
    - If it does, it uses code other than `memcpy` or `memmove`. `librknnrt.so` has no symbols, and 15.7% / 22.4% of C's samples end unresolved in it.
    - B binds its outputs the same way.
  - **strace:** C made no dma-buf `mmap` call. B's preprocessing threads made two `DMA_BUF_IOCTL_SYNC` calls on the same fd per frame, 50 frames in 10 s.
  - **Cache maintenance** (`dcache_*_poc`), at 1 / 4 channels: B 8.6% / 9.5%, C 28.4% / 26.2%, none 43.9% / 43.4%.
    - MPP itself syncs every decoded frame.
    - Under default governors, the CPU clock differs between runs, which distorts shares across runs.
- Consequence (§11.4):
  - The report states that C's zero-copy property was not met under the §11.4 perf criterion.
  - Every C result carries this label: "input path without CPU pixel copies; §11.4 perf criterion not met because of about 1,150 small copies (about 15 KB) per inference inside the NPU runtime, as in B".
  - The report also states the open question of the output conversion.
- Affected data: none.
- Release: none (before `freeze-m1`).

## A7 — 2026-11-02 — Fixed clocks, P_cap and the H2 auxiliary point
- Change: fixes these values of PROTOCOL §14 (M1-3), and departs from §8 and adds to §9.3 as follows.
  - **Fixed frequencies:** A55 (policy0) 1800 MHz, A76 policy4 and policy6 2208 MHz, DDR (`dmc`) 2112 MHz, NPU 1000 MHz. Every later run is checked against these values (§9.3 rule 1).
    - **Departure from §8:** §8 fixes the frequencies that *result* from the `performance` governor, that is, each domain's maximum. For both A76 clusters, the fixed value is instead 2208 MHz, below their maximum.
    - `tools/set_perf_mode.sh` sets the `performance` governor with each maximum at its fixed value. It fails if a value is not offered or not reached.
  - **`P_cap`** = 194.2083 inferences per second: the median of 194.5333, 194.1667 and 194.2083 (23,305 inferences completed in the 120 s window).
  - **H2 auxiliary point** = floor(0.8 × 194.2083 / 30) = 5 channels. So the auxiliary set (§6.3) is {1, 2, 3, 4, 5}. The point coincides with H2's 5-channel point, so H2's secondary verdicts (HYPOTHESES, H2) are taken at the same channel count as its verdicts.
  - **The `P_cap` part of the unsaturated condition** (§6.2): channels × fps ≤ 4661/30 ≈ 155.37, that is, at most 31 channels at the main condition and 5 at the auxiliary one. The boundary is fixed together with `L`, by a later amendment.
  - **Raw NPU limit**, for reference only: 70.7250 inferences per second on 1 core, 137.2250 on 2 and 199.2083 on 3.
  - **Addition to §9.3 for these runs:** a run of `tools/calib_npu` is also invalid if, after its window, a context's bound outputs differ from `rknn_outputs_get` on the same tensor (A3). No run was.
- Reason:
  - **A76 at 2208 MHz, not at their maximum** ([docs/notes/calibration/a76_tables.txt](../docs/notes/calibration/a76_tables.txt)):
    - The highest entry of policy4's frequency table changes between boots. It was 2304 MHz on the boot of 2026-10-30 and on the boot of 2026-11-02 at about 05:25 UTC. It was 2352 MHz on the boot of the M1-4 runs and on the boot of 2026-11-02 07:20 UTC.
    - The 05:25 boot was read by hand from sysfs; the others are in `collect_env.sh` output. policy6 showed 2352 MHz on all four.
    - A fixed value taken from the maximum would therefore differ between sessions, or make every run after a reboot invalid.
    - 2208 MHz is offered on both clusters on every boot seen. It is also the kernel's high-temperature cap for both A76 clusters (`h_limit=2208000000`, applied above 85 °C).
    - The A55 cluster and the NPU keep their maxima. Their high-temperature caps are 1608 and 800 MHz.
    - These caps are not the only limit: thermal throttling from `soc-thermal`'s 75 °C trip went lower on 2026-10-30, to 1416 MHz on the A55 cluster and 2016 MHz on the A76 clusters, with the NPU at 800 MHz (`docs/notes/env.md`, thermal observation). A clock sample below a fixed value, for either reason, makes the run invalid (§9.3 rule 1).
    - `tools/collect_env.sh` now records the frequency tables, the boot time and the `h_limit` caps before every run.
  - **Measurement:** `tools/calib_npu.sh`, session [results/calibration/20261102T073918Z_npu](../results/calibration/20261102T073918Z_npu), code `3789a7a`, ambient 28 °C, cooling "heatsink + always-on mini fan (unbranded, AliExpress)".
    - The session started with 10 minutes idle (`soc-thermal` baseline 39.9 °C), and every run passed the cooldown gate (§9.2).
    - 3 repetitions of the 4 measurements, interleaved. All 12 runs were valid: no clock sample below its fixed value, no temperature at a trip point (highest 55.5 °C).
  - **Tensor set** (§6.2): `tools/dump_tensor`, the first 1,000 frames of video ① (`file_source`), written by C's preprocessing (RGA3), into `system-uncached-dma32` buffers.
    - Its SHA-256 is `051d6f12…`. That is the hash of the `c.u8` that `tools/equivalence_dump` at code `669fb9b` wrote in the M1-4 deciding run, which is kept in Release `data-equivalence-20261102T011123Z`. The set is therefore that run's C input.
    - The set's own record (`tensors.json`) names code `38ddd37` with the then uncommitted `dump_tensor`.
  - **The raw limit keeps the bind of each step** (`rknn_set_io_mem`), which cycling the tensors needs: 0.014 to 0.021 ms per step, against 14.1 to 15.0 ms for `rknn_run`.
  - The cooling configuration is fixed by the thermal check (§12), not by this entry.
- Affected data: none. M1-4 ran with the default governors; its tensors and detections do not depend on the clocks.
- Release: none (before `freeze-m1`).

## A8 — 2026-11-03 — Held-out check of the equivalence criteria
- Change: registers, before it runs, a check that applies A5's criteria to videos they were not chosen on. A5's criteria stay in force, and M1-4's verdict under them stands.
  - **Inputs:** every frame of videos ②, ③ and ④ ([docs/notes/preprocess.md](../docs/notes/preprocess.md#benchmark-videos)), as prepared for the benchmark, read as `file_source`:

    | Video | Frames | SHA-256 |
    |---|---|---|
    | ② `pexels_29130409` | 752 | `e560dd8481d5fe1c615f208fae192fc7fd583097d202e785dc026eae8fb75b41` |
    | ③ `pexels_29969146` | 714 | `592b6847e2e63e07393af6f4a04b691be438c23eba2c73f79b021dca877c2772` |
    | ④ `pixabay_180386` | 948 | `2538f54d36e0d763ec3e13f9e606bf01e2d142fff82181eb156f792576b0555b` |

    According to the repository's records, B, C and R have not preprocessed or run inference on these videos before this entry. The only earlier run on them is the RTSP check of 2026-11-02, which decoded only (`--variant none`).
    The M1-3 sessions running while this entry is written use them too. The `L` search and the loop check decode only. The thermal check runs B alone: it records B's box count per frame, and no tensors. Nothing compares those runs with C or R.
  - **Procedure:** `tools/equivalence_holdout.sh` on the board.
    - It first checks each video's SHA-256 and frame count against the table, and stops if one differs.
    - For each video, `tools/equivalence_dump` runs C, B and R on every frame, as in M1-4: the same decoded frame for all three, NPU core 0, with the harness checks of M1-4.
    - Then `tools/equivalence_check.py --set` pools the three videos, in the order ②, ③, ④.
    - **Code:** a build that is not `-dirty`, whose latest amendment is A8, with no change under `src/`, `tools/`, `models/` or `CMakeLists.txt` since the merge of the pull request that adds this entry. So the check runs before any later amendment is merged.
  - **Which run decides:**
    - Every run is kept and reported. The first run whose check completes (exit 0 or 1) decides.
    - A run that stops earlier (exit 2 or 3, for example when a harness check fails) decides nothing. Its cause is recorded, and the check is run again.
    - If B's, C's or R's code changes before `freeze-m1`, M1-4 is redone (§15), and so is this check. The report then gives both results.
    - A rerun that needs changed code, after a stopped run or a change of B, C or R, runs on that code and names its amendment. The Code rule above applies to the first run only.
  - **Verdict:** A5's six conditions, unchanged, on the 2,414 pooled frames:
    - the border check of B and C;
    - C's MAE at most R's, and C's minimum per-frame PSNR at least R's;
    - (a) and (b) at least R's;
    - (c) C's total box count within 2% of B's.
  - **Reported, not verdicts:** each video's own results under A5, the registered criteria of §11.1 and §11.2 on the pool, and the box counts per class.
  - **What follows from the result:**
    - The result is reported whatever it is. A pass is reported as A5's equivalence holding on videos its criteria were not chosen on, together with any video that fails on its own.
    - A failure goes into the report's summary. Every hypothesis verdict then carries this caveat: B and C were shown equivalent under A5 only on video ①, the video A5's criteria were chosen on.
    - Either way, the comparison runs of §12 go ahead. §11.3 does not apply, because M1-4's verdict under A5, on its own input, stands.
    - No criterion, setting or implementation changes because of this check's result. A change made later for another reason needs its own amendment.
  - **Registration:** although this entry comes before `freeze-m1`, it gets a Release, `amend-A8`, created after the pull request that adds it is merged. Its `created_at` is the reference time (§1).
    - The check's results name A8 as the latest amendment, so `tools/check_prereg_order.py` checks that they start after that time.
    - The check runs before `freeze-m1`.
- Reason: A5's criteria were chosen after C's and R's results on video ① were known, and after A4 had moved B toward C (A5, "Not an independent test").
  - Applying them unchanged to videos that played no part in that choice tests whether they hold beyond the video they were chosen on. Fixing in advance what follows from each outcome keeps the result from being used selectively.
  - Videos ②, ③ and ④ each have fewer than the 1,000 frames §11 registers, so the check pools them.
    - The pool has 2,414 frames, comparable with the 1,000 frames A5's criteria were chosen on.
    - A single video has as few as 714 frames, which makes condition (c), a 2% bound on a box count, more sensitive to chance.
  - **Tool changes:**
    - `tools/equivalence_check.py` gains `--set DIR FRAMES`, which pools sources. Its single-source output is unchanged: on a test case of 120 frames, the old and new versions print the same report and write the same JSON.
    - `tools/equivalence_holdout.sh` is new.
- Affected data: none. M1-4's results and verdict are unchanged.
- Release: `amend-A8`.

## A9 — 2026-11-03 — `L`, main set, unsaturated boundary, channel assignment, cooling
- Change: fixes these values of PROTOCOL §14 (M1-3).
  - **`L` = 32,** at the cap of §6.1: 32 channels, the size of the channel assignment. All 28 runs of the search and its confirmation passed: 8 to 32 channels, then 3 more at 32 (§10).
  - **Main set** (§6.1): {1, 2, 4, 8, 12, 16, 32}.
  - **Unsaturated boundary** (§6.2, with A7): at most 31 channels at the main condition and 5 at the auxiliary one. In the main set, only 32 is saturated: 32 × 5 = 160 > 4661/30 ≈ 155.37.
  - **Channel-to-video assignment** (§3): [stream-server/assignment_m1.txt](../stream-server/assignment_m1.txt). Channel n plays video ((n − 1) mod 4) + 1: ch1 ①, ch2 ②, ch3 ③, ch4 ④, ch5 ①, and so on to ch32.
  - **Cooling configuration** (§12): a heatsink and an always-on mini fan (unbranded, AliExpress), as in every M1-3 session. The thermal check passed with it at 26 °C ambient.
  - **The loop check of §3** passed for all four videos.
  - **How these sessions judge their runs:** `tools/stream_session.py`, fixed before the runs in [docs/notes/calibration.md](../docs/notes/calibration.md).
    - Rule 4 of §9.3 is read from the stream server's log, whose times have one-second resolution. A line counts if its second lies between the warm-up start and the window end, both rounded down. In the window end's own second, the board's disconnect is exempt, because the bench disconnects right after the window.
    - External failures (§9.3): an input that ends early, or an unreadable server log, makes the run invalid. A stream server that is down, an unreadable server log, or a PC clock more than 5 ms behind or 100 ms ahead of the board's also stops the session.
    - In the thermal check, rule 1 is the check's criterion, not an invalidity. Every one-second sample counts, warm-up included.
  - **Time:** from 2026-11-03 02:19 UTC, the board takes its time from the stream PC's chrony over the wired link, instead of from `ntp.ubuntu.com` over Wi-Fi. From 04:10 UTC it polls the PC no less often than every 64 s. Both machines stay NTP-synchronized.
- Reason: the M1-3 sessions on 2026-11-03, each with 10 minutes idle, the cooldown gate and the environment before every run (§9.1, §9.2). Cooling as above.

  | Session | Time (UTC) | Code, amendment | Ambient | Result |
  |---|---|---|---|---|
  | [`L`](../results/calibration/20261103T000647Z_limit) | 00:16–01:51 | `9553f70`, A7 | 24 °C | 28 runs valid and passing; drift ≤ 0.033 s; no decoder drops |
  | [Loop check](../results/check/20261103T032849Z_loop) | 03:38–03:51 | `5c9bc80`, A8 | 25 °C in its header; the operator reported 26 °C at 03:50 | 4 runs valid; 3 to 5 loop points in each window, from each video's duration; no decoder drops |
  | [Thermal check](../results/calibration/20261103T035136Z_thermal) | 04:01–04:08 | `5c9bc80`, A8 | 26 °C | 2 runs valid (rule 3 applies to the 5-channel run only; 32 channels is saturated); no clock sample below its fixed value; highest `soc-thermal` 67.5 °C (trip point 75 °C) at 32 channels |

  - **Time:** the PC's clock minus the board's wandered from +8 ms (PC ahead) to beyond −5 ms. At 02:10 UTC it stopped the first loop check, after two valid runs, and the first thermal check before its first run.
    - The sessions run with the board on the PC's time recorded offsets of 0 to −3 ms, inside the bounds.
    - After the poll change at 04:10 UTC, `timesyncd` reported an offset of −0.14 ms, read by hand and not recorded. No session has run since.
  - **Build defect:** two later sessions, a loop check from 02:25 and a thermal check from 03:01 UTC, had every run crash at its end (exit −11).
    - `tools/deploy_board.sh` restored each file's original time. A build from `main` for the held-out check had rebuilt `pipeline.cpp` and `report.cpp` from `main`'s versions. The next deploy, from this branch, brought back its own versions of these files with older times, so make skipped them. The tools whose commit define changed, `bench_main` among them, were rebuilt against this branch's `RunResult`. Objects of two versions were linked together.
    - Commit `5c9bc80` makes the copy take the time of extraction, so each deploy rebuilds every file it copies.
    - The `L` session is not affected. Its 28 runs exited normally, and their `summary.csv` holds `warmup_start_utc` and `window_*_utc`, which only this branch's `pipeline.cpp` and `report.cpp` write, with times that match `runs.tsv`.
    - The held-out check of A8 is not affected. `tools/equivalence_dump` uses none of `RunResult`, `run_pipeline` or the result writers, and the four `src/` files that differ between `main` and this branch change only these and a clock helper they use.
    - The defect needs a deploy whose files are older than the objects of the build before it, made from other sources. No deploy log is kept for earlier builds. Their results carry checks of their own: M1-4's three runs gave the same bytes and its harness checks held, and every `P_cap` run passed its output checks (A7; `tools/calib_npu.cpp`).
  - **Repeated checks:** the loop check and the thermal check each ran three times.
    - The first loop check's two valid runs are not used: the check was redone in full in a new session.
    - A session with invalid ×2 runs has no result. It is not a failure of §12, which would call for a cooling change. These calibration checks are redone before `freeze-m1` (§15).
  - All four sessions that stopped or were invalid are kept and flagged ([docs/notes/calibration.md](../docs/notes/calibration.md#sessions-that-stopped-or-were-invalid)). None of their runs counts.
  - PROTOCOL sets no bound on the ambient temperature. `P_cap` ran at 28 °C, these sessions at 24 to 26 °C.
- Affected data: none. The `L` session ran before the time change, with offsets of +4 to +8 ms, inside the bounds. The held-out check of A8 reads local files and does not use the stream PC.
- Release: none (before `freeze-m1`).

## A10 — 2026-11-03 — Software, build and model for `freeze-m1`
- Change: fixes the remaining values of PROTOCOL §14 that are due before `freeze-m1`, and sets variant A's decoder threading. The channel-to-video assignment, also due then, was fixed by A9.
  - **Reference:** [docs/notes/inventory_m1.txt](../docs/notes/inventory_m1.txt) (SHA-256 `f6e5a78a15e5fecbae5ab5c132d866dcddd8eaec9562c48d6413d83f97f934dd`), written on the board by the new `tools/inventory.sh`.
    - It holds the board's versions, builds and flags, and the model's SHA-256. It also holds the SHA-256 of every shared library the dynamic linker resolves for `bench_main` (`ldd`), with its package and version.
    - The other values below are in the files this entry names. For the values the reference holds, the file governs.
  - **Decoder** (§5.1):
    - **Build:** [ffmpeg-rockchip](https://github.com/nyanmisaka/ffmpeg-rockchip) commit `8b4385e985c0a50bf7d3f96cf3cb6d0b63c53e6c` (branch 7.1), built on the board by `tools/setup/build_ffmpeg_rockchip.sh` with gcc 13.3.0, into `/opt/ffmpeg-rockchip`.
      - Configuration: `--enable-shared --disable-static --disable-doc --enable-version3 --enable-libdrm --enable-rkmpp --enable-rkrga`, with the prefix and an rpath to it.
      - libavcodec 61.19.101, libavformat 61.7.103, libavutil 59.39.100, libswresample 5.3.100.
    - **Options, B and C** (`src/core/video_source.cpp`): `h264_rkmpp`, opened with `afbc=off`, set explicitly. Its other options keep their defaults: `deint` true, `fast_parse` true, `buf_mode` half.
      - `deint` enables the IEP de-interlacer for interlaced input. The prepared videos are progressive: `prepare_videos.sh` does not ask x264 for interlaced coding, and ffprobe reported `field_order` progressive for all four on 2026-11-03 (not recorded).
      - Output `AV_PIX_FMT_DRM_PRIME`, chosen in `get_format`; `extra_hw_frames=8`; no `hw_device_ctx` (§5.1, §8).
    - **Input:** RTSP over TCP (`rtsp_transport=tcp`) with `timeout=5000000`, that is 5 s. Local files (`file_source`) are opened without options. No other demuxer or decoder option is set.
    - **A:** libavcodec's native `h264` decoder with its default options, except `thread_count = 0`: automatic threading, where libavcodec takes the thread count from the CPUs in the affinity mask (cpu4–7, §8).
      - **Code change:** the code said "automatic threading" but did not set `thread_count`, so A would have decoded with libavcodec's default of one thread. "Automatic" is only the default of the `ffmpeg` command line. The code now sets it, as it intended. No A run had been made.
      - With it, both A runs of [the run-time library check](../results/check/20261103T053143Z_runtime_libs) decoded with 5 threads (`av:h264:df0` to `df4`): the 4 CPUs of the mask, plus 1.
  - **Libraries and drivers** (§2):

    | Component | Version |
    |---|---|
    | OS, kernel | Ubuntu 24.04.4 LTS; 6.1.141 `#16 SMP Thu Dec  4 14:51:28 CST 2025` |
    | Boot firmware | `ddr-v1.18-9fa84341ce,bl31-v1.49,bl32-v1.19,uboot-44f011c4ba-07/17/2025` (`/proc/cmdline`) |
    | RKNPU driver, RGA driver | v0.9.8; v1.3.10 |
    | librknnrt | 2.3.2 (`429f97ae6b@2025-04-09T09:09:27`), SHA-256 `d31fc19c85b85f6091b2bd0f6af9d962d5264a4e410bfb536402ec92bac738e8`. It came with the board's image, not from a package. |
    | RKNN headers | rknn-toolkit2 v2.3.2, in `/opt/rknpu2/include` (`tools/setup/fetch_rknn_headers.sh`) |
    | librga | 1.10.3, package `librga2` 2.2.0-1 |
    | MPP | package `librockchip-mpp1` 20251211-1 |
    | libdrm | package `libdrm2` 2.4.125-1ubuntu0.1~24.04.2 |
    | OpenCV | 4.6.0, as fixed by A2 |
    | C and C++ runtime | `libc6` 2.39-0ubuntu8.9; `libstdc++6` 14.2.0-4ubuntu2~24.04.1 |

    - **Libraries opened at run time:** [results/check/20261103T053143Z_runtime_libs](../results/check/20261103T053143Z_runtime_libs), from the new `tools/runtime_libs.sh`. B, C and A, 1 channel, each from a local file and over RTSP, mapped one library beyond `ldd`'s list: `libtbbmalloc`, TBB's memory allocator, which `libtbb` opens with `dlopen`. The reference includes it.
  - **Model** (§4): `models/yolov8n_i8.rknn`, SHA-256 `04a76611a785802b08703192455994e07bbfe4531821ae7ed35faf78fe1ec1ee`, from the release named in [models/README.md](../models/README.md).
    - It was converted from `yolov8n.onnx` (SHA-256 `0c8716701f471067932b797eeb67c8e5db47c693c2557c881d7679ec12e21bc5`), the export that rknn_model_zoo v2.3.2 distributes, downloaded from the URL in `tools/model/convert_in_container.sh`.
    - The conversion used `examples/yolov8/python/convert.py` of rknn_model_zoo commit `bad6c7334531becaf90a561988519b7bec34d0ab` (tag v2.3.2), with rknn-toolkit2 2.3.2.
    - Settings: target `rk3588`, INT8, `mean 0, std 255`. `convert.py` sets nothing else, so every other setting is the toolkit's default.
    - **Quantization dataset:** the 20 COCO images that `datasets/COCO/coco_subset_20.txt` lists at that commit. [models/coco_subset_20.sha256](../models/coco_subset_20.sha256) gives the SHA-256 of the list and of each image.
  - **`rknn_init` flags** (§8): `rknn_init(&ctx, model, size, 0, NULL)`, that is, flags 0 and no extension, for every variant.
    - So no flag is set: neither `RKNN_FLAG_ASYNC_MASK` nor any `RKNN_FLAG_DISABLE_FLUSH_*` flag.
    - `rknn_run(ctx, NULL)` blocks until the inference ends.
    - Then `rknn_set_core_mask(ctx_i, RKNN_NPU_CORE_0 << i)` pins context i to NPU core i, for i = 0, 1, 2.
    - The flags are the constant `NpuContexts::kInitFlags` in `src/core/npu.h`.
  - **Compiler and flags:** on the board, g++ 13.3.0 (Ubuntu 13.3.0-6ubuntu2~24.04.1), GNU ld 2.42, CMake 3.28.3 and GNU Make 4.3, through `tools/deploy_board.sh`.
    - CMake build type Release. Every C++ file is compiled with `-O3 -DNDEBUG -std=gnu++17 -Wall -Wextra -fno-omit-frame-pointer -fno-optimize-sibling-calls -pthread`.
    - No `-march` or `-mcpu` is given, so GCC's default target applies.
    - `bench_main` is linked with `-O3 -DNDEBUG -pthread` and the rpath `/lib:/opt/ffmpeg-rockchip/lib`.
  - **Stream PC** (§3): mediamtx v1.21.2, binary SHA-256 `0ffb7f18210d08ee38cb16668c4f3a2c96622e69d401119c0026d078a2b89523`; ffmpeg 7.0.2, the static build from johnvansickle.com, binary SHA-256 `e7e7fb30477f717e6f55f9180a70386c62677ef8a4d4d1a5d948f4098aa3eb99`. §14 does not list them; they are fixed here too.
  - **Checks:** `tools/stream_session.py` now does the following at the start of every session.
    - Before it sets the clocks, it runs `tools/inventory.sh`, keeps the output as `inventory.txt`, and stops before the first run if the output differs from the reference. It writes the difference to `inventory.diff`.
    - It also stops if the reference is not the file of this entry: its SHA-256 is a constant in the tool.
    - Before it starts the stream server, it compares the SHA-256 of the PC's mediamtx and ffmpeg with the values above, and stops if one differs.
    - Every session of §12 has to make the same checks and stop on a difference.
    - A session's `inventory.txt` is the record of the compiler and flags (§2) for every run in that session. `tools/deploy_board.sh` builds outside sessions, and each run's header names the code's commit.
  - Changing any of these values needs an amendment (§15), which states the data it affects.
- Reason: §14 fixes these values before `freeze-m1`, and §2 freezes the versions, builds, compiler and flags there.
  - **The reference:** written on 2026-11-03 by this entry's tool. At commit `436168b`, in a build that is not `-dirty`, whose `tools/inventory.sh` and reference are this entry's, `tools/inventory.sh docs/notes/inventory_m1.txt` reported "same".
    - Nothing in the output reads the commit or the amendment (`CODE_COMMIT`, `LATEST_AMENDMENT`). It depends on `CMakeLists.txt` and on where the repository is, by design.
    - An earlier version of the tool gave the same values for the build of `cf7e40b`, the merge of PR #14.
  - **No package change since the first run under `results/`:**
    - The dpkg log shows the last change on 2026-10-30 at 07:26 UTC: `libc6` from 2.39-0ubuntu8.7 to 8.9, installed together with its debugging symbols for perf (A6).
    - The files that come from no package have these modification times: the ffmpeg-rockchip libraries 2026-10-30 01:33 UTC, the RKNN headers 04:02 UTC that day, librknnrt 2026-08-13. No earlier hash of them exists: earlier runs recorded only their version strings, which are the same.
    - Every run under `results/` started on 2026-11-02 or later.
    - `unattended-upgrades` is not installed, and APT's periodic jobs are not configured.
    - Evidence under `docs/notes/` from 2026-10-30 (M1-0 to M1-2) was partly produced before the `libc6` change. None of it is a measurement that these values bind.
  - The quantization images were downloaded from that commit of rknn_model_zoo, and their hashes were computed then. The conversion of 2026-10-30 used the same commit (`models/yolov8n.sha256`).
- Affected data: none. Only A's decoder threading changed, and no A run had been made.
- Release: none (before `freeze-m1`).

## A11 — 2026-11-03 — Exploratory M1.1 design: 4K input
- Change: adds a design to the optional M1.1 designs of PROTOCOL §13. It is registered before `freeze-m1`, so before any comparison run. Like the others, it is not used for any verdict.
  - **Question:** B's work per frame grows with the decoded frame's pixels: it maps and resizes the frame on the CPU. C's CPU does not touch the pixels. M1's 1080p input cannot show whether C's advantage grows with the input's resolution; 4K input, with four times the pixels, can.
  - **Inputs:** videos ①, ② and ④ ([docs/notes/preprocess.md](../docs/notes/preprocess.md#benchmark-videos)), re-encoded by `stream-server/prepare_videos.sh` with `SIZE=3840x2160 BITRATE=16M`. That is §3's encoding at four times the pixels and four times the bit rate: 3840×2160 H.264 High, 30 fps, no B-frames, GOP 30, 16 Mbps CBR, limited range. ffprobe reports level 5.1 and progressive frames ([docs/notes/preprocess/videos_4k_ffprobe.txt](../docs/notes/preprocess/videos_4k_ffprobe.txt)).

    | Video | Frames | SHA-256 |
    |---|---|---|
    | ① `pexels_36330861` | 1,186 | `dc02ec11ee5774588b90233e5e364bac37f2b4e8e0be771fd023e06539a59a8e` |
    | ② `pexels_29130409` | 752 | `0cd2060610d94766a27bc109d646348801b4abb75276537dc4557631bc08e7e8` |
    | ④ `pixabay_180386` | 948 | `f886a60d179264fa6683188fd1a5b6ac2178d311a10c05f7380a246ad88d6a2b` |

    - Video ③ is left out: its source is 2560×1440.
    - **Channel assignment:** [stream-server/assignment_4k.txt](../stream-server/assignment_4k.txt). Channel n plays ①, ② and ④ in turn: ch1 ①, ch2 ②, ch3 ④, ch4 ①, and so on.
  - **Pipeline:** everything that `freeze-m1` fixes stays as it is: the binary, the settings of §5 and §8, the fixed clocks, the cooling and A10's software.
    - The letterbox scales 3840×2160 by 1/6 into the same 640×360 band as at 1080p.
    - B's `INTER_AREA` is then OpenCV's own. A4's one-pixel shift of the luma box applies only at a scale of exactly 1/3.
    - C's RGA3 scales by 1/6.
  - **Runs:** run kind `m1.1`, after `freeze-m1` and after the comparison runs of §12, in sessions of their own.
    - B and C at 1, 2, 4 and 8 channels, main condition (5 fps per channel), × 3 repetitions.
    - Blocks, order, cooldown gate and validity as in §9. The channel order is the shuffle of §9.1, with its seed.
    - **Unsaturated:** §6.2 is read with M1's `L` = 32, the only `L` there is, and that `L` was measured at 1080p. With it, all four counts are unsaturated: 8 × 5 = 40 inferences per second, below 4661/30 ≈ 155.37. So rule 3 of §9.3 applies to every run.
      - That 8 channels decode in real time at 4K rests only on the decode-only check below.
      - If B's or C's load makes the decoder drop frames, those runs are invalid. A point whose runs end invalid ×2 is reported as such. That is an accepted outcome of this design.
    - **Loop check:** before the first 4K session, the loop check of §3 runs for each 4K video (run kind `check`, with the session tool). A video that fails it is reported. The runs go ahead, and rule 3 still applies to them.
    - The sessions make A10's checks, and check the 4K videos' SHA-256 against the table above.
  - **Reported:** the metrics of §7 for each point, B and C side by side. Next to them come M1's main-condition values at the same channel counts, which are all in M1's main set.
    - Each result is labelled exploratory. No hypothesis is tested.
  - **Tensors, descriptive:** after `freeze-m1`, run kind `m1.1`, stored under `results/m1.1/`.
    - `tools/equivalence_dump` runs on the first 1,000 frames of ① at 4K (`file_source`), with its new run-kind argument set to `m1.1`, followed by `tools/equivalence_check.py`.
    - Its numbers are reported: MAE, PSNR and box counts, C against B and R against B, as in A5.
    - Nothing is judged. Equivalence under A5 is not claimed at 4K.
- Reason: the project owner chose to explore a 4K condition in a small design, labelled exploratory.
  - **Why 1, 2, 4 and 8 channels:** a decode-only functional check of the 4K set ([results/check/20261103T051010Z_4k_probe](../results/check/20261103T051010Z_4k_probe), `tools/decode_probe.sh`). It ran `--variant none` over RTSP once per count, with a 5 s warm-up and a 30 s window, at 27 °C, with the clocks fixed.

    | Channels | 1, 2, 4, 6, 8 | 10 | 12 | 16 |
    |---|---|---|---|---|
    | Every channel at 30 fps, drift ≤ 1 s, no decoder drops | yes | yes | no | no |
    | Largest drift (s) | 0 to 0.033 | 0 | 1.2 to 1.7 | 10.4 to 11.9 |
    | Decoder drops per channel | 0 | 0 | 0 to 58 | 286 to 494 |
    | dma-buf memory (MiB) | 166 to 1,310 | 1,531 | 1,862 | 2,747 |

    - At 10 channels, every channel started the window behind and caught up, at up to 31.3 fps. So 10 is close to the decoder's limit, and 8 leaves room for the load that B and C add.
    - Memory is not the limit. The decoder's dma-bufs took about 150 to 172 MiB per channel. The DMA zone (memory below 4 GiB) still had at least 87 MiB of free pages at every count, the least at 16 channels.
    - **Server warnings:** from 6 channels up, mediamtx logged "reader is too slow, discarding … frames".
      - At 6, 8 and 10 channels, these lines came before or early in the warm-up, never in a window.
      - At 12 and 16 channels, they went on through the window, where the decoder fell behind.
      - They are WAR lines, which A9's method records as warnings.
    - The check is not a measurement of `L` (§6.1): one run per count, short, without the cooldown gate.
    - Its memory samples got the header of the run they belong to, from the run's `summary.csv`, after the runs. The tool now writes it itself.
  - **Paths at 4K:** a functional check of B and C, so that a problem in their code would show before `freeze-m1`. A change after the freeze would invalidate comparison runs (§15).
    - `tools/equivalence_dump` on 10 frames of ① at 4K wrote every frame, and every harness check held: equal outputs on cores 0, 1 and 2, and bound outputs equal to `rknn_outputs_get`. The band is 640×360.
    - Its directory is [results/equivalence/20261103T052617Z_4k_paths](../results/equivalence/20261103T052617Z_4k_paths). This is the 4K path check, not an M1-4 or A8 run. It was moved there from `results/check/` because `equivalence_dump` wrote run kind `equivalence`. Its tensors are replaced by their SHA-256.
    - `bench_main` with B and with C, 1 channel, `file_source`, a 5 s window ([results/check/20261103T052617Z_4k_paths](../results/check/20261103T052617Z_4k_paths)): both exited normally, with no decoder or Q1 drops, and each processed all 25 sampled frames.
  - **Timeline:** the decode-only check ran from 05:10 to 05:16 UTC on 2026-11-03, and the path checks at 05:26. This entry was first written after them; its first commit is `84185a5`, at 05:38. No record of the design's parameters exists before that commit.
    - The channel counts rest only on the decode-only check.
    - The path checks' tensors were kept only as hashes and not compared. Their CPU figures are in the committed `summary.csv` files but were not examined.
  - **Tool changes:**
    - `stream-server/prepare_videos.sh` gains `SIZE` (16:9, even height) and `BITRATE`. With the defaults, it gives ffmpeg the same arguments as before, compared on the stream PC with a stand-in for ffmpeg.
    - **Encodings are not reproducible byte for byte:** three encodings of ① at 1080p with the same arguments on the stream PC gave three different files (SHA-256 `e12b26cd…`, `98ea3c48…`, `e3dd8398…`). So the 4K files, like the 1080p ones, are identified by their SHA-256 and kept on the stream PC and the board. They are not regenerated.
    - `tools/equivalence_dump` gains an optional run-kind argument: `equivalence` by default, or `m1.1`. Without it, the tool writes what it wrote before.
    - `tools/decode_probe.sh` and `stream-server/assignment_4k.txt` are new.
    - The session tool gets the 4K set, as an option, before these runs. It is not part of the shared core.
- Affected data: none.
- Release: none. The runs come after `freeze-m1`, whose Release is their reference time (§1).
