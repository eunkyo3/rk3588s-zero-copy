#!/bin/sh
# M7's check that the earlier arms are unchanged (prereg/M7.md §8 steps 2 and 3, "Unchanged arms"), on
# the board, before freeze-m7. It builds freeze-m6's code next to this build and collects:
#   1. the SHA-256 of the object files that step 2 lists as not changed, from both builds, and the flags
#      both were compiled with;
#   2. for B, Bd, Rc, C and Cp, the input tensors, the NPU's outputs and the detections on the first 100
#      frames of video ①, from each build's own tools/m6_arms_dump.
# tools/m7_checks.py judges what this collects. The tensors and outputs are deleted once their SHA-256
# are in dump.csv; the detections are kept.
#
# The reference build: a git worktree of the tag freeze-m6 in REF_DIR, as that commit has it. libyuv's
# source, which the repository does not hold, is copied from this checkout's third_party/libyuv (the same
# pinned commit, which both builds' CMake check). reference.txt records its commit and its `git status`.
# It is configured as this build is: the same compiler, build type and options. tools/m6_arms_dump.cpp
# must be freeze-m6's in this checkout too, so that both builds run the same dump.
#
# Usage, on the board as the repository's owner, from the repository (build/ built at this commit):
#   tools/m7_unchanged.sh [out_dir]
#   (env: SUDO, default "sudo": the dumps run as root, as bench_main does; MEDIA, default
#    /home/pi/media/bench; MODEL, default models/yolov8n_i8.rknn; REF_DIR, default ../m7_freeze_m6;
#    FRAMES, default 100)
# Exit 0 when everything was collected, 3 when a step failed. It judges nothing.
set -eu
export LC_ALL=C

OUT=${1:-results/check/$(date -u +%Y%m%dT%H%M%SZ)_m7_unchanged}
SUDO=${SUDO:-sudo}
MEDIA=${MEDIA:-/home/pi/media/bench}
MODEL=${MODEL:-models/yolov8n_i8.rknn}
REF_DIR=${REF_DIR:-$(dirname "$(pwd)")/m7_freeze_m6}
FRAMES=${FRAMES:-100}
REF_TAG=freeze-m6
VIDEO=$MEDIA/pexels_36330861.mp4  # video ①
VIDEO_SHA256=435a6b05ffb60cf1f0bb4e60c86c8a2f5d5a56a5aa9ca145d845c9a5fffc37ce
# M7 §8 step 2, "Not changed": every file under src/preprocess/, and the listed files of src/core/. Each
# with the CMake target whose object directory holds its object.
UNCHANGED="core:src/preprocess/cpu_common.cpp core:src/preprocess/cpu_copy.cpp core:src/preprocess/cpu_direct.cpp
core:src/preprocess/cpu_sw.cpp core:src/preprocess/rga_copy.cpp core:src/preprocess/zero_copy_rga.cpp
core_by:src/preprocess/cpu_yuv.cpp core:src/core/npu.cpp core:src/core/video_source.cpp
core:src/core/slot_pool.cpp core:src/core/metrics.cpp core:src/core/pipeline_post.cpp
core:src/core/yolov8_post.cpp core:src/core/yolov8_post_neon.cpp core:src/core/yolov10_post.cpp"

fail() {
    echo "m7_unchanged.sh: $1" >&2
    exit 3
}

[ -f build/CMakeCache.txt ] && [ -x build/m6_arms_dump ] || fail "no build/ with m6_arms_dump: build this commit first"
[ ! -e "$OUT" ] || fail "$OUT exists"
[ "$(readlink -f "$REF_DIR")" != "$(readlink -f .)" ] || fail "REF_DIR is this repository"
# The build under check is this checkout's commit, with no local change, and up to date.
[ -z "$(git status --porcelain -- src tools tests CMakeLists.txt)" ] || fail "the checkout has local changes"
[ "$(sed -n 's/^CODE_COMMIT:[A-Z]*=//p' build/CMakeCache.txt)" = "$(git rev-parse HEAD)" ] ||
    fail "build/ was not built at the checkout's commit"
cmake --build build -j4 >/dev/null || fail "building this checkout failed"
echo "$VIDEO_SHA256  $VIDEO" | sha256sum -c --status - || fail "$VIDEO: SHA-256 is not $VIDEO_SHA256"
ref_commit=$(git rev-parse "$REF_TAG^{commit}") || fail "no tag $REF_TAG: git fetch --tags"
# Every .cpp and header under src/preprocess/ is on the list above or is a header: a file added there
# since freeze-m6 would be a change of the directory the plan says is not changed.
[ -z "$(git diff --name-only "$ref_commit" HEAD -- src/preprocess)" ] ||
    fail "src/preprocess/ differs from $REF_TAG: $(git diff --name-only "$ref_commit" HEAD -- src/preprocess | tr '\n' ' ')"
