// Functional check of B-direct before its M1.1 runs (AMENDMENTS A12). On the first frames of a
// video, B and B-direct prepare the same decoded frame, each on its own contexts, and context 0 runs
// on the bound input. Then B-direct's input tensor must equal B's byte for byte, and the outputs must
// be equal. The slot is read back only after the NPU ran, so that the read's own cache sync cannot
// stand in for prepare's. On every 100th frame and the last, B-direct's bound outputs must also equal
// rknn_outputs_get on its tensor, and contexts 1 and 2 must give context 0's outputs.
//
// Usage: bdirect_check <video.mp4> <model.rknn> <out_dir> [frames=100]
//   <out_dir>/check.csv  "# key: value" header lines (PROTOCOL §1.4, run kind check), then key,value
//                        rows: the counts and passed (1 or 0)
// Prints the counts and B-direct's description. Exit code 0 when passed.
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

extern "C" {
#include <libavutil/avutil.h>
}

#include <opencv2/core.hpp>

#include "core/metrics.h"
#include "core/npu.h"
#include "core/report.h"
#include "core/util.h"
#include "core/video_source.h"
#include "preprocess/backend.h"
#include "preprocess/cpu_common.h"

#ifndef CODE_COMMIT
#define CODE_COMMIT "unknown"
#endif
#ifndef LATEST_AMENDMENT
#define LATEST_AMENDMENT "none"
#endif

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
    if (argc < 4)
        die("usage: bdirect_check <video.mp4> <model.rknn> <out_dir> [frames=100]");
    const std::string video = argv[1], model = argv[2], out = argv[3];
    const int max_frames = argc > 4 ? std::atoi(argv[4]) : 100;
    CHECK(max_frames >= 1, "frames must be at least 1");
    std::filesystem::create_directories(out);
    const Header header = {
        {"start_time_utc", utc_now("%Y-%m-%dT%H:%M:%SZ")},
        {"ntp_synchronized", command_output("timedatectl show -p NTPSynchronized --value")},
        {"prereg_v1_commit", kPreregCommit},
        {"run_kind", "check"},
        {"model_sha256", command_output("sha256sum '" + model + "' | cut -d' ' -f1")},
        {"decoder", std::string("h264_rkmpp, ffmpeg ") + av_version_info()},
        {"code_commit", CODE_COMMIT},
        {"amendment", LATEST_AMENDMENT},
        {"source", video},
    };

    Variant b, d;
    b.backend = make_cpu_copy(opencv_interpolation());
    d.backend = make_cpu_direct(opencv_interpolation());
    for (Variant* v : {&b, &d}) {
        v->npu.open(model);
        v->backend->init(v->npu, 1);
        v->tensor.resize(size_t(v->npu.model_w()) * v->npu.model_h() * 3);
    }

    std::atomic<bool> stop{false};
    int frames = 0, tensors_equal = 0, outputs_equal = 0, checked = 0, reference_equal = 0, cores_agree = 0;
    VideoSource src(video, /*software=*/false, /*paced=*/false);
    src.open(&stop);
    src.run(
        [&](AVFrame* f) {
            if (frames < max_frames) {
                std::vector<std::vector<int8_t>> out[2];
                int i = 0;
                for (Variant* v : {&b, &d}) {
                    v->backend->prepare(f, 0, 0);
                    v->backend->bind(v->npu, 0, 0);
                    out[i++] = v->npu.run_bound(0);
                    v->backend->read_slot(0, v->tensor.data());
                }
                tensors_equal += b.tensor == d.tensor;
                outputs_equal += out[0] == out[1];
                if (frames % 100 == 0 || frames == max_frames - 1) {
                    checked++;
                    reference_equal += out[1] == d.npu.reference_outputs(d.tensor, model);
                    bool agree = true;
                    for (int k = 1; k < NpuContexts::kCount; k++) {
                        d.backend->bind(d.npu, k, 0);
                        agree &= d.npu.run_bound(k) == out[1];
                    }
                    cores_agree += agree;
                }
                if (++frames == max_frames)
                    stop = true;
            }
            av_frame_free(&f);
        },
        [] { die("decoder error"); });

    const bool passed = frames == max_frames && tensors_equal == frames && outputs_equal == frames &&
                        reference_equal == checked && cores_agree == checked;
    const Header rows = {
        {"frames", std::to_string(frames)},
        {"tensors_equal_to_b", std::to_string(tensors_equal)},
        {"outputs_equal_to_b", std::to_string(outputs_equal)},
        {"checked_frames", std::to_string(checked)},
        {"bound_outputs_equal_to_rknn_outputs_get", std::to_string(reference_equal)},
        {"outputs_equal_on_cores_0_1_2", std::to_string(cores_agree)},
        {"passed", passed ? "1" : "0"},
    };
    FILE* csv = std::fopen((out + "/check.csv").c_str(), "w");
    CHECK(csv, "cannot write %s/check.csv", out.c_str());
    for (const auto& [k, v] : header)
        std::fprintf(csv, "# %s: %s\n", k.c_str(), v.c_str());
    std::fputs("key,value\n", csv);
    for (const auto& [k, v] : rows)
        std::fprintf(csv, "%s,%s\n", k.c_str(), v.c_str());
    CHECK(!std::ferror(csv) && std::fclose(csv) == 0, "writing check.csv failed");

    std::printf("B-direct: {%s}\n", d.backend->describe().c_str());
    for (const auto& [k, v] : rows)
        std::printf("%s: %s\n", k.c_str(), v.c_str());
    for (Variant* v : {&b, &d}) {
        v->backend->shutdown(v->npu);
        v->npu.close();
    }
    return passed ? 0 : 1;
}
