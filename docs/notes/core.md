[한국어](core.ko.md)

# Shared core and `bench_main` (M1-1)

`bench_main` runs one variant at one channel count and writes the records that PROTOCOL §7 defines. This note covers how to build and run it, what it writes, and the [M1-1 checks](#m1-1-checks).

## Build and run

```sh
tools/deploy_board.sh            # copies the working tree to the board and builds it there
# on the board, as root (the NPU load comes from debugfs):
sudo ./build/bench_main --variant C --channels 4 --source 'rtsp://<server>:8554/ch{n}'
```

| Option | Default | Meaning |
|---|---|---|
| `--variant` | (required) | `none` (decode only) or `C`. B and A follow in M1-2 |
| `--channels` | (required) | 1–32 |
| `--source` | (required) | `rtsp://…/ch{n}` or a local file, read at its frame rate like a stream; `{n}` becomes 1…N |
| `--condition` | `main` | `main`: every 6th frame (5 fps); `aux`: every frame (30 fps) |
| `--warmup`, `--duration` | 20, 120 | seconds (PROTOCOL §8) |
| `--model` | `models/yolov8n_i8.rknn` | |
| `--out` | `results/<run kind>/<UTC>_<variant>_<N>ch` | |
| `--run-kind`, `--repetition`, `--freeze-commit` | `check`, 1, `none` | written to the header (PROTOCOL §1) |

## What it does

The structure is fixed by PROTOCOL §5.1 and §8:

- **Threads:** one decoder thread per channel, with the sampler. Q1 holds 2 × channels and drops its oldest item when full. Two preprocessing threads take a slot and run `prepare`. Q2 holds 6. Three NPU workers, one context each on NPU cores 0, 1 and 2, run `bind → rknn_run → post-process`. Eleven input slots.
- **Affinity:** the process is pinned to cpu4–7 before any library starts a thread. The metrics thread then moves to cpu0.
- **Decoder:** `h264_rkmpp`, DRM PRIME, `extra_hw_frames=8`, AFBC off. RTSP is read over TCP with a 5 s socket timeout. When MPP's input queue is full, the packet is kept and sent again, never dropped.
- **SPS/PPS have to be in the stream.** `h264_rkmpp` gets them only in-band: its `h264_mp4toannexb` filter inserts them for mp4 input, but not for RTSP, where they normally travel in the SDP. Without them it returns no frames at all. `stream-server/prepare_videos.sh` therefore encodes with `repeat-headers=1`.
- **Per-buffer cache** (`src/preprocess/fd_cache.h`, PROTOCOL §8): entries are keyed by dma-buf inode, per channel and decoder buffer pool, and capped at the decoder's 38 buffers. When a channel's pool or frame size changes, its entries are retired. They are not used again and are released at shutdown, because a frame from the old pool may still be in flight.
- **Variant C:** the spike's path (docs/notes/spike.md, AMENDMENTS A1). At start-up it checks, for every context, that the CPU reads the bound NPU outputs coherently. It first runs a black slot so the output buffers hold other data, then runs the 114-grey slot. The outputs must be bit-identical to `rknn_outputs_get` on the same grey input.

## Output

Every CSV starts with `# key: value` header lines: start time, NTP state, `prereg-v1` and `freeze-m1` commits, run kind, model SHA-256, decoder, variant, channels, condition, repetition, code commit, latest amendment.

| File | Content |
|---|---|
| `frames.csv` | one row per processed frame: channel, decode-order index, PTS, t0…t4 (ns since start), slot wait, context, slot, detections |
| `samples.csv` | one row per second: NPU load per core, every thermal zone, CPU/DDR/NPU clocks, RSS, frames decoded per channel |
| `channels.csv` | per channel, over the window: decoded frames and rate, drift (§7.6), decoder drops (§7.6), sampled frames, Q1 drops, inference rate (§7.4) |
| `threads.csv` | CPU seconds in the window per thread still alive at its end, with name and affinity. Threads that exit earlier show up only in `process_cpu_unattributed_s` |
| `summary.csv` | process CPU and its split by cluster (§7.1), system CPU per core and cluster (§7.2), drop rate (§7.5), latency, MPP hold time and slot wait percentiles (§7.3), RSS, clock ranges, maximum temperature |
| `env.json` | header, arguments, every thread's affinity, queue sizes, decoder options, librga and RKNN versions, binding paths |

The idle baseline of §7.2 is a per-session measurement and is not part of a run.

## M1-1 checks

The runs were made on 2026-10-30 with these conditions:

- **Stream server:** on the board itself (mediamtx over localhost), looping the 20-second test clip of docs/notes/spike.md. The separate, wired stream server of PROTOCOL §3 is not set up yet.
- **Governors:** the defaults.
- **Build:** the working tree of this commit; the result headers say `6243d2a…-dirty`.

The CPU numbers are therefore not comparable with later measurements.

| Check | Criterion (plan M1-1) | `none`, 4 channels, 600 s | `C`, 4 channels, 600 s |
|---|---|---|---|
| Crashes | 0 | 0 | 0 |
| RSS growth over the window | ≤ 5% | 0.00% (29,032 kB) | 0.00% (147,224 kB) |
| Decode rate, each channel | 30 ± 0.5 fps | 30.000 | 30.000 |
| Maximum drift | | 0.033 s | 0.033 s |
| Decoder drops | | 0 | 0 |
| Inference rate, each channel | | | 5.000 fps |
| Q1 drops | | | 0 |
| Latency p50 / p99 | | | 32.5 / 39.1 ms |
| MPP hold time p99 | | | 7.0 ms |
| Process CPU over the window | | 98.3 s | 173.3 s |

Both runs pass. Their `summary.csv`, `channels.csv`, `threads.csv` and `env.json` are in [core/](core/).

- **Output check:** at start-up, C's output coherence check passed for all three contexts.
- **Decoder buffers:** each channel's decoder used at most 7, and none were retired.
- **Decode-only CPU:** about 4% of a core per channel. That is about 2.5% in the channel's decoder thread (RTSP and ffmpeg), 1.2% in the MPP parser and 0.4% in the MPP HAL. This is at the default governor's low A76 clocks, and B and C share it.
