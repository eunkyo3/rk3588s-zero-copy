#!/bin/sh
# The board side of tools/m4_board_pass.py: one run of build/m4_accuracy, as root, from the repository.
#   - <record>/run.txt first: the start time (PROTOCOL §1.4; tools/check_prereg_order.py reads time_utc,
#     so a run that stops before the tool writes its header still has one), the command, and this
#     script's PID and the boot's id, by which m4_board_pass.py tells a board reset or a killed run.
#   - A pass then fixes the clocks (tools/set_perf_mode.sh, PROTOCOL §8; prereg/M4.md §2.5), records
#     the environment (tools/collect_env.sh, PROTOCOL §9.1), and gives the tool the fixed clocks.
#   - The tool's output goes to <record>/m4_accuracy.log, and its exit code to <record>/exit_code, which
#     m4_board_pass.py waits for; 2 when the run could not start.
# Usage, as root: tools/m4_board_run.sh <record> pass|decode|wiring ARGS...
set -u
[ "$(id -u)" -eq 0 ] || { echo "m4_board_run.sh: run as root" >&2; exit 2; }
[ $# -ge 2 ] || { echo "usage: tools/m4_board_run.sh <record> pass|decode|wiring ARGS..." >&2; exit 2; }
RECORD=$1
shift
mkdir -p "$RECORD" || exit 2
{
    echo "time_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "command: build/m4_accuracy $*"
    echo "pid: $$"
    echo "boot_id: $(cat /proc/sys/kernel/random/boot_id)"
} > "$RECORD/run.txt"
stop() {
    echo "m4_board_run.sh: $1" >> "$RECORD/m4_accuracy.log"
    echo 2 > "$RECORD/exit_code"
    exit 2
}
if [ "$1" = pass ]; then
    tools/set_perf_mode.sh > "$RECORD/fixed_clocks.txt" 2>> "$RECORD/m4_accuracy.log" || stop "set_perf_mode.sh failed"
    tools/collect_env.sh > "$RECORD/env.txt" 2>> "$RECORD/m4_accuracy.log" || stop "collect_env.sh failed"
    build/m4_accuracy "$@" --record "$RECORD" --fixed-clocks "$RECORD/fixed_clocks.txt" >> "$RECORD/m4_accuracy.log" 2>&1
else
    build/m4_accuracy "$@" --record "$RECORD" >> "$RECORD/m4_accuracy.log" 2>&1
fi
code=$?
echo "$code" > "$RECORD/exit_code"
exit "$code"
