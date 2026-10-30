[English](AMENDMENTS.md)

# 수정 기록

`prereg-v1` 이후 [HYPOTHESES.ko.md](HYPOTHESES.ko.md)와 [PROTOCOL.ko.md](PROTOCOL.ko.md)에 가해진 변경, 그리고 PROTOCOL §14가 나중에 확정한다고 한 모든 값의 추가 전용 기록이다. 기존 항목은 수정하거나 삭제하지 않는다. 영어 항목과 한국어 항목의 내용이 다르면 영어 항목이 우선한다.

각 항목의 형식은 다음과 같다.

```
## A<number> — YYYY-MM-DD — <short title>
- Change:
- Reason:
- Affected data: (runs or results that must be redone, or "none")
- Release: (after freeze-m1 only: amend-A<number>)
```

## A1 — 2026-10-30 — 스파이크 결과: RGA 코어, 컨텍스트 경로, 해제 정책, 디코더 버퍼
- Change: PROTOCOL §14의 다음 값을 확정한다.
  - RGA 코어: `im_opt_t.core = IM_SCHEDULER_RGA3_CORE0 | IM_SCHEDULER_RGA3_CORE1`. 스파이크에서 마스크가 지켜지는 것을 확인했다. RGA3 core 1만 지정하자 부하가 core 1로 옮겨갔다. 이 마스크는 RGA2를 제외한다.
  - 컨텍스트 경로: 늦은 바인딩(§5.1). dma-buf 하나를 컨텍스트 3개 모두에 등록할 수 있으므로 §5.4의 대칭 컨텍스트 대체 경로는 쓰지 않는다.
  - 해제 정책: 한 컨텍스트에서 RKNN 메모리 객체를 해제해도 다른 컨텍스트는 정상 동작하므로 §5.4의 해제 대체 정책은 쓰지 않는다. 종료 시에는 그래도 RKNN 메모리 객체를 먼저 해제한 뒤 fd를 닫는다.
  - C 경로 대체: 없음. C2가 동작한다.
  - 디코더 버퍼 캐시 여부: CPU 캐시됨. 디코더는 `system-dma32` 힙(4 GiB 아래, 캐시됨)에서 할당한다.
- Reason: M1-0 스파이크 결과, [docs/notes/spike.ko.md](../docs/notes/spike.ko.md)
- Affected data: 없음. 아직 측정하지 않았다.
- Release: 없음(`freeze-m1` 이전)

## A2 — 2026-10-30 — OpenCV 빌드
- Change: OpenCV 빌드를 확정한다(PROTOCOL §14, M1-2의 첫 B 실행 전).
  - Ubuntu 패키지 `libopencv-core406t64`, `libopencv-imgproc406t64` 4.6.0+dfsg-13.1ubuntu1: aarch64용 OpenCV 4.6.0, 기본 NEON FP16, 병렬 프레임워크 TBB 2021.11, GCC 13.2.0으로 빌드
  - `libopencv_core.so.4.6.0` SHA-256 `270377fc59116c2045fae2891c03a5abdeabb5c58982afd7183bc715cdeca6b3`, `libopencv_imgproc.so.4.6.0` SHA-256 `9f2b345b4b666bab4ce3d6db44fd5ef312a49f98d9233720f501460e64d9ebef`
  - `cv::getBuildInformation()` 전체 출력은 [docs/notes/opencv_build.txt](../docs/notes/opencv_build.txt)에 있다(SHA-256 `f639ef8a42c044303ee94b2dff3dcba728e4ae713302421c8943620588c7dd26`).
  - `bench_main`은 `cv::setNumThreads(1)`을 호출한다(PROTOCOL §8).
- Reason: PROTOCOL §14는 B를 처음 실행하기 전에 OpenCV 빌드를 확정하도록 한다.
- Affected data: 없음. 아직 B를 실행하지 않았다.
- Release: 없음(`freeze-m1` 이전)

