# M1 results

## Conditions

- The pipeline runs on the four A76 cores (cpu4-7); the metrics thread on cpu0 (PROTOCOL §8).
- The decoder's buffers are CPU-cached (AMENDMENTS A1, PROTOCOL §5.1).
- B and C are equivalent under A5's criteria on video 1 (A5), and on the held-out videos 2, 3 and 4 too: the check of A8 passed, pooled and per video.
- Every C result carries this label (A6): "input path without CPU pixel copies; §11.4 perf criterion not met because of about 1,150 small copies (about 15 KB) per inference inside the NPU runtime, as in B".
- Open question (A6): the NPU's native outputs are NC1HWC2 and both variants bind them as NCHW INT8, about 1.2 MB per inference; binding the native layout fails, which suggests the runtime converts the outputs on the CPU, with code other than memcpy or memmove.

## Completeness

Every point is complete, and H3 is done.

## Verdicts

| Hypothesis | Spec-literal | Practical | Numbers |
|---|---|---|---|
| H1, CPU at 8 channels | adopted | adopted | B 43.283 s, C 18.560 s, saving 57.1% |
| H2, p99 at 5 channels, 30 fps | rejected | rejected | B 24.5 ms, C 25.9 ms; drop rate B 0.00000, C 0.00000 |
| H3, maximum channels | adopted | indeterminate | n_B 32, n_C 32 |

The H2 auxiliary point is 5 channels (A7), so H2's secondary verdicts are the ones above.

- H3: ceiling reached: both at least L - 1.

## Sweep: main

