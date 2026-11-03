#!/bin/sh
# Print the board's software, build and model that PROTOCOL §14 freezes at freeze-m1 (AMENDMENTS
# A10), as "key: value" lines: OS, kernel, drivers and boot firmware; the ffmpeg build and the
# defaults of h264_rkmpp's options; librknnrt, librga and the RKNN headers; the compiler, the build
# tools and the flags of the build in build/; the model; and the SHA-256 of every shared library the
# dynamic linker resolves for build/bench_main (ldd), with its package.
# Nothing in the output depends on the code's commit or amendment (CODE_COMMIT, LATEST_AMENDMENT).
# It does depend on CMakeLists.txt and on where the repository is (the include paths).
# Exit 0, or 1 if a value cannot be read. With a reference file, compares with it instead: exit 0 if
# they are the same, 1 if they differ, 3 if a value cannot be read.
# Usage, from the repository on the board: tools/inventory.sh [reference]
#   (env: SUDO, FFMPEG; the reference of freeze-m1 is docs/notes/inventory_m1.txt)
set -eu
export LC_ALL=C  # the same sort order and messages whatever the caller's locale

SUDO=${SUDO:-sudo}
FFMPEG=${FFMPEG:-/opt/ffmpeg-rockchip/bin/ffmpeg}
BENCH=build/bench_main

fail() {
    echo "inventory: $1" >&2
    exit 1
}
# A command that fails inside $(...) leaves its value empty, which set -e does not catch. So every
# value is read into a variable of its own and checked here, or in sha and first_line, whose
# failures are passed on with "|| exit 1": set -e does not apply where the caller tests the result.
kv() {
    [ -n "$2" ] || fail "no value for $1"
    printf '%s: %s\n' "$1" "$2"
}
sha() {
    s=$(sha256sum "$1" | cut -c1-64)
    [ -n "$s" ] || fail "cannot hash $1"
    echo "$s"
}
# The first line a command prints on stdout. MPP prints a notice on stderr whenever it loads.
first_line() {
    l=$("$@" 2>/dev/null | head -n 1)
    [ -n "$l" ] || fail "no output from $*"
    echo "$l"
}
# The value of "NAME = value" in a CMake flags.make file, and of "NAME:TYPE=value" in the cache.
cmake_var() { sed -n "s/^$2 = //p" "build/CMakeFiles/$1/flags.make"; }
cache_var() { sed -n "s/^$1:[A-Z]*=//p" build/CMakeCache.txt; }

inventory() {
    kv os "$(. /etc/os-release && echo "$PRETTY_NAME")"
    kv kernel "$(uname -r) $(uname -v)"
    kv boot_firmware "$(tr ' ' '\n' </proc/cmdline | sed -n 's/^androidboot\.fwver=//p')"
    kv rknpu_driver "$($SUDO cat /sys/kernel/debug/rknpu/version | sed 's/^RKNPU driver: //')"
    kv rga_driver "$($SUDO cat /sys/kernel/debug/rkrga/driver_version)"

    v=$(first_line "$FFMPEG" -hide_banner -version) || exit 1
    kv ffmpeg "$v ($FFMPEG)"
    kv ffmpeg_built_with "$("$FFMPEG" -hide_banner -version 2>/dev/null | sed -n 's/^built with //p')"
    kv ffmpeg_configuration "$("$FFMPEG" -hide_banner -version 2>/dev/null | sed -n 's/^configuration: //p')"
    # One line per option of h264_rkmpp, with its default (not the value the benchmark sets).
    opts=$("$FFMPEG" -hide_banner -h decoder=h264_rkmpp 2>/dev/null |
        sed -n 's/^  -\([a-z_]*\) .*(default \(.*\))$/h264_rkmpp_default_\1: \2/p')
    [ -n "$opts" ] || fail "no options of h264_rkmpp"
    echo "$opts"

    kv librknnrt "$(strings /usr/lib/librknnrt.so | sed -n 's/.*librknnrt version: //p' | head -n 1)"
    kv librga "$(strings /usr/lib/aarch64-linux-gnu/librga.so.2 | sed -n 's/.*rga_api version //p' | head -n 1)"
    for h in /opt/rknpu2/include/*.h; do
        s=$(sha "$h") || exit 1
        kv "rknn_header $(basename "$h")" "$s"
    done

    # The tools the build in build/ used, as CMake found them.
    for tool in CMAKE_CXX_COMPILER CMAKE_LINKER CMAKE_MAKE_PROGRAM CMAKE_COMMAND; do
        path=$(cache_var $tool)
        [ -n "$path" ] || fail "no $tool in build/CMakeCache.txt"
        v=$(first_line "$path" --version) || exit 1
        kv "$tool" "$v ($path)"
    done
    kv cmake_build_type "$(cache_var CMAKE_BUILD_TYPE)"
    for target in core bench_main; do
        kv "${target}_cxx_flags" "$(cmake_var $target.dir CXX_FLAGS)"
        kv "${target}_cxx_includes" "$(cmake_var $target.dir CXX_INCLUDES)"
    done
    # bench_main's defines hold the commit and amendment, so only core's are recorded.
    d=$(cmake_var core.dir CXX_DEFINES)
    kv core_cxx_defines "${d:-none}"
    kv link_bench_main "$(sed 's/ *$//' build/CMakeFiles/bench_main.dir/link.txt)"

    s=$(sha models/yolov8n_i8.rknn) || exit 1
    kv model "$s models/yolov8n_i8.rknn"

    # Every library ldd resolves, and the dynamic loader, by resolved path. Of the libraries opened
    # at run time with dlopen, running B, C and A mapped only libtbbmalloc (AMENDMENTS A10), which
    # libtbb opens; it is added here.
    libs=$(ldd "$BENCH") || fail "ldd $BENCH failed"
    case "$libs" in
        *"not found"*) fail "ldd: a library of $BENCH is not found" ;;
    esac
    paths=$(echo "$libs" | awk '$2 == "=>" {print $3} $1 ~ /^\// {print $1}')
    [ -n "$paths" ] || fail "ldd lists no library for $BENCH"
    paths="$paths /usr/lib/aarch64-linux-gnu/libtbbmalloc.so.2"
    for l in $paths; do
        [ -e "$l" ] || fail "no $l"  # here, not in the pipeline below, whose first stage cannot stop it
    done
    for l in $paths; do
        printf '%s %s\n' "$(readlink -f "$l")" "$l"
    done | sort -u | while read -r f l; do
        # dpkg knows a file by the path its package installed, the resolved one or ldd's. It
        # prints "package:arch: path", or several packages separated by ", ".
        pkg=$(dpkg -S "$f" 2>/dev/null | head -n 1 | sed 's/: .*//; s/, .*//')
        [ -n "$pkg" ] || pkg=$(dpkg -S "$l" 2>/dev/null | head -n 1 | sed 's/: .*//; s/, .*//')
        if [ -n "$pkg" ]; then
            ver=$(dpkg-query -W -f '${Version}' "$pkg")
            [ -n "$ver" ] || fail "no version of $pkg"
            pkg="$pkg $ver"
        else
            pkg="no package"
        fi
        s=$(sha "$f") || exit 1
        kv "lib $f" "$s ($pkg)"
    done || exit 1
}

if [ $# -eq 0 ]; then
    inventory
    exit 0
fi
[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: $0 [reference]" >&2; exit 2; }
now=$(inventory) || exit 3
if ! printf '%s\n' "$now" | diff -u "$1" -; then
    echo "inventory: differs from $1" >&2
    exit 1
fi
echo "inventory: same as $1"
