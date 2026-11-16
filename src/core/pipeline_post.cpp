#include "core/pipeline_post.h"

#include <cstdio>

#include "core/yolov10_post.h"

bool parse_post_kind(const std::string& name, PostKind* kind) {
    if (name == "m1")
        *kind = PostKind::M1;
    else if (name == "neon")
        *kind = PostKind::Neon;
    else if (name == "v10")
        *kind = PostKind::V10;
    else
        return false;
    return true;
}

const char* post_kind_name(PostKind kind) {
    switch (kind) {
    case PostKind::M1:
        return "m1";
    case PostKind::Neon:
        return "neon";
    case PostKind::V10:
        return "v10";
    }
    return "?";
}

PipelinePost::PipelinePost(PostKind kind, const std::vector<rknn_tensor_attr>& attrs) : kind_(kind) {
    if (kind == PostKind::Neon)
        neon_ = std::make_unique<NeonPost>(attrs);
    if (kind == PostKind::V10)
        yolov10_check_layout(attrs);
}

std::vector<Detection> PipelinePost::run(const std::vector<const int8_t*>& outputs,
                                         const std::vector<rknn_tensor_attr>& attrs, int model_w,
                                         int model_h) const {
    switch (kind_) {
    case PostKind::Neon:
        return neon_->run(outputs, attrs, model_w, model_h, kConfThresh, kNmsThresh);
    case PostKind::V10:
        return yolov10_post(outputs, attrs, model_w, model_h, kConfThresh, kMaxDet);
    case PostKind::M1:
        break;
    }
    return yolov8_post(outputs, attrs, model_w, model_h, kConfThresh, kNmsThresh);
}

std::string PipelinePost::describe() const {
    char b[160];
    if (kind_ == PostKind::V10)
        std::snprintf(b, sizeof b, "\"name\": \"%s\", \"conf_thresh\": %g, \"max_det\": %d", post_kind_name(kind_),
                      kConfThresh, kMaxDet);
    else
        std::snprintf(b, sizeof b, "\"name\": \"%s\", \"conf_thresh\": %g, \"nms_thresh\": %g",
                      post_kind_name(kind_), kConfThresh, kNmsThresh);
    std::string s = b;
    if (neon_)
        s += ", \"tables\": " + std::to_string(neon_->table_count()) +
             ", \"table_bytes\": " + std::to_string(neon_->table_bytes());
    return s;
}
