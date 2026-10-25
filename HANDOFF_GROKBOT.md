# HANDOFF_GROKBOT.md: Grok Bot session, Oct 25 2026, 3:26 to about 4:55 PM PT

**For Claude Code.** While your usage was out, Peter had Grok Bot act as main dev on this repo. This file lists what landed, what is still in flight, what was deliberately left alone, and what to do next on the agent analyzer. Last refreshed: **4:00 PM PT, Oct 25 2026**.

Short version: 20 PRs opened against `claude/trusting-heisenberg-8xvazk` (19 merged, 1 open at refresh time). Peter asked for PRs to be **auto-merged**, so a script merged every cleanly mergeable PR into the working branch once a minute until 5 PM PT. Nothing was reviewed by hand. Treat every merged PR as unreviewed, and review it before you build on it.

## Ground rules that were held

- **Generator frozen at 3.3.** No generator changes.
- **Parser, silver, scale runs and audit 4 left alone.** That is your uncommitted work in your own environment. No Grok Bot PR touches `ccsim/parser.py`, silver compaction, the scale-run scripts, or the realism audits. Your in-flight changes should rebase cleanly, but check `ccsim/`, `bench/` and `tests/` anyway.
- **No paid API calls.** Every new LLM client raises `PaidAPIDisabled` unless `allow_paid_api=True`. Tests use scripted backends. The grid "analyst" was the cloud agent's own model (see the caveats).
- **Disjoint paths.** Each agent wrote only its own files (`analyst/approaches/<id>/`, `research/approaches/<id>.md`, `research/grid/<cell>.*`, and so on), so the PRs don't conflict with each other.
- `main` is stale. All work targets `claude/trusting-heisenberg-8xvazk`.

## What was built

### Core (wave 1)

