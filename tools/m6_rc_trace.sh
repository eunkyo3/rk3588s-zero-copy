#!/bin/sh
# M6's system-call trace of arm Rc (prereg/M6.md §8 step 3, "Rc"): a short run of bench_main --variant Rc
# on one channel of a local video, with strace attached for 10 s of its steady state. tools/m6_checks.py
# reads it: two DMA_BUF_IOCTL_SYNC calls per frame on the slot, from the NPU worker that binds it, and
# none on the decoder's buffers.
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

"$BENCH" --variant Rc --channels 1 --source "$VIDEO" --warmup 5 --duration 20 --out "$OUT/run" \
    >"$OUT/run.log" 2>&1 &
pid=$!
sleep 8
# Thread names, so that the trace's calls can be attributed.
for t in /proc/"$pid"/task/*; do
    printf '%s %s\n' "${t##*/}" "$(cat "$t/comm")"
done >"$OUT/threads.txt"
# -ttt: every call with its time, seconds since the epoch, to place it among the run's frames.
timeout 10 strace -f -ttt -e trace=ioctl -p "$pid" -o "$OUT/strace.txt" || true
# The dma-bufs among the fds, each with the name of the exporter that made it (the heap's name for a slot).
for f in /proc/"$pid"/fd/*; do
    n=${f##*/}
    exp=$(sed -n 's/^exp_name:[[:space:]]*//p' "/proc/$pid/fdinfo/$n" 2>/dev/null || true)
    printf '%s\t%s\t%s\n' "$n" "$(readlink "$f" 2>/dev/null || true)" "$exp"
done >"$OUT/fds.tsv"
wait "$pid" || fail "bench_main failed: see $OUT/run.log"
[ -s "$OUT/strace.txt" ] || fail "the trace is empty"
echo "trace in $OUT: judge with tools/m6_checks.py"
