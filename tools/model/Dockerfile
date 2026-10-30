# The base image and every Python package are pinned, so a rebuild gives the same converter.
# constraints.txt is the `pip freeze` of the image that produced the committed model.
FROM python:3.10-slim-bookworm@sha256:5be5aaecb962a41567bc8041f1e9e3432cb085b037fe057f9a562630b2e4b370

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git wget libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# torch is the CPU-only build to keep the image small. onnx is held at 1.16.1, the lower
# bound of rknn-toolkit2's requirements_cp310-2.3.2.txt: later releases drop onnx.mapping,
# which rknn-toolkit2 2.3.2 still uses.
COPY constraints.txt /tmp/constraints.txt
RUN pip install --no-cache-dir -c /tmp/constraints.txt \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        torch rknn-toolkit2
