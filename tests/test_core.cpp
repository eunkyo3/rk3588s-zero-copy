// Unit tests for the pipeline's queue, slot pool, receive checks and percentile, the /proc/schedstat
// parser, arm Cp's post-processing against M1's on random outputs, and M4's YOLOv10 selection.
// Run: build/test_core
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <random>
#include <stdexcept>
#include <thread>
#include <vector>

#include "core/metrics.h"
#include "core/report.h"
#include "core/slot_pool.h"
#include "core/stage_queue.h"
#include "core/stream_checks.h"
#include "core/yolov8_post.h"
#include "core/yolov8_post_neon.h"
#include "core/yolov10_post.h"

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
    std::printf("%d failure(s)\n", failures);
    return failures ? 1 : 0;
}
