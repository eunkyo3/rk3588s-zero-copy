#include "core/snapshot.h"

#include <dirent.h>
#include <fcntl.h>
#include <unistd.h>

#include <cctype>
#include <cstdlib>
#include <cstring>
#include <fstream>

#include "core/util.h"

namespace {

// A whole /proc file, read with plain system calls: a snapshot reads several hundred of them.
bool read_file(const char* path, std::string* out) {
    out->clear();
    const int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return false;
    char buf[8192];
    bool ok = true;
    for (;;) {
        const ssize_t n = read(fd, buf, sizeof buf);
        if (n < 0) {
            ok = false;  // ESRCH: the thread ended between open and read
            break;
        }
        if (n == 0)
            break;
        out->append(buf, static_cast<size_t>(n));
    }
    close(fd);
    return ok;
}

bool is_number(const char* s) {
    if (!*s)
        return false;
    for (; *s; s++)
        if (!std::isdigit(static_cast<unsigned char>(*s)))
            return false;
    return true;
}

// The numeric entries of a /proc directory, in the order the kernel gives them.
std::vector<int> numeric_entries(const char* path) {
    std::vector<int> ids;
    if (DIR* d = opendir(path)) {
        while (const dirent* e = readdir(d))
            if (is_number(e->d_name))
                ids.push_back(std::atoi(e->d_name));
        closedir(d);
    }
    return ids;
}

void strip_newline(std::string* s) {
    while (!s->empty() && (s->back() == '\n' || s->back() == '\r'))
        s->pop_back();
}

// The value of a "Key:\t123" line of a status text, -1 if the key is not there.
int64_t status_value(const std::string& text, const char* key) {
    const size_t klen = std::strlen(key);
    size_t pos = 0;
    while (pos < text.size()) {
        const size_t end = text.find('\n', pos);
        if (text.compare(pos, klen, key) == 0 && pos + klen < text.size() && text[pos + klen] == ':')
            return std::atoll(text.c_str() + pos + klen + 1);
        if (end == std::string::npos)
            break;
        pos = end + 1;
    }
    return -1;
}

}  // namespace

bool read_task_schedstat(int pid, int tid, int64_t* run_ns, int64_t* wait_ns, int64_t* timeslices) {
    char path[64];
    std::snprintf(path, sizeof path, "/proc/%d/task/%d/schedstat", pid, tid);
    std::string text;
    if (!read_file(path, &text))
        return false;
    long long a = 0, b = 0, c = 0;
    if (std::sscanf(text.c_str(), "%lld %lld %lld", &a, &b, &c) != 3)
        return false;
    *run_ns = a;
    if (wait_ns)
        *wait_ns = b;
    if (timeslices)
        *timeslices = c;
    return true;
}

int stat_ppid(const std::string& stat_text) {
    const size_t p = stat_text.rfind(')');
    if (p == std::string::npos)
        return -1;
    char state = 0;
    int ppid = -1;
    if (std::sscanf(stat_text.c_str() + p + 1, " %c %d", &state, &ppid) != 2)
        return -1;
    return ppid;
}

std::string csv_field(const std::string& s) {
    if (s.find_first_of(",\"\n\r") == std::string::npos)
        return s;
    std::string out = "\"";
    for (char c : s) {
        if (c == '"')
            out += '"';
        out += c;
    }
    return out + "\"";
}

