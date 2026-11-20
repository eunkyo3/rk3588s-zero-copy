[English](README.md)

# librkzc

이 저장소의 변형 C 경로(하드웨어 디코딩, RGA 레터박스, NPU 추론, 픽셀의 CPU 복사 없음)를 라이브러리로 만든 것이다. [docs/design/packaging.ko.md](../docs/design/packaging.ko.md)의 2단계와 3단계에 해당한다. 경로를 벤치마크에서 분리했고, OpenCV가 필요 없으며, 벤치마크가 종료하던 자리에서 오류를 반환하고, 멈출 때까지 실행하며, 재연결하고, 검출 결과를 내준다.

**상태:** C++, C, 파이썬 인터페이스로 동작하는 검출기다. 설치형 프로그램과 패키지는 아직 없다(4단계와 5단계). CPU 시간이나 지연은 측정한 것이 없다.

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
- **소스:** RTSP(TCP)의 H.264 또는 파일. 소스가 끝나거나, 실패하거나, 열리지 않거나, 열린 채 10초 동안 프레임을 주지 않으면 다시 연다. 대기 시간은 1초에서 시작해 프레임이 오지 않는 동안 30초까지 두 배씩 늘고, 프레임이 오면 1초로 돌아간다. 끝까지 재생한 파일은 그 채널을 끝내고, `loop_files`를 켜면 처음부터 다시 재생한다. 열리지 않는 파일은 스트림처럼 다시 시도한다.
- **콜백**은 라이브러리의 스레드에서 한 번에 하나씩 불린다. 콜백이 느리면 파이프라인이 밀리고, 샘플링된 프레임이 버려진다(`stats()`가 센다).

인터페이스는 [include/rkzc/rkzc.h](include/rkzc/rkzc.h)가 전부다. [examples/detect_jsonl.cpp](examples/detect_jsonl.cpp)는 검출 결과를 JSON 줄로 출력하는 예제다.

## C와 파이썬

- **C:** [include/rkzc/rkzc_c.h](include/rkzc/rkzc_c.h)는 같은 검출기를 일반 C 함수로 내놓는다. 같은 `librkzc.so` 안에 있다. `rkzc_open`, `rkzc_on_frame`, `rkzc_start`, `rkzc_stop`, `rkzc_close`다. 실패는 반환값으로 알리고, 이유는 호출한 쪽의 버퍼에 쓴다.
- **파이썬:** [python/rkzc](python/rkzc/__init__.py)는 `ctypes`로 C 인터페이스를 부른다. Python 3만 있으면 된다. 컴파일한 모듈도 NumPy도 필요 없다.

```python
import rkzc

def on_frame(frame):                      # 라이브러리의 스레드에서 한 번에 하나씩 불린다
    for d in frame.detections:            # x, y, w, h는 원본 프레임의 픽셀 단위
        print(frame.channel, d.name, round(d.score, 2), d.x, d.y, d.w, d.h)

with rkzc.Detector(["rtsp://10.88.0.1:8554/ch1"], "models/yolov8n_i8.rknn", on_frame=on_frame) as det:
    det.start()
    det.wait(60)                          # 초 단위. 기다리는 동안 Ctrl-C가 듣는다
```

- **라이브러리를 찾는 방법:** 아직 패키지가 없어서 모듈을 소스 트리에서 쓴다. `PYTHONPATH=lib/python`, `RKZC_LIBRARY=build-lib/librkzc.so.0`으로 지정한다(지정하지 않으면 로더 경로의 `librkzc.so.0`).
- **옵션**은 `rkzc::Config`의 것을 키워드 인자로 준다. `det.stats()`, `rkzc.check_model()`, `rkzc.check_source()`도 있다. 실패하면 라이브러리가 준 이유를 담아 `rkzc.Error`를 던진다.
- **콜백에서 난 예외**는 출력하고 `det.callback_errors`에 센다. 검출기는 계속 돈다.
- **시작한 검출기는 닫을 때까지 살아 있다.** 프로그램이 마지막 참조를 버려도 그렇다. 인터프리터가 끝날 때까지 돌고 있는 것은 그때 닫는다.
- [python/examples/detect_jsonl.py](python/examples/detect_jsonl.py)는 C++ 예제와 같은 JSON 줄을 출력한다.

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

