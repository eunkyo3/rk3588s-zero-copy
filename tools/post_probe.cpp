// Prototype before M2's pre-registration, not a run that counts: how much of yolov8_post's time a
// NEON version of its scan saves, and whether its detections are identical.
//
// The scalar post-processing (core/yolov8_post.cpp, M1's) visits every cell: it skips a cell whose
// score sum is below the threshold, else takes the best of its 80 class scores, read with a stride
// of the grid size. The NEON version does the same for 16 adjacent cells at once: one compare of 16
// score sums, and, only when one of them passes, 80 loads of 16 contiguous class scores with a
// running per-lane maximum. A lane's class is the first one strictly above the running best, as in
// the scalar loop, so the candidates, their order, the box decoding (scalar) and the NMS are the same.
//
// The frames of the video are decoded by the hardware decoder and prepared as B does, into context 0
// (NPU core 0, M1's bindings). For each frame the context runs twice on the same input; after one run
// the scalar version is timed, after the other the NEON one (thread CPU per call), the order changing
// with each frame, so both read outputs the runtime has just written. Their detections must be equal,
// field for field. A third run times the parts of the NEON version (monotonic clock, so with the
// clock reads' own cost): the scan, the box decoding of the candidates, and the sort with the NMS.
//
// Usage: post_probe <video.mp4> <model.rknn> <out_dir> [frames=300]   (as root, clocks fixed)
//   <out_dir>/post_probe.csv  "# key: value" header lines (PROTOCOL §1.4, run kind check), then key,value
#include <arm_neon.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <memory>
#include <stdexcept>
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
#include "preprocess/backend.h"
#include "preprocess/cpu_common.h"

#ifndef CODE_COMMIT
#define CODE_COMMIT "unknown"
#endif
#ifndef LATEST_AMENDMENT
#define LATEST_AMENDMENT "none"
#endif

