#!/bin/sh
# The library and its tool must not need OpenCV (docs/design/packaging.md); the library must export only
# its own functions; and it must not call exit(), which would mean the measured die() was compiled in
# instead of the shim's (lib/shim/core/util.h).
# Usage: no_opencv.sh LIBRKZC.so RKZC_CHECK
set -eu
for f in "$1" "$2"; do
    [ -f "$f" ] || { echo "FAIL: no file $f" >&2; exit 1; }
    deps=$(ldd "$f")
    if printf '%s
' "$deps" | grep -i opencv; then
        echo "FAIL: $f needs OpenCV" >&2
        exit 1
    fi
done
# Strong functions only ("T"): the linker's section marks are B and D, and the weak template instances
# of the standard library are W.
symbols=$(nm -D --defined-only -C "$1")
exported=$(printf '%s
' "$symbols" | awk '$2 == "T"' | cut -d' ' -f3- | sed 's/(.*//')
printf '%s
' "$exported" | grep -q '^rkzc::check_model$' || { echo "FAIL: rkzc::check_model is not exported" >&2; exit 1; }
others=$(printf '%s
' "$exported" | grep -v '^rkzc::' || true)
if [ -n "$others" ]; then
    echo "FAIL: $1 exports functions outside rkzc::" >&2
    printf '%s
' "$others" | head >&2
    exit 1
fi
undefined=$(nm -D --undefined-only "$1")
if printf '%s
' "$undefined" | awk '{ print $2 }' | sed 's/@.*//' | grep -E '^(exit|_exit|abort)$'; then
    echo "FAIL: $1 calls exit or abort" >&2
    exit 1
fi
echo "ok: no OpenCV, no exit; exported: $(printf '%s
' "$exported" | tr '
' ';')"
