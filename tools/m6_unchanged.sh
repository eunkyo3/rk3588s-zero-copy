#!/bin/sh
# M6's check that the earlier arms are unchanged (prereg/M6.md §8 steps 2 and 3, "Unchanged arms"), on
# the board, before freeze-m6. It builds freeze-m3's code next to this build and collects:
#   1. the SHA-256 of the object files that step 2 lists as not changed, from both builds;
#   2. for B, Bd, C and Cp, the input tensors, the NPU's outputs and the detections on the first 100
#      frames of video ①, from tools/m6_arms_dump compiled against each build's code, and for Rc from
#      this build.
# tools/m6_checks.py judges what this collects. The tensors and outputs are deleted once their SHA-256
# are in dump.csv; the detections are kept.
#
# The reference build: a git worktree of the tag freeze-m3 in REF_DIR, with this checkout's
# tools/m6_arms_dump.cpp copied in and one target for it appended to its CMakeLists.txt (compiled with
# M6_OLD_ARMS_ONLY). Nothing else of it differs from freeze-m3; reference.txt records its commit and its
# `git status`. It is configured as this build is: the same compiler, build type and options.
#
# Usage, on the board as the repository's owner, from the repository (build/ built at this commit):
#   tools/m6_unchanged.sh [out_dir]
#   (env: SUDO, default "sudo": the dumps run as root, as bench_main does; MEDIA, default
#    /home/pi/media/bench; MODEL, default models/yolov8n_i8.rknn; REF_DIR, default ../m6_freeze_m3;
#    FRAMES, default 100)
# Exit 0 when everything was collected, 3 when a step failed. It judges nothing.
set -eu
export LC_ALL=C

OUT=${1:-results/check/$(date -u +%Y%m%dT%H%M%SZ)_m6_unchanged}
SUDO=${SUDO:-sudo}
MEDIA=${MEDIA:-/home/pi/media/bench}
MODEL=${MODEL:-models/yolov8n_i8.rknn}
REF_DIR=${REF_DIR:-$(dirname "$(pwd)")/m6_freeze_m3}
FRAMES=${FRAMES:-100}
REF_TAG=freeze-m3
VIDEO=$MEDIA/pexels_36330861.mp4  # video ①
VIDEO_SHA256=435a6b05ffb60cf1f0bb4e60c86c8a2f5d5a56a5aa9ca145d845c9a5fffc37ce
# M6 §8 step 2, "Not changed": the sources of libcore whose objects must equal freeze-m3's build's.
UNCHANGED="src/preprocess/cpu_common.cpp src/preprocess/cpu_copy.cpp src/preprocess/cpu_direct.cpp
src/preprocess/cpu_sw.cpp src/preprocess/zero_copy_rga.cpp src/core/npu.cpp src/core/video_source.cpp
src/core/slot_pool.cpp src/core/metrics.cpp src/core/pipeline_post.cpp src/core/yolov8_post.cpp
src/core/yolov8_post_neon.cpp src/core/yolov10_post.cpp"

fail() {
    echo "m6_unchanged.sh: $1" >&2
    exit 3
}

[ -f build/CMakeCache.txt ] && [ -x build/m6_arms_dump ] || fail "no build/ with m6_arms_dump: build this commit first"
[ ! -e "$OUT" ] || fail "$OUT exists"
echo "$VIDEO_SHA256  $VIDEO" | sha256sum -c --status - || fail "$VIDEO: SHA-256 is not $VIDEO_SHA256"
ref_commit=$(git rev-parse "$REF_TAG^{commit}") || fail "no tag $REF_TAG: git fetch --tags"
commit=$(sed -n 's/^CODE_COMMIT:[A-Z]*=//p' build/CMakeCache.txt)
start=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# ---- the reference build ----
if [ ! -e "$REF_DIR/.git" ]; then
    git worktree add -q --detach "$REF_DIR" "$ref_commit" || fail "cannot make the worktree $REF_DIR"
