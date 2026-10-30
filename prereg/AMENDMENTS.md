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
