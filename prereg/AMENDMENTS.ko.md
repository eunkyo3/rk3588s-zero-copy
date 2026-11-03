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
- Change: PROTOCOL §5.2는 B의 `bind`를 `rknn_inputs_set` 호출로 정한다. 이를 바꿔, B의 `bind`는 슬롯을 `memcpy`로 `rknn_create_mem` 입력 버퍼(컨텍스트마다 하나, UINT8 NHWC로 `rknn_set_io_mem`에 한 번 바인딩)에 복사한다. B의 `bind`를 쓰는 A도 똑같이 바뀐다. HYPOTHESES.md("비교 대상")의 B 설명도 함께 바뀐다. B는 `rknn_inputs_set`이 아니라 `memcpy`로 NPU 입력에 복사한다.
- Reason: librknnrt 2.3.2에서는 입력을 `rknn_inputs_set`으로 받은 컨텍스트가 `rknn_set_io_mem`으로 바인딩한 출력에 결과를 쓰지 않는다. B는 그 출력 바인딩을 C와 공유한다(§5.1, §5.3).
  - 영상 ①의 첫 프레임으로 `tools/inputs_set_probe`를 써서 측정했다([출력](../docs/notes/preprocess/inputs_set_probe.txt)). 매 실행 전에 바인딩된 출력을 0으로 채웠다.
    - `rknn_inputs_set`과 `rknn_run` 뒤에도 출력은 0 그대로였고, 후처리는 여기서 박스 370개를 찾았다. 같은 입력에서 `rknn_outputs_get`은 17개를 냈다.
    - `rknn_inputs_set` 뒤에 출력을 다시 바인딩해도 달라지지 않았다.
    - `rknn_set_io_mem`으로 바인딩한 입력 버퍼에 `memcpy`한 뒤에는 출력이 `rknn_outputs_get`과 같았다.
  - B의 출력을 `rknn_outputs_get`으로 읽으면 C에 없는 출력 복사가 B에만 생긴다.
  - `memcpy`는 NPU 입력으로의 CPU 복사라는 B의 본질을 유지하고, 출력 바인딩도 C와 같게 둔다. `tools/compare_prepare`는 1,000프레임 중 첫 프레임과 마지막 프레임에서 B의 바인딩된 출력을 `rknn_outputs_get`과 비교한다. 결과는 같았다.
- Affected data: 없음. `bench_main`으로 B를 실행한 적이 아직 없었다.
- Release: 없음(`freeze-m1` 이전)

## A4 — 2026-10-30 — 스케일링: RGA3의 커널과 OpenCV의 보간
- Change: RGA의 스케일링 설정(PROTOCOL §14)을 확정하고, §8의 OpenCV 선택지 두 개에 세 번째를 더한다.
  - RGA: `im_opt_t.interp`는 `IM_INTERP_DEFAULT`로 둔다. RGA3는 이 값을 무시한다. 노이즈 프레임에서 linear, cubic, average는 기본값과 바이트 단위로 같은 출력을 냈다. 값만 넣었을 때와, librga의 적용 플래그까지 켜는 `IM_INTERP(h, v)` 형식 모두 그랬다(`tools/rga_kernel`).
  - 임펄스로 측정한 1920×1080 → 640×360 RGA3 커널(`tools/rga_kernel`):
    - 출력 픽셀 (c, r)은 입력 열 3c+1…3c+3, 행 3r+1…3r+3의 평균이다. Y와, 전체 해상도로 반복한 색차 모두 그렇다. 즉 OpenCV `INTER_AREA` 박스(3c…3c+2)보다 한 픽셀 어긋난 3×3 박스다.
    - 마지막 출력 열과 행은 프레임의 마지막 열이나 행에 약 3/9의 가중치를 준다.
    - RGA3는 프레임 아래에 있는 디코더의 패딩 행을 읽지 않는다.
  - B와 A의 OpenCV: §8은 `INTER_LINEAR`나 `INTER_AREA`를 허용한다. 이 수정은 세 번째 선택지를 더하고 그것을 고른다. 정확히 1/3 배율일 때 휘도 박스를 한 픽셀 옮겨 RGA3에 맞춘 `INTER_AREA`다.
    - 즉 한 픽셀 옮긴 프레임에 `INTER_AREA`를 적용한다. 마지막 행과 열은 프레임 가장자리를 반복하므로, 가장자리 가중치가 RGA3의 약 3/9가 아니라 2/9다.
    - 색차는 색차 해상도에서 옮기지 않은 `INTER_AREA`를 쓴다.
    - 순서는 그대로 리사이즈 다음 색 변환(BT.601 제한 범위)이다.
- Reason: §8은 RGA 설정을 먼저 고정하고, C 대비 §11.1 MAE가 낮은 쪽으로 OpenCV 보간을 고르게 한다. 한 픽셀 이동은 조정한 값이 아니라, 측정으로 확인한 고정 RGA3 스케일러의 성질이다.
  - 영상 ①의 1,000프레임(A5 참고)에서 C 대 B의 MAE: `INTER_LINEAR` 4.50(`tools/compare_prepare`), `INTER_AREA` 3.78(`tools/rga_probe`), 옮긴 `INTER_AREA` 2.41(`tools/compare_prepare`)
  - `tools/rga_probe`는 다른 OpenCV 보간, 변환 후 리사이즈, RGA3 전체 모델도 시험했다([출력](../docs/notes/preprocess/rga_probe_1000.txt)). 이들은 §8의 선택지 밖에 있는 진단용이다. 가장 가까운 것은 4:4:4 색차, 직접 작성한 색 변환, 버림 평균을 쓴 모델로 MAE 2.04였고, 등록된 기준 2보다 여전히 높다. OpenCV 밖의 코드가 필요해서 쓰지 않는다.
- Affected data: 없음
- Release: 없음(`freeze-m1` 이전)

## A5 — 2026-10-30 — B 자신의 허용 변형을 기준으로 한 동등성
- Change: PROTOCOL §11.1과 §11.2의 통과 조건을 바꾼다. 다음은 그대로다.
  - 입력
  - 테두리 검사. 여전히 통과 조건이다: 모든 B와 C 텐서의 모든 테두리 원소가 114
  - §11.2의 매칭
  - 히스토그램
