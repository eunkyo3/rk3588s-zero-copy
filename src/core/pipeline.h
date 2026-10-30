// The shared core of PROTOCOL §5.1:
//   decoder thread (1 per channel) -> sampler -> Q1 -> preprocessing pool [acquire_slot -> prepare]
//   -> Q2 -> NPU workers [bind -> rknn_run -> post-process]
// plus the timeline (warm-up, measurement window) and the per-frame and per-second records of §7.
// Without a backend (variant "none") sampled frames are released at once.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "core/metrics.h"
#include "core/npu.h"
#include "preprocess/backend.h"

constexpr int kInputSlots = 11;  // N = K + depth(Q2) + 3 (PROTOCOL §5.1)

struct PipelineConfig {
    std::vector<std::string> sources;  // one URL or file per channel
    int sample_stride = 6;             // 6: main condition (5 fps), 1: auxiliary condition (30 fps)
    int warmup_s = 20;
    int duration_s = 120;
};

struct FrameRecord {
    int channel;
    int64_t seq, pts;
    int64_t t0, t1, t2a, t2b, t2c, t3, t4;  // CLOCK_MONOTONIC ns (PROTOCOL §7.3)
    int64_t slot_wait_ns;
    int ctx, slot, detections;
};

struct ChannelResult {
    int64_t decoded_in_window = 0;   // from the samples at the window's start and end
    double drift_max_s = 0;          // PROTOCOL §7.6
    int64_t decoder_drops = 0;       // PROTOCOL §7.6, frames returned in the window
    int64_t sampled_in_window = 0;
    int64_t q1_drops_in_window = 0;  // frames of this channel dropped from Q1 in the window
    bool ended_early = false;        // the input ended or failed before the run stopped
};

struct RunResult {
    int64_t t_start = 0, t_w = 0, t_e = 0;  // timeline start, window start and end
    std::vector<ChannelResult> channels;
    std::vector<FrameRecord> frames;       // every processed frame, warm-up included
    std::vector<Sample> samples;           // one per second from t_start to t_e
    CpuSnapshot cpu_w, cpu_e;              // at t_w and t_e
};

// npu and backend are null for variant "none".
RunResult run_pipeline(const PipelineConfig& cfg, NpuContexts* npu, Backend* backend);
