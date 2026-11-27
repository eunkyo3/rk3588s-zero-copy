[한국어](env.ko.md)

# Environment (M1-0)

Baseline of the board and toolchain, taken in M1-0. It is informative: the values that bind the comparison are fixed in [AMENDMENTS](../../prereg/AMENDMENTS.md) (PROTOCOL §14), and every run records its own environment with `tools/collect_env.sh` (PROTOCOL §9.1).

## Registration

| Stage | Tag commit | Release `created_at` |
|---|---|---|
| [`prereg-v1`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/prereg-v1) | `87fe874b4f196dafd48f6a175b2d7604625aedba` | 2026-10-30T02:01:36Z |
| [`freeze-m1`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/freeze-m1) | `2eec964d27545e081a44bc5ead01bbfcd2905a54` | 2026-11-03T06:58:17Z |
| [`amend-A8`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/amend-A8) | `07fe535eefff5d13b77c0ac38ee4e03aa257cac7` | 2026-11-03T01:38:47Z |
| [`amend-A12`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/amend-A12) | `cdb309e4bbe9bf17646526a722e0a1fccb5e9d56` | 2026-11-04T05:30:51Z |
| [`prereg-m2`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/prereg-m2) | `5d9d6d04e50f17d52980dc5b958c7071d7107176` | 2026-11-05T04:45:34Z |
| [`amend-A13`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/amend-A13) | `3880af442b947f9f913578ed8fe00d13aca6b083` | 2026-11-05T07:04:00Z |
| [`freeze-m2`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/freeze-m2) | `3880af442b947f9f913578ed8fe00d13aca6b083` | 2026-11-05T07:04:00Z |
| [`prereg-m4`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/prereg-m4) | `8638685d9ef806a85e5042d7cc10febdd5ae93a9` | 2026-11-11T00:04:33Z |
| [`amend-A14`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/amend-A14) | `67221382e065f3917c28479f1bac08a4ed00d286` | 2026-11-18T00:54:49Z |
| [`freeze-m4`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/freeze-m4) | `67221382e065f3917c28479f1bac08a4ed00d286` | 2026-11-18T00:54:49Z |
| [`prereg-m3`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/prereg-m3) | `dec7c90c5d4e1addc94ebeb2d7ad4d5e8c7670f2` | 2026-11-19T00:21:17Z |
| [`amend-A15`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/amend-A15) | `dec7c90c5d4e1addc94ebeb2d7ad4d5e8c7670f2` | 2026-11-19T00:21:17Z |
| [`freeze-m3`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/freeze-m3) | `dec7c90c5d4e1addc94ebeb2d7ad4d5e8c7670f2` | 2026-11-19T00:21:17Z |
| [`prereg-m5`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/prereg-m5) | `9ac3ec137504b65751c1232ad9add69f3d15f091` | 2026-11-20T04:09:11Z |
| [`amend-A16`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/amend-A16) | `e959531f4d08054527440a57806d1ccd3f7961e6` | 2026-11-23T00:05:11Z |
| [`freeze-m5`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/freeze-m5) | `e959531f4d08054527440a57806d1ccd3f7961e6` | 2026-11-23T00:05:11Z |
| [`prereg-m6`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/prereg-m6) | `5a17d375d9aab77a302ec2295df886ee1257f3fd` | 2026-11-23T23:47:43Z |
| [`amend-A17`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/amend-A17) | `b3381dcfe7e09e43bd9be3528c5977f63a9951cc` | 2026-11-24T05:36:55Z |
| [`freeze-m6`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/freeze-m6) | `b3381dcfe7e09e43bd9be3528c5977f63a9951cc` | 2026-11-24T05:36:55Z |
| [`prereg-m7`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/prereg-m7) | `c40d1a05eadb5b9bb5ed93c592e3d339ca1aa2fb` | 2026-11-25T08:33:00Z |
| [`amend-A18`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/amend-A18) | `bfc6858638966876b280c5094cd828fb32ac38ed` | 2026-11-27T04:20:50Z |
| [`freeze-m7`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/freeze-m7) | `bfc6858638966876b280c5094cd828fb32ac38ed` | 2026-11-27T04:20:50Z |

`created_at` is the reference time (PROTOCOL §1). The tagged commit is the merge of PR #1, whose committer date (2026-10-30T01:58:11Z) is not used. `amend-A8` is the Release of an amendment made before `freeze-m1`, so that the check it registers has a reference time ([A8](../../prereg/AMENDMENTS.md)). Its tagged commit is the merge of PR #12. GitHub gives that commit's time as the Release's `created_at`; the Release was published at 2026-11-03T01:40:12Z. The check started at 02:19:49Z, after both.

