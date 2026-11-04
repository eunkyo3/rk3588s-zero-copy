// M1-7 demo, step 1: C's detections on a video file, for tools/demo_render.py to draw. C's path of the
// pipeline (RGA3 letterbox into the slot's DMA buffer, which the NPU reads; rknn_run; post-processing)
// on every stride-th decoded frame, as the main condition samples (PROTOCOL §5.1: 1 frame in 6, 5 fps
// at 30 fps). Not a measurement: one frame at a time on NPU core 0, nothing timed.
//
// Usage: demo_detect <video.mp4> <model.rknn> <out.jsonl> [frames=0, all] [stride=6]
//   out.jsonl: a {"header": ...} line (video, model, preprocessing, letterbox band, labels, commit),
//   then one line per sampled frame: {"frame", "pts", "detections"}, each detection
//   [class, score, x1, y1, x2, y2] in model input coordinates. The band maps them back to the frame.
#include <atomic>
#include <cstdio>
#include <string>
#include <vector>

extern "C" {
#include <libavutil/avutil.h>
}

#include "core/metrics.h"
#include "core/npu.h"
#include "core/report.h"
#include "core/util.h"
#include "core/video_source.h"
#include "core/yolov8_post.h"
#include "preprocess/backend.h"
#include "preprocess/cpu_common.h"

#ifndef CODE_COMMIT
#define CODE_COMMIT "unknown"
#endif

namespace {

void write_header(FILE* f, const std::string& video, const std::string& model, const std::string& preprocessing,
                  int stride, const Band& b, int mw, int mh) {
    const Header header = {
        {"start_time_utc", utc_now("%Y-%m-%dT%H:%M:%SZ")},
        {"purpose", "M1-7 demo (not a measurement)"},
        {"variant", "C"},
        {"video", video},
        {"model_sha256", command_output("sha256sum '" + model + "' | cut -d' ' -f1")},
        {"decoder", std::string("h264_rkmpp, ffmpeg ") + av_version_info()},
        {"code_commit", CODE_COMMIT},
    };
    std::string line = "{\"header\": {";
    for (const auto& [k, val] : header)
        line += json_string(k) + ": " + json_string(val) + ", ";
    line += "\"preprocessing\": " + json_string(preprocessing) + ", \"stride\": " + std::to_string(stride) +
            ", \"model_w\": " + std::to_string(mw) + ", \"model_h\": " + std::to_string(mh) + ", \"band\": [" +
            std::to_string(b.x) + ", " + std::to_string(b.y) + ", " + std::to_string(b.w) + ", " +
            std::to_string(b.h) + "], \"labels\": [";
    for (int c = 0; c < 80; c++)
        line += std::string(c ? ", " : "") + json_string(coco_label(c));
    line += "]}}\n";
    std::fputs(line.c_str(), f);
}

void write_frame(FILE* f, int index, int64_t pts, const std::vector<Detection>& dets) {
    std::fprintf(f, "{\"frame\": %d, \"pts\": %lld, \"detections\": [", index, static_cast<long long>(pts));
    for (size_t i = 0; i < dets.size(); i++) {
        const Detection& d = dets[i];
        std::fprintf(f, "%s[%d, %.4f, %.2f, %.2f, %.2f, %.2f]", i ? ", " : "", d.cls, d.score, d.x1, d.y1, d.x2,
                     d.y2);
    }
    std::fputs("]}\n", f);
}

}  // namespace

int main(int argc, char** argv) {
    pin_current_thread({4, 5, 6, 7});
    if (argc < 4)
        die("usage: demo_detect <video.mp4> <model.rknn> <out.jsonl> [frames=0, all] [stride=6]");
    const std::string video = argv[1], model = argv[2], out = argv[3];
    const int max_frames = argc > 4 ? std::atoi(argv[4]) : 0;
    const int stride = argc > 5 ? std::atoi(argv[5]) : 6;
    CHECK(max_frames >= 0 && stride >= 1, "frames must be at least 0 and stride at least 1");

    NpuContexts npu;
    npu.open(model);
    auto backend = make_zero_copy_rga(model, rga_interpolation());
    backend->init(npu, 2);  // as in equivalence_dump: C checks its output binding with a second slot
    const std::string preprocessing = "RGA3, im_opt_t.interp " + std::to_string(rga_interpolation());
    const int mw = npu.model_w(), mh = npu.model_h();
    FILE* f = std::fopen(out.c_str(), "wb");
    CHECK(f, "cannot write %s", out.c_str());

    std::atomic<bool> stop{false};
    int frames = 0, sampled = 0;
    VideoSource src(video, /*software=*/false, /*paced=*/false);
    src.open(&stop);
    src.run(
        [&](AVFrame* fr) {
            if (!stop) {
                if (frames == 0)
                    write_header(f, video, model, preprocessing, stride,
                                 letterbox_band(fr->width, fr->height, mw, mh), mw, mh);
                if (frames % stride == 0) {
                    backend->prepare(fr, 0, 0);
                    backend->bind(npu, 0, 0);
                    CHECK(rknn_run(npu.ctx[0], nullptr) == RKNN_SUCC, "rknn_run failed");
                    write_frame(f, frames, fr->pts, yolov8_post(npu.output_ptrs(0), npu.outputs, mw, mh));
                    sampled++;
                }
                if (++frames == max_frames)
                    stop = true;
            }
            av_frame_free(&fr);
        },
        [] { die("decoder error"); });

    CHECK(!std::ferror(f) && std::fclose(f) == 0, "writing %s failed", out.c_str());
    backend->shutdown(npu);
    npu.close();
    std::printf("frames: %d, sampled: %d (every %d)\n", frames, sampled, stride);
    return frames > 0 && (max_frames == 0 || frames == max_frames) ? 0 : 1;
}
