// The snapshots of --snapshot on (prereg/M7.md §1): what the board's other tasks, its interrupts and
// bench_main's own threads have used so far, read at two moments outside the measurement window. The
// difference of the two is what ran in the span between them. tools/m7_idle_snapshot.cpp takes the idle
// pair of M7 §4 with the same function.
//
// One snapshot reads, in this order:
//   /proc/schedstat, then the caller's hook (cpu0's cycle counter is enabled or read there, M7 §3),
//   /proc/stat, /proc/interrupts, /proc/softirqs, whole;
//   every thread of this process: run time, time waiting to run and timeslices from its schedstat, its
//   voluntary and involuntary context switches from its status;
//   every thread of every other process: its ids, its name, whether it is a kernel thread (its process is
//   pid 2 or has pid 2 as its parent) and its run time from its schedstat.
// A thread that ends while it is read is left out: it is then a thread of one snapshot only (M7 §1).
#pragma once

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

struct OwnTask {
    int tid = 0;
    std::string comm;
    int64_t run_ns = 0, wait_ns = 0, timeslices = 0;
    int64_t voluntary_cs = -1, involuntary_cs = -1;  // -1 if status could not be read
};

struct OtherTask {
    int pid = 0, tid = 0;
    bool kernel = false;
    std::string comm;
    int64_t run_ns = 0;
};

struct SystemSnapshot {
    bool taken = false;     // false: the option is off, or the moment never came
    bool complete = false;  // false: it stopped at its deadline, and counts as missing (M7 §1, §4)
    int64_t t_begin = 0, t_schedstat = 0, t_end = 0;  // CLOCK_MONOTONIC ns; t_schedstat: after the hook
    std::string schedstat, stat, interrupts, softirqs;
    std::vector<OwnTask> own;
    std::vector<OtherTask> others;
    int ended_while_read = 0;  // threads left out because they ended while they were read
};

// deadline_ns: CLOCK_MONOTONIC time at which the reading stops, 0 for none. after_schedstat may be empty.
SystemSnapshot take_system_snapshot(int64_t deadline_ns, const std::function<void()>& after_schedstat);

// The three numbers of /proc/<pid>/task/<tid>/schedstat: run time and time waiting to run in ns, and the
// number of timeslices. False if the file cannot be read (the thread ended).
bool read_task_schedstat(int pid, int tid, int64_t* run_ns, int64_t* wait_ns, int64_t* timeslices);

// The ppid field of a /proc/<pid>/stat text, -1 if it has none. The command name may hold spaces and
// parentheses.
int stat_ppid(const std::string& stat_text);

// A field as CSV: quoted, with its quotes doubled, if it holds a comma, a quote or a line break.
std::string csv_field(const std::string& s);

// The snapshots' files in dir (M7 §1: files of their own). t0_ns is the timeline's start: the times in
// snapshot_meta.csv are since it. header_lines: "# key: value" lines for the CSV files, ending in "\n".
//   snapshot_meta.csv    key,value: per snapshot taken, complete, its three times, its counts
//   snapshot_own.csv     snapshot,tid,comm,run_ns,wait_ns,timeslices,voluntary_cs,involuntary_cs
//   snapshot_tasks.csv   snapshot,pid,tid,kernel,comm,run_ns
//   snapshot<k>_schedstat.txt, _stat.txt, _interrupts.txt, _softirqs.txt   the files as read, k = 1, 2
// extra_meta: more key,value rows for snapshot_meta.csv (cpu0's counter, the wake-up times of the two ticks).
void write_snapshots(const std::string& dir, const std::string& header_lines, int64_t t0_ns,
                     const SystemSnapshot& s1, const SystemSnapshot& s2,
                     const std::vector<std::pair<std::string, std::string>>& extra_meta);
