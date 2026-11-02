#!/bin/sh
# M1-3 NPU capacity session (PROTOCOL §6.2, §8, §9): fixed clocks, the environment, the idle period
# and its baseline (§9.2), then 3 repetitions of P_cap (calib_npu --with-post on 3 cores) and of the
# raw NPU limit (1, 2 and 3 cores), interleaved, each run behind the cooldown gate. An invalid run
# (calib_npu exit code other than 0) is repeated once after the gate (§9.3); both are kept.
# tools/calib_summary.py then takes the medians and the derived values.
# Usage, on the board as root from the repository:
#   COOLING=<text> AMBIENT_C=<°C> tools/calib_npu.sh <tensors.u8> [out_dir]
#   (env: MODEL, default models/yolov8n_i8.rknn; FREEZE_COMMIT, default none; IDLE_S, default 600)
set -u

fail() {
    echo "calib_npu.sh: $1" >&2
    exit 1
}

[ $# -ge 1 ] || fail "usage: COOLING=... AMBIENT_C=... tools/calib_npu.sh <tensors.u8> [out_dir]"
TENSORS=$1
OUT=${2:-results/calibration/$(date -u +%Y%m%dT%H%M%SZ)_npu}
MODEL=${MODEL:-models/yolov8n_i8.rknn}
IDLE_S=${IDLE_S:-600}
[ -n "${COOLING:-}" ] && [ -n "${AMBIENT_C:-}" ] || fail "set COOLING and AMBIENT_C (PROTOCOL §2)"
[ -f "$TENSORS" ] || fail "no $TENSORS"
[ "$IDLE_S" -ge 60 ] || fail "IDLE_S must be at least 60"

mkdir -p "$OUT" || fail "cannot create $OUT"
tools/set_perf_mode.sh >"$OUT/perf_mode.txt" || fail "set_perf_mode.sh failed"
tools/collect_env.sh >"$OUT/env.txt" || fail "collect_env.sh failed"
record=${TENSORS%.u8}.json
if [ -f "$record" ]; then
    cp "$record" "$OUT/tensors.json" || fail "cannot copy $record"
fi
(cd "$(dirname "$TENSORS")" && sha256sum "$(basename "$TENSORS")") >"$OUT/tensors.sha256" || fail "sha256sum failed"

soc=
for z in /sys/class/thermal/thermal_zone*; do
    [ "$(cat "$z/type")" = soc-thermal ] && soc=$z/temp
done
[ -n "$soc" ] || fail "no soc-thermal zone"
uptime_s() { cut -d. -f1 /proc/uptime; }

# §9.2: the session starts idle; the baseline is the mean soc-thermal temperature over the last 60 s.
echo "idle for $IDLE_S s"
sum=0
i=0
while [ "$i" -lt "$IDLE_S" ]; do
    sleep 1
    i=$((i + 1))
    if [ "$i" -gt $((IDLE_S - 60)) ]; then
        sum=$((sum + $(cat "$soc")))
    fi
done
baseline=$((sum / 60))
echo "soc_thermal_baseline_mc: $baseline" | tee "$OUT/baseline.txt"
last_end=$(uptime_s)

# A run starts when soc-thermal is at most 3 °C above the baseline and no run has ended in the last 60 s.
gate() {
    while [ "$(cat "$soc")" -gt $((baseline + 3000)) ] || [ $(($(uptime_s) - last_end)) -lt 60 ]; do
        sleep 1
    done
}

printf 'repetition\tmeasurement\tcores\tattempt\tdir\texit\tgate_soc_mc\n' >"$OUT/runs.tsv"
# run <repetition> <p_cap|npu_raw> <cores>
run() {
    for attempt in 1 2; do
        gate
        temp=$(cat "$soc")
        name=${2}_${3}c_r${1}
        [ "$attempt" -eq 1 ] || name=${name}_repeat
        flag=
        [ "$2" = p_cap ] && flag=--with-post
        # $flag is unquoted on purpose: it is empty or one word.
        # shellcheck disable=SC2086
        ./build/calib_npu --tensors "$TENSORS" $flag --cores "$3" --model "$MODEL" --repetition "$1" \
            --freeze-commit "${FREEZE_COMMIT:-none}" --fixed-clocks "$OUT/perf_mode.txt" --out "$OUT/$name" \
            >"$OUT/$name.log" 2>&1
        status=$?
        last_end=$(uptime_s)
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$attempt" "$name" "$status" "$temp" >>"$OUT/runs.tsv"
        echo "$name: exit $status, $(grep '^inferences_per_s:' "$OUT/$name.log")"
        [ "$status" -eq 0 ] && return
    done
    echo "$2 $3 cores, repetition $1: invalid x2"
}

for rep in 1 2 3; do
    run "$rep" p_cap 3
    run "$rep" npu_raw 1
    run "$rep" npu_raw 2
    run "$rep" npu_raw 3
done
tools/collect_env.sh >"$OUT/env_end.txt" || fail "collect_env.sh failed"
status=0
python3 tools/calib_summary.py "$OUT" >"$OUT/calibration.txt" 2>&1 || status=$?
cat "$OUT/calibration.txt"
exit $status
