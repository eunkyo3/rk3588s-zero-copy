# Where the CPU goes from B to Cp (exploratory)

Computed after the fact, not pre-registered, no verdict. Values are the mean ± sample sd over the valid runs that count, per 120 s window, in s. Method: `tools/cpu_breakdown.py`. Each session's values are compared with its own control only: the sessions and binaries differ.

## Sessions

| Study | Point | Sweep | Session | Code | Runs (arm: n) |
|---|---|---|---|---|---|
| M1 | 8 channels, main condition (5 fps) | `main` | `20261103T073439Z_main` | `86587a2` | B: 3, C: 3 |
| M1 | 5 channels × 30 fps | `aux` | `20261103T101109Z_aux` | `86587a2` | B: 3, C: 3 |
| M1.1 | 8 channels, main condition (5 fps) | `bdirect_main` | `20261104T061958Z_bdirect_main` | `cdb309e` | B: 3, Bd: 3, C: 3 |
| M1.1 | 5 channels × 30 fps | `bdirect_aux` | `20261104T065947Z_bdirect_aux` | `cdb309e` | B: 3, Bd: 3, C: 3 |
| M2 | 8 channels, main condition (5 fps) | `m2_main` | `20261105T071518Z_m2_main` | `88206ab` | C: 3, Cp: 3 |
| M2 | 5 channels × 30 fps | `m2_aux` | `20261105T074458Z_m2_aux` | `88206ab` | C: 3, Cp: 3 |

## 8 channels, main condition (5 fps)

| Study | Arm | Process CPU | ÷ control | Decoder | NPU workers | Preprocessing | Rest | Threads' sum | Remainder | Decoder's share | Largest group (share) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| M1 | B | 43.28 ± 0.34 | control | 10.47 ± 0.18 | 8.15 ± 0.08 | 24.55 ± 0.10 | 0.10 ± 0.01 | 43.28 ± 0.35 | 0.01 ± 0.05 | 24.2% | Preprocessing (56.7%) |
| M1 | C | 18.56 ± 0.31 | 0.429 | 10.84 ± 0.33 | 7.33 ± 0.04 | 0.30 ± 0.01 | 0.11 ± 0.00 | 18.58 ± 0.37 | -0.02 ± 0.07 | 58.4% | Decoder (58.4%) |
| M1.1 | B | 44.04 ± 0.27 | control | 10.87 ± 0.20 | 8.45 ± 0.06 | 24.61 ± 0.01 | 0.10 ± 0.01 | 44.03 ± 0.26 | 0.01 ± 0.02 | 24.7% | Preprocessing (55.9%) |
| M1.1 | Bd | 42.79 ± 0.67 | 0.972 | 10.30 ± 0.42 | 7.75 ± 0.26 | 24.64 ± 0.06 | 0.10 ± 0.01 | 42.79 ± 0.72 | -0.00 ± 0.05 | 24.1% | Preprocessing (57.6%) |
| M1.1 | C | 19.49 ± 0.26 | 0.443 | 11.34 ± 0.16 | 7.75 ± 0.12 | 0.31 ± 0.02 | 0.10 ± 0.01 | 19.50 ± 0.27 | -0.01 ± 0.01 | 58.2% | Decoder (58.2%) |
| M2 | C | 18.74 ± 0.17 | control | 11.33 ± 0.15 | 6.83 ± 0.08 | 0.45 ± 0.01 | 0.11 ± 0.00 | 18.72 ± 0.15 | 0.02 ± 0.02 | 60.5% | Decoder (60.5%) |
| M2 | Cp | 17.60 ± 0.56 | 0.939 | 11.24 ± 0.53 | 5.82 ± 0.01 | 0.43 ± 0.02 | 0.11 ± 0.01 | 17.60 ± 0.56 | 0.00 ± 0.01 | 63.8% | Decoder (63.8%) |

Change against the session's control, mean − mean (s):

