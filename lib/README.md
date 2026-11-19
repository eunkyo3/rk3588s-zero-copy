[한국어](README.ko.md)

# librkzc

Variant C's path of this repository — hardware decode, letterbox on the RGA, inference on the NPU, no CPU copy of the pixels — as a library. Steps 2 and 3 of [docs/design/packaging.md](../docs/design/packaging.md): the path is split from the benchmark, needs no OpenCV, returns errors where the benchmark exits, runs until stopped, reconnects, and hands out the detections.

**State:** a working detector with a C++ interface. There is no installed program, package or Python binding yet (steps 4 and 5). Nothing about its CPU time or latency is measured.

## Use

```cpp
#include <rkzc/rkzc.h>

rkzc::Config cfg;
cfg.model = "models/yolov8n_i8.rknn";
cfg.sources = {"rtsp://10.88.0.1:8554/ch1", "rtsp://10.88.0.1:8554/ch2"};

rkzc::Status st;
auto det = rkzc::Detector::open(cfg, &st);
if (!det) { std::fprintf(stderr, "%s\n", st.message.c_str()); return 1; }

det->on_frame([](const rkzc::Frame& f) {
    for (int i = 0; i < f.count; i++) {
        const rkzc::Detection& d = f.detections[i];   // x, y, w, h in the source frame's pixels
        std::printf("ch %d: %s %.2f\n", f.channel, rkzc::class_name(d.cls), d.score);
    }
});
det->on_event([](const rkzc::Event& e) { /* a source opened, was lost, could not be opened ... */ });
det->start();
// ...
det->stop();
```

- **Sampling:** one frame in `every_nth_frame` (6 by default: 5 inferences per second from a 30 fps stream, the benchmark's main condition).
- **Models:** YOLOv8 and YOLOv10 exports of rknn_model_zoo, picked by the model's outputs. The thresholds are the measured ones and are fixed.
- **Sources:** H.264 over RTSP (TCP) or files. A stream that ends, fails or cannot be opened is tried again, after 1 s, then 2 s, up to 30 s. A file ends its channel, or starts again with `loop_files`.
- **Callbacks** come from the library's threads, one at a time. A slow callback holds the pipeline back, and sampled frames are then dropped (`stats()` counts them).

[include/rkzc/rkzc.h](include/rkzc/rkzc.h) is the whole interface. [examples/detect_jsonl.cpp](examples/detect_jsonl.cpp) prints detections as JSON lines.

## Build and test

On the board, after `scripts/setup_board.sh` (it needs ffmpeg-rockchip, librknnrt and the RKNN headers):

```sh
cmake -S lib -B build-lib \
    -DRKZC_TEST_MODEL=$PWD/models/yolov8n_i8.rknn \
    -DRKZC_TEST_VIDEO=/path/to/2_seconds.mp4 \
    -DRKZC_TEST_OBJECTS_VIDEO=/path/to/video_with_objects.mp4 \
    -DRKZC_DEMO_DETECT=$PWD/build/demo_detect
cmake --build build-lib -j4
(cd build-lib && ctest --output-on-failure)
```

It produces `librkzc.so`, `rkzc-check` (can this board run a model and a source?) and `rkzc-jsonl` (the example).

## How it shares the measured code

- **The registered code is not touched.** `src/`, `tools/`, `scripts/`, `tests/` and the root `CMakeLists.txt` equal `freeze-m3`'s, and `scripts/reproduce_m1.sh` refuses to run otherwise. So the library is a separate build in `lib/`, and `bench_main` is built exactly as before.
- **The same source files, compiled again.** `lib/CMakeLists.txt` compiles eight files of `../src` unchanged: the decoder input, the NPU contexts, the slot pool, variant C's backend (the RGA call and the dma-buf slots) and the three post-processings.
- **Two headers are replaced** through the include path (`lib/shim/`):
  - `core/util.h`: `die()` throws where the benchmark's prints and exits. The library catches it and returns a `Status` or sends an event.
  - `preprocess/fd_cache.h`: the cache of RGA handles for decoder buffers can release one channel's entries while running. The benchmark opens each input once and releases at the end; a detector reconnects, and each reconnection is a new decoder with new buffers.
- **The pipeline is written again** (`src/detector.cpp`), after `src/core/pipeline.cpp`: the same stages, queues, slot count and stop order, without the timeline and the per-frame records, with reconnection and the callback.
- **The public header** includes no FFmpeg, RGA or RKNN header. Of functions, the library exports only `rkzc::` ones (the standard library's weak template instances aside), and it has no call to `exit`.

## Files

| Path | What |
|---|---|
| `include/rkzc/rkzc.h` | the public interface |
| `shim/core/util.h`, `shim/preprocess/fd_cache.h` | the two replaced headers |
| `src/engine.*` | the model on three NPU cores with the zero-copy slots |
| `src/detector.cpp` | the pipeline: decode, sample, prepare, infer, post-process, call back |
| `src/check.cpp` | `check_model`, `check_source` |
| `tools/rkzc_check.cpp`, `examples/detect_jsonl.cpp` | `rkzc-check`, `rkzc-jsonl` |
| `tests/` | `test_checks`, `test_detector`, `equivalence.sh` (board); `no_opencv.sh` |

## Known limits

- **All sources must have one aspect ratio.** The slots' border is filled once, so every frame must letterbox to the same band. A frame that does not is skipped with a `FrameError`.
- **The thresholds are fixed** (confidence 0.25; NMS IoU 0.45 or at most 300 detections), and so are the two preprocessing threads and the three NPU contexts.
- **Frames of one channel can arrive slightly out of order,** since three NPU workers finish independently. `Frame::seq` tells.
- **A failed open can leak what the failing step held:** a slot's buffer and mapping if the RGA refuses it, or the extra RKNN context of the output check if its run fails. These need the NPU or the kernel to fail.
- **One failure is undefined behaviour:** if the decoder does not offer DRM PRIME frames, `die()` throws inside an FFmpeg callback. `h264_rkmpp` always offers them.
- **The libraries underneath print** to stderr on their own (MPP, RKNN, FFmpeg).
- **Reconnection was tested with files that start again and with a file that does not exist,** not with a camera or an RTSP server that drops.
- **Compiled with `-fPIC` and hidden visibility,** which the measured build was not. Whether that, the callback or the coordinate mapping changes CPU time has not been measured.

## Checked

On the reset NanoPi R6C of M3 (2026-11-19), with `models/yolov8n_i8.rknn`, a 2 s 1080p H.264 file made on the board, and one of the benchmark's videos (`pexels_29130409.mp4`):

- `ctest`: 4 of 4 passed.
- **Against the registered `tools/demo_detect`** on the first 300 frames of the video (50 sampled frames): the same 806 detections, with the same classes and scores and boxes that agree after mapping to the source frame.
- **Errors:** a missing or wrong model, a missing source, a file that is not a video, a server that never answers and a wrong configuration each came back as an error; the same process went on.
- **A looped file:** two channels were each reopened 7 times in 15 s with no error, and no file descriptor was left open afterwards.
- **A source that is down** was retried 12 times in 4 s (100 ms doubling to 400 ms) while the other channel played; `stop()` returned in 0.01 s.
- **Eight looped channels for 30 s:** 152 frames sampled and delivered on each channel, none dropped, no error. This is not a measurement.
- `ldd` shows no OpenCV; `exit` and `abort` are not among the undefined symbols.
