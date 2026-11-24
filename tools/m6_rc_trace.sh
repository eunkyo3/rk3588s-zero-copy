#!/bin/sh
# M6's system-call trace of arm Rc (prereg/M6.md §8 step 3, "Rc"): a short run of bench_main --variant Rc
# on one channel of a local video, with strace attached for 10 s of its steady state, and then the same
# for C. tools/m6_checks.py reads both: the arm's DMA_BUF_IOCTL_SYNC calls are two per frame on the slot,
# from the NPU worker that binds it, and none elsewhere. MPP's own sync calls on its buffers, from the
# decoder's threads, are in both traces and are counted apart.
# Usage, on the board as root from the repository: tools/m6_rc_trace.sh [out_dir]
#   (env: MEDIA, default /home/pi/media/bench; BENCH, default ./build/bench_main)
# Needs strace. Exit 0 when the run and the trace were made, 3 otherwise. It judges nothing.
set -eu
export LC_ALL=C

OUT=${1:-results/check/$(date -u +%Y%m%dT%H%M%SZ)_m6_rc_trace}
MEDIA=${MEDIA:-/home/pi/media/bench}
BENCH=${BENCH:-./build/bench_main}
VIDEO=$MEDIA/pexels_36330861.mp4  # video ①, 39.5 s: 5 s warm-up and a 20 s window fit in it

fail() {
    echo "m6_rc_trace.sh: $1" >&2
    exit 3
}

command -v strace >/dev/null || fail "strace is not installed"
[ -f "$VIDEO" ] || fail "no $VIDEO"
[ ! -e "$OUT" ] || fail "$OUT exists"
mkdir -p "$OUT"
# First, so that the record has a start time of its own (PROTOCOL §1.4).
tools/collect_env.sh >"$OUT/env.txt" || fail "collect_env.sh failed"

# One traced run. $1: the variant; $2: the files' suffix ("" for Rc, "_c" for C).
trace() {
    "$BENCH" --variant "$1" --channels 1 --source "$VIDEO" --warmup 5 --duration 20 --out "$OUT/run$2" \
        >"$OUT/run$2.log" 2>&1 &
    pid=$!
    sleep 8
    # Thread names, so that the trace's calls can be attributed.
    for t in /proc/"$pid"/task/*; do
        printf '%s %s\n' "${t##*/}" "$(cat "$t/comm")"
    done >"$OUT/threads$2.txt"
    # -ttt: every call with its time, seconds since the epoch, to place it among the run's frames.
    timeout 10 strace -f -ttt -e trace=ioctl -p "$pid" -o "$OUT/strace$2.txt" || true
    # The dma-bufs among the fds, each with its size and the name of the exporter that made it. A slot is a
    # buffer of the slot heap with the model input's size; the decoder's buffers come from the same heap
    # (A1) and are larger.
    for f in /proc/"$pid"/fd/*; do
        n=${f##*/}
        exp=$(sed -n 's/^exp_name:[[:space:]]*//p' "/proc/$pid/fdinfo/$n" 2>/dev/null || true)
        size=$(sed -n 's/^size:[[:space:]]*//p' "/proc/$pid/fdinfo/$n" 2>/dev/null || true)
        printf '%s\t%s\t%s\t%s\n' "$n" "$(readlink "$f" 2>/dev/null || true)" "$exp" "$size"
    done >"$OUT/fds$2.tsv"
    wait "$pid" || fail "bench_main --variant $1 failed: see $OUT/run$2.log"
    [ -s "$OUT/strace$2.txt" ] || fail "the trace of $1 is empty"
}

trace Rc ""
sleep 2
# C the same way: MPP's own sync calls, from the decoder's threads, are in every arm, and C makes none itself.
trace C _c
echo "trace in $OUT: judge with tools/m6_checks.py"
