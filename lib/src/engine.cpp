#include "engine.h"

#include <exception>

#include "core/pipeline.h"

namespace rkzc {

std::unique_ptr<Engine> Engine::open(const std::string& model_path, int preprocess_threads, std::string* error) {
    std::unique_ptr<Engine> e(new Engine);
    try {
        e->npu_.open(model_path);
        e->slots_ = input_slots(preprocess_threads);  // N = K + depth(Q2) + 3, as measured
        // 0 is IM_INTERP_DEFAULT, what bench_main passes for variant C (rga_interpolation()).
        CacheHooks::collecting = &e->hooks_;
        try {
            e->backend_ = make_zero_copy_rga(model_path, 0);
        } catch (...) {
            CacheHooks::collecting = nullptr;
            throw;
        }
        CacheHooks::collecting = nullptr;
        e->backend_->init(e->npu_, e->slots_);
    } catch (const std::exception& ex) {
        *error = ex.what();
        return nullptr;  // ~Engine frees what was opened
    }
    return e;
}

Engine::~Engine() {
    if (backend_)
        backend_->shutdown(npu_);
    npu_.close();
}

void Engine::forget_channel(int channel) {
    for (auto& f : hooks_.forget)
        f(channel);
}

size_t Engine::cached_buffers(int channel) {
    size_t n = 0;
    for (auto& f : hooks_.count)
        n += f(channel);
    return n;
}

}  // namespace rkzc
