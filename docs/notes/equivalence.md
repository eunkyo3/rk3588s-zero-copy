[한국어](equivalence.ko.md)

# Equivalence of B and C (M1-4)

PROTOCOL §11.1 and §11.2, decided under the criteria in force, those of [AMENDMENTS A5](../../prereg/AMENDMENTS.md).

- **Under A5, B and C are equivalent:** all six conditions pass, and `tools/equivalence_check.py` exits with 0.
- **Under the registered criteria, they are not:** every condition fails except the border check. The report states this, as §11.3 and A5's last point require.

The run:

- **Where:** on the board, 2026-11-02T01:11:23Z, code commit `669fb9b`, amendment A6 in force.
- **Governors:** default; M1-3 has not fixed the clocks yet.
- **Input:** the first 1,000 frames of video ① (Pexels 36330861), read as `file_source`.
- **Outputs:** in [results/equivalence/20261102T011123Z/](../../results/equivalence/20261102T011123Z/). The check's output is [check.txt](../../results/equivalence/20261102T011123Z/check.txt).
- **Other runs kept** (see [Checks on the result](#checks-on-the-result)):
  - `20261102T011343Z` repeats it on the same code.
  - `20261102T004513Z` was made with the tools before the review fixes (code `958641b`), and is superseded.

## Method

| Tool | What it does |
|---|---|
| `tools/equivalence_dump` | Decodes each frame once with `h264_rkmpp` and gives it to C, B and R. Each has its own three contexts. It runs every frame on NPU core 0, then the shared post-processing (confidence 0.25, NMS 0.45), and writes every frame's whole input tensor and its detections. |
| `tools/equivalence_check.py` | Computes both levels from those files alone, and prints the registered and the A5 verdicts. The exit code follows A5. |
| `tools/equivalence.sh` | Runs `collect_env.sh`, the dump, then the check. The tensors (3 × 1.2 GB) stay on the board, and their SHA-256 hashes are in `tensors.sha256`. |

- **R** is B's preprocessing with plain `INTER_LINEAR`, §8's other choice (A5). B is as used: `INTER_AREA` with the luma box moved by one pixel (A4).
- **Harness checks,** for each of C, B and R, on every 100th frame and the last (11 frames). Both held:
  - NPU cores 1 and 2 give core 0's outputs. The pipeline spreads frames over the three cores; this tool uses core 0.
  - The bound outputs equal `rknn_outputs_get` on the same tensor (A3).
- **Matching:**
  - Per frame and class, by the Hungarian algorithm on IoU.
  - IoU below 0.5 counts as 0 before solving. The matching then maximises the summed IoU of the pairs that may form (IoU ≥ 0.5). Solving on raw IoU could choose two pairs below 0.5 over one above, and then drop both.
- **Float32:** scores and coordinates are rounded to float32 when read, the type post-processing gives them. The band 0.25 ± 0.02 has float32 edges.
- **Exit codes:** 0 pass, 1 fail, 2 inconsistent inputs or a check that could not run. The wrapper exits with 3 for any failure before the check.
- **Tests:** `tests/test_equivalence_check.py`.
  - The Hungarian solver against brute force, on 500 random matrices, and a case where it differs from greedy matching.
  - The masking at IoU 0.5.
  - The near-threshold exclusion in both directions, and at the band's edges after the JSON round trip.
  - Each A5 condition failing on its own; inconsistent inputs; a crash.

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

Boxes per class, for the 8 classes with at least 100 boxes in B, against B (`check.json` has every class):

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

- **Repeatability:** [20261102T011343Z](../../results/equivalence/20261102T011343Z/) repeats the run on the same code.
  - Its `tensors.sha256` equals the deciding run's, so all three tensor files are byte-identical.
  - Its detection lines are identical, and its `check.txt` is the same.
  - The superseded run `20261102T004513Z` has the same tensor hashes and detection lines too. Its `check.txt` differs only in the code commit.
  - The computation is deterministic, and clocks change timing, not values. M1-3's fixed clocks are not expected to change this result.
  - A change to the B or C code before `freeze-m1` means a rerun (§15).
- **Agreement with M1-2's estimate:** `tools/compare_prepare` used greedy matching in C++ ([preprocess/compare_v1_1000.txt](preprocess/compare_v1_1000.txt)).
  - **C vs B:** the pairs are the same 15,246, and every share agrees to the four decimals printed.
  - **R vs B:** the Hungarian matching finds one more pair, 14,903 against 14,902. In frame 107 it pairs two boxes where greedy matching pairs one.
    - The larger denominator moves R's share of IoU ≥ 0.95 from 0.4697 (6,999 of 14,902) to 46.96% (6,999 of 14,903).
    - The other shares agree to four decimals.
  - **Tensor figures:** the same, from two implementations, one in C++ and one in Python with numpy.

## What this means

- **On every A5 measure, C is closer to B than B's two admissible OpenCV variants are to each other.** Its total box count, the post-processing workload, matches B's within 0.44%.
- **The registered criteria fail, as M1-2 predicted (A5).** Under input differences of about 2 levels in 255, the INT8 model moves confidences by up to 0.26. 3.6% of the pairs differ by more than 0.10.
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
| `tensors.sha256` | Hashes of `b.u8`, `c.u8`, `r.u8` (1,000 × 640 × 640 × 3 bytes each), kept on the board only |
| `env.txt` | `collect_env.sh` before the run |
| `env.json` | Arguments, library versions, each backend's description, the harness checks |
| `dump.txt`, `dump.log` | The dump's output and its stderr |
| `check.txt`, `check.json` | The check's report, and its numbers |

The run's start time is `start_time_utc` in the JSONL headers. `env.txt` has the time of `collect_env.sh`.
