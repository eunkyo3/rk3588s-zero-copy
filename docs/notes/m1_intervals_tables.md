# M1: paired differences and the spread between sessions (exploratory)

Computed after the fact (issue #67), once every run of M1, M1.1 and M2 had been seen. Nothing here changes a verdict. Valid runs only, per 120 s window. Method: `tools/m1_intervals.py`. The note is [m1_intervals.md](m1_intervals.md).

## Reproduction

Published values that this tool's code path computes too.

| Value | Published | Source | This tool | Same |
|---|---|---|---|---|
| M1, 8 ch main, B process CPU (s) | 43.283 ± 0.343 | [M1.md](../report/M1.md) | 43.283 ± 0.343 | yes |
| M1, 8 ch main, C process CPU (s) | 18.560 ± 0.308 | [M1.md](../report/M1.md) | 18.560 ± 0.308 | yes |
| M1, H1's saving (B − C) / B, from the means | 57.1% | [M1.md](../report/M1.md) | 57.1% | yes |
| M1, 5 ch × 30 fps, B p99 per repetition (ms) | 28.3, 23.2, 22.1 | [M1.md](../report/M1.md) | 28.3, 23.2, 22.1 | yes |
| M1, 5 ch × 30 fps, C p99 per repetition (ms) | 23.7, 23.4, 30.5 | [M1.md](../report/M1.md) | 23.7, 23.4, 30.5 | yes |
| M1.1 (bdirect_main), 8 ch main, C process CPU (s) | 19.49 ± 0.26 | [M1.1.md](../report/M1.1.md) | 19.49 ± 0.26 | yes |
| M2 (m2_main), 8 ch main, C process CPU (s) | 18.737 ± 0.170 | [M2.md](../report/M2.md) | 18.737 ± 0.170 | yes |
| M1, 5 ch × 30 fps, C process CPU (s) | 35.68 ± 0.49 | [M2.md](../report/M2.md) | 35.68 ± 0.49 | yes |
| M1.1 (bdirect_aux), 5 ch × 30 fps, C process CPU (s) | 37.45 ± 1.41 | [M1.1.md](../report/M1.1.md) | 37.45 ± 1.41 | yes |
| M2 (m2_aux), 5 ch × 30 fps, C process CPU (s) | 37.280 ± 0.528 | [M2.md](../report/M2.md) | 37.280 ± 0.528 | yes |
| M2-H1, (C − Cp) / C, from the means | 6.07% | [M2.md](../report/M2.md) | 6.07% | yes |
| M2-H1, (C_r − Cp_r) / C_r | 8.76%, 6.23%, 3.21% | [M2.md](../report/M2.md) | 8.76%, 6.23%, 3.21% | yes |
| M2-H1, 95% t-interval | -0.83% to 12.97% | [M2.md](../report/M2.md) | -0.83% to 12.97% | yes |
| M2-H2, (C − Cp) / C, from the means | 14.73% | [M2.md](../report/M2.md) | 14.73% | yes |
| M2-H2, (C_r − Cp_r) / C_r | 13.57%, 15.36%, 15.23% | [M2.md](../report/M2.md) | 13.57%, 15.36%, 15.23% | yes |
| M2-H2, 95% t-interval | 12.24% to 17.20% | [M2.md](../report/M2.md) | 12.24% to 17.20% | yes |

## Paired differences of B against C

B_r against C_r of the same repetition r. Both ran in the same session, in alternating order (B→C, C→B, B→C). Process CPU as H1's saving, (B_r − C_r) / B_r. Latency as B_r − C_r in ms, positive where C is lower; its mean is the difference of the means that H2 compares. Each with a descriptive 95% t-interval of the mean of the differences (`tools/m2_report.py`'s `t_interval`; t = 4.303 for 3 pairs). Reported, not tested.

### main: process CPU

