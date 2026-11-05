# M2: C against Cp

From the sessions of `sweep m2_main` and `sweep m2_aux` ([prereg/M2.md](../../prereg/M2.md)). Values are the mean ± sample sd (valid runs) per 120 s window. Method: `tools/m2_report.py`.

## Verdicts

| Hypothesis | C (s) | Cp (s) | (C − Cp) / C | Threshold | Spec-literal | Practical | Secondary: schedstat C / Cp (s) | Mixed |
|---|---|---|---|---|---|---|---|---|
| M2-H1 | 18.737 | 17.600 | 6.07% | 3% | adopted | adopted | 20.639 / 19.361 | no |
| M2-H2 | 37.280 | 31.790 | 14.73% | 5% | adopted | adopted | 40.389 / 35.004 | no |

**M2-H1:** paired differences (C_r − Cp_r) / C_r: r1 8.76%, r2 6.23%, r3 3.21%; mean 6.07%, descriptive 95% t-interval -0.83% to 12.97%. CV of process CPU: C 0.91%, Cp 3.18%; flagged above 1.5%: Cp.

**M2-H2:** paired differences (C_r − Cp_r) / C_r: r1 13.57%, r2 15.36%, r3 15.23%; mean 14.72%, descriptive 95% t-interval 12.24% to 17.20%. CV of process CPU: C 1.42%, Cp 1.26%; none above 2.5%.

## m2_main: 8 channels, main condition (M2-H1)

| Metric | C | Cp |
|---|---|---|
| Process CPU (s) | 18.737 ± 0.170 (3) | 17.600 ± 0.560 (3) |
| Process CPU, A76 (s) | 18.610 ± 0.149 (3) | 17.483 ± 0.548 (3) |
| Process CPU, A55 (s) | 0.110 ± 0.000 (3) | 0.113 ± 0.012 (3) |
| Process CPU, unattributed (s) | 0.017 ± 0.021 (3) | 0.003 ± 0.006 (3) |
| System CPU, /proc/schedstat, minus idle (s) | 20.639 ± 0.238 (3) | 19.361 ± 0.622 (3) |
| System CPU, /proc/stat, minus idle (s) | 21.595 ± 10.805 (3) | 17.671 ± 15.415 (3) |
| Post-processing stage p50 (ms) | 0.346 ± 0.002 (3) | 0.120 ± 0.002 (3) |
| Latency p50 (ms) | 16.9 ± 0.0 (3) | 16.7 ± 0.0 (3) |
| Latency p99 (ms) | 18.6 ± 0.9 (3) | 18.3 ± 0.7 (3) |
| Drop rate | 0.00000 ± 0.00000 (3) | 0.00000 ± 0.00000 (3) |
| Inference rate, lowest channel (fps) | 5.00 ± 0.00 (3) | 5.00 ± 0.00 (3) |
| Arrival phases' spread (ms) | 161.4 ± 0.6 (3) | 158.6 ± 4.3 (3) |
| Stagger error (ms) | 7.0 ± 2.2 (3) | 7.3 ± 3.1 (3) |

| Repetition | Arm | Run | Process CPU (s) | schedstat − idle (s) | Post p50 (ms) | p99 (ms) | Spread (ms) |
|---|---|---|---|---|---|---|---|
| 1 | C | `20261105T071518Z_m2_main/01_m2_main_C_8ch_r1` | 18.610 | 20.501 | 0.345 | 17.6 | 161.1 |
| 1 | Cp | `20261105T071518Z_m2_main/02_m2_main_Cp_8ch_r1` | 16.980 | 18.645 | 0.123 | 18.2 | 153.8 |
| 2 | C | `20261105T071518Z_m2_main/04_m2_main_C_8ch_r2` | 18.930 | 20.914 | 0.345 | 19.2 | 161.0 |
| 2 | Cp | `20261105T071518Z_m2_main/03_m2_main_Cp_8ch_r2` | 17.750 | 19.777 | 0.119 | 17.6 | 162.1 |
| 3 | C | `20261105T071518Z_m2_main/05_m2_main_C_8ch_r3` | 18.670 | 20.501 | 0.348 | 19.0 | 162.0 |
| 3 | Cp | `20261105T071518Z_m2_main/06_m2_main_Cp_8ch_r3` | 18.070 | 19.659 | 0.119 | 19.0 | 160.0 |

**Detection counts, C against Cp, frames both runs processed (§4, no verdict):**

- r1: 653 video frames compared, 314 with different counts; 0 with more than one count within a run.
- r2: 1839 video frames compared, 0 with different counts; 0 with more than one count within a run.
- r3: 1029 video frames compared, 306 with different counts; 0 with more than one count within a run.

