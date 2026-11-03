#!/bin/sh
# Which shared libraries bench_main maps while it runs, beyond those ldd lists (AMENDMENTS A10):
# libraries opened with dlopen are missing from tools/inventory.sh unless it adds them. A functional
# check (run kind "check"): each run is short and not a measurement.
# For each <variant>:<source>, one 1-channel run (warm-up 3 s, window 6 s); 6 s after its start, the
# .so files in /proc/<pid>/maps. Writes <out_dir>/ldd.txt, and per run <variant>_<n>/ (bench_main's
# files), <variant>_<n>.maps.txt and <variant>_<n>.log; prints the libraries not in ldd.txt.
# Usage, as root from the repository on the board: tools/runtime_libs.sh <out_dir> <variant>:<source>...
#   <source> as for bench_main --source, e.g. rtsp://10.88.0.1:8554/ch{n} or a local file
set -eu
export LC_ALL=C

if [ $# -lt 2 ]; then
    echo "usage: $0 <out_dir> <variant>:<source>..." >&2
    exit 2
fi
[ "$(id -u)" -eq 0 ] || { echo "runtime_libs: run as root" >&2; exit 2; }
out=$1
shift
if [ -e "$out" ] && [ -n "$(ls -A "$out")" ]; then
    echo "runtime_libs: $out is not empty" >&2
    exit 2
fi
mkdir -p "$out"

ldd build/bench_main | awk '$2 == "=>" {print $3} $1 ~ /^\// {print $1}' | xargs -n 1 readlink -f |
    sort -u >"$out/ldd.txt"
[ -s "$out/ldd.txt" ] || { echo "runtime_libs: ldd lists nothing" >&2; exit 1; }

k=0
status=0
for run in "$@"; do
    variant=${run%%:*}
    source=${run#*:}
    k=$((k + 1))
    name=${variant}_$k
    build/bench_main --variant "$variant" --channels 1 --source "$source" --warmup 3 --duration 6 \
        --out "$out/$name" >"$out/$name.log" 2>&1 &
    pid=$!
    sleep 6
    maps=$(awk '$6 ~ /\.so/ {print $6}' "/proc/$pid/maps" 2>/dev/null) || maps=
    if [ -n "$maps" ]; then
        printf '%s\n' "$maps" | xargs -n 1 readlink -f | sort -u >"$out/$name.maps.txt"
    else
        echo "runtime_libs: $name ended before its maps were read" >&2
        : >"$out/$name.maps.txt"
        status=1
    fi
    rc=0
    wait "$pid" || rc=$?
    echo "$name ($variant, $source): exit $rc, $(wc -l <"$out/$name.maps.txt") libraries mapped, not in ldd:"
    comm -23 "$out/$name.maps.txt" "$out/ldd.txt" | sed 's/^/  /'
    [ "$rc" -eq 0 ] || status=1
    sleep 2  # a publisher stops 1 s after its reader leaves (stream-server/README.md)
done
exit $status
