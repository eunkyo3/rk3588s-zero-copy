[English](install.md)

# NanoPi R6C에 `rkzc` 설치하기

`rkzc`는 이 저장소의 제로카피 경로를 패키지로 만든 것이다. systemd 서비스를 갖춘 프로그램 `rkzc-detect`와, C++·C·파이썬 인터페이스를 가진 라이브러리 `librkzc`가 들어 있다. [design/packaging.ko.md](design/packaging.ko.md)의 5단계에 해당하고, 각 부분은 [lib/README.ko.md](../lib/README.ko.md)에 설명되어 있다.

**테스트한 환경:** NanoPi R6C 한 대, FriendlyElec의 Ubuntu 24.04 이미지(`rom-version` 20251222, 커널 6.1.141, NPU 드라이버 0.9.8). 패키지는 RK3588이 아니거나 커널 장치가 없는 보드에서는 설치를 거부하고, 이미지나 NPU 드라이버가 다르면 그 사실을 알린다.

**약속하지 않는 것:** 다른 보드, 카메라, 모델에서 [M1](report/M1.ko.md)의 수치가 나온다는 것. 패키지로 만든 경로의 CPU 시간이나 지연은 측정한 적이 없다.

## 설치

```sh
wget https://github.com/eunkyo3/rk3588s-zero-copy/releases/download/rkzc-v0.1.0/rkzc_0.1.0-1_arm64.deb
sudo apt install ./rkzc_0.1.0-1_arm64.deb
```

- **설치에는 네트워크가 필요하다.** Rockchip의 NPU 런타임(`librknnrt.so` 2.3.2)을 Rockchip 저장소에서 내려받아 SHA-256을 확인한다. 이 파일은 라이선스가 재배포를 허용하지 않아 패키지에 넣을 수 없다. 보드가 오프라인이면 설치는 그대로 성공하고 그 사실을 알려 준다. 나중에 `sudo rkzc-fetch-runtime`을 실행하면 된다. 내려받기는 20초씩 세 번 시도하고 포기한다. `wget`을 쓰므로 프록시는 apt 설정이 아니라 환경 변수(`https_proxy`)에서 읽는다.
- **테스트한 이미지에서는 다른 패키지를 설치하지 않는다.** 의존하는 것이 모두 이미지에 들어 있다.
- **저절로 시작되는 것은 없다.** 서비스는 비활성 상태로 설치된다.

## 설정

1. **모델.** 패키지에는 모델이 없다(모델은 AGPL 3.0이고 패키지는 아니다).

   ```sh
   sudo rkzc-fetch-model yolov8n      # 또는 yolov10n. /var/lib/rkzc에 받는다
   ```

   직접 만든 `.rknn`도 쓸 수 있다. rknn_model_zoo 방식으로 내보낸 YOLOv8이나 YOLOv10이어야 한다. `/var/lib/rkzc`에 모두가 읽을 수 있게 둔다. 서비스는 홈 디렉터리를 읽지 못한다.
2. **카메라**를 `/etc/rkzc/detect.toml`에 적는다. RTSP로 오는 H.264이고, 화면 비율이 모두 같아야 한다. 파일 안에 모든 키의 설명이 있다.

   ```toml
   sources = [
       "rtsp://192.168.1.10:554/stream1",
       "rtsp://192.168.1.11:554/stream1",
   ]
   ```

   URL에 비밀번호가 있으면 다른 사용자가 파일을 읽지 못하게 한다. `sudo chown root:video /etc/rkzc/detect.toml && sudo chmod 0640 /etc/rkzc/detect.toml`
3. **점검.** 시작하기 전에 확인한다.

   ```sh
   sudo rkzc-detect --config /etc/rkzc/detect.toml --check
   ```

   모델을 올리고 소스를 하나씩 한 번 열어 보고, 실패한 것을 알려 준다. `sudo` 없이 하려면 장치를 소유한 `video`와 `render` 그룹에 속한 사용자여야 한다(`sudo usermod -aG video,render $USER` 후 다시 로그인). FriendlyElec의 사용자 `pi`는 두 그룹에 모두 속해 있다.
4. **시작.** 지금 시작하고, 부팅할 때마다 시작되게 한다.

   ```sh
   sudo systemctl enable --now rkzc-detect
   journalctl -u rkzc-detect -f       # 로그: 소스가 열리고 끊긴 기록, 1분마다 카운터
   ```

## 검출 결과 쓰기

소켓 `/run/rkzc/detect.sock`에서 한 줄에 JSON 객체 하나가 나온다. `video` 그룹에 속한 사용자가 읽을 수 있다.

```sh
nc -U /run/rkzc/detect.sock
```

