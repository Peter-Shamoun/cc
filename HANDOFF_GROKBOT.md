# HANDOFF_GROKBOT.md: Grok Bot session, Oct 25 2026, 3:26 to about 4:55 PM PT

**For Claude Code.** While your usage was out, Peter had Grok Bot act as main dev on this repo. This file lists what landed, what is still in flight, what was deliberately left alone, and what to do next on the agent analyzer. Last refreshed: **4:45 PM PT, Oct 25 2026** (final). Grok Bot stopped handing out new work at 4:40 PM PT; no cloud agent is still running on this repo.

Short version: 94 PRs opened against `claude/trusting-heisenberg-8xvazk` (92 merged, 2 open and conflicting: [#31](https://github.com/Peter-Shamoun/cc/pull/31) and [#50](https://github.com/Peter-Shamoun/cc/pull/50)). Peter asked for PRs to be **auto-merged**, so a script merged every cleanly mergeable PR into the working branch once a minute until 5 PM PT. Nothing was reviewed by hand. Treat every merged PR as unreviewed, and review it before you build on it.

## Ground rules that were held

- **Generator frozen at 3.3.** No generator changes.
- **Parser, silver, scale runs and audit 4 left alone.** That is your uncommitted work in your own environment. No PR touches `ccsim/parser.py`, `ccsim/edi_mock.py`, silver compaction, the scale-run scripts, or the realism audits. **One exception you must know about:** the security stress test ([#34](https://github.com/Peter-Shamoun/cc/pull/34)) made small hardening edits to `ccsim/pipeline.py` (+4/−3, a locked-down reader temp dir and memory cap), `ccsim/query.py` (+57/−1, agent SQL audit log and the guard), `bench/guard.py`, `ccsim/aws/cloudtrail.py` (hash chain) and `claude/schema/04_agent_interface.py`. If your uncommitted silver or pipeline work touches `ccsim/pipeline.py`, expect a small rebase conflict there. #34 reports `pytest -q tests/` green at 386 passed.
- **No paid API calls.** Every new LLM client raises `PaidAPIDisabled` unless `allow_paid_api=True`. Tests use scripted backends. The grid "analyst" was the cloud agent's own model (see the caveats).
- **Disjoint paths.** Each agent was told to write only its own files (`analyst/approaches/<id>/`, `analyst/variants/<name>/`, `research/approaches/<id>.md`, `research/grid/<cell>.*`, and so on). A few late PRs still conflicted. They're listed under the caveats.
- `main` is stale. All work targets `claude/trusting-heisenberg-8xvazk`.

Path audit at refresh time: these merged PRs touch guarded paths. Review them first:

- [#34](https://github.com/Peter-Shamoun/cc/pull/34): `ccsim/pipeline.py`, `ccsim/query.py`

## What was built

### Core (wave 1)

- **Research refresh** (`research/sota_refresh_2.md`). Recommendations: don't ship the screen-1 pitfalls text, because Haiku's +16 didn't replicate on held-out (−1.9) while Sonnet's +7.4 did. Treat every glossary change as a trained artifact with TRAIN/VAL/TEST splits. Agent v1 is one model in a short tool loop over `sem.*`. Screen 2 is paired one-factor-at-a-time on a VAL of about 120 harder questions, for both Haiku and Sonnet.
- **Analyzer v1** (`analyzer/`, `docs/agent/README.md`). Harness-agnostic analyst over `ccsim.query`, with these tools: describe, sample, profile, dictionary, glossary lookup, and guarded `run_sql`. It also has an audited `request_escalation` and PHI redaction on output. Loop: understand → link → draft → execute → repair (max 2) → verify → answer or refuse. Config flags map to the screen factors, plus `candidates` (execution vote) and `cascade=haiku_then_sonnet`. `python -m bench.eval_subagents prepare RUN --agent analyzer` adapts it to the bench.
- **Glossary train/val/test pipeline + power analysis + screen-2 infrastructure**: [#54](https://github.com/Peter-Shamoun/cc/pull/54) merged at 4:37 PM PT after its conflict was resolved by hand (`glossary splits, learner, screen 2`).
- **Security / HIPAA stress test**: [#34](https://github.com/Peter-Shamoun/cc/pull/34) merged.
- **Chat transcript storage, two competing designs**: [#50](https://github.com/Peter-Shamoun/cc/pull/50) open, **conflicting** (auto-merge skips it).
- **`grokbot-r1` validation questions**: [#19](https://github.com/Peter-Shamoun/cc/pull/19) merged. 50 hard everyday-analyst questions in `bench/questions_grokbot_r1.json` (intended split `validation`, snapshot `e21410ce6805`, 50/50 sound on the self-check). They are deliberately **not** wired into `load_cases`, so the grid's `grokbot-r1` exclusion was a no-op and no grid cell saw them. That makes them clean held-out material for screen 2, once a split manager ingests them.

| PR | Title | Status | Tests / result |
|---|---|---|---|
| [#1](https://github.com/Peter-Shamoun/cc/pull/1) | SOTA refresh 2: text-to-SQL agents and screen-2 design | merged | docs only, no tests |
| [#2](https://github.com/Peter-Shamoun/cc/pull/2) | Analyzer v1: analyst loop over the secure query path | merged | `pytest -q tests/test_analyzer.py tests/test_bench.py -k "not selftest"`: 51 passed; `pytest -q tests/test_analyzer.py`: 12 passed; `pytest -q tests/test_analyzer_sim.py`: 2 passed |
| [#17](https://github.com/Peter-Shamoun/cc/pull/17) | Add DEV_HARD_v1 question list | merged | n/a (see PR body) |
| [#19](https://github.com/Peter-Shamoun/cc/pull/19) | Add 50 grokbot-r1 validation questions | merged | 50 of 50 sound (bench/check_question_file.py self-check) |
| [#31](https://github.com/Peter-Shamoun/cc/pull/31) | Multi-candidate SQL with execution-based selection | open, **conflicting** (auto-merge skips it) | n/a (see PR body) |
| [#34](https://github.com/Peter-Shamoun/cc/pull/34) | Stress-test agent SQL: guard, PHI, and audit | merged | `pytest -q tests/`: 386 passed |
| [#37](https://github.com/Peter-Shamoun/cc/pull/37) | api: FastAPI SSE bridge between the analyzer and the web chat | merged | `PYTHONPATH=. pytest api/tests -q`: 22 passed |
| [#50](https://github.com/Peter-Shamoun/cc/pull/50) | Chat transcripts: ephemeral default and persisted design behind a mode switch | open, **conflicting** (auto-merge skips it) | 244 passed |
| [#52](https://github.com/Peter-Shamoun/cc/pull/52) | web: CC chat frontend v0 | merged | 18 vitest tests passed |
| [#54](https://github.com/Peter-Shamoun/cc/pull/54) | Seal glossary learning into train, validation, and test | merged (conflict resolved by hand, 4:37 PM PT) | n/a (see PR body) |
| [#78](https://github.com/Peter-Shamoun/cc/pull/78) | web v1: Streamdown, live api/ round-trip, edit-and-branch, mobile drawer, search/rename, virtualized tables | merged | 23/23 pass |
| [#84](https://github.com/Peter-Shamoun/cc/pull/84) | web v1.1: thumbs-down feedback panel, Ctrl/Cmd+K search, Ctrl/Cmd+Shift+O new chat | merged | n/a (see PR body) |
| [#87](https://github.com/Peter-Shamoun/cc/pull/87) | web v1.2: pin follow-up question to top while the answer streams (ChatGPT/Claude scroll behavior) | merged | n/a (see PR body) |

### Agent-analyzer approaches A01 to A10

Each approach is self-contained in `analyst/approaches/<id>/`, with a write-up in `research/approaches/<id>.md`. None changes `bench/eval_subagents.py`. Each one plugs in as a legal technique config plus a manifest rewrite or driver, and `eval_subagents score` re-executes the submitted SQL. None of them claims Haiku or Sonnet accuracy yet.

- **A01 single-shot**: one SELECT, one execution, no repair. This is the floor. A separate run on question slice 0 scored 17/20 with the cloud agent's model. Misses: a zero-filled empty population, a payment-method definition (checks dropped), and a flag encoded as text instead of a boolean.
- **A02 tool loop**: explore → draft → repair at most twice on an error, an empty result or a suspicious result. Programmatic checks cover `is_latest_submission`, rates within 0..100, NULL columns and grain. Known risk: a repair can drop the filter that made the result empty.
- **A03 metric-spec compiler** (the text-to-metrics arm, `analyst/approaches/a03_metric_spec/`): [#33](https://github.com/Peter-Shamoun/cc/pull/33) merged.
- **A04 multi-candidate**: 3 prompt-diverse SQLs, all executed. Agreement wins, and one pairwise selector call breaks a three-way split, with abstain allowed (CHASE-SQL-lite).
- **A05 plan → execute → fresh-context critic**: allows one revision.
- **A06 explore-first** (ReFoRCE-style templated probes, at most 13 aggregate statements, then one SQL): [#16](https://github.com/Peter-Shamoun/cc/pull/16) merged.
- **A07 checked CTE decomposition**: population → join → metric → aggregation → ranking. Each step is checked for row count, grain uniqueness and conserved sums. It catches join fan-out, but a wrong population that isn't empty still passes.
- **A08 self-consistency**: 5 attempts, a normalized vote, and the vote margin used as confidence (4–1 ships, 3–2 cascades, no majority abstains). Correlated errors make it confidently wrong, so publish `unanimous_wrong` and the calibration gap before you keep it.
- **A09 assumptions-first** (lock the reading of the question, then query): [#30](https://github.com/Peter-Shamoun/cc/pull/30) merged.
- **Typed metric compiler** (`analyst/variants/typed_metric_compiler/`, write-up `research/variants/typed_metric_compiler.md`): [#20](https://github.com/Peter-Shamoun/cc/pull/20) merged. The model submits a `QuestionSpec` (catalog metrics, dimensions, filters, grain and an explicit date basis), which is compiled through the existing `SemanticAPI` in `claude/schema/04_agent_interface.py`. Free SQL runs only after `cannot_express`. Coverage is only **11.6% of concrete analytic questions** (17.1% with families), and joins, unions and windows dominate the misses. Verdict: keep it as a first lane with free SQL behind it, and score the covered subset and the complement separately.
- **Context retrieval** (`analyst/variants/context_retrieval/`, write-up `research/variants/context_retrieval.md`): [#22](https://github.com/Peter-Shamoun/cc/pull/22) merged. A tenant value index (payers, CPT/HCPCS, CARC/RARC, dx, with no PHI columns and a tenant-checked `lookup`), a schema linker, and train-only few-shot. Verdict: schema linking and few-shot probably won't help on a 10-view `sem.*`, and the value index is the only arm with a non-zero prior. The primary screen arm is `cr_value` vs `cr0_base`. Its tests are in `tests/test_variant_context_retrieval.py` (7 passed).
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
| [#20](https://github.com/Peter-Shamoun/cc/pull/20) | Typed metric compiler: spec, date basis, gated SQL fallback | merged | n/a (see PR body) |
| [#22](https://github.com/Peter-Shamoun/cc/pull/22) | Context retrieval: tenant value index, schema linking, train-only few-shot | merged | 7 passed |
| [#30](https://github.com/Peter-Shamoun/cc/pull/30) | A09 assumptions-first analyst: lock the reading, then query | merged | n/a (see PR body) |
| [#33](https://github.com/Peter-Shamoun/cc/pull/33) | A03 metric-spec compiler for the analyst agent | merged | `python3 -m pytest analyst/approaches/a03_metric_spec/test_a03_metric_spec.py -q`: 12 passed |

### Parameter grid ("gradient descent" over analyzer settings)

Peter wanted a factorial search over analyzer parameters, where "off" counts as a level too. The factors are generation ∈ {direct_sql, metric_spec, decompose_cte} plus 7 binary flags: fewshot, explore, candidates3, verifier, repair, assumptions and multiagent. That gives 3 × 2^7 = **384** cells.

- **Design**: [`research/grid/combos.json`](research/grid/combos.json), the same as `grid_design_v1.json`. It has 100 cells, C000 to C099: 4 anchors plus 96 chosen by D-optimal random search with generation balanced, seed 7. The anchors are C000 = all-off direct_sql, C001 = direct_sql with everything on, C002 = metric_spec all-off, and C003 = direct_sql with repair only.
- **Protocol**: [`research/grid/GRID_PROTOCOL.md`](research/grid/GRID_PROTOCOL.md). One cloud agent per cell, acting as the analyst itself on frozen-3.3 data through a non-escalated `sem.*` reader. The no-contamination rule bars the pitfalls glossary, reference SQL and other cells until the answers are committed, and scoring uses `bench.compare.match`.
- **Ceiling finding**: the v1 dev set (the first 20 non-held-out questions by sha256) hit **ceiling**. 25 of the 30 v1 cells on the branch scored 20/20, including all-off direct SQL and all-on, so v1 can't separate the factors. The remaining v1 cells (C030 to C099) were abandoned, and the grid moved to **DEV_HARD_v1** under [`research/grid/GRID_PROTOCOL_V2_ADDENDUM.md`](research/grid/GRID_PROTOCOL_V2_ADDENDUM.md), as the H-series (H000+), with the same design order.
- **DEV_HARD_v1** is on the branch (`research/grid/DEV_HARD_v1.json`). Screen 1 publishes only config-level accuracy, so the builder fell back to `difficulty == hard`, taking the first 30 of 42 by sha256. That is **not** a measured-hardness set. The dataset is the 20-day bench-test snapshot `98e61f03e594`, because the 45-day world wasn't on disk.
- H-series cells on the branch at refresh time: 100. **DEV_HARD_v1 is also near ceiling for this model** (see the raw marginals below, at about 90% pooled, with several 30/30 cells). Factor differences are a point or two, which is within noise at n=30 per cell. A tag-based "hard" set isn't hard enough for a strong model.
- **DEV_HARD_v2 proposal** ([#82](https://github.com/Peter-Shamoun/cc/pull/82) merged): `research/grid/DEV_HARD_v2_PROPOSAL.md`. It moves to the 45-day bench default world (`cbb77dba2ad6`, rebuilt locally and not committed because it contains the answer key), where data traps actually occur. For example, August payments go from 0 to 122. `research/grid/INDEPENDENCE_NOTES.md` gives **weight 0** to the explicit-reuse cells (H018–20, H022–24, H026–28, H030–32, H034–36, H038–40, H066–69) in the primary analysis.

**Grid headlines**, quoted from [`research/grid/SUMMARY.md`](research/grid/SUMMARY.md) Generated by `research/grid/aggregate.py` at 2026-10-25 16:33 PDT. Re-run it to refresh; do not hand-edit. Read the file for the ranking and per-cell tables.

### Status

- Cells found: **130** (128 valid, 2 rejected by schema/design checks).
- H-series (DEV_HARD_v1, protocol v2 - the series that matters): 98 cells, 2642/2940 question-passes (89.9%).
- C-series (v1 dev set - at ceiling): 30 cells, 589/600 question-passes (98.2%).
- Sources: open PRs, working branch.
- Contamination-flagged cells (excluded from the clean fit): 20 (C000, C006, C007, C010, C011, H000, H001, H050, H051, H052, H056, H057, H058, H059, H060, H061, H062, H063, H064, H091).
- Soft-flagged only (same executor saw earlier cells' pass/fail; kept in the clean fit): 9 (H018, H026, H027, H028, H030, H031, H038, H039, H040).

### Headline

- Largest H-series effects: repair +2.1 pp [-1.4, +5.6]; candidates3 +1.5 pp [-2.1, +5.0]; gen:metric_spec +1.0 pp [-2.6, +4.6].
- CIs excluding zero: none yet.
- Ridge-logit check (naive SEs, not cluster-robust, so optimistic) has CIs excluding zero for: gen:metric_spec (+0.39 log-odds), candidates3 (+0.49 log-odds), repair (+0.57 log-odds), assumptions (+0.35 log-odds).
- Strongest 2-way interaction: fewshot x assumptions +9.2 pp [+1.8, +16.6] (one of ~30 screened, so treat as a lead, not a finding).
- H-series cell accuracy: mean 89.9%, range 70-100% across 98 cells; between-cell spread is small relative to per-question difficulty.
- Best predicted combination: 035 (metric_spec, flags 1110110) at 92.7% with ~2.9 SQL execs/question.

### Main effects: H-series (primary)

Linear probability model, pass ~ generation (ref = direct_sql) + 7 flags + question fixed effects; cluster-robust SEs by cell, t(97) CIs. 98 cells, 30 questions, 7 questions dropped as constant across cells (all-pass/all-fail carry no information under question FE), 2254 informative observations.

| term | effect (pp) | 95% CI (pp) | identified | ridge-logit log-odds [95% CI] |
|---|---|---|---|---|
| gen:metric_spec | +1.0 | [-2.6, +4.6] | yes | +0.39 [+0.04, +0.73] |
| gen:decompose_cte | -0.8 | [-5.5, +3.8] | yes | +0.16 [-0.18, +0.51] |
| fewshot | +0.1 | [-3.5, +3.7] | yes | +0.17 [-0.13, +0.46] |
| explore | +0.6 | [-2.7, +3.9] | yes | +0.27 [-0.03, +0.57] |
| candidates3 | +1.5 | [-2.1, +5.0] | yes | +0.49 [+0.21, +0.77] |
| verifier | -0.8 | [-4.5, +3.0] | yes | +0.05 [-0.25, +0.34] |
| repair | +2.1 | [-1.4, +5.6] | yes | +0.57 [+0.29, +0.85] |
| assumptions | +0.8 | [-3.0, +4.6] | yes | +0.35 [+0.06, +0.64] |
| multiagent | -0.8 | [-4.3, +2.8] | yes | +0.12 [-0.17, +0.41] |

Top two-way interactions (each added alone to the main-effects model):

| interaction | est (pp) | 95% CI (pp) | t | cells with both on |
|---|---|---|---|---|
| fewshot x assumptions | +9.2 | [+1.8, +16.6] | +2.46 | 26 |
| explore x assumptions | -6.4 | [-13.3, +0.5] | -1.82 | 26 |
| repair x assumptions | +4.8 | [-2.5, +12.1] | +1.31 | 24 |
| fewshot x verifier | +4.7 | [-2.7, +12.1] | +1.26 | 21 |
| verifier x multiagent | +4.6 | [-2.7, +11.9] | +1.25 | 18 |

### Main effects: H-series, contamination-flagged cells excluded

Linear probability model, pass ~ generation (ref = direct_sql) + 7 flags + question fixed effects; cluster-robust SEs by cell, t(82) CIs. 83 cells, 30 questions, 9 questions dropped as constant across cells (all-pass/all-fail carry no information under question FE), 1743 informative observations.

| term | effect (pp) | 95% CI (pp) | identified | ridge-logit log-odds [95% CI] |
|---|---|---|---|---|
| gen:metric_spec | +2.2 | [-2.1, +6.5] | yes | +0.49 [+0.11, +0.87] |
| gen:decompose_cte | -1.0 | [-6.9, +4.9] | yes | +0.11 [-0.26, +0.48] |
| fewshot | -0.2 | [-4.7, +4.3] | yes | +0.05 [-0.27, +0.37] |
| explore | -0.3 | [-4.3, +3.8] | yes | +0.13 [-0.19, +0.46] |
| candidates3 | +1.6 | [-3.2, +6.3] | yes | +0.50 [+0.20, +0.80] |
| verifier | -0.8 | [-5.6, +4.1] | yes | +0.09 [-0.23, +0.41] |
| repair | +2.5 | [-1.9, +6.9] | yes | +0.61 [+0.31, +0.91] |
| assumptions | +1.3 | [-3.4, +6.0] | yes | +0.39 [+0.08, +0.70] |
| multiagent | -1.8 | [-6.2, +2.6] | yes | +0.06 [-0.25, +0.37] |

#### Quick raw marginals (computed by this handoff from the cell JSONs)

Raw pooled pass rate on DEV_HARD_v1 across **94 cells**: 89.7% (94 cells). These are unadjusted marginals (the design is unbalanced, so factors are confounded). Use SUMMARY.md's regression for effects.

| Factor | Level / on | Off |
|---|---|---|
| generation = direct_sql | 89.7% (34 cells) | |
| generation = metric_spec | 90.5% (31 cells) | |
| generation = decompose_cte | 88.9% (29 cells) | |
| fewshot | 89.6% (45 cells) | 89.8% (49 cells) |
| explore | 89.8% (46 cells) | 89.7% (48 cells) |
| candidates3 | 90.3% (47 cells) | 89.1% (47 cells) |
| verifier | 89.3% (37 cells) | 90.0% (57 cells) |
| repair | 90.3% (50 cells) | 89.0% (44 cells) |
| assumptions | 90.1% (44 cells) | 89.4% (50 cells) |
| multiagent | 89.3% (41 cells) | 90.0% (53 cells) |

Top cells by raw score: H001 (direct_sql `1111111`) 30/30, H021 (decompose_cte `0110001`) 30/30, H022 (decompose_cte `1000110`) 30/30, H023 (direct_sql `0011100`) 30/30, H024 (metric_spec `1010010`) 30/30.

Cell results on the branch:

| Cell | generation | bits fewshot/explore/cand3/verifier/repair/assume/multi | dev set | accuracy | n | mean SQL execs | model | agent_note |
|---|---|---|---|---|---|---|---|---|
| C000 | direct_sql | `0000000` | v1 | 20/20 | 20 | ? | Grok 4.7 |  |
| C001 | direct_sql | `1111111` | v1 | 20/20 | 20 | 3.0 | Grok 4.7 |  |
| C002 | metric_spec | `0000000` | v1 | 20/20 | 20 | 1.0 | grok-4.7 |  |
| C003 | direct_sql | `0000100` | v1 | 20/20 | 20 | 1.0 | grok-4.7 |  |
| C004 | decompose_cte | `1101001` | v1 | 20/20 | 20 | 3.6 | Grok 4.7 |  |
| C005 | decompose_cte | `0101010` | v1 | 20/20 | 20 | 3.8 | Grok 4.7 |  |
| C006 | decompose_cte | `0101011` | v1 | 20/20 | 20 | 2.15 | grok-4.7 |  |
| C007 | direct_sql | `1110010` | v1 | 20/20 | 20 | 3.0 | Grok 4.7 |  |
| C008 | metric_spec | `1110011` | v1 | 20/20 | 20 | 8.0 | grok-4.7 |  |
| C009 | decompose_cte | `0111010` | v1 | 20/20 | 20 | 5.85 | Grok 4.7 |  |
| C010 | decompose_cte | `0100001` | v1 | 20/20 | 20 | 2.0 | Grok 4.7 |  |
| C011 | direct_sql | `0010000` | v1 | 20/20 | 20 | 3.0 | Grok 4.7 |  |
| C012 | direct_sql | `0101000` | v1 | 20/20 | 20 | 2.0 | grok-4.7 | reused analyzer-v1 builder |
| C013 | metric_spec | `0100100` | v1 | 20/20 | 20 | 2.0 | grok-4.7 | reused analyzer-v1 builder |
| C014 | metric_spec | `0011010` | v1 | 20/20 | 20 | 3.0 | grok-4.7 | reused analyzer-v1 builder |
| C015 | decompose_cte | `1001100` | v1 | 20/20 | 20 | 1.65 | Grok 4.7 | reused A10 builder |
| C016 | direct_sql | `1010010` | v1 | 20/20 | 20 | 3.0 | Grok 4.7 | reused A10 builder |
| C017 | metric_spec | `0110010` | v1 | 20/20 | 20 | 3.0 | Grok 4.7 | reused A10 builder |
| C018 | metric_spec | `1010100` | v1 | 20/20 | 20 | 3.0 | grok-4.7 | sequential cell, same agent as C002 |
| C019 | decompose_cte | `0001011` | v1 | 20/20 | 20 | 9.8 | grok-4.7 | sequential cell, same agent as C002 |
| C020 | metric_spec | `0110100` | v1 | 20/20 | 20 | 4.0 | grok-4.7 | sequential cell, same agent as C002 |
| C021 | decompose_cte | `0110001` | v1 | 17/20 | 20 | 5.0 | grok-4.7 | reused A01 builder |
| C022 | decompose_cte | `1000110` | v1 | 17/20 | 20 | 3.0 | grok-4.7 | reused A01 builder |
| C023 | direct_sql | `0011100` | v1 | 17/20 | 20 | 3.0 | grok-4.7 | reused A01 builder |
| C024 | metric_spec | `1010010` | v1 | 20/20 | 20 | 3.0 | Grok 4.7 | reused A05 builder |
| C025 | metric_spec | `0110001` | v1 | 20/20 | 20 | 4.2 | Grok 4.7 | reused A05 builder |
| C026 | metric_spec | `1101001` | v1 | 20/20 | 20 | 2.25 | Grok 4.7 | reused A05 builder |
| C027 | decompose_cte | `0000110` | v1 | 19/20 | 20 | 2.0 | grok-4.7 | reused A01-slice agent |
| C028 | decompose_cte | `0110100` | v1 | 19/20 | 20 | 4.0 | grok-4.7 | reused A01-slice agent |
| C029 | direct_sql | `1110110` | v1 | 20/20 | 20 | 3.0 | grok-4.7 | reused A01-slice agent |
| H000 | direct_sql | `0000000` | DEV_HARD_v1 | 28/30 | 30 | 1.0 | Grok 4.7 | DEV_HARD builder; saw glossary/family templates earlier |
| H001 | direct_sql | `1111111` | DEV_HARD_v1 | 30/30 | 30 | 4.33 | Grok 4.7 | DEV_HARD builder; saw glossary/family templates earlier |
| H002 | metric_spec | `0000000` | DEV_HARD_v1 | 28/30 | 30 | 1.0 | Grok 4.7 | sequential, same agent as C005 |
| H003 | direct_sql | `0000100` | DEV_HARD_v1 | 28/30 | 30 | 1.17 | Grok 4.7 | sequential, same agent as C005 |
| H004 | decompose_cte | `1101001` | DEV_HARD_v1 | 27/30 | 30 | 1.23 | Grok 4.7 | sequential, same agent as C005 |
| H005 | decompose_cte | `0101010` | DEV_HARD_v1 | 27/30 | 30 | 1.23 | Grok 4.7 | reused A04 builder |
| H006 | decompose_cte | `0101011` | DEV_HARD_v1 | 27/30 | 30 | 1.23 | Grok 4.7 | reused A04 builder |
| H007 | direct_sql | `1110010` | DEV_HARD_v1 | 28/30 | 30 | 3.23 | Grok 4.7 | reused A04 builder |
| H008 | metric_spec | `1110011` | DEV_HARD_v1 | 28/30 | 30 | 4.0 | Grok 4.7 | sequential, same agent as C001 |
| H009 | decompose_cte | `0111010` | DEV_HARD_v1 | 28/30 | 30 | 6.0 | Grok 4.7 | sequential, same agent as C001 |
| H010 | decompose_cte | `0100001` | DEV_HARD_v1 | 28/30 | 30 | 4.0 | Grok 4.7 | sequential, same agent as C001 |
| H011 | direct_sql | `0010000` | DEV_HARD_v1 | 28/30 | 30 | 3.0 | grok-4.7 | reused A07 builder |
| H012 | direct_sql | `0101000` | DEV_HARD_v1 | 29/30 | 30 | 2.267 | grok-4.7 | reused A07 builder |
| H013 | metric_spec | `0100100` | DEV_HARD_v1 | 29/30 | 30 | 2.0 | grok-4.7 | reused A07 builder |
| H014 | metric_spec | `0011010` | DEV_HARD_v1 | 26/30 | 30 | 4.0 | Grok 4.7 | reused A08 builder |
| H015 | decompose_cte | `1001100` | DEV_HARD_v1 | 26/30 | 30 | 3.0667 | Grok 4.7 | reused A08 builder |
| H016 | direct_sql | `1010010` | DEV_HARD_v1 | 26/30 | 30 | 3.0 | Grok 4.7 | reused A08 builder |
| H017 | metric_spec | `0110010` | DEV_HARD_v1 | 28/30 | 30 | 1.07 | Grok Bot (local executor; analyst = this agent) | grokbot local executor b0 |
| H018 | metric_spec | `1010100` | DEV_HARD_v1 | 28/30 | 30 | 1.13 | Grok Bot (local executor; analyst = this agent) | grokbot local executor b0 |
| H019 | decompose_cte | `0001011` | DEV_HARD_v1 | None | 30 | None | Grok Bot (local executor; analyst = this agent) | grokbot local executor b0 |
| H020 | metric_spec | `0110100` | DEV_HARD_v1 | None | 30 | None | Grok Bot (local executor; analyst = this agent) | grokbot local executor b0 |
| H021 | decompose_cte | `0110001` | DEV_HARD_v1 | 30/30 | 30 | 2.47 | Grok Bot (local executor) | grokbot local executor b1 |
| H022 | decompose_cte | `1000110` | DEV_HARD_v1 | 30/30 | 30 | 1.0 | Grok Bot (local executor) | grokbot local executor b1 |
| H023 | direct_sql | `0011100` | DEV_HARD_v1 | 30/30 | 30 | 1.07 | Grok Bot (local executor) | grokbot local executor b1 |
| H024 | metric_spec | `1010010` | DEV_HARD_v1 | 30/30 | 30 | 1.0 | Grok Bot (local executor) | grokbot local executor b1 |
| H025 | metric_spec | `0110001` | DEV_HARD_v1 | 27/30 | 30 | 1.3 | Grok Bot (local executor acting as analyst) | grokbot local executor b2 |
| H026 | metric_spec | `1101001` | DEV_HARD_v1 | 29/30 | 30 | 1 | Grok Bot (local executor acting as analyst) | grokbot local executor b2 |
| H027 | decompose_cte | `0000110` | DEV_HARD_v1 | 29/30 | 30 | 1.07 | Grok Bot (local executor acting as analyst) | grokbot local executor b2 |
| H028 | decompose_cte | `0110100` | DEV_HARD_v1 | 29/30 | 30 | 1.37 | Grok Bot (local executor acting as analyst) | grokbot local executor b2 |
| H029 | direct_sql | `1110110` | DEV_HARD_v1 | 0.9333333333333333 | 30 | 1.07 | Grok Bot (local executor acting as analyst) | grokbot local executor b3 |
| H030 | decompose_cte | `1011100` | DEV_HARD_v1 | 0.9333333333333333 | 30 | 3.47 | Grok Bot (local executor acting as analyst) | grokbot local executor b3 |
| H031 | metric_spec | `1001010` | DEV_HARD_v1 | 0.9333333333333333 | 30 | 0.0 | Grok Bot (local executor acting as analyst) | grokbot local executor b3 |
| H032 | decompose_cte | `1101101` | DEV_HARD_v1 | 0.9333333333333333 | 30 | 2.33 | Grok Bot (local executor acting as analyst) | grokbot local executor b3 |
| H033 | metric_spec | `0000101` | DEV_HARD_v1 | 26/30 | 30 | 1.77 | Grok Bot (local executor; acting as analyst) | grokbot local executor b4 |
| H034 | direct_sql | `0001110` | DEV_HARD_v1 | 27/30 | 30 | 2.2 | Grok Bot (local executor; acting as analyst) | grokbot local executor b4 |
| H035 | metric_spec | `1110110` | DEV_HARD_v1 | 27/30 | 30 | 2.07 | Grok Bot (local executor; acting as analyst) | grokbot local executor b4 |
| H036 | direct_sql | `0110100` | DEV_HARD_v1 | 27/30 | 30 | 2.0 | Grok Bot (local executor; acting as analyst) | grokbot local executor b4 |
| H037 | metric_spec | `1010001` | DEV_HARD_v1 | 24/30 | 30 | 2.47 | Grok Bot (local executor acting as analyst) | grokbot local executor b5 |
| H038 | direct_sql | `0101011` | DEV_HARD_v1 | 25/30 | 30 | 1.4 | Grok Bot (local executor acting as analyst) | grokbot local executor b5 |
| H039 | direct_sql | `0111101` | DEV_HARD_v1 | 25/30 | 30 | 1.23 | Grok Bot (local executor acting as analyst) | grokbot local executor b5 |
| H040 | metric_spec | `1001100` | DEV_HARD_v1 | 25/30 | 30 | 1.03 | Grok Bot (local executor acting as analyst) | grokbot local executor b5 |
| H041 | direct_sql | `0001100` | DEV_HARD_v1 | 28/30 | 30 | 1.233 | Grok 4.7 | reused A02 builder |
| H042 | direct_sql | `1010100` | DEV_HARD_v1 | 28/30 | 30 | 3.0 | Grok 4.7 | reused A02 builder |
| H043 | direct_sql | `1011000` | DEV_HARD_v1 | 28/30 | 30 | 3.0 | Grok 4.7 | reused A02 builder |
| H044 | direct_sql | `0010101` | DEV_HARD_v1 | 25/30 | 30 | 3.0667 | Grok 4.7 | sequential, same agent as C007 |
| H045 | direct_sql | `0010001` | DEV_HARD_v1 | 25/30 | 30 | 3.0 | Grok 4.7 | sequential, same agent as C007 |
| H046 | metric_spec | `0010001` | DEV_HARD_v1 | 25/30 | 30 | 3.0 | Grok 4.7 | sequential, same agent as C007 |
| H047 | decompose_cte | `1101111` | DEV_HARD_v1 | 27/30 | 30 | 2.3 | grok-4.7 | reused A06 builder |
| H048 | direct_sql | `0101110` | DEV_HARD_v1 | 25/30 | 30 | 1.2 | grok-4.7 | reused A06 builder |
| H049 | metric_spec | `1101111` | DEV_HARD_v1 | 27/30 | 30 | 1.3 | grok-4.7 | reused A06 builder |
| H050 | direct_sql | `1011001` | DEV_HARD_v1 | 27/30 | 30 | 3.067 | Grok 4.7 | same agent as C011; saw family templates earlier |
| H051 | metric_spec | `1000111` | DEV_HARD_v1 | 27/30 | 30 | 1.167 | Grok 4.7 | same agent as C011; saw family templates earlier |
| H052 | metric_spec | `1101101` | DEV_HARD_v1 | 27/30 | 30 | 1.133 | Grok 4.7 | same agent as C011; saw family templates earlier |
| H053 | direct_sql | `0100110` | DEV_HARD_v1 | 25/30 | 30 | 2.0 | Grok 4.7 | reused grokbot-r1 author; knows the glossary and dictionary |
| H054 | decompose_cte | `0011101` | DEV_HARD_v1 | 26/30 | 30 | 3.433 | Grok 4.7 | reused grokbot-r1 author; knows the glossary and dictionary |
| H055 | metric_spec | `0101010` | DEV_HARD_v1 | 25/30 | 30 | 2.0 | Grok 4.7 | reused grokbot-r1 author; knows the glossary and dictionary |
| H056 | direct_sql | `1100010` | DEV_HARD_v1 | 28/30 | 30 | 1.0 | Grok 4.7 | reused typed-compiler builder; saw gold queries for coverage |
| H057 | decompose_cte | `1000001` | DEV_HARD_v1 | 28/30 | 30 | 1.167 | Grok 4.7 | reused typed-compiler builder; saw gold queries for coverage |
| H058 | metric_spec | `1000010` | DEV_HARD_v1 | 25/30 | 30 | 1.0 | Grok 4.7 | reused typed-compiler builder; saw gold queries for coverage |
| H059 | decompose_cte | `0100100` | DEV_HARD_v1 | 28/30 | 30 | 3.033 | Grok 4.7 | reused context-retrieval builder; read gold SQL earlier |
| H060 | direct_sql | `1100011` | DEV_HARD_v1 | 27/30 | 30 | 2.0 | Grok 4.7 | reused context-retrieval builder; read gold SQL earlier |
| H061 | metric_spec | `0010101` | DEV_HARD_v1 | 28/30 | 30 | 3.033 | Grok 4.7 | reused context-retrieval builder; read gold SQL earlier |
| H062 | decompose_cte | `0110111` | DEV_HARD_v1 | 29/30 | 30 | 3.067 | Grok 4.7 | same agent as C010; saw some harness SQL earlier |
| H063 | metric_spec | `0111001` | DEV_HARD_v1 | 29/30 | 30 | 3.033 | Grok 4.7 | same agent as C010; saw some harness SQL earlier |
| H064 | decompose_cte | `1100000` | DEV_HARD_v1 | 29/30 | 30 | 2.033 | Grok 4.7 | same agent as C010; saw some harness SQL earlier |
| H065 | metric_spec | `1111010` | DEV_HARD_v1 | 28/30 | 30 | 4.0 | Grok Bot | local Grok Bot executor l0 |
| H066 | direct_sql | `1100100` | DEV_HARD_v1 | 28/30 | 30 | 2.07 | Grok Bot | local Grok Bot executor l0 |
| H067 | metric_spec | `0010111` | DEV_HARD_v1 | 28/30 | 30 | 3.0 | Grok Bot | local Grok Bot executor l0 |
| H068 | metric_spec | `1100110` | DEV_HARD_v1 | 28/30 | 30 | 2.07 | Grok Bot | local Grok Bot executor l0 |
| H069 | metric_spec | `1110101` | DEV_HARD_v1 | 28/30 | 30 | 4.0 | Grok Bot | local Grok Bot executor l0 |
| H070 | direct_sql | `1011100` | DEV_HARD_v1 | 22/30 | 30 | 3.0 | Grok Bot | local Grok Bot executor l1 |
| H071 | decompose_cte | `1100101` | DEV_HARD_v1 | 22/30 | 30 | 1.03 | Grok Bot | local Grok Bot executor l1 |
| H072 | decompose_cte | `1000100` | DEV_HARD_v1 | 22/30 | 30 | 1.03 | Grok Bot | local Grok Bot executor l1 |
| H073 | decompose_cte | `0101000` | DEV_HARD_v1 | 22/30 | 30 | 1.0 | Grok Bot | local Grok Bot executor l1 |
| H074 | decompose_cte | `0111111` | DEV_HARD_v1 | 22/30 | 30 | 3.03 | Grok Bot | local Grok Bot executor l1 |
| H075 | metric_spec | `1101110` | DEV_HARD_v1 | 28/30 | 30 | 1.2 | Grok Bot (local executor l2) | local Grok Bot executor l2 |
| H076 | direct_sql | `0000011` | DEV_HARD_v1 | 27/30 | 30 | 1.0 | Grok Bot (local executor l2) | local Grok Bot executor l2 |
| H077 | metric_spec | `1011111` | DEV_HARD_v1 | 28/30 | 30 | 3.2 | Grok Bot (local executor l2) | local Grok Bot executor l2 |
| H078 | direct_sql | `1100111` | DEV_HARD_v1 | 28/30 | 30 | 1.2 | Grok Bot (local executor l2) | local Grok Bot executor l2 |
| H079 | decompose_cte | `1011110` | DEV_HARD_v1 | 28/30 | 30 | 3.2 | Grok Bot (local executor l2) | local Grok Bot executor l2 |
| H080 | direct_sql | `0010100` | DEV_HARD_v1 | 29/30 | 30 | 3.1 | Grok Bot (local executor, analyst played in-context) | local Grok Bot executor l3 |
| H081 | metric_spec | `0010010` | DEV_HARD_v1 | 28/30 | 30 | 3.0 | Grok Bot (local executor, analyst played in-context) | local Grok Bot executor l3 |
| H082 | decompose_cte | `0010100` | DEV_HARD_v1 | 27/30 | 30 | 3.03 | Grok Bot (local executor, analyst played in-context) | local Grok Bot executor l3 |
| H083 | direct_sql | `0000010` | DEV_HARD_v1 | 23/30 | 30 | 1.0 | Grok Bot (local executor, analyst played in-context) | local Grok Bot executor l3 |
| H084 | direct_sql | `1001111` | DEV_HARD_v1 | 29/30 | 30 | 1.13 | Grok Bot (local executor, analyst played in-context) | local Grok Bot executor l3 |
| H085 | decompose_cte | `1010001` | DEV_HARD_v1 | 27/30 | 30 | 3.0 | Grok Bot (l4 local executor) | local Grok Bot executor l4 |
| H086 | decompose_cte | `1110001` | DEV_HARD_v1 | 27/30 | 30 | 3.23 | Grok Bot (l4 local executor) | local Grok Bot executor l4 |
| H087 | metric_spec | `0000100` | DEV_HARD_v1 | 27/30 | 30 | 1.17 | Grok Bot (l4 local executor) | local Grok Bot executor l4 |
| H088 | direct_sql | `0000110` | DEV_HARD_v1 | 27/30 | 30 | 1.17 | Grok Bot (l4 local executor) | local Grok Bot executor l4 |
| H089 | direct_sql | `0000001` | DEV_HARD_v1 | 26/30 | 30 | 1.0 | Grok Bot (l4 local executor) | local Grok Bot executor l4 |
| H090 | direct_sql | `0001101` | DEV_HARD_v1 | 27/30 | 30 | 1.2 | Grok Bot (local executor) | local Grok Bot executor l5 |
| H091 | decompose_cte | `1001111` | DEV_HARD_v1 | 28/30 | 30 | 2.0 | Grok Bot (local executor) | local Grok Bot executor l5 |
| H092 | direct_sql | `1010011` | DEV_HARD_v1 | 28/30 | 30 | 3.0 | Grok Bot (local executor) | local Grok Bot executor l5 |
| H093 | decompose_cte | `0111100` | DEV_HARD_v1 | 28/30 | 30 | 3.1 | Grok Bot (local executor) | local Grok Bot executor l5 |
| H094 | metric_spec | `0111110` | DEV_HARD_v1 | 28/30 | 30 | 3.0 | Grok Bot (local executor) | local Grok Bot executor l5 |
| H095 | decompose_cte | `1010000` | DEV_HARD_v1 | 24/30 | 30 | 3.0 | Grok Bot (local executor) | local Grok Bot executor l6 |
| H096 | metric_spec | `1111000` | DEV_HARD_v1 | 23/30 | 30 | 6.83 | Grok Bot (local executor) | local Grok Bot executor l6 |
| H097 | decompose_cte | `1010110` | DEV_HARD_v1 | 27/30 | 30 | 3.17 | Grok Bot (local executor) | local Grok Bot executor l6 |
| H098 | direct_sql | `0110010` | DEV_HARD_v1 | 24/30 | 30 | 6.8 | Grok Bot (local executor) | local Grok Bot executor l6 |
| H099 | decompose_cte | `1100011` | DEV_HARD_v1 | 21/30 | 30 | 4.8 | Grok Bot (local executor) | local Grok Bot executor l6 |

Grid PRs:

| PR | Title | Status | Tests / result |
|---|---|---|---|
| [#5](https://github.com/Peter-Shamoun/cc/pull/5) | Grid cell C000: direct SQL, 20/20 | merged | accuracy 20/20 (bench.compare.match) |
| [#8](https://github.com/Peter-Shamoun/cc/pull/8) | C002 metric-spec grid cell: 20/20 on the dev set | merged | accuracy 20/20 (bench.compare.match) |
| [#9](https://github.com/Peter-Shamoun/cc/pull/9) | Grid C005: decompose_cte answers (20/20) | merged | accuracy 20/20 (bench.compare.match) |
| [#12](https://github.com/Peter-Shamoun/cc/pull/12) | Grid C001: direct SQL on the 20-question dev set (20/20) | merged | accuracy 20/20 (bench.compare.match) |
| [#15](https://github.com/Peter-Shamoun/cc/pull/15) | C007 grid cell: direct SQL, few-shot, explore, 3 candidates | merged | accuracy 20/20 (bench.compare.match) |
| [#18](https://github.com/Peter-Shamoun/cc/pull/18) | Grid C011: direct SQL, 3 candidates, 20/20 | merged | accuracy 20/20 (bench.compare.match) |
| [#23](https://github.com/Peter-Shamoun/cc/pull/23) | Grid C010: decompose_cte + explore + multiagent (20/20) | merged | accuracy 20/20 (bench.compare.match) |
| [#24](https://github.com/Peter-Shamoun/cc/pull/24) | Grid cells C027–C029 on the sha256 dev set | merged | accuracy 19/20 (bench.compare.match) |
| [#25](https://github.com/Peter-Shamoun/cc/pull/25) | C003 direct SQL grid cell (20/20) | merged | accuracy 20/20 (bench.compare.match) |
| [#26](https://github.com/Peter-Shamoun/cc/pull/26) | Grid aggregation: pooled factor effects + SUMMARY.md | merged | n/a (see PR body) |
| [#27](https://github.com/Peter-Shamoun/cc/pull/27) | Grid cell C009: decompose-CTE analyst, 20/20 | merged | accuracy 20/20 (bench.compare.match) |
| [#29](https://github.com/Peter-Shamoun/cc/pull/29) | Grid cells C012, C013, and C014 (20/20 each) | merged | accuracy 20/20 (bench.compare.match) |
| [#32](https://github.com/Peter-Shamoun/cc/pull/32) | Grid cell C006: decompose CTE, explore, verifier (20/20) | merged | accuracy 20/20 (bench.compare.match) |
| [#35](https://github.com/Peter-Shamoun/cc/pull/35) | Grid H000 and H001 on DEV_HARD_v1 | merged | accuracy 28/30 (bench.compare.match) |
| [#36](https://github.com/Peter-Shamoun/cc/pull/36) | grid: H025–H028 (grokbot local executor b2, DEV_HARD_v1) | merged | n/a (see PR body) |
| [#38](https://github.com/Peter-Shamoun/cc/pull/38) | grid: H026–H028 + H025 scoring (grokbot local executor b2) | merged | n/a (see PR body) |
| [#39](https://github.com/Peter-Shamoun/cc/pull/39) | Grid H029–H032 (DEV_HARD_v1): grokbot local executor b3 | merged | n/a (see PR body) |
| [#40](https://github.com/Peter-Shamoun/cc/pull/40) | grid b4 (grokbot local): H033–H036 on DEV_HARD_v1 | merged | n/a (see PR body) |
| [#41](https://github.com/Peter-Shamoun/cc/pull/41) | Grid batch 5 (H037-H040): grokbot local executor b5 | merged | n/a (see PR body) |
| [#42](https://github.com/Peter-Shamoun/cc/pull/42) | Grid H002–H004 on DEV_HARD_v1 | merged | accuracy 28/30 (bench.compare.match) |
| [#43](https://github.com/Peter-Shamoun/cc/pull/43) | Grid cell C004: decompose-CTE analyst, 20/20 dev accuracy | merged | accuracy 20/20 (bench.compare.match) |
| [#44](https://github.com/Peter-Shamoun/cc/pull/44) | Grid H062-H064 on DEV_HARD_v1 (29/30 each) | merged | accuracy 29/30 (bench.compare.match) |
| [#45](https://github.com/Peter-Shamoun/cc/pull/45) | Grid H017–H020 (DEV_HARD_v1), grokbot local executor b0 | merged | n/a (see PR body) |
| [#46](https://github.com/Peter-Shamoun/cc/pull/46) | Grid H030–H032 (DEV_HARD_v1): grokbot local executor b3 (continued) | merged | n/a (see PR body) |
| [#47](https://github.com/Peter-Shamoun/cc/pull/47) | grid: batch b1 cells H021-H024 (grokbot local executor) | merged | n/a (see PR body) |
| [#48](https://github.com/Peter-Shamoun/cc/pull/48) | Grid C008: metric spec, few-shot, explore, 3 candidates (20/20) | merged | accuracy 20/20 (bench.compare.match) |
| [#49](https://github.com/Peter-Shamoun/cc/pull/49) | Grid batch 5 cont. (H038-H040): grokbot local executor b5 | merged | n/a (see PR body) |
| [#51](https://github.com/Peter-Shamoun/cc/pull/51) | Grid cells C015–C017: analyst benchmark on the 20-question dev set | merged | n/a (see PR body) |
| [#53](https://github.com/Peter-Shamoun/cc/pull/53) | grid b4 (grokbot local): H033–H036 scores + summaries | merged | n/a (see PR body) |
| [#55](https://github.com/Peter-Shamoun/cc/pull/55) | Grid H032 (DEV_HARD_v1): grokbot local executor b3 | merged | n/a (see PR body) |
| [#56](https://github.com/Peter-Shamoun/cc/pull/56) | grid: local Grok Bot executor l1 (H070–H074) | merged | n/a (see PR body) |
| [#57](https://github.com/Peter-Shamoun/cc/pull/57) | grid l3: cells H080-H084 (local Grok Bot executor) | merged | n/a (see PR body) |
| [#58](https://github.com/Peter-Shamoun/cc/pull/58) | grid: b1 H023-H024 answers + scores for H021-H024 (grokbot local executor) | merged | n/a (see PR body) |
| [#59](https://github.com/Peter-Shamoun/cc/pull/59) | Grid batch 5 (H039, H040): grokbot local executor b5 | merged | n/a (see PR body) |
| [#60](https://github.com/Peter-Shamoun/cc/pull/60) | GRID H008–H010 on DEV_HARD_v1 | merged | accuracy 28/30 (bench.compare.match) |
| [#61](https://github.com/Peter-Shamoun/cc/pull/61) | Grid H018–H020 (DEV_HARD_v1), grokbot local executor b0 | merged | n/a (see PR body) |
| [#62](https://github.com/Peter-Shamoun/cc/pull/62) | grid: H065-H069 (local Grok Bot executor l0) | merged | n/a (see PR body) |
| [#63](https://github.com/Peter-Shamoun/cc/pull/63) | Grid cells H056, H057, and H058 on DEV_HARD_v1 | merged | accuracy 28/30 (bench.compare.match) |
| [#64](https://github.com/Peter-Shamoun/cc/pull/64) | grid: H095–H099 (local Grok Bot executor l6) | merged | n/a (see PR body) |
| [#65](https://github.com/Peter-Shamoun/cc/pull/65) | grid: local executor l2 cells H075-H079 (protocol v2, DEV_HARD_v1) | merged | n/a (see PR body) |
| [#66](https://github.com/Peter-Shamoun/cc/pull/66) | grid: H090-H094 (local Grok Bot executor l5) | merged | n/a (see PR body) |
| [#67](https://github.com/Peter-Shamoun/cc/pull/67) | Grid cells H053–H055 on DEV_HARD_v1 | merged | n/a (see PR body) |
| [#68](https://github.com/Peter-Shamoun/cc/pull/68) | grid: H085–H089 (local Grok Bot executor l4, DEV_HARD_v1) | merged | n/a (see PR body) |
| [#69](https://github.com/Peter-Shamoun/cc/pull/69) | Grid cells H059–H061 on DEV_HARD_v1 | merged | accuracy 28/30 (bench.compare.match) |
| [#70](https://github.com/Peter-Shamoun/cc/pull/70) | H050–H052 on DEV_HARD_v1 | merged | n/a (see PR body) |
| [#71](https://github.com/Peter-Shamoun/cc/pull/71) | Grid cells H005, H006, and H007 on DEV_HARD_v1 | merged | n/a (see PR body) |
| [#72](https://github.com/Peter-Shamoun/cc/pull/72) | grid: H095–H099 scores + summaries (l6 follow-up) | merged | n/a (see PR body) |
| [#73](https://github.com/Peter-Shamoun/cc/pull/73) | Grid cells H014–H016 on DEV_HARD_v1 | merged | accuracy 26/30 (bench.compare.match) |
| [#74](https://github.com/Peter-Shamoun/cc/pull/74) | Grid cells C021, C022, and C023 | merged | n/a (see PR body) |
| [#75](https://github.com/Peter-Shamoun/cc/pull/75) | grid: l2 follow-up — H077/H079 answers + H075-H079 scores and summaries | merged | n/a (see PR body) |
| [#76](https://github.com/Peter-Shamoun/cc/pull/76) | Grid cells C024–C026: metric_spec benchmark results | merged | n/a (see PR body) |
| [#77](https://github.com/Peter-Shamoun/cc/pull/77) | grid l3: H080-H084 scores + summaries | merged | n/a (see PR body) |
| [#79](https://github.com/Peter-Shamoun/cc/pull/79) | Grid cells H047–H049 on DEV_HARD_v1 | merged | n/a (see PR body) |
| [#80](https://github.com/Peter-Shamoun/cc/pull/80) | Grid H011–H013 on DEV_HARD_v1 | merged | accuracy 28/30 (bench.compare.match) |
| [#81](https://github.com/Peter-Shamoun/cc/pull/81) | Grid cells H041–H043 on DEV_HARD_v1 | merged | accuracy 28/30 (bench.compare.match) |
| [#82](https://github.com/Peter-Shamoun/cc/pull/82) | grid: DEV_HARD_v2 proposal (45-day default snapshot cbb77dba2ad6) + independence notes | merged | n/a (see PR body) |
| [#83](https://github.com/Peter-Shamoun/cc/pull/83) | Grid SUMMARY refresh (1620) | merged | n/a (see PR body) |
| [#86](https://github.com/Peter-Shamoun/cc/pull/86) | grid l5: H093-H094 answers + scoring for H090-H094 | merged | n/a (see PR body) |
| [#89](https://github.com/Peter-Shamoun/cc/pull/89) | Grid aggregation v2: contamination tiers + SUMMARY refresh | merged | n/a (see PR body) |
| [#90](https://github.com/Peter-Shamoun/cc/pull/90) | GRID H044–H046 on DEV_HARD_v1 | merged | n/a (see PR body) |
| [#91](https://github.com/Peter-Shamoun/cc/pull/91) | Grid cells C018, C019, and C020 | merged | accuracy 20/20 (bench.compare.match) |
| [#92](https://github.com/Peter-Shamoun/cc/pull/92) | Grid SUMMARY refresh (1632) | merged | n/a (see PR body) |

### Product surface

- **React chat frontend in `web/`** (Vite + React + TypeScript, built by local Grok Bot workers): present on the branch. PRs: [#52](https://github.com/Peter-Shamoun/cc/pull/52) merged, [#78](https://github.com/Peter-Shamoun/cc/pull/78) merged, [#84](https://github.com/Peter-Shamoun/cc/pull/84) merged, [#87](https://github.com/Peter-Shamoun/cc/pull/87) merged. Spec: `docs/frontend-spec.md`. Architecture, the contract, the PHI rules and the to-do list: `docs/frontend.md`. It looks and behaves like ChatGPT (layout, pill composer, density) and has a Claude-style resizable artifact panel. It was built clean-room from observation, with no ChatGPT, Claude or vercel/chatbot code copied. Streamdown (Vercel, Apache-2.0) renders the markdown, and its notice is in `web/THIRD_PARTY_NOTICES.md`. It isn't a vercel/chatbot fork, and `docs/frontend.md` says what a full fork would take.
  - **Typed contract** (`web/src/lib/contract.ts`): a `Part` union and a `StreamEvent` union with a pure reducer. Parts: GFM markdown, sortable and virtualized tables (copy as TSV, download CSV, PHI masking), CSV file chips, images with a lightbox, Vega-Lite charts, Shiki code, a collapsed SQL disclosure, callouts (assumptions, caveats, PHI), "Thought for N s", tool-call rows and artifact cards.
  - **Themes:** Neutral (ChatGPT-like) and Paper (Claude-like), each in light and dark. It also has edit-and-branch, a mobile drawer, and chat search and rename.
  - **Run and test:** `cd web && npm install && npm test && npm run build`. #78 reports 23 vitest tests passing. A mock adapter runs offline, and `VITE_ANALYST_BASE` points it at `api/`.
  - **Not done:** CSP and no-store headers, self-hosted fonts, axe checks, version diffs, and the pixel polish against the logged-in UIs (those weren't captured).
- **Analyzer API in `api/`** (FastAPI + uvicorn, `docs/api.md`): present on the branch. PRs: [#37](https://github.com/Peter-Shamoun/cc/pull/37) merged.
  - `POST /api/chat` streams SSE events that match the web contract (tool calls, text, SQL, a table preview of ≤50 rows, a file chip, a chart and callouts), with `Last-Event-ID` resume and a stop endpoint. It also has a CSV download bound to the owner with a 15-minute expiry, and audited `GET /api/download/{id}`.
  - It uses the offline `ScriptedLLM` by default, and paid clients raise `PaidAPIDisabled`. `sim` auth verifies Cognito tokens and opens sessions only through `ccsim.query.open_session`, and chat never escalates. PHI columns are nulled server-side.
  - Tests: `PYTHONPATH=. pytest api/tests -q` gives 22 passed.

## Caveats (read before trusting any number here)

1. **The grid analyst was the agents' own model**, **not Haiku or Sonnet**. Cloud-agent cells record "Grok 4.7". Late H cells were run by local Grok Bot executors on Peter's box ("Grok Bot (local executor)", `agent_note` such as `local Grok Bot executor l6`). The grid ranks protocols for a strong model, and the ranking may not transfer to Haiku or Sonnet, where the screen-1 effects lived.
2. **n is small.** 20 questions per v1 cell and 30 per H cell. With n=20, one question is 5 points, and 20/20 says nothing about differences. Pool main effects across cells (that is why every cell shares a dev set), and don't compare single cells.
3. **Reused agents.** Some cells were run by agents that had already finished another task or cell, for example `agent_note: "reused A04 builder"`. Those agents had read approach code and docs first. Those records carry `agent_note`, and their no-contamination guarantee is weaker. Several cells (C000, C011) also report that family SQL templates in `bench/question_families.py` or the pitfalls text were visible while they located the dev-set rule. Treat family questions in those cells as not clean.
4. Two snapshots are in use: `98e61f03e594` (20 days, volume scale 0.3, seed 5) and `e21410ce6805` (benchmark default). Check `dataset` before pooling.
5. Approach modules were tested only with scripted backends. No end-to-end accuracy was measured with a real Haiku or Sonnet.
6. Auto-merge means no human or independent-reviewer pass. That breaks your WORKPLAN loop discipline ("independent reviewer per deliverable"), so run that review now. Also run `pytest -q tests/`, which none of the agents ran in full (about 20 min). Approach tests live next to their modules and aren't collected by `testpaths = tests/`.

### Merge conflicts

**Open PRs that conflict with the working branch (the auto-merger skips these; resolve by hand):**

- [#50](https://github.com/Peter-Shamoun/cc/pull/50) Chat transcripts: ephemeral default and persisted design behind a mode switch
- [#31](https://github.com/Peter-Shamoun/cc/pull/31) Multi-candidate SQL with execution-based selection

Conflict log from the auto-merge script (PRs that conflicted at some point; check whether each later merged above):

```
16:04:50 conflict #31 Multi-candidate SQL with execution-based selection
16:10:39 conflict #54 Seal glossary learning into train, validation, and test
16:10:46 conflict #50 Chat transcripts: ephemeral default and persisted design behind a mode switch
```

### Grid cells that never ran

- C030 to C099 (70 v1 cells): abandoned because the v1 dev set was at ceiling; superseded by the H-series.
- All 100 H cells (H000 to H099) have result files, but 2 were rejected by the aggregator's schema/design checks and 20 cells are contamination-flagged and excluded from the clean fit (see `research/grid/SUMMARY.md`).
- The other 284 of the full 384-combination design were never scheduled. The merged design is in `research/grid/design.json` (and `research/grid/combos.json`).

## Deliberately left alone

- Parser improvement loop (speed and memory, byte-identical Parquet, slimmed `edi.segment`)
- Silver changes (open-month compaction, claim-key index)
- Scale runs (2 customers × 1 year, 1 customer × 3 years)
- Realism audit 4 (the final verdict for generator 3.3)
- The generator (frozen at 3.3). `ccsim/query.py` got only the #34 hardening above, with no change to the Cognito → STS → scoped-session flow.
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
3. **Replace DEV_HARD_v1 with real hardness.** It is near ceiling for a strong model too. Re-run screen 1 with per-question pass/fail logging and take the questions Haiku actually fails, or adopt `research/grid/DEV_HARD_v2_PROPOSAL.md` (45-day world), or author the Opus multi-step hard set. The 50 `grokbot-r1` questions (#19) are unseen by every grid cell and can seed VAL once a split manager ingests them. Aim for about 120 VAL questions (the floor is 80) so that a 10-point effect is detectable, per `sota_refresh_2.md`. Treat grid factor effects as provisional until then.
4. **Transfer test on Haiku and Sonnet** once there is an API budget: `eval_subagents prepare --agent analyzer` on the held-out set, comparing analyzer v1 with the top 2 or 3 grid configs and the A04/A08 voting arms. Accept a factor only if the paired-bootstrap interval is above 0 for each model.
5. **Consolidate.** Fold the winning approaches back into `analyzer/` as config flags, and delete the losing `analyst/approaches/*`.
6. **Frontend and API**: the round trip already works in demo mode (#78). Next, swap `ScriptedLLM` for the real analyzer cascade behind `allow_paid_api`, pick the transcript store (#50's design), add CSP and no-store headers, and re-check the licence notices before anything ships.
7. Abstention, ambiguity and multi-turn splits (T20 to T22) are still unbuilt. `agree_or_abstain` and A08's abstain rule are the hooks.
