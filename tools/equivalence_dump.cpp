// M1-4 equivalence data (PROTOCOL §11.1, §11.2, AMENDMENTS A5). Runs C, B and R on the same decoded
// frames, each preprocessing on its own contexts, and writes the input tensor and the detections
// of every frame for each. R is B's preprocessing with plain INTER_LINEAR, the reference pair of
// A5. tools/equivalence_check.py decides.
//
// Usage: equivalence_dump <video.mp4> <model.rknn> <out_dir> [frames=1000] [freeze_commit=none]
//   <out_dir>/{c,b,r}.jsonl  a {"header": ...} line (PROTOCOL §1 fields, letterbox band, labels),
//                            then one line per frame: {"frame", "pts", "detections"}, each detection
//                            [class, score, x1, y1, x2, y2] in model input coordinates
//   <out_dir>/{c,b,r}.u8     every frame's whole input tensor, model_h x model_w x 3 uint8, in order
//   <out_dir>/env.json       arguments, library versions, each backend's description, the checks
//
// The pipeline spreads frames over the three NPU cores; this tool runs every frame on core 0. On
// every 100th frame and the last, it checks for each preprocessing that cores 1 and 2 give core 0's
// outputs, and that the bound outputs equal rknn_outputs_get on the same tensor (AMENDMENTS A3).
// Exit code 0 when all frames were written and every check holds.
#include <atomic>
#include <cstdio>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

extern "C" {
#include <libavutil/avutil.h>
}

#include <im2d.hpp>
#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>

#include "core/metrics.h"
#include "core/npu.h"
#include "core/report.h"
#include "core/util.h"
#include "core/video_source.h"
#include "core/yolov8_post.h"
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
    Variant(const char* n, const char* f) : name(n), file(f) {}

    const char* name;  // C, B or R
    const char* file;  // file stem in out_dir
    std::string preprocessing;
    NpuContexts npu;
    std::unique_ptr<Backend> backend;
    FILE* jsonl = nullptr;
    FILE* tensors = nullptr;
    std::vector<uint8_t> tensor;
    bool cores_agree = true;        // cores 1 and 2 give core 0's outputs
    bool matches_reference = true;  // bound outputs equal rknn_outputs_get
};

FILE* open_out(const std::string& path) {
    FILE* f = std::fopen(path.c_str(), "wb");
    CHECK(f, "cannot write %s", path.c_str());
    return f;
}

void write_header(Variant& v, Header header, const Band& b, int mw, int mh) {
    header.emplace_back("variant", v.name);
    std::string line = "{\"header\": {";
    for (const auto& [k, val] : header)
        line += json_string(k) + ": " + json_string(val) + ", ";
    line += "\"preprocessing\": " + json_string(v.preprocessing) + ", \"model_w\": " + std::to_string(mw) +
            ", \"model_h\": " + std::to_string(mh) + ", \"band\": [" + std::to_string(b.x) + ", " +
            std::to_string(b.y) + ", " + std::to_string(b.w) + ", " + std::to_string(b.h) +
            "], \"tensors\": " + json_string(std::string(v.file) + ".u8") + ", \"labels\": [";
    for (int c = 0; c < 80; c++)
        line += std::string(c ? ", " : "") + json_string(coco_label(c));
    line += "]}}\n";
    std::fputs(line.c_str(), v.jsonl);
}

// Scores and coordinates with 9 significant digits, so the float values read back exactly.
void write_frame(FILE* f, int index, int64_t pts, const std::vector<Detection>& dets) {
    std::fprintf(f, "{\"frame\": %d, \"pts\": %lld, \"detections\": [", index, static_cast<long long>(pts));
    for (size_t i = 0; i < dets.size(); i++) {
        const Detection& d = dets[i];
        std::fprintf(f, "%s[%d, %.9g, %.9g, %.9g, %.9g, %.9g]", i ? ", " : "", d.cls, d.score, d.x1, d.y1, d.x2,
                     d.y2);
    }
    std::fputs("]}\n", f);
}

}  // namespace

