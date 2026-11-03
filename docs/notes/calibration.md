[한국어](calibration.ko.md)

# Calibration (M1-3)

Hardware limits that set the channel counts of the comparison (PROTOCOL §6), the fixed clocks (§8), and the thermal check that fixes the cooling (§12). These are calibration runs, not comparison runs.

| Value | Status |
|---|---|
| Fixed clocks | fixed, [AMENDMENTS A7](../../prereg/AMENDMENTS.md) |
| `P_cap`, H2 auxiliary point | fixed, A7 |
| Raw NPU limit (1, 2, 3 cores) | measured, A7 (reference only) |
| `L`, main set, unsaturated boundary | fixed, [AMENDMENTS A9](../../prereg/AMENDMENTS.md): `L` = 32 |
| Loop check (§3) | passed, A9 |
| Thermal check, cooling configuration | passed; cooling fixed, A9 |

## Fixed clocks

| Domain | Fixed | Highest offered | High-temperature cap (`h_limit`, above 85 °C) |
|---|---|---|---|
| A55, policy0 (cpu0–3) | 1800 MHz | 1800 MHz | 1608 MHz |
| A76, policy4 (cpu4–5) | 2208 MHz | 2304 or 2352 MHz, depending on the boot | 2208 MHz |
| A76, policy6 (cpu6–7) | 2208 MHz | 2352 MHz | 2208 MHz |
| DDR, `dmc` | 2112 MHz | 2112 MHz | none |
| NPU | 1000 MHz | 1000 MHz | 800 MHz |

The caps come from the kernel log (`h_limit=` lines). They are not the only limit: thermal throttling from `soc-thermal`'s 75 °C trip can go lower. On 2026-10-30 it lowered the caps to 1416 MHz (A55), 2016 MHz (A76) and 800 MHz (NPU) ([env.md](env.md#thermal-observation)).

The highest entry of policy4's table changed between boots ([evidence](calibration/a76_tables.txt)):

| Boot | policy4 highest | Source |
|---|---|---|
| 2026-10-30 (M1-0 baseline) | 2304 MHz | `collect_env.sh` |
| 2026-11-02, before the M1-4 runs | 2352 MHz | `collect_env.sh` |
| 2026-11-02, about 05:25 UTC | 2304 MHz | read by hand from sysfs |
| 2026-11-02 07:20 UTC | 2352 MHz | `collect_env.sh` |

On the last two boots, 2208 MHz is the only other entry above 2016 MHz. So both A76 clusters are fixed at 2208 MHz:
- it is offered on every boot seen;
- it equals the kernel's high-temperature cap for both clusters.

The other domains keep their maximum. `tools/set_perf_mode.sh` sets the `performance` governor with each maximum at the fixed value, and fails when a value is not offered or not reached. A clock sample below a fixed value makes a run invalid (§9.3 rule 1), whether a cap or throttling caused it.

The frequencies read back are what the drivers report (`scaling_cur_freq`, devfreq `cur_freq`), not a measurement of the clock. `tools/collect_env.sh` now also records the frequency tables, the boot time and the `h_limit` caps before every run.

## P_cap and the raw NPU limit

### Method

- **Tensor set** (§6.2): `tools/dump_tensor` writes the first 1,000 frames of video ① (`file_source`) as C's preprocessing (RGA3) produces them. SHA-256 `051d6f12…`, the hash of the `c.u8` that `tools/equivalence_dump` at code `669fb9b` wrote in the M1-4 deciding run, kept in Release [`data-equivalence-20261102T011123Z`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/data-equivalence-20261102T011123Z). So the set is that run's C input. Its own record (`tensors.json`) names code `38ddd37` with the then uncommitted `dump_tensor`.
- **`tools/calib_npu`:**
  - Loads the 1,000 tensors into `system-uncached-dma32` buffers, the heap of C's slots.
  - Worker `w` owns the context on NPU core `w` and cycles over the tensors whose index ≡ `w` (mod workers). Each tensor is registered with that context through `rknn_create_mem_from_fd`.
  - A step binds a tensor with `rknn_set_io_mem` and runs `rknn_run`. With `--with-post` it also runs the real post-processing on the bound outputs.
  - A step counts when it completes inside the 120 s window after a 20 s warm-up (§7.4).
  - Before and after the window, each context's bound outputs are compared with `rknn_outputs_get` on the same tensor (A3). A different tensor runs first, and its outputs must differ.
  - The process is pinned to cpu4–7 and the sampling thread to cpu0, as in `bench_main` (§8). One-second samples are taken as in §7.7.
