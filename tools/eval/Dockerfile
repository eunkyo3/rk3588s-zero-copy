# M4's evaluation image (prereg/M4.md §2.2, §8 step 2): the converter image that tools/model/Dockerfile
# builds and that converted both models, unchanged, plus pycocotools pinned by version and SHA-256.
# tools/eval/build.sh builds it and checks the base image's ID and the result's pip freeze
# (tools/eval/constraints.txt). numpy, opencv-python and onnxruntime are the converter image's.
ARG BASE=rk3588s-zero-copy/rknn-toolkit2:2.3.2
FROM ${BASE}
COPY requirements.txt /tmp/eval-requirements.txt
# pycocotools needs only numpy, which the base has; --no-deps keeps the base's packages as they are.
RUN pip install --no-cache-dir --no-deps --require-hashes -r /tmp/eval-requirements.txt
