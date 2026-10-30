[한국어](README.ko.md)

# Stream server

Serves the benchmark's RTSP streams (PROTOCOL §3) from a PC that is not the board, over wired Ethernet. Each channel `chN` loops one video. Its publisher starts when the board connects and stops when the board leaves, so every run starts each video from the beginning.

## Setup

1. `stream-server/fetch_mediamtx.sh` downloads mediamtx v1.21.2 for Linux (amd64, arm64) or Windows (Git Bash) and checks its SHA-256.
2. Put `ffmpeg` on `PATH`. It only remuxes (`-c copy`), so any recent build works. Record its version with the run logs.
3. Allow inbound TCP 8554 through the firewall. The server accepts RTSP over TCP only, and only this PC may publish.

## Videos

```sh
stream-server/prepare_videos.sh <out_dir> <source>...
```

Re-encodes each 16:9 source to the benchmark encoding: 1920×1080 H.264 High, 30 fps, no B-frames, GOP 30, 4 Mbps CBR, limited range, no audio. SPS and PPS are repeated before every keyframe (`repeat-headers=1`). Over RTSP, the board's `h264_rkmpp` decoder only gets them in-band; without them it returns no frames at all. It checks the result and appends a line to `<out_dir>/manifest.tsv`. The line records the output and source SHA-256 and the source's size, frame rate, pixel format, color matrix, primaries, transfer and range. The color matrix is kept as it is (PROTOCOL §8).

The benchmark videos, with their sources and licenses, are listed here once they are chosen. That has to happen before M1-3.

## Running

```sh
cat > assignment.txt <<EOF
ch1 videos/a.mp4
ch2 videos/b.mp4
EOF
stream-server/publish.sh assignment.txt
```

The board reads `rtsp://<pc>:8554/chN` over TCP. Each start of `publish.sh` writes `stream-server/logs/<UTC time>-<pid>/` with these files:

- `assignment.tsv`: channel, video and SHA-256
- `versions.txt`: mediamtx and ffmpeg versions
- `mediamtx.log`: publisher starts and stops, and reader connects and disconnects. PROTOCOL §9.3 judges validity from these.
- `console.log`: the publishers' own error output

`publish.sh` checks the whole assignment before it starts, and refuses to start a second server. The channel-to-video assignment is fixed at `freeze-m1`.

A publisher stops 1 s after its last reader leaves. A reader that connects within that second joins the running publisher and does not start from the beginning. Leave more than 1 s between runs and any probe of the streams. The cooldown gate of PROTOCOL §9.2 already leaves at least 60 s.

## Checked

Checked on 2026-10-30, with the server and the reader both on the board, over localhost:

- **Timestamps across loops:** reading `ch1` for 65 s gave 1,951 packets with strictly increasing timestamps. The largest step was 1/30 s, across three loop points of a 20-second clip.
- **Pacing:** the 65 s of stream took 64.9 s, so the publisher sends in real time.
- **On demand:** the publisher started on connect and stopped 1 s after the reader left. The next reader's first frame was identical to the file's first frame.
- **Concurrency:** two channels read at the same time.
- **Publisher failure:** killing a publisher while it was being read logged `[path ch1] runOnDemand command stopped: path destroyed`, the publishing session as `destroyed: torn down` and the reader's session as `destroyed: terminated`. A normal stop logs `stopped: not needed by anyone` instead. The publisher was not restarted.
- **Assignment checks:** a bad channel name, a duplicate channel and an empty assignment were each rejected before anything was written.
- **Single server:** a second `publish.sh` was refused while the first ran.
- **Color tags:** `prepare_videos.sh` kept the source's color tags (BT.709) and range.

The Windows build has not been run yet.
