// Box decoding follows rknn_model_zoo v2.3.2 examples/yolov8/cpp/postprocess.cc (Apache-2.0).
#include "core/yolov8_post.h"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace {

constexpr int kNumClasses = 80;

int8_t quantize(float v, int32_t zp, float scale) {
    float q = std::round(v / scale + zp);
    return static_cast<int8_t>(std::clamp(q, -128.0f, 127.0f));
}

float dequantize(int8_t q, int32_t zp, float scale) {
    return (static_cast<float>(q) - zp) * scale;
}

// Expected distance per box side from its softmax over the DFL bins.
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

float iou(const Detection& a, const Detection& b) {
    float w = std::max(0.0f, std::min(a.x2, b.x2) - std::max(a.x1, b.x1));
    float h = std::max(0.0f, std::min(a.y2, b.y2) - std::max(a.y1, b.y1));
    float inter = w * h;
    float uni = (a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - inter;
    return uni > 0.0f ? inter / uni : 0.0f;
}

void decode_branch(const int8_t* box, const rknn_tensor_attr& box_attr,
                   const int8_t* cls, const rknn_tensor_attr& cls_attr,
                   const int8_t* sum, const rknn_tensor_attr& sum_attr,
                   int model_h, float conf_thresh, std::vector<Detection>& out) {
    const int grid_h = box_attr.dims[2];
    const int grid_w = box_attr.dims[3];
    const int grid_len = grid_h * grid_w;
    const int dfl_len = box_attr.dims[1] / 4;
    const float stride = static_cast<float>(model_h) / grid_h;
    const int8_t cls_thresh = quantize(conf_thresh, cls_attr.zp, cls_attr.scale);
    const int8_t sum_thresh = quantize(conf_thresh, sum_attr.zp, sum_attr.scale);

    std::vector<float> bins(4 * dfl_len);
    for (int y = 0; y < grid_h; y++) {
        for (int x = 0; x < grid_w; x++) {
            const int cell = y * grid_w + x;
            if (sum[cell] < sum_thresh)
                continue;
            int best_cls = -1;
            int8_t best = cls_thresh;
            for (int c = 0; c < kNumClasses; c++) {
                int8_t s = cls[c * grid_len + cell];
                if (s > best) {
                    best = s;
                    best_cls = c;
                }
            }
            if (best_cls < 0)
                continue;
            for (int k = 0; k < 4 * dfl_len; k++)
                bins[k] = dequantize(box[k * grid_len + cell], box_attr.zp, box_attr.scale);
            float d[4];
            decode_dfl(bins.data(), dfl_len, d);
            out.push_back({best_cls, dequantize(best, cls_attr.zp, cls_attr.scale),
                           (x + 0.5f - d[0]) * stride, (y + 0.5f - d[1]) * stride,
                           (x + 0.5f + d[2]) * stride, (y + 0.5f + d[3]) * stride});
        }
    }
}

}  // namespace

std::vector<Detection> yolov8_post(const std::vector<const int8_t*>& outputs,
                                   const std::vector<rknn_tensor_attr>& attrs,
                                   int /*model_w*/, int model_h,
                                   float conf_thresh, float nms_thresh) {
    if (outputs.size() != 9 || attrs.size() != 9)
        throw std::runtime_error("yolov8_post: expected 9 outputs");
    for (const auto& a : attrs) {
        if (a.fmt != RKNN_TENSOR_NCHW || a.type != RKNN_TENSOR_INT8 || a.n_dims != 4)
            throw std::runtime_error("yolov8_post: outputs must be 4-D NCHW INT8");
        if (a.w_stride != 0 && a.w_stride != a.dims[3])
            throw std::runtime_error("yolov8_post: outputs must be dense");
    }
    for (int b = 0; b < 3; b++) {
        const auto& box = attrs[b * 3];
        const auto& cls = attrs[b * 3 + 1];
        const auto& sum = attrs[b * 3 + 2];
        if (box.dims[1] % 4 || cls.dims[1] != kNumClasses || sum.dims[1] != 1)
            throw std::runtime_error("yolov8_post: unexpected branch channels");
        for (const auto* a : {&cls, &sum})
            if (a->dims[2] != box.dims[2] || a->dims[3] != box.dims[3])
                throw std::runtime_error("yolov8_post: branch grids differ");
    }

    std::vector<Detection> cand;
    for (int b = 0; b < 3; b++) {
        const int i = b * 3;
        decode_branch(outputs[i], attrs[i], outputs[i + 1], attrs[i + 1],
                      outputs[i + 2], attrs[i + 2], model_h, conf_thresh, cand);
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

const char* coco_label(int cls) {
    static const char* const kLabels[kNumClasses] = {
        "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
        "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
        "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
        "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
        "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
        "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
        "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
        "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
        "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
        "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
        "toothbrush"};
    return cls >= 0 && cls < kNumClasses ? kLabels[cls] : "?";
}
