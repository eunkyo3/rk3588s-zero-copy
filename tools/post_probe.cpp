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
// Version 2 (decode_branch_neon2) changes the order of the reads only: the class scores row by row
// over the blocks that pass, and the box bins of a block with a candidate in 16-cell loads.
//
// Version 3 is version 2 with the softmax terms of the box decoding read from a table. A term,
// exp(b[i] - max_v), takes two quantized bins only: b[i] is dequantize(q_i), and max_v, as dequantize
// increases with q, is dequantize of the side's largest q. The table holds the term for all 256 x 256
// pairs, computed once per (zp, scale) with decode_dfl's expression, so the terms are the same floats;
// the sums keep decode_dfl's order. Before the video, the table's decoding is checked against
// decode_dfl on random bins: the four distances must be the same floats.
//
// The frames of the video are decoded by the hardware decoder and prepared as B does, into context 0
// (NPU core 0, M1's bindings). For each frame the context runs four times on the same input; after
// each run one version is timed (thread CPU per call), the order rotating with each frame, so each
// reads outputs the runtime has just written. Their detections must equal the scalar version's,
// field for field. A fifth run times the parts of version 3 (monotonic clock, so with the clock
// reads' own cost): the scan, the box decoding of the candidates, and the sort with the NMS.
//
// Usage: post_probe <video.mp4> <model.rknn> <out_dir> [frames=300]   (as root, clocks fixed)
//   <out_dir>/post_probe.csv  "# key: value" header lines (PROTOCOL §1.4, run kind check), then key,value
#include <arm_neon.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <memory>
#include <random>
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

// exp(b - m) for the dequantized bins b and m of all pairs (q_m, q_b), at [(q_m + 128) * 256 + q_b + 128].
struct ExpTable {
    int32_t zp;
    float scale;
    std::vector<float> e;
};

std::vector<ExpTable> g_exp_tables;  // one per (zp, scale) of the box outputs, filled before the video

void add_exp_table(int32_t zp, float scale) {
    for (const auto& t : g_exp_tables)
        if (t.zp == zp && t.scale == scale)
            return;
    if (!(scale > 0))
        throw std::runtime_error("post_probe: box scale not positive");
    ExpTable t{zp, scale, std::vector<float>(256 * 256)};
    volatile float v[256];  // stored and read back, as decode_dfl reads its bins
    for (int q = 0; q < 256; q++)
        v[q] = dequantize(static_cast<int8_t>(q - 128), zp, scale);
    for (int m = 0; m < 256; m++)
        for (int q = 0; q < 256; q++)
            t.e[m * 256 + q] = std::exp(v[q] - v[m]);
    g_exp_tables.push_back(std::move(t));
}

const float* exp_table(const rknn_tensor_attr& a) {
    for (const auto& t : g_exp_tables)
        if (t.zp == a.zp && t.scale == a.scale)
            return t.e.data();
    throw std::runtime_error("post_probe: no table for a box output");
}

