#!/bin/sh
# M3's one command (prereg/M3.md §3): build the code, run M1's B and C at H1's point (8 channels, main
# condition, 3 repetitions) with tools/stream_session.py, and print the comparison with M1's values
# (tools/m3_report.py). The stream PC must be set up as stream-server/README.md says; the session starts and
# stops its server.
#
# Usage, as root from the repository on the board (a session takes about 45 minutes; run it detached, with the
# log outside the repository, e.g. setsid nohup ... >/home/pi/reproduce.log 2>&1 &):
#   COOLING=<text> AMBIENT_C=<°C> scripts/reproduce_m1.sh before|after [smoke]
#     before   the m3_before sweep: the board as it is, before its reset (M3 §3)
#     after    the m3_after sweep: the reset board, after scripts/setup_board.sh
#     smoke    a smoke test of the same sweep: 60 s idle, 5 s warm-up, 10 s windows, 30 s gate. Its runs are
#              of kind check, in results/check/<UTC>_m3_<sweep>_smoke, and count for nothing.
# The build: with a git checkout, CMake gets the commit from git (with "-dirty" for local changes), and the code
# must equal freeze-m3's (M3 §4); in a copy made by tools/deploy_board.sh, build/ keeps the commit that tool gave
# it.
# Exit: the session's (0 completed, 1 stopped early, 2 not started), or 2 for bad arguments or a failed build.
set -eu

usage() {
    sed -n '9,14p' "$0" >&2
    exit 2
}
[ $# -ge 1 ] && [ $# -le 2 ] || usage
case "$1" in
    before | after) sweep=m3_$1 ;;
    *) usage ;;
esac
smoke=
if [ $# -eq 2 ]; then
    [ "$2" = smoke ] || usage
    smoke="--idle-s 60 --warmup 5 --duration 10 --gate-s 30"
fi
[ -f scripts/reproduce_m1.sh ] && [ -f tools/stream_session.py ] || {
    echo "reproduce_m1: run from the repository's top folder" >&2
    exit 2
}
[ "$(id -u)" -eq 0 ] || { echo "reproduce_m1: run as root (bench_main reads debugfs)" >&2; exit 2; }
[ -n "${COOLING:-}" ] && [ -n "${AMBIENT_C:-}" ] || {
    echo "reproduce_m1: set COOLING and AMBIENT_C (PROTOCOL §2)" >&2
    exit 2
}
owner=$(stat -c %U .)
as_owner() { su "$owner" -c "$*"; }

if [ -d .git ]; then
    commit=$(as_owner "git rev-parse HEAD")
    if [ -n "$(as_owner "git status --porcelain -- . ':!results'")" ]; then
        commit=$commit-dirty
    fi
    freeze=$(python3 -c 'import sys; sys.path.insert(0, "tools"); import stream_session as s; print(s.freeze_m3_commit())')
    if [ -z "$smoke" ] && ! as_owner "git diff --quiet $freeze HEAD -- src tools scripts tests CMakeLists.txt"; then
        echo "reproduce_m1: the code differs from freeze-m3's ($freeze), or freeze-m3 has no row in" \
            "docs/notes/env.md (M3 §4)" >&2
        exit 2
    fi
    as_owner "cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCODE_COMMIT=$commit >/dev/null" || exit 2
elif [ ! -f build/CMakeCache.txt ]; then
    echo "reproduce_m1: no git checkout and no build/: deploy with tools/deploy_board.sh first" >&2
    exit 2
fi
as_owner "cmake --build build -j4" || { echo "reproduce_m1: the build failed" >&2; exit 2; }

status=0
# $smoke is a list of options, split on purpose
python3 tools/stream_session.py sweep "$sweep" $smoke || status=$?
[ "$status" -ne 2 ] || exit 2  # not started: there is no new session to report
if [ -n "$smoke" ]; then
    session=$(ls -d results/check/*_"$sweep"_smoke 2>/dev/null | tail -n 1)
else
    session=$(ls -d results/m3/*_"$sweep" 2>/dev/null | tail -n 1)
fi
if [ -n "$session" ] && [ -f "$session/runs.tsv" ]; then
    python3 tools/m3_report.py --session "$session" || true
fi
exit "$status"
