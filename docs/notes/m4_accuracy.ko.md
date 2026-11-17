[English](m4_accuracy.md)

# M4의 정확도: 도구와 절차

M4의 정확도(prereg/M4.md §2)를 어떻게 돌리는지 적는다.
- **규범:** [prereg/M4.md](../../prereg/M4.ko.md). 이 노트는 도구가 그것을 어떻게 따르는지 적는다. 영어판이 우선한다.
- **지금까지 다룬 것:** 평가 이미지, COCO 파일, 입력, Python 규칙, PC의 FP32 패스, 평가.
- **아직 없는 것:** 보드의 INT8 패스, C++ YOLOv8n 규칙, 두 구현 사이의 §8 3단계 점검. 각자의 도구와 함께 여기에 더한다.

## 평가 이미지(§8 2단계)

- **[tools/eval/Dockerfile](../../tools/eval/Dockerfile):** 변환 이미지를 그대로 쓰고 `pycocotools` 2.0.11만 더한다.
  - **변환 이미지:** `python:3.10-slim-bookworm@sha256:5be5aaec…` 위에 [tools/model/Dockerfile](../../tools/model/Dockerfile)로 만든 것이다. 이미지 ID는 `sha256:3ea3796529342281fdb543b8911261112e5ab942798c2fea4363edf3eca67f8f`이고, 두 모델을 변환했다.
  - **pycocotools:** wheel의 SHA-256으로 고정한다([requirements.txt](../../tools/eval/requirements.txt)). 필요한 것은 numpy뿐이고 그것은 바탕 이미지에 있으며, `pip check`도 깨진 의존성을 찾지 못한다.
- **패키지:** Python 3.10, numpy 1.26.4, opencv-python 4.11.0.86, onnxruntime 1.23.2, pycocotools 2.0.11.
- **이미지:** `sha256:c676e76f95356ea1aac39a87694ac5a498207890922f2a01c9fdbedd773f250a`, 2026-11-16T05:31:15Z 생성.
  - **저장소에 고정:** ID는 [tools/eval/image_id.txt](../../tools/eval/image_id.txt)에 있다. `build.sh`와 `pc_run.py`는 다른 이미지를 거부하고, 실행은 이 ID로 이미지를 쓴다.
  - **ID의 뜻:** Docker Desktop의 containerd 이미지 저장소(계속 켜 두어야 한다)에서는 ID가 이미지 인덱스의 다이제스트(`RepoDigests`)다. 인덱스는 linux/amd64 매니페스트 `sha256:aa27b59bbb633a0c5e209326e5c970bfa033710d32491fd41cba555908e69a06`을 가리키고, 그 설정은 `sha256:01691948e3ba4917bae6d056b9e18f3d0ef886e34cea08f608e799b36f494f29`다(아래 보관본에서 읽었다). containerd 저장소가 없으면 Docker는 이 설정 다이제스트로 이미지를 부른다.
  - **앞선 빌드:** 그날 한 빌드는 `sha256:e23dd324…`를 출력했다. `build.sh`가 다시 빌드하지 않게 바뀌기 전의 일이다.
- **빌드와 확인:** `MSYS_NO_PATHCONV=1 tools/eval/build.sh`.
  - **한 번만:** 이미지 위에 다시 빌드하지 않는다. 이미지가 없으면 아래 보관본에서 `docker load -i`로 되살리고, 그러면 ID가 그대로다. 다시 빌드하면 ID가 바뀔 수 있다. `--rebuild`를 주면 다시 빌드하고, 그 새 ID는 커밋으로 `image_id.txt`에 넣어야 한다.
  - **확인하는 것:**
    - 바탕 이미지의 ID, 그리고 이미지의 레이어가 바탕 이미지의 레이어로 시작하는지;
    - 이미지의 ID가 `image_id.txt`와 같은지;
    - `pip freeze`가 [constraints.txt](../../tools/eval/constraints.txt)(변환 이미지의 것에 pycocotools를 더한 것)와 같은지;
    - `pip check`, 그리고 pycocotools의 COCOeval이 import되는지.
  - **기록:** `results/check/<UTC>_m4_eval_image/image.txt`.
    - **2026-11-16 06:59:49Z:** 모든 확인을 통과했다.
    - **2026-11-16 06:44:30Z:** 앞선 기록이다. 파이프가 종료 상태를 가져가서, 이때의 `pip check`와 import 확인은 실패할 수가 없었다. 리뷰에서 이를 찾아 스크립트를 고쳤다.
