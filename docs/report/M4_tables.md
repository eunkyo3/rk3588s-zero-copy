# M4: tables

From the records of kind `m4` under `results/m4/`, made after `freeze-m4` (`6722138`) ([prereg/M4.md](../../prereg/M4.md), AMENDMENTS A14). M4 has no hypotheses: nothing here is a verdict. Method: `tools/m4_report.py`.

## Accuracy (§2, §6)

COCOeval (`iouType` bbox, default parameters) over the 5,000 images of COCO val2017, in percent. INT8 runs on the board's NPU, FP32 on the PC (ONNX Runtime), on the same letterboxed inputs. The rule is §2.3's: conf 0.001, at most 300 detections per image; YOLOv8n with class-wise NMS at 0.7, YOLOv10n without NMS.

| Model | Precision | AP@[.5:.95] | AP50 | AP75 | AP small | AP medium | AP large | AR@100 |
|---|---|---|---|---|---|---|---|---|
| YOLOv8n | INT8 | 36.8 | 51.9 | 40.1 | 18.3 | 40.6 | 52.7 | 56.9 |
| YOLOv8n | FP32 | 37.3 | 52.6 | 40.5 | 18.7 | 41.0 | 53.3 | 59.3 |
| YOLOv10n | INT8 | 37.8 | 52.7 | 41.0 | 18.6 | 41.6 | 54.7 | 57.6 |
| YOLOv10n | FP32 | 38.4 | 53.5 | 41.7 | 19.0 | 42.1 | 54.8 | 60.3 |

**Quantization cost, INT8 − FP32** (percentage points):

| Model | AP@[.5:.95] | AP50 | AP75 | AP small | AP medium | AP large | AR@100 |
|---|---|---|---|---|---|---|---|
| YOLOv8n | -0.51 | -0.68 | -0.46 | -0.37 | -0.48 | -0.61 | -2.37 |
| YOLOv10n | -0.63 | -0.77 | -0.68 | -0.45 | -0.55 | -0.10 | -2.66 |