git diff --quiet "$ref_commit" HEAD -- tools/m6_arms_dump.cpp || fail "tools/m6_arms_dump.cpp differs from $REF_TAG's"
commit=$(sed -n 's/^CODE_COMMIT:[A-Z]*=//p' build/CMakeCache.txt)
start=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# ---- the reference build ----
if [ ! -e "$REF_DIR/.git" ]; then
    git worktree add -q --detach "$REF_DIR" "$ref_commit" || fail "cannot make the worktree $REF_DIR"
fi
git -C "$REF_DIR" checkout -q --detach "$ref_commit" || fail "cannot check out $REF_TAG in $REF_DIR"
git -C "$REF_DIR" checkout -q -- . || fail "cannot reset $REF_DIR"
[ "$(git -C "$REF_DIR" rev-parse HEAD)" = "$ref_commit" ] || fail "$REF_DIR is not at $REF_TAG"
if [ ! -e "$REF_DIR/third_party/libyuv/CMakeLists.txt" ]; then
    mkdir -p "$REF_DIR/third_party" && cp -a third_party/libyuv "$REF_DIR/third_party/" ||
        fail "cannot copy third_party/libyuv to $REF_DIR"
fi
build_type=$(sed -n 's/^CMAKE_BUILD_TYPE:[A-Z]*=//p' build/CMakeCache.txt)
cmake -S "$REF_DIR" -B "$REF_DIR/build" -DCMAKE_BUILD_TYPE="$build_type" -DCODE_COMMIT="$ref_commit" \
    >/dev/null || fail "configuring $REF_DIR failed"
cmake --build "$REF_DIR/build" -j4 --target core core_by m6_arms_dump >/dev/null || fail "building $REF_DIR failed"

mkdir -p "$OUT" || fail "cannot create $OUT"
# First, so that the record has a start time of its own whatever fails below (PROTOCOL §1.4).
tools/collect_env.sh >"$OUT/env.txt" || fail "collect_env.sh failed"
{
    echo "start_time_utc: $start"
    echo "run_kind: check"
    echo "code_commit: $commit"
    echo "reference_tag: $REF_TAG"
    echo "reference_commit: $ref_commit"
    echo "reference_dir: $REF_DIR"
    echo "reference_status:"
    git -C "$REF_DIR" status --porcelain | sed 's/^/  /'
    echo "reference_diff_stat:"
    git -C "$REF_DIR" diff --stat | sed 's/^/  /'
} >"$OUT/reference.txt"

# ---- 1. the objects, and the flags they were compiled with ----
# The include paths name each build's own directory; with that replaced, the flags must be the same.
flags() {
    for t in core core_by; do
        echo "== $t"
        sed -n 's/^CXX_FLAGS = /flags: /p; s/^CXX_DEFINES = /defines: /p; s/^CXX_INCLUDES = /includes: /p' \
            "$1/build/CMakeFiles/$t.dir/flags.make" | sed "s|$2|<repo>|g"
    done
}
flags . "$(pwd)" >"$OUT/flags_m7.txt"
flags "$REF_DIR" "$REF_DIR" >"$OUT/flags_freeze_m6.txt"
{
    echo "# start_time_utc: $start"
    echo "# run_kind: check"
    echo "# code_commit: $commit"
    echo "# reference_commit: $ref_commit"
    echo "source,sha256_m7,sha256_freeze_m6"
    for entry in $UNCHANGED; do
        t=${entry%%:*}
        f=${entry#*:}
        a=$(sha256sum "build/CMakeFiles/$t.dir/$f.o" | cut -c1-64)
        b=$(sha256sum "$REF_DIR/build/CMakeFiles/$t.dir/$f.o" | cut -c1-64)
        [ -n "$a" ] && [ -n "$b" ] || fail "no object for $f in one of the builds"
        echo "$f,$a,$b"
    done
} >"$OUT/objects.csv"

# ---- 2. the arms' data, from both builds ----
abs_model=$(readlink -f "$MODEL")
$SUDO "$REF_DIR/build/m6_arms_dump" "$VIDEO" "$abs_model" "$OUT/freeze_m6" "$FRAMES" \
    >"$OUT/dump_freeze_m6.txt" 2>"$OUT/dump_freeze_m6.log" || fail "the dump of the reference build failed"
$SUDO ./build/m6_arms_dump "$VIDEO" "$abs_model" "$OUT/m7" "$FRAMES" \
    >"$OUT/dump_m7.txt" 2>"$OUT/dump_m7.log" || fail "the dump of this build failed"
# The hashes are in dump.csv; the tensors and outputs (1.2 MB and more per frame and arm) are not kept.
$SUDO find "$OUT" -name '*.u8' -delete -o -name '*.i8' -delete
$SUDO chown -R "$(id -u):$(id -g)" "$OUT"
echo "collected in $OUT: judge with tools/m7_checks.py"
