// The benchmark binary (PROTOCOL §5.1): one run of one variant at one channel count.
//
// Usage: bench_main --variant none|C --channels N --source URL [options]
//   --source URL        rtsp://host:8554/ch{n} or a local file; {n} becomes the channel number 1..N
//   --condition main|aux  sample every 6th frame (5 fps, default) or every frame (30 fps)
//   --warmup S          warm-up seconds (default 20)
//   --duration S        measurement window seconds (default 120)
//   --model PATH        RKNN model (default models/yolov8n_i8.rknn)
//   --out DIR           result directory (default results/<run kind>/<UTC time>_<variant>_<N>ch)
//   --run-kind KIND     check | calibration | equivalence | comparison | reference | m1.1 (default check)
//   --repetition R      repetition index (default 1)
//   --freeze-commit SHA freeze-m1 commit, "none" before the freeze (default none)
// Run as root: the NPU load is read from debugfs (PROTOCOL §7.7).
#include <ctime>
#include <filesystem>
#include <map>
#include <string>

extern "C" {
#include <libavutil/avutil.h>
}

#include <im2d.hpp>

#include "core/metrics.h"
#include "core/npu.h"
#include "core/pipeline.h"
#include "core/report.h"
#include "core/util.h"
#include "preprocess/backend.h"

#ifndef CODE_COMMIT
#define CODE_COMMIT "unknown"
#endif
#ifndef LATEST_AMENDMENT
#define LATEST_AMENDMENT "none"
#endif

namespace {

// Tag prereg-v1 (Release created_at 2026-10-30T02:01:36Z, docs/notes/env.md).
constexpr const char* kPreregCommit = "87fe874b4f196dafd48f6a175b2d7604625aedba";

std::string command_output(const std::string& cmd) {
    std::string out;
    if (FILE* p = popen(cmd.c_str(), "r")) {
        char buf[256];
        while (fgets(buf, sizeof buf, p))
            out += buf;
        pclose(p);
    }
    while (!out.empty() && (out.back() == '\n' || out.back() == ' '))
        out.pop_back();
    return out;
}

std::string utc_now(const char* format) {
    std::time_t t = std::time(nullptr);
    char buf[64];
    std::strftime(buf, sizeof buf, format, std::gmtime(&t));
    return buf;
}

void usage() {
    die("usage: bench_main --variant none|C --channels N --source URL [--condition main|aux] [--warmup S] "
        "[--duration S] [--model PATH] [--out DIR] [--run-kind KIND] [--repetition R] [--freeze-commit SHA]");
}

}  // namespace

int main(int argc, char** argv) {
    // Before any library starts a thread, so MPP, librknnrt and ffmpeg threads inherit it (PROTOCOL §8).
    pin_current_thread({4, 5, 6, 7});

    std::map<std::string, std::string> a = {
        {"condition", "main"}, {"warmup", "20"}, {"duration", "120"}, {"model", "models/yolov8n_i8.rknn"},
        {"run-kind", "check"}, {"repetition", "1"}, {"freeze-commit", "none"}};
    std::string args_json = "[";
    for (int i = 1; i < argc; i++) {
        args_json += (i > 1 ? ", " : "") + json_string(argv[i]);
        std::string k = argv[i];
        if (k.rfind("--", 0) != 0 || i + 1 >= argc)
            usage();
        a[k.substr(2)] = argv[++i];
        args_json += ", " + json_string(argv[i]);
    }
    args_json += "]";
    for (const char* k : {"variant", "channels", "source"})
        if (!a.count(k))
            usage();
    const std::string variant = a["variant"];
    CHECK(variant == "none" || variant == "C", "variant %s is not implemented yet", variant.c_str());
    CHECK(a["condition"] == "main" || a["condition"] == "aux", "condition must be main or aux");
    const std::string kind = a["run-kind"];  // PROTOCOL §1: check_prereg_order relies on these
    CHECK(kind == "check" || kind == "calibration" || kind == "equivalence" || kind == "comparison" ||
              kind == "reference" || kind == "m1.1",
          "unknown run kind %s", kind.c_str());
    const int channels = std::stoi(a["channels"]);
    CHECK(channels >= 1 && channels <= 32, "channels must be 1..32");

    PipelineConfig cfg;
    cfg.sample_stride = a["condition"] == "main" ? 6 : 1;
    cfg.warmup_s = std::stoi(a["warmup"]);
    cfg.duration_s = std::stoi(a["duration"]);
    CHECK(cfg.warmup_s >= 1 && cfg.duration_s >= 1, "warm-up and duration must be at least 1 s");
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
        {"decoder", std::string("h264_rkmpp, ffmpeg ") + av_version_info()},
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
    if (variant == "C") {
        npu.open(a["model"]);
        backend = make_zero_copy_rga(a["model"]);
        backend->init(npu, kInputSlots);
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
    env += "\n  ],\n  \"queues\": {\"K\": 2, \"Q1\": " + std::to_string(2 * channels) +
           ", \"Q2\": 6, \"N\": " + std::to_string(kInputSlots) + "},\n";
    env += "  \"decoder\": {\"name\": \"h264_rkmpp\", \"extra_hw_frames\": 8, \"afbc\": \"off\", "
           "\"hw_device_ctx\": \"decoder default\"},\n";
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
