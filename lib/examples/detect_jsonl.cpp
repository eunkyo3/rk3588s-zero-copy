// An example of the library, and the tool the tests compare with the benchmark's: detections as JSON
// lines on stdout, events on stderr.
// Usage: rkzc-jsonl --model FILE.rknn [--post auto|m1|neon|v10] [--every N] [--loop] [--seconds S]
//                   SOURCE...
// It runs until every source has ended (files, without --loop), for S seconds, or until SIGINT/SIGTERM.
//   {"channel": 0, "seq": 0, "pts_us": 0, "width": 1920, "height": 1080,
//    "detections": [[class, score, x, y, w, h], ...]}        x, y, w, h in the source frame's pixels
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "rkzc/rkzc.h"

namespace {

volatile std::sig_atomic_t interrupted = 0;
void on_signal(int) { interrupted = 1; }

const char* kind_name(rkzc::EventKind k) {
    switch (k) {
    case rkzc::EventKind::SourceOpened:
        return "source opened";
    case rkzc::EventKind::SourceLost:
        return "source lost";
    case rkzc::EventKind::SourceEnded:
        return "source ended";
    case rkzc::EventKind::SourceError:
        return "source error";
    case rkzc::EventKind::FrameError:
        return "frame error";
    }
    return "?";
}

}  // namespace

int main(int argc, char** argv) {
    rkzc::Config cfg;
    int seconds = -1;
    for (int i = 1; i < argc; i++) {
        const std::string a = argv[i];
        const bool has_value = i + 1 < argc;
        if (a == "--model" && has_value)
            cfg.model = argv[++i];
        else if (a == "--post" && has_value)
            cfg.post = argv[++i];
        else if (a == "--every" && has_value)
            cfg.every_nth_frame = std::atoi(argv[++i]);
        else if (a == "--seconds" && has_value)
            seconds = std::atoi(argv[++i]);
        else if (a == "--loop")
            cfg.loop_files = true;
        else if (a.rfind("--", 0) == 0) {
            std::fprintf(stderr, "usage: rkzc-jsonl --model FILE.rknn [--post auto|m1|neon|v10] [--every N] "
                                 "[--loop] [--seconds S] SOURCE...\n");
            return 2;
        } else
            cfg.sources.push_back(a);
    }

    rkzc::Status st;
    auto det = rkzc::Detector::open(cfg, &st);
    if (!det) {
        std::fprintf(stderr, "rkzc-jsonl: %s\n", st.message.c_str());
        return 1;
    }
    det->on_frame([](const rkzc::Frame& f) {
        std::printf("{\"channel\": %d, \"seq\": %lld, \"pts_us\": %lld, \"width\": %d, \"height\": %d, "
                    "\"detections\": [",
                    f.channel, static_cast<long long>(f.seq), static_cast<long long>(f.pts_us), f.width, f.height);
        for (int i = 0; i < f.count; i++) {
            const rkzc::Detection& d = f.detections[i];
            std::printf("%s[%d, %.4f, %.2f, %.2f, %.2f, %.2f]", i ? ", " : "", d.cls, d.score, d.x, d.y, d.w, d.h);
        }
        std::printf("]}\n");
    });
    det->on_event([](const rkzc::Event& e) {
        std::fprintf(stderr, "rkzc-jsonl: channel %d: %s: %s\n", e.channel, kind_name(e.kind), e.message.c_str());
    });
    std::signal(SIGINT, on_signal);
    std::signal(SIGTERM, on_signal);
    det->start();
    for (int waited_ms = 0; !interrupted && (seconds < 0 || waited_ms < seconds * 1000); waited_ms += 100)
        if (det->wait(100))
            break;
    det->stop();
    std::fflush(stdout);
    int channel = 0;
    for (const rkzc::ChannelStats& s : det->stats())
        std::fprintf(stderr,
                     "rkzc-jsonl: channel %d: decoded %lld, sampled %lld, inferred %lld, dropped %lld, errors %lld, "
                     "reconnects %lld\n",
                     channel++, static_cast<long long>(s.decoded), static_cast<long long>(s.sampled),
                     static_cast<long long>(s.inferred), static_cast<long long>(s.dropped),
                     static_cast<long long>(s.errors), static_cast<long long>(s.reconnects));
    return 0;
}
