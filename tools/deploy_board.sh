#!/bin/sh
# Copy the working tree (tracked and untracked files, minus .gitignore) to the board and build it
# there. The build records the commit, with "-dirty" when the copied tree differs from it
# (local changes or untracked files).
# Usage: tools/deploy_board.sh [user@host] [dir]   (env: SSH, default "ssh")
set -eu

HOST=${1:-pi@192.168.123.90}
DIR=${2:-rk3588s-zero-copy}
root=$(git rev-parse --show-toplevel)
commit=$(git -C "$root" rev-parse HEAD)
if [ -n "$(git -C "$root" status --porcelain)" ]; then
    commit=$commit-dirty
fi

git -C "$root" ls-files -co --exclude-standard -z |
    tar -C "$root" --null -T - -cf - |
    ${SSH:-ssh} "$HOST" "mkdir -p '$DIR' && cd '$DIR' && tar xf - &&
        cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCODE_COMMIT=$commit >/dev/null &&
        cmake --build build -j4"
echo "built $commit on $HOST:$DIR"