| Channels | B (s) | C (s) | (B − C) / B, from the means | (B_r − C_r) / B_r: r1, r2, r3 | Mean | 95% t-interval | Interval |
|---|---|---|---|---|---|---|---|
| 1 | 4.957 ± 0.015 (3) | 2.150 ± 0.040 (3) | 56.6% | 57.3%, 55.8%, 56.7% | 56.6% | 54.8% to 58.4% | above 0 |
| 2 | 10.133 ± 0.040 (3) | 4.350 ± 0.202 (3) | 57.1% | 59.4%, 55.4%, 56.4% | 57.1% | 52.0% to 62.2% | above 0 |
| 4 | 20.867 ± 0.372 (3) | 8.827 ± 0.276 (3) | 57.7% | 59.6%, 56.0%, 57.4% | 57.7% | 53.2% to 62.2% | above 0 |
| 8 | 43.283 ± 0.343 (3) | 18.560 ± 0.308 (3) | 57.1% | 58.0%, 56.9%, 56.5% | 57.1% | 55.1% to 59.1% | above 0 |
| 12 | 66.260 ± 0.480 (3) | 30.077 ± 0.430 (3) | 54.6% | 54.3%, 54.3%, 55.3% | 54.6% | 53.2% to 56.0% | above 0 |
| 16 | 90.360 ± 0.450 (3) | 41.187 ± 0.609 (3) | 54.4% | 54.3%, 55.3%, 53.7% | 54.4% | 52.3% to 56.5% | above 0 |
| 32 | 200.517 ± 0.241 (3) | 100.620 ± 1.325 (3) | 49.8% | 49.4%, 50.6%, 49.4% | 49.8% | 48.1% to 51.6% | above 0 |

Intervals: above 0 at 1, 2, 4, 8, 12, 16, 32 ch.

### main: p50 latency

| Channels | B (ms) | C (ms) | B_r − C_r (ms): r1, r2, r3 | Mean | 95% t-interval | Interval |
|---|---|---|---|---|---|---|
| 1 | 21.9 ± 0.1 (3) | 18.1 ± 0.4 (3) | 4.2, 3.6, 3.5 | 3.8 | 2.9 to 4.6 | above 0 |
| 2 | 21.8 ± 0.0 (3) | 17.0 ± 0.1 (3) | 4.8, 4.8, 4.7 | 4.8 | 4.7 to 4.9 | above 0 |
| 4 | 21.0 ± 0.1 (3) | 17.6 ± 0.2 (3) | 3.3, 3.2, 3.5 | 3.4 | 3.0 to 3.7 | above 0 |
| 8 | 33.1 ± 4.5 (3) | 30.3 ± 1.0 (3) | 6.1, 5.4, -3.1 | 2.8 | -10.0 to 15.5 | spans 0 |
| 12 | 36.1 ± 0.7 (3) | 32.2 ± 1.7 (3) | 1.6, 4.6, 5.7 | 4.0 | -1.3 to 9.2 | spans 0 |
| 16 | 41.5 ± 3.0 (3) | 31.1 ± 2.4 (3) | 8.5, 10.4, 12.3 | 10.4 | 5.6 to 15.2 | above 0 |
| 32 | 51.3 ± 2.5 (3) | 68.1 ± 0.7 (3) | -14.8, -17.8, -17.9 | -16.8 | -21.3 to -12.4 | below 0 |

Intervals: above 0 at 1, 2, 4, 16 ch; below 0 at 32 ch; spans 0 at 8, 12 ch.

### main: p99 latency

| Channels | B (ms) | C (ms) | B_r − C_r (ms): r1, r2, r3 | Mean | 95% t-interval | Interval |
|---|---|---|---|---|---|---|
| 1 | 22.2 ± 0.1 (3) | 18.8 ± 0.1 (3) | 3.4, 3.6, 3.4 | 3.4 | 3.2 to 3.7 | above 0 |
| 2 | 22.4 ± 0.0 (3) | 18.9 ± 0.1 (3) | 3.6, 3.5, 3.5 | 3.5 | 3.4 to 3.7 | above 0 |
| 4 | 32.4 ± 1.9 (3) | 22.7 ± 3.2 (3) | 13.8, 4.3, 11.2 | 9.8 | -2.4 to 21.9 | spans 0 |
| 8 | 47.4 ± 5.5 (3) | 44.0 ± 1.9 (3) | 8.6, 6.4, -4.7 | 3.4 | -14.3 to 21.2 | spans 0 |
| 12 | 49.7 ± 1.0 (3) | 48.3 ± 4.6 (3) | -3.8, 1.4, 6.8 | 1.5 | -11.8 to 14.7 | spans 0 |
| 16 | 66.6 ± 11.7 (3) | 61.6 ± 4.5 (3) | -13.7, 14.2, 14.5 | 5.0 | -35.2 to 45.2 | spans 0 |
| 32 | 120.0 ± 8.0 (3) | 143.3 ± 3.3 (3) | -30.4, -24.5, -14.7 | -23.2 | -42.9 to -3.6 | below 0 |

