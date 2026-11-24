#!/bin/sh
# Fetch libyuv's source at the commit M6 pins (prereg/M6.md §1) into third_party/libyuv, as a git
# checkout: CMakeLists.txt reads the commit from it and refuses any other. Nothing is installed, and
# no system package is used; the build compiles it as part of this repository's build.
# Usage, from the repository: tools/setup/fetch_libyuv.sh [dir]
#   (env: LIBYUV_COMMIT, LIBYUV_URL; both default to M6's)
# Exit 0 when dir holds that commit with no local change, 1 otherwise.
set -eu

DIR=${1:-third_party/libyuv}
COMMIT=${LIBYUV_COMMIT:-0ec9f68d82634f6f3414aaaf780e03f9edf002b3}
URL=${LIBYUV_URL:-https://chromium.googlesource.com/libyuv/libyuv}

fail() {
    echo "fetch_libyuv: $1" >&2
    exit 1
}

if [ ! -d "$DIR/.git" ]; then
    [ ! -e "$DIR" ] || fail "$DIR exists and is not a git checkout"
    mkdir -p "$DIR"
    git -C "$DIR" init -q
    git -C "$DIR" remote add origin "$URL"
fi
if ! git -C "$DIR" cat-file -e "$COMMIT^{commit}" 2>/dev/null; then
    # One commit if the server gives it by its SHA, else the whole history.
    git -C "$DIR" fetch -q --depth 1 origin "$COMMIT" 2>/dev/null || git -C "$DIR" fetch -q origin ||
        fail "cannot fetch from $URL"
fi
git -C "$DIR" -c advice.detachedHead=false checkout -q --detach "$COMMIT" || fail "cannot check out $COMMIT"
[ "$(git -C "$DIR" rev-parse HEAD)" = "$COMMIT" ] || fail "$DIR is not at $COMMIT"
[ -z "$(git -C "$DIR" status --porcelain)" ] || fail "$DIR has local changes"
echo "libyuv $COMMIT in $DIR"
