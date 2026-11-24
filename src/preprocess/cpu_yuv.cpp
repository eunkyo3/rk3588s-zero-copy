// Arm By of M6 (prereg/M6.md §1): B with its resize and colour conversion done by libyuv in place of
// OpenCV. What B does around them stays: the decoder's buffer mapped once per buffer, one
// DMA_BUF_IOCTL_SYNC START(READ) and one END(READ) per frame, slots in CPU memory with the border
// filled once, and bind by memcpy into the bound input buffer (CpuSlots, AMENDMENTS A3).
//
// One configuration (M6 §1), for a frame of exactly 3 times the band (1920 x 1080 -> 640 x 360):
//   luma    ScalePlane, kFilterBox, on the frame shifted by one pixel as B's resize_luma shifts it
//           (AMENDMENTS A4): the region from column 1 and row 1, 1917 x 1077, to 639 x 359. The last row
//           and column are computed as resize_luma computes them, by a copy of its code.
//   chroma  SplitUVPlane, then ScalePlane with kFilterBox on U and on V, 960 x 540 -> 320 x 180, no shift.
//   colour  I420ToRAW into the band: BT.601 limited range, bytes R, G, B.
// libyuv starts no thread. This file is compiled into its own library, core_by, which alone has
// libyuv's include path (M6 §4). The access to the decoder's buffer is a copy of cpu_copy.cpp's.
#include <linux/dma-buf.h>
#include <sys/ioctl.h>
#include <sys/mman.h>

#include <algorithm>
#include <cstring>
#include <vector>

#include <drm_fourcc.h>
#include <libyuv/convert_argb.h>
#include <libyuv/planar_functions.h>
#include <libyuv/scale.h>

#include "core/util.h"
#include "preprocess/cpu_common.h"
#include "preprocess/fd_cache.h"
#include "preprocess/m6_backends.h"

#ifndef LIBYUV_COMMIT
#error "LIBYUV_COMMIT is not defined: CMakeLists.txt sets it for core_by"
#endif

namespace {

constexpr size_t kDecoderBuffers = 20 + 10 + 8;  // as in zero_copy_rga.cpp

struct Mapping {
    const uint8_t* ptr = nullptr;
    size_t size = 0;
};

void sync(int fd, uint64_t flags) {
    dma_buf_sync s{flags};
    CHECK(ioctl(fd, DMA_BUF_IOCTL_SYNC, &s) == 0, "DMA_BUF_IOCTL_SYNC failed");
}

class CpuYuv : public Backend {
public:
    CpuYuv()
        : maps_(
              kDecoderBuffers,
              [](int fd, size_t size) {
                  void* p = mmap(nullptr, size, PROT_READ, MAP_SHARED, fd, 0);
                  CHECK(p != MAP_FAILED, "mmap of a decoder buffer failed");
                  return Mapping{static_cast<const uint8_t*>(p), size};
              },
              [](Mapping& m) { munmap(const_cast<uint8_t*>(m.ptr), m.size); }) {}

    void init(NpuContexts& npu, int n_slots) override {
        model_w_ = npu.model_w();
        model_h_ = npu.model_h();
        slots_.init(npu, n_slots);
    }

    void prepare(const AVFrame* frame, int channel, int slot) override {
        const auto* desc = reinterpret_cast<const AVDRMFrameDescriptor*>(frame->data[0]);
        const AVDRMLayerDescriptor& layer = desc->layers[0];
        CHECK(desc->nb_objects == 1 && desc->nb_layers == 1 && layer.format == DRM_FORMAT_NV12 &&
                  layer.nb_planes == 2 && desc->objects[0].format_modifier == DRM_FORMAT_MOD_LINEAR,
              "unexpected DRM frame layout");
        const Mapping m = maps_.get(frame, channel);
        const int fd = desc->objects[0].fd;
        sync(fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ);
        letterbox_nv12_yuv(m.ptr + layer.planes[0].offset, static_cast<int>(layer.planes[0].pitch),
                           m.ptr + layer.planes[1].offset, static_cast<int>(layer.planes[1].pitch), frame->width,
                           frame->height, slots_.data(slot), model_w_, model_h_);
        sync(fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ);
    }

    void bind(NpuContexts& npu, int k, int slot) override { slots_.bind(npu, k, slot); }

    void read_slot(int slot, uint8_t* out) const override {
        std::memcpy(out, slots_.data(slot), slots_.size());
    }

    void shutdown(NpuContexts& npu) override {
        maps_.clear();
        slots_.release(npu);
    }

