#!/bin/sh
# M1-4 equivalence (PROTOCOL §11.1, §11.2, AMENDMENTS A5): the environment, then C, B and R over the
# first frames of a file_source video (tools/equivalence_dump), then tools/equivalence_check.py.
# Exit code: the check's (0 pass, 1 fail, 2 inconsistent inputs), or 3 if the dump failed.
# Usage, on the board as root from the repository: tools/equivalence.sh <video.mp4> [out_dir]
#   (env: MODEL, default models/yolov8n_i8.rknn; FRAMES, default 1000; FREEZE_COMMIT, default none)
set -eu

VIDEO=$1
OUT=${2:-results/equivalence/$(date -u +%Y%m%dT%H%M%SZ)}
MODEL=${MODEL:-models/yolov8n_i8.rknn}
FRAMES=${FRAMES:-1000}
mkdir -p "$OUT"

tools/collect_env.sh >"$OUT/env.txt"
if ! ./build/equivalence_dump "$VIDEO" "$MODEL" "$OUT" "$FRAMES" "${FREEZE_COMMIT:-none}" >"$OUT/dump.txt" 2>"$OUT/dump.log"; then
    cat "$OUT/dump.txt" "$OUT/dump.log"
    exit 3
fi
cat "$OUT/dump.txt"
# The tensors are too large to keep in the repository; their hashes are kept instead.
(cd "$OUT" && sha256sum b.u8 c.u8 r.u8 >tensors.sha256)
status=0
python3 tools/equivalence_check.py --b "$OUT/b.jsonl" --c "$OUT/c.jsonl" --r "$OUT/r.jsonl" --frames "$FRAMES" \
    --json "$OUT/check.json" >"$OUT/check.txt" 2>&1 || status=$?
cat "$OUT/check.txt"
exit $status
