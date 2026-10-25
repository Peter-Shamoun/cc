# Phase 4 screen 1: technique main effects (Haiku, Sonnet)

Design: 2^(7-3) resolution IV fractional factorial (research/screening_design.json; generators E=ABC, F=BCD,
G=ACD), 16 configurations x 36 questions (research/screening_questions.json: 12 hand-written, 2 refusals,
16 Opus-authored, 6 family instances) x 1 repeat, per model. Dataset: generator 3.0 snapshot 29b42d74ee18
(acme_labs, default bench scenario). Every evaluated subagent worked only through bench/agent_cli.py; answers
scored by re-executing the submitted SQL (bench/agent_session.py), matcher bench/compare.py after the fix below.
Effects: mean over questions of (accuracy at high level - accuracy at low level), 95% interval from 2,000
question-level bootstrap resamples (python -m bench.eval_subagents effects RUN --design ...).

## Accuracy per configuration (A B C D E F G bits; 1 = technique on)

| config | Haiku | Sonnet |
|---|---|---|
| s0000000 (all off) | 72.2% | 88.9% |
| s0001011 | 75.0% | 94.4% |
| s0010111 | 77.8% | 97.2% |
| s0011100 | 75.0% | 88.9% |
| s0100110 | 86.1% | 97.2% |
| s0101101 | 94.4% | 100.0% |
| s0110001 | **97.2%** | 100.0% |
| s0111010 | 86.1% | 97.2% |
| s1000101 | 77.8% | 88.9% |
| s1001110 | 75.0% | 100.0% |
| s1010010 | 77.8% | 86.1% |
| s1011001 | 83.3% | 91.7% |
| s1100011 | 94.4% | 97.2% |
| s1101000 | 91.7% | 97.2% |
| s1110100 | 94.4% | 97.2% |
| s1111111 (all on) | 94.4% | 97.2% |

## Main effects (percentage points, 95% bootstrap interval)

| factor | Haiku | Sonnet |
|---|---|---|
| A semantic layer (typed metrics + lookup) | +3.1 [-1.7, +8.7] | -1.0 [-3.1, +1.0] |
| **B glossary: basic -> + invariants and pitfalls** | **+15.6 [+5.2, +26.7]** | **+5.9 [+0.4, +12.9]** |
| C data dictionary + data profile | +2.4 [-1.7, +7.6] | -1.0 [-4.2, +2.8] |
| D dynamic few-shot examples (k=3, own family excluded) | -0.3 [-3.1, +2.4] | +1.7 [-0.4, +4.2] |
| E SQL error hints + programmatic result checks | -0.3 [-3.1, +2.4] | +1.7 [-1.0, +4.5] |
| F reasoning effort low -> high | -2.4 [-8.3, +2.4] | +1.7 [-3.1, +6.3] |
| **G context style: goal -> procedural steps** | **+4.5 [+0.4, +9.0]** | +1.7 [-0.7, +4.2] |

## Findings

1. Domain knowledge written as invariants and traps is the dominant lever: +16 pts on Haiku (15.6), +6 on Sonnet. With
   it, Haiku (86-97%) reaches Sonnet's range (86-100%) on this question set.
2. Procedural working steps help Haiku (+5) and do not hurt Sonnet.
3. On this set the semantic layer, dictionary/profile, examples, result checks and higher effort have no
   measurable main effect. Higher effort costs latency and tokens for nothing measurable here.
4. Failure analysis (residual errors): the latest-submission trap (is_latest_submission picks the secondary
   payer's row), fan-out joins over per-submission rows, and claims-vs-lines grain. These are exactly what the
   pitfalls text names.

## Caveats (what this does not show yet)

- Ceiling: 12 of 16 Sonnet configurations score >= 97%; the set no longer discriminates for Sonnet. Next screen
  needs harder questions.
- Possible tuning to the benchmark: the pitfalls text was written by the same session that wrote the questions
  and the views (before the screen ran, from domain knowledge). Confirm on questions authored independently and
  after the pitfalls text (held-out set) before adopting it as a production technique.
- One repeat per (config, question): pass^k not measured; run-to-run variance enters only via the bootstrap.
- Res IV: main effects are clear of two-factor interactions, but two-factor interactions are aliased in pairs.
- 145 sessions were killed by a usage limit and re-run from scratch; ~2 of them may originally have been genuine
  no-answers (could not be separated), a negligible bias.
- Matcher fix during scoring (commit "matcher: same-kind measures keep order"): Sonnet answers with the exact
  gold values had been rejected for column order + an extra supporting count. All results above use the fixed
  matcher; the strict selftest still rejects every wrong_sql.
- Dataset is generator 3.0 (realism 5/10 per audit 1); rerun on the audited generator once it lands.
