[English](README.md)

# rk3588s-zero-copy

RK3588S에서 제로카피 영상 파이프라인은 실제로 얼마나 절약하며, 어떤 조건에서 그런가? 이 저장소는 NanoPi R6C에서 수행하는 사전 등록 실험으로 이 질문에 답한다. 코드는 실험을 재현할 수 있도록 여기에 둔다.

이 프로젝트가 공개할 것:
- 사전 등록한 가설마다 판정과 그 근거가 된 측정을 담은 리포트
- 다채널 검출 데모
- 결과를 다시 만드는 스크립트

라이브러리가 아니라 실험이다. 제로카피가 아무것도 절약하지 못한다는 결과도 유효한 답이다.

## 비교 대상

두 파이프라인은 RTSP 입력, 디코더(ffmpeg-rockchip `h264_rkmpp`, DRM PRIME 프레임), 모델(YOLOv8n INT8), 파이프라인 구조가 같다. 디코딩된 프레임에서 NPU 입력까지의 경로만 다르다.

| 변형 | 디코딩된 프레임 → NPU 입력 |
|---|---|
| **B** CPU 복사 | 프레임을 `mmap`하고, OpenCV로 전처리한 뒤, 바인딩된 NPU 입력 버퍼에 `memcpy`로 복사한다([AMENDMENTS A3](prereg/AMENDMENTS.ko.md)) |
| **C** 제로카피 | dma-buf fd → RGA → `rknn_create_mem_from_fd`로 등록한 dma-heap 버퍼 |
| A (참조) | 소프트웨어 디코딩 + B 경로, 1–2채널만 |
| none | 디코딩만 한다. 디코더가 몇 채널까지 감당하는지 찾는 데 쓴다 |

## 구조

### 측정 구성

```mermaid
flowchart LR
    subgraph PC["송출 PC"]
        V["영상 4개<br>1080p30 H.264"] --> P["ffmpeg 송출기<br>재인코딩 없이 반복 재생"]
        P --> M["mediamtx<br>RTSP :8554"]
    end
    subgraph BOARD["NanoPi R6C, RK3588S"]
        BM["bench_main<br>변형 하나, N채널"]
    end
    M -- "RTSP over TCP, ch1~ch32<br>유선 이더넷" --> BM
```

스트림은 별도 PC가 송출한다. 그래서 송출 부담이 보드에 걸리지 않는다. 채널 `chN`은 영상 4개 중 하나를 반복 재생하고, 보드가 접속할 때 송출을 시작한다. 그래서 실행마다 각 영상이 처음부터 재생된다. 서버 로그는 모든 실행의 유효성 검사 항목 중 하나다(PROTOCOL §9.3).

### 파이프라인

모든 변형이 같은 단계를 거친다(PROTOCOL §5.1). A만 VPU 대신 소프트웨어로 디코딩한다.

```mermaid
flowchart LR
    D["디코더 스레드<br>채널당 1개<br>VPU에서 h264_rkmpp"] --> S["샘플러<br>6프레임마다 1개: 5 fps<br>또는 모든 프레임: 30 fps"]
    S --> Q1["Q1<br>2 × 채널 수<br>가장 오래된 것을 버림"]
    Q1 --> PP["전처리 풀<br>스레드 2개<br>슬롯 확보 → prepare"]
    PP --> Q2["Q2<br>깊이 6"]
    Q2 --> W["NPU 워커<br>NPU 코어당 1개, 3개<br>bind → rknn_run<br>→ 후처리"]
    W -. "슬롯 반환" .-> PP
```

- 입력 슬롯 11개를 전처리 풀과 NPU 워커가 함께 쓴다. 프레임을 버리는 큐는 Q1뿐이다.
- NPU 워커는 각각 NPU 코어 하나에 고정된 RKNN 컨텍스트를 하나씩 가진다. Q2에서 아무 슬롯이나 받아 자기 컨텍스트에 바인딩한다.
- 파이프라인과 라이브러리가 만드는 모든 스레드는 A76 코어 4개(cpu4–7)에서 돈다. cpu0의 스레드가 NPU 부하, 온도, 클럭, 프로세스 메모리, 채널별 디코딩 프레임 수를 1초마다 기록한다.
- CPU, DDR, NPU 클럭은 고정한다([AMENDMENTS A7](prereg/AMENDMENTS.ko.md)). 클럭이 고정값 아래로 내려간 실행은 무효다.

### B와 C

`prepare`, `bind`, 입력 슬롯의 메모리만 다르다(PROTOCOL §5.2).

