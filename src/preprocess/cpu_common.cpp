#include "preprocess/cpu_common.h"

#include <algorithm>
#include <cstring>

#include <opencv2/imgproc.hpp>

#include "core/util.h"

namespace {

constexpr uint8_t kPad = 114;

// Luma downscale. For an exact 1/3 scale (1920x1080 -> 640x360) with INTER_AREA, the box is moved
// by one pixel to match RGA3, which averages input columns 3c+1..3c+3 and rows 3r+1..3r+3 for
// output (c, r) and ignores im_opt_t.interp (tools/rga_kernel, docs/notes/preprocess.md). That is
// INTER_AREA on the frame shifted by one pixel; the last row and column, whose box reaches past
// the frame, repeat the frame's last row and column.
void resize_luma(const cv::Mat& src, cv::Mat& dst, cv::Size size, int interpolation) {
    if (interpolation != cv::INTER_AREA || src.cols != 3 * size.width || src.rows != 3 * size.height) {
        cv::resize(src, dst, size, 0, 0, interpolation);
        return;
    }
    const int W = size.width, H = size.height;
    dst.create(size, CV_8UC1);
    cv::Mat inner = dst(cv::Rect(0, 0, W - 1, H - 1));
    cv::resize(src(cv::Rect(1, 1, 3 * (W - 1), 3 * (H - 1))), inner, inner.size(), 0, 0, cv::INTER_AREA);
    auto box = [&](int c, int r) {
        int sum = 0;
        for (int dy = 1; dy <= 3; dy++)
            for (int dx = 1; dx <= 3; dx++)
                sum += src.at<uint8_t>(std::min(3 * r + dy, src.rows - 1), std::min(3 * c + dx, src.cols - 1));
        return static_cast<uint8_t>((sum + 4) / 9);
    };
    for (int r = 0; r < H; r++)
        dst.at<uint8_t>(r, W - 1) = box(W - 1, r);
    for (int c = 0; c < W - 1; c++)
        dst.at<uint8_t>(H - 1, c) = box(c, H - 1);
}

}  // namespace

// AMENDMENTS A4: INTER_AREA, the lower MAE against C (with the luma box of resize_luma).
int opencv_interpolation() { return cv::INTER_AREA; }
int rga_interpolation() { return 0; }  // IM_INTERP_DEFAULT; RGA3 ignores it
const char* opencv_interpolation_name() { return "INTER_AREA, luma box moved by one pixel at 1/3 scale"; }

Band letterbox_band(int frame_w, int frame_h, int model_w, int model_h) {
    const float scale = std::min(float(model_w) / frame_w, float(model_h) / frame_h);
    const int w = int(frame_w * scale + 0.5f) & ~1;
    const int h = int(frame_h * scale + 0.5f) & ~1;
    return {(model_w - w) / 2, (model_h - h) / 2, w, h};
}

void letterbox_nv12(const uint8_t* y, int y_stride, const uint8_t* uv, int uv_stride, int w, int h,
                    uint8_t* slot, int model_w, int model_h, int interpolation) {
    const Band b = letterbox_band(w, h, model_w, model_h);
    const cv::Mat src_y(h, w, CV_8UC1, const_cast<uint8_t*>(y), y_stride);
    const cv::Mat src_uv(h / 2, w / 2, CV_8UC2, const_cast<uint8_t*>(uv), uv_stride);
    thread_local cv::Mat small_y, small_uv;
    resize_luma(src_y, small_y, cv::Size(b.w, b.h), interpolation);
    cv::resize(src_uv, small_uv, cv::Size(b.w / 2, b.h / 2), 0, 0, interpolation);
    cv::Mat band = cv::Mat(model_h, model_w, CV_8UC3, slot)(cv::Rect(b.x, b.y, b.w, b.h));
    const uint8_t* before = band.data;
    cv::cvtColorTwoPlane(small_y, small_uv, band, cv::COLOR_YUV2RGB_NV12);
    CHECK(band.data == before, "cvtColorTwoPlane did not write into the slot");
}

void letterbox_i420(const uint8_t* y, int y_stride, const uint8_t* u, int u_stride, const uint8_t* v,
                    int v_stride, int w, int h, uint8_t* slot, int model_w, int model_h, int interpolation) {
    const Band b = letterbox_band(w, h, model_w, model_h);
    // cvtColor wants I420 as one contiguous image: Y, then U, then V.
    thread_local std::vector<uint8_t> i420;
    i420.resize(size_t(b.w) * b.h * 3 / 2);
    cv::Mat small_y(b.h, b.w, CV_8UC1, i420.data());
    cv::Mat small_u(b.h / 2, b.w / 2, CV_8UC1, i420.data() + b.w * b.h);
    cv::Mat small_v(b.h / 2, b.w / 2, CV_8UC1, i420.data() + b.w * b.h * 5 / 4);
    resize_luma(cv::Mat(h, w, CV_8UC1, const_cast<uint8_t*>(y), y_stride), small_y, small_y.size(), interpolation);
    cv::resize(cv::Mat(h / 2, w / 2, CV_8UC1, const_cast<uint8_t*>(u), u_stride), small_u, small_u.size(), 0, 0,
               interpolation);
    cv::resize(cv::Mat(h / 2, w / 2, CV_8UC1, const_cast<uint8_t*>(v), v_stride), small_v, small_v.size(), 0, 0,
               interpolation);
    cv::Mat band = cv::Mat(model_h, model_w, CV_8UC3, slot)(cv::Rect(b.x, b.y, b.w, b.h));
    const uint8_t* before = band.data;
    cv::cvtColor(cv::Mat(b.h * 3 / 2, b.w, CV_8UC1, i420.data()), band, cv::COLOR_YUV2RGB_I420);
    CHECK(band.data == before, "cvtColor did not write into the slot");
}

void CpuSlots::init(NpuContexts& npu, int n_slots) {
    size_ = size_t(npu.model_w()) * npu.model_h() * 3;
    slots_.assign(n_slots, std::vector<uint8_t>(size_, kPad));
    rknn_tensor_attr attr = npu.input;
    attr.type = RKNN_TENSOR_UINT8;
    attr.fmt = RKNN_TENSOR_NHWC;
    attr.pass_through = 0;
    for (int k = 0; k < NpuContexts::kCount; k++) {
        input_[k] = rknn_create_mem(npu.ctx[k], std::max<uint32_t>(attr.size_with_stride, uint32_t(size_)));
        CHECK(input_[k] && rknn_set_io_mem(npu.ctx[k], input_[k], &attr) == RKNN_SUCC, "input binding failed");
    }
}

void CpuSlots::bind(NpuContexts&, int k, int slot) {
    std::memcpy(input_[k]->virt_addr, slots_[slot].data(), size_);
}

void CpuSlots::release(NpuContexts& npu) {
    for (int k = 0; k < NpuContexts::kCount; k++) {
        if (input_[k])
            rknn_destroy_mem(npu.ctx[k], input_[k]);
        input_[k] = nullptr;
    }
    slots_.clear();
}
