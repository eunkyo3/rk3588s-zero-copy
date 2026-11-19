// Internal: the model on the three NPU cores and variant C's input slots, opened with an error
// instead of an exit. The detector is built on this.
#pragma once

#include <memory>
#include <string>

#include "core/npu.h"
#include "preprocess/backend.h"
#include "preprocess/fd_cache.h"

namespace rkzc {

class Engine {
public:
    // Null on failure, with the reason in *error.
    static std::unique_ptr<Engine> open(const std::string& model_path, int preprocess_threads, std::string* error);
    // Frees in the order of PROTOCOL §5.4: RKNN memory objects, the buffers, then the contexts.
    ~Engine();
    Engine(const Engine&) = delete;
    Engine& operator=(const Engine&) = delete;

    NpuContexts& npu() { return npu_; }
    Backend& backend() { return *backend_; }
    int slots() const { return slots_; }
    // Releases what the backend cached for the channel's decoder buffers (shim/preprocess/fd_cache.h).
    // Only when none of the channel's frames is in prepare or waiting for it.
    void forget_channel(int channel);

private:
    Engine() = default;

    NpuContexts npu_;
    CacheHooks hooks_;  // filled while backend_ is constructed; its functions point into backend_
    std::unique_ptr<Backend> backend_;
    int slots_ = 0;
};

}  // namespace rkzc
