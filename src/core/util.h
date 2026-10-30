// Small helpers shared by the pipeline: fatal errors and the monotonic clock (PROTOCOL §7).
#pragma once

#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <ctime>

[[noreturn]] inline void die(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    std::fprintf(stderr, "bench: ");
    std::vfprintf(stderr, fmt, ap);
    std::fprintf(stderr, "\n");
    va_end(ap);
    std::exit(1);
}

#define CHECK(cond, ...)          \
    do {                          \
        if (!(cond))              \
            die(__VA_ARGS__);     \
    } while (0)

// CLOCK_MONOTONIC in nanoseconds.
inline int64_t now_ns() {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return int64_t(ts.tv_sec) * 1000000000 + ts.tv_nsec;
}
