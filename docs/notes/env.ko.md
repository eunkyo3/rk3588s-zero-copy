[English](env.md)

# 환경 (M1-0)

M1-0에서 기록한 보드와 툴체인의 기준선이다. 참고용이다. 비교를 구속하는 값은 [AMENDMENTS](../../prereg/AMENDMENTS.ko.md)에 확정하고(PROTOCOL §14), 모든 실행은 `tools/collect_env.sh`로 자기 환경을 기록한다(PROTOCOL §9.1). 영문판과 다르면 영문판을 따른다.

## 등록

| 단계 | 태그 커밋 | Release `created_at` |
|---|---|---|
| [`prereg-v1`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/prereg-v1) | `87fe874b4f196dafd48f6a175b2d7604625aedba` | 2026-10-30T02:01:36Z |
| `freeze-m1` | 아직 만들지 않음 | |

기준 시각은 `created_at`이다(PROTOCOL §1). 태그가 가리키는 커밋은 PR #1의 머지 커밋이며, 그 커미터 날짜(2026-10-30T01:58:11Z)는 쓰지 않는다.

## 보드 기준선

보드 유휴 상태에서 `tools/collect_env.sh`를 실행한 출력:

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

스크립트가 기록하지 않는 항목:

| 항목 | 값 |
|---|---|
| 부트 펌웨어 | DDR v1.18-9fa84341ce, BL31 v1.49, BL32 v1.19, U-Boot 44f011c4ba (2025-07-17), `/proc/cmdline` 기준 |
| 루트 파일 시스템 | eMMC |
| MPP pkg-config | `rockchip_mpp` 1.3.9 |
| librga pkg-config | `librga` 2.1.0 (라이브러리 자체는 1.10.3으로 보고) |
| RKNN 헤더 | rknn-toolkit2 v2.3.2, `/opt/rknpu2/include` |
| OpenCV | 4.6.0 (Ubuntu `libopencv-core406t64`). 빌드 정보는 M1-2의 첫 B 실행 전에 기록한다(PROTOCOL §14). |
| 컴파일러 | gcc 13.3.0 (Ubuntu 13.3.0-6ubuntu2~24.04.1) |
| 배포판 ffmpeg | `/usr/bin`의 6.1.1-3ubuntu5. 벤치마크는 쓰지 않는다. |

### 참고

- **A76 클러스터 두 개의 최대 클럭이 다르다.** policy4(cpu4–5)는 2304 MHz, policy6(cpu6–7)은 2352 MHz가 최대다. M1-3에서 고정 클럭을 policy별로 설정하고, CPU 시간은 클러스터별로 보고한다.
- **A55 클러스터**는 기본 거버너에서 `min = max = 1800 MHz`로 보고된다.

## 하드웨어 블록

`/sys/kernel/debug/rkrga/hardware`와 플랫폼 장치의 `iommu_group` 링크 기준:

| 블록 | 장치 | 드라이버 | 주소 변환 |
|---|---|---|---|
| RGA3 core 1 (`IM_SCHEDULER_RGA3_CORE0`) | `fdb60000.rga` | rga3 | RK_IOMMU, 그룹 2 |
| RGA3 core 2 (`IM_SCHEDULER_RGA3_CORE1`) | `fdb70000.rga` | rga3 | RK_IOMMU, 그룹 3 |
| RGA2 core 4 (`IM_SCHEDULER_RGA2_CORE0`) | `fdb80000.rga` | rga2 | 자체 RGA_MMU, IOMMU 그룹 없음 |
| NPU | `fdab0000.npu` | RKNPU | IOMMU, 그룹 0 |
| 비디오 디코더 rkvdec2, 코어 2개 | `fdc38100.rkvdec-core`, `fdc48100.rkvdec-core` | mpp_rkvdec2 | IOMMU, 그룹 12와 13 |
| 비디오 디코더 vdpu2 | `fdb50400.vdpu` | mpp_vdpu2 | IOMMU, 그룹 1 |

RGA3와 RGA2는 스케일링 샘플링이 달라서, C 경로는 RGA 작업을 RGA3 코어에 고정한다(스파이크, PROTOCOL §14). MPP가 H.264에 어느 디코더 블록을 쓰는지, 그 버퍼가 DMA32 메모리에서 오는지는 스파이크에서 확인한다.

## 디코더

[ffmpeg-rockchip](https://github.com/nyanmisaka/ffmpeg-rockchip) 커밋 `8b4385e985c0a50bf7d3f96cf3cb6d0b63c53e6c`(브랜치 7.1)를 `tools/setup/build_ffmpeg_rockchip.sh`로 보드에서 빌드해 `/opt/ffmpeg-rockchip`에 설치했다. 이 빌드의 `h264_rkmpp` 옵션과 기본값:

| 옵션 | 기본값 |
|---|---|
| `deint` | true |
| `afbc` | off |
| `fast_parse` | true |
| `buf_mode` | half |

벤치마크가 쓸 옵션은 `freeze-m1` 전에 확정한다(PROTOCOL §14). 이 프로젝트에서는 AFBC를 끈다. RGA가 프레임을 읽고 B가 프레임을 매핑하는데, 둘 다 선형 레이아웃을 전제로 하며 스파이크가 이를 assert한다.

## 모델

[models/README.ko.md](../../models/README.ko.md) 참고. rknn_model_zoo v2.3.2의 YOLOv8n을 rknn-toolkit2 2.3.2로 INT8 변환했고, SHA-256 체크섬이 있다.

## 열 관찰

2026-10-30 보드에서 ffmpeg-rockchip을 빌드하는 동안(`make -j8`, 8코어 모두 사용) `soc-thermal`이 84 °C, `bigcore`가 87 °C까지 올랐다. 커널은 클럭 상한을 A55 클러스터 1416 MHz, A76 클러스터 2016 MHz, NPU 800 MHz로 낮췄다. 즉 당시 냉각 구성으로는 CPU 전부하에서 `soc-thermal`을 트립 포인트 75 °C 아래로 유지하지 못한다. 냉각 구성을 확정하는 M1-3 열 확인 전에 냉각을 바꿔야 한다(PROTOCOL §14). 당시 냉각 구성은 기록하지 않았다.

## 네트워크

보드는 Wi-Fi(`wlan0`)로 접속한다. 이더넷 포트(`eth0`, `eth1`)는 둘 다 아직 링크가 없다. PROTOCOL §3에 따라 RTSP 스트림은 별도 PC에서 유선 이더넷으로 받아야 한다.

## 남은 항목

- **냉각:** M1-3 전에 방열판과 팬을 단다.
- **유선 링크와 송출 서버:** 아직 구성하지 않았다.
- **perf**(M1-2의 CPU 복사 없음 증거에 필요, PROTOCOL §11.4): 설치돼 있지 않다. 벤더 커널 6.1.141용 `linux-tools` 패키지가 Ubuntu에 없다. 커널은 `armv8_pmuv3` PMU를 제공하고, `perf_event_paranoid`는 2다.
