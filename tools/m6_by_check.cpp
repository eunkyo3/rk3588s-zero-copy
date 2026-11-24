// Functional check of arm By before any comparison with B (prereg/M6.md §8 step 3, "By", 1; §5). On
// synthetic 1920 x 1080 NV12 frames, with padded rows, By's conversion (letterbox_nv12_yuv) must give:
//   - the channel order R, G, B: a red, a green and a blue frame each come out with their own channel
//     high and the two others low;
//   - the band's position and the border: a white frame fills exactly the 640 x 360 band, and every
//     element outside it keeps the border value 114;
//   - the frame's orientation: with one quadrant of the frame bright, that quadrant of the band is bright
//     and the three others dark;
//   - the last row and the last column of the luma plane equal to B's, byte for byte. B's luma is not
//     reachable by itself, so both luma planes go through the same colour conversion: By's plane
//     (yuv_resize_luma) through OpenCV's COLOR_YUV2RGB_NV12 with a flat chroma of 128, B's inside
//     letterbox_nv12 on the same frame. With luma between 16 and 235 and that chroma, the conversion
//     maps different luma values to different bytes, so equal bytes mean equal luma. 20 random frames
//     and one whose last row and column differ from their neighbours;
//   - the rest of the luma plane within 2 levels of B's through that conversion, on the same frames of
//     random luma: libyuv's box rounds down and OpenCV's to nearest (M6's disclosure), which is at most
//     one luma level, and at most 2 bytes after the conversion's slope of 1.164. A box that starts one
//     pixel off would differ by tens of levels on such frames;
//   - the place of each box, exactly. Luma: with only the input columns 3c+1 to 3c+3 bright, output
//     column c is bright and its two neighbours stay dark, and the same for rows. Chroma: with only the
//     chroma columns 3c to 3c+2 blue, the two output columns of chroma column c are blue and their
//     neighbours grey, and the same for rows. A box one pixel or one chroma pair off spreads over two
//     output columns.
// Reported, not judged: the mean difference of that luma against B's, and By's whole tensor against B's
// on the random frames.
//
// Usage: m6_by_check [out_dir]   (default results/check/<UTC>_m6_by_check)
//   <out_dir>/check.csv  "# key: value" header lines (PROTOCOL §1.4, run kind check), then key,value
// Exit code 0 when it passed.
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <random>
#include <string>
#include <vector>

#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>

#include "core/metrics.h"
#include "core/report.h"
#include "core/util.h"
#include "preprocess/cpu_common.h"
#include "preprocess/m6_backends.h"

#ifndef CODE_COMMIT
#define CODE_COMMIT "unknown"
#endif
#ifndef LATEST_AMENDMENT
#define LATEST_AMENDMENT "none"
#endif

namespace {

constexpr int kW = 1920, kH = 1080, kStride = kW + 64, kModel = 640;
constexpr uint8_t kPad = 114, kFill = 0xA5;  // kFill: the rows' padding, which nothing may read

struct Frame {
    std::vector<uint8_t> y = std::vector<uint8_t>(size_t(kStride) * kH, kFill);
    std::vector<uint8_t> uv = std::vector<uint8_t>(size_t(kStride) * kH / 2, kFill);

    void fill(uint8_t yv, uint8_t u, uint8_t v) {
        for (int r = 0; r < kH; r++)
            std::fill_n(&y[size_t(r) * kStride], kW, yv);
        for (int r = 0; r < kH / 2; r++)
            for (int c = 0; c < kW / 2; c++) {
                uv[size_t(r) * kStride + 2 * c] = u;
                uv[size_t(r) * kStride + 2 * c + 1] = v;
            }
    }
    uint8_t& luma(int c, int r) { return y[size_t(r) * kStride + c]; }
};

std::vector<uint8_t> by(const Frame& f) {
    std::vector<uint8_t> slot(size_t(kModel) * kModel * 3, kPad);
    letterbox_nv12_yuv(f.y.data(), kStride, f.uv.data(), kStride, kW, kH, slot.data(), kModel, kModel);
    return slot;
}

std::vector<uint8_t> b(const Frame& f) {
    std::vector<uint8_t> slot(size_t(kModel) * kModel * 3, kPad);
    letterbox_nv12(f.y.data(), kStride, f.uv.data(), kStride, kW, kH, slot.data(), kModel, kModel,
                   opencv_interpolation());
    return slot;
}

const uint8_t* px(const std::vector<uint8_t>& slot, int x, int y) { return &slot[(size_t(y) * kModel + x) * 3]; }

}  // namespace

