[English](preprocess.md)

# 전처리: 변형 B와 A, C와의 정렬 (M1-2)

M1-2는 C(M1-1) 옆에 CPU 복사 변형 B와 소프트웨어 디코딩 기준 A를 추가한다. 또 B의 출력을 C에 맞추고, PROTOCOL §11.4의 CPU 복사 없음 증거를 모은다. 그 과정에서 나온 발견이 사전 등록한 설계를 바꿨고, 각 변경은 AMENDMENTS에 기록했다. 영문판과 다르면 영문판을 따른다.

| 발견 | 수정 기록 |
|---|---|
| B의 첫 실행 전에 OpenCV 빌드를 확정한다 | A2 |
| 등록된 B의 bind인 `rknn_inputs_set`을 쓰면 `rknn_set_io_mem`으로 바인딩한 출력에 아무것도 써지지 않는다 | A3 |
| RGA3의 3:1 스케일러는 OpenCV보다 한 픽셀 어긋난 고정 3×3 박스이고, 이제 B의 휘도 박스가 이에 맞춘다 | A4 |
| 등록된 동등성 기준은 픽셀이 완전히 같은 텐서를 요구한다. 기준을 B 자신의 허용 변형에 대한 상대 기준으로 바꿨다 | A5 |
| C의 입력 경로에는 CPU 픽셀 복사가 없지만, NPU 런타임 안의 작은 복사가 §11.4 perf 기준을 넘는다 | A6 |

모든 측정은 2026-10-30에 보드에서 기본 거버너로, 아래의 영상 ①로 했다. [preprocess/](preprocess/)의 출력은 이 변경의 코드로 만든 것이다.

## 변형

| | 디코더 | `prepare` | `bind` |
|---|---|---|---|
| **B** | `h264_rkmpp`, DRM PRIME | 디코더 버퍼를 `mmap`(버퍼별 캐시), `DMA_BUF_IOCTL_SYNC` START/END(READ), OpenCV: 리사이즈 후 BT.601 제한 범위 NV12 → RGB를 CPU 슬롯에 기록 | 슬롯을 컨텍스트의 입력 버퍼로 `memcpy`(A3) |
| **A** | libavcodec `h264`(소프트웨어) | 같은 OpenCV 코드를 I420에 적용 | B와 같음 |
| **C** | `h264_rkmpp`, DRM PRIME | RGA `improcess`로 dma-heap 슬롯에 기록 | `rknn_set_io_mem` |

세 변형은 출력 바인딩(`rknn_create_mem` + `rknn_set_io_mem`, NCHW INT8)과 파이프라인의 나머지 부분을 공유한다.

B가 `rknn_inputs_set`을 쓰지 않는 이유는 `tools/inputs_set_probe`가 보여 준다([preprocess/inputs_set_probe.txt](preprocess/inputs_set_probe.txt)). `rknn_inputs_set` 뒤에는 바인딩된 출력이 0 그대로이고, 다시 바인딩해도 마찬가지다. 그러면 후처리가 0에서 박스 370개를 찾는다. `rknn_outputs_get`으로는 17개다.

## 측정한 RGA3 스케일러

`tools/rga_kernel`은 검은 NV12 프레임에 밝은 점 하나(임펄스)를 넣고, C와 같은 방식으로 줄인다([preprocess/rga_kernel.txt](preprocess/rga_kernel.txt)).

- **휘도:** 출력 픽셀 (c, r)은 입력 열 3c+1…3c+3, 행 3r+1…3r+3의 평균이고, 가중치는 1/9다. OpenCV `INTER_AREA`는 3c…3c+2를 평균한다. 같은 박스가 한 픽셀 어긋나 있다.
- **색차:** 각 색차 표본을 전체 해상도로 반복한 뒤 같은 박스를 적용한다.
- **마지막 행과 열:**
  - 마지막 출력 열과 행은 프레임의 마지막 열이나 행에 약 3/9의 가중치를 준다. B는 가장자리를 반복하므로 2/9다. 각각 640줄과 360줄 중 한 줄이다.
  - 디코더 프레임은 1,080행을 메모리에 1,088행으로 둔다. RGA3는 패딩 행을 읽지 않는다.
- **`im_opt_t.interp`:** RGA3는 무시한다. 노이즈 프레임에서 linear, cubic, average가 기본값과 바이트 단위로 같은 출력을 냈다. 값만 넣었을 때와, librga의 적용 플래그까지 켜는 `IM_INTERP(h, v)` 형식 모두 그렇다.
- **색 변환:** 단색 프레임에서 RGA의 BT.601 제한 범위 변환은 OpenCV와 ±1 이내로 같다(평균 −0.18).