- 새 조건은 C를 기준 쌍 R과 비교한다.
  - **R:** 같은 프레임에서, 단순 `INTER_LINEAR`(두 평면 모두 `cv::resize`, 이동 없음. §8의 다른 선택지)를 쓴 B의 전처리 대 실제 쓰는 B(A4)
  - R에서는 `INTER_LINEAR` 쪽이 C의 자리에 선다. 그 박스를 B의 박스와 매칭하고, B의 박스와 수를 비교한다.
  - **텐서 수준:** C 대 B의 MAE가 R 이하이고, C 대 B의 프레임별 최소 PSNR이 R 이상이다.
  - **검출 수준:**
    - (a) IoU ≥ 0.95이면서 신뢰도 차이 ≤ 0.02인 매칭 쌍의 비율이 R 이상이다.
    - (b) 모든 클래스에서 셈한 박스 수가 일치하는 프레임의 비율이 R 이상이다.
    - (c) 전체 박스 수가 B와 2% 넘게 다르지 않다: |N_C − N_B| / N_B ≤ 0.02. N은 (b)에서 셈한 박스만이 아니라 후처리가 내는 모든 박스(NMS 뒤 신뢰도 ≥ 0.25, 모든 클래스, 모든 프레임 합계)다.
  - 등록된 §11.1과 §11.2의 결과, 그리고 클래스별 박스 수도 계속 계산해 보고한다.
- Reason: 등록된 조건은 픽셀이 완전히 같은 텐서만 통과할 수 있다.
  - 영상 ①(Pexels 36330861, 벤치마크 영상 중 1,000프레임 이상인 유일한 영상)의 1,000프레임에서 `tools/compare_prepare`(탐욕적 매칭)로 측정한 값([출력](../docs/notes/preprocess/compare_v1_1000.txt)):

    | | C 대 B | R |
    |---|---|---|
    | 텐서 MAE | 2.41 | 4.54 |
    | 최소 PSNR | 34.36 dB | 28.52 dB |
    | 두 문턱을 모두 만족한 쌍 | 41.8% | 28.4% |
    | 박스 수가 같은 프레임 | 52.4% | 34.7% |
    | 전체 박스 수, B 대비 | +0.44% | −1.04% |

  - 이 정도의 작은 입력 차이에도 INT8 모델의 신뢰도는 C와 B 사이에서 최대 0.26, OpenCV 두 변형 사이에서 최대 0.34 움직인다.
  - 바뀐 조건은 두 가지를 요구한다.
    - C가 B와 다른 정도가 B의 허용 변형끼리 다른 정도를 넘지 않는다.
    - 전체 박스 수로 잰 후처리 작업량이 일치한다.
  - **클래스별:** B에서 박스가 100개 이상인 8개 클래스 중 4개에서 R도 2%를 넘는다(person −2.02%, truck −3.98%, handbag +2.06%, stop sign −93.4%). 프로젝트 책임자는 전체 클래스 합계를 택했다. C의 클래스별 최대 편차인 bus +14.65%와 stop sign +72.64%는 보고한다.
  - **독립적인 검증이 아니다:**
    - 이 기준은 이 프레임들에서 C와 R의 결과를 본 뒤에, 그리고 A4가 B의 휘도 박스를 C 쪽으로 옮긴 뒤에 정했다.
    - M1-4의 검사도 1,000프레임을 가진 유일한 영상인 영상 ①로 한다.
    - R의 텐서 차이가 `INTER_LINEAR` B 대비 C의 차이와 비슷한 것(4.54와 4.50)은 거의 구조적이다. B가 이제 C에 가깝기 때문이다.
- Affected data: 없음. M1-4의 동등성 실행 전에 기록했다.
- Release: 없음(`freeze-m1` 이전)

