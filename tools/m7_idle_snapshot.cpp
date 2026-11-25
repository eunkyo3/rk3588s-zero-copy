// The idle pair of snapshots of prereg/M7.md §4: what the idle board does in a span like a run's. Two
// snapshots, taken by the function --snapshot on uses (core/snapshot.h), a span apart, from a thread on
// cpu0 as bench_main's metrics thread is. No counter is opened.
//
// Usage: m7_idle_snapshot OUT_DIR [--span S] [--run-kind KIND] [--freeze-m7-commit SHA]
//   --span S   seconds from the first snapshot's start to the second's (default 121: the window and the
//              second before it, M7 §1)
//   OUT_DIR gets snapshot_meta.csv, snapshot_own.csv, snapshot_tasks.csv and the snapshot<k>_*.txt files,
//   with "# key: value" header lines (PROTOCOL §1.4). snapshot_own.csv holds this tool's own threads.
#include <filesystem>
#include <map>
#include <string>

#include "core/metrics.h"
#include "core/report.h"
#include "core/snapshot.h"
#include "core/util.h"

#ifndef CODE_COMMIT
#define CODE_COMMIT "unknown"
#endif
#ifndef LATEST_AMENDMENT
#define LATEST_AMENDMENT "none"
#endif

int main(int argc, char** argv) {
    if (argc < 2 || std::string(argv[1]).rfind("--", 0) == 0)
        die("usage: m7_idle_snapshot OUT_DIR [--span S] [--run-kind KIND] [--freeze-m7-commit SHA]");
    const std::string out = argv[1];
    std::map<std::string, std::string> a = {{"span", "121"}, {"run-kind", "check"}, {"freeze-m7-commit", "none"}};
    for (int i = 2; i < argc; i++) {
        std::string k = argv[i];
        CHECK(k.rfind("--", 0) == 0 && i + 1 < argc && a.count(k.substr(2)), "unknown option %s", k.c_str());
        a[k.substr(2)] = argv[++i];
    }
    const int span_s = std::stoi(a["span"]);
    CHECK(span_s >= 1 && span_s <= 3600, "span must be 1..3600 s");
    CHECK(a["run-kind"] == "check" || a["run-kind"] == "m7", "run kind must be check or m7");
    CHECK(a["freeze-m7-commit"] == "none" || is_commit_sha(a["freeze-m7-commit"]),
          "freeze-m7-commit must be none or a full commit SHA");
    CHECK(a["run-kind"] != "m7" || a["freeze-m7-commit"] != "none", "run kind m7 needs freeze-m7's commit (M7 §8)");
    std::filesystem::create_directories(out);

    const std::string header = "# start_time_utc: " + utc_now("%Y-%m-%dT%H:%M:%SZ") +
                               "\n# ntp_synchronized: " + command_output("timedatectl show -p NTPSynchronized --value") +
                               "\n# prereg_m7_commit: " + kPreregM7Commit + "\n# freeze_m7_commit: " +
                               a["freeze-m7-commit"] + "\n# run_kind: " + a["run-kind"] +
                               "\n# code_commit: " CODE_COMMIT "\n# amendment: " LATEST_AMENDMENT
                               "\n# tool: m7_idle_snapshot\n# span_s: " + std::to_string(span_s) + "\n";

    pin_current_thread({0});
    const int64_t t0 = now_ns();
    const SystemSnapshot s1 = take_system_snapshot(0, nullptr);
    timespec until{static_cast<time_t>((t0 + span_s * 1000000000LL) / 1000000000),
                   static_cast<long>((t0 + span_s * 1000000000LL) % 1000000000)};
    while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &until, nullptr) != 0) {
    }
    const SystemSnapshot s2 = take_system_snapshot(0, nullptr);
    write_snapshots(out, header, t0, s1, s2, {{"span_s", std::to_string(span_s)}});
    CHECK(s1.complete && s2.complete, "a snapshot is incomplete");
    std::printf("idle snapshots: %zu and %zu other threads, %.3f s apart -> %s\n", s1.others.size(),
                s2.others.size(), (s2.t_begin - s1.t_begin) / 1e9, out.c_str());
    return 0;
}
