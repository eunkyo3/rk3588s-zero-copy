// What B and A share (PROTOCOL §5.2): input slots in CPU memory, the OpenCV preprocessing and
// bind through rknn_inputs_set.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "core/npu.h"

// OpenCV interpolation for B and A. PROTOCOL §8: whichever of INTER_LINEAR and INTER_AREA gives
// the lower tensor MAE against C (tools/compare_prepare), recorded in AMENDMENTS.
int opencv_interpolation();
// RGA scaling mode for C (im_opt_t.interp), fixed before the OpenCV choice (PROTOCOL §8).
int rga_interpolation();
const char* opencv_interpolation_name();

// Letterbox geometry of PROTOCOL §5.2: the frame scaled to fit, centred, even sizes.
struct Band {
    int x, y, w, h;
};
Band letterbox_band(int frame_w, int frame_h, int model_w, int model_h);

// Resize first, then convert BT.601 limited-range YUV to RGB, written straight into `band` of a
// model_w x model_h x 3 slot. NV12 for B (decoder buffer), I420 for A (software decoder).
void letterbox_nv12(const uint8_t* y, int y_stride, const uint8_t* uv, int uv_stride, int w, int h,
                    uint8_t* slot, int model_w, int model_h, int interpolation);
void letterbox_i420(const uint8_t* y, int y_stride, const uint8_t* u, int u_stride, const uint8_t* v,
                    int v_stride, int w, int h, uint8_t* slot, int model_w, int model_h, int interpolation);

// Input slots in CPU memory, border filled with 114 once. bind copies the slot with memcpy into the
// context's input buffer (rknn_create_mem, bound once with rknn_set_io_mem as UINT8 NHWC).
// rknn_inputs_set is not used: librknnrt 2.3.2 then never writes the outputs bound with
// rknn_set_io_mem, which B shares with C (PROTOCOL §5.3; docs/notes/preprocess.md).
class CpuSlots {
public:
    void init(NpuContexts& npu, int n_slots);
    uint8_t* data(int slot) { return slots_[slot].data(); }
    const uint8_t* data(int slot) const { return slots_[slot].data(); }
    size_t size() const { return size_; }
    void bind(NpuContexts& npu, int k, int slot);
    void release(NpuContexts& npu);

private:
    std::vector<std::vector<uint8_t>> slots_;
    rknn_tensor_mem* input_[NpuContexts::kCount] = {};
    size_t size_ = 0;
};