`freeze-m1` tags the merge of PR #16, whose `prereg/AMENDMENTS.md` holds every value of PROTOCOL §14 (A1 to A11). Here too GitHub gives the commit's time as `created_at`; the Release was published at 2026-11-03T06:58:50Z. Every comparison, reference and M1.1 run starts after 06:58:17Z, which `tools/check_prereg_order.py` checks.

`amend-A12` is the Release of the first amendment after `freeze-m1`, A12: how the M1.1 designs of PROTOCOL §13 are built and run ([A12](../../prereg/AMENDMENTS.md)). Its tagged commit is the merge of PR #28. Here too GitHub gives the commit's time as `created_at`; the Release was published at 2026-11-04T05:31:44Z. Every run of those designs, and their checks, starts after both.

`prereg-m2` is stage 1 of M2's pre-registration, [prereg/M2.md](../../prereg/M2.md). It tags the merge of PR #35. Here too GitHub gives the commit's time as `created_at`; the Release was published at 2026-11-05T04:49:01Z. Every M2 check run that names `prereg-m2` in its header, and every M2 run, starts after both, which `tools/check_prereg_order.py` checks (M2 §5.1).

`amend-A13` and `freeze-m2` both tag the merge of PR #37, whose `prereg/AMENDMENTS.md` holds every value of [prereg/M2.md](../../prereg/M2.md) §7 (A13). Here too GitHub gives the commit's time as `created_at`; the Releases were published at 2026-11-05T07:07:41Z and 07:08:10Z. Every M2 run starts after both, which `tools/check_prereg_order.py` checks, and the session tool passes `freeze-m2`'s commit to every M2 run (M2 §5.1).

`prereg-m4` is stage 1 of M4's pre-registration, [prereg/M4.md](../../prereg/M4.md). It tags the merge of PR #45, which follows the merge of PR #44 (the exploration it discloses). Here too GitHub gives the commit's time as `created_at`; the Release was published at 2026-11-11T00:06:05Z. Every M4 check run that names `prereg-m4` in its header starts after both, which `tools/check_prereg_order.py` checks (M4 §4).

`amend-A14` and `freeze-m4` both tag the merge of PR #77, whose `prereg/AMENDMENTS.md` holds every value of [prereg/M4.md](../../prereg/M4.md) §8 step 4 (A14). Here too GitHub gives the commit's time as `created_at`; the Releases were published at 2026-11-18T00:57:37Z and 00:57:42Z. Every M4 run and pass starts after both, which `tools/check_prereg_order.py` checks, and names `freeze-m4`'s commit in its header (M4 §4).

`prereg-m3`, `amend-A15` and `freeze-m3` all tag the merge of PR #83, which adds [prereg/M3.md](../../prereg/M3.md) and A15: M3 is registered in one stage (M3 §8). Here too GitHub gives the commit's time as `created_at`; the Releases were published at 2026-11-19T00:28:55Z, 00:28:59Z and 00:29:02Z. Every M3 run and setup record starts after both, which `tools/check_prereg_order.py` checks, and names `freeze-m3`'s commit in its header (M3 §4). The session tool and both scripts read that commit from this table.

`prereg-m5` is stage 1 of M5's pre-registration, [prereg/M5.md](../../prereg/M5.md). It tags the merge of PR #95. Here too GitHub gives the commit's time as `created_at`; the Release was published at 2026-11-20T04:10:17Z. Every M5 check run that names `prereg-m5` in its header starts after both, which M5's own order check under `lib/measure/` checks: the registered `tools/check_prereg_order.py` knows no run kind `m5` (M5 §8).

`amend-A16` and `freeze-m5` both tag the merge of PR #97, which adds A16, the measuring tool under [lib/measure/](../../lib/measure/README.md) and the source of `rkzc` 0.1.1: stage 2 of M5's registration (M5 §8 step 4). Here too GitHub gives the commit's time as `created_at`; the Releases were published at 2026-11-23T00:08:08Z and 00:08:11Z, after the package's Release [`rkzc-v0.1.1`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/rkzc-v0.1.1) at 00:08:05Z (M5 §8 step 2). Every session of M5's sweep starts after both, which `lib/measure/m5_order.py` checks, and the session tool starts a sweep only once `freeze-m5` has its row in this table.

