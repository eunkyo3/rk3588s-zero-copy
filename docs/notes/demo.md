[한국어](demo.ko.md)

# Detection demo (M1-7)

The demo video of `v0.1-m1`: eight channels in a 3 × 3 grid, each with the boxes that variant C detected, and a ninth tile that says what the video is.

## What it shows, and what it does not

- **The detections are C's:**
  - RGA3 letterboxes the decoded frame into the input buffer the NPU reads, with no CPU pixel copies (A6);
  - YOLOv8n INT8 runs on the NPU;
  - `tools/demo_detect` runs this path and the pipeline's post-processing.
- **5 fps per channel:** 1 frame in 6, as the main condition samples. A box stays up until that channel's next inference, as on a live screen.
- **Rendered offline:** `tools/demo_render.py` draws the boxes on the source videos after the detections were made. So the video says nothing about timing, CPU or latency; the benchmark measures those ([comparison.md](comparison.md)).
  - `demo_detect` runs one frame at a time on NPU core 0. It does not use the pipeline's threads, queues or slots.
  - It shares the core's code unchanged: the decoder (`h264_rkmpp`), C's backend, the NPU contexts and the post-processing.
- **Channels:** 1 to 8 of the M1 assignment ([stream-server/assignment_m1.txt](../../stream-server/assignment_m1.txt)), so videos ① to ④ twice.
  - Channels 5 to 8 start 10 s into their video, so that no two tiles show the same frame at the same moment.
  - A video starts again at its end, as the stream server's do.
- **Not a measurement:** the files carry no run kind, and they are kept out of `results/`.

## Files

| File | What |
|---|---|
| `tools/demo_detect.cpp` | C's detections on a video file, every 6th frame, as JSONL: a header (video, model hash, preprocessing, letterbox band, labels, commit), then per sampled frame its index, PTS and boxes in model input coordinates |
| `tools/demo_render.py` | the grid video, 1920 × 1080, 30 fps, 20 s, H.264 (libx264) |
| `tools/demo.sh` | both, on the board as root: it checks each video's SHA-256 against its manifest, then writes everything to `docs/report/demo/` |
| `docs/report/demo/` | the detections and logs, a still frame (`demo_m1.jpg`) and the video's SHA-256 |

- The video is too large for the repository, which ignores `*.mp4`, so it is attached to the `v0.1-m1` Release.
- **The run of 2026-11-04:** `tools/demo.sh` on the board from commit 1eebc77, 03:43 to 03:45 UTC, after the M1.1 runs. The video is 30 MB, and its SHA-256 is in `docs/report/demo/demo_m1.mp4.sha256`.
- **Tests:** `tests/test_demo_render.py` covers the assignment, the detections file, the box mapping, where a box's label goes, and which detections a channel shows for each frame (its start, the hold and the restart).

## Videos and licenses

| | Video | Creator | License |
|---|---|---|---|
| ① | [Pexels 36330861](https://www.pexels.com/video/urban-crosswalk-scene-with-busy-traffic-36330861/), urban crosswalk | SHOX ART | [Pexels License](https://www.pexels.com/license/) |
| ② | [Pexels 29130409](https://www.pexels.com/video/vibrant-city-street-with-busy-traffic-29130409/), city street | Paul Bill | Pexels License |
| ③ | [Pexels 29969146](https://www.pexels.com/video/night-traffic-and-pedestrians-in-istanbul-29969146/), Istanbul at night | Sururi Ballıdağ | Pexels License |
| ④ | [Pixabay 180386](https://pixabay.com/videos/intersection-cars-pedestrian-night-180386/), Osaka at night | Kanenori | [Pixabay Content License](https://pixabay.com/service/license-summary/) |

- **Checked on 2026-11-04 (plan R14), before publishing:**
  - Both licenses allow modifying the videos and publishing the result. Neither requires credit; the demo gives it anyway.
  - **Pexels**, among others: do not sell unaltered copies, do not redistribute the videos on other stock platforms, do not imply endorsement by the people or brands shown, and do not show identifiable people in a bad light or in an offensive way.
  - **Pixabay**, among others: do not distribute the content on a standalone basis, that is with no creative effort applied. Combining it into a new work, as the grid with its boxes does, is not standalone use. Do not use it in an immoral or illegal way, especially content with recognisable people, nor in a misleading or deceptive way.
  - The demo does none of these: it is a non-commercial grid of downscaled clips with drawn boxes.
- **The footage stays under its licenses.** The repository's Apache License 2.0 covers the code. It does not cover the footage in the demo video, nor in its still frame, `docs/report/demo/demo_m1.jpg`. The README says so, and so will the Release notes.
