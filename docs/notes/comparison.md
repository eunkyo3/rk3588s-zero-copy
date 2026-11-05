[한국어](comparison.ko.md)

# Comparison runs (M1-6)

The comparison runs of PROTOCOL §12, after `freeze-m1` ([env.md](env.md#registration)). `tools/stream_session.py sweep` runs them, one session at a time, on the board.

## Method, fixed before the runs

This section fixes how the tool carries out §12 where PROTOCOL leaves a choice. It changes nothing that PROTOCOL or AMENDMENTS fix.

### Sweeps and their order

| Sweep | Runs | Run kind | Channel order (§9.1) |
|---|---|---|---|
| `main` | B and C × main set {1, 2, 4, 8, 12, 16, 32} (A9) × 3, main condition | `comparison` | 8, 1, 16, 4, 32, 2, 12 |
| `aux` | B and C × auxiliary set {1, 2, 3, 4, 5} (§6.3, A7) × 3, auxiliary condition | `comparison` | 1, 4, 3, 2, 5 |
| `reference` | A × A set {1, 2} (§6.3) × 3, main condition | `reference` | 2, 1 |

- **Channel order:** the sorted counts, shuffled by a fresh `random.Random(20261030)` per sweep (§9.1). The A set is not a sweep in §9.1's sense, but it gets the same procedure.
- **Blocks:** one block per count, in that order. A block has 3 repetitions. In each repetition, B and C run in the order B→C, C→B, B→C (§9.1). A runs once per repetition.
- **Order of the sweeps:** main, then auxiliary, then H3, then the A set. The 4K design of A11 comes after all of them. PROTOCOL does not order the sweeps.
  - The tool starts a session only when the sweeps before its own are complete and its own is not.
  - While a sweep or H3 has a repetition or a repeat to redo, the tool starts no other session at all, smoke tests and calibration included.
  - So the session after one that left a repetition incomplete is always a session of the same sweep, as §9.1 and §9.3 require of "the next session".
- **One sweep per session.** A sweep takes as many sessions as it needs.

### Each session

- **Before the first run:**
  - the checks of A10;
  - fixed clocks (A7) and the stream server with the M1 assignment (A9);
  - 10 minutes idle (§9.2). Over its last 60 s the session takes the `soc-thermal` baseline. Over its last 120 s it takes the idle baseline of system CPU (§7.2), in `idle_baseline.csv`. Its `window_s` is the measured length, slightly over 120 s; the report scales the baseline to each run's window.
- **Every run:**
  - waits for the cooldown gate (§9.2);
  - records the environment first (§9.1);
  - carries the `freeze-m1` commit in its header;
  - is written to `started.tsv` just before its bench starts, after the gate and the checks. So a run the session's death cuts short (a reboot) is still known, and a run that never started leaves no trace;
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
- **An invalid attempt counts as one** whatever made it invalid, including an external failure, the operator stopping the run, or the session's death during it: an attempt in `started.tsv` without a row in `runs.tsv` (§9.3).
- **The last attempt counts across sessions:** a run whose first attempt failed in one session, and which a later session did not reach, still continues with its repeat.
- Then the session goes on with the repetitions not yet run, in order.
- Each session's `result.csv`, also of a session that stopped, lists the repetitions it ran and those still left. A sweep is complete when none is left.

### Smoke tests

- A session with any setting other than the protocol's (`--idle-s`, `--warmup`, `--duration`, `--gate-s`, `--counts`) is a smoke test.
  - Its runs are of kind `check`, in `results/check/` (by default `<UTC>_<sweep>_smoke/`). The tool refuses any other place for it.
  - A protocol session must be named `<UTC>_<sweep>` in `results/<run kind>/`, which is where the next one looks.
  - No sweep reads it, and it counts toward nothing.
- **First smoke test:** [results/check/20261103T070702Z_main_smoke](../../results/check/20261103T070702Z_main_smoke). Main sweep, 1 channel, 2 s warm-up, 5 s window. Its 6 runs were valid and ran in the order B, C, C, B, B, C. The headers carry the `freeze-m1` commit, and the idle baseline was written.

### H3 (§10)

- **Runs:** run kind `comparison`, in `results/comparison/<UTC>_h3/`. Main condition, B and C, `L` = 32 (A9), starting at min(8, `L`) = 8.
- **Sustained:** as HYPOTHESES H3 defines it, over the run's 120 s window: every channel at an inference rate of at least 4.75 fps (§7.4), with drift ≤ 1 s and no decoder drops (§7.6).
  - Rules 1, 2 and 4 of §9.3 make a run invalid; rule 3 does not apply (§10).
  - An invalid run is repeated once. A run that is invalid ×2 counts as not sustained and is flagged (§10 step 3).
- **One schedule for both variants** (§10):
  - **Search** (step 1): one run per variant and step. While both search, B→C at odd steps and C→B at even ones (step 4). A variant whose search has ended waits for the other.
  - **Confirmation** (step 2) begins when both searches have ended, so that the counts both need can be interleaved (step 4). PROTOCOL does not say whether a variant may confirm while the other still searches; here it may not.
  - Confirmation goes in rounds. In each round, every variant still confirming needs the counts n (if n ≥ 1) and n + 1 (if n < `L`) at which it has no confirmation runs yet. These counts run in ascending order.
  - At a count both variants need, its 6 runs are interleaved B→C, C→B, B→C. At a count only one needs, that variant's 3 runs run alone.
  - After each round, each variant goes up, goes down, or is confirmed, checking "up" first. Runs at a count are kept when n moves past it (step 2).
- **Sessions:** H3 also spans as many sessions as it needs.
  - Each session replays the earlier sessions' runs through the same schedule, then goes on with the next run. A record out of the schedule's order stops the session.
  - A run whose last attempt was an invalid first attempt continues with its repeat, as in a sweep.
  - **Interleaved pairs:** the two runs of an interleaved pair at a shared count are a repetition in §9.1's sense. If a session ends between the two runs of a pair, both are redone; the finished one is kept and replaced. If the second's first attempt was invalid, the first is redone and the second continues with its repeat.
  - A search step's B and C runs are single runs, not a repetition: a session may end between them.
- **Smoke test:** [results/check/20261103T071611Z_h3_smoke](../../results/check/20261103T071611Z_h3_smoke), `--start 1 --cap 2`, 2 s warm-up, 5 s window.
  - The search ran B1, C1, then C2, B2. At 2 = `L` there is no n + 1.
  - So the confirmation was the shared count 2 alone, in the order B, C, C, B, B, C.
  - Every run was valid and sustained: n_B = n_C = 2.

### M1.1: the 4K design (A11)

- **Runs:** `sweep 4k`, run kind `m1.1`, in `results/m1.1/<UTC>_4k/`. B and C at 1, 2, 4 and 8 channels, main condition, × 3 repetitions.
  - The channel order is §9.1's procedure with a fresh generator: 1, 8, 4, 2.
  - Blocks, variant order, gate, validity, sessions and resuming are those of the sweeps above.
  - Rule 3 applies to every run (A11).
- **Video set:** [stream-server/assignment_4k.txt](../../stream-server/assignment_4k.txt), served from `media/bench4k` on the stream PC.
  - Each 4K session checks the server's `assignment.tsv` against A11's SHA-256 of videos ①, ② and ④, and stops on a difference.
  - The M1 sessions keep the M1 set, as before.
- **Order:** after every sweep of §12, as the order above already says ("4k" last).
  - First comes the 4K loop check: `loop-check --set 4k`, in `results/check/<UTC>_loop_4k/`.
  - The tool starts a 4K session only once such a check has completed with the protocol's settings. Whether its videos passed does not matter here: A11 reports a failing video and goes ahead.
- **Tensors** (A11), after `freeze-m1`, by hand on the board, as root from the repository:
  - `echo "dc02ec11ee5774588b90233e5e364bac37f2b4e8e0be771fd023e06539a59a8e  /home/pi/media/bench4k/pexels_36330861.mp4" | sha256sum -c`, A11's hash of ① at 4K;
  - `./build/equivalence_dump /home/pi/media/bench4k/pexels_36330861.mp4 models/yolov8n_i8.rknn results/m1.1/<UTC>_4k_tensors 1000 <freeze-m1 commit> m1.1`;
  - then `tools/equivalence_check.py` on its directory. Its numbers are reported, and nothing is judged.
  - **The run of 2026-11-04** ([results/m1.1/20261104T023058Z_4k_tensors](../../results/m1.1/20261104T023058Z_4k_tensors)): the chain's check stopped with exit 2, because it took only run kind `equivalence`. `check.txt` has its error, and `recheck_env.txt` the exit code.
    - The check was fixed to take run kind `m1.1` and to judge nothing for it. It was re-run on the same tensors after their SHA-256 matched `tensors.sha256`.
    - Its output is `recheck.txt` and `recheck.json`. `recheck_env.txt` holds the tool's commit and SHA-256, the times and the exit code, 0.
    - A5's criteria do not all hold at 4K. That is reported, not judged.
    - The tensors are kept, compressed with xz, in the release [data-m1.1-20261104T023058Z](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/data-m1.1-20261104T023058Z).
- **Report:** `tools/m1_report.py` gets a 4K section before the 4K results are written up: §7's metrics, B and C side by side, M1's main-condition values at the same counts next to them, labelled exploratory (A11).

### M1.1: the designs of §13 (A12)

- **Runs:** after the 4K sweep, in this order: `sweep k4`, `sweep a55`, `sweep bdirect_main`, `sweep bdirect_aux`. They have run kind `m1.1`, use M1's 1080p video set, and go in `results/m1.1/<UTC>_<sweep>/`.
  - Each sweep reruns its controls in the same session ([A12](../../prereg/AMENDMENTS.md)):
    - B and Bk4 at 8 channels;
    - B and C on the A55 cores at 8 channels, one repetition;
    - B, C and Bd at 8 channels main, and at 5 channels aux.
  - The session tool passes `--variant Bd` for Bd, `--variant B --prep-threads 4` for Bk4, and `--cores a55` on every run of `a55`.
  - With three arms, the order within a repetition rotates: B→C→Bd, C→Bd→B, Bd→B→C.
  - Blocks, gate, validity, sessions and resuming are those of the sweeps above.
- **Before the first session of A12**, by hand on the board, as root from the repository, after `amend-A12`: a default run of B, kept in `results/check/<UTC>_default_b/`, whose `env.json` has the threads, affinities and `queues` of M1's `31_main_B_2ch_r1` (A12):
  - `./build/bench_main --variant B --channels 2 --source /home/pi/media/bench/pexels_36330861.mp4 --warmup 2 --duration 6 --out results/check/<UTC>_default_b`.
- **Before B-direct's first session**, the same way:
  - `mkdir -p results/check/<UTC>_bdirect_check && tools/collect_env.sh >results/check/<UTC>_bdirect_check/env.txt`;
  - `./build/bdirect_check /home/pi/media/bench/pexels_36330861.mp4 models/yolov8n_i8.rknn results/check/<UTC>_bdirect_check 100 2eec964d27545e081a44bc5ead01bbfcd2905a54 >results/check/<UTC>_bdirect_check/check.txt`. Its `check.csv` must say `passed,1`.
  - The session tool starts no `bdirect_main` or `bdirect_aux` session until such a check has passed, built from the commit of the session's binary with A12 or later. `k4` and `a55` do not wait for it.
- **Report:** before these designs are written up, `tools/m1_report.py` gets a section for each: §7's metrics, each arm next to the session's own controls, labelled exploratory.

### M2: C against Cp ([prereg/M2.md](../../prereg/M2.md))

- **Before `freeze-m2`**, on the board as root from the repository, with a build of the implementation that is not `-dirty`, in this order (M2 §7 step 3). The equivalence check comes first; a fix under §4's failure rules repeats every step.
  1. **Equivalence (§4):**
     - `mkdir -p results/check/<UTC>_m2_equivalence && tools/collect_env.sh >results/check/<UTC>_m2_equivalence/env.txt`;
     - `./build/m2_equivalence models/yolov8n_i8.rknn results/check/<UTC>_m2_equivalence none /home/pi/media/bench/pexels_36330861.mp4 /home/pi/media/bench/pexels_29130409.mp4 /home/pi/media/bench/pexels_29969146.mp4 /home/pi/media/bench/pixabay_180386.mp4 >results/check/<UTC>_m2_equivalence/m2_equivalence.txt`. Its `m2_equivalence.csv` must say `passed,1`.
  2. **Default path (§7 step 2):** `./build/bench_main --variant C --channels 2 --source /home/pi/media/bench/pexels_36330861.mp4 --warmup 2 --duration 6 --out results/check/<UTC>_default_c`.
  3. **Stagger and `/proc/schedstat` (§3.1, §3.2):** `COOLING=... AMBIENT_C=... python3 tools/stream_session.py m2-check`. It runs C, then Cp, at 8 channels main, staggered, with the protocol's warm-up, window, gate and idle baseline, into `results/check/<UTC>_m2_check/`.
  4. **The verdicts of these checks:** `python3 tools/m2_checks.py --equivalence results/check/<UTC>_m2_equivalence --default results/check/<UTC>_default_c --c <m2_check's C run> --cp <m2_check's Cp run>` prints them, with the check runs' CPU values (§7 step 3). Exit 0 when all pass. Its output goes into the amendment that `freeze-m2` tags.
- **Runs, after `freeze-m2`:** `sweep m2_main`, then `sweep m2_aux`. They have run kind `m2` and go in `results/m2/<UTC>_<sweep>/`.
  - The session tool passes `--variant C` for C and `--variant C --post neon` for Cp, `--stagger on` on every run of `m2_main`, and `--freeze-m2-commit` with `freeze-m2`'s commit from the Registration table of [env.md](env.md).
  - The order per repetition is C→Cp, Cp→C, C→Cp. Blocks, gate, validity, sessions and resuming are those of the sweeps above.
  - No M2 session starts until `freeze-m2` has its row in env.md and a passed equivalence check of this build's two post-processing objects (their SHA-256) is in `results/check/`. A failed check of the same objects stops Cp for good (M2 §4). `m2-check` needs the equivalence check but not `freeze-m2`.
  - Each session's idle baseline also has `/proc/schedstat`'s rows (M2 §3.2), and its `result.csv` records the two objects' SHA-256.

### Time

- A protocol run takes about 2.5 minutes, and the gate adds at least 60 s.
- So the main sweep (42 runs) takes about 2.5 to 3 hours, the auxiliary sweep (30 runs) about 2 hours, and the A set (6 runs) about 20 minutes. H3 depends on where the searches stop: up to 50 search runs and 6 or more confirmation runs.
- Each session adds 10 minutes of idling.
- The M1.1 designs of A12 have 26 runs in 4 sessions, about 2 to 2.5 hours.
- M2's `m2-check` has 2 runs, about 20 minutes; its sweeps have 12 runs in 2 sessions, about 1 to 1.25 hours.

## Files and tools

| File | Content |
|---|---|
| `results/comparison/<UTC>_main/`, `…_aux/` | one session each: `runs.tsv`, the runs, `idle_baseline.csv`, the server's log, `result.csv` |
| `results/comparison/<UTC>_h3/` | one H3 session each, the same files |
| `results/reference/<UTC>_reference/` | the same, for the A set |
| `results/m1.1/<UTC>_4k/` | the same, for the 4K design (A11) |
| `results/m1.1/<UTC>_k4/`, `…_a55/`, `…_bdirect_main/`, `…_bdirect_aux/` | the same, for the designs of §13 (A12) |
| `results/check/<UTC>_default_b/` | A12's default run of B |
| `results/check/<UTC>_bdirect_check/` | B-direct's functional check: `env.txt`, `check.txt`, `check.csv` |
| `tools/bdirect_check.cpp` | that check (A12) |
| `results/check/<UTC>_m2_equivalence/`, `…_default_c/`, `…_m2_check/` | M2's checks before `freeze-m2` (M2 §7 step 3) |
| `results/m2/<UTC>_m2_main/`, `…_m2_aux/` | one M2 session each, the files of a sweep |
| `tools/m2_equivalence.cpp`, `tools/m2_checks.py` | M2's equivalence check (§4), and the verdicts of the checks before `freeze-m2` ([tests](../../tests/test_m2_checks.py)) |
| `tools/stream_session.py sweep`, `h3` | runs a session of a sweep, or of H3 ([tests](../../tests/test_stream_session.py)) |
