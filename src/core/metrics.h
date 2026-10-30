// Readers for the measurements of PROTOCOL §7: CPU time from /proc (§7.1, §7.2) and the
// one-second samples (§7.7).
#pragma once

#include <cstdint>
#include <map>
#include <string>
#include <vector>

struct ThreadCpu {
    std::string comm;
    std::string affinity;  // CPU list, e.g. "4-7"
    double cpu_s = 0;      // utime + stime
};

struct CpuSnapshot {
    double process_s = 0;                 // utime + stime of the whole process, /proc/self/stat
    std::map<int, ThreadCpu> threads;     // by tid, /proc/self/task/<tid>/stat
    std::vector<double> sys_nonidle_s;    // per CPU, /proc/stat
};
CpuSnapshot take_cpu_snapshot();

struct Sample {
    int64_t t_ns = 0;
    int npu_load[3] = {-1, -1, -1};        // %, -1 if unreadable (needs root)
    std::vector<int> temp_mc;              // per thermal zone, see thermal_zone_names()
    int64_t freq[5] = {};                  // policy0/4/6 kHz, dmc Hz, NPU Hz
    int64_t rss_kb = 0;
    std::vector<int64_t> decoded;          // per channel, frames returned by the decoder so far
};
std::vector<std::string> thermal_zone_names();
Sample take_sample(const std::vector<int64_t>& decoded);

// CPU list of a thread's affinity mask ("4-7", "0", "0-3,6").
std::string affinity_of(int tid);
// Pins the calling thread to the given CPUs.
void pin_current_thread(const std::vector<int>& cpus);
