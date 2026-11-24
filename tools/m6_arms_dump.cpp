// M6's checks of the arms' data (prereg/M6.md §8 step 3). On the first frames of a video, each arm
// prepares the same decoded frame on its own contexts, binds it and runs context 0. Per arm and frame
// it writes the input tensor, the NPU's outputs and the detections:
//   - "Unchanged arms": this file is also compiled, unchanged, against freeze-m3's code (with
//     M6_OLD_ARMS_ONLY, which leaves Rc out), and tools/m6_checks.py compares the two builds' files for
//     B, Bd, C and Cp byte for byte.
//   - Rc: its tensor here is the input buffer the NPU read, taken after bind's memcpy and after the NPU
//     ran, and must equal C's slot byte for byte, with equal outputs.
// The tensor of B, Bd, C and Cp is the slot, read after the NPU ran, so that the read's own cache sync
// cannot stand in for prepare's (as tools/bdirect_check reads Bd's). Cp is C's backend with arm Cp's
// post-processing (M2 §1); the other arms use M1's.
//
// Usage: m6_arms_dump <video.mp4> <model.rknn> <out_dir> [frames=100]
//   <out_dir>/<arm>.u8     every frame's input tensor, model_h x model_w x 3 uint8, in order
//   <out_dir>/<arm>.i8     every frame's outputs as bound (NCHW INT8), one after the other, in order
//   <out_dir>/<arm>.detections  one line per frame: {"frame", "pts", "detections"}, each detection
//                          [class, score, x1, y1, x2, y2] with 9 significant digits. No header line of
//                          its own: the record's header is dump.csv's (PROTOCOL §1.4)
//   <out_dir>/dump.csv     "# key: value" header lines (PROTOCOL §1.4, run kind check), then key,value:
//                          the arms, the frames, and the SHA-256 of every file above
// The arms are b, bd, c, cp and, in M6's build, rc. Exit code 0 when every frame was written.
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <exception>
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
#include "core/pipeline_post.h"
#include "core/report.h"
#include "core/util.h"
#include "core/video_source.h"
#include "preprocess/backend.h"
#include "preprocess/cpu_common.h"
#ifndef M6_OLD_ARMS_ONLY
#include "preprocess/m6_backends.h"
#endif

#ifndef CODE_COMMIT
#define CODE_COMMIT "unknown"
#endif
#ifndef LATEST_AMENDMENT
#define LATEST_AMENDMENT "none"
#endif

namespace {

struct Arm {
    Arm(const char* n, PostKind p) : name(n), post_kind(p) {}

    const char* name;  // file stem
    PostKind post_kind;
    bool input_buffer = false;  // Rc: the tensor is the bound input buffer, not the slot
    NpuContexts npu;
    std::unique_ptr<Backend> backend;
    std::unique_ptr<PipelinePost> post;
    FILE* tensors = nullptr;
    FILE* outputs = nullptr;
    FILE* jsonl = nullptr;
    std::vector<uint8_t> tensor;
};

FILE* open_out(const std::string& path) {
    FILE* f = std::fopen(path.c_str(), "wb");
    CHECK(f, "cannot write %s", path.c_str());
    return f;
}

}  // namespace

