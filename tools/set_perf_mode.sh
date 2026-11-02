#!/bin/sh
# Fix the clocks for measurement (PROTOCOL §8): governor "performance" for CPU policies 0, 4 and 6,
# DDR (dmc) and the NPU devfreq, each held at its fixed frequency through its maximum. The fixed
# frequencies are the constants below (recorded in AMENDMENTS, §14):
#   - A55 (policy0) 1800 MHz, DDR 2112 MHz, NPU 1000 MHz: the highest each offers.
#   - A76 (policy4, policy6) 2208 MHz: offered on every boot seen (docs/notes/calibration/
#     a76_tables.txt). The top of policy4's table changes between boots (2304 or 2352 MHz), and
#     2208 MHz is also the kernel's high-temperature cap for both A76 clusters (h_limit, above 85 °C).
# The kernel's thermal throttling from soc-thermal's 75 °C trip can still go below these values
# (docs/notes/env.md, thermal observation); a run with such a sample is invalid (§9.3 rule 1).
# Prints them as "key: value" lines (the keys of summary.csv's freq_min_*); later runs are checked
# against them (§9.3 rule 1). Fails if a frequency is not offered or not reached; the frequencies
# read back are the ones the drivers report (scaling_cur_freq, devfreq cur_freq).
# A reboot restores the default governors.
# Usage, as root: tools/set_perf_mode.sh
set -eu

CPU_KHZ="0:1800000 4:2208000 6:2208000"  # policy:kHz
DMC_HZ=2112000000
NPU_HZ=1000000000

fail() {
    echo "set_perf_mode: $1" >&2
    exit 1
}
offers() {
    [ -r "$1" ] || fail "cannot read $1"
    tr ' ' '\n' <"$1" | grep -qx "$2"
}
[ "$(id -u)" -eq 0 ] || fail "run as root"

for pk in $CPU_KHZ; do
    d=/sys/devices/system/cpu/cpufreq/policy${pk%%:*}
    offers "$d/scaling_available_frequencies" "${pk#*:}" || fail "policy${pk%%:*} does not offer ${pk#*:} kHz"
    echo "${pk#*:}" >"$d/scaling_max_freq"
    echo performance >"$d/scaling_governor"
done
for nh in dmc:$DMC_HZ fdab0000.npu:$NPU_HZ; do
    d=/sys/class/devfreq/${nh%%:*}
    offers "$d/available_frequencies" "${nh#*:}" || fail "${nh%%:*} does not offer ${nh#*:} Hz"
    echo "${nh#*:}" >"$d/max_freq"
    echo performance >"$d/governor"
done
sleep 1

# Check the result rather than trust the writes. A thermal or PM-QoS limit shows up here as a lower
# max_freq or cur_freq.
for pk in $CPU_KHZ; do
    p=${pk%%:*}
    d=/sys/devices/system/cpu/cpufreq/policy$p
    [ "$(cat "$d/scaling_governor")" = performance ] || fail "policy$p governor is $(cat "$d/scaling_governor")"
    cur=$(cat "$d/scaling_cur_freq")
    [ "$cur" = "${pk#*:}" ] || fail "policy$p runs at $cur kHz, not ${pk#*:}"
    echo "policy${p}_khz: $cur"
done
for nh in dmc:$DMC_HZ:dmc_hz fdab0000.npu:$NPU_HZ:npu_hz; do
    name=${nh%%:*}
    rest=${nh#*:}
    want=${rest%%:*}
    d=/sys/class/devfreq/$name
    [ "$(cat "$d/governor")" = performance ] || fail "$name governor is $(cat "$d/governor")"
    [ "$(cat "$d/max_freq")" = "$want" ] || fail "$name max_freq is $(cat "$d/max_freq") Hz, not $want"
    cur=$(cat "$d/cur_freq")
    [ "$cur" = "$want" ] || fail "$name runs at $cur Hz, not $want"
    echo "${rest#*:}: $cur"
done
