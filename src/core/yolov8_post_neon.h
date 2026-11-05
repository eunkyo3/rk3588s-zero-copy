// Arm Cp's post-processing (prereg/M2.md §1): yolov8_post's detections, field for field, from a NEON
// scan of the score sums and class scores, a table of the box decoding's softmax terms, and software
// prefetches of the outputs. Version 4 of tools/post_probe.cpp (docs/notes/m2_exploration.md).
#pragma once

#include <cstdint>
#include <vector>

#include "core/yolov8_post.h"

class NeonPost {
public:
    // Builds the tables, one per (zp, scale) of the box outputs (attrs 0, 3 and 6), before any run.
    // Throws if the outputs' layout is not yolov8_post's, a grid size is not a multiple of 16, or a box
    // scale is not positive.
    explicit NeonPost(const std::vector<rknn_tensor_attr>& attrs);

    // As yolov8_post. Reads only the tables, so the NPU workers share one NeonPost.
    std::vector<Detection> run(const std::vector<const int8_t*>& outputs, const std::vector<rknn_tensor_attr>& attrs,
                               int model_w, int model_h, float conf_thresh = 0.25f, float nms_thresh = 0.45f) const;

    size_t table_count() const { return tables_.size(); }
    size_t table_bytes() const;

private:
    // exp(b - m) for the dequantized bins b and m of all pairs (q_m, q_b), at [(q_m + 128) * 256 + q_b + 128].
    struct Table {
        int32_t zp;
        float scale;
        std::vector<float> e;
    };
    std::vector<Table> tables_;

    const float* table(const rknn_tensor_attr& a) const;
};