int main(int argc, char** argv) {
    pin_current_thread({4, 5, 6, 7});
    cv::setNumThreads(1);  // PROTOCOL §8
    if (argc < 4)
        die("usage: m6_arms_dump <video.mp4> <model.rknn> <out_dir> [frames=100]");
    const std::string video = argv[1], model = argv[2], out = argv[3];
    const int max_frames = argc > 4 ? std::atoi(argv[4]) : 100;
    CHECK(max_frames >= 1, "frames must be at least 1");
    std::filesystem::create_directories(out);
    const Header header = {
        {"start_time_utc", utc_now("%Y-%m-%dT%H:%M:%SZ")},
        {"ntp_synchronized", command_output("timedatectl show -p NTPSynchronized --value")},
        {"prereg_v1_commit", kPreregCommit},
#ifndef M6_OLD_ARMS_ONLY
        {"prereg_m6_commit", kPreregM6Commit},
#endif
        {"run_kind", "check"},
        {"model_sha256", command_output("sha256sum '" + model + "' | cut -d' ' -f1")},
        {"decoder", std::string("h264_rkmpp, ffmpeg ") + av_version_info()},
        {"code_commit", CODE_COMMIT},
        {"amendment", LATEST_AMENDMENT},
        {"source", video},
        {"source_sha256", command_output("sha256sum '" + video + "' | cut -d' ' -f1")},
    };

    std::vector<std::unique_ptr<Arm>> arms;
    arms.push_back(std::make_unique<Arm>("b", PostKind::M1));
    arms.back()->backend = make_cpu_copy(opencv_interpolation());
    arms.push_back(std::make_unique<Arm>("bd", PostKind::M1));
    arms.back()->backend = make_cpu_direct(opencv_interpolation());
    arms.push_back(std::make_unique<Arm>("c", PostKind::M1));
    arms.back()->backend = make_zero_copy_rga(model, rga_interpolation());
    arms.push_back(std::make_unique<Arm>("cp", PostKind::Neon));
    arms.back()->backend = make_zero_copy_rga(model, rga_interpolation());
#ifndef M6_OLD_ARMS_ONLY
    arms.push_back(std::make_unique<Arm>("rc", PostKind::M1));
    arms.back()->backend = make_rga_copy(rga_interpolation());
    arms.back()->input_buffer = true;
#endif
    for (auto& a : arms) {
        a->npu.open(model);
        a->backend->init(a->npu, 2);  // C checks its output binding with a second slot
        try {
            a->post = std::make_unique<PipelinePost>(a->post_kind, a->npu.outputs);
        } catch (const std::exception& e) {
            die("%s", e.what());
        }
        a->tensor.resize(size_t(a->npu.model_w()) * a->npu.model_h() * 3);
        a->tensors = open_out(out + "/" + a->name + ".u8");
        a->outputs = open_out(out + "/" + a->name + ".i8");
        a->jsonl = open_out(out + "/" + a->name + ".detections");
    }

    std::atomic<bool> stop{false};
    int frames = 0;
    VideoSource src(video, /*software=*/false, /*paced=*/false);
    src.open(&stop);
    src.run(
        [&](AVFrame* f) {
            if (frames < max_frames) {
                for (auto& a : arms) {
                    a->backend->prepare(f, 0, 0);
                    a->backend->bind(a->npu, 0, 0);
                    const auto outs = a->npu.run_bound(0);
#ifndef M6_OLD_ARMS_ONLY
                    if (a->input_buffer) {
                        auto* reader = dynamic_cast<InputReader*>(a->backend.get());
                        CHECK(reader, "%s has no input buffer to read", a->name);
                        reader->read_input(0, a->tensor.data());
                    } else
#endif
                        a->backend->read_slot(0, a->tensor.data());
                    CHECK(std::fwrite(a->tensor.data(), 1, a->tensor.size(), a->tensors) == a->tensor.size(),
                          "tensor write failed");
                    for (const auto& o : outs)
                        CHECK(std::fwrite(o.data(), 1, o.size(), a->outputs) == o.size(), "output write failed");
                    const auto dets = a->post->run(a->npu.output_ptrs(0), a->npu.outputs, a->npu.model_w(),
                                                   a->npu.model_h());
                    std::fprintf(a->jsonl, "{\"frame\": %d, \"pts\": %lld, \"detections\": [", frames,
                                 static_cast<long long>(f->pts));
                    for (size_t i = 0; i < dets.size(); i++)
                        std::fprintf(a->jsonl, "%s[%d, %.9g, %.9g, %.9g, %.9g, %.9g]", i ? ", " : "", dets[i].cls,
                                     dets[i].score, dets[i].x1, dets[i].y1, dets[i].x2, dets[i].y2);
                    std::fputs("]}\n", a->jsonl);
                }
                if (++frames == max_frames)
                    stop = true;
            }
            av_frame_free(&f);
        },
        [] { die("decoder error"); });

    Header rows = {{"frames", std::to_string(frames)}};
    std::string names;
    for (auto& a : arms) {
        for (FILE* f : {a->tensors, a->outputs, a->jsonl})
            CHECK(!std::ferror(f) && std::fclose(f) == 0, "writing %s's files failed", a->name);
        names += std::string(names.empty() ? "" : " ") + a->name;
        for (const char* ext : {".u8", ".i8", ".detections"})
            rows.push_back({std::string("sha256_") + a->name + ext, file_sha256(out + "/" + a->name + ext)});
    }
    rows.insert(rows.begin(), {"arms", names});
    FILE* csv = open_out(out + "/dump.csv");
    for (const auto& [k, v] : header)
        std::fprintf(csv, "# %s: %s\n", k.c_str(), v.c_str());
    std::fputs("key,value\n", csv);
    for (const auto& [k, v] : rows)
        std::fprintf(csv, "%s,%s\n", k.c_str(), v.c_str());
    CHECK(!std::ferror(csv) && std::fclose(csv) == 0, "writing dump.csv failed");
    for (const auto& [k, v] : rows)
        std::printf("%s: %s\n", k.c_str(), v.c_str());
    for (auto& a : arms) {
        a->backend->shutdown(a->npu);
        a->npu.close();
    }
    return frames == max_frames ? 0 : 1;
}
