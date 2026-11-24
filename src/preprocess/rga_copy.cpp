// Arm Rc of M6 (prereg/M6.md §1): the conversion is C's, the binding is B's.
//   prepare  C's RGA3 call, from the decoder's DRM PRIME buffer into a slot from a CPU-cached heap
//            (system-dma32, the heap of B-direct's slots). The CPU does not read the decoder's buffer.
//   bind     one DMA_BUF_IOCTL_SYNC START(READ) and one END(READ) on the slot, around a memcpy of the
//            slot into the context's input buffer (rknn_create_mem, bound once with rknn_set_io_mem):
//            B's binding (AMENDMENTS A3).
// The RGA3 call is a copy of zero_copy_rga.cpp's and the binding a copy of cpu_common.cpp's, so that
// neither file changes (M6 §8 step 2).
#include <fcntl.h>
#include <linux/dma-buf.h>
#include <linux/dma-heap.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#include <algorithm>
#include <cstring>
#include <vector>

#include <drm_fourcc.h>
#include <im2d.hpp>

#include "core/util.h"
#include "preprocess/fd_cache.h"
#include "preprocess/m6_backends.h"

namespace {

constexpr uint8_t kPad = 114;
constexpr int kRgaCores = IM_SCHEDULER_RGA3_CORE0 | IM_SCHEDULER_RGA3_CORE1;  // AMENDMENTS A1
constexpr size_t kDecoderBuffers = 20 + 10 + 8;  // as in zero_copy_rga.cpp
const char* const kHeap = "system-dma32";        // cached (M6 §1)

int heap_alloc(size_t len) {
    int hfd = open((std::string("/dev/dma_heap/") + kHeap).c_str(), O_RDONLY | O_CLOEXEC);
    if (hfd < 0)
        return -1;
    dma_heap_allocation_data d{};
    d.len = len;
    d.fd_flags = O_RDWR | O_CLOEXEC;
    int r = ioctl(hfd, DMA_HEAP_IOCTL_ALLOC, &d);
    close(hfd);
    return r < 0 ? -1 : static_cast<int>(d.fd);
}

void sync(int fd, uint64_t flags) {
    dma_buf_sync s{flags};
    CHECK(ioctl(fd, DMA_BUF_IOCTL_SYNC, &s) == 0, "DMA_BUF_IOCTL_SYNC failed");
}

class RgaCopy : public Backend, public InputReader {
public:
    explicit RgaCopy(int interp)
        : interp_(interp),
          sources_(
              kDecoderBuffers,
              [](int fd, size_t size) {
                  rga_buffer_handle_t h = importbuffer_fd(fd, static_cast<int>(size));
                  CHECK(h, "importbuffer_fd(frame) failed");
                  return h;
              },
              [](rga_buffer_handle_t& h) { releasebuffer_handle(h); }) {}

    void init(NpuContexts& npu, int n_slots) override {
        model_w_ = npu.model_w();
        model_h_ = npu.model_h();
        size_ = size_t(model_w_) * model_h_ * 3;
        rknn_tensor_attr attr = npu.input;
        attr.type = RKNN_TENSOR_UINT8;
        attr.fmt = RKNN_TENSOR_NHWC;
        attr.pass_through = 0;
        // bind copies the slot as one block, so the input rows must not be padded (as in CpuSlots).
        CHECK(attr.w_stride == 0 || attr.w_stride == uint32_t(model_w_), "input row stride %u, expected %d",
              attr.w_stride, model_w_);
        for (int s = 0; s < n_slots; s++) {
            Slot sl;
            sl.fd = heap_alloc(size_);
            CHECK(sl.fd >= 0, "%s allocation failed", kHeap);
            sl.virt = static_cast<uint8_t*>(mmap(nullptr, size_, PROT_READ | PROT_WRITE, MAP_SHARED, sl.fd, 0));
            CHECK(sl.virt != MAP_FAILED, "mmap of slot failed");
            sync(sl.fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_WRITE);  // the border, once
            std::memset(sl.virt, kPad, size_);
            sync(sl.fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_WRITE);
            sl.rga = importbuffer_fd(sl.fd, static_cast<int>(size_));
            CHECK(sl.rga, "importbuffer_fd(slot) failed");
            slots_.push_back(sl);
        }
        for (int k = 0; k < NpuContexts::kCount; k++) {
            input_[k] = rknn_create_mem(npu.ctx[k], std::max<uint32_t>(attr.size_with_stride, uint32_t(size_)));
            CHECK(input_[k] && rknn_set_io_mem(npu.ctx[k], input_[k], &attr) == RKNN_SUCC, "input binding failed");
        }
    }

