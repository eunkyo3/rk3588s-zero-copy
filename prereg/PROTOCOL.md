[한국어](PROTOCOL.ko.md)

# Measurement protocol v1 (M1)

- **Registration:** stage 1 of 2, tag `prereg-v1`, together with [HYPOTHESES.md](HYPOTHESES.md).
- **Normative text:** this English file. `PROTOCOL.ko.md` is a translation; if they disagree, this file governs.
- **Changes:** not edited after `prereg-v1`. Later changes, and every value this file says is fixed later (§14), go to [AMENDMENTS.md](AMENDMENTS.md) (§15).

## 1. Registration and timeline

1. **Stage 1, `prereg-v1`:** hypotheses, verdict rules and this protocol. Registered before any measurement.
2. **Stage 2, `freeze-m1`:** tags the commit whose AMENDMENTS.md holds every value of §14. It comes after calibration (M1-3) and equivalence (M1-4) and before any run of §12.
3. **Reference time** of each stage is the `created_at` of its GitHub Release. It is recorded in `docs/notes/env.md` together with the tag's commit SHA. Amendments after `freeze-m1` get their own Releases (§15).
4. Every results CSV has a header with:
   - ISO 8601 start time from an NTP-synced clock (the sync state from `timedatectl` is recorded)
   - `prereg-v1` commit hash, and `freeze-m1` commit hash (`none` before the freeze)
   - run kind: `check` (M1-1/M1-2 functional checks, §11.4, the loop check of §3), `calibration`, `equivalence`, `comparison`, `reference` (A) or `m1.1`
   - model SHA-256, decoder, variant, channel count, repetition index
   - commit hash of the code that produced the run, and the latest amendment in force (`none` if none)
5. `tools/check_prereg_order.py` checks that:
   - every file under `results/` starts after `prereg-v1`;
   - every `comparison`, `reference` and `m1.1` run starts after `freeze-m1`;
   - every run whose header names an amendment published as Release `amend-A<n>` (§15) starts after that Release's `created_at`.

   File modification times are not used.

## 2. System under test

- One FriendlyElec NanoPi R6C (RK3588S: 4× Cortex-A55 cpu0–3, 2× Cortex-A76 cpu4–5, 2× Cortex-A76 cpu6–7; 3-core NPU), 8 GB RAM.
- FriendlyElec Ubuntu image. `tools/collect_env.sh` records before every run: kernel, RKNPU driver, librknnrt, librga, MPP and ffmpeg versions, clocks, temperatures and NTP state. The OpenCV version and build information (`cv::getBuildInformation()`) and the compiler and flags of `bench_main` are recorded too. The versions, builds, compiler and flags are frozen at `freeze-m1` (§14); the OpenCV build is fixed in AMENDMENTS before the first B run of M1-2.
- Observed at registration (informative, not binding): Ubuntu 24.04.4, kernel 6.1.141, RKNPU driver v0.9.8, librknnrt 2.3.2, librga 1.10.3.
- The cooling configuration (heatsink, fan, enclosure) is fixed at `freeze-m1` and recorded with the ambient temperature of each session.
- **Session:** starts with the 10-minute idle period of §9.2. It ends when the board reboots, the cooling configuration changes, or 30 minutes pass without a run.

## 3. Input streams

- A separate PC runs the RTSP server (mediamtx), so serving does not load the board. The board pulls RTSP over TCP through a wired Ethernet link.
- Sources are public videos whose licenses have been checked. Each is re-encoded to 1920×1080 H.264, 30 fps, no B-frames (`-bf 0`), GOP 30, 4 Mbps CBR, limited range signalled in the stream. The original color matrix is recorded.
- Each channel is its own RTSP path that loops one video. Publishers start on demand when the bench connects and stop after it disconnects, so each channel starts from the beginning of its video in every run. Timestamps stay continuous across loops. Before `freeze-m1`, for every video in the channel assignment, a 1-channel `--variant none` run that crosses at least two of its loop points must show zero decoder drops (§7.6).
- The channel-to-video assignment is fixed at `freeze-m1`. The stream server's log is saved with every run.
- Equivalence checks (§11) and calibration (§6.2) read a local mp4 with the same encoding (`file_source`) instead of RTSP.

