[English](licences.md)

# 패키지가 건드리는 것들의 라이선스, 그리고 "담을지 받을지"

**상태:** [packaging.ko.md](packaging.ko.md)의 1단계다. 2026-11-19에, 이 저장소가 고정한 버전의 라이선스 원문을 읽었다. 프로젝트가 읽은 결과이며 법률 자문이 아니다. 상업적으로 쓰려면 따로 확인을 받아야 한다.

**용도(소유자, 2026-11-19):** 상업 제품이 아니라 오픈소스 포트폴리오 프로젝트다. 아래 결정은 용도와 무관하게 같다. Rockchip 파일의 재배포 제한은 배포하는 누구에게나 적용되고, 모델을 밖에 두어야 패키지가 Apache 2.0으로 남는다.

**결과:**

| 대상 | 읽은 라이선스 | 결정 |
|---|---|---|
| librknnrt 2.3.2(바이너리) | Rockchip 자체 라이선스. 재배포 권리를 주지 않는다 | **설치할 때 받는다.** 담지 않는다 |
| RKNN 헤더(`rknn_api.h`) | 독점 고지. 서면 허락 없이 복제 금지 | **빌드할 때 받는다.** 담지 않고, 공개 헤더에도 넣지 않는다 |
| 여기서 빌드한 ffmpeg-rockchip | LGPL v3 또는 그 이후 | 별도 공유 라이브러리로 **담는다.** 고지와 소스를 함께 둔다 |
| MPP 유저스페이스 | Apache 2.0 또는 MIT | 담지 않는다. 이미지의 것을 쓴다 |
| librga | Apache 2.0 | 담지 않는다. 이미지의 것을 쓴다 |
| 모델(`.rknn`) | AGPL 3.0 | **패키지에 넣지 않는다.** 사용자가 선택해서 받거나 자기 모델을 쓴다 |
| 이 저장소 | Apache 2.0 | — |

제안서와 달라진 점이 두 가지다. RKNN **헤더**에도 제한이 있고, 사용자에게는 두 라이브러리보다 **모델의 AGPL**이 더 중요하다.

## librknnrt와 헤더: 받는다

