// M1-0 spike: decoded DRM PRIME frame -> RGA -> dma-heap slot registered with RKNN through
// rknn_create_mem_from_fd (path C2), one channel from a local file. Besides running the chain,
// it records what the plan asks the spike to establish (docs/notes/spike.md):
//   - frame layout (modifier, fd, strides) and how stable the buffers are (dma-buf inodes)
//   - which heap the decoder buffers come from, and whether the CPU reads them as cached memory
//   - whether one slot can be registered with all three contexts, and what freeing it in one
//     context does to the others
//   - whether RGA honours the RGA3 core mask, and whether RKNN converts the input on the CPU
// The debug checks read pixel memory with the CPU; the streaming loop itself does not.
// Run as root: the debugfs files (dma_buf/bufinfo, rkrga/load) need it.
//
// Usage: spike_fd_chain <video.mp4> <model.rknn> <out_dir> [frames=300] [rga_core_mask=0x3]
//   rga_core_mask: IM_SCHEDULER_* bits, 0x1/0x2 = RGA3 core 0/1, 0x4 = RGA2

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/hwcontext.h>
#include <libavutil/hwcontext_drm.h>
#include <libavutil/opt.h>
}

#include <drm_fourcc.h>
#include <fcntl.h>
#include <linux/dma-buf.h>
#include <linux/dma-heap.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cctype>
#include <cerrno>
#include <chrono>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include <im2d.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>

#include "core/yolov8_post.h"
#include "rknn_api.h"

namespace {

constexpr int kContexts = 3;
constexpr int kSlots = 11;  // N = K + depth(Q2) + 3 (PROTOCOL §5.1)
constexpr int kModelW = 640, kModelH = 640;
constexpr uint8_t kPad = 114;

[[noreturn]] void die(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    std::fprintf(stderr, "spike: ");
    std::vfprintf(stderr, fmt, ap);
    std::fprintf(stderr, "\n");
    va_end(ap);
    std::exit(1);
}

#define CHECK(cond, ...) \
    do {                 \
        if (!(cond))     \
            die(__VA_ARGS__); \
    } while (0)

void kv(const char* key, const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    std::printf("%s: ", key);
    std::vprintf(fmt, ap);
    std::printf("\n");
    va_end(ap);
    std::fflush(stdout);
}

using Clock = std::chrono::steady_clock;
double ms_since(Clock::time_point t) {
    return std::chrono::duration<double, std::milli>(Clock::now() - t).count();
}

// ---- dma-buf helpers -------------------------------------------------------

int heap_alloc(const std::string& heap, size_t len) {
    int hfd = open(("/dev/dma_heap/" + heap).c_str(), O_RDONLY | O_CLOEXEC);
    if (hfd < 0)
        return -1;
    dma_heap_allocation_data d{};
    d.len = len;
    d.fd_flags = O_RDWR | O_CLOEXEC;
    int r = ioctl(hfd, DMA_HEAP_IOCTL_ALLOC, &d);
    close(hfd);
    return r < 0 ? -1 : static_cast<int>(d.fd);
}

void dmabuf_sync(int fd, uint64_t flags) {
    dma_buf_sync s{flags};
    CHECK(ioctl(fd, DMA_BUF_IOCTL_SYNC, &s) == 0, "DMA_BUF_IOCTL_SYNC failed: %s", strerror(errno));
}

ino_t inode_of(int fd) {
    struct stat st {};
    CHECK(fstat(fd, &st) == 0, "fstat failed");
    return st.st_ino;
}

// CPU read bandwidth through a mapping (debug only). Cached memory reads at several GB/s,
// uncached memory at a few hundred MB/s. The first pass faults the pages in and is not timed.
double read_mb_s(const void* p, size_t len) {
    const auto* words = static_cast<const volatile uint64_t*>(p);
    uint64_t acc = 0;
    for (size_t i = 0; i < len / 8; i++)
        acc += words[i];
    auto t = Clock::now();
    for (int rep = 0; rep < 3; rep++)
        for (size_t i = 0; i < len / 8; i++)
            acc += words[i];
    double ms = ms_since(t);
    if (acc == 42)
        std::printf(" ");  // keep the reads
    return 3.0 * len / 1e6 / (ms / 1e3);
}

double dmabuf_read_mb_s(int fd, size_t len) {
    void* p = mmap(nullptr, len, PROT_READ, MAP_SHARED, fd, 0);
    CHECK(p != MAP_FAILED, "mmap of dma-buf failed: %s", strerror(errno));
    dmabuf_sync(fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ);
    double r = read_mb_s(p, len);
    dmabuf_sync(fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ);
    munmap(p, len);
    return r;
}

// Blocks of /sys/kernel/debug/dma_buf/bufinfo whose inode is in `inodes`.
std::map<ino_t, std::string> bufinfo_for(const std::set<ino_t>& inodes) {
    std::map<ino_t, std::string> out;
    std::ifstream f("/sys/kernel/debug/dma_buf/bufinfo");
    std::string line, block;
    ino_t cur = 0;
    auto flush = [&] {
        if (cur && inodes.count(cur))
            out[cur] = block;
        block.clear();
        cur = 0;
    };
    while (std::getline(f, line)) {
        std::istringstream ss(line);
        std::string size, flags, mode, count, exp, ino;
        if (!line.empty() && std::isdigit(static_cast<unsigned char>(line[0])) &&
            (ss >> size >> flags >> mode >> count >> exp >> ino)) {
            flush();
            cur = std::strtoul(ino.c_str(), nullptr, 10);
            block = "exp_name=" + exp + " size=" + std::to_string(std::stoul(size)) + " attached=";
        } else if (cur && !line.empty() && line[0] == '\t' && line.find("Attached") == std::string::npos) {
            block += line.substr(1) + ",";
        }
    }
    flush();
    return out;
}

// ---- RGA load sampling -----------------------------------------------------

struct RgaLoadSampler {
    std::atomic<bool> stop{false};
    std::map<std::string, int> max_load;  // "scheduler[i]: name" -> max %
    std::thread th;

