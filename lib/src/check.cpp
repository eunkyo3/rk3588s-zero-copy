#include <atomic>
#include <exception>

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

Status check_source(const std::string& url, SourceInfo* info) {
    Status st;
    std::atomic<bool> stop{false};
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
        st.ok = false;
        st.message = ex.what();
        return st;
    }
    if (frames == 0) {
        st.ok = false;
        st.message = "no frame decoded from " + url;
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