- **보관본:** `MSYS_NO_PATHCONV=1 tools/eval/save_image.sh`가 바탕 레이어까지 포함해 이미지를 `docker save`로 저장소 밖에 저장한다.
  - **이유:** 그러지 않으면 Docker Desktop 초기화나 prune으로 잃을 수 있다.
  - **복원:** `docker load -i <파일>`.
  - **2026-11-16의 보관본:** `D:/m4data/images/m4-eval_c676e76f95356ea1.tar`, 583,230,976바이트, SHA-256 `ae9a7dcfd59672e8573cda16c7985c99542c0e8f0c4c2543ca9f38d3f53d71ab`. 그 기록은 `results/check/<UTC>_m4_eval_image_archive/archive.txt`다.

## COCO 파일(§2.1)

- **내려받기:** `tools/eval/fetch_coco.sh [DATA]`(기본 `D:/m4data`)가 `val2017.zip`과 `annotations_trainval2017.zip`을 저장소 밖의 `DATA/coco`에 둔다.
  - 그다음 [tools/eval/coco.sha256](../../tools/eval/coco.sha256)과 대조한다.
  - 이미 있는 파일은 다시 내려받지 않고 대조만 한다. 잘못된 파일은 먼저 지워야 한다.
- **출처:** S3 버킷 `images.cocodataset.org`를 `s3.amazonaws.com/images.cocodataset.org`에서 HTTPS로만(리다이렉트 포함) 읽는다.
  - 버킷 자체의 이름에는 맞는 인증서가 없다. `https://images.cocodataset.org`는 이름이 맞지 않아 실패한다.
  - 평문 `http://`는 전송 중 무결성을 주지 않는다.
- **2026-11-16의 내려받기:**
  - **크기:** 815,585,330바이트와 252,907,541바이트.
  - **S3 `Last-Modified`:** 2018-07-11과 2018-07-10.
  - **MD5:** `442b8da7639aecaf257c1dceb8ba8c80`과 `f4bbac642086de4f52a3fdda2de5fa2c`. 이 파일들에 대해 흔히 공개된 값과 같다. 주석 zip의 MD5는 S3 `ETag`와도 같다.
- **라이선스:** 이미지는 Flickr 라이선스를 따르므로 저장소 밖에 둔다. 주석은 CC BY 4.0이다.

## PC에서의 실행(§4)

[tools/eval/pc_run.py](../../tools/eval/pc_run.py)가 저장소의 도구를 평가 이미지에서 돌린다.
`MSYS_NO_PATHCONV=1 python tools/eval/pc_run.py --kind <check|m4> --name <이름> [--data D:/m4data] -- <도구> <인자>`
- **Git Bash에서:** `MSYS_NO_PATHCONV=1`이 반드시 필요하고, 도구가 이를 확인한다. 없으면 Git Bash가 컨테이너의 `/data/...`를 Windows 경로로 바꾼다. `--data`에는 Windows 경로(`D:/...`)를 준다.
- **도구를 시작하기 전에 확인하는 것:**
  - **코드:** `HEAD`. 도구가 git으로 추적되어야 한다. 추적되는 파일이 `HEAD`와 다르거나, 도구가 import할 수 있는 추적되지 않는 파일이 `tools/`나 `tests/`에 있으면 실행을 거부한다. `--allow-dirty`를 주면 개발용 점검은 그래도 돌고 그 사실이 기록되지만, 실행 종류 `m4`에는 줄 수 없다;
  - **이미지:** `image_id.txt`의 이미지를 그 ID로 찾는다;
  - **CPU 모델;**
  - **호스트 시계:** NTP와의 오프셋. `w32tm /stripchart /computer:time.windows.com /samples:3 /dataonly`로 잰다. 응답한 샘플만 세고, 나머지는 기록한다;
  - **시계의 동기화 상태:** `w32tm /query /status`;
  - **컨테이너 시계:** 호스트 시계와의 오프셋. 컨테이너 하나로 5번 주고받아 왕복이 가장 짧은 것으로 정한다.