    void start() {
        th = std::thread([this] {
            while (!stop) {
                std::ifstream f("/sys/kernel/debug/rkrga/load");
                std::string line, sched;
                while (std::getline(f, line)) {
                    if (line.rfind("scheduler[", 0) == 0) {
                        sched = line;
                    } else if (auto p = line.find("load = "); p != std::string::npos && !sched.empty()) {
                        int v = std::atoi(line.c_str() + p + 7);
                        max_load[sched] = std::max(max_load[sched], v);
                    }
                }
                std::this_thread::sleep_for(std::chrono::milliseconds(50));
            }
        });
    }
    void finish() {
        stop = true;
        th.join();
    }
};

// ---- RKNN ------------------------------------------------------------------

std::string attr_str(const rknn_tensor_attr& a) {
    std::string dims;
    for (uint32_t i = 0; i < a.n_dims; i++)
        dims += (i ? "x" : "") + std::to_string(a.dims[i]);
    char rest[160];
    std::snprintf(rest, sizeof rest, " fmt=%s type=%s size=%u size_with_stride=%u w_stride=%u zp=%d scale=%g",
                  get_format_string(a.fmt), get_type_string(a.type), a.size, a.size_with_stride, a.w_stride, a.zp,
                  a.scale);
    return std::string(a.name) + " dims=" + dims + rest;
}

double process_cpu_ms() {
    timespec ts{};
    clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &ts);
    return ts.tv_sec * 1e3 + ts.tv_nsec / 1e6;
}

