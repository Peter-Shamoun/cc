# Screen 2 power: questions and repeats for a paired effect

Source: `research/heldout_results_1.md` (27 questions, 2 repeats, 4 configs, Haiku and Sonnet).
The per-question rows were not kept. Everything below is recovered from the published accuracy, pass^2,
and the question-level bootstrap intervals. Regenerate with `python -m bench.power`.

## What was observed

For two repeats, pass^2 is P(both correct) and accuracy is the mean of the question-level average.
That pair identifies how often a question was right once, twice, or never, and so the variance of the
question-level score. The repeat correlation is the usual exchangeable-trials correlation implied by that
variance. The SD of the paired difference is the bootstrap half-width times sqrt(27) / 1.96 (a normal
approximation to a percentile interval, which is slightly asymmetric in the writeup).

| model | contrast | effect (pp) | 95% CI (pp) | SD of paired difference (pp) | repeat corr. base | repeat corr. alt | corr. of the two conditions |
|---|---|---|---|---|---|---|---|
| haiku | pitfalls | -1.9 | [-14.8, 13.0] | 36.9 | 0.81 | 0.73 | 0.61 |
| haiku | pitfalls_procedural | 1.9 | [-13.0, 18.5] | 41.8 | 0.81 | 0.29 | 0.41 |
| haiku | screen1_winner | 11.1 | [-3.7, 27.8] | 41.8 | 0.81 | 0.71 | 0.39 |
| sonnet | pitfalls | 7.4 | [0.0, 16.7] | 22.1 | 0.87 | 0.79 | 0.79 |
| sonnet | pitfalls_procedural | 0.0 | [-5.6, 5.6] | 14.8 | 0.87 | 0.87 | 0.92 |
| sonnet | screen1_winner | 3.7 | [0.0, 11.1] | 14.7 | 0.87 | 0.84 | 0.91 |

Haiku's paired differences have an SD of about 35–42 points. Sonnet's are about 15–22 points.
Repeat correlation is high on both models (a question that is right on one repeat is usually right on the
other). The two conditions are positively correlated as well: many questions are solved, or missed, under
both configs. The interval is wide because the questions that do flip dominate a set of 27.

One published subtraction does not match to the last tenth of a point (Haiku pitfalls+procedural accuracy
75.9 − 74.1 = 1.8, while the paired effect is printed as +1.9). That is rounding in the one-decimal table.
The SD uses the printed interval, not the subtracted accuracies.

## Planning model

A new repeat count changes the within-question noise and not the covariance between conditions.
Var(question mean) = p(1−p) · (1 + (r−1)ρ) / r, with ρ the repeat correlation above.
Var(paired difference) = Var(base) + Var(alt) − 2 Cov, and Cov is held at the r=2 value.
At r=2 this returns the observed SD. At r=1 it is wider. As r grows it floors at the between-question variance.

Two sample sizes, both two-sided:

- **80% power** at α=0.05 for a normal test of the paired mean. This is "detect".
- **Expected CI excludes 0**: the n at which a true effect of that size has a 95% interval that just clears 0
  in expectation. That is a lower bar (about 50% power). It is the bar in the held-out note's "~80+ questions",
  and it is also the CI half of the learner's gate.

The learner accepts a candidate when the effect is at least 5 points **or** the CI lower bound clears 0.
The effect rule will accept a noisy +5 or +6 that the CI still covers. Until validation is large enough for
the CI column below, treat an accept-on-effect-only as provisional.

Numbers are the **conservative** SD for that model (the noisiest of the three contrasts) and, in the second
table, the pitfalls-versus-base contrast alone.

### Conservative (noisiest contrast)

| model | repeats | SD (pp) | n for 5 pp, 80% | n for 10 pp, 80% | n for 5 pp, CI clears 0 | n for 10 pp, CI clears 0 |
|---|---|---|---|---|---|---|
| haiku | 1 | 50.7 | 808 | 202 | 396 | 99 |
| haiku | 2 | 41.8 | 548 | 137 | 268 | 67 |
| haiku | 3 | 40.3 | 509 | 128 | 249 | 63 |
| haiku | 5 | 39.0 | 478 | 120 | 234 | 59 |
| haiku | 8 | 38.3 | 461 | 116 | 226 | 57 |
| sonnet | 1 | 25.9 | 211 | 53 | 103 | 26 |
| sonnet | 2 | 22.1 | 154 | 39 | 76 | 19 |
| sonnet | 3 | 20.7 | 136 | 34 | 67 | 17 |
| sonnet | 5 | 19.5 | 120 | 30 | 59 | 15 |
| sonnet | 8 | 18.8 | 112 | 28 | 55 | 14 |

### Pitfalls versus basic glossary

| model | repeats | SD (pp) | n for 5 pp, 80% | n for 10 pp, 80% | n for 5 pp, CI clears 0 | n for 10 pp, CI clears 0 |
|---|---|---|---|---|---|---|
| haiku | 1 | 42.6 | 571 | 143 | 280 | 70 |
| haiku | 2 | 36.9 | 427 | 107 | 209 | 53 |
| haiku | 3 | 34.7 | 379 | 95 | 186 | 47 |
| haiku | 5 | 32.9 | 340 | 85 | 167 | 42 |
| haiku | 8 | 31.8 | 319 | 80 | 156 | 39 |
| sonnet | 1 | 25.9 | 211 | 53 | 103 | 26 |
| sonnet | 2 | 22.1 | 154 | 39 | 76 | 19 |
| sonnet | 3 | 20.7 | 136 | 34 | 67 | 17 |
| sonnet | 5 | 19.5 | 120 | 30 | 59 | 15 |
| sonnet | 8 | 18.8 | 112 | 28 | 55 | 14 |

## What to run

- **10 points on Haiku, 80% power, 2 repeats:** about **137** questions.
  The expected interval clears 0 at about **67**. The held-out note's "~80+"
  sits on that second bar, not on 80% power.
- **5 points on Haiku, 80% power, 2 repeats:** about **548** questions.
  A 5-point Haiku effect is not a realistic target for the next screen.
- **10 points on Sonnet, 80% power, 2 repeats:** about **39** questions.
  **5 points:** about **154**.
- **Repeats are a weak substitute for questions.** Going from 2 to 8 repeats moves Haiku's 10-point
  80% sample size from 137 to 116. The repeat correlation
  is high, so the second repeat already captured most of what repeats can capture. Use 2 repeats
  (pass^2, as in the held-out run). A third repeat is optional; it is not the way to make a 5-point
  Haiku effect detectable.
- **Validation after round 3.** The frozen split puts 25 of the current 99 concrete questions in
  validation. Round 3 is about 56 questions and the split manager sends a new, unlinked question to
  validation. 25 + 56 = 81, which is the held-out note's bar for *seeing* a 10-point Haiku effect
  (CI clears 0 around 67) and is enough for a 10-point Sonnet effect at 80% power.
  It is not enough for 80% power on a 10-point Haiku effect (137).
- **Do not unseal the test split to buy power.** Those 25 questions are the one-shot end run.

## What this does not say

- The normal approximation is not the bootstrap. The published intervals are percentile intervals and
  some are asymmetric; the SD uses their width.
- Binary questions are not normal at n=27. The n above is a planning number, not a promise that the
  next bootstrap interval will have that width.
- Power is for a paired difference of question-level means. It is not power for pass^k, and it is not
  power for an effect that itself varies by question family.