int main(int argc, char** argv) {
    pin_current_thread({4, 5, 6, 7});
    cv::setNumThreads(1);  // PROTOCOL §8
    if (argc < 4)
        die("usage: equivalence_dump <video.mp4> <model.rknn> <out_dir> [frames=1000] [freeze_commit=none]");
    const std::string video = argv[1], model = argv[2], out = argv[3];
    const int max_frames = argc > 4 ? std::atoi(argv[4]) : 1000;
    const std::string freeze = argc > 5 ? argv[5] : "none";
    CHECK(max_frames >= 1, "frames must be at least 1");
    std::filesystem::create_directories(out);
    std::string args_json = "[";
    for (int i = 1; i < argc; i++)
        args_json += (i > 1 ? ", " : "") + json_string(argv[i]);
    args_json += "]";

    const Header header = {
        {"start_time_utc", utc_now("%Y-%m-%dT%H:%M:%SZ")},
        {"ntp_synchronized", command_output("timedatectl show -p NTPSynchronized --value")},
        {"prereg_v1_commit", kPreregCommit},
        {"freeze_m1_commit", freeze},
        {"run_kind", "equivalence"},
        {"model_sha256", command_output("sha256sum '" + model + "' | cut -d' ' -f1")},
        {"decoder", std::string("h264_rkmpp, ffmpeg ") + av_version_info()},
        {"channels", "1"},
        {"repetition", "1"},
        {"code_commit", CODE_COMMIT},
        {"amendment", LATEST_AMENDMENT},
        {"source", video},
    };

    // Each preprocessing gets its own contexts: C and B both bind every context's input with
    // rknn_set_io_mem, to their own buffers.
    Variant vars[3] = {{"C", "c"}, {"B", "b"}, {"R", "r"}};
    vars[0].backend = make_zero_copy_rga(model, rga_interpolation());
    vars[0].preprocessing = "RGA3, im_opt_t.interp " + std::to_string(rga_interpolation());
    vars[1].backend = make_cpu_copy(opencv_interpolation());
    vars[1].preprocessing = opencv_interpolation_name(opencv_interpolation());
    vars[2].backend = make_cpu_copy(cv::INTER_LINEAR);
    vars[2].preprocessing = opencv_interpolation_name(cv::INTER_LINEAR);
    for (Variant& v : vars) {
        v.npu.open(model);
        v.backend->init(v.npu, 2);  // C checks its output binding with a second slot
        v.tensor.resize(size_t(v.npu.model_w()) * v.npu.model_h() * 3);
        v.jsonl = open_out(out + "/" + v.file + ".jsonl");
        v.tensors = open_out(out + "/" + v.file + ".u8");
    }
    const int mw = vars[0].npu.model_w(), mh = vars[0].npu.model_h();

    std::atomic<bool> stop{false};
    int frames = 0, checked = 0;
    VideoSource src(video, /*software=*/false, /*paced=*/false);
    src.open(&stop);
    src.run(
        [&](AVFrame* f) {
            if (frames < max_frames) {
                const bool check = frames % 100 == 0 || frames == max_frames - 1;
                checked += check;
                for (Variant& v : vars) {
                    if (frames == 0)
                        write_header(v, header, letterbox_band(f->width, f->height, mw, mh), mw, mh);
                    v.backend->prepare(f, 0, 0);
                    v.backend->read_slot(0, v.tensor.data());
                    CHECK(std::fwrite(v.tensor.data(), 1, v.tensor.size(), v.tensors) == v.tensor.size(),
                          "tensor write failed");
                    v.backend->bind(v.npu, 0, 0);
                    CHECK(rknn_run(v.npu.ctx[0], nullptr) == RKNN_SUCC, "rknn_run failed");
                    write_frame(v.jsonl, frames, f->pts, yolov8_post(v.npu.output_ptrs(0), v.npu.outputs, mw, mh));
                    if (check) {
                        const auto core0 = v.npu.run_bound(0);
                        for (int k = 1; k < NpuContexts::kCount; k++) {
                            v.backend->bind(v.npu, k, 0);
                            v.cores_agree &= v.npu.run_bound(k) == core0;
                        }
                        v.matches_reference &= core0 == v.npu.reference_outputs(v.tensor, model);
                    }
                }
                if (++frames == max_frames)
                    stop = true;
            }
            av_frame_free(&f);
        },
        [] { die("decoder error"); });

    for (Variant& v : vars) {
        CHECK(!std::ferror(v.jsonl) && !std::ferror(v.tensors), "writing %s's files failed", v.name);
        CHECK(std::fclose(v.jsonl) == 0, "closing %s.jsonl failed", v.file);
        CHECK(std::fclose(v.tensors) == 0, "closing %s.u8 failed", v.file);
    }
    bool checks_ok = true;
    for (const Variant& v : vars)
        checks_ok &= v.cores_agree && v.matches_reference;
    std::string env = "{\n  \"args\": " + args_json + ",\n  \"frames\": " + std::to_string(frames) +
                      ",\n  \"checked_frames\": " + std::to_string(checked) + ",\n";
    env += "  \"opencv\": {\"version\": \"" CV_VERSION "\", \"threads\": " + std::to_string(cv::getNumThreads()) +
           "},\n";
    env += "  \"librga\": " + json_string(querystring(RGA_VERSION)) + ",\n";
    env += "  \"rknn\": {\"init_flags\": " + std::to_string(NpuContexts::kInitFlags) +
           ", \"sdk\": " + json_string(vars[0].npu.sdk_version) + ", \"npu_core\": 0},\n  \"backends\": {";
    for (int i = 0; i < 3; i++)
        env += std::string(i ? ", " : "") + json_string(vars[i].name) + ": {" + vars[i].backend->describe() + "}";
    env += "},\n  \"checks\": {";
    for (int i = 0; i < 3; i++)
        env += std::string(i ? ", " : "") + "\"outputs_equal_on_cores_0_1_2_" + vars[i].file +
               "\": " + (vars[i].cores_agree ? "true" : "false") + ", \"bound_outputs_match_rknn_outputs_get_" +
               vars[i].file + "\": " + (vars[i].matches_reference ? "true" : "false");
    env += "}\n}\n";
    FILE* f = open_out(out + "/env.json");
    CHECK(std::fputs(env.c_str(), f) >= 0 && std::fclose(f) == 0, "writing env.json failed");

    std::printf("frames: %d, checked: %d (every 100th and the last)\n", frames, checked);
    std::printf("outputs_equal_on_cores_0_1_2: C %d, B %d, R %d\n", vars[0].cores_agree, vars[1].cores_agree,
                vars[2].cores_agree);
    std::printf("bound_outputs_match_rknn_outputs_get: C %d, B %d, R %d\n", vars[0].matches_reference,
                vars[1].matches_reference, vars[2].matches_reference);
    for (Variant& v : vars) {
        v.backend->shutdown(v.npu);
        v.npu.close();
    }
    return frames == max_frames && checks_ok ? 0 : 1;
}
