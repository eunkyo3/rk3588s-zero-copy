// The variant-specific part of the pipeline (PROTOCOL §5.2): the memory behind the input slots,
// prepare (decoded frame -> letterboxed model input in a slot) and bind (slot -> NPU context).
// Everything else is shared.
#pragma once

#include <memory>
#include <string>

extern "C" {
#include <libavutil/frame.h>
}

#include "core/npu.h"

class Backend {
public:
    virtual ~Backend() = default;

    // Allocates n_slots input slots with the letterbox border already filled (value 114).
    virtual void init(NpuContexts& npu, int n_slots) = 0;
    // Writes the 640x360 image band of `frame` (from `channel`) into `slot`. Called from the
    // preprocessing threads; the caller releases the frame afterwards.
    virtual void prepare(const AVFrame* frame, int channel, int slot) = 0;
    // Makes `slot` the input of context k. Called only from context k's NPU worker.
    virtual void bind(NpuContexts& npu, int k, int slot) = 0;
    // Copies the whole slot (model_w x model_h x 3) into `out`. For tools only: the pipeline never
    // reads slot pixels with the CPU.
    virtual void read_slot(int slot, uint8_t* out) const = 0;
    // Frees the slots: RKNN memory objects first, then the buffers (PROTOCOL §5.4).
    virtual void shutdown(NpuContexts& npu) = 0;
    // "key": value pairs for env.json (heap, RGA core mask, ...), without braces.
    virtual std::string describe() const = 0;
};

std::unique_ptr<Backend> make_zero_copy_rga(const std::string& model_path, int rga_interp);  // C
std::unique_ptr<Backend> make_cpu_copy(int interpolation);                    // B
std::unique_ptr<Backend> make_cpu_software();                                 // A
