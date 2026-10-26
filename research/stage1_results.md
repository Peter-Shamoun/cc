# Stage 1: eleven bundles on Haiku, validation split, one repeat

Runs `s1` (B0, B2–B9) and `s1b` (B1, B10; after the playbook froze), 2026-10-26, snapshot `50054399f815`,
validation split (114 questions plus family-template draws = 129 runs per bundle), `claude-haiku-5-5`, Sonnet only
where a bundle calls for it. Half of round 0 ran as workflow subagents, the rest as local `claude -p` workers
(`bench/claude_print_runner.py`), with the same briefing and unrestricted vs analyzer-CLI-only Bash respectively; a
session the headless sandbox denied was re-run (up to 3 times) and never scored as denied.

Paired against B0 by question; 95% interval from a question-level bootstrap (4,000 resamples). Cost is what
`claude -p` reports per session, summed over role sessions (workflow-run sessions report no cost, so the cost
columns are lower bounds for bundles whose round 0 ran partly as workflows).

| bundle | accuracy | vs B0 (pp) | 95% CI | $/question | $/correct |
|---|---|---|---|---|---|
| B1 knowledge only | 83.7% | +5.4 | [-0.8, +11.6] | 0.0066 | 0.0078 |
| B10 everything on | 83.7% | +5.4 | [-1.6, +12.4] | 0.2340 | 0.2795 |
| B9 Sonnet planner, Haiku executor | 82.9% | +4.7 | [-1.6, +11.6] | 0.0616 | 0.0743 |
| B2 3 candidates + selector | 82.2% | +3.9 | [-1.6, +10.1] | 0.0450 | 0.0547 |
| B8 Haiku then Sonnet cascade | 82.2% | +3.9 | [-2.3, +10.1] | 0.0089 | 0.0108 |
| B3 typed route | 80.6% | +2.3 | [-4.7, +10.1] | 0.0089 | 0.0110 |
| B4 explore freely | 80.6% | +2.3 | [-3.9, +8.5] | 0.0047 | 0.0059 |
| B6 lean | 80.6% | +2.3 | [-3.1, +7.8] | 0.0040 | 0.0050 |
| B7 traps in code | 80.6% | +2.3 | [-3.9, +8.5] | 0.0039 | 0.0048 |
| B0 base | 78.3% | 0 | | 0.0036 | 0.0046 |
| B5 deliberate | 77.5% | -0.8 | [-8.5, +7.0] | 0.0058 | 0.0075 |

Reading:

- No bundle's interval excludes zero at one repeat. Point estimates span 77.5–83.7%.
- B1 (knowledge only: playbook, dictionary with derivations, enum profile, lookup, BM25 examples) leads and costs
  1.7× B0 per correct answer. B10 (24 parameters on) ties it at about 35× the cost: the techniques did not add up.
- Contradicting pairs: knowledge (B1) ≥ compute (B2); explore (B4) = typed route (B3); deliberate (B5) is the only
  bundle below base, lean (B6) is not; cascade (B8) and up-front planner (B9) both help a little, B8 far cheaper.
- By the pre-registered rule (ties with the leader go to the lowest cost per correct answer) the winner is not yet
  decidable: every bundle ties B1 at this precision. A second repeat runs on B0, B1, B7 and B9 (`s1r2`) before the
  winner is named. B10 is dropped: at 35× it would have to beat B1 clearly.

## Second repeat (`s1r2`): B0, B1, B7, B9 at two repeats

| bundle | accuracy | pass^2 | vs B0 (pp) | 95% CI | answered and wrong | $/correct |
|---|---|---|---|---|---|---|
| B1 knowledge only | 85.7% | 82.2% | +5.8 | [+0.8, +10.9] | 13.6% | 0.0079 |
| B9 planner → executor | 79.8% | 73.6% | 0.0 | [-5.0, +5.0] | 19.4% | 0.1125 |
| B7 traps in code | 79.1% | 72.9% | -0.8 | [-5.8, +3.9] | 15.1% | 0.0074 |
| B0 base | 79.8% | 72.9% | 0 | | 20.2% | 0.0068 |

B1 − B9 = +5.8 [+1.6, +10.1]; B1 − B7 = +6.6 [+1.9, +11.2].

**Stage-1 winner: B1.** It is the only bundle whose interval against B0 excludes zero, it beats the next two
candidates with intervals excluding zero, it cuts answered-and-wrong by a third, and its cost per correct answer is
within 16% of B0's. The one-repeat leads of B9 and B7 did not survive the second repeat.

Next (compressed stage 2 + 3, one repeat, paired against B1's two repeats, run `s2`): leave-one-out arms for the
playbook, examples, dictionary + profile, lookup and the invariants glossary; additions for repair with hints and for
repair + domain lint + reconciliation.
