#!/bin/sh
# Start the RTSP server for a channel-to-video assignment (PROTOCOL §3).
# Usage: stream-server/publish.sh <assignment>   (env: MEDIAMTX)
#   <assignment>: one "chN <video.mp4>" per line; "#" starts a comment. Paths are relative to the
#   current directory and may not contain spaces. The assignment is fixed at freeze-m1.
# Each start writes logs/<UTC start time>-<pid>/ with assignment.tsv (channel, video, SHA-256),
# versions.txt, mediamtx.log and console.log (mediamtx and publisher output, including ffmpeg's
# errors). The bench saves them with every run.
# Needs: mediamtx (fetch_mediamtx.sh) and ffmpeg on PATH.
set -eu

VERSION=v1.21.2
here=$(cd "$(dirname "$0")" && pwd)
if [ -x "$here/mediamtx.exe" ]; then
    MEDIAMTX=${MEDIAMTX:-$here/mediamtx.exe}
else
    MEDIAMTX=${MEDIAMTX:-$here/mediamtx}
fi

if [ $# -ne 1 ]; then
    echo "usage: $0 <assignment>" >&2
    exit 2
fi
version=$("$MEDIAMTX" --version)
if [ "$version" != "$VERSION" ]; then
    echo "mediamtx is $version, expected $VERSION (run fetch_mediamtx.sh)" >&2
    exit 1
fi
command -v ffmpeg >/dev/null || { echo "ffmpeg is not on PATH" >&2; exit 1; }
# A second server would replace the running one's channel files.
if [ -f "$here/mediamtx.pid" ] && kill -0 "$(cat "$here/mediamtx.pid")" 2>/dev/null; then
    echo "a server started by publish.sh is already running (pid $(cat "$here/mediamtx.pid"))" >&2
    exit 1
fi

# Check the whole assignment before touching channels/ or logs/.
pairs=
while read -r ch video rest || [ -n "$ch" ]; do
    case "$ch" in
        '' | '#'*) continue ;;
        ch*[!0-9]* | ch) echo "bad channel name: $ch" >&2; exit 1 ;;
        ch[0-9]*) ;;
        *) echo "bad channel name: $ch" >&2; exit 1 ;;
    esac
    if [ -n "$rest" ] || [ ! -f "$video" ]; then
        echo "bad line for $ch: $video $rest" >&2
        exit 1
    fi
    case " $pairs " in
        *" $ch="*) echo "duplicate channel: $ch" >&2; exit 1 ;;
    esac
    pairs="$pairs $ch=$video"
done <"$1"
if [ -z "$pairs" ]; then
    echo "no channels in $1" >&2
    exit 1
fi

log_dir=$here/logs/$(date -u +%Y%m%dT%H%M%SZ)-$$
mkdir -p "$log_dir"
{
    echo "mediamtx $version"
    ffmpeg -hide_banner -version | head -n 1
} >"$log_dir/versions.txt"
rm -rf "$here/channels"
mkdir "$here/channels"
n=0
for pair in $pairs; do
    ch=${pair%%=*}
    video=${pair#*=}
    # Copies, not links: links need extra rights on Windows.
    cp "$video" "$here/channels/$ch.mp4"
    sha=$(sha256sum <"$video")
    printf '%s\t%s\t%s\n' "$ch" "$video" "${sha%% *}" >>"$log_dir/assignment.tsv"
    n=$((n + 1))
done
echo "$n channels, logs in $log_dir"

cd "$here"
echo $$ >mediamtx.pid
MTX_LOGFILE=$log_dir/mediamtx.log exec "$MEDIAMTX" mediamtx.yml >"$log_dir/console.log" 2>&1
