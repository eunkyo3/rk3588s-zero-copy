#!/bin/sh
# Held-out equivalence check (AMENDMENTS A8): tools/equivalence_dump on every frame of videos ②, ③
# and ④, which A5's criteria were not chosen on, then tools/equivalence_check.py on the three pooled.
# Each video must have its registered SHA-256 and frame count; otherwise nothing is dumped.
# Exit code: the check's (0 pass, 1 fail, 2 inconsistent inputs or the check could not run), or 3 if
# anything before the check failed.
# Usage, on the board as root from the repository: tools/equivalence_holdout.sh [out_dir]
#   (env: MEDIA, default /home/pi/media/bench; MODEL, default models/yolov8n_i8.rknn;
#    FFPROBE, default /opt/ffmpeg-rockchip/bin/ffprobe; FREEZE_COMMIT, default none)
set -u

OUT=${1:-results/equivalence/$(date -u +%Y%m%dT%H%M%SZ)_holdout}
MEDIA=${MEDIA:-/home/pi/media/bench}
MODEL=${MODEL:-models/yolov8n_i8.rknn}
FFPROBE=${FFPROBE:-/opt/ffmpeg-rockchip/bin/ffprobe}
# name, frames, SHA-256 (docs/notes/preprocess/videos_manifest.tsv), in the order they are pooled
VIDEOS="pexels_29130409 752 e560dd8481d5fe1c615f208fae192fc7fd583097d202e785dc026eae8fb75b41
pexels_29969146 714 592b6847e2e63e07393af6f4a04b691be438c23eba2c73f79b021dca877c2772
pixabay_180386 948 2538f54d36e0d763ec3e13f9e606bf01e2d142fff82181eb156f792576b0555b"

fail() {
    echo "equivalence_holdout.sh: $1" >&2
    exit 3
}

[ ! -e "$OUT" ] || fail "$OUT exists"
# Check every input before the first dump.
set -- $VIDEOS
while [ $# -ge 3 ]; do
    video=$MEDIA/$1.mp4
    echo "$3  $video" | sha256sum -c --status - || fail "$video: SHA-256 is not $3"
    n=$("$FFPROBE" -v error -count_packets -select_streams v:0 -show_entries stream=nb_read_packets \
        -of csv=p=0 "$video" 2>/dev/null) || fail "$FFPROBE failed on $video"
    [ "$n" = "$2" ] || fail "$video: $n frames, registered $2"
    shift 3
done

mkdir -p "$OUT" || fail "cannot create $OUT"
tools/collect_env.sh >"$OUT/env.txt" || fail "collect_env.sh failed"
sets=
set -- $VIDEOS
while [ $# -ge 3 ]; do
    dir=$OUT/$1
    mkdir "$dir" || fail "cannot create $dir"
    if ! ./build/equivalence_dump "$MEDIA/$1.mp4" "$MODEL" "$dir" "$2" "${FREEZE_COMMIT:-none}" \
        >"$dir/dump.txt" 2>"$dir/dump.log"; then
        cat "$dir/dump.txt" "$dir/dump.log"
        fail "equivalence_dump failed on $1"
    fi
    echo "$1: $(cat "$dir/dump.txt")"
    # The tensors are too large to keep in the repository; their hashes are kept instead.
    (cd "$dir" && sha256sum b.u8 c.u8 r.u8 >tensors.sha256) || fail "sha256sum failed on $1"
    sets="$sets --set $dir $2"
    shift 3
done
tools/collect_env.sh >"$OUT/env_end.txt" || fail "collect_env.sh failed at the end"
status=0
# $sets is split on purpose: --set DIR FRAMES per video; the paths have no spaces.
python3 tools/equivalence_check.py $sets --json "$OUT/check.json" >"$OUT/check.txt" 2>&1 || status=$?
cat "$OUT/check.txt"
case $status in
0 | 1 | 2) ;;
*) status=2 ;; # python3 missing, or a signal: the check did not run
esac
exit $status
