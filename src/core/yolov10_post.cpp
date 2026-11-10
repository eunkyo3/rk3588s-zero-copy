// Selection follows rknn_model_zoo v2.3.2 examples/yolov10/python/yolov10.py post_process_yolov10
// (Apache-2.0). Unlike that example's C++ process_i8, each candidate reads its own cell's box bins.
#include "core/yolov10_post.h"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace {

constexpr int kNumClasses = 80;  // As in core/yolov8_post.cpp.

// As in core/yolov8_post.cpp.
float dequantize(int8_t q, int32_t zp, float scale) {
    return (static_cast<float>(q) - zp) * scale;
}

// The smallest int8 value whose dequantized score is above conf_thresh, found with dequantize itself
// so that comparing int8 values agrees with comparing the scores. 128 when none is.
int first_above(float conf_thresh, int32_t zp, float scale) {
    for (int q = -128; q <= 127; q++)
        if (dequantize(static_cast<int8_t>(q), zp, scale) > conf_thresh)
            return q;
    return 128;
}

// Expected distance per box side from its softmax over the DFL bins. As in core/yolov8_post.cpp.
void decode_dfl(const float* bins, int dfl_len, float out[4]) {
    for (int side = 0; side < 4; side++) {
        const float* b = bins + side * dfl_len;
        float max_v = *std::max_element(b, b + dfl_len);
        float sum = 0.0f, acc = 0.0f;
        for (int i = 0; i < dfl_len; i++) {
            float e = std::exp(b[i] - max_v);
            sum += e;
            acc += e * i;
        }
        out[side] = acc / sum;
    }
}

struct Candidate {
    int branch;
    int cell;
    int cls;
};

}  // namespace

std::vector<Detection> yolov10_post(const std::vector<const int8_t*>& outputs,
                                    const std::vector<rknn_tensor_attr>& attrs,
                                    int model_w, int model_h,
                                    float conf_thresh, int max_det) {
    if (outputs.size() != 6 || attrs.size() != 6)
        throw std::runtime_error("yolov10_post: expected 6 outputs");
    for (const auto& a : attrs) {
        if (a.fmt != RKNN_TENSOR_NCHW || a.type != RKNN_TENSOR_INT8 || a.n_dims != 4)
            throw std::runtime_error("yolov10_post: outputs must be 4-D NCHW INT8");
        if (a.w_stride != 0 && a.w_stride != a.dims[3])
            throw std::runtime_error("yolov10_post: outputs must be dense");
        if (!(a.scale > 0.0f))
            throw std::runtime_error("yolov10_post: scales must be positive");
    }
    for (int b = 0; b < 3; b++) {
        const auto& box = attrs[b * 2];
        const auto& cls = attrs[b * 2 + 1];
        if (box.dims[1] < 4 || box.dims[1] % 4 || cls.dims[1] != kNumClasses)
            throw std::runtime_error("yolov10_post: unexpected branch channels");
        if (cls.dims[2] != box.dims[2] || cls.dims[3] != box.dims[3])
            throw std::runtime_error("yolov10_post: branch grids differ");
    }
    if (max_det <= 0)
        return {};

    // A cell is scanned only when its best class is a candidate. The best classes are found row by
    // row, reading each class's plane in order.
    std::vector<Candidate> cand;
    std::vector<float> score;
    std::vector<int8_t> cell_max;
    for (int b = 0; b < 3; b++) {
        const auto& a = attrs[b * 2 + 1];
        const int8_t* cls = outputs[b * 2 + 1];
        const int grid_len = a.dims[2] * a.dims[3];
        const int q_min = first_above(conf_thresh, a.zp, a.scale);
        if (q_min > 127)
            continue;
        cell_max.assign(cls, cls + grid_len);
        for (int c = 1; c < kNumClasses; c++) {
            const int8_t* row = cls + c * grid_len;
            for (int cell = 0; cell < grid_len; cell++)
                cell_max[cell] = std::max(cell_max[cell], row[cell]);
        }
        for (int cell = 0; cell < grid_len; cell++) {
            if (cell_max[cell] < q_min)
                continue;
            for (int c = 0; c < kNumClasses; c++) {
                const int8_t q = cls[c * grid_len + cell];
                if (q >= q_min) {
                    cand.push_back({b, cell, c});
                    score.push_back(dequantize(q, a.zp, a.scale));
                }
            }
        }
    }

    // Scores of different branches have different scales, so candidates are compared by score.
    // cand is in (branch, cell, class) order, so its index orders equal scores.
    std::vector<int> order(cand.size());
    for (size_t i = 0; i < order.size(); i++)
        order[i] = static_cast<int>(i);
    const size_t keep = std::min(order.size(), static_cast<size_t>(max_det));
    std::partial_sort(order.begin(), order.begin() + keep, order.end(), [&](int i, int j) {
        return score[i] != score[j] ? score[i] > score[j] : i < j;
    });
    order.resize(keep);

    std::vector<Detection> out;
    out.reserve(keep);
    std::vector<float> bins;
    for (int i : order) {
        const Candidate& c = cand[i];
        const auto& a = attrs[c.branch * 2];
        const int8_t* box = outputs[c.branch * 2];
        const int grid_w = a.dims[3];
        const int grid_len = a.dims[2] * grid_w;
        const int dfl_len = a.dims[1] / 4;
        const float stride_x = static_cast<float>(model_w) / grid_w;
        const float stride_y = static_cast<float>(model_h) / a.dims[2];
        bins.resize(4 * dfl_len);
        for (int k = 0; k < 4 * dfl_len; k++)
            bins[k] = dequantize(box[k * grid_len + c.cell], a.zp, a.scale);
        float d[4];
        decode_dfl(bins.data(), dfl_len, d);
        const int x = c.cell % grid_w, y = c.cell / grid_w;
        out.push_back({c.cls, score[i], (x + 0.5f - d[0]) * stride_x, (y + 0.5f - d[1]) * stride_y,
                       (x + 0.5f + d[2]) * stride_x, (y + 0.5f + d[3]) * stride_y});
    }
    return out;
}