| Study | Step | Process CPU | Decoder | NPU workers | Preprocessing | Rest | Remainder |
|---|---|---|---|---|---|---|---|
| M1 | B → C | -24.72 | +0.38 | -0.82 | -24.26 | +0.01 | -0.03 |
| M1.1 | B → Bd | -1.25 | -0.57 | -0.71 | +0.03 | +0.01 | -0.01 |
| M1.1 | B → C | -24.55 | +0.47 | -0.70 | -24.30 | +0.01 | -0.02 |
| M2 | C → Cp | -1.14 | -0.10 | -1.01 | -0.02 | +0.00 | -0.01 |

C's process CPU in the three sessions (not one scale): M1 18.56 s, M1.1 19.49 s, M2 18.74 s; (max − min) / min = 5.0%.
B's in M1 and M1.1: 43.28 s, 44.04 s; (max − min) / min = 1.7%.

## 5 channels × 30 fps

| Study | Arm | Process CPU | ÷ control | Decoder | NPU workers | Preprocessing | Rest | Threads' sum | Remainder | Decoder's share | Largest group (share) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| M1 | B | 129.59 ± 1.24 | control | 8.34 ± 0.43 | 29.20 ± 0.53 | 91.92 ± 0.33 | 0.12 ± 0.00 | 129.57 ± 1.22 | 0.01 ± 0.02 | 6.4% | Preprocessing (70.9%) |
| M1 | C | 35.68 ± 0.49 | 0.275 | 8.44 ± 0.65 | 25.91 ± 0.15 | 1.18 ± 0.07 | 0.14 ± 0.01 | 35.68 ± 0.48 | 0.00 ± 0.03 | 23.7% | NPU workers (72.6%) |
| M1.1 | B | 130.95 ± 0.56 | control | 8.24 ± 0.12 | 30.49 ± 0.17 | 92.06 ± 0.46 | 0.13 ± 0.02 | 130.92 ± 0.54 | 0.03 ± 0.03 | 6.3% | Preprocessing (70.3%) |
| M1.1 | Bd | 129.85 ± 0.66 | 0.992 | 8.01 ± 0.30 | 28.61 ± 0.52 | 93.08 ± 0.10 | 0.12 ± 0.01 | 129.82 ± 0.64 | 0.03 ± 0.03 | 6.2% | Preprocessing (71.7%) |
| M1.1 | C | 37.45 ± 1.41 | 0.286 | 8.42 ± 0.72 | 27.65 ± 0.69 | 1.23 ± 0.04 | 0.14 ± 0.01 | 37.44 ± 1.43 | 0.00 ± 0.02 | 22.5% | NPU workers (73.8%) |
| M2 | C | 37.28 ± 0.53 | control | 8.74 ± 0.25 | 27.10 ± 0.46 | 1.31 ± 0.02 | 0.14 ± 0.01 | 37.29 ± 0.54 | -0.01 ± 0.01 | 23.4% | NPU workers (72.7%) |
| M2 | Cp | 31.79 ± 0.40 | 0.853 | 8.29 ± 0.29 | 22.15 ± 0.18 | 1.21 ± 0.04 | 0.14 ± 0.01 | 31.80 ± 0.38 | -0.01 ± 0.02 | 26.1% | NPU workers (69.7%) |

Change against the session's control, mean − mean (s):

| Study | Step | Process CPU | Decoder | NPU workers | Preprocessing | Rest | Remainder |
|---|---|---|---|---|---|---|---|
| M1 | B → C | -93.91 | +0.10 | -3.29 | -90.73 | +0.02 | -0.01 |
| M1.1 | B → Bd | -1.10 | -0.23 | -1.88 | +1.01 | -0.00 | -0.00 |
| M1.1 | B → C | -93.51 | +0.18 | -2.84 | -90.83 | +0.02 | -0.03 |
| M2 | C → Cp | -5.49 | -0.45 | -4.95 | -0.10 | +0.01 | +0.00 |

C's process CPU in the three sessions (not one scale): M1 35.68 s, M1.1 37.45 s, M2 37.28 s; (max − min) / min = 5.0%.
B's in M1 and M1.1: 129.59 s, 130.95 s; (max − min) / min = 1.1%.

## Against the published values