## 4. Model and post-processing

- YOLOv8n, COCO-pretrained, 640×640 input, INT8 RKNN model.
- Converted with rknn-toolkit2 of exactly the same version as the board's librknnrt. Normalization (mean 0, std 255) is inside the model, so the input tensor is uint8 RGB NHWC. The quantization dataset and the model SHA-256 are recorded; the model is frozen at `freeze-m1`.
- Post-processing is the same code for every variant: YOLOv8 box decoding, confidence threshold 0.25, per-class NMS with IoU threshold 0.45.

## 5. Pipeline under test

### 5.1 Shared core

One binary, `bench_main --variant {none,A,B,C}`. All variants use the same stages:

```
decoder thread (1 per channel) → sampler → Q1 → preprocessing pool [acquire_slot → prepare] → Q2 → NPU workers [bind → rknn_run → post-process]
```

- **Decoder:** `h264_rkmpp` from [nyanmisaka/ffmpeg-rockchip](https://github.com/nyanmisaka/ffmpeg-rockchip), producing `AV_PIX_FMT_DRM_PRIME` frames, with `extra_hw_frames=8` and the decoder's default buffer allocation (no custom `hw_device_ctx`).
  - If ffmpeg-rockchip fails to build twice, jellyfin-ffmpeg's rkmpp decoder is used instead, recorded in AMENDMENTS. The distribution's ffmpeg package is not used.
  - The build, its commit and all decoder options are frozen at `freeze-m1`. B and C use the same build.
  - Whether the decoder's buffers are CPU-cached is checked in the spike and recorded (§14). It affects only B's read cost, so it is stated with every result (HYPOTHESES, Verdicts).
- **Sampler:** a per-channel decode-order counter. Main condition: frames with `counter % 6 == 0` are sampled (5 fps per channel). Auxiliary condition: every frame is sampled (30 fps). Frames that are not sampled are released immediately.
- **Q1:** depth `2 × channels`, drop-oldest. Q1 is the only queue that drops.
- **Preprocessing pool:** K = 2 threads. Each takes a frame from Q1, gets a free input slot (`acquire_slot`), runs `prepare` and releases the DRM PRIME frame as soon as `prepare` completes.
- **Q2:** depth 6.
- **NPU workers:** 3 workers, each owning one RKNN context pinned to one NPU core (0, 1, 2). A worker runs `bind → rknn_run → post-process` inline, then returns the slot. Each context has one output set (`rknn_create_mem` + `rknn_set_io_mem`). The next `rknn_run` starts only after post-processing finishes, so outputs are never overwritten while in use.
- **Input slots:** N = 11 (= K + depth(Q2) + 3) for both B and C.
- **Context selection:** late binding. A worker takes any slot from Q2 and binds it to its own context.

### 5.2 Variants

Only `prepare`, `bind` and the memory behind the input slots differ.

- **B (CPU copy):**
  - Slots are CPU memory.
  - `prepare` maps the DRM PRIME fd with `mmap` (cached per buffer, §8) and calls `DMA_BUF_IOCTL_SYNC` START(READ) and END(READ) exactly once each per frame. `av_hwframe_map` is not used.
  - It then resizes first, converts color second, and writes the letterboxed image into the slot.
  - `bind` is `rknn_inputs_set`.
- **C (zero copy):**
  - Slots are 640×640×3 uint8 dma-heap buffers from `system-uncached-dma32` (fallback `system-dma32`). Each is registered with all 3 contexts through `rknn_create_mem_from_fd`, input attribute UINT8/NHWC.
  - `prepare` runs RGA `improcess` from the imported DRM PRIME buffer (cached per buffer, §8) into the slot, with `IM_SYNC`.
  - `bind` is `rknn_set_io_mem`.
  - While streaming, the CPU does not read or write pixel data; the slot border is written once at initialization. Checked by §11.4.
- **A (software decode, reference):** libavcodec software H.264 decoding into CPU memory, then B's resize, color and letterbox code and B's `bind`.
- **none (decode only):** decoder and sampler only; sampled frames are released immediately. Used for the decode-only limit (§6.1) and as the baseline of §11.4.
- **Letterbox:** for B and C, the slot border is filled once at initialization with a CPU `memset` to value 114. `prepare` writes only the 640×360 image region (1920×1080 scaled by 1/3, centred vertically).

### 5.3 Fairness invariants

B and C have identical thread layout, queues, drop policy, slot count, output binding, post-processing location, instrumentation and per-buffer cache policy. Any fallback (§5.4) applies to both.

### 5.4 Fallbacks (pre-registered)

- **Symmetric context fallback:** if one dma-buf cannot be registered with more than one context, **both** B and C choose the context round-robin at `acquire_slot` time. Q2 is then split into 3 per-context queues of depth 2 (total 6). N stays 11.
- **Release policy:** if freeing an RKNN memory object in one context breaks runs in another context, all RKNN memory objects are freed only at shutdown, after which the fds are closed.
- **C path fallback:** if the C path of §5.2 cannot be made to work, the fallbacks are, in order: C1 (RGA writes into memory from `rknn_create_mem2`), then ffmpeg's `scale_rkrga` filter. A fallback is adopted only with the project owner's approval and recorded in AMENDMENTS before `freeze-m1`. Every C result is then labelled with it.
- The paths in use are recorded in AMENDMENTS (§14) and in each run's `env.json`.

## 6. Channel sets and capacity

### 6.1 Decode-only limit `L` and main channel set

- **`L`:** a `--variant none` run *passes* when, over the 120 s window, every channel keeps receive drift ≤ 1 s and has zero decoder drops (§7.6). `L` is found in M1-3 with the procedure of §10, using "passes" in place of "sustains", 8 channels as the start (in place of `min(8, L)`), and 32 channels in place of `L` as the cap. Rule 4 of §9.3 applies to these runs as invalidity. The confirmed count is `L`.
- `L` includes the stream server and network, so it is the ceiling of every channel sweep.
- **Main set:** `{1, 2, 4, 8, 12, 16}` restricted to values ≤ `L`, plus `L` itself if it is not already in the set.

### 6.2 Pipeline capacity `P_cap` and derived values

- **`P_cap`:** measured with `tools/calib_npu --with-post`.
  - It preloads a set of 1,000 input tensors into dma-heap buffers.
  - Worker `w` (one context on NPU core `w`, `w` = 0, 1, 2) cycles over the tensors whose index ≡ `w` (mod 3). Each tensor is registered with that context through `rknn_create_mem_from_fd` and bound with `rknn_set_io_mem`. Each step runs `rknn_run` plus the real post-processing on the real outputs.
  - There is no `prepare` and no CPU copy, so `P_cap` is an upper bound for both variants. The same value is used for both.
  - `P_cap` = completed inferences in 120 s after a 20 s warm-up, divided by 120. Median of 3 runs.
  - The tensor set is dumped with `tools/dump_tensor` from the `file_source` video at the start of M1-3, and the generating path is recorded.
- The raw NPU limit (`rknn_run` only, 1, 2 and 3 cores) is also recorded, for reference only.
- **Unsaturated condition:** channels ≤ `L` **and** `channels × inference fps ≤ 0.8 × P_cap`, where inference fps is 5 (main) or 30 (auxiliary).
- **H2 auxiliary point:** `floor(0.8 × P_cap / 30)` channels. If it is 0, there is no auxiliary point.

### 6.3 Other sets

- **Auxiliary set:** `{1, 2, 3, 4, 5} ∪ {H2 auxiliary point}`.
- **A set:** `{1, 2}`, main condition.

## 7. Metrics

All times use `CLOCK_MONOTONIC`. The measurement window is the 120 s after the 20 s warm-up.

### 7.1 Process CPU (H1 primary)

- Δ(utime + stime) of the bench process over the window, from `/proc/<pid>/stat`, in CPU-seconds. All threads are included.
- Per-cluster breakdown from `/proc/<pid>/task/<tid>/stat`, attributed by each thread's affinity (§8): pipeline threads (cpu4–7) count as A76, the metrics thread (cpu0) as A55, and threads with any other affinity as unattributed.

### 7.2 System CPU (H1 secondary)

- Per-CPU Δ non-idle time from `/proc/stat` (non-idle = user + nice + system + irq + softirq + steal), in CPU-seconds. Reported per CPU, per cluster (cpu0–3, cpu4–5, cpu6–7) and in total. Kernel threads (driver work, interrupts) appear here, not in §7.1.
- **Idle-subtracted:** the run's value minus an idle baseline. The baseline is the same quantity over 120 s with no pipeline running, measured once per session.

### 7.3 Latency

Timestamps per processed frame:

| Name | Point |
|---|---|
| t0 | the decoder returns the frame (`avcodec_receive_frame`) |
| t1 | `prepare` starts |
| t2a | `prepare` completes and the DRM PRIME frame is released |
| t2b | `bind` starts (in the NPU worker) |
| t2c | `bind` completes |
| t3 | `rknn_run` returns |
| t4 | post-processing completes |

- **End-to-end latency:** `t4 − t0`, for processed frames only. Frames dropped from Q1 are counted in the drop rate instead.
- **MPP hold time:** `t2a − t0`. **Slot wait:** time spent in `acquire_slot`.
- **p50 / p99:** per run, over all processed frames of all channels with t4 inside the window, using `numpy.percentile` with its default (linear) interpolation.

### 7.4 Inference rate

Per channel: the number of that channel's frames with t4 inside the window, divided by 120 s.

### 7.5 Drop rate

Q1 drops divided by sampled frames, over the window, all channels together.

### 7.6 Receive drift and decoder drops

- **Drift** per channel: `D(t) = (30 × (t − t_w) − frames returned by the decoder in [t_w, t]) / 30` seconds, where `t_w` is the start of the window. `D(t)` is evaluated at every one-second sample (§7.7) and at the end of the window. The channel's drift is the maximum of these values.
- **Decoder drops** per channel, over the frames returned in the window (including the step from the last frame returned before `t_w`):
  - each PTS step ≤ 0 or > 1.5 frame periods counts `max(1, round(Δ / period) − 1)` missing frames;
  - each frame with `AV_FRAME_FLAG_CORRUPT` or non-zero `decode_error_flags` counts one;
  - each decoder error return other than `EAGAIN` or `EOF` counts one.

### 7.7 One-second samples

A metrics thread on cpu0 samples once per second:

- NPU load per core (`/sys/kernel/debug/rknpu/load`)
- every thermal zone's temperature
- current frequency of CPU policies 0, 4 and 6, DDR (`dmc`) and NPU devfreq
- VmRSS of the bench process
- per channel, the number of frames returned by the decoder so far

### 7.8 Reporting

Every measurement point reports the mean and standard deviation of each metric over its valid runs.

## 8. Execution settings

Identical for B and C unless stated.

- Warm-up 20 s, measurement 120 s, 3 repetitions per measurement point.
- Governors set to `performance` for CPU policies 0, 4 and 6, DDR (`dmc`) and NPU devfreq with `tools/set_perf_mode.sh`. The resulting fixed frequencies are read from sysfs in M1-3 and recorded in AMENDMENTS. Every later run is checked against those values (§9.3 rule 1).
- **Affinity:** the process affinity is set to cpu4–7 before any library is initialized, so library threads (MPP, librknnrt, ffmpeg) inherit it. The metrics thread then moves itself to cpu0. Every thread's affinity is recorded. `cv::setNumThreads(1)`.
- `rknn_init` flags are identical for B and C: synchronous execution only (no `RKNN_FLAG_ASYNC_MASK`, no non-blocking run) and none of the `RKNN_FLAG_DISABLE_FLUSH_*` flags. The exact flags are recorded.
- K = 2, Q1 = 2 × channels, Q2 = 6 (2 per context under the fallback of §5.4), N = 11, `extra_hw_frames=8`.
- **Per-buffer cache:** keyed by the dma-buf inode (`st_ino` from `fstat`), not by the fd number, and invalidated on decoder info-change. B caches its `mmap`, C caches its RGA `importbuffer_fd` handle.
- **RGA core:** `im_opt_t.core = IM_SCHEDULER_RGA3_CORE0 | IM_SCHEDULER_RGA3_CORE1`. If the rkrga debugfs load shows that the mask is not honoured, `IM_SCHEDULER_RGA3_CORE0` alone. RGA2 is never used: RGA2 and RGA3 sample differently when scaling, so mixing them makes tensors non-deterministic.
- **Color:** BT.601 limited range for both RGA and OpenCV, matching the limited range signalled by the streams (§3). The sources' original color matrices are recorded. Any mismatch with BT.601 is the same for B and C and is reported as a threat to validity.
- **Interpolation:** RGA's scaling settings are fixed first. OpenCV then uses whichever of `INTER_LINEAR` and `INTER_AREA` gives the lower §11.1 mean absolute error against C. The choice is recorded in AMENDMENTS.

## 9. Run procedure and validity

### 9.1 Order

- Before every run, `tools/collect_env.sh` records versions, clocks, temperatures and NTP sync state.
- The channel order of each sweep is a seeded shuffle: the sorted channel list is shuffled with Python `random.Random(20261030).shuffle()`. Each sweep (main, auxiliary) uses a fresh generator with this seed.
- For each channel count, all 6 runs (B and C, 3 repetitions) form one block. Inside a block, the variant order per repetition is B→C, C→B, B→C.
- The two runs of one repetition are never split across sessions. If a session ends between them, both are rerun at the start of the next session; the run that had already finished is kept and flagged.

### 9.2 Cooldown gate

- At the start of a session the board idles for 10 minutes. The baseline is the mean `soc-thermal` temperature over the last 60 s of that idle period.
- A run starts only when `soc-thermal` ≤ baseline + 3 °C and no bench process has run for at least 60 s.
- The ambient temperature and the cooling configuration are recorded per session.

### 9.3 Invalid runs

A run is invalid if any of these holds:

1. any one-second clock sample in the window (CPU policies 0/4/6, DDR, NPU) is below its fixed value (§8);
2. the bench process crashes or exits abnormally;
3. at an unsaturated condition (§6.2), decoder drops > 0. This rule does not apply to H3 runs (§10);
4. between the start of the warm-up and the end of the window, the stream server logs, for any channel, an error, a reader disconnect, or a publisher exit or restart. The first publisher start of each channel and the bench's disconnect after the window are not events. This rule also applies to H3 runs and to the `L` runs of §6.1.

Handling:

- An invalid run is repeated once, immediately after it (after the cooldown gate). The repeat replaces it under the same repetition index; the invalid run is kept and flagged.
- If the session ends first (for example, a crash forces a reboot), the repeat runs at the start of the next session, together with a rerun of the other run of that repetition (§9.1). These reruns are the repeat and replace both earlier runs, which are kept and flagged.
- If the repeat is also invalid, the run is recorded as **invalid ×2** and reported.
- The operator stops a run only for a logged external failure (power, network link, stream server). Such a run is invalid, and the reason is reported.
- A measurement point is complete when it has 3 valid runs or an invalid ×2 record for each missing run.
- A sample at or above a thermal zone's `trip_point_0_temp` is recorded as a **warning**, not an invalidity.
- A coefficient of variation above 10% for a hypothesis metric at a measurement point is flagged in the report. It does not trigger a re-measurement.
- Every raw run, valid or not, is kept under `results/`.

## 10. H3 search

Run separately for B and for C at the main condition. Dropped frames and missed fps are outcomes here, not invalidity; rules 1, 2 and 4 of §9.3 apply.

1. **Search:** start at `min(8, L)` channels with one run, then +1 channel per run while the run sustains (HYPOTHESES H3). Stop at the first run that does not sustain, or at `L`. If the starting count does not sustain, go down by 1 per run until a run sustains. `n` is the largest count whose search run sustained (0 if none did).
2. **Confirmation:** 3 runs at `n` and 3 runs at `n + 1`. There are no runs at `n + 1` if `n = L`, and none at `n` if `n = 0`.
   - **Up:** if at least 2 of 3 runs at `n + 1` sustain, set `n ← n + 1` and confirm again. The old `n + 1` serves as the new `n` with the runs it already has.
   - **Down:** otherwise, if `n ≥ 1` and at most 1 of 3 runs at `n` sustains, set `n ← n − 1` and confirm again. The old `n` serves as the new `n + 1` with the runs it already has.
   - Otherwise `n` is confirmed. "Up" is checked first because H3 asks for the maximum.
3. Only runs made under this section count. Search runs do not count toward confirmation, and main-sweep runs are not used, because §9.3 rule 3 applies to them and not here. A run that is invalid ×2 counts as not sustained and is flagged.
4. **Order:** while both variants are searching, they alternate at each step (B→C at the first step, C→B at the next, and so on). At a count both variants need for confirmation, the 6 runs (3 per variant) are interleaved B→C, C→B, B→C, with counts in ascending order. A count that only one variant needs runs alone in its place. The cooldown gate applies to every run.
5. The confirmed `n` is `n_B` or `n_C`.

## 11. Equivalence and zero-copy evidence

Inputs for §11.1 and §11.2: the same 1,000 frames from `file_source`.

### 11.1 Tensor level (B vs C)

- Computed on the 640×360 image region of each input tensor:
  - mean absolute error over all frames and elements ≤ 2 (uint8 scale);
  - per-frame PSNR (peak 255): the minimum over all frames ≥ 35 dB.
- Checked separately: every border element of every B and C tensor equals 114.

### 11.2 Detection level (B vs C)

`tools/equivalence_check.py` passes (exit code 0) when all of these hold:

- Boxes are matched per class with the Hungarian algorithm on IoU. A pair is formed only if IoU ≥ 0.5.
- **Every** matched pair has IoU ≥ 0.95 and confidence difference ≤ 0.02.
- **Box counts:** a box is excluded from the counts if its confidence is within 0.25 ± 0.02, or if it is matched to a box whose confidence is. A frame agrees when B and C have the same number of counted boxes in every class. At least 99% of frames agree.

Histograms of matched-pair IoU and confidence difference are reported alongside.

### 11.3 Failure

If §11.1 or §11.2 fails:

- The cause is analysed and recorded.
- Any change to these criteria, to the OpenCV or RGA settings, or to the implementation is recorded in AMENDMENTS before `freeze-m1`.
- The check is then rerun in full, and the report states the original failure.
- No run of §12 starts until §11.1 and §11.2 pass under the criteria then in force.

### 11.4 Zero-copy evidence (M1-2)

- **perf:** `perf record -g` on C and on `--variant none`, at 1 and 4 channels, 10 s each.
  - Copy symbols (`memcpy`, `__arch_copy*`) whose call stack includes `prepare`, `bind` or `rknn_run` must be < 0.5% of all samples. `rknn_run` is included to catch hidden CPU conversion inside the runtime.
  - Copies outside these stacks (MPP, demuxing) are present in both C and `--variant none`. The C − none difference in total copy samples is reported.
- **Cache maintenance:** the share of `dcache_*_poc` and `__dma_*sync*` samples is reported for B and for C. Because no `DISABLE_FLUSH` flag is used, the runtime may maintain caches on C's buffers too, and that cost counts on C's side.
- **strace**, attached in steady state: C makes 0 dma-buf `mmap` calls; B makes exactly 2 `DMA_BUF_IOCTL_SYNC` calls per frame.
- **Failure:** if a criterion of this section fails, the cause is recorded in AMENDMENTS before `freeze-m1`, every C result is labelled with it, and the report states that C's zero-copy property was not met.

## 12. Measurement matrix (release `v0.1-m1`)

1. **Main:** B and C × main set (§6.1) × 3 repetitions, 5 fps inference per channel.
2. **Auxiliary:** B and C × auxiliary set (§6.3) × 3 repetitions, 30 fps inference per channel. H2 uses 5 channels and the H2 auxiliary point.
3. **H3:** search and confirmation for B and C (§10).
4. **A (reference):** A set (§6.3) × 3 repetitions.

These are not comparison runs:

- calibration (M1-3): `L`, `P_cap`, the raw NPU limit and the thermal check
- equivalence (M1-4)
- the idle baseline, measured once per session (§7.2)

**Thermal check (M1-3, before `freeze-m1`):**
- Runs: B at the largest auxiliary channel count and B at the largest main channel count, 140 s each.
- It passes when no clock sample falls below its fixed value and every temperature stays below `trip_point_0_temp`.
- On failure, the cooling configuration is changed and recorded, and the check is repeated.
- If, after one cooling change, temperatures still reach `trip_point_0_temp` but no clock sample falls below its fixed value, the check is accepted with a recorded warning.

## 13. Optional M1.1 designs (not used for any verdict)

Registered now so that they cannot be shaped by the M1 results.

- **B-direct:** like B, but the CPU writes into a cached dma32 pool, calls `DMA_BUF_IOCTL_SYNC` END, and binds with `rknn_set_io_mem`. This splits C's gain into "removing the last copy" and "removing CPU conversion". 8 channels main + H2 auxiliary point, × 3 repetitions.
- **B with K = 4:** 8 channels, × 3 repetitions. Checks whether the shared structure is unfair to B.
- **Exploratory, A55 only:** pipeline pinned to cpu0–3, B and C at 8 channels, × 1. Labelled exploratory.

M1.1 code added after `freeze-m1` falls under §15 only if it changes the shared core, the decoder, or the B or C code paths.

## 14. Values fixed later

All of these are recorded in AMENDMENTS. `freeze-m1` tags the commit whose AMENDMENTS holds all of them.

| Value | Fixed in |
|---|---|
| `L`, main set, `P_cap`, H2 auxiliary point, unsaturated boundary | M1-3 |
| Fixed CPU (policies 0/4/6), DDR and NPU frequencies | M1-3 |
| Cooling configuration | M1-3 thermal check |
| OpenCV build | before the first B run of M1-2 |
| OpenCV interpolation (§8) | M1-2 / M1-4 |
| RGA core (mask or `RGA3_CORE0`), context path and release policy (§5.4), C path fallback if any, decoder buffer cacheability | spike / M1-1 |
| ffmpeg build, commit and decoder options, library versions, model SHA-256 and quantization dataset, channel-to-video assignment, exact `rknn_init` flags, compiler and flags | before `freeze-m1` |

## 15. Amendments

- [AMENDMENTS.md](AMENDMENTS.md) is append-only. Each entry has a date, what changed, why, and which data it affects.
- Before `freeze-m1`, measurements affected by a change are redone from scratch.
- After `freeze-m1`, a change to the shared core, the decoder, or either variant's code invalidates every comparison run that used it, **for both variants**. Affected points are rerun as complete blocks (§9.1), and affected H3 searches are rerun from §10 step 1. Superseded runs stay under `results/` and are reported.
- Each amendment after `freeze-m1` is published as its own GitHub Release (`amend-A<n>`), and its `created_at` is the reference time.
