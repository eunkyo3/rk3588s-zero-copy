#!/bin/sh
# M1-4 equivalence (PROTOCOL §11.1, §11.2, AMENDMENTS A5): the environment, then C, B and R over the
# first frames of a file_source video (tools/equivalence_dump), then tools/equivalence_check.py.
# Exit code: the check's (0 pass, 1 fail, 2 inconsistent inputs or the check could not run), or 3 if
# anything before the check failed.
# Usage, on the board as root from the repository: tools/equivalence.sh <video.mp4> [out_dir]
#   (env: MODEL, default models/yolov8n_i8.rknn; FRAMES, default 1000; FREEZE_COMMIT, default none;
#    C_PLACE, default C. C_PLACE=By checks arm By of M6 in C's place, as run kind check, into
#    results/check/<UTC>_m6_by_equivalence: prereg/M6.md §8 step 3)
set -u

if [ $# -lt 1 ]; then
    echo "usage: tools/equivalence.sh <video.mp4> [out_dir]" >&2
    exit 3
fi
VIDEO=$1
C_PLACE=${C_PLACE:-C}
if [ "$C_PLACE" = By ]; then
    KIND=check
    OUT=${2:-results/check/$(date -u +%Y%m%dT%H%M%SZ)_m6_by_equivalence}
else
    KIND=equivalence
    OUT=${2:-results/equivalence/$(date -u +%Y%m%dT%H%M%SZ)}
fi
MODEL=${MODEL:-models/yolov8n_i8.rknn}
FRAMES=${FRAMES:-1000}

fail() {
    echo "equivalence.sh: $1" >&2
    exit 3
}

mkdir -p "$OUT" || fail "cannot create $OUT"
tools/collect_env.sh >"$OUT/env.txt" || fail "collect_env.sh failed"
if [ "$C_PLACE" = By ]; then # M6 §8 step 3: the check is on video ①, and tools/m6_checks.py reads this
    sha256sum "$VIDEO" | cut -c1-64 >"$OUT/source.sha256" || fail "cannot hash $VIDEO"
fi
if ! ./build/equivalence_dump "$VIDEO" "$MODEL" "$OUT" "$FRAMES" "${FREEZE_COMMIT:-none}" "$KIND" "$C_PLACE" \
    >"$OUT/dump.txt" 2>"$OUT/dump.log"; then
    cat "$OUT/dump.txt" "$OUT/dump.log"
    fail "equivalence_dump failed"
fi
cat "$OUT/dump.txt"
# The tensors are too large to keep in the repository; their hashes are kept instead.
(cd "$OUT" && sha256sum b.u8 c.u8 r.u8 >tensors.sha256) || fail "sha256sum failed"
status=0
python3 tools/equivalence_check.py --b "$OUT/b.jsonl" --c "$OUT/c.jsonl" --r "$OUT/r.jsonl" --frames "$FRAMES" \
    --json "$OUT/check.json" --c-place "$C_PLACE" >"$OUT/check.txt" 2>&1 || status=$?
cat "$OUT/check.txt"
case $status in
0 | 1 | 2) ;;
*) status=2 ;; # python3 missing, or a signal: the check did not run
esac
exit $status
