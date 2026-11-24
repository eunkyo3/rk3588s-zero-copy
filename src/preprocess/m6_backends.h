// M6's two backends (prereg/M6.md §1), declared apart from preprocess/backend.h so that the files of
// the earlier arms do not change (M6 §8 step 2).
//   Rc  RGA3 converts as in C, into a CPU-cached slot; bind copies the slot into the bound input.
//   By  B with its resize and colour conversion done by libyuv.
#pragma once

#include <cstdint>
#include <memory>
#include <string>

#include "preprocess/backend.h"

// A backend whose bind copies into a bound input buffer can give that buffer back: what the NPU read.
// For tools only (tools/m6_rc_check).
class InputReader {
public:
    virtual ~InputReader() = default;
    // Copies context k's bound input buffer (model_w x model_h x 3) into `out`.
    virtual void read_input(int k, uint8_t* out) const = 0;
};

// Rc. rga_interp as for C (preprocess/cpu_common.h). The object is also an InputReader.
std::unique_ptr<Backend> make_rga_copy(int rga_interp);
// The heap Rc's slots come from (M6 §1).
const char* rga_copy_heap();

// By. Built in its own library (core_by), which alone sees libyuv's headers (M6 §4).
std::unique_ptr<Backend> make_cpu_yuv();
// The commit of the libyuv source that library was built from, as CMake read it.
const char* libyuv_commit();

// By's steps, for tools/m6_by_check. Both need the frame at exactly 3 times the band: 1920 x 1080 for
// a 640 x 640 model.
// Luma: w x h -> (w / 3) x (h / 3), rows of w / 3 bytes. libyuv's box on the frame shifted by one pixel,
// and the last row and column as B's resize_luma computes them (AMENDMENTS A4).
void yuv_resize_luma(const uint8_t* y, int y_stride, int w, int h, uint8_t* dst);
// The whole of By's conversion: the NV12 frame into the band of a model_w x model_h x 3 slot, R, G, B.
void letterbox_nv12_yuv(const uint8_t* y, int y_stride, const uint8_t* uv, int uv_stride, int w, int h,
                        uint8_t* slot, int model_w, int model_h);
