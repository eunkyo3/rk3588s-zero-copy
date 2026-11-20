// The C interface (include/rkzc/rkzc_c.h): a thin layer over rkzc::Detector. No exception leaves it.
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <exception>
#include <memory>
#include <string>
#include <type_traits>

#include "rkzc/rkzc.h"
#include "rkzc/rkzc_c.h"

// rkzc_frame hands out rkzc::Detection's array as rkzc_detection's.
static_assert(sizeof(rkzc_detection) == sizeof(rkzc::Detection), "Detection layouts differ");
static_assert(alignof(rkzc_detection) == alignof(rkzc::Detection), "Detection layouts differ");
static_assert(std::is_standard_layout<rkzc::Detection>::value, "Detection is not standard layout");
static_assert(offsetof(rkzc_detection, x) == offsetof(rkzc::Detection, x), "Detection layouts differ");
static_assert(offsetof(rkzc_detection, y) == offsetof(rkzc::Detection, y), "Detection layouts differ");
static_assert(offsetof(rkzc_detection, w) == offsetof(rkzc::Detection, w), "Detection layouts differ");
static_assert(offsetof(rkzc_detection, h) == offsetof(rkzc::Detection, h), "Detection layouts differ");
static_assert(offsetof(rkzc_detection, score) == offsetof(rkzc::Detection, score), "Detection layouts differ");
static_assert(offsetof(rkzc_detection, cls) == offsetof(rkzc::Detection, cls), "Detection layouts differ");
static_assert(RKZC_SOURCE_OPENED == static_cast<int>(rkzc::EventKind::SourceOpened) &&
                  RKZC_SOURCE_LOST == static_cast<int>(rkzc::EventKind::SourceLost) &&
                  RKZC_SOURCE_ENDED == static_cast<int>(rkzc::EventKind::SourceEnded) &&
                  RKZC_SOURCE_ERROR == static_cast<int>(rkzc::EventKind::SourceError) &&
                  RKZC_FRAME_ERROR == static_cast<int>(rkzc::EventKind::FrameError),
              "event kinds differ");

struct rkzc_detector {
    std::unique_ptr<rkzc::Detector> det;
    int channels = 0;
};

namespace {

// The detector whose callback this thread is in, so that rkzc_stop and rkzc_close can refuse there:
// stop() joins the calling thread's own thread.
thread_local const rkzc_detector* t_in_callback = nullptr;
struct InCallback {
    const rkzc_detector* before;
    explicit InCallback(const rkzc_detector* d) : before(t_in_callback) { t_in_callback = d; }
    ~InCallback() { t_in_callback = before; }
};

void set_error(char* error, size_t size, const std::string& message) {
    if (error && size > 0)
        std::snprintf(error, size, "%s", message.c_str());
}

// Runs f; an exception becomes the error text and `failed`.
template <typename F, typename R>
R guarded(char* error, size_t size, R failed, F&& f) {
    try {
        return f();
    } catch (const std::exception& e) {
        set_error(error, size, e.what());
    } catch (...) {
        set_error(error, size, "unknown failure");
    }
    return failed;
}

}  // namespace

