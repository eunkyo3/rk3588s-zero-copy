#!/bin/sh
# A manual test of the package on a clean image without resetting the board: install it inside a chroot of
# the board's base image and use it there, with the board's real devices.
#
# FriendlyElec's root is an overlay; its read-only lower layer (mmcblk2p8) is the image as shipped. This
# script mounts that layer again, puts a tmpfs on top as the chroot's upper layer, and works in it:
# nothing is written to the board's own root, and everything is gone after `stop`.
#
# The chroot gets fresh proc, sysfs and debugfs mounts and a plain bind of /dev, all made private. Do
# not bind /sys or /dev recursively here: with the host's shared propagation, unmounting such a tree
# unmounted the host's own /sys and /dev submounts once, and the board had to be rebooted (M3, A15).
#
# As root, on the board:
#   test_clean_image.sh start
#   test_clean_image.sh run RKZC.deb VIDEO.mp4        VIDEO: a short H.264 file (a few seconds)
#   test_clean_image.sh stop
# `run` needs the network (apt, Rockchip's repository, this repository's Releases). It prints what it
# finds; a line that starts with FAIL is a failed check, and the exit status is then 1.
set -eu
LOWER=/mnt/rkzc-lower
TMP=/mnt/rkzc-tmp
ROOT=/mnt/rkzc-root
DEVICE=/dev/mmcblk2p8

case "${1:-}" in
start)
    mkdir -p $LOWER $TMP $ROOT
    mountpoint -q $LOWER || mount -o ro $DEVICE $LOWER
    mountpoint -q $TMP || mount -t tmpfs -o size=1500m rkzc-tmp $TMP
    mkdir -p $TMP/upper $TMP/work
    mountpoint -q $ROOT || mount -t overlay overlay -o lowerdir=$LOWER,upperdir=$TMP/upper,workdir=$TMP/work $ROOT
    mount --make-private $ROOT
    mount -t proc proc $ROOT/proc
    mount -t sysfs sysfs $ROOT/sys
    mount -t debugfs debugfs $ROOT/sys/kernel/debug
    mount --bind /dev $ROOT/dev
    for m in proc sys sys/kernel/debug dev; do
        mount --make-private $ROOT/$m
    done
    rm -f $ROOT/etc/resolv.conf
    grep '^nameserver' /run/systemd/resolve/resolv.conf >$ROOT/etc/resolv.conf
    # No service is started by a package's scripts in the chroot.
    printf '#!/bin/sh\nexit 101\n' >$ROOT/usr/sbin/policy-rc.d
    chmod 755 $ROOT/usr/sbin/policy-rc.d
    echo "chroot ready: image $(cat $ROOT/etc/rom-version), $(chroot $ROOT sh -c '. /etc/os-release; echo $PRETTY_NAME')"
    ;;