Intervals: above 0 at 1, 2 ch; below 0 at 32 ch; spans 0 at 4, 8, 12, 16 ch.

### aux: process CPU

| Channels | B (s) | C (s) | (B − C) / B, from the means | (B_r − C_r) / B_r: r1, r2, r3 | Mean | 95% t-interval | Interval |
|---|---|---|---|---|---|---|---|
| 1 | 24.173 ± 0.169 (3) | 6.437 ± 0.038 (3) | 73.4% | 73.3%, 73.4%, 73.4% | 73.4% | 73.3% to 73.5% | above 0 |
| 2 | 49.663 ± 0.254 (3) | 13.437 ± 0.198 (3) | 72.9% | 73.1%, 72.6%, 73.1% | 72.9% | 72.2% to 73.7% | above 0 |
| 3 | 75.323 ± 0.483 (3) | 20.867 ± 0.335 (3) | 72.3% | 72.2%, 71.8%, 72.9% | 72.3% | 70.9% to 73.7% | above 0 |
| 4 | 101.627 ± 0.544 (3) | 27.863 ± 0.190 (3) | 72.6% | 72.5%, 72.6%, 72.6% | 72.6% | 72.4% to 72.7% | above 0 |
| 5 | 129.587 ± 1.236 (3) | 35.680 ± 0.486 (3) | 72.5% | 72.5%, 73.0%, 71.9% | 72.5% | 71.2% to 73.7% | above 0 |

Intervals: above 0 at 1, 2, 3, 4, 5 ch.

### aux: p50 latency

| Channels | B (ms) | C (ms) | B_r − C_r (ms): r1, r2, r3 | Mean | 95% t-interval | Interval |
|---|---|---|---|---|---|---|
| 1 | 21.4 ± 0.3 (3) | 16.6 ± 0.0 (3) | 4.8, 5.1, 4.5 | 4.8 | 4.1 to 5.5 | above 0 |
| 2 | 21.0 ± 0.4 (3) | 16.8 ± 0.0 (3) | 4.5, 3.6, 4.3 | 4.2 | 3.0 to 5.3 | above 0 |
| 3 | 20.7 ± 0.2 (3) | 17.2 ± 0.1 (3) | 3.4, 3.5, 3.7 | 3.5 | 3.1 to 4.0 | above 0 |
| 4 | 21.5 ± 0.3 (3) | 17.3 ± 0.1 (3) | 3.9, 4.1, 4.6 | 4.2 | 3.4 to 5.0 | above 0 |
| 5 | 21.3 ± 0.5 (3) | 18.1 ± 0.8 (3) | 3.5, 4.1, 1.8 | 3.1 | 0.2 to 6.0 | above 0 |

Intervals: above 0 at 1, 2, 3, 4, 5 ch.

### aux: p99 latency

| Channels | B (ms) | C (ms) | B_r − C_r (ms): r1, r2, r3 | Mean | 95% t-interval | Interval |
|---|---|---|---|---|---|---|
| 1 | 22.1 ± 0.0 (3) | 18.7 ± 0.0 (3) | 3.3, 3.4, 3.5 | 3.4 | 3.2 to 3.7 | above 0 |
| 2 | 22.4 ± 0.1 (3) | 18.8 ± 0.1 (3) | 3.7, 3.5, 3.7 | 3.6 | 3.3 to 3.9 | above 0 |
| 3 | 22.6 ± 0.2 (3) | 18.4 ± 0.6 (3) | 4.7, 4.0, 3.8 | 4.2 | 3.0 to 5.4 | above 0 |
| 4 | 31.6 ± 1.4 (3) | 21.8 ± 5.3 (3) | 13.1, 2.1, 14.2 | 9.8 | -6.8 to 26.4 | spans 0 |
| 5 | 24.5 ± 3.3 (3) | 25.9 ± 4.0 (3) | 4.7, -0.2, -8.4 | -1.3 | -17.7 to 15.0 | spans 0 |

Intervals: above 0 at 1, 2, 3 ch; spans 0 at 4, 5 ch.

**H3:** its search ran one run per count and variant, so it has no interval. Its confirmation ran 32 channels three times per variant to check that the count is sustained; the main sweep covers 32 channels with 3 repetitions, above.

