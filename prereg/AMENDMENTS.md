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

## A12 — 2026-11-04 — M1.1 designs of §13: how they are built and run
- Change: fixes what PROTOCOL §13 leaves open for its three optional M1.1 designs, before any of their runs. §13 itself registered the designs at `prereg-v1`. None of them is used for any verdict.
  - **B-direct** (variant `Bd`):
    - B's decoder-buffer access and its OpenCV preprocessing (`letterbox_nv12` with A4's interpolation) are unchanged. They write into input slots from the cached heap `system-dma32`, mapped by the CPU.
    - Each `prepare` brackets its writes with one `DMA_BUF_IOCTL_SYNC` START(WRITE) and one END(WRITE) on the slot.
    - Every context registers every slot with `rknn_create_mem_from_fd`, and `bind` is `rknn_set_io_mem`, as in C. So `bind` copies nothing.
    - N = 11 slots. Their border is filled with 114 once, as for B and C; here, after the registration.
    - The runtime keeps its default input flush (§8). Any cache maintenance it adds on these cached slots is part of what B-direct measures.
    - The code is a new file, `src/preprocess/cpu_direct.cpp`. B's and C's files do not change.
  - **B-direct's functional check** (`tools/bdirect_check`, run kind `check`, in `results/check/<UTC>_bdirect_check/`, with a `check.csv` whose header names the commit, the amendment and `freeze-m1`):
    - On the first 100 frames of ① at 1080p, B and B-direct prepare every frame, each on its own contexts, and context 0 runs on the bound input.
    - B-direct's input tensor, read back after the run, must equal B's byte for byte, and the outputs must be equal.
    - On frames 0 and 99, B-direct's bound outputs must also equal `rknn_outputs_get`, and contexts 1 and 2 must give context 0's outputs.
    - It runs after `amend-A12`. The session tool starts no B-direct session until a check has passed that was built from the same commit as the session's binary, with A12 or a later amendment. If it fails, B-direct does not run; the other two designs do not depend on it.
  - **B with K = 4** (arm `Bk4`): `bench_main --variant B --prep-threads 4`. It runs 4 preprocessing threads and N = K + 6 + 3 = 13 slots. Everything else is B.
  - **A55 only** (`bench_main --cores a55`):
    - The binary pins itself to cpu0–3 instead of cpu4–7 before any library starts a thread. So every decoder, MPP, preprocessing, NPU and runtime thread runs on the A55 cores.
    - The metrics thread, on cpu0 in M1, moves to cpu4, off the measured cores.
    - Process CPU then counts the metrics thread on the A76 cores, where M1 counted it on the A55 cores (about 0.1 s per run).
  - **The code change and §15:**
    - K and the metrics thread's CPU become fields of the shared core's `PipelineConfig`. `bench_main` gains `--prep-threads` and `--cores`, and now rejects an option it does not know. M1's sessions passed none.
    - §13's last sentence puts this change under §15, because it touches the shared core.
    - Read literally, §15 would invalidate M1's comparison and reference runs and A11's 4K runs, which used the shared core this change touches. A12 does not rerun them. That is a judgement made here, after M1's results, on two grounds:
      - they all ran before this change, on the code of `freeze-m1` and of A11, which stays tagged, and their verdicts and numbers rest on that code;
      - the change leaves the default path as it was. Without the new options, the threads, slots, pinning and queues are M1's.
    - **Evidence for the default path:** a short run of B at 2 channels during development had the same threads, affinities and `queues` record in `env.json` as M1's run `31_main_B_2ch_r1`. It was not kept. So after `amend-A12`, with the binary of the runs, one such run is kept in `results/check/<UTC>_default_b/` and compared the same way, before the first session.
    - The output of a default run changes in three places: `env.json` gains a `cores` line; the header names amendment A12; and `code_commit` changes.
    - Every M1.1 run of these designs uses the changed code, and is compared only with the controls of its own session.
  - **Sessions:** `tools/stream_session.py sweep <name>`, run kind `m1.1`, M1's 1080p video set, `results/m1.1/<UTC>_<name>/`. They run in this order, after the 4K sweep:

    | Sweep | Channels, condition | Arms | Repetitions |
    |---|---|---|---|
    | `k4` | 8, main | B, Bk4 | 3 |
    | `a55` | 8, main, every arm with `--cores a55` | B, C | 1 |
    | `bdirect_main` | 8, main | B, C, Bd | 3 |
    | `bdirect_aux` | 5, auxiliary (H2's point) | B, C, Bd | 3 |

    - The order within a repetition rotates. With three arms it is B→C→Bd, C→Bd→B, Bd→B→C; with two, B→C, C→B, B→C, as in §9.1.
    - Cooldown gate, validity rules 1 to 4 and the repeat of an invalid run are as in §9. Every point is unsaturated, so rule 3 applies to every run.
    - A run that is invalid twice is reported as such. On the A55 cores that is an accepted outcome.
    - **Where the arm is recorded:** in `runs.tsv` and the run's name. A Bk4 run's `summary.csv` header says variant B, and an A55 run's header does not name its cores. `env.json` records K, N and the cores of every run.
  - **Reported:** §7's metrics, each design side by side with its own controls. Nothing is judged.
    - B-direct: Bd against B and C. How much of C's saving remains when only the last copy is removed.
    - K = 4: Bk4 against B.
    - A55: B against C on the A55 cores.
- Reason:
  - §13 registered these designs but not how to build or run them. Their details are fixed here, before any of their runs.
  - **What was done before this entry:** while the code was written, there were short runs on the board, in `/tmp` and not kept: Bd, Bk4, C with `--cores a55` and B, at 2 channels from a file, with 6 s windows. They checked threads, slots, pinning and `env.json`. Their CPU figures were printed, but they come from 6 s windows and are not used. `tools/bdirect_check` also passed on 100 frames, in an earlier form and in this one, with its output in `/tmp`.
  - This amendment is written after M1's results are known. The designs, their channel counts and repetitions are §13's own. What is fixed here is how they are built, the order and the controls.
  - **Why the controls are rerun:** K and the cores change the shared core's code, so no comparison mixes binaries. A repetition's runs complete within one session (§9.1), so each comparison within a repetition is on one binary and one day.
- Affected data: none, by the judgement above. M1's runs and A11's 4K runs are not rerun, and no M1.1 run of these designs is compared with them.
- Release: `amend-A12`. Every run of these designs, B-direct's check included, starts after it.

## A13 — 2026-11-05 — M2: the values fixed before `freeze-m2`
- Change: fixes the values of the table in [M2.md](M2.md) §7, after the checks of §7 step 3. Nothing in M2.md changes.
  - **Code:** commit `7104241`, the merge of PR #36, built on the board without `-dirty`.
    - Arm C is `bench_main --variant C`. Arm Cp is `bench_main --variant C --post neon`.
    - Every run of `m2_main` adds `--stagger on`.
    - The session tool passes every M2 run `--run-kind m2` and `--freeze-m2-commit` with `freeze-m2`'s commit from the Registration table of `docs/notes/env.md`.
  - **Post-processing objects (§4):** the SHA-256 of `build/CMakeFiles/core.dir/src/core/yolov8_post.cpp.o` is `536b9351e78e815c09ba6e8c4f643e06e2e06454028163362ed5eaeaa4adbfa2`. That of `…/yolov8_post_neon.cpp.o` is `3956c1ba1f541bfe6ebd44b67cef4b67403eb1c1deb31cd842354d034bae7555`. Both stayed the same through full rebuilds during the implementation.
  - **Compiler, flags, libraries, model, clocks and cooling:** those of A10, A7 and A9; none differs.
    - With this build, `tools/inventory.sh` matches `docs/notes/inventory_m1.txt`. The m2-check session checked it before its runs.
    - The session fixed the clocks with `tools/set_perf_mode.sh`. The cooling is A9's heatsink and always-on mini fan.
  - **Equivalence (§4):** [results/check/20261105T061858Z_m2_equivalence](../results/check/20261105T061858Z_m2_equivalence), passed.
    - Videos ① to ④ had 1,186, 752, 714 and 948 frames, each with the SHA-256 of `docs/notes/preprocess/videos_manifest.tsv`, and none of their frames differed.
    - 12,500 synthetic sets, with 1,000,000 box decodings, had none that differed.
  - **Default path (§7 step 2):** [results/check/20261105T062050Z_default_c](../results/check/20261105T062050Z_default_c). This is C with the defaults, at 2 channels, main condition, from a file, with a 6 s window. Its 13 threads, their affinities and its `queues` equal those of M1's `32_main_C_2ch_r1`.
  - **m2-check (§7 step 3):** [results/check/20261105T062145Z_m2_check](../results/check/20261105T062145Z_m2_check), 06:21 to 06:38 UTC, ambient 28 °C. Both runs were valid.

    | Arm | Sampler rule | Stagger error | Phase spread | Process CPU | `/proc/schedstat` sum | Every CPU above 0 |
    |---|---|---|---|---|---|---|
    | C | held | 3.8 ms | 160.2 ms | 17.910 s | 21.665 s | yes |
    | Cp | held | 6.0 ms | 160.7 ms | 18.280 s | 21.705 s | yes |

    - `tools/m2_checks.py` passes all four checks.
    - The schedstat sums are at least process CPU, so M2's secondary metric is `/proc/schedstat` (§3.2). There is no fallback.
  - **The check runs' CPU (reported; §7 step 3; it changes nothing):** one run of each arm.
    - Process CPU was 2.1% higher for Cp (17.910 s against 18.280 s).
    - Cp's post-processing stage took 0.118 ms at the median, against 0.344 ms for C. Its NPU workers used 5.77 s against 6.69 s.
    - The decoder's threads (`dec-*`, MPP's parser and hal threads) used 11.88 s against 10.66 s. They do the same work in both arms.
