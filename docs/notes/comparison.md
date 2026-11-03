[한국어](comparison.ko.md)

# Comparison runs (M1-6)

The comparison runs of PROTOCOL §12, after `freeze-m1` ([env.md](env.md#registration)). `tools/stream_session.py sweep` runs them, one session at a time, on the board.

## Method, fixed before the runs

This section fixes how the tool carries out §12 where PROTOCOL leaves a choice. It changes nothing that PROTOCOL or AMENDMENTS fix. H3 (§10) gets its own section before its first run.

### Sweeps and their order

| Sweep | Runs | Run kind | Channel order (§9.1) |
|---|---|---|---|
| `main` | B and C × main set {1, 2, 4, 8, 12, 16, 32} (A9) × 3, main condition | `comparison` | 8, 1, 16, 4, 32, 2, 12 |
| `aux` | B and C × auxiliary set {1, 2, 3, 4, 5} (§6.3, A7) × 3, auxiliary condition | `comparison` | 1, 4, 3, 2, 5 |
| `reference` | A × A set {1, 2} (§6.3) × 3, main condition | `reference` | 2, 1 |

- **Channel order:** the sorted counts, shuffled by a fresh `random.Random(20261030)` per sweep (§9.1). The A set is not a sweep in §9.1's sense, but it gets the same procedure.
- **Blocks:** one block per count, in that order. A block has 3 repetitions. In each repetition, B and C run in the order B→C, C→B, B→C (§9.1). A runs once per repetition.
- **Order of the sweeps:** main, then auxiliary, then H3, then the A set. The 4K design of A11 comes after all of them. PROTOCOL does not order the sweeps.
- **One sweep per session.** A sweep takes as many sessions as it needs.

### Each session

- **Before the first run:**
  - the checks of A10;
  - fixed clocks (A7) and the stream server with the M1 assignment (A9);
  - 10 minutes idle (§9.2). Over its last 60 s the session takes the `soc-thermal` baseline. Over its last 120 s it takes the idle baseline of system CPU (§7.2), in `idle_baseline.csv`.
- **Every run:**
  - waits for the cooldown gate (§9.2);
  - records the environment first (§9.1);
  - carries the `freeze-m1` commit in its header;
  - is judged by §9.3, with A9's method for rule 4 and for external failures.
- **Rule 3 applies at unsaturated conditions** (§6.2 with `L` = 32, A9): every main count except 32, every auxiliary count, and both A counts.
- **The ambient temperature** is what the operator reports at the session's start.
- **A session ends** as §2 says: a reboot, a cooling change, or 30 minutes without a run. An external failure, or the operator, also ends it.

### Resuming a sweep (§9.1, §9.3)

- A session of a sweep first reads the `runs.tsv` of the sweep's earlier sessions, in `results/<run kind>/<UTC>_<sweep>/`.
- **Complete repetition:** all of its runs are final within one session. A final run is valid, or invalid ×2.
- **Incomplete repetitions** are redone first, all of their runs, in the repetition's order.
  - A run whose last attempt was an invalid first attempt continues with its repeat, attempt 2 (§9.3).
  - Every other run of that repetition starts again at attempt 1. This includes a valid run, or an invalid ×2 one, whose partner never ran (§9.1).
  - The earlier runs stay where they are. The redo lists them in its `replaces` column.
- **An invalid attempt counts as one** whatever made it invalid, including an external failure or the operator stopping the run (§9.3).
- Then the session goes on with the repetitions not yet run, in order.
- Each session's `result.csv` lists the repetitions it ran and those still left. A sweep is complete when none is left.

### Smoke tests

- A session with any setting other than the protocol's (`--idle-s`, `--warmup`, `--duration`, `--gate-s`, `--counts`) is a smoke test.
  - Its runs are of kind `check`, in `results/check/<UTC>_<sweep>_smoke/`.
  - No sweep reads it, and it counts toward nothing.
- **First smoke test:** [results/check/20261103T070702Z_main_smoke](../../results/check/20261103T070702Z_main_smoke). Main sweep, 1 channel, 2 s warm-up, 5 s window. Its 6 runs were valid and ran in the order B, C, C, B, B, C. The headers carry the `freeze-m1` commit, and the idle baseline was written.

### Time

- A protocol run takes about 2.5 minutes, and the gate adds at least 60 s.
- So the main sweep (42 runs) takes about 2.5 to 3 hours, the auxiliary sweep (30 runs) about 2 hours, and the A set (6 runs) about 20 minutes.
- Each session adds 10 minutes of idling.

## Files and tools

| File | Content |
|---|---|
| `results/comparison/<UTC>_main/`, `…_aux/` | one session each: `runs.tsv`, the runs, `idle_baseline.csv`, the server's log, `result.csv` |
| `results/reference/<UTC>_reference/` | the same, for the A set |
| `tools/stream_session.py sweep` | runs a session of a sweep ([tests](../../tests/test_stream_session.py)) |
