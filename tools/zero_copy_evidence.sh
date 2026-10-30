#!/bin/sh
# Collects the zero-copy evidence of PROTOCOL §11.4 (M1-2): perf at 1 and 4 channels for C, the
# decode-only baseline and B, 10 s each in steady state, and strace on C and B. Summarise with
# tools/zero_copy_evidence.py <out_dir>.
# Usage, on the board as root from the repository: tools/zero_copy_evidence.sh <video.mp4> <out_dir>
# Needs perf, strace, and libc6-dbg so that perf resolves memcpy inside libc.
#   (env: PERF, the perf binary; BENCH, the bench_main binary)
set -eu

VIDEO=$1
OUT=$2
PERF=${PERF:-/usr/lib/linux-tools/6.8.0-149-generic/perf}
BENCH=${BENCH:-./build/bench_main}
mkdir -p "$OUT"

# Starts bench_main in the background (5 s warm-up, 20 s window: the file lasts 39 s) and waits 8 s
# for steady state. Leaves the pid in $pid.
start() {
    "$BENCH" --variant "$1" --channels "$2" --source "$VIDEO" --warmup 5 --duration 20 \
        --out "$OUT/run_$1_$2ch" >"$OUT/run_$1_$2ch.log" 2>&1 &
    pid=$!
    sleep 8
}

for n in 1 4; do
    for v in none C B; do
        start "$v" "$n"
        "$PERF" record -g -p "$pid" -o "$OUT/perf_${v}_${n}ch.data" -- sleep 10 >/dev/null 2>&1
        wait "$pid"
        "$PERF" script -f -i "$OUT/perf_${v}_${n}ch.data" -F comm,tid,ip,sym,dso >"$OUT/perf_${v}_${n}ch.txt" 2>/dev/null
        sleep 2
    done
done

for v in C B; do
    start "$v" 1
    # Thread names and the fds that are dma-bufs, so the trace can be attributed.
    for t in /proc/"$pid"/task/*; do
        printf '%s %s\n' "${t##*/}" "$(cat "$t/comm")"
    done >"$OUT/strace_${v}_threads.txt"
    timeout 10 strace -f -e trace=mmap,ioctl -p "$pid" -o "$OUT/strace_${v}.txt" || true
    for f in /proc/"$pid"/fd/*; do
        printf '%s %s\n' "${f##*/}" "$(readlink "$f")"
    done >"$OUT/strace_${v}_fds.txt"
    wait "$pid"
    sleep 2
done
echo "evidence in $OUT"