- **`tools/calib_npu.sh`** runs one session:
  - fixed clocks, then `collect_env.sh`;
  - 10 minutes idle and the `soc-thermal` baseline (§9.2);
  - P_cap and the raw limit on 1, 2 and 3 cores, interleaved, 3 repetitions;
  - before every run, the cooldown gate and `collect_env.sh` (§9.1);
  - an invalid run is repeated once (§9.3).
- **`tools/calib_summary.py`** takes the medians and computes the derived values exactly, with fractions. It flags settings other than the protocol's, and a measurement without 3 valid runs or invalid ×2 records.

### Results

Session [results/calibration/20261102T073918Z_npu](../../results/calibration/20261102T073918Z_npu):
- 2026-11-02, 07:50–08:30 UTC, code `3789a7a`;
- ambient 28 °C, cooling: heatsink and an always-on mini fan (unbranded, AliExpress);
- `soc-thermal` baseline 39.9 °C.

All 12 runs were valid. No clock sample fell below its fixed value, and no temperature reached a trip point.

| Measurement | Repetition 1 | 2 | 3 | Median, inferences/s |
|---|---|---|---|---|
| **P_cap** (3 cores, with post-processing) | 194.5333 | 194.1667 | 194.2083 | **194.2083** |
| Raw, 1 core | 70.7333 | 70.7250 | 70.7000 | 70.7250 |
| Raw, 2 cores | 137.2250 | 137.0750 | 137.2833 | 137.2250 |
| Raw, 3 cores | 199.1500 | 199.3750 | 199.2083 | 199.2083 |

Means over the 3 repetitions:

| Measurement | `rknn_run` | Post-processing | Bind | NPU load per core in use | Process CPU in 120 s | Highest temperature |
|---|---|---|---|---|---|---|
| P_cap | 15.04 ms | 0.374 ms | 0.023 ms | 91% | 32.4 s | 55.5 °C |
| Raw, 1 core | 14.13 ms | | 0.014 ms | 94% | 7.2 s | 47.2 °C |
| Raw, 2 cores | 14.56 ms | | 0.016 ms | 94% | 14.8 s | 50.8 °C |
| Raw, 3 cores | 15.04 ms | | 0.019 ms | 93% | 23.9 s | 55.5 °C |

### Derived values (§6.2)

- **H2 auxiliary point:** floor(0.8 × 194.2083 / 30) = floor(5.18) = **5 channels**. The auxiliary set (§6.3) is {1, 2, 3, 4, 5}. `P_cap` would have to fall below 187.5, 3.5% lower, for the point to become 4.
- **Unsaturated condition, `P_cap` part:** channels × fps ≤ 0.8 × 194.2083 = 155.37. That allows up to 31 channels at the main condition (5 fps) and 5 at the auxiliary one (30 fps). The condition also needs channels ≤ `L`.

### Observations

- **Post-processing:** it costs 2.5% of the throughput (194.21 against 199.21 on 3 cores), 0.37 ms per inference on an A76 at 2208 MHz. There are 15.8 detections per inference, as in M1-4 (15,810 on 1,000 frames for C).
- **Scaling:**
  - 2 cores give 1.94 times the 1-core rate, and 3 cores 2.82 times.
  - `rknn_run` takes longer as cores are added: 14.13, 14.56 and 15.04 ms. A shared resource, likely memory bandwidth, limits the cores together.
- **Spread:** the P_cap runs differ by at most 0.19%.

