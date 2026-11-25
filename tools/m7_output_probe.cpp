// The probe of the NPU's output bindings (prereg/M7.md §3 "The probe of the output bindings (#60)", §4,
// §5 "The probe", §8 step 3). One process does one job for one configuration, so that a configuration
// whose process crashes is refused and the others still run (§3). tools/m7_probe.py starts the processes,
// compares the outputs with configuration 1's and computes the cost's statistics.
//
// As tools/output_probe measured the output path before M2: one context on NPU core 0 with the pipeline's
// init flags (none, core/npu.h), the YOLOv8n model, and a cached input from rknn_create_mem (cacheable by
// default) bound as B binds it (UINT8 NHWC, preprocess/cpu_common.cpp) and written by the CPU with memcpy
// before every run. The input alternates two frames of video ①, decoded frames 0 and 30 letterboxed as A
// does, as in output_probe. Each configuration binds every output with rknn_set_io_mem, in memory from
// rknn_create_mem:
//   1  RKNN_QUERY_OUTPUT_ATTR as NCHW INT8: the pipeline's binding (core/npu.cpp), the reference
//   2  RKNN_QUERY_OUTPUT_ATTR as NHWC INT8
//   3  RKNN_QUERY_OUTPUT_ATTR as NCHW FLOAT32
//   4  RKNN_QUERY_OUTPUT_ATTR as NHWC FLOAT32
//   5  RKNN_QUERY_NATIVE_OUTPUT_ATTR, unchanged
//   6  RKNN_QUERY_NATIVE_NHWC_OUTPUT_ATTR, unchanged
// For 1 to 4 the query's attributes get the configuration's fmt and type and nothing else changes but the
// sizes below: the dims stay the query's, in NCHW order, and tools/m7_probe.py reads every layout with
// configuration 1's shape. The query gives NCHW INT8, so configuration 1 binds what core/npu.cpp binds.
// The sizes: an INT8 binding keeps the query's size and size_with_stride, as core/npu.cpp binds. A FLOAT32
// binding has size = n_elems x 4, the dense tensor in 4-byte elements, and size_with_stride = the query's
// x 4, the same stride counted in 4-byte elements; whichever of the two the runtime checks against the
// memory, the memory holds it. Every output's memory has max(size, size_with_stride) bytes of its
// attributes as bound, as core/npu.cpp allocates, and a dump writes all of them: the values are at its
// start, and for configurations 1 to 4 the first n_elems elements are the dense tensor.
//
// Modes:
//   dump  RUNS runs (default 40) alternating the two frames, the first on frame 0; after each run every
//         output's bytes go to run<RR>_out<K>.<ext> (ext i8 for INT8, f32 for FLOAT32, bin otherwise).
//         With --runs 1 only frame 0 is decoded: §8 step 3's smoke, configuration 1 on one frame.
//   time  40 untimed runs, then RUNS timed runs (default 20), alternating the two frames throughout. Per
//         timed run, across rknn_run alone (the input's memcpy is outside it): the calling thread's CPU
//         (CLOCK_THREAD_CPUTIME_ID), the process CPU (CLOCK_PROCESS_CPUTIME_ID: every thread, the
//         runtime's included) and the wall time. The untimed runs keep the two tools comparable:
//         output_probe timed each context after its 40 correctness runs, so a context here is timed after
//         as many runs, and the costlier first runs of a new context are outside the timing.
//
// Usage: m7_output_probe dump|time CONFIG OUT_DIR [--model PATH] [--video PATH] [--runs N]
//                        [--run-kind check|m7] [--freeze-m7-commit SHA]       (as root, clocks fixed)
//   --model             default models/yolov8n_i8.rknn
//   --video             default video ①, /home/pi/media/bench/pexels_36330861.mp4
//   --run-kind          check (default) or m7; m7 needs freeze-m7's commit, the YOLOv8n model and the
//                       registered numbers of runs (40 to dump, 20 to time)
//   --freeze-m7-commit  freeze-m7's commit, or none before it (default none; prereg/M7.md §8)
// Files in OUT_DIR, each CSV with "# key: value" header lines (PROTOCOL §1.4):
//   attrs.csv             one row per output: its attributes as bound, and the fmt and type its query
//                         gave; written before the first run
//   run<RR>_out<K>.<ext>  (dump) the output's bytes after run RR
//   time.csv              (time) run,frame,thread_cpu_ns,process_cpu_ns,wall_ns, one row per timed run
//   refused.csv           a call failed: key,value rows exit_code, stage, reason, run
// Exit code: 0 done; 2 rknn_init or rknn_set_core_mask failed; 3 a query failed; 4 rknn_create_mem or
// rknn_set_io_mem failed; 5 rknn_run failed; 1 any other failure (its reason on stderr).
#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <map>
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
#include "preprocess/cpu_common.h"

