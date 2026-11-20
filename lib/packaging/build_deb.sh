#!/bin/sh
# Builds the package rkzc_<version>_arm64.deb and the source archive of the FFmpeg it bundles
# (docs/design/packaging.md, step 5). It runs on the board, after scripts/setup_board.sh: it needs the
# compiler, ffmpeg-rockchip built at /opt/ffmpeg-rockchip from the source tree in ~/src/ffmpeg-rockchip,
# the RKNN headers, and dpkg-dev. It changes nothing outside the output directory and needs no root.
#
# Usage: lib/packaging/build_deb.sh [OUT_DIR]        (from the repository's top; default OUT_DIR: dist)
#   env: FFMPEG_PREFIX (/opt/ffmpeg-rockchip), FFMPEG_SRC (~/src/ffmpeg-rockchip), RKZC_COMMIT (git's HEAD)
# Output: OUT_DIR/rkzc_<version>_arm64.deb, OUT_DIR/ffmpeg-rockchip-<commit>.tar.gz, OUT_DIR/SHA256SUMS
set -eu
export LC_ALL=C
umask 022  # the files written here go into the package with these permissions

TOP=$(cd "$(dirname "$0")/../.." && pwd)
PKG=$TOP/lib/packaging
OUT=$(mkdir -p "${1:-dist}" && cd "${1:-dist}" && pwd)
FFMPEG_PREFIX=${FFMPEG_PREFIX:-/opt/ffmpeg-rockchip}
FFMPEG_SRC=${FFMPEG_SRC:-$HOME/src/ffmpeg-rockchip}
RUNTIME_LIBDIR=/opt/rkzc/lib
REVISION=1
ARCH=$(dpkg --print-architecture)
[ "$ARCH" = arm64 ] || { echo "build_deb: this builds on the board (arm64), not on $ARCH" >&2; exit 1; }

VERSION=$(sed -n 's/^project(rkzc VERSION \([0-9.]*\) .*/\1/p' "$TOP/lib/CMakeLists.txt")
[ -n "$VERSION" ] || { echo "build_deb: no version in lib/CMakeLists.txt" >&2; exit 1; }
# The source tree may belong to another user (it was built with sudo): git then needs to be told.
ffgit() { git -c safe.directory="$FFMPEG_SRC" -C "$FFMPEG_SRC" "$@"; }
FFMPEG_COMMIT=$(ffgit rev-parse HEAD)
RKZC_COMMIT=${RKZC_COMMIT:-$(git -C "$TOP" rev-parse HEAD 2>/dev/null || echo unknown)}
# Tracked files only: the output directory may lie in the checkout.
if [ "$RKZC_COMMIT" != unknown ] && [ -n "$(git -C "$TOP" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
    RKZC_COMMIT=$RKZC_COMMIT-dirty
fi
# The installed FFmpeg must be the build of that source: its version string carries the commit.
"$FFMPEG_PREFIX/bin/ffmpeg" -hide_banner -version | head -n 1 | grep -q "$(echo "$FFMPEG_COMMIT" | cut -c1-8)" || {
    echo "build_deb: $FFMPEG_PREFIX is not a build of $FFMPEG_SRC at $FFMPEG_COMMIT" >&2
    exit 1
}
# The archive made below is that commit: the tree that was built must have no change of its own.
ffgit diff --quiet HEAD || { echo "build_deb: $FFMPEG_SRC has local changes; the source archive would not be what was built" >&2; exit 1; }
# The configure line as FFmpeg recorded it, with the build's directories as they were.
FFMPEG_CONFIGURE=$("$FFMPEG_PREFIX/bin/ffmpeg" -hide_banner -version | sed -n 's/^configuration: //p')
# The copyright file says LGPL v3: that holds for this configuration only.
case "$FFMPEG_CONFIGURE" in
*--enable-gpl* | *--enable-nonfree*) echo "build_deb: this FFmpeg is not an LGPL build: $FFMPEG_CONFIGURE" >&2; exit 1 ;;
*--enable-version3*) ;;
*) echo "build_deb: this FFmpeg was not configured with --enable-version3: $FFMPEG_CONFIGURE" >&2; exit 1 ;;
esac
# The same commit gives the same timestamps in the package.
if [ "$RKZC_COMMIT" != unknown ] && [ -z "${SOURCE_DATE_EPOCH:-}" ]; then
    SOURCE_DATE_EPOCH=$(git -C "$TOP" log -1 --format=%ct 2>/dev/null || true)
