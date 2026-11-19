#!/bin/sh
# The library and its tool must not need OpenCV (docs/design/packaging.md), and the library must export
# only its own API. Usage: no_opencv.sh LIBRKZC.so RKZC_CHECK
set -eu
for f in "$1" "$2"; do
    if ldd "$f" | grep -i opencv; then
        echo "FAIL: $f needs OpenCV" >&2
        exit 1
    fi
done
# Functions only ("T"): the linker's own section marks are B and D, and the weak template instances
# of the standard library are W.
exported=$(nm -D --defined-only -C "$1" | awk '$2 == "T"' | cut -d' ' -f3- | sed 's/(.*//')
others=$(printf '%s\n' "$exported" | grep -v '^rkzc::' || true)
if [ -n "$others" ]; then
    echo "FAIL: $1 exports symbols outside rkzc::" >&2
    printf '%s\n' "$others" | head >&2
    exit 1
fi
echo "ok: no OpenCV; exported: $(printf '%s\n' "$exported" | tr '\n' ';')"