`prereg-m6` is stage 1 of M6's pre-registration, [prereg/M6.md](../../prereg/M6.md). It tags the merge of PR #103, which follows the merge of PR #102 (a note on H1's threshold). Here too GitHub gives the commit's time as `created_at`; the Release was published at 2026-11-23T23:49:30Z. Every M6 check run that names `prereg-m6` in its header starts after both, which `tools/check_prereg_order.py` checks once stage 2 has added run kind `m6` to it (M6 §8).

`amend-A17` and `freeze-m6` both tag the merge of PR #105, which adds A17, M6's arms and its tools: stage 2 of M6's registration (M6 §8 step 4). Here too GitHub gives the commit's time as `created_at`; the Releases were published at 2026-11-24T05:40:51Z and 05:40:56Z. Every run of M6's sweep starts after both, which `tools/check_prereg_order.py` checks. The session tool starts the sweep only once `freeze-m6` has its row in this table, from a checkout whose `src/`, `tools/`, `tests/` and `CMakeLists.txt` equal that commit's.

`prereg-m7` is stage 1 of M7's pre-registration, [prereg/M7.md](../../prereg/M7.md). It tags the merge of PR #111. Here too GitHub gives the commit's time as `created_at`; the Release was published at 2026-11-25T08:34:20Z. Every M7 check run that names `prereg-m7` in its header starts after both, which `tools/check_prereg_order.py` checks once stage 2 has added run kind `m7` to it (M7 §8).

`amend-A18` and `freeze-m7` both tag the merge of PR #113, which adds A18, M7's options and its tools: stage 2 of M7's registration (M7 §8 step 4). Here too GitHub gives the commit's time as `created_at`; the Releases were published at 2026-11-27T04:21:57Z and 04:22:00Z. Every run of M7's sweeps starts after both, which `tools/check_prereg_order.py` checks. The session tool starts a sweep only once `freeze-m7` has its row in this table, from a checkout whose `src/`, `tools/`, `tests/` and `CMakeLists.txt` equal that commit's.

## Board baseline

Output of `tools/collect_env.sh`, board idle:

```
time_utc: 2026-10-30T03:53:38Z
ntp_synchronized: yes
board: FriendlyElec NanoPi R6C
os: Ubuntu 24.04.4 LTS
kernel: 6.1.141 #16 SMP Thu Dec  4 14:51:28 CST 2025
mem_total_kib: 8111652
rknpu_driver: v0.9.8
librknnrt: 2.3.2
librga: 1.10.3 (deb 2.2.0-1)
rga_driver: RGA multicore Device Driver: v1.3.10
mpp: deb 20251211-1
ffmpeg: ffmpeg version 8b4385e9 Copyright (c) 2000-2026 the FFmpeg developers (/opt/ffmpeg-rockchip/bin/ffmpeg)
dma_heaps: cma cma-uncached system system-dma32 system-uncached system-uncached-dma32
cpufreq_policy0: cpus=0 1 2 3 governor=ondemand cur_khz=1800000 min_khz=1800000 max_khz=1800000
cpufreq_policy4: cpus=4 5 governor=ondemand cur_khz=600000 min_khz=408000 max_khz=2304000
cpufreq_policy6: cpus=6 7 governor=ondemand cur_khz=600000 min_khz=408000 max_khz=2352000
devfreq_dmc: governor=dmc_ondemand cur_hz=528000000 min_hz=528000000 max_hz=2112000000
devfreq_fdab0000.npu: governor=rknpu_ondemand cur_hz=1000000000 min_hz=300000000 max_hz=1000000000
thermal_soc-thermal: temp_mc=41615 trip0_mc=75000
thermal_bigcore0-thermal: temp_mc=41615 trip0_mc=115000
thermal_bigcore1-thermal: temp_mc=41615 trip0_mc=115000
thermal_littlecore-thermal: temp_mc=41615 trip0_mc=115000
thermal_center-thermal: temp_mc=40692 trip0_mc=115000
thermal_gpu-thermal: temp_mc=40692 trip0_mc=115000
thermal_npu-thermal: temp_mc=40692 trip0_mc=115000
cooling: unrecorded
ambient_c: unrecorded
```

Not covered by the script:

