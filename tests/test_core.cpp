// Unit tests for the pipeline's queue, slot pool, receive checks and percentile, the /proc/schedstat
// parser, arm Cp's post-processing against M1's on random outputs, M4's YOLOv10 selection, the
// shared choice of post-processing, M4's, M6's and M7's run rules, and M7's snapshots and counters.
// Run: build/test_core
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <random>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include <sys/syscall.h>
#include <unistd.h>

#include "core/metrics.h"
#include "core/perf_counters.h"
#include "core/pipeline_post.h"
#include "core/report.h"
#include "core/run_rules.h"
#include "core/slot_pool.h"
#include "core/snapshot.h"
#include "core/stage_queue.h"
#include "core/stream_checks.h"
#include "core/util.h"
#include "core/yolov8_post.h"
#include "core/yolov8_post_neon.h"
#include "core/yolov10_post.h"
#include "m4/map_rules.h"
#include "m4/sha256.h"

namespace {

int failures = 0;

void expect(bool ok, const char* what) {
    std::printf("%s %s\n", ok ? "ok  " : "FAIL", what);
    failures += !ok;
}

void queue_drops_oldest() {
    StageQueue<int> q(2);
    expect(!q.push_drop_oldest(1) && !q.push_drop_oldest(2), "Q1: no drop below capacity");
    auto d = q.push_drop_oldest(3);
    expect(d && *d == 1, "Q1: full queue drops the oldest item");
    expect(*q.pop() == 2 && *q.pop() == 3, "Q1: FIFO order after a drop");
}

void queue_blocks_when_full() {
    StageQueue<int> q(1);
    q.push(1);
    std::atomic<bool> pushed{false};
    std::thread t([&] {
        q.push(2);
        pushed = true;
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    expect(!pushed, "Q2: push waits while full");
    expect(*q.pop() == 1, "Q2: pop frees a place");
    t.join();
    expect(pushed && *q.pop() == 2, "Q2: waiting push completes");
}

void queue_close_wakes_everyone() {
    StageQueue<int> q(1);
    std::thread consumer([&] { expect(!q.pop(), "close: waiting pop returns nothing"); });
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    q.close();
    consumer.join();
    StageQueue<int> full(1);
    full.push(1);
    std::thread producer([&] { expect(!full.push(2), "close: waiting push fails"); });
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    full.close();
    producer.join();
    expect(full.drain().size() == 1, "close: drain returns what is left");
}

void slot_pool_cycles() {
    SlotPool pool(2);
    int a = pool.acquire(), b = pool.acquire();
    expect(a != b && a >= 0 && b >= 0 && a < 2 && b < 2, "slots: distinct slots");
    std::atomic<int> got{-2};
    std::thread t([&] { got = pool.acquire(); });
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    expect(got == -2, "slots: acquire waits when none is free");
    pool.release(a);
    t.join();
    expect(got == a, "slots: released slot is handed out");
    std::thread u([&] { got = pool.acquire(); });
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    pool.close();
    u.join();
    expect(got == -1, "slots: close wakes a waiting acquire with -1");
}

void decoder_drop_rule() {
    // PROTOCOL §7.6 at 30 fps, RTP time base 1/90000: one period is 3000 ticks.
    expect(missing_frames(3000, 1, 90000, 30) == 0, "drops: one period");
    expect(missing_frames(4500, 1, 90000, 30) == 0, "drops: exactly 1.5 periods is not a gap");
    expect(missing_frames(4501, 1, 90000, 30) == 1, "drops: just over 1.5 periods counts 1");
    expect(missing_frames(6000, 1, 90000, 30) == 1, "drops: 2 periods, 1 missing");
    expect(missing_frames(10800, 1, 90000, 30) == 3, "drops: 3.6 periods rounds to 4, 3 missing");
    expect(missing_frames(0, 1, 90000, 30) == 1, "drops: repeated PTS counts 1");
    expect(missing_frames(-3000, 1, 90000, 30) == 1, "drops: PTS going back counts 1");
    // mp4 time base 1/15360: one period is 512 ticks.
    expect(missing_frames(512, 1, 15360, 30) == 0 && missing_frames(1536, 1, 15360, 30) == 2,
           "drops: other time base");
}

void drift_formula() {
    expect(drift_s(10.0, 300, 30) == 0.0, "drift: on time");
    expect(drift_s(10.0, 270, 30) == 1.0, "drift: 30 frames behind is 1 s");
    expect(drift_s(10.0, 301, 30) < 0, "drift: ahead is negative");
}

void percentile_matches_numpy() {
    // numpy.percentile([1, 2, 3, 4], [50, 99]) = [2.5, 3.97]
    expect(percentile({4, 1, 3, 2}, 50) == 2.5, "percentile: median of 4 values");
    const double p99 = percentile({1, 2, 3, 4}, 99);
    expect(p99 > 3.9699 && p99 < 3.9701, "percentile: linear interpolation at p99");
    expect(percentile({7}, 99) == 7, "percentile: single value");
}

void schedstat_parse() {
    int version = 0;
    const auto run = parse_schedstat("version 15\ntimestamp 4296361390\n"
                                     "cpu0 0 0 0 0 0 0 1500000000 200 30\n"
                                     "domain0 ff 0 0 0 0 0 0 0 0\n"
                                     "cpu1 0 0 0 0 0 0 250000000 0 0\n",
                                     &version);
    expect(version == 15, "schedstat: version line");
    expect(run.size() == 2 && run[0] == 1.5 && run[1] == 0.25, "schedstat: 7th number of each cpu line, in s");
    expect(parse_schedstat("", &version).empty() && version == -1, "schedstat: nothing to read");
}

// M1's three branches at 640x640, with quantization parameters like the model's.
std::vector<rknn_tensor_attr> output_attrs() {
    std::vector<rknn_tensor_attr> attrs;
    for (int g : {80, 40, 20})
        for (int ch : {64, 80, 1}) {
            rknn_tensor_attr a;
            std::memset(&a, 0, sizeof a);
            a.n_dims = 4;
            a.dims[0] = 1;
            a.dims[1] = ch;
            a.dims[2] = a.dims[3] = g;
            a.fmt = RKNN_TENSOR_NCHW;
            a.type = RKNN_TENSOR_INT8;
            a.zp = ch == 64 ? -56 + g / 20 : -128;
            a.scale = ch == 64 ? 0.09f + 0.01f * (g / 20) : 0.0039f;
            attrs.push_back(a);
        }
    return attrs;
}

void neon_post_matches_m1() {
    const auto attrs = output_attrs();
    const NeonPost neon(attrs);
    expect(neon.table_count() == 3 && neon.table_bytes() == 3 * 256 * 256 * sizeof(float),
           "NeonPost: one table per box (zp, scale)");
    std::mt19937 rng(1);
    std::vector<std::vector<int8_t>> bufs;
    std::vector<const int8_t*> ptrs;
    for (const auto& a : attrs)
        bufs.emplace_back(size_t(a.dims[1]) * a.dims[2] * a.dims[3]);
    for (const auto& b : bufs)
        ptrs.push_back(b.data());
    // Dense random scores: many candidates, ties between classes, and NMS at work.
    int same_sets = 0, detections = 0;
    for (int set = 0; set < 20; set++) {
        for (auto& b : bufs)
            for (auto& v : b)
                v = static_cast<int8_t>(static_cast<int>(rng() % 256) - 128);
        const auto want = yolov8_post(ptrs, attrs, 640, 640);
        const auto got = neon.run(ptrs, attrs, 640, 640);
        bool same = want.size() == got.size();
        for (size_t i = 0; same && i < want.size(); i++)
            same = want[i].cls == got[i].cls && want[i].score == got[i].score && want[i].x1 == got[i].x1 &&
                   want[i].y1 == got[i].y1 && want[i].x2 == got[i].x2 && want[i].y2 == got[i].y2;
        same_sets += same;
        detections += static_cast<int>(want.size());
    }
    expect(same_sets == 20 && detections > 0, "NeonPost: yolov8_post's detections on 20 random output sets");
}

// M4's YOLOv10n outputs at 640x640: three branches of {box, class scores}, class scale 1/256 so that
// int8 -64 is exactly 0.25.
std::vector<rknn_tensor_attr> v10_attrs() {
    std::vector<rknn_tensor_attr> attrs;
    for (int g : {80, 40, 20})
        for (int ch : {64, 80}) {
            rknn_tensor_attr a;
            std::memset(&a, 0, sizeof a);
            a.n_dims = 4;
            a.dims[0] = 1;
            a.dims[1] = ch;
            a.dims[2] = a.dims[3] = g;
            a.fmt = RKNN_TENSOR_NCHW;
            a.type = RKNN_TENSOR_INT8;
            a.zp = -128;
            a.scale = ch == 64 ? 0.1f : 1.0f / 256;
            attrs.push_back(a);
        }
    return attrs;
}

void yolov10_post_selects() {
    const auto attrs = v10_attrs();
    std::vector<std::vector<int8_t>> bufs;
    std::vector<const int8_t*> ptrs;
    for (const auto& a : attrs)
        bufs.emplace_back(size_t(a.dims[1]) * a.dims[2] * a.dims[3], int8_t(-128));
    for (const auto& b : bufs)
        ptrs.push_back(b.data());
    // Branch 0 (stride 8), cell (x 3, y 2): box bins one-hot at 2, 3, 4, 5 for left, top, right, bottom.
    const int cell = 2 * 80 + 3, len0 = 80 * 80;
    for (int side = 0; side < 4; side++)
        bufs[0][(side * 16 + 2 + side) * len0 + cell] = 127;
    bufs[1][7 * len0 + cell] = -64;  // exactly 0.25: not above the threshold
    bufs[1][9 * len0 + cell] = -63;  // just above
    bufs[1][11 * len0 + cell] = 0;   // 0.5, the same cell's second class
    auto d = yolov10_post(ptrs, attrs, 640, 640);
    expect(d.size() == 2 && d[0].cls == 11 && d[0].score == 0.5f && d[1].cls == 9,
           "yolov10_post: pairs above 0.25 only, by score");
    // softmax of 25.5 against 15 zeros: the expected bin is within 0.0001 of the one-hot bin
    auto near = [](float a, float b) { return std::fabs(a - b) < 0.01f; };
    expect(d.size() == 2 && near(d[0].x1, (3.5f - 2) * 8) && near(d[0].y1, (2.5f - 3) * 8) &&
               near(d[0].x2, (3.5f + 4) * 8) && near(d[0].y2, (2.5f + 5) * 8),
           "yolov10_post: DFL box decoding");
    expect(d.size() == 2 && d[0].x1 == d[1].x1 && d[0].y1 == d[1].y1 && d[0].x2 == d[1].x2 && d[0].y2 == d[1].y2,
           "yolov10_post: two classes at one cell share its box");

    // 402 equal scores, 134 per branch: the 300 kept are the first by branch, cell, class.
    for (int b = 0; b < 3; b++)
        for (int k = 0; k < 400 / 3 + 1; k++)
            bufs[b * 2 + 1][(k % 80) * attrs[b * 2 + 1].dims[2] * attrs[b * 2 + 1].dims[3] + k] = 64;
    d = yolov10_post(ptrs, attrs, 640, 640, 0.25f, 300);
    bool order_ok = d.size() == 300;
    for (size_t i = 1; order_ok && i < d.size(); i++)
        order_ok = d[i - 1].score >= d[i].score;
    expect(order_ok && d.front().score == 0.75f && d.back().score == 0.75f,
           "yolov10_post: max_det keeps the best, sorted");
    // 134 per branch: the last kept is branch 2's 32nd, cell 31 (x 11, y 1), class 31; uniform bins give 7.5.
    expect(d.size() == 300 && d.front().cls == 0 && near(d.front().x1, (0.5f - 7.5f) * 8) && d.back().cls == 31 &&
               near(d.back().x1, (11.5f - 7.5f) * 32) && near(d.back().y1, (1.5f - 7.5f) * 32),
           "yolov10_post: equal scores in branch, cell, class order");
    expect(yolov10_post(ptrs, attrs, 640, 640, 0.99f).empty(), "yolov10_post: nothing above 0.99");
    bool threw = false;
    try {
        yolov10_post({ptrs.begin(), ptrs.begin() + 5}, {attrs.begin(), attrs.begin() + 5}, 640, 640);
    } catch (const std::runtime_error&) {
        threw = true;
    }
    expect(threw, "yolov10_post: rejects 5 outputs");
}

// The specification written plainly: every pair above conf, by score with ties in (branch, cell,
// class) order, the first max_det, each with its cell's box.
struct RefPair {
    float score;
    int branch, cell, cls;
};

std::vector<RefPair> yolov10_reference_pairs(const std::vector<std::vector<int8_t>>& bufs,
                                             const std::vector<rknn_tensor_attr>& attrs, float conf) {
    std::vector<RefPair> all;
    for (int b = 0; b < 3; b++) {
        const auto& ca = attrs[b * 2 + 1];
        const int len = ca.dims[2] * ca.dims[3];
        for (int cell = 0; cell < len; cell++)
            for (int c = 0; c < 80; c++) {
                float s = (static_cast<float>(bufs[b * 2 + 1][c * len + cell]) - ca.zp) * ca.scale;
                if (s > conf)
                    all.push_back({s, b, cell, c});
            }
    }
    std::stable_sort(all.begin(), all.end(), [](const RefPair& a, const RefPair& b) { return a.score > b.score; });
    return all;
}

Detection yolov10_reference_box(const std::vector<std::vector<int8_t>>& bufs,
                                const std::vector<rknn_tensor_attr>& attrs, const RefPair& p) {
    const auto& ba = attrs[p.branch * 2];
    const int g = ba.dims[2], len = g * g;
    float d[4];
    for (int side = 0; side < 4; side++) {
        float v[16], m = -1e30f, sum = 0, acc = 0;
        for (int i = 0; i < 16; i++) {
            v[i] = (static_cast<float>(bufs[p.branch * 2][(side * 16 + i) * len + p.cell]) - ba.zp) * ba.scale;
            m = std::max(m, v[i]);
        }
        for (int i = 0; i < 16; i++) {
            float e = std::exp(v[i] - m);
            sum += e;
            acc += e * i;
        }
        d[side] = acc / sum;
    }
    const float st = 640.0f / g;
    const int x = p.cell % g, y = p.cell / g;
    return {p.cls, p.score, (x + 0.5f - d[0]) * st, (y + 0.5f - d[1]) * st, (x + 0.5f + d[2]) * st,
            (y + 0.5f + d[3]) * st};
}

void yolov10_post_matches_reference() {
    auto attrs = v10_attrs();
    // Class scales of the real model per branch: int8 order and score order disagree across branches.
    attrs[1].scale = 0.0020011724f;
    attrs[3].scale = 0.00350535568f;
    attrs[5].scale = 0.00379228638f;
    std::mt19937 rng(7);
    std::vector<std::vector<int8_t>> bufs;
    std::vector<const int8_t*> ptrs;
    for (const auto& a : attrs)
        bufs.emplace_back(size_t(a.dims[1]) * a.dims[2] * a.dims[3]);
    for (const auto& b : bufs)
        ptrs.push_back(b.data());
    int cases = 0, same_cases = 0;
    for (int set = 0; set < 4; set++) {
        // Even sets: scores over the whole range. Odd sets: 6 values near the bottom, so most pairs tie,
        // also within a cell.
        for (size_t k = 0; k < bufs.size(); k++)
            for (auto& v : bufs[k])
                v = static_cast<int8_t>(k % 2 == 0 || set % 2 == 0 ? static_cast<int>(rng() % 256) - 128
                                                                   : -128 + static_cast<int>(rng() % 6));
        for (float conf : {0.001f, 0.25f, 0.45f}) {
            const auto pairs = yolov10_reference_pairs(bufs, attrs, conf);
            for (int max_det : {-1, 0, 1, 300, 1000000}) {
                const size_t n = std::min(pairs.size(), static_cast<size_t>(std::max(max_det, 0)));
                const auto got = yolov10_post(ptrs, attrs, 640, 640, conf, max_det);
                bool same = got.size() == n;
                for (size_t i = 0; same && i < n; i++) {
                    const Detection want = yolov10_reference_box(bufs, attrs, pairs[i]);
                    same = want.cls == got[i].cls && want.score == got[i].score &&
                           std::fabs(want.x1 - got[i].x1) < 1e-3f && std::fabs(want.y1 - got[i].y1) < 1e-3f &&
                           std::fabs(want.x2 - got[i].x2) < 1e-3f && std::fabs(want.y2 - got[i].y2) < 1e-3f;
                }
                cases++;
                same_cases += same;
            }
        }
    }
    expect(same_cases == cases, "yolov10_post: the plain specification on 60 random cases");
}

bool same_detections(const std::vector<Detection>& a, const std::vector<Detection>& b) {
    if (a.size() != b.size())
        return false;
    for (size_t i = 0; i < a.size(); i++)
        if (a[i].cls != b[i].cls || a[i].score != b[i].score || a[i].x1 != b[i].x1 || a[i].y1 != b[i].y1 ||
            a[i].x2 != b[i].x2 || a[i].y2 != b[i].y2)
            return false;
    return true;
}

// Random int8 outputs for attrs, and pointers to them.
struct RandomOutputs {
    std::vector<std::vector<int8_t>> bufs;
    std::vector<const int8_t*> ptrs;
    RandomOutputs(const std::vector<rknn_tensor_attr>& attrs, std::mt19937& rng) {
        for (const auto& a : attrs) {
            bufs.emplace_back(size_t(a.dims[1]) * a.dims[2] * a.dims[3]);
            for (auto& v : bufs.back())
                v = static_cast<int8_t>(static_cast<int>(rng() % 256) - 128);
        }
        for (const auto& b : bufs)
            ptrs.push_back(b.data());
    }
};

void pipeline_post_dispatches() {
    PostKind k = PostKind::V10;
    expect(parse_post_kind("m1", &k) && k == PostKind::M1 && parse_post_kind("neon", &k) && k == PostKind::Neon &&
               parse_post_kind("v10", &k) && k == PostKind::V10 && !parse_post_kind("M1", &k) &&
               !parse_post_kind("", &k) && !parse_post_kind("v8", &k) && k == PostKind::V10,
           "pipeline_post: the names m1, neon and v10 only");
    expect(std::string(post_kind_name(PostKind::M1)) == "m1" && std::string(post_kind_name(PostKind::Neon)) == "neon" &&
               std::string(post_kind_name(PostKind::V10)) == "v10",
           "pipeline_post: names back");

    const auto v8 = output_attrs(), v10 = v10_attrs();
    const PipelinePost m1(PostKind::M1, v8), neon(PostKind::Neon, v8), c10(PostKind::V10, v10);
    const NeonPost neon_direct(v8);
    std::mt19937 rng(2);
    int same_m1 = 0, same_neon = 0, same_v10 = 0, v10_count = 0;
    for (int set = 0; set < 5; set++) {
        const RandomOutputs o8(v8, rng), o10(v10, rng);
        same_m1 += same_detections(m1.run(o8.ptrs, v8, 640, 640), yolov8_post(o8.ptrs, v8, 640, 640, 0.25f, 0.45f));
        same_neon +=
            same_detections(neon.run(o8.ptrs, v8, 640, 640), neon_direct.run(o8.ptrs, v8, 640, 640, 0.25f, 0.45f));
        const auto want = yolov10_post(o10.ptrs, v10, 640, 640, 0.25f, 300);
        same_v10 += same_detections(c10.run(o10.ptrs, v10, 640, 640), want);
        v10_count += want.size() == 300;
    }
    expect(same_m1 == 5, "pipeline_post: m1 is yolov8_post at conf 0.25, NMS 0.45");
    // NeonPost's detections equal yolov8_post's by design, so neon is told apart by what only NeonPost
    // does: it looks up the table built for each box output's (zp, scale), and throws without one.
    {
        auto other = v8;
        other[0].zp += 1;
        const RandomOutputs o8(v8, rng);
        bool neon_threw = false, m1_threw = false;
        try {
            neon.run(o8.ptrs, other, 640, 640);
        } catch (const std::runtime_error&) {
            neon_threw = true;
        }
        try {
            m1.run(o8.ptrs, other, 640, 640);
        } catch (const std::runtime_error&) {
            m1_threw = true;
        }
        expect(neon_threw && !m1_threw, "pipeline_post: neon runs NeonPost (its tables), m1 does not");
    }
    expect(same_neon == 5, "pipeline_post: neon is NeonPost at conf 0.25, NMS 0.45");
    expect(same_v10 == 5 && v10_count == 5, "pipeline_post: v10 is yolov10_post at conf 0.25, max_det 300");

    auto throws = [](PostKind kind, const std::vector<rknn_tensor_attr>& attrs) {
        try {
            PipelinePost p(kind, attrs);
        } catch (const std::runtime_error&) {
            return true;
        }
        return false;
    };
    auto v10_classes = v10, v10_grid = v10, v10_nhwc = v10;
    v10_classes[3].dims[1] = 79;
    v10_grid[5].dims[2] = 21;
    v10_nhwc[0].fmt = RKNN_TENSOR_NHWC;
    expect(throws(PostKind::V10, v8) && throws(PostKind::V10, v10_classes) && throws(PostKind::V10, v10_grid) &&
               throws(PostKind::V10, v10_nhwc) && throws(PostKind::Neon, v10) && !throws(PostKind::M1, v10) &&
               !throws(PostKind::V10, v10),
           "pipeline_post: neon and v10 check the layout when built, m1 does not");
    bool five_attrs = false;
    try {
        const RandomOutputs o10(v10, rng);
        c10.run(o10.ptrs, {v10.begin(), v10.begin() + 5}, 640, 640);
    } catch (const std::runtime_error&) {
        five_attrs = true;
    }
    expect(five_attrs, "yolov10_post: rejects 6 outputs with 5 attrs");

    // The pipeline's three NPU workers share one PipelinePost: each thread's results equal a serial run.
    for (const PipelinePost* p : {&m1, &neon, &c10}) {
        const auto& attrs = p == &c10 ? v10 : v8;
        std::vector<RandomOutputs> sets;
        sets.reserve(3);  // ptrs point into each set's buffers
        std::vector<std::vector<Detection>> serial;
        for (int t = 0; t < 3; t++) {
            sets.emplace_back(attrs, rng);
            serial.push_back(p->run(sets.back().ptrs, attrs, 640, 640));
        }
        std::atomic<int> same{0};
        std::vector<std::thread> threads;
        for (int t = 0; t < 3; t++)
            threads.emplace_back([&, t] {
                bool ok = true;
                for (int i = 0; i < 4; i++)
                    ok &= same_detections(p->run(sets[t].ptrs, attrs, 640, 640), serial[t]);
                same += ok;
            });
        for (auto& th : threads)
            th.join();
        expect(same == 3, (std::string("pipeline_post: ") + post_kind_name(p->kind()) +
                           " from 3 threads at once equals a serial run").c_str());
    }
    expect(m1.describe() == "\"name\": \"m1\", \"conf_thresh\": 0.25, \"nms_thresh\": 0.45" &&
               c10.describe() == "\"name\": \"v10\", \"conf_thresh\": 0.25, \"max_det\": 300" &&
               neon.describe() == "\"name\": \"neon\", \"conf_thresh\": 0.25, \"nms_thresh\": 0.45, \"tables\": 3, "
                                  "\"table_bytes\": 786432",
           "pipeline_post: env.json fields");
}

void sha256_known_values() {
    expect(sha256_hex("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" &&
               sha256_hex("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad" &&
               sha256_hex("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq") ==
                   "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1" &&
               sha256_hex(std::string(1000000, 'a')) ==
                   "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0",
           "sha256: FIPS 180-4's examples");
    // Every length around the padding's edges, against sha256sum.
    std::mt19937 rng(7);
    std::string bytes(1300, '\0');
    for (auto& c : bytes)
        c = static_cast<char>(rng());
    bool same = true;
    for (size_t n : {size_t(55), size_t(56), size_t(63), size_t(64), size_t(65), size_t(119), size_t(120), size_t(1300)}) {
        const std::string path = "/tmp/test_core_sha256_" + std::to_string(n);
        FILE* f = std::fopen(path.c_str(), "wb");
        same = same && f && std::fwrite(bytes.data(), 1, n, f) == n && std::fclose(f) == 0 &&
               sha256_hex(bytes.substr(0, n)) == file_sha256(path);
        std::remove(path.c_str());
    }
    expect(same, "sha256: lengths 55 to 1300 as sha256sum");
}

// M4's YOLOv8n outputs at 640x640: three branches of {box, class scores, score sum}, class scale 1/256 so
// that int8 -64 is exactly 0.25.
std::vector<rknn_tensor_attr> v8_map_attrs() {
    std::vector<rknn_tensor_attr> attrs;
    for (int g : {80, 40, 20})
        for (int ch : {64, 80, 1}) {
            rknn_tensor_attr a;
            std::memset(&a, 0, sizeof a);
            a.n_dims = 4;
            a.dims[0] = 1;
            a.dims[1] = ch;
            a.dims[2] = a.dims[3] = g;
            a.fmt = RKNN_TENSOR_NCHW;
            a.type = RKNN_TENSOR_INT8;
            a.zp = -128;
            a.scale = ch == 64 ? 0.1f : 1.0f / 256;
            attrs.push_back(a);
        }
    return attrs;
}

void yolov8_map_rule() {
    const auto attrs = v8_map_attrs();
    std::vector<std::vector<int8_t>> bufs;
    std::vector<const int8_t*> ptrs;
    for (const auto& a : attrs)
        bufs.emplace_back(size_t(a.dims[1]) * a.dims[2] * a.dims[3], int8_t(-128));
    for (const auto& b : bufs)
        ptrs.push_back(b.data());
    const int len0 = 80 * 80;
    // Box bins one-hot at 4 on every side: a 64 × 64 px box centred on its cell.
    const auto peaked = [&](int branch, int cell) {
        const int len = attrs[branch * 3].dims[2] * attrs[branch * 3].dims[3];
        for (int side = 0; side < 4; side++)
            bufs[branch * 3][(side * 16 + 4) * len + cell] = 127;
    };
    // Cells 0 and 1 of stride 8 have boxes 8 px apart (IoU 0.78); cell 300 is far away.
    for (int cell : {0, 1, 300})
        peaked(0, cell);
    bufs[1][3 * len0 + 0] = 102;    // class 3 at cell 0: 0.9
    bufs[1][3 * len0 + 1] = 76;     // class 3 at cell 1: 0.8, overlaps cell 0's: dropped
    bufs[1][4 * len0 + 1] = 51;     // class 4 at cell 1: 0.7, another class: kept
    bufs[1][3 * len0 + 300] = 25;   // class 3 at cell 300: 0.6, far: kept. Every other score is 0.
    MapResult r = yolov8_map(ptrs, attrs, 640, 640);
    expect(r.dets.size() == 3 && r.candidates == 4 && r.dets[0].cls == 3 && r.dets[1].cls == 4 &&
               r.dets[2].cls == 3 && r.dets[2].score == (25.0f + 128) / 256 && !r.tied,
           "yolov8_map: class-wise greedy NMS at IoU 0.7");
    // The score sums are not read: yolov8_post would drop cells whose sum is below conf.
    for (int b = 0; b < 3; b++)
        std::fill(bufs[b * 3 + 2].begin(), bufs[b * 3 + 2].end(), int8_t(-128));
    expect(yolov8_map(ptrs, attrs, 640, 640).dets.size() == 3, "yolov8_map: the score sums are not used");
    const float lowest = (25.0f + 128) / 256;
    expect(yolov8_map(ptrs, attrs, 640, 640, std::nextafter(lowest, 0.0f)).dets.size() == 3 &&
               yolov8_map(ptrs, attrs, 640, 640, lowest).dets.size() == 2,
           "yolov8_map: conf is strict");

    // 402 equal scores of 0.75, 134 per branch, the k-th of a branch at cell k × step with class k % 80:
    // ties in branch, cell, class order; max_det, its tie, and max_nms. A class's boxes in one branch are
    // at least 7 rows apart (k and k + 80), and boxes of two branches differ in size (64, 128, 256 px):
    // no IoU reaches 0.7, so nothing is dropped.
    for (auto& b : bufs)
        std::fill(b.begin(), b.end(), int8_t(-128));
    for (int b = 0; b < 3; b++) {
        const int g = attrs[b * 3].dims[2], step = b == 2 ? 2 : 7;
        for (int k = 0; k < 134; k++) {
            peaked(b, k * step);
            bufs[b * 3 + 1][(k % 80) * g * g + k * step] = 64;
        }
    }
    r = yolov8_map(ptrs, attrs, 640, 640, 0.25f);
    bool order_ok = r.dets.size() == 300 && r.tied && r.candidates == 402;
    for (size_t i = 0; order_ok && i < r.dets.size(); i++)
        order_ok = r.dets[i].score == 0.75f;
    // The 300 kept: branch 0's 134, branch 1's 134 and branch 2's first 32, in that order.
    expect(order_ok && r.dets[133].cls == 133 % 80 && r.dets[134].cls == 0 && r.dets[299].cls == 31 &&
               std::fabs(r.dets[299].x1 - ((62 % 20) + 0.5f - 4) * 32) < 0.01f,
           "yolov8_map: equal scores in branch, cell, class order, 300 kept, the 301st tied");
    expect(yolov8_map(ptrs, attrs, 640, 640, 0.25f, 300, 250).dets.size() == 250,
           "yolov8_map: only the first max_nms go to the NMS");
    for (int k = 32; k < 134; k++)  // branch 2's from the 33rd on are lower now: no tie at the 300th
        bufs[7][(k % 80) * 400 + k * 2] = 63;
    r = yolov8_map(ptrs, attrs, 640, 640, 0.25f);
    expect(r.dets.size() == 300 && !r.tied && r.candidates == 402, "yolov8_map: no tie when the 301st is lower");

    bool threw = false;
    try {
        yolov8_map({ptrs.begin(), ptrs.begin() + 6}, {attrs.begin(), attrs.begin() + 6}, 640, 640);
    } catch (const std::runtime_error&) {
        threw = true;
    }
    auto swapped = attrs;
    std::swap_ranges(swapped.begin(), swapped.begin() + 3, swapped.begin() + 3);
    bool threw_order = false;
    try {
        yolov8_map_check_layout(swapped);
    } catch (const std::runtime_error&) {
        threw_order = true;
    }
    expect(threw && threw_order, "yolov8_map: rejects 6 outputs and branches out of stride order");
}

void yolov10_map_rule() {
    const auto attrs = v10_attrs();
    std::vector<std::vector<int8_t>> bufs;
    std::vector<const int8_t*> ptrs;
    for (const auto& a : attrs)
        bufs.emplace_back(size_t(a.dims[1]) * a.dims[2] * a.dims[3], int8_t(-128));
    for (const auto& b : bufs)
        ptrs.push_back(b.data());
    for (int b = 0; b < 3; b++)
        for (int k = 0; k < 134; k++)
            bufs[b * 2 + 1][(k % 80) * attrs[b * 2 + 1].dims[2] * attrs[b * 2 + 1].dims[3] + k] = 64;
    MapResult r = yolov10_map(ptrs, attrs, 640, 640, 0.25f);
    const auto want = yolov10_post(ptrs, attrs, 640, 640, 0.25f, 300);
    bool same = r.dets.size() == want.size();
    for (size_t i = 0; same && i < want.size(); i++)
        same = r.dets[i].cls == want[i].cls && r.dets[i].score == want[i].score && r.dets[i].x1 == want[i].x1 &&
               r.dets[i].y2 == want[i].y2;
    expect(same && r.candidates == 402 && r.tied, "yolov10_map: yolov10_post's 300, the candidates and the tie");
    // Branch 2 keeps its first 32 at 0.75 and one lower candidate: 301 candidates, the 301st not tied.
    for (int k = 32; k < 134; k++)
        bufs[5][(k % 80) * 400 + k] = k == 133 ? 63 : -128;
    r = yolov10_map(ptrs, attrs, 640, 640, 0.25f);
    expect(r.dets.size() == 300 && r.candidates == 301 && !r.tied, "yolov10_map: no tie when the 301st is lower");
}

void commit_sha_form() {
    expect(is_commit_sha("8638685d9ef806a85e5042d7cc10febdd5ae93a9"), "is_commit_sha: 40 lowercase hex digits");
    expect(!is_commit_sha("8638685D9EF806A85E5042D7CC10FEBDD5AE93A9") && !is_commit_sha("none") &&
               !is_commit_sha("8638685d9ef806a85e5042d7cc10febdd5ae93a") &&
               !is_commit_sha("8638685d9ef806a85e5042d7cc10febdd5ae93a90") &&
               !is_commit_sha("8638685d9ef806a85e5042d7cc10febdd5ae93ag") && !is_commit_sha(""),
           "is_commit_sha: not uppercase, none, 39 or 41 digits, a non-hex digit, empty");
}

void m4_run_rules() {
    const std::string freeze = "0123456789abcdef0123456789abcdef01234567";
    const std::string other_model(64, 'a');
    // The two m4 arms at the main point, and calib_npu's two m4 measurements per model.
    RunChoice c8;
    c8.kind = "m4";
    c8.post = PostKind::Neon;
    c8.variant = "C";
    c8.freeze_m4 = freeze;
    c8.model_sha = kYolov8nSha256;
    c8.channels = 8;
    c8.condition = "main";
    c8.stagger = true;
    RunChoice c10 = c8;
    c10.post = PostKind::V10;
    c10.model_sha = kYolov10nSha256;
    RunChoice cap8;
    cap8.calib = true;
    cap8.kind = "m4";
    cap8.post = PostKind::Neon;
    cap8.freeze_m4 = freeze;
    cap8.model_sha = kYolov8nSha256;
    cap8.cores = 3;
    RunChoice cap10 = cap8;
    cap10.post = PostKind::V10;
    cap10.model_sha = kYolov10nSha256;
    RunChoice raw10 = cap10;
    raw10.post = PostKind::M1;
    raw10.post_runs = false;
    raw10.cores = 1;
    RunChoice raw8 = raw10;
    raw8.model_sha = kYolov8nSha256;

    struct Case {
        const char* what;
        RunChoice c;
        bool ok;
    };
    std::vector<Case> cases;
    auto add = [&](const char* what, RunChoice c, bool ok) { cases.push_back({what, c, ok}); };
    add("C8 main", c8, true);
    add("C10 main", c10, true);
    {
        RunChoice x = c8;
        x.condition = "aux";
        x.channels = 5;
        x.stagger = false;
        add("C8 aux", x, true);
        x.post = PostKind::V10;
        x.model_sha = kYolov10nSha256;
        add("C10 aux", x, true);
    }
    add("P_cap YOLOv8n neon", cap8, true);
    add("P_cap YOLOv10n v10", cap10, true);
    add("raw YOLOv10n 1 core", raw10, true);
    add("raw YOLOv8n 1 core", raw8, true);
    auto with = [](RunChoice c, auto f) {
        f(c);
        return c;
    };
    // bench_main, m4
    add("m4 without freeze-m4", with(c10, [](RunChoice& c) { c.freeze_m4 = "none"; }), false);
    add("m4 freeze-m4 not a SHA", with(c10, [](RunChoice& c) { c.freeze_m4 = "abc"; }), false);
    add("m4 with m1", with(c8, [](RunChoice& c) { c.post = PostKind::M1; }), false);
    add("m4 C8 with YOLOv10n", with(c8, [](RunChoice& c) { c.model_sha = kYolov10nSha256; }), false);
    add("m4 C10 with YOLOv8n", with(c10, [](RunChoice& c) { c.model_sha = kYolov8nSha256; }), false);
    add("m4 with another model", with(c8, [&](RunChoice& c) { c.model_sha = other_model; }), false);
    add("m4 variant B", with(c8, [](RunChoice& c) { c.variant = "B"; }), false);
    add("m4 main without stagger", with(c8, [](RunChoice& c) { c.stagger = false; }), false);
    add("m4 main at 5 channels", with(c8, [](RunChoice& c) { c.channels = 5; }), false);
    add("m4 aux at 8 channels", with(c8, [](RunChoice& c) {
            c.condition = "aux";
            c.stagger = false;
        }), false);
    add("m4 on the A55 cores", with(c8, [](RunChoice& c) { c.a55 = true; }), false);
    add("m4 with K = 4", with(c8, [](RunChoice& c) { c.prep_threads = 4; }), false);
    // calib_npu, m4
    add("calib m4 without freeze-m4", with(cap10, [](RunChoice& c) { c.freeze_m4 = "none"; }), false);
    add("calib m4 P_cap with m1", with(cap8, [](RunChoice& c) { c.post = PostKind::M1; }), false);
    add("calib m4 P_cap neon on YOLOv10n", with(cap10, [](RunChoice& c) { c.post = PostKind::Neon; }), false);
    add("calib m4 P_cap v10 on YOLOv8n", with(cap8, [](RunChoice& c) { c.post = PostKind::V10; }), false);
    add("calib m4 P_cap on 1 core", with(cap10, [](RunChoice& c) { c.cores = 1; }), false);
    add("calib m4 raw on 3 cores", with(raw10, [](RunChoice& c) { c.cores = 3; }), false);
    add("calib m4 raw with another model", with(raw10, [&](RunChoice& c) { c.model_sha = other_model; }), false);
    // Other run kinds
    RunChoice m1_default;  // bench_main's defaults: variant C, M1's post-processing and model
    m1_default.kind = "check";
    m1_default.variant = "C";
    m1_default.model_sha = kYolov8nSha256;
    add("default C, check", m1_default, true);
    add("comparison with another model", with(m1_default, [&](RunChoice& c) {
            c.kind = "comparison";
            c.model_sha = other_model;
        }), true);
    add("m2 Cp", with(m1_default, [&](RunChoice& c) {
            c.kind = "m2";
            c.post = PostKind::Neon;
        }), true);
    add("check C10", with(c10, [](RunChoice& c) {
            c.kind = "check";
            c.freeze_m4 = "none";
            c.channels = 2;
        }), true);
    add("v10 with run kind m2", with(c10, [](RunChoice& c) { c.kind = "m2"; }), false);
    add("v10 with YOLOv8n, check", with(c10, [](RunChoice& c) {
            c.kind = "check";
            c.model_sha = kYolov8nSha256;
        }), false);
    add("YOLOv10n with m1, comparison", with(m1_default, [](RunChoice& c) {
            c.kind = "comparison";
            c.model_sha = kYolov10nSha256;
        }), false);
    add("neon with variant B, m2", with(m1_default, [](RunChoice& c) {
            c.kind = "m2";
            c.post = PostKind::Neon;
            c.variant = "B";
        }), false);
    add("freeze-m4 not a SHA, check", with(m1_default, [](RunChoice& c) { c.freeze_m4 = "abc"; }), false);
    add("calib calibration, M1-3", with(raw8, [](RunChoice& c) {
            c.kind = "calibration";
            c.freeze_m4 = "none";
            c.cores = 2;
        }), true);
    add("calib calibration P_cap m1", with(cap8, [](RunChoice& c) {
            c.kind = "calibration";
            c.post = PostKind::M1;
            c.freeze_m4 = "none";
        }), true);
    add("calib calibration with neon", with(cap8, [](RunChoice& c) {
            c.kind = "calibration";
            c.freeze_m4 = "none";
        }), false);
    add("calib calibration raw on YOLOv10n", with(raw10, [](RunChoice& c) {
            c.kind = "calibration";
            c.freeze_m4 = "none";
        }), false);
    add("calib check P_cap neon", with(cap8, [](RunChoice& c) {
            c.kind = "check";
            c.freeze_m4 = "none";
            c.cores = 2;
        }), true);
    int right = 0;
    for (const auto& k : cases) {
        const std::string problem = m4_rule_problem(k.c);
        if (problem.empty() == k.ok)
            right++;
        else
            std::printf("     %s: %s\n", k.what, problem.empty() ? "accepted" : problem.c_str());
    }
    char what[96];
    std::snprintf(what, sizeof what, "run_rules: %zu combinations accepted or rejected as M4 says", cases.size());
    expect(right == static_cast<int>(cases.size()), what);
}

// prereg/M6.md §1 and §8 step 2: the eight arms, and what an m6 run may be (core/run_rules.h).
void m6_run_rules() {
    const std::string freeze(40, 'a');
    struct ArmCase {
        const char* variant;
        PostKind post;
        const char* cores;
        bool time_rknn;
        const char* arm;
    };
    const ArmCase arm_cases[] = {
        {"B", PostKind::M1, "a76", false, "B"},     {"Bd", PostKind::M1, "a76", false, "Bd"},
        {"Rc", PostKind::M1, "a76", false, "Rc"},   {"C", PostKind::M1, "a76", false, "C"},
        {"By", PostKind::M1, "a76", false, "By"},   {"C", PostKind::Neon, "a76", false, "Cp"},
        {"C", PostKind::Neon, "all", false, "Cpf"}, {"C", PostKind::M1, "a76", true, "Ct"},
        // not arms
        {"A", PostKind::M1, "a76", false, ""},      {"none", PostKind::M1, "a76", false, ""},
        {"B", PostKind::M1, "all", false, ""},      {"B", PostKind::M1, "a76", true, ""},
        {"C", PostKind::M1, "all", false, ""},      {"C", PostKind::Neon, "all", true, ""},
        {"C", PostKind::Neon, "a76", true, ""},     {"C", PostKind::V10, "a76", false, ""},
        {"C", PostKind::M1, "a55", false, ""},      {"Rc", PostKind::Neon, "a76", false, ""},
        {"By", PostKind::M1, "all", false, ""},
    };
    int arms_right = 0, arms_total = 0;
    for (const ArmCase& a : arm_cases) {
        arms_total++;
        arms_right += m6_arm(a.variant, a.post, a.cores, a.time_rknn) == a.arm;
    }
    expect(arms_right == arms_total, "run_rules: m6_arm names the eight arms and nothing else");

    M6Choice ok;  // arm C at the point, as the session tool runs it
    ok.kind = "m6";
    ok.variant = "C";
    ok.freeze_m6 = freeze;
    ok.channels = 8;
    ok.condition = "main";
    ok.warmup_s = 20;
    ok.duration_s = 120;
    ok.model_path = "models/yolov8n_i8.rknn";
    ok.model_sha = kYolov8nSha256;
    struct Case {
        const char* what;
        M6Choice c;
        bool ok;
    };
    std::vector<Case> cases;
    auto add = [&](const char* what, M6Choice c, bool accepted) { cases.push_back({what, c, accepted}); };
    auto with = [](M6Choice c, auto f) {
        f(c);
        return c;
    };
    for (const ArmCase& a : arm_cases)
        add(a.arm[0] ? "m6: an arm" : "m6: not an arm", with(ok, [&](M6Choice& c) {
                c.variant = a.variant;
                c.post = a.post;
                c.cores = a.cores;
                c.time_rknn = a.time_rknn;
            }), a.arm[0] != 0);
    add("m6 without freeze-m6", with(ok, [](M6Choice& c) { c.freeze_m6 = "none"; }), false);
    add("m6 freeze-m6 not a SHA", with(ok, [](M6Choice& c) { c.freeze_m6 = "abc"; }), false);
    add("m6 at 4 channels", with(ok, [](M6Choice& c) { c.channels = 4; }), false);
    add("m6 aux", with(ok, [](M6Choice& c) { c.condition = "aux"; }), false);
    add("m6 with a 10 s window", with(ok, [](M6Choice& c) { c.duration_s = 10; }), false);
    add("m6 with a 5 s warm-up", with(ok, [](M6Choice& c) { c.warmup_s = 5; }), false);
    add("m6 staggered", with(ok, [](M6Choice& c) { c.stagger = true; }), false);
    add("m6 with K = 4", with(ok, [](M6Choice& c) { c.prep_threads = 4; }), false);
    add("m6 with another model file", with(ok, [](M6Choice& c) { c.model_sha = kYolov10nSha256; }), false);
    add("m6 with another model path", with(ok, [](M6Choice& c) { c.model_path = "other.rknn"; }), false);
    add("m6 with freeze-m3's commit", with(ok, [](M6Choice& c) { c.other_freeze = true; }), false);
    // the other run kinds
    M6Choice check = ok;
    check.kind = "check";
    check.freeze_m6 = "none";
    check.channels = 1;
    check.duration_s = 10;
    add("check: any shape", check, true);
    add("check Rc", with(check, [](M6Choice& c) { c.variant = "Rc"; }), true);
    add("check By, time-rknn, all cores", with(check, [](M6Choice& c) {
            c.variant = "By";
            c.time_rknn = true;
            c.cores = "all";
        }), true);
    add("check with freeze-m6's commit", with(check, [&](M6Choice& c) { c.freeze_m6 = freeze; }), true);
    add("check freeze-m6 not a SHA", with(check, [](M6Choice& c) { c.freeze_m6 = "abc"; }), false);
    for (const char* kind : {"comparison", "m1.1", "m2", "m3", "m4", "calibration", "equivalence", "reference"}) {
        M6Choice other = check;
        other.kind = kind;
        add("another kind: B, C as before", other, true);
        add("another kind: Rc", with(other, [](M6Choice& c) { c.variant = "Rc"; }), false);
        add("another kind: By", with(other, [](M6Choice& c) { c.variant = "By"; }), false);
        add("another kind: all cores", with(other, [](M6Choice& c) { c.cores = "all"; }), false);
        add("another kind: time-rknn", with(other, [](M6Choice& c) { c.time_rknn = true; }), false);
    }
    int right = 0;
    for (const auto& k : cases) {
        const std::string problem = m6_rule_problem(k.c);
        if (problem.empty() == k.ok)
            right++;
        else
            std::printf("     %s: %s\n", k.what, problem.empty() ? "accepted" : problem.c_str());
    }
    char what[96];
    std::snprintf(what, sizeof what, "run_rules: %zu combinations accepted or rejected as M6 says", cases.size());
    expect(right == static_cast<int>(cases.size()), what);
}

// ---- M7 (prereg/M7.md §1) ----

void m7_run_rules() {
    struct ArmCase {
        const char* variant;
        PostKind post;
        const char* cores;
        bool time_rknn, snapshot, counters;
        const char* arm;
    };
    const ArmCase arms[] = {
        {"B", PostKind::M1, "a76", false, true, false, "B"},     {"Bd", PostKind::M1, "a76", false, true, false, "Bd"},
        {"Rc", PostKind::M1, "a76", false, true, false, "Rc"},   {"C", PostKind::M1, "a76", false, true, false, "C"},
        {"B", PostKind::M1, "a76", false, true, true, "Bk"},     {"Bd", PostKind::M1, "a76", false, true, true, "Bdk"},
        {"Rc", PostKind::M1, "a76", false, true, true, "Rck"},   {"C", PostKind::M1, "a76", false, true, true, "Ck"},
        {"C", PostKind::M1, "a76", true, true, false, "Ct"},     {"C", PostKind::Neon, "a76", true, true, false, "Cpt"},
        // not arms: no snapshot, the counters with Ct, other variants, other cores, other post-processings
        {"C", PostKind::M1, "a76", false, false, false, ""},     {"C", PostKind::M1, "a76", false, false, true, ""},
        {"C", PostKind::M1, "a76", true, true, true, ""},        {"C", PostKind::Neon, "a76", true, true, true, ""},
        {"By", PostKind::M1, "a76", false, true, false, ""},     {"A", PostKind::M1, "a76", false, true, false, ""},
        {"none", PostKind::M1, "a76", false, true, false, ""},   {"C", PostKind::M1, "all", false, true, false, ""},
        {"C", PostKind::M1, "a55", false, true, false, ""},      {"C", PostKind::Neon, "a76", false, true, false, ""},
        {"C", PostKind::V10, "a76", true, true, false, ""},      {"B", PostKind::M1, "a76", true, true, false, ""},
        {"B", PostKind::Neon, "a76", false, true, false, ""},
    };
    int arms_right = 0, arms_total = 0;
    for (const auto& a : arms) {
        arms_total++;
        arms_right += m7_arm(a.variant, a.post, a.cores, a.time_rknn, a.snapshot, a.counters) == a.arm;
    }
    expect(arms_right == arms_total, "run_rules: m7_arm names the ten arms and nothing else");

    int at_right = 0, at_total = 0;
    for (const char* arm : {"B", "Bd", "Rc", "C"}) {
        at_total += 2;
        at_right += m7_arm_at(arm, "main") + m7_arm_at(arm, "aux");
    }
    for (const char* arm : {"Bk", "Bdk", "Rck", "Ck"}) {
        at_total += 2;
        at_right += m7_arm_at(arm, "main") + !m7_arm_at(arm, "aux");
    }
    for (const char* arm : {"Ct", "Cpt"}) {
        at_total += 2;
        at_right += !m7_arm_at(arm, "main") + m7_arm_at(arm, "aux");
    }
    at_total += 2;
    at_right += !m7_arm_at("", "main") + !m7_arm_at("C", "other");
    expect(at_right == at_total, "run_rules: each arm of M7 at its points only");

    const std::string sha(40, 'a');
    auto base = [&](const char* variant, const char* condition, int channels) {
        M7Choice c;
        c.kind = "m7";
        c.variant = variant;
        c.snapshot = true;
        c.freeze_m7 = sha;
        c.channels = channels;
        c.condition = condition;
        c.warmup_s = 20;
        c.duration_s = 120;
        c.model_path = "models/yolov8n_i8.rknn";
        c.model_sha = kYolov8nSha256;
        return c;
    };
    struct Case {
        const char* what;
        M7Choice c;
        bool ok;
    };
    std::vector<Case> cases;
    for (const char* v : {"B", "Bd", "Rc", "C"}) {
        cases.push_back({"a cell at the main point", base(v, "main", 8), true});
        cases.push_back({"a cell at the auxiliary point", base(v, "aux", 5), true});
        M7Choice k = base(v, "main", 8);
        k.thread_counters = true;
        cases.push_back({"a counted arm at the main point", k, true});
        k = base(v, "aux", 5);
        k.thread_counters = true;
        cases.push_back({"a counted arm at the auxiliary point", k, false});
    }
    {
        M7Choice c = base("C", "aux", 5);
        c.time_rknn = true;
        cases.push_back({"Ct at the auxiliary point", c, true});
        c.post = PostKind::Neon;
        cases.push_back({"Cpt at the auxiliary point", c, true});
        c = base("C", "main", 8);
        c.time_rknn = true;
        cases.push_back({"Ct at the main point", c, false});
        c = base("C", "main", 8);
        c.snapshot = false;
        cases.push_back({"an m7 run without the snapshots", c, false});
        c = base("C", "main", 8);
        c.freeze_m7 = "none";
        cases.push_back({"an m7 run without freeze-m7's commit", c, false});
        c = base("C", "main", 8);
        c.freeze_m7 = "abc";
        cases.push_back({"a freeze-m7 commit that is not a SHA", c, false});
        c = base("C", "main", 5);
        cases.push_back({"5 channels at the main condition", c, false});
        c = base("C", "aux", 8);
        cases.push_back({"8 channels at the auxiliary condition", c, false});
        c = base("C", "main", 8);
        c.warmup_s = 10;
        cases.push_back({"another warm-up", c, false});
        c = base("C", "main", 8);
        c.duration_s = 10;
        cases.push_back({"another window", c, false});
        c = base("C", "main", 8);
        c.stagger = true;
        cases.push_back({"the staggered sampler", c, false});
        c = base("C", "main", 8);
        c.prep_threads = 4;
        cases.push_back({"K = 4", c, false});
        c = base("C", "main", 8);
        c.model_sha = kYolov10nSha256;
        cases.push_back({"another model", c, false});
        c = base("C", "main", 8);
        c.model_path = "models/other.rknn";
        cases.push_back({"another model path", c, false});
        c = base("C", "main", 8);
        c.other_freeze = true;
        cases.push_back({"another freeze commit", c, false});
        c = base("C", "main", 8);
        c.cores = "all";
        cases.push_back({"the pipeline not pinned", c, false});
        c = base("By", "main", 8);
        cases.push_back({"By", c, false});
        // the options outside M7
        c = base("C", "main", 8);
        c.kind = "check";
        c.freeze_m7 = "none";
        c.duration_s = 10;
        c.thread_counters = true;
        cases.push_back({"a check run with both options and a 10 s window", c, true});
        c.warmup_s = 2;
        cases.push_back({"the options with a 2 s warm-up", c, false});
        c.warmup_s = 3;
        cases.push_back({"the options with a 3 s warm-up", c, true});
        for (const char* kind : {"m6", "m3", "m4", "m2", "m1.1", "comparison", "calibration", "reference"}) {
            c = base("C", "main", 8);
            c.kind = kind;
            c.freeze_m7 = "none";
            cases.push_back({"--snapshot on in another run kind", c, false});
            c.snapshot = false;
            c.thread_counters = true;
            cases.push_back({"--thread-counters on in another run kind", c, false});
            c.thread_counters = false;
            cases.push_back({"another run kind without the options", c, true});
        }
    }
    int right = 0;
    for (const auto& k : cases) {
        const std::string problem = m7_rule_problem(k.c);
        if (problem.empty() == k.ok)
            right++;
        else
            std::printf("     %s: %s\n", k.what, problem.empty() ? "accepted" : problem.c_str());
    }
    char what[96];
    std::snprintf(what, sizeof what, "run_rules: %zu combinations accepted or rejected as M7 says", cases.size());
    expect(right == static_cast<int>(cases.size()), what);

    // M6's rule lets run kind m7 use Rc and --time-rknn on; what an m7 run is stays M7's rule.
    M6Choice m6;
    m6.kind = "m7";
    m6.variant = "Rc";
    m6.time_rknn = true;
    expect(m6_rule_problem(m6).empty(), "run_rules: M6's rule leaves an m7 run to M7's");
    m6.kind = "m3";
    expect(!m6_rule_problem(m6).empty(), "run_rules: Rc stays out of the other run kinds");
}

void snapshot_helpers() {
    expect(stat_ppid("12 (kworker/0:1) I 2 0 0 0") == 2, "snapshot: ppid of a kernel thread's stat");
    expect(stat_ppid("345 (a b) c) S 1 345 345") == 1, "snapshot: ppid after a name with spaces and parentheses");
    expect(stat_ppid("no parenthesis") == -1 && stat_ppid("7 (x)") == -1, "snapshot: no ppid in a broken stat");
    expect(csv_field("npu-0") == "npu-0" && csv_field("a,b") == "\"a,b\"" && csv_field("a\"b") == "\"a\"\"b\"",
           "snapshot: a name with a comma or a quote is quoted");
    uint64_t config = 0;
    expect(parse_event_config("event=0x0011\n", &config) && config == 0x11, "counters: an event's code from sysfs");
    expect(parse_event_config("event=0x08,long=1\n", &config) && config == 0x8, "counters: a code with more terms");
    expect(!parse_event_config("", &config) && !parse_event_config("config=1", &config),
           "counters: no code without an event= term");
}

void snapshot_reads_the_system() {
    std::atomic<bool> stop{false};
    std::thread other([&] {
        while (!stop)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
    });
    int hook_calls = 0;
    const SystemSnapshot s = take_system_snapshot(0, [&] { hook_calls++; });
    stop = true;
    other.join();
    expect(s.taken && s.complete && hook_calls == 1, "snapshot: complete, and the hook ran once");
    expect(s.t_begin <= s.t_schedstat && s.t_schedstat <= s.t_end, "snapshot: its three times are in order");
    expect(s.schedstat.rfind("version", 0) == 0 && s.stat.rfind("cpu", 0) == 0 && !s.interrupts.empty() &&
               !s.softirqs.empty(),
           "snapshot: the four /proc files were read");
    const int self = getpid();
    bool own_has_self = false, own_complete = s.own.size() >= 2;
    for (const auto& t : s.own) {
        own_has_self |= t.tid == self;
        own_complete &= t.run_ns >= 0 && t.timeslices >= 0 && t.voluntary_cs >= 0 && t.involuntary_cs >= 0 &&
                        !t.comm.empty();
    }
    expect(own_has_self && own_complete, "snapshot: this process's threads, each with its five values");
    bool none_is_self = true, has_init = false, has_kthreadd = false, kernel_right = true;
    for (const auto& t : s.others) {
        none_is_self &= t.pid != self;
        has_init |= t.pid == 1 && !t.kernel;
        has_kthreadd |= t.pid == 2 && t.kernel;
        kernel_right &= t.pid != 2 || t.kernel;
    }
    expect(none_is_self, "snapshot: no thread of this process among the other tasks");
    expect(has_init && has_kthreadd && kernel_right, "snapshot: pid 1 is not a kernel thread, pid 2 is");

    const SystemSnapshot late = take_system_snapshot(now_ns() - 1, nullptr);
    expect(late.taken && !late.complete && late.others.empty() && !late.schedstat.empty(),
           "snapshot: past its deadline it stops, incomplete");

    int64_t run = -1, wait = -1, slices = -1;
    expect(read_task_schedstat(self, self, &run, &wait, &slices) && run > 0 && slices > 0,
           "snapshot: a thread's schedstat");
    expect(!read_task_schedstat(self, 0x7ffffff0, &run, nullptr, nullptr), "snapshot: no schedstat for a thread that is not there");
}

// The counters need the PMU and root (perf_event_paranoid is 2 on the board, and the groups count in
// kernel mode): run build/test_core as root to cover them.
void thread_counters_count() {
    if (geteuid() != 0) {
        std::printf("note counters: not run as root, the groups are not opened here\n");
        return;
    }
    std::atomic<bool> go{false}, stop{false};
    std::atomic<int> busy_tid{0};
    std::atomic<uint64_t> sink{0};
    std::thread busy([&] {
        busy_tid = static_cast<int>(syscall(SYS_gettid));
        while (!go)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        uint64_t x = 1;
        while (!stop)
            x = x * 6364136223846793005ull + 1442695040888963407ull;
        sink = x;
    });
    while (!busy_tid)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    ThreadCounters counters;
    counters.open_all();
    counters.enable_all();
    go = true;
    std::this_thread::sleep_for(std::chrono::milliseconds(300));
    counters.disable_all();
    stop = true;
    busy.join();

    bool events_found = counters.events().size() == static_cast<size_t>(kThreadCounterEventCount);
    for (const auto& e : counters.events())
        events_found &= e.found;
    expect(events_found, "counters: every event of the group is in sysfs");
    bool all_open = counters.threads().size() >= 2, all_read = true, never_multiplexed = true;
    const ThreadCount* b = nullptr;
    for (const auto& t : counters.threads()) {
        all_open &= t.opened;
        all_read &= t.enable_ok && t.disable_ok && t.read_ok && t.values.size() == counters.events().size();
        never_multiplexed &= t.time_running == t.time_enabled;
        if (t.tid == busy_tid)
            b = &t;
        if (!t.opened)
            std::printf("     tid %d (%s): %s, errno %d\n", t.tid, t.comm.c_str(), t.failed_event.c_str(), t.open_errno);
    }
    expect(all_open && all_read, "counters: a group on every thread, each enabled, disabled and read");
    expect(never_multiplexed, "counters: time_running equals time_enabled");
    if (!b || !b->read_ok) {
        expect(false, "counters: the busy thread has its group");
        return;
    }
    const double run_s = (b->run_ns_disable - b->run_ns_enable) / 1e9, enabled_s = b->time_enabled / 1e9;
    std::printf("     busy thread: run %.3f s, enabled %.3f s, cycles %llu, instructions %llu\n", run_s, enabled_s,
                static_cast<unsigned long long>(b->values[0]), static_cast<unsigned long long>(b->values[1]));
    expect(run_s > 0.2 && run_s < 0.4 && std::fabs(enabled_s - run_s) < 0.2 * run_s,
           "counters: the busy thread ran about 0.3 s by its schedstat and by its group");
    // 0.3 s at 408 MHz or more, and more than one instruction per ten cycles
    expect(b->values[0] > 100000000ull && b->values[1] > b->values[0] / 10,
           "counters: the busy thread's cycles and instructions");

    CpuCycleCounter cpu;
    cpu.open(0);
    cpu.enable();
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    cpu.read_now();
    cpu.close();
    expect(cpu.open_errno == 0 && cpu.enabled && cpu.read_ok && cpu.time_running == cpu.time_enabled &&
               cpu.time_enabled > 90000000ull,
           "counters: cpu0's cycle counter opens, runs for 0.1 s and reads");
}

}  // namespace

int main() {
    queue_drops_oldest();
    queue_blocks_when_full();
    queue_close_wakes_everyone();
    slot_pool_cycles();
    decoder_drop_rule();
    drift_formula();
    percentile_matches_numpy();
    schedstat_parse();
    neon_post_matches_m1();
    yolov10_post_selects();
    yolov10_post_matches_reference();
    pipeline_post_dispatches();
    sha256_known_values();
    yolov8_map_rule();
    yolov10_map_rule();
    commit_sha_form();
    m4_run_rules();
    m6_run_rules();
    m7_run_rules();
    snapshot_helpers();
    snapshot_reads_the_system();
    thread_counters_count();
    std::printf("%d failure(s)\n", failures);
    return failures ? 1 : 0;
}
