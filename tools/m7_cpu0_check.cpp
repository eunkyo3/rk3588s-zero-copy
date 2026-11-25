// The check of cpu0's cycle counter (prereg/M7.md §8 step 3), with nothing of the pipeline running: the
// counter --snapshot on opens on cpu0 (core/perf_counters.h), over 10 s of idle, and over 10 s in which a
// loop runs on cpu0 for 2.0 s of its thread's CPU time. It passes if
//   - the idle count is below 5% of 10 s x 1800 MHz, and
//   - the difference of the two counts is within 5% of 2.0 s x 1800 MHz.
// cpu0's fixed clock is 1800 MHz (AMENDMENTS A7); the tool reads the clock and stops if it is another.
// Next to the counts, not judged: cpu0's run time from /proc/schedstat over each span.
//
// Usage: m7_cpu0_check [out_dir]   (default results/check/<UTC>_m7_cpu0_check)   Run as root.
//   <out_dir>/check.csv  "# key: value" header lines (PROTOCOL §1.4, run kind check), then key,value
// Exit code 0 when it passed.
#include <atomic>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include "core/metrics.h"
#include "core/perf_counters.h"
#include "core/report.h"
#include "core/util.h"

#ifndef CODE_COMMIT
#define CODE_COMMIT "unknown"
#endif
#ifndef LATEST_AMENDMENT
#define LATEST_AMENDMENT "none"
#endif

namespace {

constexpr double kSpanS = 10.0, kLoopS = 2.0, kClockHz = 1800e6, kTolerance = 0.05;

std::string read_text(const std::string& path) {
    std::ifstream f(path);
    std::stringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

double cpu0_sched_s() {
    int version = -1;
    const std::vector<double> run = parse_schedstat(read_text("/proc/schedstat"), &version);
    return run.empty() ? -1.0 : run[0];
}

int64_t thread_cpu_ns() {
    timespec ts{};
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts);
    return int64_t(ts.tv_sec) * 1000000000 + ts.tv_nsec;
}

void sleep_until(int64_t t) {
    timespec ts{static_cast<time_t>(t / 1000000000), static_cast<long>(t % 1000000000)};
    while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, nullptr) != 0) {
    }
}

struct Span {
    bool ok = false;
    uint64_t cycles = 0, time_enabled = 0, time_running = 0;
    double sched_s = 0, wall_s = 0, loop_cpu_s = 0;
};

// One span of kSpanS with the counter enabled; with loop, a thread on cpu0 runs for kLoopS of its CPU time.
Span measure(bool loop) {
    Span s;
    CpuCycleCounter c;
    c.open(0);
    CHECK(c.open_errno == 0, "cpu0's counter cannot be opened: errno %d (run as root)", c.open_errno);
    const double sched0 = cpu0_sched_s();
    c.enable();
    const int64_t t0 = c.t_enable;
    std::atomic<int64_t> loop_ns{0};
    std::atomic<uint64_t> sink{0};
    std::thread worker;
    if (loop)
        worker = std::thread([&] {
            pin_current_thread({0});
            const int64_t begin = thread_cpu_ns();
            uint64_t x = 1;
            int64_t used = 0;
            while ((used = thread_cpu_ns() - begin) < static_cast<int64_t>(kLoopS * 1e9))
                for (int i = 0; i < 1000; i++)
                    x = x * 6364136223846793005ull + 1442695040888963407ull;
            sink = x;  // the loop's result is used, so the loop stays
            loop_ns = used;
        });
    if (loop)
        worker.join();
    sleep_until(t0 + static_cast<int64_t>(kSpanS * 1e9));
    const double sched1 = cpu0_sched_s();
    c.read_now();
    c.close();
    s.ok = c.enabled && c.read_ok;
    s.cycles = c.cycles;
    s.time_enabled = c.time_enabled;
    s.time_running = c.time_running;
    s.sched_s = sched1 - sched0;
    s.wall_s = (c.t_read - t0) / 1e9;
    s.loop_cpu_s = loop_ns / 1e9;
    return s;
}

}  // namespace