| Channels | Metric | B: mean ± sd (n) | C (A6 label): mean ± sd (n) |
|---|---|---|---|
| 1 | process CPU (s) | 4.957 ± 0.015 (3) | 2.150 ± 0.040 (3) |
| 1 | process CPU, A76 (s) | 4.853 ± 0.006 (3) | 2.043 ± 0.042 (3) |
| 1 | process CPU, A55 (s) | 0.097 ± 0.006 (3) | 0.097 ± 0.006 (3) |
| 1 | process CPU, unattributed (s) | 0.007 ± 0.015 (3) | 0.010 ± 0.017 (3) |
| 1 | system cpu0 (s) | 0.777 ± 0.352 (3) | 0.483 ± 0.283 (3) |
| 1 | system cpu1 (s) | 0.103 ± 0.025 (3) | 0.117 ± 0.031 (3) |
| 1 | system cpu2 (s) | 0.107 ± 0.021 (3) | 0.083 ± 0.025 (3) |
| 1 | system cpu3 (s) | 0.120 ± 0.040 (3) | 0.103 ± 0.023 (3) |
| 1 | system cpu4 (s) | 0.423 ± 0.370 (3) | 0.107 ± 0.176 (3) |
| 1 | system cpu5 (s) | 1.780 ± 1.691 (3) | 0.267 ± 0.297 (3) |
| 1 | system cpu6 (s) | 0.890 ± 0.963 (3) | 0.303 ± 0.086 (3) |
| 1 | system cpu7 (s) | 0.573 ± 0.351 (3) | 0.400 ± 0.308 (3) |
| 1 | system cpu0-3 (s) | 1.107 ± 0.378 (3) | 0.787 ± 0.272 (3) |
| 1 | system cpu4-5 (s) | 2.203 ± 1.690 (3) | 0.373 ± 0.267 (3) |
| 1 | system cpu6-7 (s) | 1.463 ± 0.655 (3) | 0.703 ± 0.270 (3) |
| 1 | system cpu total (s) | 4.773 ± 1.439 (3) | 1.863 ± 0.566 (3) |
| 1 | system cpu0 minus idle (s) | 0.207 ± 0.352 (3) | -0.087 ± 0.283 (3) |
| 1 | system cpu1 minus idle (s) | 0.003 ± 0.025 (3) | 0.017 ± 0.031 (3) |
| 1 | system cpu2 minus idle (s) | 0.057 ± 0.021 (3) | 0.033 ± 0.025 (3) |
| 1 | system cpu3 minus idle (s) | 0.080 ± 0.040 (3) | 0.063 ± 0.023 (3) |
| 1 | system cpu4 minus idle (s) | 0.423 ± 0.370 (3) | 0.107 ± 0.176 (3) |
| 1 | system cpu5 minus idle (s) | 1.770 ± 1.691 (3) | 0.257 ± 0.297 (3) |
| 1 | system cpu6 minus idle (s) | 0.840 ± 0.963 (3) | 0.253 ± 0.086 (3) |
| 1 | system cpu7 minus idle (s) | 0.563 ± 0.351 (3) | 0.390 ± 0.308 (3) |
| 1 | system cpu0-3 minus idle (s) | 0.347 ± 0.378 (3) | 0.027 ± 0.272 (3) |
| 1 | system cpu4-5 minus idle (s) | 2.193 ± 1.690 (3) | 0.363 ± 0.267 (3) |
| 1 | system cpu6-7 minus idle (s) | 1.403 ± 0.655 (3) | 0.643 ± 0.270 (3) |
| 1 | system cpu total minus idle (s) | 3.943 ± 1.439 (3) | 1.033 ± 0.566 (3) |
| 1 | p50 latency (ms) | 21.882 ± 0.063 (3) | 18.132 ± 0.422 (3) |
| 1 | p99 latency (ms) | 22.226 ± 0.050 (3) | 18.788 ± 0.061 (3) |
| 1 | p99 latency, bench_main's (ms) | 22.226 ± 0.050 (3) | 18.788 ± 0.061 (3) |
| 1 | MPP hold p50 (ms) | 4.964 ± 0.078 (3) | 1.715 ± 0.004 (3) |
| 1 | MPP hold p99 (ms) | 5.021 ± 0.012 (3) | 1.731 ± 0.009 (3) |
| 1 | slot wait p99 (ms) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) |
| 1 | inference rate, lowest channel (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 1 | inference rate, channel mean (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 1 | inference rate, highest channel (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 1 | drop rate | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 1 | decoder drops | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 1 | largest drift (s) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 1 | largest RSS (kB) | 89117.333 ± 411.015 (3) | 87865.333 ± 374.045 (3) |
| 1 | highest temperature (m°C) | 40076.667 ± 532.894 (3) | 39769.000 ± 0.000 (3) |
| 2 | process CPU (s) | 10.133 ± 0.040 (3) | 4.350 ± 0.202 (3) |
| 2 | process CPU, A76 (s) | 10.017 ± 0.031 (3) | 4.250 ± 0.208 (3) |
| 2 | process CPU, A55 (s) | 0.110 ± 0.000 (3) | 0.100 ± 0.000 (3) |
| 2 | process CPU, unattributed (s) | 0.007 ± 0.015 (3) | 0.000 ± 0.026 (3) |
| 2 | system cpu0 (s) | 1.150 ± 0.465 (3) | 0.867 ± 0.356 (3) |
| 2 | system cpu1 (s) | 0.427 ± 0.127 (3) | 0.263 ± 0.162 (3) |
| 2 | system cpu2 (s) | 0.147 ± 0.074 (3) | 0.207 ± 0.115 (3) |
| 2 | system cpu3 (s) | 0.150 ± 0.217 (3) | 0.103 ± 0.059 (3) |
| 2 | system cpu4 (s) | 2.250 ± 1.024 (3) | 0.447 ± 0.193 (3) |
| 2 | system cpu5 (s) | 2.373 ± 0.909 (3) | 1.073 ± 0.979 (3) |
| 2 | system cpu6 (s) | 1.500 ± 0.603 (3) | 0.733 ± 0.333 (3) |
| 2 | system cpu7 (s) | 1.960 ± 0.504 (3) | 1.173 ± 1.403 (3) |
| 2 | system cpu0-3 (s) | 1.873 ± 0.864 (3) | 1.440 ± 0.656 (3) |
| 2 | system cpu4-5 (s) | 4.623 ± 1.925 (3) | 1.520 ± 0.985 (3) |
| 2 | system cpu6-7 (s) | 3.460 ± 1.090 (3) | 1.907 ± 1.718 (3) |
| 2 | system cpu total (s) | 9.957 ± 3.874 (3) | 4.867 ± 3.233 (3) |
| 2 | system cpu0 minus idle (s) | 0.580 ± 0.465 (3) | 0.297 ± 0.356 (3) |
| 2 | system cpu1 minus idle (s) | 0.327 ± 0.127 (3) | 0.163 ± 0.162 (3) |
| 2 | system cpu2 minus idle (s) | 0.097 ± 0.074 (3) | 0.157 ± 0.115 (3) |
| 2 | system cpu3 minus idle (s) | 0.110 ± 0.217 (3) | 0.063 ± 0.059 (3) |
| 2 | system cpu4 minus idle (s) | 2.250 ± 1.024 (3) | 0.447 ± 0.193 (3) |
| 2 | system cpu5 minus idle (s) | 2.363 ± 0.909 (3) | 1.063 ± 0.979 (3) |
| 2 | system cpu6 minus idle (s) | 1.450 ± 0.603 (3) | 0.683 ± 0.333 (3) |
| 2 | system cpu7 minus idle (s) | 1.950 ± 0.504 (3) | 1.163 ± 1.403 (3) |
| 2 | system cpu0-3 minus idle (s) | 1.113 ± 0.864 (3) | 0.680 ± 0.656 (3) |
| 2 | system cpu4-5 minus idle (s) | 4.613 ± 1.925 (3) | 1.510 ± 0.985 (3) |
| 2 | system cpu6-7 minus idle (s) | 3.400 ± 1.090 (3) | 1.847 ± 1.718 (3) |
| 2 | system cpu total minus idle (s) | 9.127 ± 3.874 (3) | 4.037 ± 3.233 (3) |
| 2 | p50 latency (ms) | 21.769 ± 0.035 (3) | 16.961 ± 0.091 (3) |
| 2 | p99 latency (ms) | 22.444 ± 0.006 (3) | 18.911 ± 0.060 (3) |
| 2 | p99 latency, bench_main's (ms) | 22.444 ± 0.006 (3) | 18.911 ± 0.060 (3) |
| 2 | MPP hold p50 (ms) | 5.033 ± 0.010 (3) | 1.719 ± 0.005 (3) |
| 2 | MPP hold p99 (ms) | 5.120 ± 0.088 (3) | 1.751 ± 0.002 (3) |
| 2 | slot wait p99 (ms) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) |
| 2 | inference rate, lowest channel (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 2 | inference rate, channel mean (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 2 | inference rate, highest channel (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 2 | drop rate | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 2 | decoder drops | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 2 | largest drift (s) | 0.011 ± 0.019 (3) | 0.000 ± 0.000 (3) |
| 2 | largest RSS (kB) | 110945.333 ± 6.110 (3) | 109377.333 ± 286.784 (3) |
| 2 | highest temperature (m°C) | 41307.333 ± 1921.378 (3) | 39769.000 ± 0.000 (3) |
| 4 | process CPU (s) | 20.867 ± 0.372 (3) | 8.827 ± 0.276 (3) |
| 4 | process CPU, A76 (s) | 20.757 ± 0.394 (3) | 8.723 ± 0.252 (3) |
| 4 | process CPU, A55 (s) | 0.100 ± 0.000 (3) | 0.097 ± 0.006 (3) |
| 4 | process CPU, unattributed (s) | 0.010 ± 0.026 (3) | 0.007 ± 0.031 (3) |
| 4 | system cpu0 (s) | 1.913 ± 0.551 (3) | 1.160 ± 0.265 (3) |
| 4 | system cpu1 (s) | 0.657 ± 0.310 (3) | 0.557 ± 0.142 (3) |
| 4 | system cpu2 (s) | 0.370 ± 0.118 (3) | 0.170 ± 0.142 (3) |
| 4 | system cpu3 (s) | 0.077 ± 0.057 (3) | 0.100 ± 0.053 (3) |
| 4 | system cpu4 (s) | 5.257 ± 1.875 (3) | 1.067 ± 0.212 (3) |
| 4 | system cpu5 (s) | 5.310 ± 1.918 (3) | 1.303 ± 0.155 (3) |
| 4 | system cpu6 (s) | 5.057 ± 1.813 (3) | 1.550 ± 0.440 (3) |
| 4 | system cpu7 (s) | 5.190 ± 1.668 (3) | 1.413 ± 0.408 (3) |
| 4 | system cpu0-3 (s) | 3.017 ± 0.142 (3) | 1.987 ± 0.147 (3) |
| 4 | system cpu4-5 (s) | 10.567 ± 3.553 (3) | 2.370 ± 0.095 (3) |
| 4 | system cpu6-7 (s) | 10.247 ± 3.414 (3) | 2.963 ± 0.832 (3) |
| 4 | system cpu total (s) | 23.830 ± 7.094 (3) | 7.320 ± 1.020 (3) |
| 4 | system cpu0 minus idle (s) | 1.343 ± 0.551 (3) | 0.590 ± 0.265 (3) |
| 4 | system cpu1 minus idle (s) | 0.557 ± 0.310 (3) | 0.457 ± 0.142 (3) |
| 4 | system cpu2 minus idle (s) | 0.320 ± 0.118 (3) | 0.120 ± 0.142 (3) |
| 4 | system cpu3 minus idle (s) | 0.037 ± 0.057 (3) | 0.060 ± 0.053 (3) |
| 4 | system cpu4 minus idle (s) | 5.257 ± 1.875 (3) | 1.067 ± 0.212 (3) |
| 4 | system cpu5 minus idle (s) | 5.300 ± 1.918 (3) | 1.293 ± 0.155 (3) |
| 4 | system cpu6 minus idle (s) | 5.007 ± 1.813 (3) | 1.500 ± 0.440 (3) |
| 4 | system cpu7 minus idle (s) | 5.180 ± 1.668 (3) | 1.403 ± 0.408 (3) |
| 4 | system cpu0-3 minus idle (s) | 2.257 ± 0.142 (3) | 1.227 ± 0.147 (3) |
| 4 | system cpu4-5 minus idle (s) | 10.557 ± 3.553 (3) | 2.360 ± 0.095 (3) |
| 4 | system cpu6-7 minus idle (s) | 10.187 ± 3.414 (3) | 2.903 ± 0.832 (3) |
| 4 | system cpu total minus idle (s) | 23.000 ± 7.094 (3) | 6.490 ± 1.020 (3) |
| 4 | p50 latency (ms) | 20.990 ± 0.115 (3) | 17.626 ± 0.192 (3) |
| 4 | p99 latency (ms) | 32.418 ± 1.880 (3) | 22.666 ± 3.165 (3) |
| 4 | p99 latency, bench_main's (ms) | 32.418 ± 1.880 (3) | 22.666 ± 3.165 (3) |
| 4 | MPP hold p50 (ms) | 5.099 ± 0.025 (3) | 1.684 ± 0.017 (3) |
| 4 | MPP hold p99 (ms) | 5.651 ± 0.184 (3) | 2.866 ± 0.965 (3) |
| 4 | slot wait p99 (ms) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) |
| 4 | inference rate, lowest channel (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 4 | inference rate, channel mean (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 4 | inference rate, highest channel (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 4 | drop rate | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 4 | decoder drops | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 4 | largest drift (s) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 4 | largest RSS (kB) | 153166.667 ± 359.652 (3) | 151918.667 ± 150.537 (3) |
| 4 | highest temperature (m°C) | 42845.667 ± 532.894 (3) | 41922.667 ± 532.894 (3) |
| 8 | process CPU (s) | 43.283 ± 0.343 (3) | 18.560 ± 0.308 (3) |
| 8 | process CPU, A76 (s) | 43.173 ± 0.348 (3) | 18.470 ± 0.370 (3) |
| 8 | process CPU, A55 (s) | 0.103 ± 0.006 (3) | 0.110 ± 0.000 (3) |
| 8 | process CPU, unattributed (s) | 0.007 ± 0.050 (3) | -0.020 ± 0.072 (3) |
| 8 | system cpu0 (s) | 2.527 ± 0.595 (3) | 1.760 ± 0.763 (3) |
| 8 | system cpu1 (s) | 1.357 ± 1.021 (3) | 0.657 ± 0.125 (3) |
| 8 | system cpu2 (s) | 0.507 ± 0.463 (3) | 0.207 ± 0.072 (3) |
| 8 | system cpu3 (s) | 0.580 ± 0.168 (3) | 0.233 ± 0.012 (3) |
| 8 | system cpu4 (s) | 11.660 ± 1.394 (3) | 2.047 ± 0.220 (3) |
| 8 | system cpu5 (s) | 11.240 ± 1.585 (3) | 2.103 ± 0.384 (3) |
| 8 | system cpu6 (s) | 11.817 ± 1.217 (3) | 2.133 ± 0.483 (3) |
| 8 | system cpu7 (s) | 12.053 ± 1.319 (3) | 2.027 ± 0.376 (3) |
| 8 | system cpu0-3 (s) | 4.970 ± 1.876 (3) | 2.857 ± 0.671 (3) |
| 8 | system cpu4-5 (s) | 22.900 ± 2.969 (3) | 4.150 ± 0.566 (3) |
| 8 | system cpu6-7 (s) | 23.870 ± 2.330 (3) | 4.160 ± 0.815 (3) |
| 8 | system cpu total (s) | 51.740 ± 6.971 (3) | 11.167 ± 1.816 (3) |
| 8 | system cpu0 minus idle (s) | 1.957 ± 0.595 (3) | 1.190 ± 0.763 (3) |
| 8 | system cpu1 minus idle (s) | 1.257 ± 1.021 (3) | 0.557 ± 0.125 (3) |
| 8 | system cpu2 minus idle (s) | 0.457 ± 0.463 (3) | 0.157 ± 0.072 (3) |
| 8 | system cpu3 minus idle (s) | 0.540 ± 0.168 (3) | 0.193 ± 0.012 (3) |
| 8 | system cpu4 minus idle (s) | 11.660 ± 1.394 (3) | 2.047 ± 0.220 (3) |
| 8 | system cpu5 minus idle (s) | 11.230 ± 1.585 (3) | 2.093 ± 0.384 (3) |
| 8 | system cpu6 minus idle (s) | 11.767 ± 1.217 (3) | 2.083 ± 0.483 (3) |
| 8 | system cpu7 minus idle (s) | 12.043 ± 1.319 (3) | 2.017 ± 0.376 (3) |
| 8 | system cpu0-3 minus idle (s) | 4.210 ± 1.876 (3) | 2.097 ± 0.671 (3) |
| 8 | system cpu4-5 minus idle (s) | 22.890 ± 2.969 (3) | 4.140 ± 0.566 (3) |
| 8 | system cpu6-7 minus idle (s) | 23.810 ± 2.330 (3) | 4.100 ± 0.815 (3) |
| 8 | system cpu total minus idle (s) | 50.910 ± 6.971 (3) | 10.337 ± 1.816 (3) |
| 8 | p50 latency (ms) | 33.117 ± 4.543 (3) | 30.340 ± 0.961 (3) |
| 8 | p99 latency (ms) | 47.395 ± 5.538 (3) | 43.959 ± 1.859 (3) |
| 8 | p99 latency, bench_main's (ms) | 47.395 ± 5.538 (3) | 43.959 ± 1.859 (3) |
| 8 | MPP hold p50 (ms) | 8.628 ± 2.862 (3) | 1.751 ± 0.046 (3) |
| 8 | MPP hold p99 (ms) | 12.911 ± 3.258 (3) | 3.951 ± 0.591 (3) |
| 8 | slot wait p99 (ms) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) |
| 8 | inference rate, lowest channel (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 8 | inference rate, channel mean (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 8 | inference rate, highest channel (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 8 | drop rate | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 8 | decoder drops | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 8 | largest drift (s) | 0.000 ± 0.000 (3) | 0.011 ± 0.019 (3) |
| 8 | largest RSS (kB) | 241725.333 ± 1064.153 (3) | 236780.000 ± 1686.004 (3) |
| 8 | highest temperature (m°C) | 47153.000 ± 0.000 (3) | 45307.000 ± 0.000 (3) |
| 12 | process CPU (s) | 66.260 ± 0.480 (3) | 30.077 ± 0.430 (3) |
| 12 | process CPU, A76 (s) | 66.093 ± 0.490 (3) | 29.977 ± 0.415 (3) |
| 12 | process CPU, A55 (s) | 0.110 ± 0.010 (3) | 0.103 ± 0.006 (3) |
| 12 | process CPU, unattributed (s) | 0.057 ± 0.021 (3) | -0.003 ± 0.015 (3) |
| 12 | system cpu0 (s) | 2.837 ± 1.201 (3) | 2.010 ± 0.410 (3) |
| 12 | system cpu1 (s) | 2.083 ± 0.788 (3) | 2.393 ± 1.114 (3) |
| 12 | system cpu2 (s) | 0.920 ± 0.469 (3) | 0.697 ± 0.410 (3) |
| 12 | system cpu3 (s) | 0.230 ± 0.130 (3) | 0.313 ± 0.309 (3) |
| 12 | system cpu4 (s) | 14.290 ± 3.685 (3) | 6.257 ± 2.722 (3) |
| 12 | system cpu5 (s) | 15.217 ± 2.934 (3) | 6.307 ± 2.111 (3) |
| 12 | system cpu6 (s) | 14.967 ± 1.569 (3) | 6.700 ± 3.077 (3) |
| 12 | system cpu7 (s) | 15.560 ± 1.391 (3) | 6.667 ± 3.274 (3) |
| 12 | system cpu0-3 (s) | 6.070 ± 1.429 (3) | 5.413 ± 1.460 (3) |
| 12 | system cpu4-5 (s) | 29.507 ± 6.587 (3) | 12.563 ± 4.805 (3) |
| 12 | system cpu6-7 (s) | 30.527 ± 2.651 (3) | 13.367 ± 6.349 (3) |
| 12 | system cpu total (s) | 66.103 ± 10.023 (3) | 31.343 ± 9.194 (3) |
| 12 | system cpu0 minus idle (s) | 2.267 ± 1.201 (3) | 1.440 ± 0.410 (3) |
| 12 | system cpu1 minus idle (s) | 1.983 ± 0.788 (3) | 2.293 ± 1.114 (3) |
| 12 | system cpu2 minus idle (s) | 0.870 ± 0.469 (3) | 0.647 ± 0.410 (3) |
| 12 | system cpu3 minus idle (s) | 0.190 ± 0.130 (3) | 0.273 ± 0.309 (3) |
| 12 | system cpu4 minus idle (s) | 14.290 ± 3.685 (3) | 6.257 ± 2.722 (3) |
| 12 | system cpu5 minus idle (s) | 15.207 ± 2.934 (3) | 6.297 ± 2.111 (3) |
| 12 | system cpu6 minus idle (s) | 14.917 ± 1.569 (3) | 6.650 ± 3.077 (3) |
| 12 | system cpu7 minus idle (s) | 15.550 ± 1.391 (3) | 6.657 ± 3.274 (3) |
| 12 | system cpu0-3 minus idle (s) | 5.310 ± 1.429 (3) | 4.653 ± 1.460 (3) |
| 12 | system cpu4-5 minus idle (s) | 29.497 ± 6.587 (3) | 12.553 ± 4.805 (3) |
| 12 | system cpu6-7 minus idle (s) | 30.467 ± 2.651 (3) | 13.307 ± 6.349 (3) |
| 12 | system cpu total minus idle (s) | 65.273 ± 10.023 (3) | 30.513 ± 9.194 (3) |
| 12 | p50 latency (ms) | 36.142 ± 0.711 (3) | 32.168 ± 1.724 (3) |
| 12 | p99 latency (ms) | 49.721 ± 1.036 (3) | 48.260 ± 4.578 (3) |
| 12 | p99 latency, bench_main's (ms) | 49.721 ± 1.036 (3) | 48.260 ± 4.578 (3) |
| 12 | MPP hold p50 (ms) | 5.413 ± 0.129 (3) | 1.727 ± 0.019 (3) |
| 12 | MPP hold p99 (ms) | 10.303 ± 0.508 (3) | 3.352 ± 0.038 (3) |
| 12 | slot wait p99 (ms) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) |
| 12 | inference rate, lowest channel (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 12 | inference rate, channel mean (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 12 | inference rate, highest channel (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 12 | drop rate | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 12 | decoder drops | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 12 | largest drift (s) | 0.011 ± 0.019 (3) | 0.022 ± 0.019 (3) |
| 12 | largest RSS (kB) | 327205.333 ± 883.578 (3) | 321512.000 ± 696.310 (3) |
| 12 | highest temperature (m°C) | 49307.667 ± 532.894 (3) | 47153.000 ± 0.000 (3) |
| 16 | process CPU (s) | 90.360 ± 0.450 (3) | 41.187 ± 0.609 (3) |
| 16 | process CPU, A76 (s) | 90.243 ± 0.446 (3) | 41.080 ± 0.603 (3) |
| 16 | process CPU, A55 (s) | 0.110 ± 0.010 (3) | 0.110 ± 0.000 (3) |
| 16 | process CPU, unattributed (s) | 0.007 ± 0.029 (3) | -0.003 ± 0.047 (3) |
| 16 | system cpu0 (s) | 2.730 ± 0.842 (3) | 3.837 ± 1.524 (3) |
| 16 | system cpu1 (s) | 4.573 ± 1.938 (3) | 4.887 ± 2.334 (3) |
| 16 | system cpu2 (s) | 1.040 ± 0.252 (3) | 0.813 ± 0.414 (3) |
| 16 | system cpu3 (s) | 1.037 ± 0.999 (3) | 1.430 ± 1.454 (3) |
| 16 | system cpu4 (s) | 23.650 ± 3.038 (3) | 13.613 ± 4.535 (3) |
| 16 | system cpu5 (s) | 22.147 ± 3.193 (3) | 13.920 ± 4.586 (3) |
| 16 | system cpu6 (s) | 22.660 ± 2.754 (3) | 13.930 ± 4.888 (3) |
| 16 | system cpu7 (s) | 22.660 ± 4.223 (3) | 13.490 ± 4.564 (3) |
| 16 | system cpu0-3 (s) | 9.380 ± 1.530 (3) | 10.967 ± 5.164 (3) |
| 16 | system cpu4-5 (s) | 45.797 ± 6.194 (3) | 27.533 ± 9.027 (3) |
| 16 | system cpu6-7 (s) | 45.320 ± 6.717 (3) | 27.420 ± 9.451 (3) |
| 16 | system cpu total (s) | 100.497 ± 13.786 (3) | 65.920 ± 22.857 (3) |
| 16 | system cpu0 minus idle (s) | 2.160 ± 0.842 (3) | 3.267 ± 1.524 (3) |
| 16 | system cpu1 minus idle (s) | 4.473 ± 1.938 (3) | 4.787 ± 2.334 (3) |
| 16 | system cpu2 minus idle (s) | 0.990 ± 0.252 (3) | 0.763 ± 0.414 (3) |
| 16 | system cpu3 minus idle (s) | 0.997 ± 0.999 (3) | 1.390 ± 1.454 (3) |
| 16 | system cpu4 minus idle (s) | 23.650 ± 3.038 (3) | 13.613 ± 4.535 (3) |
| 16 | system cpu5 minus idle (s) | 22.137 ± 3.193 (3) | 13.910 ± 4.586 (3) |
| 16 | system cpu6 minus idle (s) | 22.610 ± 2.754 (3) | 13.880 ± 4.888 (3) |
| 16 | system cpu7 minus idle (s) | 22.650 ± 4.223 (3) | 13.480 ± 4.564 (3) |
| 16 | system cpu0-3 minus idle (s) | 8.620 ± 1.530 (3) | 10.207 ± 5.164 (3) |
| 16 | system cpu4-5 minus idle (s) | 45.787 ± 6.194 (3) | 27.523 ± 9.027 (3) |
| 16 | system cpu6-7 minus idle (s) | 45.260 ± 6.717 (3) | 27.360 ± 9.451 (3) |
| 16 | system cpu total minus idle (s) | 99.667 ± 13.786 (3) | 65.090 ± 22.857 (3) |
| 16 | p50 latency (ms) | 41.466 ± 2.988 (3) | 31.069 ± 2.421 (3) |
| 16 | p99 latency (ms) | 66.599 ± 11.709 (3) | 61.621 ± 4.536 (3) |
| 16 | p99 latency, bench_main's (ms) | 66.599 ± 11.709 (3) | 61.621 ± 4.535 (3) |
| 16 | MPP hold p50 (ms) | 9.065 ± 3.365 (3) | 1.699 ± 0.013 (3) |
| 16 | MPP hold p99 (ms) | 17.045 ± 5.719 (3) | 4.310 ± 1.630 (3) |
| 16 | slot wait p99 (ms) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) |
| 16 | inference rate, lowest channel (fps) | 5.000 ± 0.000 (3) | 4.997 ± 0.005 (3) |
| 16 | inference rate, channel mean (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 16 | inference rate, highest channel (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 16 | drop rate | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 16 | decoder drops | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 16 | largest drift (s) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 16 | largest RSS (kB) | 412362.667 ± 1105.914 (3) | 407393.333 ± 222.938 (3) |
| 16 | highest temperature (m°C) | 53922.667 ± 1065.789 (3) | 50846.000 ± 0.000 (3) |
| 32 | process CPU (s) | 200.517 ± 0.241 (3) | 100.620 ± 1.325 (3) |
| 32 | process CPU, A76 (s) | 200.347 ± 0.295 (3) | 100.410 ± 1.369 (3) |
| 32 | process CPU, A55 (s) | 0.160 ± 0.017 (3) | 0.163 ± 0.012 (3) |
| 32 | process CPU, unattributed (s) | 0.010 ± 0.050 (3) | 0.047 ± 0.051 (3) |
| 32 | system cpu0 (s) | 5.183 ± 1.345 (3) | 4.480 ± 0.528 (3) |
| 32 | system cpu1 (s) | 17.087 ± 7.746 (3) | 16.553 ± 4.067 (3) |
| 32 | system cpu2 (s) | 7.587 ± 4.498 (3) | 5.890 ± 3.475 (3) |
| 32 | system cpu3 (s) | 2.557 ± 1.199 (3) | 2.213 ± 1.185 (3) |
| 32 | system cpu4 (s) | 56.447 ± 6.262 (3) | 27.710 ± 4.415 (3) |
| 32 | system cpu5 (s) | 57.363 ± 3.620 (3) | 27.407 ± 2.247 (3) |
| 32 | system cpu6 (s) | 56.413 ± 6.133 (3) | 26.367 ± 2.408 (3) |
| 32 | system cpu7 (s) | 55.797 ± 5.956 (3) | 27.000 ± 3.370 (3) |
| 32 | system cpu0-3 (s) | 32.413 ± 13.737 (3) | 29.137 ± 6.620 (3) |
| 32 | system cpu4-5 (s) | 113.810 ± 9.704 (3) | 55.117 ± 6.626 (3) |
| 32 | system cpu6-7 (s) | 112.210 ± 12.085 (3) | 53.367 ± 5.754 (3) |
| 32 | system cpu total (s) | 258.433 ± 35.517 (3) | 137.620 ± 18.242 (3) |
| 32 | system cpu0 minus idle (s) | 4.613 ± 1.345 (3) | 3.910 ± 0.528 (3) |
| 32 | system cpu1 minus idle (s) | 16.987 ± 7.746 (3) | 16.453 ± 4.067 (3) |
| 32 | system cpu2 minus idle (s) | 7.537 ± 4.498 (3) | 5.840 ± 3.475 (3) |
| 32 | system cpu3 minus idle (s) | 2.517 ± 1.199 (3) | 2.173 ± 1.185 (3) |
| 32 | system cpu4 minus idle (s) | 56.447 ± 6.262 (3) | 27.710 ± 4.415 (3) |
| 32 | system cpu5 minus idle (s) | 57.353 ± 3.620 (3) | 27.397 ± 2.247 (3) |
| 32 | system cpu6 minus idle (s) | 56.363 ± 6.133 (3) | 26.317 ± 2.408 (3) |
| 32 | system cpu7 minus idle (s) | 55.787 ± 5.956 (3) | 26.990 ± 3.370 (3) |
| 32 | system cpu0-3 minus idle (s) | 31.653 ± 13.737 (3) | 28.377 ± 6.620 (3) |
| 32 | system cpu4-5 minus idle (s) | 113.800 ± 9.704 (3) | 55.107 ± 6.626 (3) |
| 32 | system cpu6-7 minus idle (s) | 112.150 ± 12.085 (3) | 53.307 ± 5.754 (3) |
| 32 | system cpu total minus idle (s) | 257.603 ± 35.517 (3) | 136.790 ± 18.242 (3) |
| 32 | p50 latency (ms) | 51.292 ± 2.477 (3) | 68.138 ± 0.699 (3) |
| 32 | p99 latency (ms) | 120.049 ± 7.956 (3) | 143.271 ± 3.275 (3) |
| 32 | p99 latency, bench_main's (ms) | 120.049 ± 7.956 (3) | 143.271 ± 3.275 (3) |
| 32 | MPP hold p50 (ms) | 11.151 ± 3.198 (3) | 4.579 ± 1.874 (3) |
| 32 | MPP hold p99 (ms) | 60.978 ± 4.957 (3) | 80.907 ± 0.893 (3) |
| 32 | slot wait p99 (ms) | 0.001 ± 0.001 (3) | 0.001 ± 0.000 (3) |
| 32 | inference rate, lowest channel (fps) | 4.997 ± 0.005 (3) | 4.997 ± 0.005 (3) |
| 32 | inference rate, channel mean (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 32 | inference rate, highest channel (fps) | 5.000 ± 0.000 (3) | 5.005 ± 0.005 (3) |
| 32 | drop rate | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 32 | decoder drops | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 32 | largest drift (s) | 0.011 ± 0.019 (3) | 0.022 ± 0.019 (3) |
| 32 | largest RSS (kB) | 746284.000 ± 714.288 (3) | 744704.000 ± 107.852 (3) |
| 32 | highest temperature (m°C) | 68384.000 ± 0.000 (3) | 61923.000 ± 0.000 (3) |

## Sweep: aux

| Channels | Metric | B: mean ± sd (n) | C (A6 label): mean ± sd (n) |
|---|---|---|---|
| 1 | process CPU (s) | 24.173 ± 0.169 (3) | 6.437 ± 0.038 (3) |
| 1 | process CPU, A76 (s) | 24.047 ± 0.170 (3) | 6.327 ± 0.038 (3) |
| 1 | process CPU, A55 (s) | 0.103 ± 0.006 (3) | 0.100 ± 0.000 (3) |
| 1 | process CPU, unattributed (s) | 0.023 ± 0.023 (3) | 0.010 ± 0.000 (3) |
| 1 | system cpu0 (s) | 4.583 ± 0.377 (3) | 1.753 ± 1.784 (3) |
| 1 | system cpu1 (s) | 0.207 ± 0.083 (3) | 0.313 ± 0.232 (3) |
| 1 | system cpu2 (s) | 0.137 ± 0.121 (3) | 0.680 ± 1.117 (3) |
| 1 | system cpu3 (s) | 0.100 ± 0.106 (3) | 0.467 ± 0.632 (3) |
| 1 | system cpu4 (s) | 5.763 ± 3.854 (3) | 1.513 ± 2.379 (3) |
| 1 | system cpu5 (s) | 4.943 ± 5.314 (3) | 2.987 ± 1.474 (3) |
| 1 | system cpu6 (s) | 5.623 ± 5.632 (3) | 3.440 ± 4.486 (3) |
| 1 | system cpu7 (s) | 9.293 ± 3.749 (3) | 3.630 ± 1.716 (3) |
| 1 | system cpu0-3 (s) | 5.027 ± 0.356 (3) | 3.213 ± 3.503 (3) |
| 1 | system cpu4-5 (s) | 10.707 ± 9.030 (3) | 4.500 ± 3.663 (3) |
| 1 | system cpu6-7 (s) | 14.917 ± 9.112 (3) | 7.070 ± 4.231 (3) |
| 1 | system cpu total (s) | 30.650 ± 7.803 (3) | 14.783 ± 11.379 (3) |
| 1 | system cpu0 minus idle (s) | 4.363 ± 0.377 (3) | 1.533 ± 1.784 (3) |
| 1 | system cpu1 minus idle (s) | 0.157 ± 0.083 (3) | 0.263 ± 0.232 (3) |
| 1 | system cpu2 minus idle (s) | 0.117 ± 0.121 (3) | 0.660 ± 1.117 (3) |
| 1 | system cpu3 minus idle (s) | 0.080 ± 0.106 (3) | 0.447 ± 0.632 (3) |
| 1 | system cpu4 minus idle (s) | 5.763 ± 3.854 (3) | 1.513 ± 2.379 (3) |
| 1 | system cpu5 minus idle (s) | 4.913 ± 5.314 (3) | 2.957 ± 1.474 (3) |
| 1 | system cpu6 minus idle (s) | 5.623 ± 5.632 (3) | 3.440 ± 4.486 (3) |
| 1 | system cpu7 minus idle (s) | 9.153 ± 3.749 (3) | 3.490 ± 1.716 (3) |
| 1 | system cpu0-3 minus idle (s) | 4.717 ± 0.356 (3) | 2.903 ± 3.503 (3) |
| 1 | system cpu4-5 minus idle (s) | 10.677 ± 9.030 (3) | 4.470 ± 3.663 (3) |
| 1 | system cpu6-7 minus idle (s) | 14.777 ± 9.112 (3) | 6.930 ± 4.231 (3) |
| 1 | system cpu total minus idle (s) | 30.170 ± 7.803 (3) | 14.303 ± 11.379 (3) |
| 1 | p50 latency (ms) | 21.420 ± 0.287 (3) | 16.645 ± 0.021 (3) |
| 1 | p99 latency (ms) | 22.105 ± 0.049 (3) | 18.697 ± 0.050 (3) |
| 1 | p99 latency, bench_main's (ms) | 22.105 ± 0.049 (3) | 18.697 ± 0.049 (3) |
| 1 | MPP hold p50 (ms) | 4.929 ± 0.074 (3) | 1.700 ± 0.003 (3) |
| 1 | MPP hold p99 (ms) | 5.084 ± 0.072 (3) | 1.715 ± 0.002 (3) |
| 1 | slot wait p99 (ms) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) |
| 1 | inference rate, lowest channel (fps) | 30.000 ± 0.000 (3) | 30.000 ± 0.000 (3) |
| 1 | inference rate, channel mean (fps) | 30.000 ± 0.000 (3) | 30.000 ± 0.000 (3) |
| 1 | inference rate, highest channel (fps) | 30.000 ± 0.000 (3) | 30.000 ± 0.000 (3) |
| 1 | drop rate | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 1 | decoder drops | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 1 | largest drift (s) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 1 | largest RSS (kB) | 88974.667 ± 376.220 (3) | 87621.333 ± 151.279 (3) |
| 1 | highest temperature (m°C) | 41615.000 ± 0.000 (3) | 40692.000 ± 0.000 (3) |
| 2 | process CPU (s) | 49.663 ± 0.254 (3) | 13.437 ± 0.198 (3) |
| 2 | process CPU, A76 (s) | 49.567 ± 0.249 (3) | 13.327 ± 0.201 (3) |
| 2 | process CPU, A55 (s) | 0.110 ± 0.000 (3) | 0.110 ± 0.010 (3) |
| 2 | process CPU, unattributed (s) | -0.013 ± 0.012 (3) | 0.000 ± 0.017 (3) |
| 2 | system cpu0 (s) | 5.677 ± 2.048 (3) | 1.283 ± 0.287 (3) |
| 2 | system cpu1 (s) | 0.263 ± 0.049 (3) | 0.420 ± 0.131 (3) |
| 2 | system cpu2 (s) | 0.293 ± 0.227 (3) | 0.120 ± 0.066 (3) |
| 2 | system cpu3 (s) | 0.247 ± 0.117 (3) | 0.037 ± 0.015 (3) |
| 2 | system cpu4 (s) | 13.597 ± 5.451 (3) | 2.597 ± 0.947 (3) |
| 2 | system cpu5 (s) | 10.997 ± 1.157 (3) | 2.657 ± 0.912 (3) |
| 2 | system cpu6 (s) | 13.140 ± 1.064 (3) | 4.113 ± 2.605 (3) |
| 2 | system cpu7 (s) | 13.677 ± 3.894 (3) | 3.757 ± 1.937 (3) |
| 2 | system cpu0-3 (s) | 6.480 ± 2.436 (3) | 1.860 ± 0.416 (3) |
| 2 | system cpu4-5 (s) | 24.593 ± 6.554 (3) | 5.253 ± 1.609 (3) |
| 2 | system cpu6-7 (s) | 26.817 ± 4.948 (3) | 7.870 ± 4.511 (3) |
| 2 | system cpu total (s) | 57.890 ± 9.544 (3) | 14.983 ± 6.347 (3) |
| 2 | system cpu0 minus idle (s) | 5.457 ± 2.048 (3) | 1.063 ± 0.287 (3) |
| 2 | system cpu1 minus idle (s) | 0.213 ± 0.049 (3) | 0.370 ± 0.131 (3) |
| 2 | system cpu2 minus idle (s) | 0.273 ± 0.227 (3) | 0.100 ± 0.066 (3) |
| 2 | system cpu3 minus idle (s) | 0.227 ± 0.117 (3) | 0.017 ± 0.015 (3) |
| 2 | system cpu4 minus idle (s) | 13.597 ± 5.451 (3) | 2.597 ± 0.947 (3) |
| 2 | system cpu5 minus idle (s) | 10.967 ± 1.157 (3) | 2.627 ± 0.912 (3) |
| 2 | system cpu6 minus idle (s) | 13.140 ± 1.064 (3) | 4.113 ± 2.605 (3) |
| 2 | system cpu7 minus idle (s) | 13.537 ± 3.894 (3) | 3.617 ± 1.937 (3) |
| 2 | system cpu0-3 minus idle (s) | 6.170 ± 2.436 (3) | 1.550 ± 0.416 (3) |
| 2 | system cpu4-5 minus idle (s) | 24.563 ± 6.554 (3) | 5.223 ± 1.609 (3) |
| 2 | system cpu6-7 minus idle (s) | 26.677 ± 4.948 (3) | 7.730 ± 4.511 (3) |
| 2 | system cpu total minus idle (s) | 57.410 ± 9.544 (3) | 14.503 ± 6.347 (3) |
| 2 | p50 latency (ms) | 20.995 ± 0.447 (3) | 16.842 ± 0.017 (3) |
| 2 | p99 latency (ms) | 22.412 ± 0.075 (3) | 18.820 ± 0.118 (3) |
| 2 | p99 latency, bench_main's (ms) | 22.412 ± 0.075 (3) | 18.820 ± 0.118 (3) |
| 2 | MPP hold p50 (ms) | 4.955 ± 0.079 (3) | 1.694 ± 0.016 (3) |
| 2 | MPP hold p99 (ms) | 5.142 ± 0.128 (3) | 1.737 ± 0.002 (3) |
| 2 | slot wait p99 (ms) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) |
| 2 | inference rate, lowest channel (fps) | 30.000 ± 0.000 (3) | 30.000 ± 0.000 (3) |
| 2 | inference rate, channel mean (fps) | 30.000 ± 0.000 (3) | 30.000 ± 0.000 (3) |
| 2 | inference rate, highest channel (fps) | 30.000 ± 0.000 (3) | 30.000 ± 0.000 (3) |
| 2 | drop rate | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 2 | decoder drops | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 2 | largest drift (s) | 0.011 ± 0.019 (3) | 0.011 ± 0.019 (3) |
| 2 | largest RSS (kB) | 110509.333 ± 151.279 (3) | 109166.667 ± 748.656 (3) |
| 2 | highest temperature (m°C) | 45614.667 ± 532.894 (3) | 42845.667 ± 532.894 (3) |
| 3 | process CPU (s) | 75.323 ± 0.483 (3) | 20.867 ± 0.335 (3) |
| 3 | process CPU, A76 (s) | 75.197 ± 0.492 (3) | 20.753 ± 0.356 (3) |
| 3 | process CPU, A55 (s) | 0.113 ± 0.006 (3) | 0.113 ± 0.006 (3) |
| 3 | process CPU, unattributed (s) | 0.013 ± 0.015 (3) | 0.000 ± 0.026 (3) |
| 3 | system cpu0 (s) | 1.923 ± 0.961 (3) | 2.023 ± 0.918 (3) |
| 3 | system cpu1 (s) | 0.840 ± 0.252 (3) | 0.380 ± 0.210 (3) |
| 3 | system cpu2 (s) | 0.210 ± 0.147 (3) | 0.440 ± 0.193 (3) |
| 3 | system cpu3 (s) | 0.267 ± 0.107 (3) | 0.593 ± 0.628 (3) |
| 3 | system cpu4 (s) | 19.867 ± 9.783 (3) | 4.690 ± 3.124 (3) |
| 3 | system cpu5 (s) | 16.720 ± 4.630 (3) | 4.707 ± 2.725 (3) |
| 3 | system cpu6 (s) | 12.043 ± 5.536 (3) | 4.467 ± 4.212 (3) |
| 3 | system cpu7 (s) | 15.727 ± 13.462 (3) | 3.970 ± 4.582 (3) |
| 3 | system cpu0-3 (s) | 3.240 ± 1.010 (3) | 3.437 ± 1.652 (3) |
| 3 | system cpu4-5 (s) | 36.587 ± 13.749 (3) | 9.397 ± 5.844 (3) |
| 3 | system cpu6-7 (s) | 27.770 ± 18.917 (3) | 8.437 ± 8.794 (3) |
| 3 | system cpu total (s) | 67.597 ± 22.111 (3) | 21.270 ± 15.618 (3) |
| 3 | system cpu0 minus idle (s) | 1.703 ± 0.961 (3) | 1.803 ± 0.918 (3) |
| 3 | system cpu1 minus idle (s) | 0.790 ± 0.252 (3) | 0.330 ± 0.210 (3) |
| 3 | system cpu2 minus idle (s) | 0.190 ± 0.147 (3) | 0.420 ± 0.193 (3) |
| 3 | system cpu3 minus idle (s) | 0.247 ± 0.107 (3) | 0.573 ± 0.628 (3) |
| 3 | system cpu4 minus idle (s) | 19.867 ± 9.783 (3) | 4.690 ± 3.124 (3) |
| 3 | system cpu5 minus idle (s) | 16.690 ± 4.630 (3) | 4.677 ± 2.725 (3) |
| 3 | system cpu6 minus idle (s) | 12.043 ± 5.536 (3) | 4.467 ± 4.212 (3) |
| 3 | system cpu7 minus idle (s) | 15.587 ± 13.462 (3) | 3.830 ± 4.582 (3) |
| 3 | system cpu0-3 minus idle (s) | 2.930 ± 1.010 (3) | 3.127 ± 1.652 (3) |
| 3 | system cpu4-5 minus idle (s) | 36.557 ± 13.749 (3) | 9.367 ± 5.844 (3) |
| 3 | system cpu6-7 minus idle (s) | 27.630 ± 18.917 (3) | 8.297 ± 8.794 (3) |
| 3 | system cpu total minus idle (s) | 67.117 ± 22.111 (3) | 20.790 ± 15.618 (3) |
| 3 | p50 latency (ms) | 20.718 ± 0.169 (3) | 17.176 ± 0.101 (3) |
| 3 | p99 latency (ms) | 22.573 ± 0.197 (3) | 18.395 ± 0.555 (3) |
| 3 | p99 latency, bench_main's (ms) | 22.574 ± 0.197 (3) | 18.395 ± 0.555 (3) |
| 3 | MPP hold p50 (ms) | 4.999 ± 0.084 (3) | 1.693 ± 0.022 (3) |
| 3 | MPP hold p99 (ms) | 5.245 ± 0.203 (3) | 2.272 ± 0.922 (3) |
| 3 | slot wait p99 (ms) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) |
| 3 | inference rate, lowest channel (fps) | 30.000 ± 0.000 (3) | 30.000 ± 0.000 (3) |
| 3 | inference rate, channel mean (fps) | 30.000 ± 0.000 (3) | 30.000 ± 0.000 (3) |
| 3 | inference rate, highest channel (fps) | 30.000 ± 0.000 (3) | 30.000 ± 0.000 (3) |
| 3 | drop rate | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 3 | decoder drops | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 3 | largest drift (s) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 3 | largest RSS (kB) | 132657.333 ± 406.356 (3) | 130853.333 ± 270.121 (3) |
| 3 | highest temperature (m°C) | 49923.000 ± 0.000 (3) | 46230.000 ± 923.000 (3) |
| 4 | process CPU (s) | 101.627 ± 0.544 (3) | 27.863 ± 0.190 (3) |
| 4 | process CPU, A76 (s) | 101.537 ± 0.571 (3) | 27.707 ± 0.175 (3) |
| 4 | process CPU, A55 (s) | 0.110 ± 0.010 (3) | 0.140 ± 0.010 (3) |
| 4 | process CPU, unattributed (s) | -0.020 ± 0.026 (3) | 0.017 ± 0.006 (3) |
| 4 | system cpu0 (s) | 1.037 ± 0.379 (3) | 1.707 ± 0.514 (3) |
| 4 | system cpu1 (s) | 0.723 ± 0.458 (3) | 0.697 ± 0.256 (3) |
| 4 | system cpu2 (s) | 0.233 ± 0.197 (3) | 0.300 ± 0.078 (3) |
| 4 | system cpu3 (s) | 0.677 ± 0.876 (3) | 0.287 ± 0.239 (3) |
| 4 | system cpu4 (s) | 27.707 ± 2.010 (3) | 3.463 ± 0.829 (3) |
| 4 | system cpu5 (s) | 24.323 ± 3.649 (3) | 3.453 ± 0.826 (3) |
| 4 | system cpu6 (s) | 27.957 ± 5.490 (3) | 3.263 ± 1.168 (3) |
| 4 | system cpu7 (s) | 27.910 ± 5.774 (3) | 3.127 ± 0.490 (3) |
| 4 | system cpu0-3 (s) | 2.670 ± 1.859 (3) | 2.990 ± 0.541 (3) |
| 4 | system cpu4-5 (s) | 52.030 ± 2.979 (3) | 6.917 ± 1.352 (3) |
| 4 | system cpu6-7 (s) | 55.867 ± 11.240 (3) | 6.390 ± 1.632 (3) |
| 4 | system cpu total (s) | 110.567 ± 12.355 (3) | 16.297 ± 2.413 (3) |
| 4 | system cpu0 minus idle (s) | 0.817 ± 0.379 (3) | 1.487 ± 0.514 (3) |
| 4 | system cpu1 minus idle (s) | 0.673 ± 0.458 (3) | 0.647 ± 0.256 (3) |
| 4 | system cpu2 minus idle (s) | 0.213 ± 0.197 (3) | 0.280 ± 0.078 (3) |
| 4 | system cpu3 minus idle (s) | 0.657 ± 0.876 (3) | 0.267 ± 0.239 (3) |
| 4 | system cpu4 minus idle (s) | 27.707 ± 2.010 (3) | 3.463 ± 0.829 (3) |
| 4 | system cpu5 minus idle (s) | 24.293 ± 3.649 (3) | 3.423 ± 0.826 (3) |
| 4 | system cpu6 minus idle (s) | 27.957 ± 5.490 (3) | 3.263 ± 1.168 (3) |
| 4 | system cpu7 minus idle (s) | 27.770 ± 5.774 (3) | 2.987 ± 0.490 (3) |
| 4 | system cpu0-3 minus idle (s) | 2.360 ± 1.859 (3) | 2.680 ± 0.541 (3) |
| 4 | system cpu4-5 minus idle (s) | 52.000 ± 2.979 (3) | 6.887 ± 1.352 (3) |
| 4 | system cpu6-7 minus idle (s) | 55.727 ± 11.240 (3) | 6.250 ± 1.632 (3) |
| 4 | system cpu total minus idle (s) | 110.087 ± 12.355 (3) | 15.817 ± 2.413 (3) |
| 4 | p50 latency (ms) | 21.536 ± 0.318 (3) | 17.346 ± 0.060 (3) |
| 4 | p99 latency (ms) | 31.560 ± 1.408 (3) | 21.755 ± 5.287 (3) |
| 4 | p99 latency, bench_main's (ms) | 31.560 ± 1.408 (3) | 21.755 ± 5.287 (3) |
| 4 | MPP hold p50 (ms) | 5.344 ± 0.172 (3) | 1.672 ± 0.008 (3) |
| 4 | MPP hold p99 (ms) | 7.537 ± 0.627 (3) | 2.244 ± 0.911 (3) |
| 4 | slot wait p99 (ms) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) |
| 4 | inference rate, lowest channel (fps) | 30.000 ± 0.000 (3) | 30.000 ± 0.000 (3) |
| 4 | inference rate, channel mean (fps) | 30.000 ± 0.000 (3) | 30.000 ± 0.000 (3) |
| 4 | inference rate, highest channel (fps) | 30.000 ± 0.000 (3) | 30.000 ± 0.000 (3) |
| 4 | drop rate | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 4 | decoder drops | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 4 | largest drift (s) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 4 | largest RSS (kB) | 154341.333 ± 437.673 (3) | 151756.000 ± 598.090 (3) |
| 4 | highest temperature (m°C) | 54230.333 ± 532.894 (3) | 49000.000 ± 0.000 (3) |
| 5 | process CPU (s) | 129.587 ± 1.236 (3) | 35.680 ± 0.486 (3) |
| 5 | process CPU, A76 (s) | 129.453 ± 1.225 (3) | 35.533 ± 0.491 (3) |
| 5 | process CPU, A55 (s) | 0.120 ± 0.000 (3) | 0.143 ± 0.012 (3) |
| 5 | process CPU, unattributed (s) | 0.013 ± 0.021 (3) | 0.003 ± 0.031 (3) |
| 5 | system cpu0 (s) | 1.943 ± 1.250 (3) | 1.317 ± 0.254 (3) |
| 5 | system cpu1 (s) | 1.187 ± 0.605 (3) | 0.997 ± 0.532 (3) |
| 5 | system cpu2 (s) | 0.593 ± 0.412 (3) | 0.203 ± 0.112 (3) |
| 5 | system cpu3 (s) | 0.227 ± 0.085 (3) | 0.177 ± 0.059 (3) |
| 5 | system cpu4 (s) | 30.943 ± 3.909 (3) | 5.337 ± 0.681 (3) |
| 5 | system cpu5 (s) | 32.613 ± 5.030 (3) | 5.793 ± 1.348 (3) |
| 5 | system cpu6 (s) | 31.803 ± 5.238 (3) | 5.617 ± 1.856 (3) |
| 5 | system cpu7 (s) | 30.737 ± 4.486 (3) | 5.097 ± 2.087 (3) |
| 5 | system cpu0-3 (s) | 3.950 ± 0.923 (3) | 2.693 ± 0.792 (3) |
| 5 | system cpu4-5 (s) | 63.557 ± 8.939 (3) | 11.130 ± 2.029 (3) |
| 5 | system cpu6-7 (s) | 62.540 ± 9.713 (3) | 10.713 ± 3.827 (3) |
| 5 | system cpu total (s) | 130.047 ± 17.735 (3) | 24.537 ± 6.125 (3) |
| 5 | system cpu0 minus idle (s) | 1.723 ± 1.250 (3) | 1.097 ± 0.254 (3) |
| 5 | system cpu1 minus idle (s) | 1.137 ± 0.605 (3) | 0.947 ± 0.532 (3) |
| 5 | system cpu2 minus idle (s) | 0.573 ± 0.412 (3) | 0.183 ± 0.112 (3) |
| 5 | system cpu3 minus idle (s) | 0.207 ± 0.085 (3) | 0.157 ± 0.059 (3) |
| 5 | system cpu4 minus idle (s) | 30.943 ± 3.909 (3) | 5.337 ± 0.681 (3) |
| 5 | system cpu5 minus idle (s) | 32.583 ± 5.030 (3) | 5.763 ± 1.348 (3) |
| 5 | system cpu6 minus idle (s) | 31.803 ± 5.238 (3) | 5.617 ± 1.856 (3) |
| 5 | system cpu7 minus idle (s) | 30.597 ± 4.486 (3) | 4.957 ± 2.087 (3) |
| 5 | system cpu0-3 minus idle (s) | 3.640 ± 0.923 (3) | 2.383 ± 0.792 (3) |
| 5 | system cpu4-5 minus idle (s) | 63.527 ± 8.939 (3) | 11.100 ± 2.029 (3) |
| 5 | system cpu6-7 minus idle (s) | 62.400 ± 9.713 (3) | 10.573 ± 3.827 (3) |
| 5 | system cpu total minus idle (s) | 129.567 ± 17.735 (3) | 24.057 ± 6.125 (3) |
| 5 | p50 latency (ms) | 21.254 ± 0.454 (3) | 18.148 ± 0.806 (3) |
| 5 | p99 latency (ms) | 24.543 ± 3.305 (3) | 25.860 ± 4.017 (3) |
| 5 | p99 latency, bench_main's (ms) | 24.543 ± 3.305 (3) | 25.860 ± 4.017 (3) |
| 5 | MPP hold p50 (ms) | 5.161 ± 0.069 (3) | 1.701 ± 0.053 (3) |
| 5 | MPP hold p99 (ms) | 5.469 ± 0.276 (3) | 2.297 ± 1.006 (3) |
| 5 | slot wait p99 (ms) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) |
| 5 | inference rate, lowest channel (fps) | 30.000 ± 0.000 (3) | 30.000 ± 0.000 (3) |
| 5 | inference rate, channel mean (fps) | 30.000 ± 0.000 (3) | 30.000 ± 0.000 (3) |
| 5 | inference rate, highest channel (fps) | 30.000 ± 0.000 (3) | 30.000 ± 0.000 (3) |
| 5 | drop rate | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 5 | decoder drops | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 5 | largest drift (s) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 5 | largest RSS (kB) | 175946.667 ± 316.893 (3) | 174100.000 ± 407.077 (3) |
| 5 | highest temperature (m°C) | 57922.333 ± 532.894 (3) | 50846.000 ± 0.000 (3) |

## Sweep: reference

| Channels | Metric | A: mean ± sd (n) |
|---|---|---|
| 1 | process CPU (s) | 31.673 ± 0.023 (3) |
| 1 | process CPU, A76 (s) | 31.563 ± 0.032 (3) |
| 1 | process CPU, A55 (s) | 0.097 ± 0.006 (3) |
| 1 | process CPU, unattributed (s) | 0.013 ± 0.006 (3) |
| 1 | system cpu0 (s) | 4.300 ± 0.881 (3) |
| 1 | system cpu1 (s) | 0.263 ± 0.095 (3) |
| 1 | system cpu2 (s) | 0.243 ± 0.061 (3) |
| 1 | system cpu3 (s) | 0.043 ± 0.023 (3) |
| 1 | system cpu4 (s) | 11.740 ± 1.742 (3) |
| 1 | system cpu5 (s) | 4.257 ± 1.284 (3) |
| 1 | system cpu6 (s) | 2.597 ± 1.366 (3) |
| 1 | system cpu7 (s) | 12.733 ± 2.497 (3) |
| 1 | system cpu0-3 (s) | 4.850 ± 0.908 (3) |
| 1 | system cpu4-5 (s) | 15.997 ± 1.839 (3) |
| 1 | system cpu6-7 (s) | 15.330 ± 3.135 (3) |
| 1 | system cpu total (s) | 36.177 ± 3.238 (3) |
| 1 | system cpu0 minus idle (s) | 3.790 ± 0.881 (3) |
| 1 | system cpu1 minus idle (s) | 0.203 ± 0.095 (3) |
| 1 | system cpu2 minus idle (s) | 0.213 ± 0.061 (3) |
| 1 | system cpu3 minus idle (s) | 0.013 ± 0.023 (3) |
| 1 | system cpu4 minus idle (s) | 11.680 ± 1.742 (3) |
| 1 | system cpu5 minus idle (s) | 4.087 ± 1.284 (3) |
| 1 | system cpu6 minus idle (s) | 2.597 ± 1.366 (3) |
| 1 | system cpu7 minus idle (s) | 12.713 ± 2.497 (3) |
| 1 | system cpu0-3 minus idle (s) | 4.220 ± 0.908 (3) |
| 1 | system cpu4-5 minus idle (s) | 15.767 ± 1.839 (3) |
| 1 | system cpu6-7 minus idle (s) | 15.310 ± 3.135 (3) |
| 1 | system cpu total minus idle (s) | 35.297 ± 3.238 (3) |
| 1 | p50 latency (ms) | 21.845 ± 0.090 (3) |
| 1 | p99 latency (ms) | 22.195 ± 0.051 (3) |
| 1 | p99 latency, bench_main's (ms) | 22.195 ± 0.051 (3) |
| 1 | MPP hold p50 (ms) | 4.891 ± 0.062 (3) |
| 1 | MPP hold p99 (ms) | 4.953 ± 0.001 (3) |
| 1 | slot wait p99 (ms) | 0.001 ± 0.001 (3) |
| 1 | inference rate, lowest channel (fps) | 5.000 ± 0.000 (3) |
| 1 | inference rate, channel mean (fps) | 5.000 ± 0.000 (3) |
| 1 | inference rate, highest channel (fps) | 5.000 ± 0.000 (3) |
| 1 | drop rate | 0.000 ± 0.000 (3) |
| 1 | decoder drops | 0.000 ± 0.000 (3) |
| 1 | largest drift (s) | 0.000 ± 0.000 (3) |
| 1 | largest RSS (kB) | 134430.667 ± 605.116 (3) |
| 1 | highest temperature (m°C) | 38846.000 ± 0.000 (3) |
| 2 | process CPU (s) | 70.230 ± 0.191 (3) |
| 2 | process CPU, A76 (s) | 70.130 ± 0.193 (3) |
| 2 | process CPU, A55 (s) | 0.107 ± 0.006 (3) |
| 2 | process CPU, unattributed (s) | -0.007 ± 0.012 (3) |
| 2 | system cpu0 (s) | 6.357 ± 0.275 (3) |
| 2 | system cpu1 (s) | 0.307 ± 0.106 (3) |
| 2 | system cpu2 (s) | 0.173 ± 0.095 (3) |
| 2 | system cpu3 (s) | 0.167 ± 0.111 (3) |
| 2 | system cpu4 (s) | 19.097 ± 7.177 (3) |
| 2 | system cpu5 (s) | 17.840 ± 10.340 (3) |
| 2 | system cpu6 (s) | 21.540 ± 9.485 (3) |
| 2 | system cpu7 (s) | 13.187 ± 7.677 (3) |
| 2 | system cpu0-3 (s) | 7.003 ± 0.189 (3) |
| 2 | system cpu4-5 (s) | 36.937 ± 10.253 (3) |
| 2 | system cpu6-7 (s) | 34.727 ± 6.456 (3) |
| 2 | system cpu total (s) | 78.667 ± 5.369 (3) |
| 2 | system cpu0 minus idle (s) | 5.847 ± 0.275 (3) |
| 2 | system cpu1 minus idle (s) | 0.247 ± 0.106 (3) |
| 2 | system cpu2 minus idle (s) | 0.143 ± 0.095 (3) |
| 2 | system cpu3 minus idle (s) | 0.137 ± 0.111 (3) |
| 2 | system cpu4 minus idle (s) | 19.037 ± 7.177 (3) |
| 2 | system cpu5 minus idle (s) | 17.670 ± 10.340 (3) |
| 2 | system cpu6 minus idle (s) | 21.540 ± 9.485 (3) |
| 2 | system cpu7 minus idle (s) | 13.167 ± 7.677 (3) |
| 2 | system cpu0-3 minus idle (s) | 6.373 ± 0.189 (3) |
| 2 | system cpu4-5 minus idle (s) | 36.707 ± 10.253 (3) |
| 2 | system cpu6-7 minus idle (s) | 34.707 ± 6.456 (3) |
| 2 | system cpu total minus idle (s) | 77.787 ± 5.369 (3) |
| 2 | p50 latency (ms) | 21.991 ± 0.035 (3) |
| 2 | p99 latency (ms) | 22.622 ± 0.078 (3) |
| 2 | p99 latency, bench_main's (ms) | 22.622 ± 0.078 (3) |
| 2 | MPP hold p50 (ms) | 4.946 ± 0.030 (3) |
| 2 | MPP hold p99 (ms) | 5.263 ± 0.027 (3) |
| 2 | slot wait p99 (ms) | 0.001 ± 0.000 (3) |
| 2 | inference rate, lowest channel (fps) | 5.000 ± 0.000 (3) |
| 2 | inference rate, channel mean (fps) | 5.000 ± 0.000 (3) |
| 2 | inference rate, highest channel (fps) | 5.000 ± 0.000 (3) |
| 2 | drop rate | 0.000 ± 0.000 (3) |
| 2 | decoder drops | 0.000 ± 0.000 (3) |
| 2 | largest drift (s) | 0.000 ± 0.000 (3) |
| 2 | largest RSS (kB) | 200453.333 ± 1617.585 (3) |
| 2 | highest temperature (m°C) | 41922.667 ± 532.894 (3) |

## Flags

- main 4 ch C: p99 latency (ms) CV 14.0%
- main 8 ch B: p99 latency (ms) CV 11.7%
- main 16 ch B: p99 latency (ms) CV 17.6%
- aux 4 ch C: p99 latency (ms) CV 24.3%
- aux 5 ch B: p99 latency (ms) CV 13.5%
- aux 5 ch C: p99 latency (ms) CV 15.5%

## Invalid x2

None.

## Attempts that do not count

Repeated invalid attempts, runs that a later session redid, and attempts cut short (§9.1, §9.3). Until a sweep is complete, the runs of its repetitions still in progress are listed here too.
None.

## Warnings: samples at a trip point

None.

## M1.1: 4K input (exploratory, A11)

Exploratory (A11): no hypothesis is tested, and nothing here is judged. Videos ①, ② and ④ at 3840×2160, 16 Mbps (stream-server/assignment_4k.txt), main condition. Next to them are M1's main-condition values at the same counts, from the main sweep. Equivalence under A5 is not claimed at 4K. The flags and lists below are the 4K sessions' own.

Every point is complete.

| Channels | Metric | B, 4K: mean ± sd (n) | C, 4K (A6 label): mean ± sd (n) | B, M1 main: mean ± sd (n) | C, M1 main (A6 label): mean ± sd (n) |
|---|---|---|---|---|---|
| 1 | process CPU (s) | 10.747 ± 0.031 (3) | 4.400 ± 0.046 (3) | 4.957 ± 0.015 (3) | 2.150 ± 0.040 (3) |
| 1 | process CPU, A76 (s) | 10.633 ± 0.025 (3) | 4.290 ± 0.062 (3) | 4.853 ± 0.006 (3) | 2.043 ± 0.042 (3) |
| 1 | process CPU, A55 (s) | 0.100 ± 0.000 (3) | 0.100 ± 0.000 (3) | 0.097 ± 0.006 (3) | 0.097 ± 0.006 (3) |
| 1 | process CPU, unattributed (s) | 0.013 ± 0.006 (3) | 0.010 ± 0.017 (3) | 0.007 ± 0.015 (3) | 0.010 ± 0.017 (3) |
| 1 | system cpu0 (s) | 1.193 ± 0.214 (3) | 1.027 ± 0.045 (3) | 0.777 ± 0.352 (3) | 0.483 ± 0.283 (3) |
| 1 | system cpu1 (s) | 0.187 ± 0.080 (3) | 0.377 ± 0.190 (3) | 0.103 ± 0.025 (3) | 0.117 ± 0.031 (3) |
| 1 | system cpu2 (s) | 0.200 ± 0.141 (3) | 0.183 ± 0.097 (3) | 0.107 ± 0.021 (3) | 0.083 ± 0.025 (3) |
| 1 | system cpu3 (s) | 0.160 ± 0.125 (3) | 0.070 ± 0.056 (3) | 0.120 ± 0.040 (3) | 0.103 ± 0.023 (3) |
| 1 | system cpu4 (s) | 0.800 ± 0.962 (3) | 0.390 ± 0.420 (3) | 0.423 ± 0.370 (3) | 0.107 ± 0.176 (3) |
| 1 | system cpu5 (s) | 1.710 ± 2.559 (3) | 0.597 ± 0.086 (3) | 1.780 ± 1.691 (3) | 0.267 ± 0.297 (3) |
| 1 | system cpu6 (s) | 3.640 ± 2.700 (3) | 0.583 ± 0.482 (3) | 0.890 ± 0.963 (3) | 0.303 ± 0.086 (3) |
| 1 | system cpu7 (s) | 2.393 ± 1.833 (3) | 0.200 ± 0.261 (3) | 0.573 ± 0.351 (3) | 0.400 ± 0.308 (3) |
| 1 | system cpu0-3 (s) | 1.740 ± 0.297 (3) | 1.657 ± 0.206 (3) | 1.107 ± 0.378 (3) | 0.787 ± 0.272 (3) |
| 1 | system cpu4-5 (s) | 2.510 ± 2.216 (3) | 0.987 ± 0.505 (3) | 2.203 ± 1.690 (3) | 0.373 ± 0.267 (3) |
| 1 | system cpu6-7 (s) | 6.033 ± 1.567 (3) | 0.783 ± 0.742 (3) | 1.463 ± 0.655 (3) | 0.703 ± 0.270 (3) |
| 1 | system cpu total (s) | 10.283 ± 1.635 (3) | 3.427 ± 0.851 (3) | 4.773 ± 1.439 (3) | 1.863 ± 0.566 (3) |
| 1 | system cpu0 minus idle (s) | 1.023 ± 0.214 (3) | 0.857 ± 0.045 (3) | 0.207 ± 0.352 (3) | -0.087 ± 0.283 (3) |
| 1 | system cpu1 minus idle (s) | 0.177 ± 0.080 (3) | 0.367 ± 0.190 (3) | 0.003 ± 0.025 (3) | 0.017 ± 0.031 (3) |
| 1 | system cpu2 minus idle (s) | 0.150 ± 0.141 (3) | 0.133 ± 0.097 (3) | 0.057 ± 0.021 (3) | 0.033 ± 0.025 (3) |
| 1 | system cpu3 minus idle (s) | 0.040 ± 0.125 (3) | -0.050 ± 0.056 (3) | 0.080 ± 0.040 (3) | 0.063 ± 0.023 (3) |
| 1 | system cpu4 minus idle (s) | 0.780 ± 0.962 (3) | 0.370 ± 0.420 (3) | 0.423 ± 0.370 (3) | 0.107 ± 0.176 (3) |
| 1 | system cpu5 minus idle (s) | 1.680 ± 2.559 (3) | 0.567 ± 0.086 (3) | 1.770 ± 1.691 (3) | 0.257 ± 0.297 (3) |
| 1 | system cpu6 minus idle (s) | 3.610 ± 2.700 (3) | 0.553 ± 0.482 (3) | 0.840 ± 0.963 (3) | 0.253 ± 0.086 (3) |
| 1 | system cpu7 minus idle (s) | 2.323 ± 1.833 (3) | 0.130 ± 0.261 (3) | 0.563 ± 0.351 (3) | 0.390 ± 0.308 (3) |
| 1 | system cpu0-3 minus idle (s) | 1.390 ± 0.297 (3) | 1.307 ± 0.206 (3) | 0.347 ± 0.378 (3) | 0.027 ± 0.272 (3) |
| 1 | system cpu4-5 minus idle (s) | 2.460 ± 2.216 (3) | 0.937 ± 0.505 (3) | 2.193 ± 1.690 (3) | 0.363 ± 0.267 (3) |
| 1 | system cpu6-7 minus idle (s) | 5.933 ± 1.567 (3) | 0.683 ± 0.742 (3) | 1.403 ± 0.655 (3) | 0.643 ± 0.270 (3) |
| 1 | system cpu total minus idle (s) | 9.783 ± 1.635 (3) | 2.927 ± 0.851 (3) | 3.943 ± 1.439 (3) | 1.033 ± 0.566 (3) |
| 1 | p50 latency (ms) | 28.713 ± 0.064 (3) | 22.824 ± 0.019 (3) | 21.882 ± 0.063 (3) | 18.132 ± 0.422 (3) |
| 1 | p99 latency (ms) | 29.168 ± 0.173 (3) | 23.216 ± 0.033 (3) | 22.226 ± 0.050 (3) | 18.788 ± 0.061 (3) |
| 1 | p99 latency, bench_main's (ms) | 29.168 ± 0.173 (3) | 23.216 ± 0.033 (3) | 22.226 ± 0.050 (3) | 18.788 ± 0.061 (3) |
| 1 | MPP hold p50 (ms) | 11.852 ± 0.165 (3) | 6.065 ± 0.003 (3) | 4.964 ± 0.078 (3) | 1.715 ± 0.004 (3) |
| 1 | MPP hold p99 (ms) | 12.031 ± 0.198 (3) | 6.090 ± 0.003 (3) | 5.021 ± 0.012 (3) | 1.731 ± 0.009 (3) |
| 1 | slot wait p99 (ms) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) |
| 1 | inference rate, lowest channel (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 1 | inference rate, channel mean (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 1 | inference rate, highest channel (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 1 | drop rate | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 1 | decoder drops | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 1 | largest drift (s) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 1 | largest RSS (kB) | 144653.333 ± 132.323 (3) | 143229.333 ± 224.867 (3) | 89117.333 ± 411.015 (3) | 87865.333 ± 374.045 (3) |
| 1 | highest temperature (m°C) | 39153.667 ± 532.894 (3) | 39461.333 ± 532.894 (3) | 40076.667 ± 532.894 (3) | 39769.000 ± 0.000 (3) |
| 2 | process CPU (s) | 21.973 ± 0.163 (3) | 8.803 ± 0.368 (3) | 10.133 ± 0.040 (3) | 4.350 ± 0.202 (3) |
| 2 | process CPU, A76 (s) | 21.870 ± 0.181 (3) | 8.673 ± 0.369 (3) | 10.017 ± 0.031 (3) | 4.250 ± 0.208 (3) |
| 2 | process CPU, A55 (s) | 0.103 ± 0.006 (3) | 0.100 ± 0.000 (3) | 0.110 ± 0.000 (3) | 0.100 ± 0.000 (3) |
| 2 | process CPU, unattributed (s) | 0.000 ± 0.017 (3) | 0.030 ± 0.010 (3) | 0.007 ± 0.015 (3) | 0.000 ± 0.026 (3) |
| 2 | system cpu0 (s) | 2.170 ± 1.053 (3) | 1.247 ± 0.333 (3) | 1.150 ± 0.465 (3) | 0.867 ± 0.356 (3) |
| 2 | system cpu1 (s) | 0.497 ± 0.160 (3) | 0.517 ± 0.177 (3) | 0.427 ± 0.127 (3) | 0.263 ± 0.162 (3) |
| 2 | system cpu2 (s) | 0.097 ± 0.045 (3) | 0.090 ± 0.010 (3) | 0.147 ± 0.074 (3) | 0.207 ± 0.115 (3) |
| 2 | system cpu3 (s) | 0.277 ± 0.350 (3) | 0.180 ± 0.151 (3) | 0.150 ± 0.217 (3) | 0.103 ± 0.059 (3) |
| 2 | system cpu4 (s) | 7.113 ± 3.260 (3) | 1.273 ± 0.289 (3) | 2.250 ± 1.024 (3) | 0.447 ± 0.193 (3) |
| 2 | system cpu5 (s) | 7.773 ± 3.796 (3) | 3.097 ± 3.875 (3) | 2.373 ± 0.909 (3) | 1.073 ± 0.979 (3) |
| 2 | system cpu6 (s) | 5.067 ± 3.282 (3) | 2.057 ± 0.793 (3) | 1.500 ± 0.603 (3) | 0.733 ± 0.333 (3) |
| 2 | system cpu7 (s) | 2.090 ± 1.845 (3) | 2.317 ± 3.114 (3) | 1.960 ± 0.504 (3) | 1.173 ± 1.403 (3) |
| 2 | system cpu0-3 (s) | 3.040 ± 1.591 (3) | 2.033 ± 0.649 (3) | 1.873 ± 0.864 (3) | 1.440 ± 0.656 (3) |
| 2 | system cpu4-5 (s) | 14.887 ± 1.591 (3) | 4.370 ± 3.711 (3) | 4.623 ± 1.925 (3) | 1.520 ± 0.985 (3) |
| 2 | system cpu6-7 (s) | 7.157 ± 5.010 (3) | 4.373 ± 3.335 (3) | 3.460 ± 1.090 (3) | 1.907 ± 1.718 (3) |
| 2 | system cpu total (s) | 25.083 ± 6.622 (3) | 10.777 ± 4.400 (3) | 9.957 ± 3.874 (3) | 4.867 ± 3.233 (3) |
| 2 | system cpu0 minus idle (s) | 2.000 ± 1.053 (3) | 1.077 ± 0.333 (3) | 0.580 ± 0.465 (3) | 0.297 ± 0.356 (3) |
| 2 | system cpu1 minus idle (s) | 0.487 ± 0.160 (3) | 0.507 ± 0.177 (3) | 0.327 ± 0.127 (3) | 0.163 ± 0.162 (3) |
| 2 | system cpu2 minus idle (s) | 0.047 ± 0.045 (3) | 0.040 ± 0.010 (3) | 0.097 ± 0.074 (3) | 0.157 ± 0.115 (3) |
| 2 | system cpu3 minus idle (s) | 0.157 ± 0.350 (3) | 0.060 ± 0.151 (3) | 0.110 ± 0.217 (3) | 0.063 ± 0.059 (3) |
| 2 | system cpu4 minus idle (s) | 7.093 ± 3.260 (3) | 1.253 ± 0.289 (3) | 2.250 ± 1.024 (3) | 0.447 ± 0.193 (3) |
| 2 | system cpu5 minus idle (s) | 7.743 ± 3.796 (3) | 3.067 ± 3.875 (3) | 2.363 ± 0.909 (3) | 1.063 ± 0.979 (3) |
| 2 | system cpu6 minus idle (s) | 5.037 ± 3.282 (3) | 2.027 ± 0.793 (3) | 1.450 ± 0.603 (3) | 0.683 ± 0.333 (3) |
| 2 | system cpu7 minus idle (s) | 2.020 ± 1.845 (3) | 2.247 ± 3.114 (3) | 1.950 ± 0.504 (3) | 1.163 ± 1.403 (3) |
| 2 | system cpu0-3 minus idle (s) | 2.690 ± 1.591 (3) | 1.683 ± 0.649 (3) | 1.113 ± 0.864 (3) | 0.680 ± 0.656 (3) |
| 2 | system cpu4-5 minus idle (s) | 14.837 ± 1.591 (3) | 4.320 ± 3.711 (3) | 4.613 ± 1.925 (3) | 1.510 ± 0.985 (3) |
| 2 | system cpu6-7 minus idle (s) | 7.057 ± 5.010 (3) | 4.273 ± 3.335 (3) | 3.400 ± 1.090 (3) | 1.847 ± 1.718 (3) |
| 2 | system cpu total minus idle (s) | 24.583 ± 6.622 (3) | 10.277 ± 4.400 (3) | 9.127 ± 3.874 (3) | 4.037 ± 3.233 (3) |
| 2 | p50 latency (ms) | 27.781 ± 0.319 (3) | 21.181 ± 0.031 (3) | 21.769 ± 0.035 (3) | 16.961 ± 0.091 (3) |
| 2 | p99 latency (ms) | 29.754 ± 0.292 (3) | 22.941 ± 0.097 (3) | 22.444 ± 0.006 (3) | 18.911 ± 0.060 (3) |
| 2 | p99 latency, bench_main's (ms) | 29.754 ± 0.292 (3) | 22.940 ± 0.097 (3) | 22.444 ± 0.006 (3) | 18.911 ± 0.060 (3) |
| 2 | MPP hold p50 (ms) | 12.110 ± 0.020 (3) | 6.054 ± 0.011 (3) | 5.033 ± 0.010 (3) | 1.719 ± 0.005 (3) |
| 2 | MPP hold p99 (ms) | 12.409 ± 0.230 (3) | 6.095 ± 0.001 (3) | 5.120 ± 0.088 (3) | 1.751 ± 0.002 (3) |
| 2 | slot wait p99 (ms) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) |
| 2 | inference rate, lowest channel (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 2 | inference rate, channel mean (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 2 | inference rate, highest channel (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 2 | drop rate | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 2 | decoder drops | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 2 | largest drift (s) | 0.011 ± 0.019 (3) | 0.000 ± 0.000 (3) | 0.011 ± 0.019 (3) | 0.000 ± 0.000 (3) |
| 2 | largest RSS (kB) | 221012.000 ± 410.483 (3) | 219705.333 ± 2.309 (3) | 110945.333 ± 6.110 (3) | 109377.333 ± 286.784 (3) |
| 2 | highest temperature (m°C) | 42230.333 ± 532.894 (3) | 42230.333 ± 1065.789 (3) | 41307.333 ± 1921.378 (3) | 39769.000 ± 0.000 (3) |
| 4 | process CPU (s) | 45.580 ± 0.229 (3) | 18.317 ± 1.259 (3) | 20.867 ± 0.372 (3) | 8.827 ± 0.276 (3) |
| 4 | process CPU, A76 (s) | 45.497 ± 0.221 (3) | 18.227 ± 1.231 (3) | 20.757 ± 0.394 (3) | 8.723 ± 0.252 (3) |
| 4 | process CPU, A55 (s) | 0.107 ± 0.006 (3) | 0.107 ± 0.006 (3) | 0.100 ± 0.000 (3) | 0.097 ± 0.006 (3) |
| 4 | process CPU, unattributed (s) | -0.023 ± 0.015 (3) | -0.017 ± 0.035 (3) | 0.010 ± 0.026 (3) | 0.007 ± 0.031 (3) |
| 4 | system cpu0 (s) | 1.770 ± 0.800 (3) | 2.743 ± 1.467 (3) | 1.913 ± 0.551 (3) | 1.160 ± 0.265 (3) |
| 4 | system cpu1 (s) | 1.213 ± 0.487 (3) | 1.803 ± 0.886 (3) | 0.657 ± 0.310 (3) | 0.557 ± 0.142 (3) |
| 4 | system cpu2 (s) | 0.313 ± 0.172 (3) | 0.407 ± 0.230 (3) | 0.370 ± 0.118 (3) | 0.170 ± 0.142 (3) |
| 4 | system cpu3 (s) | 0.240 ± 0.145 (3) | 0.227 ± 0.061 (3) | 0.077 ± 0.057 (3) | 0.100 ± 0.053 (3) |
| 4 | system cpu4 (s) | 9.070 ± 2.868 (3) | 4.803 ± 2.080 (3) | 5.257 ± 1.875 (3) | 1.067 ± 0.212 (3) |
| 4 | system cpu5 (s) | 11.003 ± 2.906 (3) | 4.477 ± 3.247 (3) | 5.310 ± 1.918 (3) | 1.303 ± 0.155 (3) |
| 4 | system cpu6 (s) | 9.530 ± 4.102 (3) | 5.743 ± 2.066 (3) | 5.057 ± 1.813 (3) | 1.550 ± 0.440 (3) |
| 4 | system cpu7 (s) | 12.660 ± 4.642 (3) | 4.960 ± 3.235 (3) | 5.190 ± 1.668 (3) | 1.413 ± 0.408 (3) |
| 4 | system cpu0-3 (s) | 3.537 ± 0.798 (3) | 5.180 ± 2.373 (3) | 3.017 ± 0.142 (3) | 1.987 ± 0.147 (3) |
| 4 | system cpu4-5 (s) | 20.073 ± 1.640 (3) | 9.280 ± 5.081 (3) | 10.567 ± 3.553 (3) | 2.370 ± 0.095 (3) |
| 4 | system cpu6-7 (s) | 22.190 ± 6.596 (3) | 10.703 ± 5.294 (3) | 10.247 ± 3.414 (3) | 2.963 ± 0.832 (3) |
| 4 | system cpu total (s) | 45.800 ± 7.614 (3) | 25.163 ± 11.056 (3) | 23.830 ± 7.094 (3) | 7.320 ± 1.020 (3) |
| 4 | system cpu0 minus idle (s) | 1.600 ± 0.800 (3) | 2.573 ± 1.467 (3) | 1.343 ± 0.551 (3) | 0.590 ± 0.265 (3) |
| 4 | system cpu1 minus idle (s) | 1.203 ± 0.487 (3) | 1.793 ± 0.886 (3) | 0.557 ± 0.310 (3) | 0.457 ± 0.142 (3) |
| 4 | system cpu2 minus idle (s) | 0.263 ± 0.172 (3) | 0.357 ± 0.230 (3) | 0.320 ± 0.118 (3) | 0.120 ± 0.142 (3) |
| 4 | system cpu3 minus idle (s) | 0.120 ± 0.145 (3) | 0.107 ± 0.061 (3) | 0.037 ± 0.057 (3) | 0.060 ± 0.053 (3) |
| 4 | system cpu4 minus idle (s) | 9.050 ± 2.868 (3) | 4.783 ± 2.080 (3) | 5.257 ± 1.875 (3) | 1.067 ± 0.212 (3) |
| 4 | system cpu5 minus idle (s) | 10.973 ± 2.906 (3) | 4.447 ± 3.247 (3) | 5.300 ± 1.918 (3) | 1.293 ± 0.155 (3) |
| 4 | system cpu6 minus idle (s) | 9.500 ± 4.102 (3) | 5.713 ± 2.066 (3) | 5.007 ± 1.813 (3) | 1.500 ± 0.440 (3) |
| 4 | system cpu7 minus idle (s) | 12.590 ± 4.642 (3) | 4.890 ± 3.235 (3) | 5.180 ± 1.668 (3) | 1.403 ± 0.408 (3) |
| 4 | system cpu0-3 minus idle (s) | 3.187 ± 0.798 (3) | 4.830 ± 2.373 (3) | 2.257 ± 0.142 (3) | 1.227 ± 0.147 (3) |
| 4 | system cpu4-5 minus idle (s) | 20.023 ± 1.640 (3) | 9.230 ± 5.081 (3) | 10.557 ± 3.553 (3) | 2.360 ± 0.095 (3) |
| 4 | system cpu6-7 minus idle (s) | 22.090 ± 6.596 (3) | 10.603 ± 5.294 (3) | 10.187 ± 3.414 (3) | 2.903 ± 0.832 (3) |
| 4 | system cpu total minus idle (s) | 45.300 ± 7.614 (3) | 24.663 ± 11.056 (3) | 23.000 ± 7.094 (3) | 6.490 ± 1.020 (3) |
| 4 | p50 latency (ms) | 28.990 ± 0.656 (3) | 22.242 ± 0.128 (3) | 20.990 ± 0.115 (3) | 17.626 ± 0.192 (3) |
| 4 | p99 latency (ms) | 35.410 ± 0.985 (3) | 23.581 ± 0.626 (3) | 32.418 ± 1.880 (3) | 22.666 ± 3.165 (3) |
| 4 | p99 latency, bench_main's (ms) | 35.410 ± 0.985 (3) | 23.581 ± 0.626 (3) | 32.418 ± 1.880 (3) | 22.666 ± 3.165 (3) |
| 4 | MPP hold p50 (ms) | 12.689 ± 0.587 (3) | 6.078 ± 0.038 (3) | 5.099 ± 0.025 (3) | 1.684 ± 0.017 (3) |
| 4 | MPP hold p99 (ms) | 18.548 ± 0.801 (3) | 7.164 ± 0.979 (3) | 5.651 ± 0.184 (3) | 2.866 ± 0.965 (3) |
| 4 | slot wait p99 (ms) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) |
| 4 | inference rate, lowest channel (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 4 | inference rate, channel mean (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 4 | inference rate, highest channel (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 4 | drop rate | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 4 | decoder drops | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 4 | largest drift (s) | 0.011 ± 0.019 (3) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 4 | largest RSS (kB) | 375238.667 ± 347.340 (3) | 374252.000 ± 1238.664 (3) | 153166.667 ± 359.652 (3) | 151918.667 ± 150.537 (3) |
| 4 | highest temperature (m°C) | 45614.667 ± 532.894 (3) | 45614.667 ± 1065.789 (3) | 42845.667 ± 532.894 (3) | 41922.667 ± 532.894 (3) |
| 8 | process CPU (s) | 96.150 ± 0.665 (3) | 39.517 ± 0.627 (3) | 43.283 ± 0.343 (3) | 18.560 ± 0.308 (3) |
| 8 | process CPU, A76 (s) | 95.993 ± 0.691 (3) | 39.413 ± 0.661 (3) | 43.173 ± 0.348 (3) | 18.470 ± 0.370 (3) |
| 8 | process CPU, A55 (s) | 0.137 ± 0.015 (3) | 0.103 ± 0.015 (3) | 0.103 ± 0.006 (3) | 0.110 ± 0.000 (3) |
| 8 | process CPU, unattributed (s) | 0.020 ± 0.036 (3) | 0.000 ± 0.035 (3) | 0.007 ± 0.050 (3) | -0.020 ± 0.072 (3) |
| 8 | system cpu0 (s) | 5.543 ± 1.478 (3) | 2.680 ± 1.235 (3) | 2.527 ± 0.595 (3) | 1.760 ± 0.763 (3) |
| 8 | system cpu1 (s) | 10.937 ± 6.607 (3) | 6.733 ± 0.729 (3) | 1.357 ± 1.021 (3) | 0.657 ± 0.125 (3) |
| 8 | system cpu2 (s) | 1.950 ± 1.257 (3) | 0.847 ± 0.395 (3) | 0.507 ± 0.463 (3) | 0.207 ± 0.072 (3) |
| 8 | system cpu3 (s) | 0.820 ± 0.531 (3) | 0.800 ± 0.316 (3) | 0.580 ± 0.168 (3) | 0.233 ± 0.012 (3) |
| 8 | system cpu4 (s) | 27.217 ± 5.207 (3) | 8.753 ± 2.834 (3) | 11.660 ± 1.394 (3) | 2.047 ± 0.220 (3) |
| 8 | system cpu5 (s) | 27.797 ± 6.323 (3) | 10.290 ± 5.294 (3) | 11.240 ± 1.585 (3) | 2.103 ± 0.384 (3) |
| 8 | system cpu6 (s) | 27.163 ± 5.543 (3) | 9.437 ± 5.454 (3) | 11.817 ± 1.217 (3) | 2.133 ± 0.483 (3) |
| 8 | system cpu7 (s) | 25.927 ± 6.006 (3) | 8.837 ± 4.914 (3) | 12.053 ± 1.319 (3) | 2.027 ± 0.376 (3) |
| 8 | system cpu0-3 (s) | 19.250 ± 9.682 (3) | 11.060 ± 2.365 (3) | 4.970 ± 1.876 (3) | 2.857 ± 0.671 (3) |
| 8 | system cpu4-5 (s) | 55.013 ± 10.780 (3) | 19.043 ± 8.059 (3) | 22.900 ± 2.969 (3) | 4.150 ± 0.566 (3) |
| 8 | system cpu6-7 (s) | 53.090 ± 11.542 (3) | 18.273 ± 10.368 (3) | 23.870 ± 2.330 (3) | 4.160 ± 0.815 (3) |
| 8 | system cpu total (s) | 127.353 ± 31.834 (3) | 48.377 ± 20.590 (3) | 51.740 ± 6.971 (3) | 11.167 ± 1.816 (3) |
| 8 | system cpu0 minus idle (s) | 5.373 ± 1.478 (3) | 2.510 ± 1.235 (3) | 1.957 ± 0.595 (3) | 1.190 ± 0.763 (3) |
| 8 | system cpu1 minus idle (s) | 10.927 ± 6.607 (3) | 6.723 ± 0.729 (3) | 1.257 ± 1.021 (3) | 0.557 ± 0.125 (3) |
| 8 | system cpu2 minus idle (s) | 1.900 ± 1.257 (3) | 0.797 ± 0.395 (3) | 0.457 ± 0.463 (3) | 0.157 ± 0.072 (3) |
| 8 | system cpu3 minus idle (s) | 0.700 ± 0.531 (3) | 0.680 ± 0.316 (3) | 0.540 ± 0.168 (3) | 0.193 ± 0.012 (3) |
| 8 | system cpu4 minus idle (s) | 27.197 ± 5.207 (3) | 8.733 ± 2.834 (3) | 11.660 ± 1.394 (3) | 2.047 ± 0.220 (3) |
| 8 | system cpu5 minus idle (s) | 27.767 ± 6.323 (3) | 10.260 ± 5.294 (3) | 11.230 ± 1.585 (3) | 2.093 ± 0.384 (3) |
| 8 | system cpu6 minus idle (s) | 27.133 ± 5.543 (3) | 9.407 ± 5.454 (3) | 11.767 ± 1.217 (3) | 2.083 ± 0.483 (3) |
| 8 | system cpu7 minus idle (s) | 25.857 ± 6.006 (3) | 8.767 ± 4.914 (3) | 12.043 ± 1.319 (3) | 2.017 ± 0.376 (3) |
| 8 | system cpu0-3 minus idle (s) | 18.900 ± 9.682 (3) | 10.710 ± 2.365 (3) | 4.210 ± 1.876 (3) | 2.097 ± 0.671 (3) |
| 8 | system cpu4-5 minus idle (s) | 54.963 ± 10.780 (3) | 18.993 ± 8.059 (3) | 22.890 ± 2.969 (3) | 4.140 ± 0.566 (3) |
| 8 | system cpu6-7 minus idle (s) | 52.990 ± 11.542 (3) | 18.173 ± 10.368 (3) | 23.810 ± 2.330 (3) | 4.100 ± 0.815 (3) |
| 8 | system cpu total minus idle (s) | 126.853 ± 31.834 (3) | 47.877 ± 20.590 (3) | 50.910 ± 6.971 (3) | 10.337 ± 1.816 (3) |
| 8 | p50 latency (ms) | 29.278 ± 0.412 (3) | 22.510 ± 0.068 (3) | 33.117 ± 4.543 (3) | 30.340 ± 0.961 (3) |
| 8 | p99 latency (ms) | 32.412 ± 1.865 (3) | 25.336 ± 2.201 (3) | 47.395 ± 5.538 (3) | 43.959 ± 1.859 (3) |
| 8 | p99 latency, bench_main's (ms) | 32.412 ± 1.865 (3) | 25.336 ± 2.201 (3) | 47.395 ± 5.538 (3) | 43.959 ± 1.859 (3) |
| 8 | MPP hold p50 (ms) | 12.594 ± 0.156 (3) | 6.086 ± 0.012 (3) | 8.628 ± 2.862 (3) | 1.751 ± 0.046 (3) |
| 8 | MPP hold p99 (ms) | 14.668 ± 2.423 (3) | 8.156 ± 3.406 (3) | 12.911 ± 3.258 (3) | 3.951 ± 0.591 (3) |
| 8 | slot wait p99 (ms) | 0.001 ± 0.001 (3) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) | 0.001 ± 0.000 (3) |
| 8 | inference rate, lowest channel (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 8 | inference rate, channel mean (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 8 | inference rate, highest channel (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) |
| 8 | drop rate | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 8 | decoder drops | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) |
| 8 | largest drift (s) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) | 0.000 ± 0.000 (3) | 0.011 ± 0.019 (3) |
| 8 | largest RSS (kB) | 689536.000 ± 1349.441 (3) | 679741.333 ± 4804.007 (3) | 241725.333 ± 1064.153 (3) | 236780.000 ± 1686.004 (3) |
| 8 | highest temperature (m°C) | 54538.000 ± 0.000 (3) | 50538.333 ± 532.894 (3) | 47153.000 ± 0.000 (3) | 45307.000 ± 0.000 (3) |

### Flags

No process CPU or p99 latency value has a coefficient of variation above 10%.

### Invalid x2

None.

### Attempts that do not count

Repeated invalid attempts, runs that a later session redid, and attempts cut short (§9.1, §9.3). Until a sweep is complete, the runs of its repetitions still in progress are listed here too.
None.

### Warnings: samples at a trip point

None.

## M1.1: the designs of §13 (exploratory, A12)

Exploratory (A12): the optional designs of PROTOCOL §13, each with its controls rerun in its own sessions, on M1's 1080p videos. Nothing is judged, and no value here is set against M1's: the binary differs. Bd is B-direct; Bk4 is B with K = 4; in a55, B and C run on cpu0-3, with the metrics thread on cpu4. The flags and lists of each design are its sessions' own.

### B with K = 4 (`k4`): 8 channels, main condition

No session yet.

### A55 cores only (`a55`): 8 channels, main condition, one repetition

No session yet.

### B-direct (`bdirect_main`): 8 channels, main condition

No session yet.

### B-direct (`bdirect_aux`): 5 channels, auxiliary condition

No session yet.
