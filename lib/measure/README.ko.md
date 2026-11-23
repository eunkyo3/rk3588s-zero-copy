[English](README.md)

# M5의 측정 도구

[prereg/M5.md](../../prereg/M5.ko.md) §8 2단계의 도구다. 설치한 패키지의 서비스 `rkzc-detect`를 벤치마크의 B, C와 나란히 보드에서 측정한다. 패키지에 들어가지 않는다.

| 파일 | 하는 일 | 실행 위치 |
|---|---|---|
| `m5_session.py` | 세션 하나: §4의 스윕, 또는 §8 3단계의 점검. 서비스를 시작하고 멈추며, 소켓을 읽고, §3의 읽기와 샘플을 얻고, §4의 규칙을 적용하고, 실행마다 기록을 쓴다. | 보드, root |
| `m5_metrics.py` | 실행이 남긴 파일에서 §3의 값과 §4의 규칙 1, 2, 3, 5를 계산한다. | 양쪽 |
| `m5_summary.py` | 모든 실행의 값, 집계에 드는 실행, §2의 판정, §6의 값. 지연 백분위수에 numpy가 필요하다. | PC |
| `m5_order.py` | §8 2단계의 순서 검사: 모든 기록의 시작 시각을 `prereg-m5`, `freeze-m5`, 수정안의 Release와 비교한다. | PC |
| `m5_detections.py` | §8 3단계의 점검 "검출": 파일 하나에 대해 서비스의 줄과 `rkzc-jsonl`의 줄을 비교한다. | 보드, root |
| `test_m5.py` | 보드가 필요 없는 부분의 테스트. | PC |
| `test_m5_service.py` | 서비스와 systemd의 대역을 상대로 돌리는 서비스 실행 루프의 테스트. | Linux |
| `package.txt` | 측정하는 `.deb`의 버전과 SHA-256. 세션은 다른 파일을 거부한다. | |
| `results/` | 기록: 스윕의 세션은 `<UTC>_m5/`, 점검은 `<UTC>_m5_check/`와 `<UTC>_m5_detections/`. | |

## 등록된 세션 도구에서 가져다 쓰는 것

`m5_session.py`는 `tools/stream_session.py`를 가져와, B와 C에 필요한 모든 것과 실행을 둘러싼 세션에 그대로 쓴다.

- 세션의 시작: 인벤토리, 고정 클럭, PC의 스트림 서버, 기준선을 얻는 유휴 구간;
- 쿨다운 게이트, PC와의 시계 교환, 규칙 4를 위한 서버 로그 읽기;
- 모든 B, C 실행과 PROTOCOL §9.3에 따른 판정, 무효 실행의 재실행;
- `runs.tsv`, `started.tsv`, 세션을 넘나드는 반복의 관리.

이를 위해 가져온 스윕 표에 항목 하나를 M3의 종류로 더한다. M5의 B, C 실행은 M3의 실행이다(M3 §1의 옵션, M3 §3의 유효성, M3 §4의 인벤토리 규칙). 등록된 파일은 바뀌지 않는다.

직접 구현하는 것: 패키지 점검, 서비스의 실행과 M5 §4에 따른 판정, 실행마다 곁에 두는 M5의 머리말, 구성의 순서.

## 세션

보드에서 root로, 저장소에서, 패키지를 설치하고 모델을 받아 두고 서비스는 멈추고 비활성화한 상태로 실행한다.

```sh
COOLING="<text>" AMBIENT_C=<°C> python3 lib/measure/m5_session.py sweep --deb ~/rkzc-work/dist/rkzc_0.1.1-1_arm64.deb
COOLING="<text>" AMBIENT_C=<°C> python3 lib/measure/m5_session.py check --deb ~/rkzc-work/dist/rkzc_0.1.1-1_arm64.deb \
    --duration 10 [--arms B C P P4] [--cpu-check]
```

- **유휴 구간 전에** 세션은 `.deb`가 기록된 그 파일인지, 그 안의 모든 파일이 설치된 파일과 같은지, 모델이 M1의 것인지 확인한다. `/etc/rkzc/detect.toml`은 패키지가 넣은 파일에서 `sources`만 바꿔 쓴다.
- **모든 세션**은 작업 트리가 깨끗해야 한다. 도구는 한 커밋의 도구다.
- **스윕**은 `freeze-m5`의 행이 `docs/notes/env.md`에 있고 프로토콜의 설정일 때만 시작한다. 같은 도구 커밋과 패키지로 돌린 이전 세션을 읽어 끝내지 못한 것을 다시 한다.
- **`.deb`**는 저장소 체크아웃 밖에 둔다. 세션은 깨끗하지 않은 작업 트리를 거부한다.
- **드롭인** `CPUAffinity=4-7`은 P4 실행 동안에만 있다. 세션은 끝날 때, 일찍 멈출 때도, 이를 지운다.

## 실행의 기록

실행마다 `m5_run.csv`가 있다. M5의 머리말, 그리고 실행이 유효한지와 아닌 이유, 서비스 실행이면 §3의 값이다. 그 곁에:

- **B, C:** `bench_main`이 쓴 그대로의 출력.
- **P, P4:** 소켓에서 받은 그대로의 줄(`lines.jsonl`), 시각과 시각을 맞춘 읽기(`marks.csv`), 구간 양 끝의 `/proc/stat`과 `/proc/schedstat`, 양 끝에서 모든 스레드의 허용 CPU, 1초 샘플, 유닛의 저널, 스트림 서버의 로그.

`m5_summary.py`는 이 파일들에서 서비스 실행의 값을 다시 계산하고, 세션이 쓴 값과 같은지 알린다.

## 테스트

```sh
python3 lib/measure/test_m5.py
python3 lib/measure/test_m5_service.py   # Linux
python3 lib/measure/m5_order.py
```
