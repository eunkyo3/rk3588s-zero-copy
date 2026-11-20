// rkzc-detect: the detector as a program. A configuration file in, JSON lines out, until it is told to stop.
//
//   rkzc-detect --config FILE            run
//   rkzc-detect --config FILE --check    read the file, open the detector as a run would, open every
//                                        source once, and print what was found (status 1 if one failed)
//   rkzc-detect --version
//
// Output lines (stdout or a Unix socket, see the configuration):
//   {"channel":0,"seq":12,"pts_us":400000,"time_ms":1763600000123,"width":1920,"height":1080,
//    "detections":[{"class":0,"name":"person","score":0.87,"x":100.0,"y":50.5,"w":80.0,"h":200.0}]}
//   {"event":"source_lost","channel":0,"time_ms":1763600000456,"message":"..."}
// x, y, w, h are in the source frame's pixels; time_ms is the wall clock when the line was written.
// A user and password in a source's URL are replaced by *** in every line and in the log.
// The log goes to stderr (under systemd: the journal).
//
// Exit status: 0 stopped by a signal (SIGTERM, SIGINT, SIGHUP, SIGQUIT), or every source ended; 1 a
// failure while running; 2 the configuration cannot be used (a bad file, a model that does not load, a
// socket that cannot be opened). The service file does not restart on 2, also when the model failed
// to load for a passing reason such as memory.
#include <signal.h>
#include <time.h>

#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>

#include "detect_config.h"
#include "line_output.h"
#include "rkzc/rkzc.h"

namespace {

using rkzc_detect::DetectConfig;
using rkzc_detect::json_escape;
using rkzc_detect::LineOutput;
using rkzc_detect::redact_credentials;

long long wall_ms() {
    timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return static_cast<long long>(ts.tv_sec) * 1000 + ts.tv_nsec / 1000000;
}

const char* event_name(rkzc::EventKind k) {
    switch (k) {
    case rkzc::EventKind::SourceOpened:
        return "source_opened";
    case rkzc::EventKind::SourceLost:
        return "source_lost";
    case rkzc::EventKind::SourceEnded:
        return "source_ended";
    case rkzc::EventKind::SourceError:
        return "source_error";
    case rkzc::EventKind::FrameError:
        return "frame_error";
    }
    return "unknown";
}

void append_frame(const rkzc::Frame& f, std::string* line) {
    char buf[256];
    std::snprintf(buf, sizeof buf,
                  "{\"channel\":%d,\"seq\":%lld,\"pts_us\":%lld,\"time_ms\":%lld,\"width\":%d,\"height\":%d,"
                  "\"detections\":[",
                  f.channel, static_cast<long long>(f.seq), static_cast<long long>(f.pts_us), wall_ms(), f.width,
                  f.height);
    *line = buf;
    int written = 0;
    for (int i = 0; i < f.count; i++) {
        const rkzc::Detection& d = f.detections[i];
        // "nan" or "inf" would not be JSON.
        if (!std::isfinite(d.score) || !std::isfinite(d.x) || !std::isfinite(d.y) || !std::isfinite(d.w) ||
            !std::isfinite(d.h))
            continue;
        std::snprintf(buf, sizeof buf,
                      "%s{\"class\":%d,\"name\":\"%s\",\"score\":%.4f,\"x\":%.2f,\"y\":%.2f,\"w\":%.2f,\"h\":%.2f}",
                      written++ ? "," : "", d.cls, rkzc::class_name(d.cls), d.score, d.x, d.y, d.w, d.h);
        *line += buf;
    }
    *line += "]}";
}

int check(const DetectConfig& cfg) {
    bool failed = false;
    rkzc::ModelInfo m;
    rkzc::Status st = rkzc::check_model(cfg.detector.model, &m);
    if (st)
        std::printf("model  ok      %s: %dx%d, %d outputs, runtime %s\n", cfg.detector.model.c_str(), m.width,
                    m.height, m.outputs, m.runtime.c_str());
    else
        std::printf("model  FAILED  %s: %s\n", cfg.detector.model.c_str(), st.message.c_str());
    failed |= !st.ok;
    if (st) {
        // What a run does first: the same call, so what it would refuse is refused here.
        const auto det = rkzc::Detector::open(cfg.detector, &st);
        if (det)
            std::printf("detector ok    post \"%s\", every %d frames\n", cfg.detector.post.c_str(),
                        cfg.detector.every_nth_frame);
        else
            std::printf("detector FAILED  %s\n", st.message.c_str());
        failed |= !st.ok;
    }
    for (const std::string& source : cfg.detector.sources) {
        rkzc::SourceInfo s;
        st = rkzc::check_source(source, &s);
        const std::string shown = redact_credentials(source);
        if (st)
            std::printf("source ok      %s: %dx%d\n", shown.c_str(), s.width, s.height);
        else
            std::printf("source FAILED  %s: %s\n", shown.c_str(), redact_credentials(st.message).c_str());
        failed |= !st.ok;
    }
    return failed ? 1 : 0;
}

void log_stats(const rkzc::Detector& det, LineOutput& out, bool is_socket) {
    int channel = 0;
    for (const rkzc::ChannelStats& s : det.stats())
        std::fprintf(stderr,
                     "rkzc-detect: channel %d: %s, decoded %lld, inferred %lld, dropped %lld, errors %lld, "
                     "decode errors %lld, reconnects %lld\n",
                     channel++, s.connected ? "connected" : "not connected", static_cast<long long>(s.decoded),
                     static_cast<long long>(s.inferred), static_cast<long long>(s.dropped),
                     static_cast<long long>(s.errors), static_cast<long long>(s.decode_errors),
                     static_cast<long long>(s.reconnects));
    if (is_socket)
        std::fprintf(stderr,
                     "rkzc-detect: output: %d clients, %lld disconnected for not reading, %lld refused as too many\n",
                     out.clients(), out.disconnected(), out.refused());
}

int usage() {
    std::fprintf(stderr, "usage: rkzc-detect --config FILE [--check]\n       rkzc-detect --version\n");
    return 2;
}

}  // namespace

