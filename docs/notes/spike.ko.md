[English](spike.md)

# 스파이크: fd 체인 (M1-0)

**결과: C2 경로가 동작한다.** 디코딩된 DRM PRIME 프레임이 RGA를 거쳐 dma-heap 슬롯에 기록되고, `rknn_create_mem_from_fd`로 RKNN에 등록된 그 슬롯에서 NPU가 실행된다. 슬롯은 컨텍스트 3개 모두에 등록됐다. 한 컨텍스트에서 해제해도 다른 컨텍스트에 영향이 없었다. PROTOCOL §5.4의 대체 경로는 필요 없다. 스파이크는 2026-10-30에 상한 15시간 안에 끝났다.

이 결과로 확정되는 PROTOCOL §14의 값은 [AMENDMENTS A1](../../prereg/AMENDMENTS.ko.md)에 기록했다. 영문판과 다르면 영문판을 따른다.

## 구성

- **프로그램:** [`tools/spike_fd_chain.cpp`](../../tools/spike_fd_chain.cpp)와 공유 후처리 [`src/core/yolov8_post.cpp`](../../src/core/yolov8_post.cpp). 채널 1개, 스레드 1개. 프레임 `i`는 슬롯 `i mod 11`과 컨텍스트 `i mod 3`을 쓴다.
- **보드:** [env.ko.md](env.ko.md)와 같음, 기본 거버너. PROTOCOL §5에 따라 프로세스를 A76 코어(cpu4–7)에 고정했다.
- **입력:** rknn_model_zoo v2.3.2의 `bus.jpg`(SHA-256 `fb4914d1…`)로 보드에서 만든 20초 테스트 클립. 벤치마크 영상은 아니다. 인코딩은 벤치마크와 같다: 1920×1080 H.264 High, 30 fps, B프레임 없음, GOP 30, 4 Mbps CBR, 600프레임. 스트림에는 BT.709로 표시돼 있지만, 아래의 RGA와 OpenCV는 둘 다 BT.601로 변환한다.

  ```sh
  ffmpeg -f lavfi -i "color=c=0x404040:s=1920x1080:r=30:d=20" -loop 1 -framerate 30 -i bus.jpg \
    -filter_complex "[1:v]scale=-2:1080[fg];[0:v][fg]overlay=x=(main_w-overlay_w)*(0.5+0.4*sin(2*PI*t/10)):y=0:shortest=1,format=yuv420p" \
    -c:v libx264 -preset medium -profile:v high -bf 0 -g 30 -keyint_min 30 -sc_threshold 0 \
    -b:v 4M -minrate 4M -maxrate 4M -bufsize 4M -x264-params nal-hrd=cbr \
    -color_range tv -colorspace bt709 -color_primaries bt709 -color_trc bt709 -t 20 spike_bus_1080p30.mp4
  ```

- **실행:** debugfs 파일을 읽어야 하므로 root로 실행한다.

  ```sh
  cmake -S . -B build && cmake --build build -j4
  sudo taskset -c 4-7 ./build/spike_fd_chain spike_bus_1080p30.mp4 models/yolov8n_i8.rknn <out_dir> 300 [rga_core_mask]
  ```

- **로그:** [spike/spike_run.log](spike/spike_run.log)(300프레임, 기본 마스크), [spike/spike_rga_core1.log](spike/spike_rga_core1.log)(120프레임, 마스크 = RGA3 core 1만)

## 결과