## L, the loop check and the thermal check: method

Fixed before the runs. `tools/stream_session.py` runs each of the three as one session on the board, and drives the stream PC over the wired link (`limit`, `loop-check`, `thermal --limit L`).

- **Session:**
  - fixed clocks and the environment;
  - the stream server with the M1 assignment ([stream-server/assignment_m1.txt](../../stream-server/assignment_m1.txt): ch1 to ch32 take videos ① to ④ in turn), its log in UTC, the PC kept awake;
  - 10 minutes idle and the cooldown gate (§9.2);
  - the environment before every run (§9.1);
  - the server's log lines and its publishers' output kept with every run (§3);
  - an invalid run repeated once (§9.3).
- **`L`** (§6.1): `--variant none` runs, from 8 channels up to the cap of 32, then confirmation as in §10.
  - A run passes when every channel keeps drift ≤ 1 s and has no decoder drops.
  - Rules 1, 2 and 4 of §9.3 apply. An invalid ×2 run counts as not passing.
  - Main set: {1, 2, 4, 8, 12, 16} up to `L`, plus `L`. Unsaturated: at most min(`L`, 31) channels at 5 fps and min(`L`, 5) at 30 fps (A7).
- **Loop check** (§3): for each video, one 1-channel `--variant none` run on its first channel, with at least two loop points in the window. It passes with zero decoder drops. Rules 1, 2 and 4 apply.
- **Thermal check** (§12): B with 5 channels at the auxiliary condition and with `L` channels at the main condition, 140 s each.
  - It passes when no clock sample falls below its fixed value and every temperature stays below its zone's trip point 0. This uses every one-second sample, warm-up included.
  - Rule 1 is the check's criterion, so it does not make these runs invalid. Rules 2, 3 (both runs are unsaturated if `L` ≤ 31) and 4 apply.
- **Rule 4,** read from a log with one-second resolution: an event counts if its second lies between the warm-up start and the window end, both rounded down.
  - In the window end's own second, only the board's disconnect is exempt, because the bench disconnects right after the window. The publishers stop at least 1 s later.
  - Events:
    - an `ERR` line;
    - a closed connection or session of the board;
    - a publisher that stops or exits;
    - a second publisher start on a channel within a run;
    - a closed session of any other host.
  - `WAR` lines and publisher (ffmpeg) output are recorded as warnings.
- **External failures** (§9.3):
  - An input that ends early, or an unreadable server log, makes the run invalid.
  - A stream server that is down, an unreadable server log, or a PC clock more than 5 ms behind or 100 ms ahead of the board's, also stops the session.
  - An operator's stop (SIGTERM) stops the session; a run it cuts short is recorded with that reason and neither repeated nor counted.
  - A stopped `L` search is kept and flagged, and the search is run again from §10 step 1 in a new session.

## L: results

Session [results/calibration/20261103T000647Z_limit](../../results/calibration/20261103T000647Z_limit):
- 2026-11-03, 00:16–01:51 UTC, code `9553f70`, amendment A7;
- ambient 24 °C, cooling as for `P_cap`;
- `soc-thermal` baseline 35.2 °C.

All 28 runs were valid and passed: the search from 8 to 32 channels, then 3 confirmation runs at 32 (§10). **`L` = 32, at the cap:** the procedure goes no higher than 32 channels, the size of the channel assignment (§6.1). The decoder may sustain more.

| Channels | Largest drift | Decoder drops | Process CPU in 120 s | Highest `soc-thermal` |
|---|---|---|---|---|
| 8 | 0 s | 0 | 9.6 s | 37.9 °C |
| 16 | 0 s | 0 | 19.5 s | 41.6 °C |
| 24 | 0 s | 0 | 32.4 s | 44.4 °C |
| 32, 4 runs | ≤ 0.033 s | 0 | 41.8–44.0 s | 47.2 °C |

