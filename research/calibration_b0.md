# Calibration: B0 on the validation split, Haiku

Run `calib_b0`, 2026-10-26: `bench.eval_subagents prepare --agent analyzer --split validation`, B0 = held-out base
(basic glossary, goal briefing, free SQL) with effort low, no repair. Snapshot `f74ec5665649` (45 days, generator 3.3).
One Haiku subagent per question, 1 repeat, 129 runs (114 concrete questions plus family-template draws).

| source | correct | accuracy |
|---|---|---|
| opus_1 (validation share) | 5/10 | 50% |
| heldout_hard_1 (validation share) | 3/6 | 50% |
| handwritten | 7/9 | 78% |
| grokbot_r1 | 41/50 | 82% |
| step0_hard_1 | 34/39 | 87% |
| family templates | 14/15 | 93% |
| **all** | **104/129** | **80.6%** |

- Below the ~90% stop line, so the bundles have room on Haiku (about 19 points).
- step0_hard_1 came out easier than intended: its questions state the logic they need, so the trap rarely bites.
- Two misses spot-checked: one wrong population (mid-stream data start), one fraction where the question asked
  for a percentage. The scores look trustworthy; percent-vs-fraction is strict by the question's wording.
- Cost: about 62k subagent tokens per question run (8.0M for 129), 4.7 tool calls per run, ~13 minutes per 65 runs
  with two workflows in parallel.
