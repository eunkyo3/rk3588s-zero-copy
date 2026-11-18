#!/bin/sh
# M3's board setup (prereg/M3.md §3): from FriendlyElec's Ubuntu 24.04 image for the NanoPi R6C, with the board
# online and this repository cloned at /home/pi/rk3588s-zero-copy, install and configure what M1's runs need,
# build the code, and record it all in results/m3/<UTC>_setup/.
#
# Usage, as root from the repository on the board (detached, so that a lost login does not stop it):
#   scripts/setup_board.sh --stream-key FILE [--known-hosts FILE] [--not-fresh] [--no-services]
#     --stream-key FILE   the private key with which the board logs in to the stream PC (eunkyo@10.88.0.1, as
#                         tools/stream_session.py does). Its public half must be in the PC's authorized_keys.
#                         Make one with: ssh-keygen -t ed25519 -N "" -f bench_stream
#     --known-hosts FILE  the stream PC's host key, as a known_hosts file; without it, ssh-keyscan takes it over
#                         the bench link
#     --not-fresh         set up a board that was set up before, even in part. Without it, the script stops at
#                         once on such a board, before it changes anything.
#     --no-services       skip what needs systemd and the bench link (the network, time sync, the stream PC):
#                         for a test of the rest inside a chroot of the image.
#
# Steps (each logged with its time in setup.log):
#   1. the bench link (eth1, 10.88.0.2/24, NetworkManager), time from the stream PC (systemd-timesyncd), and the
#      stream PC: its key, its host key and the video manifest, checked against its SHA-256. These come first
#      because they need nothing else, and a problem with them shows within a minute;
#   2. packages: apt-get update and upgrade, then the packages the build and the tools need (PACKAGES); then the
#      keys of M3 §4 that come with the image (kernel, librga, MPP) are checked against M1's inventory;
#   3. librknnrt 2.3.2 into /usr/lib, checked against its SHA-256, and the RKNN headers
#      (tools/setup/fetch_rknn_headers.sh);
#   4. ffmpeg-rockchip at its pinned commit (tools/setup/build_ffmpeg_rockchip.sh);
#   5. the model from its Release, checked against its SHA-256;
#   6. the build (CMake, Release), with the commit from git;
#   7. the record: setup.csv, the package list (dpkg.tsv), the inventory (tools/inventory.sh) and its diff from
#      M1's (docs/notes/inventory_m1.txt), and which keys M3 §4 pins differ (inventory_pinned.txt).
# The image's kernel, drivers, MPP and librga are not installed here: they come with the image.
#
# The record is M3's (run kind m3, results/m3/<UTC>_setup) only for a fresh board, with its services, after
# freeze-m3 (its row in docs/notes/env.md), from a checkout whose code equals freeze-m3's. Any other is a
# development record: run kind check, in results/check/<UTC>_m3_setup.
# Exit 0 when every step passed, 1 when one failed (the record says which), 2 for bad arguments or a board that
# is not fresh.
set -eu
export LC_ALL=C DEBIAN_FRONTEND=noninteractive

REPO_DIR=/home/pi/rk3588s-zero-copy  # inventory_m1.txt's include paths name it (M3 §4)
OWNER=pi
SERVER=10.88.0.1
BOARD_ADDR=10.88.0.2/24
PC_USER=eunkyo
RKNN_VERSION=2.3.2
RKNNRT_URL=https://raw.githubusercontent.com/airockchip/rknn-toolkit2/v$RKNN_VERSION/rknpu2/runtime/Linux/librknn_api/aarch64/librknnrt.so
RKNNRT_SHA256=d31fc19c85b85f6091b2bd0f6af9d962d5264a4e410bfb536402ec92bac738e8
MODEL_URL=https://github.com/eunkyo3/rk3588s-zero-copy/releases/download/data-model-yolov8n_i8-04a76611/yolov8n_i8.rknn
MODEL_SHA256=04a76611a785802b08703192455994e07bbfe4531821ae7ed35faf78fe1ec1ee
MANIFEST_SHA256=fcd33374d77ede742ef3a646ac870f9ab6d8e16c605f31b122f607ec78cbbf93  # media/bench/manifest.tsv (A9)
PACKAGES="build-essential cmake git pkg-config libdrm-dev libopencv-dev binutils wget ca-certificates python3"
# apt never asks: keep a changed configuration file, and wait for another apt (apt-daily) to finish.
APT="apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold -o DPkg::Lock::Timeout=900"
STARTED=/var/lib/rk3588s-bench/setup-started  # written before the first change (M3 §3)
# What a set-up board has and a fresh image does not (M3 §3, A15).
SET_UP_MARKERS="$STARTED /opt/ffmpeg-rockchip /opt/rknpu2 /usr/lib/librknnrt.so /home/pi/.ssh/bench_stream
/etc/systemd/timesyncd.conf.d/50-bench.conf"
CODE_PATHS="src tools scripts tests CMakeLists.txt"  # what must equal freeze-m3's (M3 §4)

