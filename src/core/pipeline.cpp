#include "core/pipeline.h"

#include <algorithm>
#include <atomic>
#include <climits>
#include <cmath>
#include <condition_variable>
#include <memory>
#include <mutex>
#include <thread>

#include "core/slot_pool.h"
#include "core/stage_queue.h"
#include "core/stream_checks.h"
#include "core/util.h"
#include "core/video_source.h"
#include "core/yolov8_post.h"

namespace {

constexpr int kPreprocessThreads = 2;  // K
constexpr size_t kQ2Depth = 6;
constexpr int kNominalFps = 30;
constexpr int64_t kSecond = 1000000000;

struct Sampled {
    AVFrame* frame;
    int channel;
    int64_t seq, pts, t0;
};

struct Prepared {
    int channel;
    int64_t seq, pts, t0, t1, t2a, wait_ns;
    int slot;
};

struct Channel {
    std::unique_ptr<VideoSource> src;
    std::atomic<int64_t> decoded{0};
    std::atomic<int64_t> q1_drops{0};
    // Written by the channel's decoder thread only, read after it joins.
    int64_t counter = 0;  // decode order, for the sampler
    int64_t last_pts = AV_NOPTS_VALUE;
    int64_t sampled = 0, drops = 0;
    bool ended_early = false;
};

void sleep_until_ns(int64_t t) {
    timespec ts{static_cast<time_t>(t / kSecond), static_cast<long>(t % kSecond)};
    while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, nullptr) != 0) {
    }
}

}  // namespace