## A6 — 2026-10-30 — CPU 복사 없음 증거: NPU 런타임 안의 작은 복사
- Change: 프로토콜은 바꾸지 않는다. §11.4가 정한 대로, 충족하지 못한 §11.4 기준과 그 원인을 기록한다.
- 결과: 영상 ①, 기본 거버너
  - **도구:**
    - `tools/zero_copy_evidence.sh`와 `.py`: perf, 실행마다 10초([출력](../docs/notes/preprocess/zero_copy_evidence.txt))
    - `tools/copy_sizes.sh`와 `.py`: 이 CPU에서 glibc가 고르는 `__memcpy_generic`과 `__memmove_generic`에 uprobe를 걸어 호출마다 길이를 기록한다. 1채널 4초, 추론 20~21회, 잃은 이벤트 없음([출력](../docs/notes/preprocess/copy_sizes.txt)). 커널의 사용자 메모리 복사는 재지 않는다.
  - **빌드:** 모든 코드를 `-fno-omit-frame-pointer -fno-optimize-sibling-calls`로 빌드해, 복사를 호출한 함수가 스택에 남게 했다. 두 번째 플래그 없이 한 첫 실행에서는 꼬리 호출이던 B의 `bind` 복사를 귀속시키지 못했다.
  - **perf, C:** `prepare`, `bind` 또는 `rknn_run`이 스택에 있는 복사 샘플이 전체 샘플의 1채널 1.04%, 4채널 1.43%로 0.5%를 넘는다. 셋 중 가장 안쪽 함수 기준으로, 1채널 / 4채널:
    - `prepare`: 0 / 1개. 1개는 커널이 `fstat` 결과(버퍼별 캐시의 키)를 복사한 것이다.
    - `bind`: 0 / 2개. 하나는 `bind` 자체에서 `rknn_tensor_attr` 구조체를 복사한 것이고, 다른 하나는 `rknn_set_io_mem` 안에서 libstdc++가 스트림에 쓴 것(`basic_streambuf::xsputn`)이다.
    - `rknn_run`: 39 / 171개. `librknnrt.so`가 호출한 `__memcpy_generic`과, 런타임의 `ioctl` 호출 안에서 일어난 커널의 사용자 메모리 복사다.
  - **복사가 옮기는 양**, 1채널:
    - **C, `rknn_run` 아래**(추론 21회): 23,994번, 306,170바이트. 추론 한 번에 약 1,150번, 15 KB이고, 512바이트를 넘는 것은 없다. 대부분은 `librknnrt.so` 안의 `std::vector` 복사이며 `memmove`를 거친다.
    - **C, `prepare`와 `bind` 아래:** 903번, 33,726바이트. 512바이트를 넘는 것은 없다.
    - **B, `bind` 아래**(20프레임): 1,228,800바이트짜리 20번. 프레임마다 입력 텐서 하나다.
    - **B, `rknn_run` 아래**(추론 20회): 23,408번, 297,640바이트. C와 같은 런타임 복사다. C는 여기에 더해 추론마다 약 24번의 서식 복사(`printf`와 libstdc++ 문자열 쓰기)를 하는데, B에는 없다.
  - **양성 대조군, B(perf):**
    - `bind`: 78 / 473개. 입력 복사다.
    - `rknn_run`: 29 / 113개. C와 같은 런타임 복사다.
  - **C − none, 전체 복사 샘플:** 1채널 +33개, 4채널 +75개. 복사 비중으로는 −0.96%p와 −1.61%p다. 실행마다 전체 샘플 수가 다르기 때문이다.
  - **원인:** 이 기준은 바이트가 아니라 샘플을 센다. 런타임은 `rknn_run` 안에서 추론마다 약 1,150번의 작은 복사를 하고, B에서도 C와 같다. 512바이트를 넘는 것이 없으니 픽셀이나 텐서 데이터를 옮기는 복사는 없다. 그래도 합치면 샘플의 0.5%를 넘는다.
  - **이 기준이 다루지 않는 것:** NPU의 native 출력은 NC1HWC2이고, 파이프라인은 이를 NCHW INT8로 바인딩한다. 추론 한 번에 약 1.2 MB다.
    - native NC1HWC2 레이아웃으로 바인딩하면 `pass_through`를 주어도 `rknn_run`이 실패한다("unsupported tensor dtype … OutputOperator … fallback cpu failed"). 런타임이 출력을 CPU에서 변환한다는 뜻으로 보인다.
    - 그렇다면 `memcpy`나 `memmove`가 아닌 코드로 변환한다. `librknnrt.so`에는 심볼이 없고, C 샘플의 15.7% / 22.4%가 이 안에서 풀리지 않은 채 끝난다.
    - B도 출력을 같은 방식으로 바인딩한다.
  - **strace:** C는 dma-buf `mmap`을 한 번도 호출하지 않았다. B의 전처리 스레드는 프레임마다 같은 fd에 `DMA_BUF_IOCTL_SYNC`를 두 번 호출했다(10초에 50프레임).
  - **캐시 유지보수**(`dcache_*_poc`), 1채널 / 4채널: B 8.6% / 9.5%, C 28.4% / 26.2%, none 43.9% / 43.4%
    - MPP 자체가 디코딩한 프레임마다 동기화한다.
    - 기본 거버너에서는 실행마다 CPU 클럭이 달라서 실행 사이의 비중 비교가 왜곡된다.
- 결과 처리(§11.4):
  - 리포트는 §11.4 perf 기준에서 C의 제로카피 성질이 충족되지 않았다고 밝힌다.
  - 모든 C 결과에 다음 표시를 붙인다: "입력 경로에서 CPU 픽셀 복사 없음. NPU 런타임 안에서 추론마다 약 1,150번(약 15 KB)의 작은 복사가 일어나 §11.4 perf 기준을 충족하지 못함. B도 같음"
  - 리포트는 출력 변환이라는 미해결 질문도 밝힌다.
- Affected data: 없음
- Release: 없음(`freeze-m1` 이전)

## A7 — 2026-11-02 — 고정 클럭, P_cap, H2 보조 지점
- Change: PROTOCOL §14의 다음 값을 확정하고(M1-3), 아래와 같이 §8과 다르게 정하며 §9.3에 조건을 더한다.
  - **고정 주파수:** A55(policy0) 1800 MHz, A76 policy4와 policy6 2208 MHz, DDR(`dmc`) 2112 MHz, NPU 1000 MHz. 이후 모든 실행은 이 값과 대조한다(§9.3 규칙 1).
    - **§8과 다른 점:** §8은 `performance` 거버너로 *정해지는* 주파수, 즉 각 대상의 최대값을 고정값으로 한다. A76 두 클러스터는 그 대신 최대값보다 낮은 2208 MHz로 고정한다.
    - `tools/set_perf_mode.sh`가 `performance` 거버너를 걸고 각 최대값을 고정값으로 둔다. 값이 제공되지 않거나 그 값에 도달하지 못하면 실패한다.
  - **`P_cap`** = 초당 194.2083회 추론: 194.5333, 194.1667, 194.2083의 중앙값(120초 창에서 23,305회 완료).
  - **H2 보조 지점** = floor(0.8 × 194.2083 / 30) = 5채널. 따라서 보조 집합(§6.3)은 {1, 2, 3, 4, 5}다. 이 지점은 H2의 5채널 지점과 같으므로, H2의 이차 판정(HYPOTHESES, H2)은 H2 판정과 같은 채널 수에서 나온다.
  - **비포화 조건 중 `P_cap` 부분**(§6.2): 채널 수 × fps ≤ 4661/30 ≈ 155.37. 즉 본편 조건에서 최대 31채널, 보조 조건에서 최대 5채널이다. 경계는 이후 수정 항목에서 `L`과 함께 확정한다.
  - **NPU 원시 상한**(참고용): 코어 1개 초당 70.7250회, 2개 137.2250회, 3개 199.2083회.
  - **이 실행들에 대한 §9.3 추가 조건:** `tools/calib_npu` 실행은, 창이 끝난 뒤 어떤 컨텍스트의 바인딩 출력이 같은 텐서의 `rknn_outputs_get`과 다르면 무효다(A3). 그런 실행은 없었다.