```mermaid
flowchart TB
    F["디코딩된 프레임<br>DRM PRIME dma-buf, NV12 1920×1080"]
    subgraph B["B: CPU 복사"]
        B1["mmap, DMA_BUF sync<br>CPU가 프레임을 읽음"] --> B2["A76 코어에서 OpenCV<br>크기 조정 후 NV12 → RGB"]
        B2 --> B3["CPU 메모리의 슬롯"] --> B4["bind: memcpy로<br>컨텍스트 입력 버퍼에 복사"]
    end
    subgraph C["C: 제로카피"]
        C1["dma-buf fd를<br>RGA로 가져옴"] --> C2["RGA3<br>크기 조정과 NV12 → RGB"]
        C2 --> C3["슬롯: 모든 컨텍스트에<br>등록한 dma-heap 버퍼"] --> C4["bind: rknn_set_io_mem"]
    end
    F --> B1
    F --> C1
    B4 --> N["NPU: rknn_run<br>YOLOv8n INT8, 640×640"]
    C4 --> N
    N --> PO["CPU 후처리<br>박스 디코딩, NMS"]
```

둘 다 640×640 슬롯 가운데에 640×360 이미지를 쓴다. 슬롯 테두리는 시작할 때 한 번 채운다. C에서는 스트리밍 중에 CPU가 픽셀 데이터를 읽거나 쓰지 않는다. perf 프로파일로 이를 확인하며(PROTOCOL §11.4), 확인한 결과는 [AMENDMENTS A6](prereg/AMENDMENTS.ko.md)에 있다.

## 스택

