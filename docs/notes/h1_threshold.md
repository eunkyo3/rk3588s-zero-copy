[한국어](h1_threshold.ko.md)

# H1's threshold of 25%: where it came from

[HYPOTHESES.md](../../prereg/HYPOTHESES.md) says that C's process CPU is "at least 25% lower than B's" and does not say why 25 ([issue #71](https://github.com/eunkyo3/rk3588s-zero-copy/issues/71)). This note records what is known about that number. It was written on 2026-11-23, after every run of M1 to M5 had been seen. It changes no threshold and no verdict.

## What is known

- **It has no recorded rationale, and none can be given now.** The pre-registration was drafted by the AI assistant the project is written with (Claude). The draft held 25%, and the registrant approved it unchanged. Neither the draft nor anything else in the repository says what the number was based on: no calculation, no earlier measurement, no source.
- **It was fixed before any measurement.** `HYPOTHESES.md` is in the repository's second commit (`3888b3b`, 2026-10-30) and was registered as `prereg-v1`. The first program that ran the path on the board, the spike, came after it. So the number was not chosen with B's or C's CPU time in view.
- **It says nothing about what size of saving matters.** A reader should not take 25% as a judgement that a smaller saving would be unimportant, or that a larger one is needed for some use. No such judgement was made.
- **This note does not supply a rationale after the fact.** A reason written now, with the results known, would not be the reason the number was chosen.

## What it decided

Nothing, in the sense that no nearby threshold would have changed a verdict.

| Where | Compared | Saving against B | Verdict at 25% |
|---|---|---|---|
| M1, H1 | C | 57.1% (descriptive interval 55.1% to 59.1%, [m1_intervals.md](m1_intervals.md)) | adopted |
| M3, after the reset | C | 56.1% | not judged |
| M5 | C | 56.0% | not judged |
| M5, M5-H1 | the packaged service as 0.1.1 installed it, on all eight cores | 38.5% | adopted |
| M5, M5-H2 | the packaged service on the A76 cores | 58.6% | adopted |

- **H1** would have the same verdict at any threshold up to about 55%.
- **M5** took the threshold over unchanged and said so ([M5.md](../../prereg/M5.md) §2). Its closest result, 38.5%, clears it by 13 points.

## H1's decision rule, computed after the fact

M2's pre-registration gave the approximate error rates of its decision rule ([M2.md](../../prereg/M2.md) §2). The same arithmetic for H1, labelled as what it is: computed after M1's runs, from M1's own variation.

- **Inputs:** M1's three runs per variant at 8 channels: B 43.283 ± 0.343 s, C 18.560 ± 0.308 s (mean ± sd).
- **Assumptions,** as in M2: independent runs, normal errors, the same sd whatever the true saving; the pairing of runs is ignored, and the sd comes from runs within one session.
- **Standard error of `(B − C) / B`:** about 0.46 percentage points.
- **So the rule,** which compares the point estimate with 25% and uses no test:
  - adopts with a probability of about 1% if the true saving is 24%, and of about 99% if it is 26%;
  - at a true saving equal to the threshold, either verdict is about as likely as the other;
  - would adopt a true saving of zero with a probability that is zero for any practical purpose.

The rule was sharp at this point: with this variation, it separates true savings one point apart. That is a property of M1's small run-to-run spread, found afterwards. It was not known when the threshold was set.

## For later milestones

A threshold should come with its reason and, where the variation is known, with the rule's error rates, as M2 gave them. M5 reused H1's threshold on purpose, to ask H1's question of the package, and disclosed that the number has no rationale.
