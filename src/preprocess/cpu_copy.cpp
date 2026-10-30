// Variant B (PROTOCOL §5.2): the CPU maps the decoder's DRM PRIME buffer (cached per buffer),
// brackets the read with exactly one DMA_BUF_IOCTL_SYNC START(READ) and one END(READ) per frame,
// preprocesses with OpenCV into a CPU slot, and bind copies it into the bound input buffer
// (AMENDMENTS A3).
// av_hwframe_map is not used, so there is no second sync.
#include <linux/dma-buf.h>
#include <sys/ioctl.h>
#include <sys/mman.h>

#include <cstring>

#include <drm_fourcc.h>

#include "core/util.h"
#include "preprocess/backend.h"
#include "preprocess/cpu_common.h"
#include "preprocess/fd_cache.h"

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

class CpuCopy : public Backend {
public:
    explicit CpuCopy(int interpolation)
        : interpolation_(interpolation),
          maps_(
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
        letterbox_nv12(m.ptr + layer.planes[0].offset, static_cast<int>(layer.planes[0].pitch),
                       m.ptr + layer.planes[1].offset, static_cast<int>(layer.planes[1].pitch), frame->width,
                       frame->height, slots_.data(slot), model_w_, model_h_, interpolation_);
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
                           "DMA_BUF_IOCTL_SYNC START/END(READ) per frame\", \"opencv_interpolation\": \"") +
               opencv_interpolation_name(interpolation_) +
               "\", \"opencv_color\": \"COLOR_YUV2RGB_NV12 (BT.601 limited)\", \"input_binding\": "
               "\"memcpy into rknn_create_mem bound with rknn_set_io_mem, UINT8 NHWC\", \"decoder_buffers_per_channel_max\": " +
               std::to_string(maps_.max_entries()) + ", \"decoder_buffers_retired\": " +
               std::to_string(maps_.retired());
    }

private:
    const int interpolation_;
    int model_w_ = 0, model_h_ = 0;
    CpuSlots slots_;
    FdCache<Mapping> maps_;
};

}  // namespace

std::unique_ptr<Backend> make_cpu_copy(int interpolation) { return std::make_unique<CpuCopy>(interpolation); }