## The spread between sessions

B and C at the same point in different sessions. Mean ± sample sd (n). The sessions differ in their binary (code commit), and M2's main session in its sampler (stagger on). M2 ran C and Cp, no B; k4 ran B and Bk4, no C. Latency spreads leave out sessions with the stagger on.

### 8 channels, main condition

| Milestone | Session | Code | Stagger | B process CPU (s) | C process CPU (s) | B p50 (ms) | C p50 (ms) | B p99 (ms) | C p99 (ms) |
|---|---|---|---|---|---|---|---|---|---|
| M1 | `20261103T073439Z_main` | 86587a2 | – | 43.283 ± 0.343 (3) | 18.560 ± 0.308 (3) | 33.1 ± 4.5 (3) | 30.3 ± 1.0 (3) | 47.4 ± 5.5 (3) | 44.0 ± 1.9 (3) |
| M1.1 | `20261104T053416Z_k4` | cdb309e | – | 43.850 ± 0.274 (3) | – | 32.1 ± 3.4 (3) | – | 45.1 ± 2.4 (3) | – |
| M1.1 | `20261104T061958Z_bdirect_main` | cdb309e | – | 44.037 ± 0.268 (3) | 19.487 ± 0.257 (3) | 35.6 ± 1.4 (3) | 29.1 ± 0.5 (3) | 48.1 ± 1.0 (3) | 41.0 ± 4.1 (3) |
| M2 | `20261105T071518Z_m2_main` | 88206ab | on | – | 18.737 ± 0.170 (3) | – | 16.9 ± 0.0 (3) | – | 18.6 ± 0.9 (3) |

- **B, process CPU:** 43.283 (M1 `main`), 43.850 (M1.1 `k4`), 44.037 (M1.1 `bdirect_main`). The highest mean is 1.7% above the lowest. Within these sessions, the CV is at most 0.8%.
- **C, process CPU:** 18.560 (M1 `main`), 19.487 (M1.1 `bdirect_main`), 18.737 (M2 `m2_main`). The highest mean is 5.0% above the lowest. Within these sessions, the CV is at most 1.7%.
- **B, p50 latency:** 33.1 (M1 `main`), 32.1 (M1.1 `k4`), 35.6 (M1.1 `bdirect_main`). The highest mean is 11.2% above the lowest. Within these sessions, the CV is at most 13.7%.
- **C, p50 latency:** 30.3 (M1 `main`), 29.1 (M1.1 `bdirect_main`). The highest mean is 4.1% above the lowest. Within these sessions, the CV is at most 3.2%.
- **B, p99 latency:** 47.4 (M1 `main`), 45.1 (M1.1 `k4`), 48.1 (M1.1 `bdirect_main`). The highest mean is 6.5% above the lowest. Within these sessions, the CV is at most 11.7%.
- **C, p99 latency:** 44.0 (M1 `main`), 41.0 (M1.1 `bdirect_main`). The highest mean is 7.1% above the lowest. Within these sessions, the CV is at most 10.1%.

### 5 channels, 30 fps

| Milestone | Session | Code | Stagger | B process CPU (s) | C process CPU (s) | B p50 (ms) | C p50 (ms) | B p99 (ms) | C p99 (ms) |
|---|---|---|---|---|---|---|---|---|---|
| M1 | `20261103T101109Z_aux` | 86587a2 | – | 129.587 ± 1.236 (3) | 35.680 ± 0.486 (3) | 21.3 ± 0.5 (3) | 18.1 ± 0.8 (3) | 24.5 ± 3.3 (3) | 25.9 ± 4.0 (3) |
| M1.1 | `20261104T065947Z_bdirect_aux` | cdb309e | – | 130.953 ± 0.556 (3) | 37.447 ± 1.408 (3) | 22.6 ± 2.4 (3) | 17.7 ± 0.2 (3) | 30.5 ± 3.2 (3) | 22.8 ± 3.0 (3) |
| M2 | `20261105T074458Z_m2_aux` | 88206ab | off | – | 37.280 ± 0.528 (3) | – | 17.7 ± 0.1 (3) | – | 21.8 ± 4.8 (3) |

