[English](reproduce.md)

# M1의 H1 지점을 명령 하나로 재현하기

스크립트 두 개가 FriendlyElec 이미지의 NanoPi R6C를 설정하고, 그 위에서 M1의 H1 지점을 잰다. 주 조건 8채널에서 B와 C를 각각 3회 반복하고, 끝에서 M1의 값과 비교한다. M3는 이 스크립트를 초기화한 보드에서 쓴다([prereg/M3.md](../../prereg/M3.ko.md)).

- **`scripts/setup_board.sh`:** 실행에 필요한 것을 설치하고 설정한 뒤 코드를 빌드하고, 보드 상태를 기록한다.
- **`scripts/reproduce_m1.sh`:** 코드를 빌드하고 `tools/stream_session.py`의 세션 하나를 돌린 뒤, 비교를 출력한다(`tools/m3_report.py`).

## 필요한 것

- **보드:** eMMC에 FriendlyElec의 Ubuntu 24.04 이미지가 있는 NanoPi R6C. M3는 `rom-version` 20251222, 커널 6.1.141을 썼다([env.md](env.ko.md)).
  - 상시 도는 팬이 달린 방열판(A9).
  - 패키지와 다운로드를 위해 Wi-Fi나 다른 이더넷 포트로 인터넷에 연결된 상태.
- **스트림 PC:** [stream-server/README.md](../../stream-server/README.ko.md)대로 설정하고, M1의 영상과 그 `manifest.tsv`를 `~/media/bench`에 둔다.
  - **벤치 링크:** 보드의 `eth1`과 유선으로 잇는다. PC는 `10.88.0.1/24`, 보드는 `10.88.0.2/24`가 된다.
  - **네트워크 서비스:** PC가 보드에 NTP를 제공하고(chrony), 방화벽이 `10.88.0.2`의 RTSP와 NTP를 받는다([env.md](env.ko.md)).
  - **PC 사용자:** `tools/stream_session.py`는 `eunkyo@10.88.0.1`로 로그인한다. 다른 사용자라면 그 파일의 `SSH`와 `setup_board.sh`의 `PC_USER`를 바꾼다.
- **스트림 PC용 키:** 보드가 이 키로 PC에 로그인해 스트림 서버를 켜고 끈다.
  - `ssh-keygen -t ed25519 -N "" -f bench_stream`으로 만든다.
  - `bench_stream.pub`를 PC의 `~/.ssh/authorized_keys`에 더한다.

## 순서

보드에서, 사용자 `pi`로:

```sh
git clone https://github.com/eunkyo3/rk3588s-zero-copy.git ~/rk3588s-zero-copy
cd ~/rk3588s-zero-copy
sudo -v                                         # 아래 작업 전에 sudo 비밀번호를 한 번 넣는다
sudo setsid nohup scripts/setup_board.sh --stream-key ~/bench_stream >~/setup.log 2>&1 &
# ~/setup.log가 "setup: ok"로 끝나면:
sudo COOLING="heatsink + always-on fan" AMBIENT_C=25 setsid nohup scripts/reproduce_m1.sh after >~/reproduce.log 2>&1 &
```

