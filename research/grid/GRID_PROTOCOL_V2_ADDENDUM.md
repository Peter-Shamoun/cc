# GRID PROTOCOL v2 addendum (supersedes the dev-set rule only)

Why: cells C000, C002 and C005 all scored 20/20 on the v1 dev set. That's a ceiling, so it can't tell the factors apart.
Everything else in GRID_PROTOCOL.md still applies: factor definitions, the no-contamination rule, the output schema and the file paths.

## v2 dev set: DEV_HARD_v1
- The id list lives at `research/grid/DEV_HARD_v1.json` on `claude/trusting-heisenberg-8xvazk`. The builder agent writes it, and it gets merged automatically.
- If you are a cell agent and the file isn't on the branch yet: `git fetch` every 2 minutes for up to 15 minutes. If it still hasn't appeared, run on the v1 dev set and set `"dev_set": "v1_fallback"`.
- Use the dataset named in that file (prefer the largest frozen-3.3 scenario that's available, for example the 45-day bench world, over the 20-day bench-test snapshot).
- In each cell JSON set `"dev_set": "DEV_HARD_v1"` and `"protocol": "v2"`.

## Building DEV_HARD_v1 (builder agent only)
- Pool: every benchmark question, minus the 27 `heldout_hard_1` questions, minus anything tagged `grokbot-r1` or labelled validation or test by a split manager on the branch.
- Hardness signal: per-question pass rates from existing screen-1 / screen results in the repo (Haiku preferred, then Sonnet). Read ONLY the pass and fail outcome per question id. Don't read answer text, reference SQL, gold, or failure write-ups.
- Pick the 30 questions with the lowest pass rate. Break ties by sha256(question_id). If there's no screen data at all, use the hardest question families by tag or difficulty instead.
- Write `research/grid/DEV_HARD_v1.json` as {"ids": [...], "dataset": {...scenario/snapshot id...}, "selection_rule": "...", "source_files": [...]}, and open a PR on its own, quickly (within about 10 minutes), before you run any cell.

## Contamination hotspots (do NOT open before your answers are committed)
- `bench/question_families.py`: it holds gold SQL templates for the parametric families.
- The pitfalls glossary, and any answer key or reference_sql fields in the question bank. To get question ids and text, read only the id and question fields.
