#!/bin/sh
# Builds M4's evaluation image (prereg/M4.md §8 step 2) from tools/eval/Dockerfile and checks it:
#   - the base is the converter image that converted both models (tools/model/Dockerfile), by its ID;
#   - the built image's `pip freeze` equals tools/eval/constraints.txt: the converter's
#     (tools/model/constraints.txt) plus pycocotools.
# Prints the evaluation image's ID, which every pass and check on the PC records (M4 §4).
# Usage, on the PC (Windows Git Bash): MSYS_NO_PATHCONV=1 tools/eval/build.sh
set -eu

# Docker Desktop takes Windows paths (D:/...), not Git Bash's /d/...; pwd -W gives them there.
ROOT=$(cd "$(dirname "$0")/../.." && { pwd -W 2>/dev/null || pwd; })
BASE=rk3588s-zero-copy/rknn-toolkit2:2.3.2
BASE_ID=sha256:3ea3796529342281fdb543b8911261112e5ab942798c2fea4363edf3eca67f8f  # converted both models
IMAGE=rk3588s-zero-copy/m4-eval:1

fail() {
    echo "build.sh: $1" >&2
    exit 1
}

id=$(docker image inspect --format '{{.Id}}' "$BASE") || fail "no $BASE: build it with tools/model/Dockerfile"
[ "$id" = "$BASE_ID" ] || fail "$BASE is $id, not the converter image $BASE_ID"
docker build --build-arg BASE="$BASE" -t "$IMAGE" "$ROOT/tools/eval" >&2
freeze=$(docker run --rm --memory=2g --memory-swap=4g "$IMAGE" pip freeze) || fail "pip freeze failed"
printf '%s\n' "$freeze" | tr -d '\r' | diff - "$ROOT/tools/eval/constraints.txt" >&2 ||
    fail "the image's pip freeze differs from tools/eval/constraints.txt"
docker image inspect --format '{{.Id}}' "$IMAGE"
