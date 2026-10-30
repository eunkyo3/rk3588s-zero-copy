#!/bin/sh
# Runs inside the rknn-toolkit2 image; writes into /models.
set -eu

ZOO_REF=${ZOO_REF:-v2.3.2}
ONNX_URL=https://ftrg.zbox.filez.com/v2/delivery/data/95f00b0fc900458ba134f8b180b3f7a1/examples/yolov8/yolov8n.onnx
ONNX_SHA256=0c8716701f471067932b797eeb67c8e5db47c693c2557c881d7679ec12e21bc5

git clone -q --depth 1 --branch "$ZOO_REF" https://github.com/airockchip/rknn_model_zoo.git /tmp/zoo
wget -q -O /models/yolov8n.onnx "$ONNX_URL"
echo "$ONNX_SHA256  /models/yolov8n.onnx" | sha256sum -c -

# convert.py resolves its calibration list (datasets/COCO/coco_subset_20.txt) relative to its own directory.
cd /tmp/zoo/examples/yolov8/python
python convert.py /models/yolov8n.onnx rk3588 i8 /models/yolov8n_i8.rknn

cd /models
{
    sha256sum yolov8n.onnx yolov8n_i8.rknn
    echo "rknn-toolkit2 $(python -c "import importlib.metadata as m; print(m.version('rknn-toolkit2'))")"
    echo "rknn_model_zoo $ZOO_REF $(git -C /tmp/zoo rev-parse HEAD)"
} > yolov8n.sha256.tmp
mv yolov8n.sha256.tmp yolov8n.sha256
cat yolov8n.sha256