namespace {

constexpr int kNumClasses = 80;

// As in core/yolov8_post.cpp.
int8_t quantize(float v, int32_t zp, float scale) {
    float q = std::round(v / scale + zp);
    return static_cast<int8_t>(std::clamp(q, -128.0f, 127.0f));
}

float dequantize(int8_t q, int32_t zp, float scale) {
    return (static_cast<float>(q) - zp) * scale;
}

void decode_dfl(const float* bins, int dfl_len, float out[4]) {
    for (int side = 0; side < 4; side++) {
        const float* b = bins + side * dfl_len;
        float max_v = *std::max_element(b, b + dfl_len);
        float sum = 0.0f, acc = 0.0f;
        for (int i = 0; i < dfl_len; i++) {
            float e = std::exp(b[i] - max_v);
            sum += e;
            acc += e * i;
        }
        out[side] = acc / sum;
    }
}

float iou(const Detection& a, const Detection& b) {
    float w = std::max(0.0f, std::min(a.x2, b.x2) - std::max(a.x1, b.x1));
    float h = std::max(0.0f, std::min(a.y2, b.y2) - std::max(a.y1, b.y1));
    float inter = w * h;
    float uni = (a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - inter;
    return uni > 0.0f ? inter / uni : 0.0f;
}

struct Counts {
    long cells = 0, blocks = 0, blocks_scanned = 0, cells_passing = 0, candidates = 0;
    bool profile = false;  // time the parts below
    int64_t branch_ns = 0, decode_ns = 0, nms_ns = 0;
};

void decode_branch_neon(const int8_t* box, const rknn_tensor_attr& box_attr, const int8_t* cls,
                        const rknn_tensor_attr& cls_attr, const int8_t* sum, const rknn_tensor_attr& sum_attr,
                        int model_h, float conf_thresh, std::vector<Detection>& out, Counts& n) {
    const int grid_h = box_attr.dims[2];
    const int grid_w = box_attr.dims[3];
    const int grid_len = grid_h * grid_w;
    const int dfl_len = box_attr.dims[1] / 4;
    const float stride = static_cast<float>(model_h) / grid_h;
    const int8_t cls_thresh = quantize(conf_thresh, cls_attr.zp, cls_attr.scale);
    const int8_t sum_thresh = quantize(conf_thresh, sum_attr.zp, sum_attr.scale);
    if (grid_len % 16)
        throw std::runtime_error("post_probe: grid size not a multiple of 16");

    std::vector<float> bins(4 * dfl_len);
    const int8x16_t sum_t = vdupq_n_s8(sum_thresh), cls_t = vdupq_n_s8(cls_thresh);
    alignas(16) int8_t best_v[16];
    alignas(16) uint8_t best_c[16], pass_v[16];
    n.cells += grid_len;
    const int64_t t_branch = n.profile ? now_ns() : 0;
    for (int cell0 = 0; cell0 < grid_len; cell0 += 16) {
        n.blocks++;
        const uint8x16_t pass = vcgeq_s8(vld1q_s8(sum + cell0), sum_t);  // not below the threshold
        if (vmaxvq_u8(pass) == 0)
            continue;
        n.blocks_scanned++;
        int8x16_t best = cls_t;
        uint8x16_t best_cls = vdupq_n_u8(0xff);
        for (int c = 0; c < kNumClasses; c++) {
            const int8x16_t v = vld1q_s8(cls + c * grid_len + cell0);
            const uint8x16_t gt = vcgtq_s8(v, best);
            best = vbslq_s8(gt, v, best);
            best_cls = vbslq_u8(gt, vdupq_n_u8(static_cast<uint8_t>(c)), best_cls);
        }
        vst1q_s8(best_v, best);
        vst1q_u8(best_c, best_cls);
        vst1q_u8(pass_v, pass);
        for (int lane = 0; lane < 16; lane++) {
            if (!pass_v[lane])
                continue;
            n.cells_passing++;
            if (best_c[lane] == 0xff)
                continue;
            n.candidates++;
            const int64_t t_decode = n.profile ? now_ns() : 0;
            const int cell = cell0 + lane, y = cell / grid_w, x = cell % grid_w;
            for (int k = 0; k < 4 * dfl_len; k++)
                bins[k] = dequantize(box[k * grid_len + cell], box_attr.zp, box_attr.scale);
            float d[4];
            decode_dfl(bins.data(), dfl_len, d);
            out.push_back({best_c[lane], dequantize(best_v[lane], cls_attr.zp, cls_attr.scale),
                           (x + 0.5f - d[0]) * stride, (y + 0.5f - d[1]) * stride, (x + 0.5f + d[2]) * stride,
                           (y + 0.5f + d[3]) * stride});
            if (n.profile)
                n.decode_ns += now_ns() - t_decode;
        }
    }
    if (n.profile)
        n.branch_ns += now_ns() - t_branch;
}

// yolov8_post with the scan of decode_branch_neon; the checks of the outputs' layout are those of
// yolov8_post, which runs on the same outputs first.
std::vector<Detection> yolov8_post_neon(const std::vector<const int8_t*>& outputs,
                                        const std::vector<rknn_tensor_attr>& attrs, int model_h, Counts& n,
                                        float conf_thresh = 0.25f, float nms_thresh = 0.45f) {
    std::vector<Detection> cand;
    for (int b = 0; b < 3; b++) {
        const int i = b * 3;
        decode_branch_neon(outputs[i], attrs[i], outputs[i + 1], attrs[i + 1], outputs[i + 2], attrs[i + 2], model_h,
                           conf_thresh, cand, n);
    }
    const int64_t t_nms = n.profile ? now_ns() : 0;
    std::stable_sort(cand.begin(), cand.end(), [](const Detection& a, const Detection& b) { return a.score > b.score; });
    std::vector<Detection> kept;
    std::vector<bool> removed(cand.size(), false);
    for (size_t i = 0; i < cand.size(); i++) {
        if (removed[i])
            continue;
        kept.push_back(cand[i]);
        for (size_t j = i + 1; j < cand.size(); j++) {
            if (!removed[j] && cand[j].cls == cand[i].cls && iou(cand[i], cand[j]) > nms_thresh)
                removed[j] = true;
        }
    }
    if (n.profile)
        n.nms_ns += now_ns() - t_nms;
    return kept;
}

bool same(const std::vector<Detection>& a, const std::vector<Detection>& b) {
    if (a.size() != b.size())
        return false;
    for (size_t i = 0; i < a.size(); i++)
        if (a[i].cls != b[i].cls || a[i].score != b[i].score || a[i].x1 != b[i].x1 || a[i].y1 != b[i].y1 ||
            a[i].x2 != b[i].x2 || a[i].y2 != b[i].y2)
            return false;
    return true;
}

double thread_cpu_ms() {
    timespec ts{};
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts);
    return ts.tv_sec * 1e3 + ts.tv_nsec / 1e6;
}

double nearest_rank(std::vector<double> v, double p) {
    std::sort(v.begin(), v.end());
    return v[std::min(v.size() - 1, static_cast<size_t>(p * (v.size() - 1) + 0.5))];
}

}  // namespace

