// The benchmark binary (PROTOCOL §5.1): one run of one variant at one channel count.
//
// Usage: bench_main --variant none|A|B|C|Bd|Rc|By --channels N --source URL [options]
//   --variant Bd        B-direct of PROTOCOL §13 (M1.1, AMENDMENTS A12)
//   --variant Rc|By     M6's arms (prereg/M6.md §1): RGA3's conversion with B's copy, and B with libyuv;
//                       run kind check or m6
//   --source URL        rtsp://host:8554/ch{n} or a local file; {n} becomes the channel number 1..N
//   --condition main|aux  sample every 6th frame (5 fps, default) or every frame (30 fps)
//   --warmup S          warm-up seconds (default 20)
//   --duration S        measurement window seconds (default 120)
//   --model PATH        RKNN model (default models/yolov8n_i8.rknn)
//   --out DIR           result directory (default results/<run kind>/<UTC time>_<variant>_<N>ch)
//   --run-kind KIND     check | calibration | equivalence | comparison | reference | m1.1 | m2 | m3 | m4 | m6
//                       (default check). What an m4 or m6 run may be is in core/run_rules.h (M4 §3.2,
//                       M6 §1). An m3 run is M1's B or C with the defaults, at the main condition
//                       (prereg/M3.md §1).
//   --repetition R      repetition index (default 1)
//   --freeze-commit SHA freeze-m1 commit, "none" before the freeze (default none)
//   --freeze-m2-commit SHA  freeze-m2 commit, "none" before it (default none; prereg/M2.md §5.1)
//   --freeze-m4-commit SHA  freeze-m4 commit, "none" before it (default none; prereg/M4.md §4)
//   --freeze-m3-commit SHA  freeze-m3 commit, "none" before it (default none; prereg/M3.md §4)
//   --freeze-m6-commit SHA  freeze-m6 commit, "none" before it (default none; prereg/M6.md §8)
//   --post m1|neon|v10  post-processing: M1's yolov8_post (default), arm Cp's NeonPost (M2 §1), or
//                       yolov10_post for the YOLOv10n export (arm C10, prereg/M4.md §3.2); neon and v10
//                       with variant C only (core/pipeline_post.h). v10 and the YOLOv10n model are M4's:
//                       run kind check or m4.
//   --stagger off|on    at the main condition, channel n samples the frames with counter % 6 == (n - 1) % 6
//                       (M2 §3.1; default off)
//   --prep-threads K    preprocessing threads (default 2, PROTOCOL §5.1; 4 for M1.1's B with K = 4, A12)
//   --cores a76|a55|all the cores the pipeline runs on: cpu4-7 (default), cpu0-3 (M1.1, A12) or cpu0-7 (arm
//                       Cpf, M6 §1); with a55 the metrics thread moves from cpu0 to cpu4
//   --time-rknn off|on  on: each NPU worker reads its thread CPU time before and after each rknn_run (arm
//                       Ct, M6 §1; default off)
// Run as root: the NPU load is read from debugfs (PROTOCOL §7.7).
#include <exception>
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
#include "core/pipeline_post.h"
#include "core/report.h"
#include "core/run_rules.h"
#include "core/util.h"
#include "preprocess/backend.h"
#include "preprocess/cpu_common.h"
#include "preprocess/m6_backends.h"

#ifndef CODE_COMMIT
#define CODE_COMMIT "unknown"
#endif
#ifndef LATEST_AMENDMENT
#define LATEST_AMENDMENT "none"
#endif

namespace {

void usage() {
    die("usage: bench_main --variant none|A|B|C|Bd|Rc|By --channels N --source URL [--condition main|aux] [--warmup S] "
        "[--duration S] [--model PATH] [--out DIR] [--run-kind KIND] [--repetition R] [--freeze-commit SHA] "
        "[--prep-threads K] [--cores a76|a55|all] [--freeze-m2-commit SHA] [--freeze-m4-commit SHA] "
        "[--freeze-m3-commit SHA] [--freeze-m6-commit SHA] [--post m1|neon|v10] [--stagger off|on] "
        "[--time-rknn off|on]");
}

}  // namespace

