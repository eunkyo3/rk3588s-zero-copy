[한국어](m1_intervals.ko.md)

# M1's intervals and the spread between sessions (exploratory)

What M1's 3-run means leave out: how firm each difference between B and C is, and how far the same arm moves between sessions ([issue #67](https://github.com/eunkyo3/rk3588s-zero-copy/issues/67)).
- **Exploratory, after the fact:** computed on 2026-11-17, after every run of M1, M1.1 and M2 had been seen. Nothing here was pre-registered, and no verdict changes.
- **Tool:** [tools/m1_intervals.py](../../tools/m1_intervals.py). Its full output is [m1_intervals_tables.md](m1_intervals_tables.md), and every number below comes from it.
- **Runs:** the runs that count, as `tools/m1_report.py` takes them, valid runs only. Every run of these sessions was valid.
- **Covered:**
  - M1's paired differences of B against C, with descriptive 95% t-intervals;
  - the spread of B and C between the sessions of M1, M1.1 and M2;
  - how many repetitions a latency interval of a given width would need, as guidance.

## The check first

- **M1:** the tool gives back M1's published values ([M1.md](../report/M1.md)).
  - At 8 channels, main condition: B 43.283 ± 0.343 s, C 18.560 ± 0.308 s, and H1's saving of 57.1%.
  - At 5 channels × 30 fps, H2's p99 per repetition: B 28.3, 23.2 and 22.1 ms; C 23.7, 23.4 and 30.5 ms.
