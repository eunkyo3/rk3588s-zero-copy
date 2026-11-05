[한국어](m2_exploration.ko.md)

# Exploration before M2's pre-registration

These runs are not pre-registered and are not used for any verdict. They are runs of kind `check`, made on 2026-11-04 and 2026-11-05 before M2's pre-registration. M2's technique and its thresholds were chosen after seeing them, and this note is the record of what was seen.

| Question | Probe | Result | For M2 |
|---|---|---|---|
| What do the runtime's output-path options cost and save? | `output_probe` | Skipping the output flush cuts `rknn_run`'s CPU from 0.844 to 0.662 ms. The other options save little or cost more. | Recorded only. Not an M2 technique. |
| Why do some uncached-input runs give wrong outputs, and does C's path do it? | `first_runs_probe` | A CPU-written `rknn_create_mem2` uncached input gets parts of itself overwritten with zeros. C's slots, written by RGA, are not affected. | M1's bindings are kept. |
| How much CPU can a faster post-processing save? | `post_probe` | Version 4 takes 0.129 ms per call against M1's 0.361 ms (−64%), with identical detections on the 300 frames tested. | M2's technique. Thresholds 3% and 5%. |

All three probes ran as root on the board with the clocks fixed (`tools/set_perf_mode.sh`), on NPU core 0 (part B of `first_runs_probe` on cores 0 to 2), with the video ① `pexels_36330861`. The model is `yolov8n_i8.rknn` and the runtime librknnrt 2.3.2. The runs are listed [at the end](#runs).

## 1. Output-path options (`output_probe`)

**Method.** Each configuration gets its own context, with its init flags and its input and output memories, bound with `rknn_set_io_mem` as M1 binds them. M1 uses no init flag (PROTOCOL §8). Its outputs and A's and B's inputs come from `rknn_create_mem`, whose memory is cacheable by default (`core/npu.cpp`, `preprocess/cpu_common.cpp`).
- **Correctness:** 40 runs alternate two frames. After each run, every output must equal `rknn_outputs_get` of a separate context on that frame.
- **Cost:** 15 rounds of 20 runs per configuration. The cost is the process CPU per run (all threads, the runtime's included) and the thread CPU of `yolov8_post` on the outputs, as the median over the rounds.

Second run (`20261104T085841Z`):

| Configuration | Init flags | Input | Output | Outputs equal | `rknn_run` CPU (ms) | Post CPU (ms) |
|---|---|---|---|---|---|---|
| M1, cached input | none | cached | cached | 40/40 | 0.844 | 0.307 |
| M1 flags, uncached input | none | uncached | cached | 37/40 | 0.814 | 0.304 |
| No output flush, cached input | `DISABLE_FLUSH_OUTPUT_MEM_CACHE` | cached | cached | 40/40 | 0.662 | 0.301 |
| The same, with `rknn_mem_sync` of the outputs | `DISABLE_FLUSH_OUTPUT_MEM_CACHE` | cached | cached | 40/40 | 0.825 | 0.304 |
| No input flush | `DISABLE_FLUSH_INPUT_MEM_CACHE` | uncached | cached | 36/40 | 0.811 | 0.305 |
| SRAM, cached input | `ENABLE_SRAM` | cached | cached | 40/40 | 0.816 | 0.304 |
| Uncached output | none | uncached | uncached | 36/40 | 2.620 | 3.127 |

- **Skipping the output flush saves 0.18 ms of CPU per run.** Adding the sync back by hand costs almost all of it again (0.825 ms). This is consistent with the saving being the cache maintenance of the outputs itself.
  - This is only safe if the runtime's flush is redundant, that is, if the CPU can hold no stale lines of the outputs when it reads them. 40 correct runs do not show that.
  - It is recorded here and is not an M2 technique. It becomes a separate arm only if the rknpu driver source confirms that the sync is redundant.
- **Skipping the input flush and using SRAM save almost nothing.**
- **An uncached output is much worse.** `rknn_run` takes 2.6 ms of CPU and the post-processing 3.1 ms, because the CPU reads 1.2 MB of uncached outputs.
- **Every configuration with an uncached CPU-written input missed two to four of the 40 runs,** all among its first five. No flag avoided it. Section 2 looks at this. In the first run (`20261104T085442Z`, 20 runs each), they also missed 2 to 8 of 20; it did not record which runs.

## 2. Uncached CPU-written input (`first_runs_probe`)

**Method.**
- **Part A:** each case gets a new context, whose input the CPU writes before each run. After each run, the outputs are compared with `rknn_outputs_get` of a separate context on that input. The input memory is read back just before each run and just after it, counting the bytes that no longer hold what the CPU wrote.
- **Part B:** C's own path (`zero_copy_rga`, 2 slots). For each of the first 10 frames, RGA writes the slot and the slot is read back. Then contexts 0, 1 and 2 each run on it, and their outputs are compared with `rknn_outputs_get` on the slot as read back. The slot is read again after the runs.

Third run (`20261105T010648Z`). The input is 1,228,800 bytes.

Every byte that changed had become 0.

| Case | Outputs equal | Bytes changed, before run 0 / after run 0 | Later runs, after each |
|---|---|---|---|
| Cached, alternating frames | 10/10 | 0 / 0 | 0 |
| Uncached, alternating frames | 7/10 (runs 0, 1, 5 wrong) | 257,805 / 607,984 | 0 to 576 |
| Uncached, the same frame every run | 9/10 | 280,730 / 667,454 | 0 to 64 |
| Uncached, bound again before run 10 (16 runs) | 15/16 | 266,953 / 585,618 | 0 to 126 |
| Uncached, 5 runs on an all-114 input first | 10/15 (the 5 all-114 runs wrong) | not read / 332,512 | 332,704 to 332,768 in the all-114 runs; 0 once the frames are written |
| Uncached, `rknn_mem_sync` once before the first write | 9/10 | 235,938 / 505,755 | 0 |
| Uncached, `rknn_mem_sync` after each write | 8/10 | 280,100 / 612,344 | 0 to 320 |
| Uncached, 10 ms between the write and the run | 9/10 | 290,634 / 571,300 | 0 to 64 |
| Cached, all-114 input | 10/10 | 0 / 0 | 0 |
| **C: RGA into the uncached dma-heap slots** | **30/30** | **0 / 0** | **0** (slot unchanged after the runs) |

- **The wrong outputs come from the input, not from the NPU.** A CPU-written uncached input (`rknn_create_mem2` with `RKNN_FLAG_MEMORY_NON_CACHEABLE`) loses about a fifth of its bytes to zeros before the first run, and about half by the end of it. A few bytes more are lost in some later runs, so wrong outputs are not limited to the first runs (run 5 above).
- **`rknn_mem_sync` does not help, and neither does waiting.**
- **A likely cause,** not shown: the memory was zeroed through a cacheable mapping, and those dirty lines are written back later, over what the CPU wrote through the uncached one.
- **M1 is not affected.**
  - A and B write their inputs into cacheable memory (`rknn_create_mem`). Bd, of M1.1, writes a cached `system-dma32` slot, with `DMA_BUF_IOCTL_SYNC` around its writes.
  - C's slots come from `system-uncached-dma32` and are registered with `rknn_create_mem_from_fd`; RGA writes them. All 30 runs were right, and the slot did not change. C falls back to `system-dma32` if that heap fails; the probe did not record which heap it got, but all 64 records of M1's comparison runs name `system-uncached-dma32`.
- **For M2:** M1's bindings are kept, and no CPU-written uncached input is used.

## 3. Faster post-processing (`post_probe`)

**Method.**
- **Frames:** the first 300 frames of ①, decoded by the hardware decoder and prepared as B does.
- **Timing:** for each frame, context 0 runs once per version on the same input, and the version is timed right after its run. Each version thus reads outputs the runtime has just written, which are not in the CPU's caches. The order of the versions rotates with each frame.
- **Measure:** thread CPU per call, on one A76 (cpu4–7 allowed).
- **Equivalence:** every version's detections must equal M1's (`core/yolov8_post.cpp`) field for field: class, score and the four coordinates, as floats.
- **Parts:** one more run per frame times the parts of the last version with the monotonic clock, so the clock reads' own cost is included.

| Version | What changes | CPU per call, mean (ms) | p90 (ms) | Detections equal |
|---|---|---|---|---|
| M1 (scalar) | | 0.361 | 0.388 | |
| 1 | NEON scan: 16 cells per compare, an 80-class running maximum | 0.199 | 0.222 | 300/300 |
| 2 | 1, with the class rows and the box bins read in address order | 0.175 | 0.197 | 300/300 |
| 3 | 2, with the softmax terms of the box decoding from a table | 0.141 | 0.159 | 300/300 |
| 4 | 3, with software prefetches of the outputs | **0.129** | **0.147** | 300/300 |

Final run (`20261105T034429Z`), all five versions in it. Per frame there were 8,400 cells, 1,996 cells passing the score-sum filter, 107.3 candidates and 16.26 detections.

- **The NEON scan keeps M1's results.** M1 takes, for each cell whose score sum passes, the first class strictly above the running best. The NEON scan does the same for 16 adjacent cells at once. So the candidates, their order, the box arithmetic and the NMS are unchanged.
- **The table keeps them too.**
  - A softmax term is `exp(b[i] − max_v)`. It depends on two quantized bins only: `b[i]` is `dequantize(q_i)`, and `max_v` is `dequantize` of the side's largest `q`, since `dequantize` increases with `q`.
  - That needs a positive scale; the table is not built otherwise.
  - The table holds the term for all 256 × 256 pairs of `q`. It is filled once per (zp, scale) with decode_dfl's own expression, so each term is the same float; it is not an approximation. The sums keep M1's order.
  - The three box outputs need three tables, 768 KiB in all, built in 1.6 ms.
  - Before the video, the table's decoding was checked against decode_dfl on 600,000 random sets of bins: there were 0 differences. That check uses the probe's copy of decode_dfl. Against M1's own code, in `core/yolov8_post.cpp`, only the 300 frames compare it.
  - **So the results are identical as built and tested, not by construction alone.** The build is `-std=gnu++17`, under which GCC may fuse `acc += e * i` into a multiply-add in one function and not in another. A different compiler or other flags could change that, so M2's own build needs its own check.
- **Version 4's parts:** scan 0.057 ms, box decoding 0.057 ms, sort and NMS 0.014 ms.
  - Most of what remains is waiting for memory: the NPU has just written the outputs, so they are not in the CPU's caches.
  - The prefetches gained 0.012 ms only, so the refinement stopped there.

**Projection for C, made before the thresholds were set.**
- **Per inference,** version 4 saves 0.232 ms (0.3608 − 0.1289).
- **At 8 channels, main condition:** 4,800 inferences per 120 s window save 1.11 s, which is 5.7% of C's 19.49 ± 0.26 s in M1.1 ([M1.1](../report/M1.1.md#b-direct-what-the-last-copy-costs), mean ± sd over 3 runs).
- **At 5 channels × 30 fps:** 18,000 inferences save 4.17 s, which is 11.1% of 37.45 ± 1.41 s.
- **The thresholds of M2's two CPU hypotheses,** 3% and 5%, are about half of these. They were set after this projection.
- **The projection assumes the post-processing in the pipeline saves what it saves here.** In the pipeline it runs among other threads, on outputs in the same state.

## Runs

All are in `results/check/`, with run kind `check`.

| Run | Probe | Code |
|---|---|---|
| `20261104T085442Z_output_probe` | output path, first version | `c2689d7` |
| `20261104T085841Z_output_probe` | output path, with stale-output counts and the cached-input configurations | `26dffb6` |
| `20261105T010340Z_first_runs` | first runs with an uncached input, and C's path | `d6486fa` |
| `20261105T010514Z_first_runs` | the same, with the input read back after each run, the sync cases and a cached all-114 input | `60ef58c` |
| `20261105T010648Z_first_runs` | the same, with the input read back before each run too, and C's slot before and after its runs | `afca322` |
| `20261105T011219Z_post_probe` | NEON version 1 | `f9287ae` |
| `20261105T013759Z_post_probe` | version 1, with its parts timed | `2083c0b` |
| `20261105T014059Z_post_probe` | versions 1 and 2 | `b0a9224` |
| `20261105T034140Z_post_probe` | versions 1 to 3, with the table check | `d15c840` |
| `20261105T034429Z_post_probe` | versions 1 to 4 | `7eef9dc` |

The probes are `tools/output_probe.cpp`, `tools/first_runs_probe.cpp` and `tools/post_probe.cpp`. Their usage is in each file's header.
