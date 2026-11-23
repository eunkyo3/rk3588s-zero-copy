[한국어](install.ko.md)

# Installing `rkzc` on a NanoPi R6C

`rkzc` is this repository's zero-copy path as a package: the program `rkzc-detect` with a systemd service, and the library `librkzc` with C++, C and Python interfaces. Step 5 of [design/packaging.md](design/packaging.md); the parts are described in [lib/README.md](../lib/README.md).

**What it was tested on:** one NanoPi R6C with FriendlyElec's Ubuntu 24.04 image (`rom-version` 20251222, kernel 6.1.141, NPU driver 0.9.8). The package refuses a board that is not an RK3588 or lacks the kernel's devices, and says so when the image or the NPU driver is another one.

**What it does not promise:** the numbers of [M1](report/M1.md) on your board, cameras or model.

**What was measured,** on this repository's board, at one point (8 channels of 1080p RTSP, 5 inferences per second each): the [M5 report](report/M5.md).

- **As installed,** the service used 38.5% less process CPU than the benchmark's CPU path (B), and 39.8% more than its zero-copy variant (C). Close to half of its CPU time was spent on the A55 cores.
- **Restricted to the A76 cores,** it used 58.6% less than B and about as much as C. That is one drop-in file, `/etc/systemd/system/rkzc-detect.service.d/affinity.conf`, with the two lines `[Service]` and `CPUAffinity=4-7`, then `sudo systemctl daemon-reload` and a restart of the service.
- **Latency:** the report draws no conclusion. The service's latency is not taken at the same point as the benchmark's, and 3 runs give wide intervals.
- The report says what these numbers mix and what they do not cover.

## Install

```sh
wget https://github.com/eunkyo3/rk3588s-zero-copy/releases/download/rkzc-v0.1.1/rkzc_0.1.1-1_arm64.deb
sudo apt install ./rkzc_0.1.1-1_arm64.deb
```

- **The install needs the network:** it downloads Rockchip's NPU runtime (`librknnrt.so` 2.3.2) from Rockchip's repository and checks its SHA-256. The package cannot carry that file, since its licence does not allow passing it on. If the board is offline, the install still succeeds and says so; run `sudo rkzc-fetch-runtime` later. The download gives up after three tries of 20 s. It uses `wget`, which takes a proxy from the environment (`https_proxy`), not from apt's configuration.
- **It installs no other package** on the tested image: everything it depends on is in the image.
- **Nothing starts by itself.** The service is installed disabled.

## Set up

1. **A model.** The package has none (the models are AGPL 3.0, the package is not):

   ```sh
   sudo rkzc-fetch-model yolov8n      # or yolov10n; into /var/lib/rkzc
   ```

   Your own `.rknn` works too, if it is a YOLOv8 or YOLOv10 export of rknn_model_zoo. Put it in `/var/lib/rkzc`, readable by all: the service cannot read home directories.
2. **Your cameras,** in `/etc/rkzc/detect.toml`: H.264 over RTSP, all of one aspect ratio. The file explains every key.

   ```toml
   sources = [
       "rtsp://192.168.1.10:554/stream1",
       "rtsp://192.168.1.11:554/stream1",
   ]
   ```

   If a URL holds a password, keep the file from other users: `sudo chown root:video /etc/rkzc/detect.toml && sudo chmod 0640 /etc/rkzc/detect.toml`.
3. **Check** before starting anything:

   ```sh
   sudo rkzc-detect --config /etc/rkzc/detect.toml --check
   ```

   It loads the model and opens every source once, and says what failed. Without `sudo` it needs a user in the groups `video` and `render`, which own the devices (`sudo usermod -aG video,render $USER`, then log in again); FriendlyElec's user `pi` is in both.
4. **Start,** now and at every boot:

   ```sh
   sudo systemctl enable --now rkzc-detect
   journalctl -u rkzc-detect -f       # the log: sources opened and lost, counters every minute
   ```

## Use the detections

One JSON object per line comes out of the socket `/run/rkzc/detect.sock`, for members of the group `video`:

```sh
nc -U /run/rkzc/detect.sock
```

```json
{"channel":0,"seq":120,"pts_us":4000000,"time_ms":1795137966573,"width":1920,"height":1080,"detections":[{"class":2,"name":"car","score":0.8411,"x":716.40,"y":451.70,"w":202.38,"h":134.08}]}
```

- `x`, `y`, `w`, `h` are in the source frame's pixels; `x`, `y` is the box's top left corner.
- A reader that stops reading is disconnected; connect again. Drop a last line that has no newline.
- **From Python,** without the service, in your own program (as a user in the groups `video` and `render`):

  ```python
  import rkzc

  def on_frame(frame):
      for d in frame.detections:
          print(frame.channel, d.name, round(d.score, 2), d.x, d.y, d.w, d.h)

  with rkzc.Detector(["rtsp://192.168.1.10:554/stream1"], "/var/lib/rkzc/yolov8n_i8.rknn", on_frame=on_frame) as det:
      det.start()
      det.wait(60)
  ```

- **From C++ or C:** `#include <rkzc/rkzc.h>` or `<rkzc/rkzc_c.h>`, and link `-lrkzc`.

## Remove

```sh
sudo apt purge rkzc
```

It stops the service and removes the package's files, the downloaded runtime, the configuration and the models in `/var/lib/rkzc`. `apt remove` keeps the configuration and the models.

## What is in the package, and its licences

| Part | Where | Licence |
|---|---|---|
| `rkzc-detect`, `rkzc-check`, `librkzc`, headers, the Python module | `/usr` | Apache 2.0 |
| FFmpeg's libraries, built from ffmpeg-rockchip | `/opt/rkzc/lib` | LGPL v3 or later |
| Rockchip's `librknnrt.so` | `/opt/rkzc/lib`, downloaded at install | Rockchip's; not in the package |
| The models | `/var/lib/rkzc`, downloaded by `rkzc-fetch-model` | AGPL 3.0; not in the package |

- `/usr/share/doc/rkzc/copyright` on the board says the same in full, with the FFmpeg source's commit and configure line.
- **The source of the bundled FFmpeg** is the archive `ffmpeg-rockchip-8b4385e9.tar.gz` in the same Release as the package. To use your own build, replace the libraries in `/opt/rkzc/lib` with ones of the same sonames; divert each file first (`sudo dpkg-divert --local --rename --add /opt/rkzc/lib/libavcodec.so.61`, and so on), or dpkg puts its own back at the next upgrade.
- The reasons are in [design/licences.md](design/licences.md).

## Build the package yourself

On a board set up by `scripts/setup_board.sh` (it has the compiler, ffmpeg-rockchip and the RKNN headers):

```sh
lib/packaging/build_deb.sh dist
```

It writes the `.deb`, the FFmpeg source archive and `SHA256SUMS` into `dist/`, and needs no root. `lib/packaging/test_clean_image.sh`, run as root, installs a package inside a chroot of the board's base image, to try it on a clean image without resetting the board.

## Known limits

- **One board and one image were tested.** Another RK3588 board or image may work; the package only says when it sees another image or NPU driver.
- **The install depends on Rockchip's repository** staying where it is. If the tag moves or the file changes, `rkzc-fetch-runtime` fails with a clear message, and the file must be placed by hand.
- **Upgrades from one version of the package to the next** have not been tried: there is one version. The scripts are written for it (a running service is restarted, the runtime, the models and the configuration stay).
- The library's and the program's own limits are in [lib/README.md](../lib/README.md#known-limits).
