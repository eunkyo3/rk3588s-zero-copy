/* The C header from C: it must compile as C, and the calls must work without a C++ caller.
 * Usage: test_c_api MODEL.rknn VIDEO.mp4 [OBJECTS.mp4]
 * With OBJECTS (a video with objects the model detects) the detections' fields are checked too. */
#include <stdio.h>
#include <string.h>

#include "rkzc/rkzc_c.h"

static int failures = 0;
static int frames = 0, detections = 0, ended = 0, out_of_range = 0, stop_ignored = 0;
static rkzc_detector* running = NULL;

static void check(int ok, const char* what) {
    printf("%s%s\n", ok ? "ok:   " : "FAIL: ", what);
    if (!ok)
        failures++;
}

static void on_frame(const rkzc_frame* f, void* user) {
    int* counter = (int*)user;
    (*counter)++;
    detections += f->count;
    for (int i = 0; i < f->count; i++) {
        const rkzc_detection* d = &f->detections[i];
        if (d->cls < 0 || d->cls >= 80 || d->score < 0.25f || d->score > 1.0f || d->w <= 0 || d->h <= 0 ||
            d->x <= -1 || d->y <= -1 || d->x + d->w >= f->width + 1 || d->y + d->h >= f->height + 1)
            out_of_range++;
    }
    if (running) { /* from the detector's own callback these two do nothing */
        rkzc_stop(running);
        rkzc_close(running);
        stop_ignored++;
    }
}

static void on_event(const rkzc_event* e, void* user) {
    (void)user;
    if (e->kind == RKZC_SOURCE_ENDED)
        ended++;
}

int main(int argc, char** argv) {
    if (argc != 3 && argc != 4) {
        fprintf(stderr, "usage: %s MODEL.rknn VIDEO.mp4 [OBJECTS.mp4]\n", argv[0]);
        return 2;
    }
    char why[256];
    rkzc_config cfg;
    rkzc_config_init(&cfg);
    check(cfg.every_nth_frame == 6 && cfg.stall_timeout_ms == 10000 && strcmp(cfg.post, "auto") == 0,
          "rkzc_config_init sets the defaults");
    check(rkzc_version()[0] != 0 && strcmp(rkzc_class_name(0), "person") == 0, "version and class_name");

    check(rkzc_open(&cfg, why, sizeof why) == NULL && why[0] != 0, "no source: an error with a reason");
    const char* sources[] = {argv[2]};
    cfg.sources = sources;
    cfg.source_count = 1;
    cfg.model = "/nonexistent.rknn";
    char small[8];
    check(rkzc_open(&cfg, small, sizeof small) == NULL && strlen(small) == sizeof small - 1,
          "a missing model: the reason is cut to the buffer");
    check(rkzc_open(&cfg, NULL, 0) == NULL, "a null error buffer is allowed");

    rkzc_model_info mi;
    check(rkzc_check_model(argv[1], &mi, why, sizeof why) == 1 && mi.width > 0 && mi.runtime[0] != 0, "check_model");
    rkzc_source_info si;
    check(rkzc_check_source(argv[2], &si, 10, why, sizeof why) == 1 && si.width > 0, "check_source");
    check(rkzc_check_source("/nonexistent.mp4", &si, 10, why, sizeof why) == 0 && why[0] != 0,
          "check_source on a missing file");

    cfg.model = argv[1];
    rkzc_detector* det = rkzc_open(&cfg, why, sizeof why);
    check(det != NULL, "open");
    if (det) {
        check(rkzc_channel_count(det) == 1 && rkzc_model_width(det) > 0 && rkzc_model_height(det) > 0,
              "channel count and model size");
        rkzc_on_frame(det, on_frame, &frames);
        rkzc_on_event(det, on_event, NULL);
        check(rkzc_start(det, why, sizeof why) == 1, "start");
        check(rkzc_start(det, why, sizeof why) == 0 && why[0] != 0, "a second start: an error");
        check(rkzc_wait(det, 30000) == 1, "wait: the file ended");
        rkzc_stop(det);
        rkzc_channel_stats st[2];
        check(rkzc_stats(det, st, 2) == 1 && st[0].inferred == frames && frames > 0 && ended == 1,
              "the frames delivered equal the stats; one source-ended event");
        printf("      %d frames, %d detections\n", frames, detections);
        rkzc_close(det);
    }

    /* A looped file: wait times out, stop and close from the callback are ignored, a callback can be
     * cleared, and stats respects its capacity. With OBJECTS: the detections' fields are in range. */
    const char* two[] = {argc == 4 ? argv[3] : argv[2], argv[2]};
    cfg.sources = two;
    cfg.source_count = 2;
    cfg.loop_files = 1;
    det = rkzc_open(&cfg, why, sizeof why);
    check(det != NULL, "open with two looped channels");
    if (det) {
        int looped = 0;
        frames = detections = out_of_range = 0;
        rkzc_on_frame(det, on_frame, &looped);
        rkzc_on_event(det, on_event, NULL);
        rkzc_on_event(det, NULL, NULL);
        running = det;
        check(rkzc_start(det, why, sizeof why) == 1, "start");
        check(rkzc_wait(det, 3000) == 0, "wait: 3 s passed, a looped file does not end");
        rkzc_channel_stats st[2];
        memset(st, 0, sizeof st);
        check(rkzc_stats(det, st, 0) == 0 && rkzc_stats(det, st, 1) == 1 && st[1].decoded == 0 &&
                  rkzc_stats(det, st, 2) == 2 && st[1].decoded > 0,
              "stats fills no more than its capacity");
        check(looped >= 10 && stop_ignored == looped && st[0].connected == 1,
              "stop and close from the callback were ignored: the frames went on");
        running = NULL;
        rkzc_stop(det);
        check(rkzc_wait(det, 0) == 1, "wait after stop returns 1");
        check(ended == 1, "the cleared event callback was not called");
        check(out_of_range == 0 && (argc != 4 || detections > 20),
              "every detection's class, score and box in range");
        printf("      %d frames, %d detections\n", looped, detections);
        rkzc_close(det);
    }
    rkzc_close(NULL);
    printf(failures ? "%d failed\n" : "all passed\n", failures);
    return failures ? 1 : 0;
}