fi
[ -z "${SOURCE_DATE_EPOCH:-}" ] || export SOURCE_DATE_EPOCH

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
stage=$work/stage
echo "build_deb: rkzc $VERSION-$REVISION at $RKZC_COMMIT, FFmpeg $FFMPEG_COMMIT"

# 1. The library and the programs, for /usr, finding FFmpeg and librknnrt in /opt/rkzc/lib.
cmake -S "$TOP/lib" -B "$work/build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr \
    -DRKZC_FFMPEG_DIR="$FFMPEG_PREFIX" -DRKZC_RUNTIME_LIBDIR=$RUNTIME_LIBDIR >"$work/cmake.log"
cmake --build "$work/build" -j"$(nproc)" --target rkzc rkzc-detect rkzc-check >"$work/build.log" 2>&1 || {
    tail -n 30 "$work/build.log" >&2
    exit 1
}
DESTDIR=$stage cmake --install "$work/build" >/dev/null
libdir=$(dirname "$(find "$stage/usr/lib" -name 'librkzc.so.0')")

# 2. FFmpeg's libraries that librkzc needs, and theirs: one file each, under its soname, finding each
#    other by $ORIGIN.
mkdir -p "$stage$RUNTIME_LIBDIR"
needed() { readelf -d "$1" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p'; }
todo=$(needed "$libdir/librkzc.so.0")
done_list=
while [ -n "$todo" ]; do
    next=
    for so in $todo; do
        case " $done_list " in *" $so "*) continue ;; esac
        done_list="$done_list $so"
        [ -e "$FFMPEG_PREFIX/lib/$so" ] || continue  # not FFmpeg's: a system library, or librknnrt
        cp -L "$FFMPEG_PREFIX/lib/$so" "$stage$RUNTIME_LIBDIR/$so"
        next="$next $(needed "$FFMPEG_PREFIX/lib/$so")"
    done
    todo=$next