#ifndef CODE_COMMIT
#define CODE_COMMIT "unknown"
#endif
#ifndef LATEST_AMENDMENT
#define LATEST_AMENDMENT "none"
#endif

namespace {

constexpr const char* kVideo = "/home/pi/media/bench/pexels_36330861.mp4";  // video ①
constexpr int kDumpRuns = 40, kTimedRuns = 20, kUntimedRuns = 40;             // M7 §3
constexpr int kExitSetup = 2, kExitQuery = 3, kExitBinding = 4, kExitRun = 5;

struct Config {
    const char* binding;  // the header's description
    rknn_query_cmd query;
    const char* query_name;
    bool set;  // fmt and type set on the query's attributes (1 to 4), or bound unchanged (5, 6)
    rknn_tensor_format fmt;
    rknn_tensor_type type;
};

const Config kConfigs[] = {
    {"NCHW INT8 from RKNN_QUERY_OUTPUT_ATTR (the pipeline's binding; the reference)", RKNN_QUERY_OUTPUT_ATTR,
     "RKNN_QUERY_OUTPUT_ATTR", true, RKNN_TENSOR_NCHW, RKNN_TENSOR_INT8},
    {"NHWC INT8 from RKNN_QUERY_OUTPUT_ATTR", RKNN_QUERY_OUTPUT_ATTR, "RKNN_QUERY_OUTPUT_ATTR", true,
     RKNN_TENSOR_NHWC, RKNN_TENSOR_INT8},
    {"NCHW FLOAT32 from RKNN_QUERY_OUTPUT_ATTR", RKNN_QUERY_OUTPUT_ATTR, "RKNN_QUERY_OUTPUT_ATTR", true,
     RKNN_TENSOR_NCHW, RKNN_TENSOR_FLOAT32},
    {"NHWC FLOAT32 from RKNN_QUERY_OUTPUT_ATTR", RKNN_QUERY_OUTPUT_ATTR, "RKNN_QUERY_OUTPUT_ATTR", true,
     RKNN_TENSOR_NHWC, RKNN_TENSOR_FLOAT32},
    {"RKNN_QUERY_NATIVE_OUTPUT_ATTR unchanged", RKNN_QUERY_NATIVE_OUTPUT_ATTR, "RKNN_QUERY_NATIVE_OUTPUT_ATTR",
     false, RKNN_TENSOR_NCHW, RKNN_TENSOR_INT8},
    {"RKNN_QUERY_NATIVE_NHWC_OUTPUT_ATTR unchanged", RKNN_QUERY_NATIVE_NHWC_OUTPUT_ATTR,
     "RKNN_QUERY_NATIVE_NHWC_OUTPUT_ATTR", false, RKNN_TENSOR_NCHW, RKNN_TENSOR_INT8},
};

std::string fmt_name(rknn_tensor_format f) {
    switch (f) {
    case RKNN_TENSOR_NCHW:
        return "NCHW";
    case RKNN_TENSOR_NHWC:
        return "NHWC";
    case RKNN_TENSOR_NC1HWC2:
        return "NC1HWC2";
    default:
        return "fmt" + std::to_string(int(f));
    }
}

std::string type_name(rknn_tensor_type t) {
    switch (t) {
    case RKNN_TENSOR_FLOAT32:
        return "FLOAT32";
    case RKNN_TENSOR_FLOAT16:
        return "FLOAT16";
    case RKNN_TENSOR_INT8:
        return "INT8";
    case RKNN_TENSOR_UINT8:
        return "UINT8";
    case RKNN_TENSOR_INT16:
        return "INT16";
    case RKNN_TENSOR_INT32:
        return "INT32";
    default:
        return "type" + std::to_string(int(t));
    }
}

std::string qnt_name(rknn_tensor_qnt_type q) {
    switch (q) {
    case RKNN_TENSOR_QNT_NONE:
        return "NONE";
    case RKNN_TENSOR_QNT_DFP:
        return "DFP";
    case RKNN_TENSOR_QNT_AFFINE_ASYMMETRIC:
        return "AFFINE_ASYMMETRIC";
    default:
        return "qnt" + std::to_string(int(q));
    }
}

std::string extension(rknn_tensor_type t) {
    return t == RKNN_TENSOR_INT8 ? "i8" : t == RKNN_TENSOR_FLOAT32 ? "f32" : "bin";
}

int64_t clock_ns(clockid_t id) {
    timespec ts{};
    clock_gettime(id, &ts);
    return int64_t(ts.tv_sec) * 1000000000 + ts.tv_nsec;
}

std::vector<char> read_file(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    CHECK(f.good(), "cannot read %s", path.c_str());
    return std::vector<char>((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
}

void write_bytes(const std::string& path, const void* data, size_t n) {
    FILE* f = std::fopen(path.c_str(), "wb");
    CHECK(f, "cannot write %s", path.c_str());
    const bool ok = std::fwrite(data, 1, n, f) == n;
    CHECK(std::fclose(f) == 0 && ok, "writing %s failed", path.c_str());
}

// The output directory and its header: every CSV, and the refusal record.
struct Record {
    std::string dir;
    Header header;

    void write_csv(const std::string& name, const std::string& body) const {
        std::string text;
        for (const auto& [k, v] : header)
            text += "# " + k + ": " + v + "\n";
        text += body;
        write_bytes(dir + "/" + name, text.data(), text.size());
    }

    // A call failed: the configuration is refused. refused.csv, the reason on stderr, the stage's exit code.
    [[noreturn]] void refuse(int code, const std::string& reason, int run = -1) const {
        const char* stage = code == kExitSetup ? "setup" : code == kExitQuery ? "query"
                            : code == kExitBinding ? "binding" : "run";
        std::fprintf(stderr, "m7_output_probe: refused (%s): %s%s\n", stage, reason.c_str(),
                     run >= 0 ? (" in run " + std::to_string(run)).c_str() : "");
        write_csv("refused.csv", "key,value\nexit_code," + std::to_string(code) + "\nstage," + stage + "\nreason," +
                                     reason + "\nrun," + (run >= 0 ? std::to_string(run) : "none") + "\n");
        std::exit(code);
    }
};

}  // namespace

int main(int argc, char** argv) {
    // The pinning comes before any library starts a thread, so the runtime's and the decoder's threads
    // inherit it, as in bench_main (PROTOCOL §8).
    pin_current_thread({4, 5, 6, 7});
    cv::setNumThreads(1);  // PROTOCOL §8
    const std::string start = utc_now("%Y-%m-%dT%H:%M:%SZ");
    const char* usage = "usage: m7_output_probe dump|time CONFIG OUT_DIR [--model PATH] [--video PATH] [--runs N] "
                        "[--run-kind check|m7] [--freeze-m7-commit SHA]";
    if (argc < 4)
        die("%s", usage);
    const std::string mode = argv[1], out = argv[3];
    const int config = std::atoi(argv[2]);
    std::map<std::string, std::string> a = {{"model", "models/yolov8n_i8.rknn"}, {"video", kVideo}, {"runs", ""},
                                            {"run-kind", "check"}, {"freeze-m7-commit", "none"}};
    for (int i = 4; i < argc; i++) {
        const std::string k = argv[i];
        CHECK(k.rfind("--", 0) == 0 && i + 1 < argc && a.count(k.substr(2)), "unknown option %s; %s", k.c_str(),
              usage);
        a[k.substr(2)] = argv[++i];
    }
    CHECK(mode == "dump" || mode == "time", "%s", usage);
    CHECK(config >= 1 && config <= 6, "CONFIG is 1 to 6");
    const bool dump = mode == "dump";
    const int registered_runs = dump ? kDumpRuns : kTimedRuns;
    const int runs = a["runs"].empty() ? registered_runs : std::atoi(a["runs"].c_str());
    CHECK(runs >= 1 && runs <= 1000, "--runs is 1 to 1000");
    const std::string kind = a["run-kind"], freeze = a["freeze-m7-commit"];
    CHECK(kind == "check" || kind == "m7", "run kind must be check or m7");
    CHECK(freeze == "none" || is_commit_sha(freeze), "freeze-m7-commit must be none or a full commit SHA");
    const std::string model_sha = file_sha256(a["model"]);
    CHECK(!model_sha.empty(), "cannot read %s", a["model"].c_str());
    CHECK(kind != "m7" || (freeze != "none" && model_sha == kYolov8nSha256 && runs == registered_runs),
          "run kind m7 needs freeze-m7's commit, the YOLOv8n model and the registered runs (M7 §3, §8)");
    const Config& cfg = kConfigs[config - 1];
    const size_t n_frames = dump && runs == 1 ? 1 : 2;
    std::filesystem::create_directories(out);

    Record rec{out,
               {
                   {"start_time_utc", start},
                   {"ntp_synchronized", command_output("timedatectl show -p NTPSynchronized --value")},
                   {"prereg_m7_commit", kPreregM7Commit},
                   {"freeze_m7_commit", freeze},
                   {"run_kind", kind},
                   {"model_sha256", model_sha},
                   {"code_commit", CODE_COMMIT},
                   {"amendment", LATEST_AMENDMENT},
                   {"tool", "m7_output_probe"},
                   {"configuration", std::to_string(config)},
                   {"binding", cfg.binding},
                   {"mode", mode},
                   {"runs", std::to_string(runs)},
                   {"untimed_runs", dump ? "0" : std::to_string(kUntimedRuns)},
                   {"source", a["video"]},
                   {"frames", n_frames == 1 ? "decoded frame 0, letterboxed as A does"
                                            : "decoded frames 0 and 30, letterboxed as A does; run r on the "
                                              "first if r is even"},
               }};

    // ---- one context on NPU core 0 ----
    std::vector<char> model = read_file(a["model"]);
    rknn_context ctx = 0;
    int ret = rknn_init(&ctx, model.data(), model.size(), NpuContexts::kInitFlags, nullptr);
    if (ret != RKNN_SUCC)
        rec.refuse(kExitSetup, "rknn_init returned " + std::to_string(ret));
    ret = rknn_set_core_mask(ctx, RKNN_NPU_CORE_0);
    if (ret != RKNN_SUCC)
        rec.refuse(kExitSetup, "rknn_set_core_mask returned " + std::to_string(ret));
    rknn_sdk_version version{};
    if (rknn_query(ctx, RKNN_QUERY_SDK_VERSION, &version, sizeof version) == RKNN_SUCC)
        rec.header.emplace_back("librknnrt", std::string(version.api_version) + " / driver " + version.drv_version);
    rknn_input_output_num io{};
    ret = rknn_query(ctx, RKNN_QUERY_IN_OUT_NUM, &io, sizeof io);
    if (ret != RKNN_SUCC)
        rec.refuse(kExitQuery, "rknn_query RKNN_QUERY_IN_OUT_NUM returned " + std::to_string(ret));
    CHECK(io.n_input == 1, "expected one model input");
    rknn_tensor_attr in{};
    in.index = 0;
    ret = rknn_query(ctx, RKNN_QUERY_INPUT_ATTR, &in, sizeof in);
    if (ret != RKNN_SUCC)
        rec.refuse(kExitQuery, "rknn_query RKNN_QUERY_INPUT_ATTR returned " + std::to_string(ret));
    CHECK(in.n_dims == 4 && in.dims[3] == 3, "expected an NHWC RGB input");
    const int mw = static_cast<int>(in.dims[2]), mh = static_cast<int>(in.dims[1]);
    const size_t frame_bytes = size_t(mw) * mh * 3;

    // ---- the frames, decoded before any run; the decoder and its threads end with the block ----
    std::vector<std::vector<uint8_t>> frame;
    {
        std::atomic<bool> stop{false};
        int decoded = 0;
        VideoSource src(a["video"], /*software=*/true, /*paced=*/false);
        src.open(&stop);
        src.run(
            [&](AVFrame* f) {
                if (!stop && (decoded == 0 || decoded == 30)) {
                    frame.emplace_back(frame_bytes, 114);
                    letterbox_i420(f->data[0], f->linesize[0], f->data[1], f->linesize[1], f->data[2],
                                   f->linesize[2], f->width, f->height, frame.back().data(), mw, mh,
                                   opencv_interpolation());
                    stop = frame.size() == n_frames;
                }
                decoded++;
                av_frame_free(&f);
            },
            [] { die("decoder error"); });
    }
    CHECK(frame.size() == n_frames, "the video has fewer than 31 frames");

    // ---- the input, as B binds it ----
    rknn_tensor_attr in_attr = in;
    in_attr.type = RKNN_TENSOR_UINT8;
    in_attr.fmt = RKNN_TENSOR_NHWC;
    in_attr.pass_through = 0;
    CHECK(in_attr.w_stride == 0 || in_attr.w_stride == uint32_t(mw), "input row stride %u, expected %d",
          in_attr.w_stride, mw);
    rknn_tensor_mem* in_mem = rknn_create_mem(ctx, std::max<uint32_t>(in_attr.size_with_stride, uint32_t(frame_bytes)));
    if (!in_mem)
        rec.refuse(kExitBinding, "rknn_create_mem of the input returned no memory");
    ret = rknn_set_io_mem(ctx, in_mem, &in_attr);
    if (ret != RKNN_SUCC)
        rec.refuse(kExitBinding, "rknn_set_io_mem of the input returned " + std::to_string(ret));

    // ---- the outputs, as the configuration binds them ----
    std::vector<rknn_tensor_mem*> out_mem;
    std::vector<uint32_t> out_bytes;
    std::vector<std::string> out_ext;
    std::string attrs = "output,name,n_dims,dims,n_elems,size,size_with_stride,w_stride,h_stride,fmt,type,qnt_type,zp,"
                        "scale,queried_fmt,queried_type,bytes,ext\n";
    for (uint32_t o = 0; o < io.n_output; o++) {
        const std::string which = std::string("rknn_query ") + cfg.query_name + " of output " + std::to_string(o);
        rknn_tensor_attr q{};
        q.index = o;
        ret = rknn_query(ctx, cfg.query, &q, sizeof q);
        if (ret != RKNN_SUCC)
            rec.refuse(kExitQuery, which + " returned " + std::to_string(ret));
        rknn_tensor_attr b = q;
        if (cfg.set) {
            CHECK(q.type == RKNN_TENSOR_INT8, "output %u: RKNN_QUERY_OUTPUT_ATTR gives %s, not the INT8 model's INT8",
                  o, type_name(q.type).c_str());
            b.fmt = cfg.fmt;
            b.type = cfg.type;
            if (cfg.type == RKNN_TENSOR_FLOAT32) {
                b.size = q.n_elems * uint32_t(sizeof(float));
                b.size_with_stride = q.size_with_stride * uint32_t(sizeof(float));
            }
        }
        const uint32_t bytes = std::max(b.size, b.size_with_stride);
        rknn_tensor_mem* m = rknn_create_mem(ctx, bytes);
        if (!m)
            rec.refuse(kExitBinding, "rknn_create_mem of output " + std::to_string(o) + " (" + std::to_string(bytes) +
                                         " bytes) returned no memory");
        out_mem.push_back(m);
        out_bytes.push_back(bytes);
        out_ext.push_back(extension(b.type));
        ret = rknn_set_io_mem(ctx, m, &b);
        if (ret != RKNN_SUCC)
            rec.refuse(kExitBinding, "rknn_set_io_mem of output " + std::to_string(o) + " returned " +
                                         std::to_string(ret));
        std::string name = b.name, dims;
        std::replace(name.begin(), name.end(), ',', '_');
        for (uint32_t d = 0; d < b.n_dims; d++)
            dims += (d ? "x" : "") + std::to_string(b.dims[d]);
        char line[512];
        std::snprintf(line, sizeof line, "%u,%s,%u,%s,%u,%u,%u,%u,%u,%s,%s,%s,%d,%.9g,%s,%s,%u,%s\n", o, name.c_str(),
                      b.n_dims, dims.c_str(), b.n_elems, b.size, b.size_with_stride, b.w_stride, b.h_stride,
                      fmt_name(b.fmt).c_str(), type_name(b.type).c_str(), qnt_name(b.qnt_type).c_str(), b.zp,
                      double(b.scale), fmt_name(q.fmt).c_str(), type_name(q.type).c_str(), bytes,
                      out_ext.back().c_str());
        attrs += line;
    }
    rec.write_csv("attrs.csv", attrs);

    if (dump) {
        for (int r = 0; r < runs; r++) {
            std::memcpy(in_mem->virt_addr, frame[r % n_frames].data(), frame_bytes);
            ret = rknn_run(ctx, nullptr);
            if (ret != RKNN_SUCC)
                rec.refuse(kExitRun, "rknn_run returned " + std::to_string(ret), r);
            for (uint32_t o = 0; o < io.n_output; o++) {
                char name[64];
                std::snprintf(name, sizeof name, "/run%02d_out%u.", r, o);
                write_bytes(out + name + out_ext[o], out_mem[o]->virt_addr, out_bytes[o]);
            }
        }
    } else {
        for (int r = 0; r < kUntimedRuns; r++) {
            std::memcpy(in_mem->virt_addr, frame[r % n_frames].data(), frame_bytes);
            ret = rknn_run(ctx, nullptr);
            if (ret != RKNN_SUCC)
                rec.refuse(kExitRun, "rknn_run returned " + std::to_string(ret), r);
        }
        std::string rows = "run,frame,thread_cpu_ns,process_cpu_ns,wall_ns\n";
        for (int i = 0; i < runs; i++) {
            const int r = kUntimedRuns + i;
            const size_t k = r % n_frames;
            std::memcpy(in_mem->virt_addr, frame[k].data(), frame_bytes);
            const int64_t p0 = clock_ns(CLOCK_PROCESS_CPUTIME_ID), t0 = clock_ns(CLOCK_THREAD_CPUTIME_ID),
                          w0 = now_ns();
            ret = rknn_run(ctx, nullptr);
            const int64_t w1 = now_ns(), t1 = clock_ns(CLOCK_THREAD_CPUTIME_ID),
                          p1 = clock_ns(CLOCK_PROCESS_CPUTIME_ID);
            if (ret != RKNN_SUCC)
                rec.refuse(kExitRun, "rknn_run returned " + std::to_string(ret), r);
            char line[128];
            std::snprintf(line, sizeof line, "%d,%zu,%lld,%lld,%lld\n", i, k, static_cast<long long>(t1 - t0),
                          static_cast<long long>(p1 - p0), static_cast<long long>(w1 - w0));
            rows += line;
        }
        rec.write_csv("time.csv", rows);
    }

    for (auto* m : out_mem)
        rknn_destroy_mem(ctx, m);
    rknn_destroy_mem(ctx, in_mem);
    rknn_destroy(ctx);
    std::printf("m7_output_probe: configuration %d, %s, %d runs: done (%s)\n", config, mode.c_str(), runs, out.c_str());
    return 0;
}
