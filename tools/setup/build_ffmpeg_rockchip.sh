#!/bin/sh
# Build nyanmisaka/ffmpeg-rockchip (h264_rkmpp with DRM PRIME output, rkrga filters)
# on the board, installed side by side with the distro ffmpeg.
# Usage: tools/setup/build_ffmpeg_rockchip.sh   (env: SUDO, PREFIX, SRC, FFMPEG_ROCKCHIP_REF)
# The MPP and librga development packages (pkg-config rockchip_mpp, librga) come
# preinstalled with the FriendlyElec Ubuntu image; this script does not install them.
set -eu

SUDO=${SUDO:-sudo}

REPO=https://github.com/nyanmisaka/ffmpeg-rockchip.git
REF=${FFMPEG_ROCKCHIP_REF:-8b4385e985c0a50bf7d3f96cf3cb6d0b63c53e6c} # branch 7.1
PREFIX=${PREFIX:-/opt/ffmpeg-rockchip}
SRC=${SRC:-$HOME/src/ffmpeg-rockchip}

$SUDO apt-get update -qq
$SUDO apt-get install -y --no-install-recommends build-essential git pkg-config libdrm-dev
pkg-config --exists rockchip_mpp librga libdrm

if [ ! -d "$SRC/.git" ]; then
    mkdir -p "$SRC"
    git -C "$SRC" init -q
    git -C "$SRC" remote add origin "$REPO"
fi
git -C "$SRC" fetch -q --depth 1 origin "$REF"
git -C "$SRC" checkout -q --detach FETCH_HEAD

cd "$SRC"
./configure \
    --prefix="$PREFIX" \
    --enable-shared --disable-static --disable-doc \
    --enable-version3 --enable-libdrm --enable-rkmpp --enable-rkrga \
    --extra-ldflags="-Wl,-rpath,$PREFIX/lib"
make -j"$(nproc)"
$SUDO make install

"$PREFIX/bin/ffmpeg" -hide_banner -version | head -n 1
"$PREFIX/bin/ffmpeg" -hide_banner -h decoder=h264_rkmpp | grep 'hardware devices: rkmpp'
"$PREFIX/bin/ffmpeg" -hide_banner -filters | grep scale_rkrga
echo "ffmpeg-rockchip $(git -C "$SRC" rev-parse HEAD) installed in $PREFIX"
