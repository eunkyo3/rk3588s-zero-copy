#!/bin/sh
# How much the libc copies of PROTOCOL §11.4 actually copy (M1-2). perf samples show where time
# goes, not how many bytes move: a uprobe on __memcpy_generic records every call's length for 4 s
# of one channel in steady state (20 inferences at 5 fps). Summarise with tools/copy_sizes.py.
# Usage, on the board as root from the repository: tools/copy_sizes.sh <variant> <video.mp4> <out_dir>
#   (env: PERF, the perf binary; BENCH, the bench_main binary)
set -u
V=$1
VIDEO=$2
OUT=$3
PERF=${PERF:-/usr/lib/linux-tools/6.8.0-149-generic/perf}
BENCH=${BENCH:-./build/bench_main}
LIBC=$(readlink -f /lib/aarch64-linux-gnu/libc.so.6)
mkdir -p "$OUT"
$PERF probe -q -d 'probe_libc:*' 2>/dev/null
$PERF probe -q -x "$LIBC" --add '__memcpy_generic len=%x2:u64' || exit 1
"$BENCH" --variant "$V" --channels 1 --source "$VIDEO" \
    --warmup 2 --duration 15 --out "$OUT/run_$V" >"$OUT/run_$V.log" 2>&1 &
pid=$!
sleep 8
$PERF record -q -e probe_libc:__memcpy_generic -g -p "$pid" -o "$OUT/memcpy_$V.data" -- sleep 4 >/dev/null 2>&1
wait "$pid"
$PERF script -f -i "$OUT/memcpy_$V.data" -F comm,tid,ip,sym,dso,trace >"$OUT/memcpy_$V.txt" 2>/dev/null
$PERF probe -q -d 'probe_libc:*'
python3 "$(dirname "$0")/copy_sizes.py" "$OUT/memcpy_$V.txt"
