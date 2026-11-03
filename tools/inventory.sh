#!/bin/sh
# Print the software, model and build that PROTOCOL §14 freezes at freeze-m1 (AMENDMENTS A10), as
# "key: value" lines: drivers, the ffmpeg build and h264_rkmpp's options, the RKNN headers, the
# compiler and the flags of the build in build/, the model, and the SHA-256 of every shared library
# build/bench_main loads, with its package. The output does not depend on the code's commit.
# With a reference file, compares with it and exits 1 if they differ.
# Usage, from the repository on the board: tools/inventory.sh [reference]
#   (env: SUDO, FFMPEG; the reference of freeze-m1 is docs/notes/inventory_m1.txt)
set -eu
export LC_ALL=C  # the same sort order and messages whatever the caller's locale

SUDO=${SUDO:-sudo}
FFMPEG=${FFMPEG:-/opt/ffmpeg-rockchip/bin/ffmpeg}
BENCH=build/bench_main

# An empty value means a command failed inside $(...), which set -e does not catch.
kv() {
    if [ -z "$2" ]; then
        echo "inventory: no value for $1" >&2
        exit 1
    fi
    printf '%s: %s\n' "$1" "$2"
}
# The value of "NAME = value" in a CMake flags.make file.
cmake_var() { sed -n "s/^$2 = //p" "build/CMakeFiles/$1/flags.make"; }
sha() { sha256sum "$1" | cut -c1-64; }

inventory() {
    kv os "$(. /etc/os-release && echo "$PRETTY_NAME")"
    kv kernel "$(uname -r) $(uname -v)"
    kv rknpu_driver "$($SUDO cat /sys/kernel/debug/rknpu/version | sed 's/^RKNPU driver: //')"
    kv rga_driver "$($SUDO cat /sys/kernel/debug/rkrga/driver_version)"
    # MPP prints a notice on stderr whenever it loads; it is not part of the output.
    kv ffmpeg "$("$FFMPEG" -hide_banner -version 2>/dev/null | head -n 1) ($FFMPEG)"
    kv ffmpeg_built_with "$("$FFMPEG" -hide_banner -version 2>/dev/null | sed -n 's/^built with //p')"
    kv ffmpeg_configuration "$("$FFMPEG" -hide_banner -version 2>/dev/null | sed -n 's/^configuration: //p')"
    # One line per option of h264_rkmpp, with its default.
    "$FFMPEG" -hide_banner -h decoder=h264_rkmpp 2>/dev/null |
        sed -n 's/^  -\([a-z_]*\) .*(default \(.*\))$/h264_rkmpp_default_\1: \2/p'
    kv librknnrt "$(strings /usr/lib/librknnrt.so | sed -n 's/.*librknnrt version: //p' | head -n 1)"
    kv librga "$(strings /usr/lib/aarch64-linux-gnu/librga.so.2 | sed -n 's/.*rga_api version //p' | head -n 1)"
    for h in /opt/rknpu2/include/*.h; do
        kv "rknn_header $(basename "$h")" "$(sha "$h")"
    done
    kv compiler "$(c++ --version | head -n 1)"
    kv linker "$(ld --version | head -n 1)"
    kv cmake "$(cmake --version | head -n 1)"
    kv make "$(make --version | head -n 1)"
    kv cmake_build_type "$(sed -n 's/^CMAKE_BUILD_TYPE:STRING=//p' build/CMakeCache.txt)"
    # The core library's flags: bench_main's own add only the commit and amendment defines.
    kv cxx_flags "$(cmake_var core.dir CXX_FLAGS)"
    kv cxx_includes "$(cmake_var core.dir CXX_INCLUDES)"
    kv link_bench_main "$(sed 's/ *$//' build/CMakeFiles/bench_main.dir/link.txt)"
    kv model "$(sha models/yolov8n_i8.rknn) models/yolov8n_i8.rknn"
    # Every library ldd resolves, and the dynamic loader, by resolved path.
    ldd "$BENCH" | awk '$2 == "=>" {print $3} $1 ~ /^\// {print $1}' |
        while read -r l; do printf '%s %s\n' "$(readlink -f "$l")" "$l"; done | sort |
        while read -r f l; do
            # dpkg knows a file by the path its package installed, the resolved one or ldd's.
            pkg=$(dpkg -S "$f" 2>/dev/null | head -n 1 | cut -d: -f1)
            [ -n "$pkg" ] || pkg=$(dpkg -S "$l" 2>/dev/null | head -n 1 | cut -d: -f1)
            if [ -n "$pkg" ]; then
                pkg="$pkg $(dpkg-query -W -f '${Version}' "$pkg")"
            else
                pkg="no package"
            fi
            kv "lib $f" "$(sha "$f") ($pkg)"
        done
}

if [ $# -eq 0 ]; then
    inventory
    exit 0
fi
[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: $0 [reference]" >&2; exit 2; }
now=$(inventory)
if ! printf '%s\n' "$now" | diff -u "$1" -; then
    echo "inventory: differs from $1" >&2
    exit 1
fi
echo "inventory: same as $1"
