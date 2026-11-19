#!/bin/sh
# The library against the benchmark's own tool, on a video with objects in it (lib/tests/equivalence.py).
# Usage: equivalence.sh DEMO_DETECT RKZC_JSONL MODEL.rknn VIDEO
#   DEMO_DETECT: the root build's demo_detect (the registered code)
set -eu
demo=$1 jsonl=$2 model=$3 video=$4
frames=300  # 10 s at 30 fps: 50 sampled frames
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
"$demo" "$video" "$model" "$tmp/demo.jsonl" "$frames" 6 >/dev/null 2>"$tmp/demo.err"
# The library reads the file at its frame rate; 14 s covers the first 300 frames.
"$jsonl" --model "$model" --post m1 --every 6 --seconds 14 "$video" >"$tmp/lib.jsonl" 2>"$tmp/lib.err"
python3 "$(dirname "$0")/equivalence.py" "$tmp/demo.jsonl" "$tmp/lib.jsonl"
