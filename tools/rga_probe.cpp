// Diagnostic for M1-2: what does RGA3 do when it scales NV12 1920x1080 to RGB 640x360?
// Compares C's tensor with OpenCV candidates, in RGB and, after converting C back to BT.601
// limited Y, Cb, Cr, per plane. A candidate that matches the luma plane identifies the luma
// scaler; the chroma error then shows how RGA handles chroma.
//
// Usage: rga_probe <video.mp4> <model.rknn> [frames=100]   (as root)
#include <linux/dma-buf.h>
#include <sys/ioctl.h>
#include <sys/mman.h>

#include <atomic>
#include <cmath>
#include <cstdio>
#include <map>
#include <string>
#include <vector>

#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>

extern "C" {
#include <libavutil/hwcontext_drm.h>
}

#include "core/metrics.h"
#include "core/npu.h"
#include "core/util.h"
#include "core/video_source.h"
#include "preprocess/backend.h"
#include "preprocess/cpu_common.h"

namespace {

struct Acc {
    double sum = 0, min_psnr = 99;
    int64_t n = 0;
    void add(const cv::Mat& a, const cv::Mat& b) {
        cv::Mat d, d2;
        cv::absdiff(a, b, d);
        sum += cv::sum(d)[0] + cv::sum(d)[1] + cv::sum(d)[2];
        n += int64_t(d.total()) * d.channels();
        d.convertTo(d2, CV_32F);
        const cv::Scalar sq = cv::sum(d2.mul(d2));
        const double mse = (sq[0] + sq[1] + sq[2]) / double(d.total() * d.channels());
        if (mse > 0)
            min_psnr = std::min(min_psnr, 10 * std::log10(255.0 * 255.0 / mse));
    }
    double mae() const { return n ? sum / double(n) : 0; }
};

// BT.601 limited-range YUV 4:4:4 (Y and interleaved UV at the same size) to RGB, rounded.
cv::Mat yuv444_to_rgb(const cv::Mat& y, const cv::Mat& uv) {
    cv::Mat rgb(y.size(), CV_8UC3);
    for (int r = 0; r < y.rows; r++)
        for (int c = 0; c < y.cols; c++) {
            const float Y = 1.164f * (y.at<uint8_t>(r, c) - 16);
            const float U = uv.at<cv::Vec2b>(r, c)[0] - 128.0f, V = uv.at<cv::Vec2b>(r, c)[1] - 128.0f;
            rgb.at<cv::Vec3b>(r, c) = {cv::saturate_cast<uint8_t>(Y + 1.596f * V),
                                       cv::saturate_cast<uint8_t>(Y - 0.392f * U - 0.813f * V),
                                       cv::saturate_cast<uint8_t>(Y + 2.017f * U)};
        }
    return rgb;
}

// BT.601 limited-range Y, Cb, Cr of an RGB image, as floats.
void rgb_to_ycc(const cv::Mat& rgb, cv::Mat& y, cv::Mat& cb, cv::Mat& cr) {
    cv::Mat f;
    rgb.convertTo(f, CV_32FC3);
    std::vector<cv::Mat> c(3);
    cv::split(f, c);  // R, G, B
    y = 16 + 0.257f * c[0] + 0.504f * c[1] + 0.098f * c[2];
    cb = 128 - 0.148f * c[0] - 0.291f * c[1] + 0.439f * c[2];
    cr = 128 + 0.439f * c[0] - 0.368f * c[1] - 0.071f * c[2];
}

}  // namespace

