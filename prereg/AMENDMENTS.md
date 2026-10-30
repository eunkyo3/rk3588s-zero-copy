[한국어](AMENDMENTS.ko.md)

# Amendments

Append-only record of changes to [HYPOTHESES.md](HYPOTHESES.md) and [PROTOCOL.md](PROTOCOL.md) after `prereg-v1`, and of every value PROTOCOL §14 says is fixed later. Existing entries are never edited or removed. If the English and Korean entries disagree, the English entry governs.

Each entry has this form:

```
## A<number> — YYYY-MM-DD — <short title>
- Change:
- Reason:
- Affected data: (runs or results that must be redone, or "none")
- Release: (after freeze-m1 only: amend-A<number>)
```

## A1 — 2026-10-30 — Spike results: RGA core, context path, release policy, decoder buffers
- Change: fixes these values of PROTOCOL §14.
  - RGA core: `im_opt_t.core = IM_SCHEDULER_RGA3_CORE0 | IM_SCHEDULER_RGA3_CORE1`. The spike observed that the mask is honoured: with RGA3 core 1 alone, the load moved to core 1. The mask excludes RGA2.
  - Context path: late binding (§5.1). One dma-buf registers with all three contexts, so the symmetric context fallback of §5.4 is not used.
  - Release policy: freeing an RKNN memory object in one context leaves the other contexts working, so the release fallback of §5.4 is not used. At shutdown, RKNN memory objects are still freed before their fds are closed.
  - C path fallback: none. C2 works.
  - Decoder buffer cacheability: CPU-cached. The decoder allocates from the `system-dma32` heap (below 4 GiB, cached).
- Reason: results of the M1-0 spike, [docs/notes/spike.md](../docs/notes/spike.md).
- Affected data: none. No measurement has been made.
- Release: none (before `freeze-m1`).
