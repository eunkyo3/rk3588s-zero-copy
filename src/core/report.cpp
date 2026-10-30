#include "core/report.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <limits>

#include "core/util.h"

namespace {

std::ofstream open_csv(const std::string& path, const Header& header) {
    std::ofstream f(path);
    CHECK(f.good(), "cannot write %s", path.c_str());
    for (const auto& [k, v] : header)
        f << "# " << k << ": " << v << "\n";
    return f;
}

std::string fmt(double v, int digits = 3) {
    char b[64];
    std::snprintf(b, sizeof b, "%.*f", digits, v);
    return b;
}

}  // namespace

std::string json_string(const std::string& s) {
    std::string out = "\"";
    for (char c : s) {
        if (c == '"' || c == '\\')
            out += '\\';
        if (static_cast<unsigned char>(c) < 0x20)
            continue;
        out += c;
    }
    return out + "\"";
}

double percentile(std::vector<double> v, double q) {
    std::sort(v.begin(), v.end());
    const double pos = q / 100.0 * double(v.size() - 1);
    const size_t lo = static_cast<size_t>(std::floor(pos));
    const size_t hi = std::min(lo + 1, v.size() - 1);
    return v[lo] + (v[hi] - v[lo]) * (pos - double(lo));
}

Header write_results(const std::string& dir, const Header& header, const PipelineConfig& cfg,
                     const RunResult& r, const std::string& env_json_body) {
    const int n = static_cast<int>(cfg.sources.size());
    const double window_s = cfg.duration_s;
    const size_t w_idx = cfg.warmup_s - 1, e_idx = cfg.warmup_s + cfg.duration_s - 1;
    auto in_window = [&](int64_t t) { return t >= r.t_w && t < r.t_e; };

    {
        auto f = open_csv(dir + "/frames.csv", header);
        f << "channel,seq,pts,t0_ns,t1_ns,t2a_ns,t2b_ns,t2c_ns,t3_ns,t4_ns,slot_wait_ns,ctx,slot,detections\n";
        for (const auto& x : r.frames)
            f << x.channel + 1 << ',' << x.seq << ',' << x.pts << ',' << x.t0 - r.t_start << ',' << x.t1 - r.t_start
              << ',' << x.t2a - r.t_start << ',' << x.t2b - r.t_start << ',' << x.t2c - r.t_start << ','
              << x.t3 - r.t_start << ',' << x.t4 - r.t_start << ',' << x.slot_wait_ns << ',' << x.ctx << ','
              << x.slot << ',' << x.detections << '\n';
    }
    {
        auto f = open_csv(dir + "/samples.csv", header);
        f << "t_s,npu_load0,npu_load1,npu_load2";
        for (const auto& z : thermal_zone_names())
            f << ",temp_mc_" << z;
        f << ",freq_policy0_khz,freq_policy4_khz,freq_policy6_khz,freq_dmc_hz,freq_npu_hz,rss_kb";
        for (int i = 0; i < n; i++)
            f << ",decoded_ch" << i + 1;
        f << '\n';
        for (const auto& s : r.samples) {
            f << fmt((s.t_ns - r.t_start) / 1e9) << ',' << s.npu_load[0] << ',' << s.npu_load[1] << ','
              << s.npu_load[2];
            for (int t : s.temp_mc)
                f << ',' << t;
            for (int64_t q : s.freq)
                f << ',' << q;
            f << ',' << s.rss_kb;
            for (int64_t d : s.decoded)
                f << ',' << d;
            f << '\n';
        }
    }

    // ---- per channel ----
    std::vector<int64_t> processed(n, 0);
    std::vector<double> latency, hold, wait;
    for (const auto& x : r.frames) {
        if (!in_window(x.t4))
            continue;
        processed[x.channel]++;
        latency.push_back((x.t4 - x.t0) / 1e6);
        hold.push_back((x.t2a - x.t0) / 1e6);
        wait.push_back(x.slot_wait_ns / 1e6);
    }
    int64_t sampled = 0, q1_drops = 0, decoder_drops = 0;
    double fps_min = std::numeric_limits<double>::max(), fps_max = 0, drift_max = -1e9;
    bool ended_early = false;
    {
        auto f = open_csv(dir + "/channels.csv", header);
        f << "channel,decoded_in_window,decode_fps,drift_max_s,decoder_drops,sampled_in_window,"
             "q1_drops_in_window,processed_in_window,inference_fps,ended_early\n";
        for (int i = 0; i < n; i++) {
            const auto& c = r.channels[i];
            const double fps = c.decoded_in_window / window_s;
            f << i + 1 << ',' << c.decoded_in_window << ',' << fmt(fps) << ',' << fmt(c.drift_max_s) << ','
              << c.decoder_drops << ',' << c.sampled_in_window << ',' << c.q1_drops_in_window << ','
              << processed[i] << ',' << fmt(processed[i] / window_s) << ',' << c.ended_early << '\n';
            sampled += c.sampled_in_window;
            q1_drops += c.q1_drops_in_window;
            decoder_drops += c.decoder_drops;
            fps_min = std::min(fps_min, fps);
            fps_max = std::max(fps_max, fps);
            drift_max = std::max(drift_max, c.drift_max_s);
            ended_early |= c.ended_early;
        }
    }

    // ---- summary ----
    Header s;
    s.push_back({"window_s", fmt(window_s, 0)});
    const double proc = r.cpu_e.process_s - r.cpu_w.process_s;
    double a76 = 0, a55 = 0;
    std::vector<std::pair<double, std::string>> per_thread;
    for (const auto& [tid, t] : r.cpu_e.threads) {
        auto w = r.cpu_w.threads.find(tid);
        const double d = t.cpu_s - (w != r.cpu_w.threads.end() ? w->second.cpu_s : 0.0);
        if (t.affinity == "4-7")
            a76 += d;
        else if (t.affinity == "0")
            a55 += d;
        per_thread.push_back({d, std::to_string(tid) + ',' + t.comm + ",\"" + t.affinity + "\"," + fmt(d)});
    }
    {
        std::sort(per_thread.rbegin(), per_thread.rend());
        auto f = open_csv(dir + "/threads.csv", header);
        f << "tid,comm,affinity,cpu_s_in_window\n";
        for (const auto& [d, row] : per_thread)
            f << row << '\n';
    }
    s.push_back({"process_cpu_s", fmt(proc)});
    s.push_back({"process_cpu_a76_s", fmt(a76)});
    s.push_back({"process_cpu_a55_s", fmt(a55)});
    s.push_back({"process_cpu_unattributed_s", fmt(proc - a76 - a55)});
    double sys_total = 0, cl[3] = {0, 0, 0};
    for (size_t c = 0; c < r.cpu_e.sys_nonidle_s.size() && c < r.cpu_w.sys_nonidle_s.size(); c++) {
        const double d = r.cpu_e.sys_nonidle_s[c] - r.cpu_w.sys_nonidle_s[c];
        s.push_back({"system_cpu" + std::to_string(c) + "_s", fmt(d)});
        sys_total += d;
        cl[c < 4 ? 0 : c < 6 ? 1 : 2] += d;
    }
    s.push_back({"system_cpu0-3_s", fmt(cl[0])});
    s.push_back({"system_cpu4-5_s", fmt(cl[1])});
    s.push_back({"system_cpu6-7_s", fmt(cl[2])});
    s.push_back({"system_cpu_total_s", fmt(sys_total)});

    s.push_back({"decode_fps_min", fmt(fps_min)});
    s.push_back({"decode_fps_max", fmt(fps_max)});
    s.push_back({"drift_max_s", fmt(drift_max)});
    s.push_back({"decoder_drops", std::to_string(decoder_drops)});
    s.push_back({"input_ended_early", ended_early ? "1" : "0"});
    s.push_back({"sampled_in_window", std::to_string(sampled)});
    s.push_back({"q1_drops_in_window", std::to_string(q1_drops)});
    s.push_back({"drop_rate", sampled ? fmt(double(q1_drops) / sampled, 5) : "nan"});
    s.push_back({"processed_in_window", std::to_string(latency.size())});
    if (!latency.empty()) {
        s.push_back({"latency_p50_ms", fmt(percentile(latency, 50))});
        s.push_back({"latency_p99_ms", fmt(percentile(latency, 99))});
        s.push_back({"mpp_hold_p50_ms", fmt(percentile(hold, 50))});
        s.push_back({"mpp_hold_p99_ms", fmt(percentile(hold, 99))});
        s.push_back({"slot_wait_p99_ms", fmt(percentile(wait, 99))});
    }

    const int64_t rss_w = r.samples[w_idx].rss_kb, rss_e = r.samples[e_idx].rss_kb;
    int64_t rss_max = 0;
    int64_t fmin[5], fmax[5];
    std::fill(fmin, fmin + 5, std::numeric_limits<int64_t>::max());
    std::fill(fmax, fmax + 5, 0);
    for (size_t j = w_idx; j <= e_idx; j++) {
        rss_max = std::max(rss_max, r.samples[j].rss_kb);
        for (int q = 0; q < 5; q++) {
            fmin[q] = std::min(fmin[q], r.samples[j].freq[q]);
            fmax[q] = std::max(fmax[q], r.samples[j].freq[q]);
        }
    }
    s.push_back({"rss_window_start_kb", std::to_string(rss_w)});
    s.push_back({"rss_window_end_kb", std::to_string(rss_e)});
    s.push_back({"rss_window_max_kb", std::to_string(rss_max)});
    s.push_back({"rss_growth_pct", fmt(100.0 * double(rss_e - rss_w) / double(rss_w), 2)});
    const char* domains[5] = {"policy0_khz", "policy4_khz", "policy6_khz", "dmc_hz", "npu_hz"};
    for (int q = 0; q < 5; q++) {
        s.push_back({std::string("freq_min_") + domains[q], std::to_string(fmin[q])});
        s.push_back({std::string("freq_max_") + domains[q], std::to_string(fmax[q])});
    }
    int temp_max = 0;
    for (size_t j = w_idx; j <= e_idx; j++)
        for (int t : r.samples[j].temp_mc)
            temp_max = std::max(temp_max, t);
    s.push_back({"temp_max_mc", std::to_string(temp_max)});

    {
        auto f = open_csv(dir + "/summary.csv", header);
        f << "key,value\n";
        for (const auto& [k, v] : s)
            f << k << ',' << v << '\n';
    }
    {
        std::ofstream f(dir + "/env.json");
        CHECK(f.good(), "cannot write env.json");
        f << "{\n";
        for (const auto& [k, v] : header)
            f << "  " << json_string("header_" + k) << ": " << json_string(v) << ",\n";
        f << env_json_body << "\n}\n";
    }
    return s;
}
