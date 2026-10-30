// Diagnostic for M1-2: the impulse response of RGA3's 3:1 down-scaler. A black NV12 1920x1080
// frame (Y=16, Cb=Cr=128) gets one bright pixel (Y=235) at column x0 (row 540) or row y0
// (column 960), or one Cb sample is raised. RGA converts it to RGB 640x360 exactly as variant C
// does. The output pixels that change show which input pixels each output pixel averages, and with
// which weights. The source buffer is exactly 1920x1080 (no padding rows), unlike decoder frames.
//
// Usage: rga_kernel   (as root)
#include <fcntl.h>
#include <linux/dma-buf.h>
#include <linux/dma-heap.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#include <algorithm>
#include <cstdio>
#include <cstring>

#include <im2d.hpp>
#include <opencv2/imgproc.hpp>

#include "core/util.h"

namespace {

constexpr int W = 1920, H = 1080, OW = 640, OH = 360;

int heap_alloc(size_t len) {
    int hfd = open("/dev/dma_heap/system-dma32", O_RDONLY | O_CLOEXEC);
    CHECK(hfd >= 0, "open heap failed");
    dma_heap_allocation_data d{};
    d.len = len;
    d.fd_flags = O_RDWR | O_CLOEXEC;
    CHECK(ioctl(hfd, DMA_HEAP_IOCTL_ALLOC, &d) == 0, "heap alloc failed");
    close(hfd);
    return static_cast<int>(d.fd);
}

void sync(int fd, uint64_t flags) {
    dma_buf_sync s{flags};
    CHECK(ioctl(fd, DMA_BUF_IOCTL_SYNC, &s) == 0, "DMA_BUF_IOCTL_SYNC failed");
}

}  // namespace

