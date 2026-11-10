// YOLOv10 post-processing for the rknn_model_zoo v2.3.2 export (M4): box decoding (DFL) and
// YOLOv10's one-to-one selection, without NMS.
#pragma once

#include <cstdint>
#include <vector>

#include "core/yolov8_post.h"
#include "rknn_api.h"

// outputs[i] points at output i in the layout of attrs[i], which must be NCHW INT8
// (RKNN_QUERY_OUTPUT_ATTR). The model has 3 branches of {box (4 x DFL bins), class scores}, in that
// order. Every (cell, class) pair scoring above conf_thresh is a candidate; the max_det best are kept,
// so a cell can give one detection per class. This equals the zoo's post_process_yolov10 (top max_det
// cells, then top max_det pairs), except which pairs tied at the max_det-th score are kept and the
// order of equal scores. Here equal scores are ordered by branch, then cell, then class. Results are
// sorted by score, highest first; max_det <= 0 gives none.
std::vector<Detection> yolov10_post(const std::vector<const int8_t*>& outputs,
                                    const std::vector<rknn_tensor_attr>& attrs,
                                    int model_w, int model_h,
                                    float conf_thresh = 0.25f, int max_det = 300);