**Investigated (exploratory, after the fact):** pts names the same video frame only within a run; its origin differs between runs. Shifting each channel's Cp frames by the one shift that agrees most, over 24 channel-repetitions: at that shift, the lowest share of compared frames whose counts agree is 100%; at any other shift, the highest is 44%. Shifts: -15, -13, -12, -11, -3, -2, +0, +3, +4, +7, +14, +15 frames.

**M2's C next to earlier C (exploratory):** M2 18.737 ± 0.170 (3) s; M1 18.560 ± 0.308 s; M1.1 19.490 ± 0.260 s. The main condition's sampler and the sessions differ.

## m2_aux: 5 channels, aux condition (M2-H2)

| Metric | C | Cp |
|---|---|---|
| Process CPU (s) | 37.280 ± 0.528 (3) | 31.790 ± 0.401 (3) |
| Process CPU, A76 (s) | 37.153 ± 0.534 (3) | 31.653 ± 0.380 (3) |
| Process CPU, A55 (s) | 0.137 ± 0.006 (3) | 0.143 ± 0.006 (3) |
| Process CPU, unattributed (s) | -0.010 ± 0.010 (3) | -0.007 ± 0.021 (3) |
| System CPU, /proc/schedstat, minus idle (s) | 40.389 ± 0.393 (3) | 35.004 ± 0.540 (3) |
| System CPU, /proc/stat, minus idle (s) | 48.160 ± 25.757 (3) | 32.750 ± 14.453 (3) |
| Post-processing stage p50 (ms) | 0.361 ± 0.004 (3) | 0.121 ± 0.004 (3) |
| Latency p50 (ms) | 17.7 ± 0.1 (3) | 18.0 ± 0.7 (3) |
| Latency p99 (ms) | 21.8 ± 4.8 (3) | 24.8 ± 2.5 (3) |
| Drop rate | 0.00000 ± 0.00000 (3) | 0.00000 ± 0.00000 (3) |
| Inference rate, lowest channel (fps) | 30.00 ± 0.00 (3) | 30.00 ± 0.00 (3) |
| Arrival phases' spread (ms) | 16.3 ± 0.3 (3) | 11.4 ± 4.6 (3) |

| Repetition | Arm | Run | Process CPU (s) | schedstat − idle (s) | Post p50 (ms) | p99 (ms) | Spread (ms) |
|---|---|---|---|---|---|---|---|
| 1 | C | `20261105T074458Z_m2_aux/01_m2_aux_C_5ch_r1` | 36.990 | 40.249 | 0.361 | 18.6 | 16.5 |
| 1 | Cp | `20261105T074458Z_m2_aux/02_m2_aux_Cp_5ch_r1` | 31.970 | 34.880 | 0.120 | 27.5 | 6.2 |
| 2 | C | `20261105T074458Z_m2_aux/04_m2_aux_C_5ch_r2` | 37.890 | 40.833 | 0.365 | 19.4 | 16.5 |
| 2 | Cp | `20261105T074458Z_m2_aux/03_m2_aux_Cp_5ch_r2` | 32.070 | 35.595 | 0.125 | 24.3 | 13.6 |
| 3 | C | `20261105T074458Z_m2_aux/05_m2_aux_C_5ch_r3` | 36.960 | 40.085 | 0.358 | 27.4 | 16.0 |
| 3 | Cp | `20261105T074458Z_m2_aux/06_m2_aux_Cp_5ch_r3` | 31.330 | 34.537 | 0.118 | 22.5 | 14.5 |

**Detection counts, C against Cp, frames both runs processed (§4, no verdict):**

- r1: 4786 video frames compared, 1981 with different counts; 0 with more than one count within a run.
- r2: 4786 video frames compared, 2086 with different counts; 0 with more than one count within a run.
- r3: 4786 video frames compared, 2715 with different counts; 0 with more than one count within a run.

**Investigated (exploratory, after the fact):** pts names the same video frame only within a run; its origin differs between runs. Shifting each channel's Cp frames by the one shift that agrees most, over 15 channel-repetitions: at that shift, the lowest share of compared frames whose counts agree is 100%; at any other shift, the highest is 53%. Shifts: -15, -14, -5, -4, -3, -2, +0, +1, +9 frames.

**M2's C next to earlier C (exploratory):** M2 37.280 ± 0.528 (3) s; M1 35.680 ± 0.486 s; M1.1 37.450 ± 1.410 s. The main condition's sampler and the sessions differ.
