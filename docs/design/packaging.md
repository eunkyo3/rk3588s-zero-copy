[한국어](packaging.ko.md)

# Design: shipping the zero-copy path as a library, a program and a package

**Status:** the proposal as it was written, with what became of it. Steps 1 to 5 of "Suggested steps" are done, each marked there; [install.md](../install.md) is the result for a user. Where the result differs from the proposal, the step says so. Nothing here is measured; the packaged path was measured afterwards, in M5 ([report](../report/M5.md)).

**The goal:** someone with a NanoPi R6C installs one package and gets variant C's path — hardware decode (MPP), conversion and letterbox on the RGA, inference on the NPU, with no CPU copy of the pixels — either as a program that runs, or as a library to build on.

**What the path is worth:** at M1's H1 point (8 channels, 5 inferences per second each), C used 18.6 s of process CPU per 120 s against B's 43.3 s ([M1](../report/M1.md)). M3 repeated both numbers on a reset board ([M3](../report/M3.md)). The package would carry that path; it would not carry the benchmark.

## The proposal in short

Three layers, one code base:

| Layer | What it is | Who uses it |
|---|---|---|
| `librkzc` | a C++ library: streams and a model in, detections out through a callback | someone writing their own program |
| `rkzc-detect` | a thin program on the library: a config file in, JSON lines out, run by systemd | someone who wants it to run |
| `rkzc_*.deb` | both, with the headers, the service file and an example config | everyone, through `apt install ./rkzc_*.deb` |

- **Why not the library alone:** to try it, a user would first need a build environment.
- **Why not the program alone:** any other output or post-processing would mean patching the source.
- **Why the program sits on the library:** one path is maintained and tested, not two.

## The library

### What it offers

A sketch of the interface, to show its size. It is not final.

```cpp
namespace rkzc {

struct Detection { float x, y, w, h, score; int cls; };   // in the source frame's pixels

struct Frame {
    int channel;
    int64_t pts;                 // the stream's timestamp
    int64_t capture_ns;          // CLOCK_MONOTONIC when the frame left the decoder
    const Detection* detections;
    int count;
};

struct Config {
    std::vector<std::string> sources;   // RTSP URLs or files, one per channel
    std::string model;                  // a .rknn file
    int every_nth_frame = 6;            // 6: 5 inferences/s from a 30 fps stream
    float conf = 0.25f, nms_iou = 0.45f;
};

class Detector {
public:
    static Result<std::unique_ptr<Detector>> open(const Config&);   // an error, not an abort
    void on_frame(std::function<void(const Frame&)>);               // called from a worker thread
    void on_event(std::function<void(const Event&)>);               // stream lost, reconnected, frames dropped
    void start();
    void stop();
    Stats stats() const;                                            // per channel: decoded, inferred, dropped
};

}  // namespace rkzc
```

- **Hidden:** the DRM PRIME frames, the dma-buf file descriptors, the input slots, the three RKNN contexts and their binding. The user never sees a pixel buffer by default.
- **Fixed, at first:** H.264 input, one model for all channels, three NPU contexts (one per core), the YOLOv8n and YOLOv10n output layouts this repository already handles.

### What must change in the code

The path exists in `src/core` and `src/preprocess`, but it was written to be measured, not embedded. The gaps, from reading the code:

| Today | Needed |
|---|---|
| Failures abort the process ("dies on failure" in `VideoSource::open`, `NpuContexts::open`) | errors returned to the caller |
| A fixed timeline: warm-up, a 120 s window, then exit (`PipelineConfig`) | run until stopped |
| A stream that ends or fails stops its channel for good | reconnect with backoff, and report it as an event |
| Every frame's seven timestamps and per-second samples are kept in memory (`RunResult`) | off by default; counters only |
| The letterbox assumes a 16:9 source into 640 × 640 (the "640x360 image band") | computed from the stream's and the model's sizes |
| Detections are counted, not handed out (`FrameRecord::detections`) | delivered, in source-frame coordinates |
| `core` is one static library that links OpenCV for variants A and B | C's path alone, without OpenCV |
| Internal headers include FFmpeg's and RKNN's headers | a public header with neither |

- **How to get there without touching what was measured:** the registered code (`freeze-m1` to `freeze-m3`) stays as it is, and `bench_main` keeps building from it. The library is built from the same sources where they can be shared, with the changes above behind it.
- **A guard against drift:** a test that feeds the same file through `bench_main`'s variant C and through `librkzc` and compares the detections. `tools/m2_equivalence` already does this kind of comparison between post-processing arms.

## The program

- **Input:** one config file, `/etc/rkzc/detect.toml`: the sources, the model, the sampling, the thresholds, and where the output goes.
- **Output:** one JSON object per inferred frame, on stdout or a Unix socket. A line holds the channel, the timestamp and the detections.
- **Run:** `systemctl enable --now rkzc-detect`. Logs go to the journal; stream events are log lines.
- **Not in the first version:** MQTT, HTTP, drawing boxes on video, recording. Each is a consumer of the JSON lines and can be a separate small program. Which of them matters depends on the use, which is not decided yet.

