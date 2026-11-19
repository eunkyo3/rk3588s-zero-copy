// The library returns errors where bench_main exits, and stays usable afterwards.
// Usage: test_checks MODEL.rknn VIDEO   (a model the board can run, and an H.264 file or rtsp:// URL)
// It runs on the board: it needs the NPU, the RGA and the hardware decoder.
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <string>

#include "rkzc/rkzc.h"

namespace {

int failures = 0;

void expect(bool cond, const char* what, const std::string& detail = "") {
    std::printf("%s  %s%s%s\n", cond ? "ok  " : "FAIL", what, detail.empty() ? "" : ": ", detail.c_str());
    failures += !cond;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 3) {
        std::fprintf(stderr, "usage: test_checks MODEL.rknn VIDEO\n");
        return 2;
    }
    const std::string model = argv[1], video = argv[2];

    rkzc::Status st = rkzc::check_model("/nonexistent/model.rknn");
    expect(!st.ok && !st.message.empty(), "a missing model is an error", st.message);

    char garbage[] = "/tmp/rkzc_test_XXXXXX";
    const int fd = mkstemp(garbage);
    expect(fd >= 0, "temporary file");
    close(fd);
    std::ofstream(garbage) << "this is not an RKNN model\n";
    st = rkzc::check_model(garbage);
    expect(!st.ok && !st.message.empty(), "a file that is not a model is an error", st.message);
    std::remove(garbage);

    st = rkzc::check_source("/nonexistent/video.mp4");
    expect(!st.ok && !st.message.empty(), "a missing source is an error", st.message);
    st = rkzc::check_source(model);  // exists, but is not a video
    expect(!st.ok && !st.message.empty(), "a file that is not a video is an error", st.message);

    for (int round = 1; round <= 2; round++) {  // after the failures, and again after a clean close
        rkzc::ModelInfo m;
        st = rkzc::check_model(model, &m);
        expect(st.ok, "the model opens and its bound outputs are coherent", st.message);
        expect(m.width > 0 && m.height > 0 && m.outputs > 0 && !m.runtime.empty(), "the model's info is filled",
               std::to_string(m.width) + "x" + std::to_string(m.height) + ", " + std::to_string(m.outputs) +
                   " outputs, " + m.runtime);
        rkzc::SourceInfo s;
        st = rkzc::check_source(video, &s);
        expect(st.ok, "the source opens and a frame is decoded", st.message);
        expect(s.width > 0 && s.height > 0, "the frame's size is reported",
               std::to_string(s.width) + "x" + std::to_string(s.height));
    }

    std::printf("%d failure(s)\n", failures);
    return failures ? 1 : 0;
}