std::vector<char> read_file(const char* path) {
    std::ifstream f(path, std::ios::binary);
    CHECK(f.good(), "cannot read %s", path);
    return std::vector<char>((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
}

struct Context {
    rknn_context ctx = 0;
    std::vector<rknn_tensor_mem*> in_mem;  // one per slot
    std::vector<rknn_tensor_mem*> out_mem;
};

struct Slot {
    int fd = -1;
    size_t size = 0;
    void* virt = nullptr;
    ino_t ino = 0;
    rga_buffer_handle_t rga = 0;
};

AVPixelFormat pick_drm_prime(AVCodecContext*, const AVPixelFormat* fmts) {
    for (const AVPixelFormat* p = fmts; *p != AV_PIX_FMT_NONE; p++)
        if (*p == AV_PIX_FMT_DRM_PRIME)
            return *p;
    die("decoder does not offer AV_PIX_FMT_DRM_PRIME");
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 4)
        die("usage: spike_fd_chain <video.mp4> <model.rknn> <out_dir> [frames=300] [rga_core_mask=0x3]");
    const char* video = argv[1];
    const char* model_path = argv[2];
    const std::string out_dir = argv[3];
    const int max_frames = argc > 4 ? std::atoi(argv[4]) : 300;
    const int rga_core_mask = argc > 5 ? static_cast<int>(std::strtol(argv[5], nullptr, 0))
                                       : IM_SCHEDULER_RGA3_CORE0 | IM_SCHEDULER_RGA3_CORE1;
    kv("rga_core_mask", "0x%x", rga_core_mask);

    // ---- RKNN contexts, one per NPU core ----
    std::vector<char> model = read_file(model_path);
    Context ctx[kContexts];
    for (int i = 0; i < kContexts; i++) {
        CHECK(rknn_init(&ctx[i].ctx, model.data(), model.size(), 0, nullptr) == RKNN_SUCC, "rknn_init failed");
        CHECK(rknn_set_core_mask(ctx[i].ctx, static_cast<rknn_core_mask>(RKNN_NPU_CORE_0 << i)) == RKNN_SUCC,
              "rknn_set_core_mask failed");
    }
    rknn_input_output_num io{};
    rknn_query(ctx[0].ctx, RKNN_QUERY_IN_OUT_NUM, &io, sizeof io);
    CHECK(io.n_input == 1, "expected one input");

    rknn_tensor_attr in_attr{}, in_native{};
    in_attr.index = in_native.index = 0;
    rknn_query(ctx[0].ctx, RKNN_QUERY_INPUT_ATTR, &in_attr, sizeof in_attr);
    rknn_query(ctx[0].ctx, RKNN_QUERY_NATIVE_INPUT_ATTR, &in_native, sizeof in_native);
    kv("rknn_input_attr", "%s", attr_str(in_attr).c_str());
    kv("rknn_native_input_attr", "%s", attr_str(in_native).c_str());

    std::vector<rknn_tensor_attr> out_attr(io.n_output), out_native(io.n_output);
    for (uint32_t o = 0; o < io.n_output; o++) {
        out_attr[o].index = out_native[o].index = o;
        rknn_query(ctx[0].ctx, RKNN_QUERY_OUTPUT_ATTR, &out_attr[o], sizeof out_attr[o]);
        rknn_query(ctx[0].ctx, RKNN_QUERY_NATIVE_OUTPUT_ATTR, &out_native[o], sizeof out_native[o]);
        kv(("rknn_output_attr_" + std::to_string(o)).c_str(), "%s", attr_str(out_attr[o]).c_str());
        kv(("rknn_native_output_attr_" + std::to_string(o)).c_str(), "%s", attr_str(out_native[o]).c_str());
    }

    // The slot holds uint8 RGB NHWC; RKNN converts nothing if that matches the native layout.
    in_attr.type = RKNN_TENSOR_UINT8;
    in_attr.fmt = RKNN_TENSOR_NHWC;
    in_attr.pass_through = 0;
    const size_t slot_size = std::max<size_t>(in_attr.size_with_stride, kModelW * kModelH * 3);
    CHECK(in_attr.dims[1] == kModelH && in_attr.dims[2] == kModelW && in_attr.dims[3] == 3,
          "unexpected input shape");

    // ---- input slots from the dma32 heap, registered with every context ----
    std::string heap = "system-uncached-dma32";
    Slot slot[kSlots];
    for (int s = 0; s < kSlots; s++) {
        slot[s].fd = heap_alloc(heap, slot_size);
        if (slot[s].fd < 0 && s == 0) {
            heap = "system-dma32";
            slot[s].fd = heap_alloc(heap, slot_size);
        }
        CHECK(slot[s].fd >= 0, "dma-heap allocation failed");
        slot[s].size = slot_size;
        slot[s].ino = inode_of(slot[s].fd);
        slot[s].virt = mmap(nullptr, slot_size, PROT_READ | PROT_WRITE, MAP_SHARED, slot[s].fd, 0);
        CHECK(slot[s].virt != MAP_FAILED, "mmap of slot failed");
        dmabuf_sync(slot[s].fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_WRITE);
        std::memset(slot[s].virt, kPad, slot_size);  // letterbox border, written once
        dmabuf_sync(slot[s].fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_WRITE);
        slot[s].rga = importbuffer_fd(slot[s].fd, static_cast<int>(slot_size));
        CHECK(slot[s].rga, "importbuffer_fd(slot) failed");
    }
    kv("slot_heap", "%s", heap.c_str());

    int multi_ctx_registration_ok = 1;
    for (int i = 0; i < kContexts; i++) {
        ctx[i].in_mem.resize(kSlots);
        for (int s = 0; s < kSlots; s++) {
            ctx[i].in_mem[s] = rknn_create_mem_from_fd(ctx[i].ctx, slot[s].fd, slot[s].virt,
                                                       static_cast<uint32_t>(slot_size), 0);
            if (!ctx[i].in_mem[s])
                multi_ctx_registration_ok = 0;
        }
        for (uint32_t o = 0; o < io.n_output; o++) {
            auto* m = rknn_create_mem(ctx[i].ctx, std::max(out_attr[o].size, out_attr[o].size_with_stride));
            CHECK(m, "rknn_create_mem(output) failed");
            CHECK(rknn_set_io_mem(ctx[i].ctx, m, &out_attr[o]) == RKNN_SUCC, "rknn_set_io_mem(output) failed");
            ctx[i].out_mem.push_back(m);
        }
    }
    kv("multi_context_registration", "%s", multi_ctx_registration_ok ? "ok" : "FAILED");
    CHECK(multi_ctx_registration_ok, "rknn_create_mem_from_fd failed for some context");

    {
        std::vector<uint8_t> cached(slot_size, 1);
        kv("cpu_read_mb_s", "slot=%.0f malloc=%.0f", dmabuf_read_mb_s(slot[0].fd, slot_size),
           read_mb_s(cached.data(), cached.size()));
    }

    // ---- decoder: h264_rkmpp, DRM PRIME, default buffer allocation ----
    AVFormatContext* fmt = nullptr;
    CHECK(avformat_open_input(&fmt, video, nullptr, nullptr) == 0, "cannot open %s", video);
    CHECK(avformat_find_stream_info(fmt, nullptr) >= 0, "no stream info");
    const int vs = av_find_best_stream(fmt, AVMEDIA_TYPE_VIDEO, -1, -1, nullptr, 0);
    CHECK(vs >= 0, "no video stream");
    const AVCodec* dec = avcodec_find_decoder_by_name("h264_rkmpp");
    CHECK(dec, "h264_rkmpp not found");
    AVCodecContext* cc = avcodec_alloc_context3(dec);
    avcodec_parameters_to_context(cc, fmt->streams[vs]->codecpar);
    cc->get_format = pick_drm_prime;
    cc->extra_hw_frames = 8;
    av_opt_set(cc->priv_data, "afbc", "off", 0);
    CHECK(avcodec_open2(cc, dec, nullptr) == 0, "avcodec_open2 failed");

    // ---- streaming loop ----
    std::map<ino_t, rga_buffer_handle_t> src_cache;  // keyed by dma-buf inode, not fd number
    int cache_w = 0, cache_h = 0;
    std::set<ino_t> dec_inodes;
    std::set<int> dec_fds;
    std::vector<double> dec_read_mb_s;
    const int settle_frame = max_frames / 3;  // buffers seen by then = the circulating set
    size_t inodes_settled = 0;
    int frames = 0, last_slot = -1, last_ctx = -1;
    double t_rga = 0, t_bind = 0, t_run = 0, t_post = 0;
    size_t total_dets = 0;
    std::vector<Detection> last_dets;
    double nv12_rgb_mae = -1;

    RgaLoadSampler rga_load;
    rga_load.start();

    AVPacket* pkt = av_packet_alloc();
    AVFrame* frame = av_frame_alloc();
    bool eof = false;
    while (frames < max_frames) {
        int r = avcodec_receive_frame(cc, frame);
        if (r == AVERROR(EAGAIN)) {
            if (eof)
                break;
            if (av_read_frame(fmt, pkt) < 0) {
                avcodec_send_packet(cc, nullptr);
                eof = true;
                continue;
            }
            if (pkt->stream_index == vs)
                CHECK(avcodec_send_packet(cc, pkt) == 0, "send_packet failed");
            av_packet_unref(pkt);
            continue;
        }
        if (r == AVERROR_EOF)
            break;
        CHECK(r == 0, "receive_frame failed");
        CHECK(frame->format == AV_PIX_FMT_DRM_PRIME, "frame is not DRM PRIME");

        const auto* desc = reinterpret_cast<const AVDRMFrameDescriptor*>(frame->data[0]);
        const AVDRMLayerDescriptor& layer = desc->layers[0];
        if (frames == 0) {
            kv("frame", "%dx%d nb_objects=%d nb_layers=%d layer_format=%.4s nb_planes=%d modifier=0x%llx "
               "object_size=%zu pitch0=%td offset1=%td pitch1=%td",
               frame->width, frame->height, desc->nb_objects, desc->nb_layers, (const char*)&layer.format,
               layer.nb_planes, (unsigned long long)desc->objects[0].format_modifier, desc->objects[0].size,
               layer.planes[0].pitch, layer.planes[1].offset, layer.planes[1].pitch);
        }
        if (frames == settle_frame)
            inodes_settled = dec_inodes.size();
        CHECK(desc->nb_objects == 1 && desc->nb_layers == 1 && layer.format == DRM_FORMAT_NV12 &&
                  layer.nb_planes == 2,
              "unexpected DRM frame layout");
        CHECK(desc->objects[0].format_modifier == DRM_FORMAT_MOD_LINEAR, "frame is not linear");
        const int fd = desc->objects[0].fd;
        const int wstride = static_cast<int>(layer.planes[0].pitch);
        CHECK(layer.planes[1].offset % layer.planes[0].pitch == 0, "UV offset is not a whole number of rows");
        const int hstride = static_cast<int>(layer.planes[1].offset / layer.planes[0].pitch);
        const ino_t ino = inode_of(fd);
        dec_fds.insert(fd);

        if (frame->width != cache_w || frame->height != cache_h) {  // info change: drop cached handles
            for (auto& [i, h] : src_cache)
                releasebuffer_handle(h);
            src_cache.clear();
            cache_w = frame->width;
            cache_h = frame->height;
        }
        if (!dec_inodes.count(ino)) {
            dec_inodes.insert(ino);
            dec_read_mb_s.push_back(dmabuf_read_mb_s(fd, desc->objects[0].size));  // debug only, first sight
        }
        auto it = src_cache.find(ino);
        if (it == src_cache.end()) {
            rga_buffer_handle_t h = importbuffer_fd(fd, static_cast<int>(desc->objects[0].size));
            CHECK(h, "importbuffer_fd(frame) failed");
            it = src_cache.emplace(ino, h).first;
        }

        // prepare: RGA scales 1/3 and converts NV12 -> RGB into the 640x360 band of the slot
        const int s = frames % kSlots;
        const int k = frames % kContexts;
        const float scale = std::min(float(kModelW) / frame->width, float(kModelH) / frame->height);
        const int dw = int(frame->width * scale + 0.5f) & ~1, dh = int(frame->height * scale + 0.5f) & ~1;
        const im_rect srect{0, 0, frame->width, frame->height};
        const im_rect drect{(kModelW - dw) / 2, (kModelH - dh) / 2, dw, dh};
        rga_buffer_t src = wrapbuffer_handle(it->second, frame->width, frame->height,
                                             RK_FORMAT_YCbCr_420_SP, wstride, hstride);
        rga_buffer_t dst = wrapbuffer_handle(slot[s].rga, kModelW, kModelH, RK_FORMAT_RGB_888);
        imsetColorSpace(&dst, IM_YUV_TO_RGB_BT601_LIMIT);
        rga_buffer_t pat{};
        im_opt_t opt{};
        opt.version = RGA_CURRENT_API_HEADER_VERSION;
        opt.core = rga_core_mask;
        auto t0 = Clock::now();
        IM_STATUS st = improcess(src, dst, pat, srect, drect, {}, -1, nullptr, &opt, IM_SYNC);
        t_rga += ms_since(t0);
        CHECK(st == IM_STATUS_SUCCESS, "improcess failed: %s", imStrError(st));

        // Debug only: compare the RGA band with OpenCV's BT.601 limited-range conversion of the same frame.
        if (frames == max_frames - 1) {
            const size_t len = desc->objects[0].size;
            auto* p = static_cast<uint8_t*>(mmap(nullptr, len, PROT_READ, MAP_SHARED, fd, 0));
            CHECK(p != MAP_FAILED, "mmap of frame failed");
            dmabuf_sync(fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ);
            cv::Mat y(frame->height, frame->width, CV_8UC1, p, wstride);
            cv::Mat uv(frame->height / 2, frame->width / 2, CV_8UC2, p + layer.planes[1].offset, wstride);
            cv::Mat rgb;
            cv::cvtColorTwoPlane(y, uv, rgb, cv::COLOR_YUV2RGB_NV12);
            dmabuf_sync(fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ);
            munmap(p, len);
            cv::Mat ref;
            cv::resize(rgb, ref, cv::Size(dw, dh), 0, 0, cv::INTER_LINEAR);
            dmabuf_sync(slot[s].fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ);
            cv::Mat band = cv::Mat(kModelH, kModelW, CV_8UC3, slot[s].virt)(cv::Rect(drect.x, drect.y, dw, dh)).clone();
            dmabuf_sync(slot[s].fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ);
            cv::Mat diff;
            cv::absdiff(band, ref, diff);
            nv12_rgb_mae = cv::sum(cv::mean(diff))[0] / 3;  // mean over pixels and the 3 channels
        }
        av_frame_unref(frame);  // released right after prepare (PROTOCOL §5.1)

        // bind + run + post-process
        t0 = Clock::now();
        CHECK(rknn_set_io_mem(ctx[k].ctx, ctx[k].in_mem[s], &in_attr) == RKNN_SUCC, "rknn_set_io_mem(input) failed");
        t_bind += ms_since(t0);
        t0 = Clock::now();
        CHECK(rknn_run(ctx[k].ctx, nullptr) == RKNN_SUCC, "rknn_run failed");
        t_run += ms_since(t0);
        t0 = Clock::now();
        std::vector<const int8_t*> outs;
        for (auto* m : ctx[k].out_mem)
            outs.push_back(static_cast<const int8_t*>(m->virt_addr));
        last_dets = yolov8_post(outs, out_attr, kModelW, kModelH);
        t_post += ms_since(t0);
        total_dets += last_dets.size();
        last_slot = s;
        last_ctx = k;
        frames++;
    }
    rga_load.finish();

    // In buf_mode=half, ffmpeg-rockchip caps the MPP buffer group at decoder_pool_size (20 for H.264)
    // + 10 + extra_hw_frames (rkmppdec.c); hw_frames_ctx->initial_pool_size stays 0. Not queryable,
    // so the limit is taken from the source.
    const size_t group_limit = 20 + 10 + 8;
    kv("frames", "%d", frames);
    kv("distinct_decoder_inodes", "%zu (by frame %d: %zu)", dec_inodes.size(), settle_frame, inodes_settled);
    kv("distinct_decoder_fds", "%zu", dec_fds.size());
    kv("fd_stability", "%s (distinct inodes %zu <= buffer group limit %zu from rkmppdec.c, no new buffer after frame %d)",
       dec_inodes.size() <= group_limit && dec_inodes.size() == inodes_settled ? "ok" : "FAILED",
       dec_inodes.size(), group_limit, settle_frame);
    kv("mean_ms", "rga=%.3f bind=%.3f run=%.3f post=%.3f", t_rga / frames, t_bind / frames, t_run / frames,
       t_post / frames);
    kv("detections", "total=%zu last_frame=%zu", total_dets, last_dets.size());
    kv("nv12_rgb_mae_vs_opencv", "%.3f", nv12_rgb_mae);
    for (auto& [sched, v] : rga_load.max_load)
        kv("rga_max_load", "%s %d%%", sched.c_str(), v);

    kv("cpu_read_mb_s", "decoder=%.0f..%.0f", *std::min_element(dec_read_mb_s.begin(), dec_read_mb_s.end()),
       *std::max_element(dec_read_mb_s.begin(), dec_read_mb_s.end()));

    std::set<ino_t> all_inodes = dec_inodes;
    for (auto& sl : slot)
        all_inodes.insert(sl.ino);
    for (auto& [ino, info] : bufinfo_for(all_inodes))
        kv("bufinfo", "%s ino=%lu %s", dec_inodes.count(ino) ? "decoder" : "slot", (unsigned long)ino, info.c_str());

    // ---- multi-context checks on the last slot ----
    CHECK(last_slot >= 0, "no frame processed");
    const int s = last_slot;
    std::vector<std::vector<int8_t>> ref(kContexts);
    for (int i = 0; i < kContexts; i++) {
        CHECK(rknn_set_io_mem(ctx[i].ctx, ctx[i].in_mem[s], &in_attr) == RKNN_SUCC, "bind failed");
        CHECK(rknn_run(ctx[i].ctx, nullptr) == RKNN_SUCC, "run failed");
        for (size_t o = 0; o < ctx[i].out_mem.size(); o++) {
            auto* p = static_cast<const int8_t*>(ctx[i].out_mem[o]->virt_addr);
            ref[i].insert(ref[i].end(), p, p + out_attr[o].size);
        }
    }
    kv("same_slot_all_contexts_identical", "%d", ref[0] == ref[1] && ref[1] == ref[2]);

    // Free the slot in context 0, then run it in contexts 1 and 2. Before each run, the context first
    // runs another slot (a different frame), so the output buffers cannot still hold the reference.
    auto run_slot = [&](int i, int slot_idx, std::vector<int8_t>& got) {
        got.clear();
        if (rknn_set_io_mem(ctx[i].ctx, ctx[i].in_mem[slot_idx], &in_attr) != RKNN_SUCC ||
            rknn_run(ctx[i].ctx, nullptr) != RKNN_SUCC)
            return false;
        for (size_t o = 0; o < ctx[i].out_mem.size(); o++) {
            auto* p = static_cast<const int8_t*>(ctx[i].out_mem[o]->virt_addr);
            got.insert(got.end(), p, p + out_attr[o].size);
        }
        return true;
    };
    const int other = (s + kSlots - 1) % kSlots;  // the previous frame
    CHECK(rknn_destroy_mem(ctx[0].ctx, ctx[0].in_mem[s]) == RKNN_SUCC, "rknn_destroy_mem failed");
    ctx[0].in_mem[s] = nullptr;
    int after_release_ok = 1, other_differs = 1;
    for (int i = 1; i < kContexts; i++) {
        std::vector<int8_t> got;
        after_release_ok &= run_slot(i, other, got);
        other_differs &= got != ref[i];
        after_release_ok &= run_slot(i, s, got) && got == ref[i];
    }
    std::vector<int8_t> got0;
    after_release_ok &= run_slot(0, other, got0);
    kv("release_test_outputs_changed_in_between", "%d", other_differs);
    kv("release_in_one_context_others_unaffected", "%d", after_release_ok);

    // ---- CPU time inside rknn_run: does the runtime convert the input type or the output layout? ----
    // The native input is INT8 NHWC and the native outputs are NC1HWC2. A pass-through INT8 input
    // gives wrong detections but shows the cost without input conversion. The configurations of
    // one output binding alternate in rounds, so drift affects them alike; per-run process CPU time
    // (all threads, including the runtime's) is reported as median and range over the rounds.
    rknn_tensor_attr in_raw = in_native;
    in_raw.pass_through = 1;
    auto compare_inputs = [&](const char* out_name) {
        constexpr int kRounds = 15, kRuns = 20;
        const char* names[2] = {"uint8_in", "int8_passthrough_in"};
        const rknn_tensor_attr* attrs[2] = {&in_attr, &in_raw};
        std::vector<double> cpu[2], wall[2];
        for (int round = 0; round < kRounds; round++) {
            for (int c = 0; c < 2; c++) {
                rknn_tensor_attr a = *attrs[c];
                CHECK(rknn_set_io_mem(ctx[0].ctx, ctx[0].in_mem[other], &a) == RKNN_SUCC, "bind failed");
                const double c0 = process_cpu_ms();
                const auto t0 = Clock::now();
                for (int r = 0; r < kRuns; r++)
                    CHECK(rknn_run(ctx[0].ctx, nullptr) == RKNN_SUCC, "run failed (%s %s)", names[c], out_name);
                wall[c].push_back(ms_since(t0) / kRuns);
                cpu[c].push_back((process_cpu_ms() - c0) / kRuns);
            }
        }
        for (int c = 0; c < 2; c++) {
            std::sort(cpu[c].begin(), cpu[c].end());
            std::sort(wall[c].begin(), wall[c].end());
            kv("rknn_run_cost", "%-20s %-10s cpu_ms median=%.3f range=%.3f..%.3f wall_ms median=%.3f "
               "(%d rounds x %d runs)", names[c], out_name, cpu[c][kRounds / 2], cpu[c].front(), cpu[c].back(),
               wall[c][kRounds / 2], kRounds, kRuns);
        }
    };
    auto bind_outputs = [&](rknn_query_cmd cmd, const char* name) {
        std::vector<rknn_tensor_mem*> mems;
        bool ok = true;
        for (uint32_t o = 0; o < io.n_output && ok; o++) {
            rknn_tensor_attr a{};
            a.index = o;
            rknn_query(ctx[0].ctx, cmd, &a, sizeof a);
            auto* m = rknn_create_mem(ctx[0].ctx, std::max(a.size, a.size_with_stride));
            ok = m && rknn_set_io_mem(ctx[0].ctx, m, &a) == RKNN_SUCC;
            mems.push_back(m);
        }
        rknn_tensor_attr a = in_attr;
        ok = ok && rknn_set_io_mem(ctx[0].ctx, ctx[0].in_mem[other], &a) == RKNN_SUCC &&
             rknn_run(ctx[0].ctx, nullptr) == RKNN_SUCC;
        kv("output_binding", "%s %s", name, ok ? "runs" : "FAILED (bind or rknn_run)");
        return std::make_pair(ok, mems);
    };
    compare_inputs("nchw_out");
    auto nhwc = bind_outputs(RKNN_QUERY_NATIVE_NHWC_OUTPUT_ATTR, "native_nhwc");
    if (nhwc.first)
        compare_inputs("nhwc_out");
    // Last, because a failing rknn_run may leave the context unusable.
    auto nc1hwc2 = bind_outputs(RKNN_QUERY_NATIVE_OUTPUT_ATTR, "native_nc1hwc2");
    for (auto* m : nhwc.second)
        if (m)
            rknn_destroy_mem(ctx[0].ctx, m);
    for (auto* m : nc1hwc2.second)
        if (m)
            rknn_destroy_mem(ctx[0].ctx, m);

    // ---- result image (debug readback of the last slot) ----
    {
        cv::Mat rgb(kModelH, kModelW, CV_8UC3);
        dmabuf_sync(slot[s].fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ);
        std::memcpy(rgb.data, slot[s].virt, rgb.total() * 3);
        dmabuf_sync(slot[s].fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ);
        cv::Mat bgr;
        cv::cvtColor(rgb, bgr, cv::COLOR_RGB2BGR);
        for (const auto& d : last_dets) {
            cv::rectangle(bgr, cv::Point(int(d.x1), int(d.y1)), cv::Point(int(d.x2), int(d.y2)), {0, 255, 0}, 2);
            char label[64];
            std::snprintf(label, sizeof label, "%s %.2f", coco_label(d.cls), d.score);
            cv::putText(bgr, label, cv::Point(int(d.x1), std::max(12, int(d.y1) - 4)), cv::FONT_HERSHEY_SIMPLEX,
                        0.5, {0, 255, 0}, 1);
            kv("det", "%s %.3f %.0f,%.0f,%.0f,%.0f (ctx %d)", coco_label(d.cls), d.score, d.x1, d.y1, d.x2, d.y2,
               last_ctx);
        }
        const std::string path = out_dir + "/spike_result.jpg";
        CHECK(cv::imwrite(path, bgr), "cannot write %s", path.c_str());
        kv("result_image", "%s", path.c_str());
    }

    // ---- shutdown: RKNN memory first, then fds (PROTOCOL §5.4 release policy) ----
    for (auto& c : ctx) {
        for (auto* m : c.in_mem)
            if (m)
                rknn_destroy_mem(c.ctx, m);
        for (auto* m : c.out_mem)
            rknn_destroy_mem(c.ctx, m);
        rknn_destroy(c.ctx);
    }
    for (auto& [i, h] : src_cache)
        releasebuffer_handle(h);
    for (auto& sl : slot) {
        releasebuffer_handle(sl.rga);
        munmap(sl.virt, sl.size);
        close(sl.fd);
    }
    av_frame_free(&frame);
    av_packet_free(&pkt);
    avcodec_free_context(&cc);
    avformat_close_input(&fmt);
    return 0;
}
