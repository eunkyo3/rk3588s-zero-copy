// The equivalence of arm Cp (prereg/M2.md §4), before freeze-m2: on the same NPU outputs, Cp's
// post-processing (NeonPost) must give C's detections (yolov8_post), field for field: the same count,
// in the same order, with the same class, score and four coordinates as floats.
//
// Frames: every frame of each video, read as file_source by the hardware decoder and prepared by C's
// RGA path into a slot, run on NPU core 0 (context 0), as in M1-4.
// Synthetic outputs: 12,500 sets of outputs where every cell fails the score-sum threshold except 80
// candidate cells, split 27, 27 and 26 across the three branches, each with a class of its own above
// the class threshold. So NMS removes none of them, and every set decodes 80 boxes from random bins:
// every other set uniform over the 256 values, the others within a narrower range at a random level.
// Both post-processings are the compiled objects of libcore that bench_main links; their SHA-256 is
// recorded (M2 §4).
//
// It passes only as the whole check of §4: the videos given are ① to ④, in that order, each with the
// SHA-256 of docs/notes/preprocess/videos_manifest.tsv and its frame count, and no frame or set differs.
// differing_total counts the frames and sets that differ: above 0, the objects are not equivalent.
//
// Usage: m2_equivalence <model.rknn> <out_dir> <freeze_m2_commit|none> <video.mp4>...
//        (as root, from the repository; the videos are /home/pi/media/bench/<name>.mp4)
//   <out_dir>/m2_equivalence.csv  "# key: value" header lines (PROTOCOL §1.4, run kind check), then
//                                 key,value: per video its SHA-256, frames and differing frames; the
//                                 synthetic sets; the objects' SHA-256; complete, differing_total and
//                                 passed (1 or 0)
// Exit code 0 when it passed.
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <numeric>
#include <random>
#include <string>
#include <vector>

extern "C" {
#include <libavutil/avutil.h>
}

#include <opencv2/core.hpp>

#include "core/metrics.h"
#include "core/npu.h"
#include "core/report.h"
#include "core/util.h"
#include "core/video_source.h"
#include "core/yolov8_post.h"
#include "core/yolov8_post_neon.h"
#include "preprocess/backend.h"
#include "preprocess/cpu_common.h"

#ifndef CODE_COMMIT
#define CODE_COMMIT "unknown"
#endif
#ifndef LATEST_AMENDMENT
#define LATEST_AMENDMENT "none"
#endif

namespace {

constexpr int kNumClasses = 80;
constexpr int kSyntheticSets = 12500;
constexpr int kPerBranch[3] = {27, 27, 26};

struct Video {
    const char* file;
    long frames;
    const char* sha256;  // docs/notes/preprocess/videos_manifest.tsv; ② to ④ are also in AMENDMENTS A8
};
const Video kVideos[4] = {
    {"pexels_36330861.mp4", 1186, "435a6b05ffb60cf1f0bb4e60c86c8a2f5d5a56a5aa9ca145d845c9a5fffc37ce"},
    {"pexels_29130409.mp4", 752, "e560dd8481d5fe1c615f208fae192fc7fd583097d202e785dc026eae8fb75b41"},
    {"pexels_29969146.mp4", 714, "592b6847e2e63e07393af6f4a04b691be438c23eba2c73f79b021dca877c2772"},
    {"pixabay_180386.mp4", 948, "2538f54d36e0d763ec3e13f9e606bf01e2d142fff82181eb156f792576b0555b"},
};

bool same(const std::vector<Detection>& a, const std::vector<Detection>& b) {
    if (a.size() != b.size())
        return false;
    for (size_t i = 0; i < a.size(); i++)
        if (a[i].cls != b[i].cls || a[i].score != b[i].score || a[i].x1 != b[i].x1 || a[i].y1 != b[i].y1 ||
            a[i].x2 != b[i].x2 || a[i].y2 != b[i].y2)
            return false;
    return true;
}

// As in core/yolov8_post.cpp.
int8_t quantize(float v, int32_t zp, float scale) {
    float q = std::round(v / scale + zp);
    return static_cast<int8_t>(std::clamp(q, -128.0f, 127.0f));
}

int elements(const rknn_tensor_attr& a) {
    return a.dims[0] * a.dims[1] * a.dims[2] * a.dims[3];
}

}  // namespace

