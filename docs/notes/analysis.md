[한국어](analysis.ko.md)

# Analysis of M1 (M1-7)

How `tools/m1_report.py` turns the comparison runs into tables and verdicts. The verdict rules are HYPOTHESES' own. This note fixes the choices that HYPOTHESES and PROTOCOL leave to the analysis.

## When this was fixed

This note and `tools/m1_report.py` were committed while the first main sweep session was running ([comparison.md](comparison.md)). By then, of that session's runs, only whether each was valid had been seen, from the session's progress lines. No metric of any comparison run had been looked at.

## The runs that count

- **Which runs:** for each measurement point and repetition, the runs of the session that completed the repetition ([comparison.md](comparison.md#resuming-a-sweep-91-93)). Of each such run, its last attempt counts. That attempt is valid, or the run is invalid ×2.
  - Runs of a repetition that a later session redid do not count; they are reported as replaced.
- **Complete point:** 3 valid runs, or invalid ×2 records for the runs that are not valid (§9.3). `tools/m1_report.py --check` lists the points that are not complete.

## Metrics of one run

- **From the run's `summary.csv`:**
  - process CPU, total and per cluster (§7.1);
  - system CPU (§7.2), p50 latency, drop rate (§7.5);
  - decoder drops and drift (§7.6);
  - the highest temperature.
- **p99 latency** (§7.3) is computed again from `frames.csv` with `numpy.percentile` and its default (linear) interpolation. It covers `t4 − t0` of every frame whose `t4` falls in the window: from the end of the warm-up, for the window's length.
  - `bench_main` computes the same quantity in C++ with the same interpolation. The report keeps both. On the smoke runs they agree to 0.001 ms.
- **System CPU minus idle** (§7.2): the run's total system CPU minus the idle baseline of the same session. The baseline is scaled from its own length (`window_s` of `idle_baseline.csv`, about 120 s) to the run's window.
- **Lowest channel inference rate:** the minimum over the run's channels of §7.4's rate. H1's practical verdict uses it.

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
  - **"1 percentage point" of drop rate** is 0.01, since the drop rate is a fraction.
  - **Secondary verdicts:** the H2 auxiliary point is 5 channels (A7), the same point. So the secondary verdicts are the 5-channel verdicts.
- **Fewer than 3 valid runs** at a point a verdict needs:
  - the spec-literal verdict uses the valid runs there are and is flagged;
  - the practical verdict is indeterminate;
  - with no valid run of either variant, both verdicts are indeterminate.
- **H3:** `n_B` and `n_C` come from replaying the H3 sessions through the schedule of §10 ([comparison.md](comparison.md#h3-10)). The practical verdict is indeterminate when both are at least `L` − 1 = 31.

## Reported with every result

- The pipeline is pinned to the A76 cores (cpu4–7, §8).
- The decoder's buffers are CPU-cached (A1).
- B and C are equivalent under A5's criteria, on video ①. The held-out check of A8 gives the other videos.

## Tools

| Tool | Does |
|---|---|
| `tools/m1_report.py` | tables, verdicts, flags and the invalid ×2 list, as Markdown; `--check` for completeness ([tests](../../tests/test_m1_report.py)) |
