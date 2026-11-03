#!/bin/sh
# Print the board environment as "key: value" lines. Run before every measurement
# (PROTOCOL §9.1) and paste into docs/notes/env.md for the baseline.
# Usage: tools/collect_env.sh   (env: SUDO, FFMPEG; COOLING and AMBIENT_C are free text)
set -eu

SUDO=${SUDO:-sudo}
FFMPEG=${FFMPEG:-/opt/ffmpeg-rockchip/bin/ffmpeg}

# An empty value means a command failed inside $(...), which set -e does not catch.
kv() {
    if [ -z "$2" ]; then
        echo "collect_env: no value for $1" >&2
        exit 1
    fi
    printf '%s: %s\n' "$1" "$2"
}
lib_version() { strings "$1" 2>/dev/null | sed -n "s/.*$2[[:space:]]*\([0-9][0-9.]*\).*/\1/p" | head -n 1; }

kv time_utc "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
kv boot_time_utc "$(date -u -d "@$(awk '/^btime/ {print $2}' /proc/stat)" +%Y-%m-%dT%H:%M:%SZ)"
kv ntp_synchronized "$(timedatectl show -p NTPSynchronized --value)"
kv board "$(tr -d '\0' < /proc/device-tree/model)"
kv os "$(. /etc/os-release && echo "$PRETTY_NAME")"
kv kernel "$(uname -r) $(uname -v)"
kv mem_total_kib "$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
kv rknpu_driver "$($SUDO cat /sys/kernel/debug/rknpu/version | sed 's/^RKNPU driver: //')"
kv librknnrt "$(lib_version /usr/lib/librknnrt.so 'librknnrt version:')"
kv librga "$(lib_version /usr/lib/aarch64-linux-gnu/librga.so.2 'rga_api version') (deb $(dpkg-query -W -f '${Version}' librga2))"
kv rga_driver "$($SUDO cat /sys/kernel/debug/rkrga/driver_version)"
kv mpp "deb $(dpkg-query -W -f '${Version}' librockchip-mpp1)"
kv ffmpeg "$("$FFMPEG" -hide_banner -version 2>/dev/null | head -n 1) ($FFMPEG)"
kv dma_heaps "$(ls /dev/dma_heap | tr '\n' ' ')"

for p in /sys/devices/system/cpu/cpufreq/policy*; do
    kv "cpufreq_$(basename "$p")" "cpus=$(cat "$p/related_cpus") governor=$(cat "$p/scaling_governor") cur_khz=$(cat "$p/scaling_cur_freq") min_khz=$(cat "$p/scaling_min_freq") max_khz=$(cat "$p/scaling_max_freq")"
done
for d in dmc fdab0000.npu; do
    f=/sys/class/devfreq/$d
    kv "devfreq_$d" "governor=$(cat "$f/governor") cur_hz=$(cat "$f/cur_freq") min_hz=$(cat "$f/min_freq") max_hz=$(cat "$f/max_freq")"
done
# The frequency tables themselves: their tops can change between boots (AMENDMENTS A7).
# A file is read into a variable first, so that a missing one stops the script (set -e).
list() {
    v=$(cat "$1")
    if [ -z "$v" ]; then
        echo "collect_env: $1 is empty" >&2
        exit 1
    fi
    echo $v | tr ' ' ','  # unquoted: one space between the values
}
for p in /sys/devices/system/cpu/cpufreq/policy*; do
    hw=$(cat "$p/cpuinfo_max_freq")
    avail=$(list "$p/scaling_available_frequencies")
    kv "cpufreq_$(basename "$p")_table" "hw_max_khz=$hw available_khz=$avail"
done
for d in dmc fdab0000.npu; do
    avail=$(list "/sys/class/devfreq/$d/available_frequencies")
    kv "devfreq_${d}_table" "available_hz=$avail"
done
# The kernel's high-temperature frequency caps (h_limit, applied above h m°C), from the boot log.
log=$($SUDO dmesg)
limits=$(echo "$log" | sed -n 's/.* \([^ ]*\): l=\(-\{0,1\}[0-9]*\) h=\([0-9]*\) hyst=[0-9]* l_limit=[0-9]* h_limit=\([0-9]*\).*/\1:h=\3:h_limit=\4/p' | tr '\n' ' ')
kv opp_temperature_limits "${limits:-not in the kernel log any more}"
for z in /sys/class/thermal/thermal_zone*; do
    kv "thermal_$(cat "$z/type")" "temp_mc=$(cat "$z/temp") trip0_mc=$(cat "$z/trip_point_0_temp" 2>/dev/null || echo none)"
done

kv cooling "${COOLING:-unrecorded}"
kv ambient_c "${AMBIENT_C:-unrecorded}"