`librkzc.so`, `rkzc-check`(이 보드가 모델과 소스를 돌릴 수 있는지 확인), `rkzc-jsonl`(예제)이 만들어진다. 파이썬 테스트는 `python3`만 있으면 되고, 따로 설치할 것이 없다.

## 측정한 코드를 공유하는 방법

- **등록된 코드는 건드리지 않는다.** `src/`, `tools/`, `scripts/`, `tests/`, 루트 `CMakeLists.txt`는 `freeze-m3`와 같고, 다르면 `scripts/reproduce_m1.sh`가 실행을 거부한다. 그래서 라이브러리는 `lib/`의 별도 빌드이며, `bench_main`은 전과 똑같이 빌드된다.
- **같은 소스 파일을 다시 컴파일한다.** `lib/CMakeLists.txt`는 `../src`의 파일 여덟 개를 그대로 컴파일한다. 디코더 입력, NPU 컨텍스트, 슬롯 풀, 변형 C의 백엔드(RGA 호출과 dma-buf 슬롯), 후처리 세 가지다.
- **헤더 두 개를 include 경로로 바꿔 끼운다**(`lib/shim/`).
  - `core/util.h`: 벤치마크의 `die()`는 메시지를 찍고 종료하지만, 여기서는 예외를 던진다. 라이브러리가 이를 잡아 `Status`로 반환하거나 이벤트로 보낸다.
  - `preprocess/fd_cache.h`: 디코더 버퍼의 RGA 핸들 캐시가, 실행 중에 한 채널의 항목을 해제할 수 있다. 벤치마크는 입력을 한 번 열고 끝에서 해제한다. 검출기는 재연결하며, 재연결할 때마다 새 디코더와 새 버퍼가 생긴다.
- **파이프라인은 새로 썼다**(`src/detector.cpp`). `src/core/pipeline.cpp`를 따랐다. 단계, 큐, 슬롯 수, 정지 순서가 같고, 타임라인과 프레임별 기록이 없으며, 재연결과 콜백이 있다.
- **공개 헤더**는 FFmpeg, RGA, RKNN 헤더를 포함하지 않는다. 라이브러리가 내보내는 함수는 `rkzc::`와 C 인터페이스의 `rkzc_`뿐이고(표준 라이브러리의 약한 템플릿 인스턴스는 제외), `exit` 호출이 없다.

## 파일

| 경로 | 내용 |
|---|---|
| `include/rkzc/rkzc.h` | 공개 인터페이스 |
| `include/rkzc/rkzc_c.h`, `src/c_api.cpp` | C 인터페이스 |
| `python/rkzc/`, `python/examples/detect_jsonl.py` | 파이썬 모듈과 예제 |
| `shim/core/util.h`, `shim/preprocess/fd_cache.h` | 바꿔 끼운 헤더 두 개 |
| `src/engine.*` | NPU 코어 세 개에 올린 모델과 제로카피 슬롯 |
| `src/detector.cpp` | 파이프라인: 디코딩, 샘플링, 전처리, 추론, 후처리, 콜백 |
| `src/check.cpp` | `check_model`, `check_source` |
| `tools/rkzc_check.cpp`, `examples/detect_jsonl.cpp` | `rkzc-check`, `rkzc-jsonl` |
| `tests/` | `test_checks`, `test_detector`, `test_c_api`, `test_python.py`, `equivalence.sh`(보드용), `no_opencv.sh`, `rtsp_interruptions.sh`(PC에서 하는 수동 테스트) |

## 알려진 한계

