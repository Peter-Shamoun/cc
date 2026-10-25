# DEV_HARD_v2 proposal: rerun the hard set on the 45-day bench default snapshot

Written by a Grok Bot local executor on 2026-10-25 (PT), branch `grokbot/dev-hard-v2`, base
`claude/trusting-heisenberg-8xvazk` at `8ffbc42`. Generator, parser, silver, scale runs and bench code are untouched.

## TL;DR

- **New snapshot:** `cbb77dba2ad6`. This is the bench default scenario (`days=45, volume_scale=0.6, seed=2026,
  p_corrupt_file=0.1`) on **generator 3.3**, customer `acme_labs`. Built once, serially, in **262 s** on
  the box. No crash. The box kept 10 GB+ RAM free during the build. Snapshot size is 15 MB. Volume was **not** lowered.
- **All four DEV_HARD_v1 trap conditions that were missing on `98e61f03e594` now exist in the data** (table below).
- **29 of 30 DEV_HARD_v1 ids exist on the new snapshot.** `f_cpt_share_of_line_payments__g0480` is not generated
  there. Proposed replacement: `f_cpt_share_of_line_payments__g0481`. It's in the same family, not held out, and not
  already in the set. It was picked by id only, without opening the question or its key.
- **Rerun protocol:** one fresh agent context per cell, and every cell's answers committed before *any* cell is scored.
  The reasoning is in `INDEPENDENCE_NOTES.md`.

## Build

```bash
cd <clone of claude/trusting-heisenberg-8xvazk>
python -c "from bench.dataset import snapshot, DEFAULT; from ccsim.edi_mock import Scenario; print(snapshot(Scenario(**DEFAULT)))"
# -> .ccsim/bench/cbb77dba2ad6   (~4.5 min, single process; do NOT run several builds in parallel)
```

The exact script used was `snapshot(Scenario(**dict(DEFAULT, volume_scale=0.6)))`, which is the same thing. It ran with
`nice -n 5` using an existing grid venv (`/workspace/cc_grid/b0/.venv`) and `PYTHONPATH` set to this clone.

