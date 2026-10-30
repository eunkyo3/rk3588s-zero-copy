[한국어](HYPOTHESES.ko.md)

# Pre-registered hypotheses (M1)

- **Registration:** stage 1 of 2, tag `prereg-v1`. The reference time is the `created_at` of the GitHub Release `prereg-v1`, recorded together with the tag's commit SHA. Commit dates are self-reported, so they are not used as evidence.
- **Normative text:** this English file. `HYPOTHESES.ko.md` is a translation; if they disagree, this file governs.
- **Changes:** this file is not edited after `prereg-v1`. Every later change goes to [AMENDMENTS.md](AMENDMENTS.md) (append-only, PROTOCOL §15).
- **Definitions** of every term used here (channel sets, `L`, `P_cap`, "unsaturated", metrics, validity, H3 search) are in [PROTOCOL.md](PROTOCOL.md) v1.

## What is compared

One NanoPi R6C (RK3588S) runs the same model (YOLOv8n INT8, 640×640) behind the same decoder (ffmpeg-rockchip `h264_rkmpp`, DRM PRIME frames). Only the path from the decoded frame to the NPU input differs:

- **B (CPU copy):** map the DRM PRIME buffer into CPU memory, preprocess with OpenCV, copy into the NPU input with `rknn_inputs_set`.
- **C (zero copy):** pass the dma-buf fd to RGA, which writes into a dma-heap buffer registered with the NPU through `rknn_create_mem_from_fd`. While streaming, the CPU does not read or write pixel data; this is checked with profiles (PROTOCOL §11.4).
- **A (software decode):** CPU decoding plus the B preprocessing code. Reference only; no hypothesis uses A.

The goal is to measure where and by how much zero copy makes a difference, including the case where it makes none. A rejected hypothesis is a valid result, not a project failure.

## Verdicts

Every hypothesis gets two verdicts, and the report states both:

1. **Spec-literal verdict:** the sentence of the hypothesis applied as written. Outcome: *adopted* or *rejected* (or *indeterminate* in the cases below).
2. **Practical verdict:** a stricter reading that adds saturation and consistency checks. Outcome: *adopted*, *rejected*, or *indeterminate*.

For H1 and H2: if a measurement point that a verdict needs has fewer than 3 valid runs (PROTOCOL §9.3), the spec-literal verdict uses the valid runs that exist and is flagged, and the practical verdict is *indeterminate*. If the point has no valid run at all, both verdicts are *indeterminate*. H3 handles invalid runs as PROTOCOL §10 says. The report lists every invalid ×2 record by variant and reason.

Every result is reported with two conditions: the pipeline is restricted to the four A76 cores (cpu4–7), and whether the decoder's buffers are CPU-cached (PROTOCOL §5.1).

## H1: CPU at 8 channels

> At the main condition with 8 channels, C's pipeline process CPU usage (sum over cores) is at least 25% lower than B's.

- **Metric:** process CPU-seconds over the 120 s measurement window (PROTOCOL §7.1), mean of 3 runs.
- If 8 channels is not in the main channel set (decode-only limit `L` < 8, PROTOCOL §6.1), no 8-channel run is made and both verdicts are *indeterminate*.
- **Spec-literal verdict:** with `B` and `C` the 3-run means, *adopted* if `(B − C) / B ≥ 0.25`, otherwise *rejected*.
- **Practical verdict:** the same threshold, except that the verdict is *indeterminate* if, in any valid run of either variant at 8 channels, some channel's inference rate is below 4.75 fps (PROTOCOL §7.4).
- **Secondary metric:** system CPU with the idle baseline subtracted (PROTOCOL §7.2). If `(B − C)` has the opposite sign on this metric than on the primary metric, the H1 result is labelled *mixed* in addition to its verdicts.

## H2: tail latency at 5 channels, full-rate inference

> At the auxiliary condition with 5 channels, C's end-to-end p99 latency is lower than B's.

- **Metric:** per run, the p99 of `t4 − t0` over all processed frames (PROTOCOL §7.3). 3 runs. The drop rate (PROTOCOL §7.5) is reported next to it.
- **Spec-literal verdict:** *adopted* if the 3-run mean p99 of C is lower than that of B, otherwise *rejected*.
- **Practical verdict:**
  - *adopted* if, for every repetition `r` = 1, 2, 3, `p99(C, r) ≤ 0.9 × p99(B, r)`, and C's 3-run mean drop rate is at most 1 percentage point higher than B's;
  - *rejected* if C's mean p99 ≥ B's mean p99;
  - *indeterminate* otherwise.
- Runs are paired by repetition index because B and C of the same repetition run back to back in the same session (PROTOCOL §9.1).
- The drop-rate condition is one-sided on purpose. Q1 drops the oldest frame, so a variant that drops more frames processes fresher frames and gets lower latency. The condition stops C from winning on latency that way. A lower drop rate for C works against C's latency and needs no guard.
- **Which point decides:** the H2 verdicts are those at 5 channels. The same rules applied at the H2 auxiliary point (`floor(0.8 × P_cap / 30)` channels, PROTOCOL §6.2) give secondary verdicts, reported next to them. They never replace the 5-channel verdicts. If the auxiliary point is 0, there are no secondary verdicts.

## H3: maximum sustained channel count

> At the main condition, the maximum number of channels for which every channel sustains 5 fps inference is at least as high for C as for B.

- **Sustained** (for one 120 s run): all of the following hold.
  - every channel's inference rate ≥ 4.75 fps (PROTOCOL §7.4)
  - every channel's receive drift ≤ 1 s (PROTOCOL §7.6)
  - zero decoder drops (PROTOCOL §7.6)
- **Metric:** the maximum sustained channel counts `n_B` and `n_C`, found with the search and confirmation procedure of PROTOCOL §10.
- **Spec-literal verdict:** *adopted* if `n_C ≥ n_B`, otherwise *rejected*.
- **Practical verdict:**
  - *indeterminate (ceiling reached)* if both `n_B` and `n_C` are at least `L − 1` (PROTOCOL §6.1). Within one channel of the decode-only limit, decoder noise rather than the variant can decide which count is confirmed;
  - otherwise *adopted* if `n_C ≥ n_B` and *rejected* if `n_C < n_B`.
