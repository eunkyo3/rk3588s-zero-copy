#!/bin/sh
# M1-3 NPU capacity session (PROTOCOL §6.2, §8, §9): fixed clocks, the environment, the idle period
# and its baseline (§9.2), then 3 repetitions of P_cap (calib_npu --with-post on 3 cores) and of the
# raw NPU limit (1, 2 and 3 cores), interleaved. Before each run: the cooldown gate, then
# tools/collect_env.sh (§9.1). An invalid run (calib_npu exit code other than 0) is repeated once,
# after the gate (§9.3); both are kept. tools/calib_summary.py then takes the medians and the
# derived values.
# If the gate holds a run back until 30 minutes have passed since the previous run, the session has
# ended (§2): the script stops, and the missing runs need a new session.
# Usage, on the board as root from the repository:
#   COOLING=<text> AMBIENT_C=<°C> tools/calib_npu.sh <tensors.u8> [out_dir]
#   (env: MODEL, default models/yolov8n_i8.rknn; FREEZE_COMMIT, default none; IDLE_S, default 600,
#    recorded, and flagged by calib_summary.py when it is not 600)
#   <tensors.u8> needs the record tools/dump_tensor writes next to it (<tensors>.json).
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
record=${TENSORS%.u8}.json
[ -f "$record" ] || fail "no $record: the generating path must be recorded (PROTOCOL §6.2)"
case $IDLE_S in
    '' | *[!0-9]*) fail "IDLE_S must be a whole number of seconds" ;;
esac
[ "$IDLE_S" -ge 60 ] || fail "IDLE_S must be at least 60"
if [ -e "$OUT" ] && [ -n "$(ls -A "$OUT")" ]; then
    fail "$OUT is not empty: every earlier run is kept (PROTOCOL §9.3)"
fi

mkdir -p "$OUT" || fail "cannot create $OUT"
tools/set_perf_mode.sh >"$OUT/perf_mode.txt" || fail "set_perf_mode.sh failed"
tools/collect_env.sh >"$OUT/env.txt" || fail "collect_env.sh failed"
cp "$record" "$OUT/tensors.json" || fail "cannot copy $record"
(cd "$(dirname "$TENSORS")" && sha256sum "$(basename "$TENSORS")") >"$OUT/tensors.sha256" || fail "sha256sum failed"
sha=$(cut -d' ' -f1 "$OUT/tensors.sha256")
grep -q "\"tensors_sha256\": \"$sha\"" "$OUT/tensors.json" || fail "$TENSORS does not match the SHA-256 in $record"

soc=
for z in /sys/class/thermal/thermal_zone*; do
    [ "$(cat "$z/type")" = soc-thermal ] && soc=$z/temp
done
[ -n "$soc" ] || fail "no soc-thermal zone"
# soc-thermal in m°C into soc_now (not in a subshell, so fail stops the script).
read_soc() {
    soc_now=$(cat "$soc")
    case $soc_now in
        '' | *[!0-9]*) fail "cannot read $soc" ;;
    esac
}
# /proc/uptime in hundredths of a second (the kernel prints two decimals).
uptime_cs() { cut -d' ' -f1 /proc/uptime | tr -d .; }

# §9.2: the session starts idle; the baseline is the mean soc-thermal temperature over the last 60 s.
echo "idle for $IDLE_S s"
sum=0
i=0
while [ "$i" -lt "$IDLE_S" ]; do
    sleep 1
    i=$((i + 1))
    if [ "$i" -gt $((IDLE_S - 60)) ]; then
        read_soc
        sum=$((sum + soc_now))
    fi
done
baseline=$((sum / 60))
printf 'idle_s: %s\nsoc_thermal_baseline_mc: %s\n' "$IDLE_S" "$baseline" | tee "$OUT/baseline.txt"
last_end=$(uptime_cs)

# A run starts when soc-thermal is at most 3 °C above the baseline and the previous run ended at
# least 60 s ago. soc_now then holds the temperature that passed.
gate() {
    while :; do
        read_soc
        since=$(($(uptime_cs) - last_end))
        if [ "$soc_now" -le $((baseline + 3000)) ] && [ "$since" -ge 6000 ]; then
            return
        fi
        [ "$since" -lt 180000 ] || fail "no run for 30 minutes: the session has ended (PROTOCOL §2)"
        sleep 1
    done
}

printf 'repetition\tmeasurement\tcores\tattempt\tdir\texit\tgate_soc_mc\tstart_utc\tend_utc\n' >"$OUT/runs.tsv"
# run <repetition> <p_cap|npu_raw> <cores>
run() {
    for attempt in 1 2; do
        gate
        temp=$soc_now
        name=${2}_${3}c_r${1}
        [ "$attempt" -eq 1 ] || name=${name}_repeat
        flag=
        [ "$2" = p_cap ] && flag=--with-post
        tools/collect_env.sh >"$OUT/$name.env.txt" || fail "collect_env.sh failed"
        start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
        # $flag is unquoted on purpose: it is empty or one word. A run takes about 150 s.
        # shellcheck disable=SC2086
        timeout 600 ./build/calib_npu --tensors "$TENSORS" $flag --cores "$3" --model "$MODEL" --repetition "$1" \
            --freeze-commit "${FREEZE_COMMIT:-none}" --fixed-clocks "$OUT/perf_mode.txt" --out "$OUT/$name" \
            >"$OUT/$name.log" 2>&1
        status=$?
        last_end=$(uptime_cs)
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$attempt" "$name" "$status" "$temp" \
            "$start" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$OUT/runs.tsv"
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
