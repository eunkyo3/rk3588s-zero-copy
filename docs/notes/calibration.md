[한국어](calibration.ko.md)

# Calibration (M1-3)

Hardware limits that set the channel counts of the comparison (PROTOCOL §6), the fixed clocks (§8), and the thermal check that fixes the cooling (§12). These are calibration runs, not comparison runs.

| Value | Status |
|---|---|
| Fixed clocks | fixed, [AMENDMENTS A7](../../prereg/AMENDMENTS.md) |
| `P_cap`, H2 auxiliary point | fixed, A7 |
| Raw NPU limit (1, 2, 3 cores) | measured, A7 (reference only) |
| `L`, main set, unsaturated boundary | to do: needs the wired stream server, now set up ([env.md](env.md#network)) |
| Thermal check, cooling configuration | to do |

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
  - A stream server that is down, or a PC clock more than 20 ms behind or 100 ms ahead of the board's, also stops the session.
  - An operator's stop (SIGTERM) stops the session; a run it cuts short is recorded with that reason and neither repeated nor counted.
  - A stopped `L` search is kept and flagged, and the search is run again from §10 step 1 in a new session.

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

## Tools

| Tool | Purpose |
|---|---|
| `tools/set_perf_mode.sh` | fixed clocks |
| `tools/dump_tensor` | the tensor set, with its record |
| `tools/calib_npu` | one P_cap or raw-limit run |
| `tools/calib_npu.sh` | one session |
| `tools/calib_summary.py` | medians and derived values ([tests](../../tests/test_calib_summary.py)) |
| `tools/check_prereg_order.py` | the start-time checks of §1.5 ([tests](../../tests/test_check_prereg_order.py)) |
