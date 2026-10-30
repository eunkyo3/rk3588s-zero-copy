#!/bin/sh
# Re-encode source videos to the benchmark encoding (PROTOCOL §3): 1920x1080 H.264 High, 30 fps,
# no B-frames, GOP 30, 4 Mbps CBR, limited range signalled, no audio. The color matrix, primaries
# and transfer are kept and signalled as in the source (PROTOCOL §8), and recorded.
# SPS/PPS are repeated before every keyframe: over RTSP, h264_rkmpp gets them only in-band.
# Usage: stream-server/prepare_videos.sh <out_dir> <source>...   (env: FFMPEG, FFPROBE)
#   writes <out_dir>/<name>.mp4 and appends one line per video to <out_dir>/manifest.tsv
set -eu

FFMPEG=${FFMPEG:-ffmpeg}
FFPROBE=${FFPROBE:-ffprobe}

if [ $# -lt 2 ]; then
    echo "usage: $0 <out_dir> <source>..." >&2
    exit 2
fi
out=$1
shift
mkdir -p "$out"
manifest=$out/manifest.tsv
if [ ! -f "$manifest" ]; then
    printf 'name\tsha256\tsource\tsource_sha256\tsource_size\tsource_fps\tsource_pix_fmt\tsource_color_range\tcolor_space\tcolor_primaries\tcolor_transfer\tduration_s\n' >"$manifest"
fi

# One stream property of the first video stream, "unknown" if the file does not set it.
probe() {
    v=$("$FFPROBE" -v error -select_streams v:0 -show_entries "stream=$2" -of default=nw=1:nk=1 "$1")
    echo "${v:-unknown}"
}
sha256() {
    s=$(sha256sum <"$1")
    echo "${s%% *}"
}

for src in "$@"; do
    name=$(basename "${src%.*}")
    dst=$out/$name.mp4
    if cut -f1 "$manifest" | grep -qx "$name"; then
        echo "$name is already in $manifest" >&2
        exit 1
    fi
    w=$(probe "$src" width)
    h=$(probe "$src" height)
    case "$w$h" in
        *[!0-9]*) echo "$src: cannot read the frame size" >&2; exit 1 ;;
    esac
    # The pipeline letterboxes 1920x1080 into a 640x360 band (PROTOCOL §5.2), so only 16:9 sources.
    if [ $((w * 9)) -ne $((h * 16)) ]; then
        echo "$src: ${w}x${h} is not 16:9" >&2
        exit 1
    fi

    # Signal the source's color tags explicitly; scaling must not change the matrix.
    tags=
    space=$(probe "$src" color_space)
    primaries=$(probe "$src" color_primaries)
    transfer=$(probe "$src" color_transfer)
    [ "$space" = unknown ] || tags="$tags -colorspace $space"
    [ "$primaries" = unknown ] || tags="$tags -color_primaries $primaries"
    [ "$transfer" = unknown ] || tags="$tags -color_trc $transfer"

    # $tags is unquoted on purpose: it holds option words without spaces.
    # shellcheck disable=SC2086
    "$FFMPEG" -hide_banner -loglevel error -y -i "$src" -an -sn -dn \
        -vf "fps=30,scale=1920:1080:out_range=tv,setsar=1,format=yuv420p" \
        -c:v libx264 -preset medium -profile:v high -bf 0 -g 30 -keyint_min 30 -sc_threshold 0 \
        -b:v 4M -minrate 4M -maxrate 4M -bufsize 4M -x264-params nal-hrd=cbr:repeat-headers=1 \
        -color_range tv $tags -movflags +faststart "$dst"

    # Check the result rather than trust the options.
    got=$(probe "$dst" codec_name),$(probe "$dst" width)x$(probe "$dst" height),$(probe "$dst" r_frame_rate),$(probe "$dst" has_b_frames),$(probe "$dst" color_range)
    if [ "$got" != "h264,1920x1080,30/1,0,tv" ]; then
        echo "$dst: unexpected encoding $got" >&2
        exit 1
    fi
    for tag in color_space color_primaries color_transfer; do
        if [ "$(probe "$src" $tag)" != unknown ] && [ "$(probe "$dst" $tag)" != "$(probe "$src" $tag)" ]; then
            echo "$dst: $tag is $(probe "$dst" $tag), source has $(probe "$src" $tag)" >&2
            exit 1
        fi
    done

    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$name" "$(sha256 "$dst")" "$(basename "$src")" "$(sha256 "$src")" \
        "${w}x${h}" "$(probe "$src" r_frame_rate)" "$(probe "$src" pix_fmt)" "$(probe "$src" color_range)" \
        "$space" "$primaries" "$transfer" \
        "$("$FFPROBE" -v error -show_entries format=duration -of default=nw=1:nk=1 "$dst")" >>"$manifest"
    echo "$dst"
done
