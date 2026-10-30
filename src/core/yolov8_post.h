// YOLOv8 post-processing for the rknn_model_zoo v2.3.2 export (PROTOCOL §4):
// box decoding (DFL), confidence threshold, per-class NMS.
#pragma once

#include <cstdint>
#include <vector>

#include "rknn_api.h"

struct Detection {
    int cls;
    float score;
    float x1, y1, x2, y2;  // model input coordinates
};

// outputs[i] points at output i in the layout of attrs[i], which must be NCHW INT8
// (RKNN_QUERY_OUTPUT_ATTR). The model has 3 branches of {box (4 x DFL bins), class scores,
// score sum}, in that order. Results are sorted by score, highest first.
std::vector<Detection> yolov8_post(const std::vector<const int8_t*>& outputs,
                                   const std::vector<rknn_tensor_attr>& attrs,
                                   int model_w, int model_h,
                                   float conf_thresh = 0.25f, float nms_thresh = 0.45f);

const char* coco_label(int cls);
