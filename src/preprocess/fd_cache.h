// The per-buffer cache of PROTOCOL §8, shared by B (its mmap) and C (its RGA handle): one entry
// per decoder buffer, keyed by dma-buf inode, never by fd number.
// A channel's entries belong to one decoder buffer pool (hw_frames_ctx) and frame size. When
// either changes (decoder info change), the channel's entries are retired: no longer used, but
// released only by clear(), because a frame from the old pool may still be in flight on the
// other preprocessing thread.
#pragma once

#include <sys/stat.h>

#include <algorithm>
#include <functional>
#include <map>
#include <mutex>
#include <unordered_map>
#include <vector>

extern "C" {
#include <libavutil/frame.h>
#include <libavutil/hwcontext_drm.h>
}

#include "core/util.h"

template <typename T>
class FdCache {
public:
    // limit: entries per channel and pool, the decoder's buffer group cap.
    FdCache(size_t limit, std::function<T(int fd, size_t size)> make, std::function<void(T&)> release)
        : limit_(limit), make_(std::move(make)), release_(std::move(release)) {}
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
                retired_.push_back(v);
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

    // Releases every entry, retired ones included. Call after the pipeline has stopped.
    void clear() {
        std::lock_guard<std::mutex> lk(m_);
        for (auto& [ch, p] : pools_)
            for (auto& [ino, v] : p.entries)
                release_(v);
        for (auto& v : retired_)
            release_(v);
        pools_.clear();
        retired_.clear();
    }

    // Largest number of entries one channel's pool reached, and entries retired so far.
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
    std::vector<T> retired_;
    size_t max_entries_ = 0;
};