## A3 — 2026-10-30 — B의 bind는 바인딩된 입력 버퍼로 복사한다
- Change: PROTOCOL §5.2는 B의 `bind`를 `rknn_inputs_set` 호출로 정한다. 이를 바꿔, B의 `bind`는 슬롯을 `memcpy`로 `rknn_create_mem` 입력 버퍼(컨텍스트마다 하나, UINT8 NHWC로 `rknn_set_io_mem`에 한 번 바인딩)에 복사한다. B의 `bind`를 쓰는 A도 똑같이 바뀐다.
- Reason: librknnrt 2.3.2에서는 입력을 `rknn_inputs_set`으로 받은 컨텍스트가 `rknn_set_io_mem`으로 바인딩한 출력에 결과를 쓰지 않는다. B는 그 출력 바인딩을 C와 공유한다(§5.1, §5.3).
  - `tools/compare_prepare`로 측정했다. 같은 입력에서 B의 바인딩된 출력이 `rknn_outputs_get`과 달랐고, 후처리는 쓰이지 않은(0인) 버퍼에서 프레임당 370개의 박스를 찾았다. `rknn_inputs_set` 뒤에 출력을 다시 바인딩해도 해결되지 않았다.
  - B의 출력을 `rknn_outputs_get`으로 읽으면 C에 없는 출력 복사가 B에만 생긴다.
  - `memcpy`는 NPU 입력으로의 CPU 복사라는 B의 본질을 유지하고, 출력 바인딩도 C와 같게 둔다. 이제 같은 입력에서 B의 바인딩된 출력이 `rknn_outputs_get`과 같다.
- Affected data: 없음. `bench_main`으로 B를 실행한 적이 아직 없었다.
- Release: 없음(`freeze-m1` 이전)

## A4 — 2026-10-30 — 스케일링: RGA3의 커널과 OpenCV의 보간
- Change: PROTOCOL §8과 §14의 스케일링 값을 확정한다.
  - RGA: `im_opt_t.interp`는 `IM_INTERP_DEFAULT`로 둔다. RGA3는 이 값을 무시한다. default, linear, cubic, average가 비트 단위로 같은 텐서를 냈다.
  - 임펄스로 측정한 1920×1080 → 640×360 RGA3 커널(`tools/rga_kernel`): 출력 픽셀 (c, r)은 입력 열 3c+1…3c+3, 행 3r+1…3r+3의 평균이다. Y와, 전체 해상도로 반복한 색차 모두 그렇다. 즉 OpenCV `INTER_AREA` 박스(3c…3c+2)보다 한 픽셀 어긋난 3×3 박스다.
  - B와 A의 OpenCV: `INTER_AREA`. 정확히 1/3 배율일 때 B의 휘도 박스를 한 픽셀 옮겨 RGA3에 맞춘다. 즉 한 픽셀 옮긴 프레임에 `INTER_AREA`를 적용하고, 마지막 행과 열은 반복한다. 색차는 색차 해상도에서 옮기지 않은 `INTER_AREA`를 쓴다. 순서는 그대로 리사이즈 다음 색 변환(BT.601 제한 범위)이다.
- Reason: §8은 RGA 설정을 먼저 고정하고, C 대비 §11.1 MAE가 낮은 쪽으로 OpenCV 보간을 고르게 한다. 한 픽셀 이동은 조정한 값이 아니라 고정된 RGA3 스케일러의 성질이다.
  - 영상 ①의 1,000프레임(A5 참고)에서 C 대 B의 MAE는 `INTER_LINEAR`일 때 4.50, 옮긴 `INTER_AREA`일 때 2.41이었다.
  - `tools/rga_probe`의 100프레임에서 옮기지 않은 `INTER_AREA`는 3.61이었다.
- Affected data: 없음
- Release: 없음(`freeze-m1` 이전)

## A5 — 2026-10-30 — B 자신의 허용 변형을 기준으로 한 동등성
- Change: PROTOCOL §11.1과 §11.2의 통과 조건을 바꾼다. 입력, 테두리 검사(모든 테두리 원소가 114), §11.2의 매칭과 히스토그램은 그대로다. 기준 쌍은 같은 프레임에서 R = `INTER_LINEAR`를 쓴 B 대 실제 쓰는 B(A4)다. `INTER_LINEAR`와 `INTER_AREA`는 §8이 B에 허용한 두 선택지다.
  - 텐서 수준: C 대 B의 MAE가 R 이하이고, C 대 B의 프레임별 최소 PSNR이 R 이상이다.
  - 검출 수준:
    - (a) IoU ≥ 0.95이면서 신뢰도 차이 ≤ 0.02인 매칭 쌍의 비율이 R 이상이다.
    - (b) 모든 클래스에서 셈한 박스 수가 일치하는 프레임의 비율이 R 이상이다.
    - (c) 전체 클래스를 합한 C의 박스 수가 B의 ±2% 이내다.
  - 등록된 §11.1과 §11.2의 결과, 그리고 클래스별 박스 수도 계속 계산해 보고한다.
