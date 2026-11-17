// M4's post-processing rules for mAP (prereg/M4.md §2.3), on the board's INT8 outputs: the C++ side of
// the two implementations. tools/m4_rules.py is the Python side, and §8 step 3 checks that both give
// the same lists on the first 50 images.
//   - yolov8_map: YOLOv8n, multi-label. Every (cell, class) pair whose score is above conf is a
//     candidate. Candidates are ordered by score, equal scores by branch (stride 8, 16, 32), then cell
//     (row-major), then class; the first max_nms are kept. Going down that order, a candidate is dropped
//     when its IoU (as yolov8_post computes it, in model input coordinates, no class offset) with an
//     earlier kept candidate of its class is above iou_thresh. The first max_det kept are the result.
//     The score-sum outputs are not used. This is M4's own rule: core/yolov8_post.cpp and arm Cp's
//     file are not changed (§2.3, §8).
//   - yolov10_map: YOLOv10n, yolov10_post with conf and max_det, as §2.3 says.
// Both also count the candidates and tell whether the rule's next detection (the max_det + 1-th) has the
// max_det-th's score (§6, "how often the 300th score was tied").
//
// Arithmetic: float32, as yolov10_post. Each box is decoded from its own cell, per side the softmax's
// expectation over the DFL bins summed bin by bin, then (x + 0.5 − d) × stride. This file is compiled
// without floating-point contraction (CMakeLists.txt), so the IoU is computed as written; the decoding's
// acc += e × i, which GCC fuses in core/yolov10_post.cpp, is written as std::fma here, so both decode
// alike (and as tools/m4_rules.py does).
#pragma once

#include <cstdint>
#include <vector>

#include "core/yolov8_post.h"
#include "rknn_api.h"

constexpr float kMapConf = 0.001f;  // §2.3: Ultralytics' val defaults
constexpr int kMapMaxDet = 300;
constexpr int kMapMaxNms = 30000;   // Ultralytics' max_nms
constexpr float kMapIou = 0.7f;

struct MapResult {
    std::vector<Detection> dets;  // in the rule's order, at most max_det
    long candidates = 0;          // (cell, class) pairs above conf
    bool tied = false;            // the max_det + 1-th detection has the max_det-th's score
};

MapResult yolov8_map(const std::vector<const int8_t*>& outputs, const std::vector<rknn_tensor_attr>& attrs,
                     int model_w, int model_h, float conf_thresh = kMapConf, int max_det = kMapMaxDet,
                     int max_nms = kMapMaxNms, float iou_thresh = kMapIou);
MapResult yolov10_map(const std::vector<const int8_t*>& outputs, const std::vector<rknn_tensor_attr>& attrs,
                      int model_w, int model_h, float conf_thresh = kMapConf, int max_det = kMapMaxDet);

// Throw std::runtime_error unless attrs is the layout of the model's export: 3 branches of NCHW INT8
// tensors, {box (4 × DFL bins), 80 class scores, score sum} for YOLOv8n and {box, class scores} for
// YOLOv10n, each branch on one grid, the grids getting smaller (stride 8, 16, 32: the tie order).
void yolov8_map_check_layout(const std::vector<rknn_tensor_attr>& attrs);
void yolov10_map_check_layout(const std::vector<rknn_tensor_attr>& attrs);