- Reason:
  - **A76을 최대값이 아닌 2208 MHz로 두는 이유**([docs/notes/calibration/a76_tables.txt](../docs/notes/calibration/a76_tables.txt)):
    - policy4 주파수 표의 최고 항목이 부팅마다 바뀐다. 2026-10-30 부팅과 2026-11-02 약 05:25 UTC 부팅에서는 2304 MHz, M1-4 실행 때의 부팅과 2026-11-02 07:20 UTC 부팅에서는 2352 MHz였다.
    - 05:25 부팅은 sysfs를 직접 읽어 확인했고, 나머지는 `collect_env.sh` 출력에 남아 있다. policy6은 네 번 모두 2352 MHz였다.
    - 그래서 최대값을 고정값으로 쓰면 세션마다 값이 달라지거나, 재부팅 뒤의 실행이 모두 무효가 된다.
    - 2208 MHz는 지금까지 본 모든 부팅에서 두 클러스터 모두 제공됐다. 또한 A76 두 클러스터에 대한 커널의 고온 상한이기도 하다(`h_limit=2208000000`, 85 °C를 넘으면 적용).
    - A55 클러스터와 NPU는 최대값을 유지한다. 이들의 고온 상한은 각각 1608 MHz와 800 MHz다.
    - 이 상한만이 제한은 아니다. `soc-thermal`의 75 °C 트립에서 시작하는 열 스로틀링은 2026-10-30에 A55 클러스터를 1416 MHz, A76 클러스터를 2016 MHz까지 더 낮췄고, NPU는 800 MHz였다(`docs/notes/env.ko.md`, 열 관찰). 어느 이유로든 고정값보다 낮은 클럭 샘플이 나오면 그 실행은 무효다(§9.3 규칙 1).
    - 이제 `tools/collect_env.sh`가 실행마다 그 전에 주파수 표, 부팅 시각, `h_limit` 상한을 기록한다.
  - **측정:** `tools/calib_npu.sh`, 세션 [results/calibration/20261102T073918Z_npu](../results/calibration/20261102T073918Z_npu), 코드 `3789a7a`, 주변 온도 28 °C, 냉각 "방열판 + 상시 미니 팬(알리익스프레스 무브랜드)".
    - 세션은 10분 대기로 시작했고(`soc-thermal` 기준 39.9 °C), 모든 실행이 쿨다운 게이트를 통과했다(§9.2).
    - 측정 4종을 번갈아 3회 반복했다. 12회 모두 유효했다. 고정값보다 낮은 클럭 샘플이 없었고, 트립점에 닿은 온도도 없었다(최고 55.5 °C).
  - **텐서 세트**(§6.2): `tools/dump_tensor`로 영상 ①(`file_source`)의 첫 1,000프레임을 C의 전처리(RGA3)로 만들어 `system-uncached-dma32` 버퍼에 올렸다.
    - SHA-256은 `051d6f12…`다. M1-4 판정 실행에서 코드 `669fb9b`의 `tools/equivalence_dump`가 쓴 `c.u8`(Release `data-equivalence-20261102T011123Z`에 보관)의 해시와 같다. 따라서 이 세트는 그 실행의 C 입력이다.
    - 세트의 기록(`tensors.json`)에는 코드 `38ddd37`과 당시 커밋하지 않은 `dump_tensor`가 적혀 있다.
  - **원시 상한도 단계별 바인드(`rknn_set_io_mem`)를 포함한다.** 텐서를 순환하려면 필요하다. 단계당 0.014~0.021 ms로, `rknn_run`의 14.1~15.0 ms에 비하면 작다.
  - 냉각 구성은 이 항목이 아니라 열 확인(§12)에서 확정한다.
- Affected data: 없음. M1-4는 기본 거버너로 실행했지만, 그 텐서와 검출 결과는 클럭에 좌우되지 않는다.
- Release: 없음(`freeze-m1` 이전)

