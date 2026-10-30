// The three RKNN contexts, one per NPU core, with one output set each (PROTOCOL §5.1, §8).
#pragma once

#include <string>
#include <vector>

#include "rknn_api.h"

struct NpuContexts {
    static constexpr int kCount = 3;
    static constexpr uint32_t kInitFlags = 0;  // synchronous, no RKNN_FLAG_DISABLE_FLUSH_* (PROTOCOL §8)

    rknn_context ctx[kCount] = {};
    rknn_tensor_attr input{};         // RKNN_QUERY_INPUT_ATTR
    rknn_tensor_attr input_native{};  // RKNN_QUERY_NATIVE_INPUT_ATTR
    std::vector<rknn_tensor_attr> outputs;  // NCHW INT8, as bound
    std::vector<rknn_tensor_mem*> out_mem[kCount];
    std::string sdk_version;

    // Loads the model into 3 contexts pinned to NPU cores 0, 1, 2 and binds their outputs
    // (rknn_create_mem + rknn_set_io_mem). Dies on failure.
    void open(const std::string& model_path);
    void close();

    int model_w() const { return static_cast<int>(input.dims[2]); }
    int model_h() const { return static_cast<int>(input.dims[1]); }
    std::vector<const int8_t*> output_ptrs(int k) const;

    // Outputs for `input_bytes` (uint8 RGB NHWC) from a separate context through rknn_inputs_set
    // and rknn_outputs_get, the runtime's own copy path. Reference for checking the bound outputs.
    std::vector<std::vector<int8_t>> reference_outputs(const std::vector<uint8_t>& input_bytes,
                                                       const std::string& model_path) const;
    // Runs context k on its bound input and returns a copy of its bound outputs.
    std::vector<std::vector<int8_t>> run_bound(int k);
};
