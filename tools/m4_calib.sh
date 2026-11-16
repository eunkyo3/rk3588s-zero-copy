#!/bin/sh
# M4's NPU capacity session (prereg/M4.md §3.1): for each of M4's two models, P_cap (calib_npu
# --with-post on 3 cores, with the pipeline's post-processing of M4 §1: neon for YOLOv8n, v10 for
# YOLOv10n) and the raw limit on 1 core, × 3 repetitions: 12 runs, as M1-3's procedure (PROTOCOL §6.2,
# §9) runs them in tools/calib_npu.sh, whose idle period, baseline and cooldown gate this copies.
#   - Order: within a repetition, one model's two runs come first, P_cap before the raw limit. The
#     model that goes first alternates between repetitions, starting with YOLOv8n.
#   - Before the session: M1-3's tensor set (SHA-256 below, A7) and both models' SHA-256 (M4 §1).
#   - Before each run: the cooldown gate, then tools/collect_env.sh (PROTOCOL §9.1).
#   - An invalid run (calib_npu exit code other than 0: PROTOCOL §9.3 rules 1 and 2, or its output
#     check, A7) is repeated once, after the gate (M4 §5); both are kept.
#   - tools/m4_calib_summary.py then takes the medians and the saturation labels (M4 §3.2).
#   - Every run's header carries freeze-m1's commit (PROTOCOL §1.4) and freeze-m4's (M4 §4).
# If the gate holds a run back until 30 minutes have passed since the previous run, the session has
# ended (PROTOCOL §2): the script stops. M4 §3.1 has all 12 runs in one session, so the stopped session
# is kept and a new one is run in full.
# Usage, on the board as root from the repository:
#   COOLING=<text> AMBIENT_C=<°C> FREEZE_M4_COMMIT=<sha> tools/m4_calib.sh <tensors.u8> [out_dir]
#   (env: RUN_KIND, default m4, or check with FREEZE_M4_COMMIT=none for a check before freeze-m4;
#    IDLE_S, default 600; WARMUP_S and DURATION_S, default 20 and 120. Values other than these
#    defaults are recorded, and m4_calib_summary.py reports the session as not M4's settings.)
#   <tensors.u8> needs the record tools/dump_tensor writes next to it (<tensors>.json).
set -u

TENSORS_SHA256=051d6f12662e0b8ef9b72f00f74f498683913c39f197deb42c34ac1a54340c67  # A7, M4 §3.1
V8=models/yolov8n_i8.rknn
V8_SHA256=04a76611a785802b08703192455994e07bbfe4531821ae7ed35faf78fe1ec1ee  # M4 §1
V10=models/yolov10n_i8.rknn
V10_SHA256=6a6442e1addbd1f08dbdb0772aeea1a992233b4f1db3bd451d20823522246f81
FREEZE_M1_COMMIT=2eec964d27545e081a44bc5ead01bbfcd2905a54  # docs/notes/env.md, Registration

fail() {
    echo "m4_calib.sh: $1" >&2
    exit 1
}

[ $# -ge 1 ] || fail "usage: COOLING=... AMBIENT_C=... FREEZE_M4_COMMIT=... tools/m4_calib.sh <tensors.u8> [out_dir]"
TENSORS=$1
RUN_KIND=${RUN_KIND:-m4}
OUT=${2:-results/$RUN_KIND/$(date -u +%Y%m%dT%H%M%SZ)_m4_calib}
IDLE_S=${IDLE_S:-600}
WARMUP_S=${WARMUP_S:-20}
DURATION_S=${DURATION_S:-120}
[ -n "${COOLING:-}" ] && [ -n "${AMBIENT_C:-}" ] || fail "set COOLING and AMBIENT_C (PROTOCOL §2)"
[ -n "${FREEZE_M4_COMMIT:-}" ] || fail "set FREEZE_M4_COMMIT (none before freeze-m4)"
case $RUN_KIND in
    m4)
        case $FREEZE_M4_COMMIT in
            *[!0-9a-f]*) fail "FREEZE_M4_COMMIT must be freeze-m4's full commit SHA for run kind m4" ;;
        esac
        [ ${#FREEZE_M4_COMMIT} -eq 40 ] || fail "FREEZE_M4_COMMIT must be freeze-m4's full commit SHA for run kind m4"
        ;;
    check) [ "$FREEZE_M4_COMMIT" = none ] || fail "run kind check is for before freeze-m4: FREEZE_M4_COMMIT=none" ;;
    *) fail "RUN_KIND must be m4 or check" ;;
esac
for v in IDLE_S WARMUP_S DURATION_S; do
    eval "x=\$$v"
    case $x in
        '' | *[!0-9]*) fail "$v must be a whole number of seconds" ;;
    esac
done
[ "$IDLE_S" -ge 60 ] || fail "IDLE_S must be at least 60"
[ -f "$TENSORS" ] || fail "no $TENSORS"
record=${TENSORS%.u8}.json
[ -f "$record" ] || fail "no $record: the generating path must be recorded (PROTOCOL §6.2)"
[ -x ./build/calib_npu ] || fail "no ./build/calib_npu"
if [ -e "$OUT" ] && [ -n "$(ls -A "$OUT")" ]; then
    fail "$OUT is not empty: every earlier run is kept (PROTOCOL §9.3)"