- **거부:** NTP 응답이 하나도 없거나, 어느 오프셋이든 1초를 넘으면 도구를 시작하지 않는다. 실행 종류 `m4`는 `freeze-m4`의 행이 env.md에 생기기 전에는 시작하지 않는다.
- **실행:**
  - **기록:** `results/<종류>/<UTC>_<이름>/`. 그 `pc.json`에 위의 내용과 호스트 시계로 잰 시작 시각, 실행 종류, M4의 단계 커밋, 효력 있는 수정 항목이 들어간다(PROTOCOL §1.4, M4 §4). 그래서 도구가 일찍 멈춰도 `tools/check_prereg_order.py`가 확인하는 헤더가 남는다.
  - **컨테이너:** 이미지의 ID로 돌린다. 저장소는 `/repo`에 읽기 전용으로, 기록 폴더는 `/record`에, 데이터는 `/data`에 붙인다. 네트워크가 없고 새 권한을 얻지 못한다.
  - **제한:** 변환 때처럼 메모리 2 GB(스왑 포함 4 GB). 다른 프로젝트의 컨테이너가 같은 Docker VM을 쓴다.
  - **평가의 한도:** `tools/m4_coco.py`는 스왑 없이 6 GB를 쓴다(아래 평가 절 참고). 스왑이 없으니 더 필요한 실행은 디스크를 혹사하지 않고 바로 실패한다. 다른 컨테이너가 실행 중이거나 Docker VM이 7 GB보다 작으면 시작하지 않는다. `pc.json`은 한도를 `container_memory`로 기록한다.
  - **Python:** 입력이 내려받은 데이터이므로 `python3 -I`로 돌린다.
  - **`--checks-only`:** 도구를 돌리지 않고 `pc.json`만 남긴다. 예를 들어 PC의 시계를 기록할 때 쓴다.
- **PC:** Intel Core i7-6500U.
- **2026-11-16의 시계:**
  - **06:14 UTC 무렵까지:** Windows Time 서비스가 꺼져 있었다. `w32tm /query /status`는 `0x80070426`으로 실패했고, 입력 실행의 `pc.json`이 이를 기록한다. 응답한 stripchart 샘플은 +0.75~+0.76초로 §4의 1초 안이었지만, 시계를 바로잡는 것이 없었다.
  - **06:14 UTC 무렵, 사용자가 알린 대로:** 사용자가 서비스를 자동 시작으로 바꾸고, `time.windows.com`을 원본으로 지정하고, 다시 동기화했다. 직후에도 stripchart는 +0.78초였다.
  - **06:48 UTC:** `results/check/20261116T064832Z_pc_clock`이 +0.59초를 기록했다(샘플 셋 가운데 하나 응답). `w32tm /query /status`는 성공했고, 현지 시각 15:14:56에 `time.windows.com`과 동기화되어 있었다.
  - **남아 있는 이유:** 단독 Windows PC의 기본값은 1초 미만의 차이를 한 번에 옮기지 않고 천천히 바로잡는다. 약 35분 사이에 +0.78초에서 +0.59초로 줄었다. PC 패스 전에 다시 잰다.

## 입력과 데이터 점검(§2.2, §8 3단계)

`MSYS_NO_PATHCONV=1 python tools/eval/pc_run.py --kind check --name m4_inputs -- tools/m4_prep.py --coco /data/coco --out /data/<새 폴더>`

- **[tools/m4_prep.py](../../tools/m4_prep.py)가 하는 일:**
  - **zip 먼저:** 두 파일의 SHA-256을 확인한다. 어느 하나라도 다르면 다른 것은 읽지 않는다.
  - **하나씩 짝짓기:** val2017의 JPEG 5,000개와 주석의 이미지 5,000개가 하나씩 짝지어지는지, id마다 한 번이고 id를 이름으로 쓰는지 확인한다. `val2017/<12자리>.jpg` 항목만 `DATA/coco/val2017/`에 쓴다.
  - **준비:** 이미지마다 §2.2대로 준비하고, PNG를 다시 읽어 확인한다.
  - **양자화 이미지:** 20장을 [models/coco_subset_20.sha256](../../models/coco_subset_20.sha256)과 대조한다.