int main(int argc, char** argv) {
    pin_current_thread({4, 5, 6, 7});
    cv::setNumThreads(1);  // PROTOCOL §8
    const std::string out = argc > 1 ? argv[1] : "results/check/" + utc_now("%Y%m%dT%H%M%SZ") + "_m6_by_check";
    std::filesystem::create_directories(out);
    const Header header = {
        {"start_time_utc", utc_now("%Y-%m-%dT%H:%M:%SZ")},
        {"ntp_synchronized", command_output("timedatectl show -p NTPSynchronized --value")},
        {"prereg_v1_commit", kPreregCommit},
        {"prereg_m6_commit", kPreregM6Commit},
        {"libyuv_commit", libyuv_commit()},
        {"run_kind", "check"},
        {"code_commit", CODE_COMMIT},
        {"amendment", LATEST_AMENDMENT},
    };
    const Band band = letterbox_band(kW, kH, kModel, kModel);
    CHECK(band.x == 0 && band.y == 140 && band.w == 640 && band.h == 360, "unexpected band");
    Frame f;

    // ---- channel order: BT.601 limited-range red, green and blue ----
    const uint8_t colours[3][3] = {{81, 90, 240}, {145, 54, 34}, {41, 240, 110}};  // Y, U, V of R, G, B
    int channel_order = 0;
    for (int ch = 0; ch < 3; ch++) {
        f.fill(colours[ch][0], colours[ch][1], colours[ch][2]);
        const auto slot = by(f);
        bool ok = true;
        for (int y = band.y; y < band.y + band.h; y += 7)
            for (int x = band.x; x < band.x + band.w; x += 7)
                for (int k = 0; k < 3; k++)
                    ok &= k == ch ? px(slot, x, y)[k] >= 235 : px(slot, x, y)[k] <= 20;
        channel_order += ok;
    }

    // ---- band position and border: a white frame ----
    f.fill(235, 128, 128);
    int64_t band_not_white = 0, border_not_pad = 0;
    {
        const auto slot = by(f);
        for (int y = 0; y < kModel; y++)
            for (int x = 0; x < kModel; x++) {
                const bool inside = x >= band.x && x < band.x + band.w && y >= band.y && y < band.y + band.h;
                for (int k = 0; k < 3; k++) {
                    const uint8_t v = px(slot, x, y)[k];
                    if (inside)
                        band_not_white += v < 250;
                    else
                        border_not_pad += v != kPad;
                }
            }
    }

    // ---- orientation: one bright quadrant at a time ----
    int quadrants = 0;
    for (int q = 0; q < 4; q++) {
        f.fill(16, 128, 128);
        const int x0 = (q % 2) * kW / 2, y0 = (q / 2) * kH / 2;
        for (int r = y0; r < y0 + kH / 2; r++)
            std::fill_n(&f.luma(x0, r), kW / 2, 235);
        const auto slot = by(f);
        bool ok = true;
        for (int p = 0; p < 4; p++) {  // the centre of each quadrant of the band
            const int x = band.x + (p % 2) * band.w / 2 + band.w / 4, y = band.y + (p / 2) * band.h / 2 + band.h / 4;
            ok &= p == q ? px(slot, x, y)[1] >= 250 : px(slot, x, y)[1] <= 5;
        }
        quadrants += ok;
    }

    // ---- the place of each box: one bright luma stripe, one blue chroma stripe, by column and by row ----
    int stripes = 0;
    constexpr int kStripes = 4;
    {
        const int c = 211, r = 97;      // an output luma column and row, away from the edges
        const int cc = 105, cr = 48;    // an output chroma column and row
        for (int vertical = 0; vertical < 2; vertical++) {
            // Luma: the three input columns (rows) of output column c (row r), B's shifted box (A4).
            f.fill(16, 128, 128);
            for (int y = 0; y < kH; y++)
                for (int x = 0; x < kW; x++)
                    if (vertical ? (x >= 3 * c + 1 && x <= 3 * c + 3) : (y >= 3 * r + 1 && y <= 3 * r + 3))
                        f.luma(x, y) = 235;
            auto slot = by(f);
            bool ok = true;
            for (int k = 0; k < band.h && vertical; k += 5)
                ok &= px(slot, c, band.y + k)[1] >= 250 && px(slot, c - 1, band.y + k)[1] <= 5 &&
                      px(slot, c + 1, band.y + k)[1] <= 5;
            for (int k = 0; k < band.w && !vertical; k += 5)
                ok &= px(slot, k, band.y + r)[1] >= 250 && px(slot, k, band.y + r - 1)[1] <= 5 &&
                      px(slot, k, band.y + r + 1)[1] <= 5;
            stripes += ok;
            // Chroma: the three chroma columns (rows) of output chroma column cc (row cr), not shifted.
            f.fill(128, 128, 128);
            for (int y = 0; y < kH / 2; y++)
                for (int x = 0; x < kW / 2; x++)
                    if (vertical ? (x >= 3 * cc && x <= 3 * cc + 2) : (y >= 3 * cr && y <= 3 * cr + 2)) {
                        f.uv[size_t(y) * kStride + 2 * x] = 240;      // U: blue
                        f.uv[size_t(y) * kStride + 2 * x + 1] = 110;  // V
                    }
            slot = by(f);
            auto blue = [&](int x, int y) { return int(px(slot, x, y)[2]) - int(px(slot, x, y)[0]) >= 100; };
            auto grey = [&](int x, int y) { return std::abs(int(px(slot, x, y)[2]) - int(px(slot, x, y)[0])) <= 3; };
            ok = true;
            for (int k = 0; k < band.h && vertical; k += 5)
                ok &= blue(2 * cc, band.y + k) && blue(2 * cc + 1, band.y + k) && grey(2 * cc - 1, band.y + k) &&
                      grey(2 * cc + 2, band.y + k);
            for (int k = 0; k < band.w && !vertical; k += 5)
                ok &= blue(k, band.y + 2 * cr) && blue(k, band.y + 2 * cr + 1) && grey(k, band.y + 2 * cr - 1) &&
                      grey(k, band.y + 2 * cr + 2);
            stripes += ok;
        }
    }

    // ---- the last row and column of the luma plane against B's, through the same colour conversion ----
    std::mt19937 rng(20261124);
    std::uniform_int_distribution<int> level(16, 235);
    const cv::Mat flat_uv(band.h / 2, band.w / 2, CV_8UC2, cv::Scalar(128, 128));
    constexpr int kEdgeFrames = 21;
    int edge_frames_equal = 0;
    int64_t edge_bytes = 0, edge_bytes_differ = 0, inner_bytes = 0, inner_differ = 0, inner_max = 0;
    double inner_signed = 0, tensor_abs = 0, tensor_signed = 0;
    int64_t tensor_bytes = 0;
    for (int n = 0; n < kEdgeFrames; n++) {
        f.fill(16, 128, 128);
        for (int r = 0; r < kH; r++)
            for (int c = 0; c < kW; c++)
                f.luma(c, r) = static_cast<uint8_t>(level(rng));
        if (n == kEdgeFrames - 1) {  // the frame's last row and column far from their neighbours
            for (int c = 0; c < kW; c++)
                f.luma(c, kH - 1) = c % 2 ? 16 : 235;
            for (int r = 0; r < kH; r++)
                f.luma(kW - 1, r) = r % 2 ? 235 : 16;
        }
        std::vector<uint8_t> small(size_t(band.w) * band.h);
        yuv_resize_luma(f.y.data(), kStride, kW, kH, small.data());
        cv::Mat mine(band.h, band.w, CV_8UC3);
        cv::cvtColorTwoPlane(cv::Mat(band.h, band.w, CV_8UC1, small.data()), flat_uv, mine, cv::COLOR_YUV2RGB_NV12);
        const auto theirs = b(f);
        int64_t differ = 0;
        for (int y = 0; y < band.h; y++)
            for (int x = 0; x < band.w; x++) {
                const bool edge = y == band.h - 1 || x == band.w - 1;
                for (int k = 0; k < 3; k++) {
                    const int d = int(mine.at<cv::Vec3b>(y, x)[k]) - int(px(theirs, band.x + x, band.y + y)[k]);
                    if (edge) {
                        edge_bytes++;
                        differ += d != 0;
                    } else {
                        inner_bytes++;
                        inner_differ += d != 0;
                        inner_max = std::max<int64_t>(inner_max, std::abs(d));
                        inner_signed += d;
                    }
                }
            }
        edge_bytes_differ += differ;
        edge_frames_equal += differ == 0;
        const auto whole = by(f);  // reported: By's whole conversion against B's
        for (size_t i = 0; i < whole.size(); i++) {
            const int d = int(whole[i]) - int(theirs[i]);
            tensor_abs += std::abs(d);
            tensor_signed += d;
        }
        tensor_bytes += static_cast<int64_t>(whole.size());
    }

    const bool passed = channel_order == 3 && band_not_white == 0 && border_not_pad == 0 && quadrants == 4 &&
                        stripes == kStripes && edge_frames_equal == kEdgeFrames && inner_max <= 2;
    char buf[64];
    auto num = [&](double v) {
        std::snprintf(buf, sizeof buf, "%.4f", v);
        return std::string(buf);
    };
    const Header rows = {
        {"channel_order_colours_ok", std::to_string(channel_order) + " of 3"},
        {"band_elements_not_white", std::to_string(band_not_white)},
        {"border_elements_not_114", std::to_string(border_not_pad)},
        {"quadrants_ok", std::to_string(quadrants) + " of 4"},
        {"stripes_ok", std::to_string(stripes) + " of " + std::to_string(kStripes)},
        {"edge_frames", std::to_string(kEdgeFrames)},
        {"edge_frames_equal_to_b", std::to_string(edge_frames_equal)},
        {"edge_bytes", std::to_string(edge_bytes)},
        {"edge_bytes_differing", std::to_string(edge_bytes_differ)},
        {"inner_bytes", std::to_string(inner_bytes)},
        {"inner_max_abs_diff", std::to_string(inner_max) + " (at most 2)"},
        {"reported_inner_bytes_differing", std::to_string(inner_differ)},
        {"reported_inner_mean_diff_by_minus_b", num(inner_signed / double(inner_bytes))},
        {"reported_tensor_mae_by_vs_b", num(tensor_abs / double(tensor_bytes))},
        {"reported_tensor_mean_diff_by_minus_b", num(tensor_signed / double(tensor_bytes))},
        {"passed", passed ? "1" : "0"},
    };
    FILE* csv = std::fopen((out + "/check.csv").c_str(), "w");
    CHECK(csv, "cannot write %s/check.csv", out.c_str());
    for (const auto& [k, v] : header)
        std::fprintf(csv, "# %s: %s\n", k.c_str(), v.c_str());
    std::fputs("key,value\n", csv);
    for (const auto& [k, v] : rows)
        std::fprintf(csv, "%s,%s\n", k.c_str(), v.c_str());
    CHECK(!std::ferror(csv) && std::fclose(csv) == 0, "writing check.csv failed");
    for (const auto& [k, v] : rows)
        std::printf("%s: %s\n", k.c_str(), v.c_str());
    return passed ? 0 : 1;
}