int main(int argc, char** argv) {
    const std::string out = argc > 1 ? argv[1] : "results/check/" + utc_now("%Y%m%dT%H%M%SZ") + "_m7_cpu0_check";
    const std::string clock = command_output("cat /sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq");
    CHECK(clock == "1800000", "cpu0's clock is %s kHz, not 1800000 (A7): set the fixed clocks first", clock.c_str());
    pin_current_thread({4});  // the tool itself waits on another core
    std::filesystem::create_directories(out);
    std::ofstream f(out + "/check.csv");
    CHECK(f.good(), "cannot write %s/check.csv", out.c_str());
    f << "# start_time_utc: " << utc_now("%Y-%m-%dT%H:%M:%SZ") << "\n# ntp_synchronized: "
      << command_output("timedatectl show -p NTPSynchronized --value") << "\n# prereg_m7_commit: " << kPreregM7Commit
      << "\n# freeze_m7_commit: none\n# run_kind: check\n# code_commit: " CODE_COMMIT
         "\n# amendment: " LATEST_AMENDMENT "\n# tool: m7_cpu0_check\n";

    const Span idle = measure(false);
    const Span loop = measure(true);
    const double idle_limit = kTolerance * kSpanS * kClockHz, want = kLoopS * kClockHz;
    const double diff = double(loop.cycles) - double(idle.cycles);
    const bool counted = idle.ok && loop.ok;  // without a count there is nothing to judge
    const bool idle_ok = counted && double(idle.cycles) < idle_limit;
    const bool diff_ok = counted && std::fabs(diff - want) <= kTolerance * want;
    const bool passed = idle_ok && diff_ok;

    f << "key,value\n";
    f << "span_s," << kSpanS << "\nloop_s," << kLoopS << "\nclock_hz," << static_cast<long long>(kClockHz) << '\n';
    const std::pair<const char*, const Span*> spans[2] = {{"idle", &idle}, {"loop", &loop}};
    for (const auto& [name, s] : spans) {
        f << name << "_read_ok," << s->ok << '\n'
          << name << "_cycles," << s->cycles << '\n'
          << name << "_time_enabled_ns," << s->time_enabled << '\n'
          << name << "_time_running_ns," << s->time_running << '\n'
          << name << "_cycles_as_s," << s->cycles / kClockHz << '\n'
          << name << "_cpu0_schedstat_s," << s->sched_s << '\n'
          << name << "_wall_s," << s->wall_s << '\n';
    }
    f << "loop_thread_cpu_s," << loop.loop_cpu_s << '\n';
    f << "idle_limit_cycles," << static_cast<long long>(idle_limit) << '\n';
    f << "difference_cycles," << static_cast<long long>(diff) << '\n';
    f << "difference_as_s," << diff / kClockHz << '\n';
    f << "difference_wanted_cycles," << static_cast<long long>(want) << '\n';
    f << "idle_below_limit," << idle_ok << "\ndifference_within_5pct," << diff_ok << "\npassed," << passed << '\n';
    f.close();

    std::printf("idle: %llu cycles (%.3f s at 1800 MHz; limit %.3f s), cpu0 schedstat %.3f s\n",
                static_cast<unsigned long long>(idle.cycles), idle.cycles / kClockHz, idle_limit / kClockHz,
                idle.sched_s);
    std::printf("loop: %llu cycles (%.3f s), cpu0 schedstat %.3f s, the loop's thread %.3f s\n",
                static_cast<unsigned long long>(loop.cycles), loop.cycles / kClockHz, loop.sched_s, loop.loop_cpu_s);
    std::printf("difference: %.3f s, wanted %.1f s within 5%%\n%s -> %s\n", diff / kClockHz, kLoopS,
                passed ? "passed" : "FAILED", out.c_str());
    return passed ? 0 : 1;
}
