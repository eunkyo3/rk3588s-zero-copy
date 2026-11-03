#!/bin/sh
# Decode-only functional check (run kind "check") for an encoding other than M1's: for each channel
# count, one bench_main --variant none run over RTSP, with the environment before it and the memory
# behind the decoder's buffers sampled every second. Fixes the clocks first (tools/set_perf_mode.sh).
# The stream server must already serve the channels. Not a measurement of L (PROTOCOL §6.1): no
# cooldown gate, no stream-server log check; it shows whether and how far the decoder keeps up.
# Writes <out_dir>/<n>ch/ (bench_main's files), <n>ch.env.txt, <n>ch.log and <n>ch.mem.csv, with
#   t_s, dma-buf objects and bytes (debugfs), free pages of the DMA zone (below 4 GiB, where the
#   decoder's system-dma32 buffers come from, AMENDMENTS A1) and MemAvailable.
# Stops after the first run that exits with an error.
# Usage, as root from the repository on the board: tools/decode_probe.sh <out_dir> <channels>...
#   (env: SOURCE, default rtsp://10.88.0.1:8554/ch{n}; WARMUP, default 5; DURATION, default 30;
#    GAP_S between runs, default 10; COOLING and AMBIENT_C as for tools/collect_env.sh)
set -eu

SOURCE=${SOURCE:-rtsp://10.88.0.1:8554/ch\{n\}}
WARMUP=${WARMUP:-5}
DURATION=${DURATION:-30}
GAP_S=${GAP_S:-10}

if [ $# -lt 2 ]; then
    echo "usage: $0 <out_dir> <channels>..." >&2
    exit 2
fi
[ "$(id -u)" -eq 0 ] || { echo "decode_probe: run as root" >&2; exit 2; }
out=$1
shift
if [ -e "$out" ] && [ -n "$(ls -A "$out")" ]; then
    echo "decode_probe: $out is not empty" >&2
    exit 2
fi
mkdir -p "$out"

tools/set_perf_mode.sh >"$out/perf_mode.txt"
tools/inventory.sh >"$out/inventory.txt"

sample_memory() {
    echo "t_s,dmabuf_objects,dmabuf_bytes,dma_zone_free_pages,mem_available_kb"
    t=0
    while :; do
        b=$(sed -n 's/^Total \([0-9]*\) objects, \([0-9]*\) bytes$/\1,\2/p' /sys/kernel/debug/dma_buf/bufinfo)
        z=$(awk '/^Node 0, zone/ {zone = $4} zone == "DMA" && $1 == "pages" && $2 == "free" {print $3; exit}' /proc/zoneinfo)
        m=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
        echo "$t,$b,$z,$m"
        sleep 1
        t=$((t + 1))
    done
}

for n in "$@"; do
    case "$n" in
        '' | *[!0-9]* | 0) echo "decode_probe: bad channel count $n" >&2; exit 2 ;;
    esac
    tools/collect_env.sh >"$out/${n}ch.env.txt"
    sample_memory >"$out/${n}ch.mem.csv" &
    sampler=$!
    status=0
    build/bench_main --variant none --channels "$n" --source "$SOURCE" --warmup "$WARMUP" \
        --duration "$DURATION" --out "$out/${n}ch" >"$out/${n}ch.log" 2>&1 || status=$?
    kill "$sampler"
    wait "$sampler" 2>/dev/null || true
    echo "${n} channels: exit $status"
    if [ "$status" -ne 0 ]; then
        exit 1
    fi
    sleep "$GAP_S"
done
