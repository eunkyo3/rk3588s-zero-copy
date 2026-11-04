#!/bin/sh
# M1-7 demo (docs/notes/demo.md): C's detections on each M1 video (tools/demo_detect), then the
# 8-channel grid video (tools/demo_render.py). Checks the videos against their manifest first.
# Usage, on the board as root from the repository: tools/demo.sh [out_dir] [media_dir]
#   out_dir    default docs/report/demo: <video>.jsonl and .log, demo_m1.jpg, demo_m1.mp4 and its SHA-256
#   media_dir  default /home/pi/media/bench, the prepared videos and manifest.tsv
set -eu

OUT=${1:-docs/report/demo}
MEDIA=${2:-/home/pi/media/bench}
MODEL=models/yolov8n_i8.rknn

mkdir -p "$OUT"
for v in pexels_36330861 pexels_29130409 pexels_29969146 pixabay_180386; do
    want=$(awk -F'\t' -v v="$v" '$1 == v { print $2 }' "$MEDIA/manifest.tsv")
    [ -n "$want" ] || { echo "demo.sh: $v is not in $MEDIA/manifest.tsv" >&2; exit 1; }
    echo "$want  $MEDIA/$v.mp4" | sha256sum -c --quiet || exit 1
    ./build/demo_detect "$MEDIA/$v.mp4" "$MODEL" "$OUT/$v.jsonl" >"$OUT/$v.log" 2>&1 ||
        { cat "$OUT/$v.log" >&2; exit 1; }
    tail -n 1 "$OUT/$v.log"
done
python3 tools/demo_render.py --media "$MEDIA" --detections "$OUT" --out "$OUT/demo_m1.mp4" --poster "$OUT/demo_m1.jpg"
(cd "$OUT" && sha256sum demo_m1.mp4 >demo_m1.mp4.sha256)
cat "$OUT/demo_m1.mp4.sha256"
