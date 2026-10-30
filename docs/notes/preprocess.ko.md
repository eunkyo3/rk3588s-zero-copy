[English](preprocess.md)

# 전처리: 변형 B와 A, C와의 정렬 (M1-2)

M1-2는 C(M1-1) 옆에 CPU 복사 변형 B와 소프트웨어 디코딩 기준 A를 추가한다. 또 B의 출력을 C에 맞추고, PROTOCOL §11.4의 CPU 복사 없음 증거를 모은다. 그 과정에서 나온 발견이 사전 등록한 설계를 바꿨고, 각 변경은 AMENDMENTS에 기록했다. 영문판과 다르면 영문판을 따른다.

| 발견 | 수정 기록 |
|---|---|
| B의 첫 실행 전에 OpenCV 빌드를 확정한다 | A2 |
| 등록된 B의 bind인 `rknn_inputs_set`을 쓰면 `rknn_set_io_mem`으로 바인딩한 출력에 아무것도 써지지 않는다 | A3 |
| RGA3의 3:1 스케일러는 OpenCV보다 한 픽셀 어긋난 고정 3×3 박스이고, 이제 B의 휘도 박스가 이에 맞춘다 | A4 |
| 등록된 동등성 기준은 픽셀이 완전히 같은 텐서를 요구한다. 기준을 B 자신의 허용 변형에 대한 상대 기준으로 바꿨다 | A5 |
| C의 입력 경로에는 CPU 픽셀 복사가 없지만, 런타임이 출력을 CPU로 복사해 §11.4 perf 기준을 충족하지 못한다 | A6 |

모든 측정은 2026-10-30에 보드에서 기본 거버너로, 아래의 영상 ①로 했다.

## 변형

| | 디코더 | `prepare` | `bind` |
|---|---|---|---|
| **B** | `h264_rkmpp`, DRM PRIME | 디코더 버퍼를 `mmap`(버퍼별 캐시), `DMA_BUF_IOCTL_SYNC` START/END(READ), OpenCV: 리사이즈 후 BT.601 제한 범위 NV12 → RGB를 CPU 슬롯에 기록 | 슬롯을 컨텍스트의 입력 버퍼로 `memcpy`(A3) |
| **A** | libavcodec `h264`(소프트웨어) | 같은 OpenCV 코드를 I420에 적용 | B와 같음 |
| **C** | `h264_rkmpp`, DRM PRIME | RGA `improcess`로 dma-heap 슬롯에 기록 | `rknn_set_io_mem` |

세 변형은 출력 바인딩(`rknn_create_mem` + `rknn_set_io_mem`, NCHW INT8)과 파이프라인의 나머지 부분을 공유한다.

## 측정한 RGA3 스케일러

`tools/rga_kernel`은 검은 NV12 프레임에 밝은 점 하나(임펄스)를 넣고, C와 같은 방식으로 줄인다.

- **휘도:** 출력 픽셀 (c, r)은 입력 열 3c+1…3c+3, 행 3r+1…3r+3의 평균이고, 가중치는 1/9다. OpenCV `INTER_AREA`는 3c…3c+2를 평균한다. 같은 박스가 한 픽셀 어긋나 있다.
- **색차:** 각 색차 표본을 전체 해상도로 반복한 뒤 같은 박스를 적용한다.
- **마지막 행과 열:** 다르게 처리하지만, 각각 640줄과 360줄 중 한 줄이다.
- **`im_opt_t.interp`:** RGA3는 무시한다. default, linear, cubic, average가 비트 단위로 같은 텐서를 냈다. librga에는 이 옵션을 켜는 usage 플래그가 없다.
- **색 변환:** 단색 프레임에서 RGA의 BT.601 제한 범위 변환은 OpenCV와 ±1 이내로 같다(평균 −0.18).

## 정렬

640×360 영역에서 C와 B를 비교했다(`tools/compare_prepare`, `tools/rga_probe`).

| B의 휘도 축소 | MAE | 최소 PSNR | 프레임 |
|---|---|---|---|
| `INTER_LINEAR` | 4.50 | 28.7 dB | 1,000 |
| `INTER_AREA` | 3.61 | — | 100 |
| **박스를 한 픽셀 옮긴 `INTER_AREA`(A4)** | **2.41** | **34.4 dB** | 1,000 |
| 4:4:4 색차와 직접 작성한 변환으로 만든 RGA3 전체 모델(진단용) | 2.09 | 35.3 dB | 100 |