**Without the 20 quantization images** (4,980 images, COCOeval's `params.imgIds`; the 5,000-image values are the main ones):

| Model | Evaluated | AP@[.5:.95], 5,000 | AP@[.5:.95], 4,980 | AP50, 5,000 | AP50, 4,980 |
|---|---|---|---|---|---|
| YOLOv8n | INT8 | 36.83 | 36.84 | 51.87 | 51.89 |
| YOLOv8n | FP32 | 37.33 | 37.35 | 52.55 | 52.57 |
| YOLOv8n | INT8, the pipeline's settings | 30.57 | 30.57 | 41.36 | 41.36 |
| YOLOv10n | INT8 | 37.76 | 37.77 | 52.71 | 52.71 |
| YOLOv10n | FP32 | 38.39 | 38.40 | 53.48 | 53.51 |
| YOLOv10n | INT8, the pipeline's settings | 30.18 | 30.19 | 39.92 | 39.92 |

**The pipeline's settings** (§2.4), INT8 on the board: the post-processing the pipeline runs, evaluated on COCO's still images. Not comparable with published values (§2.4).

| Model | Post-processing | AP@[.5:.95] | AP50 | AP75 | AP small | AP medium | AP large | AR@100 |
|---|---|---|---|---|---|---|---|---|
| YOLOv8n | NeonPost (arm Cp), conf 0.25, NMS 0.45; yolov8_post compared field for field | 30.6 | 41.4 | 33.5 | 10.7 | 33.7 | 46.8 | 35.4 |
| YOLOv10n | yolov10_post, conf 0.25, max 300, no NMS | 30.2 | 39.9 | 33.3 | 9.4 | 32.7 | 48.3 | 34.6 |

**Context: the published values** (§5), obtained under other settings (Ultralytics' and THU-MIG's own validation, rectangular inputs, their own exports):

| Model | Published AP@[.5:.95] | M4's FP32, rounded to 0.1 | Difference (points) | §5's trigger (more than 2.0) |
|---|---|---|---|---|
| YOLOv8n | 37.3 | 37.3 | +0.0 | not fired |
| YOLOv10n | 38.5 | 38.4 | -0.1 | not fired |

**Detections kept** (§6):

| Model | Evaluated | Record | Detections | Per image | Images at 300 | Of those, 300th score tied |
|---|---|---|---|---|---|---|
| YOLOv8n | INT8 | `20261118T042948Z_eval_int8_yolov8n_map` | 1,500,000 | 300.00 | 5,000 | 4,984 |
| YOLOv8n | FP32 | `20261118T045237Z_eval_fp32_yolov8n` | 881,820 | 176.36 | 1,556 | 2 |
| YOLOv8n | INT8, the pipeline's settings | `20261118T045952Z_eval_int8_yolov8n_pipeline` | 24,276 | 4.86 | 0 | 0 |
| YOLOv10n | INT8 | `20261118T044537Z_eval_int8_yolov10n_map` | 1,500,000 | 300.00 | 5,000 | 4,991 |
| YOLOv10n | FP32 | `20261118T045624Z_eval_fp32_yolov10n` | 720,008 | 144.00 | 1,135 | 2 |
| YOLOv10n | INT8, the pipeline's settings | `20261118T050044Z_eval_int8_yolov10n_pipeline` | 21,156 | 4.23 | 0 | 0 |

**The passes** (§2.5):

| Model | Precision | Record | Outcome | Images | Seconds | Checks |
|---|---|---|---|---|---|---|
| YOLOv8n | INT8 | `20261118T010957Z_int8_yolov8n` | valid | 5000 | 261 | bound outputs checked against `rknn_outputs_get` on 50 images; 0 of 50 dump images differ from §8 step 3's; NeonPost and `yolov8_post` differ on 0 images |
| YOLOv8n | FP32 | `20261118T011034Z_fp32_yolov8n` | valid | 5000 | 2971 | – |
| YOLOv10n | INT8 | `20261118T011620Z_int8_yolov10n` | valid | 5000 | 315 | bound outputs checked against `rknn_outputs_get` on 50 images; 0 of 50 dump images differ from §8 step 3's |
| YOLOv10n | FP32 | `20261118T012408Z_fp32_yolov10n` | valid | 5000 | 2229 | – |

## NPU capacity (§3.1)

From [`20261118T022716Z_m4_calib`](../../results/m4/20261118T022716Z_m4_calib) (24 °C), the session the pipeline sessions took. Medians of 3 runs on M1-3's 1,000 tensors; times are means over a run.

| Model | Measurement | Inferences/s | Valid runs | `rknn_run` (ms) | Bind (ms) | Post-processing (ms) | NPU load, cores 0/1/2 (%) |
|---|---|---|---|---|---|---|---|
| YOLOv8n | `P_cap`, 3 cores, with `neon` | 193.1667 | 3 | 15.378 | 0.024 | 0.128 | 92.0/92.0/92.0 |
| YOLOv8n | raw, 1 core | 69.2083 | 3 | 14.435 | 0.014 | – | 94.0/0.0/0.0 |
| YOLOv10n | `P_cap`, 3 cores, with `v10` | 161.8417 | 3 | 18.409 | 0.020 | 0.108 | 93.0/93.0/93.1 |
| YOLOv10n | raw, 1 core | 57.9083 | 3 | 17.254 | 0.014 | – | 94.0/0.0/0.0 |

For reference, M1-3 ([`20261102T073918Z_npu`](../../results/calibration/20261102T073918Z_npu), code `3789a7a`): YOLOv8n `P_cap` 194.2083, with M1's `yolov8_post`, not Cp's; raw rate on 1 core 70.7250.

**Saturation labels** (§3.2): a point is unsaturated for a model only if channels × fps ≤ 0.8 × `P_cap`.

| Point | Load (inferences/s) | C8 (yolov8n): 0.8 × `P_cap` | C10 (yolov10n): 0.8 × `P_cap` | C8's label | C10's label |
|---|---|---|---|---|---|
| main, 8 × 5 | 40 | 154.5 | 129.5 | unsaturated | unsaturated |
| aux, 5 × 30 | 150 | 154.5 | 129.5 | unsaturated | saturated |

A saturated arm's runs are still made. Rule 3 of PROTOCOL §9.3 does not judge them, and the comparison at that point is between unequal loads (§3.2).

## Pipeline (§3.2, §3.3)

C8 is YOLOv8n with Cp's post-processing (`--post neon`), C10 YOLOv10n with `yolov10_post` (`--post v10`), both variant C from the same binary. Values are the mean ± sample sd (valid runs) per 120 s window. Differences are C10_r − C8_r per repetition, with their mean and a descriptive 95% t interval (2 degrees of freedom); for CPU also (C10_r − C8_r) / C8_r. No threshold and no verdict (§3.2).

### m4_main: 8 channels × 5 fps, main point

Session `20261118T043338Z_m4_main` (25 °C). Saturation labels: C8 unsaturated, C10 unsaturated.

| Metric | C8 | C10 | C10_r − C8_r | Mean [95% t interval] |
|---|---|---|---|---|
| Process CPU (s) | 17.797 ± 0.570 (3) | 18.070 ± 1.031 (3) | r1 +1.480, r2 +0.880, r3 -1.540 | +0.273 [-3.698, +4.245] |
| Process CPU (s), (C10 − C8) / C8 | | | r1 +8.51%, r2 +5.02%, r3 -8.35% | +1.73% [-20.37%, +23.82%] |
| Process CPU, A76 (s) | 17.657 ± 0.594 (3) | 17.960 ± 1.038 (3) | r1 +1.510, r2 +0.950, r3 -1.550 | +0.303 [-3.744, +4.351] |
| Process CPU, A55 (s) | 0.120 ± 0.010 (3) | 0.100 ± 0.010 (3) | r1 -0.020, r2 -0.010, r3 -0.030 | -0.020 [-0.045, +0.005] |
| Process CPU, unattributed (s) | 0.020 ± 0.036 (3) | 0.010 ± 0.017 (3) | r1 -0.010, r2 -0.060, r3 +0.040 | -0.010 [-0.134, +0.114] |
| System CPU, /proc/schedstat, minus idle (s) | 20.043 ± 0.480 (3) | 20.374 ± 1.058 (3) | r1 +1.574, r2 +0.809, r3 -1.389 | +0.331 [-3.490, +4.152] |
| System CPU, /proc/schedstat, minus idle (s), (C10 − C8) / C8 | | | r1 +8.01%, r2 +4.07%, r3 -6.75% | +1.78% [-17.21%, +20.76%] |
| System CPU, /proc/stat, minus idle (s) | 26.843 ± 13.351 (3) | 15.967 ± 5.058 (3) | r1 -0.450, r2 -16.710, r3 -15.470 | -10.877 [-33.361, +11.607] |
| System CPU, /proc/stat, minus idle (s), (C10 − C8) / C8 | | | r1 -3.86%, r2 -52.02%, r3 -42.10% | -32.66% [-95.83%, +30.51%] |
| Latency p50 (ms) | 16.93 ± 0.06 (3) | 19.79 ± 0.08 (3) | r1 +2.85, r2 +2.86, r3 +2.89 | +2.87 [+2.82, +2.91] |
| Latency p99 (ms) | 17.66 ± 0.07 (3) | 21.02 ± 0.87 (3) | r1 +4.34, r2 +2.81, r3 +2.92 | +3.36 [+1.25, +5.47] |
| Drop rate | 0.00000 ± 0.00000 (3) | 0.00000 ± 0.00000 (3) | r1 +0.00000, r2 +0.00000, r3 +0.00000 | +0.00000 [+0.00000, +0.00000] |
| Inference rate, lowest channel (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | r1 +0.000, r2 +0.000, r3 +0.000 | +0.000 [+0.000, +0.000] |
| Inference rate, channel mean (fps) | 5.000 ± 0.000 (3) | 5.000 ± 0.000 (3) | r1 +0.000, r2 +0.000, r3 +0.000 | +0.000 [+0.000, +0.000] |
| Arrival phases' spread (ms) | 161.2 ± 0.4 (3) | 158.5 ± 5.7 (3) | r1 +3.2, r2 -2.4, r3 -8.8 | -2.7 [-17.6, +12.3] |
| Stagger error (ms) | 5.7 ± 1.5 (3) | 8.8 ± 3.6 (3) | r1 +1.9, r2 -0.5, r3 +7.7 | +3.0 [-7.5, +13.5] |
| Stage Q1 wait, p50 (ms) | 0.010 ± 0.001 (3) | 0.010 ± 0.001 (3) | r1 +0.001, r2 -0.000, r3 -0.001 | +0.000 [-0.003, +0.003] |
| Stage Q1 wait, p99 (ms) | 0.045 ± 0.053 (3) | 0.038 ± 0.041 (3) | r1 +0.072, r2 -0.001, r3 -0.095 | -0.008 [-0.215, +0.200] |
| Stage prepare, p50 (ms) | 1.713 ± 0.018 (3) | 1.700 ± 0.007 (3) | r1 +0.004, r2 -0.003, r3 -0.041 | -0.014 [-0.073, +0.046] |
| Stage prepare, p99 (ms) | 1.764 ± 0.016 (3) | 1.769 ± 0.032 (3) | r1 +0.055, r2 +0.001, r3 -0.041 | +0.005 [-0.115, +0.124] |
| Stage Q2 wait, p50 (ms) | 0.009 ± 0.000 (3) | 0.009 ± 0.000 (3) | r1 +0.000, r2 -0.001, r3 -0.001 | -0.000 [-0.002, +0.001] |
| Stage Q2 wait, p99 (ms) | 0.014 ± 0.001 (3) | 0.014 ± 0.002 (3) | r1 +0.002, r2 -0.002, r3 -0.001 | -0.000 [-0.005, +0.005] |
| Stage bind, p50 (ms) | 0.055 ± 0.006 (3) | 0.050 ± 0.005 (3) | r1 +0.006, r2 -0.006, r3 -0.016 | -0.005 [-0.032, +0.021] |
| Stage bind, p99 (ms) | 0.068 ± 0.002 (3) | 0.065 ± 0.006 (3) | r1 +0.006, r2 -0.004, r3 -0.011 | -0.003 [-0.024, +0.018] |
| Stage run, p50 (ms) | 15.023 ± 0.051 (3) | 17.915 ± 0.083 (3) | r1 +2.875, r2 +2.870, r3 +2.931 | +2.892 [+2.809, +2.976] |
| Stage run, p99 (ms) | 15.752 ± 0.093 (3) | 19.129 ± 0.875 (3) | r1 +4.392, r2 +2.842, r3 +2.898 | +3.377 [+1.193, +5.562] |
| Stage post, p50 (ms) | 0.119 ± 0.000 (3) | 0.091 ± 0.001 (3) | r1 -0.029, r2 -0.029, r3 -0.027 | -0.029 [-0.032, -0.025] |
| Stage post, p99 (ms) | 0.158 ± 0.001 (3) | 0.112 ± 0.010 (3) | r1 -0.053, r2 -0.053, r3 -0.034 | -0.047 [-0.074, -0.019] |

CPU by thread group (exploratory; `threads.csv`, the threads alive at the window's end):

| Thread group | C8 | C10 | C10_r − C8_r | Mean [95% t interval] |
|---|---|---|---|---|
| NPU workers (`npu-*`) (s) | 5.840 ± 0.090 (3) | 5.993 ± 0.049 (3) | r1 +0.220, r2 +0.120, r3 +0.120 | +0.153 [+0.010, +0.297] |
| Decoder (`dec-*`, `mpp_*`) (s) | 11.377 ± 0.497 (3) | 11.543 ± 1.074 (3) | r1 +1.290, r2 +0.830, r3 -1.620 | +0.167 [-3.719, +4.053] |
| Preprocessing (`pre-*`) (s) | 0.440 ± 0.017 (3) | 0.423 ± 0.012 (3) | r1 +0.000, r2 +0.000, r3 -0.050 | -0.017 [-0.088, +0.055] |
| The rest (`bench_main`, `metrics`) (s) | 0.120 ± 0.010 (3) | 0.100 ± 0.010 (3) | r1 -0.020, r2 -0.010, r3 -0.030 | -0.020 [-0.045, +0.005] |

CV of process CPU: C8 3.20%, C10 5.70%; none above 10% (PROTOCOL §9.3).

| Repetition | Arm | Run | Process CPU (s) | schedstat − idle (s) | `rknn_run` p50 (ms) | Post p50 (ms) | p99 (ms) |
|---|---|---|---|---|---|---|---|
| 1 | C8 | `20261118T043338Z_m4_main/01_m4_main_C8_8ch_r1` | 17.400 | 19.651 | 14.978 | 0.120 | 17.67 |
| 1 | C10 | `20261118T043338Z_m4_main/02_m4_main_C10_8ch_r1` | 18.880 | 21.225 | 17.854 | 0.090 | 22.01 |
| 2 | C8 | `20261118T043338Z_m4_main/04_m4_main_C8_8ch_r2` | 17.540 | 19.899 | 15.013 | 0.119 | 17.59 |
| 2 | C10 | `20261118T043338Z_m4_main/03_m4_main_C10_8ch_r2` | 18.420 | 20.708 | 17.883 | 0.090 | 20.40 |
| 3 | C8 | `20261118T043338Z_m4_main/05_m4_main_C8_8ch_r3` | 18.450 | 20.578 | 15.079 | 0.120 | 17.73 |
| 3 | C10 | `20261118T043338Z_m4_main/06_m4_main_C10_8ch_r3` | 16.910 | 19.189 | 18.010 | 0.092 | 20.65 |

Every repetition has both runs valid at the first attempt; no sample at a trip point.

### m4_aux: 5 channels × 30 fps, aux point

Session `20261118T053535Z_m4_aux` (25 °C). Saturation labels: C8 unsaturated, C10 saturated.

| Metric | C8 | C10 | C10_r − C8_r | Mean [95% t interval] |
|---|---|---|---|---|
| Process CPU (s) | 33.250 ± 1.051 (3) | 35.037 ± 0.999 (3) | r1 +0.680, r2 +1.970, r3 +2.710 | +1.787 [-0.765, +4.339] |
| Process CPU (s), (C10 − C8) / C8 | | | r1 +2.03%, r2 +5.77%, r3 +8.44% | +5.41% [-2.59%, +13.41%] |
| Process CPU, A76 (s) | 33.110 ± 1.044 (3) | 34.907 ± 0.977 (3) | r1 +0.730, r2 +1.960, r3 +2.700 | +1.797 [-0.675, +4.269] |
| Process CPU, A55 (s) | 0.147 ± 0.006 (3) | 0.117 ± 0.012 (3) | r1 -0.040, r2 -0.040, r3 -0.010 | -0.030 [-0.073, +0.013] |
| Process CPU, unattributed (s) | -0.007 ± 0.006 (3) | 0.013 ± 0.025 (3) | r1 -0.010, r2 +0.050, r3 +0.020 | +0.020 [-0.055, +0.095] |
| System CPU, /proc/schedstat, minus idle (s) | 36.144 ± 1.253 (3) | 38.221 ± 1.227 (3) | r1 +0.623, r2 +2.354, r3 +3.254 | +2.077 [-1.245, +5.399] |
| System CPU, /proc/schedstat, minus idle (s), (C10 − C8) / C8 | | | r1 +1.71%, r2 +6.33%, r3 +9.36% | +5.80% [-3.78%, +15.38%] |
| System CPU, /proc/stat, minus idle (s) | 39.963 ± 18.555 (3) | 49.767 ± 32.491 (3) | r1 +5.820, r2 +25.730, r3 -2.140 | +9.803 [-25.858, +45.465] |
| System CPU, /proc/stat, minus idle (s), (C10 − C8) / C8 | | | r1 +20.43%, r2 +41.93%, r3 -7.13% | +18.41% [-42.67%, +79.49%] |
| Latency p50 (ms) | 17.92 ± 0.36 (3) | 24.95 ± 0.55 (3) | r1 +6.79, r2 +6.54, r3 +7.77 | +7.04 [+5.42, +8.65] |
| Latency p99 (ms) | 23.87 ± 3.79 (3) | 29.95 ± 1.89 (3) | r1 +3.41, r2 +8.15, r3 +6.68 | +6.08 [+0.04, +12.12] |
| Drop rate | 0.00000 ± 0.00000 (3) | 0.00000 ± 0.00000 (3) | r1 +0.00000, r2 +0.00000, r3 +0.00000 | +0.00000 [+0.00000, +0.00000] |
| Inference rate, lowest channel (fps) | 30.000 ± 0.000 (3) | 30.000 ± 0.000 (3) | r1 +0.000, r2 +0.000, r3 +0.000 | +0.000 [+0.000, +0.000] |
| Inference rate, channel mean (fps) | 30.000 ± 0.000 (3) | 30.000 ± 0.000 (3) | r1 +0.000, r2 +0.000, r3 +0.000 | +0.000 [+0.000, +0.000] |
| Stage Q1 wait, p50 (ms) | 0.008 ± 0.001 (3) | 0.008 ± 0.001 (3) | r1 -0.001, r2 +0.001, r3 +0.001 | +0.000 [-0.002, +0.003] |
| Stage Q1 wait, p99 (ms) | 0.174 ± 0.216 (3) | 0.038 ± 0.038 (3) | r1 -0.408, r2 +0.000, r3 -0.002 | -0.137 [-0.721, +0.448] |
| Stage prepare, p50 (ms) | 1.676 ± 0.013 (3) | 1.685 ± 0.024 (3) | r1 -0.008, r2 +0.025, r3 +0.011 | +0.010 [-0.032, +0.051] |
| Stage prepare, p99 (ms) | 2.254 ± 0.932 (3) | 2.270 ± 0.958 (3) | r1 -0.009, r2 +0.046, r3 +0.010 | +0.016 [-0.054, +0.085] |
| Stage Q2 wait, p50 (ms) | 0.008 ± 0.001 (3) | 3.919 ± 1.181 (3) | r1 +3.952, r2 +2.710, r3 +5.071 | +3.911 [+0.977, +6.845] |
| Stage Q2 wait, p99 (ms) | 6.597 ± 3.792 (3) | 9.130 ± 1.827 (3) | r1 +0.302, r2 +4.707, r3 +2.590 | +2.533 [-2.938, +8.005] |
| Stage bind, p50 (ms) | 0.033 ± 0.000 (3) | 0.035 ± 0.001 (3) | r1 +0.002, r2 +0.003, r3 +0.002 | +0.002 [-0.000, +0.005] |
| Stage bind, p99 (ms) | 0.059 ± 0.012 (3) | 0.055 ± 0.007 (3) | r1 -0.022, r2 +0.011, r3 -0.002 | -0.004 [-0.046, +0.037] |
| Stage run, p50 (ms) | 15.521 ± 0.070 (3) | 18.814 ± 0.101 (3) | r1 +3.298, r2 +3.342, r3 +3.237 | +3.292 [+3.161, +3.424] |
| Stage run, p99 (ms) | 16.227 ± 0.226 (3) | 19.308 ± 0.101 (3) | r1 +3.157, r2 +2.897, r3 +3.189 | +3.081 [+2.683, +3.479] |
| Stage post, p50 (ms) | 0.127 ± 0.002 (3) | 0.110 ± 0.002 (3) | r1 -0.014, r2 -0.018, r3 -0.018 | -0.017 [-0.022, -0.011] |
| Stage post, p99 (ms) | 0.180 ± 0.012 (3) | 0.153 ± 0.011 (3) | r1 -0.029, r2 -0.029, r3 -0.023 | -0.027 [-0.036, -0.019] |

CPU by thread group (exploratory; `threads.csv`, the threads alive at the window's end):

| Thread group | C8 | C10 | C10_r − C8_r | Mean [95% t interval] |
|---|---|---|---|---|
| NPU workers (`npu-*`) (s) | 23.247 ± 0.538 (3) | 24.830 ± 0.348 (3) | r1 +1.240, r2 +1.470, r3 +2.040 | +1.583 [+0.560, +2.606] |
| Decoder (`dec-*`, `mpp_*`) (s) | 8.583 ± 0.441 (3) | 8.767 ± 0.590 (3) | r1 -0.470, r2 +0.470, r3 +0.550 | +0.183 [-1.226, +1.592] |
| Preprocessing (`pre-*`) (s) | 1.280 ± 0.070 (3) | 1.310 ± 0.040 (3) | r1 -0.040, r2 +0.020, r3 +0.110 | +0.030 [-0.158, +0.218] |
| The rest (`bench_main`, `metrics`) (s) | 0.147 ± 0.006 (3) | 0.117 ± 0.012 (3) | r1 -0.040, r2 -0.040, r3 -0.010 | -0.030 [-0.073, +0.013] |

CV of process CPU: C8 3.16%, C10 2.85%; none above 10% (PROTOCOL §9.3).

| Repetition | Arm | Run | Process CPU (s) | schedstat − idle (s) | `rknn_run` p50 (ms) | Post p50 (ms) | p99 (ms) |
|---|---|---|---|---|---|---|---|
| 1 | C8 | `20261118T053535Z_m4_aux/01_m4_aux_C8_5ch_r1` | 33.490 | 36.492 | 15.449 | 0.125 | 27.07 |
| 1 | C10 | `20261118T053535Z_m4_aux/02_m4_aux_C10_5ch_r1` | 34.170 | 37.115 | 18.747 | 0.111 | 30.48 |
| 2 | C8 | `20261118T053535Z_m4_aux/04_m4_aux_C8_5ch_r2` | 34.160 | 37.186 | 15.588 | 0.129 | 19.69 |
| 2 | C10 | `20261118T053535Z_m4_aux/03_m4_aux_C10_5ch_r2` | 36.130 | 39.540 | 18.930 | 0.112 | 27.84 |
| 3 | C8 | `20261118T053535Z_m4_aux/05_m4_aux_C8_5ch_r3` | 32.100 | 34.753 | 15.528 | 0.127 | 24.84 |
| 3 | C10 | `20261118T053535Z_m4_aux/06_m4_aux_C10_5ch_r3` | 34.810 | 38.007 | 18.765 | 0.109 | 31.52 |

Every repetition has both runs valid at the first attempt; no sample at a trip point.

## M4's C8 next to M2's Cp (exploratory, §6)

The same arm (C with `--post neon`, YOLOv8n) from another binary and session. Mean ± sample sd (valid runs).

| Point | Metric | M2's Cp | M4's C8 |
|---|---|---|---|
| main | Process CPU (s) | 17.600 ± 0.560 (3) | 17.797 ± 0.570 (3) |
| main | System CPU, schedstat, minus idle (s) | 19.361 ± 0.622 (3) | 20.043 ± 0.480 (3) |
| main | Post-processing stage p50 (ms) | 0.120 ± 0.002 (3) | 0.119 ± 0.000 (3) |
| main | Latency p50 (ms) | 16.69 ± 0.04 (3) | 16.93 ± 0.06 (3) |
| main | Latency p99 (ms) | 18.25 ± 0.72 (3) | 17.66 ± 0.07 (3) |
| aux | Process CPU (s) | 31.790 ± 0.401 (3) | 33.250 ± 1.051 (3) |
| aux | System CPU, schedstat, minus idle (s) | 35.004 ± 0.540 (3) | 36.144 ± 1.253 (3) |
| aux | Post-processing stage p50 (ms) | 0.121 ± 0.004 (3) | 0.127 ± 0.002 (3) |
| aux | Latency p50 (ms) | 18.01 ± 0.72 (3) | 17.92 ± 0.36 (3) |
| aux | Latency p99 (ms) | 24.76 ± 2.52 (3) | 23.87 ± 3.79 (3) |