- Reason:
  - M2.md §7 fixes these values before `freeze-m2`.
  - **Development runs**, all of run kind `check` and kept in `results/check/`:
    - `20261105T050152Z_m2_smoke`: C and Cp, 2 channels, 6 s windows.
    - `20261105T050252Z_m2_equivalence_smoke` and `20261105T060549Z_m2_equivalence_smoke`: the equivalence tool on video ② only. The second, with the revised tool, is recorded as incomplete.
    - They checked the options and records; their CPU figures come from 6 s windows and are not used.
  - **A board reset:** the board reset itself during a rebuild. Its journal's last entry is at 05:43:53 UTC, when the rebuild's ssh session opened, and it booted again at 05:44:15 (`boot_time_utc` in the checks' `env.txt`). No shutdown and no kernel warning were logged. Its cause is unknown. The checks above ran after it, and every session fixes the clocks again.
- Affected data: none.
- Release: `amend-A13`. `freeze-m2` tags the merge of the pull request that adds this entry. Its `created_at` is the reference time of M2's runs.

## A14 — 2026-11-18 — M4: the values fixed before `freeze-m4`
- Change: fixes the values of the table in [M4.md](M4.md) §8 step 4, after the checks of §8 step 3. Nothing in M4.md changes.
  - **Code:** commit `63febdfd9c84c9f30fb4c7ef56d1a052a0eeb38b`, the merge of PR #55, built on the board without `-dirty`. Every check of §8 step 3 ran with this build.
    - **Later commits, up to this entry:** in `src/`, `CMakeLists.txt`, `tools/` and `tests/` they change only tools that read records, which M4's runs do not use. These are `tools/m4_checks.py` (the verdicts below), `tools/cpu_breakdown.py` and `tools/m1_intervals.py` (exploratory notes on M1 and M2), their tests, and a fix to `tests/test_demo_render.py`.
    - **M4's runs** are built from a later commit of `main`, which each run's header names. Between `63febdf` and that commit nothing in §9's list may change. M4's report lists the files changed between them in `src/`, `CMakeLists.txt`, `tools/` and `tests/`.
  - **Arms (§1, §3.2):** both are `bench_main --variant C`.
    - **C8:** `--post neon --model models/yolov8n_i8.rknn` (SHA-256 `04a76611a785802b08703192455994e07bbfe4531821ae7ed35faf78fe1ec1ee`). This is Cp's post-processing, at conf 0.25 and NMS 0.45.
    - **C10:** `--post v10 --model models/yolov10n_i8.rknn` (SHA-256 `6a6442e1addbd1f08dbdb0772aeea1a992233b4f1db3bd451d20823522246f81`). This is `yolov10_post`, at conf 0.25 and `max_det` 300.
    - **Sampler:** every run of `m4_main` adds `--stagger on`. The runs of `m4_aux` do not.
    - **Freeze commit:** the session tool passes every M4 run `--run-kind m4`, and `--freeze-m4-commit` with `freeze-m4`'s commit from the Registration table of `docs/notes/env.md`.
    - **`calib_npu` (§3.1):** `tools/m4_calib.sh` runs on M1-3's tensors (`051d6f12…`, A7).
      - `P_cap` is measured on 3 cores, with `--with-post` and the arm's post-processing (`--post neon` for YOLOv8n, `--post v10` for YOLOv10n).
      - The raw limit is measured on 1 core, without post-processing.
    - **Accuracy passes (§2.5):** two kinds, at §2.3's settings, which the tools fix. The commands are in [m4_accuracy.md](../docs/notes/m4_accuracy.md).
      - The board's INT8 passes: `tools/m4_board_pass.py pass --model <model> --kind m4`.
      - The PC's FP32 passes: `tools/m4_fp32.py` through `tools/eval/pc_run.py --kind m4`.
  - **Post-processing objects (§8 step 3):** all 7 records that name them give the same objects.
    - **A13's two:**
      - `build/CMakeFiles/core.dir/src/core/yolov8_post.cpp.o`: `536b9351e78e815c09ba6e8c4f643e06e2e06454028163362ed5eaeaa4adbfa2`;
      - `…/yolov8_post_neon.cpp.o`: `3956c1ba1f541bfe6ebd44b67cef4b67403eb1c1deb31cd842354d034bae7555`.
      - Both are A13's, so M2 §4's equivalence check is not repeated.
    - **`…/yolov10_post.cpp.o`:** `b7642a3b11681ac79c4d0f0bb494f04988eb444bfdc9361f195983560f7cbeb8`.
    - **`…/pipeline_post.cpp.o`:** `28a471ca0bfa158e34d320c05c6d68950e665b62c9c43d38f7d03baec9c9a14a`. This is the shared function that chooses the post-processing.
    - **The accuracy tool's YOLOv8n rule,** `build/CMakeFiles/m4map.dir/src/m4/map_rules.cpp.o`: `94f5f4316f0505c5b1ac0d298258d578c910f697b475c112712a89dec38be35e`.
  - **COCO's files and the input manifest (§2.1, §2.2):**
    - **The two zips,** as in [tools/eval/coco.sha256](../tools/eval/coco.sha256):
      - `val2017.zip`: `4f7e2ccb2866ec5041993c9cf2a952bbed69647b115d0f74da7ce8f4bef82f05`;
      - `annotations_trainval2017.zip`: `113a836d90195ee1f884e704da6304dfaaecff1f023f49b6ca93c4aaae470268`.
    - **The input manifest:** [results/check/20261116T064917Z_m4_inputs](../results/check/20261116T064917Z_m4_inputs)'s `manifest.tsv`.
      - Its SHA-256 is `b29adf50175677fd031adbc2600f68a5115a075c80095b73d5b8665b35ffc444`, and that of its rows `5a63ad71886c7d670184bd1f91601a719d6d0f456d5e5c35bfa93c2418d69a69`.
      - Every accuracy pass is of this manifest.
  - **The evaluation image (§8 step 2):** `sha256:c676e76f95356ea1aac39a87694ac5a498207890922f2a01c9fdbedd773f250a`, as in `tools/eval/image_id.txt`.
    - **Base:** the converter image `sha256:3ea3796529342281fdb543b8911261112e5ab942798c2fea4363edf3eca67f8f`. That image is built on `python:3.10-slim-bookworm@sha256:5be5aaecb962a41567bc8041f1e9e3432cb085b037fe057f9a562630b2e4b370`.
    - **`pip freeze`:** it equals [tools/eval/constraints.txt](../tools/eval/constraints.txt), 25 packages, whose SHA-256 is `d62f5342280814927165b03de87c7d49e463e397bb786206186d576110af67e9`.
      - The record is `results/check/20261116T065949Z_m4_eval_image`, which passed every check of `tools/eval/build.sh`.
    - **Archive:** `m4-eval_c676e76f95356ea1.tar`, SHA-256 `ae9a7dcfd59672e8573cda16c7985c99542c0e8f0c4c2543ca9f38d3f53d71ab`, kept outside the repository. Its record is `results/check/20261116T064711Z_m4_eval_image_archive`.
  - **Results of §8 step 3:** all passed. On these records `tools/m4_checks.py` exits 0. The commands are in [m4_accuracy.md](../docs/notes/m4_accuracy.md#8-step-3-at-the-code-to-be-frozen).

    | Check | Record in `results/check/` | Result |
    |---|---|---|
    | Data | `20261116T064917Z_m4_inputs` | COCO's two zips as above. 5,000 images, each at its annotated size, and the 20 quantization images by name and SHA-256 |
    | Decoding | `20261117T081135Z_m4_decode` | 5,000 of 5,000 PNGs decoded to the manifest's bytes |
    | Rules, YOLOv8n | `20261117T081427Z_rules_yolov8n`, `20261117T081555Z_rules_check_yolov8n` | 50 of 50 lists equal. 74,996 of 75,000 values bit for bit; the other 4 differ by 1 ulp. No IoU within 1e-6 of 0.7 |
    | Rules, YOLOv10n | `20261117T081441Z_rules_yolov10n`, `20261117T081622Z_rules_check_yolov10n` | 50 of 50 lists equal. 75,000 of 75,000 values bit for bit |
    | Wiring, C8 and C10 | `20261117T081456Z_wiring_yolov8n`, `20261117T081504Z_wiring_yolov10n` | 10 of 10 frames equal, each |
    | Default path | `20261117T081656Z_m4_default_c` | the threads, affinities and `queues` of M1's `32_main_C_2ch_r1` |
    | Arms | `20261117T082036Z_m4_check` | both runs valid. Every frame follows M2's sampler rule, and each `env.json` records the arm's model SHA-256 and post-processing |
    | Build | the 20 places the records name it | `63febdf` in all. The data record's `9dbf814` has the same `tools/m4_prep.py` and evaluation image |
    | Objects | the 7 records that name them | as above |

    - **For §2.5's determinism check:** the dumps' `dump.sha256` are `74e4829065b022f15ec6d30271ea28bc226f7848977542b5221d4fe62568df97` (YOLOv8n) and `b7b4f93aa5fd3ee7e66ddd64752d96a053bb4dbe11e72d53fe99db9c4debd439` (YOLOv10n). The dumps are kept on the PC and the board.
    - **The arms check** ran from 08:20 to 08:37 UTC on 2026-11-17, at an ambient 25 °C.
  - **The check runs' values (reported; §8 step 3; they change nothing):** one run of each arm at 8 channels, main condition, staggered.
    - **Process CPU:** C8 17.560 s, C10 17.810 s.
    - **Stagger error:** 7.4 ms and 17.0 ms. **Phase spread:** 159.9 ms and 136.9 ms.
    - **Saturation labels:** `unknown`, since no `calib_npu` session of kind `m4` existed yet.
  - **The COCO evaluation's memory:** checked at 5,000 images before `freeze-m4`. It is not one of §8's checks; it is a check of the tool, which the accuracy notes required first.
    - The record is [results/check/20261117T134147Z_m4_coco_memory](../results/check/20261117T134147Z_m4_coco_memory), made on another PC at `3f6d24d`.
    - It passed: exit 0, with a peak of 4,883 MB under the container's 6 GB without swap. `tools/eval/pc_run.py` keeps that limit for `m4_coco.py`.
  - **Compiler, flags, libraries, clocks and cooling:** those of A10, A7, A9 and A13; none differs.
    - **Inventory:** the arms check's `inventory.txt` equals `docs/notes/inventory_m1.txt`. The session checked it before its runs.
    - **Clocks:** fixed with `tools/set_perf_mode.sh`.
    - **Cooling:** A9's heatsink and always-on mini fan.
- Reason:
  - M4.md §8 fixes these values before `freeze-m4`.
  - **Development runs:** all are of run kind `check` and kept in `results/check/`.
    - They are the exploration (`20261110T073848Z_m4_probe`, which M4.md discloses), and the tools' tests and trials of 2026-11-16 and 2026-11-17.
    - [m4_accuracy.md](../docs/notes/m4_accuracy.md) lists the trials. Their values are not used.
- Affected data: none.
- Release: `amend-A14`. `freeze-m4` tags the merge of the pull request that adds this entry. Its `created_at` is the reference time for M4's runs.

## A15 — 2026-11-18 — M3: the values registered with `freeze-m3`
- Change: registers [M3.md](M3.md) and fixes the values of its §8 step 2. Nothing in M3.md changes.
  - **One stage:** `prereg-m3`, `amend-A15` and `freeze-m3` all tag one commit, the merge of the pull request that adds M3.md and this entry.
  - **Code:** commit `0114c2431dfcc9927b77d5d266204960bae61b85`, which adds M3's tools.
    - **What runs:** both sweeps run code whose `src/`, `tools/`, `scripts/`, `tests/` and `CMakeLists.txt` equal this commit's. Both scripts check this against `freeze-m3`'s commit (M3 §3, §4).
    - **Against `freeze-m4`'s code:** `src/app/bench_main.cpp` gains run kind `m3`, `--freeze-m3-commit` and the header field `freeze_m3_commit`. Nothing else in `src/` or `CMakeLists.txt` changes.
    - **The tools:**
      - `tools/stream_session.py` gains the sweeps `m3_before` and `m3_after`.
      - `tools/check_prereg_order.py` gains M3's order checks.
      - `tools/setup/build_ffmpeg_rockchip.sh` fixes git's hash abbreviation at 8 characters (below).
      - New: `tools/m3_report.py`, `scripts/setup_board.sh`, `scripts/reproduce_m1.sh` and `tests/test_m3.py`.
  - **The runs (§1):**
    - **Options passed:** the session tool passes `--variant B` or `--variant C`, `--channels 8`, `--condition main`, `--warmup 20`, `--duration 120` and `--run-kind m3`. It also passes `--freeze-m3-commit`, with `freeze-m3`'s commit from the Registration table of `docs/notes/env.md`.
    - **Defaults:** it passes no other option, so `--post m1`, `--stagger off`, `--prep-threads 2`, `--cores a76` and `models/yolov8n_i8.rknn` stay at their defaults.
    - **Refusal:** `bench_main` refuses a run of kind `m3` that differs in any of these.
  - **Pinned inventory keys (§4):** the session stops if any of these 25 keys of `tools/inventory.sh` differs from [inventory_m1.txt](../docs/notes/inventory_m1.txt). The list is `M3_PINNED` in `tools/stream_session.py`:
    - `kernel`, `boot_firmware`, `rknpu_driver`, `rga_driver`;
    - `ffmpeg`, `ffmpeg_configuration`, and the four `h264_rkmpp_default_*`;
    - `librknnrt`, `librga`, and the three `rknn_header *`;
    - `cmake_build_type`, `core_cxx_flags`, `core_cxx_includes`, `bench_main_cxx_flags`, `bench_main_cxx_includes`, `core_cxx_defines`;
    - `model`;
    - `lib /usr/lib/librknnrt.so`, `lib /usr/lib/aarch64-linux-gnu/librga.so.2.1.0`, `lib /usr/lib/aarch64-linux-gnu/librockchip_mpp.so.0`.
  - **Not pinned:** the other 36 keys are recorded and reported. They are:
    - `os` and `ffmpeg_built_with`;
    - the four `CMAKE_*` tools;
    - `link_bench_main`, the link line, whose library paths follow from the pinned keys and the packages;
    - 29 `lib` lines of Ubuntu's packages and ffmpeg-rockchip's own libraries.
  - **Fresh-board markers (§3):**
    - `/var/lib/rk3588s-bench/setup-started`, which the setup writes before its first change;
    - `/opt/ffmpeg-rockchip`, `/opt/rknpu2`, `/usr/lib/librknnrt.so`, `/home/pi/.ssh/bench_stream` and `/etc/systemd/timesyncd.conf.d/50-bench.conf`.

    A setup that finds none of them records `fresh: yes`. The board's base image has none of them. The board as set up for M1 has all of them but the first.
  - **What the setup checks (§3):**
    - `librknnrt.so` 2.3.2 from rknn-toolkit2 `v2.3.2` (`rknpu2/runtime/Linux/librknn_api/aarch64/`): SHA-256 `d31fc19c85b85f6091b2bd0f6af9d962d5264a4e410bfb536402ec92bac738e8`, the same as the board's;
    - the model, from the Release `data-model-yolov8n_i8-04a76611`: `04a76611a785802b08703192455994e07bbfe4531821ae7ed35faf78fe1ec1ee`;
    - the video manifest `media/bench/manifest.tsv` from the stream PC: `fcd33374d77ede742ef3a646ac870f9ab6d8e16c605f31b122f607ec78cbbf93`, the same on the board and on the PC;
    - after the packages, the kernel and the SHA-256 of librga's and MPP's libraries, against `inventory_m1.txt`.
  - **What the setup installs (§3):**
    - `apt-get upgrade`, then `build-essential cmake git pkg-config libdrm-dev libopencv-dev binutils wget ca-certificates python3`, from the image's own sources (`mirrors.ustc.edu.cn`, `ubuntu-ports`);
    - ffmpeg-rockchip at `8b4385e985c0a50bf7d3f96cf3cb6d0b63c53e6c`, as for M1.
  - **The reset (§3):** FriendlyElec's `/usr/local/bin/firstboot`, then a reboot. It marks the data partition (`data=` on the kernel command line), and the next boot wipes it.
  - **Development runs before this registration (§8 step 1),** all of run kind `check`:
    - **Smoke test** [results/check/20261118T091529Z_m3_before_smoke](../results/check/20261118T091529Z_m3_before_smoke). It ran before the tools' commit (`db0308f…-dirty`). All 3 repetitions completed, with no invalid run, and the inventory equalled M1's.
    - **Chroot test 1,** 09:22–09:27 UTC:
      - The packages step passed.
      - My guard stopped the test in the ffmpeg step. The data partition, which the live root file system shares, had fallen below 700 MB.
      - An earlier version of the script wrote its record inside the chroot, as run kind `m3`. So the record is not kept, and these lines stand for it.
    - **Taking down chroot test 1** unmounted the host's `/sys` and `/dev` submounts, among them debugfs, `/dev/pts` and the cgroup tree.
      - **Why:** the test had bound `/sys` recursively, and the host's mounts are shared.
      - **Repair:** the board was rebooted at 09:41 UTC to restore them. No measurement was running, and nothing on disk changed.
      - **Since then:** the tests mount fresh, private file systems in the chroot.
    - **Chroot test 2,** [results/check/20261118T093021Z_m3_setup](../results/check/20261118T093021Z_m3_setup): every step passed up to the record. The record failed because debugfs was missing after test 1's teardown.
    - **Chroot test 3,** [results/check/20261118T094315Z_m3_setup](../results/check/20261118T094315Z_m3_setup): every step passed up to the record. The record failed on one pinned key, `ffmpeg`:
      - M1's build reads `ffmpeg version 8b4385e9`; this one read `8b4385e`.
      - The commit is the same. FFmpeg's version string is git's abbreviated hash, and git chooses its length from the clone's size. M1's source folder was a larger clone than the script's shallow fetch.
      - **Fix:** `tools/setup/build_ffmpeg_rockchip.sh` now fixes the abbreviation at 8.
    - **A review of the tools and of M3.md,** before the tools' commit. It led to these changes:
      - the clone's commit, and the check that its code equals `freeze-m3`'s;
      - the setup's own marker, written before its first change;
      - only the latest setup record counts;
      - the network and stream PC steps come first;
      - apt never prompts, and waits for another apt;
      - early checks of the image's pinned keys;
      - the boot time from `/proc/stat`;
      - the scripts committed as executable;
      - no outcome before a sweep is complete, and `m3_before` reported as descriptive;
      - the hedges on §2's probabilities.
    - **At the tools' commit `0114c24`:**
      - **Smoke test** [results/check/20261118T100910Z_m3_before_smoke](../results/check/20261118T100910Z_m3_before_smoke): all 3 repetitions completed, with no invalid run, and the inventory equalled M1's.
      - **Chroot test 4,** [results/check/20261118T101519Z_m3_setup](../results/check/20261118T101519Z_m3_setup): result `ok`, on a fresh board, in 550 s.
        - **Steps:** packages 201 s, librknnrt 2 s, ffmpeg 310 s, model 1 s, build 32 s, record 4 s.
        - **Pinned keys:** every one equals M1's.
        - **Keys that differ:** 10 of the 61:
          - `os` (Ubuntu 24.04.5 against 24.04.4);
          - libavutil's SHA-256 (libavcodec, libavformat and libswresample are byte-identical to M1's);
          - libc6 `2.39-0ubuntu8.12` against `8.9`, in 5 lines;
          - the security updates of libX11, liblzma and zlib.
        - **Not covered:** a chroot cannot test the network and stream PC steps (`--no-services`).
    - **Their values:** the smoke tests' CPU and latency come from 10 s windows. They are not compared with anything.
- Reason: M3's plan and its values are registered together, after the tools were written, tested and reviewed, and before any of M3's runs.
- Affected data: none.
- Release: `amend-A15`. `prereg-m3` and `freeze-m3` tag the same commit, the merge of the pull request that adds this entry. `freeze-m3`'s `created_at` is the reference time for M3's runs.

## A16 — 2026-11-20 — M5: the values fixed before `freeze-m5`
- Change: fixes the values of [M5.md](M5.md) §8 step 4, and changes one sentence of its §8 step 3 (the check "The tool against `bench_main`"). Nothing else in M5.md changes.
  - **The package (§8 step 2):** `rkzc_0.1.1-1_arm64.deb`, SHA-256 `d88a13c82781dd256f742aacfd87d9ad41016393361982180efeb183975a8241`, built on the board by `lib/packaging/build_deb.sh` from commit `3fdc5caa9ea98e214a4cd4eebe734a61ceffc5d8`. [lib/measure/package.txt](../lib/measure/package.txt) records it, and the tool refuses any other file.
    - **Its source against 0.1.0's** (`rkzc-v0.1.0`, built from `0b6f5bd`): `lib/tools/rkzc_detect.cpp` gains the three fields and their comment, and `lib/CMakeLists.txt` the version. `lib/src/`, `lib/shim/`, `lib/include/`, `lib/python/`, `lib/packaging/` and `lib/dist/` are unchanged.
    - **Its files against 0.1.0's:** both hold 30. `librkzc.so.0.1.0` is now `librkzc.so.0.1.1`. Six differ in content: `rkzc-detect`, `rkzc-check`, `librkzc.so.0.1.1`, and `control`, `md5sums` and `copyright`, which carry the version, the checksums and the source commit. The other 24 are byte-identical, the four FFmpeg libraries, the unit file and the shipped configuration among them.
    - **Fixed when:** the SHA-256 was committed (`a44d742`) before the service was first started. Before that the package's own test `lib/tests/test_rkzc_detect.py` ran the installed program directly, not as the service: 32 of its 33 checks passed. The one that failed needs a video with a frame that has no detection, and the video given had none.
    - **The package did not change after it was fixed.**
  - **The tool (§8 step 2):** commit `a8eaa31dbfd4afbcd7c77882fb41014996929e2a`, under [lib/measure/](../lib/measure/README.md).
    - **Its content:** one SHA-256 over every `.py` of `lib/measure/` and `package.txt`: `5f419c1ce89d0416dbe979fba001811dc9f3c94a93b09941738244959ea1beba`. Every record's header carries it (`tool_sha256`) next to the commit the board had checked out (`tool_commit`). The sweep's sessions run from the commit that adds `freeze-m5`'s row to `docs/notes/env.md`, which is later than `a8eaa31`; the content must be this one.
    - **One change after it was fixed.** The tool was first fixed at `a44d742` (content `c67fdcde…`), before the service's first start. The first check session showed a defect of the check "The tool against `bench_main`", and `a8eaa31` fixes it (below). Every check of step 3 was then redone with `a8eaa31`.
    - **What it takes from the registered `tools/stream_session.py`,** imported and unchanged: the session's start, the gate, the clock exchange, the idle baselines, every B and C run with its judgement and its repeat, `runs.tsv` and `started.tsv`, the server log's parsing with rule 4's events, and the bookkeeping of repetitions across sessions.
      - **How:** the tool adds one entry, `m5`, to the imported table `SWEEPS`, with M3's kind. So a session keeps to M3 §4's pinned inventory keys, and B and C run with M3 §1's options and M3 §3's validity rules.
      - **Options passed to `bench_main`:** `--variant`, `--channels 8`, `--source`, `--condition main`, `--warmup 20`, `--duration 120`, `--run-kind check`, `--repetition` and `--out`, as §1 says. As in M3's runs, also `--freeze-commit` with `freeze-m1`'s commit and `--freeze-m3-commit` with `freeze-m3`'s. These two are header fields.
    - **What it implements itself:**
      - the checks that the `.deb` is the recorded one, that each of its files equals the installed one, and that the model is M1's;
      - `/etc/rkzc/detect.toml`, written from the package's shipped copy with only `sources` changed;
      - P4's drop-in, its removal and the `daemon-reload` after each change;
      - the service's run: the start, the socket's reader, the timed reads, the one-second samples, the stop and the unit's journal;
      - §3's values and §4's rules 1, 2, 3 and 5 for the service, computed from the run's stored files (`m5_metrics.py`);
      - rule 4 for the service: the reading of the server's log follows the registered tool's `judge` line for line, and the events come from its `server_events`, from `t_svc` to `t_e`;
      - M5's header beside every run, the order of the arms, and the session's own record;
      - the order check (`m5_order.py`), which uses the registered order checker's two readers;
      - the summary (`m5_summary.py`): the runs that count, §2's verdicts and §6's values. It was written, reviewed and committed with the tool, before any run. It runs on the PC, since §3's latency percentiles need numpy and the board has none.
  - **The change to §8 step 3:** "the tool also reads `bench_main`'s utime + stime from `/proc` once per second" becomes "ten times per second". The rest of the check stands: interpolation at `bench_main`'s `window_start_utc` and `window_end_utc`, and agreement within 0.1 s.
    - **Why:** `bench_main` exits within half a second of its window's end. In the first check session its last one-second read came 0.59 s before the end and none came after, so there was nothing to interpolate between.
    - **This is the thread that reads `bench_main`'s CPU in a check session.** It does not run in the sweep, and nothing of the service's measurement changes.
  - **Readings of M5.md** where its text leaves a choice. The tool had them before its first run:
    - **"Between `t_svc` and `t_e`" in rule 2:** the tool reads until `t_e` + 2 s, so a connection the service closes in those 2 s, or a main process that is another one at the stop, also makes the run invalid.
    - **A journal without the program's stop log** makes the run invalid by rule 2: the two client counters could not be read.
    - **A line that is not JSON,** other than a last line cut by the tool's own close, makes the run invalid by rule 2: the tool has not read every line.
    - **A clock that could not be read** counts as below its fixed value (rule 1).
    - **The one-second samples "in the window"** are those from `t_w` to `t_e`, both included, as `bench_main` takes its own.
    - **Receive drift:** a channel's lines "in the window" are those with `out_ns` inside it, as for the latency and the rate.
    - **Decoder drops:** the difference of `decode_errors` between the two lines is added when it is positive.
    - **Arrival phases** are those of `tools/m1_phases.py`, which M2 §3.1 names: each channel's phase and drift, and the run's spread, with `capture_ns` as `t0`, over the lines with `out_ns` in the window. For B and C the summary calls that script.
    - **Per-thread CPU and the share on cpu0–3** come from the one-second samples, a few milliseconds after the timed reads. Process CPU comes from the timed reads.
    - **The sweep's sessions** are those that ran this tool's content and this package. After a change to either, the sweep starts again, and the earlier sessions stay where they are.
    - **The CV flag** of §9.3 is raised above 10%, for B and for the hypothesis's arm.
    - **A check session's ambient temperature** is recorded as "not reported": the operator reports it for the sweep's sessions.
  - **The checks of step 3,** all of run kind `check`, naming `prereg-m5` and the package's SHA-256.
    - **With the tool at `a44d742`:**
      - **Detections,** [20261120T081511Z_m5_detections](../lib/measure/results/20261120T081511Z_m5_detections): passed. This was the service's first start.
      - **One run of each arm,** [20261120T081615Z_m5_check](../lib/measure/results/20261120T081615Z_m5_check): all four valid. The tool against `bench_main` could not be judged (above).
    - **With the tool at `a8eaa31`,** all redone:
      - **Detections,** [20261120T082514Z_m5_detections](../lib/measure/results/20261120T082514Z_m5_detections): passed. `rkzc-jsonl`, built from `3fdc5ca`, and the service read the same file once, with 1 frame in 6: 126 frames, and the same 1,810 detections.
      - **One run of each arm** with a 10 s window, [20261120T082607Z_m5_check](../lib/measure/results/20261120T082607Z_m5_check), in the order B, C, P, P4: all four valid at the first attempt.
      - **Fields:** in both service runs of each session, `out_ns − capture_ns` is positive for every line, `capture_ns` increases with `seq`, and no channel's `decode_errors` decreases.
      - **The tool against `bench_main`,** on the C run: 1.635 s from the tool's reads against `bench_main`'s 1.630 s. The difference of 0.005 s is within 0.1 s.
      - **Affinity:** all 32 threads of the P run were allowed cpu0–7, and all 32 of the P4 run cpu4–7 only, at both ends of the window.
      - **The timed reads** came 0.5 ms after `t_w` and `t_e` in both service runs, against the limit of 50 ms.
    - **Their values,** from 10 s windows. Nothing M5.md fixes changes because of them (§5):

      | Session | Arm | Process CPU (s) | Latency p50 (ms) | Latency p99 (ms) | Share of P's CPU on cpu0–3 |
      |---|---|---|---|---|---|
      | `a44d742` | B | 3.65 | 35.4 | 49.0 | |
      | `a44d742` | C | 1.58 | 24.7 | 32.1 | |
      | `a44d742` | P | 2.26 | 30.8 | 44.7 | 0.52 |
      | `a44d742` | P4 | 1.62 | 29.5 | 44.7 | 0.00 |
      | `a8eaa31` | B | 3.69 | 36.7 | 49.2 | |
      | `a8eaa31` | C | 1.63 | 30.5 | 44.7 | |
      | `a8eaa31` | P | 2.25 | 30.9 | 47.3 | 0.49 |
      | `a8eaa31` | P4 | 1.50 | 30.4 | 44.2 | 0.00 |

    - **Every run:** inference at 5.000 fps on every channel, no drop, no decoder drop, and the service's values recomputed by the summary equal the session's.
    - **The unit's journal is stored with each service run.** It holds systemd's own line of the CPU time the service consumed. The tool does not read that line, and I have not read it.
  - **Reviews of the tool before it was fixed:** three passes by an independent reviewer. The first asked for changes (6 defects, 9 risks), the second too (2 defects in the bookkeeping across sessions, 3 smaller ones), and the third approved `a44d742`. The change of `a8eaa31` came after and was not reviewed separately; it is the one interval.
  - **Library versions, clocks and cooling:**
    - **Clocks:** A7's values, as `tools/set_perf_mode.sh` set and read them.
    - **Cooling:** A9's.
    - **Inventory:** every key M3 §4 pins equals `docs/notes/inventory_m1.txt`. Ten other lines differ, as after M3's reset (A15): `os`, libavutil's file, and Ubuntu's libc, libX11, liblzma and zlib.
    - **The package's own libraries** in `/opt/rkzc/lib` are hashed per session (`package.txt` in the session's record). `bench_main` does not load them.
