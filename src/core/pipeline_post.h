// The pipeline's choice of post-processing (M2 §1, prereg/M4.md §1, §8): one function that bench_main,
// calib_npu and M4's wiring check all call.
//   m1    M1's yolov8_post, conf 0.25, NMS 0.45 (the default)
//   neon  arm Cp's NeonPost, conf 0.25, NMS 0.45 (M2 §1); M4's arm C8
//   v10   yolov10_post, conf 0.25, at most 300 detections, no NMS; M4's arm C10
#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "core/yolov8_post.h"
#include "core/yolov8_post_neon.h"
#include "rknn_api.h"

enum class PostKind { M1, Neon, V10 };

// "m1", "neon" or "v10" into *kind; false for any other name.
bool parse_post_kind(const std::string& name, PostKind* kind);
const char* post_kind_name(PostKind kind);

class PipelinePost {
public:
    static constexpr float kConfThresh = 0.25f;  // PROTOCOL §4, M4 §1
    static constexpr float kNmsThresh = 0.45f;   // m1 and neon
    static constexpr int kMaxDet = 300;          // v10 (M4 §1)

    // Everything a run needs is built here, before the warm-up: neon's tables (M2 §1). neon and v10
    // throw std::runtime_error if attrs is not their model's layout. m1 checks nothing here, as in
    // M1; yolov8_post checks the layout on every call.
    PipelinePost(PostKind kind, const std::vector<rknn_tensor_attr>& attrs);

    // The detections of the chosen post-processing with the settings above. Reads only what the
    // constructor built, so the NPU workers share one PipelinePost.
    std::vector<Detection> run(const std::vector<const int8_t*>& outputs, const std::vector<rknn_tensor_attr>& attrs,
                               int model_w, int model_h) const;

    PostKind kind() const { return kind_; }
    // The fields of env.json's "post" object, without the braces: the name, the settings, and for
    // neon its tables.
    std::string describe() const;

private:
    PostKind kind_;
    std::unique_ptr<NeonPost> neon_;
};