run)
    [ $# -eq 3 ] && [ -f "$2" ] && [ -f "$3" ] || { echo "usage: $0 run RKZC.deb VIDEO.mp4" >&2; exit 2; }
    mountpoint -q $ROOT || { echo "start first" >&2; exit 1; }
    cp "$2" $ROOT/tmp/rkzc.deb
    cp "$3" $ROOT/tmp/video.mp4
    cat >$ROOT/tmp/inside.sh <<'EOF'
#!/bin/sh
# Inside the chroot.
export LC_ALL=C DEBIAN_FRONTEND=noninteractive
failed=0
ok() { echo "ok:   $*"; }
fail() { echo "FAIL: $*"; failed=1; }
expect() {  # description, command...
    what=$1
    shift
    if "$@" >/tmp/out.txt 2>/tmp/err.txt; then ok "$what"; else fail "$what (status $?): $(tail -n 3 /tmp/err.txt | tr '\n' ' ')"; fi
}
# The program's own lines, without what MPP, FFmpeg and librknnrt print.
quiet() { grep -E '^(model|source|detector|rkzc|ModelInfo)' "$@" || true; }

echo "== the image before"
for f in /opt/ffmpeg-rockchip /opt/rknpu2 /usr/lib/librknnrt.so /opt/rkzc /etc/rkzc /var/lib/rkzc; do
    [ -e "$f" ] && fail "the clean image already has $f"
done
[ "$failed" = 0 ] && ok "no FFmpeg build, no librknnrt, no rkzc in the image"
dpkg-query -W -f '${Package} ${Version}\n' librga2 librockchip-mpp1 libdrm2 wget python3 2>&1 | sed 's/^/      /'
find / -xdev \( -path /proc -o -path /sys -o -path /dev -o -path /tmp \) -prune -o -type f -print 2>/dev/null |
    sort >/tmp/files_before.txt

echo "== offline: the install succeeds and says what is missing"
https_proxy=http://127.0.0.1:9 http_proxy=http://127.0.0.1:9 dpkg -i /tmp/rkzc.deb >/tmp/offline.txt 2>&1
status=$?
if [ $status -eq 0 ] && grep -q 'sudo rkzc-fetch-runtime' /tmp/offline.txt && [ ! -e /opt/rkzc/lib/librknnrt.so ]; then
    ok "dpkg -i without the network: status 0, the message names rkzc-fetch-runtime"
else
    fail "dpkg -i without the network: status $status"; cat /tmp/offline.txt
fi
if rkzc-detect --version >/tmp/out.txt 2>&1; then
    fail "rkzc-detect ran without librknnrt"
else
    ok "without librknnrt the program does not start: $(head -n 1 /tmp/out.txt)"
fi
expect "apt-get purge after the offline install" apt-get -y purge rkzc

echo "== apt install"
if apt-get -y install /tmp/rkzc.deb >/tmp/install.txt 2>&1; then
    ok "apt-get install ./rkzc.deb"
    grep -E '^(The following NEW|  [a-z])|newly installed|rkzc' /tmp/install.txt | sed 's/^/      /' | head -n 20
else
    fail "apt-get install"; tail -n 20 /tmp/install.txt
fi
[ "$(sha256sum /opt/rkzc/lib/librknnrt.so 2>/dev/null | cut -c1-64)" = d31fc19c85b85f6091b2bd0f6af9d962d5264a4e410bfb536402ec92bac738e8 ] &&
    ok "librknnrt 2.3.2 was fetched into /opt/rkzc/lib" || fail "librknnrt is not in /opt/rkzc/lib"
missing=$(ldd /usr/bin/rkzc-detect /usr/bin/rkzc-check | grep 'not found' || true)
[ -z "$missing" ] && ok "every library of the programs is found" || fail "libraries not found: $missing"
ldd /usr/bin/rkzc-detect | grep -E 'libav|rknn|rkzc|swresample' | awk '{print "      " $1 " => " $3}'
expect "rkzc-detect --version" rkzc-detect --version
cat /tmp/out.txt | sed 's/^/      /'

echo "== a model, and the checks"
expect "rkzc-fetch-model yolov8n" rkzc-fetch-model yolov8n
ls -l /var/lib/rkzc | sed 's/^/      /'
expect "rkzc-check: the model and a file through the hardware decoder" \
    rkzc-check --model /var/lib/rkzc/yolov8n_i8.rknn --source /tmp/video.mp4
quiet /tmp/out.txt | sed 's/^/      /'

echo "== rkzc-detect with the installed configuration"
expect "the shipped /etc/rkzc/detect.toml is read (its camera does not answer: status 1 from --check is expected)" \
    sh -c 'timeout 60 rkzc-detect --config /etc/rkzc/detect.toml --check; [ $? -eq 1 ]'
quiet /tmp/out.txt | sed 's/^/      /'
sed -e 's|"rtsp://192.168.1.10:554/stream1",|"/tmp/video.mp4",|' -e 's|^to = .*|to = "stdout"|' \
    -e '/^socket_/d' -e '/^max_clients/d' /etc/rkzc/detect.toml >/tmp/file.toml
expect "rkzc-detect plays the file and exits 0" rkzc-detect --config /tmp/file.toml
frames=$(grep -c '"seq"' /tmp/out.txt || true)
[ "$frames" -ge 5 ] && ok "$frames frame lines" || fail "$frames frame lines"
grep '"seq"' /tmp/out.txt | head -n 1 | cut -c1-200 | sed 's/^/      /'

echo "== the Python module"
expect "python3: import rkzc, check_model" python3 -c '
import rkzc
print("rkzc", rkzc.version(), "from", rkzc.__file__)
print(rkzc.check_model("/var/lib/rkzc/yolov8n_i8.rknn"))'
quiet /tmp/out.txt | sed 's/^/      /'
expect "python3: the example plays the file" \
    python3 /usr/share/doc/rkzc/examples/detect_jsonl.py --model /var/lib/rkzc/yolov8n_i8.rknn /tmp/video.mp4
pyframes=$(grep -c '"seq"' /tmp/out.txt || true)
[ "$pyframes" = "$frames" ] && ok "the Python example printed the same $pyframes frames" ||
    fail "the Python example printed $pyframes frames, the program $frames"

echo "== the package's own files"
expect "dpkg --verify rkzc" dpkg --verify rkzc
[ -f /usr/share/doc/rkzc/copyright ] && [ -f /usr/share/doc/rkzc/COPYING.LGPLv3 ] && ok "the copyright file and the LGPL text are installed" ||
    fail "the licence files are missing"

echo "== remove, then purge"
expect "apt-get remove rkzc" apt-get -y remove rkzc
if [ -f /etc/rkzc/detect.toml ] && [ -f /var/lib/rkzc/yolov8n_i8.rknn ] && [ ! -e /usr/bin/rkzc-detect ] && [ ! -e /opt/rkzc ]; then
    ok "after remove: the configuration and the model are kept, the programs and /opt/rkzc are gone"
else
    fail "after remove: $(ls -d /etc/rkzc/detect.toml /var/lib/rkzc/yolov8n_i8.rknn /usr/bin/rkzc-detect /opt/rkzc 2>&1 | tr '\n' ' ')"
fi
expect "apt-get purge rkzc" apt-get -y purge rkzc
find / -xdev \( -path /proc -o -path /sys -o -path /dev -o -path /tmp \) -prune -o -type f -print 2>/dev/null |
    sort >/tmp/files_after.txt
# apt's and dpkg's own records and caches change with any install.
left=$(comm -13 /tmp/files_before.txt /tmp/files_after.txt |
    grep -v -e '^/var/lib/dpkg/' -e '^/var/lib/apt/' -e '^/var/cache/' -e '^/var/log/' -e '__pycache__' -e '^/etc/ld.so.cache' || true)
[ -z "$left" ] && ok "no file is left that was not there before" || fail "left behind: $left"
for f in /opt/rkzc /etc/rkzc /var/lib/rkzc; do
    [ -e "$f" ] && fail "left behind: $f"
done
echo "== $([ "$failed" = 0 ] && echo "all passed" || echo "FAILED")"
exit $failed
EOF
    chroot $ROOT sh /tmp/inside.sh
    ;;
stop)
    for m in dev sys/kernel/debug sys proc; do
        mountpoint -q $ROOT/$m && umount $ROOT/$m
    done
    mountpoint -q $ROOT && umount $ROOT
    mountpoint -q $TMP && umount $TMP
    mountpoint -q $LOWER && umount $LOWER
    rmdir $LOWER $TMP $ROOT 2>/dev/null || true
    echo "mounts left under /mnt: $(grep -c ' /mnt/rkzc' /proc/mounts || true)"
    ;;
*)
    echo "usage: $0 start | run RKZC.deb VIDEO.mp4 | stop" >&2
    exit 2
    ;;
esac
