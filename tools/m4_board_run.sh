#!/bin/sh
# The board side of tools/m4_board_pass.py: one run of build/m4_accuracy, as root, from the repository.
# A pass first fixes the clocks (tools/set_perf_mode.sh, PROTOCOL §8; prereg/M4.md §2.5) and gives the
# tool their record. The tool's output goes to <record>/m4_accuracy.log and its exit code to
# <record>/exit_code, which m4_board_pass.py waits for.
# Usage, as root: tools/m4_board_run.sh <record> pass|decode|wiring ARGS...
set -u
[ "$(id -u)" -eq 0 ] || { echo "m4_board_run.sh: run as root" >&2; exit 2; }
[ $# -ge 2 ] || { echo "usage: tools/m4_board_run.sh <record> pass|decode|wiring ARGS..." >&2; exit 2; }
RECORD=$1
shift
mkdir -p "$RECORD" || exit 2
if [ "$1" = pass ]; then
    if ! tools/set_perf_mode.sh > "$RECORD/fixed_clocks.txt" 2>> "$RECORD/m4_accuracy.log"; then
        echo 2 > "$RECORD/exit_code"
        exit 2
    fi
    build/m4_accuracy "$@" --record "$RECORD" --fixed-clocks "$RECORD/fixed_clocks.txt" >> "$RECORD/m4_accuracy.log" 2>&1
else
    build/m4_accuracy "$@" --record "$RECORD" >> "$RECORD/m4_accuracy.log" 2>&1
fi
code=$?
echo "$code" > "$RECORD/exit_code"
exit "$code"