SystemSnapshot take_system_snapshot(int64_t deadline_ns, const std::function<void()>& after_schedstat) {
    SystemSnapshot s;
    s.taken = true;
    s.t_begin = now_ns();
    bool files_ok = read_file("/proc/schedstat", &s.schedstat);
    if (after_schedstat)
        after_schedstat();
    s.t_schedstat = now_ns();
    files_ok &= read_file("/proc/stat", &s.stat);
    files_ok &= read_file("/proc/interrupts", &s.interrupts);
    files_ok &= read_file("/proc/softirqs", &s.softirqs);

    const int self = getpid();
    char path[96];
    std::string text;
    auto late = [&] { return deadline_ns != 0 && now_ns() >= deadline_ns; };

    std::snprintf(path, sizeof path, "/proc/%d/task", self);
    for (int tid : numeric_entries(path)) {
        if (late()) {
            s.t_end = now_ns();
            return s;
        }
        OwnTask t;
        t.tid = tid;
        if (!read_task_schedstat(self, tid, &t.run_ns, &t.wait_ns, &t.timeslices)) {
            s.ended_while_read++;
            continue;
        }
        std::snprintf(path, sizeof path, "/proc/%d/task/%d/status", self, tid);
        if (read_file(path, &text)) {
            t.voluntary_cs = status_value(text, "voluntary_ctxt_switches");
            t.involuntary_cs = status_value(text, "nonvoluntary_ctxt_switches");
        }
        std::snprintf(path, sizeof path, "/proc/%d/task/%d/comm", self, tid);
        if (!read_file(path, &t.comm)) {
            s.ended_while_read++;
            continue;
        }
        strip_newline(&t.comm);
        s.own.push_back(std::move(t));
    }

    for (int pid : numeric_entries("/proc")) {
        if (pid == self)
            continue;
        if (late()) {
            s.t_end = now_ns();
            return s;
        }
        std::snprintf(path, sizeof path, "/proc/%d/stat", pid);
        if (!read_file(path, &text))
            continue;  // the process ended: none of its threads is in this snapshot
        const bool kernel = pid == 2 || stat_ppid(text) == 2;
        std::snprintf(path, sizeof path, "/proc/%d/task", pid);
        for (int tid : numeric_entries(path)) {
            OtherTask t;
            t.pid = pid;
            t.tid = tid;
            t.kernel = kernel;
            if (!read_task_schedstat(pid, tid, &t.run_ns, nullptr, nullptr)) {
                s.ended_while_read++;
                continue;
            }
            std::snprintf(path, sizeof path, "/proc/%d/task/%d/comm", pid, tid);
            if (!read_file(path, &t.comm)) {
                s.ended_while_read++;
                continue;
            }
            strip_newline(&t.comm);
            s.others.push_back(std::move(t));
        }
    }
    s.t_end = now_ns();
    s.complete = files_ok;  // a /proc file that could not be read: the values are missing
    return s;
}

void write_snapshots(const std::string& dir, const std::string& header_lines, int64_t t0_ns,
                     const SystemSnapshot& s1, const SystemSnapshot& s2,
                     const std::vector<std::pair<std::string, std::string>>& extra_meta) {
    const SystemSnapshot* snaps[2] = {&s1, &s2};
    {
        std::ofstream f(dir + "/snapshot_meta.csv");
        CHECK(f.good(), "cannot write snapshot_meta.csv");
        f << header_lines << "key,value\n";
        for (int k = 0; k < 2; k++) {
            const SystemSnapshot& s = *snaps[k];
            const std::string p = "snapshot" + std::to_string(k + 1) + "_";
            f << p << "taken," << s.taken << '\n' << p << "complete," << s.complete << '\n';
            if (!s.taken)
                continue;
            f << p << "begin_ns," << s.t_begin - t0_ns << '\n'
              << p << "schedstat_read_ns," << s.t_schedstat - t0_ns << '\n'
              << p << "end_ns," << s.t_end - t0_ns << '\n'
              << p << "own_threads," << s.own.size() << '\n'
              << p << "other_threads," << s.others.size() << '\n'
              << p << "ended_while_read," << s.ended_while_read << '\n';
        }
        for (const auto& [k, v] : extra_meta)
            f << k << ',' << v << '\n';
    }
    {
        std::ofstream f(dir + "/snapshot_own.csv");
        CHECK(f.good(), "cannot write snapshot_own.csv");
        f << header_lines << "snapshot,tid,comm,run_ns,wait_ns,timeslices,voluntary_cs,involuntary_cs\n";
        for (int k = 0; k < 2; k++)
            for (const auto& t : snaps[k]->own)
                f << k + 1 << ',' << t.tid << ',' << csv_field(t.comm) << ',' << t.run_ns << ',' << t.wait_ns << ','
                  << t.timeslices << ',' << t.voluntary_cs << ',' << t.involuntary_cs << '\n';
    }
    {
        std::ofstream f(dir + "/snapshot_tasks.csv");
        CHECK(f.good(), "cannot write snapshot_tasks.csv");
        f << header_lines << "snapshot,pid,tid,kernel,comm,run_ns\n";
        for (int k = 0; k < 2; k++)
            for (const auto& t : snaps[k]->others)
                f << k + 1 << ',' << t.pid << ',' << t.tid << ',' << t.kernel << ',' << csv_field(t.comm) << ','
                  << t.run_ns << '\n';
    }
    for (int k = 0; k < 2; k++) {
        if (!snaps[k]->taken)
            continue;
        const std::string p = dir + "/snapshot" + std::to_string(k + 1) + "_";
        const std::pair<const char*, const std::string*> files[4] = {{"schedstat.txt", &snaps[k]->schedstat},
                                                                     {"stat.txt", &snaps[k]->stat},
                                                                     {"interrupts.txt", &snaps[k]->interrupts},
                                                                     {"softirqs.txt", &snaps[k]->softirqs}};
        for (const auto& [name, text] : files) {
            std::ofstream f(p + name, std::ios::binary);
            CHECK(f.good(), "cannot write %s%s", p.c_str(), name);
            f << *text;
        }
    }
}
