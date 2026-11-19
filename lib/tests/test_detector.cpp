// The detector: it refuses a bad configuration with an error, delivers a file's frames, starts a looped
// file's decoder again and again without errors, keeps retrying a source that is down, and stops.
// Usage: test_detector MODEL.rknn VIDEO   (VIDEO: a short H.264 file; the test plays it about 8 times)
// It runs on the board.
#include <dirent.h>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <string>

#include "rkzc/rkzc.h"

namespace {

int failures = 0;

void expect(bool cond, const char* what, const std::string& detail = "") {
    std::printf("%s  %s%s%s\n", cond ? "ok  " : "FAIL", what, detail.empty() ? "" : ": ", detail.c_str());
    failures += !cond;
}

int open_fds() {
    int n = 0;
    if (DIR* d = opendir("/proc/self/fd")) {
        while (readdir(d))
            n++;
        closedir(d);
    }
    return n;
}

double seconds_since(std::chrono::steady_clock::time_point t0) {
    return std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
}

struct Counts {
    std::atomic<int> frames{0}, opened{0}, ended{0}, source_errors{0}, frame_errors{0}, bad_frames{0};
};

void attach(rkzc::Detector& det, Counts& c) {
    det.on_frame([&c](const rkzc::Frame& f) {
        c.frames++;
        bool ok = f.width > 0 && f.height > 0 && f.count >= 0 && f.seq >= 0 && f.capture_ns > 0;
        for (int i = 0; i < f.count; i++) {
            const rkzc::Detection& d = f.detections[i];
            ok = ok && d.x >= 0 && d.y >= 0 && d.w >= 0 && d.h >= 0 && d.x + d.w <= f.width + 0.01f &&
                 d.y + d.h <= f.height + 0.01f && d.score >= 0.25f && d.cls >= 0 && d.cls < 80;
        }
        c.bad_frames += !ok;
    });
    det.on_event([&c](const rkzc::Event& e) {
        if (e.kind == rkzc::EventKind::SourceOpened)
            c.opened++;
        else if (e.kind == rkzc::EventKind::SourceEnded)
            c.ended++;
        else if (e.kind == rkzc::EventKind::SourceError)
            c.source_errors++;
        else if (e.kind == rkzc::EventKind::FrameError)
            c.frame_errors++;
    });
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 3) {
        std::fprintf(stderr, "usage: test_detector MODEL.rknn VIDEO\n");
        return 2;
    }
    const std::string model = argv[1], video = argv[2];
    rkzc::Status st;

    // ---- configuration errors ----
    rkzc::Config cfg;
    cfg.model = model;
    expect(!rkzc::Detector::open(cfg, &st) && !st.ok, "no sources is an error", st.message);
    cfg.sources = {video};
    cfg.every_nth_frame = 0;
    expect(!rkzc::Detector::open(cfg, &st) && !st.ok, "every_nth_frame 0 is an error", st.message);
    cfg.every_nth_frame = 6;
    cfg.post = "v10";
    expect(!rkzc::Detector::open(cfg, &st) && !st.ok, "the wrong post-processing for the model is an error",
           st.message);
    cfg.post = "nope";
    expect(!rkzc::Detector::open(cfg, &st) && !st.ok, "an unknown post-processing is an error", st.message);
    cfg.post = "auto";
    cfg.model = "/nonexistent/model.rknn";
    expect(!rkzc::Detector::open(cfg, &st) && !st.ok, "a missing model is an error", st.message);
    cfg.model = model;
    cfg.stall_timeout_ms = 0;
    expect(!rkzc::Detector::open(cfg, &st) && !st.ok, "stall_timeout_ms 0 is an error", st.message);
    cfg.stall_timeout_ms = 10000;

    const int fds_before = open_fds();

    // ---- one file, played once ----
    {
        Counts c;
        auto det = rkzc::Detector::open(cfg, &st);
        expect(det && st.ok, "the detector opens", st.message);
        if (!det)
            return 1;
        attach(*det, c);
        expect(det->start().ok, "it starts");
        expect(!det->start().ok, "it does not start twice");
        expect(det->wait(60000), "the file ends and wait() returns");
        det->stop();
        const rkzc::ChannelStats s = det->stats()[0];
        expect(s.decoded > 0 && s.sampled == (s.decoded + 5) / 6, "one frame in six is sampled",
               std::to_string(s.decoded) + " decoded, " + std::to_string(s.sampled) + " sampled");
        expect(s.inferred == s.sampled && s.dropped == 0 && s.errors == 0, "every sampled frame is delivered",
               std::to_string(s.inferred) + " inferred, " + std::to_string(s.dropped) + " dropped");
        expect(c.frames == s.inferred && c.bad_frames == 0, "the callback got them, with boxes inside the frame");
        expect(c.opened == 1 && c.ended == 1 && c.source_errors == 0 && c.frame_errors == 0,
               "one opened event and one ended event");
    }

