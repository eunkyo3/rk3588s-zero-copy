[English](README.md)

# rk3588s-zero-copy

RK3588S에서 제로카피 영상 파이프라인은 실제로 얼마나 절약하며, 어떤 조건에서 그런가? 이 저장소는 NanoPi R6C에서 수행하는 사전 등록 실험으로 이 질문에 답한다. 코드는 실험을 재현할 수 있도록 여기에 둔다.

## 비교 대상

두 파이프라인은 RTSP 입력, 디코더(ffmpeg-rockchip `h264_rkmpp`, DRM PRIME 프레임), 모델(YOLOv8n INT8), 파이프라인 구조가 같다. 디코딩된 프레임에서 NPU 입력까지의 경로만 다르다.

| 변형 | 디코딩된 프레임 → NPU 입력 |
|---|---|
| **B** CPU 복사 | 프레임을 `mmap`하고, OpenCV로 전처리한 뒤, `rknn_inputs_set`으로 복사해 넣는다 |
| **C** 제로카피 | dma-buf fd → RGA → `rknn_create_mem_from_fd`로 등록한 dma-heap 버퍼 |
| A (참조) | 소프트웨어 디코딩 + B 경로, 1–2채널만 |

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

**현재 상태:** M1-2. 변형 B, A, C가 보드에서 동작하고, B를 C에 맞췄다([docs/notes/preprocess.ko.md](docs/notes/preprocess.ko.md)). C의 입력 경로에는 CPU 픽셀 복사가 없지만, 런타임이 출력을 CPU로 복사해서 §11.4의 CPU 복사 없음 기준은 충족하지 못했다([AMENDMENTS A6](prereg/AMENDMENTS.ko.md)). 다음: 캘리브레이션(M1-3). 유선 송출 서버와 냉각이 필요하다.

## 하드웨어와 소프트웨어

버전, 하드웨어 블록, 등록 기준 시각은 [docs/notes/env.ko.md](docs/notes/env.ko.md)에, 모델 변환은 [models/README.ko.md](models/README.ko.md)에 있다.

- FriendlyElec NanoPi R6C (RK3588S, 8 GB), FriendlyElec Ubuntu 24.04
- C++, [ffmpeg-rockchip](https://github.com/nyanmisaka/ffmpeg-rockchip) (`h264_rkmpp`), librga (im2d), RKNN C API (librknnrt)
- RTSP 스트림은 별도 PC의 mediamtx가 유선 이더넷으로 송출한다
