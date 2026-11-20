[한국어](README.ko.md)

# librkzc

Variant C's path of this repository — hardware decode, letterbox on the RGA, inference on the NPU, no CPU copy of the pixels — as a library. Steps 2 to 5 of [docs/design/packaging.md](../docs/design/packaging.md): the path is split from the benchmark, needs no OpenCV, returns errors where the benchmark exits, runs until stopped, reconnects, and hands out the detections.

**State:** a working detector with C++, C and Python interfaces, a program with a service file, and a `.deb` that installs them: [docs/install.md](../docs/install.md). Nothing about its CPU time or latency is measured.

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
- **An exception in a callback** is printed and counted in `det.callback_errors`; the detector goes on. The library does not see it: the frame counts as inferred, and there is no `FRAME_ERROR` event as there is for a C++ callback that throws.
- **From a callback,** `stats()` works; `start()`, `stop()`, `close()` and `wait()` are refused.
- **A started detector lives until it is stopped or closed,** also when the program drops its last reference; the ones still running are closed when the interpreter exits. One that is not running is freed as soon as it is dropped.
- [python/examples/detect_jsonl.py](python/examples/detect_jsonl.py) prints the same JSON lines as the C++ example.

## The program: `rkzc-detect`

The detector as a program for someone who wants it to run: a configuration file in, JSON lines out, until it is stopped (step 4).

```sh
rkzc-detect --config detect.toml --check    # reads the file, opens the detector as a run would, and every source once
rkzc-detect --config detect.toml            # runs until SIGTERM or Ctrl-C
```

- **The configuration** is a small file ([dist/detect.toml](dist/detect.toml) is the commented example): the model, the sources, the sampling, the output, the reconnection waits. The format is a subset of TOML read without a library; a key the program does not know is an error with its line number.
- **The output** is one JSON object per line, on stdout or on a Unix socket that any number of clients read:

  ```json
  {"channel":0,"seq":12,"pts_us":400000,"time_ms":1795135300305,"width":1920,"height":1080,"detections":[{"class":0,"name":"person","score":0.8411,"x":408.60,"y":415.60,"w":104.73,"h":298.37}]}
  {"event":"source_lost","channel":0,"time_ms":1795135300456,"message":"..."}
  ```

  `x`, `y`, `w`, `h` are in the source frame's pixels; `time_ms` is the wall clock when the line was written. To read the socket: `nc -U /run/rkzc/detect.sock`.
- **A socket client that stops reading is disconnected** once its buffer is full (a few hundred lines); the program does not wait for it, and a reader should drop a last line that has no newline. A client beyond `max_clients` (16) is closed at once. stdout does wait for its reader, and a reader that goes away ends the program.
- **Who may read the socket** is set by `socket_group` and `socket_mode`; the example lets the group `video` in. Connecting needs the write bit.
- **A user and password in a source's URL** are replaced by `***` in the program's log and in every line it writes. The configuration file holds them in the clear: the example says how to keep it from other users.
- **Exit status:** 0 when stopped or when every source has ended; 1 for a failure while running; 2 for a configuration that cannot be used.
- **As a service:** [dist/rkzc-detect.service.in](dist/rkzc-detect.service.in) runs it with `/etc/rkzc/detect.toml` as a user made for the run, in the groups `video` and `render` that own the path's devices. The socket is `/run/rkzc/detect.sock`; the log goes to the journal. It is restarted after a failure, but not after status 2. `cmake --install` installs the library, the headers, the programs, the unit and the example; it installs neither `/etc/rkzc/detect.toml` nor a model. The unit's path to the program is fixed when configuring (`CMAKE_INSTALL_PREFIX`). The package of step 5 is to do all of this for the user.
- **Not there:** MQTT, HTTP, drawn boxes, recording, a reload without a restart. Each of the first four can be a reader of the lines.

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

It produces `librkzc.so`, `rkzc-detect` (the program), `rkzc-check` (can this board run a model and a source?) and `rkzc-jsonl` (the example). The Python test needs `python3` and nothing installed.

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
| `tools/rkzc_detect.cpp`, `tools/detect_config.*`, `tools/line_output.*` | `rkzc-detect`: the program, its configuration reader, its output |
| `dist/detect.toml`, `dist/rkzc-detect.service.in` | the example configuration and the service file |
| `packaging/build_deb.sh`, `packaging/debian/` | builds the `.deb` and FFmpeg's source archive; the control file, the install and removal scripts, the copyright file |
| `packaging/rkzc-fetch-runtime`, `packaging/rkzc-fetch-model` | the installed commands that download librknnrt and a model |
| `packaging/set_runpath.py` | sets the bundled FFmpeg libraries' run path to `$ORIGIN` |
| `packaging/test_clean_image.sh` | a manual test: the package in a chroot of the board's base image |
| `tests/` | `test_checks`, `test_detector`, `test_c_api`, `test_python.py`, `test_rkzc_detect.py`, `equivalence.sh` (board); `test_detect_config`; `no_opencv.sh`; `rtsp_interruptions.sh` (manual, on a PC) |

