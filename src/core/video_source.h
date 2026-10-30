// One channel's input: an RTSP stream (over TCP) or a local file (file_source, PROTOCOL §3),
// decoded by h264_rkmpp into DRM PRIME frames with extra_hw_frames=8 and the decoder's default
// buffer allocation (PROTOCOL §5.1).
// h264_rkmpp gets SPS/PPS only in-band: its h264_mp4toannexb filter inserts them for mp4 input,
// but an RTSP stream must carry them itself (stream-server/prepare_videos.sh: repeat-headers=1).
#pragma once

#include <atomic>
#include <functional>
#include <string>

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
}

class VideoSource {
public:
    explicit VideoSource(std::string url) : url_(std::move(url)) {}
    ~VideoSource();

    // Opens the input and the decoder; dies on failure.
    void open(const std::atomic<bool>* stop);

    // Reads and decodes until *stop is set or the input ends. A file is read at its frame rate.
    // on_frame gets every decoded frame and owns it; on_error is called for each decoder error
    // other than EAGAIN and EOF. Returns true if the input ended (or failed) before *stop was set.
    bool run(const std::function<void(AVFrame*)>& on_frame, const std::function<void()>& on_error);

    AVRational time_base() const { return time_base_; }

private:
    void pace(const AVPacket* pkt);

    std::string url_;
    bool live_ = false;
    const std::atomic<bool>* stop_ = nullptr;
    AVFormatContext* fmt_ = nullptr;
    AVCodecContext* dec_ = nullptr;
    int stream_ = -1;
    AVRational time_base_{0, 1};
    int64_t pace_pts0_ = AV_NOPTS_VALUE, pace_t0_ = 0;
};