int main(int argc, char** argv) {
    std::map<std::string, std::string> a = {
        {"condition", "main"}, {"warmup", "20"}, {"duration", "120"}, {"model", "models/yolov8n_i8.rknn"},
        {"run-kind", "check"}, {"repetition", "1"}, {"freeze-commit", "none"}, {"prep-threads", "2"},
        {"cores", "a76"}, {"freeze-m2-commit", "none"}, {"freeze-m4-commit", "none"}, {"post", "m1"},
        {"stagger", "off"}, {"freeze-m3-commit", "none"}, {"freeze-m6-commit", "none"}, {"time-rknn", "off"}};
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
    CHECK(a["cores"] == "a76" || a["cores"] == "a55" || a["cores"] == "all", "cores must be a76, a55 or all");
    const bool a55 = a["cores"] == "a55", all_cores = a["cores"] == "all";
    // Parsing starts no thread. The pinning comes before any library starts one, so MPP, librknnrt and
    // ffmpeg threads inherit it (PROTOCOL §8). With all (arm Cpf, M6 §1) the process is allowed cpu0-7.
    pin_current_thread(all_cores ? std::vector<int>{0, 1, 2, 3, 4, 5, 6, 7}
                       : a55     ? std::vector<int>{0, 1, 2, 3}
                                 : std::vector<int>{4, 5, 6, 7});

    const std::string variant = a["variant"];
    CHECK(variant == "none" || variant == "A" || variant == "B" || variant == "C" || variant == "Bd" ||
              variant == "Rc" || variant == "By",
          "unknown variant %s", variant.c_str());
    cv::setNumThreads(1);  // PROTOCOL §8
    CHECK(a["condition"] == "main" || a["condition"] == "aux", "condition must be main or aux");
    const std::string kind = a["run-kind"];  // PROTOCOL §1: check_prereg_order relies on these
    CHECK(kind == "check" || kind == "calibration" || kind == "equivalence" || kind == "comparison" ||
              kind == "reference" || kind == "m1.1" || kind == "m2" || kind == "m3" || kind == "m4" || kind == "m6",
          "unknown run kind %s", kind.c_str());
    PostKind post_kind;
    CHECK(parse_post_kind(a["post"], &post_kind), "post must be m1, neon or v10");
    CHECK(a["stagger"] == "off" || a["stagger"] == "on", "stagger must be off or on");
    CHECK(a["stagger"] == "off" || a["condition"] == "main", "stagger is for the main condition (M2 §3.1)");
    CHECK(a["time-rknn"] == "off" || a["time-rknn"] == "on", "time-rknn must be off or on");
    CHECK(kind != "m2" || (variant == "C" && is_commit_sha(a["freeze-m2-commit"])),
          "run kind m2 is variant C with freeze-m2's commit (M2 §5.1)");
    // prereg/M3.md §1 and §4: M1's B or C at H1's point (8 channels, main condition, 20 s and 120 s), with every
    // other option at its default.
    CHECK(kind != "m3" || ((variant == "B" || variant == "C") && is_commit_sha(a["freeze-m3-commit"]) &&
                           a["channels"] == "8" && a["warmup"] == "20" && a["duration"] == "120" &&
                           a["condition"] == "main" && a["post"] == "m1" && a["stagger"] == "off" &&
                           a["model"] == "models/yolov8n_i8.rknn" && a["prep-threads"] == "2" &&
                           a["cores"] == "a76" && a["freeze-m2-commit"] == "none" &&
                           a["freeze-m4-commit"] == "none" && a["freeze-m6-commit"] == "none" &&
                           a["time-rknn"] == "off"),
          "run kind m3 is M1's B or C at 8 channels, main condition, 20 s warm-up and 120 s window, with the "
          "defaults and freeze-m3's commit (M3 §1, §4)");
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
    cfg.stagger = a["stagger"] == "on";
    cfg.time_rknn = a["time-rknn"] == "on";
    for (int i = 1; i <= channels; i++) {
        std::string url = a["source"];
        if (auto p = url.find("{n}"); p != std::string::npos)
            url.replace(p, 3, std::to_string(i));
        cfg.sources.push_back(url);
    }
    const std::string model_sha = command_output("sha256sum '" + a["model"] + "' | cut -d' ' -f1");
    RunChoice choice;  // M4's rules (core/run_rules.h), before the output directory exists
    choice.kind = kind;
    choice.post = post_kind;
    choice.post_runs = variant != "none";
    choice.variant = variant;
    choice.freeze_m4 = a["freeze-m4-commit"];
    choice.model_sha = model_sha;
    choice.channels = channels;
    choice.condition = a["condition"];
    choice.stagger = cfg.stagger;
    choice.a55 = a55 || all_cores;  // M4 runs on the A76 cores only
    choice.prep_threads = cfg.preprocess_threads;
    if (const std::string problem = m4_rule_problem(choice); !problem.empty())
        die("%s (model %s)", problem.c_str(), a["model"].c_str());
    M6Choice m6;  // M6's rules, as M4's
    m6.kind = kind;
    m6.variant = variant;
    m6.post = post_kind;
    m6.cores = a["cores"];
    m6.time_rknn = cfg.time_rknn;
    m6.freeze_m6 = a["freeze-m6-commit"];
    m6.channels = channels;
    m6.condition = a["condition"];
    m6.warmup_s = cfg.warmup_s;
    m6.duration_s = cfg.duration_s;
    m6.stagger = cfg.stagger;
    m6.prep_threads = cfg.preprocess_threads;
    m6.model_path = a["model"];
    m6.model_sha = model_sha;
    m6.other_freeze = a["freeze-m2-commit"] != "none" || a["freeze-m4-commit"] != "none" ||
                      a["freeze-m3-commit"] != "none";
    if (const std::string problem = m6_rule_problem(m6); !problem.empty())
        die("%s", problem.c_str());
    const std::string out = a.count("out") ? a["out"]
                                           : "results/" + a["run-kind"] + "/" + utc_now("%Y%m%dT%H%M%SZ") + "_" +
                                                 variant + "_" + std::to_string(channels) + "ch";
    std::filesystem::create_directories(out);

    Header header = {
        {"start_time_utc", utc_now("%Y-%m-%dT%H:%M:%SZ")},
        {"ntp_synchronized", command_output("timedatectl show -p NTPSynchronized --value")},
        {"prereg_v1_commit", kPreregCommit},
        {"freeze_m1_commit", a["freeze-commit"]},
        {"prereg_m2_commit", kPreregM2Commit},
        {"freeze_m2_commit", a["freeze-m2-commit"]},
        {"prereg_m4_commit", kPreregM4Commit},
        {"freeze_m4_commit", a["freeze-m4-commit"]},
        {"freeze_m3_commit", a["freeze-m3-commit"]},
        {"prereg_m6_commit", kPreregM6Commit},
        {"freeze_m6_commit", a["freeze-m6-commit"]},
        {"libyuv_commit", libyuv_commit()},
        {"run_kind", a["run-kind"]},
        {"model_sha256", model_sha},
        {"decoder", std::string(cfg.software_decode ? "h264 (software)" : "h264_rkmpp") + ", ffmpeg " +
                        av_version_info()},
        {"variant", variant},
        {"channels", std::to_string(channels)},
        {"condition", a["condition"]},
        {"post", a["post"]},
        {"stagger", a["stagger"]},
        {"repetition", a["repetition"]},
        {"code_commit", CODE_COMMIT},
        {"amendment", LATEST_AMENDMENT},
        {"warmup_s", std::to_string(cfg.warmup_s)},
        {"duration_s", std::to_string(cfg.duration_s)},
        {"source", a["source"]},
    };

    NpuContexts npu;
    std::unique_ptr<Backend> backend;
    std::unique_ptr<PipelinePost> post;
    if (variant != "none") {
        npu.open(a["model"]);
        backend = variant == "C"    ? make_zero_copy_rga(a["model"], rga_interpolation())
                  : variant == "B"  ? make_cpu_copy(opencv_interpolation())
                  : variant == "Bd" ? make_cpu_direct(opencv_interpolation())
                  : variant == "Rc" ? make_rga_copy(rga_interpolation())
                  : variant == "By" ? make_cpu_yuv()
                                    : make_cpu_software();
        backend->init(npu, input_slots(cfg.preprocess_threads));
        try {  // neon's tables are built here, before the warm-up (M2 §1)
            post = std::make_unique<PipelinePost>(post_kind, npu.outputs);
        } catch (const std::exception& e) {
            die("%s", e.what());
        }
        cfg.post = post.get();
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
    env += "  \"cores\": {\"pipeline\": \"" + std::string(all_cores ? "cpu0-7" : a55 ? "cpu0-3" : "cpu4-7") +
           "\", \"metrics_thread\": \"cpu" + std::to_string(cfg.metrics_cpu) + "\"},\n";
    env += cfg.software_decode
               ? "  \"decoder\": {\"name\": \"h264\", \"threads\": \"auto (thread_count 0)\"},\n"
               : "  \"decoder\": {\"name\": \"h264_rkmpp\", \"extra_hw_frames\": 8, \"afbc\": \"off\", "
                 "\"hw_device_ctx\": \"decoder default\"},\n";
    env += "  \"opencv\": {\"version\": \"" CV_VERSION "\", \"threads\": " + std::to_string(cv::getNumThreads()) +
           "},\n";
    env += "  \"librga\": " + json_string(querystring(RGA_VERSION)) + ",\n";
    env += "  \"sampler\": {\"stride\": " + std::to_string(cfg.sample_stride) +
           ", \"stagger\": " + (cfg.stagger ? "true" : "false") + "},\n";
    env += "  \"post\": {" + (post ? post->describe() : "\"name\": " + json_string(a["post"])) +
           ", \"objects_sha256\": " + post_objects_json() + "},\n";
    if (backend) {
        env += "  \"rknn\": {\"init_flags\": " + std::to_string(NpuContexts::kInitFlags) +
               ", \"sdk\": " + json_string(npu.sdk_version) +
               ", \"core_masks\": [1, 2, 4], \"output_binding\": \"NCHW INT8 rknn_create_mem + rknn_set_io_mem\", "
               "\"context_path\": \"late binding\"},\n";
        env += "  \"backend\": {" + backend->describe() + "},\n";
    }
    env += "  \"m6\": {\"arm\": " + json_string(m6_arm(variant, post_kind, a["cores"], cfg.time_rknn)) +
           ", \"time_rknn\": " + (cfg.time_rknn ? "true" : "false") + "},\n";
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