| Item | Value |
|---|---|
| Boot firmware | DDR v1.18-9fa84341ce, BL31 v1.49, BL32 v1.19, U-Boot 44f011c4ba (2025-07-17), from `/proc/cmdline` |
| Root file system | eMMC |
| MPP pkg-config | `rockchip_mpp` 1.3.9 |
| librga pkg-config | `librga` 2.1.0 (the library itself reports 1.10.3) |
| RKNN headers | rknn-toolkit2 v2.3.2, in `/opt/rknpu2/include` |
| OpenCV | 4.6.0 (Ubuntu `libopencv-core406t64`). The build information is recorded before the first B run of M1-2 (PROTOCOL §14). |
| Compiler | gcc 13.3.0 (Ubuntu 13.3.0-6ubuntu2~24.04.1) |
| Distro ffmpeg | 6.1.1-3ubuntu5 in `/usr/bin`. Not used by the benchmark. |

### Notes

- **A76 clusters differ.** policy4 (cpu4–5) tops out at 2304 MHz and policy6 (cpu6–7) at 2352 MHz. Fixed clocks are set per policy in M1-3, and CPU time is reported per cluster.
  - Later boots showed that policy4's top changes between boots, 2304 or 2352 MHz. M1-3 fixed both A76 clusters at 2208 MHz ([AMENDMENTS A7](../../prereg/AMENDMENTS.md), [calibration.md](calibration.md#fixed-clocks)).
- **A55 cluster** reports `min = max = 1800 MHz` under the default governor.

## Hardware blocks

From `/sys/kernel/debug/rkrga/hardware` and the platform devices' `iommu_group` links:

| Block | Device | Driver | Address translation |
|---|---|---|---|
| RGA3 core 1 (`IM_SCHEDULER_RGA3_CORE0`) | `fdb60000.rga` | rga3 | RK_IOMMU, group 2 |
| RGA3 core 2 (`IM_SCHEDULER_RGA3_CORE1`) | `fdb70000.rga` | rga3 | RK_IOMMU, group 3 |
| RGA2 core 4 (`IM_SCHEDULER_RGA2_CORE0`) | `fdb80000.rga` | rga2 | its own RGA_MMU, no IOMMU group |
| NPU | `fdab0000.npu` | RKNPU | IOMMU, group 0 |
| Video decoder rkvdec2, 2 cores | `fdc38100.rkvdec-core`, `fdc48100.rkvdec-core` | mpp_rkvdec2 | IOMMU, groups 12 and 13 |
| Video decoder vdpu2 | `fdb50400.vdpu` | mpp_vdpu2 | IOMMU, group 1 |

RGA3 scales with a different sampling than RGA2, so the C path pins RGA work to the RGA3 cores (spike, PROTOCOL §14). Which decoder block MPP uses for H.264 and whether its buffers come from DMA32 memory are checked in the spike.

## Decoder

