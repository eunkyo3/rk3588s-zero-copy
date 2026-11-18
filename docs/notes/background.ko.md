[English](background.md)

# 배경: 기존 Rockchip 파이프라인과 측정값

RK3588 계열에서 디코딩된 영상 프레임을 RKNN NPU에 넣는 방법에 대해 다른 자료가 무엇을 말하는지, 어떤 처리량 수치를 공개하는지, 이 프로젝트가 겪은 런타임 동작이 문서화돼 있는지를 정리한다. 이슈 #72에 답한다. 영어 문서와 내용이 다르면 [영어 문서](background.md)가 우선한다.

- **자료:** 1차 자료만 쓴다. Rockchip과 airockchip의 저장소와 문서, Rockchip의 RK3588S 데이터시트, ffmpeg-rockchip, GStreamer 문서다. 목록은 [출처](#출처)에 있다. GitHub 링크는 태그나 커밋에 고정했다.
- **버전:** rknn-toolkit2와 rknn_model_zoo는 이 프로젝트가 쓰는 `v2.3.2`다([env.ko.md](env.ko.md)). librga에는 보드의 1.10.3에 해당하는 태그가 없다. 이 노트는 태그 `v1.10.0`을 인용하고, 한 번은 그 뒤의 커밋을 인용한다. ffmpeg-rockchip은 이 프로젝트가 빌드하는 커밋 `8b4385e`다.
- **2026-11-18에 읽었다.** 버전이 없는 페이지는 바뀔 수 있다.
- **구분:** 각 절은 먼저 자료가 말하는 것을, 그다음 이 프로젝트가 한 것을 노트 링크와 함께 적는다. 이 노트는 이 프로젝트의 수치를 다른 수치와 우열로 견주지 않는다.
- **등록하지 않았다.** 여기 있는 것은 등록된 값이나 판정을 바꾸지 않는다.

## 디코딩된 프레임을 NPU에 넣기

### RKNN의 제로 카피 API

자료가 말하는 것:
- **API 묶음 두 가지** ([User Guide][ug] §1.1.3, §5.2.1):
  - 일반 API(`rknn_inputs_set`, `rknn_outputs_get`, §3.3)는 프레임마다 입력을 NPU 메모리로 복사한다. 정규화, 양자화, 레이아웃 변환, 역양자화는 CPU에서 한다. 입력이 제로 카피 정렬 조건을 만족하면 정규화와 양자화는 NPU로 옮겨간다.
  - 제로 카피 API는 미리 할당한 메모리를 쓴다. "including memory created by NPU or other frame, such as DRM". 정규화, 양자화, 추론은 NPU에서 한다. "The data layout conversion and dequantization of the NPU output are executed on the CPU or NPU."
  - 가이드의 예: "decoded data can be directly imported into the NPU by zero-copy API".
  - 가상 주소만 있는 입력은 일반 API만 쓸 수 있다. 물리 주소나 fd가 있는 입력은 둘 다 쓸 수 있다.
  - "General API and zero-copy API interface cannot be called together."
- **호출 방식 세 가지** (§5.2.2):
  - 런타임이 입력과 출력을 할당한다(`rknn_create_mem`);
  - 호출하는 쪽이 할당한다(`rknn_create_mem_from_fd`, `rknn_create_mem_from_phys`);
  - 호출하는 쪽이 가중치와 내부 메모리까지 할당한다(`rknn_set_weight_mem`, `rknn_set_internal_mem`).
  - `rknn_set_io_mem`이 메모리 객체를 바인딩한다. 한 번 호출한 뒤 `rknn_run`을 반복해서 실행한다(§5.2.3).
- **native 레이아웃** (§5.2.3):
  - 입력과 출력은 `RKNN_QUERY_NATIVE_INPUT_ATTR`과 `RKNN_QUERY_NATIVE_OUTPUT_ATTR`로 조회하며, 이것이 "the optimal hardware efficiency"를 준다.
  - 표 5-6: RK3588의 int8 모델에서 4차원 제로 카피 출력은 NCHW, NC1HWC2, NHWC일 수 있다.
  - 사용자가 NCHW로 필요로 하는 NC1HWC2 출력은 "externally" 변환한다.
- **Rockchip의 예제:**
  - rknn-toolkit2의 [`rknn_zero_copy` README][tk2-zc]는 `rknpu2/examples/rknn_api_demo`의 데모 네 개를 든다. [`rknn_create_mem_demo`][tk2-mem]는 입력을 `memcpy`로 `rknn_create_mem` 버퍼에 복사한다. [`rknn_create_mem_with_rga_demo`][tk2-rga]는 RGA(`imresize`)가 그 버퍼의 fd에 쓰게 한다. 나머지 둘은 MMZ 라이브러리로 할당해 `rknn_create_mem_from_fd`와 `rknn_create_mem_from_phys`로 등록한다.
  - rknn_model_zoo의 [`yolov8_zero_copy.cc`][mz-zc]: UINT8로 바인딩한 `rknn_create_mem` 입력의 fd에 RGA가 레터박스를 쓴다. [`image_utils.c`][mz-img]는 폭이 16의 배수가 아니면 CPU로 넘어간다. 출력은 native 속성으로 바인딩하고, 예제가 직접 CPU에서 NC1HWC2를 NCHW로 바꾼다(`NC1HWC2_i8_to_NCHW_i8`).
  - 여기서 읽은 예제는 파일에서 정지 이미지를 읽는다. 비디오 디코더의 프레임을 받는 예제는 없다.

이 프로젝트:
- **C**는 호출하는 쪽이 할당하는 방식이다. RGA가 디코더의 DRM PRIME dma-buf에서 읽어, `rknn_create_mem_from_fd`로 등록한 dma-heap 슬롯에 쓴다([spike.ko.md](spike.ko.md), [AMENDMENTS A1](../../prereg/AMENDMENTS.ko.md)).
- **B**는 `rknn_create_mem`에서 받아 `rknn_set_io_mem`으로 바인딩한 입력에 `memcpy`로 복사한다(A3).
- **둘 다** 출력을 native 레이아웃이 아니라 NCHW INT8로 바인딩한다([아래](#이-프로젝트가-겪은-런타임-동작)).

### ffmpeg-rockchip의 RGA 필터

자료가 말하는 것, 커밋 `8b4385e` 기준:
- **프로젝트:** [README][ffr-readme]는 이 프로젝트를 "an UNOFFICIAL fork of FFmpeg"라고 하며, Rockchip의 소유가 아니라고 밝힌다.
  - MPP 디코더와 인코더, 그리고 RGA 필터 세 개가 있다: `scale_rkrga`("video resizer and format converter"), `vpp_rkrga`(크기 조정, 자르기, 전치), `overlay_rkrga`(합성).
  - "Zero-copy DMA in above stages"라고 적는다.
  - 최대 해상도와 프레임률은 "the datasheet or TRM"을 보라고 한다.
- **`scale_rkrga`** ([vf_vpp_rkrga.c][ffr-vpp], [rkrga_common.c][ffr-common]):
  - DRM PRIME 프레임을 받아 DRM PRIME 프레임을 돌려준다. 필터가 직접 할당한 풀의 프레임에 쓴다.
  - 옵션에는 `w`, `h`, `format`, `force_original_aspect_ratio`, 그리고 RGA3나 RGA2 코어를 고르는 `core`가 있다. RGB24도 지원 형식에 있다.
  - 이 노트는 여백(padding) 옵션을 찾지 못했다. 레터박스에는 다른 단계가 필요하다.
- **DMA32:** MPP 장치 컨텍스트는 장치 옵션 `dma32=0`이 없으면 `MPP_BUFFER_FLAGS_DMA32`로 할당한다([hwcontext_rkmpp.c][ffr-hwctx]).

이 프로젝트:
- 이 포크의 `h264_rkmpp` 디코더를 쓴다([env.ko.md](env.ko.md#디코더)). librga는 직접 호출해, RGA가 NPU가 읽는 슬롯에 바로 쓰게 한다.
- PROTOCOL §5.4는 `scale_rkrga`를 C의 마지막 대체 경로로 정했다. A1은 대체 경로가 필요 없다고 기록했으므로 `scale_rkrga`는 시험하지 않았다.

### GStreamer

자료가 말하는 것:
- **GStreamer:** [플러그인 문서][gst-doc]에는 Rockchip MPP나 RGA용 엘리먼트가 없다.
- **Rockchip의 플러그인**은 [JeffyCN/mirrors][gst-tree]의 `gstreamer-rockchip` 브랜치로 공개돼 있다.
  - 이 브랜치의 커밋은 대부분 rock-chips.com 주소에서 왔다. 가장 최근 커밋은 다른 기여자의 것이다.
  - `rockchip-linux` GitHub 조직에는 그 이름의 저장소가 없다.
- **커밋 `bff0659` 기준:**
  - 플러그인 `rockchipmpp`는 디코더 `mppvideodec`, JPEG 디코더, 그리고 H.264, H.265, VP8, JPEG 인코더를 등록한다([gstmpp.c][gst-mpp]).
  - 별도의 RGA 엘리먼트는 없다. 디코더가 크기 조정, 자르기, 회전을 하고(`width`, `height`, `crop-rectangle`, `rotation`), `mppvideodec`가 픽셀 형식을 바꾼다(`format`)([gstmppdec.c][gst-dec], [gstmppvideodec.c][gst-vdec]).
  - 플러그인을 librga와 함께 빌드하면(`rga` 옵션, [meson_options.txt][gst-opt]) 이 변환은 librga를 먼저, 그다음 GPU를 시도한다.
  - 플러그인에 NPU용 엘리먼트는 없다.

이 프로젝트는 GStreamer를 쓰지 않는다.

## 공개된 측정값

| 출처 | 수치 | 밝힌 조건 | 밝히지 않은 것 |
|---|---|---|---|
| [RK3588S 데이터시트][ds] Rev 1.5, §1.2.4 | H.264 디코딩 "8K@30fps (7680x4320)"; H.265와 VP9는 8K@60fps | 코덱, 프로파일, 레벨, 해상도, 프레임률; "Multi-channel decoder in parallel for less resolution" | 비트레이트; 낮은 해상도의 스트림 수 |
| 같은 문서, §1.2.6 | NPU "up to 6 TOPS", 코어 3개 | 없음 | 데이터 형식, 클럭, 모델 |
| [librga 가이드][rga-guide] v1.10.0, "Design Index" | RK3588의 RGA3: 3 pixels/cycle "by pass", 2 "scale"; RGA2-Enhance: 2 | "theoretical data"; 실제 속도는 대역폭과 클럭에 따라 다름 | RK3588의 RGA 클럭; 측정 시간 |
| [rknn_model_zoo README][mz-readme] v2.3.2, "Model performance benchmark(FPS)" | RK3588, 코어 1개: YOLOv8n 73.5 FPS, YOLOv10n 61.2 FPS | INT8; 입력 [1, 3, 640, 640]; "maximum NPU frequency"; 추론만, 전처리와 후처리 제외("if not specified") | 보드, 런타임 버전, 시간 측정 방법, CPU와 DDR 클럭 |

- **librga의 측정 시간**([FAQ][rga-faq] Q1.1)은 Android 11의 RK3566(RGA2)에서 복사만 잰 것이다. 계산식 예시는 RGA3에 4 pixels/cycle을 쓰는데, Design Index는 3과 2를 준다.
- **모델 주**는 데모를 RKNPU2 SDK ≥ 2.3.2로 검증했다고 한다. 표를 어느 런타임으로 쟀는지는 밝히지 않는다.
- **찾지 못한 것:** MPP `1.1.0`의 [개발자 가이드][mpp-guide]에 있는 RK3588 처리량 수치; 데이터시트의 병렬 디코딩 채널 수.

이 프로젝트의 측정값, 이 프로젝트의 조건에서:
- **디코더:** 1080p30 H.264 32채널을 실시간으로 디코딩했다. 절차의 상한이다([calibration.ko.md](calibration.ko.md#l-결과)). 4K30에서는 짧은 디코딩 전용 점검이 10채널은 따라갔고 12채널은 따라가지 못했다([AMENDMENTS A11](../../prereg/AMENDMENTS.ko.md)).
- **RGA:** 1080p NV12 프레임에 대한 C의 레터박스가 프레임당 1.80 ms였다. 동기 호출, 채널 1개, 기본 거버너다([spike.ko.md](spike.ko.md)).
- **NPU:** YOLOv8n INT8, 코어 1개에서 고정 클럭으로 초당 70.73회 추론했다. 각 단계는 입력을 `rknn_set_io_mem`으로 바인딩한다([calibration.ko.md](calibration.ko.md#p_cap과-npu-원시-상한)). M4의 세션이 YOLOv8n과 YOLOv10n을 다시 쟀다([M4.ko.md](../report/M4.ko.md#npu-용량)).

조건이 비교 가능한지:
- **NPU:** 모델 주의 행과 이 프로젝트의 코어 1개 속도는 모델 이름, 입력 크기, INT8, 코어 1개가 같다. 모델 주의 표는 보드, 런타임, 클럭, 시간 측정 방법을 밝히지 않는다. 그래서 비교 가능한지 알 수 없다.
- **디코더:** 데이터시트는 코덱별로 스트림 하나의 최대치를 준다. 이 프로젝트는 병렬 1080p와 4K 스트림의 수를 센다. 비교할 수 없다.
- **RGA:** Design Index는 클럭 없이 사이클당 이론 픽셀 수를 준다. 스파이크의 시간은 호출과 동기화를 포함한다. 비교할 수 없다.

## 이 프로젝트가 겪은 런타임 동작

| 이 프로젝트에서의 동작 | 다른 곳에 문서화돼 있나? |
|---|---|
| **DMA32 힙.** 디코더는 `system-dma32`에서 할당하고, C의 슬롯은 `system-uncached-dma32`에서 온다(A1, [spike.ko.md](spike.ko.md)). 이 프로젝트는 DMA32 힙만 썼고 4 GiB 위의 메모리는 시험하지 않았다. | **RGA에 대해서는 그렇다.** librga [가이드][rga-guide]: RK3588의 RGA2는 32비트 IOMMU, RGA3는 40비트 IOMMU이고, "RGA equipped with 32bit IOMMU only supports 0~4G memory space". [FAQ][rga-faq] Q1.9와 Q4.5: 멀티코어 드라이버에서는 4 GiB 위 메모리에 대한 RGA2 작업이 실패한다("RGA2 only support under 4G memory!"). `rga_allocator_dma32_demo.cpp`처럼 4 GiB 아래에 할당하라고 한다. RK3588에서 색 채우기와 planar YUV는 RGA2에만 있다. ffmpeg-rockchip은 DMA32 플래그를 기본으로 켠다([위](#ffmpeg-rockchip의-rga-필터)). |
| **RGA2와 RGA3의 스케일링이 다르고, RGA3는 `interp`를 무시한다.** C는 RGA를 RGA3 코어에 고정한다(A1). RGA3의 1/3 커널은 OpenCV `INTER_AREA`와 한 픽셀 어긋난 3×3 상자다(A4, [preprocess.ko.md](preprocess.ko.md#측정한-rga3-스케일러)). | **일부만.** [FAQ][rga-faq] Q2.22: RK3588에서 RGA2와 RGA3 코어는 스케일링할 때 샘플링이 달라 영상이 밀리며, 코어를 지정하라고 권한다. [가이드][rga-guide] v1.10.0의 `imresize`: "Only hardware version RGA1/RGA1 plus supports interpolation configuration." librga 1.10.6인 [`2b32edc`의 가이드][rga-guide-main]는 RGA3의 축소를 "average"로 적는다. 커널의 정확한 어긋남은 찾지 못했다. |
| **`rknn_inputs_set`은 `rknn_set_io_mem`으로 바인딩한 출력을 쓰지 않는다**(A3). | **일반적인 말로만.** [User Guide][ug] §5.2.1: "General API and zero-copy API interface cannot be called together." 이 프로젝트가 본 결과는 설명돼 있지 않다. |
| **native NC1HWC2 출력.** 이를 바인딩하면 `rknn_run`이 실패했다("unsupported tensor dtype … fallback cpu failed"). `pass_through`를 켜도 같았다. 이 프로젝트는 NCHW INT8로 바인딩하며, 추론 한 번에 약 1.2 MB다(A6, [spike.ko.md](spike.ko.md)). | **지원된다고 문서화돼 있다.** [User Guide][ug] 표 5-6은 RK3588의 int8 모델에 NC1HWC2를 들고, 모델 주의 [YOLOv8 예제][mz-zc]는 native 출력을 바인딩한다. 이 프로젝트는 그 예제를 돌리지 않았다. 이 노트는 차이를 설명하지 않는다. |
| **NCHW 변환을 어디서 하나**(A6의 열린 질문). | **정해지지 않았다.** [User Guide][ug] §1.1.3은 출력의 레이아웃 변환과 역양자화가 "on the CPU or NPU"에서 이뤄진다고 할 뿐, 바인딩에 따라 어느 쪽인지는 말하지 않는다. |
| **`librknnrt.so` 안에서 추론 한 번에 약 1,150번의 작은 복사.** B에서도 C와 같고, 512바이트를 넘는 것은 없다(A6, [preprocess.ko.md](preprocess.ko.md#cpu-복사-없음-증거-protocol-114)). | **찾지 못했다.** [User Guide][ug], [RKNNRT API 레퍼런스][rt], rknn-toolkit2의 GitHub 이슈 어디에도 없다. 가이드 §8.1.2는 제로 카피 API에서 "the input-output memory is directly accessed by the NPU"라고 하는데, 이는 텐서 데이터에 관한 말이다. |
| **UINT8 입력은 CPU 없이 변환된다.** UINT8 바인딩의 실행당 CPU는 INT8 pass-through 바인딩보다 많지 않았다([spike.ko.md](spike.ko.md)). | **그렇다.** [User Guide][ug] §1.1.3: 제로 카피 API에서는 정규화와 양자화를 NPU에서 한다. |
| **바인딩한 출력을 `rknn_mem_sync` 없이 `virt_addr`로 읽는다.** 이 프로젝트는 init 플래그를 쓰지 않고, 시작할 때 CPU가 출력을 일관되게 읽는지 확인한다([core.ko.md](core.ko.md)). | **헤더에 있다.** [`rknn_api.h`][rknn-h]는 `RKNN_FLAG_DISABLE_FLUSH_OUTPUT_MEM_CACHE`를 "Don't invalid output buffer cache"로 설명하며, 이 플래그를 쓰면 `virt_addr`를 읽을 때 `rknn_mem_sync`가 필요하다고 한다. |

- **이슈 검색:** rknn-toolkit2의 GitHub 이슈에서 `fallback cpu failed`, `unsupported tensor dtype`, `NC1HWC2`, `memmove`, `memcpy rknn_run`, `rknn_inputs_set rknn_set_io_mem`을, rknn_model_zoo의 이슈에서 `NC1HWC2`를 검색했다. A3나 A6의 동작, NC1HWC2 바인딩 실패를 보고한 이슈는 찾지 못했다. 검색 결과는 지원되지 않는 연산자, 다른 칩, 모델 변환에 관한 것이었다.

## 출처

Rockchip과 airockchip:
- [User Guide][ug]: `doc/02_Rockchip_RKNPU_User_Guide_RKNN_SDK_V2.3.2_EN.pdf`, rknn-toolkit2 `v2.3.2`.
- [RKNNRT API 레퍼런스][rt]: `doc/04_Rockchip_RKNPU_API_Reference_RKNNRT_V2.3.2_EN.pdf`, rknn-toolkit2 `v2.3.2`.
- [`rknn_api.h`][rknn-h], rknn-toolkit2 `v2.3.2`.
- rknn-toolkit2 `v2.3.2` 예제: [`rknn_zero_copy` README][tk2-zc], [`rknn_create_mem_demo.cpp`][tk2-mem], [`rknn_create_mem_with_rga_demo.cpp`][tk2-rga].
- rknn_model_zoo `v2.3.2`: [README][mz-readme], [`yolov8_zero_copy.cc`][mz-zc], [`utils/image_utils.c`][mz-img].
- librga: `v1.10.0`의 [개발자 가이드][rga-guide]와 [FAQ][rga-faq]; [`2b32edc`의 개발자 가이드][rga-guide-main].
- MPP: `1.1.0`의 [개발자 가이드][mpp-guide].
- [RK3588S 데이터시트][ds], Rev 1.5(2023-11-10), FriendlyElec의 [NanoPi R6C 위키][r6c-wiki]에서.

그 밖:
- ffmpeg-rockchip `8b4385e`: [README][ffr-readme], [`vf_vpp_rkrga.c`][ffr-vpp], [`rkrga_common.c`][ffr-common], [`hwcontext_rkmpp.c`][ffr-hwctx].
- GStreamer: [플러그인 문서][gst-doc]; Rockchip의 플러그인 [JeffyCN/mirrors `bff0659`][gst-tree]: [`gstmpp.c`][gst-mpp], [`gstmppdec.c`][gst-dec], [`gstmppvideodec.c`][gst-vdec], [`meson_options.txt`][gst-opt].

[ug]: https://github.com/airockchip/rknn-toolkit2/blob/v2.3.2/doc/02_Rockchip_RKNPU_User_Guide_RKNN_SDK_V2.3.2_EN.pdf
[rt]: https://github.com/airockchip/rknn-toolkit2/blob/v2.3.2/doc/04_Rockchip_RKNPU_API_Reference_RKNNRT_V2.3.2_EN.pdf
[rknn-h]: https://github.com/airockchip/rknn-toolkit2/blob/v2.3.2/rknpu2/runtime/Linux/librknn_api/include/rknn_api.h
[tk2-zc]: https://github.com/airockchip/rknn-toolkit2/blob/v2.3.2/rknpu2/examples/rknn_zero_copy/README.md
[tk2-mem]: https://github.com/airockchip/rknn-toolkit2/blob/v2.3.2/rknpu2/examples/rknn_api_demo/src/rknn_create_mem_demo.cpp
[tk2-rga]: https://github.com/airockchip/rknn-toolkit2/blob/v2.3.2/rknpu2/examples/rknn_api_demo/src/rknn_create_mem_with_rga_demo.cpp
[mz-readme]: https://github.com/airockchip/rknn_model_zoo/blob/v2.3.2/README.md
[mz-zc]: https://github.com/airockchip/rknn_model_zoo/blob/v2.3.2/examples/yolov8/cpp/rknpu2/yolov8_zero_copy.cc
[mz-img]: https://github.com/airockchip/rknn_model_zoo/blob/v2.3.2/utils/image_utils.c
[rga-guide]: https://github.com/airockchip/librga/blob/v1.10.0/docs/Rockchip_Developer_Guide_RGA_EN.md
[rga-faq]: https://github.com/airockchip/librga/blob/v1.10.0/docs/Rockchip_FAQ_RGA_EN.md
[rga-guide-main]: https://github.com/airockchip/librga/blob/2b32edcb97b601b25683e2941d888c8515da6d55/docs/Rockchip_Developer_Guide_RGA_EN.md
[mpp-guide]: https://github.com/rockchip-linux/mpp/blob/1.1.0/doc/Rockchip_Developer_Guide_MPP_EN.md
[ds]: https://wiki.friendlyelec.com/wiki/images/8/8b/Rockchip_RK3588S_Datasheet_V1.5-20231110.pdf
[r6c-wiki]: https://wiki.friendlyelec.com/wiki/index.php/NanoPi_R6C
[ffr-readme]: https://github.com/nyanmisaka/ffmpeg-rockchip/blob/8b4385e985c0a50bf7d3f96cf3cb6d0b63c53e6c/README.md
[ffr-vpp]: https://github.com/nyanmisaka/ffmpeg-rockchip/blob/8b4385e985c0a50bf7d3f96cf3cb6d0b63c53e6c/libavfilter/vf_vpp_rkrga.c
[ffr-common]: https://github.com/nyanmisaka/ffmpeg-rockchip/blob/8b4385e985c0a50bf7d3f96cf3cb6d0b63c53e6c/libavfilter/rkrga_common.c
[ffr-hwctx]: https://github.com/nyanmisaka/ffmpeg-rockchip/blob/8b4385e985c0a50bf7d3f96cf3cb6d0b63c53e6c/libavutil/hwcontext_rkmpp.c
[gst-doc]: https://gstreamer.freedesktop.org/documentation/plugins_doc.html
[gst-tree]: https://github.com/JeffyCN/mirrors/tree/bff0659a51c13585b3c9c9a68aa8c435beeb3fe1
[gst-mpp]: https://github.com/JeffyCN/mirrors/blob/bff0659a51c13585b3c9c9a68aa8c435beeb3fe1/gst/rockchipmpp/gstmpp.c
[gst-dec]: https://github.com/JeffyCN/mirrors/blob/bff0659a51c13585b3c9c9a68aa8c435beeb3fe1/gst/rockchipmpp/gstmppdec.c
[gst-vdec]: https://github.com/JeffyCN/mirrors/blob/bff0659a51c13585b3c9c9a68aa8c435beeb3fe1/gst/rockchipmpp/gstmppvideodec.c
[gst-opt]: https://github.com/JeffyCN/mirrors/blob/bff0659a51c13585b3c9c9a68aa8c435beeb3fe1/meson_options.txt