- **출력:**
  - **입력:** PNG 5,000개, 새 폴더에 `<이미지 id>.png`로.
  - **기록:** 기록 폴더의 `manifest.tsv`와 `m4_inputs.csv`.
  - **헤더:** 둘 다 `pc.json`의 헤더와, OpenCV의 버전과 최적화 상태를 담는다.
  - **해시:** `m4_inputs.csv`는 매니페스트 전체의 SHA-256과 행만의 SHA-256을 준다. 행만(열 이름 줄과 데이터, 헤더 빼고)의 값은 픽셀이 같은 실행이면 같다.
- **보드의 점검:** 보드가 PNG 5,000개를 모두 매니페스트의 바이트로 디코딩하는지는 보드 도구와 함께 들어온다.
- **테스트:** OpenCV가 필요한 테스트는 이미지 안에서, 기록과 함께 돈다: `MSYS_NO_PATHCONV=1 python tools/eval/pc_run.py --kind check --name m4_prep_tests -- tests/test_m4_prep.py`.
- **실행:**
  - **`results/check/20261116T053859Z_m4_inputs`:** 곧바로 멈췄다. Git Bash에서 `MSYS_NO_PATHCONV=1` 없이 돌려서 `/data/coco`가 Windows 경로로 바뀌었다. 이제 `pc_run.py`가 그것을 거부한다.
  - **`results/check/20261116T054025Z_m4_inputs`:** 38분, `passed,1`, PNG는 `D:/m4data/inputs`.
    - **이미지:** 5,000장 모두 주석의 크기로 디코딩되었다. 903장은 크기를 바꿨고, 4,097장은 긴 변이 이미 640이었다.
    - **PNG:** 모두 다시 읽었을 때 캔버스의 픽셀과 같았다.
    - **양자화 이미지:** 20장 모두 5,000장 안에 있고, 각각 기록된 SHA-256과 같다.
    - **`manifest.tsv`:** SHA-256 `f3ad1dbee36fd6109637374a75c16ff7a6bb150d0e266b2df02d6f8215e1d766`.
    - **대체됨:** 이후 리뷰가 헤더에 `freeze_m1_commit`(PROTOCOL §1.4)을 넣고 위의 확인들을 더하라고 했다. 그래서 최종 도구로 다시 돌렸다(아래).
  - **`results/check/20261116T064917Z_m4_inputs`, M4가 쓰는 입력:** 최종 도구(커밋 `9dbf814`)로 다시 돌린 실행, 37분, `passed,1`, PNG는 `D:/m4data/inputs2`.
    - **같은 수치:** 이미지 5,000장, 크기를 바꾼 것 903장, 크기나 PNG의 불일치 없음, 양자화 이미지 20장 모두 기록된 SHA-256과 같음.
    - **`manifest_rows_sha256`:** `5a63ad71886c7d670184bd1f91601a719d6d0f456d5e5c35bfa93c2418d69a69`, 첫 실행의 행과 같다. 즉 입력 5,000개가 바이트 단위로 똑같이 나왔다.
    - **`manifest.tsv`:** 헤더를 포함한 SHA-256 `b29adf50175677fd031adbc2600f68a5115a075c80095b73d5b8665b35ffc444`.
    - **그 전의 확인:** NTP 오프셋 +0.582초(샘플 셋 가운데 하나 응답), 컨테이너 −0.003초. Windows Time 서비스는 켜져 있었다.
    - **OpenCV:** 4.11.0, 최적화 사용.

## Python 규칙(§2.3)

