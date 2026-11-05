// Diagnostic before M2's pre-registration, not a run that counts: output_probe found that a new
// context whose input memory is uncached (rknn_create_mem2 NON_CACHEABLE, written by the CPU) gave
// outputs unlike rknn_outputs_get in its first 2 to 5 runs, and right ones after; with a cacheable
// input it never did. This looks at those first runs, and at C's own path, where RGA writes the input
// into uncached dma32 slots registered with rknn_create_mem_from_fd.
//
// A. CPU-written input, one new context on NPU core 0 per case, 10 runs each (+ 6 after a rebind):
//      cached_alternating, uncached_alternating   the two frames in turn
//      uncached_same_frame                        frame 0 every run
//      uncached_rebind                            alternating; then the input bound again, 6 more runs
//      uncached_pad_warmup                        5 runs on an all-114 input first, then alternating
//      uncached_wait_10ms                         alternating, 10 ms between the write and rknn_run
//      uncached_sync_first                        rknn_mem_sync(TO_DEVICE) on the input once, before
//                                                 the first write; then alternating
//      uncached_sync_each                         alternating, rknn_mem_sync(TO_DEVICE) after each write
//      cached_pad                                 a cacheable input, all 114, every run
//    Each run: whether the outputs equal rknn_outputs_get of a separate context on that input, else
//    whether they equal the previous run's outputs, those of an all-zero input or of an all-114 input,
//    and how many output bytes differ (and by how much at most); then the input memory is read back
//    and the bytes that no longer hold what the CPU wrote are counted, and how many of them are 0.
// B. C's path (zero_copy_rga, 2 slots), new NpuContexts: the first 10 decoded frames, each prepared
//    by RGA and run on contexts 0, 1 and 2; each output compared with rknn_outputs_get on the slot as
//    read back after the runs.
//
// Usage: first_runs_probe <video.mp4> <model.rknn> <out_dir>   (as root)
//   <out_dir>/first_runs.csv  "# key: value" header lines (PROTOCOL §1.4, run kind check), then one row
//                             per run
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <memory>
#include <string>
#include <thread>
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

using Outputs = std::vector<std::vector<int8_t>>;

struct Case {
    const char* name;
    uint64_t in_mem;
    bool same_frame, rebind, pad_warmup, wait, sync_first, sync_each, pad_only;
};

constexpr uint64_t kCached = RKNN_FLAG_MEMORY_CACHEABLE, kUncached = RKNN_FLAG_MEMORY_NON_CACHEABLE;

const Case kCases[] = {
    {"cached_alternating", kCached, false, false, false, false, false, false, false},
    {"uncached_alternating", kUncached, false, false, false, false, false, false, false},
    {"uncached_same_frame", kUncached, true, false, false, false, false, false, false},
    {"uncached_rebind", kUncached, false, true, false, false, false, false, false},
    {"uncached_pad_warmup", kUncached, false, false, true, false, false, false, false},
    {"uncached_wait_10ms", kUncached, false, false, false, true, false, false, false},
    {"uncached_sync_first", kUncached, false, false, false, false, true, false, false},
    {"uncached_sync_each", kUncached, false, false, false, false, false, true, false},
    {"cached_pad", kCached, false, false, false, false, false, false, true},
};

