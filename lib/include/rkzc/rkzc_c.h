/* librkzc's C interface: the same detector as rkzc.h, for C and for languages that call C (the Python
 * module in lib/python uses it through ctypes). The meaning of every field is rkzc.h's.
 *
 *   rkzc_config cfg;
 *   rkzc_config_init(&cfg);
 *   const char* sources[] = {"rtsp://camera/stream"};
 *   cfg.sources = sources;
 *   cfg.source_count = 1;
 *   cfg.model = "yolov8n_i8.rknn";
 *   char why[256];
 *   rkzc_detector* det = rkzc_open(&cfg, why, sizeof why);
 *   if (!det) { ... why says ... }
 *   rkzc_on_frame(det, my_frame, my_pointer);
 *   rkzc_start(det, why, sizeof why);
 *   ...
 *   rkzc_close(det);
 *
 * Functions that can fail return 1 on success and 0 on failure (rkzc_open: a null pointer), and write the
 * reason into `error` (always terminated, cut to error_size; `error` may be null).
 */
#ifndef RKZC_C_H
#define RKZC_C_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define RKZC_C_API __attribute__((visibility("default")))

typedef struct rkzc_detector rkzc_detector;

typedef struct rkzc_detection {
    float x, y, w, h; /* the box in the source frame's pixels; x, y is its top left corner */
    float score;
    int32_t cls;
} rkzc_detection;

/* One inferred frame. `detections` is valid only during the callback. */
typedef struct rkzc_frame {
    int32_t channel;
    int32_t width, height;
    int32_t count;
    int64_t seq;
    int64_t pts_us;
    int64_t capture_ns;
    const rkzc_detection* detections;
} rkzc_frame;

enum {
    RKZC_SOURCE_OPENED = 0,
    RKZC_SOURCE_LOST = 1,
    RKZC_SOURCE_ENDED = 2,
    RKZC_SOURCE_ERROR = 3,
    RKZC_FRAME_ERROR = 4
};

/* `message` is valid only during the callback. */
typedef struct rkzc_event {
    int32_t kind;
    int32_t channel;
    const char* message;
} rkzc_event;

/* Call rkzc_config_init first: it sets the defaults of rkzc::Config. The strings are copied by rkzc_open. */
typedef struct rkzc_config {
    const char* const* sources;
    int32_t source_count;
    const char* model;
    const char* post; /* "auto", "m1", "neon" or "v10" */
    int32_t every_nth_frame;
    int32_t loop_files; /* 0 or 1 */
    int32_t reconnect_initial_ms;
    int32_t reconnect_max_ms;
    int32_t stall_timeout_ms;
} rkzc_config;

typedef struct rkzc_channel_stats {
    int64_t decoded, sampled, inferred, dropped, errors, decode_errors, reconnects;
    int32_t connected;
    int32_t cached_buffers;
} rkzc_channel_stats;

typedef struct rkzc_model_info {
    int32_t width, height, outputs;
    char runtime[128];
} rkzc_model_info;

typedef struct rkzc_source_info {
    int32_t width, height;
} rkzc_source_info;

/* The callbacks come from the library's threads, one at a time. Do not call rkzc_stop or rkzc_close from
 * one. */
typedef void (*rkzc_frame_fn)(const rkzc_frame* frame, void* user);
typedef void (*rkzc_event_fn)(const rkzc_event* event, void* user);

RKZC_C_API const char* rkzc_version(void);
RKZC_C_API const char* rkzc_class_name(int32_t cls);

RKZC_C_API void rkzc_config_init(rkzc_config* config);
RKZC_C_API rkzc_detector* rkzc_open(const rkzc_config* config, char* error, size_t error_size);
/* Stops if running, then frees. A null pointer is allowed. */
RKZC_C_API void rkzc_close(rkzc_detector* detector);

/* Set before rkzc_start. */
RKZC_C_API void rkzc_on_frame(rkzc_detector* detector, rkzc_frame_fn callback, void* user);
RKZC_C_API void rkzc_on_event(rkzc_detector* detector, rkzc_event_fn callback, void* user);

RKZC_C_API int rkzc_start(rkzc_detector* detector, char* error, size_t error_size);
RKZC_C_API void rkzc_stop(rkzc_detector* detector);
/* 1 if every source has ended or the detector was stopped; 0 if timeout_ms (-1: none) passed first. */
RKZC_C_API int rkzc_wait(rkzc_detector* detector, int32_t timeout_ms);

RKZC_C_API int32_t rkzc_channel_count(const rkzc_detector* detector);
/* Fills at most `capacity` entries, one per channel; returns the number filled. */
RKZC_C_API int32_t rkzc_stats(const rkzc_detector* detector, rkzc_channel_stats* out, int32_t capacity);
RKZC_C_API int32_t rkzc_model_width(const rkzc_detector* detector);
RKZC_C_API int32_t rkzc_model_height(const rkzc_detector* detector);

RKZC_C_API int rkzc_check_model(const char* model_path, rkzc_model_info* info, char* error, size_t error_size);
RKZC_C_API int rkzc_check_source(const char* url, rkzc_source_info* info, int32_t timeout_s, char* error,
                                 size_t error_size);

#ifdef __cplusplus
}
#endif

#endif