- **커밋:** M3의 실행은 `freeze-m3`에 등록한 코드를 쓰고, [env.md](env.ko.md#등록)에 `freeze-m3`의 행도 있는 체크아웃에서 돈다. 그 행은 태그 다음 커밋에 들어오므로, 태그가 아니라 `main`을 클론한다. 두 스크립트가 `src/`, `tools/`, `scripts/`, `tests/`, `CMakeLists.txt`가 `freeze-m3`의 것과 같은지 확인한다.
- **저장소는 `/home/pi/rk3588s-zero-copy`에 있어야 한다.** M1의 인벤토리가 빌드의 include 경로에 이 경로를 담고 있고, 세션이 이를 비교한다(M3 §4).
- **로그는 저장소 밖에 둔다.** 저장소 안에 파일이 생기면 모든 기록의 커밋이 `-dirty`가 된다.
- **`setsid nohup`**이면 로그인이 끊겨도 작업이 계속 돈다. 중간에 끊긴 설정은 이어서 할 수 없고, 보드를 다시 초기화해야 한다(M3 §5).
- **`COOLING`과 `AMBIENT_C`**는 모든 기록에 들어간다. `AMBIENT_C`는 온도계로 읽은 실내 온도다.
- **설정**은 이미지의 chroot에서 약 9분 걸렸다(A15). 벤치 링크와 스트림 PC가 필요한 단계를 먼저 하므로, 거기에 문제가 있으면 1분 안에 드러난다.
- **세션 하나에 약 45분이 걸린다:**
  - 기준선을 위한 유휴 10분;
  - 140초 실행 6회;
  - 매 실행 전의 냉각 게이트.
- **비교**는 `~/reproduce.log` 끝에 출력된다. 세션 기록은 `results/m3/<UTC>_m3_after/`에 있다.
- **초기화 전에:** 초기화가 보드의 `results/`를 지우므로 보드 밖으로 옮겨 둔다. M3는 `m3_before` 세션에 이렇게 한다(M3 §3).

## `setup_board.sh`가 하는 일

1. **벤치 링크와 시간:**
   - `eth1`의 NetworkManager 연결 `bench-link`, `10.88.0.2/24`, 기본 경로로는 쓰지 않음;
   - `systemd-timesyncd`가 `10.88.0.1`에서 시간을 받음.
2. **스트림 PC:**
   - 키와 PC의 호스트 키;
   - PC의 `media/bench/manifest.tsv`를 받아 SHA-256을 확인;
   - 시계가 동기화되기를 최대 2분 기다림.
3. **패키지:**
   - `apt-get update`, `apt-get upgrade`, 그다음 빌드 도구, OpenCV, binutils, wget을 설치하고 `apt-get clean`을 한다. apt는 바뀐 설정 파일을 그대로 두고, 다른 apt 실행이 끝나기를 기다린다.
   - 그다음, M3 §4가 고정하고 이미지가 제공하는 것을 [inventory_m1.txt](inventory_m1.txt)와 대조한다: 커널, librga, MPP.
4. **NPU 런타임:** rknn-toolkit2의 `librknnrt.so` 2.3.2를 `/usr/lib`에 넣고 SHA-256을 확인한다. RKNN 헤더는 `/opt/rknpu2/include`에 넣는다([tools/setup/fetch_rknn_headers.sh](../../tools/setup/fetch_rknn_headers.sh)).
   - 이미지에는 librknnrt가 없다. MPP와 librga는 있다.
5. **ffmpeg-rockchip:** M1이 쓴 커밋으로 `/opt/ffmpeg-rockchip`에 빌드한다([tools/setup/build_ffmpeg_rockchip.sh](../../tools/setup/build_ffmpeg_rockchip.sh)). 버전 문자열의 해시는 M1처럼 8자로 고정한다.
6. **모델:** Release [`data-model-yolov8n_i8-04a76611`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/data-model-yolov8n_i8-04a76611)의 `yolov8n_i8.rknn`을 받아 SHA-256을 확인한다.
7. **빌드:** CMake, Release, 커밋은 git에서.
8. **기록:** `results/m3/<UTC>_setup/`에 남긴다.
   - `setup.csv`: 결과, 깨끗한 보드였는지, 코드가 `freeze-m3`의 것과 같은지, 단계별 시간;
   - `setup.log`: 모든 명령과 그 출력;
   - `dpkg.tsv`: 패키지와 버전;
   - `inventory.txt`와 `inventory.diff`: [inventory_m1.txt](inventory_m1.txt)와 비교한 보드;
   - `inventory_pinned.txt`: 고정한 키 중 다른 것이 있으면 그것.

- **깨끗한 보드에서만:** 일부라도 전에 설정한 보드에서는 아무것도 바꾸기 전에 멈춘다.
  - 첫 변경 전에 `/var/lib/rk3588s-bench/setup-started`를 쓰므로, 그 뒤로 이 보드는 깨끗하지 않다.
  - 개발용으로는 `--not-fresh`로 넘어갈 수 있다.
  - `--no-services`는 1단계와 2단계를 빼고, 나머지를 chroot에서 시험할 때 쓴다.
- **M3의 기록인가, 개발 기록인가:** 네 가지가 모두 성립할 때만 M3의 기록이다(실행 종류 `m3`, `results/m3/<UTC>_setup/`).
  - 보드가 깨끗하다;
  - 서비스와 함께 돈다;
  - `freeze-m3`의 행이 있다;
  - 코드가 `freeze-m3`의 것과 같다.

  그렇지 않으면 개발 기록이다: 실행 종류 `check`, `results/check/<UTC>_m3_setup/`.
- **첫 실패에서 멈춘다.** 기록에 어느 단계였는지 남는다.

## `reproduce_m1.sh`가 하는 일

- **빌드:** 클론에서는 git의 커밋으로, 복사본에서는 `tools/deploy_board.sh`가 남긴 대로 빌드한다.
- **실행:** `tools/stream_session.py`의 프로토콜 세션 하나, 스윕 `m3_before`나 `m3_after`를 돌린다(M3 §3). 세션은:
  - 보드의 인벤토리를 기록한다;
  - M3 §4가 고정한 키가 M1과 다르면 멈춘다;
  - 클럭을 고정한다(A7);
  - PC의 스트림 서버를 켠다;
  - PROTOCOL §9.1의 순서와 유효성 규칙으로 B와 C를 돌린다;
  - 서버를 끈다.
- **두 번째 인자 `smoke`**는 빠른 기능 시험이다: 유휴 60초, 창 10초, 게이트 30초. 기록은 `results/check/`에 가고, 어디에도 집계되지 않는다.
- **출력:** 그 세션의 `tools/m3_report.py --session`을 출력한다.