**Notes on the dataset id.** The id hashes the scenario plus `ccsim/edi_mock.py`, the CLFS csv, `ccsim/parser.py`,
`ccsim/pipeline.py` and the two schema SQL files. `ccsim/pipeline.py` changed after DEV_HARD_v1 was built, in commit
`991cf3b` ("Harden agent SQL …", security PR #34). So on the current branch the *old* scenario
(`days=20, volume_scale=0.3, seed=5`) now hashes to `76597c34786d`, not `98e61f03e594`. If anyone touches those
six files again, the id changes and the snapshot has to be rebuilt. **Pin the commit when you rerun.** This build was at `8ffbc42`.

The snapshot holds the answer key (`truth*`, `answer_key.duckdb`, `generated_questions.json`), so it is **not**
committed. Rebuild it with the command above. `.ccsim/` is gitignored. It was built on the Grok Bot box at
`/workspace/cc_grid/big/.ccsim/bench/cbb77dba2ad6`.

The earlier bus error came from about 6 builds running in parallel. A single build needs well under 1 GB RSS for most of its run
on this 16 GB box. Build once per machine, then copy the `.ccsim/bench/<id>` dir to the other clones.

## Trap-condition stats (data-level counts only)

Computed by `research/grid/dev_hard_v2_trap_stats.py`, which reads only `data/sem/*.parquet`. It does not touch the
answer key, question families, glossary or reference SQL. Full JSON is in `research/grid/dev_hard_v2/`.

Condition | v1 snapshot `98e61f03e594` (20 d, 0.3, seed 5) | **v2 snapshot `cbb77dba2ad6` (45 d, 0.6, seed 2026)**
---|---|---
Submission date range | 2026-07-01 .. 07-20 | 2026-07-01 .. **08-14**
Payment date range | 2026-07-01 .. 07-22 | 2026-07-01 .. **08-18**
**August payments** (sem.payments / sem.remit_claims) | **0 / 0** | **122 / 760**
**Freq-8 voids** in sem.claims (rows / claim_keys) | **0 / 0** (1 freq-8 row on the 835 side only) | **1 / 1** (0 on the 835 side)
**Referring providers with ≥15 claims** (distinct claim_key) | 1 (max 19) | **30** (max 84, 298 providers)
Referring providers with ≥15 *denied* latest claims | 0 (max 1, 3 denied claims total) | 0 (max 3, 72 denied claims total)
**Corrected (freq-7) claims with the freq-1 original in range** | **0 of 10** | **6 of 61**
claim_keys with >1 submission | 1 | 64
Claims ever reversed | 0 | 1
Rows: claims / claim_lines / payments / remit_claims / adjustments | 513 / 2,464 / 130 / 504 / 3,979 | 2,448 / 12,529 / 381 / 2,441 / 19,604
Generated question pool (`load_cases`) | 128 (27 held out) | 200 (27 held out)

Caveats:
- **Freq-8 voids are still thin** (exactly 1 claim). Any void question will hinge on one row. It is no longer
  degenerate, but it's weak.
- "Provider with ≥15 claims" holds for referring NPIs counted over all claims. Counted over *denied* claims it
  still fails (max 3). If the trap is about denial counts per provider, this snapshot does not exercise it. A larger
  volume (e.g. 1.0) would be the next lever. Keep `days=45`.
- `rendering_npi` is null everywhere and there is a single billing NPI, so "provider" means referring NPI.

## Recommended rerun protocol (for Claude Code)

1. **Pin the data.** Rebuild `cbb77dba2ad6` at commit `8ffbc42` (or confirm the id matches on your commit), with one build
   per machine. Record `dataset_id` in every cell JSON.
2. **Dev set = `DEV_HARD_v2`.** Use the 29 surviving DEV_HARD_v1 ids plus `f_cpt_share_of_line_payments__g0481`. Optionally re-run
   the DEV_HARD selection rule (lowest pass-rate, non-held-out) on the new pool and report overlap. Don't re-tune the set
   on v2 scores, or you've selected on the outcome.
3. **One fresh agent context per cell.** No agent runs two cells. The agent sees only `GRID_PROTOCOL.md`, the v2
   addendum, its cell config, the question texts, the sem schema, and the sandboxed snapshot connection. It must
   not see other cells' SQL, answers, scores, `research/grid/H*.json|md`, `bench/question_families.py`, the pitfalls
   glossary, `answer_key*`, `truth*`, `generated_questions.json`, or any `gold_sql/alt_sql/wrong_sql/answer_key_sql`
   field. Run `load_cases` only through a wrapper that strips those keys.
4. **Commit before scoring, globally.** Phase 1: every cell writes `answers/<cell>.json` (SQL + result + assumptions)
   and commits. Phase 2 starts only after all cells in the wave have committed. A single scorer process
   runs `bench.compare.match` over all of them and writes the scores into a separate file. Cells never see their own score.
5. **Verifier / multiagent factors must use fresh sub-contexts** too. "Same agent, separate pass" counts as `multiagent=0`.
6. **Concurrency:** run cells in parallel (they are independent), but snapshot builds serially.
7. **Analysis:** report v1-vs-v2 per config. Use cell-level independence, so no cluster correction is needed if step 3 holds.
   Keep a per-question breakdown of the trap questions (August, void, provider, corrected-claim) to show the
   ceiling is gone.
8. **Budget:** 100 cells × 30 questions. No paid API calls; use the same model tier as v1.

## Files in this PR

- `research/grid/DEV_HARD_v2_PROPOSAL.md`: this file
- `research/grid/INDEPENDENCE_NOTES.md`: cells that reused earlier cells' SQL, with recommended weights
- `research/grid/dev_hard_v2_trap_stats.py`: data-only trap counter (`python research/grid/dev_hard_v2_trap_stats.py .ccsim/bench/<id>`)
- `research/grid/dev_hard_v2/stats_{cbb77dba2ad6,98e61f03e594}.json`: raw counts
