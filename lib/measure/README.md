[한국어](README.ko.md)

# M5's measuring tool

The tool of [prereg/M5.md](../../prereg/M5.md) §8 step 2. It measures the installed package's service `rkzc-detect` next to the benchmark's B and C, on the board. It is not part of the package.

| File | What it does | Runs on |
|---|---|---|
| `m5_session.py` | A session: the sweep of §4, or the checks of §8 step 3. Starts and stops the service, reads its socket, takes §3's reads and samples, applies §4's rules, writes each run's record. | the board, as root |
| `m5_metrics.py` | §3's values and §4's rules 1, 2, 3 and 5, computed from a run's stored files. | both |
| `m5_summary.py` | Every run's values, the runs that count, §2's verdicts and §6's values. Needs numpy for the latency percentiles. | the PC |
| `m5_order.py` | The order check of §8 step 2: every record's start time against `prereg-m5`, `freeze-m5` and the amendments' Releases. | the PC |
| `m5_detections.py` | The check "Detections" of §8 step 3: the service's lines against `rkzc-jsonl`'s on one file. | the board, as root |
| `test_m5.py` | Tests of the parts that need no board. | the PC |
| `test_m5_service.py` | The service run's loop against a stand-in for the service and for systemd. | Linux |
| `package.txt` | The version and SHA-256 of the `.deb` that is measured. The session refuses any other file. | |
| `results/` | The records: `<UTC>_m5/` for the sweep's sessions, `<UTC>_m5_check/` and `<UTC>_m5_detections/` for checks. | |

## What it takes from the registered session tool

`m5_session.py` imports `tools/stream_session.py` and uses it unchanged for everything B and C need and for the session around the runs:

- the session's start: the inventory, the fixed clocks, the stream server on the PC, the idle period with its baselines;
- the cooldown gate, the clock exchange with the PC, and the reading of the server's log for rule 4;
- every B and C run, with its judgement by PROTOCOL §9.3 and the repeat of an invalid run;
- `runs.tsv`, `started.tsv`, and the bookkeeping of repetitions across sessions.

For this it adds one entry to the imported table of sweeps, with M3's kind: M5's B and C runs are M3's runs (M3 §1's options, M3 §3's validity, M3 §4's inventory rule). No registered file changes.

It implements itself: the package's checks, the service's run and its judgement by M5 §4, M5's header beside every run, and the order of the arms.

## A session

On the board, as root, from the repository, with the package installed, its model fetched, and the service stopped and disabled:

```sh
COOLING="<text>" AMBIENT_C=<°C> python3 lib/measure/m5_session.py sweep --deb ~/rkzc-work/dist/rkzc_0.1.1-1_arm64.deb
COOLING="<text>" AMBIENT_C=<°C> python3 lib/measure/m5_session.py check --deb ~/rkzc-work/dist/rkzc_0.1.1-1_arm64.deb \
    --duration 10 [--arms B C P P4] [--cpu-check]
```

- **Before the idle period** the session checks that the `.deb` is the recorded one, that every file it holds equals the installed one, and that the model is M1's. It writes `/etc/rkzc/detect.toml` from the package's shipped copy with only `sources` changed.
- **Every session** needs a clean working tree: the tool is the tool of a commit.
- **A sweep** starts only once `freeze-m5` has its row in `docs/notes/env.md`, with the protocol's settings. It reads the earlier sessions that ran the same tool commit and package, and redoes what they left incomplete.
- **The `.deb`** stays outside the repository's checkout: a session refuses a working tree that is not clean.
- **The drop-in** `CPUAffinity=4-7` exists only during a P4 run. The session removes it at its end, also when it stops early.

## A run's record

Every run has `m5_run.csv`: M5's header, then whether the run is valid, why not, and for a service run §3's values. Beside it:

- **B, C:** `bench_main`'s own output, as it wrote it.
- **P, P4:** the socket's lines as received (`lines.jsonl`), the times and timed reads (`marks.csv`), `/proc/stat` and `/proc/schedstat` at both ends of the window, every thread's affinity at both ends, the one-second samples, the unit's journal, and the stream server's log.

`m5_summary.py` recomputes a service run's values from these files and says whether they equal what the session wrote.

## Tests

```sh
python3 lib/measure/test_m5.py
python3 lib/measure/test_m5_service.py   # Linux
python3 lib/measure/m5_order.py
```