- **B가 쓰는 것:** 한 픽셀 이동만 쓴다. 옮긴 영역에 같은 OpenCV 호출을 하는 것이라 B의 CPU 비용은 변하지 않는다.
- **전체 모델:** OpenCV 밖의 코드가 필요하고, 그래도 픽셀 단위로 같아지지 않는다.
- **남은 차이:** 평균 약 −0.6의 편차(C가 더 어두움)가 남는데, 반올림만으로는 설명되지 않는다.

## 동등성

등록된 §11.1과 §11.2 기준은 실패한다. 영상 ①의 1,000프레임에서 B 대비 C의 검출을 비교했다(탐욕적 매칭. 최종 판정은 M1-4 도구가 헝가리안 매칭으로 한다).

| | C 대 B | R = `INTER_LINEAR`를 쓴 B 대 B |
|---|---|---|
| 텐서 MAE / 최소 PSNR | 2.41 / 34.36 dB | 4.54 / 28.52 dB |
| IoU ≥ 0.95이면서 신뢰도 차이 ≤ 0.02인 매칭 쌍 | 41.8% | 28.4% |
| 박스 수가 같은 프레임 | 52.4% | 34.7% |
| 최대 신뢰도 차이 | 0.26 | 0.34 |
| 전체 박스 수, B 대비 | +0.44% | −1.04% |

- **C는 B의 허용된 OpenCV 변형 두 개끼리보다 B에 더 가깝다.** 이 정도의 작은 입력 차이에도 INT8 모델의 신뢰도는 0.1 단위로 움직인다. 그래서 등록된 기준은 픽셀이 완전히 같은 텐서만 통과할 수 있다.
- **A5가 이를 대체한다.** C가 B와 다른 정도는 R 이하여야 하고, 전체 박스 수는 2% 이내여야 한다. A5 기준으로는 추정치가 통과한다.
- **클래스별:** B 대비 박스 수를 보고한다.

  | | person | car | bus | truck | traffic light | stop sign | backpack | handbag |
  |---|---|---|---|---|---|---|---|---|
  | C | −0.51% | −0.01% | **+14.7%** | 0.00% | −0.73% | **+72.6%** | −1.36% | −1.03% |
  | R | −2.02% | +0.60% | −1.27% | −3.98% | −0.39% | −93.4% | −0.85% | +2.06% |

  R도 8개 중 4개 클래스에서 2%를 넘는다. stop sign은 모든 변형에서 0.25 문턱 근처를 오간다. bus는 C 쪽 편차가 R보다 크다.

전체 출력: [preprocess/compare_v1_1000.txt](preprocess/compare_v1_1000.txt)

## M1-2 완료 확인

영상 ① 1채널, `file_source`, 측정 구간 30초([preprocess/check_summary.txt](preprocess/check_summary.txt)):

| | 크래시 | 추론 속도 | 같은 160개 샘플 프레임의 박스 수 |
|---|---|---|---|
| A | 0 | 5.000 fps | 2,541 |
| B | 0 | 5.000 fps | 2,541 |
| C | 0 | 5.000 fps | 2,538 (−0.12%) |

- **기준 충족:** 크래시 0, 5 ± 0.25 fps, A/B/C 박스 수 2% 이내
- **A와 B가 정확히 같다:** 소프트웨어 디코더와 MPP가 비트 단위로 같은 픽셀을 낸다.
- **이 확인 실행의 프로세스 CPU:** A 17.5초, B 5.05초, C 2.05초. 기본 거버너, 1채널, 영상 하나로 돌린 확인 실행이라 측정값이 아니다.

## CPU 복사 없음 증거 (PROTOCOL §11.4)

`tools/zero_copy_evidence.sh`가 perf(실행마다 정상 상태에서 10초)와 strace를 수집하고, `tools/zero_copy_evidence.py`가 요약한다([preprocess/zero_copy_evidence.txt](preprocess/zero_copy_evidence.txt)). perf가 libc 안의 `memcpy`를 풀려면 실행 중인 libc와 맞는 `libc6-dbg`가 필요하다. 그것이 없으면 복사 비중이 0.82%가 아니라 0.12%로 나온다.

| 실행 | 복사 샘플 | `prepare`/`bind`/`rknn_run` 아래 | 캐시 유지보수 |
|---|---|---|---|
| none, 1 / 4채널 | 2.19% / 4.78% | 0 / 0 | 41.1% / 43.6% |
| **C, 1 / 4채널** | 2.23% / 3.25% | **0.82% / 1.08%** | 25.6% / 28.4% |
| B, 1 / 4채널 | 2.25% / 4.11% | 0.36% / 0.70% | 4.6% / 8.2% |

