// Unit tests for the pipeline's queue, slot pool, receive checks and percentile. Run: build/test_core
#include <atomic>
#include <chrono>
#include <cstdio>
#include <thread>
#include <vector>

#include "core/report.h"
#include "core/slot_pool.h"
#include "core/stage_queue.h"
#include "core/stream_checks.h"

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

}  // namespace

int main() {
    queue_drops_oldest();
    queue_blocks_when_full();
    queue_close_wakes_everyone();
    slot_pool_cycles();
    decoder_drop_rule();
    drift_formula();
    percentile_matches_numpy();
    std::printf("%d failure(s)\n", failures);
    return failures ? 1 : 0;
}