usage() {
    sed -n '6,17p' "$0" >&2
    exit 2
}
key= known= fresh_ok=no services=yes
while [ $# -gt 0 ]; do
    case "$1" in
        --stream-key) [ $# -ge 2 ] || usage; key=$2; shift 2 ;;
        --known-hosts) [ $# -ge 2 ] || usage; known=$2; shift 2 ;;
        --not-fresh) fresh_ok=yes; shift ;;
        --no-services) services=no; shift ;;
        *) usage ;;
    esac
done
[ "$(id -u)" -eq 0 ] || { echo "setup_board: run as root" >&2; exit 2; }
[ "$(pwd -P)" = "$REPO_DIR" ] || { echo "setup_board: run from $REPO_DIR (M3 §4)" >&2; exit 2; }
if [ "$services" = yes ]; then
    [ -n "$key" ] && [ -r "$key" ] || {
        echo "setup_board: --stream-key FILE is needed: the key for $PC_USER@$SERVER (see the usage)" >&2
        exit 2
    }
fi
[ -z "$known" ] || [ -r "$known" ] || { echo "setup_board: cannot read $known" >&2; exit 2; }

found=
for m in $SET_UP_MARKERS; do
    [ -e "$m" ] && found="$found $m"
done
fresh=yes
if [ -n "$found" ]; then
    fresh=no
    if [ "$fresh_ok" = no ]; then
        echo "setup_board: this board was set up before (found:$found). Reset it first (M3 §3), or pass" \
            "--not-fresh, which M3's after runs do not accept." >&2
        exit 2
    fi
fi

utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }
start=$(utc)
stamp=$(date -u +%Y%m%dT%H%M%SZ)
# Read before anything changes them.
first_dpkg=$(zcat -f /var/log/dpkg.log* 2>/dev/null | sort | head -n 1 | tr ',' ' ')
boot_time=$(date -u -d "@$(awk '/^btime/ {print $2}' /proc/stat)" +%Y-%m-%dT%H:%M:%SZ)
rom_version=$(tr -d ',\n' </etc/rom-version 2>/dev/null || echo unknown)
# git as the repository's owner: run as root on another user's clone, it refuses ("dubious ownership").
git_as_owner() { su "$OWNER" -c "git $*" 2>/dev/null; }
code_commit=$(git_as_owner rev-parse HEAD || echo unknown)
if [ "$code_commit" != unknown ] && [ -n "$(git_as_owner "status --porcelain -- . ':!results'")" ]; then
    code_commit=$code_commit-dirty
fi
freeze_m3=$(python3 -c 'import sys; sys.path.insert(0, "tools"); import stream_session as s; print(s.freeze_m3_commit())' \
    2>/dev/null || echo none)
same_code=no  # whether the checkout's code equals freeze-m3's (CODE_PATHS)
if [ "$freeze_m3" != none ] && git_as_owner "diff --quiet $freeze_m3 HEAD -- $CODE_PATHS"; then
    same_code=yes
fi
amendment=$(sed -n 's/^## \(A[0-9][0-9]*\) .*/\1/p' prereg/AMENDMENTS.md | tail -n 1)
if [ "$fresh" = yes ] && [ "$services" = yes ] && [ "$freeze_m3" != none ] && [ "$same_code" = yes ] \
    && [ "${code_commit%-dirty}" = "$code_commit" ]; then
    kind=m3
    out=results/m3/${stamp}_setup
