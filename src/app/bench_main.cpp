// The benchmark binary (PROTOCOL §5.1): one run of one variant at one channel count.
//
// Usage: bench_main --variant none|A|B|C|Bd --channels N --source URL [options]
//   --variant Bd        B-direct of PROTOCOL §13 (M1.1, AMENDMENTS A12)
//   --source URL        rtsp://host:8554/ch{n} or a local file; {n} becomes the channel number 1..N
//   --condition main|aux  sample every 6th frame (5 fps, default) or every frame (30 fps)
//   --warmup S          warm-up seconds (default 20)
//   --duration S        measurement window seconds (default 120)
//   --model PATH        RKNN model (default models/yolov8n_i8.rknn)
//   --out DIR           result directory (default results/<run kind>/<UTC time>_<variant>_<N>ch)
//   --run-kind KIND     check | calibration | equivalence | comparison | reference | m1.1 (default check)
//   --repetition R      repetition index (default 1)
//   --freeze-commit SHA freeze-m1 commit, "none" before the freeze (default none)
//   --prep-threads K    preprocessing threads (default 2, PROTOCOL §5.1; 4 for M1.1's B with K = 4, A12)
//   --cores a76|a55     the cores the pipeline runs on: cpu4-7 (default) or cpu0-3 (M1.1, A12); with a55
//                       the metrics thread moves from cpu0 to cpu4
// Run as root: the NPU load is read from debugfs (PROTOCOL §7.7).
#include <filesystem>
#include <map>
#include <set>
#include <string>
#include <vector>

extern "C" {
#include <libavutil/avutil.h>
}

#include <im2d.hpp>
#include <opencv2/core.hpp>

#include "core/metrics.h"
#include "core/npu.h"
#include "core/pipeline.h"
#include "core/report.h"
#include "core/util.h"
#include "preprocess/backend.h"
#include "preprocess/cpu_common.h"

#ifndef CODE_COMMIT
#define CODE_COMMIT "unknown"
#endif
#ifndef LATEST_AMENDMENT
#define LATEST_AMENDMENT "none"
#endif

namespace {

void usage() {
    die("usage: bench_main --variant none|A|B|C|Bd --channels N --source URL [--condition main|aux] [--warmup S] "
        "[--duration S] [--model PATH] [--out DIR] [--run-kind KIND] [--repetition R] [--freeze-commit SHA] "
        "[--prep-threads K] [--cores a76|a55]");
}

}  // namespace

