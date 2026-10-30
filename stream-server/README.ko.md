[English](README.md)

# 송출 서버

벤치마크의 RTSP 스트림(PROTOCOL §3)을 보드가 아닌 PC에서 유선 이더넷으로 송출한다. 채널 `chN`마다 영상 하나를 반복한다. 송출 프로세스는 보드가 접속하면 시작하고 접속을 끊으면 멈추므로, 매 실행은 각 영상을 처음부터 받는다. 영문판과 다르면 영문판을 따른다.

## 준비

1. `stream-server/fetch_mediamtx.sh`가 Linux(amd64, arm64) 또는 Windows(Git Bash)용 mediamtx v1.21.2를 내려받고 SHA-256을 확인한다.
2. `ffmpeg`를 `PATH`에 둔다. 재다중화(`-c copy`)만 하므로 최근 빌드면 된다. 버전은 실행 로그와 함께 기록한다.
3. 방화벽에서 TCP 8554 수신을 허용한다. 서버는 RTSP over TCP만 받고, 이 PC만 송출할 수 있다.

## 영상

```sh
stream-server/prepare_videos.sh <out_dir> <source>...
```

16:9 원본을 벤치마크 인코딩으로 재인코딩한다: 1920×1080 H.264 High, 30 fps, B프레임 없음, GOP 30, 4 Mbps CBR, 제한 범위, 오디오 없음. 결과를 검사하고 `<out_dir>/manifest.tsv`에 한 줄을 추가한다. 이 줄에는 출력과 원본의 SHA-256, 원본의 크기, 프레임 레이트, 픽셀 포맷, 색 행렬, 원색, 전달 특성, 범위가 기록된다. 색 행렬은 바꾸지 않는다(PROTOCOL §8).

벤치마크 영상과 그 출처, 라이선스는 선정되면 여기에 적는다. 선정은 M1-3 전에 해야 한다.

## 실행

```sh
cat > assignment.txt <<EOF
ch1 videos/a.mp4
ch2 videos/b.mp4
EOF
stream-server/publish.sh assignment.txt
```

보드는 `rtsp://<pc>:8554/chN`을 TCP로 읽는다. `publish.sh`를 시작할 때마다 `stream-server/logs/<UTC 시각>-<pid>/`이 생기고, 그 안에 다음 파일이 들어간다.

- `assignment.tsv`: 채널, 영상, SHA-256
- `versions.txt`: mediamtx와 ffmpeg 버전
- `mediamtx.log`: 송출 프로세스의 시작과 중지, 수신 측의 접속과 해제. PROTOCOL §9.3이 이를 근거로 유효성을 판정한다.
- `console.log`: 송출 프로세스 자체의 오류 출력

`publish.sh`는 시작 전에 배정 전체를 검사하고, 서버를 두 번 띄우지 않는다. 채널-영상 배정은 `freeze-m1`에서 고정한다.

송출 프로세스는 마지막 수신 측이 떠나고 1초 뒤 멈춘다. 그 1초 안에 접속한 수신 측은 돌고 있던 송출에 붙어서 영상을 처음부터 받지 못한다. 실행과 스트림 점검 사이에는 1초 넘게 간격을 둔다. PROTOCOL §9.2의 쿨다운 게이트가 이미 60초 이상을 둔다.

## 확인한 것

2026-10-30에 서버와 수신 측을 둘 다 보드에 두고 localhost로 확인했다.

- **루프 경계의 타임스탬프:** `ch1`을 65초 읽어 패킷 1,951개를 받았고, 타임스탬프는 엄격히 증가했다. 20초 클립의 루프 경계 세 곳을 지나는 동안 가장 큰 간격은 1/30초였다.
- **송출 속도:** 스트림 65초분을 받는 데 64.9초가 걸렸다. 즉 실시간으로 보낸다.
- **요청 시 시작:** 송출 프로세스는 접속하면 시작했고, 수신 측이 떠나고 1초 뒤 멈췄다. 다음 수신 측이 받은 첫 프레임은 파일의 첫 프레임과 같았다.
- **동시 수신:** 채널 두 개를 동시에 읽었다.
- **송출 실패:** 수신 중에 송출 프로세스를 강제로 끄자 로그에 `[path ch1] runOnDemand command stopped: path destroyed`가 남았고, 송출 세션은 `destroyed: torn down`, 수신 세션은 `destroyed: terminated`로 기록됐다. 정상 중지는 `stopped: not needed by anyone`으로 남는다. 송출 프로세스는 다시 시작되지 않았다.
- **배정 검사:** 잘못된 채널 이름, 중복 채널, 빈 배정을 각각 아무것도 쓰기 전에 거부했다.
- **서버 하나만:** 첫 `publish.sh`가 도는 동안 두 번째 실행을 거부했다.
- **색 태그:** `prepare_videos.sh`가 원본의 색 태그(BT.709)와 범위를 유지했다.

Windows에서는 아직 실행해 보지 않았다.