- **저장소의 라이선스**([rknn-toolkit2 v2.3.2, `LICENSE`](https://github.com/airockchip/rknn-toolkit2/blob/v2.3.2/LICENSE))는 저작권 표시와 보증 부인이다. "All rights reserved"라고 쓰여 있고, 복제, 수정, 재배포를 허락하는 문장이 없다.
- **헤더** `rknn_api.h`(같은 태그)에는 내용이 "confidential"이며 "Rockchip Corporation의 명시적인 서면 허락 없이는 어떤 목적으로도 공개, 복제, 복사, 전송, 사용할 수 없다"는 고지가 있다.
- **Rockchip이 하는 일:** 두 파일을 공개 저장소에 올려 내려받게 한다. 이것은 얻는 방법이지, 남에게 다시 넘겨도 된다는 허락이 아니다.
- **그래서:**
  - 패키지는 설치할 때 Rockchip의 저장소에서 `librknnrt.so`를 태그와 SHA-256으로 받는다. 지금 `scripts/setup_board.sh`가 하는 방식이다.
  - RKNN 헤더는 이 저장소에도 패키지에도 복사하지 않는다. 지금도 저장소에는 없다. `tools/setup/fetch_rknn_headers.sh`가 빌드할 때 `/opt/rknpu2/include`에 받는다.
  - 라이브러리의 공개 헤더는 RKNN 타입을 드러내지 않는다. 제안서가 다른 이유로 이미 원하던 것이다.
- **받는 방식의 비용:** 설치에 네트워크와 GitHub이 필요하고, Rockchip이 태그를 옮기거나 지우면 설치가 깨진다. SHA-256이 있으므로 파일이 바뀌면 분명한 실패로 드러난다. 오프라인 설치에서는 사용자가 파일을 직접 넣어야 한다.
- **담으려면** Rockchip의 서면 허락이 필요하다. 아직 요청한 적이 없다.

## ffmpeg-rockchip: LGPL v3으로 담는다

- **이 빌드의 라이선스:** 고정한 커밋(`8b4385e9`)의 `configure`는 `rkmpp`와 `rkrga`를 `EXTERNAL_LIBRARY_VERSION3_LIST`에 두므로 `--enable-version3`이 필요하다. 이 플래그가 있고 `--enable-gpl`과 `--enable-nonfree`가 없으면 `configure`는 라이선스를 "LGPL version 3 or later"로 정한다. `tools/setup/build_ffmpeg_rockchip.sh`의 빌드가 이것이다.
- **왜 버전 3인가:** MPP와 librga는 Apache 2.0이다. FFmpeg의 `LICENSE.md`는 Apache 2.0이 LGPL v2.1과는 맞지 않고 v3과는 맞는다고 한다.
- **Rockchip용 파일**(`libavcodec/rkmppdec.c`, `libavfilter/rkrga_common.c`)에는 FFmpeg의 통상적인 LGPL v2.1-or-later 헤더가 있다.
- **Apache 2.0 코드에서 링크:** 이 저장소의 프로그램과 라이브러리는 FFmpeg을 동적으로 링크한다. LGPL이 전제하는 형태다.
- **라이브러리 네 개**(libavcodec, libavformat, libavutil, libswresample)**를 담으려면 패키지에 있어야 하는 것:**
  1. LGPL v3과 GPL v3의 전문(`COPYING.LGPLv3`, `COPYING.GPLv3`), 그리고 패키지가 FFmpeg을 LGPL v3으로 쓴다는 고지.
  2. 정확한 소스: 커밋, `configure` 줄, 빌드 스크립트. 가장 안전하고 간단한 형태는 그 커밋의 소스 압축 파일을 `.deb`와 같은 Release에 함께 올리는 것이다. 남의 저장소 링크만으로 끝내지 않는다.
  3. 사용자가 자기 빌드로 바꿀 수 있는 별도 `.so` 파일. `/opt/rkzc/lib`와 rpath가 이 조건을 채운다.
- **이 빌드가 Ubuntu에서 링크하는 것:** zlib, liblzma, libX11/libxcb, libdrm. `configure`가 자동으로 찾아 동적으로 링크한다. (5단계에서 패키지로 만든 결과: 함께 넣는 라이브러리 네 개는 zlib, liblzma, libdrm이 필요하다. libX11과 libxcb는 넣지 않는 FFmpeg 부분에만 필요하다.) 이들은 Ubuntu 패키지로 두고(`.deb`의 `Depends:`) 담지 않는다. `configure` 줄을 줄이면 일부를 뺄 수 있지만, 그러면 측정한 빌드가 아니게 된다.
- **설치할 때 빌드하는 방법**(보드에서 6분)을 쓰면 FFmpeg 바이너리를 아예 배포하지 않는다. 이것은 대안으로만 둔다. 사용자 보드에 컴파일러가 필요하고, 설치가 느리고 깨지기 쉬워진다.

## MPP와 librga: 이미지의 것

- **MPP**([rockchip-linux/mpp](https://github.com/rockchip-linux/mpp), `LICENSES/`): Apache 2.0과 MIT.
- **librga**([airockchip/librga](https://github.com/airockchip/librga), `COPYING`): Apache 2.0.
- 둘 다 FriendlyElec 이미지에 들어 있고, 패키지는 여기에 의존만 한다. 라이선스로는 담아도 되지만, 이들이 상대하는 커널 드라이버가 이미지의 것이라서 다른 버전을 담으면 얻는 것 없이 위험만 늘어난다.
- 읽은 것은 업스트림 저장소의 라이선스다. FriendlyElec 이미지에 든 파일 자체를 확인한 것은 아니다.

## 모델: AGPL 3.0, 패키지 밖에 둔다

- **YOLOv8n**은 Ultralytics YOLOv8에서, **YOLOv10n**은 THU-MIG의 YOLOv10에서 파생됐다. 두 저장소의 `LICENSE`는 GNU AGPL v3이다. 이 저장소는 이미 모델 파일을 트리가 아닌 Release에서 AGPL 3.0으로 배포한다([models/README.ko.md](../../models/README.ko.md)).
- **패키지에는 모델을 넣지 않는다.** `.deb`는 Apache 2.0에 LGPL 라이브러리가 더해진 상태로 남는다. 모델은 별도의 명시적인 명령으로 이 저장소의 Release에서 받고, 그때 라이선스를 보여 준다.
- **사용자에게 의미하는 것:**
  - 자기 보드에서 개인 용도나 연구로 쓰는 경우: 실질적인 부담이 없다.
  - 이 모델로 네트워크 서비스를 제공하거나 제품을 출하하는 경우: AGPL의 소스 공개 의무가 모델에 적용된다. Ultralytics의 해석으로는 모델을 둘러싼 소프트웨어에도 적용된다. Ultralytics는 이 경우를 위한 별도 라이선스를 판매한다.
  - AGPL을 받아들일 수 없는 사용자는 다른 라이선스의 모델이 필요하다. 제안서의 열린 질문 2번(사용자가 후처리를 주는 임의의 `.rknn`)이 이것이며, 이제 기술적 이유 말고 라이선스 이유도 생겼다.
- **이 저장소의 박스 디코딩 코드**는 rknn_model_zoo v2.3.2를 따랐고, 그것은 Apache 2.0이다. AGPL 모델을 실행하는 프로그램까지 AGPL이 되는지는 논쟁이 있는 문제이며, 이 문서는 결론을 내리지 않는다.

## 제안서에서 바뀌는 것

- **의존성 표:** librknnrt의 선택지는 "받는다" 하나다. ffmpeg-rockchip은 위 세 가지를 갖춰서 담는다.
- **패키지에 새로 들어가는 파일:** `/usr/share/doc/rkzc/copyright`. 구성 요소마다 라이선스와 소스의 위치를 적는다.
- **패키지의 약속**에 한 줄이 늘어난다: Rockchip의 것은 Rockchip에서 내려받은 것만 설치하고, 모델은 설치하지 않는다.
- **5단계**(패키징 스크립트)에 ffmpeg-rockchip 소스 압축 파일을 Release 자산으로 올리는 일이 추가된다.

## 확인하지 않은 것

- FriendlyElec 이미지 자체와 그 안의 커널 드라이버의 라이선스. 패키지는 이들을 배포하지 않는다.
- `.deb`가 의존할 Ubuntu 패키지 각각의 라이선스. apt가 Ubuntu에서 설치한다.
- H.264 디코딩의 특허. 디코더는 보드의 하드웨어다. 제품으로 만들려면 따로 살펴야 한다.
- 인용한 두 파일 밖에 Rockchip이 서면으로 낸 조건이 있는지. Rockchip이 재배포 라이선스를 공개하거나 허락하면 "받는다"를 "담는다"로 바꿀 수 있다.
