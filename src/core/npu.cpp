#include "core/npu.h"

#include <algorithm>
#include <cstring>
#include <fstream>
#include <iterator>

#include "core/util.h"

namespace {

std::vector<char> read_file(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    CHECK(f.good(), "cannot read %s", path.c_str());
    return std::vector<char>((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
}

}  // namespace

void NpuContexts::open(const std::string& model_path) {
    std::vector<char> model = read_file(model_path);
    for (int i = 0; i < kCount; i++) {
        CHECK(rknn_init(&ctx[i], model.data(), model.size(), kInitFlags, nullptr) == RKNN_SUCC,
              "rknn_init failed");
        CHECK(rknn_set_core_mask(ctx[i], static_cast<rknn_core_mask>(RKNN_NPU_CORE_0 << i)) == RKNN_SUCC,
              "rknn_set_core_mask failed");
    }
    rknn_input_output_num io{};
    CHECK(rknn_query(ctx[0], RKNN_QUERY_IN_OUT_NUM, &io, sizeof io) == RKNN_SUCC, "query in/out failed");
    CHECK(io.n_input == 1, "expected one model input");
    input.index = input_native.index = 0;
    CHECK(rknn_query(ctx[0], RKNN_QUERY_INPUT_ATTR, &input, sizeof input) == RKNN_SUCC, "query input failed");
    CHECK(rknn_query(ctx[0], RKNN_QUERY_NATIVE_INPUT_ATTR, &input_native, sizeof input_native) == RKNN_SUCC,
          "query native input failed");
    CHECK(input.n_dims == 4 && input.dims[3] == 3, "expected an NHWC RGB input");

    outputs.resize(io.n_output);
    for (uint32_t o = 0; o < io.n_output; o++) {
        outputs[o].index = o;
        CHECK(rknn_query(ctx[0], RKNN_QUERY_OUTPUT_ATTR, &outputs[o], sizeof outputs[o]) == RKNN_SUCC,
              "query output failed");
    }
    // NCHW INT8 outputs: the cheapest binding the runtime accepts (docs/notes/spike.md).
    for (int i = 0; i < kCount; i++) {
        for (auto& a : outputs) {
            auto* m = rknn_create_mem(ctx[i], std::max(a.size, a.size_with_stride));
            CHECK(m && rknn_set_io_mem(ctx[i], m, &a) == RKNN_SUCC, "output binding failed");
            out_mem[i].push_back(m);
        }
    }
    rknn_sdk_version v{};
    if (rknn_query(ctx[0], RKNN_QUERY_SDK_VERSION, &v, sizeof v) == RKNN_SUCC)
        sdk_version = std::string(v.api_version) + " / driver " + v.drv_version;
}

void NpuContexts::close() {
    for (int i = 0; i < kCount; i++) {
        for (auto* m : out_mem[i])
            rknn_destroy_mem(ctx[i], m);
        out_mem[i].clear();
        if (ctx[i])
            rknn_destroy(ctx[i]);
        ctx[i] = 0;
    }
}

std::vector<const int8_t*> NpuContexts::output_ptrs(int k) const {
    std::vector<const int8_t*> p;
    for (auto* m : out_mem[k])
        p.push_back(static_cast<const int8_t*>(m->virt_addr));
    return p;
}

std::vector<std::vector<int8_t>> NpuContexts::run_bound(int k) {
    CHECK(rknn_run(ctx[k], nullptr) == RKNN_SUCC, "rknn_run failed");
    std::vector<std::vector<int8_t>> out;
    for (size_t o = 0; o < outputs.size(); o++) {
        auto* p = static_cast<const int8_t*>(out_mem[k][o]->virt_addr);
        out.emplace_back(p, p + outputs[o].size);
    }
    return out;
}

std::vector<std::vector<int8_t>> NpuContexts::reference_outputs(const std::vector<uint8_t>& input_bytes,
                                                                const std::string& model_path) const {
    std::vector<char> model = read_file(model_path);
    rknn_context ref = 0;
    CHECK(rknn_init(&ref, model.data(), model.size(), kInitFlags, nullptr) == RKNN_SUCC, "rknn_init failed");
    rknn_input in{};
    in.index = 0;
    in.type = RKNN_TENSOR_UINT8;
    in.fmt = RKNN_TENSOR_NHWC;
    in.size = static_cast<uint32_t>(input_bytes.size());
    in.buf = const_cast<uint8_t*>(input_bytes.data());
    CHECK(rknn_inputs_set(ref, 1, &in) == RKNN_SUCC && rknn_run(ref, nullptr) == RKNN_SUCC, "reference run failed");
    std::vector<rknn_output> out(outputs.size());
    for (auto& o : out)
        o.want_float = 0;
    CHECK(rknn_outputs_get(ref, static_cast<uint32_t>(out.size()), out.data(), nullptr) == RKNN_SUCC,
          "rknn_outputs_get failed");
    std::vector<std::vector<int8_t>> result;
    for (auto& o : out) {
        auto* p = static_cast<const int8_t*>(o.buf);
        result.emplace_back(p, p + o.size);
    }
    rknn_outputs_release(ref, static_cast<uint32_t>(out.size()), out.data());
    rknn_destroy(ref);
    return result;
}
