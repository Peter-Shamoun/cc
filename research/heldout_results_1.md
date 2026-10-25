# Held-out validation 1: does the screen-1 winner generalize?

Question set `heldout_hard_1`: 27 hard questions authored by Opus after screen 1, by authors who saw only the
basic glossary (never the pitfalls text), each with an independent Opus verifier (three-way agreement).
Dataset: generator 3.1 default bench snapshot 5832dadedee8. 4 configs x Haiku/Sonnet x 2 repeats = 432
sessions (research/heldout_configs_1.json). Paired by question, question-level bootstrap 95% interval
(`python -m bench.eval_subagents contrast heldout1 --baseline base`).

| config | Haiku acc (pass^2) | Sonnet acc (pass^2) |
|---|---|---|
| base (basic glossary, goal style) | 74.1% (70.4%) | 83.3% (81.5%) |
| pitfalls | 72.2% (66.7%) | 90.7% (88.9%) |
| pitfalls + procedural | 75.9% (63.0%) | 83.3% (81.5%) |
| screen-1 winner (pitfalls, dictionary + profile, procedural, low effort) | 85.2% (81.5%) | 87.0% (85.2%) |

| minus base | Haiku | Sonnet |
|---|---|---|
| pitfalls | -1.9 [-14.8, +13.0] | +7.4 [0.0, +16.7] |
| pitfalls + procedural | +1.9 [-13.0, +18.5] | 0.0 [-5.6, +5.6] |
| screen-1 winner | +11.1 [-3.7, +27.8] | +3.7 [0.0, +11.1] |

## Reading

- The +16-point Haiku effect of the pitfalls text in screen 1 does NOT carry over to independently authored hard
  questions. Either the text matched screen-1's specific traps (the tuning-to-benchmark risk flagged in
  screening_results_1.md), or hard questions need different knowledge. On Sonnet, pitfalls still helps (+7).
- The full screen-1 winner is the best Haiku configuration here (+11, interval includes 0): the data dictionary and
  profile probably carry the weight on hard questions.
- 27 questions is too few: intervals are +/-15 points. Next validation needs ~80+ questions.
- Three questions failed for every model and config (0/16, 0/16, 2/16). Cause: generator 3.1 starts the data
  mid-stream, so claims billed before the first file appear only through 835s (or only a later secondary /
  corrected 837), and those questions did not say how to treat them. Fixed after scoring (question text states
  the population; one gold + answer key restricted to claims with a primary submission), and the basic glossary
  now states the data-start fact. These three were constant failures across configs, so the contrasts above are
  unaffected by them.

## Method change for the next iterations (anti-overfit)

Knowledge text (glossary/pitfalls) is now treated as a trained artifact:
1. TRAIN split: failures here may be read and turned into general knowledge entries.
2. VALIDATION split: never read; used to accept or reject a knowledge change.
3. TEST split: authored later, run once at the end.
