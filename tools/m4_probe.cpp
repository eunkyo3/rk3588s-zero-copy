// Exploration before M4's plan, not a run that counts: the YOLOv10n model's INT8 outputs on still
// images, and yolov10_post on them.
//
// The inputs are 640x640 letterboxed PNGs made by tools/m4_explore.py (reference), the same pixels its
// ONNX FP32 reference ran on. Each goes into context 0 (NPU core 0, M1's bindings) through a CPU slot,
// as B binds it (CpuSlots). For each image:
//   - The bound outputs must equal rknn_outputs_get of a separate context on the same input.
//   - The bound outputs are written as raw int8 (NCHW) for the comparison with ONNX, and
//     yolov10_post's detections at conf 0.25 (the pipeline's) and 0.001 (mAP's).
//   - The context runs `runs` times per conf; after each run yolov10_post is timed (thread CPU per
//     call) on the outputs the runtime has just written, as post_probe timed yolov8_post.
//
// Usage: m4_probe <model.rknn> <out_dir> [runs=5] <input.png>...   (as root, clocks fixed)
//   <out_dir>/m4_probe.csv        "# key: value" header lines (PROTOCOL §1.4, run kind check), then
//                                 key,value rows: the outputs' attributes (scales as %.9g), checks
//                                 and times
//   <out_dir>/detections.csv      image,conf,rank,cls,score,x1,y1,x2,y2 (model input coordinates)
//   <out_dir>/<image>_out<k>.i8   output k of the bound context (not committed)
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <string>
#include <vector>

#include <opencv2/core.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>

#include "core/metrics.h"
#include "core/npu.h"
#include "core/report.h"
#include "core/util.h"
#include "core/yolov10_post.h"
#include "preprocess/cpu_common.h"

namespace {

double thread_cpu_ms() {
    timespec ts{};
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts);
    return ts.tv_sec * 1e3 + ts.tv_nsec / 1e6;
}

double nearest_rank(std::vector<double> v, double p) {
    std::sort(v.begin(), v.end());
    return v[std::min(v.size() - 1, static_cast<size_t>(p * (v.size() - 1) + 0.5))];
}

std::string fmt(const char* f, double x) {
    char buf[64];
    std::snprintf(buf, sizeof buf, f, x);
    return buf;
}

}  // namespace

