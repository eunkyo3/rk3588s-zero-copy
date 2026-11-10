[English](README.md)

# 모델

모델 파일은 커밋하지 않는다. 벤치마크가 쓰는 파일은 릴리스 [`data-model-yolov8n_i8-04a76611`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/data-model-yolov8n_i8-04a76611)에 보관한다. 다음 명령으로 내려받는다.

```sh
cd models
for f in yolov8n_i8.rknn yolov8n.onnx SHA256SUMS; do
    curl -fLO https://github.com/eunkyo3/rk3588s-zero-copy/releases/download/data-model-yolov8n_i8-04a76611/$f
done
sha256sum -c SHA256SUMS && rm SHA256SUMS
```

변환은 아래 명령으로 다시 할 수 있지만, 다른 `.rknn` 파일이 나온다(아래 참고).

```sh
tools/model/convert_yolov8n.sh
# Windows (Git Bash + Docker Desktop):
MSYS_NO_PATHCONV=1 ROOT="$(pwd -W)" tools/model/convert_yolov8n.sh
```

이 스크립트는 rknn-toolkit2(보드 librknnrt와 같은 2.3.2)가 든 Docker 이미지를 만들고, YOLOv8n을 `models/yolov8n_i8.rknn`으로 변환한다. 베이스 이미지는 digest로, Python 패키지는 `tools/model/constraints.txt`로 고정한다.

- **원본:** [rknn_model_zoo](https://github.com/airockchip/rknn_model_zoo) `v2.3.2`(`examples/yolov8`)가 배포하는 RKNN용 YOLOv8n ONNX. 출력이 박스 디코딩 직전에서 끝나므로 후처리는 CPU에서 한다.
- **변환:** 같은 태그의 `examples/yolov8/python/convert.py`, 대상 `rk3588`, INT8. `mean 0, std 255`가 모델에 들어 있어 입력은 uint8 RGB NHWC 640×640이다.
- **양자화 데이터셋:** rknn_model_zoo 커밋 `bad6c73`(태그 `v2.3.2`)의 `datasets/COCO/coco_subset_20.txt`가 나열하는 COCO 이미지 20장. [coco_subset_20.sha256](coco_subset_20.sha256)에 목록 파일과 이미지마다의 SHA-256이 `datasets/COCO` 기준 경로로 있다(AMENDMENTS A10).
- **체크섬:** 내려받은 ONNX는 변환 전에 고정된 SHA-256과 대조한다. `models/yolov8n.sha256`은 변환 시 기록한다.

| 파일 | SHA-256 |
|---|---|
| `yolov8n.onnx` | `0c8716701f471067932b797eeb67c8e5db47c693c2557c881d7679ec12e21bc5` |
| `yolov8n_i8.rknn` | `04a76611a785802b08703192455994e07bbfe4531821ae7ed35faf78fe1ec1ee` |

**변환 결과는 비트 단위로 재현되지 않는다.** 고정한 같은 이미지에서 두 번 변환하면 ONNX 입력이 같아도 서로 다른 `.rknn` 파일이 나온다(약 1,200바이트 차이). 벤치마크는 위 SHA-256의 파일을 쓴다. 새로 변환한 파일은 다른 파일이므로 이것을 대체하면 안 된다.

YOLOv8은 Ultralytics가 AGPL-3.0으로 배포한다. 두 파일은 여기서 파생됐으므로 같은 라이선스로 배포하며, 라이선스 전문은 릴리스에 있다.

## YOLOv10n (M4)

M4는 YOLOv8n과, NMS가 필요 없는 YOLOv10n을 비교한다. 변환은 같은 고정 이미지에서 한다.

```sh
tools/model/convert_yolov10n.sh
# Windows (Git Bash + Docker Desktop):
MSYS_NO_PATHCONV=1 ROOT="$(pwd -W)" tools/model/convert_yolov10n.sh
```

- **원본과 변환:** rknn_model_zoo `v2.3.2`의 YOLOv10n ONNX와 `examples/yolov10/python/convert.py`, 대상 `rk3588`, INT8, 같은 양자화 이미지 20장.
- **변환 전 확인:** 호스트가 YOLOv8n 때와 같은 x86_64인지, 이미지의 `pip freeze`가 `tools/model/constraints.txt`와 같은지, 양자화 이미지가 [coco_subset_20.sha256](coco_subset_20.sha256)과 맞는지, ONNX 파일의 SHA-256이 `4a925bcb01b261ab97955bfe6c0dd5ad2e638f6db13198d4b6602750870603f6`인지 확인한다.
- **결과:** `models/yolov10n.onnx`, `models/yolov10n_i8.rknn`, `models/yolov10n.sha256`. 변환이 비트 단위로 재현되지 않으므로, M4가 쓸 `.rknn` 파일은 YOLOv8n처럼 아래 SHA-256의 파일이다.
- **M4가 쓰는 변환**은 2026-11-10 이 프로젝트의 Windows PC에서, 컨테이너 메모리를 2 GB로 제한하고 돌렸다. 2–4초마다 잰 사용량은 최대 약 0.5 GB였다. 툴킷은 분류 헤드의 두 층(`model.23.one2one_cv3.{0,1}.1.1.conv.weight`)에 이상치 가중치가 있다고 경고했다(절댓값 평균 0.5–0.6에 대해 약 −11). 양자화 정확도에 영향을 줄 수 있다.

| 파일 | SHA-256 |
|---|---|
| `yolov10n.onnx` | `4a925bcb01b261ab97955bfe6c0dd5ad2e638f6db13198d4b6602750870603f6` |
| `yolov10n_i8.rknn` | `6a6442e1addbd1f08dbdb0772aeea1a992233b4f1db3bd451d20823522246f81` |

YOLOv10은 THU-MIG가 AGPL-3.0으로 배포한다.
