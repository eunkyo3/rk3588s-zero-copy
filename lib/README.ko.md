[English](README.md)

# librkzc

이 저장소의 변형 C 경로(하드웨어 디코딩, RGA 레터박스, NPU 추론, 픽셀의 CPU 복사 없음)를 라이브러리로 만든 것이다. [docs/design/packaging.ko.md](../docs/design/packaging.ko.md)의 2단계와 3단계에 해당한다. 경로를 벤치마크에서 분리했고, OpenCV가 필요 없으며, 벤치마크가 종료하던 자리에서 오류를 반환하고, 멈출 때까지 실행하며, 재연결하고, 검출 결과를 내준다.

**상태:** C++ 인터페이스로 동작하는 검출기다. 설치형 프로그램, 패키지, 파이썬 바인딩은 아직 없다(4단계와 5단계). CPU 시간이나 지연은 측정한 것이 없다.

## 사용

```cpp
#include <rkzc/rkzc.h>

rkzc::Config cfg;
cfg.model = "models/yolov8n_i8.rknn";
cfg.sources = {"rtsp://10.88.0.1:8554/ch1", "rtsp://10.88.0.1:8554/ch2"};

rkzc::Status st;
auto det = rkzc::Detector::open(cfg, &st);
if (!det) { std::fprintf(stderr, "%s\n", st.message.c_str()); return 1; }

det->on_frame([](const rkzc::Frame& f) {
    for (int i = 0; i < f.count; i++) {
        const rkzc::Detection& d = f.detections[i];   // x, y, w, h는 원본 프레임의 픽셀 단위
        std::printf("ch %d: %s %.2f\n", f.channel, rkzc::class_name(d.cls), d.score);
    }
});
det->on_event([](const rkzc::Event& e) { /* 소스가 열렸다, 끊겼다, 열 수 없다 ... */ });
det->start();
// ...
det->stop();
```

- **샘플링:** `every_nth_frame`장 중 한 장을 추론한다(기본 6: 30 fps 스트림에서 초당 5회, 벤치마크의 주 조건).
- **모델:** rknn_model_zoo가 내보낸 YOLOv8과 YOLOv10. 모델의 출력 형식을 보고 고른다. 임계값은 측정에 쓴 값으로 고정이다.
- **소스:** RTSP(TCP)의 H.264 또는 파일. 스트림이 끝나거나, 실패하거나, 열리지 않으면 1초 뒤, 그다음 2초 뒤, 최대 30초 간격으로 다시 시도한다. 파일은 끝나면 그 채널이 끝나고, `loop_files`를 켜면 처음부터 다시 재생한다.
- **콜백**은 라이브러리의 스레드에서 한 번에 하나씩 불린다. 콜백이 느리면 파이프라인이 밀리고, 샘플링된 프레임이 버려진다(`stats()`가 센다).

인터페이스는 [include/rkzc/rkzc.h](include/rkzc/rkzc.h)가 전부다. [examples/detect_jsonl.cpp](examples/detect_jsonl.cpp)는 검출 결과를 JSON 줄로 출력하는 예제다.

## 빌드와 테스트

보드에서 `scripts/setup_board.sh`를 실행한 뒤에 한다(ffmpeg-rockchip, librknnrt, RKNN 헤더가 필요하다).

```sh
cmake -S lib -B build-lib \
    -DRKZC_TEST_MODEL=$PWD/models/yolov8n_i8.rknn \
    -DRKZC_TEST_VIDEO=/path/to/2_seconds.mp4 \
    -DRKZC_TEST_OBJECTS_VIDEO=/path/to/video_with_objects.mp4 \
    -DRKZC_DEMO_DETECT=$PWD/build/demo_detect
cmake --build build-lib -j4
(cd build-lib && ctest --output-on-failure)
```

`librkzc.so`, `rkzc-check`(이 보드가 모델과 소스를 돌릴 수 있는지 확인), `rkzc-jsonl`(예제)이 만들어진다.

## 측정한 코드를 공유하는 방법

- **등록된 코드는 건드리지 않는다.** `src/`, `tools/`, `scripts/`, `tests/`, 루트 `CMakeLists.txt`는 `freeze-m3`와 같고, 다르면 `scripts/reproduce_m1.sh`가 실행을 거부한다. 그래서 라이브러리는 `lib/`의 별도 빌드이며, `bench_main`은 전과 똑같이 빌드된다.
- **같은 소스 파일을 다시 컴파일한다.** `lib/CMakeLists.txt`는 `../src`의 파일 여덟 개를 그대로 컴파일한다. 디코더 입력, NPU 컨텍스트, 슬롯 풀, 변형 C의 백엔드(RGA 호출과 dma-buf 슬롯), 후처리 세 가지다.
- **헤더 두 개를 include 경로로 바꿔 끼운다**(`lib/shim/`).
  - `core/util.h`: 벤치마크의 `die()`는 메시지를 찍고 종료하지만, 여기서는 예외를 던진다. 라이브러리가 이를 잡아 `Status`로 반환하거나 이벤트로 보낸다.
  - `preprocess/fd_cache.h`: 디코더 버퍼의 RGA 핸들 캐시가, 실행 중에 한 채널의 항목을 해제할 수 있다. 벤치마크는 입력을 한 번 열고 끝에서 해제한다. 검출기는 재연결하며, 재연결할 때마다 새 디코더와 새 버퍼가 생긴다.
