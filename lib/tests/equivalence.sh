#!/bin/sh
# The library against the benchmark's own tool, on a video with objects in it (lib/tests/equivalence.py).
# Usage: equivalence.sh DEMO_DETECT RKZC_JSONL MODEL.rknn VIDEO [FFMPEG]
#   DEMO_DETECT: the root build's demo_detect (the registered code)
#   FFMPEG: ffmpeg-rockchip's ffmpeg; with it, a portrait version of the video is made and compared too
# Three comparisons, each with demo_detect (M1's post-processing):
#   1. the library with --post m1
#   2. the library with its default post-processing (for YOLOv8: neon)
#   3. --post m1 on a portrait clip, whose letterbox has bars at the sides (at 16:9 they are above and
#      below, so a mistake in the horizontal offset would not show)
set -eu
demo=$1 jsonl=$2 model=$3 video=$4 ffmpeg=${5:-}
frames=300  # 10 s at 30 fps: 50 sampled frames
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

compare() {  # name, video, post
    "$demo" "$2" "$model" "$tmp/demo.jsonl" "$frames" 6 >/dev/null 2>"$tmp/demo.err"
    # The library reads the file at its frame rate; 14 s covers the first 300 frames.
    "$jsonl" --model "$model" --post "$3" --every 6 --seconds 14 "$2" >"$tmp/lib.jsonl" 2>"$tmp/lib.err"
    printf '%s: ' "$1"
    python3 "$(dirname "$0")/equivalence.py" "$tmp/demo.jsonl" "$tmp/lib.jsonl"
}

compare "m1" "$video" m1
compare "default" "$video" auto
if [ -n "$ffmpeg" ]; then
    "$ffmpeg" -nostdin -hide_banner -loglevel error -y -t 11 -i "$video"         -vf "crop=trunc(ih*9/32)*2:ih,scale=1080:1920" -c:v h264_rkmpp -b:v 6M "$tmp/portrait.mp4"         >"$tmp/ffmpeg.out" 2>"$tmp/ffmpeg.err" </dev/null ||
        { echo "FAIL: could not make the portrait clip:"; grep -v '^mpp\[' "$tmp/ffmpeg.err" | head; exit 1; }
    compare "portrait" "$tmp/portrait.mp4" m1
fi
