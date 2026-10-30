// The receive checks of PROTOCOL §7.6, as plain functions so they can be tested.
#pragma once

#include <algorithm>
#include <cstdint>

// Frames missing before a frame whose PTS is step_ticks after the previous frame's, for a stream
// with time base tb_num/tb_den and a nominal rate of fps. A step <= 0 counts 1; a step of at most
// 1.5 periods counts 0; a longer step counts max(1, round(step / period) - 1). Exact integer
// arithmetic, so a step of exactly 1.5 periods is not a tie.
inline int64_t missing_frames(int64_t step_ticks, int64_t tb_num, int64_t tb_den, int64_t fps) {
    if (step_ticks <= 0)
        return 1;
    const int64_t num = step_ticks * tb_num * fps;  // step / period = num / tb_den
    if (2 * num <= 3 * tb_den)
        return 0;
    const int64_t periods = (2 * num + tb_den) / (2 * tb_den);  // rounded, halves up
    return std::max<int64_t>(1, periods - 1);
}

// Receive drift in seconds after `seconds` of the window, with `frames` returned in it.
inline double drift_s(double seconds, int64_t frames, double fps) {
    return (fps * seconds - double(frames)) / fps;
}
