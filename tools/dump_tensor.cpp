// M1-3 tensor set (PROTOCOL §6.2): the model input tensors of the first frames of a file_source
// video, back to back, as C's preprocessing writes them (RGA3, AMENDMENTS A1, A4). tools/calib_npu
// reads them. The record of the generating path goes next to them.
//
// Usage: dump_tensor <video.mp4> <model.rknn> <out.u8> [frames=1000]
//   <out.u8>    frames x model_h x model_w x 3 uint8 (RGB, NHWC), in decode order
//   <out>.json  arguments, SHA-256 of the source, the model and the tensors, the preprocessing
// Exit code 0 when all frames were written; otherwise 1, and <out.u8> is removed.
#include <atomic>
#include <cstdio>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

extern "C" {
#include <libavutil/avutil.h>
}

#include <im2d.hpp>

#include "core/metrics.h"
#include "core/npu.h"
#include "core/report.h"
#include "core/util.h"
#include "core/video_source.h"
#include "preprocess/backend.h"
#include "preprocess/cpu_common.h"

#ifndef CODE_COMMIT
#define CODE_COMMIT "unknown"
#endif
#ifndef LATEST_AMENDMENT
#define LATEST_AMENDMENT "none"
#endif

namespace {

std::string sha256_of(const std::string& path) {
    return command_output("sha256sum '" + path + "' | cut -d' ' -f1");
}

}  // namespace

int main(int argc, char** argv) {
    pin_current_thread({4, 5, 6, 7});
    if (argc < 4)
        die("usage: dump_tensor <video.mp4> <model.rknn> <out.u8> [frames=1000]");
    const std::string video = argv[1], model = argv[2], out = argv[3];
    const int max_frames = argc > 4 ? std::atoi(argv[4]) : 1000;
    CHECK(max_frames >= 1, "frames must be at least 1");
    std::string args_json = "[";
    for (int i = 1; i < argc; i++)
        args_json += (i > 1 ? ", " : "") + json_string(argv[i]);
    args_json += "]";
    const std::string start = utc_now("%Y-%m-%dT%H:%M:%SZ");

    NpuContexts npu;
    npu.open(model);
    std::unique_ptr<Backend> backend = make_zero_copy_rga(model, rga_interpolation());
    backend->init(npu, 2);  // C checks its output binding with a second slot
    std::vector<uint8_t> tensor(size_t(npu.model_w()) * npu.model_h() * 3);
    FILE* f = std::fopen(out.c_str(), "wb");
    CHECK(f, "cannot write %s", out.c_str());

    std::atomic<bool> stop{false};
    int frames = 0;
    VideoSource src(video, /*software=*/false, /*paced=*/false);
    src.open(&stop);
    src.run(
        [&](AVFrame* frame) {
            if (frames < max_frames) {
                backend->prepare(frame, 0, 0);
                backend->read_slot(0, tensor.data());
                CHECK(std::fwrite(tensor.data(), 1, tensor.size(), f) == tensor.size(), "tensor write failed");
                if (++frames == max_frames)
                    stop = true;
            }
            av_frame_free(&frame);
        },
        [] { die("decoder error"); });
    CHECK(!std::ferror(f) && std::fclose(f) == 0, "writing %s failed", out.c_str());
    if (frames < max_frames) {  // a short set must not look like a complete one
        std::filesystem::remove(out);
        die("%s has only %d frames, %d requested", video.c_str(), frames, max_frames);
    }

    std::string env = "{\n  \"args\": " + args_json + ",\n  \"start_time_utc\": " + json_string(start) +
                      ",\n  \"code_commit\": " + json_string(CODE_COMMIT) + ",\n  \"amendment\": " +
                      json_string(LATEST_AMENDMENT) + ",\n  \"source\": " + json_string(video) +
                      ",\n  \"source_sha256\": " + json_string(sha256_of(video)) + ",\n  \"model_sha256\": " +
                      json_string(sha256_of(model)) + ",\n  \"decoder\": " +
                      json_string(std::string("h264_rkmpp, ffmpeg ") + av_version_info()) +
                      ",\n  \"preprocessing\": " +
                      json_string("C: RGA3, im_opt_t.interp " + std::to_string(rga_interpolation())) +
                      ",\n  \"backend\": {" + backend->describe() + "},\n  \"librga\": " +
                      json_string(querystring(RGA_VERSION)) + ",\n  \"frames\": " + std::to_string(frames) +
                      ",\n  \"tensor_shape\": [" + std::to_string(npu.model_h()) + ", " +
                      std::to_string(npu.model_w()) + ", 3],\n  \"tensors\": " +
                      json_string(std::filesystem::path(out).filename().string()) + ",\n  \"tensors_sha256\": " +
                      json_string(sha256_of(out)) + "\n}\n";
    const std::string record = std::filesystem::path(out).replace_extension(".json").string();
    FILE* r = std::fopen(record.c_str(), "w");
    CHECK(r && std::fputs(env.c_str(), r) >= 0 && std::fclose(r) == 0, "writing %s failed", record.c_str());

    std::printf("frames: %d -> %s, record %s\n", frames, out.c_str(), record.c_str());
    backend->shutdown(npu);
    npu.close();
    return 0;
}
