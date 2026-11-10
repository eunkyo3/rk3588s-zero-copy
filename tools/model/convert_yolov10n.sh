#!/bin/sh
# Convert YOLOv10n (rknn_model_zoo ONNX export) to an INT8 RKNN model for RK3588,
# in the same pinned rknn-toolkit2 2.3.2 image as convert_yolov8n.sh.
# Usage: tools/model/convert_yolov10n.sh   (env: ROOT)
#   Windows Git Bash: MSYS_NO_PATHCONV=1 ROOT="$(pwd -W)" tools/model/convert_yolov10n.sh
#   (Docker Desktop cannot mount /d/... paths.)
set -eu

ROOT=${ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}
IMAGE=rk3588s-zero-copy/rknn-toolkit2:2.3.2

docker build -t "$IMAGE" "$ROOT/tools/model"
docker run --rm \
    --mount type=bind,source="$ROOT/models",target=/models \
    --mount type=bind,source="$ROOT/tools/model",target=/work,readonly \
    "$IMAGE" sh /work/convert_yolov10n_in_container.sh
