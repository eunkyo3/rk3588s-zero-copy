#!/bin/sh
# Downloads COCO val2017's images and annotations (prereg/M4.md §2.1) from images.cocodataset.org into
# DATA/coco, a directory of their own, and checks their SHA-256 against tools/eval/coco.sha256.
#   - images.cocodataset.org is an S3 bucket, and its own name has no certificate (https fails with a
#     name mismatch), so the bucket is read over HTTPS at s3.amazonaws.com/images.cocodataset.org, not
#     over plain http.
#   - The images keep their Flickr licences: DATA stays outside the repository, and nothing here commits
#     or uploads them. The annotations are under CC BY 4.0.
#   - The files are data, not code: nothing here runs them. The prepared inputs come from
#     tools/m4_prep.py, in the evaluation image.
#   - A file already in DATA/coco is not downloaded again, only checked.
# Usage, on the PC (Windows Git Bash): tools/eval/fetch_coco.sh [DATA]   (default D:/m4data)
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${1:-D:/m4data}
URL=https://s3.amazonaws.com/images.cocodataset.org

fail() {
    echo "fetch_coco.sh: $1" >&2
    exit 1
}

mkdir -p "$DATA/coco"
cd "$DATA/coco"
for f in zips/val2017.zip annotations/annotations_trainval2017.zip; do
    name=$(basename "$f")
    if [ ! -f "$name" ]; then
        # -f: an HTTP error is an error; the partial file is not kept under the final name.
        curl -fL --retry 3 --retry-delay 10 -o "$name.part" "$URL/$f" || fail "cannot download $URL/$f"
        mv "$name.part" "$name"
    fi
done
sha256sum -c "$ROOT/tools/eval/coco.sha256" || fail "a file differs from tools/eval/coco.sha256 (M4 §2.1)"
