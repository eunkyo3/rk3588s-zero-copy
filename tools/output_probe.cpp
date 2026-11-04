// Exploration before M2's pre-registration, not a run that counts: the runtime's options for the
// output path (librknnrt 2.3.2), whether each one runs and gives the right outputs, and what rknn_run
// costs with it. M1 binds every context with no init flag and cacheable memory (PROTOCOL §8).
//
// Each configuration gets its own context on NPU core 0, with its init flags, an input memory and
// output memories (rknn_create_mem2, cacheable or not, or SRAM), all bound with rknn_set_io_mem as
// M1 binds them. Inputs are two decoded frames of the video, letterboxed as A does.
//   - Correctness: 20 runs alternating the two frames; after each, every output must equal
//     rknn_outputs_get of a separate context on that frame (NpuContexts::reference_outputs).
//   - Cost: in each round, every configuration in turn runs the round's frame `runs` times. Process
//     CPU time (every thread, the runtime's included) and wall time per run, then the thread CPU of
//     yolov8_post on its outputs per call. Median and range over the rounds.
//
// Usage: output_probe <video.mp4> <model.rknn> <out_dir> [rounds=15] [runs=20]   (as root, clocks fixed)
//   <out_dir>/output_probe.csv  "# key: value" header lines (PROTOCOL §1.4, run kind check), then one
//                               row per configuration
#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iterator>
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
#include "core/yolov8_post.h"
#include "preprocess/cpu_common.h"

#ifndef CODE_COMMIT
#define CODE_COMMIT "unknown"
#endif
#ifndef LATEST_AMENDMENT
#define LATEST_AMENDMENT "none"
#endif