int main(int argc, char** argv) {
    pin_current_thread({4, 5, 6, 7});
    cv::setNumThreads(1);  // PROTOCOL §8
    if (argc < 4)
        die("usage: m4_probe <model.rknn> <out_dir> [runs=5] <input.png>...");
    const std::string model = argv[1], out_dir = argv[2];
    int first = 3, runs = 5;
    if (std::filesystem::path(argv[3]).extension() != ".png") {
        runs = std::atoi(argv[3]);
        first = 4;
    }
    CHECK(runs >= 1 && first < argc, "runs must be at least 1 and an input is needed");
    std::filesystem::create_directories(out_dir);
    const Header header = {
        {"start_time_utc", utc_now("%Y-%m-%dT%H:%M:%SZ")},
        {"ntp_synchronized", command_output("timedatectl show -p NTPSynchronized --value")},
        {"prereg_v1_commit", kPreregCommit},
        {"run_kind", "check"},
        {"model_sha256", file_sha256(model)},
        {"code_commit", CODE_COMMIT},
        {"amendment", LATEST_AMENDMENT},
        {"inputs", std::to_string(argc - first) + " PNGs from tools/m4_explore.py reference"},
        {"purpose", "exploration before M4's plan; not a run that counts"},
    };

    NpuContexts npu;
    npu.open(model);
    CHECK(npu.outputs.size() == 6, "expected the YOLOv10n export's 6 outputs, got %zu", npu.outputs.size());
    const int mw = npu.model_w(), mh = npu.model_h();
    CpuSlots slot;
    slot.init(npu, 1);

    Header rows = {{"sdk_version", npu.sdk_version}, {"outputs", "6"}};
    for (size_t k = 0; k < npu.outputs.size(); k++) {
        const auto& a = npu.outputs[k];
        std::string dims;
        for (uint32_t d = 0; d < a.n_dims; d++)
            dims += (d ? "x" : "") + std::to_string(a.dims[d]);
        const std::string p = "output" + std::to_string(k) + "_";
        rows.push_back({p + "name", a.name});
        rows.push_back({p + "dims", dims});
        rows.push_back({p + "zp", std::to_string(a.zp)});
        rows.push_back({p + "scale", fmt("%.9g", a.scale)});
        rows.push_back({p + "max_value", fmt("%.9g", (127.0 - a.zp) * a.scale)});
    }

    const std::string det_path = out_dir + "/detections.csv";
    FILE* det = std::fopen(det_path.c_str(), "w");
    CHECK(det, "cannot write %s", det_path.c_str());
    for (const auto& [k, v] : header)
        std::fprintf(det, "# %s: %s\n", k.c_str(), v.c_str());
    std::fputs("image,conf,rank,cls,score,x1,y1,x2,y2\n", det);

    const float confs[2] = {0.25f, 0.001f};
    std::vector<double> ms[2];
    long detections[2] = {}, equal_reference = 0;
    for (int i = first; i < argc; i++) {
        const std::string stem = std::filesystem::path(argv[i]).stem().string();
        cv::Mat bgr = cv::imread(argv[i], cv::IMREAD_COLOR);
        CHECK(bgr.cols == mw && bgr.rows == mh, "%s is not %dx%d", argv[i], mw, mh);
        cv::Mat rgb(mh, mw, CV_8UC3, slot.data(0));
        cv::cvtColor(bgr, rgb, cv::COLOR_BGR2RGB);
        slot.bind(npu, 0, 0);

        const std::vector<uint8_t> input(slot.data(0), slot.data(0) + slot.size());
        const auto reference = npu.reference_outputs(input, model);
        const auto bound = npu.run_bound(0);
        equal_reference += bound == reference;
        for (size_t k = 0; k < bound.size(); k++) {
            const std::string path = out_dir + "/" + stem + "_out" + std::to_string(k) + ".i8";
            FILE* f = std::fopen(path.c_str(), "wb");
            CHECK(f && std::fwrite(bound[k].data(), 1, bound[k].size(), f) == bound[k].size() && std::fclose(f) == 0,
                  "writing %s failed", path.c_str());
        }

        for (int c = 0; c < 2; c++) {
            std::vector<Detection> got;
            for (int r = 0; r < runs; r++) {
                CHECK(rknn_run(npu.ctx[0], nullptr) == RKNN_SUCC, "rknn_run failed");
                const auto ptrs = npu.output_ptrs(0);
                const double t0 = thread_cpu_ms();
                got = yolov10_post(ptrs, npu.outputs, mw, mh, confs[c], 300);
                ms[c].push_back(thread_cpu_ms() - t0);
            }
            detections[c] += static_cast<long>(got.size());
            for (size_t n = 0; n < got.size(); n++) {
                const auto& d = got[n];
                std::fprintf(det, "%s,%g,%zu,%d,%.9g,%.9g,%.9g,%.9g,%.9g\n", stem.c_str(), confs[c], n, d.cls,
                             d.score, d.x1, d.y1, d.x2, d.y2);
            }
        }
    }
    CHECK(!std::ferror(det) && std::fclose(det) == 0, "writing %s failed", det_path.c_str());
    slot.release(npu);
    npu.close();

    const int images = argc - first;
    rows.push_back({"images", std::to_string(images)});
    rows.push_back({"runs_per_conf_per_image", std::to_string(runs)});
    rows.push_back({"bound_outputs_equal_reference", std::to_string(equal_reference)});
    const char* names[2] = {"conf_0.25", "conf_0.001"};
    for (int c = 0; c < 2; c++) {
        const std::string p = std::string(names[c]) + "_";
        rows.push_back({p + "detections_per_image", fmt("%.4f", double(detections[c]) / images)});
        rows.push_back({p + "post_cpu_ms_median", fmt("%.4f", nearest_rank(ms[c], 0.5))});
        rows.push_back({p + "post_cpu_ms_p90", fmt("%.4f", nearest_rank(ms[c], 0.9))});
        rows.push_back({p + "post_cpu_ms_max", fmt("%.4f", nearest_rank(ms[c], 1.0))});
    }
    const std::string path = out_dir + "/m4_probe.csv";
    FILE* csv = std::fopen(path.c_str(), "w");
    CHECK(csv, "cannot write %s", path.c_str());
    for (const auto& [k, v] : header)
        std::fprintf(csv, "# %s: %s\n", k.c_str(), v.c_str());
    std::fputs("key,value\n", csv);
    for (const auto& [k, v] : rows) {
        std::fprintf(csv, "%s,%s\n", k.c_str(), v.c_str());
        std::printf("%s: %s\n", k.c_str(), v.c_str());
    }
    CHECK(!std::ferror(csv) && std::fclose(csv) == 0, "writing %s failed", path.c_str());
    return equal_reference == images ? 0 : 1;
}
