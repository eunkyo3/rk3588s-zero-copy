[English](README.md)

# librkzc

이 저장소의 변형 C 경로(하드웨어 디코딩, RGA 레터박스, NPU 추론, 픽셀의 CPU 복사 없음)를 라이브러리로 만든 것이다. [docs/design/packaging.ko.md](../docs/design/packaging.ko.md)의 2단계에 해당한다. 경로를 벤치마크에서 분리했고, OpenCV가 필요 없으며, 벤치마크가 종료하던 자리에서 오류를 반환한다.

**상태:** 모델과 소스를 열어 확인하는 데까지 된다. 검출은 아직 하지 않는다. 검출기(스트림을 받아 검출 결과를 내주는 것)는 3단계다. 이 라이브러리에 대해 측정한 것은 없다.

## 빌드와 테스트

보드에서 `scripts/setup_board.sh`를 실행한 뒤에 한다(ffmpeg-rockchip, librknnrt, RKNN 헤더가 필요하다).

```sh
cmake -S lib -B build-lib \
    -DRKZC_TEST_MODEL=$PWD/models/yolov8n_i8.rknn -DRKZC_TEST_VIDEO=/path/to/h264.mp4
cmake --build build-lib -j4
(cd build-lib && ctest --output-on-failure)
```

`librkzc.so`와 `rkzc-check`가 만들어진다.

```
$ build-lib/rkzc-check --model models/yolov8n_i8.rknn --source rtsp://10.88.0.1:8554/ch1 --model /nope.rknn
model  ok      models/yolov8n_i8.rknn: 640x640, 9 outputs, runtime 2.3.2 (...) / driver 0.9.8
source ok      rtsp://10.88.0.1:8554/ch1: 1920x1080
model  FAILED  /nope.rknn: cannot read /nope.rknn
```

## 측정한 코드를 공유하는 방법

- **등록된 코드는 건드리지 않는다.** `src/`, `tools/`, `scripts/`, `tests/`, 루트 `CMakeLists.txt`는 `freeze-m3`와 같고, 다르면 `scripts/reproduce_m1.sh`가 실행을 거부한다. 그래서 라이브러리는 `lib/`의 별도 빌드이며, `bench_main`은 전과 똑같이 빌드된다.
- **같은 소스 파일을 다시 컴파일한다.** `lib/CMakeLists.txt`는 `../src`의 파일 여덟 개를 그대로 컴파일한다. 디코더 입력, NPU 컨텍스트, 슬롯 풀, 변형 C의 백엔드, 후처리 세 가지다. 변형 A와 B(OpenCV가 필요하다), 그리고 벤치마크의 타임라인, 기록, 보고서는 뺀다.
- **종료 대신 오류.** 측정한 소스는 `core/util.h`의 `die()`를 부르고, 이것은 메시지를 찍고 종료한다. `lib/shim/core/util.h`가 라이브러리의 include 경로에서 먼저 잡히며, 여기서는 `die()`가 예외를 던진다. 라이브러리는 경계에서 이 예외를 잡아 `Status`로 반환한다.
- **공개 헤더**(`include/rkzc/rkzc.h`)는 FFmpeg, RGA, RKNN 헤더를 포함하지 않는다. 라이브러리는 `rkzc::` 함수만 내보낸다.

## 파일

| 경로 | 내용 |
|---|---|
| `include/rkzc/rkzc.h` | 공개 인터페이스: `check_model`, `check_source`, `version` |
| `shim/core/util.h` | 측정한 소스를 위한, 예외를 던지는 `die()` |
| `src/engine.*` | NPU 코어 세 개에 올린 모델과 제로카피 슬롯. 오류를 반환하며 열고, PROTOCOL §5.4의 순서로 해제한다 |
| `src/check.cpp` | 공개 함수 |
| `tools/rkzc_check.cpp` | `rkzc-check` |
| `tests/` | `test_checks`(보드용), `no_opencv.sh`(의존성과 내보낸 심볼) |

## 알려진 한계

- **여는 데 실패하면, 실패한 단계의 자원 하나가 샐 수 있다**(예: 할당했지만 아직 등록하지 않은 슬롯). 그 전에 연 것은 모두 해제된다. 이것을 막으려면 공유하는 파일을 고쳐야 한다.
- **프로세스가 여전히 끝나는 실패가 하나 있다.** 디코더가 DRM PRIME 프레임을 제공하지 않으면 FFmpeg 콜백 안에서 `die()`가 불리는데, 예외는 FFmpeg의 C 코드를 넘어올 수 없다. `h264_rkmpp`는 항상 DRM PRIME을 제공한다.
- **워커 스레드의 실패**(`prepare`, `bind`, `rknn_run`)는 이 두 확인 함수가 거치지 않는 경로다. 3단계의 검출기가 잡아야 한다.
- **아래 라이브러리들**(MPP, RKNN, FFmpeg)**이 stderr에 직접 출력한다.**
- **`-fPIC`로 컴파일한다.** 측정한 빌드는 그렇지 않았다. 나머지 옵션은 같다. 이것이 CPU 시간을 바꾸는지는 재지 않았다.

## 확인한 것

M3의 초기화된 NanoPi R6C에서(2026-11-19), `models/yolov8n_i8.rknn`과 보드에서 만든 2초짜리 1080p H.264 파일로 확인했다.

- `ctest`: 2개 중 2개 통과.
- 없는 모델, 모델이 아닌 파일, 없는 소스, 영상이 아닌 파일이 각각 오류를 반환했고, 같은 프로세스가 그 뒤에 모델과 소스를 두 번 열었다.
- `rkzc-check`가 한 프로세스에서 모델과 소스를 20번 열고 닫았다.
- `ldd`에 OpenCV가 없다. 내보낸 함수는 `rkzc::check_model`, `rkzc::check_source`, `rkzc::version`이다.
