// Diagnostic for AMENDMENTS A3: does rknn_inputs_set feed a context whose outputs are bound with
// rknn_set_io_mem (librknnrt 2.3.2)? The first frame of a video, preprocessed as A does (software
// decoder, OpenCV), goes in three ways: twice into context 0, then into the untouched context 1.
// Each time the bound outputs are zeroed first, then compared with rknn_outputs_get of a separate
// context on the same input.
//   inputs_set         rknn_inputs_set (PROTOCOL §5.2 as registered)
//   inputs_set+rebind  rknn_inputs_set, then the outputs bound again with rknn_set_io_mem
//   bound_input        memcpy into an input buffer bound with rknn_set_io_mem (A3)
//
// Usage: inputs_set_probe <video.mp4> <model.rknn>   (as root)
#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <vector>

#include "core/npu.h"
#include "core/util.h"
#include "core/video_source.h"
#include "core/yolov8_post.h"
#include "preprocess/cpu_common.h"

int main(int argc, char** argv) {
    if (argc < 3)
        die("usage: inputs_set_probe <video.mp4> <model.rknn>");
    NpuContexts npu;
    npu.open(argv[2]);
    const int mw = npu.model_w(), mh = npu.model_h();

    std::vector<uint8_t> input(size_t(mw) * mh * 3, 114);
    std::atomic<bool> stop{false};
    VideoSource src(argv[1], /*software=*/true, /*paced=*/false);
    src.open(&stop);
    src.run(
        [&](AVFrame* f) {
            if (!stop) {
                letterbox_i420(f->data[0], f->linesize[0], f->data[1], f->linesize[1], f->data[2], f->linesize[2],
                               f->width, f->height, input.data(), mw, mh, opencv_interpolation());
                stop = true;
            }
            av_frame_free(&f);
        },
        [] { die("decoder error"); });

    const auto reference = npu.reference_outputs(input, argv[2]);
    std::vector<const int8_t*> ref_ptrs;
    for (const auto& o : reference)
        ref_ptrs.push_back(o.data());
    std::printf("librknnrt: %s\nreference (rknn_outputs_get): %zu boxes\n", npu.sdk_version.c_str(),
                yolov8_post(ref_ptrs, npu.outputs, mw, mh).size());

    rknn_input in{};
    in.index = 0;
    in.type = RKNN_TENSOR_UINT8;
    in.fmt = RKNN_TENSOR_NHWC;
    in.size = static_cast<uint32_t>(input.size());
    in.buf = input.data();
    auto report = [&](const char* how, int k) {
        const auto out = npu.run_bound(k);
        bool zero = true;
        for (const auto& o : out)
            zero &= std::all_of(o.begin(), o.end(), [](int8_t v) { return v == 0; });
        std::printf("%-18s outputs still zero %d, equal to reference %d, %zu boxes\n", how, zero, out == reference,
                    yolov8_post(npu.output_ptrs(k), npu.outputs, mw, mh).size());
    };
    auto zero_outputs = [&](int k) {
        for (size_t o = 0; o < npu.outputs.size(); o++)
            std::memset(npu.out_mem[k][o]->virt_addr, 0, npu.outputs[o].size);
    };

    zero_outputs(0);
    CHECK(rknn_inputs_set(npu.ctx[0], 1, &in) == RKNN_SUCC, "rknn_inputs_set failed");
    report("inputs_set", 0);

    zero_outputs(0);
    CHECK(rknn_inputs_set(npu.ctx[0], 1, &in) == RKNN_SUCC, "rknn_inputs_set failed");
    for (size_t o = 0; o < npu.outputs.size(); o++)
        CHECK(rknn_set_io_mem(npu.ctx[0], npu.out_mem[0][o], &npu.outputs[o]) == RKNN_SUCC, "rebind failed");
    report("inputs_set+rebind", 0);

    // A3: B's binding (CpuSlots), on context 1, which has never seen rknn_inputs_set.
    rknn_tensor_attr attr = npu.input;
    attr.type = RKNN_TENSOR_UINT8;
    attr.fmt = RKNN_TENSOR_NHWC;
    attr.pass_through = 0;
    rknn_tensor_mem* mem = rknn_create_mem(npu.ctx[1], std::max<uint32_t>(attr.size_with_stride, in.size));
    CHECK(mem && rknn_set_io_mem(npu.ctx[1], mem, &attr) == RKNN_SUCC, "input binding failed");
    zero_outputs(1);
    std::memcpy(mem->virt_addr, input.data(), input.size());
    report("bound_input", 1);

    rknn_destroy_mem(npu.ctx[1], mem);
    npu.close();
    return 0;
}
