[한국어](spike.ko.md)

# Spike: fd chain (M1-0)

**Result: path C2 works.** A decoded DRM PRIME frame goes through RGA into a dma-heap slot registered with RKNN through `rknn_create_mem_from_fd`, and the NPU runs on it. The slot was registered with all three contexts. Freeing it in one context did not affect the others. No fallback of PROTOCOL §5.4 is needed. The spike was done on 2026-10-30, within its 15-hour cap.

The values this fixes for PROTOCOL §14 are recorded in [AMENDMENTS A1](../../prereg/AMENDMENTS.md).

## Setup

- **Program:** [`tools/spike_fd_chain.cpp`](../../tools/spike_fd_chain.cpp) with the shared post-processing [`src/core/yolov8_post.cpp`](../../src/core/yolov8_post.cpp). One channel, one thread. Each frame goes to slot `i mod 11` and context `i mod 3`.
- **Board:** as in [env.md](env.md), default governors, process pinned to the A76 cores (cpu4–7) as in PROTOCOL §5.
- **Input:** a 20-second test clip made on the board from `bus.jpg` of rknn_model_zoo v2.3.2 (SHA-256 `fb4914d1…`). The clip is not a benchmark video. It has the benchmark encoding: 1920×1080 H.264 High, 30 fps, no B-frames, GOP 30, 4 Mbps CBR, 600 frames. It is tagged BT.709, but both RGA and OpenCV below convert with BT.601.

  ```sh
  ffmpeg -f lavfi -i "color=c=0x404040:s=1920x1080:r=30:d=20" -loop 1 -framerate 30 -i bus.jpg \
    -filter_complex "[1:v]scale=-2:1080[fg];[0:v][fg]overlay=x=(main_w-overlay_w)*(0.5+0.4*sin(2*PI*t/10)):y=0:shortest=1,format=yuv420p" \
    -c:v libx264 -preset medium -profile:v high -bf 0 -g 30 -keyint_min 30 -sc_threshold 0 \
    -b:v 4M -minrate 4M -maxrate 4M -bufsize 4M -x264-params nal-hrd=cbr \
    -color_range tv -colorspace bt709 -color_primaries bt709 -color_trc bt709 -t 20 spike_bus_1080p30.mp4
  ```

- **Run:** as root, because the debugfs files need it.

  ```sh
  cmake -S . -B build && cmake --build build -j4
  sudo taskset -c 4-7 ./build/spike_fd_chain spike_bus_1080p30.mp4 models/yolov8n_i8.rknn <out_dir> 300 [rga_core_mask]
  ```

- **Logs:** [spike/spike_run.log](spike/spike_run.log) (300 frames, default mask) and [spike/spike_rga_core1.log](spike/spike_rga_core1.log) (120 frames, mask = RGA3 core 1 only).

## Results

| Check | Result |
|---|---|
| Frame layout | 1 object, 1 layer, NV12, 2 planes, modifier `DRM_FORMAT_MOD_LINEAR`. Object size 4,177,920 bytes. `wstride` = pitch = 1920. `hstride` = UV offset / pitch = 2,088,960 / 1920 = 1088. |
| fd stability | 300 frames used 6 distinct dma-buf inodes and 6 distinct fd numbers. No new buffer appeared after frame 100. The buffer group limit is 38 (see below). |
| Decoder buffers | Exporter `system-dma32`, so they are **CPU-cached and allocated from the DMA32 zone (below 4 GiB)**. The address range follows from the heap; it was not measured directly. Attached to `fdc38100.rkvdec-core` (rkvdec2 core 0) and RGA. CPU reads them at 2.7–5.4 GB/s. For comparison, it reads the uncached slot at 264 MB/s and `malloc` memory at 9.9 GB/s. |
| Slot buffers | `system-uncached-dma32`. Attached to the NPU and RGA. |
| Registration with 3 contexts | Every slot registered with all 3 contexts. The same slot run on contexts 0, 1 and 2 gave bit-identical outputs. |
| Free in one context | The slot's memory object was freed in context 0 with `rknn_destroy_mem`. Contexts 1 and 2 then ran a different slot, which changed their outputs, and then the freed slot again. That gave outputs bit-identical to before. Context 0 still ran other slots. |
| RGA core mask | Mask RGA3 core 0 \| core 1: RGA3 core 0 peaked at 7% load, RGA3 core 1 and RGA2 at 0%. Mask RGA3 core 1 only: the load moved to core 1 (7%). So `im_opt_t.core` is honoured. With one synchronous submitter, every job went to core 0 in this run. |
| Hidden input conversion | The native input is INT8 NHWC (zp −128, scale 1/255); the slot is bound as UINT8 NHWC. Process CPU time per `rknn_run`, median (range) over 15 alternating rounds of 20 runs: 2.47 ms (2.36–2.58) bound as UINT8, 2.50 ms (2.37–2.53) with an INT8 pass-through binding. A CPU conversion would have to read the 1.2 MB uncached slot, which alone takes about 4.6 ms at 264 MB/s, more than a whole run's CPU time. **The uint8→int8 step is not done on the CPU.** |
| Output layout | The native outputs are NC1HWC2. Bound as NCHW INT8: 2.5 ms CPU per run. Bound as native NHWC: 15.6 ms CPU and 34 ms wall time per run. Bound as native NC1HWC2: `rknn_run` fails ("unsupported tensor dtype", logged). The pipeline binds NCHW INT8. |
| Time per frame (mean over all 300 frames, 1 channel) | RGA `improcess` with `IM_SYNC` 1.80 ms, bind 0.09 ms, `rknn_run` 17.7 ms on one NPU core, post-processing 0.89 ms. |
| Detections, last frame | bus 0.86; person 0.88, 0.73, 0.66, 0.27. |