| Study | Point | Arm | Value | Published | This tool | Match | Source |
|---|---|---|---|---|---|---|---|
| M1 | 8 ch | B | Process CPU | 43.283 ± 0.343 | 43.283 ± 0.343 | yes | M1_tables.md |
| M1 | 8 ch | C | Process CPU | 18.560 ± 0.308 | 18.560 ± 0.308 | yes | M1_tables.md |
| M1 | 5 ch | B | Process CPU | 129.587 ± 1.236 | 129.587 ± 1.236 | yes | M1_tables.md |
| M1 | 5 ch | C | Process CPU | 35.680 ± 0.486 | 35.680 ± 0.486 | yes | M1_tables.md |
| M1.1 | 8 ch | B | Process CPU | 44.037 ± 0.268 | 44.037 ± 0.268 | yes | M1_tables.md (A12) |
| M1.1 | 8 ch | Bd | Process CPU | 42.787 ± 0.673 | 42.787 ± 0.673 | yes | M1_tables.md (A12) |
| M1.1 | 8 ch | C | Process CPU | 19.487 ± 0.257 | 19.487 ± 0.257 | yes | M1_tables.md (A12) |
| M1.1 | 5 ch | B | Process CPU | 130.953 ± 0.556 | 130.953 ± 0.556 | yes | M1_tables.md (A12) |
| M1.1 | 5 ch | Bd | Process CPU | 129.853 ± 0.655 | 129.853 ± 0.655 | yes | M1_tables.md (A12) |
| M1.1 | 5 ch | C | Process CPU | 37.447 ± 1.408 | 37.447 ± 1.408 | yes | M1_tables.md (A12) |
| M2 | 8 ch | C | Process CPU | 18.737 ± 0.170 | 18.737 ± 0.170 | yes | M2_tables.md; groups: M2.md |
| M2 | 8 ch | C | NPU workers (npu-*) | 6.83 ± 0.08 | 6.83 ± 0.08 | yes | M2_tables.md; groups: M2.md |
| M2 | 8 ch | C | Decoder (dec-*, mpp_*) | 11.33 ± 0.15 | 11.33 ± 0.15 | yes | M2_tables.md; groups: M2.md |
| M2 | 8 ch | C | Preprocessing (pre-*) | 0.45 | 0.45 | yes | M2_tables.md; groups: M2.md |
| M2 | 8 ch | Cp | Process CPU | 17.600 ± 0.560 | 17.600 ± 0.560 | yes | M2_tables.md; groups: M2.md |
| M2 | 8 ch | Cp | NPU workers (npu-*) | 5.82 ± 0.01 | 5.82 ± 0.01 | yes | M2_tables.md; groups: M2.md |
| M2 | 8 ch | Cp | Decoder (dec-*, mpp_*) | 11.24 ± 0.53 | 11.24 ± 0.53 | yes | M2_tables.md; groups: M2.md |
| M2 | 8 ch | Cp | Preprocessing (pre-*) | 0.43 | 0.43 | yes | M2_tables.md; groups: M2.md |
| M2 | 5 ch | C | Process CPU | 37.280 ± 0.528 | 37.280 ± 0.528 | yes | M2_tables.md; groups: M2.md |
| M2 | 5 ch | C | NPU workers (npu-*) | 27.10 ± 0.46 | 27.10 ± 0.46 | yes | M2_tables.md; groups: M2.md |
| M2 | 5 ch | C | Decoder (dec-*, mpp_*) | 8.74 ± 0.25 | 8.74 ± 0.25 | yes | M2_tables.md; groups: M2.md |
| M2 | 5 ch | C | Preprocessing (pre-*) | 1.31 | 1.31 | yes | M2_tables.md; groups: M2.md |
| M2 | 5 ch | Cp | Process CPU | 31.790 ± 0.401 | 31.790 ± 0.401 | yes | M2_tables.md; groups: M2.md |
| M2 | 5 ch | Cp | NPU workers (npu-*) | 22.15 ± 0.18 | 22.15 ± 0.18 | yes | M2_tables.md; groups: M2.md |
| M2 | 5 ch | Cp | Decoder (dec-*, mpp_*) | 8.29 ± 0.29 | 8.29 ± 0.29 | yes | M2_tables.md; groups: M2.md |
| M2 | 5 ch | Cp | Preprocessing (pre-*) | 1.21 | 1.21 | yes | M2_tables.md; groups: M2.md |
