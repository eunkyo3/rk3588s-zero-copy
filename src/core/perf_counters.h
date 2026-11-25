// The PMU's counters of prereg/M7.md §1, opened with perf_event_open (no tool is installed on the board):
//   - ThreadCounters, for --thread-counters on: one group of events per thread of this process, counting
//     in user and kernel mode on that thread wherever it runs;
//   - CpuCycleCounter, for --snapshot on: one cpu_cycles counter on one CPU, for every task and the kernel.
// The events are those of the PMU armv8_pmuv3, and their codes are read from sysfs.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

constexpr const char* kPmuName = "armv8_pmuv3";
// The group's events, in its order (M7 §1). The set is fixed before freeze-m7 (M7 §8): an event that is
// left out is removed here, and the amendment says so. cpu_cycles and inst_retired are needed.
constexpr int kThreadCounterEventCount = 5;
constexpr const char* kThreadCounterEvents[kThreadCounterEventCount] = {
    "cpu_cycles", "inst_retired", "l2d_cache_refill", "l3d_cache_refill", "stall_backend"};

struct PmuEvent {
    std::string name;
    bool found = false;  // the PMU and the event are in sysfs
    uint32_t type = 0;   // /sys/bus/event_source/devices/<pmu>/type
    uint64_t config = 0; // "event=0x..." of /sys/bus/event_source/devices/<pmu>/events/<name>
};
PmuEvent pmu_event(const std::string& name);
// The config of an events file's text ("event=0x0011\n"); false if it has no event= term.
bool parse_event_config(const std::string& text, uint64_t* config);

struct ThreadCount {
    int tid = 0;
    std::string comm;
    bool opened = false;       // the whole group
    int open_errno = 0;        // of the first event that could not be opened
    std::string failed_event;  // its name
    bool enable_ok = false, disable_ok = false;  // the two ioctls on the group
    bool read_ok = false;
    uint64_t time_enabled = 0, time_running = 0;  // ns, of the group's leader
    std::vector<uint64_t> values;                 // one per event, in the group's order
    // The thread's run time from its schedstat (-1 if unreadable), read right after its group was
    // enabled and right after it was disabled, and when (CLOCK_MONOTONIC ns).
    int64_t run_ns_enable = -1, run_ns_disable = -1;
    int64_t t_enable = 0, t_disable = 0;
};

class ThreadCounters {
public:
    ThreadCounters() = default;
    ThreadCounters(const ThreadCounters&) = delete;
    ThreadCounters& operator=(const ThreadCounters&) = delete;
    ~ThreadCounters();
    // One group, disabled, for every thread this process has now. A thread whose group cannot be opened
    // keeps its row with opened = false.
    void open_all();
    void enable_all();   // one ioctl per thread, then its schedstat
    void disable_all();  // one ioctl per thread, then its schedstat; then every group's values
    const std::vector<PmuEvent>& events() const { return events_; }
    const std::vector<ThreadCount>& threads() const { return threads_; }

private:
    std::vector<PmuEvent> events_;
    std::vector<ThreadCount> threads_;
    std::vector<std::vector<int>> fds_;  // per thread: the group's descriptors, leader first
};

struct CpuCycleCounter {
    int cpu = 0;
    int fd = -1;
    int open_errno = 0;  // 0 when it is open
    bool enabled = false, read_ok = false;
    int64_t t_enable = 0, t_read = 0;  // CLOCK_MONOTONIC ns
    uint64_t cycles = 0, time_enabled = 0, time_running = 0;
    PmuEvent event;

    void open(int cpu);  // disabled
    void enable();
    void read_now();     // the counter keeps counting
    void close();
};

// The counters' files in dir (M7 §1), with "# key: value" header lines:
//   counter_events.csv  name,pmu_type,config (hex): the group's events and their codes
//   counters.csv        tid,comm,opened,open_errno,failed_event,enable_ok,disable_ok,read_ok,time_enabled_ns,
//                       time_running_ns,run_ns_enable,run_ns_disable,t_enable_ns,t_disable_ns, then one
//                       column per event
// t0_ns is the timeline's start: t_enable_ns and t_disable_ns are since it.
void write_thread_counters(const std::string& dir, const std::string& header_lines, int64_t t0_ns,
                           const ThreadCounters& counters);