| 계층 | 사용한 것 | 비고 |
|---|---|---|
| 보드 | FriendlyElec NanoPi R6C: RK3588S, Cortex-A76 4개와 Cortex-A55 4개, 3코어 NPU, 8 GB | 방열판과 팬. 냉각 구성은 M1-3 열 확인으로 확정한다 |
| OS | FriendlyElec Ubuntu 24.04, 커널 6.1 | RKNPU 드라이버 0.9.8 |
| 디코딩 | [ffmpeg-rockchip](https://github.com/nyanmisaka/ffmpeg-rockchip) 커밋 `8b4385e9`, Rockchip MPP 위의 `h264_rkmpp` | DRM PRIME 프레임. A는 ffmpeg의 소프트웨어 H.264 디코더를 쓴다. |
| C의 전처리 | librga 1.10.3, im2d API, RGA3 코어 2개 | |
| B와 A의 전처리 | OpenCV 4.6.0(Ubuntu 패키지), 단일 스레드 | [AMENDMENTS A2](prereg/AMENDMENTS.ko.md) |
| 추론 | RKNN 런타임(librknnrt) 2.3.2, C API | 컨텍스트 3개, NPU 코어당 1개 |
| 모델 | rknn_model_zoo v2.3.2의 YOLOv8n, rknn-toolkit2 2.3.2로 INT8 변환 | [models/README.ko.md](models/README.ko.md) |
| 코드 | C++17, CMake, gcc 13.3 | |
| 스트림 | 별도 Linux PC의 mediamtx v1.21.2와 ffmpeg | [stream-server/README.ko.md](stream-server/README.ko.md) |
| 스크립트 | POSIX 셸, Python 3(표준 라이브러리만, `tools/equivalence_check.py`는 numpy도 씀) | 세션, 분석, 검사 |

정확한 버전과 하드웨어 블록은 [docs/notes/env.ko.md](docs/notes/env.ko.md)에 있다.

## 사전 등록

가설, 판정 규칙, 측정 프로토콜은 어떤 측정보다 먼저 등록했다.

- [prereg/HYPOTHESES.ko.md](prereg/HYPOTHESES.ko.md): H1 (8채널에서의 CPU), H2 (5채널에서의 p99 지연), H3 (최대 채널 수)
- [prereg/PROTOCOL.ko.md](prereg/PROTOCOL.ko.md): 조건, 지표, 유효성 규칙, 동등성 기준
- [prereg/AMENDMENTS.ko.md](prereg/AMENDMENTS.ko.md): 이후 변경의 추가 전용 기록

등록은 두 단계다. `prereg-v1`은 가설과 프로토콜을 고정한다. `freeze-m1`은 B/C 비교 실행 전에 구현 설정을 고정한다. 각 단계의 기준 시각은 커밋 날짜가 아니라 해당 GitHub Release의 `created_at`이다.

## 마일스톤

| 마일스톤 | 내용 | 릴리스 |
|---|---|---|
| M1 | B 대 C 파이프라인, 채널 스윕 리포트, 다채널 검출 데모 | `v0.1-m1` |
| M1.1 (선택) | 민감도 및 탐색 실행 | `v0.1.1` |
| M2 | 후처리 최적화, 동일 모델 | `v0.2-m2` |
| M3 | 명령 하나로 재현하는 스크립트 | `v0.3-m3` |
| M4 | 부록: NMS 없는 모델, mAP와 처리량 | `v0.4-m4` |

**현재 상태:** M1-3, 캘리브레이션. 고정 클럭, `P_cap`(초당 194.2회 추론), H2 보조 지점(5채널)을 확정했다([AMENDMENTS A7](prereg/AMENDMENTS.ko.md), [docs/notes/calibration.ko.md](docs/notes/calibration.ko.md)). 유선 송출 서버를 구성했다([docs/notes/env.ko.md](docs/notes/env.ko.md#네트워크)). 다음: 디코딩 전용 상한 `L`, §3의 반복 재생 검사, 열 확인. 앞서 끝난 것: M1-4 동등성. B와 C는 현재 기준([AMENDMENTS A5](prereg/AMENDMENTS.ko.md))으로는 동등하고, 등록 기준으로는 동등하지 않다([docs/notes/equivalence.ko.md](docs/notes/equivalence.ko.md)). C의 입력 경로에는 CPU 픽셀 복사가 없지만, NPU 런타임 안의 작은 복사(B도 같음)가 §11.4 perf 기준을 넘는다([AMENDMENTS A6](prereg/AMENDMENTS.ko.md)).

비교 결과는 `v0.1-m1` 리포트와 함께 공개한다.

## 빌드와 실행

보드에서 한다. MPP, librga, librknnrt는 FriendlyElec 이미지에 들어 있다.

```sh
sudo apt-get update && sudo apt-get install cmake libopencv-dev
tools/setup/build_ffmpeg_rockchip.sh   # build-essential, pkg-config, libdrm-dev 설치 후 ffmpeg-rockchip을 /opt/ffmpeg-rockchip에 설치
tools/setup/fetch_rknn_headers.sh      # RKNN C API 헤더를 /opt/rknpu2/include에 설치
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCODE_COMMIT=$(git rev-parse HEAD) # git 클론에서
cmake --build build -j4
```

다른 컴퓨터에서는 `tools/deploy_board.sh [user@host]`가 작업 트리를 보드에 복사하고 보드에서 빌드한다. 모든 결과 헤더에는 CMake에 `CODE_COMMIT`으로 넘긴 커밋이 들어간다(없으면 `unknown`). `deploy_board.sh`는 이 값을 넣고, 복사한 트리가 커밋과 다르면 `-dirty`를 붙인다.

그다음:
1. 모델을 `models/`에 내려받는다([models/README.ko.md](models/README.ko.md)).
2. PC에서 송출 서버를 시작한다([stream-server/README.ko.md](stream-server/README.ko.md)).
3. 변형 하나를 N채널로 root 권한으로 실행한다(NPU 부하를 debugfs에서 읽기 때문이다).

```sh
sudo build/bench_main --variant C --channels 8 --source 'rtsp://<pc>:8554/ch{n}'
```

`{n}`은 채널 번호로 바뀐다. 실행은 20초 예열한 뒤 120초 측정한다. `summary.csv`, `channels.csv`, `frames.csv`, `samples.csv`, `threads.csv`, `env.json`을 `results/check/<UTC 시각>_C_8ch/`에 쓴다. 옵션은 [src/app/bench_main.cpp](src/app/bench_main.cpp) 맨 위에 있다.

판정에 쓰는 실행은 PROTOCOL §9의 순서, 냉각 대기, 유효성 규칙도 따른다. 지금은 `tools/calib_npu.sh`가 `P_cap` 캘리브레이션에 이를 적용하고, 이후 실행의 세션 도구는 각 마일스톤에서 나온다. 명령 하나로 재현하는 스크립트는 M3에서 나온다.

테스트: 파이프라인 코어는 `build/test_core`, Python 도구는 `python3 tests/test_calib_summary.py`, `tests/test_check_prereg_order.py`, `tests/test_equivalence_check.py`(마지막은 numpy 필요).

## 저장소 구조

| 경로 | 내용 |
|---|---|
| `prereg/` | 가설, 프로토콜, 수정 기록. 영어판이 기준이고 `*.ko.md`는 번역이다. |
| `src/core/` | 공통 파이프라인: 입력, 큐, 슬롯, NPU 컨텍스트, 후처리, 지표, 결과 기록 |
| `src/preprocess/` | 변형별 부분: B(`cpu_copy`), C(`zero_copy_rga`), A(`cpu_sw`) |
| `src/app/bench_main.cpp` | 벤치마크 실행 파일: 변형 하나를 채널 수 하나로 한 번 실행 |
| `tools/` | 클럭 고정, 캘리브레이션, 동등성, 제로카피 근거 도구, 진단 도구, 사전 등록 순서 검사, 보드 준비, 모델 변환, 배포, 환경 기록 |
| `stream-server/` | mediamtx 설정, 영상 준비, 주어진 채널 배정을 검사하는 송출 스크립트 |
| `models/` | 모델 다운로드와 변환. 파일은 Release에 있다. |
| `tests/` | 파이프라인 코어와 Python 도구 3개의 테스트 |
| `docs/notes/` | 마일스톤별 기록: 환경, 스파이크, 전처리, 코어, 동등성, 캘리브레이션 |
| `results/` | 모든 실행의 원자료. 큰 텐서 덤프는 Release에 둔다. 파일마다 헤더에 코드, 모델, 수정 기록 번호가 있다(PROTOCOL §1). |
