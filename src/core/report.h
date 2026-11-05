// Writes one run's results (PROTOCOL §1, §7): every CSV starts with "# key: value" header lines.
//   frames.csv    one row per processed frame, times in ns since the timeline start
//   samples.csv   one row per second (§7.7)
//   channels.csv  per channel: decode rate, drift, decoder drops, sampling, Q1 drops, inference rate
//   summary.csv   key,value: CPU (§7.1, §7.2), latency percentiles (§7.3), drop rate (§7.5), RSS, clocks
//   threads.csv   CPU seconds in the window per thread that was alive at its end, with its affinity
//   env.json      arguments, every thread's affinity, library versions, binding paths
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
// The compiled objects of the two post-processings, relative to the repository (M2 §4): the check
// tool and bench_main link the same ones from libcore.
constexpr const char* kPostObjects[2] = {"build/CMakeFiles/core.dir/src/core/yolov8_post.cpp.o",
                                         "build/CMakeFiles/core.dir/src/core/yolov8_post_neon.cpp.o"};

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
