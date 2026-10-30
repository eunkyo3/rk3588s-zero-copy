// Variant A (PROTOCOL §5.2, reference only): libavcodec's software H.264 decoder gives I420 frames
// in CPU memory; B's resize, color and letterbox code and B's bind follow.
#include <cstring>

#include "core/util.h"
#include "preprocess/backend.h"
#include "preprocess/cpu_common.h"

namespace {

class CpuSoftware : public Backend {
public:
    void init(NpuContexts& npu, int n_slots) override {
        model_w_ = npu.model_w();
        model_h_ = npu.model_h();
        slots_.init(npu, n_slots);
    }

    void prepare(const AVFrame* frame, int, int slot) override {
        CHECK(frame->format == AV_PIX_FMT_YUV420P, "software decoder gave pixel format %d, expected yuv420p",
              frame->format);
        letterbox_i420(frame->data[0], frame->linesize[0], frame->data[1], frame->linesize[1], frame->data[2],
                       frame->linesize[2], frame->width, frame->height, slots_.data(slot), model_w_, model_h_,
                       opencv_interpolation());
    }

    void bind(NpuContexts& npu, int k, int slot) override { slots_.bind(npu, k, slot); }

    void read_slot(int slot, uint8_t* out) const override {
        std::memcpy(out, slots_.data(slot), slots_.size());
    }

    void shutdown(NpuContexts& npu) override { slots_.release(npu); }

    std::string describe() const override {
        return std::string("\"slot_memory\": \"CPU (std::vector)\", \"decoder\": \"libavcodec h264 (software)\", "
                           "\"opencv_interpolation\": \"") +
               opencv_interpolation_name() +
               "\", \"opencv_color\": \"COLOR_YUV2RGB_I420 (BT.601 limited)\", \"input_binding\": "
               "\"memcpy into rknn_create_mem bound with rknn_set_io_mem, UINT8 NHWC\"";
    }

private:
    int model_w_ = 0, model_h_ = 0;
    CpuSlots slots_;
};

}  // namespace

std::unique_ptr<Backend> make_cpu_software() { return std::make_unique<CpuSoftware>(); }
