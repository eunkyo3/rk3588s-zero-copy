#!/usr/bin/env python3
"""Summarises tools/copy_sizes.sh: the bytes the libc copies move under prepare, bind and rknn_run.

Input is perf script -F comm,tid,event,ip,sym,dso,trace of the uprobes on __memcpy_generic and
__memmove_generic: a header line per call naming the probe and holding "len=0x...", then the call
stack, leaf first. Per probe, lengths are summed by the innermost of prepare/bind/rknn_run, with
the calls by size, and under rknn_run by immediate caller.

Usage: tools/copy_sizes.py <memcpy_<variant>.txt>
"""
import re
import sys

STACKS = re.compile(r"::(prepare|bind)\(|^(rknn_run)$")
by_fn, by_caller, sizes, calls = {}, {}, {}, {}
event = length = None
frames = []


def flush():
    if length is None:
        return
    calls[event] = calls.get(event, 0) + 1
    syms = []
    for f in frames:
        parts = f.split(None, 1)
        rest = parts[1] if len(parts) > 1 else "[unknown] (?)"
        sym, _, dso = rest.rpartition(" (")
        syms.append((re.sub(r"\+0x[0-9a-f]+$", "", sym), dso.rstrip(")").rsplit("/", 1)[-1]))
    for s, _ in syms[1:]:
        m = STACKS.search(s)
        if m:
            fn = (event, m.group(1) or m.group(2))
            n, b = by_fn.get(fn, (0, 0))
            by_fn[fn] = (n + 1, b + length)
            bucket = (fn, 1 << max(0, length - 1).bit_length())
            sizes[bucket] = sizes.get(bucket, 0) + 1
            if fn[1] == "rknn_run":
                caller = (event, f"{syms[1][0][:70]} ({syms[1][1]})")
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
        e = re.search(r"probe_libc:(\w+)", line)
        event = e.group(1) if e else "?"
        frames = []
    else:
        frames.append(line.strip())
flush()

for ev, n in sorted(calls.items()):
    print(f"{ev} calls in the 4 s window: {n}")
for (ev, fn), (n, b) in sorted(by_fn.items()):
    print(f"{ev} under {fn:<8}: {n:>7} calls, {b:>12} bytes")
print("under rknn_run, by immediate caller:")
for (ev, caller), (n, b) in sorted(by_caller.items(), key=lambda kv: -kv[1][1])[:10]:
    print(f"  {ev} {n:>7} calls, {b:>12} bytes  {caller}")
print("calls by size (up to):")
for (ev, fn), bucket in sorted(sizes):
    print(f"  {ev} {fn:<8} {bucket:>9} B: {sizes[((ev, fn), bucket)]}")