- Reason: 등록된 조건은 픽셀이 완전히 같은 텐서만 통과할 수 있다.
  - 영상 ①(Pexels 36330861, 벤치마크 영상 중 1,000프레임 이상인 유일한 영상)의 1,000프레임에서 `tools/compare_prepare`(탐욕적 매칭)로 측정한 값:

    | | C 대 B | R |
    |---|---|---|
    | 텐서 MAE | 2.41 | 4.54 |
    | 최소 PSNR | 34.36 dB | 28.52 dB |
    | 두 문턱을 모두 만족한 쌍 | 41.8% | 28.4% |
    | 박스 수가 같은 프레임 | 52.4% | 34.7% |
    | 전체 박스 수, B 대비 | +0.44% | −1.04% |

  - 이 정도의 작은 입력 차이에도 INT8 모델의 신뢰도는 C와 B 사이에서 최대 0.26, OpenCV 두 변형 사이에서 최대 0.34 움직인다.
  - 바뀐 조건은 C가 B와 다른 정도가 B의 허용 변형끼리 다른 정도를 넘지 않고, 전체 박스 수로 잰 후처리 작업량이 일치할 것을 요구한다.
  - 클래스별로는 R도 8개 중 4개 클래스에서 2%를 넘는다(person −2.0%, truck −4.0%, handbag +2.1%, stop sign −93%). 프로젝트 책임자는 전체 클래스 합계를 택했다. C의 클래스별 최대 편차인 bus +14.7%와 stop sign +72.6%는 보고한다.
- Affected data: 없음. M1-4의 동등성 실행 전에 기록했다.
- Release: 없음(`freeze-m1` 이전)

## A6 — 2026-10-30 — CPU 복사 없음 증거: 런타임이 출력을 CPU로 복사한다
- Change: 프로토콜은 바꾸지 않는다. §11.4가 정한 대로, 충족하지 못한 §11.4 기준과 그 원인을 기록한다.
- 결과: `tools/zero_copy_evidence.sh`와 `.py`로, 영상 ①에서 기본 거버너로 실행마다 10초씩 쟀다.
  - **perf, C:** `prepare`, `bind` 또는 `rknn_run`이 스택에 있는 복사 샘플이 전체 샘플의 1채널 0.82%, 4채널 1.08%로 0.5%를 넘는다.
    - 함수별로는 `prepare` 0개, `bind` 1개와 3개(`rknn_tensor_attr` 구조체 복사), `rknn_run` 24개와 139개다. `rknn_run` 쪽은 모두 `librknnrt.so`가 호출한 `__memcpy_generic`이거나 커널의 사용자 메모리 복사다.
    - B에도 같은 `rknn_run` 복사가 있다(17개와 120개).
    - C − none의 전체 복사 비중 차이는 1채널 +0.04%p, 4채널 −1.53%p다.
  - **원인:** 모델의 출력 연산자가 NPU의 native NC1HWC2 출력을 바인딩된 NCHW INT8 버퍼로 CPU에서 변환한다. 추론 한 번에 약 1.2 MB다.
    - native NC1HWC2 레이아웃으로 바인딩하면 `pass_through`를 주어도 `rknn_run`이 실패한다("unsupported tensor dtype … OutputOperator … fallback cpu failed").
    - native NHWC 레이아웃은 실행당 CPU 2.5 ms 대신 15 ms가 든다(docs/notes/spike.ko.md).
  - **strace:** C는 dma-buf `mmap`을 한 번도 호출하지 않았다. B의 전처리 스레드는 프레임마다 같은 fd에 `DMA_BUF_IOCTL_SYNC`를 두 번 호출했다(10초에 50프레임).
  - **캐시 유지보수**(`dcache_*_poc`), 1채널 / 4채널: B 4.6% / 8.2%, C 25.6% / 28.4%, none 41.1% / 43.6%.
    - MPP 자체가 디코딩한 프레임마다 동기화한다.
    - 기본 거버너에서는 실행마다 CPU 클럭이 달라서 실행 사이의 비중 비교가 왜곡된다.
- 결과 처리(§11.4): 리포트는 §11.4 perf 기준에서 C의 제로카피 성질이 충족되지 않았다고 밝힌다. 모든 C 결과에 "입력 경로에서 CPU 픽셀 복사 없음. 런타임의 출력 연산자가 B와 마찬가지로 추론마다 약 1.2 MB를 CPU로 복사함"이라는 표시를 붙인다.
- Affected data: 없음
- Release: 없음(`freeze-m1` 이전)
