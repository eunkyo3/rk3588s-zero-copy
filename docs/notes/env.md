[한국어](env.ko.md)

# Environment (M1-0)

Baseline of the board and toolchain, taken in M1-0. It is informative: the values that bind the comparison are fixed in [AMENDMENTS](../../prereg/AMENDMENTS.md) (PROTOCOL §14), and every run records its own environment with `tools/collect_env.sh` (PROTOCOL §9.1).

## Registration

| Stage | Tag commit | Release `created_at` |
|---|---|---|
| [`prereg-v1`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/prereg-v1) | `87fe874b4f196dafd48f6a175b2d7604625aedba` | 2026-10-30T02:01:36Z |
| `freeze-m1` | not created yet | |

`created_at` is the reference time (PROTOCOL §1). The tagged commit is the merge of PR #1, whose committer date (2026-10-30T01:58:11Z) is not used.

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

The options used by the benchmark are fixed before `freeze-m1` (PROTOCOL §14). AFBC stays off in this project: RGA reads the frames and B maps them, and both expect a linear layout, which the spike asserts.

## Model

See [models/README.md](../../models/README.md): YOLOv8n from rknn_model_zoo v2.3.2, converted to INT8 with rknn-toolkit2 2.3.2, with SHA-256 checksums.

## Thermal observation

While the board built ffmpeg-rockchip (`make -j8`, all eight cores busy) on 2026-10-30, `soc-thermal` reached 84 °C and `bigcore` 87 °C. The kernel lowered the frequency caps to 1416 MHz on the A55 cluster, 2016 MHz on the A76 clusters and 800 MHz on the NPU. So the cooling as set up then does not keep `soc-thermal` below its 75 °C trip point under full CPU load. The cooling has to change before the M1-3 thermal check, which fixes the cooling configuration (PROTOCOL §14). The cooling at the time was not recorded.

## Network

The board is reached over Wi-Fi (`wlan0`). Neither Ethernet port (`eth0`, `eth1`) has a link yet. PROTOCOL §3 requires the RTSP streams to arrive over wired Ethernet from a separate PC.

## Open items

- **Cooling:** add a heatsink and fan before M1-3.
- **Wired link and stream server:** not set up yet.
- **perf** (needed for the zero-copy evidence in M1-2, PROTOCOL §11.4): not installed. Ubuntu has no `linux-tools` package for the vendor kernel 6.1.141. The kernel exposes the `armv8_pmuv3` PMU and `perf_event_paranoid` is 2.
