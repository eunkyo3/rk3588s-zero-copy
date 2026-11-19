// The library's failure policy for the measured sources. They include "core/util.h" and call die()
// or CHECK() when something fails; in bench_main that prints and exits. This directory comes before
// src/ on the library's include path, so the same source files get this header instead, and here
// die() throws rkzc::Fatal. The measured header is included for everything else (the clocks, CHECK),
// with its own die() renamed out of the way, so nothing is written twice.
#pragma once

#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <stdexcept>

// The standard headers above are the measured header's too: included here first, so none of them is
// read while `die` is a macro.
#define die rkzc_measured_die
#include "../../../src/core/util.h"
#undef die

namespace rkzc {
struct Fatal : std::runtime_error {
    using std::runtime_error::runtime_error;
};
}  // namespace rkzc

[[noreturn]] inline void die(const char* fmt, ...) {
    char msg[512];
    va_list ap;
    va_start(ap, fmt);
    std::vsnprintf(msg, sizeof msg, fmt, ap);
    va_end(ap);
    throw rkzc::Fatal(msg);
}