[ffmpeg-rockchip](https://github.com/nyanmisaka/ffmpeg-rockchip) commit `8b4385e985c0a50bf7d3f96cf3cb6d0b63c53e6c` (branch 7.1), built on the board by `tools/setup/build_ffmpeg_rockchip.sh` into `/opt/ffmpeg-rockchip`. Options of `h264_rkmpp` and their defaults in this build:

| Option | Default |
|---|---|
| `deint` | true |
| `afbc` | off |
| `fast_parse` | true |
| `buf_mode` | half |

The benchmark sets `afbc=off` and keeps the other defaults ([AMENDMENTS A10](../../prereg/AMENDMENTS.md)). AFBC stays off in this project: RGA reads the frames and B maps them, and both expect a linear layout, which the spike asserts.

## Model

See [models/README.md](../../models/README.md): YOLOv8n from rknn_model_zoo v2.3.2, converted to INT8 with rknn-toolkit2 2.3.2, with SHA-256 checksums. The files are kept in a release.

## Software frozen at `freeze-m1`

[inventory_m1.txt](inventory_m1.txt) records the board's drivers and boot firmware, the ffmpeg build and the defaults of `h264_rkmpp`'s options, the RKNN headers, the compiler, build tools and flags, the model, and the SHA-256 of every shared library the dynamic linker resolves for `bench_main` (`ldd`), and of `libtbbmalloc`, which libtbb opens at run time, each with its package ([AMENDMENTS A10](../../prereg/AMENDMENTS.md)).

- `tools/inventory.sh` writes it on the board. `tools/stream_session.py` runs the tool at the start of every session and stops if the output differs, or if the file is not the one A10 fixes.
- Before it starts the stream server, the session also checks the SHA-256 of the PC's mediamtx and ffmpeg.
- To check by hand, from the repository on the board: `tools/inventory.sh docs/notes/inventory_m1.txt`.

## Thermal observation

While the board built ffmpeg-rockchip (`make -j8`, all eight cores busy) on 2026-10-30, `soc-thermal` reached 84 °C and `bigcore` 87 °C. The kernel lowered the frequency caps to 1416 MHz on the A55 cluster, 2016 MHz on the A76 clusters and 800 MHz on the NPU. So the cooling as set up then does not keep `soc-thermal` below its 75 °C trip point under full CPU load. The cooling has to change before the M1-3 thermal check, which fixes the cooling configuration (PROTOCOL §14). The cooling at the time was not recorded.

Since 2026-11-02 the board has a heatsink and an always-on mini fan (unbranded, AliExpress), and the room was at 28 °C during the first M1-3 session. During the P_cap runs `soc-thermal` stayed at or below 55.5 °C ([calibration.md](calibration.md)). The thermal check of M1-3 fixes the cooling configuration.

## Network

The board is reached over Wi-Fi (`wlan0`) for administration. The RTSP streams (PROTOCOL §3) use a direct cable from the stream PC to the board's `eth1`, set up on 2026-11-02:

| | Board | Stream PC |
|---|---|---|
| Interface | `eth1`, 1000 Mb/s full duplex | `enp3s0`, 1000 Mb/s full duplex |
| Address | `10.88.0.2/24` | `10.88.0.1/24` |
| NetworkManager profile | `bench-link`: static, never the default route, IPv6 off. The DHCP profile "Wired connection 2" no longer connects on its own. | `bench-link`, the same settings |

- The round-trip time is 0.2 ms.
- **Stream PC:** Rocky Linux 9.7, kernel 5.14.0-611.13.1.el9_7, Intel Core i7-4712MQ (8 threads), 3.5 GB RAM. It is a laptop and also runs a Docker container unrelated to the benchmark.
  - mediamtx v1.21.2 from `stream-server/fetch_mediamtx.sh`.
  - ffmpeg 7.0.2, the static build from johnvansickle.com, in the user's `~/.local/bin`. Its archive SHA-256 is `abda8d77…`.
  - The firewall (`firewalld`, zone `public`) accepts TCP 8554 from `10.88.0.2` only.
  - The four prepared videos are in `~/media/bench`; their hashes match the manifest.
- **Check:** `bench_main --variant none` read 4 channels (videos ① to ④) from `rtsp://10.88.0.1:8554/ch{n}`, with a 3 s warm-up and a 10 s window: a functional check, not a calibration run ([results/check/20261102T083613Z_none_4ch_rtsp](../../results/check/20261102T083613Z_none_4ch_rtsp), with the server's log).
  - Every channel decoded at 30.000 fps, with 0 s drift and no decoder drops.
  - The server log had no errors or warnings.
- The PC's log times are local time (KST, UTC+9); `tools/stream_session.py` starts the server with `TZ=UTC`.
- **Time:** the PC takes its time with chrony from public servers in Korea (stratum 2).
  - Since 2026-11-03 02:19 UTC, the board takes its time from the PC over the wired link: `/etc/systemd/timesyncd.conf.d/50-bench.conf` sets `NTP=10.88.0.1`, `FallbackNTP=ntp.ubuntu.com`, and, since 04:10 UTC, `PollIntervalMaxSec=64`.
  - On the PC, `/etc/chrony.conf` has `allow 10.88.0.2/32`, and the firewall accepts NTP from `10.88.0.2` only.
  - Before, the board used `ntp.ubuntu.com` over Wi-Fi, and the PC's clock minus the board's wandered from +8 ms (PC ahead) to beyond −5 ms. On the PC's time, the sessions recorded 0 to −3 ms; after the poll change, `timesyncd` reported −0.14 ms, read by hand and not recorded ([AMENDMENTS A9](../../prereg/AMENDMENTS.md)).

## Open items

- **Cooling:** fixed by the M1-3 thermal check: a heatsink and an always-on mini fan ([AMENDMENTS A9](../../prereg/AMENDMENTS.md)).
- **perf** (needed for the zero-copy evidence in M1-2, PROTOCOL §11.4): not installed at the time of this baseline, because Ubuntu has no `linux-tools` package for the vendor kernel 6.1.141. It is now at `/usr/bin/perf`, and M1-2 used it ([AMENDMENTS A6](../../prereg/AMENDMENTS.md)). The kernel exposes the `armv8_pmuv3` PMU and `perf_event_paranoid` is 2.
