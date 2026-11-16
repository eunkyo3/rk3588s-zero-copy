#!/bin/sh
# Keeps M4's evaluation image (prereg/M4.md §8: freeze-m4 fixes the image the runs used): `docker save` of
# the image of tools/eval/image_id.txt, with the converter image's layers it is built on, to
# DATA/images/m4-eval_<first 16 hex of the ID>.tar, outside the repository. A reset of Docker Desktop or a
# prune would otherwise lose it, and a rebuild may give another ID. `docker load -i <file>` restores it.
# Records the archive's size and SHA-256 in results/check/<UTC>_m4_eval_image_archive/archive.txt.
# Usage, on the PC (Windows Git Bash): MSYS_NO_PATHCONV=1 tools/eval/save_image.sh [DATA]  (default D:/m4data)
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && { pwd -W 2>/dev/null || pwd; })
DATA=${1:-D:/m4data}
IMAGE=rk3588s-zero-copy/m4-eval:1

fail() {
    echo "save_image.sh: $1" >&2
    exit 1
}

expected=$(tr -d '\r\n' <"$ROOT/tools/eval/image_id.txt")
id=$(docker image inspect --format '{{.Id}}' "$IMAGE") || fail "no $IMAGE"
[ "$id" = "$expected" ] || fail "$IMAGE is $id, not $expected of tools/eval/image_id.txt"
hex=${id#sha256:}
out="$DATA/images/m4-eval_$(printf '%s' "$hex" | cut -c1-16).tar"
[ ! -e "$out" ] || fail "$out exists"
mkdir -p "$DATA/images"
docker save -o "$out.part" "$IMAGE" || fail "docker save failed"
mv "$out.part" "$out"
sha=$(sha256sum "$out" | cut -c1-64)
record="$ROOT/results/check/$(date -u +%Y%m%dT%H%M%SZ)_m4_eval_image_archive"
mkdir -p "$record"
printf 'time_utc: %s\nimage: %s %s\narchive: %s\nbytes: %s\nsha256: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$IMAGE" "$id" "$out" "$(wc -c <"$out" | tr -d ' ')" "$sha" >"$record/archive.txt"
cat "$record/archive.txt"
