[English](m4_accuracy.md)

# M4의 정확도: 도구와 절차

M4의 정확도(prereg/M4.md §2)를 어떻게 돌리는지 적는다.
- **규범:** [prereg/M4.md](../../prereg/M4.ko.md). 이 노트는 도구가 그것을 어떻게 따르는지 적는다. 영어판이 우선한다.
- **지금까지 다룬 것:** 평가 이미지, COCO 파일, 입력.
- **아직 없는 것:** 보드 패스, PC 패스, 평가. 각자의 도구와 함께 여기에 더한다.

## 평가 이미지(§8 2단계)

- **[tools/eval/Dockerfile](../../tools/eval/Dockerfile):** 변환 이미지를 그대로 쓰고 `pycocotools` 2.0.11만 더한다.
  - **변환 이미지:** [tools/model/Dockerfile](../../tools/model/Dockerfile)로 만든 것으로, 이미지 ID는 `sha256:3ea3796529342281fdb543b8911261112e5ab942798c2fea4363edf3eca67f8f`다. 두 모델을 변환했다.
  - **pycocotools:** wheel의 SHA-256으로 고정한다([requirements.txt](../../tools/eval/requirements.txt)). 필요한 것은 numpy뿐이고, 그것은 바탕 이미지에 있다.
- **패키지:** Python 3.10, numpy 1.26.4, opencv-python 4.11.0.86, onnxruntime 1.23.2, pycocotools 2.0.11.
- **빌드:** `MSYS_NO_PATHCONV=1 tools/eval/build.sh`가 `rk3588s-zero-copy/m4-eval:1`을 한 번 만들고 그 ID를 출력한다. 다음을 확인한다.
  - 바탕 이미지의 ID, 그리고 이미지의 레이어가 바탕 이미지의 레이어로 시작하는지;
  - 이미지의 `pip freeze`가 [constraints.txt](../../tools/eval/constraints.txt)(변환 이미지의 것에 pycocotools를 더한 것)와 같은지.
- **한 번만:** 이미 있는 이미지는 다시 빌드하지 않고 확인만 한다. 같은 파일로도 다시 빌드하면 이미지 ID가 바뀌는데, 실행마다 쓴 ID를 기록하고 `freeze-m4`가 그것을 고정한다. `--rebuild`를 주면 다시 빌드한다.
- **쓰는 이미지:** `sha256:c676e76f95356ea1aac39a87694ac5a498207890922f2a01c9fdbedd773f250a`, 2026-11-16T05:31:15Z 생성. 그날 앞선 빌드는 `sha256:e23dd324…`를 출력했다. 그때는 `build.sh`가 아직 매번 다시 빌드했다.

## COCO 파일(§2.1)

- **내려받기:** `tools/eval/fetch_coco.sh [DATA]`(기본 `D:/m4data`)가 `val2017.zip`과 `annotations_trainval2017.zip`을 저장소 밖의 `DATA/coco`에 둔다.
  - 그다음 [tools/eval/coco.sha256](../../tools/eval/coco.sha256)과 대조한다.
  - 이미 있는 파일은 다시 내려받지 않고 대조만 한다.
- **출처:** S3 버킷 `images.cocodataset.org`를 `s3.amazonaws.com/images.cocodataset.org`의 HTTPS로 읽는다.
  - 버킷 자체의 이름에는 맞는 인증서가 없다. `https://images.cocodataset.org`는 이름이 맞지 않아 실패한다.
  - 평문 `http://`는 전송 중 무결성을 주지 않는다.
- **2026-11-16의 내려받기:**
  - **크기:** 815,585,330바이트와 252,907,541바이트.
  - **S3 `Last-Modified`:** 2018-07-11과 2018-07-10.
  - **MD5:** `442b8da7639aecaf257c1dceb8ba8c80`과 `f4bbac642086de4f52a3fdda2de5fa2c`. 이 파일들에 대해 흔히 공개된 값과 같다. 주석 zip의 MD5는 S3 `ETag`와도 같다.
- **라이선스:** 이미지는 Flickr 라이선스를 따르므로 저장소 밖에 둔다. 주석은 CC BY 4.0이다.

## PC에서의 실행(§4)

[tools/eval/pc_run.py](../../tools/eval/pc_run.py)가 저장소의 도구를 평가 이미지에서 돌린다.
`MSYS_NO_PATHCONV=1 python tools/eval/pc_run.py --kind <check|m4> --name <이름> -- <도구> <인자>`
- **Git Bash에서:** `MSYS_NO_PATHCONV=1`이 반드시 필요하고, 도구가 이를 확인한다. 없으면 Git Bash가 컨테이너의 `/data/...`를 Windows 경로로 바꾼다.

- **도구를 시작하기 전에 확인하는 것:**
  - **코드:** `HEAD`, 그리고 추적되는 파일이 그것과 다르지 않은지;
  - **이미지:** 평가 이미지의 ID;
  - **CPU 모델;**
  - **호스트 시계:** NTP와의 오프셋. `w32tm /stripchart /computer:time.windows.com /samples:3 /dataonly`로 잰다. 응답한 샘플만 세고, 나머지는 기록한다;
  - **시계의 동기화 상태:** `w32tm /query /status`;
  - **컨테이너 시계:** 호스트 시계와의 오프셋. 컨테이너 하나로 5번 주고받아 왕복이 가장 짧은 것으로 정한다.