![Result: last frame of the slot, with detections](spike/spike_result.jpg)

The image is the slot as the NPU read it: the 640×360 band and the 114-grey letterbox border. The black bars are part of `bus.jpg`.

### Notes

- **Decoder buffer group:** with `buf_mode=half`, ffmpeg-rockchip caps the MPP buffer group at 20 (H.264) + 10 + `extra_hw_frames` (8) = 38 buffers (`rkmppdec.c`; the cap cannot be queried, so it is taken from the source). `hw_frames_ctx->initial_pool_size` is 0 in this mode. The spike frees each frame right after RGA, so only 6 buffers circulate. The pipeline holds frames in Q1, so more will circulate there. M1-1 measures how long MPP buffers are held.
- **DMA32 and cache flags in the code:** `hwcontext_rkmpp.c` sets `MPP_BUFFER_FLAGS_DMA32` by default, and `rkmppdec.c` adds `MPP_BUFFER_FLAGS_CACHABLE`. The exporter name matches.
- **Pinning matters for CPU time.** In two unpinned runs, the same comparison spread over 2.4–5.8 ms per run in both bindings. The most likely cause is the runtime's threads moving between the A55 and A76 cores.
- **CPU inside `rknn_run`:** the 2.5 ms per run with NCHW outputs was not broken down further. B and C bind outputs the same way (PROTOCOL §5.1), so they share it. The M1-2 perf evidence (PROTOCOL §11.4) counts copy symbols under `rknn_run`. If output handling appears there as a copy, it shows up in both variants.
- **RGA vs OpenCV:** the mean absolute difference between the RGA band and OpenCV on the last frame was 4.76. OpenCV converted the full frame (BT.601 limited range) and then resized with `INTER_LINEAR`. This is above the M1-4 tensor criterion (MAE ≤ 2). B's actual order is resize first, then convert. M1-2 has to bring B and C within the criterion, by aligning the color conversion and the interpolation.
- **Where the CPU touches pixels in the spike:** only in the debug checks. These are the first read of each decoder buffer (bandwidth), the RGA/OpenCV comparison on the last frame, the result image, and the one-time border `memset`. The chain itself (`importbuffer_fd`, `improcess`, `rknn_set_io_mem`, `rknn_run`) maps no pixel memory. The profile evidence of PROTOCOL §11.4 comes in M1-2.

### Follow-ups for M1-1

- The RGA handle cache keyed by dma-buf inode keeps every imported buffer alive. It is cleared only when the frame size changes. M1-1 also clears it when the decoder signals an info change, and bounds it to the buffer group limit.
- The frame's fd is used without `dup`. That is safe only while `improcess` is synchronous and runs before the frame is released, which is the case in PROTOCOL §5.
- The outputs from `rknn_create_mem` are read through `virt_addr` without an explicit `rknn_mem_sync`. The detections are correct, and the runtime should invalidate outputs because no `DISABLE_FLUSH_*` flag is set (plan M1-1). M1-1 checks this against outputs read with `rknn_outputs_get`.
