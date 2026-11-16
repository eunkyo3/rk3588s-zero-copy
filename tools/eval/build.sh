#!/bin/sh
# Builds M4's evaluation image (prereg/M4.md §8 step 2) from tools/eval/Dockerfile, once, checks it and
# records the checks in results/check/<UTC>_m4_eval_image/image.txt:
#   - the base is the converter image that converted both models (tools/model/Dockerfile), by its ID, and
#     the evaluation image's layers start with the base's;
#   - the image is tools/eval/image_id.txt's, the ID every run on the PC must use (tools/eval/pc_run.py);
#   - its `pip freeze` equals tools/eval/constraints.txt (the converter's tools/model/constraints.txt plus
#     pycocotools), `pip check` finds nothing broken, and pycocotools' COCOeval imports.
# An image that exists is checked, not rebuilt: a rebuild may give a new image ID, and freeze-m4 fixes the
# ID the runs used (§8). --rebuild builds it again; its new ID then has to go to image_id.txt, by a commit,
# before pc_run.py runs anything with it.
# The ID is the image's content digest in Docker Desktop's containerd image store (its RepoDigests).
# Usage, on the PC (Windows Git Bash): MSYS_NO_PATHCONV=1 tools/eval/build.sh [--rebuild]
set -eu

# Docker Desktop takes Windows paths (D:/...), not Git Bash's /d/...; pwd -W gives them there.
ROOT=$(cd "$(dirname "$0")/../.." && { pwd -W 2>/dev/null || pwd; })
BASE=rk3588s-zero-copy/rknn-toolkit2:2.3.2
BASE_ID=sha256:3ea3796529342281fdb543b8911261112e5ab942798c2fea4363edf3eca67f8f  # converted both models
IMAGE=rk3588s-zero-copy/m4-eval:1
RUN="docker run --rm --memory=2g --memory-swap=4g --network none"

fail() {
    echo "build.sh: $1" >&2
    exit 1
}

id=$(docker image inspect --format '{{.Id}}' "$BASE") || fail "no $BASE: build it with tools/model/Dockerfile"
[ "$id" = "$BASE_ID" ] || fail "$BASE is $id, not the converter image $BASE_ID"
if [ "${1:-}" = --rebuild ] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    docker build --build-arg BASE="$BASE" -t "$IMAGE" "$ROOT/tools/eval" >&2
fi
image_id=$(docker image inspect --format '{{.Id}}' "$IMAGE")
expected=$(tr -d '\r\n' <"$ROOT/tools/eval/image_id.txt")
[ "$image_id" = "$expected" ] ||
    fail "$IMAGE is $image_id, not $expected of tools/eval/image_id.txt: a new build goes there by a commit"
base_layers=$(docker image inspect --format '{{json .RootFS.Layers}}' "$BASE")
layers=$(docker image inspect --format '{{json .RootFS.Layers}}' "$IMAGE")
case $layers in
    "${base_layers%]}",*) ;;
    *) fail "$IMAGE is not built on $BASE (its layers do not start with the base's)" ;;
esac
freeze=$($RUN "$image_id" pip freeze) || fail "pip freeze failed"
printf '%s\n' "$freeze" | tr -d '\r' | diff - "$ROOT/tools/eval/constraints.txt" >&2 ||
    fail "the image's pip freeze differs from tools/eval/constraints.txt"
pip_check=$($RUN "$image_id" pip check | tr -d '\r') || fail "pip check: $pip_check"
cocoeval=$($RUN "$image_id" python3 -c "from pycocotools.cocoeval import COCOeval; import pycocotools; print('ok')" |
    tr -d '\r') || fail "pycocotools does not import"

record="$ROOT/results/check/$(date -u +%Y%m%dT%H%M%SZ)_m4_eval_image"
mkdir -p "$record"
{
    echo "time_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "base: $BASE $BASE_ID"
    echo "base_from: $(sed -n 's/^FROM //p' "$ROOT/tools/model/Dockerfile")"
    echo "image: $IMAGE $image_id"
    echo "repo_digests: $(docker image inspect --format '{{join .RepoDigests " "}}' "$IMAGE")"
    echo "created: $(docker image inspect --format '{{.Created}}' "$IMAGE")"
    echo "layers_start_with_the_base: yes"
    echo "pip_freeze_equals_constraints: yes ($(printf '%s\n' "$freeze" | wc -l | tr -d ' ') packages)"
    echo "pip_check: $pip_check"
    echo "cocoeval_import: $cocoeval"
    echo "docker: $(docker version --format '{{.Server.Version}}')"
} >"$record/image.txt"
echo "$image_id"