    // ---- two channels of a looped file: the decoder is opened again each time ----
    {
        Counts c;
        cfg.sources = {video, video};
        cfg.loop_files = true;
        auto det = rkzc::Detector::open(cfg, &st);
        expect(det && st.ok, "the detector opens again, with two channels", st.message);
        if (!det)
            return 1;
        attach(*det, c);
        det->start();
        // While it runs: the buffers held for the RGA must not pile up as the decoder is opened again and
        // again. Without the release on reopening, each new decoder's buffers are added to the old ones.
        int first_play = 0, later = 0;
        bool ended = false;
        for (int i = 0; i < 30 && !ended; i++) {
            ended = det->wait(500);
            for (const rkzc::ChannelStats& s : det->stats()) {
                if (s.reconnects == 0)
                    first_play = s.cached_buffers > first_play ? s.cached_buffers : first_play;
                if (s.reconnects >= 4)
                    later = s.cached_buffers > later ? s.cached_buffers : later;
            }
        }
        expect(!ended, "a looped file never ends: wait() times out");
        expect(first_play > 0 && later > 0 && later <= first_play + 2,
               "after four reopenings a channel holds no more decoder buffers than in its first play",
               std::to_string(first_play) + " in the first play, " + std::to_string(later) + " later");
        const auto t0 = std::chrono::steady_clock::now();
        det->stop();
        expect(seconds_since(t0) < 3.0, "stop() returns", std::to_string(seconds_since(t0)) + " s");
        for (const rkzc::ChannelStats& s : det->stats())
            expect(s.reconnects >= 4 && s.errors == 0 && s.inferred > 0 && s.inferred + s.dropped <= s.sampled,
                   "a channel was reopened several times without an error",
                   std::to_string(s.reconnects) + " reopenings, " + std::to_string(s.inferred) + " inferred, " +
                       std::to_string(s.dropped) + " dropped, " + std::to_string(s.errors) + " errors");
        expect(c.frame_errors == 0 && c.source_errors == 0 && c.bad_frames == 0, "no error events");
        cfg.loop_files = false;
    }

    // ---- a source that is down is retried, and does not hold the other channel or stop() ----
    {
        Counts c;
        cfg.sources = {"/nonexistent/video.mp4", video};
        cfg.reconnect_initial_ms = 100;
        cfg.reconnect_max_ms = 400;
        auto det = rkzc::Detector::open(cfg, &st);
        expect(det && st.ok, "the detector opens with a source that does not exist", st.message);
        if (!det)
            return 1;
        attach(*det, c);
        det->start();
        expect(!det->wait(4000), "wait() times out while one source is still being retried");
        const auto t0 = std::chrono::steady_clock::now();
        det->stop();
        expect(seconds_since(t0) < 3.0, "stop() returns", std::to_string(seconds_since(t0)) + " s");
        const auto s = det->stats();
        // 100, 200, 400, 400, ... ms: about 10 tries in 4 s
        expect(c.source_errors >= 6 && c.source_errors <= 14, "the missing source was retried with a growing wait",
               std::to_string(c.source_errors) + " tries in 4 s");
        expect(s[0].decoded == 0 && s[1].inferred > 0 && c.ended == 1, "the other channel played its file");
    }

    // ---- a source that stalls is closed and opened again ----
    // No real stall here: with a limit of 1 ms a file read at 30 fps always looks stalled, which drives
    // the same path (the monitor halts the source, SourceLost, a wait, a new open).
    {
        Counts c;
        std::atomic<int> lost{0};
        cfg.sources = {video};
        cfg.stall_timeout_ms = 1;
        cfg.reconnect_initial_ms = 100;
        cfg.reconnect_max_ms = 100;
        auto det = rkzc::Detector::open(cfg, &st);
        expect(det && st.ok, "the detector opens with a 1 ms stall limit", st.message);
        if (!det)
            return 1;
        attach(*det, c);
        det->on_event([&](const rkzc::Event& e) {
            lost += e.kind == rkzc::EventKind::SourceLost;
            c.opened += e.kind == rkzc::EventKind::SourceOpened;
        });
        det->start();
        expect(!det->wait(3000), "a stalled source does not end its channel");
        det->stop();
        expect(lost >= 3 && c.opened >= 3 && det->stats()[0].reconnects >= 2,
               "the source was halted and opened again",
               std::to_string(lost) + " lost, " + std::to_string(c.opened) + " opened");
        cfg.stall_timeout_ms = 10000;
    }

    const int fds_after = open_fds();
    expect(fds_after == fds_before, "no file descriptor is left open",
           std::to_string(fds_before) + " before, " + std::to_string(fds_after) + " after");

    std::printf("%d failure(s)\n", failures);
    return failures ? 1 : 0;
}
