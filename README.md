[한국어](README.ko.md)

# rk3588s-zero-copy

How much does a zero-copy video pipeline actually save on an RK3588S, and under which conditions? This repository answers that with a pre-registered experiment on a NanoPi R6C. The code is here to make the experiment reproducible.

## The comparison

Two pipelines share the same RTSP input, decoder (ffmpeg-rockchip `h264_rkmpp`, DRM PRIME frames), model (YOLOv8n INT8) and pipeline structure. Only the path from the decoded frame to the NPU input differs.

| Variant | Decoded frame → NPU input |
|---|---|
| **B** CPU copy | `mmap` the frame, preprocess with OpenCV, copy in with `rknn_inputs_set` |
| **C** zero copy | dma-buf fd → RGA → dma-heap buffer registered with `rknn_create_mem_from_fd` |
| A (reference) | software decoding + the B path, 1–2 channels only |

The hypotheses, verdict rules and measurement protocol were registered before any measurement:

- [prereg/HYPOTHESES.md](prereg/HYPOTHESES.md): H1 (CPU at 8 channels), H2 (p99 latency at 5 channels), H3 (maximum channel count)
- [prereg/PROTOCOL.md](prereg/PROTOCOL.md): conditions, metrics, validity rules, equivalence criteria
- [prereg/AMENDMENTS.md](prereg/AMENDMENTS.md): append-only record of later changes

Registration has two stages. `prereg-v1` fixes the hypotheses and protocol. `freeze-m1` fixes the implementation settings before any B/C comparison run. The reference time of each stage is the `created_at` of its GitHub Release, not the commit date.

## Milestones

| Milestone | Content | Release |
|---|---|---|
| M1 | B vs C pipelines, channel sweep report, multi-channel detection demo | `v0.1-m1` |
| M1.1 (optional) | sensitivity and exploratory runs | `v0.1.1` |
| M2 | post-processing optimization, same model | `v0.2-m2` |
| M3 | one-command reproduction scripts | `v0.3-m3` |
| M4 | appendix: NMS-free model, mAP and throughput | `v0.4-m4` |

**Current status:** M1-2. Variants B, A and C run on the board, B is aligned with C, and the zero-copy evidence is in ([docs/notes/preprocess.md](docs/notes/preprocess.md)). Next: calibration (M1-3), which needs the wired stream server and cooling.

## Hardware and software

Versions, hardware blocks and the registration reference time: [docs/notes/env.md](docs/notes/env.md). Model conversion: [models/README.md](models/README.md).

- FriendlyElec NanoPi R6C (RK3588S, 8 GB), FriendlyElec Ubuntu 24.04
- C++, [ffmpeg-rockchip](https://github.com/nyanmisaka/ffmpeg-rockchip) (`h264_rkmpp`), librga (im2d), RKNN C API (librknnrt)
- RTSP streams served by mediamtx from a separate PC over wired Ethernet
