// M1-3 NPU capacity (PROTOCOL §6.2). With --with-post: P_cap, the upper bound of the pipeline for
// both variants. Without: the raw NPU limit on 1, 2 or 3 cores, for reference.
//
// The tensor set of tools/dump_tensor is preloaded into dma-heap buffers. Worker w owns the context
// on NPU core w and cycles over the tensors whose index ≡ w (mod workers), each registered with that
// context through rknn_create_mem_from_fd and bound with rknn_set_io_mem. A step is bind and
// rknn_run, plus with --with-post the real post-processing on the bound outputs. There is no
// prepare and no CPU copy. A step counts when it completes inside the window (§7.4).
//
// Usage: calib_npu --tensors FILE [options]
//   --with-post          post-process every output (P_cap)
//   --cores N            workers on NPU cores 0..N-1 (default 3)
//   --fixed-clocks FILE  the output of tools/set_perf_mode.sh: a one-second sample in the window
//                        below one of its values makes the run invalid (PROTOCOL §9.3 rule 1)
//   --warmup S, --duration S, --model PATH, --out DIR, --repetition R, --freeze-commit SHA
//                        as for bench_main
// Writes <out>/summary.csv, samples.csv and env.json, and prints the summary.
// Exit code 0 for a valid run, 1 for an invalid run or an error. Run as root (NPU load, §7.7).
#include <fcntl.h>
#include <linux/dma-buf.h>
#include <linux/dma-heap.h>
#include <pthread.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <string>
#include <thread>
#include <vector>

#include "core/metrics.h"
#include "core/npu.h"
#include "core/report.h"
#include "core/util.h"
#include "core/yolov8_post.h"

#ifndef CODE_COMMIT
#define CODE_COMMIT "unknown"
#endif
#ifndef LATEST_AMENDMENT
#define LATEST_AMENDMENT "none"
#endif

namespace {

constexpr int64_t kSecond = 1000000000;
constexpr const char* kDomains[5] = {"policy0_khz", "policy4_khz", "policy6_khz", "dmc_hz", "npu_hz"};

struct Tensor {
    int fd = -1;
    size_t size = 0;
    void* virt = nullptr;
    rknn_tensor_mem* mem = nullptr;  // in the context of its worker
};

struct WorkerResult {
    int64_t steps = 0;  // completed inside the window
    int64_t detections = 0;
    int64_t bind_ns = 0, run_ns = 0, post_ns = 0;
};

void usage() {
    die("usage: calib_npu --tensors FILE [--with-post] [--cores N] [--fixed-clocks FILE] [--warmup S] "
        "[--duration S] [--model PATH] [--out DIR] [--repetition R] [--freeze-commit SHA]");
}

// The same heaps as C's slots (zero_copy_rga.cpp).
int heap_alloc(const std::string& heap, size_t len) {
    int hfd = open(("/dev/dma_heap/" + heap).c_str(), O_RDONLY | O_CLOEXEC);
    if (hfd < 0)
        return -1;
    dma_heap_allocation_data d{};
    d.len = len;
    d.fd_flags = O_RDWR | O_CLOEXEC;
    int r = ioctl(hfd, DMA_HEAP_IOCTL_ALLOC, &d);
    close(hfd);
    return r < 0 ? -1 : static_cast<int>(d.fd);
}

void dmabuf_sync(int fd, uint64_t flags) {
    dma_buf_sync s{flags};
    CHECK(ioctl(fd, DMA_BUF_IOCTL_SYNC, &s) == 0, "DMA_BUF_IOCTL_SYNC failed");
}

void name_current_thread(const std::string& name) {
    pthread_setname_np(pthread_self(), name.substr(0, 15).c_str());
}

void sleep_until_ns(int64_t t) {
    timespec ts{static_cast<time_t>(t / kSecond), static_cast<long>(t % kSecond)};
    while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, nullptr) != 0) {
    }
}

