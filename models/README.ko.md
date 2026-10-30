[English](README.md)

# 모델

모델 파일은 커밋하지 않는다. 다음 명령으로 다시 만든다.

```sh
tools/model/convert_yolov8n.sh
# Windows (Git Bash + Docker Desktop):
MSYS_NO_PATHCONV=1 ROOT="$(pwd -W)" tools/model/convert_yolov8n.sh
```

이 스크립트는 rknn-toolkit2(보드 librknnrt와 같은 2.3.2)가 든 Docker 이미지를 만들고, YOLOv8n을 `models/yolov8n_i8.rknn`으로 변환한다. 베이스 이미지는 digest로, Python 패키지는 `tools/model/constraints.txt`로 고정한다.

- **원본:** [rknn_model_zoo](https://github.com/airockchip/rknn_model_zoo) `v2.3.2`(`examples/yolov8`)가 배포하는 RKNN용 YOLOv8n ONNX. 출력이 박스 디코딩 직전에서 끝나므로 후처리는 CPU에서 한다.
- **변환:** 같은 태그의 `examples/yolov8/python/convert.py`, 대상 `rk3588`, INT8. `mean 0, std 255`가 모델에 들어 있어 입력은 uint8 RGB NHWC 640×640이다.
- **양자화 데이터셋:** rknn_model_zoo `datasets/COCO/coco_subset_20.txt`(COCO 이미지 20장)
- **체크섬:** 내려받은 ONNX는 변환 전에 고정된 SHA-256과 대조한다. `models/yolov8n.sha256`은 변환 시 기록한다.

| 파일 | SHA-256 |
|---|---|
| `yolov8n.onnx` | `0c8716701f471067932b797eeb67c8e5db47c693c2557c881d7679ec12e21bc5` |
| `yolov8n_i8.rknn` | `04a76611a785802b08703192455994e07bbfe4531821ae7ed35faf78fe1ec1ee` |

**변환 결과는 비트 단위로 재현되지 않는다.** 고정한 같은 이미지에서 두 번 변환하면 ONNX 입력이 같아도 서로 다른 `.rknn` 파일이 나온다(약 1,200바이트 차이). 벤치마크는 위 SHA-256의 파일을 쓴다. 새로 변환한 파일은 다른 파일이므로 이것을 대체하면 안 된다.

YOLOv8은 Ultralytics가 AGPL-3.0으로 배포한다.