fi
git -C "$REF_DIR" checkout -q --detach "$ref_commit" || fail "cannot check out $REF_TAG in $REF_DIR"
git -C "$REF_DIR" checkout -q -- . || fail "cannot reset $REF_DIR"
[ "$(git -C "$REF_DIR" rev-parse HEAD)" = "$ref_commit" ] || fail "$REF_DIR is not at $REF_TAG"
cp tools/m6_arms_dump.cpp "$REF_DIR/tools/m6_arms_dump.cpp"
cat >>"$REF_DIR/CMakeLists.txt" <<'EOF'

# Appended by tools/m6_unchanged.sh (prereg/M6.md §8 step 3): M6's dump tool against this code.
add_executable(m6_arms_dump tools/m6_arms_dump.cpp)
target_link_libraries(m6_arms_dump PRIVATE core)
target_compile_definitions(m6_arms_dump PRIVATE M6_OLD_ARMS_ONLY
    CODE_COMMIT="${CODE_COMMIT}" LATEST_AMENDMENT="${LATEST_AMENDMENT}")
set_target_properties(m6_arms_dump PROPERTIES BUILD_RPATH ${FFMPEG_PREFIX}/lib)
EOF
build_type=$(sed -n 's/^CMAKE_BUILD_TYPE:[A-Z]*=//p' build/CMakeCache.txt)
cmake -S "$REF_DIR" -B "$REF_DIR/build" -DCMAKE_BUILD_TYPE="$build_type" -DCODE_COMMIT="$ref_commit" \
    >/dev/null || fail "configuring $REF_DIR failed"
cmake --build "$REF_DIR/build" -j4 --target core m6_arms_dump >/dev/null || fail "building $REF_DIR failed"

mkdir -p "$OUT" || fail "cannot create $OUT"
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
    sed -n 's/^CXX_FLAGS = /flags: /p; s/^CXX_DEFINES = /defines: /p; s/^CXX_INCLUDES = /includes: /p' \
        "$1/build/CMakeFiles/core.dir/flags.make" | sed "s|$2|<repo>|g"
}
flags . "$(pwd)" >"$OUT/flags_m6.txt"
flags "$REF_DIR" "$REF_DIR" >"$OUT/flags_freeze_m3.txt"
{
    echo "# start_time_utc: $start"
    echo "# run_kind: check"
    echo "# code_commit: $commit"
    echo "# reference_commit: $ref_commit"
    echo "source,sha256_m6,sha256_freeze_m3"
    for f in $UNCHANGED; do
        a=$(sha256sum "build/CMakeFiles/core.dir/$f.o" | cut -c1-64)
        b=$(sha256sum "$REF_DIR/build/CMakeFiles/core.dir/$f.o" | cut -c1-64)
        [ -n "$a" ] && [ -n "$b" ] || fail "no object for $f in one of the builds"
        echo "$f,$a,$b"
    done
} >"$OUT/objects.csv"

# ---- 2. the arms' data, from both builds ----
abs_model=$(readlink -f "$MODEL")
$SUDO "$REF_DIR/build/m6_arms_dump" "$VIDEO" "$abs_model" "$OUT/freeze_m3" "$FRAMES" \
    >"$OUT/dump_freeze_m3.txt" 2>"$OUT/dump_freeze_m3.log" || fail "the dump of the reference build failed"
$SUDO ./build/m6_arms_dump "$VIDEO" "$abs_model" "$OUT/m6" "$FRAMES" \
    >"$OUT/dump_m6.txt" 2>"$OUT/dump_m6.log" || fail "the dump of this build failed"
# The hashes are in dump.csv; the tensors and outputs (1.2 MB and more per frame and arm) are not kept.
$SUDO find "$OUT" -name '*.u8' -delete -o -name '*.i8' -delete
$SUDO chown -R "$(id -u):$(id -g)" "$OUT"
echo "collected in $OUT: judge with tools/m6_checks.py"
