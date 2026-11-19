// The detector: the pipeline of PROTOCOL §5.1 without the benchmark's timeline and records.
//   decoder thread (1 per channel) -> sampler -> Q1 -> preprocessing pool [acquire_slot -> prepare]
//   -> Q2 -> NPU workers [bind -> rknn_run -> post-process -> callback]
// The stages, queues, slot count and stop order are src/core/pipeline.cpp's. What is new: it runs until
// stopped, a source that ends or fails is opened again, a frame that fails is skipped, and the
// detections are handed out in the source frame's coordinates.
#include <pthread.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <exception>
#include <mutex>
#include <stdexcept>
#include <thread>

#include "core/pipeline.h"
#include "core/pipeline_post.h"
#include "core/slot_pool.h"
#include "core/stage_queue.h"
#include "core/util.h"
#include "core/video_source.h"
#include "engine.h"
#include "rkzc/rkzc.h"

namespace rkzc {

namespace {

constexpr int kPreprocessThreads = 2;  // K, M1's

struct Sampled {
    AVFrame* frame;
    int channel;
    int64_t seq, pts_us, t0;
};

struct Prepared {
    int channel;
    int64_t seq, pts_us, t0;
    int slot, width, height;
};

// Where prepare puts the image in the model's input: src/preprocess/zero_copy_rga.cpp's arithmetic.
struct Band {
    int x, y, w, h;
    bool operator==(const Band& o) const { return x == o.x && y == o.y && w == o.w && h == o.h; }
};

Band band_of(int frame_w, int frame_h, int model_w, int model_h) {
    const float scale = std::min(float(model_w) / frame_w, float(model_h) / frame_h);
    const int dw = int(frame_w * scale + 0.5f) & ~1;
    const int dh = int(frame_h * scale + 0.5f) & ~1;
    return {(model_w - dw) / 2, (model_h - dh) / 2, dw, dh};
}

bool is_stream(const std::string& url) { return url.rfind("rtsp://", 0) == 0; }

struct Channel {
    std::atomic<int64_t> decoded{0}, sampled{0}, inferred{0}, dropped{0}, errors{0}, reconnects{0};
    std::atomic<int64_t> decode_errors{0};
    std::atomic<bool> connected{false}, ended{false};
    // What the channel's VideoSource watches: set by stop(), and by the monitor when the source stalls.
    std::atomic<bool> halt{false}, stalled{false};
    std::atomic<int64_t> progress_ns{0};  // when it was opened or last returned a frame
    std::atomic<int> in_prepare{0};  // frames in Q1 or in prepare: they hold decoder buffers
};

}  // namespace

struct Detector::Impl {
    Impl(const Config& c, int n)
        : cfg(c), q1(2 * n), q2(kQ2Depth), slots(input_slots(kPreprocessThreads)) {
        for (int i = 0; i < n; i++)
            ch.push_back(std::make_unique<Channel>());
    }

    void emit(EventKind kind, int channel, const std::string& message) {
        std::lock_guard<std::mutex> lk(callback_m);
        if (!event_cb)
            return;
        try {
            event_cb(Event{kind, channel, message});
        } catch (...) {  // the callback's own failure must not end a pipeline thread
        }
    }

    void frame_error(int channel, const std::string& message) {
        ch[channel]->errors++;
        emit(EventKind::FrameError, channel, message);
    }

    // Sleeps, but not past stop().
    void pause_ms(int ms) {
        const auto until = std::chrono::steady_clock::now() + std::chrono::milliseconds(ms);
        while (!stop.load() && std::chrono::steady_clock::now() < until)
            usleep(10000);
    }

    void decode_loop(int i);
    void monitor_loop();
    void preprocess_loop();
    void npu_loop(int k);

    const Config cfg;
    std::unique_ptr<Engine> engine;
    std::unique_ptr<PipelinePost> post;
    std::function<void(const Frame&)> frame_cb;
    std::function<void(const Event&)> event_cb;
    std::mutex callback_m;

    std::atomic<bool> stop{false};
    std::mutex state_m;
    bool started = false, stopped = false;

    StageQueue<Sampled> q1;
    StageQueue<Prepared> q2;
    SlotPool slots;
    std::vector<std::unique_ptr<Channel>> ch;
    std::atomic<int64_t> pending{0};  // frames sampled and not yet delivered, dropped or failed
    std::vector<std::thread> decoders, pre, workers;
    std::thread monitor;

