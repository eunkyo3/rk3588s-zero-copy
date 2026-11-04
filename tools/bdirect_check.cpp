// Functional check of B-direct before its M1.1 runs (AMENDMENTS A12). On the first frames of a
// video, B and B-direct prepare the same decoded frame, each on its own contexts: B-direct's input
// tensor must equal B's byte for byte, and context 0's bound outputs from the two must be equal. On
// every 100th frame and the last, B-direct's bound outputs must also equal rknn_outputs_get on its
// tensor.
//
// Usage: bdirect_check <video.mp4> <model.rknn> [frames=100]
// Prints the counts. Exit code 0 when every frame was checked and every comparison holds.
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <string>
#include <vector>

#include <opencv2/core.hpp>

#include "core/metrics.h"
#include "core/npu.h"
#include "core/util.h"
#include "core/video_source.h"
#include "preprocess/backend.h"
#include "preprocess/cpu_common.h"

namespace {

struct Variant {
    NpuContexts npu;
    std::unique_ptr<Backend> backend;
    std::vector<uint8_t> tensor;
};

}  // namespace

int main(int argc, char** argv) {
    pin_current_thread({4, 5, 6, 7});
    cv::setNumThreads(1);  // PROTOCOL §8
    if (argc < 3)
        die("usage: bdirect_check <video.mp4> <model.rknn> [frames=100]");
    const std::string video = argv[1], model = argv[2];
    const int max_frames = argc > 3 ? std::atoi(argv[3]) : 100;
    CHECK(max_frames >= 1, "frames must be at least 1");

    Variant b, d;
    b.backend = make_cpu_copy(opencv_interpolation());
    d.backend = make_cpu_direct(opencv_interpolation());
    for (Variant* v : {&b, &d}) {
        v->npu.open(model);
        v->backend->init(v->npu, 1);
        v->tensor.resize(size_t(v->npu.model_w()) * v->npu.model_h() * 3);
    }

    std::atomic<bool> stop{false};
    int frames = 0, checked = 0, tensors_equal = 0, outputs_equal = 0, reference_equal = 0;
    VideoSource src(video, /*software=*/false, /*paced=*/false);
    src.open(&stop);
    src.run(
        [&](AVFrame* f) {
            if (frames < max_frames) {
                std::vector<std::vector<int8_t>> out[2];
                int i = 0;
                for (Variant* v : {&b, &d}) {
                    v->backend->prepare(f, 0, 0);
                    v->backend->read_slot(0, v->tensor.data());
                    v->backend->bind(v->npu, 0, 0);
                    out[i++] = v->npu.run_bound(0);
                }
                tensors_equal += b.tensor == d.tensor;
                outputs_equal += out[0] == out[1];
                if (frames % 100 == 0 || frames == max_frames - 1) {
                    checked++;
                    reference_equal += out[1] == d.npu.reference_outputs(d.tensor, model);
                }
                if (++frames == max_frames)
                    stop = true;
            }
            av_frame_free(&f);
        },
        [] { die("decoder error"); });

    std::printf("B-direct: %s\n", ("{" + d.backend->describe() + "}").c_str());
    std::printf("frames: %d, tensors equal to B's: %d, outputs equal to B's: %d\n", frames, tensors_equal,
                outputs_equal);
    std::printf("bound outputs equal to rknn_outputs_get: %d of %d (every 100th frame and the last)\n",
                reference_equal, checked);
    for (Variant* v : {&b, &d}) {
        v->backend->shutdown(v->npu);
        v->npu.close();
    }
    const bool ok = frames == max_frames && tensors_equal == frames && outputs_equal == frames &&
                    reference_equal == checked;
    std::printf("%s\n", ok ? "pass" : "FAIL");
    return ok ? 0 : 1;
}