    void prepare(const AVFrame* frame, int channel, int slot) override {
        const auto* desc = reinterpret_cast<const AVDRMFrameDescriptor*>(frame->data[0]);
        const AVDRMLayerDescriptor& layer = desc->layers[0];
        CHECK(desc->nb_objects == 1 && desc->nb_layers == 1 && layer.format == DRM_FORMAT_NV12 &&
                  layer.nb_planes == 2 && desc->objects[0].format_modifier == DRM_FORMAT_MOD_LINEAR,
              "unexpected DRM frame layout");
        CHECK(layer.planes[1].offset % layer.planes[0].pitch == 0, "UV offset is not a whole number of rows");
        const int wstride = static_cast<int>(layer.planes[0].pitch);
        const int hstride = static_cast<int>(layer.planes[1].offset / layer.planes[0].pitch);
        const rga_buffer_handle_t handle = sources_.get(frame, channel);

        const float scale = std::min(float(model_w_) / frame->width, float(model_h_) / frame->height);
        const int dw = int(frame->width * scale + 0.5f) & ~1;
        const int dh = int(frame->height * scale + 0.5f) & ~1;
        const im_rect srect{0, 0, frame->width, frame->height};
        const im_rect drect{(model_w_ - dw) / 2, (model_h_ - dh) / 2, dw, dh};
        rga_buffer_t src = wrapbuffer_handle(handle, frame->width, frame->height, RK_FORMAT_YCbCr_420_SP,
                                             wstride, hstride);
        rga_buffer_t dst = wrapbuffer_handle(slots_[slot].rga, model_w_, model_h_, RK_FORMAT_RGB_888);
        imsetColorSpace(&dst, IM_YUV_TO_RGB_BT601_LIMIT);
        rga_buffer_t pat{};
        im_opt_t opt{};
        opt.version = RGA_CURRENT_API_HEADER_VERSION;
        opt.core = kRgaCores;
        opt.interp = interp_;
        IM_STATUS st = improcess(src, dst, pat, srect, drect, {}, -1, nullptr, &opt, IM_SYNC);
        CHECK(st == IM_STATUS_SUCCESS, "improcess failed: %s", imStrError(st));
    }

    void bind(NpuContexts&, int k, int slot) override {
        const Slot& sl = slots_[slot];
        sync(sl.fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ);
        std::memcpy(input_[k]->virt_addr, sl.virt, size_);
        sync(sl.fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ);
    }

    void read_slot(int slot, uint8_t* out) const override {
        const Slot& sl = slots_[slot];
        sync(sl.fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ);
        std::memcpy(out, sl.virt, size_);
        sync(sl.fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ);
    }

    void read_input(int k, uint8_t* out) const override { std::memcpy(out, input_[k]->virt_addr, size_); }

    void shutdown(NpuContexts& npu) override {
        for (int k = 0; k < NpuContexts::kCount; k++) {
            if (input_[k])
                rknn_destroy_mem(npu.ctx[k], input_[k]);
            input_[k] = nullptr;
        }
        sources_.clear();
        for (auto& sl : slots_) {
            releasebuffer_handle(sl.rga);
            munmap(sl.virt, size_);
            close(sl.fd);
        }
        slots_.clear();
    }

    std::string describe() const override {
        return std::string("\"slot_heap\": \"") + kHeap + "\", \"rga_core_mask\": \"0x3\", \"rga_color\": "
               "\"IM_YUV_TO_RGB_BT601_LIMIT\", \"slot_access\": \"DMA_BUF_IOCTL_SYNC START/END(READ) per frame, "
               "in bind\", \"input_binding\": \"memcpy into rknn_create_mem bound with rknn_set_io_mem, UINT8 NHWC\", "
               "\"decoder_buffers_per_channel_max\": " + std::to_string(sources_.max_entries()) +
               ", \"decoder_buffers_retired\": " + std::to_string(sources_.retired());
    }

private:
    struct Slot {
        int fd = -1;
        uint8_t* virt = nullptr;
        rga_buffer_handle_t rga = 0;
    };

    const int interp_;
    int model_w_ = 0, model_h_ = 0;
    size_t size_ = 0;
    std::vector<Slot> slots_;
    rknn_tensor_mem* input_[NpuContexts::kCount] = {};
    FdCache<rga_buffer_handle_t> sources_;
};

}  // namespace

std::unique_ptr<Backend> make_rga_copy(int rga_interp) { return std::make_unique<RgaCopy>(rga_interp); }
const char* rga_copy_heap() { return kHeap; }