// "key: value" lines, as tools/set_perf_mode.sh prints them.
std::map<std::string, int64_t> read_fixed_clocks(const std::string& path) {
    std::ifstream f(path);
    CHECK(f.good(), "cannot read %s", path.c_str());
    std::map<std::string, int64_t> m;
    std::string line;
    while (std::getline(f, line)) {
        const size_t p = line.find(": ");
        if (p != std::string::npos)
            m[line.substr(0, p)] = std::stoll(line.substr(p + 2));
    }
    for (const char* d : kDomains)
        CHECK(m.count(d), "%s has no %s", path.c_str(), d);
    return m;
}

std::string fmt(double v, int digits = 3) {
    char b[64];
    std::snprintf(b, sizeof b, "%.*f", digits, v);
    return b;
}

}  // namespace

int main(int argc, char** argv) {
    // Before any library starts a thread, so librknnrt's threads inherit it (PROTOCOL §8).
    pin_current_thread({4, 5, 6, 7});

    std::map<std::string, std::string> a = {{"cores", "3"},     {"warmup", "20"},
                                            {"duration", "120"}, {"model", "models/yolov8n_i8.rknn"},
                                            {"repetition", "1"}, {"freeze-commit", "none"}};
    bool with_post = false;
    std::string args_json = "[";
    for (int i = 1; i < argc; i++) {
        args_json += (i > 1 ? ", " : "") + json_string(argv[i]);
        std::string k = argv[i];
        if (k == "--with-post") {
            with_post = true;
            continue;
        }
        if (k.rfind("--", 0) != 0 || i + 1 >= argc)
            usage();
        a[k.substr(2)] = argv[++i];
        args_json += ", " + json_string(argv[i]);
    }
    args_json += "]";
    if (!a.count("tensors"))
        usage();
    const int cores = std::stoi(a["cores"]);
    CHECK(cores >= 1 && cores <= NpuContexts::kCount, "cores must be 1..3");
    const int warmup_s = std::stoi(a["warmup"]), duration_s = std::stoi(a["duration"]);
    CHECK(warmup_s >= 1 && duration_s >= 1, "warm-up and duration must be at least 1 s");
    const std::string measurement = with_post ? "p_cap" : "npu_raw";
    const std::string out =
        a.count("out") ? a["out"]
                       : "results/calibration/" + utc_now("%Y%m%dT%H%M%SZ") + "_" + measurement + "_" + a["cores"] + "c";
    std::filesystem::create_directories(out);
    std::map<std::string, int64_t> fixed;
    if (a.count("fixed-clocks"))
        fixed = read_fixed_clocks(a["fixed-clocks"]);

    const Header header = {
        {"start_time_utc", utc_now("%Y-%m-%dT%H:%M:%SZ")},
        {"ntp_synchronized", command_output("timedatectl show -p NTPSynchronized --value")},
        {"prereg_v1_commit", kPreregCommit},
        {"freeze_m1_commit", a["freeze-commit"]},
        {"run_kind", "calibration"},
        {"model_sha256", command_output("sha256sum '" + a["model"] + "' | cut -d' ' -f1")},
        {"decoder", "none (preloaded tensors)"},
        {"variant", "n/a (PROTOCOL §6.2)"},
        {"channels", "n/a"},
        {"repetition", a["repetition"]},
        {"code_commit", CODE_COMMIT},
        {"amendment", LATEST_AMENDMENT},
        {"measurement", measurement},
        {"cores", a["cores"]},
        {"warmup_s", a["warmup"]},
        {"duration_s", a["duration"]},
        {"tensors", a["tensors"]},
    };

    // One fd and one mapping per tensor: raise the soft fd limit to the hard one.
    rlimit lim{};
    CHECK(getrlimit(RLIMIT_NOFILE, &lim) == 0, "getrlimit failed");
    lim.rlim_cur = lim.rlim_max;
    CHECK(setrlimit(RLIMIT_NOFILE, &lim) == 0, "setrlimit failed");

    NpuContexts npu;
    npu.open(a["model"]);
    const int mw = npu.model_w(), mh = npu.model_h();
    const size_t tensor_bytes = size_t(mw) * mh * 3;
    rknn_tensor_attr in_attr = npu.input;  // as C binds its slots
    in_attr.type = RKNN_TENSOR_UINT8;
    in_attr.fmt = RKNN_TENSOR_NHWC;
    in_attr.pass_through = 0;
    const size_t size = std::max<size_t>(in_attr.size_with_stride, tensor_bytes);

    const uintmax_t file_size = std::filesystem::file_size(a["tensors"]);
    CHECK(file_size % tensor_bytes == 0, "%s is not a whole number of %zu-byte tensors", a["tensors"].c_str(),
          tensor_bytes);
    const int count = static_cast<int>(file_size / tensor_bytes);
    CHECK(count >= cores, "fewer tensors than workers");
    std::vector<Tensor> tensors(count);
    std::vector<std::vector<uint8_t>> first(cores);  // each worker's first tensor, for the check below
    std::string heap = "system-uncached-dma32";
    {
        FILE* f = std::fopen(a["tensors"].c_str(), "rb");
        CHECK(f, "cannot read %s", a["tensors"].c_str());
        std::vector<uint8_t> buf(tensor_bytes);
        for (int i = 0; i < count; i++) {
            CHECK(std::fread(buf.data(), 1, tensor_bytes, f) == tensor_bytes, "short read in %s",
                  a["tensors"].c_str());
            Tensor& t = tensors[i];
            t.fd = heap_alloc(heap, size);
            if (t.fd < 0 && i == 0) {
                heap = "system-dma32";
                t.fd = heap_alloc(heap, size);
            }
            CHECK(t.fd >= 0, "dma-heap allocation of tensor %d failed", i);
            t.size = size;
            t.virt = mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_SHARED, t.fd, 0);
            CHECK(t.virt != MAP_FAILED, "mmap of tensor %d failed", i);
            dmabuf_sync(t.fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_WRITE);
            std::memcpy(t.virt, buf.data(), tensor_bytes);
            std::memset(static_cast<uint8_t*>(t.virt) + tensor_bytes, 0, size - tensor_bytes);
            dmabuf_sync(t.fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_WRITE);
            const int w = i % cores;
            t.mem = rknn_create_mem_from_fd(npu.ctx[w], t.fd, t.virt, static_cast<uint32_t>(size), 0);
            CHECK(t.mem, "rknn_create_mem_from_fd of tensor %d failed", i);
            if (i < cores)
                first[w] = buf;
        }
        std::fclose(f);
    }

    // Every worker's binding gives the runtime's own outputs for its first tensor (AMENDMENTS A3).
    bool outputs_match = true;
    for (int w = 0; w < cores; w++) {
        rknn_tensor_attr attr = in_attr;
        CHECK(rknn_set_io_mem(npu.ctx[w], tensors[w].mem, &attr) == RKNN_SUCC, "rknn_set_io_mem(input) failed");
        outputs_match &= npu.run_bound(w) == npu.reference_outputs(first[w], a["model"]);
    }
    CHECK(outputs_match, "bound outputs differ from rknn_outputs_get");

    std::atomic<int64_t> completed[NpuContexts::kCount] = {};  // all steps so far, for the samples
    std::vector<WorkerResult> results(cores);
    std::vector<Sample> samples;
    CpuSnapshot cpu_w, cpu_e;
    const int64_t t_start = now_ns();
    const int64_t t_w = t_start + warmup_s * kSecond, t_e = t_w + duration_s * kSecond;
    std::fprintf(stderr, "calib_npu: %s, %d cores, %d tensors in %s, warm-up %d s, window %d s -> %s\n",
                 measurement.c_str(), cores, count, heap.c_str(), warmup_s, duration_s, out.c_str());

    std::vector<std::thread> workers;
    for (int w = 0; w < cores; w++) {
        workers.emplace_back([&, w] {
            name_current_thread("npu-" + std::to_string(w));
            std::vector<const Tensor*> mine;
            for (int i = w; i < count; i += cores)
                mine.push_back(&tensors[i]);
            WorkerResult& r = results[w];
            for (size_t j = 0;; j++) {
                const int64_t t0 = now_ns();
                rknn_tensor_attr attr = in_attr;  // per call: the runtime takes a non-const pointer
                CHECK(rknn_set_io_mem(npu.ctx[w], mine[j % mine.size()]->mem, &attr) == RKNN_SUCC,
                      "rknn_set_io_mem(input) failed");
                const int64_t t1 = now_ns();
                CHECK(rknn_run(npu.ctx[w], nullptr) == RKNN_SUCC, "rknn_run failed");
                const int64_t t2 = now_ns();
                size_t dets = 0;
                if (with_post)
                    dets = yolov8_post(npu.output_ptrs(w), npu.outputs, mw, mh).size();
                const int64_t t3 = now_ns();
                completed[w]++;
                if (t3 >= t_w && t3 < t_e) {
                    r.steps++;
                    r.detections += static_cast<int64_t>(dets);
                    r.bind_ns += t1 - t0;
                    r.run_ns += t2 - t1;
                    r.post_ns += t3 - t2;
                }
                if (t3 >= t_e)
                    break;
            }
        });
    }
    std::thread metrics([&] {
        name_current_thread("metrics");
        pin_current_thread({0});
        for (int64_t tick = t_start + kSecond;; tick += kSecond) {
            sleep_until_ns(tick);
            std::vector<int64_t> done(cores);
            for (int w = 0; w < cores; w++)
                done[w] = completed[w].load();
            samples.push_back(take_sample(done));
            if (tick == t_w)
                cpu_w = take_cpu_snapshot();
            if (tick == t_e) {
                cpu_e = take_cpu_snapshot();
                break;
            }
        }
    });
    metrics.join();
    for (auto& t : workers)
        t.join();

    // ---- summary ----
    const size_t w_idx = warmup_s - 1, e_idx = warmup_s + duration_s - 1;  // sample k at t_start + (k + 1) s
    CHECK(samples.size() == e_idx + 1, "missing samples");
    WorkerResult total;
    for (const auto& r : results) {
        total.steps += r.steps;
        total.detections += r.detections;
        total.bind_ns += r.bind_ns;
        total.run_ns += r.run_ns;
        total.post_ns += r.post_ns;
    }
    CHECK(total.steps > 0, "no step completed inside the window");
    Header s = {{"measurement", measurement}, {"cores", a["cores"]}, {"tensor_count", std::to_string(count)}};
    s.push_back({"completed_in_window", std::to_string(total.steps)});
    s.push_back({"inferences_per_s", fmt(double(total.steps) / duration_s, 4)});
    for (int w = 0; w < cores; w++)
        s.push_back({"completed_core" + std::to_string(w), std::to_string(results[w].steps)});
    if (with_post)
        s.push_back({"detections_in_window", std::to_string(total.detections)});
    s.push_back({"bind_ms_mean", fmt(total.bind_ns / 1e6 / total.steps)});
    s.push_back({"run_ms_mean", fmt(total.run_ns / 1e6 / total.steps)});
    if (with_post)
        s.push_back({"post_ms_mean", fmt(total.post_ns / 1e6 / total.steps)});
    s.push_back({"process_cpu_s", fmt(cpu_e.process_s - cpu_w.process_s)});
    for (int c = 0; c < 3; c++) {
        double load = 0;
        for (size_t j = w_idx; j <= e_idx; j++)
            load += samples[j].npu_load[c];
        s.push_back({"npu_load_mean_core" + std::to_string(c), fmt(load / double(e_idx - w_idx + 1), 1)});
    }

    int64_t below = 0;
    for (int q = 0; q < 5; q++) {
        int64_t lo = INT64_MAX, hi = 0;
        for (size_t j = w_idx; j <= e_idx; j++) {
            lo = std::min(lo, samples[j].freq[q]);
            hi = std::max(hi, samples[j].freq[q]);
            if (!fixed.empty() && samples[j].freq[q] < fixed[kDomains[q]])
                below++;
        }
        s.push_back({std::string("freq_min_") + kDomains[q], std::to_string(lo)});
        s.push_back({std::string("freq_max_") + kDomains[q], std::to_string(hi)});
    }
    const std::vector<std::string> zones = thermal_zone_names();
    int temp_max = 0, at_trip = 0;
    for (size_t z = 0; z < zones.size(); z++) {
        std::ifstream f("/sys/class/thermal/thermal_zone" + std::to_string(z) + "/trip_point_0_temp");
        int trip = 0;
        const bool has_trip = static_cast<bool>(f >> trip);
        for (size_t j = w_idx; j <= e_idx; j++) {
            temp_max = std::max(temp_max, samples[j].temp_mc[z]);
            at_trip += has_trip && samples[j].temp_mc[z] >= trip;
        }
    }
    s.push_back({"temp_max_mc", std::to_string(temp_max)});
    s.push_back({"samples_at_or_above_trip0", std::to_string(at_trip)});  // a warning, not invalidity (§9.3)
    const bool valid = below == 0;
    s.push_back({"clock_check", fixed.empty() ? "not checked" : a["fixed-clocks"]});
    s.push_back({"clock_samples_below_fixed", fixed.empty() ? "n/a" : std::to_string(below)});
    s.push_back({"valid", fixed.empty() ? "unknown" : valid ? "1" : "0"});

    auto open_csv = [&](const std::string& name) {
        std::ofstream f(out + "/" + name);
        CHECK(f.good(), "cannot write %s/%s", out.c_str(), name.c_str());
        for (const auto& [k, v] : header)
            f << "# " << k << ": " << v << "\n";
        return f;
    };
    {
        auto f = open_csv("summary.csv");
        f << "key,value\n";
        for (const auto& [k, v] : s)
            f << k << ',' << v << '\n';
    }
    {
        auto f = open_csv("samples.csv");
        f << "t_s,npu_load0,npu_load1,npu_load2";
        for (const auto& z : zones)
            f << ",temp_mc_" << z;
        f << ",freq_policy0_khz,freq_policy4_khz,freq_policy6_khz,freq_dmc_hz,freq_npu_hz,rss_kb";
        for (int w = 0; w < cores; w++)
            f << ",completed_core" << w;
        f << '\n';
        for (const auto& x : samples) {
            f << fmt((x.t_ns - t_start) / 1e9) << ',' << x.npu_load[0] << ',' << x.npu_load[1] << ','
              << x.npu_load[2];
            for (int t : x.temp_mc)
                f << ',' << t;
            for (int64_t q : x.freq)
                f << ',' << q;
            f << ',' << x.rss_kb;
            for (int64_t d : x.decoded)
                f << ',' << d;
            f << '\n';
        }
    }
    {
        std::ofstream f(out + "/env.json");
        CHECK(f.good(), "cannot write env.json");
        f << "{\n";
        for (const auto& [k, v] : header)
            f << "  " << json_string("header_" + k) << ": " << json_string(v) << ",\n";
        f << "  \"args\": " << args_json << ",\n  \"threads_at_window_start\": [";
        bool first_thread = true;
        for (const auto& [tid, t] : cpu_w.threads) {
            f << (first_thread ? "\n" : ",\n") << "    {\"tid\": " << tid << ", \"comm\": " << json_string(t.comm)
              << ", \"affinity\": " << json_string(t.affinity) << "}";
            first_thread = false;
        }
        f << "\n  ],\n  \"rknn\": {\"init_flags\": " << NpuContexts::kInitFlags
          << ", \"sdk\": " << json_string(npu.sdk_version)
          << ", \"core_masks\": [1, 2, 4], \"workers\": " << cores
          << ", \"input_binding\": \"UINT8 NHWC rknn_create_mem_from_fd per tensor, rknn_set_io_mem per step\""
          << ", \"output_binding\": \"NCHW INT8 rknn_create_mem + rknn_set_io_mem\"},\n"
          << "  \"tensors\": {\"heap\": " << json_string(heap) << ", \"count\": " << count
          << ", \"buffer_bytes\": " << size << "},\n"
          << "  \"bound_outputs_match_rknn_outputs_get\": " << (outputs_match ? "true" : "false") << "\n}\n";
    }

    for (const auto& [k, v] : s)
        std::printf("%s: %s\n", k.c_str(), v.c_str());
    std::printf("results: %s\n", out.c_str());

    for (int i = 0; i < count; i++)
        rknn_destroy_mem(npu.ctx[i % cores], tensors[i].mem);
    for (auto& t : tensors) {
        munmap(t.virt, t.size);
        close(t.fd);
    }
    npu.close();
    return valid ? 0 : 1;
}