## Known limits

- **All sources must have one aspect ratio.** The slots' border is filled once, so every frame must letterbox to the same band. A frame that does not is skipped with a `FrameError`.
- **The thresholds are fixed** (confidence 0.25; NMS IoU 0.45 or at most 300 detections), and so are the two preprocessing threads and the three NPU contexts.
- **Frames of one channel can arrive slightly out of order,** since three NPU workers finish independently. `Frame::seq` tells.
- **A failed open can leak what the failing step held:** a slot's buffer and mapping if the RGA refuses it, or the extra RKNN context of the output check if its run fails. These need the NPU or the kernel to fail.
- **One failure is undefined behaviour:** if the decoder does not offer DRM PRIME frames, `die()` throws inside an FFmpeg callback. `h264_rkmpp` always offers them.
- **The libraries underneath print** to stderr on their own (MPP, RKNN, FFmpeg).
- **Reconnection was tested against one RTSP server (mediamtx) on a wired link,** not against a camera, Wi-Fi or packet loss. The table under "Checked" is from a manual test, not from `ctest`.
- **Python callbacks run under Python's global lock,** one at a time, and the library waits for each. What a callback costs in CPU time or in dropped frames has not been measured; a slow one drops frames like a slow C++ one. A callback also waits for the lock while another Python thread holds it, so CPU-bound Python elsewhere in the process delays every callback.
- **The Python module is not installable yet:** there is no `pyproject.toml`; the package of step 5 is to put it on the system's path.
- **The library's threads block the program's signals** (all but the faults a thread causes itself), so that a handler such as Python's for Ctrl-C runs on the program's own threads and does not interrupt a read in the pipeline. Whether an interrupted read would have done harm was not tested.
- **`rkzc-detect`'s configuration is a subset of TOML:** no floats, dates, inline tables, nested sections, dotted or quoted keys, multi-line strings, `0x`/`0o`/`0b` integers, or escapes other than `\"` `\\` `\n` `\t`; an array holds strings only. The thresholds cannot be set, since the library fixes them.
- **The service was run from a test prefix, not from a package,** on the one image of M3. It runs as a user made for the run, which also makes the system read-only to it, with no access to home directories and a private `/tmp`; it was not hardened further. The model's place under the service (`/var/lib/rkzc`) was not used in the test.
- **Status 2 stops the service for good, also for a passing cause:** a model that fails to load because memory is short at that moment is not retried.
- **The libraries underneath may print a source's URL themselves.** In the test with a URL that could not be reached they did not; other failures were not tried.
- **On stdout, a reader that stays and stops reading** holds the program: it then cannot stop cleanly either, and under systemd is killed after 20 s. The socket has no such reader.
- **Each socket client costs a file descriptor and up to about 400 KiB of kernel memory,** and every line is sent to every client inside the detector's callback; hence the limit on clients.
- **On `stop()` the decoders close while already prepared frames are still being inferred.** The benchmark keeps its inputs open until the pipeline has drained. Frames waiting for preprocessing are let through first, so no frame holds a decoder buffer when its decoder closes.
- **Not the measured configuration in three ways:** it is compiled with `-fPIC` and hidden visibility; it pins nothing, where `bench_main` pins its process, and so MPP's, FFmpeg's and librknnrt's threads, to the A76 cores 4 to 7; and it adds the callback, the coordinate mapping and a monitor thread. What these do to CPU time or latency has not been measured. A program that wants the benchmark's placement can pin itself before `Detector::open`.

## Checked

On the reset NanoPi R6C of M3 (2026-11-19), with `models/yolov8n_i8.rknn`, a 2 s 1080p H.264 file made on the board, and one of the benchmark's videos (`pexels_29130409.mp4`):

