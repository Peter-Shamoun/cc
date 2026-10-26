# Stage 4: sealed test, B1 vs B0

Run `s4_test`, 2026-10-26, the TEST split unsealed once (`--split test --unseal-test`), snapshot `50054399f815`,
Haiku, two repeats each, 192 runs (48 test questions including family-template draws). The final configuration was
frozen before this run (`stage2_3_results.md`); nothing was changed after it.

| bundle | accuracy | pass^2 | answered and wrong | tool calls / question |
|---|---|---|---|---|
| B0 base | 79.2% | 66.7% | 19.8% | 4.44 |
| B1 knowledge only (final) | 93.8% | 91.7% | 6.2% | 2.19 |

B1 − B0 = **+14.6 pp, 95% CI [+4.2, +25.0]** (paired by question, 10,000 bootstrap resamples). B1 is better on 14
questions and worse on 2.

**Ship rule met (interval above zero): B1 ships** as the analyzer's production configuration on Haiku.

Caveats: 48 questions is small, so the size of the effect is uncertain (the interval spans 4 to 25 points) even
though its sign is not. The test gain is larger than the validation gain (+5.8 pp), which is the expected direction of
noise on a small set, not evidence of a larger effect. Sonnet was not tested.