[tools/m4_rules.py](../../tools/m4_rules.py)에 두 규칙이 있다. 규칙은 float32 출력에서 돈다. INT8 출력은 `dequantize`, 즉 C++의 식 `(q − zp) × scale`로 float32가 된다.
- **연산:** float32로, C++(`src/core/yolov10_post.cpp`, `yolov8_post.cpp`)와 같은 순서로 한다.
  - 후보는 점수가 0.001보다 큰 (셀, 클래스) 쌍이다.
  - 후보는 점수 순으로 놓고, 점수가 같으면 분기, 셀, 클래스 순이다.
  - 박스는 각자의 셀에서 디코딩한다. 변마다 DFL 빈 16개에 대한 소프트맥스 기댓값을 빈 하나씩 더해 구하고, `(x + 0.5 − d) × stride`로 계산한다.
- **`yolov10_rule`:** 가장 좋은 후보 300개, NMS 없음.
- **`yolov8_rule`:** 처음 30,000개 후보를 그 순서대로 내려가며 클래스별 탐욕 NMS를 한다. 후보는 같은 클래스의 앞서 남긴 후보와의 IoU(`yolov8_post`와 같은 계산)가 0.7보다 크면 버린다. 처음 남은 300개가 결과다. 점수 합 출력은 쓰지 않는다.
- **동점(§6):** 두 규칙 모두 301번째 검출이 300번째와 같은 점수인지도 알려 준다.
- **보드와의 대조:** 탐색 때의 보드 덤프(21장, YOLOv10n INT8)에서 `yolov10_rule`은 `yolov10_post`의 목록과 같다. conf 0.25와 0.001의 목록 42개 모두 개수, 순서, 클래스가 같다.
  - **가장 큰 차이:** 점수 4.9e-10, 박스 6.2e-5 px. §2.3의 허용치는 1e-6과 1e-3 px다.
  - **테스트:** 덤프가 있는 곳이면 테스트가 이 대조를 되풀이한다(덤프는 커밋하지 않는다).
  - **아직 할 것:** 처음 50장에서 두 규칙 모두를 대조하는 §8 3단계 점검은 보드 도구와 함께 들어온다.

## 검출 파일

패스는 저장소 밖 자기 폴더 `D:/m4data/passes/<기록>/`에 표 두 개를 쓴다. 둘 다 `# key: value` 헤더가 있다(`pc.json`의 필드, 패스, 모델과 그 SHA-256, 정밀도, 규칙, 매니페스트의 SHA-256들).
- **`detections.tsv`:** `image_id, rank, class, score, x1, y1, x2, y2`.
  - **순서:** 검출마다 한 행이고, 이미지는 매니페스트 순서, 이미지마다 검출은 규칙의 순서다.
  - **값:** 점수와 박스(모델 입력 좌표)는 보드의 C++가 출력하는 대로 float32를 유효숫자 9자리로 쓴다.
- **`images.tsv`:** `image_id, candidates, detections, tied`.
- **두 정밀도:** 보드의 INT8 패스도 같은 두 파일을 쓴다.

## PC의 FP32 패스(§2.2, §2.5)

`MSYS_NO_PATHCONV=1 python tools/eval/pc_run.py --kind m4 --name fp32_<모델> -- tools/m4_fp32.py --model <yolov8n|yolov10n> --manifest results/check/20261116T064917Z_m4_inputs/manifest.tsv --inputs /data/inputs2`

- **모델:** 무엇이든 돌리기 전에 SHA-256이 §1의 값(`models/<모델>.sha256`)이어야 한다.
- **onnxruntime:** CPU 실행 공급자, intra-op 스레드 1개와 inter-op 스레드 1개, 기본 그래프 최적화.
- **이미지마다:**
  - 매니페스트 순서대로 PNG를 디코딩해 RGB로 만든다;
  - 그 바이트가 매니페스트와 같아야 한다. 다르면 패스는 무효(§5)이고 멈춘다;
  - 모델은 float32 NCHW의 RGB / 255를 본다;
  - 모델의 규칙이 검출을 낸다.
- **기록:** 검출 파일은 `D:/m4data/passes/<기록>/`에 간다. 기록 폴더의 `pass.csv`에 개수, 두 파일의 SHA-256, `valid`가 들어간다.
- **범위:** 실행 종류 `m4`의 패스는 5,000장을 모두 돈다. `--limit N`은 처음 N장만 돌고, 점검에만 쓴다.
- **속도:** 이 PC에서 onnxruntime은 어느 모델이든 이미지 한 장에 약 0.8초가 걸린다. 그래서 패스 하나에 한 시간이 넘게 걸린다.

