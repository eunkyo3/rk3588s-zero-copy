[한국어](analysis.ko.md)

# Analysis of M1 (M1-7)

How `tools/m1_report.py` turns the comparison runs into tables and verdicts. The verdict rules are HYPOTHESES' own. This note fixes the choices that HYPOTHESES and PROTOCOL leave to the analysis.

## When this was fixed

This note and `tools/m1_report.py` were committed while the first main sweep session was running ([comparison.md](comparison.md)). By then, of that session's runs, only whether each was valid had been seen, from the session's progress lines. No metric of any comparison run had been looked at.

## The runs that count

- **Which runs:** for each measurement point and repetition, the runs of the session that completed the repetition ([comparison.md](comparison.md#resuming-a-sweep-91-93)). Of each such run, its last attempt counts. That attempt is valid, or the run is invalid ×2.
  - The attempts that do not count are listed with their reasons: invalid first attempts that were repeated, runs of a repetition that a later session redid, and attempts cut short. An attempt the session's death cut short reads "the session ended during the run".
- **Complete point:** 3 valid runs, or invalid ×2 records for the runs that are not valid (§9.3).
- `tools/m1_report.py --check` lists every run of M1's sweeps (main, aux, reference) that is not there yet, and H3 if its search is not done. A sweep without any session counts as not complete.

## Metrics of one run

- **From the run's `summary.csv`:**
  - process CPU: total, A76, A55 and unattributed (§7.1);
  - system CPU per CPU, per cluster and in total (§7.2);
  - p50 latency, MPP hold time (p50, p99) and slot wait (p99) (§7.3);
  - drop rate (§7.5), decoder drops and drift (§7.6);
  - the largest RSS and the highest temperature (§7.7).
- **p99 latency** (§7.3) is computed again from `frames.csv` with `numpy.percentile` and its default (linear) interpolation. It covers `t4 − t0` of every frame whose `t4` falls in the window: from the end of the warm-up, for the window's length.
  - `bench_main` computes the same quantity in C++ with the same interpolation. The report keeps both. On the smoke runs they agree to 0.001 ms.
- **System CPU minus idle** (§7.2): each system CPU value of the run, per CPU, per cluster and in total, minus the same value of the session's idle baseline. The baseline is scaled from its own length (`window_s` of `idle_baseline.csv`, about 120 s) to the run's window.
- **Inference rate** (§7.4): per run, the lowest, the mean and the highest over its channels. The per-channel values stay in the run's `channels.csv`. H1's practical verdict uses the lowest.

## Points

- **Mean and standard deviation** over the valid runs (§7.8). The standard deviation is the sample one (n − 1).
- **Coefficient of variation:** standard deviation over mean. Above 10% it is flagged for a hypothesis metric (§9.3): process CPU (H1) and p99 latency (H2), at every point where they are computed.

## Verdicts

The rules are those of HYPOTHESES, applied as written. Only these readings are added:

- **H1** (main condition, 8 channels):
  - **Spec-literal:** the 3-run means of process CPU; adopted if (B − C) / B ≥ 0.25.
  - **Practical:** indeterminate if any valid run of either variant has a channel below 4.75 fps; otherwise the same threshold.
  - **Mixed:** if (B − C) of system CPU minus idle has the opposite sign to that of process CPU. A difference of exactly zero has no sign, so it is never mixed.
- **H2** (auxiliary condition, 5 channels):
  - Runs are paired by repetition index.
  - **"1 percentage point" of drop rate** is 0.01, since the drop rate is a fraction. Drop rates are written with 5 decimals, so the bound is compared with a tolerance of 10⁻⁹.
  - **Secondary verdicts:** the H2 auxiliary point is 5 channels (A7), the same point. So the secondary verdicts are the 5-channel verdicts.
- **Fewer than 3 valid runs** at a point a verdict needs:
  - the spec-literal verdict uses the valid runs there are and is flagged;
  - the practical verdict is indeterminate;
  - if either variant has no valid run at the point, both verdicts are indeterminate.
- **H3:** `n_B` and `n_C` come from replaying the H3 sessions through the schedule of §10 ([comparison.md](comparison.md#h3-10)). The practical verdict is indeterminate when both are at least `L` − 1 = 31.

## Reported with every result

The report prints these first:

- The pipeline is pinned to the A76 cores (cpu4–7, §8).
- The decoder's buffers are CPU-cached (A1).
- B and C are equivalent under A5's criteria on video ①, and on videos ②, ③ and ④ too: the held-out check of A8 passed, pooled and per video.
- A6's label on every C result, and A6's open question of the output conversion.

It also lists every invalid ×2 record by variant, with the reasons of both attempts, H3's included (HYPOTHESES; §10 step 3), and the runs with samples at a trip point (warnings, §9.3).

## The 4K design (A11)

- **When:** this section and the report's 4K part were added on 2026-11-04, while the 4K loop check ran and before the first 4K session. M1's results had been seen by then; no 4K run existed.
- **Where:** the report's last section, apart from M1 and labelled exploratory. No hypothesis is tested, and nothing is judged (A11).
  - The conditions printed first still hold, except one: equivalence under A5 is not claimed at 4K (A11). The section says so.
- **Same method as M1:** the runs that count, the metrics of one run, the mean and sample standard deviation, and the 10% flag, as above. The 4K sessions are `results/m1.1/<UTC>_4k`.
- **Next to each 4K point:** M1's main-condition values for B and C at the same count, as the main sweep's table gives them (A11).
- **Its own lists:** the 4K flags, invalid ×2 records, attempts that do not count and warnings are listed in the 4K section only.
- **Not M1's completeness:** `--check` does not read the 4K runs. The 4K section says whether its own points are complete.

## The designs of §13 (A12)

- **When:** this section and the report's A12 part were added on 2026-11-04, while A12's first session (`k4`) ran. No metric of any A12 run had been looked at.
- **Where:** after the 4K section, apart from M1 and labelled exploratory. Nothing is judged (A12).
- **Same method as M1:** the runs that count, the metrics of one run, the mean and sample standard deviation, and the 10% flag. The sessions are `results/m1.1/<UTC>_k4`, `_a55`, `_bdirect_main` and `_bdirect_aux`; a55 has one repetition.
- **Each design with its own controls:** one table per design, its arms side by side (`k4`: B, Bk4; `a55`: B, C on cpu0–3; `bdirect_*`: B, C, Bd). No value is set against M1's, because the binary differs (A12).
- **B-direct's saving:** from the mean process CPU of each arm, Bd's saving against B, (B − Bd) / B, next to C's, (B − C) / B, and Bd's as a share of C's, (B − Bd) / (B − C). From the means only, with no interval.
- **Its own lists:** each design's flags, invalid ×2 records, attempts that do not count and warnings are listed under that design.
- **Not M1's completeness:** `--check` does not read these runs. Each design says whether its own points are complete.

## Summary file and figures (M1-7)

- **`summary.json`:** `tools/m1_report.py --json FILE` writes the report's numbers: completeness, the verdicts, and per point and metric the n, mean, sample standard deviation and coefficient of variation. It also lists the invalid ×2 records, the attempts that do not count and the warnings, and has the 4K design apart (`m1.1_4k`), and A12's designs too (`m1.1_a12`, with B-direct's savings).
  - Floats are rounded to 3 decimals; nan is `null`. A clean clone must write the same file (M1-7).
- **Figures:** `tools/m1_plots.py` draws SVG files from the same points, for the main and auxiliary sweeps:
  - CPU: process CPU (total, A76, A55) and system CPU minus idle (total, cpu0–3, cpu4–7);
  - latency: p50, p99 and drop rate;
  - decoder: MPP hold time (p50, p99) and slot wait (p99);
  - H3: each valid run that counts, search and confirmation, one run per point. Invalid ×2 runs are not drawn; the report lists them.
- **In the sweeps' figures:**
  - a point is the mean over its valid runs, with the sample standard deviation as an error bar, none for a point with one valid run;
  - a point without a valid run breaks the line;
  - A's runs are drawn with the main sweep.
- **In every figure:**
  - vertical rules mark the measured limits: the NPU's, `P_cap` / fps (A7: 38.8 channels at 5 fps, 6.5 at 30 fps), and the decoder's, `L` = 32, the search's cap (A9).
- **Reproducible:** the SVG files carry no date or matplotlib version and have LF line ends, so the same matplotlib writes the same bytes on any system. Their colours, B blue, C orange and A green, pass the palette's colour-vision checks; each variant also has its own marker.

## Tools

| Tool | Does |
|---|---|
| `tools/m1_report.py` | conditions, completeness, verdicts, tables, flags, invalid ×2 records, attempts that do not count and warnings, as Markdown, then the 4K section (A11) and A12's designs; `--check` for M1's completeness; `--json` for `summary.json` ([tests](../../tests/test_m1_report.py)) |
| `tools/m1_plots.py` | the figures, as SVG ([tests](../../tests/test_m1_plots.py)) |
| `tools/m1_stages.py` | where the latency goes, per stage of §7.3's timeline, from the runs that count; exploratory, not pre-registered ([tests](../../tests/test_m1_stages.py)) |