else
    kind=check
    out=results/check/${stamp}_m3_setup
fi
mkdir -p "$out"
log=$out/setup.log
step_rows=
current=
ntp=unknown
say() { printf '%s %s\n' "$(utc)" "$*" | tee -a "$log"; }
run() {  # run a command, its output into the log, its input from nowhere; fail the step if it fails
    say "+ $*"
    "$@" </dev/null >>"$log" 2>&1 || { say "failed: $*"; return 1; }
}
as_owner() { run su "$OWNER" -c "$*"; }
begin() {
    current=$1
    step_start=$(date +%s)
    say "== step $current"
}
done_step() {
    step_rows="$step_rows
step_${current}_s,$(($(date +%s) - step_start))"
    current=
}
sha_of() { sha256sum "$1" | cut -c1-64; }
fetch_checked() {  # url, file, sha256
    run wget -q -O "$2.part" "$1"
    got=$(sha_of "$2.part")
    if [ "$got" != "$3" ]; then
        say "failed: $1 has SHA-256 $got, not $3"
        rm -f "$2.part"
        exit 1
    fi
    run mv "$2.part" "$2"
}
inventory_value() { sed -n "s|^$1: ||p" docs/notes/inventory_m1.txt; }

finish() {  # the EXIT trap: write the record, whatever ended the script
    status=$1
    trap - EXIT
    result=ok
    [ "$status" -eq 0 ] || result="failed in step ${current:-start}"
    {
        printf '# start_time_utc: %s\n# run_kind: %s\n# freeze_m3_commit: %s\n' "$start" "$kind" "$freeze_m3"
        printf '# code_commit: %s\n# amendment: %s\n' "$code_commit" "${amendment:-none}"
        printf '# measurement: the setup of the board (prereg/M3.md §3), not a run\n'
        printf 'key,value\n'
        printf 'result,%s\nfresh,%s\nservices,%s\n' "$result" "$fresh" "$services"
        printf 'code_equals_freeze_m3,%s\n' "$same_code"
        printf 'set_up_markers_found,%s\n' "$(echo "${found:-none}" | sed 's/^ //')"
        printf 'image_rom_version,%s\nboot_time_utc,%s\n' "$rom_version" "$boot_time"
        printf 'first_dpkg_log_line,%s\n' "${first_dpkg:-none}"
        printf 'ntp_synchronized,%s\n' "$ntp"
        printf '%s\n' "$step_rows" | sed '/^$/d'
        printf 'end_time_utc,%s\n' "$(utc)"
    } >"$out/setup.csv"
    say "setup: $result; record $out"
    exit "$status"
}
# set -e ends the script at the first failing command; the EXIT trap then records the step it was in.
trap 'finish $?' EXIT
trap 'exit 1' INT TERM HUP

