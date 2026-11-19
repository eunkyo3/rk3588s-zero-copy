// librkzc: variant C's path of this repository (hardware decode, RGA letterbox, NPU inference, no CPU
// copy of the pixels) as a library. This header needs no FFmpeg, RGA or RKNN header.
//
// So far it offers the two checks below. The detector itself (streams in, detections out) comes with
// the next step of docs/design/packaging.md.
#pragma once

#include <string>

#define RKZC_API __attribute__((visibility("default")))

namespace rkzc {

// The result of a call that can fail. Nothing in this library exits the process on failure.
struct Status {
    bool ok = true;
    std::string message;  // why it failed; empty when ok
    explicit operator bool() const { return ok; }
};

struct ModelInfo {
    int width = 0, height = 0;  // the model's input
    int outputs = 0;            // number of output tensors
    std::string runtime;        // librknnrt's version and the NPU driver's
};

struct SourceInfo {
    int width = 0, height = 0;  // the first decoded frame
};

RKZC_API const char* version();

// Loads the model as the pipeline does: three contexts, one per NPU core, with the dma-buf input slots
// that the RGA writes and the NPU reads. Checks that the bound outputs read the same as the runtime's
// own copy path, then frees everything.
RKZC_API Status check_model(const std::string& model_path, ModelInfo* info = nullptr);

// Opens an H.264 input (an rtsp:// URL or a file) with the hardware decoder and decodes one frame.
RKZC_API Status check_source(const std::string& url, SourceInfo* info = nullptr);

}  // namespace rkzc
