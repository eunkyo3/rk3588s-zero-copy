[한국어](equivalence.ko.md)

# Equivalence of B and C (M1-4)

PROTOCOL §11.1 and §11.2, decided under the criteria in force, those of [AMENDMENTS A5](../../prereg/AMENDMENTS.md).

- **Under A5, B and C are equivalent:** all six conditions pass, and `tools/equivalence_check.py` exits with 0.
- **Under the registered criteria, they are not:** every condition fails except the border check. §11.3 requires the report to state this.

The run:

- **Where:** on the board, 2026-11-02T00:45:13Z, code commit `958641b`, amendment A6 in force.
- **Governors:** default; M1-3 has not fixed the clocks yet.
- **Input:** the first 1,000 frames of video ① (Pexels 36330861), read as `file_source`.
- **Outputs:** in [results/equivalence/20261102T004513Z/](../../results/equivalence/20261102T004513Z/). The check's output is [check.txt](../../results/equivalence/20261102T004513Z/check.txt).

## Method

| Tool | What it does |
|---|---|
| `tools/equivalence_dump` | Decodes each frame once with `h264_rkmpp` and gives it to C, B and R. Each has its own three contexts. It runs every frame on NPU core 0, then the shared post-processing (confidence 0.25, NMS 0.45), and writes every frame's whole input tensor and its detections. |
| `tools/equivalence_check.py` | Computes both levels from those files alone, and prints the registered and the A5 verdicts. The exit code follows A5. |
| `tools/equivalence.sh` | Runs `collect_env.sh`, the dump, then the check. The tensors (3 × 1.2 GB) stay on the board, and their SHA-256 hashes are in `tensors.sha256`. |

- **R** is B's preprocessing with plain `INTER_LINEAR`, §8's other choice (A5). B is as used: `INTER_AREA` with the luma box moved by one pixel (A4).
- **Harness checks,** on the first and the last frame. Both held:
  - For each of C, B and R, NPU cores 1 and 2 give core 0's outputs. The pipeline spreads frames over the three cores; this tool uses core 0.
  - B's bound outputs equal `rknn_outputs_get` (A3).
- **Matching:**
  - Per frame and class, by the Hungarian algorithm on IoU.
  - IoU below 0.5 counts as 0 before solving. The matching then maximises the summed IoU of the pairs that may form (IoU ≥ 0.5). Solving on raw IoU could choose two pairs below 0.5 over one above, and then drop both.
- **Near-threshold exclusion:** the band 0.25 ± 0.02 is applied to the scores as float32, the type post-processing gives them.
- **Tests:** `tests/test_equivalence_check.py`.
  - The Hungarian solver against brute force, on 500 random matrices.
  - The masking at IoU 0.5 and the near-threshold exclusion.
  - The exit codes on synthetic inputs.

## Results

### Tensor level, over the 640×360 band

| | C vs B | R vs B |
|---|---|---|
| MAE | **2.4109** | 4.5384 |
| Minimum PSNR | **34.36 dB** (frame 960) | 28.52 dB (frame 337) |
| Median PSNR | 34.63 dB | 28.92 dB |
| Mean difference R, G, B | −0.62, −0.64, −0.57 | +0.11, +0.10, +0.09 |

Every border element of every B, C and R tensor is 114.

### Detection level

| | C vs B | R vs B |
|---|---|---|
| Matched pairs | 15,246 | 14,903 |
| IoU ≥ 0.95 | 67.15% | 46.96% |
| Confidence difference ≤ 0.02 | 54.54% | 43.82% |
| Both | **41.84%** | 28.37% |
| Smallest IoU / largest confidence difference | 0.500 / 0.258 | 0.501 / 0.344 |
| Frames whose counted boxes agree in every class | **524 (52.40%)** | 347 (34.70%) |
| All boxes (B: 15,740) | **15,810 (+0.44%)** | 15,577 (−1.04%) |

Matched pairs by IoU, and by confidence difference (share of pairs):

| IoU | C vs B | R vs B |
|---|---|---|
| [0.50, 0.80) | 1.73% | 3.60% |
| [0.80, 0.90) | 7.60% | 19.47% |
| [0.90, 0.95) | 23.53% | 29.97% |
| [0.95, 0.98) | 41.05% | 33.89% |
| [0.98, 0.99) | 17.30% | 9.91% |
| [0.99, 1.00] | 8.80% | 3.17% |