int main(int argc, char** argv) {
    std::map<std::string, std::string> a = {
        {"condition", "main"}, {"warmup", "20"}, {"duration", "120"}, {"model", "models/yolov8n_i8.rknn"},
        {"run-kind", "check"}, {"repetition", "1"}, {"freeze-commit", "none"}, {"prep-threads", "2"},
        {"cores", "a76"}};
    const std::set<std::string> known = {"variant", "channels", "source", "out"};
    std::string args_json = "[";
    for (int i = 1; i < argc; i++) {
        args_json += (i > 1 ? ", " : "") + json_string(argv[i]);
        std::string k = argv[i];
        if (k.rfind("--", 0) != 0 || i + 1 >= argc)
            usage();
        k = k.substr(2);
        CHECK(a.count(k) || known.count(k), "unknown option --%s", k.c_str());
        a[k] = argv[++i];
        args_json += ", " + json_string(argv[i]);
    }
    args_json += "]";
    for (const char* k : {"variant", "channels", "source"})
        if (!a.count(k))
            usage();
    CHECK(a["cores"] == "a76" || a["cores"] == "a55", "cores must be a76 or a55");
    const bool a55 = a["cores"] == "a55";
    // Parsing starts no thread. The pinning comes before any library starts one, so MPP, librknnrt and
    // ffmpeg threads inherit it (PROTOCOL §8).
    pin_current_thread(a55 ? std::vector<int>{0, 1, 2, 3} : std::vector<int>{4, 5, 6, 7});

    const std::string variant = a["variant"];
    CHECK(variant == "none" || variant == "A" || variant == "B" || variant == "C" || variant == "Bd",
          "unknown variant %s", variant.c_str());
    cv::setNumThreads(1);  // PROTOCOL §8
    CHECK(a["condition"] == "main" || a["condition"] == "aux", "condition must be main or aux");
    const std::string kind = a["run-kind"];  // PROTOCOL §1: check_prereg_order relies on these
    CHECK(kind == "check" || kind == "calibration" || kind == "equivalence" || kind == "comparison" ||
              kind == "reference" || kind == "m1.1",
          "unknown run kind %s", kind.c_str());
    const int channels = std::stoi(a["channels"]);
    CHECK(channels >= 1 && channels <= 32, "channels must be 1..32");

    PipelineConfig cfg;
    cfg.software_decode = variant == "A";
    cfg.sample_stride = a["condition"] == "main" ? 6 : 1;
    cfg.warmup_s = std::stoi(a["warmup"]);
    cfg.duration_s = std::stoi(a["duration"]);
    CHECK(cfg.warmup_s >= 1 && cfg.duration_s >= 1, "warm-up and duration must be at least 1 s");
    cfg.preprocess_threads = std::stoi(a["prep-threads"]);
    CHECK(cfg.preprocess_threads >= 1 && cfg.preprocess_threads <= 8, "prep-threads must be 1..8");
    cfg.metrics_cpu = a55 ? 4 : 0;
    for (int i = 1; i <= channels; i++) {
        std::string url = a["source"];
        if (auto p = url.find("{n}"); p != std::string::npos)
            url.replace(p, 3, std::to_string(i));
        cfg.sources.push_back(url);
    }
    const std::string out = a.count("out") ? a["out"]
                                           : "results/" + a["run-kind"] + "/" + utc_now("%Y%m%dT%H%M%SZ") + "_" +
                                                 variant + "_" + std::to_string(channels) + "ch";
    std::filesystem::create_directories(out);

    Header header = {
        {"start_time_utc", utc_now("%Y-%m-%dT%H:%M:%SZ")},
        {"ntp_synchronized", command_output("timedatectl show -p NTPSynchronized --value")},
        {"prereg_v1_commit", kPreregCommit},
        {"freeze_m1_commit", a["freeze-commit"]},
        {"run_kind", a["run-kind"]},
        {"model_sha256", command_output("sha256sum '" + a["model"] + "' | cut -d' ' -f1")},
        {"decoder", std::string(cfg.software_decode ? "h264 (software)" : "h264_rkmpp") + ", ffmpeg " +
                        av_version_info()},
        {"variant", variant},
        {"channels", std::to_string(channels)},
        {"condition", a["condition"]},
        {"repetition", a["repetition"]},
        {"code_commit", CODE_COMMIT},
        {"amendment", LATEST_AMENDMENT},
        {"warmup_s", std::to_string(cfg.warmup_s)},
        {"duration_s", std::to_string(cfg.duration_s)},
        {"source", a["source"]},
    };

    NpuContexts npu;
    std::unique_ptr<Backend> backend;
    if (variant != "none") {
        npu.open(a["model"]);
        backend = variant == "C"    ? make_zero_copy_rga(a["model"], rga_interpolation())
                  : variant == "B"  ? make_cpu_copy(opencv_interpolation())
                  : variant == "Bd" ? make_cpu_direct(opencv_interpolation())
                                    : make_cpu_software();
        backend->init(npu, input_slots(cfg.preprocess_threads));
    }

    std::fprintf(stderr, "bench: %s, %d channels, warm-up %d s, window %d s -> %s\n", variant.c_str(), channels,
                 cfg.warmup_s, cfg.duration_s, out.c_str());
    RunResult r = run_pipeline(cfg, backend ? &npu : nullptr, backend.get());

    std::string env = "  \"args\": " + args_json + ",\n  \"threads_at_window_start\": [";
    bool first = true;
    for (const auto& [tid, t] : r.cpu_w.threads) {
        env += std::string(first ? "\n" : ",\n") + "    {\"tid\": " + std::to_string(tid) +
               ", \"comm\": " + json_string(t.comm) + ", \"affinity\": " + json_string(t.affinity) + "}";
        first = false;
    }
    env += "\n  ],\n  \"queues\": {\"K\": " + std::to_string(cfg.preprocess_threads) +
           ", \"Q1\": " + std::to_string(2 * channels) + ", \"Q2\": " + std::to_string(kQ2Depth) +
           ", \"N\": " + std::to_string(input_slots(cfg.preprocess_threads)) + "},\n";
    env += "  \"cores\": {\"pipeline\": \"" + std::string(a55 ? "cpu0-3" : "cpu4-7") +
           "\", \"metrics_thread\": \"cpu" + std::to_string(cfg.metrics_cpu) + "\"},\n";
    env += cfg.software_decode
               ? "  \"decoder\": {\"name\": \"h264\", \"threads\": \"auto (thread_count 0)\"},\n"
               : "  \"decoder\": {\"name\": \"h264_rkmpp\", \"extra_hw_frames\": 8, \"afbc\": \"off\", "
                 "\"hw_device_ctx\": \"decoder default\"},\n";
    env += "  \"opencv\": {\"version\": \"" CV_VERSION "\", \"threads\": " + std::to_string(cv::getNumThreads()) +
           "},\n";
    env += "  \"librga\": " + json_string(querystring(RGA_VERSION)) + ",\n";
    if (backend) {
        env += "  \"rknn\": {\"init_flags\": " + std::to_string(NpuContexts::kInitFlags) +
               ", \"sdk\": " + json_string(npu.sdk_version) +
               ", \"core_masks\": [1, 2, 4], \"output_binding\": \"NCHW INT8 rknn_create_mem + rknn_set_io_mem\", "
               "\"context_path\": \"late binding\"},\n";
        env += "  \"backend\": {" + backend->describe() + "},\n";
    }
    env += "  \"variant\": " + json_string(variant);

    if (backend) {
        backend->shutdown(npu);
        npu.close();
    }
    Header summary = write_results(out, header, cfg, r, env);
    for (const auto& [k, v] : summary)
        std::printf("%s: %s\n", k.c_str(), v.c_str());
    std::printf("results: %s\n", out.c_str());
    return 0;
}