    std::string describe() const override {
        return std::string("\"slot_memory\": \"CPU (std::vector)\", \"decoder_buffer_access\": \"mmap per buffer, "
                           "DMA_BUF_IOCTL_SYNC START/END(READ) per frame\", \"libyuv_commit\": \"") +
               LIBYUV_COMMIT +
               "\", \"libyuv_scale\": \"ScalePlane kFilterBox: luma from (1, 1), last row and column as B's "
               "resize_luma; SplitUVPlane, then U and V\", \"libyuv_color\": \"I420ToRAW (BT.601 limited)\", "
               "\"input_binding\": \"memcpy into rknn_create_mem bound with rknn_set_io_mem, UINT8 NHWC\", "
               "\"decoder_buffers_per_channel_max\": " +
               std::to_string(maps_.max_entries()) + ", \"decoder_buffers_retired\": " +
               std::to_string(maps_.retired());
    }

private:
    int model_w_ = 0, model_h_ = 0;
    CpuSlots slots_;
    FdCache<Mapping> maps_;
};

}  // namespace

void yuv_resize_luma(const uint8_t* y, int y_stride, int w, int h, uint8_t* dst) {
    CHECK(w % 3 == 0 && h % 3 == 0 && w >= 6 && h >= 6, "By's luma is for a frame of 3 times the band");
    const int W = w / 3, H = h / 3;
    CHECK(libyuv::ScalePlane(y + y_stride + 1, y_stride, 3 * (W - 1), 3 * (H - 1), dst, W, W - 1, H - 1,
                             libyuv::kFilterBox) == 0,
          "ScalePlane(luma) failed");
    // The last row and column, whose box reaches past the frame: the copy of resize_luma's code
    // (cpu_common.cpp). Input columns 3c+1..3c+3 and rows 3r+1..3r+3, the frame's last row and column
    // repeated, rounded as (sum + 4) / 9.
    auto box = [&](int c, int r) {
        int sum = 0;
        for (int dy = 1; dy <= 3; dy++)
            for (int dx = 1; dx <= 3; dx++)
                sum += y[size_t(std::min(3 * r + dy, h - 1)) * y_stride + std::min(3 * c + dx, w - 1)];
        return static_cast<uint8_t>((sum + 4) / 9);
    };
    for (int r = 0; r < H; r++)
        dst[size_t(r) * W + W - 1] = box(W - 1, r);
    for (int c = 0; c < W - 1; c++)
        dst[size_t(H - 1) * W + c] = box(c, H - 1);
}

void letterbox_nv12_yuv(const uint8_t* y, int y_stride, const uint8_t* uv, int uv_stride, int w, int h,
                        uint8_t* slot, int model_w, int model_h) {
    const Band b = letterbox_band(w, h, model_w, model_h);
    CHECK(w == 3 * b.w && h == 3 * b.h && b.w % 2 == 0 && b.h % 2 == 0,
          "By is registered for a frame of exactly 3 times the band (M6 §1): %d x %d into %d x %d", w, h, b.w, b.h);
    const int cw = w / 2, ch = h / 2, sw = b.w / 2, sh = b.h / 2;
    thread_local std::vector<uint8_t> small_y, u, v, small_u, small_v;
    small_y.resize(size_t(b.w) * b.h);
    u.resize(size_t(cw) * ch);
    v.resize(size_t(cw) * ch);
    small_u.resize(size_t(sw) * sh);
    small_v.resize(size_t(sw) * sh);

    yuv_resize_luma(y, y_stride, w, h, small_y.data());
    libyuv::SplitUVPlane(uv, uv_stride, u.data(), cw, v.data(), cw, cw, ch);
    CHECK(libyuv::ScalePlane(u.data(), cw, cw, ch, small_u.data(), sw, sw, sh, libyuv::kFilterBox) == 0 &&
              libyuv::ScalePlane(v.data(), cw, cw, ch, small_v.data(), sw, sw, sh, libyuv::kFilterBox) == 0,
          "ScalePlane(chroma) failed");
    uint8_t* band = slot + (size_t(b.y) * model_w + b.x) * 3;
    CHECK(libyuv::I420ToRAW(small_y.data(), b.w, small_u.data(), sw, small_v.data(), sw, band, model_w * 3, b.w,
                            b.h) == 0,
          "I420ToRAW failed");
}

std::unique_ptr<Backend> make_cpu_yuv() { return std::make_unique<CpuYuv>(); }
const char* libyuv_commit() { return LIBYUV_COMMIT; }