namespace {

struct Config {
    const char* name;
    uint32_t init_flags;
    uint64_t in_mem, out_mem;  // rknn_create_mem2 flags
    bool sync_out;             // rknn_mem_sync(FROM_DEVICE) on every output after the run, timed with it
};

constexpr uint64_t kCached = RKNN_FLAG_MEMORY_CACHEABLE, kUncached = RKNN_FLAG_MEMORY_NON_CACHEABLE;

const Config kConfigs[] = {
    {"m1_cached_input", 0, kCached, kCached, false},      // B's binding
    {"m1_uncached_input", 0, kUncached, kCached, false},  // like C's uncached dma32 input
    {"no_flush_input", RKNN_FLAG_DISABLE_FLUSH_INPUT_MEM_CACHE, kUncached, kCached, false},
    {"no_flush_output", RKNN_FLAG_DISABLE_FLUSH_OUTPUT_MEM_CACHE, kUncached, kCached, false},
    {"no_flush_output_sync", RKNN_FLAG_DISABLE_FLUSH_OUTPUT_MEM_CACHE, kUncached, kCached, true},
    {"no_flush_both", RKNN_FLAG_DISABLE_FLUSH_INPUT_MEM_CACHE | RKNN_FLAG_DISABLE_FLUSH_OUTPUT_MEM_CACHE, kUncached,
     kCached, false},
    {"uncached_output", 0, kUncached, kUncached, false},
    {"sram", RKNN_FLAG_ENABLE_SRAM, kUncached, kCached, false},
    {"sram_output", RKNN_FLAG_ENABLE_SRAM, kUncached, RKNN_FLAG_MEMORY_TRY_ALLOC_SRAM, false},
};

struct Probe {
    const Config* cfg = nullptr;
    rknn_context ctx = 0;
    rknn_tensor_mem* in = nullptr;
    std::vector<rknn_tensor_mem*> out;
    std::string error;
    int runs_ok = 0, outputs_equal = 0, checked = 0;
    std::vector<double> cpu, wall, post;
};

double clock_ms(clockid_t id) {
    timespec ts{};
    clock_gettime(id, &ts);
    return ts.tv_sec * 1e3 + ts.tv_nsec / 1e6;
}

std::vector<char> read_file(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    CHECK(f.good(), "cannot read %s", path.c_str());
    return std::vector<char>((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
}

bool open_probe(Probe& p, std::vector<char>& model, const NpuContexts& ref) {
    if (rknn_init(&p.ctx, model.data(), model.size(), p.cfg->init_flags, nullptr) != RKNN_SUCC) {
        p.ctx = 0;
        p.error = "rknn_init failed";
        return false;
    }
    if (rknn_set_core_mask(p.ctx, RKNN_NPU_CORE_0) != RKNN_SUCC) {
        p.error = "rknn_set_core_mask failed";
        return false;
    }
    rknn_tensor_attr in = ref.input;
    in.type = RKNN_TENSOR_UINT8;
    in.fmt = RKNN_TENSOR_NHWC;
    in.pass_through = 0;
    p.in = rknn_create_mem2(p.ctx, std::max<uint32_t>(in.size, in.size_with_stride), p.cfg->in_mem);
    if (!p.in || rknn_set_io_mem(p.ctx, p.in, &in) != RKNN_SUCC) {
        p.error = "input binding failed";
        return false;
    }
    for (auto a : ref.outputs) {
        auto* m = rknn_create_mem2(p.ctx, std::max(a.size, a.size_with_stride), p.cfg->out_mem);
        if (m)
            p.out.push_back(m);
        if (!m || rknn_set_io_mem(p.ctx, m, &a) != RKNN_SUCC) {
            p.error = "output binding failed";
            return false;
        }
    }
    return true;
}

bool run(Probe& p) {
    if (rknn_run(p.ctx, nullptr) != RKNN_SUCC)
        return false;
    if (p.cfg->sync_out)
        for (auto* m : p.out)
            if (rknn_mem_sync(p.ctx, m, RKNN_MEMORY_SYNC_FROM_DEVICE) != RKNN_SUCC)
                return false;
    return true;
}

std::vector<std::vector<int8_t>> outputs(const Probe& p, const NpuContexts& ref) {
    std::vector<std::vector<int8_t>> o;
    for (size_t i = 0; i < p.out.size(); i++) {
        auto* b = static_cast<const int8_t*>(p.out[i]->virt_addr);
        o.emplace_back(b, b + ref.outputs[i].size);
    }
    return o;
}

std::string stats(std::vector<double> v) {
    if (v.empty())
        return ",,";
    std::sort(v.begin(), v.end());
    char s[64];
    std::snprintf(s, sizeof s, "%.3f,%.3f,%.3f", v[v.size() / 2], v.front(), v.back());
    return s;
}

}  // namespace

int main(int argc, char** argv) {
    pin_current_thread({4, 5, 6, 7});
    cv::setNumThreads(1);  // PROTOCOL §8
    if (argc < 4)
        die("usage: output_probe <video.mp4> <model.rknn> <out_dir> [rounds=15] [runs=20]");
    const std::string video = argv[1], model_path = argv[2], out_dir = argv[3];
    const int rounds = argc > 4 ? std::atoi(argv[4]) : 15, runs = argc > 5 ? std::atoi(argv[5]) : 20;
    CHECK(rounds >= 1 && runs >= 1, "rounds and runs must be at least 1");
    std::filesystem::create_directories(out_dir);
    const Header header = {
        {"start_time_utc", utc_now("%Y-%m-%dT%H:%M:%SZ")},
        {"ntp_synchronized", command_output("timedatectl show -p NTPSynchronized --value")},
        {"prereg_v1_commit", kPreregCommit},
        {"run_kind", "check"},
        {"model_sha256", command_output("sha256sum '" + model_path + "' | cut -d' ' -f1")},
        {"code_commit", CODE_COMMIT},
        {"amendment", LATEST_AMENDMENT},
        {"source", video},
        {"purpose", "exploration before M2's pre-registration; not a run that counts"},
        {"rounds", std::to_string(rounds)},
        {"runs_per_round", std::to_string(runs)},
    };

    NpuContexts ref;
    ref.open(model_path);
    const int mw = ref.model_w(), mh = ref.model_h();

    // Two frames, 30 apart, letterboxed as A does.
    std::vector<std::vector<uint8_t>> frame;
    std::atomic<bool> stop{false};
    int decoded = 0;
    VideoSource src(video, /*software=*/true, /*paced=*/false);
    src.open(&stop);
    src.run(
        [&](AVFrame* f) {
            if (!stop && (decoded == 0 || decoded == 30)) {
                frame.emplace_back(size_t(mw) * mh * 3, 114);
                letterbox_i420(f->data[0], f->linesize[0], f->data[1], f->linesize[1], f->data[2], f->linesize[2],
                               f->width, f->height, frame.back().data(), mw, mh, opencv_interpolation());
                stop = frame.size() == 2;
            }
            decoded++;
            av_frame_free(&f);
        },
        [] { die("decoder error"); });
    CHECK(frame.size() == 2, "the video has fewer than 31 frames");
    std::vector<std::vector<std::vector<int8_t>>> expected;
    for (const auto& f : frame)
        expected.push_back(ref.reference_outputs(f, model_path));
    CHECK(expected[0] != expected[1], "the two frames give the same outputs");

    std::vector<char> model = read_file(model_path);
    std::vector<Probe> probes;
    for (const auto& c : kConfigs) {
        probes.emplace_back();
        probes.back().cfg = &c;
    }
    for (auto& p : probes) {
        if (!open_probe(p, model, ref))
            continue;
        for (int i = 0; i < 20; i++) {
            std::memcpy(p.in->virt_addr, frame[i % 2].data(), frame[i % 2].size());
            if (!run(p)) {
                p.error = "rknn_run failed";
                break;
            }
            p.checked++;
            p.outputs_equal += outputs(p, ref) == expected[i % 2];
        }
    }

    for (int r = 0; r < rounds; r++) {
        for (auto& p : probes) {
            if (!p.error.empty())
                continue;
            std::memcpy(p.in->virt_addr, frame[r % 2].data(), frame[r % 2].size());
            const double c0 = clock_ms(CLOCK_PROCESS_CPUTIME_ID);
            const int64_t t0 = now_ns();
            for (int i = 0; i < runs; i++)
                p.runs_ok += run(p);
            p.wall.push_back((now_ns() - t0) / 1e6 / runs);
            p.cpu.push_back((clock_ms(CLOCK_PROCESS_CPUTIME_ID) - c0) / runs);
            std::vector<const int8_t*> ptrs;
            for (auto* m : p.out)
                ptrs.push_back(static_cast<const int8_t*>(m->virt_addr));
            const double t1 = clock_ms(CLOCK_THREAD_CPUTIME_ID);
            for (int i = 0; i < runs; i++)
                yolov8_post(ptrs, ref.outputs, mw, mh);
            p.post.push_back((clock_ms(CLOCK_THREAD_CPUTIME_ID) - t1) / runs);
        }
    }

    const std::string path = out_dir + "/output_probe.csv";
    FILE* csv = std::fopen(path.c_str(), "w");
    CHECK(csv, "cannot write %s", path.c_str());
    for (const auto& [k, v] : header)
        std::fprintf(csv, "# %s: %s\n", k.c_str(), v.c_str());
    std::fputs("config,init_flags,input_mem,output_mem,sync_output,error,checked,outputs_equal,runs_ok,"
               "cpu_ms_median,cpu_ms_min,cpu_ms_max,wall_ms_median,wall_ms_min,wall_ms_max,"
               "post_cpu_ms_median,post_cpu_ms_min,post_cpu_ms_max\n",
               csv);
    for (const auto& p : probes) {
        char line[512];
        std::snprintf(line, sizeof line, "%s,0x%x,0x%llx,0x%llx,%d,%s,%d,%d,%d,%s,%s,%s\n", p.cfg->name,
                      p.cfg->init_flags, static_cast<unsigned long long>(p.cfg->in_mem),
                      static_cast<unsigned long long>(p.cfg->out_mem), p.cfg->sync_out, p.error.c_str(), p.checked,
                      p.outputs_equal, p.runs_ok, stats(p.cpu).c_str(), stats(p.wall).c_str(), stats(p.post).c_str());
        std::fputs(line, csv);
        std::fputs(line, stdout);
    }
    CHECK(!std::ferror(csv) && std::fclose(csv) == 0, "writing %s failed", path.c_str());
    std::printf("librknnrt: %s\nwritten: %s\n", ref.sdk_version.c_str(), path.c_str());

    for (auto& p : probes) {
        if (p.in)
            rknn_destroy_mem(p.ctx, p.in);
        for (auto* m : p.out)
            rknn_destroy_mem(p.ctx, m);
        if (p.ctx)
            rknn_destroy(p.ctx);
    }
    ref.close();
    return 0;
}