extern "C" {

const char* rkzc_version(void) { return rkzc::version(); }
const char* rkzc_class_name(int32_t cls) { return rkzc::class_name(cls); }

void rkzc_config_init(rkzc_config* config) {
    if (!config)
        return;
    const rkzc::Config d;
    config->sources = nullptr;
    config->source_count = 0;
    config->model = nullptr;
    config->post = "auto";
    config->every_nth_frame = d.every_nth_frame;
    config->loop_files = d.loop_files ? 1 : 0;
    config->reconnect_initial_ms = d.reconnect_initial_ms;
    config->reconnect_max_ms = d.reconnect_max_ms;
    config->stall_timeout_ms = d.stall_timeout_ms;
}

rkzc_detector* rkzc_open(const rkzc_config* config, char* error, size_t error_size) {
    set_error(error, error_size, "");
    if (!config) {
        set_error(error, error_size, "no configuration");
        return nullptr;
    }
    return guarded(error, error_size, static_cast<rkzc_detector*>(nullptr), [&]() -> rkzc_detector* {
        rkzc::Config c;
        for (int32_t i = 0; i < config->source_count; i++) {
            if (!config->sources || !config->sources[i]) {
                set_error(error, error_size, "a source is a null pointer");
                return nullptr;
            }
            c.sources.emplace_back(config->sources[i]);
        }
        c.model = config->model ? config->model : "";
        c.post = config->post ? config->post : "auto";
        c.every_nth_frame = config->every_nth_frame;
        c.loop_files = config->loop_files != 0;
        c.reconnect_initial_ms = config->reconnect_initial_ms;
        c.reconnect_max_ms = config->reconnect_max_ms;
        c.stall_timeout_ms = config->stall_timeout_ms;
        rkzc::Status st;
        auto det = rkzc::Detector::open(c, &st);
        if (!det) {
            set_error(error, error_size, st.message);
            return nullptr;
        }
        auto* out = new rkzc_detector;
        out->det = std::move(det);
        out->channels = static_cast<int>(c.sources.size());
        return out;
    });
}

void rkzc_close(rkzc_detector* detector) {
    if (!detector || t_in_callback == detector)
        return;
    try {  // here, not in the destructor, which cannot throw
        detector->det->stop();
    } catch (...) {
    }
    delete detector;
}

void rkzc_on_frame(rkzc_detector* detector, rkzc_frame_fn callback, void* user) {
    if (!detector)
        return;
    try {
        if (!callback) {
            detector->det->on_frame(nullptr);
            return;
        }
        detector->det->on_frame([detector, callback, user](const rkzc::Frame& f) {
            const InCallback mark(detector);
            rkzc_frame out;
            out.channel = f.channel;
            out.width = f.width;
            out.height = f.height;
            out.count = f.count;
            out.seq = f.seq;
            out.pts_us = f.pts_us;
            out.capture_ns = f.capture_ns;
            out.detections = reinterpret_cast<const rkzc_detection*>(f.detections);
            callback(&out, user);
        });
    } catch (...) {
    }
}

void rkzc_on_event(rkzc_detector* detector, rkzc_event_fn callback, void* user) {
    if (!detector)
        return;
    try {
        if (!callback) {
            detector->det->on_event(nullptr);
            return;
        }
        detector->det->on_event([detector, callback, user](const rkzc::Event& e) {
            const InCallback mark(detector);
            rkzc_event out;
            out.kind = static_cast<int32_t>(e.kind);
            out.channel = e.channel;
            out.message = e.message.c_str();
            callback(&out, user);
        });
    } catch (...) {
    }
}

int rkzc_start(rkzc_detector* detector, char* error, size_t error_size) {
    set_error(error, error_size, "");
    if (!detector) {
        set_error(error, error_size, "no detector");
        return 0;
    }
    return guarded(error, error_size, 0, [&]() -> int {
        const rkzc::Status st = detector->det->start();
        if (!st.ok)
            set_error(error, error_size, st.message);
        return st.ok ? 1 : 0;
    });
}

void rkzc_stop(rkzc_detector* detector) {
    if (!detector || t_in_callback == detector)
        return;
    try {
        detector->det->stop();
    } catch (...) {
    }
}

int rkzc_wait(rkzc_detector* detector, int32_t timeout_ms) {
    if (!detector)
        return 1;
    return guarded(nullptr, 0, 1, [&]() -> int { return detector->det->wait(timeout_ms) ? 1 : 0; });
}

int32_t rkzc_channel_count(const rkzc_detector* detector) { return detector ? detector->channels : 0; }

int32_t rkzc_stats(const rkzc_detector* detector, rkzc_channel_stats* out, int32_t capacity) {
    if (!detector || !out || capacity <= 0)
        return 0;
    return guarded(nullptr, 0, static_cast<int32_t>(0), [&]() -> int32_t {
        const auto stats = detector->det->stats();
        int32_t n = 0;
        for (const rkzc::ChannelStats& s : stats) {
            if (n >= capacity)
                break;
            rkzc_channel_stats& o = out[n++];
            o.decoded = s.decoded;
            o.sampled = s.sampled;
            o.inferred = s.inferred;
            o.dropped = s.dropped;
            o.errors = s.errors;
            o.decode_errors = s.decode_errors;
            o.reconnects = s.reconnects;
            o.connected = s.connected ? 1 : 0;
            o.cached_buffers = s.cached_buffers;
        }
        return n;
    });
}

int32_t rkzc_model_width(const rkzc_detector* detector) { return detector ? detector->det->model_width() : 0; }
int32_t rkzc_model_height(const rkzc_detector* detector) { return detector ? detector->det->model_height() : 0; }

int rkzc_check_model(const char* model_path, rkzc_model_info* info, char* error, size_t error_size) {
    set_error(error, error_size, "");
    if (info)
        std::memset(info, 0, sizeof *info);
    return guarded(error, error_size, 0, [&]() -> int {
        rkzc::ModelInfo mi;
        const rkzc::Status st = rkzc::check_model(model_path ? model_path : "", &mi);
        if (!st.ok) {
            set_error(error, error_size, st.message);
            return 0;
        }
        if (info) {
            info->width = mi.width;
            info->height = mi.height;
            info->outputs = mi.outputs;
            std::snprintf(info->runtime, sizeof info->runtime, "%s", mi.runtime.c_str());
        }
        return 1;
    });
}

int rkzc_check_source(const char* url, rkzc_source_info* info, int32_t timeout_s, char* error, size_t error_size) {
    set_error(error, error_size, "");
    if (info)
        std::memset(info, 0, sizeof *info);
    return guarded(error, error_size, 0, [&]() -> int {
        rkzc::SourceInfo si;
        const rkzc::Status st = rkzc::check_source(url ? url : "", &si, timeout_s);
        if (!st.ok) {
            set_error(error, error_size, st.message);
            return 0;
        }
        if (info) {
            info->width = si.width;
            info->height = si.height;
        }
        return 1;
    });
}

}  // extern "C"