## 정렬

영상 ①의 1,000프레임, 640×360 영역에서 C와 B를 비교했다(`tools/compare_prepare`, `tools/rga_probe`, [preprocess/rga_probe_1000.txt](preprocess/rga_probe_1000.txt)).

| B의 휘도 축소 | MAE | 최소 PSNR |
|---|---|---|
| `INTER_LINEAR` | 4.50 | 28.7 dB |
| `INTER_AREA` | 3.78 | — |
| **박스를 한 픽셀 옮긴 `INTER_AREA`(A4)** | **2.41** | **34.4 dB** |
| 4:4:4 색차, 직접 작성한 변환, 버림 평균으로 만든 RGA3 전체 모델(진단용) | 2.04 | 35.3 dB |

- **B가 쓰는 것:** 한 픽셀 이동만 쓴다. 옮긴 영역에 같은 OpenCV 호출을 하는 것이라 B의 CPU 비용은 변하지 않는다.
- **전체 모델:** OpenCV 밖의 코드가 필요하고, 그래도 등록된 MAE 기준 2에 못 미친다.
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
- **독립적인 검증이 아니다:** A5 기준은 이 결과를 본 뒤에 정했고, M1-4도 같은 영상으로 검사한다.
- **클래스별:** B에서 박스가 100개 이상인 8개 클래스의 B 대비 박스 수:

  | | person | car | bus | truck | traffic light | stop sign | backpack | handbag |
  |---|---|---|---|---|---|---|---|---|
  | C | −0.51% | −0.01% | **+14.65%** | 0.00% | −0.73% | **+72.64%** | −1.36% | −1.03% |
  | R | −2.02% | +0.60% | −1.27% | −3.98% | −0.39% | −93.40% | −0.85% | +2.06% |

  - R도 이 8개 중 4개 클래스에서 2%를 넘는다.
  - stop sign은 모든 변형에서 0.25 문턱 근처를 오간다.
  - bus는 C 쪽 편차가 R보다 크다.

전체 출력: [preprocess/compare_v1_1000.txt](preprocess/compare_v1_1000.txt)

## M1-2 완료 확인

영상 ① 1채널, `file_source`, 측정 구간 30초([preprocess/check_summary.txt](preprocess/check_summary.txt)):

| | 크래시 | 추론 속도 | 같은 160개 샘플 프레임의 박스 수 |
|---|---|---|---|
| A | 0 | 5.000 fps | 2,541 |
| B | 0 | 5.000 fps | 2,541 |
| C | 0 | 5.000 fps | 2,538 (−0.12%) |

- **기준 충족:** 크래시 0, 5 ± 0.25 fps, A/B/C 박스 수 2% 이내
- **A와 B의 박스 수가 같다.** 예상한 결과다. H.264 디코딩은 비트 단위로 정확하게 정의되어 있어서, 소프트웨어 디코더와 MPP가 같은 픽셀을 내야 한다.
- **이 확인 실행의 프로세스 CPU:** A 17.4초, B 4.90초, C 2.03초. 기본 거버너, 1채널, 영상 하나로 돌린 확인 실행이라 측정값이 아니다.

## CPU 복사 없음 증거 (PROTOCOL §11.4)

`tools/zero_copy_evidence.sh`가 perf(실행마다 정상 상태에서 10초)와 strace를 수집하고, `tools/zero_copy_evidence.py`가 요약한다([preprocess/zero_copy_evidence.txt](preprocess/zero_copy_evidence.txt)).

- **perf에는 두 가지가 필요하다.** libc 안의 `memcpy`는 실행 중인 libc와 맞는 `libc6-dbg`가 있어야 풀린다. 없으면 libc 안의 복사에 이름이 붙지 않는다.
- **빌드**는 `-fno-omit-frame-pointer -fno-optimize-sibling-calls`를 써서, 복사를 호출한 함수가 스택에 남게 한다. 두 번째 플래그가 없으면 B의 `bind` 복사가 꼬리 호출이 되어 스택에 `bind` 없이 나타났다.

`prepare`, `bind` 또는 `rknn_run`이 스택에 있는 복사 샘플을 셋 중 가장 안쪽 함수로 나눈 값, 1채널 / 4채널:

| 실행 | 전체 복사 샘플 | 세 함수 아래 | `prepare` | `bind` | `rknn_run` | 캐시 유지보수 |
|---|---|---|---|---|---|---|
| none | 85 / 344 (4.09% / 5.05%) | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 43.9% / 43.4% |
| **C** | 118 / 419 (3.13% / 3.44%) | **39 / 174 (1.04% / 1.43%)** | 0 / 1 | 0 / 2 | 39 / 171 | 28.4% / 26.2% |
| B | 140 / 740 (2.49% / 4.21%) | 107 / 587 (1.91% / 3.34%) | 0 / 1 | 78 / 473 | 29 / 113 | 8.6% / 9.5% |

