#!/bin/sh
# Fix the clocks for measurement (PROTOCOL §8): governor "performance" for CPU policies 0, 4 and 6,
# DDR (dmc) and the NPU devfreq, each with its maximum reset to the hardware maximum first. Prints
# the resulting frequencies as "key: value" lines (the keys of summary.csv's freq_min_*). M1-3
# records them in AMENDMENTS (§14), and later runs are checked against them (§9.3 rule 1).
# The checks compare with the hardware maxima, so the script fails rather than record a clock that
# a thermal or PM-QoS limit holds down. A reboot restores the default governors.
# Usage, as root: tools/set_perf_mode.sh
set -eu

fail() {
    echo "set_perf_mode: $1" >&2
    exit 1
}
# The highest frequency a devfreq device offers. Its max_freq reads lower while a limit applies.
top() { tr ' ' '\n' <"/sys/class/devfreq/$1/available_frequencies" | sort -n | tail -n 1; }

for p in 0 4 6; do
    d=/sys/devices/system/cpu/cpufreq/policy$p
    cat "$d/cpuinfo_max_freq" >"$d/scaling_max_freq"
    echo performance >"$d/scaling_governor"
done
for name in dmc fdab0000.npu; do
    d=/sys/class/devfreq/$name
    top "$name" >"$d/max_freq"
    echo performance >"$d/governor"
done
sleep 1

# Check the result rather than trust the writes.
for p in 0 4 6; do
    d=/sys/devices/system/cpu/cpufreq/policy$p
    [ "$(cat "$d/scaling_governor")" = performance ] || fail "policy$p governor is $(cat "$d/scaling_governor")"
    want=$(cat "$d/cpuinfo_max_freq")
    cur=$(cat "$d/scaling_cur_freq")
    [ "$cur" = "$want" ] || fail "policy$p runs at $cur kHz, not $want"
    echo "policy${p}_khz: $cur"
done
for name in dmc fdab0000.npu; do
    d=/sys/class/devfreq/$name
    [ "$(cat "$d/governor")" = performance ] || fail "$name governor is $(cat "$d/governor")"
    want=$(top "$name")
    [ "$(cat "$d/max_freq")" = "$want" ] || fail "$name max_freq is $(cat "$d/max_freq") Hz, not $want"
    cur=$(cat "$d/cur_freq")
    [ "$cur" = "$want" ] || fail "$name runs at $cur Hz, not $want"
    case $name in
        dmc) echo "dmc_hz: $cur" ;;
        *) echo "npu_hz: $cur" ;;
    esac
done