- **M2:** with this tool's pairing and M2's `t_interval`, M2's runs give M2's intervals ([M2.md](../report/M2.md)): −0.83% to 12.97% (M2-H1) and 12.24% to 17.20% (M2-H2).
- **Sessions:** C's process CPU in M1.1 and M2, as M1.1.md and M2.md print it, comes back too.
- **All 16 published values agree** ([the table](m1_intervals_tables.md#reproduction)).

## Paired differences of B against C

- **Pairs:** B_r and C_r of the same repetition r. They ran in the same session, in alternating order: B→C, C→B, B→C.
- **Process CPU:** (B_r − C_r) / B_r. H1's saving is the same ratio, taken on the 3-run means.
- **Latency, p50 and p99:** B_r − C_r in ms. A positive value means C is lower. The mean of the differences is the difference of the means that H2 compares.
- **Interval:** a descriptive 95% t-interval of the mean of the 3 differences, as M2 reported its pairs (`tools/m2_report.py`'s `t_interval`, t = 4.303). It is reported, not tested.
- **Points:** every point of M1's main and auxiliary sweeps.
- **H3:** its search ran one run per count and variant, so it gets no interval. Its confirmation ran 32 channels three times per variant to check that the count is sustained. The main sweep covers 32 channels with 3 repetitions.

### Process CPU

- **Every interval lies far above 0,** at every point of both sweeps.
  - *Main:* the mean differences are 49.8% to 57.7%. The lowest bound is 48.1%, at 32 channels.
  - *Auxiliary:* 72.3% to 73.4%. The lowest bound is 70.9%, at 3 channels.
- **H1's point, 8 channels:** 58.0%, 56.9% and 56.5%. Their mean is 57.1%, and the interval is 55.1% to 59.1%.
  - So the whole interval is more than twice H1's threshold of 25%.

### Latency

| Sweep | p99: C lower (interval above 0) | p99: spans 0 | p99: C higher | p50: C lower | p50: spans 0 | p50: C higher |
|---|---|---|---|---|---|---|
| Main | 1, 2 ch | 4, 8, 12, 16 ch | 32 ch | 1, 2, 4, 16 ch | 8, 12 ch | 32 ch |
| Auxiliary | 1, 2, 3 ch | 4, 5 ch | – | 1 to 5 ch | – | – |

- **Firm, either way:**
  - at 1 and 2 channels, C's p99 is lower by 3.4 to 3.6 ms in both sweeps, with narrow intervals;
  - at 32 channels, C is slower: p99 by 23.2 ms (interval −42.9 to −3.6 ms), p50 by 16.8 ms (−21.3 to −12.4 ms).
- **Not firm at 3 runs:** p99 at 4 to 16 channels (main), and at 4 and 5 channels (auxiliary).
  - At 8 channels the pairs are 8.6, 6.4 and −4.7 ms: mean 3.4 ms, interval −14.3 to 21.2 ms.
  - At 16 channels they are −13.7, 14.2 and 14.5 ms: interval −35.2 to 45.2 ms.
  - These are multi-channel points. There, M1.1 found that latency went with the arrival phases, which differed between runs ([M1.1.md](../report/M1.1.md#arrival-phases-found-after-the-fact)).
- **H2's point, 5 channels × 30 fps:** the pairs are 4.7, −0.2 and −8.4 ms. Their mean is −1.3 ms, and the interval is −17.7 to 15.0 ms.
  - H2 is rejected as registered, because C's mean is not lower. The interval adds that 3 runs cannot tell the direction at this point.
- **p50 is firmer than p99:** at the auxiliary condition every interval lies above 0. At 5 channels it is 0.2 to 6.0 ms.
- **Against M1's text:** M1.md says that up to 16 channels C's median latency is lower, and "its p99 is lower too". That holds for the means. With these intervals it is firm for p99 only at 1 and 2 channels, and for p50 at 1, 2, 4 and 16.

## The spread between sessions

B and C at the same point, in different sessions. Mean ± sample sd (3 runs).

| Point | Arm | M1 | M1.1, `k4` | M1.1, `bdirect_*` | M2 | Highest above lowest | CV within a session, at most |
|---|---|---|---|---|---|---|---|
| 8 ch, main | C | 18.560 ± 0.308 s | – | 19.487 ± 0.257 s | 18.737 ± 0.170 s | 5.0% | 1.7% |
| | B | 43.283 ± 0.343 s | 43.850 ± 0.274 s | 44.037 ± 0.268 s | – | 1.7% | 0.8% |
| 5 ch, 30 fps | C | 35.680 ± 0.486 s | – | 37.447 ± 1.408 s | 37.280 ± 0.528 s | 5.0% | 3.8% |
| | B | 129.587 ± 1.236 s | – | 130.953 ± 0.556 s | – | 1.1% | 1.0% |

- **C's process CPU moved by 5.0% at both points.** That is more than its CV within any of these sessions: at most 1.7% and 3.8%.
- **B's moved less:** 1.7% and 1.1%.
- **The sessions differ in more than time:**
  - **Binary:** code `86587a2` (M1), `cdb309e` (M1.1) and `88206ab` (M2), from the runs' headers.
  - **Sampler:** M2's main session staggered its channels' frames (`stagger: on`). M2's auxiliary session did not. M1's and M1.1's headers have no such field: the option came with M2.
  - So these data cannot split the session from the binary. One hint: `k4` and `bdirect_main` share a binary, and their B, 43.850 and 44.037 s, lie closer to each other than to M1's 43.283 s. Two sessions are too few to say more.
- **Against M2-H1's effect:** C's spread of 5.0% is close to M2-H1's (C − Cp) / C of 6.07%.
  - M2's verdict stands: it compares C and Cp within sessions, so a shift between sessions falls on both arms ([prereg/M2.md](../../prereg/M2.md), "Pairing").
  - Values set side by side across sessions carry this spread.
- **Latency:**
  - **With M2's stagger,** C's p99 at 8 channels was 18.6 ± 0.9 ms, against 44.0 ± 1.9 ms (M1) and 41.0 ± 4.1 ms (M1.1). That is a different condition, not a spread between sessions.
  - **Without the stagger,** the means of p50 and p99 moved by 2.5% to 24.1% between sessions. Within a session their CV reached 22.2%. So for latency the spread between sessions is of the same order as the variation between runs.
- **Where values from different sessions sit together,** read them with this spread:
  - [M2.md](../report/M2.md#other-results-no-verdict): "M2's C next to earlier C", and M2's latency next to M1's under "Staggered sampling";
  - the 4K design next to M1's 1080p values: [M1_tables.md](../report/M1_tables.md#m11-4k-input-exploratory-a11), [M1.md](../report/M1.md#after-m1-4k-input-m11-exploratory) and [M1.1.md](../report/M1.1.md#4k-input-a11). There the input differs too;
  - [prereg/M2.md](../../prereg/M2.md): its error rates use M1.1's sd, and its "Pairing" bullet already gives the rise from M1 to M1.1.

## Repetitions for later pre-registrations (guidance)

This is guidance for designing later milestones, not a registration. M2's pre-registration weighed its CPU thresholds against M1.1's run-to-run variation ([prereg/M2.md](../../prereg/M2.md), "What the decision rule can and cannot tell"). The same can be done for latency.

- **The rule:** n is the fewest runs whose 95% t-interval of a mean, ± t(n − 1) × CV / √n, is at most h of the mean. The observed CV is taken as the true one.
- **A difference of two arms:** with that CV in each arm and independent errors, the difference's sd is about √2 × CV. Pairing within a session can make it smaller, if a session shifts both arms.
- **Latency varies far more than CPU:**
  - p99's CV is above 10% at six of M1's 24 points and arms, up to 24.3% (auxiliary, 4 channels, C). Its median is 3.6%.
  - p50's median is 1.4%, and its largest 13.7%.
  - Process CPU's largest is 4.6%.

| Variation | CV | One arm, h = 2%, 5%, 10% | Difference (√2 × CV), h = 2%, 5%, 10% |
|---|---|---|---|
| p99 latency, median over M1's points and arms | 3.6% | 16, 5, 3 | 28, 7, 4 |
| p99 latency, largest in M1 | 24.3% | > 100, 94, 26 | > 100, > 100, 48 |
| p50 latency, median over M1's points and arms | 1.4% | 5, 3, 3 | 7, 3, 3 |
| p50 latency, largest in M1 | 13.7% | > 100, 32, 10 | > 100, 61, 17 |
| p99 latency, M2's 8 ch main (stagger on), larger of C and Cp | 4.6% | 23, 6, 4 | 43, 9, 5 |
| p99 latency, M2's 5 ch × 30 fps, larger of C and Cp | 22.2% | > 100, 79, 22 | > 100, > 100, 41 |
| process CPU, largest in M1 (for comparison) | 4.6% | 24, 6, 4 | 44, 10, 5 |

- **Reading it:**
  - At a typical p99 CV, 5 to 7 runs give ±5%.
  - At the worst seen, ±10% on a difference needs 48 runs. So at multi-channel points, more runs alone are a costly fix.
  - M2's staggered main point had a p99 CV of at most 4.6%, against up to 11.7% at M1's 8-channel point. The sampler, sessions and binary all differ, so this is not a measured effect of the stagger. It fits M1.1's finding that frames which arrive together wait for the NPU.
  - So a later pre-registration with a latency hypothesis could first fix or record the arrival phases (M1.1), then set its repetitions from rows like these, with h at most half the smallest difference it means to see.
- **Limits:**
  - each CV comes from 3 runs, so it is rough itself;
  - the rule takes the CV as known, and assumes normal errors and independent runs;
  - M1's CPU difference was so large that 3 runs sufficed for H1, whatever h these rows give.

## Released reports

- **[M1.md](../report/M1.md):** one line under "Data quality" points here. It is marked as added after the release, as the M1.1 lines there are. Nothing else in it changed.
- **M1.1.md and M2.md:** unchanged.

## Reproducing

From a clone of the repository, with Python 3 and numpy:

```sh
python3 tools/m1_intervals.py --out docs/notes/m1_intervals_tables.md
python3 tests/test_m1_intervals.py
git diff --exit-code docs/notes/m1_intervals_tables.md   # the same file
```