fi
# The files are checked before the session directory exists, so a wrong one leaves nothing behind.
tensors_line=$(cd "$(dirname "$TENSORS")" && sha256sum "$(basename "$TENSORS")") || fail "sha256sum failed"
sha=${tensors_line%% *}
[ "$sha" = "$TENSORS_SHA256" ] || fail "$TENSORS is not M1-3's tensor set (SHA-256 $sha, A7)"
grep -q "\"tensors_sha256\": \"$sha\"" "$record" || fail "$TENSORS does not match the SHA-256 in $record"
models_lines=$(sha256sum "$V8" "$V10") || fail "sha256sum of the models failed"
echo "$models_lines" | grep -q "^$V8_SHA256  $V8\$" || fail "$V8 is not M4's YOLOv8n (M4 §1)"
echo "$models_lines" | grep -q "^$V10_SHA256  $V10\$" || fail "$V10 is not M4's YOLOv10n (M4 §1)"

mkdir -p "$OUT" || fail "cannot create $OUT"
printf 'run_kind: %s\nfreeze_m4_commit: %s\ncooling: %s\nambient_c: %s\nwarmup_s: %s\nduration_s: %s\n' \
    "$RUN_KIND" "$FREEZE_M4_COMMIT" "$COOLING" "$AMBIENT_C" "$WARMUP_S" "$DURATION_S" >"$OUT/session.txt"
echo "$tensors_line" >"$OUT/tensors.sha256"
echo "$models_lines" >"$OUT/models.sha256"
# Not as .json: the record's start_time_utc is the set's dump time (2026-11-02), which
# tools/check_prereg_order.py would read as this session's start.
cp "$record" "$OUT/tensor_set.record" || fail "cannot copy $record"
tools/set_perf_mode.sh >"$OUT/perf_mode.txt" || fail "set_perf_mode.sh failed"
tools/collect_env.sh >"$OUT/env.txt" || fail "collect_env.sh failed"

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

# PROTOCOL §9.2: the session starts idle; the baseline is the mean soc-thermal temperature over the
# last 60 s.
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
        # 30 minutes less a minute for collect_env.sh and loading the tensors.
        [ "$since" -lt 174000 ] || fail "no run for 30 minutes: the session has ended (PROTOCOL §2)"
        if [ "$soc_now" -le $((baseline + 3000)) ] && [ "$since" -ge 6000 ]; then
            return
        fi
        sleep 1
    done
}

printf 'repetition\tmodel\tmeasurement\tcores\tattempt\tdir\texit\tgate_soc_mc\tstart_utc\tend_utc\n' >"$OUT/runs.tsv"
# run <repetition> <yolov8n|yolov10n> <p_cap|npu_raw>
run() {
    if [ "$2" = yolov8n ]; then model=$V8 post=neon; else model=$V10 post=v10; fi
    if [ "$3" = p_cap ]; then cores=3 flags="--with-post --post $post"; else cores=1 flags=; fi
    for attempt in 1 2; do
        gate
        temp=$soc_now
        name=${2}_${3}_${cores}c_r${1}
        [ "$attempt" -eq 1 ] || name=${name}_repeat
        tools/collect_env.sh >"$OUT/$name.env.txt" || fail "collect_env.sh failed"
        start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
        # $flags is unquoted on purpose: it is empty or three words. A run takes about 150 s; the limit
        # is calib_npu.sh's 600 s at M4's warm-up and window.
        # shellcheck disable=SC2086
        timeout -k 30 $((WARMUP_S + DURATION_S + 460)) ./build/calib_npu --tensors "$TENSORS" $flags \
            --cores "$cores" --model "$model" --repetition "$1" --run-kind "$RUN_KIND" \
            --freeze-commit "$FREEZE_M1_COMMIT" --freeze-m4-commit "$FREEZE_M4_COMMIT" \
            --warmup "$WARMUP_S" --duration "$DURATION_S" --fixed-clocks "$OUT/perf_mode.txt" --out "$OUT/$name" \
            >"$OUT/$name.log" 2>&1
        status=$?
        last_end=$(uptime_cs)
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$cores" "$attempt" "$name" "$status" \
            "$temp" "$start" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$OUT/runs.tsv"
        echo "$name: exit $status, $(grep '^inferences_per_s:' "$OUT/$name.log")"
        [ "$status" -eq 0 ] && return
    done
    echo "$2 $3, repetition $1: invalid x2"
}

for rep in 1 2 3; do
    if [ $((rep % 2)) -eq 1 ]; then order="yolov8n yolov10n"; else order="yolov10n yolov8n"; fi
    for m in $order; do
        run "$rep" "$m" p_cap
        run "$rep" "$m" npu_raw
    done
done
tools/collect_env.sh >"$OUT/env_end.txt" || fail "collect_env.sh failed"
status=0
python3 tools/m4_calib_summary.py "$OUT" >"$OUT/m4_calibration.txt" 2>&1 || status=$?
cat "$OUT/m4_calibration.txt"
exit $status
