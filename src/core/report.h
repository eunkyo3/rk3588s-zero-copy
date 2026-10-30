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

// numpy.percentile with linear interpolation (PROTOCOL §7.3). v must not be empty.
double percentile(std::vector<double> v, double q);

// s as a JSON string literal, quotes included.
std::string json_string(const std::string& s);

// Returns the summary as key/value pairs, also written to summary.csv.
Header write_results(const std::string& dir, const Header& header, const PipelineConfig& cfg,
                     const RunResult& r, const std::string& env_json_body);