| 확인 항목 | 결과 |
|---|---|
| 프레임 레이아웃 | 객체 1개, 레이어 1개, NV12, 플레인 2개, modifier `DRM_FORMAT_MOD_LINEAR`. 객체 크기 4,177,920바이트. `wstride` = pitch = 1920. `hstride` = UV 오프셋 / pitch = 2,088,960 / 1920 = 1088. |
| fd 안정성 | 300프레임 동안 서로 다른 dma-buf inode 6개, fd 번호 6개. 100프레임 이후 새 버퍼 없음. 버퍼 그룹 한도는 38(아래 참고). |
| 디코더 버퍼 | exporter가 `system-dma32`이므로 **CPU 캐시가 되고 DMA32 영역(4 GiB 아래)에서 할당된다**. 주소 범위는 힙의 성질에서 나온 것이고 직접 재지는 않았다. `fdc38100.rkvdec-core`(rkvdec2 코어 0)와 RGA에 연결돼 있다. CPU 읽기 속도는 2.7–5.4 GB/s다. 비교하면 캐시되지 않는 슬롯은 264 MB/s, `malloc` 메모리는 9.9 GB/s다. |
| 슬롯 버퍼 | `system-uncached-dma32`. NPU와 RGA에 연결 |
| 컨텍스트 3개 등록 | 모든 슬롯이 컨텍스트 3개에 등록됐다. 같은 슬롯을 컨텍스트 0, 1, 2에서 실행한 출력이 비트 단위로 같았다. |
| 한 컨텍스트에서 해제 | 컨텍스트 0에서 그 슬롯의 메모리 객체를 `rknn_destroy_mem`으로 해제했다. 이어서 컨텍스트 1과 2가 다른 슬롯을 실행해 출력을 바꾼 뒤, 해제한 슬롯을 다시 실행했다. 출력은 이전과 비트 단위로 같았다. 컨텍스트 0은 다른 슬롯을 계속 실행했다. |
| RGA 코어 마스크 | 마스크 RGA3 core 0 \| core 1일 때 RGA3 core 0이 최대 부하 7%, RGA3 core 1과 RGA2는 0%였다. 마스크를 RGA3 core 1만으로 하자 부하가 core 1로 옮겨갔다(7%). 즉 `im_opt_t.core`가 지켜진다. 이 실행에서는 동기 제출 스레드 하나의 작업이 모두 core 0으로 갔다. |
| 숨은 입력 변환 | native 입력은 INT8 NHWC(zp −128, scale 1/255)이고, 슬롯은 UINT8 NHWC로 바인딩한다. 번갈아 실행한 15라운드×20회 기준 `rknn_run` 1회당 프로세스 CPU 시간의 중앙값(범위)은 UINT8 바인딩 2.47 ms(2.36–2.58), INT8 pass-through 바인딩 2.50 ms(2.37–2.53)였다. CPU가 변환하려면 1.2 MB의 캐시되지 않는 슬롯을 읽어야 하는데, 264 MB/s에서 그것만 약 4.6 ms로 실행 1회의 CPU 시간 전체보다 길다. **uint8→int8 변환은 CPU에서 하지 않는다.** |
| 출력 레이아웃 | native 출력은 NC1HWC2다. NCHW INT8로 바인딩하면 실행당 CPU 2.5 ms다. native NHWC로 바인딩하면 실행당 CPU 15.6 ms, 실제 시간 34 ms다. native NC1HWC2로 바인딩하면 `rknn_run`이 실패한다("unsupported tensor dtype", 로그에 있음). 파이프라인은 NCHW INT8로 바인딩한다. |
| 프레임당 시간 (300프레임 전체 평균, 1채널) | RGA `improcess`(`IM_SYNC`) 1.80 ms, bind 0.09 ms, `rknn_run` 17.7 ms(NPU 코어 1개), 후처리 0.89 ms |
| 검출, 마지막 프레임 | bus 0.86; person 0.88, 0.73, 0.66, 0.27 |

![결과: 마지막 프레임의 슬롯과 검출](spike/spike_result.jpg)

이미지는 NPU가 읽은 슬롯 그대로다. 640×360 영상 영역과 114 회색 레터박스 테두리가 보인다. 검은 띠는 `bus.jpg`에 원래 있는 것이다.

### 참고