```json
{"channel":0,"seq":120,"pts_us":4000000,"time_ms":1795137966573,"width":1920,"height":1080,"detections":[{"class":2,"name":"car","score":0.8411,"x":716.40,"y":451.70,"w":202.38,"h":134.08}]}
```

- `x`, `y`, `w`, `h`는 원본 프레임의 픽셀 단위다. `x`, `y`는 박스의 왼쪽 위 모서리다.
- 읽기를 멈춘 쪽은 연결이 끊긴다. 다시 접속하면 된다. 줄바꿈 없이 끝난 마지막 줄은 버린다.
- **파이썬에서.** 서비스 없이 자기 프로그램 안에서 쓴다(`video`와 `render` 그룹에 속한 사용자로).

  ```python
  import rkzc

  def on_frame(frame):
      for d in frame.detections:
          print(frame.channel, d.name, round(d.score, 2), d.x, d.y, d.w, d.h)

  with rkzc.Detector(["rtsp://192.168.1.10:554/stream1"], "/var/lib/rkzc/yolov8n_i8.rknn", on_frame=on_frame) as det:
      det.start()
      det.wait(60)
  ```

- **C++이나 C에서.** `#include <rkzc/rkzc.h>` 또는 `<rkzc/rkzc_c.h>`를 쓰고 `-lrkzc`로 링크한다.

## 제거

```sh
sudo apt purge rkzc
```

서비스를 멈추고, 패키지의 파일, 내려받은 런타임, 설정, `/var/lib/rkzc`의 모델을 지운다. `apt remove`는 설정과 모델을 남긴다.

## 패키지에 든 것과 라이선스

| 부분 | 위치 | 라이선스 |
|---|---|---|
| `rkzc-detect`, `rkzc-check`, `librkzc`, 헤더, 파이썬 모듈 | `/usr` | Apache 2.0 |
| ffmpeg-rockchip으로 빌드한 FFmpeg 라이브러리 | `/opt/rkzc/lib` | LGPL v3 이상 |
| Rockchip의 `librknnrt.so` | `/opt/rkzc/lib`, 설치할 때 내려받음 | Rockchip의 것. 패키지에 없다 |
| 모델 | `/var/lib/rkzc`, `rkzc-fetch-model`로 내려받음 | AGPL 3.0. 패키지에 없다 |

- 보드의 `/usr/share/doc/rkzc/copyright`에 같은 내용이 전부 적혀 있다. FFmpeg 소스의 커밋과 configure 줄도 거기 있다.
- **함께 넣은 FFmpeg의 소스**는 패키지와 같은 Release에 있는 `ffmpeg-rockchip-8b4385e9.tar.gz`다. 직접 빌드한 것을 쓰려면 `/opt/rkzc/lib`의 라이브러리를 같은 soname의 것으로 바꾸면 된다. 그 전에 파일마다 divert를 걸어 둔다(`sudo dpkg-divert --local --rename --add /opt/rkzc/lib/libavcodec.so.61` 등). 그러지 않으면 다음 업그레이드 때 dpkg가 원래 파일로 되돌린다.
- 이렇게 정한 이유는 [design/licences.ko.md](design/licences.ko.md)에 있다.

## 패키지를 직접 빌드하기

`scripts/setup_board.sh`로 설정한 보드에서 한다(컴파일러, ffmpeg-rockchip, RKNN 헤더가 있다).

```sh
lib/packaging/build_deb.sh dist
```

`dist/`에 `.deb`, FFmpeg 소스 아카이브, `SHA256SUMS`를 쓴다. root 권한은 필요 없다. `lib/packaging/test_clean_image.sh`는 root로 실행하며, 보드의 기본 이미지를 chroot로 띄워 그 안에 패키지를 설치해 본다. 보드를 초기화하지 않고 깨끗한 이미지에서 시험하는 방법이다.

## 알려진 한계

- **보드 한 대와 이미지 하나에서만 테스트했다.** 다른 RK3588 보드나 이미지에서도 동작할 수 있다. 패키지는 다른 이미지나 NPU 드라이버를 보면 알려 주기만 한다.
- **설치는 Rockchip 저장소가 지금 자리에 있어야 한다.** 태그가 옮겨지거나 파일이 바뀌면 `rkzc-fetch-runtime`이 분명한 메시지와 함께 실패하고, 파일을 직접 놓아야 한다.
- **패키지의 한 버전에서 다음 버전으로 올리는 것**은 해 보지 않았다. 버전이 하나뿐이다. 스크립트는 그 경우를 고려해 썼다(돌고 있는 서비스는 다시 시작하고, 런타임, 모델, 설정은 그대로 둔다).
- 라이브러리와 프로그램 자체의 한계는 [lib/README.ko.md](../lib/README.ko.md#알려진-한계)에 있다.
