#!/usr/bin/env python3
"""Summarises the zero-copy evidence of PROTOCOL §11.4 collected by tools/zero_copy_evidence.sh.

perf: for each run, the share of samples whose leaf is a copy symbol (memcpy, __arch_copy*) with
prepare, bind or rknn_run on the call stack (C must stay below 0.5%), split by the innermost of
the three; all copy samples (C minus the decode-only baseline is reported); and the share of
cache-maintenance samples (dcache_*_poc, __dma_*sync*), reported for B and C.
strace: dma-buf mmap calls of C (must be 0), and DMA_BUF_IOCTL_SYNC calls per frame of B's
preprocessing threads (must be exactly 2: one START and one END around each frame).

Usage: tools/zero_copy_evidence.py <out_dir>
"""
import re
import sys
from pathlib import Path

COPY = re.compile(r"memcpy|__arch_copy")
STACKS = re.compile(r"::(prepare|bind)\(|^(rknn_run)$")
CACHE = re.compile(r"dcache_\w*_poc|__dma_\w*sync")  # the kernel names them __pi_dcache_inval_poc etc.


def samples(path):
    """Yields each sample's frames as (symbol, dso), leaf first."""
    frames = []
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            if frames:
                yield frames
            frames = []
        elif line[0] in " \t":
            parts = line.split(None, 1)
            rest = parts[1] if len(parts) > 1 else "[unknown] (?)"
            sym, _, dso = rest.rpartition(" (")
            frames.append((re.sub(r"\+0x[0-9a-f]+$", "", sym), dso.rstrip(")")))
    if frames:
        yield frames


def perf_summary(path):
    total = copy_all = cache = 0
    copy_stack = {"prepare": 0, "bind": 0, "rknn_run": 0}  # by the innermost of the three
    detail = {fn: {} for fn in copy_stack}  # fn -> "leaf <- caller (dso)" -> samples
    top_cache, unresolved = {}, {}
    for frames in samples(path):
        total += 1
        leaf, dso = frames[0]
        if leaf == "[unknown]":
            name = dso.rsplit("/", 1)[-1]
            unresolved[name] = unresolved.get(name, 0) + 1
        if COPY.search(leaf):
            copy_all += 1
            for f, _ in frames[1:]:
                m = STACKS.search(f)
                if m:
                    fn = m.group(1) or m.group(2)
                    copy_stack[fn] += 1
                    caller, caller_dso = frames[1] if len(frames) > 1 else ("?", "?")
                    key = f"{leaf} <- {caller} ({caller_dso.rsplit('/', 1)[-1]})"
                    detail[fn][key] = detail[fn].get(key, 0) + 1
                    break
        if CACHE.search(leaf):
            cache += 1
            top_cache[leaf] = top_cache.get(leaf, 0) + 1
    return total, copy_all, copy_stack, detail, cache, top_cache, unresolved


def strace_summary(out, variant):
    threads = dict(l.split(" ", 1) for l in (out / f"strace_{variant}_threads.txt").read_text().splitlines())
    dmabuf_fds = {l.split(" ", 1)[0] for l in (out / f"strace_{variant}_fds.txt").read_text().splitlines()
                  if "dmabuf" in l}
    mmaps = dmabuf_mmaps = 0
    syncs = {}  # tid -> list of "START"/"END"
    for line in (out / f"strace_{variant}.txt").read_text().splitlines():
        m = re.match(r"(\d+)\s+(mmap|ioctl)\((.*)", line)
        if not m:
            continue
        tid, call, args = m.groups()
        if call == "mmap":
            mmaps += 1
            fields = [a.strip() for a in args.split(",")]
            if len(fields) >= 5 and fields[4] in dmabuf_fds:
                dmabuf_mmaps += 1
        elif "DMA_BUF_IOCTL_SYNC" in args:
            # strace does not decode struct dma_buf_sync; B's START and END of a frame are two
            # consecutive calls on the same fd from the same thread.
            syncs.setdefault(tid, []).append(args.split(",")[0].strip())
    return threads, mmaps, dmabuf_mmaps, syncs


def main():
    out = Path(sys.argv[1])
    print("perf (10 s per run)")
    print(f"{'run':<10} {'samples':>8} {'copy':>7} {'copy%':>7} {'under prepare/bind/rknn_run':>28} "
          f"{'prepare':>8} {'bind':>6} {'rknn_run':>9} {'cache%':>7}")
    copies = {}
    for path in sorted(out.glob("perf_*.txt")):
        run = path.stem[5:]
        total, copy_all, copy_stack, detail, cache, top, unresolved = perf_summary(path)
        copies[run] = (copy_all, total)
        under = sum(copy_stack.values())
        print(f"{run:<10} {total:>8} {copy_all:>7} {100 * copy_all / total:>6.2f}% "
              f"{under:>19} ({100 * under / total:5.2f}%) {copy_stack['prepare']:>8} {copy_stack['bind']:>6} "
              f"{copy_stack['rknn_run']:>9} {100 * cache / total:>6.2f}%"
              + (f"  [{', '.join(f'{k} {v}' for k, v in sorted(top.items(), key=lambda kv: -kv[1])[:3])}]"
                 if top else ""))
        print(f"{'':<10} unresolved leaf samples: "
              + ", ".join(f"{k} {100 * v / total:.1f}%" for k, v in sorted(unresolved.items(), key=lambda kv: -kv[1])[:4]))
        for fn, kinds in detail.items():  # the copies under each function: leaf and its caller
            for key, n in sorted(kinds.items(), key=lambda kv: -kv[1])[:4]:
                print(f"{'':<10} {fn:>8}: {n:>4}  {key}")
        if f"C_{n}" in copies and f"none_{n}" in copies:
            (c, ct), (z, zt) = copies[f"C_{n}"], copies[f"none_{n}"]
            print(f"C - none, {n}: copy samples {c} - {z} = {c - z:+d}; "
                  f"copy share {100 * (c / ct - z / zt):+.2f} points")

    print("\nstrace (10 s, 1 channel)")
    for variant in ("C", "B"):
        if not (out / f"strace_{variant}.txt").exists():
            continue
        threads, mmaps, dmabuf_mmaps, syncs = strace_summary(out, variant)
        print(f"{variant}: mmap calls {mmaps}, of dma-bufs {dmabuf_mmaps}")
        for tid, fds in sorted(syncs.items()):
            name = threads.get(tid, "?")
            paired = len(fds) % 2 == 0 and all(fds[i] == fds[i + 1] for i in range(0, len(fds), 2))
            print(f"   DMA_BUF_IOCTL_SYNC from {name} ({tid}): {len(fds)} calls"
                  + (f", {len(fds) // 2} frames, two calls on the same fd per frame" if paired else ", not in pairs"))


if __name__ == "__main__":
    main()