- **파이프라인은 새로 썼다**(`src/detector.cpp`). `src/core/pipeline.cpp`를 따랐다. 단계, 큐, 슬롯 수, 정지 순서가 같고, 타임라인과 프레임별 기록이 없으며, 재연결과 콜백이 있다.
- **공개 헤더**는 FFmpeg, RGA, RKNN 헤더를 포함하지 않는다. 라이브러리가 내보내는 함수는 `rkzc::`뿐이고(표준 라이브러리의 약한 템플릿 인스턴스는 제외), `exit` 호출이 없다.

## 파일

| 경로 | 내용 |
|---|---|
| `include/rkzc/rkzc.h` | 공개 인터페이스 |
| `shim/core/util.h`, `shim/preprocess/fd_cache.h` | 바꿔 끼운 헤더 두 개 |
| `src/engine.*` | NPU 코어 세 개에 올린 모델과 제로카피 슬롯 |
| `src/detector.cpp` | 파이프라인: 디코딩, 샘플링, 전처리, 추론, 후처리, 콜백 |
| `src/check.cpp` | `check_model`, `check_source` |
| `tools/rkzc_check.cpp`, `examples/detect_jsonl.cpp` | `rkzc-check`, `rkzc-jsonl` |
| `tests/` | `test_checks`, `test_detector`, `equivalence.sh`(보드용), `no_opencv.sh` |

## 알려진 한계

- **모든 소스의 화면 비율이 같아야 한다.** 슬롯의 테두리는 한 번만 채우므로, 모든 프레임이 같은 영역으로 레터박스되어야 한다. 그렇지 않은 프레임은 `FrameError`와 함께 건너뛴다.
- **임계값은 고정이다**(신뢰도 0.25, NMS IoU 0.45 또는 최대 검출 300개). 전처리 스레드 두 개와 NPU 컨텍스트 세 개도 고정이다.
- **한 채널의 프레임 순서가 조금 뒤바뀔 수 있다.** NPU 워커 세 개가 따로 끝나기 때문이다. `Frame::seq`로 알 수 있다.
- **여는 데 실패하면, 실패한 단계가 쥐고 있던 것이 샐 수 있다.** RGA가 슬롯을 거부하면 그 슬롯의 버퍼와 매핑이, 출력 확인의 실행이 실패하면 거기서 쓰던 별도 RKNN 컨텍스트가 샌다. NPU나 커널이 실패해야 일어나는 일이다.
- **동작이 정의되지 않는 실패가 하나 있다.** 디코더가 DRM PRIME 프레임을 제공하지 않으면 `die()`가 FFmpeg 콜백 안에서 예외를 던진다. `h264_rkmpp`는 항상 DRM PRIME을 제공한다.
- **아래 라이브러리들**(MPP, RKNN, FFmpeg)**이 stderr에 직접 출력한다.**
- **재연결은 다시 재생되는 파일과 존재하지 않는 파일로 테스트했다.** 카메라나 연결을 끊는 RTSP 서버로는 테스트하지 않았다.
- **`-fPIC`와 숨김 가시성(hidden visibility)으로 컴파일한다.** 측정한 빌드는 그렇지 않았다. 이것과 콜백, 좌표 변환이 CPU 시간을 바꾸는지는 재지 않았다.

## 확인한 것

M3의 초기화된 NanoPi R6C에서(2026-11-19), `models/yolov8n_i8.rknn`, 보드에서 만든 2초짜리 1080p H.264 파일, 벤치마크 영상 하나(`pexels_29130409.mp4`)로 확인했다.

- `ctest`: 4개 중 4개 통과.
- **등록된 `tools/demo_detect`와 비교:** 영상의 처음 300프레임(샘플링된 50프레임)에서 검출 806개가 같았다. 클래스와 점수가 같고, 박스는 원본 프레임 좌표로 옮긴 뒤 일치했다.
- **오류:** 없거나 잘못된 모델, 없는 소스, 영상이 아닌 파일, 응답하지 않는 서버, 잘못된 설정이 각각 오류로 돌아왔고, 같은 프로세스가 계속 실행됐다.
- **반복 재생 파일:** 두 채널이 15초 동안 각각 7번 다시 열렸고 오류가 없었다. 끝난 뒤 열린 채 남은 파일 디스크립터가 없었다.
- **꺼져 있는 소스:** 다른 채널이 재생되는 동안 4초에 12번 재시도했다(100 ms에서 400 ms까지 두 배씩). `stop()`은 0.01초에 반환했다.
- **반복 재생 8채널, 30초:** 채널마다 152프레임이 샘플링되어 모두 전달됐고, 버려진 프레임과 오류가 없었다. 이것은 측정이 아니다.
- `ldd`에 OpenCV가 없다. 미정의 심볼에 `exit`와 `abort`가 없다.