- **B, process CPU:** 129.587 (M1 `aux`), 130.953 (M1.1 `bdirect_aux`). The highest mean is 1.1% above the lowest. Within these sessions, the CV is at most 1.0%.
- **C, process CPU:** 35.680 (M1 `aux`), 37.447 (M1.1 `bdirect_aux`), 37.280 (M2 `m2_aux`). The highest mean is 5.0% above the lowest. Within these sessions, the CV is at most 3.8%.
- **B, p50 latency:** 21.3 (M1 `aux`), 22.6 (M1.1 `bdirect_aux`). The highest mean is 6.5% above the lowest. Within these sessions, the CV is at most 10.7%.
- **C, p50 latency:** 18.1 (M1 `aux`), 17.7 (M1.1 `bdirect_aux`), 17.7 (M2 `m2_aux`). The highest mean is 2.5% above the lowest. Within these sessions, the CV is at most 4.4%.
- **B, p99 latency:** 24.5 (M1 `aux`), 30.5 (M1.1 `bdirect_aux`). The highest mean is 24.1% above the lowest. Within these sessions, the CV is at most 13.5%.
- **C, p99 latency:** 25.9 (M1 `aux`), 22.8 (M1.1 `bdirect_aux`), 21.8 (M2 `m2_aux`). The highest mean is 18.5% above the lowest. Within these sessions, the CV is at most 22.2%.

## Repetitions for a given interval (guidance)

The fewest runs n whose 95% t-interval of a mean, ± t(n − 1) × CV / √n, is at most h of the mean, with the CV observed here taken as the true one; more than 100 is shown as "> 100". For the difference of two arms with that CV each, paired runs and independent errors, the sd of the difference is about √2 × CV of one arm's mean. Each CV comes from 3 runs, so it is rough itself.

### Coefficients of variation in M1

| Sweep | Channels | Process CPU, B | Process CPU, C | p50, B | p50, C | p99, B | p99, C |
|---|---|---|---|---|---|---|---|
| main | 1 | 0.3% | 1.9% | 0.3% | 2.3% | 0.2% | 0.3% |
| main | 2 | 0.4% | 4.6% | 0.2% | 0.5% | 0.0% | 0.3% |
| main | 4 | 1.8% | 3.1% | 0.5% | 1.1% | 5.8% | 14.0% |
| main | 8 | 0.8% | 1.7% | 13.7% | 3.2% | 11.7% | 4.2% |
| main | 12 | 0.7% | 1.4% | 2.0% | 5.4% | 2.1% | 9.5% |
| main | 16 | 0.5% | 1.5% | 7.2% | 7.8% | 17.6% | 7.4% |
| main | 32 | 0.1% | 1.3% | 4.8% | 1.0% | 6.6% | 2.3% |
| aux | 1 | 0.7% | 0.6% | 1.3% | 0.1% | 0.2% | 0.3% |
| aux | 2 | 0.5% | 1.5% | 2.1% | 0.1% | 0.3% | 0.6% |
| aux | 3 | 0.6% | 1.6% | 0.8% | 0.6% | 0.9% | 3.0% |
| aux | 4 | 0.5% | 0.7% | 1.5% | 0.3% | 4.5% | 24.3% |
| aux | 5 | 1.0% | 1.4% | 2.1% | 4.4% | 13.5% | 15.5% |

M1 has 24 points and arms (main and aux sweeps, B and C). p99 latency's CV is above 10% at 6 of them.

### Runs for a half-width h of the mean

| Variation | CV | One arm, h = 2%, 5%, 10% | Difference (√2 × CV), h = 2%, 5%, 10% |
|---|---|---|---|
| p99 latency, median over M1's points and arms | 3.6% | 16, 5, 3 | 28, 7, 4 |
| p99 latency, largest in M1 | 24.3% | > 100, 94, 26 | > 100, > 100, 48 |
| p50 latency, median over M1's points and arms | 1.4% | 5, 3, 3 | 7, 3, 3 |
| p50 latency, largest in M1 | 13.7% | > 100, 32, 10 | > 100, 61, 17 |
| p99 latency, M2's 8 ch main (stagger on), larger of C and Cp | 4.6% | 23, 6, 4 | 43, 9, 5 |
| p99 latency, M2's 5 ch × 30 fps, larger of C and Cp | 22.2% | > 100, 79, 22 | > 100, > 100, 41 |
| process CPU, largest in M1 (for comparison) | 4.6% | 24, 6, 4 | 44, 10, 5 |