- **Research refresh** (`research/sota_refresh_2.md`). Recommendations: don't ship the screen-1 pitfalls text, because Haiku's +16 didn't replicate on held-out (−1.9) while Sonnet's +7.4 did. Treat every glossary change as a trained artifact with TRAIN/VAL/TEST splits. Agent v1 is one model in a short tool loop over `sem.*`. Screen 2 is paired one-factor-at-a-time on a VAL of about 120 harder questions, for both Haiku and Sonnet.
- **Analyzer v1** (`analyzer/`, `docs/agent/README.md`). Harness-agnostic analyst over `ccsim.query`, with these tools: describe, sample, profile, dictionary, glossary lookup, and guarded `run_sql`. It also has an audited `request_escalation` and PHI redaction on output. Loop: understand → link → draft → execute → repair (max 2) → verify → answer or refuse. Config flags map to the screen factors, plus `candidates` (execution vote) and `cascade=haiku_then_sonnet`. `python -m bench.eval_subagents prepare RUN --agent analyzer` adapts it to the bench.
- **Glossary train/val/test pipeline + power analysis + screen-2 infrastructure**: no PR yet.
- **Security / HIPAA stress test**: no PR yet.
- **Chat transcript storage, two competing designs**: no PR yet.
- **`grokbot-r1` validation questions**: [#19](https://github.com/Peter-Shamoun/cc/pull/19) merged. These landed after the v1 dev set and DEV_HARD_v1 were drawn, so the grid's `grokbot-r1` exclusion was a no-op for both; they are untouched by the grid and usable as fresh validation.

| PR | Title | Status | Tests / result |
|---|---|---|---|
| [#1](https://github.com/Peter-Shamoun/cc/pull/1) | SOTA refresh 2: text-to-SQL agents and screen-2 design | merged | docs only, no tests |
| [#2](https://github.com/Peter-Shamoun/cc/pull/2) | Analyzer v1: analyst loop over the secure query path | merged | `pytest -q tests/test_analyzer.py tests/test_bench.py -k "not selftest"`: 51 passed; `pytest -q tests/test_analyzer.py`: 12 passed; `pytest -q tests/test_analyzer_sim.py`: 2 passed |
| [#17](https://github.com/Peter-Shamoun/cc/pull/17) | Add DEV_HARD_v1 question list | merged | n/a (see PR body) |
| [#19](https://github.com/Peter-Shamoun/cc/pull/19) | Add 50 grokbot-r1 validation questions | merged | n/a (see PR body) |
| [#20](https://github.com/Peter-Shamoun/cc/pull/20) | Typed metric compiler: spec, date basis, gated SQL fallback | open (auto-merge pending) | n/a (see PR body) |

### Agent-analyzer approaches A01 to A10

Each approach is self-contained in `analyst/approaches/<id>/`, with a write-up in `research/approaches/<id>.md`. None changes `bench/eval_subagents.py`. Each one plugs in as a legal technique config plus a manifest rewrite or driver, and `eval_subagents score` re-executes the submitted SQL. None of them claims Haiku or Sonnet accuracy yet.

- **A01 single-shot**: one SELECT, one execution, no repair. This is the floor. A separate run on question slice 0 scored 17/20 with the cloud agent's model. Misses: a zero-filled empty population, a payment-method definition (checks dropped), and a flag encoded as text instead of a boolean.
- **A02 tool loop**: explore → draft → repair at most twice on an error, an empty result or a suspicious result. Programmatic checks cover `is_latest_submission`, rates within 0..100, NULL columns and grain. Known risk: a repair can drop the filter that made the result empty.
- **A03**: no PR yet.
- **A04 multi-candidate**: 3 prompt-diverse SQLs, all executed. Agreement wins, and one pairwise selector call breaks a three-way split, with abstain allowed (CHASE-SQL-lite).
- **A05 plan → execute → fresh-context critic**: allows one revision.
- **A06 explore-first** (ReFoRCE-style templated probes, at most 13 aggregate statements, then one SQL): [#16](https://github.com/Peter-Shamoun/cc/pull/16) merged.
- **A07 checked CTE decomposition**: population → join → metric → aggregation → ranking. Each step is checked for row count, grain uniqueness and conserved sums. It catches join fan-out, but a wrong population that isn't empty still passes.
- **A08 self-consistency**: 5 attempts, a normalized vote, and the vote margin used as confidence (4–1 ships, 3–2 cascades, no majority abstains). Correlated errors make it confidently wrong, so publish `unanimous_wrong` and the calibration gap before you keep it.
- **A09**: no PR yet.
- **A10 dynamic few-shot**: TRAIN-only TF-IDF/BM25 retrieval of 3 examples. Hard leakage guards drop the same family or template, masked-SQL matches, near-duplicate questions, `heldout_hard_1`, and anything that isn't train.

| PR | Title | Status | Tests / result |
|---|---|---|---|
| [#3](https://github.com/Peter-Shamoun/cc/pull/3) | A10 dynamic few-shot: train-only retrieval with leakage guards | merged | `pytest -q analyst/approaches/a10_dynamic_fewshot/test_a10.py`: 11 passed |
| [#4](https://github.com/Peter-Shamoun/cc/pull/4) | A01 single-shot on question slice 0 (17/20) | merged | accuracy 17/20 (bench.compare.match) |
| [#6](https://github.com/Peter-Shamoun/cc/pull/6) | A05 plan-execute-verify analyst with a fresh-context critic | merged | 13 tests passed (scripted) |
| [#7](https://github.com/Peter-Shamoun/cc/pull/7) | A01 single-shot baseline: one SQL statement, one execution | merged | `python -m pytest analyst/approaches/a01_single_shot/test_a01_single_shot.py -q`: 12 passed |
| [#10](https://github.com/Peter-Shamoun/cc/pull/10) | A04 multi-candidate SQL with execution-based selection | merged | `pytest analyst/approaches/a04_multi_candidate/test_a04.py -q`: 24 passed |
| [#11](https://github.com/Peter-Shamoun/cc/pull/11) | A07 decomposition: checked CTE steps for the analyst | merged | 12 passed |
| [#13](https://github.com/Peter-Shamoun/cc/pull/13) | A08 self-consistency: five attempts, normalized vote, margin as confidence | merged | `python -m pytest analyst/approaches/a08_self_consistency/test_self_consistency.py`: 19 passed |
| [#14](https://github.com/Peter-Shamoun/cc/pull/14) | A02 tool loop: explore schema, draft SQL, repair at most twice | merged | 24 passed |
| [#16](https://github.com/Peter-Shamoun/cc/pull/16) | A06 explore-first: probe sem.* before writing SQL | merged | 13 passed |

### Parameter grid ("gradient descent" over analyzer settings)

Peter wanted a factorial search over analyzer parameters, where "off" counts as a level too. The factors are generation ∈ {direct_sql, metric_spec, decompose_cte} plus 7 binary flags: fewshot, explore, candidates3, verifier, repair, assumptions and multiagent. That gives 3 × 2^7 = **384** cells.

- **Design**: [`research/grid/combos.json`](research/grid/combos.json), the same as `grid_design_v1.json`. It has 100 cells, C000 to C099: 4 anchors plus 96 chosen by D-optimal random search with generation balanced, seed 7. The anchors are C000 = all-off direct_sql, C001 = direct_sql with everything on, C002 = metric_spec all-off, and C003 = direct_sql with repair only.
- **Protocol**: [`research/grid/GRID_PROTOCOL.md`](research/grid/GRID_PROTOCOL.md). One cloud agent per cell, acting as the analyst itself on frozen-3.3 data through a non-escalated `sem.*` reader. The no-contamination rule bars the pitfalls glossary, reference SQL and other cells until the answers are committed, and scoring uses `bench.compare.match`.
- **Ceiling finding**: the v1 dev set (the first 20 non-held-out questions by sha256) hit **ceiling**. 6 of the 6 v1 cells on the branch scored 20/20, including all-off direct SQL and all-on, so v1 can't separate the factors. The remaining v1 cells (C030 to C099) were abandoned, and the grid moved to **DEV_HARD_v1** under [`research/grid/GRID_PROTOCOL_V2_ADDENDUM.md`](research/grid/GRID_PROTOCOL_V2_ADDENDUM.md), as the H-series (H000+), with the same design order.
- **DEV_HARD_v1** is on the branch (`research/grid/DEV_HARD_v1.json`). Screen 1 publishes only config-level accuracy, so the builder fell back to `difficulty == hard`, taking the first 30 of 42 by sha256. That is **not** a measured-hardness set. The dataset is the 20-day bench-test snapshot `98e61f03e594`, because the 45-day world wasn't on disk.
- H-series cells on the branch at refresh time: 0.

`research/grid/SUMMARY.md` is not on the branch yet. A Grok Bot aggregation worker (branch `grokbot/grid-aggregate`) is writing it; when it lands it pools main effects across all cells.

Cell results on the branch:

| Cell | generation | bits fewshot/explore/cand3/verifier/repair/assume/multi | dev set | accuracy | n | mean SQL execs | model | agent_note |
|---|---|---|---|---|---|---|---|---|
| C000 | direct_sql | `0000000` | v1 | 20/20 | 20 | ? | Grok 4.7 |  |
| C001 | direct_sql | `1111111` | v1 | 20/20 | 20 | 3.0 | Grok 4.7 |  |
| C002 | metric_spec | `0000000` | v1 | 20/20 | 20 | 1.0 | grok-4.7 |  |
| C005 | decompose_cte | `0101010` | v1 | 20/20 | 20 | 3.8 | Grok 4.7 |  |
| C007 | direct_sql | `1110010` | v1 | 20/20 | 20 | 3.0 | Grok 4.7 |  |
| C011 | direct_sql | `0010000` | v1 | 20/20 | 20 | 3.0 | Grok 4.7 |  |

Grid PRs:

| PR | Title | Status | Tests / result |
|---|---|---|---|
| [#5](https://github.com/Peter-Shamoun/cc/pull/5) | Grid cell C000: direct SQL, 20/20 | merged | accuracy 20/20 (bench.compare.match) |
| [#8](https://github.com/Peter-Shamoun/cc/pull/8) | C002 metric-spec grid cell: 20/20 on the dev set | merged | accuracy 20/20 (bench.compare.match) |
| [#9](https://github.com/Peter-Shamoun/cc/pull/9) | Grid C005: decompose_cte answers (20/20) | merged | accuracy 20/20 (bench.compare.match) |
| [#12](https://github.com/Peter-Shamoun/cc/pull/12) | Grid C001: direct SQL on the 20-question dev set (20/20) | merged | accuracy 20/20 (bench.compare.match) |
| [#15](https://github.com/Peter-Shamoun/cc/pull/15) | C007 grid cell: direct SQL, few-shot, explore, 3 candidates | merged | accuracy 20/20 (bench.compare.match) |
| [#18](https://github.com/Peter-Shamoun/cc/pull/18) | Grid C011: direct SQL, 3 candidates, 20/20 | merged | accuracy 20/20 (bench.compare.match) |

### Product surface

- **React chat frontend in `web/`**: not merged yet (no PR yet). It is built by Grok Bot workers from the open-source Vercel AI Chatbot template (Apache-2.0, notice kept) under a clean-room rule: no proprietary ChatGPT or Claude code, CSS, fonts or logos. It reproduces their look from public observation and screenshots. A response is a stream of typed parts: markdown, sortable table with CSV download, image or plot, Vega-Lite chart, SQL or code with copy, an assumptions callout, tool-call and thinking disclosures, and a side-panel artifact. It has two themes, neutral (ChatGPT-like) and warm serif (Claude-like). It runs on fake data with no paid API. Many pixel sizes in the spec are estimates, because the logged-in UIs weren't captured.
- **Analyzer API in `api/`**: not merged yet (no PR yet). It is the backend for the frontend. It wraps `analyzer/` and streams the typed response parts, using the scripted LLM by default. The UI shows only what the scoped query session returned, and PHI is redacted server-side.

## Caveats (read before trusting any number here)

1. **The grid analyst was the cloud agents' own model** (records say "Grok 4.7"), **not Haiku or Sonnet**. The grid ranks protocols for a strong model, and the ranking may not transfer to Haiku or Sonnet, where the screen-1 effects lived.
2. **n is small.** 20 questions per v1 cell and 30 per H cell. With n=20, one question is 5 points, and 20/20 says nothing about differences. Pool main effects across cells (that is why every cell shares a dev set), and don't compare single cells.
3. **Reused agents.** Some cells were run by agents that had already finished another task or cell. Those records carry `agent_note`, and their no-contamination guarantee is weaker. Several cells (C000, C011) also report that family SQL templates in `bench/question_families.py` or the pitfalls text were visible while they located the dev-set rule. Treat family questions in those cells as not clean.
4. Two snapshots are in use: `98e61f03e594` (20 days, volume scale 0.3, seed 5) and `e21410ce6805` (benchmark default). Check `dataset` before pooling.
5. Approach modules were tested only with scripted backends. No end-to-end accuracy was measured with a real Haiku or Sonnet.
6. Auto-merge means no human or independent-reviewer pass. That breaks your WORKPLAN loop discipline ("independent reviewer per deliverable"), so run that review now. Also run `pytest -q tests/`, which none of the agents ran in full (about 20 min). Approach tests live next to their modules and aren't collected by `testpaths = tests/`.

Merge conflicts logged by the auto-merge script:

None logged.

## Deliberately left alone

- Parser improvement loop (speed and memory, byte-identical Parquet, slimmed `edi.segment`)
- Silver changes (open-month compaction, claim-key index)
- Scale runs (2 customers × 1 year, 1 customer × 3 years)
- Realism audit 4 (the final verdict for generator 3.3)
- The generator (frozen at 3.3) and `ccsim/query.py`'s security path
- Paid API runs of any kind, including the held-out screen of the analyzer

## Stale WORKPLAN entries (update `WORKPLAN.md` when you're back)

- `[running] Calibrate generator…`, `[running] realism engineer on audit-1…`, `[running] realism round 2…` and `[next] Independent realism audit…`: superseded by the 3.3 freeze plus audit 4 as the final verdict.
- `[running] Lake baseline + optimization`: already `[done]` further down (incremental gold, manifests and so on).
- `[running] held-out validation (heldout_hard_1…)`: results are in `research/heldout_results_1.md`, so mark it done.
- `[done, branch cursor/analyzer-v1-9e95] analyzer v1`: now merged via PR #2.
- `PARKED … SOTA research refresh, screen 2 / gradient search, then React front end`: all three were started this session (PR #1, the grid and approaches, and `web/`). Un-park them.
- `[next] harder question set`: DEV_HARD_v1 is only a difficulty-tag stopgap, and the Opus-authored hard set is still open.
- Parser, silver, scale and design-check entries: status is in your environment, not here.

## Recommended next steps for the agent analyzer

1. **Review and test what was auto-merged.** Run `pytest -q tests/` plus each `analyst/approaches/*/test_*.py`. Consider adding `analyst/` to `testpaths`.
2. **Read `research/grid/SUMMARY.md`.** Use it to pick the factors that matter for a strong model on DEV_HARD_v1, then finish the remaining H cells. The full 384 can be run as the remaining 284 cells if the main effects are unclear.
3. **Replace DEV_HARD_v1 with real hardness.** Re-run screen 1 with per-question pass/fail logging, or author the Opus multi-step hard set. Aim for about 120 VAL questions (the floor is 80) so that a 10-point effect is detectable, per `sota_refresh_2.md`.
4. **Transfer test on Haiku and Sonnet** once there is an API budget: `eval_subagents prepare --agent analyzer` on the held-out set, comparing analyzer v1 with the top 2 or 3 grid configs and the A04/A08 voting arms. Accept a factor only if the paired-bootstrap interval is above 0 for each model.
5. **Consolidate.** Fold the winning approaches back into `analyzer/` as config flags, and delete the losing `analyst/approaches/*`.
6. **Frontend and API**: wire `web/` to `api/`, swap the scripted LLM for the real cascade behind `allow_paid_api`, and verify the clean-room and licence notices before anything ships.
7. Abstention, ambiguity and multi-turn splits (T20 to T22) are still unbuilt. `agree_or_abstain` and A08's abstain rule are the hooks.