RunResult run_pipeline(const PipelineConfig& cfg, NpuContexts* npu, Backend* backend) {
    const int n = static_cast<int>(cfg.sources.size());
    std::vector<std::unique_ptr<Channel>> ch;
    for (int i = 0; i < n; i++)
        ch.push_back(std::make_unique<Channel>());
    std::atomic<bool> stop{false};
    StageQueue<Sampled> q1(2 * n);
    StageQueue<Prepared> q2(kQ2Depth);
    SlotPool slots(kInputSlots);
    RunResult res;
    std::atomic<int64_t> t_w{INT64_MAX}, t_e{INT64_MAX};
    auto in_window = [&](int64_t t) { return t >= t_w.load() && t < t_e.load(); };

    // ---- decoders: open all inputs, then start the timeline ----
    std::mutex ready_m;
    std::condition_variable ready_cv;
    int ready = 0;
    bool go = false;
    std::vector<std::thread> decoders;
    for (int i = 0; i < n; i++) {
        decoders.emplace_back([&, i] {
            Channel& c = *ch[i];
            c.src = std::make_unique<VideoSource>(cfg.sources[i]);
            c.src->open(&stop);
            {
                std::unique_lock<std::mutex> lk(ready_m);
                ready++;
                ready_cv.notify_all();
                ready_cv.wait(lk, [&] { return go; });
            }
            const AVRational tb = c.src->time_base();
            auto on_frame = [&](AVFrame* f) {
                const int64_t t0 = now_ns();
                c.decoded.fetch_add(1, std::memory_order_relaxed);
                const bool window = in_window(t0);
                const int64_t pts = f->pts != AV_NOPTS_VALUE ? f->pts : f->best_effort_timestamp;
                if (window) {  // decoder drops, PROTOCOL §7.6
                    if (c.last_pts != AV_NOPTS_VALUE && pts != AV_NOPTS_VALUE)
                        c.drops += missing_frames(pts - c.last_pts, tb.num, tb.den, kNominalFps);
                    if ((f->flags & AV_FRAME_FLAG_CORRUPT) || f->decode_error_flags)
                        c.drops++;
                }
                if (pts != AV_NOPTS_VALUE)
                    c.last_pts = pts;
                if (c.counter++ % cfg.sample_stride != 0) {  // sampler
                    av_frame_free(&f);
                    return;
                }
                if (window)
                    c.sampled++;
                if (!backend) {  // variant none
                    av_frame_free(&f);
                    return;
                }
                auto dropped = q1.push_drop_oldest({f, i, c.counter - 1, pts, t0});
                if (dropped) {
                    if (in_window(dropped->t0))  // same window rule as the sampled frames
                        ch[dropped->channel]->q1_drops++;
                    av_frame_free(&dropped->frame);
                }
            };
            auto on_error = [&] {
                if (in_window(now_ns()))
                    c.drops++;
            };
            c.ended_early = c.src->run(on_frame, on_error);
        });
    }
    {
        std::unique_lock<std::mutex> lk(ready_m);
        ready_cv.wait(lk, [&] { return ready == n; });
        res.t_start = now_ns();
        res.t_w = res.t_start + cfg.warmup_s * kSecond;
        res.t_e = res.t_w + cfg.duration_s * kSecond;
        t_w = res.t_w;
        t_e = res.t_e;
        go = true;
    }
    ready_cv.notify_all();

    // ---- preprocessing pool and NPU workers ----
    std::vector<std::thread> pre, workers;
    std::vector<std::vector<FrameRecord>> records(NpuContexts::kCount);
    if (backend) {
        // Room for every frame the run can sample, so the records do not grow (and show up as
        // RSS growth) during the window.
        const size_t expected = size_t(n) * kNominalFps / cfg.sample_stride * (cfg.warmup_s + cfg.duration_s + 10);
        for (auto& v : records)
            v.reserve(expected / NpuContexts::kCount + 64);
        for (int p = 0; p < kPreprocessThreads; p++) {
            pre.emplace_back([&] {
                while (auto item = q1.pop()) {
                    const int64_t a0 = now_ns();
                    const int slot = slots.acquire();
                    const int64_t t1 = now_ns();
                    if (slot < 0) {
                        av_frame_free(&item->frame);
                        break;
                    }
                    backend->prepare(item->frame, item->channel, slot);
                    av_frame_free(&item->frame);  // released right after prepare
                    const int64_t t2a = now_ns();
                    if (!q2.push({item->channel, item->seq, item->pts, item->t0, t1, t2a, t1 - a0, slot})) {
                        slots.release(slot);
                        break;
                    }
                }
            });
        }
        for (int k = 0; k < NpuContexts::kCount; k++) {
            workers.emplace_back([&, k] {
                while (auto p = q2.pop()) {
                    FrameRecord r{};
                    r.channel = p->channel;
                    r.seq = p->seq;
                    r.pts = p->pts;
                    r.t0 = p->t0;
                    r.t1 = p->t1;
                    r.t2a = p->t2a;
                    r.slot_wait_ns = p->wait_ns;
                    r.ctx = k;
                    r.slot = p->slot;
                    r.t2b = now_ns();
                    backend->bind(*npu, k, p->slot);
                    r.t2c = now_ns();
                    CHECK(rknn_run(npu->ctx[k], nullptr) == RKNN_SUCC, "rknn_run failed");
                    r.t3 = now_ns();
                    r.detections = static_cast<int>(
                        yolov8_post(npu->output_ptrs(k), npu->outputs, npu->model_w(), npu->model_h()).size());
                    r.t4 = now_ns();
                    slots.release(p->slot);
                    records[k].push_back(r);
                }
            });
        }
    }

    // ---- metrics thread on cpu0: one sample per second, CPU snapshots at t_w and t_e ----
    std::thread metrics([&] {
        pin_current_thread({0});
        for (int64_t tick = res.t_start + kSecond;; tick += kSecond) {
            sleep_until_ns(tick);
            // Frame counts first, at the window's start and end alike, then the CPU snapshot.
            std::vector<int64_t> decoded(n);
            for (int i = 0; i < n; i++)
                decoded[i] = ch[i]->decoded.load();
            res.samples.push_back(take_sample(decoded));
            if (tick == res.t_w)
                res.cpu_w = take_cpu_snapshot();
            if (tick == res.t_e) {
                res.cpu_e = take_cpu_snapshot();
                break;
            }
        }
    });
    metrics.join();

    // ---- stop: inputs first, then each stage in order ----
    stop = true;
    for (auto& t : decoders)
        t.join();
    q1.close();
    slots.close();
    for (auto& t : pre)
        t.join();
    for (auto& item : q1.drain())
        av_frame_free(&item.frame);
    q2.close();
    for (auto& t : workers)
        t.join();

    // ---- results ----
    const size_t w_idx = cfg.warmup_s - 1;  // sample k is taken at t_start + (k + 1) s
    const size_t e_idx = cfg.warmup_s + cfg.duration_s - 1;
    CHECK(res.samples.size() == e_idx + 1, "missing samples");
    for (int i = 0; i < n; i++) {
        ChannelResult r;
        const int64_t base = res.samples[w_idx].decoded[i];
        r.decoded_in_window = res.samples[e_idx].decoded[i] - base;
        r.drift_max_s = -1e9;
        for (size_t j = w_idx + 1; j <= e_idx; j++)  // sample j is (j - w_idx) s after t_w
            r.drift_max_s = std::max(r.drift_max_s,
                                     drift_s(double(j - w_idx), res.samples[j].decoded[i] - base, kNominalFps));
        r.decoder_drops = ch[i]->drops;
        r.sampled_in_window = ch[i]->sampled;
        r.q1_drops_in_window = ch[i]->q1_drops;
        r.ended_early = ch[i]->ended_early;
        res.channels.push_back(r);
    }
    for (auto& v : records)
        res.frames.insert(res.frames.end(), v.begin(), v.end());
    std::sort(res.frames.begin(), res.frames.end(),
              [](const FrameRecord& a, const FrameRecord& b) { return a.t0 < b.t0; });
    return res;
}
