#include "m4/map_rules.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <stdexcept>
#include <string>

#include "core/yolov10_post.h"

namespace {

constexpr int kClasses = 80;

// As core/yolov8_post.cpp and core/yolov10_post.cpp.
float dequantize(int8_t q, int32_t zp, float scale) {
    return (static_cast<float>(q) - zp) * scale;
}

// As core/yolov10_post.cpp: the smallest int8 value whose dequantized score is above conf_thresh, so
// that q >= first_above(...) is dequantize(q) > conf_thresh. 128 when none is.
int first_above(float conf_thresh, int32_t zp, float scale) {
    for (int q = -128; q <= 127; q++)
        if (dequantize(static_cast<int8_t>(q), zp, scale) > conf_thresh)
            return q;
    return 128;
}

// As decode_dfl of core/yolov10_post.cpp, with its acc += e * i fused as GCC compiles it there.
void decode_dfl(const float* bins, int dfl_len, float out[4]) {
    for (int side = 0; side < 4; side++) {
        const float* b = bins + side * dfl_len;
        float max_v = *std::max_element(b, b + dfl_len);
        float sum = 0.0f, acc = 0.0f;
        for (int i = 0; i < dfl_len; i++) {
            float e = std::exp(b[i] - max_v);
            sum += e;
            acc = std::fma(e, static_cast<float>(i), acc);
        }
        out[side] = acc / sum;
    }
}

// core/yolov8_post.cpp's iou(a, b), operand for operand; a is the earlier kept detection.
float iou(const Detection& a, const Detection& b) {
    float w = std::max(0.0f, std::min(a.x2, b.x2) - std::max(a.x1, b.x1));
    float h = std::max(0.0f, std::min(a.y2, b.y2) - std::max(a.y1, b.y1));
    float inter = w * h;
    float uni = (a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - inter;
    return uni > 0.0f ? inter / uni : 0.0f;
}

void check_layout(const std::vector<rknn_tensor_attr>& attrs, int per_branch, const char* who) {
    const auto fail = [who](const char* what) { throw std::runtime_error(std::string(who) + ": " + what); };
    if (attrs.size() != static_cast<size_t>(3 * per_branch))
        fail(per_branch == 3 ? "expected 9 outputs" : "expected 6 outputs");
    for (const auto& a : attrs) {
        if (a.fmt != RKNN_TENSOR_NCHW || a.type != RKNN_TENSOR_INT8 || a.n_dims != 4 || a.dims[0] != 1)
            fail("outputs must be 1×C×H×W NCHW INT8");
        if (a.w_stride != 0 && a.w_stride != a.dims[3])
            fail("outputs must be dense");
        if (!(a.scale > 0.0f))
            fail("scales must be positive");
    }
    for (int b = 0; b < 3; b++) {
        const auto& box = attrs[b * per_branch];
        const auto& cls = attrs[b * per_branch + 1];
        if (box.dims[1] < 4 || box.dims[1] % 4 || cls.dims[1] != kClasses)
            fail("unexpected branch channels");
        if (per_branch == 3 && attrs[b * 3 + 2].dims[1] != 1)
            fail("the score sum must have 1 channel");
        for (int t = 1; t < per_branch; t++)
            if (attrs[b * per_branch + t].dims[2] != box.dims[2] || attrs[b * per_branch + t].dims[3] != box.dims[3])
                fail("a branch's tensors must be on one grid");
        if (b && !(box.dims[2] < attrs[(b - 1) * per_branch].dims[2] && box.dims[3] < attrs[(b - 1) * per_branch].dims[3]))
            fail("the branches must come in stride 8, 16, 32 order (the tie order follows it)");
    }
}

// Pairs whose score is above conf_thresh, over the class tensors (output b * per_branch + 1).
long count_candidates(const std::vector<const int8_t*>& outputs, const std::vector<rknn_tensor_attr>& attrs,
                      int per_branch, float conf_thresh) {
    long n = 0;
    for (int b = 0; b < 3; b++) {
        const auto& a = attrs[b * per_branch + 1];
        const int q_min = first_above(conf_thresh, a.zp, a.scale);
        const int8_t* cls = outputs[b * per_branch + 1];
        const long size = long(a.dims[1]) * a.dims[2] * a.dims[3];
        for (long i = 0; i < size; i++)
            n += cls[i] >= q_min;
    }
    return n;
}

}  // namespace

void yolov8_map_check_layout(const std::vector<rknn_tensor_attr>& attrs) {
    check_layout(attrs, 3, "yolov8_map");
}

void yolov10_map_check_layout(const std::vector<rknn_tensor_attr>& attrs) {
    yolov10_check_layout(attrs);
    check_layout(attrs, 2, "yolov10_map");
}

MapResult yolov8_map(const std::vector<const int8_t*>& outputs, const std::vector<rknn_tensor_attr>& attrs,
                     int model_w, int model_h, float conf_thresh, int max_det, int max_nms, float iou_thresh) {
    if (outputs.size() != 9)
        throw std::runtime_error("yolov8_map: expected 9 outputs");
    yolov8_map_check_layout(attrs);
    MapResult r;

    // Candidates in (branch, cell, class) order, so that an index orders equal scores.
    struct Candidate {
        int branch, cell, cls;
        float score;
    };
    std::vector<Candidate> cand;
    for (int b = 0; b < 3; b++) {
        const auto& a = attrs[b * 3 + 1];
        const int8_t* cls = outputs[b * 3 + 1];
        const int grid_len = a.dims[2] * a.dims[3];
        const int q_min = first_above(conf_thresh, a.zp, a.scale);
        for (int cell = 0; cell < grid_len; cell++)
            for (int c = 0; c < kClasses; c++) {
                const int8_t q = cls[c * grid_len + cell];
                if (q >= q_min)
                    cand.push_back({b, cell, c, dequantize(q, a.zp, a.scale)});
            }
    }
    r.candidates = static_cast<long>(cand.size());
    if (max_det <= 0 || max_nms <= 0)
        return r;

    std::vector<int> order(cand.size());
    for (size_t i = 0; i < order.size(); i++)
        order[i] = static_cast<int>(i);
    const size_t first = std::min(order.size(), static_cast<size_t>(max_nms));
    std::partial_sort(order.begin(), order.begin() + first, order.end(), [&](int i, int j) {
        return cand[i].score != cand[j].score ? cand[i].score > cand[j].score : i < j;
    });
    order.resize(first);

    // Each cell's box, decoded once.
    std::vector<std::array<float, 4>> box_of[3];
    std::vector<char> decoded[3];
    for (int b = 0; b < 3; b++) {
        const int grid_len = attrs[b * 3].dims[2] * attrs[b * 3].dims[3];
        box_of[b].resize(grid_len);
        decoded[b].assign(grid_len, 0);
    }
    std::vector<float> bins;
    const auto box = [&](const Candidate& c) {
        const auto& a = attrs[c.branch * 3];
        auto& d = box_of[c.branch][c.cell];
        if (!decoded[c.branch][c.cell]) {
            const int8_t* raw = outputs[c.branch * 3];
            const int grid_w = a.dims[3];
            const int grid_len = a.dims[2] * grid_w;
            const int dfl_len = a.dims[1] / 4;
            bins.resize(4 * dfl_len);
            for (int k = 0; k < 4 * dfl_len; k++)
                bins[k] = dequantize(raw[k * grid_len + c.cell], a.zp, a.scale);
            float dist[4];
            decode_dfl(bins.data(), dfl_len, dist);
            const float stride_x = static_cast<float>(model_w) / grid_w;
            const float stride_y = static_cast<float>(model_h) / a.dims[2];
            const int x = c.cell % grid_w, y = c.cell / grid_w;
            d = {(x + 0.5f - dist[0]) * stride_x, (y + 0.5f - dist[1]) * stride_y, (x + 0.5f + dist[2]) * stride_x,
                 (y + 0.5f + dist[3]) * stride_y};
            decoded[c.branch][c.cell] = 1;
        }
        return Detection{c.cls, c.score, d[0], d[1], d[2], d[3]};
    };

    // Greedy NMS per class, going down the order, up to the max_det + 1-th kept (only to tell a tie).
    std::vector<Detection> kept;
    std::vector<std::vector<int>> by_class(kClasses);
    for (int i : order) {
        const Detection d = box(cand[i]);
        bool dropped = false;
        for (int k : by_class[d.cls])
            if (iou(kept[k], d) > iou_thresh) {
                dropped = true;
                break;
            }
        if (dropped)
            continue;
        by_class[d.cls].push_back(static_cast<int>(kept.size()));
        kept.push_back(d);
        if (kept.size() > static_cast<size_t>(max_det))
            break;
    }
    r.tied = kept.size() > static_cast<size_t>(max_det) && kept[max_det].score == kept[max_det - 1].score;
    if (kept.size() > static_cast<size_t>(max_det))
        kept.resize(max_det);
    r.dets = std::move(kept);
    return r;
}

MapResult yolov10_map(const std::vector<const int8_t*>& outputs, const std::vector<rknn_tensor_attr>& attrs,
                      int model_w, int model_h, float conf_thresh, int max_det) {
    if (outputs.size() != 6)
        throw std::runtime_error("yolov10_map: expected 6 outputs");
    yolov10_map_check_layout(attrs);
    MapResult r;
    r.candidates = count_candidates(outputs, attrs, 2, conf_thresh);
    if (max_det <= 0)
        return r;
    // yolov10_post keeps the max_det + 1 best in its order: the first max_det are its max_det best.
    r.dets = yolov10_post(outputs, attrs, model_w, model_h, conf_thresh, max_det + 1);
    r.tied = r.dets.size() > static_cast<size_t>(max_det) && r.dets[max_det].score == r.dets[max_det - 1].score;
    if (r.dets.size() > static_cast<size_t>(max_det))
        r.dets.resize(max_det);
    return r;
}