int main(int argc, char** argv) {
    pin_current_thread({4, 5, 6, 7});
    cv::setNumThreads(1);
    if (argc < 3)
        die("usage: rga_probe <video.mp4> <model.rknn> [frames=100]");
    const int max_frames = argc > 3 ? std::atoi(argv[3]) : 100;
    NpuContexts npu;
    npu.open(argv[2]);
    const int mw = npu.model_w(), mh = npu.model_h();
    auto c = make_zero_copy_rga(argv[2], rga_interpolation());
    c->init(npu, 2);

    const std::map<std::string, int> interps = {
        {"NEAREST", cv::INTER_NEAREST}, {"LINEAR", cv::INTER_LINEAR}, {"AREA", cv::INTER_AREA},
        {"CUBIC", cv::INTER_CUBIC}, {"LINEAR_EXACT", cv::INTER_LINEAR_EXACT}};
    std::map<std::string, Acc> rgb_resize_first, rgb_convert_first, luma, chroma_resize_first, model;
    std::vector<uint8_t> tc(size_t(mw) * mh * 3);
    std::atomic<bool> stop{false};
    int frames = 0;
    VideoSource src(argv[1], false, false);
    src.open(&stop);
    src.run(
        [&](AVFrame* f) {
            if (frames >= max_frames) {
                av_frame_free(&f);
                return;
            }
            const auto* desc = reinterpret_cast<const AVDRMFrameDescriptor*>(f->data[0]);
            const auto& L = desc->layers[0];
            const Band band = letterbox_band(f->width, f->height, mw, mh);
            if (frames == 0)
                std::printf("decoder frame %dx%d: pitch %u, rows in memory %u\n", f->width, f->height,
                            unsigned(L.planes[0].pitch), unsigned(L.planes[1].offset / L.planes[0].pitch));
            c->prepare(f, 0, 0);
            c->read_slot(0, tc.data());
            const cv::Mat c_band = cv::Mat(mh, mw, CV_8UC3, tc.data())(cv::Rect(band.x, band.y, band.w, band.h));
            cv::Mat cy, ccb, ccr;
            rgb_to_ycc(c_band, cy, ccb, ccr);

            const int fd = desc->objects[0].fd;
            const size_t size = desc->objects[0].size;
            auto* p = static_cast<uint8_t*>(mmap(nullptr, size, PROT_READ, MAP_SHARED, fd, 0));
            CHECK(p != MAP_FAILED, "mmap failed");
            dma_buf_sync s{DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ};
            ioctl(fd, DMA_BUF_IOCTL_SYNC, &s);
            const cv::Mat y(f->height, f->width, CV_8UC1, p + L.planes[0].offset, L.planes[0].pitch);
            const cv::Mat uv(f->height / 2, f->width / 2, CV_8UC2, p + L.planes[1].offset, L.planes[1].pitch);
            cv::Mat full_rgb;
            cv::cvtColorTwoPlane(y, uv, full_rgb, cv::COLOR_YUV2RGB_NV12);
            for (const auto& [name, interp] : interps) {
                cv::Mat sy, suv, out, out2, fy;
                cv::resize(y, sy, cv::Size(band.w, band.h), 0, 0, interp);
                cv::resize(uv, suv, cv::Size(band.w / 2, band.h / 2), 0, 0, interp);
                cv::cvtColorTwoPlane(sy, suv, out, cv::COLOR_YUV2RGB_NV12);
                rgb_resize_first[name].add(c_band, out);
                cv::resize(full_rgb, out2, cv::Size(band.w, band.h), 0, 0, interp);
                rgb_convert_first[name].add(c_band, out2);
                sy.convertTo(fy, CV_32F);
                cv::Mat d;
                cv::absdiff(cy, fy, d);
                luma[name].sum += cv::sum(d)[0];
                luma[name].n += int64_t(d.total());
                // chroma of the resize-first candidate, compared with C's chroma (both at 640x360)
                cv::Mat oy, ocb, ocr;
                rgb_to_ycc(out, oy, ocb, ocr);
                cv::Mat dcb, dcr;
                cv::absdiff(ccb, ocb, dcb);
                cv::absdiff(ccr, ocr, dcr);
                chroma_resize_first[name].sum += cv::sum(dcb)[0] + cv::sum(dcr)[0];
                chroma_resize_first[name].n += 2 * int64_t(dcb.total());
            }
            // RGA3 model (tools/rga_kernel): output pixel (c, r) averages input columns 3c+1..3c+3 and
            // rows 3r+1..3r+3 of Y and of the chroma upsampled 2x by repetition. "Shifted" is OpenCV
            // INTER_AREA on the frame moved by one pixel, the last row and column repeated.
            if (f->width == 3 * band.w && f->height == 3 * band.h) {
                auto shifted_area = [&](const cv::Mat& m) {
                    cv::Mat padded, small;
                    cv::copyMakeBorder(m(cv::Rect(1, 1, m.cols - 1, m.rows - 1)), padded, 0, 1, 0, 1,
                                       cv::BORDER_REPLICATE);
                    cv::resize(padded, small, cv::Size(band.w, band.h), 0, 0, cv::INTER_AREA);
                    return small;
                };
                const cv::Mat yb = shifted_area(y);
                cv::Mat uva, out_a;
                cv::resize(uv, uva, cv::Size(band.w / 2, band.h / 2), 0, 0, cv::INTER_AREA);
                cv::cvtColorTwoPlane(yb, uva, out_a, cv::COLOR_YUV2RGB_NV12);
                model["shifted_luma"].add(c_band, out_a);
                cv::Mat uv_full;
                cv::resize(uv, uv_full, cv::Size(f->width, f->height), 0, 0, cv::INTER_NEAREST);
                model["shifted_luma_chroma444"].add(c_band, yuv444_to_rgb(yb, shifted_area(uv_full)));
                // The same boxes with the average truncated instead of rounded.
                auto shifted_floor = [&](const cv::Mat& m) {
                    cv::Mat padded, f32, small, out;
                    cv::copyMakeBorder(m(cv::Rect(1, 1, m.cols - 1, m.rows - 1)), padded, 0, 1, 0, 1,
                                       cv::BORDER_REPLICATE);
                    padded.convertTo(f32, CV_32F);
                    cv::resize(f32, small, cv::Size(band.w, band.h), 0, 0, cv::INTER_AREA);
                    small.forEach<float>([](float& v, const int*) { v = std::floor(v + 1e-3f); });
                    if (small.channels() == 2)
                        small.forEach<cv::Vec2f>([](cv::Vec2f& v, const int*) {
                            v[0] = std::floor(v[0] + 1e-3f);
                            v[1] = std::floor(v[1] + 1e-3f);
                        });
                    small.convertTo(out, m.channels() == 1 ? CV_8UC1 : CV_8UC2);
                    return out;
                };
                const cv::Mat ybf = shifted_floor(y);
                cv::Mat out_f;
                cv::cvtColorTwoPlane(ybf, uva, out_f, cv::COLOR_YUV2RGB_NV12);
                model["shifted_luma_floor"].add(c_band, out_f);
                model["shifted_luma_chroma444_floor"].add(c_band, yuv444_to_rgb(ybf, shifted_floor(uv_full)));
            }
            s.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ;
            ioctl(fd, DMA_BUF_IOCTL_SYNC, &s);
            munmap(p, size);
            if (++frames == max_frames)
                stop = true;
            av_frame_free(&f);
        },
        [] { die("decoder error"); });

    std::printf("frames: %d\n%-14s %10s %10s %10s %10s\n", frames, "opencv", "rgb_rs1st", "rgb_cvt1st", "luma",
                "chroma_rs1st");
    for (const auto& [name, interp] : interps)
        std::printf("%-14s %10.3f %10.3f %10.3f %10.3f\n", name.c_str(), rgb_resize_first[name].mae(),
                    rgb_convert_first[name].mae(), luma[name].mae(), chroma_resize_first[name].mae());
    for (const auto& [name, a] : model)
        std::printf("model %-24s rgb_mae=%.3f min_psnr_db=%.2f\n", name.c_str(), a.mae(), a.min_psnr);
    c->shutdown(npu);
    npu.close();
    return 0;
}
