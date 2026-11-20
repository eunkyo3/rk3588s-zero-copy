[한국어](README.ko.md)

# librkzc

Variant C's path of this repository — hardware decode, letterbox on the RGA, inference on the NPU, no CPU copy of the pixels — as a library. Steps 2 and 3 of [docs/design/packaging.md](../docs/design/packaging.md): the path is split from the benchmark, needs no OpenCV, returns errors where the benchmark exits, runs until stopped, reconnects, and hands out the detections.

**State:** a working detector with C++, C and Python interfaces. There is no installed program or package yet (steps 4 and 5). Nothing about its CPU time or latency is measured.

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
- **Sources:** H.264 over RTSP (TCP) or files. A source that ends, fails, cannot be opened, or is open and gives no frame for 10 s is opened again. The wait is 1 s, doubling up to 30 s while no frame arrives, and 1 s again once one does. A file that played to its end finishes its channel, or starts again with `loop_files`; a file that cannot be opened is retried like a stream.
- **Callbacks** come from the library's threads, one at a time. A slow callback holds the pipeline back, and sampled frames are then dropped (`stats()` counts them).

[include/rkzc/rkzc.h](include/rkzc/rkzc.h) is the whole interface. [examples/detect_jsonl.cpp](examples/detect_jsonl.cpp) prints detections as JSON lines.

## C and Python

- **C:** [include/rkzc/rkzc_c.h](include/rkzc/rkzc_c.h) is the same detector as plain C functions in the same `librkzc.so`: `rkzc_open`, `rkzc_on_frame`, `rkzc_start`, `rkzc_stop`, `rkzc_close`. A failure is a return value and a reason in the caller's buffer.
- **Python:** [python/rkzc](python/rkzc/__init__.py) calls the C interface through `ctypes`. It needs Python 3 and nothing else: no compiled module, no NumPy.

```python
import rkzc

def on_frame(frame):                      # called from the library's threads, one at a time
    for d in frame.detections:            # x, y, w, h in the source frame's pixels
        print(frame.channel, d.name, round(d.score, 2), d.x, d.y, d.w, d.h)

with rkzc.Detector(["rtsp://10.88.0.1:8554/ch1"], "models/yolov8n_i8.rknn", on_frame=on_frame) as det:
    det.start()
    det.wait(60)                          # seconds; Ctrl-C works while waiting
```

- **Finding the library:** there is no package yet, so the module is used from the source tree: `PYTHONPATH=lib/python`, and `RKZC_LIBRARY=build-lib/librkzc.so.0` (otherwise `librkzc.so.0` on the loader's path).
- **The options** are `rkzc::Config`'s, as keyword arguments; `det.stats()`, `rkzc.check_model()` and `rkzc.check_source()` are there too. A failure raises `rkzc.Error` with the library's reason.
- **An exception in a callback** is printed and counted in `det.callback_errors`; the detector goes on.
- **A started detector lives until it is closed,** also when the program drops its last reference; the ones still running are closed when the interpreter exits.
- [python/examples/detect_jsonl.py](python/examples/detect_jsonl.py) prints the same JSON lines as the C++ example.

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

It produces `librkzc.so`, `rkzc-check` (can this board run a model and a source?) and `rkzc-jsonl` (the example). The Python test needs `python3` and nothing installed.

## How it shares the measured code

- **The registered code is not touched.** `src/`, `tools/`, `scripts/`, `tests/` and the root `CMakeLists.txt` equal `freeze-m3`'s, and `scripts/reproduce_m1.sh` refuses to run otherwise. So the library is a separate build in `lib/`, and `bench_main` is built exactly as before.
- **The same source files, compiled again.** `lib/CMakeLists.txt` compiles eight files of `../src` unchanged: the decoder input, the NPU contexts, the slot pool, variant C's backend (the RGA call and the dma-buf slots) and the three post-processings.
- **Two headers are replaced** through the include path (`lib/shim/`):
  - `core/util.h`: `die()` throws where the benchmark's prints and exits. The library catches it and returns a `Status` or sends an event.
  - `preprocess/fd_cache.h`: the cache of RGA handles for decoder buffers can release one channel's entries while running. The benchmark opens each input once and releases at the end; a detector reconnects, and each reconnection is a new decoder with new buffers.
- **The pipeline is written again** (`src/detector.cpp`), after `src/core/pipeline.cpp`: the same stages, queues, slot count and stop order, without the timeline and the per-frame records, with reconnection and the callback.
- **The public header** includes no FFmpeg, RGA or RKNN header. Of functions, the library exports only `rkzc::` ones and the C interface's `rkzc_` ones (the standard library's weak template instances aside), and it has no call to `exit`.

## Files

| Path | What |
|---|---|
| `include/rkzc/rkzc.h` | the public interface |
| `include/rkzc/rkzc_c.h`, `src/c_api.cpp` | the C interface |
| `python/rkzc/`, `python/examples/detect_jsonl.py` | the Python module and its example |
| `shim/core/util.h`, `shim/preprocess/fd_cache.h` | the two replaced headers |
| `src/engine.*` | the model on three NPU cores with the zero-copy slots |
| `src/detector.cpp` | the pipeline: decode, sample, prepare, infer, post-process, call back |
| `src/check.cpp` | `check_model`, `check_source` |
| `tools/rkzc_check.cpp`, `examples/detect_jsonl.cpp` | `rkzc-check`, `rkzc-jsonl` |
| `tests/` | `test_checks`, `test_detector`, `test_c_api`, `test_python.py`, `equivalence.sh` (board); `no_opencv.sh`; `rtsp_interruptions.sh` (manual, on a PC) |

## Known limits

