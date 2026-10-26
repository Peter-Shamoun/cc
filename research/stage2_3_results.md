# Stages 2 and 3 (compressed): what B1 needs, and what it does not

Run `s2`, 2026-10-26, validation split, Haiku, one repeat per arm, each arm paired by question against B1's two
repeats (`s1b`, `s1r2`). 95% intervals from a question-level bootstrap. Decision rules are the pre-registered ones in
`analyzer_bundles_v1.md` §3.2 (leave-one-out) and §3.3 (additions). Cost is per correct answer, from `claude -p`.

B1 (reference): 85.7% accuracy, 13.6% answered and wrong, $0.0079 per correct answer.

| arm | accuracy | vs B1 (pp) | 95% CI | answered and wrong | $/correct | decision |
|---|---|---|---|---|---|---|
| remove playbook (#2) | 81.4% | -4.3 | [-9.7, +0.8] | 17.8% | +1% | keep (removal costs ≥ 3 pp) |
| remove examples (#6) | 79.8% | -5.8 | [-10.5, -1.2] | 18.6% | +19% | keep (removal costs ≥ 3 pp) |
| remove dictionary + profile (#3, #4) | 84.5% | -1.2 | [-5.0, +2.7] | 15.5% | +3% | keep (between 1 and 3 pp, adds ≤ 10% cost) |
| remove lookup (#5) | 84.5% | -1.2 | [-5.8, +3.5] | 14.7% | +3% | keep (same) |
| glossary back to basic (#1) | 83.7% | -1.9 | [-6.6, +2.3] | 15.5% | +4% | keep (same) |
| add repair with hints (#13, #19) | 86.0% | +0.4 | [-3.5, +4.7] | 13.2% | +0% | reject (needs ≥ +4 pp, or answered-and-wrong down ≥ 3 pp) |
| add repair + domain lint + reconciliation (#13, #14, #22, #23) | 82.9% | -2.7 | [-7.8, +2.3] | 13.2% | +5% | reject |

Final configuration: **B1 unchanged** (playbook v1, invariants glossary, dictionary with derivations, enum profile,
value lookup, three BM25 examples from TRAIN, free SQL, goal briefing, effort low, Haiku, one candidate, no repair).
No parameter was dropped, so the pruned-winner confirmation run is not needed.

Reading:

- The two clear contributors are retrieved examples and the playbook, both learned from TRAIN only. Removing
  either one also raises answered-and-wrong by 4–5 points.
- Checking and fixing (repair, lint, reconciliation) adds nothing once the model has the knowledge, and the full
  check chain trends worse. That matches stage 1, where B7 (traps in code) tied B0.
- Not run in the 7-hour plan: the remaining descent arms (MCP interaction layer, cascade, effort, examples k), the
  abstention and clarification arms, and the Sonnet transfer check. B1's cascade variant is the most promising
  follow-up (B8 was the cheapest multi-model bundle in stage 1).
