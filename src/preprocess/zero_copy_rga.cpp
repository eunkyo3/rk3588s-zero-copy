// Variant C (PROTOCOL §5.2): RGA writes the letterboxed RGB image from the DRM PRIME frame into a
// system-uncached-dma32 slot registered with all three contexts through rknn_create_mem_from_fd.
// The CPU never touches pixel data after init (the border memset). Validated in docs/notes/spike.md.
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
#include "preprocess/backend.h"
#include "preprocess/fd_cache.h"

namespace {

constexpr uint8_t kPad = 114;
constexpr int kRgaCores = IM_SCHEDULER_RGA3_CORE0 | IM_SCHEDULER_RGA3_CORE1;  // AMENDMENTS A1
// ffmpeg-rockchip caps a decoder's buffer group at 20 + 10 + extra_hw_frames (docs/notes/spike.md).
constexpr size_t kDecoderBuffers = 20 + 10 + 8;

int heap_alloc(const std::string& heap, size_t len) {
    int hfd = open(("/dev/dma_heap/" + heap).c_str(), O_RDONLY | O_CLOEXEC);
    if (hfd < 0)
        return -1;
    dma_heap_allocation_data d{};
    d.len = len;
    d.fd_flags = O_RDWR | O_CLOEXEC;
    int r = ioctl(hfd, DMA_HEAP_IOCTL_ALLOC, &d);
    close(hfd);
    return r < 0 ? -1 : static_cast<int>(d.fd);
}

void dmabuf_sync(int fd, uint64_t flags) {
    dma_buf_sync s{flags};
    CHECK(ioctl(fd, DMA_BUF_IOCTL_SYNC, &s) == 0, "DMA_BUF_IOCTL_SYNC failed");
}

class ZeroCopyRga : public Backend {
public:
    ZeroCopyRga(std::string model_path, int interp)
        : model_path_(std::move(model_path)),
          interp_(interp),
          sources_(
              kDecoderBuffers,
              [](int fd, size_t size) {
                  rga_buffer_handle_t h = importbuffer_fd(fd, static_cast<int>(size));
                  CHECK(h, "importbuffer_fd(frame) failed");
                  return h;
              },
              [](rga_buffer_handle_t& h) { releasebuffer_handle(h); }) {}

    void init(NpuContexts& npu, int n_slots) override {
        CHECK(n_slots >= 2, "need at least 2 slots");
        model_w_ = npu.model_w();
        model_h_ = npu.model_h();
        in_attr_ = npu.input;
        in_attr_.type = RKNN_TENSOR_UINT8;
        in_attr_.fmt = RKNN_TENSOR_NHWC;
        in_attr_.pass_through = 0;
        const size_t size = std::max<size_t>(in_attr_.size_with_stride, size_t(model_w_) * model_h_ * 3);

        heap_ = "system-uncached-dma32";
        for (int s = 0; s < n_slots; s++) {
            Slot sl;
            sl.fd = heap_alloc(heap_, size);
            if (sl.fd < 0 && s == 0) {
                heap_ = "system-dma32";
                sl.fd = heap_alloc(heap_, size);
            }
            CHECK(sl.fd >= 0, "dma-heap allocation failed");
            sl.size = size;
            sl.virt = mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_SHARED, sl.fd, 0);
            CHECK(sl.virt != MAP_FAILED, "mmap of slot failed");
            fill(sl, kPad);
            sl.rga = importbuffer_fd(sl.fd, static_cast<int>(size));
            CHECK(sl.rga, "importbuffer_fd(slot) failed");
            slots_.push_back(sl);
        }
        for (int k = 0; k < NpuContexts::kCount; k++) {
            for (auto& sl : slots_) {
                auto* m = rknn_create_mem_from_fd(npu.ctx[k], sl.fd, sl.virt, static_cast<uint32_t>(size), 0);
                CHECK(m, "rknn_create_mem_from_fd failed");
                mem_[k].push_back(m);
            }
        }
        check_outputs(npu, size);
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

    void bind(NpuContexts& npu, int k, int slot) override {
        rknn_tensor_attr attr = in_attr_;  // per call: the runtime takes a non-const pointer
        CHECK(rknn_set_io_mem(npu.ctx[k], mem_[k][slot], &attr) == RKNN_SUCC, "rknn_set_io_mem(input) failed");
    }

    void read_slot(int slot, uint8_t* out) const override {
        const Slot& sl = slots_[slot];
        dmabuf_sync(sl.fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ);
        std::memcpy(out, sl.virt, size_t(model_w_) * model_h_ * 3);
        dmabuf_sync(sl.fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ);
    }

    void shutdown(NpuContexts& npu) override {
        for (int k = 0; k < NpuContexts::kCount; k++) {
            for (auto* m : mem_[k])
                rknn_destroy_mem(npu.ctx[k], m);
            mem_[k].clear();
        }
        sources_.clear();
        for (auto& sl : slots_) {
            releasebuffer_handle(sl.rga);
            munmap(sl.virt, sl.size);
            close(sl.fd);
        }
        slots_.clear();
    }

    std::string describe() const override {
        return "\"slot_heap\": \"" + heap_ + "\", \"rga_core_mask\": \"0x3\", \"rga_color\": "
               "\"IM_YUV_TO_RGB_BT601_LIMIT\", \"input_binding\": \"UINT8 NHWC rknn_create_mem_from_fd\", "
               "\"output_coherence_check\": " + std::string(output_check_ ? "true" : "false") +
               ", \"decoder_buffers_per_channel_max\": " + std::to_string(sources_.max_entries()) +
               ", \"decoder_buffers_retired\": " + std::to_string(sources_.retired());
    }

private:
    struct Slot {
        int fd = -1;
        size_t size = 0;
        void* virt = nullptr;
        rga_buffer_handle_t rga = 0;
    };

    static void fill(Slot& sl, uint8_t value) {
        dmabuf_sync(sl.fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_WRITE);
        std::memset(sl.virt, value, sl.size);
        dmabuf_sync(sl.fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_WRITE);
    }

    // The CPU must read the bound outputs coherently (the M1-1 follow-up of docs/notes/spike.md).
    // For every context: run a black slot so the output buffers hold other data, then the grey
    // slot, and compare with rknn_outputs_get on the grey input.
    void check_outputs(NpuContexts& npu, size_t size) {
        const auto reference = npu.reference_outputs(std::vector<uint8_t>(size, kPad), model_path_);
        fill(slots_[1], 0);
        output_check_ = true;
        for (int k = 0; k < NpuContexts::kCount; k++) {
            bind(npu, k, 1);
            const bool primed_differs = npu.run_bound(k) != reference;
            bind(npu, k, 0);
            output_check_ &= primed_differs && npu.run_bound(k) == reference;
        }
        fill(slots_[1], kPad);
        CHECK(output_check_, "bound NPU outputs differ from rknn_outputs_get");
    }

    std::string model_path_;
    const int interp_;
    std::string heap_;
    int model_w_ = 0, model_h_ = 0;
    rknn_tensor_attr in_attr_{};
    std::vector<Slot> slots_;
    std::vector<rknn_tensor_mem*> mem_[NpuContexts::kCount];
    FdCache<rga_buffer_handle_t> sources_;
    bool output_check_ = false;
};

}  // namespace

std::unique_ptr<Backend> make_zero_copy_rga(const std::string& model_path, int rga_interp) {
    return std::make_unique<ZeroCopyRga>(model_path, rga_interp);
}