done
bundled=$(cd "$stage$RUNTIME_LIBDIR" && ls)
[ -n "$bundled" ] || { echo "build_deb: no FFmpeg library was found to bundle" >&2; exit 1; }
chmod 0644 "$stage$RUNTIME_LIBDIR"/*
# The run path is rewritten inside the libraries' string table. A linker may store a shorter string
# as the tail of a longer one, so a symbol's name could share those bytes: the symbols and versions
# must read the same before and after.
symbols() { ( cd "$stage$RUNTIME_LIBDIR" && for so in $bundled; do readelf -W --dyn-syms -V "$so"; readelf -d "$so" | grep -E 'NEEDED|SONAME'; done ); }
symbols >"$work/symbols.before"
( cd "$stage$RUNTIME_LIBDIR" && python3 "$PKG/set_runpath.py" '$ORIGIN' $bundled ) >"$work/runpath.log"
symbols >"$work/symbols.after"
cmp -s "$work/symbols.before" "$work/symbols.after" || {
    echo "build_deb: rewriting the run path changed a library's symbols; it shares its string with one" >&2
    diff "$work/symbols.before" "$work/symbols.after" | head -n 10 >&2
    exit 1
}
# What the copyright file names, and nothing of Rockchip's.
[ "$(echo $bundled)" = "libavcodec.so.61 libavformat.so.61 libavutil.so.59 libswresample.so.5" ] || {
    echo "build_deb: the bundled libraries are not the four the copyright file names: $(echo $bundled)" >&2
    exit 1
}
strip --strip-unneeded "$stage$RUNTIME_LIBDIR"/* "$libdir"/librkzc.so.0.* "$stage/usr/bin/rkzc-detect" \
    "$stage/usr/bin/rkzc-check"

# 3. The Python module, the configuration, the commands, the model's directory.
install -D -m 0644 "$TOP/lib/python/rkzc/__init__.py" "$stage/usr/lib/python3/dist-packages/rkzc/__init__.py"
install -D -m 0644 "$TOP/lib/dist/detect.toml" "$stage/etc/rkzc/detect.toml"
install -D -m 0755 "$PKG/rkzc-fetch-runtime" "$stage/usr/sbin/rkzc-fetch-runtime"
install -D -m 0755 "$PKG/rkzc-fetch-model" "$stage/usr/sbin/rkzc-fetch-model"
install -d -m 0755 "$stage/var/lib/rkzc"
install -D -m 0644 "$TOP/lib/python/examples/detect_jsonl.py" "$stage/usr/share/doc/rkzc/examples/detect_jsonl.py"

# 4. The licences and where the source is.
doc=$stage/usr/share/doc/rkzc
archive=ffmpeg-rockchip-$(echo "$FFMPEG_COMMIT" | cut -c1-8).tar.gz
sed -e "s|@RKZC_COMMIT@|$RKZC_COMMIT|" -e "s|@FFMPEG_COMMIT@|$FFMPEG_COMMIT|" -e "s|@FFMPEG_ARCHIVE@|$archive|" \
    -e "s|@FFMPEG_CONFIGURE@|$FFMPEG_CONFIGURE|" "$PKG/debian/copyright" >"$doc/copyright"
install -m 0644 "$FFMPEG_SRC/COPYING.LGPLv3" "$FFMPEG_SRC/COPYING.GPLv3" "$doc/"
install -m 0644 "$TOP/tools/setup/build_ffmpeg_rockchip.sh" "$doc/build_ffmpeg_rockchip.sh"
if grep -q '@[A-Z_]*@' "$doc/copyright"; then
    echo "build_deb: a placeholder is left in the copyright file" >&2
    exit 1
fi
if [ -n "$(find "$stage" -name 'librknn*' -o -name 'rknn_*.h' -o -name '*.rknn')" ]; then
    echo "build_deb: a file of Rockchip's runtime or a model is in the package" >&2
    exit 1
fi

# 5. The control files.
mkdir -p "$stage/DEBIAN" "$work/shlibs/debian"
: >"$work/shlibs/debian/control"
# The packages the binaries need, from their sonames. librknnrt belongs to no package (it is fetched),
# and the bundled libraries are found in the stage.
depends=$(cd "$work/shlibs" && dpkg-shlibdeps -O --ignore-missing-info -l"$stage$RUNTIME_LIBDIR" -l"$libdir" \
    -xrkzc "$stage/usr/bin/rkzc-detect" "$stage/usr/bin/rkzc-check" "$libdir"/librkzc.so.0.* \
    "$stage$RUNTIME_LIBDIR"/* 2>"$work/shlibdeps.log" | sed -n 's/^shlibs:Depends=//p')
[ -n "$depends" ] || { cat "$work/shlibdeps.log" >&2; echo "build_deb: dpkg-shlibdeps found nothing" >&2; exit 1; }
for want in librga2 librockchip-mpp1 libdrm2; do
    case "$depends" in *"$want"*) ;; *) echo "build_deb: $want is not among the dependencies: $depends" >&2; exit 1 ;; esac
done
size=$(du -sk --exclude=DEBIAN "$stage" | cut -f1)
sed -e "s|@VERSION@|$VERSION-$REVISION|" -e "s|@INSTALLED_SIZE@|$size|" -e "s|@SHLIBS_DEPENDS@|$depends|" \
    "$PKG/debian/control.in" >"$stage/DEBIAN/control"
for f in preinst postinst prerm postrm; do
    install -m 0755 "$PKG/debian/$f" "$stage/DEBIAN/$f"
done
install -m 0644 "$PKG/debian/triggers" "$PKG/debian/conffiles" "$stage/DEBIAN/"
( cd "$stage" && find . -type f ! -path './DEBIAN/*' -printf '%P\0' | sort -z | xargs -0 md5sum ) >"$stage/DEBIAN/md5sums"
# Directories 0755 and no group or other write bit, whatever the build user's umask was.
find "$stage" -type d -exec chmod 0755 {} +
chmod -R go-w "$stage"

# 6. The package, and the source of the FFmpeg in it.
deb=$OUT/rkzc_${VERSION}-${REVISION}_arm64.deb
dpkg-deb --root-owner-group -Zxz --build "$stage" "$deb" >/dev/null
ffgit archive --format=tar.gz --prefix="${archive%.tar.gz}/" -o "$OUT/$archive" "$FFMPEG_COMMIT"
( cd "$OUT" && sha256sum "$(basename "$deb")" "$archive" >SHA256SUMS )

echo "build_deb: bundled from FFmpeg: $(echo $bundled)"
echo "build_deb: Depends: $depends"
dpkg-deb --info "$deb" | sed -n '/^ Package/,$p' | head -n 12
ls -l "$OUT"