int main(int argc, char** argv) {
    pin_current_thread({4, 5, 6, 7});
    cv::setNumThreads(1);  // PROTOCOL §8
    if (argc < 5)
        die("usage: m2_equivalence <model.rknn> <out_dir> <freeze_m2_commit|none> <video.mp4>...");
    const std::string model = argv[1], out_dir = argv[2], freeze_m2 = argv[3];
    const std::vector<std::string> videos(argv + 4, argv + argc);
    std::filesystem::create_directories(out_dir);
    const Header header = {
        {"start_time_utc", utc_now("%Y-%m-%dT%H:%M:%SZ")},
        {"ntp_synchronized", command_output("timedatectl show -p NTPSynchronized --value")},
        {"prereg_v1_commit", kPreregCommit},
        {"prereg_m2_commit", kPreregM2Commit},
        {"freeze_m2_commit", freeze_m2},
        {"run_kind", "check"},
        {"model_sha256", file_sha256(model)},
        {"decoder", std::string("h264_rkmpp, ffmpeg ") + av_version_info()},
        {"code_commit", CODE_COMMIT},
        {"amendment", LATEST_AMENDMENT},
        {"purpose", "equivalence of arm Cp (prereg/M2.md §4)"},
    };

    NpuContexts npu;
    npu.open(model);
    auto c = make_zero_copy_rga(model, rga_interpolation());
    c->init(npu, 2);  // as in tools/equivalence_dump
    const NeonPost neon(npu.outputs);
    const int mw = npu.model_w(), mh = npu.model_h();
    Header rows;
    bool complete = videos.size() == 4;
    long differing = 0;

    // ---- the videos ----
    for (size_t v = 0; v < videos.size(); v++) {
        long frames = 0, differ = 0, detections = 0;
        std::atomic<bool> stop{false};
        VideoSource src(videos[v], /*software=*/false, /*paced=*/false);
        src.open(&stop);
        src.run(
            [&](AVFrame* f) {
                c->prepare(f, 0, 0);
                av_frame_free(&f);
                c->bind(npu, 0, 0);
                CHECK(rknn_run(npu.ctx[0], nullptr) == RKNN_SUCC, "rknn_run failed");
                const auto ptrs = npu.output_ptrs(0);
                const auto want = yolov8_post(ptrs, npu.outputs, mw, mh);
                const auto got = neon.run(ptrs, npu.outputs, mw, mh);
                differ += !same(want, got);
                detections += static_cast<long>(want.size());
                frames++;
            },
            [] { die("decoder error"); });
        const std::string k = "video" + std::to_string(v + 1), sha = file_sha256(videos[v]);
        rows.push_back({k + "_path", videos[v]});
        rows.push_back({k + "_sha256", sha});
        rows.push_back({k + "_frames", std::to_string(frames)});
        rows.push_back({k + "_detections", std::to_string(detections)});
        rows.push_back({k + "_differing_frames", std::to_string(differ)});
        const std::string name = std::filesystem::path(videos[v]).filename().string();
        complete = complete && v < 4 && name == kVideos[v].file && sha == kVideos[v].sha256 &&
                   frames == kVideos[v].frames;
        differing += differ;
    }

    // ---- the synthetic sets ----
    std::vector<std::vector<int8_t>> bufs;
    for (const auto& a : npu.outputs)
        bufs.emplace_back(elements(a));
    std::vector<const int8_t*> ptrs;
    for (const auto& b : bufs)
        ptrs.push_back(b.data());
    int8_t sum_t[3], cls_t[3];
    for (int b = 0; b < 3; b++) {
        const auto& cls = npu.outputs[b * 3 + 1];
        const auto& sum = npu.outputs[b * 3 + 2];
        cls_t[b] = quantize(0.25f, cls.zp, cls.scale);
        sum_t[b] = quantize(0.25f, sum.zp, sum.scale);
        CHECK(sum_t[b] > -128 && cls_t[b] < 127, "thresholds leave no room for synthetic outputs");
        std::fill(bufs[b * 3 + 1].begin(), bufs[b * 3 + 1].end(), cls_t[b]);      // no class above it
        std::fill(bufs[b * 3 + 2].begin(), bufs[b * 3 + 2].end(), sum_t[b] - 1);  // every cell fails
    }
    std::mt19937 rng(20261105);
    auto uniform = [&](int lo, int hi) { return lo + static_cast<int>(rng() % unsigned(hi - lo + 1)); };
    long decodes = 0, set_differ = 0, short_sets = 0;
    std::vector<int> classes(kNumClasses);
    for (int set = 0; set < kSyntheticSets; set++) {
        std::iota(classes.begin(), classes.end(), 0);
        std::shuffle(classes.begin(), classes.end(), rng);
        std::vector<std::pair<int, int>> touched;  // (branch, cell)
        int next = 0;
        for (int b = 0; b < 3; b++) {
            const auto& box = npu.outputs[b * 3];
            const int grid_len = box.dims[2] * box.dims[3], bins = box.dims[1];
            std::vector<int> cells(grid_len);
            std::iota(cells.begin(), cells.end(), 0);
            for (int j = 0; j < kPerBranch[b]; j++) {  // distinct cells: a partial shuffle
                std::swap(cells[j], cells[j + rng() % unsigned(grid_len - j)]);
                const int cell = cells[j], cls = classes[next++];
                bufs[b * 3 + 2][cell] = static_cast<int8_t>(uniform(sum_t[b], 127));
                bufs[b * 3 + 1][size_t(cls) * grid_len + cell] = static_cast<int8_t>(uniform(cls_t[b] + 1, 127));
                const int width = set % 2 ? 256 : uniform(1, 32);
                const int low = uniform(0, 256 - width) - 128;
                for (int k = 0; k < bins; k++)
                    bufs[b * 3][size_t(k) * grid_len + cell] = static_cast<int8_t>(low + uniform(0, width - 1));
                touched.push_back({b, cell});
            }
        }
        const auto want = yolov8_post(ptrs, npu.outputs, mw, mh);
        const auto got = neon.run(ptrs, npu.outputs, mw, mh);
        decodes += static_cast<long>(want.size());
        short_sets += want.size() != size_t(kNumClasses);
        set_differ += !same(want, got);
        for (const auto& [b, cell] : touched) {  // back to no candidate
            const int grid_len = npu.outputs[b * 3].dims[2] * npu.outputs[b * 3].dims[3];
            bufs[b * 3 + 2][cell] = static_cast<int8_t>(sum_t[b] - 1);
            for (int k = 0; k < kNumClasses; k++)
                bufs[b * 3 + 1][size_t(k) * grid_len + cell] = cls_t[b];
        }
    }
    rows.push_back({"synthetic_sets", std::to_string(kSyntheticSets)});
    rows.push_back({"synthetic_decodes", std::to_string(decodes)});
    rows.push_back({"synthetic_sets_short", std::to_string(short_sets)});
    rows.push_back({"synthetic_differing_sets", std::to_string(set_differ)});
    complete = complete && short_sets == 0;
    differing += set_differ;

    c->shutdown(npu);
    npu.close();
    const std::string sha_post = file_sha256(kPostObjects[0]), sha_neon = file_sha256(kPostObjects[1]);
    complete = complete && sha_post.size() == 64 && sha_neon.size() == 64;
    rows.push_back({"neon_tables", std::to_string(neon.table_count())});
    rows.push_back({"sha256_yolov8_post_o", sha_post});
    rows.push_back({"sha256_yolov8_post_neon_o", sha_neon});
    rows.push_back({"complete", complete ? "1" : "0"});
    rows.push_back({"differing_total", std::to_string(differing)});
    const bool passed = complete && differing == 0;
    rows.push_back({"passed", passed ? "1" : "0"});

    const std::string path = out_dir + "/m2_equivalence.csv";
    FILE* csv = std::fopen(path.c_str(), "w");
    CHECK(csv, "cannot write %s", path.c_str());
    for (const auto& [k, v] : header)
        std::fprintf(csv, "# %s: %s\n", k.c_str(), v.c_str());
    std::fputs("key,value\n", csv);
    for (const auto& [k, v] : rows) {
        std::fprintf(csv, "%s,%s\n", k.c_str(), v.c_str());
        std::printf("%s: %s\n", k.c_str(), v.c_str());
    }
    CHECK(!std::ferror(csv) && std::fclose(csv) == 0, "writing %s failed", path.c_str());
    return passed ? 0 : 1;
}