- **함수별 C의 복사:**
  - `prepare`: 없음
  - `bind`: 1개와 3개, `rknn_tensor_attr` 구조체 복사
  - `rknn_run`: 나머지 전부. B와 마찬가지로 `librknnrt.so`가 호출한 `__memcpy_generic`이다(B는 17개와 120개).
  - 이 복사는 모델의 출력 연산자에서 나온다. 이 연산자가 NPU의 NC1HWC2 출력을 CPU에서 NCHW로 바꾸며, 추론 한 번에 약 1.2 MB다. native NC1HWC2 바인딩은 `pass_through`를 주어도 `rknn_run`에서 실패한다.
  - 즉 C의 입력 경로에는 CPU 픽셀 복사가 없지만, §11.4 규칙은 `rknn_run`을 포함하므로 0.5% 기준에서 실패한다.
  - A6가 이를 기록한다. 리포트는 이를 밝히고, 모든 C 결과에 표시를 붙인다.
- **C − none 전체 복사 비중:** 1채널 +0.04%p, 4채널 −1.53%p
- **strace:**
  - C는 dma-buf `mmap`을 한 번도 호출하지 않았다.
  - B의 전처리 스레드는 프레임마다 같은 fd에 `DMA_BUF_IOCTL_SYNC`를 정확히 두 번 호출했다.
  - 모든 변형에서 디코더 스레드와 MPP 파서도 디코딩한 프레임마다 동기화한다(각각 초당 30번). 캐시되는 디코더 버퍼에 대한 MPP 자체의 캐시 관리이며, 모든 변형이 똑같이 부담한다.
- **주의:**
  - 기본 거버너에서는 실행마다 CPU 클럭이 달라서 실행 사이의 비중 비교가 왜곡된다. 캐시 유지보수 열이 가장 크게 영향받는다.
  - `librknnrt.so`와 OpenCV에는 심볼이 없다. B의 `bind` 복사는 꼬리 호출이라 스택에 `bind` 없이 나타난다.

## 벤치마크 영상

프로젝트 책임자가 후보 넷을 골랐고, 셋을 준비했다([preprocess/videos_manifest.tsv](preprocess/videos_manifest.tsv)). 원본은 커밋하지 않는다.

| | 출처 | 제작자 | 라이선스 | 원본 | 준비 결과 |
|---|---|---|---|---|---|
| ① | [Pexels 36330861](https://www.pexels.com/video/urban-crosswalk-scene-with-busy-traffic-36330861/), 도심 횡단보도 | SHOX ART | [Pexels License](https://www.pexels.com/license/) | 3840×2160, 23.98 fps, 39.5초, BT.709 | 1,186프레임 |
| ② | [Pexels 29130409](https://www.pexels.com/video/vibrant-city-street-with-busy-traffic-29130409/), 도심 거리 | Paul Bill | Pexels License | 3840×2160, 23.98 fps, 25.1초, 태그 없음 | 752프레임 |
| ③ | [Pexels 29969146](https://www.pexels.com/video/night-traffic-and-pedestrians-in-istanbul-29969146/), 이스탄불 야간 | Sururi Ballıdağ | Pexels License | 2560×1440, 29.97 fps, 23.8초, BT.709 | 714프레임 |
| ④ | [Pixabay 180386](https://pixabay.com/videos/intersection-cars-pedestrian-night-180386/), 오사카 야간 | Kanenori | [Pixabay Content License](https://pixabay.com/service/license-summary/) | — | 아직 없음. 사이트가 스크립트 다운로드를 막는다 |

- **영상 ①**만 1,000프레임 이상이라, §11과 M1-3 텐서 세트의 `file_source` 영상이 된다.
- **프레임 레이트:** ①과 ②는 23.98 fps에서 30 fps로 바꿨다. 다섯 프레임에 한 번꼴로 프레임이 반복된다.

## 도구

| 도구 | 용도 |
|---|---|
| `tools/compare_prepare` | N개 프레임에서 C와 B(두 보간)를 비교: 텐서 MAE/PSNR, 테두리 검사, 검출 일치, 클래스별 박스 수, 등록 기준과 수정 기준 판정 |
| `tools/rga_probe` | 평면별로 C와 OpenCV 후보, RGA3 모델을 비교 |
| `tools/rga_kernel` | RGA3의 임펄스 응답과 단색 프레임에서의 색 변환 |
| `tools/zero_copy_evidence.sh` / `.py` | §11.4의 perf와 strace 증거 |