- **거부:** NTP 응답이 하나도 없거나, 어느 오프셋이든 1초를 넘으면 도구를 시작하지 않는다.
- **실행:**
  - **기록:** `results/<종류>/<UTC>_<이름>/`. 그 `pc.json`에 위의 내용과 시작 시각이 들어간다. 시작 시각은 호스트 시계에서 잰다.
  - **마운트:** 저장소는 `/repo`에 읽기 전용으로, 기록 폴더는 `/record`에, `D:/m4data`는 `/data`에 붙인다.
  - **제한:** 변환 때처럼 메모리 2 GB(스왑 포함 4 GB). 다른 프로젝트의 컨테이너가 같은 Docker VM을 쓴다.
  - **Python:** 입력이 내려받은 데이터이므로 `python3 -I`로 돌린다.
- **PC:** Intel Core i7-6500U.
- **2026-11-16의 시계:**
  - **06:14 UTC까지:** Windows Time 서비스가 꺼져 있었다. `w32tm /query /status`는 `0x80070426`으로 실패했다. 응답한 stripchart 샘플은 +0.75~+0.76초로 §4의 1초 안이었지만, 시계를 바로잡는 것이 없었다.
  - **06:14 UTC:** 사용자가 서비스를 자동 시작으로 바꾸고 `time.windows.com`을 원본으로 지정했고, 동기화되었다. 직후에도 stripchart는 +0.78초였다.
  - **남아 있을 수 있는 이유:** 단독 Windows PC의 기본값은 1초 미만의 차이를 한 번에 옮기지 않고 천천히 바로잡는다. PC 패스 전에 오프셋을 다시 잰다.

## 입력과 데이터 점검(§2.2, §8 3단계)

`MSYS_NO_PATHCONV=1 python tools/eval/pc_run.py --kind check --name m4_inputs -- tools/m4_prep.py --coco /data/coco --out /data/inputs`

- **[tools/m4_prep.py](../../tools/m4_prep.py)가 하는 일:**
  - 두 파일의 SHA-256을 확인한다;
  - val2017의 JPEG 5,000개와 주석의 이미지 5,000개가 하나씩 짝지어지는지 확인한다. `val2017/<12자리>.jpg` 항목만 `DATA/coco/val2017/`에 쓴다;
  - 이미지마다 §2.2대로 준비하고, PNG를 다시 읽어 확인한다;
  - 양자화 이미지 20장을 [models/coco_subset_20.sha256](../../models/coco_subset_20.sha256)과 대조한다.
- **출력:**
  - **입력:** PNG 5,000개, `DATA/inputs/<이미지 id>.png`.
  - **기록:** 기록 폴더의 `manifest.tsv`와 `m4_inputs.csv`.
- **보드의 점검:** 보드가 PNG 5,000개를 모두 매니페스트의 바이트로 디코딩하는지는 보드 도구와 함께 들어온다.
- **2026-11-16의 실행:** `results/check/20261116T054025Z_m4_inputs`, 38분, `passed,1`.
  - **이미지:** 5,000장 모두 주석의 크기로 디코딩되었다. 903장은 크기를 바꿨고, 4,097장은 긴 변이 이미 640이었다.
  - **PNG:** 모두 다시 읽었을 때 캔버스의 픽셀과 같았다.
  - **양자화 이미지:** 20장 모두 5,000장 안에 있고, 각각 기록된 SHA-256과 같다.
  - **`manifest.tsv`:** SHA-256 `f3ad1dbee36fd6109637374a75c16ff7a6bb150d0e266b2df02d6f8215e1d766`.
  - **그 전의 확인:** NTP 오프셋 +0.760초(샘플 셋 모두 응답), 컨테이너 오프셋 −0.003초. 그때는 Windows Time 서비스가 아직 꺼져 있었다.
- **앞선 시도:** `results/check/20261116T053859Z_m4_inputs`는 곧바로 멈췄다. Git Bash에서 `MSYS_NO_PATHCONV=1` 없이 돌려서 `/data/coco`가 Windows 경로로 바뀌었다. 이제 `pc_run.py`가 그것을 거부한다.

## 파일

| 파일 | 내용 |
|---|---|
| `tools/eval/Dockerfile`, `requirements.txt`, `constraints.txt`, `build.sh` | 평가 이미지와 그 확인 |
| `tools/eval/fetch_coco.sh`, `coco.sha256` | COCO의 두 파일과 그 SHA-256 |
| `tools/eval/pc_run.py` | §4의 확인을 거친 PC 실행([테스트](../../tests/test_pc_run.py)) |
| `tools/m4_prep.py` | 입력과 데이터 점검([테스트](../../tests/test_m4_prep.py); OpenCV가 필요한 테스트는 평가 이미지에서 돈다) |
| `results/check/<UTC>_m4_inputs/` | `pc.json`, `manifest.tsv`, `m4_inputs.csv`, `m4_prep.log` |
