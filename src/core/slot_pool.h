// The N input slots shared by the preprocessing pool and the NPU workers (PROTOCOL §5.1).
// A slot is taken in acquire_slot before prepare and returned after post-processing.
#pragma once

#include <condition_variable>
#include <mutex>
#include <vector>

class SlotPool {
public:
    explicit SlotPool(int n);

    // Waits for a free slot. Returns -1 once the pool is closed.
    int acquire();
    void release(int slot);
    void close();
    int size() const { return n_; }

private:
    const int n_;
    std::mutex m_;
    std::condition_variable cv_;
    std::vector<int> free_;
    bool closed_ = false;
};
