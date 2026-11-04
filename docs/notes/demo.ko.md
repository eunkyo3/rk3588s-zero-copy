[English](demo.md)

# 검출 데모(M1-7)

`v0.1-m1`의 데모 영상이다. 3 × 3 그리드에 여덟 채널을 놓고, 채널마다 변형 C가 검출한 박스를 그린다. 아홉째 칸은 영상이 무엇인지 적는다. 영문판과 다르면 영문판을 따른다.

## 보여 주는 것과 보여 주지 않는 것

- **검출은 C의 것이다:**
  - RGA3가 디코딩된 프레임을 NPU가 읽는 입력 버퍼에 레터박스로 넣는다. CPU 픽셀 복사는 없다(A6).
  - NPU에서 YOLOv8n INT8을 돌린다.
  - `tools/demo_detect`가 이 경로와 파이프라인의 후처리를 돌린다.
- **채널마다 5 fps:** 본편 조건처럼 6프레임에 1프레임이다. 박스는 그 채널의 다음 추론까지 화면에 남는다. 실시간 화면과 같다.
- **오프라인 렌더:** 검출을 먼저 다 해 두고, `tools/demo_render.py`가 원본 영상 위에 박스를 그린다. 그래서 영상은 시간, CPU, 지연에 대해 아무것도 말하지 않는다. 그것은 벤치마크가 잰다([comparison.ko.md](comparison.ko.md)).
  - `demo_detect`는 NPU 코어 0에서 한 프레임씩 처리한다. 파이프라인의 스레드, 큐, 슬롯은 쓰지 않는다.
  - 코어의 코드는 바꾸지 않고 그대로 쓴다. 디코더(`h264_rkmpp`), C의 백엔드, NPU 컨텍스트, 후처리다.
- **채널:** M1 배정([stream-server/assignment_m1.txt](../../stream-server/assignment_m1.txt))의 1–8번이다. 그래서 영상 ①–④가 두 번씩 나온다.
  - 두 칸이 같은 프레임을 보이지 않도록, 채널 5–8은 영상의 10초 지점에서 시작한다.
  - 영상이 끝나면 처음부터 다시 나온다. 송출 서버와 같다.
- **측정이 아니다:** 파일에 실행 종류가 없고, `results/` 밖에 둔다.

## 파일

| 파일 | 내용 |
|---|---|
| `tools/demo_detect.cpp` | 영상 파일에서 6프레임마다 C로 검출한 결과를 JSONL로 쓴다. 헤더(영상, 모델 해시, 전처리, 레터박스 띠, 레이블, 커밋) 다음에, 샘플한 프레임마다 번호, PTS, 모델 입력 좌표의 박스 |
| `tools/demo_render.py` | 그리드 영상. 1920 × 1080, 30 fps, 20초, H.264(libx264) |
| `tools/demo.sh` | 둘 다 보드에서 root로 돌린다. 영상마다 SHA-256을 매니페스트와 대조한 뒤, 모두 `docs/report/demo/`에 쓴다. |
| `docs/report/demo/` | 검출 결과와 로그, 정지 화면(`demo_m1.jpg`), 영상의 SHA-256 |

- 영상은 저장소에 넣기에 크고 저장소가 `*.mp4`를 무시하므로, `v0.1-m1` Release에 첨부한다.
- **2026-11-04의 실행:** M1.1 실행을 마친 뒤 03:43–03:45 UTC에 커밋 1eebc77로 보드에서 `tools/demo.sh`를 돌렸다. 영상은 30 MB이고, 그 SHA-256은 `docs/report/demo/demo_m1.mp4.sha256`에 있다.
- **테스트:** `tests/test_demo_render.py`가 채널 배정, 검출 파일, 박스 좌표 변환을 다룬다.

## 영상과 라이선스

| | 영상 | 제작자 | 라이선스 |
|---|---|---|---|
| ① | [Pexels 36330861](https://www.pexels.com/video/urban-crosswalk-scene-with-busy-traffic-36330861/), 도심 횡단보도 | SHOX ART | [Pexels License](https://www.pexels.com/license/) |
| ② | [Pexels 29130409](https://www.pexels.com/video/vibrant-city-street-with-busy-traffic-29130409/), 도시 거리 | Paul Bill | Pexels License |
| ③ | [Pexels 29969146](https://www.pexels.com/video/night-traffic-and-pedestrians-in-istanbul-29969146/), 밤의 이스탄불 | Sururi Ballıdağ | Pexels License |
| ④ | [Pixabay 180386](https://pixabay.com/videos/intersection-cars-pedestrian-night-180386/), 밤의 오사카 | Kanenori | [Pixabay Content License](https://pixabay.com/service/license-summary/) |

- **공개 전 확인(계획 R14), 2026-11-04:**
  - 두 라이선스 모두 영상을 수정해 결과를 공개하는 것을 허용한다. 출처 표기는 둘 다 필수가 아니지만, 데모는 표기한다.
  - **Pexels:** 수정하지 않은 사본을 팔지 말 것, 다른 스톡 플랫폼에 재배포하지 말 것, 영상 속 사람이나 브랜드가 보증한다고 암시하지 말 것
  - **Pixabay:** 콘텐츠를 단독(standalone)으로 배포하지 말 것. 박스를 그린 그리드처럼 새 작업물로 합친 것은 단독 사용이 아니다. 보증을 암시하거나 오해를 부르는 방식으로 쓰지 말 것
  - 데모는 이 중 어디에도 해당하지 않는다. 축소한 클립에 박스를 그린 비상업적 그리드다.
- **영상 원본은 각자의 라이선스를 따른다.** 저장소의 Apache License 2.0은 코드에 적용되고, 데모 영상 속 원본 영상에는 적용되지 않는다. Release 설명에도 그렇게 적는다.
