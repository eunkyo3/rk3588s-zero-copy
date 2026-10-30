#include "core/slot_pool.h"

SlotPool::SlotPool(int n) : n_(n) {
    for (int i = n - 1; i >= 0; i--)
        free_.push_back(i);
}

int SlotPool::acquire() {
    std::unique_lock<std::mutex> lk(m_);
    cv_.wait(lk, [&] { return !free_.empty() || closed_; });
    if (closed_)
        return -1;
    int slot = free_.back();
    free_.pop_back();
    return slot;
}

void SlotPool::release(int slot) {
    {
        std::lock_guard<std::mutex> lk(m_);
        free_.push_back(slot);
    }
    cv_.notify_one();
}

void SlotPool::close() {
    {
        std::lock_guard<std::mutex> lk(m_);
        closed_ = true;
    }
    cv_.notify_all();
}