- `ctest`: 8 of 8 passed (2026-11-20; 4 of 4 before the C and Python interfaces, 6 of 6 before the program).
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
- **An independent review of the C and Python interfaces** found no blocker. Its points are addressed: a lock that a garbage collection could take twice, a second `close()` that returned before the detector had stopped, two statements here that the tests did not show (they do now), and smaller ones.
- **The C and Python interfaces** (2026-11-20, same board, model and videos):
  - **Python against C++:** `python/examples/detect_jsonl.py` and `rkzc-jsonl` printed the same lines for the 34 frames both saw of the benchmark video, 535 detections.
  - **Callbacks:** a callback that raised on every frame was counted 8 times in 8 frames and the detector went on; `stop()`, `start()` and `close()` from a callback were refused. A callback that was held until the main thread was inside `stop()` then called `stats()` and got its answer; `stop()` returned after it, in 0.30 s.
  - **Two threads closing at once:** no frame arrived after the second `close()` returned.
  - **A detector whose last reference was dropped** went on delivering until it was closed; no file descriptor was left open. Three detectors dropped without being started were freed at once.
  - **At exit:** a program that returned with a detector running exited with status 0, and so did one whose main thread returned 0, 0.1 and 0.3 s into another thread's `close()`.
  - **Ctrl-C:** the Python example, sent SIGINT while waiting, stopped and exited with status 0 in 0.20 s.
  - **Eight looped channels for 20 s from Python:** 100 frames sampled and delivered on each channel, none dropped. This is not a measurement.
  - **From C:** `tests/test_c_api.c`, compiled as C99 without extensions, opened, played a file, counted the frames and closed; `rkzc_stop` and `rkzc_close` called from the callback did nothing and the frames went on.
  - **The fields of a detection,** read from Python and from C on the benchmark video (217 detections): every class, score and box in its range.
- **An independent review of `rkzc-detect`** found no blocker. Its points are addressed: a camera's password went out in event lines and the log; clients were unlimited and a failed accept spun; `--check` passed configurations a run refused; the socket's mode could not let a chosen group in under the service; three tests that could pass or fail for the wrong reason; and smaller ones.
- **`rkzc-detect`** (2026-11-20, same board, model and videos):
  - **The configuration reader and the output's text functions:** 50 cases, among them 39 files that must be refused, each with the expected reason, 35 of them with the line (a missing key has none, and the too-large integer is checked by its reason); the shipped example parses. The same test passed on a PC with the address and undefined-behaviour sanitizers.
  - **Exit statuses:** 2 for no arguments, a missing or misspelt configuration, a missing model, a socket that cannot be made and a group that does not exist; `--check` gave 0 for a good file, and 1 for a missing source and for a post-processing that does not fit the model, which a run refused with 2.
  - **On stdout:** a file played once gave 10 well-formed frame lines and the two event lines, and the program exited with status 0. With events and empty frames turned off, the one frame with a detection was the one line. A reader that closed the pipe ended the program with status 1.
  - **A password in a URL:** with a source `rtsp://someone:hunter2@...` that could not be reached, the 10 error lines and the program's log showed `rtsp://***@...`, and the password was nowhere in the output.
  - **On the socket:** the file had the mode and group asked for; a second program on the same socket was refused; a file left by a program that was gone did not stop the start; two clients each got well-formed lines of both channels with detections; with `max_clients = 3` a third client was served and a fourth closed without a line; a client that never read was disconnected, and only it, while the other two read 601 lines; SIGTERM ended the program with status 0 in 0.16 s and the socket file was removed.
  - **As a service,** installed for the test under `/opt/rkzc-test` and removed afterwards, with the configuration `root:video 0640` and the example's socket settings: it started, ran as a user made for the run with the groups 44 and 992 (`video`, `render`), and user `pi` read 30 frame lines of two channels with 498 detections from `/run/rkzc/detect.sock` (`video`, `0660`). `systemctl stop` returned in 0.19 s with status 0. Killed with SIGKILL, it was running again 9 s later under a new process. With an unknown key in the file it failed with status 2 and was not restarted.
- **The package** (2026-11-20, `rkzc_0.1.0-1_arm64.deb`, 6.3 MB, built on the board by `packaging/build_deb.sh`):
  - **In a chroot of the board's base image** (`packaging/test_clean_image.sh`; the image has no FFmpeg build and no librknnrt): `dpkg -i` without the network succeeded and named `rkzc-fetch-runtime`; `apt-get install` installed no other package and fetched librknnrt; every library of the programs was found in `/opt/rkzc/lib`; `rkzc-fetch-model` fetched the model; `rkzc-check` loaded it on the NPU and decoded a file; `rkzc-detect` read the installed configuration and played a file to 10 frame lines; the Python module and its example gave the same 10 frames; after `apt-get remove` the configuration and the model were still there, and after `apt-get purge` no file was left that was not there before.
  - **On the live board:** `apt install` added one package; with the service enabled, user `pi` read 30 frame lines of two channels from the socket, with `nc` and from Python; a C program compiled against the installed header ran; `systemctl stop` returned in 0.17 s with status 0; a purge while the service ran stopped it; afterwards the list of packages and the files under `/etc`, `/opt`, `/usr` and `/var/lib` were the same as before.
  - **The refusal:** on a PC, the pre-install script ended with status 1 and its message, and with status 0 under `RKZC_FORCE=1`.
- `ldd` shows no OpenCV; `exit` and `abort` are not among the undefined symbols.