- **모든 소스의 화면 비율이 같아야 한다.** 슬롯의 테두리는 한 번만 채우므로, 모든 프레임이 같은 영역으로 레터박스되어야 한다. 그렇지 않은 프레임은 `FrameError`와 함께 건너뛴다.
- **임계값은 고정이다**(신뢰도 0.25, NMS IoU 0.45 또는 최대 검출 300개). 전처리 스레드 두 개와 NPU 컨텍스트 세 개도 고정이다.
- **한 채널의 프레임 순서가 조금 뒤바뀔 수 있다.** NPU 워커 세 개가 따로 끝나기 때문이다. `Frame::seq`로 알 수 있다.
- **여는 데 실패하면, 실패한 단계가 쥐고 있던 것이 샐 수 있다.** RGA가 슬롯을 거부하면 그 슬롯의 버퍼와 매핑이, 출력 확인의 실행이 실패하면 거기서 쓰던 별도 RKNN 컨텍스트가 샌다. NPU나 커널이 실패해야 일어나는 일이다.
- **동작이 정의되지 않는 실패가 하나 있다.** 디코더가 DRM PRIME 프레임을 제공하지 않으면 `die()`가 FFmpeg 콜백 안에서 예외를 던진다. `h264_rkmpp`는 항상 DRM PRIME을 제공한다.
- **아래 라이브러리들**(MPP, RKNN, FFmpeg)**이 stderr에 직접 출력한다.**
- **재연결은 유선으로 연결한 RTSP 서버 하나(mediamtx)로 테스트했다.** 카메라, Wi-Fi, 패킷 손실로는 테스트하지 않았다. "확인한 것"의 표는 `ctest`가 아니라 수동 테스트의 결과다.
- **파이썬 콜백은 파이썬의 전역 잠금 아래에서** 한 번에 하나씩 실행되고, 라이브러리는 콜백이 끝나기를 기다린다. 콜백이 CPU 시간이나 프레임 손실에 주는 영향은 측정하지 않았다. 느린 콜백은 느린 C++ 콜백과 마찬가지로 프레임을 버리게 만든다.
- **파이썬 모듈은 아직 설치할 수 없다.** `pyproject.toml`이 없다. 5단계의 패키지가 시스템 경로에 놓을 예정이다.
- **`stop()`에서는, 이미 전처리된 프레임을 추론하는 동안 디코더가 먼저 닫힌다.** 벤치마크는 파이프라인이 비워질 때까지 입력을 열어 둔다. 전처리를 기다리던 프레임은 먼저 통과시키므로, 디코더가 닫힐 때 그 디코더의 버퍼를 쥔 프레임은 없다.
- **측정한 구성과 세 가지가 다르다.** `-fPIC`와 숨김 가시성(hidden visibility)으로 컴파일한다. CPU 고정을 하지 않는다(`bench_main`은 프로세스를, 따라서 MPP, FFmpeg, librknnrt의 스레드까지 A76 코어 4~7에 고정한다). 콜백, 좌표 변환, 감시 스레드가 추가됐다. 이것들이 CPU 시간이나 지연을 어떻게 바꾸는지는 재지 않았다. 벤치마크와 같은 배치를 원하는 프로그램은 `Detector::open` 전에 스스로 고정하면 된다.

## 확인한 것

M3의 초기화된 NanoPi R6C에서(2026-11-19), `models/yolov8n_i8.rknn`, 보드에서 만든 2초짜리 1080p H.264 파일, 벤치마크 영상 하나(`pexels_29130409.mp4`)로 확인했다.

