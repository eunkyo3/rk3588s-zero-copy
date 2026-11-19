#!/bin/sh
# A manual test of reconnection against a real RTSP server. It runs on a PC the board can reach, not on
# the board: it starts mediamtx and one ffmpeg publisher in a temporary directory, then interrupts them
# on a fixed schedule and prints when. Nothing outside the temporary directory is changed.
#
# Usage: rtsp_interruptions.sh MEDIAMTX VIDEO.mp4      (about 3.5 minutes; port 8554 must be free)
# Meanwhile, on the board, with the PC's address:
#   build-lib/rkzc-jsonl --model MODEL.rknn --retry-max 4000 --seconds 205 rtsp://PC:8554/cam >/dev/null
# and compare its "source lost" and "source opened" lines with this script's times.
#   A  the publisher is killed; the server stays up and closes the stream
#   B  the publisher is frozen: the server is up and sends nothing
#   C  the server is frozen: the connection stays and nothing answers
#   D  the server and the publisher are killed: the connection is refused
set -u
if [ $# -ne 2 ] || [ ! -x "$1" ] || [ ! -f "$2" ]; then
    echo "usage: $0 MEDIAMTX VIDEO.mp4" >&2
    exit 2
fi
MTX=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
VIDEO=$(cd "$(dirname "$2")" && pwd)/$(basename "$2")
dir=$(mktemp -d /tmp/rkzc-rtsp.XXXXXX)
cd "$dir"
cat >mediamtx.yml <<'EOF'
logLevel: info
logDestinations: [file]
logFile: mediamtx.log
api: false
metrics: false
pprof: false
playback: false
rtsp: true
rtspTransports: [tcp]
rtspAddress: :8554
rtmp: false
hls: false
webrtc: false
srt: false
paths:
  cam:
EOF
say() { echo "$(date -u +%H:%M:%S.%N | cut -c1-12) $*"; }
start_server() { "$MTX" mediamtx.yml >/dev/null 2>&1 & mtx=$!; }
start_publisher() {
    ffmpeg -nostdin -hide_banner -loglevel error -re -stream_loop -1 -i "$VIDEO" -c copy -f rtsp \
        -rtsp_transport tcp rtsp://127.0.0.1:8554/cam >>publisher.log 2>&1 &
    pub=$!
}
alive() { kill -0 "$1" 2>/dev/null; }
# After an interruption: whatever exited because of it is started again, and said so.
restore() {
    if ! alive "$mtx"; then start_server; sleep 1; say "   (the server had exited: started again)"; fi
    if ! alive "$pub"; then start_publisher; say "   (the publisher had exited: started again)"; fi
}
cleanup() { kill -CONT "$mtx" "$pub" 2>/dev/null; kill -9 "$pub" "$mtx" 2>/dev/null; cd /; rm -rf "$dir"; }
trap cleanup EXIT INT TERM

start_server; sleep 1; start_publisher
say "server and publisher started"
sleep 25
# SIGKILL: on SIGTERM this ffmpeg went on publishing.
kill -9 "$pub"; say "A: publisher killed (the server stays up)"
sleep 15
start_publisher; say "A: publisher started again"
sleep 25
kill -STOP "$pub"; say "B: publisher frozen (the server is up, no packets)"
sleep 20
kill -CONT "$pub"; say "B: publisher resumed"; sleep 2; restore
sleep 25
kill -STOP "$mtx"; say "C: server frozen (the connection stays, nothing answers)"
sleep 20
kill -CONT "$mtx"; say "C: server resumed"; sleep 2; restore
sleep 25
kill -9 "$pub" "$mtx"; say "D: server and publisher killed (connection refused)"
sleep 15
start_server; sleep 1; start_publisher; say "D: server and publisher started again"
sleep 30
say "end"
