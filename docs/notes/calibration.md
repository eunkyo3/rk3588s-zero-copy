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

| Domain | Fixed | Highest offered | Above 85 °C the kernel limits it to |
|---|---|---|---|
| A55, policy0 (cpu0–3) | 1800 MHz | 1800 MHz | 1608 MHz |
| A76, policy4 (cpu4–5) | 2208 MHz | 2304 or 2352 MHz, depending on the boot | 2208 MHz |
| A76, policy6 (cpu6–7) | 2208 MHz | 2352 MHz | 2208 MHz |
| DDR, `dmc` | 2112 MHz | 2112 MHz | no limit |
| NPU | 1000 MHz | 1000 MHz | 800 MHz |

The limits come from the kernel log (`h_limit=` lines). The highest entry of policy4's table changed between boots:

| Boot | policy4 highest |
|---|---|
| 2026-10-30 (M1-0 baseline) | 2304 MHz |
| 2026-11-02, before the M1-4 runs | 2352 MHz |
| 2026-11-02 05:13 UTC | 2304 MHz |
| 2026-11-02 07:20 UTC | 2352 MHz |

So both A76 clusters are fixed at 2208 MHz, which every boot offers and which the kernel's own limit above 85 °C equals. The other domains keep their maximum. `tools/set_perf_mode.sh` sets the `performance` governor with each maximum at the fixed value, and fails when a value is not offered or not reached. A clock sample below a fixed value makes a run invalid (§9.3 rule 1).

## P_cap and the raw NPU limit

### Method

- **Tensor set** (§6.2): `tools/dump_tensor` writes the first 1,000 frames of video ① (`file_source`) as C's preprocessing (RGA3) produces them. SHA-256 `051d6f12…`, the same file as the `c.u8` of the M1-4 deciding run, kept in Release [`data-equivalence-20261102T011123Z`](https://github.com/eunkyo3/rk3588s-zero-copy/releases/tag/data-equivalence-20261102T011123Z). Two dumps, with code `38ddd37` plus the uncommitted tool and with `3789a7a`, gave the same file.
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

| Measurement | `rknn_run` | Post-processing | Bind | NPU load per core | Process CPU in 120 s | Highest temperature |
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