## A8 — 2026-11-03 — 동등성 기준의 홀드아웃 검사
- Change: A5의 기준을, 그 기준을 정할 때 쓰지 않은 영상에 적용하는 검사를 실행 전에 등록한다. A5의 기준은 그대로 유효하고, 그 기준에 따른 M1-4의 판정도 그대로다.
  - **입력:** 벤치마크용으로 준비한 영상 ②, ③, ④([docs/notes/preprocess.ko.md](../docs/notes/preprocess.ko.md#벤치마크-영상))의 모든 프레임을 `file_source`로 읽는다.

    | 영상 | 프레임 | SHA-256 |
    |---|---|---|
    | ② `pexels_29130409` | 752 | `e560dd8481d5fe1c615f208fae192fc7fd583097d202e785dc026eae8fb75b41` |
    | ③ `pexels_29969146` | 714 | `592b6847e2e63e07393af6f4a04b691be438c23eba2c73f79b021dca877c2772` |
    | ④ `pixabay_180386` | 948 | `2538f54d36e0d763ec3e13f9e606bf01e2d142fff82181eb156f792576b0555b` |

    저장소 기록으로는, 이 항목 전에 B, C, R이 이 영상들로 전처리나 추론을 한 적이 없다. 그전에 이 영상들로 한 실행은 디코딩만 한 2026-11-02의 RTSP 확인(`--variant none`)뿐이다.
    이 항목을 쓰는 동안 돌고 있는 M1-3 세션도 이 영상들을 쓴다. `L` 탐색과 반복 재생 검사는 디코딩만 한다. 열 확인은 B만 실행하며, B의 프레임별 박스 수만 기록하고 텐서는 남기지 않는다. 이 실행들을 C나 R과 비교하는 일은 없다.
  - **절차:** 보드에서 `tools/equivalence_holdout.sh`를 실행한다.
    - 먼저 각 영상의 SHA-256과 프레임 수를 표와 대조하고, 하나라도 다르면 멈춘다.
    - 영상마다 `tools/equivalence_dump`가 M1-4와 같은 방식으로 모든 프레임에 C, B, R을 실행한다. 세 변형이 같은 디코딩 프레임을 쓰고, NPU 코어 0에서 돌며, M1-4의 하네스 검사를 거친다.
    - 그다음 `tools/equivalence_check.py --set`이 세 영상을 ②, ③, ④ 순서로 합친다.
    - **코드:** `-dirty`가 아니고, 최신 수정 기록이 A8이며, 이 항목을 추가하는 풀 리퀘스트가 머지된 뒤 `src/`, `tools/`, `models/`, `CMakeLists.txt`에 변경이 없는 빌드. 따라서 검사는 다음 수정 기록이 머지되기 전에 실행한다.
  - **어느 실행이 판정하는가:**
    - 모든 실행을 남기고 보고한다. 검사까지 끝난(종료 코드 0 또는 1) 첫 실행이 판정한다.
    - 그 전에 멈춘 실행(종료 코드 2 또는 3, 예를 들어 하네스 검사 실패)은 판정하지 않는다. 원인을 기록하고 검사를 다시 실행한다.
    - `freeze-m1` 전에 B, C, R의 코드가 바뀌면 M1-4를 다시 하고(§15) 이 검사도 다시 한다. 리포트에는 두 결과를 모두 적는다.
    - 멈춘 실행이나 B, C, R의 변경 때문에 코드를 바꿔 다시 실행할 때는 바뀐 코드로 실행하고, 그 수정 기록을 헤더에 적는다. 위의 코드 규칙은 첫 실행에만 적용한다.
  - **판정:** 합친 2,414프레임에, A5의 여섯 조건을 바꾸지 않고 적용한다.
    - B와 C의 테두리 검사
    - C의 MAE가 R 이하, C의 프레임별 최소 PSNR이 R 이상
    - (a)와 (b)가 R 이상
    - (c) C의 전체 박스 수가 B의 2% 이내
  - **판정이 아니라 보고만 하는 것:** 영상별 A5 결과, 합친 프레임에 대한 §11.1과 §11.2의 등록 기준 결과, 클래스별 박스 수
  - **결과에 따른 처리:**
    - 결과가 어떻든 보고한다. 통과하면, A5의 동등성이 기준을 정할 때 쓰지 않은 영상에서도 성립했다고 보고하고, 개별로 실패한 영상이 있으면 함께 적는다.
    - 실패하면 리포트 요약에 적는다. 그리고 모든 가설 판정에 이 단서를 붙인다: B와 C의 A5 동등성은 A5의 기준을 정할 때 쓴 영상 ①에서만 확인됐다.
    - 어느 쪽이든 §12의 비교 실행은 진행한다. M1-4가 자기 입력에서 A5로 내린 판정이 그대로이므로 §11.3은 적용하지 않는다.
    - 이 검사 결과 때문에 기준, 설정, 구현을 바꾸지 않는다. 나중에 다른 이유로 바꾼다면 별도 수정 기록이 필요하다.
  - **등록:** 이 항목은 `freeze-m1` 전이지만 Release `amend-A8`을 둔다. 이 항목을 추가하는 풀 리퀘스트가 머지된 뒤 만들며, 그 `created_at`이 기준 시각이다(§1).
    - 검사 결과의 헤더는 최신 수정 기록으로 A8을 적으므로, `tools/check_prereg_order.py`가 결과가 그 시각 뒤에 시작했는지 확인한다.
    - 검사는 `freeze-m1` 전에 실행한다.
- Reason: A5의 기준은 영상 ①에서 C와 R의 결과를 안 뒤에, 그리고 A4가 B를 C 쪽으로 옮긴 뒤에 정했다(A5, "독립적인 검증이 아니다").
  - 그 선택에 관여하지 않은 영상에 기준을 그대로 적용하면, 기준이 그것을 정한 영상 밖에서도 성립하는지 검사할 수 있다. 결과별 처리를 미리 정해 두면 결과를 골라 쓸 수 없다.
  - 영상 ②, ③, ④는 각각 §11에 등록한 1,000프레임보다 짧아서 합쳐서 판정한다.
    - 합치면 2,414프레임으로, A5의 기준을 정한 1,000프레임과 견줄 만하다.
    - 영상 하나만으로는 714프레임까지 줄어서, 박스 수에 2% 한계를 두는 조건 (c)가 우연에 더 흔들린다.
  - **도구 변경:**
    - `tools/equivalence_check.py`에 출처를 합치는 `--set DIR FRAMES`를 추가한다. 출처가 하나일 때의 출력은 그대로다. 120프레임 시험 사례에서 이전 버전과 새 버전이 같은 보고서와 같은 JSON을 낸다.
    - `tools/equivalence_holdout.sh`를 새로 추가한다.
- Affected data: 없음. M1-4의 결과와 판정은 바뀌지 않는다.
- Release: `amend-A8`

## A9 — 2026-11-03 — `L`, 본편 채널 집합, 비포화 경계, 채널 배정, 냉각
- Change: PROTOCOL §14의 다음 값을 확정한다(M1-3).
  - **`L` = 32.** §6.1의 상한, 즉 채널 배정의 크기인 32채널에 도달했다. 탐색과 확인의 28회 실행이 모두 통과했다. 8채널부터 32채널까지 올린 뒤, 32채널에서 3회 더 확인했다(§10).
  - **본편 채널 집합**(§6.1): {1, 2, 4, 8, 12, 16, 32}
  - **비포화 경계**(§6.2, A7과 함께): 본편 조건에서 최대 31채널, 보조 조건에서 최대 5채널. 본편 채널 집합에서 포화 지점은 32뿐이다. 32 × 5 = 160 > 4661/30 ≈ 155.37
  - **채널-영상 배정**(§3): [stream-server/assignment_m1.txt](../stream-server/assignment_m1.txt). 채널 n은 영상 ((n − 1) mod 4) + 1을 재생한다. ch1 ①, ch2 ②, ch3 ③, ch4 ④, ch5 ①, 이런 식으로 ch32까지.
  - **냉각 구성**(§12): 방열판과 상시 동작하는 미니 팬(브랜드 없음, AliExpress). M1-3의 모든 세션과 같다. 이 구성으로 주변 온도 26 °C에서 열 확인을 통과했다.
  - **§3의 반복 재생 검사**는 영상 4개 모두 통과했다.
  - **이 세션들이 실행을 판정하는 방식:** `tools/stream_session.py`. 실행 전에 [docs/notes/calibration.ko.md](../docs/notes/calibration.ko.md)에 고정했다.
    - §9.3 규칙 4는 송출 서버 로그로 판정한다. 로그 시각은 1초 단위다. 예열 시작과 측정 끝을 각각 초 단위로 내림하고, 그 사이의 초에 찍힌 줄을 센다. 측정 끝 시각이 속한 초에서는 보드의 연결 종료를 세지 않는다. 벤치가 측정 직후 연결을 끊기 때문이다.
    - 외부 장애(§9.3): 입력이 일찍 끝나거나 서버 로그를 읽을 수 없으면 그 실행은 무효다. 송출 서버가 죽었거나, 서버 로그를 읽을 수 없거나, PC 시계가 보드보다 5 ms 넘게 늦거나 100 ms 넘게 빠르면 세션도 멈춘다.
    - 열 확인에서 규칙 1은 무효 조건이 아니라 확인 기준이다. 예열을 포함한 모든 1초 샘플을 본다.
  - **시간:** 2026-11-03 02:19 UTC부터 보드는 Wi-Fi로 `ntp.ubuntu.com`에 맞추는 대신, 유선 링크로 송출 PC의 chrony에 맞춘다. 04:10 UTC부터는 늦어도 64초마다 PC에 시간을 묻는다. 두 기기 모두 NTP 동기 상태를 유지한다.
- Reason: 2026-11-03의 M1-3 세션들이다. 각 세션은 10분 대기, 냉각 대기 관문, 실행마다의 환경 기록을 거쳤다(§9.1, §9.2). 냉각은 위와 같다.

  | 세션 | 시각(UTC) | 코드, 수정 기록 | 주변 온도 | 결과 |
  |---|---|---|---|---|
  | [`L`](../results/calibration/20261103T000647Z_limit) | 00:16–01:51 | `9553f70`, A7 | 24 °C | 28회 모두 유효하고 통과. 드리프트 ≤ 0.033 s. 디코더 드롭 없음 |
  | [반복 재생 검사](../results/check/20261103T032849Z_loop) | 03:38–03:51 | `5c9bc80`, A8 | 헤더에는 25 °C. 운영자가 03:50에 26 °C로 보고 | 4회 모두 유효. 측정 구간마다 반복 지점 3~5개(영상 길이로 계산). 디코더 드롭 없음 |
  | [열 확인](../results/calibration/20261103T035136Z_thermal) | 04:01–04:08 | `5c9bc80`, A8 | 26 °C | 2회 모두 유효(규칙 3은 5채널 실행에만 적용. 32채널은 포화). 고정값 아래로 내려간 클럭 샘플 없음. 32채널에서 `soc-thermal` 최고 67.5 °C(트립 75 °C) |

  - **시간:** PC 시계에서 보드 시계를 뺀 차이가 +8 ms(PC가 빠름)에서 −5 ms 너머까지 흔들렸다. 02:10 UTC에 이 때문에 첫 반복 재생 검사가 유효한 실행 2회 뒤에 멈췄고, 첫 열 확인은 첫 실행 전에 멈췄다.
    - 보드를 PC 시간에 맞춘 뒤 돌린 세션들은 0 ~ −3 ms를 기록했다. 허용 범위 안이다.
    - 04:10 UTC에 폴링 간격을 바꾼 뒤 `timesyncd`가 보고한 차이는 −0.14 ms다(손으로 읽었고 기록은 남지 않음). 그 뒤로는 아직 세션을 돌리지 않았다.
  - **빌드 결함:** 그 뒤의 두 세션(02:25 UTC의 반복 재생 검사, 03:01 UTC의 열 확인)은 모든 실행이 끝에서 죽었다(종료 코드 −11).
    - `tools/deploy_board.sh`가 파일마다 원래 시각을 복원했다. 홀드아웃 검사를 위한 `main` 빌드가 `pipeline.cpp`와 `report.cpp`를 `main` 버전으로 다시 컴파일했다. 이어서 이 브랜치를 배포하자 두 파일이 자기 버전으로 돌아왔지만 시각이 더 오래돼서, make가 다시 컴파일하지 않았다. 커밋 정의가 바뀐 도구들(`bench_main` 포함)은 이 브랜치의 `RunResult`로 다시 컴파일됐다. 두 버전의 오브젝트가 한 실행 파일에 섞였다.
    - 커밋 `5c9bc80`부터는 복사한 파일이 압축을 푼 시각을 갖는다. 그래서 배포할 때마다 복사한 파일을 모두 다시 컴파일한다.
    - `L` 세션은 영향받지 않았다. 28회 실행이 모두 정상 종료했고, `summary.csv`에 이 브랜치의 `pipeline.cpp`와 `report.cpp`만 쓰는 `warmup_start_utc`와 `window_*_utc`가 `runs.tsv`와 맞는 시각으로 들어 있다.
    - A8의 홀드아웃 검사도 영향받지 않았다. `tools/equivalence_dump`는 `RunResult`, `run_pipeline`, 결과 기록 함수를 쓰지 않으며, `main`과 이 브랜치 사이에 다른 `src/` 파일 4개는 이것들과 이것들이 쓰는 시계 함수만 바꾼다.
    - 이 결함은 이전 빌드의 오브젝트보다 오래된 파일, 그것도 다른 소스에서 온 파일을 배포할 때만 생긴다. 이전 빌드들의 배포 기록은 남아 있지 않다. 그 결과들에는 각자의 검사가 있다. M1-4의 세 실행은 같은 바이트를 냈고 하네스 검사가 통과했으며, 모든 `P_cap` 실행은 출력 검사를 통과했다(A7, `tools/calib_npu.cpp`).
  - **다시 한 검사:** 반복 재생 검사와 열 확인은 각각 세 번 실행했다.
    - 첫 반복 재생 검사의 유효한 실행 2회는 쓰지 않는다. 검사 전체를 새 세션에서 다시 했다.
    - 무효 ×2 실행만 있는 세션은 결과가 없는 것이다. 냉각 구성 변경을 요구하는 §12의 실패가 아니다. 이런 캘리브레이션 검사는 `freeze-m1` 전에 다시 한다(§15).
  - 멈췄거나 무효였던 네 세션은 모두 보관하고 표시한다([docs/notes/calibration.ko.md](../docs/notes/calibration.ko.md#멈췄거나-무효였던-세션)). 그 실행들은 어느 것도 세지 않는다.
  - PROTOCOL은 주변 온도에 한계를 두지 않는다. `P_cap`은 28 °C에서, 이 세션들은 24~26 °C에서 실행했다.
- Affected data: 없음. `L` 세션은 시간 설정을 바꾸기 전에 실행했고, 시계 차이는 +4 ~ +8 ms로 허용 범위 안이었다. A8의 홀드아웃 검사는 로컬 파일을 읽으며 송출 PC를 쓰지 않는다.
- Release: 없음(`freeze-m1` 이전)

## A10 — 2026-11-03 — `freeze-m1`의 소프트웨어, 빌드, 모델
- Change: PROTOCOL §14에서 `freeze-m1` 전에 정해야 하는 값 중 남은 것을 확정하고, 변형 A 디코더의 스레드 설정을 정한다. 같은 시점에 정하는 채널-영상 배정은 A9가 확정했다.
  - **기준 파일:** [docs/notes/inventory_m1.txt](../docs/notes/inventory_m1.txt)(SHA-256 `f6e5a78a15e5fecbae5ab5c132d866dcddd8eaec9562c48d6413d83f97f934dd`). 새 도구 `tools/inventory.sh`가 보드에서 썼다.
    - 보드의 버전, 빌드, 플래그와 모델의 SHA-256을 담는다. 동적 링커가 `bench_main`에 대해 찾는 모든 공유 라이브러리(`ldd`)의 SHA-256도 패키지·버전과 함께 담는다.
    - 아래의 다른 값은 이 항목이 가리키는 파일들에 있다. 기준 파일이 담는 값에 대해서는 파일이 우선한다.
  - **디코더**(§5.1):
    - **빌드:** [ffmpeg-rockchip](https://github.com/nyanmisaka/ffmpeg-rockchip) 커밋 `8b4385e985c0a50bf7d3f96cf3cb6d0b63c53e6c`(브랜치 7.1). `tools/setup/build_ffmpeg_rockchip.sh`가 보드에서 gcc 13.3.0으로 빌드해 `/opt/ffmpeg-rockchip`에 설치했다.
      - 구성: `--enable-shared --disable-static --disable-doc --enable-version3 --enable-libdrm --enable-rkmpp --enable-rkrga`, 그리고 설치 경로와 그 경로의 rpath
      - libavcodec 61.19.101, libavformat 61.7.103, libavutil 59.39.100, libswresample 5.3.100
    - **옵션, B와 C**(`src/core/video_source.cpp`): `h264_rkmpp`를 `afbc=off`로 연다. 이 옵션만 명시적으로 설정한다. 나머지 옵션은 기본값 그대로다. `deint` true, `fast_parse` true, `buf_mode` half
      - `deint`는 인터레이스 입력에 IEP 디인터레이서를 켠다. 준비한 영상은 프로그레시브다. `prepare_videos.sh`는 x264에 인터레이스 부호화를 요청하지 않으며, 2026-11-03에 ffprobe가 영상 4개 모두 `field_order` progressive로 보고했다(기록은 남기지 않음).
      - 출력은 `get_format`에서 고르는 `AV_PIX_FMT_DRM_PRIME`이고, `extra_hw_frames=8`이며, `hw_device_ctx`는 쓰지 않는다(§5.1, §8).
    - **입력:** RTSP는 TCP로 받고(`rtsp_transport=tcp`), `timeout=5000000`(5초)을 준다. 로컬 파일(`file_source`)은 옵션 없이 연다. 그 밖의 디먹서·디코더 옵션은 설정하지 않는다.
    - **A:** libavcodec 자체의 `h264` 디코더. 옵션은 기본값이되 `thread_count = 0`이다. 즉 자동 스레딩으로, libavcodec이 affinity 마스크의 CPU(cpu4–7, §8)에서 스레드 수를 정한다.
      - **코드 변경:** 코드는 "자동 스레딩"이라고 적었지만 `thread_count`를 설정하지 않았다. 그래서 A는 libavcodec 기본값인 스레드 1개로 디코딩했을 것이다. "자동"은 `ffmpeg` 명령줄 도구의 기본값일 뿐이다. 이제 코드가 원래 의도대로 값을 설정한다. A 실행은 아직 없었다.
  - **라이브러리와 드라이버**(§2):

    | 구성 요소 | 버전 |
    |---|---|
    | OS, 커널 | Ubuntu 24.04.4 LTS; 6.1.141 `#16 SMP Thu Dec  4 14:51:28 CST 2025` |
    | 부트 펌웨어 | `ddr-v1.18-9fa84341ce,bl31-v1.49,bl32-v1.19,uboot-44f011c4ba-07/17/2025`(`/proc/cmdline`) |
    | RKNPU 드라이버, RGA 드라이버 | v0.9.8; v1.3.10 |
    | librknnrt | 2.3.2(`429f97ae6b@2025-04-09T09:09:27`), SHA-256 `d31fc19c85b85f6091b2bd0f6af9d962d5264a4e410bfb536402ec92bac738e8`. 패키지가 아니라 보드 이미지에 들어 있던 파일이다. |
    | RKNN 헤더 | rknn-toolkit2 v2.3.2, `/opt/rknpu2/include`(`tools/setup/fetch_rknn_headers.sh`) |
    | librga | 1.10.3, 패키지 `librga2` 2.2.0-1 |
    | MPP | 패키지 `librockchip-mpp1` 20251211-1 |
    | libdrm | 패키지 `libdrm2` 2.4.125-1ubuntu0.1~24.04.2 |
    | OpenCV | 4.6.0, A2에서 확정 |
    | C·C++ 런타임 | `libc6` 2.39-0ubuntu8.9; `libstdc++6` 14.2.0-4ubuntu2~24.04.1 |

    - **실행 중에 여는 라이브러리:** B, C, A를 실행했을 때(1채널, `file_source`, 2026-11-03) `ldd` 목록 밖의 라이브러리는 하나만 열렸다. TBB의 메모리 할당기 `libtbbmalloc`으로, `libtbb`가 `dlopen`으로 연다. 기준 파일은 이것도 담는다.
  - **모델**(§4): `models/yolov8n_i8.rknn`, SHA-256 `04a76611a785802b08703192455994e07bbfe4531821ae7ed35faf78fe1ec1ee`. [models/README.ko.md](../models/README.ko.md)에 적힌 릴리스에 있다.
    - `yolov8n.onnx`(SHA-256 `0c8716701f471067932b797eeb67c8e5db47c693c2557c881d7679ec12e21bc5`)에서 변환했다. rknn_model_zoo v2.3.2가 배포하는 export이며, `tools/model/convert_in_container.sh`에 적힌 URL에서 내려받았다.
    - 변환에는 rknn_model_zoo 커밋 `bad6c7334531becaf90a561988519b7bec34d0ab`(태그 v2.3.2)의 `examples/yolov8/python/convert.py`와 rknn-toolkit2 2.3.2를 썼다.
    - 설정: 대상 `rk3588`, INT8, `mean 0, std 255`. `convert.py`는 그 밖에 아무것도 설정하지 않으므로, 나머지 설정은 모두 툴킷의 기본값이다.
    - **양자화 데이터셋:** 그 커밋의 `datasets/COCO/coco_subset_20.txt`가 나열하는 COCO 이미지 20장. [models/coco_subset_20.sha256](../models/coco_subset_20.sha256)에 목록 파일과 이미지마다의 SHA-256이 있다.
  - **`rknn_init` 플래그**(§8): 모든 변형에서 `rknn_init(&ctx, model, size, 0, NULL)`, 즉 플래그 0이고 확장 인자가 없다.
    - 따라서 아무 플래그도 켜지 않는다. `RKNN_FLAG_ASYNC_MASK`도, 어떤 `RKNN_FLAG_DISABLE_FLUSH_*` 플래그도 없다.
    - `rknn_run(ctx, NULL)`은 추론이 끝날 때까지 반환하지 않는다.
    - 이어서 `rknn_set_core_mask(ctx_i, RKNN_NPU_CORE_0 << i)`로 컨텍스트 i를 NPU 코어 i에 고정한다(i = 0, 1, 2).
    - 플래그는 `src/core/npu.h`의 상수 `NpuContexts::kInitFlags`다.
  - **컴파일러와 플래그:** 보드에서 `tools/deploy_board.sh`로 빌드한다. g++ 13.3.0(Ubuntu 13.3.0-6ubuntu2~24.04.1), GNU ld 2.42, CMake 3.28.3, GNU Make 4.3
    - CMake 빌드 유형은 Release다. 모든 C++ 파일을 `-O3 -DNDEBUG -std=gnu++17 -Wall -Wextra -fno-omit-frame-pointer -fno-optimize-sibling-calls -pthread`로 컴파일한다.
    - `-march`나 `-mcpu`는 주지 않으므로 GCC의 기본 대상이 적용된다.
    - `bench_main`은 `-O3 -DNDEBUG -pthread`와 rpath `/lib:/opt/ffmpeg-rockchip/lib`로 링크한다.
  - **송출 PC**(§3): mediamtx v1.21.2(바이너리 SHA-256 `0ffb7f18210d08ee38cb16668c4f3a2c96622e69d401119c0026d078a2b89523`), ffmpeg 7.0.2(johnvansickle.com 정적 빌드, 바이너리 SHA-256 `e7e7fb30477f717e6f55f9180a70386c62677ef8a4d4d1a5d948f4098aa3eb99`). §14 목록에는 없지만 여기서 함께 확정한다.
  - **검사:** 이제 `tools/stream_session.py`는 세션을 시작할 때마다 다음을 한다.
    - 클럭을 고정하기 전에 `tools/inventory.sh`를 실행하고 그 출력을 `inventory.txt`로 남긴다. 출력이 기준 파일과 다르면 첫 실행 전에 멈추고, 차이를 `inventory.diff`에 쓴다.
    - 기준 파일이 이 항목의 파일이 아니어도 멈춘다. 그 SHA-256은 도구 안에 상수로 있다.
    - 송출 서버를 시작하기 전에 PC의 mediamtx와 ffmpeg의 SHA-256을 위 값과 비교하고, 하나라도 다르면 멈춘다.
    - §12의 모든 세션은 같은 검사를 하고, 다르면 멈춰야 한다.
    - 세션의 `inventory.txt`가 그 세션의 모든 실행에 대한 컴파일러와 플래그의 기록(§2)이다. `tools/deploy_board.sh`는 세션 밖에서 빌드하며, 실행마다 헤더에 코드 커밋이 적힌다.
  - 이 값 중 무엇이든 바꾸려면 수정 기록이 필요하고(§15), 그 기록에 영향받는 데이터를 적는다.
- Reason: §14는 이 값들을 `freeze-m1` 전에 확정하고, §2는 그 시점에 버전, 빌드, 컴파일러와 플래그를 고정한다.
  - **기준 파일:** 2026-11-03에 이 항목의 도구로 썼다. 이 항목의 `tools/inventory.sh`와 기준 파일을 가진 커밋 `bd66404`의 빌드(`-dirty` 아님)에서 `tools/inventory.sh docs/notes/inventory_m1.txt`가 "same"을 보고했다.
    - 출력은 커밋이나 수정 기록 번호(`CODE_COMMIT`, `LATEST_AMENDMENT`)를 읽지 않는다. 설계상 `CMakeLists.txt`와 저장소 위치에는 좌우된다.
    - 이전 버전의 도구는 PR #14의 머지인 `cf7e40b`의 빌드에서도 같은 값을 냈다.
  - **`results/`의 첫 실행 이후 패키지 변경 없음:**
    - dpkg 기록상 마지막 변경은 2026-10-30 07:26 UTC다. perf용 디버깅 심볼과 함께 `libc6`가 2.39-0ubuntu8.7에서 8.9로 올라갔다(A6).
    - 패키지가 아닌 파일들의 수정 시각은 이렇다. ffmpeg-rockchip 라이브러리 2026-10-30 01:33 UTC, RKNN 헤더 그날 04:02 UTC, librknnrt 2026-08-13. 이전 해시는 없다. 이전 실행들은 버전 문자열만 기록했고, 그 값은 같다.
    - `results/`의 모든 실행은 2026-11-02 이후에 시작했다.
    - `unattended-upgrades`는 설치돼 있지 않고, APT 주기 작업도 설정돼 있지 않다.
    - `docs/notes/`의 2026-10-30 증거(M1-0 ~ M1-2) 중 일부는 `libc6` 변경 전에 만들었다. 그중 이 값들이 구속하는 측정은 없다.
  - 양자화 이미지는 rknn_model_zoo의 그 커밋에서 내려받아 해시를 계산했다. 2026-10-30의 변환도 같은 커밋을 썼다(`models/yolov8n.sha256`).
- Affected data: 없음. 바뀐 것은 A의 디코더 스레드 설정뿐이고, A 실행은 아직 없었다.
- Release: 없음(`freeze-m1` 이전)