std::vector<char> read_file(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    CHECK(f.good(), "cannot read %s", path.c_str());
    return std::vector<char>((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
}

Outputs read_outputs(const std::vector<rknn_tensor_mem*>& mem, const NpuContexts& ref) {
    Outputs o;
    for (size_t i = 0; i < mem.size(); i++) {
        auto* b = static_cast<const int8_t*>(mem[i]->virt_addr);
        o.emplace_back(b, b + ref.outputs[i].size);
    }
    return o;
}

// "equal" or what the outputs equal instead, the differing bytes and the largest difference.
std::string compare(const Outputs& got, const Outputs& want, const Outputs& prev, const Outputs& zero,
                    const Outputs& pad) {
    long ndiff = 0;
    int maxabs = 0;
    for (size_t i = 0; i < got.size(); i++)
        for (size_t j = 0; j < got[i].size(); j++)
            if (got[i][j] != want[i][j]) {
                ndiff++;
                maxabs = std::max(maxabs, std::abs(int(got[i][j]) - int(want[i][j])));
            }
    const char* what = ndiff == 0        ? "equal"
                       : got == prev     ? "previous_outputs"
                       : got == zero     ? "zero_input_outputs"
                       : got == pad      ? "pad_input_outputs"
                                         : "other";
    return std::string(what) + "," + std::to_string(ndiff) + "," + std::to_string(maxabs);
}

}  // namespace

int main(int argc, char** argv) {
    pin_current_thread({4, 5, 6, 7});
    cv::setNumThreads(1);  // PROTOCOL §8
    if (argc < 4)
        die("usage: first_runs_probe <video.mp4> <model.rknn> <out_dir>");
    const std::string video = argv[1], model_path = argv[2], out_dir = argv[3];
    std::filesystem::create_directories(out_dir);
    const Header header = {
        {"start_time_utc", utc_now("%Y-%m-%dT%H:%M:%SZ")},
        {"ntp_synchronized", command_output("timedatectl show -p NTPSynchronized --value")},
        {"prereg_v1_commit", kPreregCommit},
        {"run_kind", "check"},
        {"model_sha256", command_output("sha256sum '" + model_path + "' | cut -d' ' -f1")},
        {"decoder", std::string("h264_rkmpp, ffmpeg ") + av_version_info()},
        {"code_commit", CODE_COMMIT},
        {"amendment", LATEST_AMENDMENT},
        {"source", video},
        {"purpose", "diagnostic before M2's pre-registration; not a run that counts"},
    };
    const std::string path = out_dir + "/first_runs.csv";
    FILE* csv = std::fopen(path.c_str(), "w");
    CHECK(csv, "cannot write %s", path.c_str());
    for (const auto& [k, v] : header)
        std::fprintf(csv, "# %s: %s\n", k.c_str(), v.c_str());
    std::fputs("part,case,run,context,frame,result,differing_bytes,largest_difference,input_changed_bytes,"
               "input_changed_to_zero\n",
               csv);
    auto row = [&](const std::string& r) {
        std::fputs((r + "\n").c_str(), csv);
        std::puts(r.c_str());
    };

    NpuContexts ref;
    ref.open(model_path);
    const int mw = ref.model_w(), mh = ref.model_h();
    const size_t bytes = size_t(mw) * mh * 3;

    // ---- A: CPU-written input ----
    std::vector<std::vector<uint8_t>> frame;
    {
        std::atomic<bool> stop{false};
        int decoded = 0;
        VideoSource src(video, /*software=*/true, /*paced=*/false);
        src.open(&stop);
        src.run(
            [&](AVFrame* f) {
                if (!stop && (decoded == 0 || decoded == 30)) {
                    frame.emplace_back(bytes, 114);
                    letterbox_i420(f->data[0], f->linesize[0], f->data[1], f->linesize[1], f->data[2],
                                   f->linesize[2], f->width, f->height, frame.back().data(), mw, mh,
                                   opencv_interpolation());
                    stop = frame.size() == 2;
                }
                decoded++;
                av_frame_free(&f);
            },
            [] { die("decoder error"); });
    }
    CHECK(frame.size() == 2, "the video has fewer than 31 frames");
    const std::vector<uint8_t> zeros(bytes, 0), pads(bytes, 114);
    const Outputs expected[2] = {ref.reference_outputs(frame[0], model_path), ref.reference_outputs(frame[1], model_path)};
    const Outputs zero_out = ref.reference_outputs(zeros, model_path), pad_out = ref.reference_outputs(pads, model_path);

    std::vector<char> model = read_file(model_path);
    rknn_tensor_attr in_attr = ref.input;
    in_attr.type = RKNN_TENSOR_UINT8;
    in_attr.fmt = RKNN_TENSOR_NHWC;
    in_attr.pass_through = 0;
    for (const auto& c : kCases) {
        rknn_context ctx = 0;
        CHECK(rknn_init(&ctx, model.data(), model.size(), 0, nullptr) == RKNN_SUCC, "rknn_init failed");
        CHECK(rknn_set_core_mask(ctx, RKNN_NPU_CORE_0) == RKNN_SUCC, "rknn_set_core_mask failed");
        auto* in = rknn_create_mem2(ctx, std::max<uint32_t>(in_attr.size_with_stride, uint32_t(bytes)), c.in_mem);
        CHECK(in && rknn_set_io_mem(ctx, in, &in_attr) == RKNN_SUCC, "input binding failed");
        std::vector<rknn_tensor_mem*> out;
        for (auto a : ref.outputs) {
            out.push_back(rknn_create_mem(ctx, std::max(a.size, a.size_with_stride)));
            CHECK(out.back() && rknn_set_io_mem(ctx, out.back(), &a) == RKNN_SUCC, "output binding failed");
        }
        Outputs prev = read_outputs(out, ref);
        // The input as read back after the run: bytes that no longer hold what was written, and how many
        // of those are 0.
        auto input_check = [&](const std::vector<uint8_t>& written) {
            const auto* b = static_cast<const uint8_t*>(in->virt_addr);
            long changed = 0, zero = 0;
            for (size_t j = 0; j < bytes; j++)
                if (b[j] != written[j]) {
                    changed++;
                    zero += b[j] == 0;
                }
            return "," + std::to_string(changed) + "," + std::to_string(zero);
        };
        if (c.sync_first)
            CHECK(rknn_mem_sync(ctx, in, RKNN_MEMORY_SYNC_TO_DEVICE) == RKNN_SUCC, "rknn_mem_sync failed");
        if (c.pad_warmup) {
            std::memcpy(in->virt_addr, pads.data(), bytes);
            for (int i = 0; i < 5; i++) {
                CHECK(rknn_run(ctx, nullptr) == RKNN_SUCC, "rknn_run failed");
                Outputs got = read_outputs(out, ref);
                row(std::string("A,") + c.name + ",warmup" + std::to_string(i) + ",0,pad," +
                    compare(got, pad_out, prev, zero_out, pad_out) + input_check(pads));
                prev = std::move(got);
            }
        }
        const int runs = c.rebind ? 16 : 10;
        for (int i = 0; i < runs; i++) {
            if (c.rebind && i == 10)
                CHECK(rknn_set_io_mem(ctx, in, &in_attr) == RKNN_SUCC, "input binding failed");
            const int fi = c.same_frame ? 0 : i % 2;
            const auto& data = c.pad_only ? pads : frame[fi];
            std::memcpy(in->virt_addr, data.data(), bytes);
            if (c.sync_each)
                CHECK(rknn_mem_sync(ctx, in, RKNN_MEMORY_SYNC_TO_DEVICE) == RKNN_SUCC, "rknn_mem_sync failed");
            if (c.wait)
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
            CHECK(rknn_run(ctx, nullptr) == RKNN_SUCC, "rknn_run failed");
            Outputs got = read_outputs(out, ref);
            row(std::string("A,") + c.name + "," + std::to_string(i) + ",0," +
                (c.pad_only ? std::string("pad") : std::to_string(fi)) + "," +
                compare(got, c.pad_only ? pad_out : expected[fi], prev, zero_out, pad_out) + input_check(data));
            prev = std::move(got);
        }
        for (auto* m : out)
            rknn_destroy_mem(ctx, m);
        rknn_destroy_mem(ctx, in);
        rknn_destroy(ctx);
    }

    // ---- B: C's path, RGA into uncached dma32 slots ----
    {
        NpuContexts npu;
        npu.open(model_path);
        auto c = make_zero_copy_rga(model_path, rga_interpolation());
        c->init(npu, 2);
        std::vector<uint8_t> tensor(bytes);
        Outputs prev[NpuContexts::kCount];
        for (int k = 0; k < NpuContexts::kCount; k++)
            prev[k] = read_outputs(npu.out_mem[k], ref);
        std::atomic<bool> stop{false};
        int frames = 0;
        VideoSource src(video, /*software=*/false, /*paced=*/false);
        src.open(&stop);
        src.run(
            [&](AVFrame* f) {
                if (frames < 10) {
                    const int slot = frames % 2;
                    c->prepare(f, 0, slot);
                    Outputs got[NpuContexts::kCount];
                    for (int k = 0; k < NpuContexts::kCount; k++) {
                        c->bind(npu, k, slot);
                        got[k] = npu.run_bound(k);
                    }
                    c->read_slot(slot, tensor.data());
                    const Outputs want = ref.reference_outputs(tensor, model_path);
                    for (int k = 0; k < NpuContexts::kCount; k++) {
                        row("B,c_rga," + std::to_string(frames) + "," + std::to_string(k) + "," +
                            std::to_string(frames) + "," + compare(got[k], want, prev[k], zero_out, pad_out) +
                            ",,");
                        prev[k] = got[k];
                    }
                    if (++frames == 10)
                        stop = true;
                }
                av_frame_free(&f);
            },
            [] { die("decoder error"); });
        c->shutdown(npu);
        npu.close();
    }
    CHECK(!std::ferror(csv) && std::fclose(csv) == 0, "writing %s failed", path.c_str());
    std::printf("librknnrt: %s\nwritten: %s\n", ref.sdk_version.c_str(), path.c_str());
    ref.close();
    return 0;
}