int main(int argc, char** argv) {
    std::string config_path;
    bool check_only = false;
    for (int i = 1; i < argc; i++) {
        const std::string a = argv[i];
        if (a == "--config" && i + 1 < argc)
            config_path = argv[++i];
        else if (a == "--check")
            check_only = true;
        else if (a == "--version") {
            std::printf("rkzc-detect %s\n", rkzc::version());
            return 0;
        } else
            return usage();
    }
    if (config_path.empty())
        return usage();
    setvbuf(stderr, nullptr, _IOLBF, 0);

    DetectConfig cfg;
    std::string error;
    if (!rkzc_detect::load_detect_config(config_path, &cfg, &error)) {
        std::fprintf(stderr, "rkzc-detect: %s\n", error.c_str());
        return 2;
    }
    if (check_only)
        return check(cfg);

    // The stop signals are taken by sigtimedwait below, not by a handler. Blocked before any thread
    // exists, so every thread of this program inherits the mask. A reader that goes away is a failed
    // write, not SIGPIPE.
    sigset_t stop_signals;
    sigemptyset(&stop_signals);
    sigaddset(&stop_signals, SIGINT);
    sigaddset(&stop_signals, SIGTERM);
    sigaddset(&stop_signals, SIGHUP);
    sigaddset(&stop_signals, SIGQUIT);
    pthread_sigmask(SIG_BLOCK, &stop_signals, nullptr);
    signal(SIGPIPE, SIG_IGN);

    rkzc::Status st;
    auto det = rkzc::Detector::open(cfg.detector, &st);
    if (!det) {
        std::fprintf(stderr, "rkzc-detect: %s\n", st.message.c_str());
        return 2;
    }
    LineOutput out;
    if (!out.open(cfg.output, cfg.socket, &error)) {
        std::fprintf(stderr, "rkzc-detect: %s\n", error.c_str());
        return 2;
    }
    const bool is_socket = cfg.output != "stdout";

    std::atomic<bool> output_failed{false};
    // The callbacks come one at a time, so `line` is reused.
    std::string line;
    det->on_frame([&](const rkzc::Frame& f) {
        if (f.count == 0 && !cfg.empty_frames)
            return;
        append_frame(f, &line);
        if (!out.write(line))
            output_failed = true;
    });
    det->on_event([&](const rkzc::Event& e) {
        const std::string message = redact_credentials(e.message);
        std::fprintf(stderr, "rkzc-detect: channel %d: %s: %s\n", e.channel, event_name(e.kind), message.c_str());
        if (!cfg.events)
            return;
        const std::string event_line = std::string("{\"event\":\"") + event_name(e.kind) +
                                       "\",\"channel\":" + std::to_string(e.channel) +
                                       ",\"time_ms\":" + std::to_string(wall_ms()) + ",\"message\":\"" +
                                       json_escape(message) + "\"}";
        if (!out.write(event_line))
            output_failed = true;
    });

    st = det->start();
    if (!st) {
        std::fprintf(stderr, "rkzc-detect: %s\n", st.message.c_str());
        return 1;
    }
    std::fprintf(stderr, "rkzc-detect %s: %zu sources, model %s (%dx%d), every %d frames, output %s\n",
                 rkzc::version(), cfg.detector.sources.size(), cfg.detector.model.c_str(), det->model_width(),
                 det->model_height(), cfg.detector.every_nth_frame, cfg.output.c_str());

    int status = 0;
    long long seconds = 0;
    for (;;) {
        const timespec one_second = {1, 0};
        const int sig = sigtimedwait(&stop_signals, nullptr, &one_second);
        if (sig > 0) {
            std::fprintf(stderr, "rkzc-detect: %s, stopping\n",
                         sig == SIGTERM ? "SIGTERM" : sig == SIGINT ? "SIGINT" : sig == SIGHUP ? "SIGHUP" : "SIGQUIT");
            break;
        }
        if (output_failed) {
            std::fprintf(stderr, "rkzc-detect: the output cannot be written (its reader is gone), stopping\n");
            status = 1;
            break;
        }
        if (det->wait(0)) {
            std::fprintf(stderr, "rkzc-detect: every source has ended\n");
            break;
        }
        seconds++;
        if (cfg.stats_every_s > 0 && seconds % cfg.stats_every_s == 0)
            log_stats(*det, out, is_socket);
    }
    det->stop();
    log_stats(*det, out, is_socket);
    out.close();
    return status;
}