## 평가(§2.3, §6)

`MSYS_NO_PATHCONV=1 python tools/eval/pc_run.py --kind <check|m4> --name eval_<패스> -- tools/m4_coco.py --dets /data/passes/<패스 기록> --manifest <manifest.tsv> --annotations /data/coco/annotations_trainval2017.zip`

- **변환([tools/m4_coco.py](../../tools/m4_coco.py)), 두 정밀도에 한 함수:**
  - 박스마다 이미지로 되돌린다. `left`나 `top`을 빼고, `r`로 나누고, 이미지 안으로 자른 뒤 `[x, y, width, height]`로 쓴다;
  - 값은 float32를 유효숫자 9자리로 쓴다;
  - 클래스 k는 k번째로 작은 카테고리 id가 된다;
  - 이미지마다 검출의 순서를 그대로 둔다.
- **COCOeval:** `iouType` bbox, 기본 매개변수로, 패스의 이미지 전체와 양자화 이미지 20장을 뺀 이미지에서 각각 돈다.
- **`coco_eval.csv`:** 두 이미지 집합의 COCOeval 통계 12개; 이미지당 검출 수; 300개에 이른 이미지 수와 그 가운데 동점이었던 수; 파일들의 SHA-256.
- **주석:** `coco.sha256`과 대조하고, zip에서 바로 읽는다.
- **크기:** 패스 하나에 검출이 약 150만 개다. 메모리는 주로 두 곳에 쓰인다:
  - COCOeval이 이미지, 카테고리, 면적 구간마다 만드는 기록(`evaluate`);
  - 불러온 결과(`loadRes`).
- **메모리 절약:** 도구는 COCOeval 전에 검출 표를 메모리에서 내리고, 결과는 두 이미지 집합에 한 번만 불러온다.
- **측정:** 이미지 안에서 합성 검출(무작위 박스와 클래스, 이미지당 300개; 모델의 것이 아님)로 쟀다:
  - 500장: 최고 0.64 GB;
  - 2,500장: 최고 2.5 GB, 스왑 없는 6 GB에서 종료 코드 0;
  - 장 수에 비례해 늘어나므로 5,000장은 약 4.9 GB로, 6 GB 한도 안에 든다.
- **2 GB 한도로는 모자란다:** 2 GB에 스왑 2 GB를 더한 한도에서 5,000장은 34분 동안 스왑을 쓰다 메모리 초과로 강제 종료됐다.

## 파일

| 파일 | 내용 |
|---|---|
| `tools/m4_rules.py` | Python 규칙 두 개([테스트](../../tests/test_m4_rules.py)) |
| `tools/m4_fp32.py` | PC의 FP32 패스([테스트](../../tests/test_m4_fp32.py)) |
| `tools/m4_coco.py` | 검출 파일, 그 COCO 변환과 평가([테스트](../../tests/test_m4_coco.py)) |
| `tools/eval/Dockerfile`, `requirements.txt`, `constraints.txt`, `image_id.txt`, `build.sh`, `save_image.sh` | 평가 이미지, 그 ID, 확인, 보관본 |
| `tools/eval/fetch_coco.sh`, `coco.sha256` | COCO의 두 파일과 그 SHA-256 |
| `tools/eval/pc_run.py` | §4의 확인을 거친 PC 실행([테스트](../../tests/test_pc_run.py)) |
| `tools/m4_prep.py` | 입력과 데이터 점검([테스트](../../tests/test_m4_prep.py); OpenCV가 필요한 테스트는 평가 이미지에서 돈다) |
| `results/check/<UTC>_m4_eval_image/`, `…_m4_eval_image_archive/` | 이미지의 확인과 보관본의 SHA-256 |
| `results/check/<UTC>_m4_inputs/` | `pc.json`, `manifest.tsv`, `m4_inputs.csv`, `m4_prep.log` |