int main(int argc, char** argv) {
    pin_current_thread({4, 5, 6, 7});
    cv::setNumThreads(1);  // PROTOCOL §8
    if (argc < 4)
        die("usage: post_probe <video.mp4> <model.rknn> <out_dir> [frames=300]");
    const std::string video = argv[1], model = argv[2], out_dir = argv[3];
    const int max_frames = argc > 4 ? std::atoi(argv[4]) : 300;
    CHECK(max_frames >= 1, "frames must be at least 1");
    std::filesystem::create_directories(out_dir);
    const Header header = {
        {"start_time_utc", utc_now("%Y-%m-%dT%H:%M:%SZ")},
        {"ntp_synchronized", command_output("timedatectl show -p NTPSynchronized --value")},
        {"prereg_v1_commit", kPreregCommit},
        {"run_kind", "check"},
        {"model_sha256", command_output("sha256sum '" + model + "' | cut -d' ' -f1")},
        {"decoder", std::string("h264_rkmpp, ffmpeg ") + av_version_info()},
        {"code_commit", CODE_COMMIT},
        {"amendment", LATEST_AMENDMENT},
        {"source", video},
        {"purpose", "NEON prototype before M2's pre-registration; not a run that counts"},
    };

    NpuContexts npu;
    npu.open(model);
    auto b = make_cpu_copy(opencv_interpolation());
    b->init(npu, 1);
    const int mw = npu.model_w(), mh = npu.model_h();

    std::vector<double> scalar_ms, neon_ms;
    long identical = 0, detections = 0;
    Counts n, profiled;
    int64_t prof_total_ns = 0;
    std::atomic<bool> stop{false};
    int frames = 0;
    VideoSource src(video, /*software=*/false, /*paced=*/false);
    src.open(&stop);
    src.run(
        [&](AVFrame* f) {
            if (frames < max_frames) {
                b->prepare(f, 0, 0);
                b->bind(npu, 0, 0);
                std::vector<Detection> got[2];
                for (int pass = 0; pass < 2; pass++) {
                    const bool neon = (pass + frames) % 2 == 1;
                    CHECK(rknn_run(npu.ctx[0], nullptr) == RKNN_SUCC, "rknn_run failed");
                    const auto ptrs = npu.output_ptrs(0);
                    const double t0 = thread_cpu_ms();
                    got[neon] = neon ? yolov8_post_neon(ptrs, npu.outputs, mh, n) : yolov8_post(ptrs, npu.outputs, mw, mh);
                    (neon ? neon_ms : scalar_ms).push_back(thread_cpu_ms() - t0);
                }
                {
                    CHECK(rknn_run(npu.ctx[0], nullptr) == RKNN_SUCC, "rknn_run failed");
                    Counts& prof = profiled;
                    prof.profile = true;
                    const int64_t t0 = now_ns();
                    yolov8_post_neon(npu.output_ptrs(0), npu.outputs, mh, prof);
                    prof_total_ns += now_ns() - t0;
                }
                identical += same(got[0], got[1]);
                detections += static_cast<long>(got[0].size());
                if (++frames == max_frames)
                    stop = true;
            }
            av_frame_free(&f);
        },
        [] { die("decoder error"); });
    b->shutdown(npu);
    npu.close();

    auto mean = [](const std::vector<double>& v) {
        double s = 0;
        for (double x : v)
            s += x;
        return v.empty() ? 0.0 : s / v.size();
    };
    char buf[64];
    auto f3 = [&](double x) {
        std::snprintf(buf, sizeof buf, "%.4f", x);
        return std::string(buf);
    };
    const Header rows = {
        {"frames", std::to_string(frames)},
        {"identical_detections", std::to_string(identical)},
        {"detections_per_frame", f3(frames ? double(detections) / frames : 0.0)},
        {"scalar_cpu_ms_mean", f3(mean(scalar_ms))},
        {"scalar_cpu_ms_median", f3(nearest_rank(scalar_ms, 0.5))},
        {"scalar_cpu_ms_p90", f3(nearest_rank(scalar_ms, 0.9))},
        {"neon_cpu_ms_mean", f3(mean(neon_ms))},
        {"neon_cpu_ms_median", f3(nearest_rank(neon_ms, 0.5))},
        {"neon_cpu_ms_p90", f3(nearest_rank(neon_ms, 0.9))},
        {"cells_per_frame", f3(double(n.cells) / std::max(1, frames))},
        {"cells_passing_score_sum_per_frame", f3(double(n.cells_passing) / std::max(1, frames))},
        {"blocks_of_16_per_frame", f3(double(n.blocks) / std::max(1, frames))},
        {"blocks_scanned_per_frame", f3(double(n.blocks_scanned) / std::max(1, frames))},
        {"candidates_per_frame", f3(double(n.candidates) / std::max(1, frames))},
        {"profiled_neon_ms_mean", f3(prof_total_ns / 1e6 / std::max(1, frames))},
        {"profiled_scan_ms_mean", f3((profiled.branch_ns - profiled.decode_ns) / 1e6 / std::max(1, frames))},
        {"profiled_box_decoding_ms_mean", f3(profiled.decode_ns / 1e6 / std::max(1, frames))},
        {"profiled_sort_and_nms_ms_mean", f3(profiled.nms_ns / 1e6 / std::max(1, frames))},
    };
    const std::string path = out_dir + "/post_probe.csv";
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
    return identical == frames ? 0 : 1;
}