- **디코더 버퍼 그룹:** `buf_mode=half`에서 ffmpeg-rockchip은 MPP 버퍼 그룹 한도를 20(H.264) + 10 + `extra_hw_frames`(8) = 38개로 둔다(`rkmppdec.c`. 한도는 조회할 수 없어서 소스에서 가져왔다). 이 모드에서 `hw_frames_ctx->initial_pool_size`는 0이다. 스파이크는 RGA 직후 프레임을 해제해서 버퍼가 6개만 돈다. 파이프라인은 Q1에 프레임을 쌓으므로 더 많이 돈다. MPP 버퍼 보유 시간은 M1-1에서 잰다.
- **코드상의 DMA32와 캐시 플래그:** `hwcontext_rkmpp.c`가 기본으로 `MPP_BUFFER_FLAGS_DMA32`를 설정하고, `rkmppdec.c`가 `MPP_BUFFER_FLAGS_CACHABLE`을 더한다. exporter 이름과 일치한다.
- **CPU 시간은 고정 여부에 따라 달라진다.** 고정하지 않은 두 번의 실행에서 같은 비교가 두 바인딩 모두 실행당 2.4–5.8 ms로 퍼졌다. 런타임 스레드가 A55와 A76 코어 사이를 옮겨 다니는 것이 가장 유력한 원인이다.
- **`rknn_run` 안의 CPU 시간:** NCHW 출력일 때의 실행당 2.5 ms는 더 쪼개 보지 않았다. B와 C는 출력을 같은 방식으로 바인딩하므로(PROTOCOL §5.1) 이 비용을 공유한다. M1-2의 perf 증거(PROTOCOL §11.4)는 `rknn_run` 아래의 복사 심볼을 센다. 출력 처리가 그 집계에 복사로 잡히면 두 변형 모두에 나타난다.
- **RGA와 OpenCV 비교:** 마지막 프레임에서 RGA 영상 영역과 OpenCV 결과의 평균 절대 오차는 4.76이었다. OpenCV는 전체 프레임을 변환(BT.601 제한 범위)한 뒤 `INTER_LINEAR`로 리사이즈했다. 이는 M1-4 텐서 기준(MAE ≤ 2)보다 크다. B의 실제 순서는 리사이즈 먼저, 색 변환 다음이다. M1-2에서 색 변환과 보간을 맞춰 B와 C를 기준 안으로 들여와야 한다.
- **스파이크에서 CPU가 픽셀에 접근하는 곳:** 디버그 확인에서만이다. 각 디코더 버퍼의 첫 읽기(대역폭 측정), 마지막 프레임의 RGA/OpenCV 비교, 결과 이미지, 초기화 시 한 번 하는 테두리 `memset`이 그것이다. 체인 자체(`importbuffer_fd`, `improcess`, `rknn_set_io_mem`, `rknn_run`)는 픽셀 메모리를 매핑하지 않는다. PROTOCOL §11.4의 프로파일 증거는 M1-2에서 만든다.

### M1-1에서 이어서 할 일

- dma-buf inode를 키로 하는 RGA 핸들 캐시는 가져온 버퍼를 모두 살려 둔다. 지금은 프레임 크기가 바뀔 때만 비운다. M1-1에서는 디코더가 info change를 알릴 때도 비우고, 크기를 버퍼 그룹 한도로 제한한다.
- 프레임의 fd를 `dup` 없이 쓴다. 이는 `improcess`가 동기이고 프레임 해제 전에 실행될 때만 안전하며, PROTOCOL §5가 그렇게 돼 있다.
- `rknn_create_mem` 출력을 명시적 `rknn_mem_sync` 없이 `virt_addr`로 읽는다. 검출은 정확하고, `DISABLE_FLUSH_*` 플래그를 쓰지 않으므로 런타임이 출력 캐시를 무효화할 것이다(계획 M1-1). M1-1에서 `rknn_outputs_get`으로 읽은 출력과 대조해 확인한다.
