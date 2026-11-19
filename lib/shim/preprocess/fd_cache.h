// The library's version of src/preprocess/fd_cache.h, found first on its include path (like
// shim/core/util.h). Same interface and the same lookup: one entry per decoder buffer, keyed by dma-buf
// inode, a channel's entries retired when its buffer pool or frame size changes.
//
// What differs: a channel's entries can be released while the pipeline runs. The benchmark opens every
// input once, so the measured cache releases only in clear(). A detector reconnects; each reconnection
// is a new decoder with a new buffer pool, and entries that are never released keep the old pool's
// buffers alive. forget(channel) releases them. The caller must make sure no frame of that channel is
// in prepare or waiting for it.
//
// The backend keeps its cache private, so the cache reports itself: while rkzc::CacheHooks::collecting
// is set on the constructing thread, each new cache adds its forget() to it (lib/src/engine.cpp).
#pragma once

#include <sys/stat.h>

#include <algorithm>
#include <functional>
#include <map>
#include <mutex>
#include <unordered_map>
#include <utility>
#include <vector>

extern "C" {
#include <libavutil/frame.h>
#include <libavutil/hwcontext_drm.h>
}

#include "core/util.h"

namespace rkzc {
struct CacheHooks {
    std::vector<std::function<void(int channel)>> forget;
    std::vector<std::function<size_t(int channel)>> count;  // entries held for the channel
    static inline thread_local CacheHooks* collecting = nullptr;
};
}  // namespace rkzc

template <typename T>
class FdCache {
public:
    // limit: entries per channel and pool, the decoder's buffer group cap.
    FdCache(size_t limit, std::function<T(int fd, size_t size)> make, std::function<void(T&)> release)
        : limit_(limit), make_(std::move(make)), release_(std::move(release)) {
        if (rkzc::CacheHooks::collecting) {
            rkzc::CacheHooks::collecting->forget.push_back([this](int channel) { forget(channel); });
            rkzc::CacheHooks::collecting->count.push_back([this](int channel) { return held(channel); });
        }
    }
    ~FdCache() { clear(); }

    T get(const AVFrame* frame, int channel) {
        const auto* desc = reinterpret_cast<const AVDRMFrameDescriptor*>(frame->data[0]);
        const int fd = desc->objects[0].fd;
        struct stat st {};
        CHECK(fstat(fd, &st) == 0, "fstat of a decoder buffer failed");
        const void* pool_id = frame->hw_frames_ctx ? frame->hw_frames_ctx->data : nullptr;

        std::lock_guard<std::mutex> lk(m_);
        Pool& p = pools_[channel];
        if (p.id != pool_id || p.w != frame->width || p.h != frame->height) {
            for (auto& [ino, v] : p.entries)
                retired_.emplace_back(channel, v);
            p.entries.clear();
            p.id = pool_id;
            p.w = frame->width;
            p.h = frame->height;
        }
        auto it = p.entries.find(st.st_ino);
        if (it != p.entries.end())
            return it->second;
        T v = make_(fd, desc->objects[0].size);
        p.entries.emplace(st.st_ino, v);
        CHECK(p.entries.size() <= limit_, "channel %d: more than %zu decoder buffers", channel, limit_);
        max_entries_ = std::max(max_entries_, p.entries.size());
        return v;
    }

    // Releases the channel's entries, retired ones included. Only when none of its frames is in use.
    void forget(int channel) {
        std::lock_guard<std::mutex> lk(m_);
        auto it = pools_.find(channel);
        if (it != pools_.end()) {
            for (auto& [ino, v] : it->second.entries)
                release_(v);
            pools_.erase(it);
        }
        auto keep = retired_.begin();
        for (auto& r : retired_) {
            if (r.first == channel)
                release_(r.second);
            else
                *keep++ = r;
        }
        retired_.erase(keep, retired_.end());
    }

    // Entries held for the channel, retired ones included.
    size_t held(int channel) {
        std::lock_guard<std::mutex> lk(m_);
        auto it = pools_.find(channel);
        size_t n = it == pools_.end() ? 0 : it->second.entries.size();
        for (auto& r : retired_)
            n += r.first == channel;
        return n;
    }

    // Releases every entry, retired ones included. Call after the pipeline has stopped.
    void clear() {
        std::lock_guard<std::mutex> lk(m_);
        for (auto& [ch, p] : pools_)
            for (auto& [ino, v] : p.entries)
                release_(v);
        for (auto& r : retired_)
            release_(r.second);
        pools_.clear();
        retired_.clear();
    }

    // Largest number of entries one channel's pool reached, and entries retired and not yet released.
    size_t max_entries() const { return max_entries_; }
    size_t retired() const { return retired_.size(); }

private:
    struct Pool {
        const void* id = nullptr;
        int w = 0, h = 0;
        std::unordered_map<ino_t, T> entries;
    };

    const size_t limit_;
    std::function<T(int, size_t)> make_;
    std::function<void(T&)> release_;
    std::mutex m_;
    std::map<int, Pool> pools_;
    std::vector<std::pair<int, T>> retired_;
    size_t max_entries_ = 0;
};
