[한국어](reproduce.ko.md)

# Reproducing M1's H1 point with one command

Two scripts set up a NanoPi R6C from FriendlyElec's image and run M1's H1 point on it: B and C at 8 channels in the main condition, 3 repetitions each. At the end they compare the result with M1's values. M3 uses them on a reset board ([prereg/M3.md](../../prereg/M3.md)).

- **`scripts/setup_board.sh`:** installs and configures what the runs need, builds the code, and records the board.
- **`scripts/reproduce_m1.sh`:** builds the code, runs one session of `tools/stream_session.py`, and prints the comparison (`tools/m3_report.py`).

## What you need

- **The board:** a NanoPi R6C with FriendlyElec's Ubuntu 24.04 image on its eMMC. M3 used `rom-version` 20251222, kernel 6.1.141 ([env.md](env.md)).
  - A heatsink with an always-on fan (A9).
  - The board online, by Wi-Fi or its other Ethernet port, for the packages and downloads.
- **The stream PC:** set up as [stream-server/README.md](../../stream-server/README.md) says, with M1's videos in `~/media/bench` and their `manifest.tsv`.
  - **The bench link:** wired to the board's `eth1`. The PC is `10.88.0.1/24`; the board becomes `10.88.0.2/24`.
  - **Network services:** the PC serves NTP to the board (chrony), and its firewall accepts RTSP and NTP from `10.88.0.2` ([env.md](env.md)).
  - **The PC's user:** `tools/stream_session.py` logs in as `eunkyo@10.88.0.1`. With another user, change `SSH` there and `PC_USER` in `setup_board.sh`.
- **A key for the stream PC:** the board logs in to the PC with it, to start and stop the stream server.
  - Make one with `ssh-keygen -t ed25519 -N "" -f bench_stream`.
  - Add `bench_stream.pub` to the PC's `~/.ssh/authorized_keys`.

## Steps

On the board, as the user `pi`:

```sh
git clone https://github.com/eunkyo3/rk3588s-zero-copy.git ~/rk3588s-zero-copy
cd ~/rk3588s-zero-copy
sudo -v                                         # sudo's password once, before the jobs below
sudo setsid nohup scripts/setup_board.sh --stream-key ~/bench_stream >~/setup.log 2>&1 &
# when ~/setup.log ends with "setup: ok":
sudo COOLING="heatsink + always-on fan" AMBIENT_C=25 setsid nohup scripts/reproduce_m1.sh after >~/reproduce.log 2>&1 &
```

- **The commit:** M3's runs use the code registered at `freeze-m3`, from a checkout that also has `freeze-m3`'s row in [env.md](env.md#registration). That row comes in the commit after the tag, so clone `main`, not the tag. Both scripts check that `src/`, `tools/`, `scripts/`, `tests/` and `CMakeLists.txt` equal `freeze-m3`'s.
- **The repository must be at `/home/pi/rk3588s-zero-copy`.** M1's inventory names that path in the build's include paths, and the session compares them (M3 §4).
- **Logs outside the repository:** a file in it would make the commit read `-dirty` in every record.
- **`setsid nohup`** keeps each job running if the login ends. A setup cut short cannot be resumed: the board must be reset again (M3 §5).
- **`COOLING` and `AMBIENT_C`** go into every record. `AMBIENT_C` is the room temperature, as read from a thermometer.
- **The setup** took about 9 minutes in a chroot of the image (A15). The steps that need the bench link and the stream PC come first, so a problem with them shows within a minute.
- **A session takes about 45 minutes:**
  - 10 minutes idle, for the baselines;
  - 6 runs of 140 s;
  - a cooldown gate before each run.
- **The comparison** is printed at the end of `~/reproduce.log`. The session's records are in `results/m3/<UTC>_m3_after/`.
- **Before a reset:** copy the board's `results/` off it, since the reset wipes them. M3 does this for its `m3_before` session (M3 §3).

## What `setup_board.sh` does

1. **The bench link and time:**
   - a NetworkManager connection `bench-link` on `eth1`, `10.88.0.2/24`, never the default route;
   - `systemd-timesyncd` taking its time from `10.88.0.1`.
2. **The stream PC:**
   - the key, and the PC's host key;
   - `media/bench/manifest.tsv` from the PC, checked against its SHA-256;
   - up to two minutes for the clock to synchronise.
3. **Packages:**
   - `apt-get update`, `apt-get upgrade`, then the build tools, OpenCV, binutils and wget, then `apt-get clean`. apt keeps changed configuration files and waits for another apt run to finish.
   - Then it checks what M3 §4 pins and the image provides: the kernel, librga and MPP, against [inventory_m1.txt](inventory_m1.txt).
4. **NPU runtime:** `librknnrt.so` 2.3.2 from rknn-toolkit2 into `/usr/lib`, checked against its SHA-256. The RKNN headers go into `/opt/rknpu2/include` ([tools/setup/fetch_rknn_headers.sh](../../tools/setup/fetch_rknn_headers.sh)).
   - The image does not include librknnrt. It does include MPP and librga.
5. **ffmpeg-rockchip** at the commit M1 used, into `/opt/ffmpeg-rockchip` ([tools/setup/build_ffmpeg_rockchip.sh](../../tools/setup/build_ffmpeg_rockchip.sh)). Its version string is fixed at M1's 8-character hash.
6. **The model** `yolov8n_i8.rknn` from the Release [`data-model-yolov8n_i8-04a76611`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/data-model-yolov8n_i8-04a76611), checked against its SHA-256.
7. **The build:** CMake, Release, with the commit from git.
8. **The record,** in `results/m3/<UTC>_setup/`:
   - `setup.csv`: the result, whether the board was fresh, whether the code equals `freeze-m3`'s, each step's time;
   - `setup.log`: every command and its output;
   - `dpkg.tsv`: the packages and their versions;
   - `inventory.txt` and `inventory.diff`: the board against [inventory_m1.txt](inventory_m1.txt);
   - `inventory_pinned.txt`: the pinned keys that differ, if any.

- **A fresh board only:** it stops before changing anything on a board that was set up before, even in part.
  - Before its first change it writes `/var/lib/rk3588s-bench/setup-started`, which makes the board not fresh.
  - `--not-fresh` overrides this, for development.
  - `--no-services` leaves out steps 1 and 2, to test the rest in a chroot.
- **M3's record or a development one:** the record is M3's (run kind `m3`, `results/m3/<UTC>_setup/`) only when four things hold:
  - the board is fresh;
  - the setup runs with its services;
  - `freeze-m3` has its row;
  - the code equals `freeze-m3`'s.

  Otherwise it is a development record, of run kind `check`, in `results/check/<UTC>_m3_setup/`.
- **It stops at the first failure.** The record says in which step.

## What `reproduce_m1.sh` does

- **Builds** the code: with the commit from git in a clone, or as `tools/deploy_board.sh` left it in a copy.
- **Runs** one protocol session of `tools/stream_session.py`: the sweep `m3_before` or `m3_after` (M3 §3). The session:
  - records the board's inventory;
  - stops if a key that M3 §4 pins differs from M1's;
  - fixes the clocks (A7);
  - starts the stream server on the PC;
  - runs B and C in PROTOCOL §9.1's order, with its validity rules;
  - stops the server.
- **`smoke`** as a second argument makes it a quick functional test: 60 s idle, 10 s windows, 30 s gate. Its records go to `results/check/` and count for nothing.
- **Prints** `tools/m3_report.py --session` for the session.
