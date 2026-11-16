#!/bin/sh
# Builds M4's evaluation image (prereg/M4.md §8 step 2) from tools/eval/Dockerfile, once, and checks it:
#   - the base is the converter image that converted both models (tools/model/Dockerfile), by its ID,
#     and the evaluation image's layers start with the base's;
#   - the image's `pip freeze` equals tools/eval/constraints.txt: the converter's
#     (tools/model/constraints.txt) plus pycocotools.
# An image that exists is checked, not rebuilt: a rebuild gives a new image ID, and every pass and check
# on the PC records the ID it ran with (M4 §4), which freeze-m4 fixes (§8). --rebuild builds it again.
# Prints the evaluation image's ID.
# Usage, on the PC (Windows Git Bash): MSYS_NO_PATHCONV=1 tools/eval/build.sh [--rebuild]
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
if [ "${1:-}" = --rebuild ] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    docker build --build-arg BASE="$BASE" -t "$IMAGE" "$ROOT/tools/eval" >&2
fi
base_layers=$(docker image inspect --format '{{json .RootFS.Layers}}' "$BASE")
layers=$(docker image inspect --format '{{json .RootFS.Layers}}' "$IMAGE")
case $layers in
    "${base_layers%]}",*) ;;
    *) fail "$IMAGE is not built on $BASE (its layers do not start with the base's)" ;;
esac
freeze=$(docker run --rm --memory=2g --memory-swap=4g "$IMAGE" pip freeze) || fail "pip freeze failed"
printf '%s\n' "$freeze" | tr -d '\r' | diff - "$ROOT/tools/eval/constraints.txt" >&2 ||
    fail "the image's pip freeze differs from tools/eval/constraints.txt"
docker image inspect --format '{{.Id}}' "$IMAGE"
