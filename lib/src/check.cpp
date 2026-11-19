#include <atomic>
#include <chrono>
#include <condition_variable>
#include <exception>
#include <mutex>
#include <thread>

#include "core/video_source.h"
#include "engine.h"
#include "rkzc/rkzc.h"

namespace rkzc {

const char* version() { return RKZC_VERSION; }

Status check_model(const std::string& model_path, ModelInfo* info) {
    Status st;
    std::unique_ptr<Engine> e = Engine::open(model_path, 2, &st.message);  // K = 2, M1's
    if (!e) {
        st.ok = false;
        return st;
    }
    if (info) {
        info->width = e->npu().model_w();
        info->height = e->npu().model_h();
        info->outputs = static_cast<int>(e->npu().outputs.size());
        info->runtime = e->npu().sdk_version;
    }
    return st;
}

Status check_source(const std::string& url, SourceInfo* info, int timeout_s) {
    Status st;
    std::atomic<bool> stop{false};
    // A watchdog: VideoSource has no time limit of its own once packets arrive. Setting `stop` ends its
    // loop and, through FFmpeg's interrupt callback, a read or a connect that is blocked.
    std::mutex m;
    std::condition_variable cv;
    bool finished = false, timed_out = false;
    std::thread watchdog([&] {
        std::unique_lock<std::mutex> lk(m);
        if (!cv.wait_for(lk, std::chrono::seconds(timeout_s), [&] { return finished; })) {
            timed_out = true;
            stop = true;
        }
    });
    auto end_watchdog = [&] {
        {
            std::lock_guard<std::mutex> lk(m);
            finished = true;
        }
        cv.notify_all();
        watchdog.join();
    };
    int frames = 0, width = 0, height = 0;
    bool drm_prime = false;
    try {
        VideoSource src(url);
        src.open(&stop);
        src.run(
            [&](AVFrame* f) {
                if (frames++ == 0) {
                    width = f->width;
                    height = f->height;
                    drm_prime = f->format == AV_PIX_FMT_DRM_PRIME;
                }
                av_frame_free(&f);
                stop = true;
            },
            [] {});
    } catch (const std::exception& ex) {
        end_watchdog();
        st.ok = false;
        st.message = timed_out ? "no answer from " + url + " within " + std::to_string(timeout_s) + " s" : ex.what();
        return st;
    }
    end_watchdog();
    if (frames == 0) {
        st.ok = false;
        st.message = timed_out ? "no frame decoded from " + url + " within " + std::to_string(timeout_s) + " s"
                               : "no frame decoded from " + url;
    } else if (!drm_prime) {
        st.ok = false;
        st.message = "the decoder's frames are not DRM PRIME for " + url;
    }
    if (info) {
        info->width = width;
        info->height = height;
    }
    return st;
}

}  // namespace rkzc
