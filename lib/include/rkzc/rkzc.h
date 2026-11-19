// librkzc: variant C's path of this repository (hardware decode, RGA letterbox, NPU inference, no CPU
// copy of the pixels) as a library. This header needs no FFmpeg, RGA or RKNN header.
//
//   rkzc::Config cfg;
//   cfg.model = "yolov8n_i8.rknn";
//   cfg.sources = {"rtsp://camera/stream"};
//   rkzc::Status st;
//   auto det = rkzc::Detector::open(cfg, &st);
//   if (!det) { /* st.message says why */ }
//   det->on_frame([](const rkzc::Frame& f) { /* f.detections[0 .. f.count) */ });
//   det->start();
//   ...
//   det->stop();
#pragma once

#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>

#define RKZC_API __attribute__((visibility("default")))

namespace rkzc {

// The result of a call that can fail. Nothing in this library exits the process on failure.
struct Status {
    bool ok = true;
    std::string message;  // why it failed; empty when ok
    explicit operator bool() const { return ok; }
};

RKZC_API const char* version();

// ---- the detector ----

struct Detection {
    float x, y, w, h;  // the box in the source frame's pixels; x, y is its top left corner
    float score;
    int cls;           // class index; class_name() gives COCO's name for it
};

// One inferred frame. The pointers are valid only during the callback.
struct Frame {
    int channel;         // index into Config::sources
    int64_t seq;         // the frame's number in decode order on its channel, from 0
    int64_t pts_us;      // the stream's timestamp in microseconds, -1 if it has none
    int64_t capture_ns;  // CLOCK_MONOTONIC when the frame left the decoder
    int width, height;   // the source frame
    const Detection* detections;
    int count;
};

enum class EventKind {
    SourceOpened,  // the input and its decoder are open; also after a reconnection
    SourceLost,    // a stream ended or failed; the detector will reconnect
    SourceEnded,   // a file reached its end (and Config::loop_files is off); the channel is finished
    SourceError,   // the input could not be opened; the detector will try again
    FrameError,    // one frame failed in preprocessing, inference or the frame callback; it was skipped
};

struct Event {
    EventKind kind;
    int channel;
    std::string message;
};

struct Config {
    std::vector<std::string> sources;  // rtsp:// URLs or H.264 files, one per channel
    std::string model;                 // a .rknn file; one model for all channels
    // 6: 5 inferences per second from a 30 fps stream, the benchmark's main condition.
    int every_nth_frame = 6;
    // "auto" picks by the model's outputs between "neon" (YOLOv8) and "v10" (YOLOv10). "m1" is the
    // scalar YOLOv8 post-processing M1 measured. The thresholds are the measured ones: confidence
    // 0.25; NMS IoU 0.45 for YOLOv8; at most 300 detections for YOLOv10.
    std::string post = "auto";
    bool loop_files = false;           // start a file again when it ends
    int reconnect_initial_ms = 1000;   // the wait before the first retry; doubles up to the maximum
    int reconnect_max_ms = 30000;
};

struct ChannelStats {
    int64_t decoded = 0;     // frames the decoder returned
    int64_t sampled = 0;     // every_nth_frame of them
    int64_t inferred = 0;    // delivered to the frame callback
    int64_t dropped = 0;     // sampled, then dropped because the pipeline was behind
    int64_t errors = 0;      // FrameError events
    int64_t reconnects = 0;  // times the source was opened again
    bool connected = false;
};

class Detector {
public:
    // Loads the model and checks the configuration. Null on failure, with the reason in *status.
    // The sources are opened by start(), not here: one that is down is retried, not an error.
    RKZC_API static std::unique_ptr<Detector> open(const Config& config, Status* status = nullptr);
    RKZC_API ~Detector();  // stops if running
    Detector(const Detector&) = delete;
    Detector& operator=(const Detector&) = delete;

    // Set before start(). The callbacks come from the library's threads, one at a time: no two run
    // together, so a slow callback holds the pipeline back and frames are dropped. Frames of one
    // channel can arrive slightly out of order; seq tells. Do not call stop() from a callback.
    RKZC_API void on_frame(std::function<void(const Frame&)> callback);
    RKZC_API void on_event(std::function<void(const Event&)> callback);

    RKZC_API Status start();  // once per Detector
    RKZC_API void stop();     // returns when every thread has ended; frames still in the pipeline are discarded
    // Waits until every source has ended and its frames are delivered, which only happens with files
    // and loop_files off, or until stop(). False if timeout_ms (-1: none) passed first.
    RKZC_API bool wait(int timeout_ms = -1);

    RKZC_API std::vector<ChannelStats> stats() const;
    RKZC_API int model_width() const;
    RKZC_API int model_height() const;

private:
    struct Impl;
    explicit Detector(std::unique_ptr<Impl> impl);
    std::unique_ptr<Impl> impl_;
};

// COCO's name for a class index of the YOLO models, "?" outside 0..79.
RKZC_API const char* class_name(int cls);

// ---- checks ----

struct ModelInfo {
    int width = 0, height = 0;  // the model's input
    int outputs = 0;            // number of output tensors
    std::string runtime;        // librknnrt's version and the NPU driver's
};

struct SourceInfo {
    int width = 0, height = 0;  // the first decoded frame
};

// Loads the model as the pipeline does: three contexts, one per NPU core, with the dma-buf input slots
// that the RGA writes and the NPU reads. Checks that the bound outputs read the same as the runtime's
// own copy path, then frees everything.
RKZC_API Status check_model(const std::string& model_path, ModelInfo* info = nullptr);

// Opens an H.264 input (an rtsp:// URL or a file) with the hardware decoder and decodes one frame.
// Gives up after timeout_s seconds: a stream can connect and deliver packets that never decode.
RKZC_API Status check_source(const std::string& url, SourceInfo* info = nullptr, int timeout_s = 10);

}  // namespace rkzc