- Reason: M5 §8 step 4. The package and the tool were fixed before the service's first start, and the checks of step 3 passed before `freeze-m5`.
- Affected data: none. The first check session's runs are kept and listed above; no run of the sweep has been made.
- Release: `amend-A16`. `freeze-m5` tags the same commit, the merge of the pull request that adds this entry; its `created_at` is the reference time for M5's runs. The Release `rkzc-v0.1.1`, with the file of the SHA-256 above, is published before both (§8 step 2).

## A17 — 2026-11-24 — M6: the values fixed before `freeze-m6`
- Change: fixes the values of [M6.md](M6.md) §8 step 4, and states four readings of its text. Nothing in M6.md changes.
  - **The code (§8 step 2):** commit `7d8e520207cf6f8c18ff8d1d2a3e49e7ef7443e0`. `freeze-m6`'s commit is the merge of the pull request that adds this entry; its `src/`, `tools/`, `tests/` and `CMakeLists.txt` equal that commit's.
    - **Its diff from `freeze-m3`** in the registered paths: 27 files, 4,463 lines added and 80 removed.
      - **New:** `src/preprocess/rga_copy.cpp` (Rc), `src/preprocess/cpu_yuv.cpp` (By), their header `src/preprocess/m6_backends.h`; `tools/m6_arms_dump.cpp`, `m6_by_check.cpp`, `m6_unchanged.sh`, `m6_rc_trace.sh`, `m6_checks.py`, `m6_report.py`, `tools/setup/fetch_libyuv.sh`; `tests/test_m6.py`, `tests/test_m6_report.py`.
      - **Changed, as §8 step 2 lists:** `src/app/bench_main.cpp`, `src/core/pipeline.cpp` and `.h`, `report.cpp` and `.h`, `run_rules.cpp` and `.h`, `CMakeLists.txt`, `tools/stream_session.py`, `tools/check_prereg_order.py`, `tests/test_core.cpp`.
      - **Changed, for By's check "with `tools/equivalence_dump` (By in C's place)":** `tools/equivalence_dump.cpp`, `equivalence_check.py`, `equivalence.sh` and `equivalence_holdout.sh` take By in C's place when asked; without that option they do what they did.
      - **`scripts/` is not changed.** `scripts/setup_board.sh` and `reproduce_m1.sh` refuse `main`, whose code no longer equals `freeze-m3`'s; M3 runs from `v0.3-m3` and M5 from `v0.5-m5` ([reproduce.md](../docs/notes/reproduce.md)).
    - **What changes for B, Bd, C and Cp with the new options at their defaults:**
      - every header gains `prereg_m6_commit`, `freeze_m6_commit` and `libyuv_commit`, and `env.json` a key `m6` with the arm's name;
      - a frame's record grows from 104 to 112 bytes, about 38 kB more over a run at 8 channels;
      - each NPU worker takes one more branch per inference, on the option of Ct;
      - `bench_main` links By's library and libyuv statically, so the binary and the inventory's `link_bench_main` line differ. No shared library is added;
      - `frames.csv`'s columns, `summary.csv`'s rows and `threads.csv` are as before. Ct alone has one more column and six more rows.
    - **The 13 objects of §8 step 2** have the same SHA-256 in this build and in a build of `freeze-m3`'s commit made on the board with the same compiler and flags ([20261124T044548Z_m6_unchanged](../results/check/20261124T044548Z_m6_unchanged)). One list, since both sets are equal:

      | Source | SHA-256 of its object, both builds |
      |---|---|
      | `src/preprocess/cpu_common.cpp` | `19c900cd0840c36283a85e0c07b7fe34bbc3c91d1dd7235234411056bb29cc96` |
      | `src/preprocess/cpu_copy.cpp` | `e99d168f8074409d14c5b0ab89c0de9d1a15e8c77f1a071850227bd4eb4bb2a7` |
      | `src/preprocess/cpu_direct.cpp` | `ee2d7443b5d9e013f44618e993ccbdd2f217e9af93507f21060686299aff93ab` |
      | `src/preprocess/cpu_sw.cpp` | `496ca33bed61058b4911338e624fe477b5870b87193a7b50157d3cd1a4d7c84b` |
      | `src/preprocess/zero_copy_rga.cpp` | `7ddd5060afe89910654feddc79ad0211cdb469b10971cbf59456b437613fab06` |
      | `src/core/npu.cpp` | `01db03a89aeeefa94e4ca84ea9fc2da1fef2519ff469cdc926b6a9020f929c14` |
      | `src/core/video_source.cpp` | `99f57937d8fc579899aa735dc4feda4417782325e2708a1ca338162d59499e3a` |
      | `src/core/slot_pool.cpp` | `08173a21643a49ee88027aef5c6efc26258fe359e7b47aad14fbd6c34f2d35da` |
      | `src/core/metrics.cpp` | `dd8bc752ab0c405aaa8226060e9ed1a7a05bcb2e1b39b29b2addc9deeb8e0e2d` |
      | `src/core/pipeline_post.cpp` | `28a471ca0bfa158e34d320c05c6d68950e665b62c9c43d38f7d03baec9c9a14a` |
      | `src/core/yolov8_post.cpp` | `536b9351e78e815c09ba6e8c4f643e06e2e06454028163362ed5eaeaa4adbfa2` |
      | `src/core/yolov8_post_neon.cpp` | `3956c1ba1f541bfe6ebd44b67cef4b67403eb1c1deb31cd842354d034bae7555` |
      | `src/core/yolov10_post.cpp` | `b7642a3b11681ac79c4d0f0bb494f04988eb444bfdc9361f195983560f7cbeb8` |

      The reference build is a git worktree of `freeze-m3` with `tools/m6_arms_dump.cpp` copied in and one target for it appended to its `CMakeLists.txt`; nothing else of it differs.
  - **Rc's heap:** `system-dma32`. RGA3 writes into it, so §1's fallback is not taken.
  - **libyuv's commit:** `0ec9f68d82634f6f3414aaaf780e03f9edf002b3`. It builds on the board, so §1's fallback is not taken.
    - **How it is added:** `tools/setup/fetch_libyuv.sh` puts a git checkout of that commit in `third_party/libyuv`, which is not in the repository; the configure step runs the script when the directory is missing, and stops unless it is a clean checkout of that commit.
    - **As built:** libyuv's CMake found Ubuntu's libjpeg and so defines `HAVE_JPEG`, its default; GCC 13 cannot compile its SME files, which it then leaves out (`LIBYUV_DISABLE_SME`). By calls no function these touch. The flags are in the record (`flags_libyuv.txt`).
  - **By's equivalence: passed. By is run.** A5's criteria against B on the 1,000 frames of video ①, R computed in the same run ([20261124T044621Z_m6_by_equivalence](../results/check/20261124T044621Z_m6_by_equivalence)):

    | Criterion | By against B | R against B | |
    |---|---|---|---|
    | Tensor MAE | 1.5143 | 4.5384 | pass |
    | Tensor minimum PSNR | 42.16 dB | 28.52 dB | pass |
    | (a) pairs within both thresholds | 51.34% | 28.37% | pass |
    | (b) frames agreeing | 62.30% | 34.70% | pass |
    | (c) total boxes | 15,711 against B's 15,740: 0.18% | | pass (≤ 2%) |
    | Border 114 in every tensor | yes | | pass |

    - **By's tensors are darker than B's** in red and blue: the mean difference is −1.82, +0.16 and −2.18 for R, G and B. On synthetic frames with flat chroma, By's luma is 0.65 of a byte below B's on average, and never more than 2 bytes. M6's disclosure expected about half a level.
    - **Held out,** as A8 made it for C, on every frame of videos ②, ③ and ④ pooled ([20261124T044731Z_m6_by_holdout](../results/check/20261124T044731Z_m6_by_holdout)): every criterion holds on the pool (MAE 1.4871 against R's 5.8061; (a) 51.90% against 25.78%; (b) 64.50% against 32.81%; (c) 0.91%). Video ④ alone has (c) at −2.12%, over the 2%; ② and ③ pass alone. These are reported and do not decide (§8 step 3).
    - **Where the check ran:** the board has no numpy since its reset (M3), and `tools/equivalence_check.py` needs it. The dumps were made on the board; the check ran on the PC, with the same script at the same commit, over the record's files copied from the board, their SHA-256 checked against the record's. Each record keeps the board's failed output beside the PC's.
  - **Results of the other checks of step 3,** all of run kind `check`, all from the build `7d8e520`, judged by `tools/m6_checks.py` ([20261124T052943Z_m6_checks](../results/check/20261124T052943Z_m6_checks)): every check passes.
    - **Unchanged arms:** the objects above. For B, Bd, C and Cp, the input tensors, the NPU's outputs and the detections on the first 100 frames of video ① have the same SHA-256 from this build and from the reference build. The run of C with every option at its default, at 2 channels, has the threads, affinities and queues of M1's `32_main_C_2ch_r1`.
    - **Rc:** the input buffer the NPU read and the outputs equal C's, byte for byte, on the same 100 frames.
    - **Rc's trace** ([20261124T050450Z_m6_rc_trace](../results/check/20261124T050450Z_m6_rc_trace)), 1 channel, 10 s: 100 `DMA_BUF_IOCTL_SYNC` calls from the three NPU workers, all on one slot's dma-buf (`system-dma32`, 1,228,800 bytes), in pairs; 90 of them for the 45 frames bound inside the trace without its first and last half second. No other thread of the pipeline made one.
    - **By's functional check:** channel order, band, border and orientation as required; the last row and the last column equal to B's on 21 frames; and the two checks added in stage 2: each box in its place (4 stripes of 4), and the rest of the luma plane within 2 bytes of B's.
    - **Cpf:** all pipeline threads allowed cpu0–7 and the metrics thread cpu0 only, at the window's start and end.
    - **Ct:** 400 timed calls for 400 inferences in the window, the smallest 1.03 ms of thread CPU.
    - **One run of each arm** with a 10 s window ([20261124T050543Z_m6_check](../results/check/20261124T050543Z_m6_check)), in the order B, Bd, Rc, C, By, Cp, Cpf, Ct after the default run: all valid at the first attempt, no drop. Nothing M6.md fixes changes because of these values (§5):

      | Arm | Process CPU (s) | Latency p50 (ms) | Latency p99 (ms) |
      |---|---|---|---|
      | B | 3.67 | 35.6 | 47.0 |
      | Bd | 3.56 | 21.2 | 34.4 |
      | Rc | 1.66 | 27.1 | 33.8 |
      | C | 1.55 | 29.7 | 43.8 |
      | By | 2.64 | 33.1 | 51.8 |
      | Cp | 1.48 | 29.7 | 42.7 |
      | Cpf | 2.31 | 32.6 | 47.8 |
      | Ct | 1.66 | 29.4 | 44.0 |

      Ct: 0.465 s of the NPU workers' thread CPU inside `rknn_run`, 28.0% of its process CPU; 1.163 ms per call against 15.6 ms of wall time.
    - **The order of step 3:** the records of the unchanged arms, of By's functional check and of By's equivalence were committed at 2026-11-24T05:04:29Z (`743bde7`). Rc's trace started at 05:04:51Z and the arms' runs at 05:15:56Z.
  - **Readings of M6.md** where its text leaves a choice or cannot be met as written:
    - **"None on the decoder's buffer" (§8 step 3, Rc's trace).** MPP makes `DMA_BUF_IOCTL_SYNC` calls of its own on its buffers, from the decoder's threads (`dec-<n>` and `mpp_dec_parser`): 600 in 10 s for one channel, on two 2 MB bitstream buffers and four 4,177,920-byte frame buffers. They are in every arm; a trace of C made the same way has the same 600 and no other sync call. No arm can show none. The sentence is read with §1 ("Rc and C do not read it with the CPU") as being about the arm: of the calls made outside the decoder's threads, two per frame, on a slot, from an NPU worker, and none on any other buffer; the decoder's threads touch no slot; and C shows the decoder's calls at the same rate within 10% and nothing else. B's count was taken per thread in the same way ([preprocess.md](../docs/notes/preprocess.md)).
      - **Not in M6's disclosure:** these calls are part of every arm's process CPU, in the decoder's threads. §1's table of syncs counts the slot only.
      - **The flags are not read from the trace.** The board's strace does not print them. That Rc's two calls are START(READ) and END(READ) is shown by its code.
    - **"Cpf drops frames" (§5) and the 1-point rule (§2).** Both apply. M6-H2's practical verdict is *indeterminate* if a run of Cpf that counts has a frame dropped from Q1 in the window, whatever Cp's drop rate; and, as for all three hypotheses, if the mean drop rates of two named arms differ by more than 1 percentage point.
    - **A threshold met exactly** counts as met: the report compares exact fractions of the records' decimal values, with no tolerance, for §2's ratios, the 1-point rule, 4.75 fps and the CV flags.
    - **Latency and rates in the report** are computed from `frames.csv` and the window's counts, not from `summary.csv`'s rounded values; the last digit can differ from the earlier reports' tables.
  - **Fixed for a rerun (§5, §9):** the session tool and the report count no run of a session named in `M6_SUPERSEDED`, and a session's code must equal that of the stage named in `M6_CODE_TAG` (`freeze-m6`). An amendment that reruns the sweep sets both.
  - **Earlier rounds of the checks.** They were made three times, since the tools changed twice after a round (§9, "redone from scratch"). `src/` did not change between the rounds.
    - **At `1bad68a`:** the four checks without a window passed. Rc's first trace showed MPP's sync calls, and the judge was changed to the reading above.
    - **At `de64e35`:** every check passed ([20261124T044115Z_m6_checks](../results/check/20261124T044115Z_m6_checks)). `tools/check_prereg_order.py` then took the dump's detection files, named `.jsonl` and without a header line, for results files with no header; the dump now names them `.detections`.
    - **Their records are kept** in `results/check/` (20261124T0111Z to T0417Z). In the two earlier `m6_unchanged` records those files were renamed, their content unchanged (`renamed.txt`). By's equivalence gave the same tensors and the same values in all three rounds.
  - **Library versions, clocks and cooling:** as A7, A9, A10 and A15. Every key M3 §4 pins equals `docs/notes/inventory_m1.txt`; 22 other lines differ: `os`, `link_bench_main` (By's library and libyuv), and the files of libavutil and Ubuntu's libc, libX11, liblzma and zlib, as after M3's reset.
- Reason: M6 §8 step 4. The checks of step 3 passed before `freeze-m6`, and the readings are stated before any run of the sweep.
- Affected data: none. The check records of the earlier rounds are kept and listed above; no run of the sweep has been made.
- Release: `amend-A17`. `freeze-m6` tags the same commit, the merge of the pull request that adds this entry; its `created_at` is the reference time for M6's runs.

## A18 — 2026-11-27 — M7: the values fixed before `freeze-m7`
- Change: fixes the values of [M7.md](M7.md) §8 step 4, records that the counters' check failed and what follows from it by M7 §5, and states the readings of its text. Nothing in M7.md changes.
  - **The code (§8 step 2):** commit `e2a6b7f4b8fc3913b06247c24e37c60860abc182`. `freeze-m7`'s commit is the merge of the pull request that adds this entry; its `src/`, `tools/`, `tests/` and `CMakeLists.txt` equal that commit's.
    - **Its diff from `freeze-m6`** in the registered paths: 26 files, 7,803 lines added and 84 removed.
      - **New:** `src/core/snapshot.cpp` and `.h` (the snapshots), `src/core/perf_counters.cpp` and `.h` (the counters); `tools/m7_idle_snapshot.cpp`, `m7_cpu0_check.cpp`, `m7_output_probe.cpp`, `m7_probe.py`, `m7_unchanged.sh`, `m7_checks.py`, `m7_report.py`; `tests/test_m7.py`, `test_m7_checks.py`, `test_m7_probe.py`, `test_m7_report.py`.
      - **Changed, as §8 step 2 lists:** `src/app/bench_main.cpp`, `src/core/pipeline.cpp` and `.h`, `report.cpp` and `.h`, `run_rules.cpp` and `.h`, `CMakeLists.txt`, `tools/stream_session.py`, `tools/check_prereg_order.py`, `tests/test_core.cpp`.
      - **`scripts/` and `lib/` are not changed by M7's commits.** M6's sweep runs from `v0.6-m6` ([reproduce.md](../docs/notes/reproduce.md)).
    - **What changes for the earlier arms with the new options at their defaults:**
      - every header gains `prereg_m7_commit` and `freeze_m7_commit`, and `env.json` a key `m7` with the arm's name, empty outside M7's runs, and the two options, in every run;
      - the metrics thread takes a few more branches per tick, on the two options;
      - `frames.csv`, `summary.csv` and `threads.csv` are as before. The options write files of their own (§1).
    - **The 15 objects of §8 step 2** have the same SHA-256 in this build and in a build of `freeze-m6`'s commit (`b3381dc`) made on the board with the same compiler and flags ([20261127T023424Z_m7_unchanged](../results/check/20261127T023424Z_m7_unchanged)). One list, since both sets are equal:

      | Source | SHA-256 of its object, both builds |
      |---|---|
      | `src/preprocess/cpu_common.cpp` | `19c900cd0840c36283a85e0c07b7fe34bbc3c91d1dd7235234411056bb29cc96` |
      | `src/preprocess/cpu_copy.cpp` | `e99d168f8074409d14c5b0ab89c0de9d1a15e8c77f1a071850227bd4eb4bb2a7` |
      | `src/preprocess/cpu_direct.cpp` | `ee2d7443b5d9e013f44618e993ccbdd2f217e9af93507f21060686299aff93ab` |
      | `src/preprocess/cpu_sw.cpp` | `496ca33bed61058b4911338e624fe477b5870b87193a7b50157d3cd1a4d7c84b` |
      | `src/preprocess/rga_copy.cpp` | `7933762d2bf16d3c4386f02efe6fe6824a1932b4ee08bf546e290e3e149d4d12` |
      | `src/preprocess/zero_copy_rga.cpp` | `7ddd5060afe89910654feddc79ad0211cdb469b10971cbf59456b437613fab06` |
      | `src/preprocess/cpu_yuv.cpp` | `c42bef79df9e758390c4278149fe6a78ddf19a55fd4505636e5ed3d2b691c3a1` |
      | `src/core/npu.cpp` | `01db03a89aeeefa94e4ca84ea9fc2da1fef2519ff469cdc926b6a9020f929c14` |
      | `src/core/video_source.cpp` | `99f57937d8fc579899aa735dc4feda4417782325e2708a1ca338162d59499e3a` |
      | `src/core/slot_pool.cpp` | `08173a21643a49ee88027aef5c6efc26258fe359e7b47aad14fbd6c34f2d35da` |
      | `src/core/metrics.cpp` | `dd8bc752ab0c405aaa8226060e9ed1a7a05bcb2e1b39b29b2addc9deeb8e0e2d` |
      | `src/core/pipeline_post.cpp` | `28a471ca0bfa158e34d320c05c6d68950e665b62c9c43d38f7d03baec9c9a14a` |
      | `src/core/yolov8_post.cpp` | `536b9351e78e815c09ba6e8c4f643e06e2e06454028163362ed5eaeaa4adbfa2` |
      | `src/core/yolov8_post_neon.cpp` | `3956c1ba1f541bfe6ebd44b67cef4b67403eb1c1deb31cd842354d034bae7555` |
      | `src/core/yolov10_post.cpp` | `b7642a3b11681ac79c4d0f0bb494f04988eb444bfdc9361f195983560f7cbeb8` |

      The reference build is a git worktree of `freeze-m6`, as that commit has it, with libyuv's source copied from this checkout (the same pinned commit); nothing else of it differs.
  - **The counters' events and their codes:** none was left out. All five opened on every thread and counted above zero in the decoder's group. From the board's sysfs, PMU `armv8_pmuv3` (type 7): `cpu_cycles` 0x11, `inst_retired` 0x8, `l2d_cache_refill` 0x17, `l3d_cache_refill` 0x2a, `stall_backend` 0x24. They count user and kernel mode. `perf_event_open` works as root on the board's kernel with nothing installed.
  - **The counters' check failed in its second part. Bk, Bdk, Rck and Ck are not run (§5).** One run of Ck at 8 channels with a 10 s window, valid, both snapshots complete ([20261127T003302Z_m7_check_counters](../results/check/20261127T003302Z_m7_check_counters)):
    - **Part 1, passed:** each of the 31 threads in `threads.csv` has its group, every `ioctl` on it succeeded, and `time_running` equals `time_enabled` for each.
    - **Part 2, failed:** the NPU workers' cycles are 5.6% below their run time, outside the 5%.

      | Group | Threads | Run time (s) | Cycles ÷ 2208 MHz (s) | Ratio | |
      |---|---|---|---|---|---|
      | Decoder | 24 | 0.9999 | 0.9499 | 0.9501 | within 5% |
      | NPU workers | 3 | 0.6778 | 0.6396 | 0.9436 | **not within 5%** |
      | Preprocessing | 2 | 0.0270 | 0.0253 | 0.936 | below 0.2 s: not judged |
      | Metrics | 1 | 0.0126 | 0.0099 | 0.785 | pinned to cpu0 at 1800 MHz: not compared |
      | The main thread | 1 | 0.0000 | 0.0000 | | did not run |

    - **Part 3, passed:** every event counts above zero on a thread of the decoder's group.
    - **What follows, by §5:** the four counted arms are not run in either the 10 s runs of the arms or `m7_main`, which is then 4 arms × 3 repetitions = 12 runs, in the registered orders with the counted arms taken out. Question 2's description by counters is missing, and with it `Xk − X` and §5's 2% rule. M7-H2 is not affected. `tools/stream_session.py` names the four arms and the reason (`M7_NOT_RUN`), and the report says so.
    - **The cause is not established.** The cycles of every group that ran are below its run time, the decoder's by 5.0%. The criterion was not changed and the check was not run again.
  - **cpu0's counter: passed. Its value is reported** ([20261125T095023Z_m7_cpu0_check](../results/check/20261125T095023Z_m7_cpu0_check)). Over 10 s of idle 86,069,291 cycles, 0.048 s at 1800 MHz and 0.48% of 10 s × 1800 MHz; over 10 s with a 2.0 s loop on cpu0 3,727,782,079; the difference is 2.023 s at 1800 MHz, 1.2% above 2.0 s.
  - **Results of the other checks of step 3,** all of run kind `check`, judged by `tools/m7_checks.py` ([20261127T030600Z_m7_checks](../results/check/20261127T030600Z_m7_checks)): every one passes.
    - **Unchanged arms:** the objects above. For B, Bd, Rc, C and Cp, the input tensors, the NPU's outputs and the detections on the first 100 frames of video ① have the same SHA-256 from this build and from the reference build. The run of C with every option at its default, at 2 channels, has the threads, affinities and queues of M1's `32_main_C_2ch_r1`.
    - **The snapshots,** in a run of C at 2 channels with `--snapshot on`: both exist and parse; no thread of `bench_main` is among the 505 other threads; the first took 35.2 ms and ended 964 ms before the window's start; `frames.csv`, `channels.csv` and `threads.csv` have the columns, `channels.csv` the number of rows, and `summary.csv` the keys, of the default run of the same session.
    - **Cpt: passed. Cpt is run.** At the auxiliary condition, 1,500 timed calls for 1,500 inferences in the window, the smallest 0.977 ms of thread CPU; 1.654 s inside `rknn_run`, 60.4% of the run's process CPU; 1.103 ms per call against 15.3 ms of wall time.
    - **The probe's tool starts:** configuration 1 ran on one frame ([20261127T023501Z_m7_probe_smoke](../results/check/20261127T023501Z_m7_probe_smoke)). Configurations 2 to 6 were not run.
    - **One run of each arm that is run** with a 10 s window ([20261127T023509Z_m7_check_rest](../results/check/20261127T023509Z_m7_check_rest)), after the default, snapshot and Cpt runs: all valid at the first attempt, no drop, both snapshots complete in each. Nothing M7.md fixes changes because of these values (§5):

      | Point | Arm | Process CPU (s) | Latency p50 (ms) | Latency p99 (ms) |
      |---|---|---|---|---|
      | 8 ch, main | B | 3.68 | 36.1 | 53.0 |
      | | Bd | 3.62 | 21.2 | 26.0 |
      | | Rc | 1.74 | 30.1 | 44.8 |
      | | C | 1.66 | 29.7 | 44.5 |
      | 5 ch, auxiliary | B | 10.93 | 22.5 | 31.4 |
      | | Bd | 10.87 | 21.1 | 22.4 |
      | | Rc | 3.30 | 17.9 | 25.2 |
      | | C | 3.02 | 17.8 | 25.8 |
      | | Ct | 3.03 | 18.0 | 28.3 |
      | | Cpt | 2.72 | 18.9 | 25.7 |

    - **The order of step 3:** cpu0's check started at 2026-11-25T09:50:23Z and the counters' run at 2026-11-27T00:43:14Z; their records, with the events' set, were committed at 02:32:15Z (`e2a6b7f`). The unchanged arms' check started at 02:34:25Z, the probe's start at 02:35:01Z and the session of the other runs at 02:35:09Z, its first run at 02:45:21Z.
    - **Two builds.** The two first checks ran from the build `28b09bf`, the others from `e2a6b7f`. Between the two, in the registered paths, only `tools/stream_session.py`, `tools/m7_checks.py` and files under `tests/` differ: the list of the arms not run and its reason, and the judge's rule that lets the two first checks name an earlier commit when nothing but those two files, `src/core/perf_counters.h` and tests differ. `src/` is the same, so the binary that was checked is the one frozen but for its commit stamp.
  - **Readings of M7.md** where its text leaves a choice:
    - **"One run of each arm" (§8 step 3) after the counters' check failed.** §5 says the counted arms are not run; the 10 s runs are then of the ten other arm-point pairs. The only run of a counted arm ever made is the check's own run of Ck.
    - **"Every pipeline thread … has its group" (§8 step 3, §4's rule 5).** The metrics thread, which opens the groups, is not a pipeline thread here, as in PROTOCOL §9.3: rule 5 does not ask for its group, and it is not compared with 2208 MHz, being pinned to cpu0, which runs at 1800 MHz. Its group is still opened, and every opened group must have `time_running` equal to `time_enabled`. A group that did not open, that could not be read, whose `ioctl` failed, or that was enabled for 0 ns on a thread that used CPU, counts as no group.
    - **When the groups are opened and stopped.** They are opened at the tick 2 s before the window's start, enabled at the window's start right after the window's CPU reading, and disabled at its end right after the end's CPU reading, before the second snapshot; disabling took about 1 ms in the check's run, which the time before the second snapshot includes in a counted arm.
    - **The snapshots' moments.** The first is taken at the tick 1 s before the window's start and stops 50 ms before it if it is not finished; the second right after the window's end. cpu0's counter is enabled, and read, right after each snapshot's read of `/proc/schedstat`.
    - **The idle pair (§4)** is taken once per session, 121 s apart, from second 329 of the 600 s idle period: it ends 30 s before the period's last 120 s, over which the idle baselines are read. "During the idle baseline" is read as during the 10 minutes of idle: 121 s do not fit in the 120 s, and the pair's own reads then stay out of the baselines' values.
    - **M7-H2's scaling (§2, §3):** a value from the snapshots is scaled to the window by the span from the first snapshot's start to the second's start. cpu0's counter covers the span from its enabling to its reading, and the report corrects both ends with the recorded times of the ticks. The per-inference `Rc − C` and `B − Bd` use the nominal 4,800 (main) and 18,000 (auxiliary) inferences of a window; the NPU workers' CPU per inference of Ct and Cpt divides by the run's own number of timed calls.
    - **Kernel threads in the report** are grouped by name at the second snapshot, the NPU's before `kworker`. A `kworker`'s name carries its current work item, so one whose name holds `npu` at the second snapshot counts in the NPU's group (§3's order); the time of the other `kworker` threads is not attributed to a driver.
    - **The snapshot check's "columns and rows"** are compared with the default 2-channel run of the same session: the columns of `frames.csv`, `channels.csv` and `threads.csv`, the number of rows of `channels.csv`, and the keys of `summary.csv`.
    - **The probe (§3).** Configurations 1 to 4 keep the dimensions the query returns and change only the format and the type; for FLOAT32 the size is the number of elements × 4, and the size with stride × 4. The header does not say whether a requested layout must come with reordered dimensions; a refusal is a result (§5). Each configuration makes 40 untimed runs before its 20 timed ones, an addition to §3: the earlier output probe timed each context after its 40 correctness runs, and a context here is timed after as many; its value is the mean per round. The full probe is refused unless its run kind is `m7`.
    - **"Each naming `prereg-m7`" (§8 step 3).** The runs and the tools new in M7 write `prereg_m7_commit`. The unchanged arms' record does not: its dumps are made by `freeze-m6`'s `tools/m6_arms_dump`, which must be the same in both builds and writes M6's header. That record is tied to M7 by its `code_commit` in `reference.txt`.
    - **A threshold met exactly** counts as met, as in A17: the 5% and 0.2 s of the checks and §2's thresholds are compared without tolerance.
  - **Fixed for a rerun (§5, §9):** as A17, with `M7_SUPERSEDED` and `M7_CODE_TAG` (`freeze-m7`).
  - **Tries during development, and a failed start.** Before the checks above, the tools were tried on the board from a separate copy of the working tree: cpu0's counter (idle 0.052 s, difference 1.987 s), a snapshot run from a local file, and the unit tests of the snapshots and the counters as root. No record of them is kept; none was a run of kind `check` from a committed build. The first start of the unchanged arms' check, at 02:33:44Z, built both trees and then stopped while it recorded the environment, because `sudo` could not ask for its password; it hashed and dumped nothing, wrote part of one `env.txt` and judged nothing ([20261127T023344Z_m7_unchanged](../results/check/20261127T023344Z_m7_unchanged)).
  - **Library versions, clocks and cooling:** as A7, A9, A10 and A15. Every key M3 §4 pins equals `docs/notes/inventory_m1.txt`; the same 22 other lines differ as in A17.
- Reason: M7 §8 step 4. The checks of step 3 were made before `freeze-m7`; the one that failed has the consequence M7 §5 registered for it, and the readings are stated before any run of the sweeps.
- Affected data: none. No run of the sweeps has been made. The check records are listed above.
- Release: `amend-A18`. `freeze-m7` tags the same commit, the merge of the pull request that adds this entry; its `created_at` is the reference time for M7's runs.
