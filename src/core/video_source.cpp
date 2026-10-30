#include "core/video_source.h"

#include <unistd.h>

#include "core/util.h"

extern "C" {
#include <libavutil/opt.h>
}

namespace {

AVPixelFormat pick_drm_prime(AVCodecContext*, const AVPixelFormat* fmts) {
    for (const AVPixelFormat* p = fmts; *p != AV_PIX_FMT_NONE; p++)
        if (*p == AV_PIX_FMT_DRM_PRIME)
            return *p;
    die("decoder does not offer AV_PIX_FMT_DRM_PRIME");
}

int interrupted(void* opaque) {
    return static_cast<const std::atomic<bool>*>(opaque)->load() ? 1 : 0;
}

}  // namespace

VideoSource::~VideoSource() {
    avcodec_free_context(&dec_);
    avformat_close_input(&fmt_);
}

void VideoSource::open(const std::atomic<bool>* stop) {
    stop_ = stop;
    live_ = url_.rfind("rtsp://", 0) == 0;
    fmt_ = avformat_alloc_context();
    fmt_->interrupt_callback = {interrupted, const_cast<std::atomic<bool>*>(stop)};
    AVDictionary* opts = nullptr;
    if (live_) {
        av_dict_set(&opts, "rtsp_transport", "tcp", 0);
        av_dict_set(&opts, "timeout", "5000000", 0);  // µs; a stalled stream fails instead of hanging
    }
    int r = avformat_open_input(&fmt_, url_.c_str(), nullptr, &opts);
    av_dict_free(&opts);
    if (r != 0) {
        char msg[AV_ERROR_MAX_STRING_SIZE];
        av_strerror(r, msg, sizeof msg);
        die("cannot open %s: %s", url_.c_str(), msg);
    }
    CHECK(avformat_find_stream_info(fmt_, nullptr) >= 0, "no stream info in %s", url_.c_str());
    stream_ = av_find_best_stream(fmt_, AVMEDIA_TYPE_VIDEO, -1, -1, nullptr, 0);
    CHECK(stream_ >= 0, "no video stream in %s", url_.c_str());
    time_base_ = fmt_->streams[stream_]->time_base;

    const AVCodec* codec = avcodec_find_decoder_by_name("h264_rkmpp");
    CHECK(codec, "h264_rkmpp not found");
    dec_ = avcodec_alloc_context3(codec);
    avcodec_parameters_to_context(dec_, fmt_->streams[stream_]->codecpar);
    dec_->get_format = pick_drm_prime;
    dec_->extra_hw_frames = 8;
    av_opt_set(dec_->priv_data, "afbc", "off", 0);  // RGA and B need a linear layout (env.md)
    CHECK(avcodec_open2(dec_, codec, nullptr) == 0, "cannot open h264_rkmpp for %s", url_.c_str());
}

// A file is read at its own frame rate, like a stream: packet i is released at the time its PTS
// says, counted from the first packet.
void VideoSource::pace(const AVPacket* pkt) {
    if (live_ || pkt->pts == AV_NOPTS_VALUE)
        return;
    const int64_t t = now_ns();
    if (pace_pts0_ == AV_NOPTS_VALUE) {
        pace_pts0_ = pkt->pts;
        pace_t0_ = t;
        return;
    }
    const int64_t due = pace_t0_ + av_rescale_q(pkt->pts - pace_pts0_, time_base_, AVRational{1, 1000000000});
    if (due > t)
        usleep(static_cast<useconds_t>((due - t) / 1000));
}

bool VideoSource::run(const std::function<void(AVFrame*)>& on_frame, const std::function<void()>& on_error) {
    AVPacket* pkt = av_packet_alloc();
    bool have_packet = false;  // a packet the decoder has not accepted yet
    bool input_done = false;
    int drain_waits = 0;
    while (!stop_->load()) {
        AVFrame* frame = av_frame_alloc();
        int r = avcodec_receive_frame(dec_, frame);
        if (r == 0) {
            on_frame(frame);
            continue;
        }
        av_frame_free(&frame);
        if (r == AVERROR_EOF)
            break;
        if (r != AVERROR(EAGAIN))
            on_error();  // and go on feeding input
        if (input_done) {
            // Draining: MPP may still be decoding the last frames. Give it up to 1 s.
            if (++drain_waits > 1000)
                break;
            usleep(1000);
            continue;
        }
        if (!have_packet) {
            if (av_read_frame(fmt_, pkt) < 0) {  // end of file, stream failure or interrupt
                avcodec_send_packet(dec_, nullptr);
                input_done = true;
                continue;
            }
            if (pkt->stream_index != stream_) {
                av_packet_unref(pkt);
                continue;
            }
            pace(pkt);
            have_packet = true;
        }
        r = avcodec_send_packet(dec_, pkt);
        if (r == AVERROR(EAGAIN)) {
            // MPP's input queue is full: keep the packet, let the decoder output a frame first.
            usleep(500);
            continue;
        }
        if (r < 0 && r != AVERROR_EOF)
            on_error();
        av_packet_unref(pkt);
        have_packet = false;
    }
    av_packet_free(&pkt);
    return !stop_->load();
}
