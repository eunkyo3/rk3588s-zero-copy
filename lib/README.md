[한국어](README.ko.md)

# librkzc

Variant C's path of this repository — hardware decode, letterbox on the RGA, inference on the NPU, no CPU copy of the pixels — as a library. This is step 2 of [docs/design/packaging.md](../docs/design/packaging.md): the path is split from the benchmark, needs no OpenCV, and returns errors where the benchmark exits.

**State:** it opens and checks a model and a source. It does not detect anything yet; the detector (streams in, detections out) is step 3. Nothing about it is measured.

## Build and test

On the board, after `scripts/setup_board.sh` (it needs ffmpeg-rockchip, librknnrt and the RKNN headers):

```sh
cmake -S lib -B build-lib \
    -DRKZC_TEST_MODEL=$PWD/models/yolov8n_i8.rknn -DRKZC_TEST_VIDEO=/path/to/h264.mp4
cmake --build build-lib -j4
(cd build-lib && ctest --output-on-failure)
```

It produces `librkzc.so` and `rkzc-check`:

```
$ build-lib/rkzc-check --model models/yolov8n_i8.rknn --source rtsp://10.88.0.1:8554/ch1 --model /nope.rknn
model  ok      models/yolov8n_i8.rknn: 640x640, 9 outputs, runtime 2.3.2 (...) / driver 0.9.8
source ok      rtsp://10.88.0.1:8554/ch1: 1920x1080
model  FAILED  /nope.rknn: cannot read /nope.rknn
```

## How it shares the measured code

- **The registered code is not touched.** `src/`, `tools/`, `scripts/`, `tests/` and the root `CMakeLists.txt` equal `freeze-m3`'s, and `scripts/reproduce_m1.sh` refuses to run otherwise. So the library is a separate build in `lib/`, and `bench_main` is built exactly as before.
- **The same source files, compiled again.** `lib/CMakeLists.txt` compiles eight files of `../src` unchanged: the decoder input, the NPU contexts, the slot pool, variant C's backend and the three post-processings. It leaves out variants A and B (which need OpenCV) and the benchmark's timeline, records and reports.
- **Errors instead of exits.** The measured sources call `die()` from `core/util.h`, which prints and exits. `lib/shim/core/util.h` comes first on the library's include path and makes `die()` throw; the library catches it at its boundary and returns a `Status`.
- **The public header** (`include/rkzc/rkzc.h`) includes no FFmpeg, RGA or RKNN header. Of functions, the library exports only `rkzc::` ones (the standard library's weak template instances aside), and it has no call to `exit`.

## Files

| Path | What |
|---|---|
| `include/rkzc/rkzc.h` | the public interface: `check_model`, `check_source`, `version` |
| `shim/core/util.h` | `die()` that throws, for the measured sources |
| `src/engine.*` | the model on three NPU cores with the zero-copy slots; opened with an error, freed in PROTOCOL §5.4's order |
| `src/check.cpp` | the public functions |
| `tools/rkzc_check.cpp` | `rkzc-check` |
| `tests/` | `test_checks` (board), `no_opencv.sh` (dependencies and exported symbols) |

## Known limits

- **A failed open can leak what the failing step held:** a slot's buffer and mapping if the RGA refuses it, or the extra RKNN context of the output check if its run fails. What earlier steps finished is freed. These need the NPU or the kernel to fail; closing them needs a change in the shared files.
- **One failure is undefined behaviour:** if the decoder does not offer DRM PRIME frames, `die()` throws inside an FFmpeg callback, through FFmpeg's C code. The process may end, or FFmpeg's state may be left half-built. `h264_rkmpp` always offers them.
- **Failures on the worker threads** (`prepare`, `bind`, `rknn_run`) are not reached by these two checks. The detector of step 3 has to catch them.
- **The libraries underneath print** to stderr on their own (MPP, RKNN, FFmpeg).
- **Compiled with `-fPIC` and hidden visibility,** which the measured build was not; the warning and frame-pointer options are the same. Whether that changes CPU time has not been measured.

## Checked

On the reset NanoPi R6C of M3 (2026-11-19), with `models/yolov8n_i8.rknn` and a 2 s 1080p H.264 file made on the board:

- `ctest`: 2 of 2 passed.
- A missing model, a file that is not a model, a missing source and a file that is not a video each returned an error, and the same process then opened the model and the source twice.
- A server that accepts the connection and never answers failed at the 1 s time limit (after 1.0 s).
- `rkzc-check` opened and closed the model and the source 20 times in one process.
- `ldd` shows no OpenCV; the exported functions are `rkzc::check_model`, `rkzc::check_source` and `rkzc::version`; `exit` and `abort` are not among the undefined symbols.
- An independent review of the first version found the missing time limit in `check_source`; it was added before this was merged.