int main() {
    const size_t src_size = size_t(W) * H * 3 / 2, dst_size = size_t(OW) * OH * 3;
    const int sfd = heap_alloc(src_size), dfd = heap_alloc(dst_size);
    auto* src = static_cast<uint8_t*>(mmap(nullptr, src_size, PROT_READ | PROT_WRITE, MAP_SHARED, sfd, 0));
    auto* dst = static_cast<uint8_t*>(mmap(nullptr, dst_size, PROT_READ | PROT_WRITE, MAP_SHARED, dfd, 0));
    const rga_buffer_handle_t sh = importbuffer_fd(sfd, static_cast<int>(src_size));
    const rga_buffer_handle_t dh = importbuffer_fd(dfd, static_cast<int>(dst_size));
    rga_buffer_t s = wrapbuffer_handle(sh, W, H, RK_FORMAT_YCbCr_420_SP, W, H);
    rga_buffer_t d = wrapbuffer_handle(dh, OW, OH, RK_FORMAT_RGB_888);
    imsetColorSpace(&d, IM_YUV_TO_RGB_BT601_LIMIT);

    // Luma impulse at (x0, y0) if x0 >= 0; Cb impulse at chroma sample (cx0, cy0) if cx0 > 0.
    auto run = [&](int x0, int y0, int cx0, int cy0) {
        sync(sfd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_WRITE);
        std::memset(src, 16, size_t(W) * H);
        std::memset(src + size_t(W) * H, 128, size_t(W) * H / 2);
        if (x0 >= 0)
            src[size_t(y0) * W + x0] = 235;
        if (cx0 > 0)
            src[size_t(W) * H + size_t(cy0) * W + size_t(cx0) * 2] = 240;
        sync(sfd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_WRITE);
        im_opt_t opt{};
        opt.version = RGA_CURRENT_API_HEADER_VERSION;
        opt.core = IM_SCHEDULER_RGA3_CORE0;
        IM_STATUS st = improcess(s, d, {}, {0, 0, W, H}, {0, 0, OW, OH}, {}, -1, nullptr, &opt, IM_SYNC);
        CHECK(st == IM_STATUS_SUCCESS, "improcess failed: %s", imStrError(st));
        sync(dfd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ);  // ended at the next run, before RGA writes
    };

    // Luma: for each impulse position, every output pixel that changed, with its value.
    auto changed = [&](const char* what, int x0, int y0) {
        std::printf("%s (%d,%d) ->", what, x0, y0);
        for (int y = std::max(0, y0 / 3 - 2); y <= std::min(OH - 1, y0 / 3 + 2); y++)
            for (int x = std::max(0, x0 / 3 - 2); x <= std::min(OW - 1, x0 / 3 + 2); x++) {
                const uint8_t* p = &dst[(size_t(y) * OW + x) * 3];
                if (p[0] || p[1] || p[2])
                    std::printf(" [%d,%d]=%d/%d/%d", x, y, p[0], p[1], p[2]);
            }
        std::printf("\n");
    };
    for (int x0 : {951, 952, 953, 954}) {
        run(x0, 540, 0, 0);
        changed("luma", x0, 540);
    }
    for (int y0 : {537, 538, 539, 540}) {
        run(960, y0, 0, 0);
        changed("luma", 960, y0);
    }
    // Chroma: one Cb sample (column cx0, row 270 of the 960x540 chroma plane) set to 240, on grey.
    // Output Cb from RGB: 128 - 0.148 R - 0.291 G + 0.439 B, with Y at 16 everywhere.
    for (int cx0 : {474, 475, 476, 477, 478, 479}) {
        run(-1, -1, cx0, 270);
        std::printf("cb (%d,270) ->", cx0);
        for (int y = 177; y <= 182; y++)
            for (int x = 314; x <= 321; x++) {
                const uint8_t* p = &dst[(size_t(y) * OW + x) * 3];
                if (p[0] || p[1] || p[2])
                    std::printf(" [%d,%d]=%d/%d/%d", x, y, p[0], p[1], p[2]);
            }
        std::printf("\n");
    }
    // Edges: an impulse in the last input column / row.
    run(1919, 540, 0, 0);
    changed("luma", 1919, 540);
    run(960, 1079, 0, 0);
    changed("luma", 960, 1079);

    // Color conversion alone: flat frames (no scaling effect), RGA's RGB next to OpenCV's
    // COLOR_YUV2RGB_NV12 for the same Y, Cb, Cr.
    std::printf("flat Y,Cb,Cr -> RGA RGB | OpenCV RGB\n");
    int diff_sum = 0, n = 0;
    for (int Y : {16, 30, 60, 90, 120, 150, 180, 210, 235})
        for (auto [U, V] : {std::pair{128, 128}, {64, 192}, {192, 64}, {100, 150}, {160, 110}}) {
            sync(sfd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_WRITE);
            std::memset(src, Y, size_t(W) * H);
            for (size_t i = 0; i < size_t(W) * H / 2; i += 2) {
                src[size_t(W) * H + i] = static_cast<uint8_t>(U);
                src[size_t(W) * H + i + 1] = static_cast<uint8_t>(V);
            }
            sync(sfd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_WRITE);
            im_opt_t opt{};
            opt.version = RGA_CURRENT_API_HEADER_VERSION;
            opt.core = IM_SCHEDULER_RGA3_CORE0;
            CHECK(improcess(s, d, {}, {0, 0, W, H}, {0, 0, OW, OH}, {}, -1, nullptr, &opt, IM_SYNC) ==
                      IM_STATUS_SUCCESS,
                  "improcess failed");
            sync(dfd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ);
            const uint8_t* p = &dst[(size_t(180) * OW + 320) * 3];
            uint8_t nv12[6] = {uint8_t(Y), uint8_t(Y), uint8_t(Y), uint8_t(Y), uint8_t(U), uint8_t(V)};
            cv::Mat rgb;
            cv::cvtColor(cv::Mat(3, 2, CV_8UC1, nv12), rgb, cv::COLOR_YUV2RGB_NV12);
            const cv::Vec3b o = rgb.at<cv::Vec3b>(0, 0);
            std::printf("%3d,%3d,%3d -> %3d %3d %3d | %3d %3d %3d\n", Y, U, V, p[0], p[1], p[2], o[0], o[1], o[2]);
            for (int c = 0; c < 3; c++)
                diff_sum += int(p[c]) - int(o[c]);
            n += 3;
        }
    std::printf("mean RGA - OpenCV: %.3f\n", double(diff_sum) / n);
    releasebuffer_handle(sh);
    releasebuffer_handle(dh);
    return 0;
}
