// The shared core of PROTOCOL §5.1:
//   decoder thread (1 per channel) -> sampler -> Q1 -> preprocessing pool [acquire_slot -> prepare]
//   -> Q2 -> NPU workers [bind -> rknn_run -> post-process]
// plus the timeline (warm-up, measurement window) and the per-frame and per-second records of §7.
// Without a backend (variant "none") sampled frames are released at once.
#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "core/metrics.h"
#include "core/npu.h"
#include "core/perf_counters.h"
#include "core/pipeline_post.h"
#include "core/snapshot.h"
#include "preprocess/backend.h"

constexpr int kQ2Depth = 6;
// N = K + depth(Q2) + 3 (PROTOCOL §5.1): 11 for M1's K = 2.
constexpr int input_slots(int preprocess_threads) { return preprocess_threads + kQ2Depth + 3; }

struct PipelineConfig {
    std::vector<std::string> sources;  // one URL or file per channel
    int sample_stride = 6;             // 6: main condition (5 fps), 1: auxiliary condition (30 fps)
    int warmup_s = 20;
    int duration_s = 120;
    bool software_decode = false;      // variant A
    int preprocess_threads = 2;        // K; 4 for M1.1's B with K = 4 (AMENDMENTS A12)
    int metrics_cpu = 0;               // 4 when the pipeline runs on the A55 cores (A12)
    bool stagger = false;              // M2 §3.1: channel i samples the frames with counter % stride == i % stride
    const PipelinePost* post = nullptr;   // required with a backend: M1's, arm Cp's (M2 §1) or C10's (M4 §1)
    bool time_rknn = false;            // arm Ct (M6 §1): the worker's thread CPU time around each rknn_run
    // M7 §1. snapshot: the metrics thread reads the system's tasks and interrupts one second before the
    // window and right after it, with a cycle counter on cpu0 between the two. thread_counters: a group of
    // PMU counters on every thread of the process over the window. Both need a warm-up of at least 3 s.
    bool snapshot = false;
    bool thread_counters = false;
};

struct FrameRecord {
    int channel;
    int64_t seq, pts;
    int64_t t0, t1, t2a, t2b, t2c, t3, t4;  // CLOCK_MONOTONIC ns (PROTOCOL §7.3)
    int64_t slot_wait_ns;
    int ctx, slot, detections;
    int64_t rknn_cpu_ns;  // with time_rknn: CLOCK_THREAD_CPUTIME_ID after rknn_run minus before it; else 0
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
    int64_t wall_start_ns = 0;               // CLOCK_REALTIME at t_start, to align other machines' logs
    std::vector<ChannelResult> channels;
    std::vector<FrameRecord> frames;       // every processed frame, warm-up included
    std::vector<Sample> samples;           // one per second from t_start to t_e
    CpuSnapshot cpu_w, cpu_e;              // at t_w and t_e
    // M7 §1, with their options; otherwise not taken, not open and null.
    SystemSnapshot snap1, snap2;           // at the tick one second before t_w, and after cpu_e
    CpuCycleCounter cpu0;                  // enabled inside snap1, read inside snap2
    int64_t t_snap1_wake = 0, t_end_wake = 0;  // when the metrics thread woke for snap1's tick and for t_e's
    std::shared_ptr<ThreadCounters> counters;  // opened two seconds before t_w, enabled after cpu_w,
                                               // disabled after cpu_e
};

// npu and backend are null for variant "none".
RunResult run_pipeline(const PipelineConfig& cfg, NpuContexts* npu, Backend* backend);