- `ctest`: 6개 중 6개 통과(2026-11-20. C와 파이썬 인터페이스를 넣기 전에는 4개 중 4개).
- 독립 리뷰에서 막아야 할 문제는 나오지 않았다. 지적된 점(정체 제한 없음, 버퍼 해제 여부를 가려내지 못하는 테스트, 밝히지 않은 CPU 고정, 그 밖의 작은 것들)은 코드와 이 문서에 반영했다.
- **등록된 `tools/demo_detect`와 비교:** 영상의 처음 300프레임(샘플링된 50프레임)에서 검출 806개가 같았다. 클래스와 점수가 같고, 박스는 원본 프레임 좌표로 옮긴 뒤 일치했다. M1의 후처리로도, 라이브러리의 기본 후처리(neon)로도 같았다. 레터박스 여백이 좌우에 생기는 세로 영상에서는 검출 367개가 같았다(테스트가 이 영상을 매번 새로 인코딩하므로 개수가 한두 개 달라진다).
- **오류:** 없거나 잘못된 모델, 없는 소스, 영상이 아닌 파일, 응답하지 않는 서버, 잘못된 설정이 각각 오류로 돌아왔고, 같은 프로세스가 계속 실행됐다.
- **반복 재생 파일:** 두 채널이 15초 동안 각각 7번 다시 열렸고 오류가 없었다. 한 채널이 RGA용으로 쥔 디코더 버퍼는 첫 재생에서 최대 2개, 네 번 다시 연 뒤에도 최대 2개였다. 다시 열 때의 해제를 빼면 같은 테스트에서 16개가 되어 실패했다. 끝난 뒤 열린 채 남은 파일 디스크립터가 없었다.
- **정체:** 제한을 1 ms로 두자, 파일 채널이 3초 동안 15번 중단되고 다시 열렸다.
- **꺼져 있는 소스:** 다른 채널이 재생되는 동안 4초에 12번 재시도했다(100 ms에서 400 ms까지 두 배씩). `stop()`은 0.01초에 반환했다.
- **실제 RTSP 서버를 끊어 본 결과**(스트림 PC에서 `tests/rtsp_interruptions.sh`: mediamtx와 ffmpeg 송출 하나. 보드의 검출기는 재시도 대기 상한을 4초로 두었다). 채널은 매번 돌아왔고, 596프레임을 추론하는 동안 버려진 프레임과 프레임 오류가 없었다.

  | 끊김 | 알아챈 시점 | 읽기를 끝낸 것 | 소스가 돌아온 뒤 다시 열린 시점 |
  |---|---|---|---|
  | A: 송출을 죽이고 서버는 그대로 | 0.01초 | 서버가 스트림을 닫음 | 5.5초 |
  | B: 송출을 20초 동안 멈춤 | 10.0초 | 서버가 송출을 포기하고 스트림을 닫음 | 4.5초 |
  | C: 서버를 20초 동안 멈춤 | 10.2초 | FFmpeg 자체의 읽기 타임아웃(5초 두 번) | 0.1초 |
  | D: 서버와 송출을 모두 죽임 | 0.01초 | 연결이 닫힘 | 4.5초 |

  - **이 실행에서는 정체 제한이 작동할 일이 없었다.** 기본값 10초에서는 서버나 FFmpeg이 먼저 읽기를 끝냈다. 제한을 3초로 둔 두 번째 실행에서는 멈춘 서버와 멈춘 송출을 각각 3.0초 뒤에 알아챘고, 막혀 있던 네트워크 읽기는 감시 스레드가 요청한 지 0.04초 만에 반환했다.
  - **다시 열리기까지 걸리는 시간**은 소스가 돌아온 순간의 재시도 대기 시간에 달려 있다. 최대 상한까지이며, 기본 상한은 30초다.
- **반복 재생 8채널, 30초:** 채널마다 152프레임이 샘플링되어 모두 전달됐고, 버려진 프레임과 오류가 없었다. 이것은 측정이 아니다.
- **C와 파이썬 인터페이스**(2026-11-20, 같은 보드, 모델, 영상):
  - **파이썬과 C++ 비교:** `python/examples/detect_jsonl.py`와 `rkzc-jsonl`이 벤치마크 영상에서 둘 다 본 34프레임에 대해 같은 줄을 출력했다. 검출은 535개였다.
  - **콜백:** 프레임마다 예외를 던지는 콜백은 8프레임에서 8번 세어졌고 검출기는 계속 돌았다. 콜백 안에서 부른 `stop()`은 거부됐다. 메인 스레드가 `stop()` 안에 있는 동안 콜백에서 부른 `stats()`는 동작했고, `stop()`은 0.02초 만에 반환했다.
  - **마지막 참조를 버린 검출기**는 계속 프레임을 전달했고, 종료 시 실행되는 함수가 닫았다. 열린 채 남은 파일 디스크립터는 없었다.
  - **Ctrl-C:** 기다리는 중인 파이썬 예제에 SIGINT를 보내자 0.19초 만에 멈추고 상태 0으로 종료했다.
  - **파이썬에서 반복 재생 8채널, 20초:** 채널마다 100프레임이 샘플링되어 모두 전달됐고, 버려진 프레임이 없었다. 이것은 측정이 아니다.
  - **C에서:** C99로 컴파일한 `tests/test_c_api.c`가 검출기를 열고, 파일을 재생하고, 프레임을 세고, 닫았다.
- `ldd`에 OpenCV가 없다. 미정의 심볼에 `exit`와 `abort`가 없다.