- Drift never exceeded 0.033 s, one frame. No run had a rule 4 event or a warning. The PC's clock was 4 to 8 ms ahead of the board's.
- At the fixed clocks, decoding costs about 1.35 s of CPU per channel in 120 s at 32 channels, 1.1% of a core. From 8 to 32 channels, each added channel adds about 1.4 s.

### Derived values (§6.1, §6.2)

- **Main set:** {1, 2, 4, 8, 12, 16, 32}.
- **Unsaturated:** at most 31 channels at the main condition and 5 at the auxiliary one. In the main set only 32 is saturated: 32 × 5 = 160 inferences/s > 155.37.
- **H3:** B and C are also searched up to 32 channels. If the confirmed counts `n_B` and `n_C` are both at least 31 (`L` − 1), H3's practical verdict is *indeterminate (ceiling reached)* (HYPOTHESES, H3).
- PROTOCOL sets no bound on the ambient temperature. `P_cap` ran at 28 °C, the sessions below at 24 to 26 °C.

## Loop check: results

Session [results/check/20261103T032849Z_loop](../../results/check/20261103T032849Z_loop):
- 2026-11-03, 03:38–03:51 UTC, code `5c9bc80`, amendment A8;
- ambient 25 °C in its header (the operator reported 26 °C at 03:50);
- `soc-thermal` baseline 37.8 °C.

**Passed:** each video's 1-channel run is valid, crosses at least two loop points in its window, and has no decoder drops. The loop points are computed from each video's duration.

| Video | Channel | Loop points in the window | Decoder drops |
|---|---|---|---|
| ① `pexels_36330861` | ch1 | 3 | 0 |
| ② `pexels_29130409` | ch2 | 5 | 0 |
| ③ `pexels_29969146` | ch3 | 5 | 0 |
| ④ `pixabay_180386` | ch4 | 4 | 0 |

## Thermal check: results

Session [results/calibration/20261103T035136Z_thermal](../../results/calibration/20261103T035136Z_thermal):
- 2026-11-03, 04:01–04:08 UTC, code `5c9bc80`, amendment A8;
- ambient 26 °C, cooling: heatsink and an always-on mini fan (unbranded, AliExpress);
- `soc-thermal` baseline 37.9 °C.

**Passed:** both runs are valid, no clock sample fell below its fixed value, and every temperature stayed below its zone's trip point 0. This fixes the cooling configuration (§12, [AMENDMENTS A9](../../prereg/AMENDMENTS.md)).

| Run | Highest `soc-thermal` (trip point 0: 75 °C) | Highest other zone (trip point 0: 115 °C) | Clocks |
|---|---|---|---|
| B, 5 channels, auxiliary | 58.2 °C | 60.1 °C, `bigcore1` | always at the fixed values |
| B, 32 channels, main | 67.5 °C | 68.4 °C, `bigcore0` and `bigcore1` | always at the fixed values |

- Every one-second sample counts, warm-up included: 140 per run.
- Rule 3 of §9.3 applies to the 5-channel run only: 32 channels at 5 fps is saturated.
- At 32 channels, `soc-thermal` stayed 7.5 °C below its trip point.
- B's throughput and latency in these runs are not analysed. They are calibration runs, not comparison runs (§12).

## Sessions that stopped or were invalid

They are kept under `results/` and flagged. None of their runs counts.

| Session | What happened | Runs |
|---|---|---|
| [check/20261103T015110Z_loop](../../results/check/20261103T015110Z_loop) | Stopped: after its third run, the PC's clock was more than 5 ms behind the board's. | 2 valid, 1 invalid (external failure) |
| [calibration/20261103T021032Z_thermal](../../results/calibration/20261103T021032Z_thermal) | Stopped at its start, for the same reason. | none |
| [check/20261103T022521Z_loop](../../results/check/20261103T022521Z_loop) | Build defect: every bench run crashed at its end (exit −11). | 8 invalid: 4 invalid ×2 |
| [calibration/20261103T030136Z_thermal](../../results/calibration/20261103T030136Z_thermal) | The same build defect. | 4 invalid: 2 invalid ×2 |