steps() {
    say "setup_board: fresh $fresh, services $services, code $code_commit, freeze-m3 $freeze_m3" \
        "(code equal: $same_code), record kind $kind"
    mkdir -p "$(dirname "$STARTED")"
    printf '%s %s\n' "$start" "$out" >>"$STARTED"

    if [ "$services" = yes ]; then
        begin network
        if ! nmcli -t -f NAME connection show | grep -qx bench-link; then
            run nmcli connection add type ethernet ifname eth1 con-name bench-link ipv4.method manual \
                ipv4.addresses "$BOARD_ADDR" ipv4.never-default yes ipv6.method disabled \
                connection.autoconnect-priority 10
        fi
        run nmcli connection up bench-link
        run mkdir -p /etc/systemd/timesyncd.conf.d
        printf '[Time]\nNTP=%s\nFallbackNTP=ntp.ubuntu.com\nPollIntervalMaxSec=64\n' "$SERVER" \
            >/etc/systemd/timesyncd.conf.d/50-bench.conf
        run systemctl restart systemd-timesyncd
        done_step

        begin stream_pc
        ssh_dir=/home/$OWNER/.ssh
        run install -d -m 700 -o "$OWNER" -g "$OWNER" "$ssh_dir"
        run install -m 600 -o "$OWNER" -g "$OWNER" "$key" "$ssh_dir/bench_stream"
        if [ -n "$known" ]; then
            run install -m 644 -o "$OWNER" -g "$OWNER" "$known" "$ssh_dir/known_hosts"
        else
            ssh-keyscan -T 10 "$SERVER" >"$ssh_dir/known_hosts" 2>>"$log"
            chown "$OWNER:$OWNER" "$ssh_dir/known_hosts"
        fi
        pc="ssh -i $ssh_dir/bench_stream -o UserKnownHostsFile=$ssh_dir/known_hosts -o BatchMode=yes"
        pc="$pc -o ConnectTimeout=10 $PC_USER@$SERVER"
        as_owner "mkdir -p /home/$OWNER/media/bench"
        as_owner "$pc cat media/bench/manifest.tsv > /home/$OWNER/media/bench/manifest.tsv"
        got=$(sha_of "/home/$OWNER/media/bench/manifest.tsv")
        if [ "$got" != "$MANIFEST_SHA256" ]; then
            say "failed: the manifest has SHA-256 $got, not $MANIFEST_SHA256"
            exit 1
        fi
        for _ in $(seq 1 60); do  # the session checks the clock against the PC's; give timesyncd its minute
            ntp=$(timedatectl show -p NTPSynchronized --value 2>/dev/null || echo unknown)
            [ "$ntp" = yes ] && break
            sleep 2
        done
        say "NTP synchronized: $ntp"
        done_step
    fi

    begin packages
    run $APT update
    run $APT upgrade
    run $APT install $PACKAGES
    run $APT clean
    # The keys of M3 §4 that come with the image, now rather than after the build.
    for k in "lib /usr/lib/aarch64-linux-gnu/librga.so.2.1.0" "lib /usr/lib/aarch64-linux-gnu/librockchip_mpp.so.0"; do
        want=$(inventory_value "$k" | cut -c1-64)
        got=$(sha_of "${k#lib }")
        if [ "$got" != "$want" ]; then
            say "failed: ${k#lib } has SHA-256 $got, not M1's $want (M3 §4)"
            exit 1
        fi
    done
    if [ "$(uname -r) $(uname -v)" != "$(inventory_value kernel)" ]; then
        say "failed: the kernel is $(uname -r) $(uname -v), not M1's $(inventory_value kernel) (M3 §4)"
        exit 1
    fi
    done_step

    begin rknn
    fetch_checked "$RKNNRT_URL" /usr/lib/librknnrt.so "$RKNNRT_SHA256"
    run env SUDO= RKNN_VERSION="$RKNN_VERSION" tools/setup/fetch_rknn_headers.sh
    done_step

    begin ffmpeg
    as_owner "mkdir -p /home/$OWNER/src"
    run env SUDO= SRC="/home/$OWNER/src/ffmpeg-rockchip" tools/setup/build_ffmpeg_rockchip.sh
    done_step

    begin model
    fetch_checked "$MODEL_URL" models/yolov8n_i8.rknn "$MODEL_SHA256"
    chown "$OWNER:$OWNER" models/yolov8n_i8.rknn
    done_step

    begin build
    as_owner "cd $REPO_DIR && cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCODE_COMMIT=$code_commit"
    as_owner "cd $REPO_DIR && cmake --build build -j4"
    done_step

    begin record
    dpkg-query -W -f='${Package}\t${Version}\t${db:Status-Abbrev}\n' >"$out/dpkg.tsv"
    run sh -c "tools/inventory.sh >$out/inventory.txt"
    diff -u docs/notes/inventory_m1.txt "$out/inventory.txt" >"$out/inventory.diff" || true
    python3 -c '
import sys
sys.path.insert(0, "tools")
import stream_session as s
now, ref = open(sys.argv[1]).read(), open("docs/notes/inventory_m1.txt").read()
for p in s.m3_inventory_problems(now, ref):
    print(p)
' "$out/inventory.txt" >"$out/inventory_pinned.txt"
    if [ -s "$out/inventory_pinned.txt" ]; then
        say "failed: keys that M3 §4 pins differ from inventory_m1.txt: see inventory_pinned.txt"
        exit 1
    fi
    done_step
}

steps
exit 0
