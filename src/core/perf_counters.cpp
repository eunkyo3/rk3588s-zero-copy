#include "core/perf_counters.h"

#include <dirent.h>
#include <linux/perf_event.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <unistd.h>

#include <cctype>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>

#include "core/snapshot.h"
#include "core/util.h"

namespace {

std::string read_text(const std::string& path) {
    std::ifstream f(path);
    std::stringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

int perf_open(perf_event_attr* attr, pid_t pid, int cpu, int group_fd) {
    return static_cast<int>(syscall(SYS_perf_event_open, attr, pid, cpu, group_fd, PERF_FLAG_FD_CLOEXEC));
}

// User and kernel mode both count (M7 §1); the hypervisor's mode is left as the kernel has it.
perf_event_attr make_attr(const PmuEvent& e, bool leader_disabled, uint64_t read_format) {
    perf_event_attr attr;
    std::memset(&attr, 0, sizeof attr);
    attr.size = sizeof attr;
    attr.type = e.type;
    attr.config = e.config;
    attr.disabled = leader_disabled ? 1 : 0;
    attr.read_format = read_format;
    return attr;
}

}  // namespace

bool parse_event_config(const std::string& text, uint64_t* config) {
    const size_t p = text.find("event=");
    if (p == std::string::npos)
        return false;
    const char* begin = text.c_str() + p + 6;
    char* end = nullptr;
    errno = 0;
    const unsigned long long v = std::strtoull(begin, &end, 0);
    if (end == begin || errno != 0)
        return false;
    *config = v;
    return true;
}

PmuEvent pmu_event(const std::string& name) {
    PmuEvent e;
    e.name = name;
    const std::string base = std::string("/sys/bus/event_source/devices/") + kPmuName;
    const std::string type = read_text(base + "/type");
    if (type.empty() || !std::isdigit(static_cast<unsigned char>(type[0])))
        return e;
    e.type = static_cast<uint32_t>(std::strtoul(type.c_str(), nullptr, 10));
    e.found = parse_event_config(read_text(base + "/events/" + name), &e.config);
    return e;
}

ThreadCounters::~ThreadCounters() {
    for (const auto& group : fds_)
        for (int fd : group)
            if (fd >= 0)
                close(fd);
}

void ThreadCounters::open_all() {
    events_.clear();
    for (const char* name : kThreadCounterEvents)
        events_.push_back(pmu_event(name));
    const uint64_t format = PERF_FORMAT_GROUP | PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING;
    std::vector<int> tids;
    if (DIR* d = opendir("/proc/self/task")) {
        while (const dirent* ent = readdir(d))
            if (std::isdigit(static_cast<unsigned char>(ent->d_name[0])))
                tids.push_back(std::atoi(ent->d_name));
        closedir(d);
    }
    for (int tid : tids) {
        ThreadCount t;
        t.tid = tid;
        t.comm = read_text("/proc/self/task/" + std::to_string(tid) + "/comm");
        while (!t.comm.empty() && t.comm.back() == '\n')
            t.comm.pop_back();
        std::vector<int> group;
        t.opened = true;
        for (const PmuEvent& e : events_) {
            int fd = -1, err = ENOENT;  // ENOENT: the event is not in sysfs
            if (e.found) {
                perf_event_attr attr = make_attr(e, group.empty(), format);
                fd = perf_open(&attr, tid, -1, group.empty() ? -1 : group[0]);
                err = errno;
            }
            if (fd < 0) {
                t.opened = false;
                t.open_errno = err;
                t.failed_event = e.name;
                break;
            }
            group.push_back(fd);
        }
        if (!t.opened) {  // no part of a group counts
            for (int fd : group)
                close(fd);
            group.clear();
        }
        threads_.push_back(std::move(t));
        fds_.push_back(std::move(group));
    }
}

void ThreadCounters::enable_all() {
    const int pid = getpid();
    for (size_t i = 0; i < threads_.size(); i++) {
        if (fds_[i].empty())
            continue;
        threads_[i].enable_ok = ioctl(fds_[i][0], PERF_EVENT_IOC_ENABLE, PERF_IOC_FLAG_GROUP) == 0;
        threads_[i].t_enable = now_ns();
        int64_t run = -1;
        if (read_task_schedstat(pid, threads_[i].tid, &run, nullptr, nullptr))
            threads_[i].run_ns_enable = run;
    }
}

void ThreadCounters::disable_all() {
    const int pid = getpid();
    for (size_t i = 0; i < threads_.size(); i++) {
        if (fds_[i].empty())
            continue;
        threads_[i].disable_ok = ioctl(fds_[i][0], PERF_EVENT_IOC_DISABLE, PERF_IOC_FLAG_GROUP) == 0;
        threads_[i].t_disable = now_ns();
        int64_t run = -1;
        if (read_task_schedstat(pid, threads_[i].tid, &run, nullptr, nullptr))
            threads_[i].run_ns_disable = run;
    }
    // { nr, time_enabled, time_running, value[nr] }
    std::vector<uint64_t> buf(3 + events_.size());
    for (size_t i = 0; i < threads_.size(); i++) {
        if (fds_[i].empty())
            continue;
        const ssize_t want = static_cast<ssize_t>(buf.size() * sizeof(uint64_t));
        if (read(fds_[i][0], buf.data(), static_cast<size_t>(want)) != want || buf[0] != events_.size())
            continue;
        threads_[i].read_ok = true;
        threads_[i].time_enabled = buf[1];
        threads_[i].time_running = buf[2];
        threads_[i].values.assign(buf.begin() + 3, buf.end());
    }
}

void CpuCycleCounter::open(int on_cpu) {
    cpu = on_cpu;
    event = pmu_event("cpu_cycles");
    if (!event.found) {
        open_errno = ENOENT;
        return;
    }
    perf_event_attr attr =
        make_attr(event, true, PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING);
    fd = perf_open(&attr, -1, cpu, -1);
    open_errno = fd < 0 ? errno : 0;
}

void CpuCycleCounter::enable() {
    if (fd < 0)
        return;
    enabled = ioctl(fd, PERF_EVENT_IOC_ENABLE, 0) == 0;
    t_enable = now_ns();
}

void CpuCycleCounter::read_now() {
    if (fd < 0)
        return;
    uint64_t buf[3] = {0, 0, 0};  // { value, time_enabled, time_running }
    read_ok = read(fd, buf, sizeof buf) == static_cast<ssize_t>(sizeof buf);
    t_read = now_ns();
    cycles = buf[0];
    time_enabled = buf[1];
    time_running = buf[2];
}

void CpuCycleCounter::close() {
    if (fd >= 0)
        ::close(fd);
    fd = -1;
}

void write_thread_counters(const std::string& dir, const std::string& header_lines, int64_t t0_ns,
                           const ThreadCounters& counters) {
    {
        std::ofstream f(dir + "/counter_events.csv");
        CHECK(f.good(), "cannot write counter_events.csv");
        f << header_lines << "name,found,pmu_type,config\n";
        for (const PmuEvent& e : counters.events()) {
            char config[32];
            std::snprintf(config, sizeof config, "0x%llx", static_cast<unsigned long long>(e.config));
            f << e.name << ',' << e.found << ',' << e.type << ',' << config << '\n';
        }
    }
    std::ofstream f(dir + "/counters.csv");
    CHECK(f.good(), "cannot write counters.csv");
    f << header_lines
      << "tid,comm,opened,open_errno,failed_event,enable_ok,disable_ok,read_ok,time_enabled_ns,time_running_ns,"
         "run_ns_enable,run_ns_disable,t_enable_ns,t_disable_ns";
    for (const PmuEvent& e : counters.events())
        f << ',' << e.name;
    f << '\n';
    for (const ThreadCount& t : counters.threads()) {
        f << t.tid << ',' << csv_field(t.comm) << ',' << t.opened << ',' << t.open_errno << ',' << t.failed_event
          << ',' << t.enable_ok << ',' << t.disable_ok << ',' << t.read_ok << ',' << t.time_enabled << ',' << t.time_running << ',' << t.run_ns_enable << ','
          << t.run_ns_disable << ',' << (t.opened ? t.t_enable - t0_ns : 0) << ','
          << (t.opened ? t.t_disable - t0_ns : 0);
        for (size_t i = 0; i < counters.events().size(); i++)
            f << ',' << (i < t.values.size() ? std::to_string(t.values[i]) : "");
        f << '\n';
    }
}
