// B-direct, an optional M1.1 design (PROTOCOL §13, AMENDMENTS A12): B's decoder-buffer access and
// OpenCV preprocessing, unchanged, but the CPU writes the letterboxed image straight into a cached
// dma32 slot that every context registered with rknn_create_mem_from_fd. Each prepare brackets its
// writes with one DMA_BUF_IOCTL_SYNC START(WRITE) and one END(WRITE) on the slot, and bind is
// rknn_set_io_mem, as in C, so bind copies nothing. B's and C's own files are not changed.
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

#include "core/util.h"
#include "preprocess/backend.h"
#include "preprocess/cpu_common.h"
#include "preprocess/fd_cache.h"

namespace {

constexpr uint8_t kPad = 114;
constexpr size_t kDecoderBuffers = 20 + 10 + 8;  // as in zero_copy_rga.cpp
const char* const kHeap = "system-dma32";        // cached (PROTOCOL §13)

struct Mapping {
    const uint8_t* ptr = nullptr;
    size_t size = 0;
};

void sync(int fd, uint64_t flags) {
    dma_buf_sync s{flags};
    CHECK(ioctl(fd, DMA_BUF_IOCTL_SYNC, &s) == 0, "DMA_BUF_IOCTL_SYNC failed");
}

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

class CpuDirect : public Backend {
public:
    explicit CpuDirect(int interpolation)
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
        in_attr_ = npu.input;
        in_attr_.type = RKNN_TENSOR_UINT8;
        in_attr_.fmt = RKNN_TENSOR_NHWC;
        in_attr_.pass_through = 0;
        // letterbox_nv12 writes unpadded rows, as into B's CPU slots.
        CHECK(in_attr_.w_stride == 0 || in_attr_.w_stride == uint32_t(model_w_), "input row stride %u, expected %d",
              in_attr_.w_stride, model_w_);
        const size_t size = std::max<size_t>(in_attr_.size_with_stride, size_t(model_w_) * model_h_ * 3);
        for (int s = 0; s < n_slots; s++) {
            Slot sl;
            sl.fd = heap_alloc(size);
            CHECK(sl.fd >= 0, "%s allocation failed", kHeap);
            sl.size = size;
            sl.virt = static_cast<uint8_t*>(mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_SHARED, sl.fd, 0));
            CHECK(sl.virt != MAP_FAILED, "mmap of slot failed");
            sync(sl.fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_WRITE);
            std::memset(sl.virt, kPad, size);
            sync(sl.fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_WRITE);
            slots_.push_back(sl);
        }
        for (int k = 0; k < NpuContexts::kCount; k++) {
            for (auto& sl : slots_) {
                auto* m = rknn_create_mem_from_fd(npu.ctx[k], sl.fd, sl.virt, static_cast<uint32_t>(size), 0);
                CHECK(m, "rknn_create_mem_from_fd failed");
                mem_[k].push_back(m);
            }
        }
    }

    void prepare(const AVFrame* frame, int channel, int slot) override {
        const auto* desc = reinterpret_cast<const AVDRMFrameDescriptor*>(frame->data[0]);
        const AVDRMLayerDescriptor& layer = desc->layers[0];
        CHECK(desc->nb_objects == 1 && desc->nb_layers == 1 && layer.format == DRM_FORMAT_NV12 &&
                  layer.nb_planes == 2 && desc->objects[0].format_modifier == DRM_FORMAT_MOD_LINEAR,
              "unexpected DRM frame layout");
        const Mapping m = maps_.get(frame, channel);
        const int fd = desc->objects[0].fd;
        const Slot& sl = slots_[slot];
        sync(fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ);
        sync(sl.fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_WRITE);
        letterbox_nv12(m.ptr + layer.planes[0].offset, static_cast<int>(layer.planes[0].pitch),
                       m.ptr + layer.planes[1].offset, static_cast<int>(layer.planes[1].pitch), frame->width,
                       frame->height, sl.virt, model_w_, model_h_, interpolation_);
        sync(sl.fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_WRITE);
        sync(fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ);
    }

    void bind(NpuContexts& npu, int k, int slot) override {
        rknn_tensor_attr attr = in_attr_;  // per call: the runtime takes a non-const pointer
        CHECK(rknn_set_io_mem(npu.ctx[k], mem_[k][slot], &attr) == RKNN_SUCC, "rknn_set_io_mem(input) failed");
    }

    void read_slot(int slot, uint8_t* out) const override {
        const Slot& sl = slots_[slot];
        sync(sl.fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ);
        std::memcpy(out, sl.virt, size_t(model_w_) * model_h_ * 3);
        sync(sl.fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ);
    }

    void shutdown(NpuContexts& npu) override {
        for (int k = 0; k < NpuContexts::kCount; k++) {
            for (auto* m : mem_[k])
                rknn_destroy_mem(npu.ctx[k], m);
            mem_[k].clear();
        }
        maps_.clear();
        for (auto& sl : slots_) {
            munmap(sl.virt, sl.size);
            close(sl.fd);
        }
        slots_.clear();
    }

    std::string describe() const override {
        return std::string("\"slot_heap\": \"") + kHeap + "\", \"decoder_buffer_access\": \"mmap per buffer, "
               "DMA_BUF_IOCTL_SYNC START/END(READ) per frame\", \"slot_access\": \"DMA_BUF_IOCTL_SYNC "
               "START/END(WRITE) per frame\", \"opencv_interpolation\": \"" +
               opencv_interpolation_name(interpolation_) +
               "\", \"opencv_color\": \"COLOR_YUV2RGB_NV12 (BT.601 limited)\", \"input_binding\": "
               "\"UINT8 NHWC rknn_create_mem_from_fd\", \"decoder_buffers_per_channel_max\": " +
               std::to_string(maps_.max_entries()) + ", \"decoder_buffers_retired\": " +
               std::to_string(maps_.retired());
    }

private:
    struct Slot {
        int fd = -1;
        size_t size = 0;
        uint8_t* virt = nullptr;
    };

    const int interpolation_;
    int model_w_ = 0, model_h_ = 0;
    rknn_tensor_attr in_attr_{};
    std::vector<Slot> slots_;
    std::vector<rknn_tensor_mem*> mem_[NpuContexts::kCount];
    FdCache<Mapping> maps_;
};

}  // namespace

std::unique_ptr<Backend> make_cpu_direct(int interpolation) { return std::make_unique<CpuDirect>(interpolation); }