| Confidence difference | C vs B | R vs B |
|---|---|---|
| 0 | 7.99% | 5.94% |
| (0, 0.01] | 26.82% | 21.06% |
| (0.01, 0.02] | 19.73% | 16.82% |
| (0.02, 0.05] | 28.22% | 25.51% |
| (0.05, 0.10] | 13.59% | 17.50% |
| (0.10, 0.20] | 3.61% | 10.05% |
| > 0.20 | 0.04% | 3.12% |

Boxes per class, for the 8 classes with at least 100 boxes in B, against B:

| | car | person | traffic light | backpack | handbag | truck | bus | stop sign |
|---|---|---|---|---|---|---|---|---|
| B | 6,886 | 5,307 | 2,049 | 590 | 291 | 226 | 157 | 106 |
| C | −0.01% | −0.51% | −0.73% | −1.36% | −1.03% | 0.00% | **+14.65%** | **+72.64%** |
| R | +0.60% | −2.02% | −0.39% | −0.85% | +2.06% | −3.98% | −1.27% | −93.40% |

### Verdicts

| Criterion | Value | Verdict |
|---|---|---|
| **Registered (§11.1, §11.2)** | | **FAIL** |
| MAE ≤ 2 | 2.4109 | FAIL |
| Minimum PSNR ≥ 35 dB | 34.36 dB | FAIL |
| Border 114 | every tensor | pass |
| Every matched pair with IoU ≥ 0.95 and confidence difference ≤ 0.02 | 6,379 of 15,246 | FAIL |
| At least 99% of frames agree | 52.40% | FAIL |
| **In force (A5)** | | **PASS** |
| Border 114 in every B and C tensor | yes | pass |
| C's MAE ≤ R's | 2.4109 ≤ 4.5384 | pass |
| C's minimum PSNR ≥ R's | 34.36 ≥ 28.52 dB | pass |
| (a) pairs within both thresholds, C ≥ R | 41.84% ≥ 28.37% | pass |
| (b) agreeing frames, C ≥ R | 52.40% ≥ 34.70% | pass |
| (c) \|N_C − N_B\| / N_B ≤ 2% | 0.44% | pass |

## Checks on the result

- **Repeatability:** a second dump of the same 1,000 frames gave byte-identical tensors (all three SHA-256 hashes match) and identical detections for C, B and R. A second check on the same files printed the same output.
  - The computation is deterministic, and clocks change timing, not values. M1-3's fixed clocks are not expected to change this result.
  - A change to the B or C code before `freeze-m1` means a rerun (§15).
- **Agreement with M1-2's estimate:** `tools/compare_prepare` used greedy matching in C++ ([preprocess/compare_v1_1000.txt](preprocess/compare_v1_1000.txt)). The two agree:
  - **Pairs:** C vs B gives the same 15,246. For R vs B, the Hungarian matching finds one more pair (14,903 against 14,902).
  - **Shares and tensor figures:** every share agrees to the four decimals printed, and so do the tensor figures.
  - Two implementations, in C++ and in Python with numpy, give the same tensor figures.

## What this means

- **On every A5 measure, C is closer to B than B's two admissible OpenCV variants are to each other.** Its total box count, the post-processing workload, matches B's within 0.44%.
- **The registered criteria fail, as M1-2 predicted (A5).** The INT8 model moves confidences by tenths under input differences of about 2 levels in 255.
- **Not an independent test:**
  - A5's criteria were chosen after M1-2's estimates on these same frames were known.
  - M1-4 repeats that comparison with the registered matching algorithm, on the only video with 1,000 frames.
- **Per class, C is far from B for bus and stop sign:**
  - **Bus:** +14.65%, where R is −1.27%.
  - **Stop sign:** C +72.64% and R −93.40%. Stop sign flickers around the 0.25 threshold in every variant.
  - The report states these with the total.
- **§11's gate for §12 is passed** under the criteria in force. What remains before `freeze-m1`:
  - calibration and the thermal check (M1-3);
  - the loop check of §3;
  - the values of §14 not yet fixed.

## Files

| File | Content |
|---|---|
| `b.jsonl`, `c.jsonl`, `r.jsonl` | A header line with the PROTOCOL §1 fields, then each frame's index, pts and detections `[class, score, x1, y1, x2, y2]` |
| `tensors.sha256` | Hashes of `b.u8`, `c.u8`, `r.u8` (1,000 × 640 × 640 × 3 bytes each), kept on the board |
| `env.txt` | `collect_env.sh` before the run |
| `env.json` | Arguments, library versions, each backend's description, the harness checks |
| `dump.txt`, `dump.log` | The dump's output and its stderr |
| `check.txt`, `check.json` | The check's report, and its numbers |
