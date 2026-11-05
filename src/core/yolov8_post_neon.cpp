// Version 4 of tools/post_probe.cpp: decode_branch_neon2 with its table and prefetches, without the
// probe's counts and clocks. Everything around the scan and the box decoding is yolov8_post's.
#include "core/yolov8_post_neon.h"

#include <arm_neon.h>

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace {

constexpr int kNumClasses = 80;

// As in core/yolov8_post.cpp.
int8_t quantize(float v, int32_t zp, float scale) {
    float q = std::round(v / scale + zp);
    return static_cast<int8_t>(std::clamp(q, -128.0f, 127.0f));
}

float dequantize(int8_t q, int32_t zp, float scale) {
    return (static_cast<float>(q) - zp) * scale;
}

float iou(const Detection& a, const Detection& b) {
    float w = std::max(0.0f, std::min(a.x2, b.x2) - std::max(a.x1, b.x1));
    float h = std::max(0.0f, std::min(a.y2, b.y2) - std::max(a.y1, b.y1));
    float inter = w * h;
    float uni = (a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - inter;
    return uni > 0.0f ? inter / uni : 0.0f;
}

// yolov8_post's checks of the outputs' layout, and the scan's own: grids of whole 16-cell blocks.
void check_layout(const std::vector<rknn_tensor_attr>& attrs) {
    if (attrs.size() != 9)
        throw std::runtime_error("yolov8_post_neon: expected 9 outputs");
    for (const auto& a : attrs) {
        if (a.fmt != RKNN_TENSOR_NCHW || a.type != RKNN_TENSOR_INT8 || a.n_dims != 4)
            throw std::runtime_error("yolov8_post_neon: outputs must be 4-D NCHW INT8");
        if (a.w_stride != 0 && a.w_stride != a.dims[3])
            throw std::runtime_error("yolov8_post_neon: outputs must be dense");
    }
    for (int b = 0; b < 3; b++) {
        const auto& box = attrs[b * 3];
        const auto& cls = attrs[b * 3 + 1];
        const auto& sum = attrs[b * 3 + 2];
        if (box.dims[1] % 4 || cls.dims[1] != kNumClasses || sum.dims[1] != 1)
            throw std::runtime_error("yolov8_post_neon: unexpected branch channels");
        for (const auto* a : {&cls, &sum})
            if (a->dims[2] != box.dims[2] || a->dims[3] != box.dims[3])
                throw std::runtime_error("yolov8_post_neon: branch grids differ");
        if (box.dims[2] * box.dims[3] % 16)
            throw std::runtime_error("yolov8_post_neon: grid size not a multiple of 16");
    }
}

// decode_dfl of core/yolov8_post.cpp on quantized bins, the bin i of a side at q[(side * dfl_len + i) * step],
// with the terms from the table. dequantize increases with q, so the largest bin is that of the largest q.
void decode_dfl_table(const float* table, const int8_t* q, size_t step, int dfl_len, float out[4]) {
    for (int side = 0; side < 4; side++) {
        const int8_t* b = q + side * dfl_len * step;
        int8_t max_q = b[0];
        for (int i = 1; i < dfl_len; i++)
            max_q = std::max(max_q, b[i * step]);
        const float* e = table + (max_q + 128) * 256 + 128;
        float sum = 0.0f, acc = 0.0f;
        for (int i = 0; i < dfl_len; i++) {
            const float ei = e[b[i * step]];
            sum += ei;
            acc += ei * i;
        }
        out[side] = acc / sum;
    }
}

// decode_branch of core/yolov8_post.cpp: first the blocks of 16 cells whose score sums pass; then their
// class scores row by row, a lane's class being the first strictly above its running best; then, block
// by block in cell order, the candidates. The class scores are prefetched two rows ahead, and the box
// bins of every block with a candidate before the first is decoded.
void decode_branch(const int8_t* box, const rknn_tensor_attr& box_attr, const int8_t* cls,
                   const rknn_tensor_attr& cls_attr, const int8_t* sum, const rknn_tensor_attr& sum_attr,
                   int model_h, float conf_thresh, const float* table, std::vector<Detection>& out) {
    const int grid_h = box_attr.dims[2];
    const int grid_w = box_attr.dims[3];
    const int grid_len = grid_h * grid_w;
    const int dfl_len = box_attr.dims[1] / 4;
    const float stride = static_cast<float>(model_h) / grid_h;
    const int8_t cls_thresh = quantize(conf_thresh, cls_attr.zp, cls_attr.scale);
    const int8_t sum_thresh = quantize(conf_thresh, sum_attr.zp, sum_attr.scale);

    const int8x16_t sum_t = vdupq_n_s8(sum_thresh);
    std::vector<int> scan;
    std::vector<uint8x16_t> pass;
    for (int cell0 = 0; cell0 < grid_len; cell0 += 16) {
        const uint8x16_t p = vcgeq_s8(vld1q_s8(sum + cell0), sum_t);  // not below the threshold
        if (vmaxvq_u8(p) != 0) {
            scan.push_back(cell0);
            pass.push_back(p);
        }
    }
    std::vector<int8x16_t> best(scan.size(), vdupq_n_s8(cls_thresh));
    std::vector<uint8x16_t> best_cls(scan.size(), vdupq_n_u8(0xff));
    for (int c = 0; c < kNumClasses; c++) {
        const int8_t* row = cls + c * grid_len;
        const int8_t* ahead = cls + std::min(c + 2, kNumClasses - 1) * grid_len;
        const uint8x16_t cv = vdupq_n_u8(static_cast<uint8_t>(c));
        for (size_t i = 0; i < scan.size(); i++) {
            __builtin_prefetch(ahead + scan[i]);
            const int8x16_t v = vld1q_s8(row + scan[i]);
            const uint8x16_t gt = vcgtq_s8(v, best[i]);
            best[i] = vbslq_s8(gt, v, best[i]);
            best_cls[i] = vbslq_u8(gt, cv, best_cls[i]);
        }
    }
    for (size_t i = 0; i < scan.size(); i++)
        if (vmaxvq_u8(vandq_u8(pass[i], vmvnq_u8(vceqq_u8(best_cls[i], vdupq_n_u8(0xff))))) != 0)
            for (int k = 0; k < 4 * dfl_len; k++)
                __builtin_prefetch(box + k * grid_len + scan[i]);

    std::vector<int8_t> rows(size_t(4 * dfl_len) * 16);
    alignas(16) int8_t best_v[16];
    alignas(16) uint8_t best_c[16], pass_v[16];
    for (size_t i = 0; i < scan.size(); i++) {
        const uint8x16_t cand = vandq_u8(pass[i], vmvnq_u8(vceqq_u8(best_cls[i], vdupq_n_u8(0xff))));
        if (vmaxvq_u8(cand) == 0)
            continue;
        const int cell0 = scan[i];
        for (int k = 0; k < 4 * dfl_len; k++)
            vst1q_s8(&rows[size_t(k) * 16], vld1q_s8(box + k * grid_len + cell0));
        vst1q_s8(best_v, best[i]);
        vst1q_u8(best_c, best_cls[i]);
        vst1q_u8(pass_v, pass[i]);
        for (int lane = 0; lane < 16; lane++) {
            if (!pass_v[lane] || best_c[lane] == 0xff)
                continue;
            const int cell = cell0 + lane, y = cell / grid_w, x = cell % grid_w;
            float d[4];
            decode_dfl_table(table, &rows[lane], 16, dfl_len, d);
            out.push_back({best_c[lane], dequantize(best_v[lane], cls_attr.zp, cls_attr.scale),
                           (x + 0.5f - d[0]) * stride, (y + 0.5f - d[1]) * stride, (x + 0.5f + d[2]) * stride,
                           (y + 0.5f + d[3]) * stride});
        }
    }
}

}  // namespace

NeonPost::NeonPost(const std::vector<rknn_tensor_attr>& attrs) {
    check_layout(attrs);
    for (int b = 0; b < 3; b++) {
        const int32_t zp = attrs[b * 3].zp;
        const float scale = attrs[b * 3].scale;
        if (std::any_of(tables_.begin(), tables_.end(), [&](const Table& t) { return t.zp == zp && t.scale == scale; }))
            continue;
        if (!(scale > 0))
            throw std::runtime_error("yolov8_post_neon: box scale not positive");
        Table t{zp, scale, std::vector<float>(256 * 256)};
        volatile float v[256];  // stored and read back, as decode_dfl reads its bins
        for (int q = 0; q < 256; q++)
            v[q] = dequantize(static_cast<int8_t>(q - 128), zp, scale);
        for (int m = 0; m < 256; m++)
            for (int q = 0; q < 256; q++)
                t.e[m * 256 + q] = std::exp(v[q] - v[m]);
        tables_.push_back(std::move(t));
    }
}

size_t NeonPost::table_bytes() const {
    size_t n = 0;
    for (const auto& t : tables_)
        n += t.e.size() * sizeof(float);
    return n;
}

const float* NeonPost::table(const rknn_tensor_attr& a) const {
    for (const auto& t : tables_)
        if (t.zp == a.zp && t.scale == a.scale)
            return t.e.data();
    throw std::runtime_error("yolov8_post_neon: no table for a box output");
}

std::vector<Detection> NeonPost::run(const std::vector<const int8_t*>& outputs,
                                     const std::vector<rknn_tensor_attr>& attrs, int /*model_w*/, int model_h,
                                     float conf_thresh, float nms_thresh) const {
    if (outputs.size() != 9)
        throw std::runtime_error("yolov8_post_neon: expected 9 outputs");
    check_layout(attrs);

    std::vector<Detection> cand;
    for (int b = 0; b < 3; b++) {
        const int i = b * 3;
        decode_branch(outputs[i], attrs[i], outputs[i + 1], attrs[i + 1], outputs[i + 2], attrs[i + 2], model_h,
                      conf_thresh, table(attrs[i]), cand);
    }

    std::stable_sort(cand.begin(), cand.end(),
                     [](const Detection& a, const Detection& b) { return a.score > b.score; });
    std::vector<Detection> kept;
    std::vector<bool> removed(cand.size(), false);
    for (size_t i = 0; i < cand.size(); i++) {
        if (removed[i])
            continue;
        kept.push_back(cand[i]);
        for (size_t j = i + 1; j < cand.size(); j++) {
            if (!removed[j] && cand[j].cls == cand[i].cls && iou(cand[i], cand[j]) > nms_thresh)
                removed[j] = true;
        }
    }
    return kept;
}
