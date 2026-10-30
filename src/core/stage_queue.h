// Bounded FIFO between pipeline stages (PROTOCOL §5.1). Q1 drops its oldest item when full;
// Q2 makes the producer wait instead.
#pragma once

#include <condition_variable>
#include <cstddef>
#include <deque>
#include <mutex>
#include <optional>

template <typename T>
class StageQueue {
public:
    explicit StageQueue(size_t capacity) : cap_(capacity) {}

    // Adds the item; when the queue is full, removes and returns the oldest one.
    std::optional<T> push_drop_oldest(T item) {
        std::optional<T> dropped;
        {
            std::lock_guard<std::mutex> lk(m_);
            if (q_.size() >= cap_) {
                dropped = std::move(q_.front());
                q_.pop_front();
            }
            q_.push_back(std::move(item));
        }
        not_empty_.notify_one();
        return dropped;
    }

    // Waits for room. Returns false, without adding, once the queue is closed.
    bool push(T item) {
        {
            std::unique_lock<std::mutex> lk(m_);
            not_full_.wait(lk, [&] { return q_.size() < cap_ || closed_; });
            if (closed_)
                return false;
            q_.push_back(std::move(item));
        }
        not_empty_.notify_one();
        return true;
    }

    // Waits for an item. Returns nothing once the queue is closed and empty.
    std::optional<T> pop() {
        std::optional<T> item;
        {
            std::unique_lock<std::mutex> lk(m_);
            not_empty_.wait(lk, [&] { return !q_.empty() || closed_; });
            if (q_.empty())
                return item;
            item = std::move(q_.front());
            q_.pop_front();
        }
        not_full_.notify_one();
        return item;
    }

    void close() {
        {
            std::lock_guard<std::mutex> lk(m_);
            closed_ = true;
        }
        not_empty_.notify_all();
        not_full_.notify_all();
    }

    // Removes and returns everything left, so the caller can release it.
    std::deque<T> drain() {
        std::lock_guard<std::mutex> lk(m_);
        std::deque<T> rest;
        rest.swap(q_);
        return rest;
    }

    size_t capacity() const { return cap_; }

private:
    const size_t cap_;
    std::mutex m_;
    std::condition_variable not_empty_, not_full_;
    std::deque<T> q_;
    bool closed_ = false;
};
