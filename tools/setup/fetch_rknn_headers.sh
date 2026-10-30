#!/bin/sh
# Fetch the RKNN C API headers matching the board's librknnrt version.
# Usage: tools/setup/fetch_rknn_headers.sh   (env: SUDO, PREFIX, RKNN_VERSION)
# Needs: binutils (strings), wget
set -eu

SUDO=${SUDO:-sudo}

RKNN_VERSION=${RKNN_VERSION:-2.3.2}
PREFIX=${PREFIX:-/opt/rknpu2}
BASE=https://raw.githubusercontent.com/airockchip/rknn-toolkit2/v$RKNN_VERSION/rknpu2/runtime/Linux/librknn_api/include

runtime=$(strings /usr/lib/librknnrt.so | sed -n 's/.*librknnrt version: \([0-9.]*\).*/\1/p' | head -n 1)
if [ "$runtime" != "$RKNN_VERSION" ]; then
    echo "librknnrt is $runtime, headers requested for $RKNN_VERSION" >&2
    exit 1
fi

$SUDO mkdir -p "$PREFIX/include"
for h in rknn_api.h rknn_custom_op.h rknn_matmul_api.h; do
    $SUDO wget -q -O "$PREFIX/include/$h" "$BASE/$h"
done
grep -m 1 'RKNN_API' "$PREFIX/include/rknn_api.h" >/dev/null
(cd "$PREFIX/include" && sha256sum rknn_api.h rknn_custom_op.h rknn_matmul_api.h)
echo "RKNN $RKNN_VERSION headers installed in $PREFIX/include"