- **C의 복사:**
  - `prepare` 1개: 커널이 `fstat` 결과(버퍼별 캐시의 키)를 복사한 것
  - `bind` 2개: `bind` 자체에서 `rknn_tensor_attr` 구조체를 복사한 것과, `rknn_set_io_mem` 안에서 libstdc++가 문자열을 복사한 것
  - `rknn_run`, 나머지 전부: `librknnrt.so`가 호출한 `__memcpy_generic`과, 런타임의 `ioctl` 호출 안에서 일어난 커널의 사용자 메모리 복사
- **양성 대조군 B:** 입력 복사가 `bind` 아래에 나타난다. `rknn_run` 복사는 C와 같다.

perf는 바이트가 아니라 샘플을 센다. `tools/copy_sizes.sh`는 `__memcpy_generic`에 uprobe를 걸어, 1채널 4초(추론 20회) 동안 호출마다 길이를 기록한다([preprocess/copy_sizes.txt](preprocess/copy_sizes.txt)).

| | `prepare` 아래 | `bind` 아래 | `rknn_run` 아래 |
|---|---|---|---|
| **C** | 40번, 16,320바이트 | 640번, 13,800바이트 | 6,056번, 84,790바이트 |
| B | — | 20번, 24,576,000바이트 | 5,576번, 78,030바이트 |

- **`rknn_run` 안의 복사:**
  - 추론 한 번에 약 300번, 4 KB이고, 128바이트를 넘는 것은 없다. B에서도 C와 같다. 픽셀이나 텐서 데이터가 아니라 메타데이터를 옮긴다.
  - 그래도 합치면 샘플의 0.5%를 넘는다. 즉 C의 입력 경로에는 CPU 픽셀 복사가 없지만, §11.4 규칙은 `rknn_run`을 포함하므로 실패한다.
  - A6가 이를 기록한다. 리포트는 이를 밝히고, 모든 C 결과에 표시를 붙인다.
- **B의 입력 복사:** 프레임마다 1,228,800바이트, 640×640×3 텐서 전체다.
- **이 기준이 다루지 않는 것:**
  - NPU의 native 출력은 NC1HWC2이고, 파이프라인은 이를 NCHW INT8로 바인딩한다. 추론 한 번에 약 1.2 MB다.
  - native 바인딩은 `rknn_run`에서 실패한다("… OutputOperator … fallback cpu failed"). 런타임이 출력을 CPU에서 변환한다는 뜻으로 보인다. 그렇다면 `memcpy`가 아닌 코드로 변환하며, perf는 `librknnrt.so` 안의 그 코드에 이름을 붙이지 못한다.
  - B도 출력을 같은 방식으로 바인딩한다.
- **C − none 전체 복사 샘플:** 1채널 +33개, 4채널 +75개. 비중으로는 −0.96%p와 −1.61%p인데, 실행마다 전체 샘플 수가 다르기 때문이다.
- **strace:**
  - C는 dma-buf `mmap`을 한 번도 호출하지 않았다.
  - B의 전처리 스레드는 프레임마다 같은 fd에 `DMA_BUF_IOCTL_SYNC`를 정확히 두 번 호출했다.
  - C와 B 모두에서 디코더 스레드와 MPP 파서도 디코딩한 프레임마다 동기화한다(각각 초당 30번). 캐시되는 디코더 버퍼에 대한 MPP 자체의 캐시 관리이며, MPP로 디코딩하는 모든 변형이 부담한다.
- **주의:**
  - 기본 거버너에서는 실행마다 CPU 클럭이 달라서 실행 사이의 비중 비교가 왜곡된다. 캐시 유지보수 열이 가장 크게 영향받는다.
  - `librknnrt.so`와 OpenCV에는 심볼이 없다. C 샘플의 15.7% / 22.4%가 `librknnrt.so` 안에서 풀리지 않은 채 끝난다.

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
| `tools/rga_kernel` | RGA3의 임펄스 응답, 가장자리와 패딩 행, 단색 프레임에서의 색 변환, `interp` 설정 |
| `tools/inputs_set_probe` | 바인딩된 출력에 대한 `rknn_inputs_set`, 그리고 B의 바인딩된 입력(A3) |
| `tools/copy_sizes.sh` / `.py` | §11.4의 복사가 옮기는 바이트 수. `__memcpy_generic`에 건 uprobe로 잰다 |
| `tools/zero_copy_evidence.sh` / `.py` | §11.4의 perf와 strace 증거 |
