// Internal: the model on the three NPU cores and variant C's input slots, opened with an error
// instead of an exit. The detector of the next step is built on this.
#pragma once

#include <memory>
#include <string>

#include "core/npu.h"
#include "preprocess/backend.h"

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

private:
    Engine() = default;

    NpuContexts npu_;
    std::unique_ptr<Backend> backend_;
    int slots_ = 0;
};

}  // namespace rkzc
