#include "core/metrics.h"

#include <sched.h>
#include <unistd.h>

#include <cctype>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <sstream>

#include "core/util.h"

namespace fs = std::filesystem;

namespace {

const double kTick = 1.0 / sysconf(_SC_CLK_TCK);

std::string read_text(const std::string& path) {
    std::ifstream f(path);
    std::stringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

int64_t read_int(const std::string& path, int64_t fallback = -1) {
    std::ifstream f(path);
    int64_t v;
    return (f >> v) ? v : fallback;
}

// utime + stime (fields 14 and 15) of a /proc stat file, in seconds.
double stat_cpu_s(const std::string& path) {
    std::string s = read_text(path);
    size_t p = s.rfind(')');  // the command name may contain spaces
    if (p == std::string::npos)
        return 0;
    std::istringstream ss(s.substr(p + 2));
    std::string field;
    unsigned long long utime = 0, stime = 0;
    for (int i = 3; i <= 15 && ss >> field; i++) {
        if (i == 14)
            utime = std::stoull(field);
        if (i == 15)
            stime = std::stoull(field);
    }
    return (utime + stime) * kTick;
}

}  // namespace

std::string affinity_of(int tid) {
    cpu_set_t set;
    CPU_ZERO(&set);
    if (sched_getaffinity(tid, sizeof set, &set) != 0)
        return "?";
    std::string out;
    for (int c = 0; c < CPU_SETSIZE; c++) {
        if (!CPU_ISSET(c, &set))
            continue;
        int e = c;
        while (e + 1 < CPU_SETSIZE && CPU_ISSET(e + 1, &set))
            e++;
        out += (out.empty() ? "" : ",") + std::to_string(c) + (e > c ? "-" + std::to_string(e) : "");
        c = e;
    }
    return out;
}

void pin_current_thread(const std::vector<int>& cpus) {
    cpu_set_t set;
    CPU_ZERO(&set);
    for (int c : cpus)
        CPU_SET(c, &set);
    CHECK(sched_setaffinity(0, sizeof set, &set) == 0, "sched_setaffinity failed");
}

CpuSnapshot take_cpu_snapshot() {
    CpuSnapshot s;
    s.process_s = stat_cpu_s("/proc/self/stat");
    for (const auto& e : fs::directory_iterator("/proc/self/task")) {
        const int tid = std::stoi(e.path().filename().string());
        ThreadCpu t;
        t.cpu_s = stat_cpu_s(e.path().string() + "/stat");
        t.comm = read_text(e.path().string() + "/comm");
        if (!t.comm.empty() && t.comm.back() == '\n')
            t.comm.pop_back();
        t.affinity = affinity_of(tid);
        s.threads[tid] = t;
    }
    std::istringstream stat(read_text("/proc/stat"));
    std::string line;
    while (std::getline(stat, line)) {
        if (line.rfind("cpu", 0) != 0 || line.size() < 4 || !std::isdigit(static_cast<unsigned char>(line[3])))
            continue;
        std::istringstream ls(line);
        std::string name;
        unsigned long long user, nice, system, idle, iowait, irq, softirq, steal;
        ls >> name >> user >> nice >> system >> idle >> iowait >> irq >> softirq >> steal;
        const size_t cpu = std::stoul(name.substr(3));  // offline CPUs have no line
        if (s.sys_nonidle_s.size() <= cpu)
            s.sys_nonidle_s.resize(cpu + 1, 0.0);
        s.sys_nonidle_s[cpu] = (user + nice + system + irq + softirq + steal) * kTick;
    }
    return s;
}

std::vector<std::string> thermal_zone_names() {
    std::vector<std::string> names;
    for (int z = 0; fs::exists("/sys/class/thermal/thermal_zone" + std::to_string(z)); z++) {
        std::string t = read_text("/sys/class/thermal/thermal_zone" + std::to_string(z) + "/type");
        if (!t.empty() && t.back() == '\n')
            t.pop_back();
        names.push_back(t);
    }
    return names;
}

Sample take_sample(const std::vector<int64_t>& decoded) {
    Sample s;
    s.t_ns = now_ns();
    // "NPU load:  Core0:  3%, Core1:  0%, Core2:  0%,"
    std::string load = read_text("/sys/kernel/debug/rknpu/load");
    for (int c = 0; c < 3; c++) {
        size_t p = load.find("Core" + std::to_string(c) + ":");
        if (p != std::string::npos)
            s.npu_load[c] = std::atoi(load.c_str() + p + 6);
    }
    for (int z = 0; fs::exists("/sys/class/thermal/thermal_zone" + std::to_string(z)); z++)
        s.temp_mc.push_back(static_cast<int>(read_int("/sys/class/thermal/thermal_zone" + std::to_string(z) + "/temp")));
    s.freq[0] = read_int("/sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq");
    s.freq[1] = read_int("/sys/devices/system/cpu/cpufreq/policy4/scaling_cur_freq");
    s.freq[2] = read_int("/sys/devices/system/cpu/cpufreq/policy6/scaling_cur_freq");
    s.freq[3] = read_int("/sys/class/devfreq/dmc/cur_freq");
    s.freq[4] = read_int("/sys/class/devfreq/fdab0000.npu/cur_freq");
    std::istringstream status(read_text("/proc/self/status"));
    std::string line;
    while (std::getline(status, line))
        if (line.rfind("VmRSS:", 0) == 0)
            s.rss_kb = std::atoll(line.c_str() + 6);
    s.decoded = decoded;
    return s;
}
