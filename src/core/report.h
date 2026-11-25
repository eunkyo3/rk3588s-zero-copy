// Writes one run's results (PROTOCOL §1, §7): every CSV starts with "# key: value" header lines.
//   frames.csv    one row per processed frame, times in ns since the timeline start; with --time-rknn on
//                 (arm Ct, M6 §1) one more column, rknn_cpu_ns
//   samples.csv   one row per second (§7.7)
//   channels.csv  per channel: decode rate, drift, decoder drops, sampling, Q1 drops, inference rate
//   summary.csv   key,value: CPU (§7.1, §7.2), latency percentiles (§7.3), drop rate (§7.5), RSS, clocks;
//                 with --time-rknn on, the rknn_run_* rows of M6 §3
//   threads.csv   CPU seconds in the window per thread that was alive at its end, with its affinity
//   env.json      arguments, every thread's affinity, library versions, binding paths
// With --snapshot on (M7 §1) also the files of core/snapshot.h, and with --thread-counters on those of
// core/perf_counters.h. The files above are then what they are without the options.
#pragma once

#include <string>
#include <utility>
#include <vector>

#include "core/pipeline.h"

using Header = std::vector<std::pair<std::string, std::string>>;

// Tag prereg-v1 (Release created_at 2026-10-30T02:01:36Z, docs/notes/env.md).
constexpr const char* kPreregCommit = "87fe874b4f196dafd48f6a175b2d7604625aedba";
// Tag prereg-m2 (Release created_at 2026-11-05T04:45:34Z, docs/notes/env.md).
constexpr const char* kPreregM2Commit = "5d9d6d04e50f17d52980dc5b958c7071d7107176";
// Tag prereg-m4 (Release created_at 2026-11-11T00:04:33Z, docs/notes/env.md).
constexpr const char* kPreregM4Commit = "8638685d9ef806a85e5042d7cc10febdd5ae93a9";
// Tag prereg-m6 (Release created_at 2026-11-23T23:47:43Z, docs/notes/env.md).
constexpr const char* kPreregM6Commit = "5a17d375d9aab77a302ec2295df886ee1257f3fd";
// Tag prereg-m7 (Release created_at 2026-11-25T08:33:00Z, docs/notes/env.md).
constexpr const char* kPreregM7Commit = "c40d1a05eadb5b9bb5ed93c592e3d339ca1aa2fb";
// M4's two INT8 models (prereg/M4.md §1, models/README.md): every m4 run checks its model file.
constexpr const char* kYolov8nSha256 = "04a76611a785802b08703192455994e07bbfe4531821ae7ed35faf78fe1ec1ee";
constexpr const char* kYolov10nSha256 = "6a6442e1addbd1f08dbdb0772aeea1a992233b4f1db3bd451d20823522246f81";
// The compiled objects of the pipeline's post-processings, relative to the repository: M1's and arm
// Cp's (M2 §4), then yolov10_post and the shared choice (M4 §8). The check tools, bench_main and
// calib_npu link the same ones from libcore.
constexpr int kPostObjectCount = 4;
constexpr const char* kPostObjects[kPostObjectCount] = {
    "build/CMakeFiles/core.dir/src/core/yolov8_post.cpp.o", "build/CMakeFiles/core.dir/src/core/yolov8_post_neon.cpp.o",
    "build/CMakeFiles/core.dir/src/core/yolov10_post.cpp.o", "build/CMakeFiles/core.dir/src/core/pipeline_post.cpp.o"};
// env.json's "objects_sha256": {"<path>": "<SHA-256>", ...} for kPostObjects, braces included.
std::string post_objects_json();

// Whether s is a full commit SHA: 40 lowercase hex digits.
bool is_commit_sha(const std::string& s);

// The output of a shell command, trailing newlines and spaces removed.
std::string command_output(const std::string& cmd);
// The current UTC time, formatted with strftime.
std::string utc_now(const char* format);
// The SHA-256 of a file as hex, empty if it cannot be read.
std::string file_sha256(const std::string& path);

// numpy.percentile with linear interpolation (PROTOCOL §7.3). v must not be empty.
double percentile(std::vector<double> v, double q);

// s as a JSON string literal, quotes included.
std::string json_string(const std::string& s);

// Returns the summary as key/value pairs, also written to summary.csv.
Header write_results(const std::string& dir, const Header& header, const PipelineConfig& cfg,
                     const RunResult& r, const std::string& env_json_body);
