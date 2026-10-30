// M1-2 alignment of B with C (PROTOCOL §8, §11.1). Runs C's and B's prepare on the same decoded
// frames, B under both candidate interpolations, and reports over the 640x360 band the mean
// absolute error, the minimum per-frame PSNR and the mean signed difference per RGB channel
// (a color bias shows up there), and whether every border element of both tensors is 114.
//
// Usage: compare_prepare <video.mp4> <model.rknn> [frames=1000] [rga_interp=0]
//   rga_interp: im_opt_t.interp for C (0 default, 1 linear, 2 cubic, 3 average)
#include <algorithm>
#include <atomic>
#include <cmath>
#include <map>
#include <tuple>
#include <cstdio>
#include <limits>
#include <string>
#include <vector>

#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>

#include "core/metrics.h"
#include "core/npu.h"
#include "core/util.h"
#include "core/video_source.h"
#include "core/yolov8_post.h"
#include "preprocess/backend.h"
#include "preprocess/cpu_common.h"

namespace {

constexpr uint8_t kPad = 114;

struct Stats {
    const char* name;
    double abs_sum = 0, signed_sum[3] = {0, 0, 0};
    int64_t elements = 0;
    double min_psnr = std::numeric_limits<double>::infinity();
    bool border_ok = true;
};

bool border_is_pad(const std::vector<uint8_t>& t, int mw, int mh, const Band& b) {
    for (int y = 0; y < mh; y++)
        for (int x = 0; x < mw; x++) {
            if (x >= b.x && x < b.x + b.w && y >= b.y && y < b.y + b.h)
                continue;
            const uint8_t* p = &t[(size_t(y) * mw + x) * 3];
            if (p[0] != kPad || p[1] != kPad || p[2] != kPad)
                return false;
        }
    return true;
}

void compare(const std::vector<uint8_t>& c, const std::vector<uint8_t>& b, int mw, const Band& band, Stats& s) {
    double sq = 0, abs = 0;
    for (int y = band.y; y < band.y + band.h; y++) {
        for (int x = band.x; x < band.x + band.w; x++) {
            const size_t i = (size_t(y) * mw + x) * 3;
            for (int ch = 0; ch < 3; ch++) {
                const double d = double(c[i + ch]) - double(b[i + ch]);
                abs += std::fabs(d);
                sq += d * d;
                s.signed_sum[ch] += d;
            }
        }
    }
    const int64_t n = int64_t(band.w) * band.h * 3;
    s.abs_sum += abs;
    s.elements += n;
    const double mse = sq / double(n);
    s.min_psnr = std::min(s.min_psnr, mse == 0 ? 99.0 : 10.0 * std::log10(255.0 * 255.0 / mse));
}

float iou(const Detection& a, const Detection& b) {
    const float w = std::max(0.0f, std::min(a.x2, b.x2) - std::max(a.x1, b.x1));
    const float h = std::max(0.0f, std::min(a.y2, b.y2) - std::max(a.y1, b.y1));
    const float u = (a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - w * h;
    return u > 0 ? w * h / u : 0;
}

// Greedy per-class matching at IoU >= 0.5 (the M1-4 tool uses the Hungarian algorithm). Boxes with
// confidence within 0.25 +- 0.02, or matched to one, are left out of the per-frame counts (§11.2).
struct DetStats {
    int64_t pairs = 0, iou_ok = 0, conf_ok = 0, both_ok = 0, frames = 0, frames_same = 0, unmatched = 0;
    float min_iou = 1, max_dconf = 0;

    void add(const std::vector<Detection>& c, const std::vector<Detection>& b) {
        auto near_threshold = [](const Detection& d) { return d.score >= 0.23f && d.score <= 0.27f; };
        std::vector<int> b_match(b.size(), -1);
        std::vector<bool> c_matched(c.size(), false);
        std::vector<std::tuple<float, int, int>> cand;
        for (size_t i = 0; i < c.size(); i++)
            for (size_t j = 0; j < b.size(); j++)
                if (c[i].cls == b[j].cls) {
                    const float v = iou(c[i], b[j]);
                    if (v >= 0.5f)
                        cand.emplace_back(v, int(i), int(j));
                }
        std::sort(cand.rbegin(), cand.rend());
        for (auto [v, i, j] : cand) {
            if (c_matched[i] || b_match[j] >= 0)
                continue;
            c_matched[i] = true;
            b_match[j] = i;
            pairs++;
            const float dconf = std::fabs(c[i].score - b[j].score);
            iou_ok += v >= 0.95f;
            conf_ok += dconf <= 0.02f;
            both_ok += v >= 0.95f && dconf <= 0.02f;
            min_iou = std::min(min_iou, v);
            max_dconf = std::max(max_dconf, dconf);
        }
        std::map<int, int> counts;  // class -> C count - B count
        for (size_t i = 0; i < c.size(); i++) {
            bool skip = near_threshold(c[i]);
            for (size_t j = 0; j < b.size(); j++)
                skip |= b_match[j] == int(i) && near_threshold(b[j]);
            if (!skip)
                counts[c[i].cls]++;
            unmatched += !c_matched[i];
        }
        for (size_t j = 0; j < b.size(); j++) {
            bool skip = near_threshold(b[j]) || (b_match[j] >= 0 && near_threshold(c[b_match[j]]));
            if (!skip)
                counts[b[j].cls]--;
            unmatched += b_match[j] < 0;
        }
        frames++;
        frames_same += std::all_of(counts.begin(), counts.end(), [](auto& kv) { return kv.second == 0; });
    }
};

}  // namespace

int main(int argc, char** argv) {
    pin_current_thread({4, 5, 6, 7});
    cv::setNumThreads(1);
    if (argc < 3)
        die("usage: compare_prepare <video.mp4> <model.rknn> [frames=1000] [rga_interp=0]");
    const int max_frames = argc > 3 ? std::atoi(argv[3]) : 1000;
    const int rga_interp = argc > 4 ? std::atoi(argv[4]) : rga_interpolation();

    // Each preprocessing gets its own contexts: C and B both bind every context's input with
    // rknn_set_io_mem, to their own buffers.
    NpuContexts npu, npu_b, npu_l;
    npu.open(argv[2]);
    npu_b.open(argv[2]);
    npu_l.open(argv[2]);
    const int mw = npu.model_w(), mh = npu.model_h();
    auto c = make_zero_copy_rga(argv[2], rga_interp);
    auto b_linear = make_cpu_copy(cv::INTER_LINEAR);
    auto b_area = make_cpu_copy(cv::INTER_AREA);
    c->init(npu, 2);
    b_linear->init(npu_l, 1);
    b_area->init(npu_b, 1);

    // B is INTER_AREA with the moved luma box (AMENDMENTS A4). R, B with INTER_LINEAR against B, is
    // the reference pair of AMENDMENTS A5: two preprocessings PROTOCOL §8 allows for B.
    Stats area{"C vs B"}, linear{"C vs B(INTER_LINEAR)"}, reference{"R = B(INTER_LINEAR) vs B"};
    DetStats det, det_ref;
    std::map<int, int64_t> class_c, class_b, class_l;
    bool b_binding_ok = true;
    bool c_border_ok = true;
    std::vector<uint8_t> tc(size_t(mw) * mh * 3), tl(tc.size()), ta(tc.size());
    std::atomic<bool> stop{false};
    int frames = 0;
    VideoSource src(argv[1], /*software=*/false, /*paced=*/false);
    src.open(&stop);
    src.run(
        [&](AVFrame* f) {
            if (frames < max_frames) {
                const Band band = letterbox_band(f->width, f->height, mw, mh);
                c->prepare(f, 0, 0);
                b_linear->prepare(f, 0, 0);
                b_area->prepare(f, 0, 0);
                c->read_slot(0, tc.data());
                b_linear->read_slot(0, tl.data());
                b_area->read_slot(0, ta.data());
                compare(tc, tl, mw, band, linear);
                compare(tc, ta, mw, band, area);
                c_border_ok &= border_is_pad(tc, mw, mh, band);
                linear.border_ok &= border_is_pad(tl, mw, mh, band);
                area.border_ok &= border_is_pad(ta, mw, mh, band);
                compare(ta, tl, mw, band, reference);
                // Detection level, C and both B variants on NPU core 0 of their own contexts. An estimate
                // with greedy matching; tools/equivalence_check.py (M1-4) decides.
                c->bind(npu, 0, 0);
                b_area->bind(npu_b, 0, 0);
                b_linear->bind(npu_l, 0, 0);
                CHECK(rknn_run(npu.ctx[0], nullptr) == RKNN_SUCC && rknn_run(npu_b.ctx[0], nullptr) == RKNN_SUCC &&
                          rknn_run(npu_l.ctx[0], nullptr) == RKNN_SUCC,
                      "rknn_run failed");
                const auto dc = yolov8_post(npu.output_ptrs(0), npu.outputs, mw, mh);
                const auto db = yolov8_post(npu_b.output_ptrs(0), npu_b.outputs, mw, mh);
                const auto dl = yolov8_post(npu_l.output_ptrs(0), npu_l.outputs, mw, mh);
                // B's binding against the runtime's own copy path (AMENDMENTS A3), first and last frame
                if (frames == 0 || frames == max_frames - 1)
                    b_binding_ok &= npu_b.run_bound(0) == npu_b.reference_outputs(ta, argv[2]);
                det.add(dc, db);
                det_ref.add(dl, db);
                for (const auto& d : dc)
                    class_c[d.cls]++;
                for (const auto& d : db)
                    class_b[d.cls]++;
                for (const auto& d : dl)
                    class_l[d.cls]++;
                if (++frames == max_frames)
                    stop = true;
            }
            av_frame_free(&f);
        },
        [] { die("decoder error"); });

    auto mae = [](const Stats& s) { return s.abs_sum / double(s.elements); };
    std::printf("frames: %d\n", frames);
    std::printf("b_binding_matches_rknn_outputs_get (first and last frame): %d\n", b_binding_ok);
    std::printf("border_114: C %d, B(INTER_AREA) %d, B(INTER_LINEAR) %d\n", c_border_ok, area.border_ok,
                linear.border_ok);
    for (const Stats* s : {&area, &linear, &reference})
        std::printf("tensor %-32s mae=%.4f min_psnr_db=%.2f mean_diff_rgb=%.3f,%.3f,%.3f\n", s->name, mae(*s),
                    s->min_psnr, s->signed_sum[0] / (s->elements / 3.0), s->signed_sum[1] / (s->elements / 3.0),
                    s->signed_sum[2] / (s->elements / 3.0));
    for (auto [name, d] : {std::pair{"C vs B", &det}, {"R = B(INTER_LINEAR) vs B", &det_ref}})
        std::printf("detections %-26s pairs=%lld iou>=0.95 %.4f, |dconf|<=0.02 %.4f, both %.4f, min_iou %.3f, "
                    "max_dconf %.3f, frames with equal counts %.4f\n",
                    name, (long long)d->pairs, double(d->iou_ok) / d->pairs, double(d->conf_ok) / d->pairs,
                    double(d->both_ok) / d->pairs, d->min_iou, d->max_dconf, double(d->frames_same) / d->frames);
    // AMENDMENTS A5 (c): every box post-processing returns, summed over frames and classes, within
    // 2% of B's. Per class is reported only, for classes with at least 100 boxes in B.
    std::printf("boxes per class (classes with at least 100 B boxes), against B:\n");
    int64_t total_b = 0, total_c = 0, total_l = 0;
    for (const auto& [cls, nb] : class_b) {
        total_b += nb;
        total_c += class_c[cls];
        total_l += class_l[cls];
        if (nb < 100)
            continue;
        const double rel_c = (double(class_c[cls]) - nb) / nb, rel_l = (double(class_l[cls]) - nb) / nb;
        std::printf("class %-14s B %6lld  C %6lld (%+7.2f%%)  R %6lld (%+7.2f%%)\n", coco_label(cls), (long long)nb,
                    (long long)class_c[cls], 100 * rel_c, (long long)class_l[cls], 100 * rel_l);
    }
    for (const auto& [cls, nc] : class_c)
        if (!class_b.count(cls))
            total_c += nc;
    for (const auto& [cls, nl] : class_l)
        if (!class_b.count(cls))
            total_l += nl;
    std::printf("all classes    B %6lld  C %6lld (%+7.2f%%)  R %6lld (%+7.2f%%)\n", (long long)total_b,
                (long long)total_c, 100.0 * (total_c - total_b) / total_b, (long long)total_l,
                100.0 * (total_l - total_b) / total_b);
    // PROTOCOL §11.1 / §11.2 as registered, and as amended (AMENDMENTS A5). Estimates.
    const bool orig_tensor = mae(area) <= 2 && area.min_psnr >= 35;
    const bool orig_det = det.both_ok == det.pairs && double(det.frames_same) / det.frames >= 0.99;
    const bool a5_tensor = mae(area) <= mae(reference) && area.min_psnr >= reference.min_psnr;
    const bool a5_det = double(det.both_ok) / det.pairs >= double(det_ref.both_ok) / det_ref.pairs &&
                        double(det.frames_same) / det.frames >= double(det_ref.frames_same) / det_ref.frames &&
                        std::fabs(double(total_c - total_b) / total_b) <= 0.02;
    std::printf("registered criteria: tensor %s, detection %s\n", orig_tensor ? "pass" : "FAIL",
                orig_det ? "pass" : "FAIL");
    std::printf("amended criteria (A5): tensor %s, detection %s\n", a5_tensor ? "pass" : "FAIL",
                a5_det ? "pass" : "FAIL");
    std::printf("lower_mae_against_c: %s\n", mae(linear) <= mae(area) ? "INTER_LINEAR" : "INTER_AREA");
    c->shutdown(npu);
    b_linear->shutdown(npu_l);
    b_area->shutdown(npu_b);
    npu.close();
    npu_b.close();
    npu_l.close();
    return frames == max_frames ? 0 : 1;
}