- **Clock:**
  - The board took its time from `ntp.ubuntu.com` over Wi-Fi (SNTP); the PC takes it from servers in Korea with chrony. Their offset wandered from +8 ms (PC ahead) to beyond −5 ms.
  - The records say −5 ms because `tools/stream_session.py` rounded offsets to whole milliseconds; it now keeps 0.1 ms.
  - Since 02:19 UTC the board takes its time from the PC over the wired link, and since 04:10 UTC it polls the PC no less often than every 64 s ([env.md](env.md#network)).
  - The sessions run on the PC's time recorded offsets of 0 to −3 ms, inside the bounds. After the poll change, `timesyncd` reported −0.14 ms; no session has run since.
- **Build defect:**
  - `tools/deploy_board.sh` restored each file's original time. A deploy from `main` for the held-out check rebuilt `pipeline.cpp` and `report.cpp` from `main`'s versions.
  - The next deploy, from this branch, brought back their own versions with older times, so make skipped them. The tools whose commit define changed, `bench_main` among them, were rebuilt against this branch's `RunResult`.
  - Since commit `5c9bc80`, the copy takes the time of extraction, so each deploy rebuilds every file it copies.
  - **The `L` session (`9553f70`) is not affected.** Its 28 runs exited normally. Their `summary.csv` holds `warmup_start_utc` and `window_*_utc`, which only this branch's `pipeline.cpp` and `report.cpp` write, with times that match `runs.tsv`.
  - **The held-out check (`07fe535`) is not affected.** `tools/equivalence_dump` uses none of `RunResult`, `run_pipeline` or the result writers. The four `src/` files that differ between `main` and this branch change only these and a clock helper they use.
  - **Earlier builds:** the defect needs a deploy whose files are older than the objects of the build before it, made from other sources. No deploy log is kept for earlier builds. Their results carry checks of their own: M1-4's three runs gave the same bytes and its harness checks held, and every `P_cap` run passed its output checks (A7).
- **Repeated checks:**
  - The loop check and the thermal check each ran three times. The first loop check's two valid runs are not used: the check was redone in full in a new session.
  - A session with invalid ×2 runs has no result. It is not a failure of §12, which would call for a cooling change. Calibration checks are redone before `freeze-m1` (§15).

## Files

| File | Content |
|---|---|
| `perf_mode.txt` | the fixed clocks the runs were checked against |
| `env.txt`, `env_end.txt`, `<run>.env.txt` | `collect_env.sh` at the session's start and end, and before each run |
| `baseline.txt` | idle time and `soc-thermal` baseline |
| `tensors.json`, `tensors.sha256` | the tensor set's record and hash |
| `runs.tsv` | every attempt: exit code, temperature at the gate, start and end time |
| `<run>/summary.csv`, `samples.csv`, `env.json`, `<run>.log` | each run's result, one-second samples, threads and bindings, console output |
| `calibration.csv`, `calibration.txt` | the medians and the derived values |
| `result.csv`, `session.log` | a stream session's result and its console |
| `server.txt`, `server/` | the stream server: clock offset, the PC's commit and file hashes, versions; its whole log and assignment |
| `<run>/server.log`, `server_console.txt` | the server's log lines and publisher output for that run (rule 4) |
| `<run>/thermal_check.txt` | the thermal check's verdict for that run |

## Tools

| Tool | Purpose |
|---|---|
| `tools/set_perf_mode.sh` | fixed clocks |
| `tools/dump_tensor` | the tensor set, with its record |
| `tools/calib_npu` | one P_cap or raw-limit run |
| `tools/calib_npu.sh` | one session |
| `tools/calib_summary.py` | medians and derived values ([tests](../../tests/test_calib_summary.py)) |
| `tools/check_prereg_order.py` | the start-time checks of §1.5 ([tests](../../tests/test_check_prereg_order.py)) |
| `tools/stream_session.py` | one session of `L`, the loop check or the thermal check, driving the stream PC ([tests](../../tests/test_stream_session.py)) |