- **All sources must have one aspect ratio.** The slots' border is filled once, so every frame must letterbox to the same band. A frame that does not is skipped with a `FrameError`.
- **The thresholds are fixed** (confidence 0.25; NMS IoU 0.45 or at most 300 detections), and so are the two preprocessing threads and the three NPU contexts.
- **Frames of one channel can arrive slightly out of order,** since three NPU workers finish independently. `Frame::seq` tells.
- **A failed open can leak what the failing step held:** a slot's buffer and mapping if the RGA refuses it, or the extra RKNN context of the output check if its run fails. These need the NPU or the kernel to fail.
- **One failure is undefined behaviour:** if the decoder does not offer DRM PRIME frames, `die()` throws inside an FFmpeg callback. `h264_rkmpp` always offers them.
- **The libraries underneath print** to stderr on their own (MPP, RKNN, FFmpeg).
- **Reconnection was tested against one RTSP server (mediamtx) on a wired link,** not against a camera, Wi-Fi or packet loss. The table under "Checked" is from a manual test, not from `ctest`.
- **Python callbacks run under Python's global lock,** one at a time, and the library waits for each. What a callback costs in CPU time or in dropped frames has not been measured; a slow one drops frames like a slow C++ one.
- **The Python module is not installable yet:** there is no `pyproject.toml`; the package of step 5 is to put it on the system's path.
- **On `stop()` the decoders close while already prepared frames are still being inferred.** The benchmark keeps its inputs open until the pipeline has drained. Frames waiting for preprocessing are let through first, so no frame holds a decoder buffer when its decoder closes.
- **Not the measured configuration in three ways:** it is compiled with `-fPIC` and hidden visibility; it pins nothing, where `bench_main` pins its process, and so MPP's, FFmpeg's and librknnrt's threads, to the A76 cores 4 to 7; and it adds the callback, the coordinate mapping and a monitor thread. What these do to CPU time or latency has not been measured. A program that wants the benchmark's placement can pin itself before `Detector::open`.

## Checked

On the reset NanoPi R6C of M3 (2026-11-19), with `models/yolov8n_i8.rknn`, a 2 s 1080p H.264 file made on the board, and one of the benchmark's videos (`pexels_29130409.mp4`):

- `ctest`: 6 of 6 passed (2026-11-20; 4 of 4 before the C and Python interfaces).
- An independent review found no blocker; its points (no stall limit, a test that could not tell whether buffers were released, the undisclosed pinning, and smaller ones) are addressed in the code and in this file.
- **Against the registered `tools/demo_detect`** on the first 300 frames of the video (50 sampled frames): the same 806 detections, with the same classes and scores and boxes that agree after mapping to the source frame. This held with M1's post-processing and with the library's default (neon). On a portrait version of the clip, where the letterbox bars are at the sides: the same 367 detections (the test encodes this clip anew each run, so the count moves by one or two).
- **Errors:** a missing or wrong model, a missing source, a file that is not a video, a server that never answers and a wrong configuration each came back as an error; the same process went on.
- **A looped file:** two channels were each reopened 7 times in 15 s with no error. A channel held at most 2 decoder buffers for the RGA in its first play and at most 2 after four reopenings; with the release on reopening taken out, the same test saw 16 and failed. No file descriptor was left open afterwards.
- **A stall:** with the limit at 1 ms, a file's channel was halted and opened again 15 times in 3 s.
- **A source that is down** was retried 12 times in 4 s (100 ms doubling to 400 ms) while the other channel played; `stop()` returned in 0.01 s.
- **A real RTSP server, interrupted** (`tests/rtsp_interruptions.sh` on the stream PC: mediamtx and one ffmpeg publisher; the detector on the board with the retry wait capped at 4 s). The channel came back every time, with 596 frames inferred, none dropped and no frame error:

  | Interruption | Noticed after | What ended the read | Open again, after the source was back |
  |---|---|---|---|
  | A: the publisher is killed, the server stays up | 0.01 s | the server closed the stream | 5.5 s |
  | B: the publisher is frozen for 20 s | 10.0 s | the server gave the publisher up and closed the stream | 4.5 s |
  | C: the server is frozen for 20 s | 10.2 s | FFmpeg's own read timeout, twice 5 s | 0.1 s |
  | D: the server and the publisher are killed | 0.01 s | the connection closed | 4.5 s |

  - **The stall limit did not act in that run:** at its default of 10 s, the server or FFmpeg ended the read first. In a second run with the limit at 3 s, a frozen server and a frozen publisher were each noticed after 3.0 s, and the blocked network read returned 0.04 s after the monitor asked.
  - **How long the reopening takes** depends on where the retry wait stands when the source returns: up to the cap, 30 s by default.
- **Eight looped channels for 30 s:** 152 frames sampled and delivered on each channel, none dropped, no error. This is not a measurement.
- **The C and Python interfaces** (2026-11-20, same board, model and videos):
  - **Python against C++:** `python/examples/detect_jsonl.py` and `rkzc-jsonl` printed the same lines for the 34 frames both saw of the benchmark video, 535 detections.
  - **Callbacks:** a callback that raised on every frame was counted 8 times in 8 frames and the detector went on; `stop()` from a callback was refused; `stats()` from a callback worked while the main thread was inside `stop()`, which returned in 0.02 s.
  - **A detector whose last reference was dropped** went on delivering and was closed by the function that runs at exit; no file descriptor was left open.
  - **Ctrl-C:** the Python example, sent SIGINT while waiting, stopped and exited with status 0 in 0.19 s.
  - **Eight looped channels for 20 s from Python:** 100 frames sampled and delivered on each channel, none dropped. This is not a measurement.
  - **From C:** `tests/test_c_api.c`, compiled as C99, opened, played a file, counted the frames and closed.
- `ldd` shows no OpenCV; `exit` and `abort` are not among the undefined symbols.