## The package and its dependencies

The hard part of installation is not this repository's code. M3's setup script shows what a clean FriendlyElec image lacks ([reproduce.md](../notes/reproduce.md)):

| Dependency | On the image? | In apt? | Options |
|---|---|---|---|
| Kernel drivers (rknpu, rga, MPP) | yes | — | require them; check versions at install |
| librga, MPP userspace | yes | no (FriendlyElec's own packages) | depend on the image's; check versions |
| librknnrt 2.3.2 | **no** | no | fetch it at install with its SHA-256; its licence does not allow bundling |
| ffmpeg-rockchip | no | no | (a) bundle the four built libraries, (b) build at install (6 min on the board) |
| The model (`.rknn`) | no | no | not in the package (AGPL 3.0): fetched from this repository's Release by a separate command, or the user's own |

- **Decided by the licences** ([licences.md](licences.md)):
  - **ffmpeg-rockchip** is LGPL v3 as built here. Its four libraries are bundled privately under `/opt/rkzc/lib` (as the setup script does under `/opt/ffmpeg-rockchip`), so they cannot collide with Ubuntu's FFmpeg. The package carries the licence texts, and the Release carries the exact source.
  - **librknnrt** is fetched at install from Rockchip's repository. Its licence grants no right to redistribute, and the RKNN headers forbid copying, so neither is in the package or in this repository.
  - **The models** are AGPL 3.0 and stay outside the package.
  - **This repository** is Apache 2.0.
- **Versions:** the package states the image it was tested on (`rom-version` 20251222, kernel 6.1.141, rknpu driver 0.9.8) and refuses, with a clear message, on a kernel driver or runtime it does not know. M3's pinned inventory keys are the starting list. (As built: it refuses a board without the hardware or the kernel's devices, and only says so when the image or the NPU driver is another one. The runtime is always the one it fetches.)
- **Built how:** on the board, or in an arm64 container, by a script in the repository; published as a Release asset. No apt repository at first.

## What the package would and would not promise

- **Would:** that the path it runs is variant C's, and that it was checked against `bench_main` on the tested image.
- **Would not:** M1's numbers on another board, image, camera or model. M1 to M3 measured one board, with file-backed RTSP streams at 1080p. A user's CPU saving depends on their resolution, frame rate and model; the package could include a small command that reports the user's own CPU and latency.
- **Not measured at all yet:** the library's own overhead (callbacks, coordinate conversion, JSON). If a number is to be claimed for the packaged path, it needs its own plan first, like the milestones'.

## Open questions

These change the design, and they are the owner's to answer:

1. **Scope of boards:** the NanoPi R6C with FriendlyElec's image only, or other RK3588 boards and images too? The second multiplies the version checks.
2. **Models:** YOLOv8n and YOLOv10n only, or any `.rknn` with user-supplied post-processing? The second makes the callback hand out raw tensors.
3. **Language:** is C++ enough, or is a C or Python binding needed from the start? **Answered (2026-11-19): a Python binding is wanted.** Done as a C interface and a `ctypes` module: [lib/](../../lib/README.md#c-and-python).
4. **Output of the program:** are JSON lines enough as a first step?
5. **Camera input:** only RTSP and files, or MIPI/USB cameras too? The measured path starts at a hardware decoder; a raw camera frame enters at the RGA and was never measured here.

## Suggested steps

1. Read the licences of librknnrt and ffmpeg-rockchip, and settle "bundle or fetch". **Done:** [licences.md](licences.md).
2. Split C's path out of `core` into a library target without OpenCV, with errors instead of aborts. `bench_main` keeps its behaviour, checked by the existing tests and a smoke run. **Done:** [lib/](../../lib/README.md), a separate build that compiles the measured sources unchanged. The registered code was not edited, so `bench_main` needed no new check.
3. Add the run-until-stopped mode, reconnection and the detection callback; add the equivalence test against `bench_main`. **Done:** `rkzc::Detector` in [lib/](../../lib/README.md). The equivalence test compares with the registered `tools/demo_detect`, which writes the detections; `bench_main` records only their count.
4. Write `rkzc-detect` and its service file. **Done:** [lib/](../../lib/README.md#the-program-rkzc-detect). The configuration is a subset of TOML; the output is JSON lines on stdout or a Unix socket; the service was run on the board from a test prefix.
5. Write the packaging script, with the `copyright` file and ffmpeg-rockchip's source archive; install the `.deb` on a reset board, the way M3 did its setup. **Done:** `lib/packaging/build_deb.sh` and [install.md](../install.md). The board was not reset again: the package was installed in a chroot of the board's base image, the way M3 tested its setup before the reset, and then on the live board with the service, and purged.
6. Decide whether to measure the packaged path, and register a plan if so. **Decided:** it is measured, as M5 ([prereg/M5.md](../../prereg/M5.md)). `rkzc` 0.1.1 adds the three fields the measurement reads, and the tool is in [lib/measure/](../../lib/measure/README.md).

Each step is a pull request of its own.