// decode_dfl on quantized bins, the bin i of a side at q[(side * dfl_len + i) * step], with the terms
// from the table.
void decode_dfl_table(const float* table, const int8_t* q, size_t step, int dfl_len, float out[4]) {
    for (int side = 0; side < 4; side++) {
        const int8_t* b = q + side * dfl_len * step;
        int8_t max_q = b[0];
        for (int i = 1; i < dfl_len; i++)
            max_q = std::max(max_q, b[i * step]);
        const float* e = table + (max_q + 128) * 256 + 128;
        float sum = 0.0f, acc = 0.0f;
        for (int i = 0; i < dfl_len; i++) {
            const float ei = e[b[i * step]];
            sum += ei;
            acc += ei * i;
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

// Version 2: first the blocks whose score sums pass; then their class scores row by row, so each class
// row is read in address order; then, block by block in cell order, the candidates, with the box
// bins of a block that has one loaded once, 16 cells at a time. Same comparisons, same candidates in
// the same order, same box arithmetic. With a table (version 3), the box decoding is decode_dfl_table's.
void decode_branch_neon2(const int8_t* box, const rknn_tensor_attr& box_attr, const int8_t* cls,
                         const rknn_tensor_attr& cls_attr, const int8_t* sum, const rknn_tensor_attr& sum_attr,
                         int model_h, float conf_thresh, std::vector<Detection>& out, Counts& n,
                         const float* table) {
    const int grid_h = box_attr.dims[2];
    const int grid_w = box_attr.dims[3];
    const int grid_len = grid_h * grid_w;
    const int dfl_len = box_attr.dims[1] / 4;
    const float stride = static_cast<float>(model_h) / grid_h;
    const int8_t cls_thresh = quantize(conf_thresh, cls_attr.zp, cls_attr.scale);
    const int8_t sum_thresh = quantize(conf_thresh, sum_attr.zp, sum_attr.scale);
    if (grid_len % 16)
        throw std::runtime_error("post_probe: grid size not a multiple of 16");
    const int64_t t_branch = n.profile ? now_ns() : 0;
    n.cells += grid_len;

    const int8x16_t sum_t = vdupq_n_s8(sum_thresh);
    std::vector<int> scan;
    std::vector<uint8x16_t> pass;
    for (int cell0 = 0; cell0 < grid_len; cell0 += 16) {
        n.blocks++;
        const uint8x16_t p = vcgeq_s8(vld1q_s8(sum + cell0), sum_t);  // not below the threshold
        if (vmaxvq_u8(p) != 0) {
            scan.push_back(cell0);
            pass.push_back(p);
        }
    }
    n.blocks_scanned += static_cast<long>(scan.size());
    std::vector<int8x16_t> best(scan.size(), vdupq_n_s8(cls_thresh));
    std::vector<uint8x16_t> best_cls(scan.size(), vdupq_n_u8(0xff));
    for (int c = 0; c < kNumClasses; c++) {
        const int8_t* row = cls + c * grid_len;
        const uint8x16_t cv = vdupq_n_u8(static_cast<uint8_t>(c));
        for (size_t i = 0; i < scan.size(); i++) {
            const int8x16_t v = vld1q_s8(row + scan[i]);
            const uint8x16_t gt = vcgtq_s8(v, best[i]);
            best[i] = vbslq_s8(gt, v, best[i]);
            best_cls[i] = vbslq_u8(gt, cv, best_cls[i]);
        }
    }

    std::vector<float> bins(4 * dfl_len);
    std::vector<int8_t> rows(size_t(4 * dfl_len) * 16);
    alignas(16) int8_t best_v[16];
    alignas(16) uint8_t best_c[16], pass_v[16];
    for (size_t i = 0; i < scan.size(); i++) {
        const uint8x16_t cand = vandq_u8(pass[i], vmvnq_u8(vceqq_u8(best_cls[i], vdupq_n_u8(0xff))));
        n.cells_passing += vaddvq_u8(vshrq_n_u8(pass[i], 7));
        if (vmaxvq_u8(cand) == 0)
            continue;
        const int cell0 = scan[i];
        const int64_t t_decode = n.profile ? now_ns() : 0;
        for (int k = 0; k < 4 * dfl_len; k++)
            vst1q_s8(&rows[size_t(k) * 16], vld1q_s8(box + k * grid_len + cell0));
        vst1q_s8(best_v, best[i]);
        vst1q_u8(best_c, best_cls[i]);
        vst1q_u8(pass_v, pass[i]);
        for (int lane = 0; lane < 16; lane++) {
            if (!pass_v[lane] || best_c[lane] == 0xff)
                continue;
            n.candidates++;
            const int cell = cell0 + lane, y = cell / grid_w, x = cell % grid_w;
            float d[4];
            if (table) {
                decode_dfl_table(table, &rows[lane], 16, dfl_len, d);
            } else {
                for (int k = 0; k < 4 * dfl_len; k++)
                    bins[k] = dequantize(rows[size_t(k) * 16 + lane], box_attr.zp, box_attr.scale);
                decode_dfl(bins.data(), dfl_len, d);
            }
            out.push_back({best_c[lane], dequantize(best_v[lane], cls_attr.zp, cls_attr.scale),
                           (x + 0.5f - d[0]) * stride, (y + 0.5f - d[1]) * stride, (x + 0.5f + d[2]) * stride,
                           (y + 0.5f + d[3]) * stride});
        }
        if (n.profile)
            n.decode_ns += now_ns() - t_decode;
    }
    if (n.profile)
        n.branch_ns += now_ns() - t_branch;
}

// yolov8_post with the scan of decode_branch_neon (version 1) or decode_branch_neon2 (version 2, and
// with the table version 3); the checks of the outputs' layout are those of yolov8_post, which runs on
// the same outputs too.
std::vector<Detection> yolov8_post_neon(const std::vector<const int8_t*>& outputs,
                                        const std::vector<rknn_tensor_attr>& attrs, int model_h, Counts& n,
                                        int version, float conf_thresh = 0.25f, float nms_thresh = 0.45f) {
    std::vector<Detection> cand;
    for (int b = 0; b < 3; b++) {
        const int i = b * 3;
        if (version == 1)
            decode_branch_neon(outputs[i], attrs[i], outputs[i + 1], attrs[i + 1], outputs[i + 2], attrs[i + 2],
                               model_h, conf_thresh, cand, n);
        else
            decode_branch_neon2(outputs[i], attrs[i], outputs[i + 1], attrs[i + 1], outputs[i + 2], attrs[i + 2],
                                model_h, conf_thresh, cand, n, version == 3 ? exp_table(attrs[i]) : nullptr);
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

    const int64_t t_tables = now_ns();
    for (int b = 0; b < 3; b++)
        add_exp_table(npu.outputs[b * 3].zp, npu.outputs[b * 3].scale);
    const double table_build_ms = (now_ns() - t_tables) / 1e6;
    // The table's decoding against decode_dfl on random bins, for each table: every other set of bins
    // uniform over the 256 values, the others within a narrower range at a random level.
    const int dfl_len = npu.outputs[0].dims[1] / 4;
    long table_checks = 0, table_mismatches = 0;
    std::mt19937 rng(1);
    for (const auto& t : g_exp_tables) {
        std::vector<int8_t> q(4 * dfl_len);
        std::vector<float> bins(4 * dfl_len);
        for (int r = 0; r < 200000; r++) {
            const int width = r % 2 ? 256 : 1 + static_cast<int>(rng() % 32);
            const int low = static_cast<int>(rng() % (257 - width)) - 128;
            for (auto& v : q)
                v = static_cast<int8_t>(low + static_cast<int>(rng() % width));
            for (int k = 0; k < 4 * dfl_len; k++)
                bins[k] = dequantize(q[k], t.zp, t.scale);
            float want[4], got[4];
            decode_dfl(bins.data(), dfl_len, want);
            decode_dfl_table(t.e.data(), q.data(), 1, dfl_len, got);
            table_checks++;
            table_mismatches += std::memcmp(want, got, sizeof want) != 0;
        }
    }

    std::vector<double> ms[4];  // scalar, NEON versions 1, 2 and 3
    long identical[4] = {0, 0, 0, 0}, detections = 0;
    Counts n[4], profiled;
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
                std::vector<Detection> got[4];
                for (int pass = 0; pass < 4; pass++) {
                    const int v = (pass + frames) % 4;
                    CHECK(rknn_run(npu.ctx[0], nullptr) == RKNN_SUCC, "rknn_run failed");
                    const auto ptrs = npu.output_ptrs(0);
                    const double t0 = thread_cpu_ms();
                    got[v] = v == 0 ? yolov8_post(ptrs, npu.outputs, mw, mh) : yolov8_post_neon(ptrs, npu.outputs, mh, n[v], v);
                    ms[v].push_back(thread_cpu_ms() - t0);
                }
                {
                    CHECK(rknn_run(npu.ctx[0], nullptr) == RKNN_SUCC, "rknn_run failed");
                    profiled.profile = true;
                    const int64_t t0 = now_ns();
                    yolov8_post_neon(npu.output_ptrs(0), npu.outputs, mh, profiled, 3);
                    prof_total_ns += now_ns() - t0;
                }
                for (int v = 0; v < 4; v++)
                    identical[v] += same(got[0], got[v]);
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
    const double fr = std::max(1, frames);
    Header rows = {
        {"frames", std::to_string(frames)},
        {"detections_per_frame", f3(detections / fr)},
        {"cells_per_frame", f3(n[2].cells / fr)},
        {"cells_passing_score_sum_per_frame", f3(n[2].cells_passing / fr)},
        {"blocks_of_16_per_frame", f3(n[2].blocks / fr)},
        {"blocks_scanned_per_frame", f3(n[2].blocks_scanned / fr)},
        {"candidates_per_frame", f3(n[2].candidates / fr)},
        {"exp_tables", std::to_string(g_exp_tables.size())},
        {"exp_table_build_ms", f3(table_build_ms)},
        {"exp_table_random_checks", std::to_string(table_checks)},
        {"exp_table_random_mismatches", std::to_string(table_mismatches)},
    };
    const char* names[4] = {"scalar", "neon1", "neon2", "neon3"};
    for (int v = 0; v < 4; v++) {
        rows.push_back({std::string(names[v]) + "_identical_to_scalar", std::to_string(identical[v])});
        rows.push_back({std::string(names[v]) + "_cpu_ms_mean", f3(mean(ms[v]))});
        rows.push_back({std::string(names[v]) + "_cpu_ms_median", f3(nearest_rank(ms[v], 0.5))});
        rows.push_back({std::string(names[v]) + "_cpu_ms_p90", f3(nearest_rank(ms[v], 0.9))});
    }
    rows.push_back({"profiled_neon3_ms_mean", f3(prof_total_ns / 1e6 / fr)});
    rows.push_back({"profiled_neon3_scan_ms_mean", f3((profiled.branch_ns - profiled.decode_ns) / 1e6 / fr)});
    rows.push_back({"profiled_neon3_box_decoding_ms_mean", f3(profiled.decode_ns / 1e6 / fr)});
    rows.push_back({"profiled_neon3_sort_and_nms_ms_mean", f3(profiled.nms_ns / 1e6 / fr)});
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
    return identical[1] == frames && identical[2] == frames && identical[3] == frames && table_mismatches == 0 ? 0 : 1;
}
