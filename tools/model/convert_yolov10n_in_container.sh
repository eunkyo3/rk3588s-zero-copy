#!/bin/sh
# Runs inside the rknn-toolkit2 image; writes into /models.
set -eu

ZOO_REF=${ZOO_REF:-v2.3.2}
ONNX_URL=https://ftrg.zbox.filez.com/v2/delivery/data/95f00b0fc900458ba134f8b180b3f7a1/examples/yolov10/yolov10n.onnx
ONNX_SHA256=4a925bcb01b261ab97955bfe6c0dd5ad2e638f6db13198d4b6602750870603f6

# YOLOv8n was converted on x86_64 in an image whose packages equal constraints.txt; hold YOLOv10n to the same.
[ "$(uname -m)" = x86_64 ] || { echo "needs an x86_64 host, got $(uname -m)" >&2; exit 1; }
pip freeze | diff /work/constraints.txt - || { echo "image packages differ from constraints.txt" >&2; exit 1; }

git clone -q --depth 1 --branch "$ZOO_REF" https://github.com/airockchip/rknn_model_zoo.git /tmp/zoo
(cd /tmp/zoo/datasets/COCO && sha256sum -c --quiet /models/coco_subset_20.sha256)
wget -q -O /models/yolov10n.onnx "$ONNX_URL"
echo "$ONNX_SHA256  /models/yolov10n.onnx" | sha256sum -c -

# convert.py resolves its calibration list (datasets/COCO/coco_subset_20.txt) relative to its own directory.
cd /tmp/zoo/examples/yolov10/python
python convert.py /models/yolov10n.onnx rk3588 i8 /models/yolov10n_i8.rknn

cd /models
{
    sha256sum yolov10n.onnx yolov10n_i8.rknn
    echo "rknn-toolkit2 $(python -c "import importlib.metadata as m; print(m.version('rknn-toolkit2'))")"
    echo "rknn_model_zoo $ZOO_REF $(git -C /tmp/zoo rev-parse HEAD)"
    echo "host $(uname -m)"
} > yolov10n.sha256.tmp
mv yolov10n.sha256.tmp yolov10n.sha256
cat yolov10n.sha256
