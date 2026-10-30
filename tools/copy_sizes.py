#!/usr/bin/env python3
"""Summarises tools/copy_sizes.sh: the bytes the libc copies move under prepare, bind and rknn_run.

Input is perf script -F comm,tid,ip,sym,dso,trace of the __memcpy_generic uprobe: a header line per
call holding "len=0x...", then the call stack, leaf first. Lengths are summed by the innermost of
prepare/bind/rknn_run, with the calls by size, and under rknn_run by immediate caller.

Usage: tools/copy_sizes.py <memcpy_<variant>.txt>
"""
import re
import sys

STACKS = re.compile(r"::(prepare|bind)\(|^(rknn_run)$")
by_fn, by_caller, sizes = {}, {}, {}
calls = 0
length = None
frames = []


def flush():
    global calls
    if length is None:
        return
    calls += 1
    syms = []
    for f in frames:
        parts = f.split(None, 1)
        rest = parts[1] if len(parts) > 1 else "[unknown] (?)"
        sym, _, dso = rest.rpartition(" (")
        syms.append((re.sub(r"\+0x[0-9a-f]+$", "", sym), dso.rstrip(")").rsplit("/", 1)[-1]))
    for i, (s, _) in enumerate(syms[1:], 1):
        m = STACKS.search(s)
        if m:
            fn = m.group(1) or m.group(2)
            n, b = by_fn.get(fn, (0, 0))
            by_fn[fn] = (n + 1, b + length)
            bucket = (fn, 1 << max(0, length - 1).bit_length())
            sizes[bucket] = sizes.get(bucket, 0) + 1
            if fn == "rknn_run":
                caller = f"{syms[1][0][:70]} ({syms[1][1]})"
                n, b = by_caller.get(caller, (0, 0))
                by_caller[caller] = (n + 1, b + length)
            break


for line in open(sys.argv[1], errors="replace"):
    if not line.strip():
        continue
    if line[0] not in " \t":
        flush()
        m = re.search(r"len=(0x[0-9a-f]+|\d+)", line)
        length = int(m.group(1), 0) if m else None
        frames = []
    else:
        frames.append(line.strip())
flush()

print(f"memcpy calls in the 4 s window: {calls}")
for fn, (n, b) in sorted(by_fn.items()):
    print(f"under {fn:<8}: {n:>7} calls, {b:>12} bytes")
print("under rknn_run, by immediate caller:")
for caller, (n, b) in sorted(by_caller.items(), key=lambda kv: -kv[1][1])[:8]:
    print(f"  {n:>7} calls, {b:>12} bytes  {caller}")
print("calls by size (up to):")
for fn, bucket in sorted(sizes):
    print(f"  {fn:<8} {bucket:>9} B: {sizes[(fn, bucket)]}")