    std::mutex band_m;
    bool band_set = false;
    Band band{};
};

void Detector::Impl::decode_loop(int i) {
    pthread_setname_np(pthread_self(), ("dec-" + std::to_string(i + 1)).substr(0, 15).c_str());
    Channel& c = *ch[i];
    const std::string& url = cfg.sources[i];
    int64_t counter = 0;  // decode order over the channel's life, for the sampler and Frame::seq
    int64_t wait_ms = cfg.reconnect_initial_ms;
    bool opened_before = false;

    while (!stop.load()) {
        c.halt = false;
        c.stalled = false;
        if (stop.load())  // stop() sets stop, then halt: a stop before this line is seen here
            break;
        bool opened = false;
        std::string error;
        const int64_t decoded_before = c.decoded.load();
        {
            VideoSource src(url);
            try {
                src.open(&c.halt);
                opened = true;
            } catch (const std::exception& e) {
                error = e.what();
            }
            if (opened) {
                if (opened_before)
                    c.reconnects++;
                opened_before = true;
                c.progress_ns = now_ns();
                c.connected = true;
                emit(EventKind::SourceOpened, i, url);
                const AVRational tb = src.time_base();
                src.run(
                    [&](AVFrame* f) {
                        const int64_t t0 = now_ns();
                        c.decoded++;
                        c.progress_ns = t0;
                        if (counter++ % cfg.every_nth_frame != 0) {  // sampler
                            av_frame_free(&f);
                            return;
                        }
                        c.sampled++;
                        const int64_t pts = f->pts != AV_NOPTS_VALUE ? f->pts : f->best_effort_timestamp;
                        const int64_t pts_us =
                            pts == AV_NOPTS_VALUE ? -1 : av_rescale_q(pts, tb, AVRational{1, 1000000});
                        c.in_prepare++;
                        pending++;
                        auto dropped = q1.push_drop_oldest({f, i, counter - 1, pts_us, t0});
                        if (dropped) {
                            Channel& d = *ch[dropped->channel];
                            av_frame_free(&dropped->frame);
                            d.dropped++;
                            d.in_prepare--;
                            pending--;
                        }
                    },
                    [&] { c.decode_errors++; });  // the decoder goes on with the next packet
                c.connected = false;
                if (c.decoded.load() > decoded_before)
                    wait_ms = cfg.reconnect_initial_ms;
                // The channel's frames still in Q1 or in prepare hold this decoder's buffers. When they
                // are through, release what the backend cached for them, before the decoder closes. On
                // stop() too: the preprocessing threads run until the decoders have ended.
                while (c.in_prepare.load() > 0)
                    usleep(1000);
                engine->forget_channel(i);
            }
        }  // the input and its decoder close here
        if (stop.load())
            break;
        if (!opened) {
            emit(EventKind::SourceError, i, error);
        } else if (c.stalled.load()) {
            emit(EventKind::SourceLost, i,
                 "no frame for " + std::to_string(cfg.stall_timeout_ms) + " ms from " + url);
        } else if (is_stream(url)) {
            emit(EventKind::SourceLost, i, url);
        } else if (cfg.loop_files && c.decoded.load() > decoded_before) {
            continue;  // from its start again, at once
        } else if (cfg.loop_files) {
            emit(EventKind::SourceError, i, "no frame decoded from " + url);  // and wait, not spin
        } else {
            emit(EventKind::SourceEnded, i, url);
            c.ended = true;
            return;
        }
        pause_ms(static_cast<int>(wait_ms));
        wait_ms = std::min<int64_t>(wait_ms * 2, cfg.reconnect_max_ms);
    }
}

// Closes a source that is open and returns no frame: VideoSource has no time limit of its own once
// packets arrive.
void Detector::Impl::monitor_loop() {
    pthread_setname_np(pthread_self(), "monitor");
    while (!stop.load()) {
        const int64_t limit = now_ns() - int64_t(cfg.stall_timeout_ms) * 1000000;
        for (auto& c : ch)
            if (c->connected.load() && c->progress_ns.load() < limit && !c->stalled.exchange(true))
                c->halt = true;
        pause_ms(100);
    }
}

void Detector::Impl::preprocess_loop() {
    const int mw = engine->npu().model_w(), mh = engine->npu().model_h();
    while (auto item = q1.pop()) {
        Channel& c = *ch[item->channel];
        const int slot = slots.acquire();
        if (slot < 0) {
            av_frame_free(&item->frame);
            c.in_prepare--;
            pending--;
            break;
        }
        const int w = item->frame->width, h = item->frame->height;
        std::string error;
        try {
            // The slots' border is filled once, so every source must letterbox to the same band.
            const Band b = band_of(w, h, mw, mh);
            {
                std::lock_guard<std::mutex> lk(band_m);
                if (!band_set) {
                    band = b;
                    band_set = true;
                }
                if (!(b == band))
                    throw std::runtime_error("a " + std::to_string(w) + "x" + std::to_string(h) +
                                             " frame does not letterbox like the first frame; all sources "
                                             "must have one aspect ratio");
            }
            engine->backend().prepare(item->frame, item->channel, slot);
        } catch (const std::exception& e) {
            error = e.what();
        }
        av_frame_free(&item->frame);  // released right after prepare
        c.in_prepare--;
        if (!error.empty()) {
            slots.release(slot);
            frame_error(item->channel, error);
            pending--;
            continue;
        }
        if (!q2.push({item->channel, item->seq, item->pts_us, item->t0, slot, w, h})) {
            slots.release(slot);
            pending--;
            break;
        }
    }
}

void Detector::Impl::npu_loop(int k) {
    NpuContexts& npu = engine->npu();
    const int mw = npu.model_w(), mh = npu.model_h();
    std::vector<Detection> out;
    while (auto p = q2.pop()) {
        Channel& c = *ch[p->channel];
        std::vector<::Detection> dets;  // in the model input's coordinates
        std::string error;
        try {
            engine->backend().bind(npu, k, p->slot);
            CHECK(rknn_run(npu.ctx[k], nullptr) == RKNN_SUCC, "rknn_run failed");
            dets = post->run(npu.output_ptrs(k), npu.outputs, mw, mh);
        } catch (const std::exception& e) {
            error = e.what();
        }
        slots.release(p->slot);
        if (!error.empty()) {
            frame_error(p->channel, error);
            pending--;
            continue;
        }
        // Back through the letterbox, into the source frame.
        const Band b = band_of(p->width, p->height, mw, mh);
        const float sx = float(p->width) / b.w, sy = float(p->height) / b.h;
        out.clear();
        for (const ::Detection& d : dets) {
            const float x1 = std::clamp((d.x1 - b.x) * sx, 0.f, float(p->width));
            const float y1 = std::clamp((d.y1 - b.y) * sy, 0.f, float(p->height));
            const float x2 = std::clamp((d.x2 - b.x) * sx, 0.f, float(p->width));
            const float y2 = std::clamp((d.y2 - b.y) * sy, 0.f, float(p->height));
            out.push_back({x1, y1, x2 - x1, y2 - y1, d.score, d.cls});
        }
        const Frame frame{p->channel, p->seq,   p->pts_us,  p->t0,
                          p->width,   p->height, out.data(), static_cast<int>(out.size())};
        bool callback_failed = false;
        {
            std::lock_guard<std::mutex> lk(callback_m);
            if (frame_cb) {
                try {
                    frame_cb(frame);
                } catch (...) {
                    callback_failed = true;
                }
            }
        }
        if (callback_failed)
            frame_error(p->channel, "the frame callback threw an exception");
        else
            c.inferred++;
        pending--;
    }
}

Detector::Detector(std::unique_ptr<Impl> impl) : impl_(std::move(impl)) {}

Detector::~Detector() { stop(); }

std::unique_ptr<Detector> Detector::open(const Config& config, Status* status) {
    Status local;
    Status& st = status ? *status : local;
    st = Status{};
    auto fail = [&](const std::string& message) {
        st.ok = false;
        st.message = message;
        return nullptr;
    };
    if (config.sources.empty())
        return fail("no sources");
    if (config.every_nth_frame < 1)
        return fail("every_nth_frame must be at least 1");
    if (config.reconnect_initial_ms < 1 || config.reconnect_max_ms < config.reconnect_initial_ms)
        return fail("reconnect_initial_ms must be at least 1 and at most reconnect_max_ms");
    if (config.stall_timeout_ms < 1)
        return fail("stall_timeout_ms must be at least 1");
    PostKind kind = PostKind::Neon;
    if (config.post != "auto" && !parse_post_kind(config.post, &kind))
        return fail("post must be auto, m1, neon or v10");

    auto impl = std::make_unique<Impl>(config, static_cast<int>(config.sources.size()));
    std::string error;
    impl->engine = Engine::open(config.model, kPreprocessThreads, &error);
    if (!impl->engine)
        return fail(error);
    const auto& attrs = impl->engine->npu().outputs;
    try {
        if (config.post == "auto") {
            try {
                impl->post = std::make_unique<PipelinePost>(PostKind::Neon, attrs);
            } catch (const std::exception&) {
                impl->post = std::make_unique<PipelinePost>(PostKind::V10, attrs);
            }
        } else {
            impl->post = std::make_unique<PipelinePost>(kind, attrs);
        }
    } catch (const std::exception& e) {
        return fail(config.post == "auto"
                        ? std::string("the model's outputs are neither YOLOv8's nor YOLOv10's layout (") +
                              e.what() + ")"
                        : std::string(e.what()));
    }
    return std::unique_ptr<Detector>(new Detector(std::move(impl)));
}

void Detector::on_frame(std::function<void(const Frame&)> callback) { impl_->frame_cb = std::move(callback); }
void Detector::on_event(std::function<void(const Event&)> callback) { impl_->event_cb = std::move(callback); }

Status Detector::start() {
    Impl& d = *impl_;
    std::lock_guard<std::mutex> lk(d.state_m);
    if (d.started)
        return Status{false, "a Detector starts once"};
    d.started = true;
    const int n = static_cast<int>(d.ch.size());
    for (int p = 0; p < kPreprocessThreads; p++)
        d.pre.emplace_back([&d, p] {
            pthread_setname_np(pthread_self(), ("pre-" + std::to_string(p)).c_str());
            d.preprocess_loop();
        });
    for (int k = 0; k < NpuContexts::kCount; k++)
        d.workers.emplace_back([&d, k] {
            pthread_setname_np(pthread_self(), ("npu-" + std::to_string(k)).c_str());
            d.npu_loop(k);
        });
    for (int i = 0; i < n; i++)
        d.decoders.emplace_back([&d, i] { d.decode_loop(i); });
    d.monitor = std::thread([&d] { d.monitor_loop(); });
    return Status{};
}

void Detector::stop() {
    Impl& d = *impl_;
    std::lock_guard<std::mutex> lk(d.state_m);
    if (!d.started || d.stopped)
        return;
    d.stopped = true;
    // Inputs first, then each stage in order (src/core/pipeline.cpp).
    d.stop = true;
    d.monitor.join();  // first, so that nothing but the line below and the decoders touches halt
    for (auto& c : d.ch)
        c->halt = true;
    for (auto& t : d.decoders)
        t.join();
    d.q1.close();
    d.slots.close();
    for (auto& t : d.pre)
        t.join();
    for (auto& item : d.q1.drain())
        av_frame_free(&item.frame);
    d.q2.close();
    for (auto& t : d.workers)
        t.join();
}

bool Detector::wait(int timeout_ms) {
    Impl& d = *impl_;
    const auto until = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
    for (;;) {
        if (d.stop.load())
            return true;
        bool done = d.pending.load() == 0;
        for (auto& c : d.ch)
            done = done && c->ended.load();
        if (done && d.pending.load() == 0)
            return true;
        if (timeout_ms >= 0 && std::chrono::steady_clock::now() >= until)
            return false;
        usleep(5000);
    }
}

std::vector<ChannelStats> Detector::stats() const {
    std::vector<ChannelStats> all;
    for (auto& c : impl_->ch) {
        ChannelStats s;
        s.decoded = c->decoded;
        s.sampled = c->sampled;
        s.inferred = c->inferred;
        s.dropped = c->dropped;
        s.errors = c->errors;
        s.decode_errors = c->decode_errors;
        s.reconnects = c->reconnects;
        s.connected = c->connected;
        s.cached_buffers = static_cast<int>(impl_->engine->cached_buffers(static_cast<int>(all.size())));
        all.push_back(s);
    }
    return all;
}

int Detector::model_width() const { return impl_->engine->npu().model_w(); }
int Detector::model_height() const { return impl_->engine->npu().model_h(); }

const char* class_name(int cls) { return cls >= 0 && cls < 80 ? coco_label(cls) : "?"; }

}  // namespace rkzc
