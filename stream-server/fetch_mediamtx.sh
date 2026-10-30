#!/bin/sh
# Download the pinned mediamtx release for this platform into stream-server/ and verify it.
# Usage: stream-server/fetch_mediamtx.sh   (Linux amd64/arm64, or Windows Git Bash)
# Needs: curl or wget, sha256sum, tar (Linux) or unzip (Windows)
set -eu

VERSION=v1.21.2
here=$(cd "$(dirname "$0")" && pwd)

case "$(uname -s)-$(uname -m)" in
    Linux-aarch64)
        asset=mediamtx_${VERSION}_linux_arm64.tar.gz
        sha=180a235374a548d9133cdd5e20f5dbd8b01317be1c86e0d7e66df585dc42ed6f ;;
    Linux-x86_64)
        asset=mediamtx_${VERSION}_linux_amd64.tar.gz
        sha=121be6e00397e473a95d83c75f4fcd3819a6af3b0e1a50889cba43ab4fab6a27 ;;
    MINGW64*-x86_64 | MSYS*-x86_64)
        asset=mediamtx_${VERSION}_windows_amd64.zip
        sha=a84224f5a490855684e62ba6fb71e80ea2a5e16aed61883bb58380290e93e467 ;;
    *)
        echo "unsupported platform: $(uname -s) $(uname -m)" >&2
        exit 1 ;;
esac

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
url=https://github.com/bluenviron/mediamtx/releases/download/$VERSION/$asset
if command -v curl >/dev/null; then
    curl -sSfL -o "$tmp/$asset" "$url"
else
    wget -q -O "$tmp/$asset" "$url"
fi
echo "$sha  $tmp/$asset" | sha256sum -c -

# Only the binary: the archive's own mediamtx.yml would replace this repository's.
case "$asset" in
    *.zip)
        if command -v unzip >/dev/null; then
            unzip -o -q "$tmp/$asset" mediamtx.exe -d "$here"
        else  # Windows' own tar reads zip files
            (cd "$tmp" && /c/Windows/System32/tar.exe -xf "$asset" mediamtx.exe)
            mv "$tmp/mediamtx.exe" "$here/"
        fi ;;
    *) tar -xzf "$tmp/$asset" -C "$here" mediamtx ;;
esac
echo "mediamtx $VERSION installed in $here"
