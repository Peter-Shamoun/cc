# A04 — multi-candidate SQL with execution-based selection

Approach module: `analyst/approaches/a04_multi_candidate/`. Harness config name: `a04_multi_candidate`.

A04 asks one question three ways, runs every SQL candidate on the guarded `sem.*` path, and returns a result only when the executions agree (after sanity checks) or a pairwise selector picks one side of a disagreement. It is the CHASE-SQL selection pattern, cut down to the three prompt shapes named for screen 2 in `research/sota_refresh_2.md`.

This file is the design. It is not a measured effect. Nothing here was run against Haiku or Sonnet.

## Design

Screen 1 did not turn this factor on. The held-out set then showed that the screen-1 pitfalls text does not travel, and that Sonnet is already high enough on that set that another prompt tweak will not separate configs. The literature review's stage-2 order puts three candidates after a cheap execution-repair factor, and it specifies the shape:

1. Three generators, no temperature. Temperature, top_p, and top_k return HTTP 400 on the current Claude models, so diversity has to come from the prompt.
2. Same system prefix on every generator (schema, glossary, output contract). Only the user message changes, so a backend that caches a prefix pays for it once.
3. Group candidates that return the same rows. A strict majority wins.
4. If there is no majority, one pairwise selector call sees the question, the SQL, and the result rows. It may abstain.
5. Abstaining is a wrong answer for accuracy. The current bench has no reliability score, so a declined selector must not be recorded as a refusal.

The three strategies follow that note, and they line up with two of CHASE-SQL's generators:

| Strategy | User message | CHASE analogue |
|---|---|---|
| `direct` | Write the SELECT. One-line restatement in `notes`. | The single-prompt baseline. |
| `query_plan` | Name grain, population, and time basis (`payment_date`, `service_date`, or `submission_date`), then write the SELECT. | Query-plan chain of thought. |
| `divide_and_conquer` | Split the question into a filter and an aggregation. The SELECT should put the filter in a CTE or subquery. | Divide-and-conquer chain of thought. |

CHASE's third generator builds online synthetic examples. That is left out. Few-shot examples are their own factor, they are off in the screen-2 base, and a near-duplicate example is how a library leaks into the score.

The generations always all run, in strategy order, including after a refusal. They do not share a draft. A production backend can issue them concurrently; this implementation runs them in order so a scripted backend is deterministic.

## Selection

A candidate votes only when three things are true: the model returned SQL, the guard and DuckDB both accepted it, and the result is not an empty set or a rate outside 0..100.

Voters are grouped by result identity. Column names and row order are ignored. Width is not: `(paid, denied)` is a different result from `(denied, paid)`. Integral `2` and `2.0` are the same value. This is stricter than `bench/compare.py`, which will line columns up by value. A cosmetic split falls through to the selector instead of being declared an agreement.

The decision, in order:

1. Two or three generators refused. The question is refused, even if the other generator produced SQL, and the text joins each refusal reason. One refusal does not veto.
2. Nothing executed. Abstain.
3. Every executed result failed a hard sanity check. Abstain. An agreed empty result is not shipped. A `count(*)` of zero is one row, not an empty result, and it still votes. That matches the existing tool warning, which treats `count(*)` of an empty filter as a real row.
4. The voters form one group. Take it. If hard checks threw other executions out, the reason is `sanity:` rather than `agreement:`. A single sane result beats an empty majority or a majority whose rate is outside 0..100.
5. One group is a strict majority of the executed candidates. Take it, including when the minority is also sane.
6. Otherwise one pairwise call. The two groups with the fewest soft warnings are shown (earlier strategy breaks a tie). `a` is the earlier strategy, not a better query. The third group is recorded as dropped and is not shown. If the selector's reply is not `a`, `b`, or `abstain`, or is not JSON, the outcome is abstain.

Soft warnings do not remove a vote. They are the grain and filter traps already enforced as tool warnings in `bench/agent_cli.py`: `sem.remit_claims` without `is_current_adjudication`, `sem.claims` or `sem.claim_lines` without `is_latest_submission`, an all-NULL column, and a repeated first column on a multi-column result. The selector sees them. Inside a tied group, the submitted SQL is the member with the fewest soft warnings, then the earliest strategy.

Hard checks are result-level (empty set, rate column outside 0..100). The rate window is 0..100, not 0..1, because this bench's gold rates are percentages (`100.0 * x / y`). The same window is what the tool warning already uses. A column is treated as a rate when its name matches `rate`, `pct`, `percent`, or `share`.

The submitted SQL is re-executed by `score_session` on the subagent path. The protocol still keeps the rows it already fetched, because `bench.eval` scores `Answer.rows` directly.

Every candidate's SQL is written to `a04_decision.json` in the session. A later pass can execute those strings and compute the upper bound (the fraction of questions where at least one candidate was right) without this module ever seeing the gold answer. That upper bound is the quantity CHASE's Table 1 says to look at before trusting a selector. This module does not compute it.

## Secure execution

Candidates run through `bench.guard.guard` before DuckDB. That is the agent-facing half of the secure path. The other half is `ccsim.query.open_session`: a fresh DuckDB, secrets and `allowed_directories` limited to that customer's semantic-layer prefix, `lossless.*` only when the question escalates, external access off, configuration locked. `executor_for_query_session` binds a `QuerySession` and does not open AWS itself.

`ToolsExecutor` uses `bench.agents.Tools.sql`, which is the same guard on a snapshot of those views. A SELECT that names no schema-qualified table is rejected, matching the harness rule that a final answer has to read the data. A guard failure or a grounding failure is a candidate that did not vote. It does not abort the other two. There is no repair loop. CHASE's query fixer is a separate factor (stage 2 runs repair first, alone).

## Harness adapter

`bench.eval_subagents.prepare` rejects technique keys it does not already know. A04 does not add one. `configs()` returns one entry, `a04_multi_candidate`, whose technique settings are the screen-2 base: basic glossary, goal briefing, free SQL, effort low, and dictionary, profile, metrics, lookup, examples, ask, error hints, and result checks all off. Every key is one `bench.eval_subagents.prepare` already accepts. A later contrast then measures selection rather than a glossary or effort change.

```text
python -c "import json; from analyst.approaches.a04_multi_candidate import configs; json.dump(configs(), open('a04.json','w'))"
python -m bench.eval_subagents prepare RUN --configs a04.json --models haiku --repeats 1
```

`prepare` builds the sessions and the manifest. Completing them is `complete_manifest(run, backend)`, which writes `calls.jsonl`, `final.json` (`answer`, `refusal`, or `abstain`), and `a04_decision.json`. `python -m bench.eval_subagents score RUN` then re-executes the submitted SQL. An abstain scores as no answer. A refusal scores as a refusal. Entries whose config name is not `a04_multi_candidate` are left alone.

The same protocol is `MultiCandidateAnalyst` for `python -m bench.eval run --agent analyst.approaches.a04_multi_candidate:MultiCandidateAnalyst`. The loader calls the constructor with no arguments. `answer` raises if no backend was injected. The module does not construct an API client.

A backend is `complete(system, user, purpose) -> str`. `purpose` is `generate:direct`, `generate:query_plan`, `generate:divide_and_conquer`, or `select`. Routing `select` to Sonnet while the generators stay on Haiku is the caller's job, which is what the screen-2 note asks for. `ScriptedBackend` is the test double. A purpose with no scripted response raises `KeyError`.

The test is `analyst/approaches/a04_multi_candidate/test_a04.py`. Pytest's default `testpaths` is `tests/`, so run it by path:

```text
pytest analyst/approaches/a04_multi_candidate/test_a04.py -q
```

The test uses an in-memory `sem.claims` and, for the adapter, a one-file parquet snapshot. It does not build a generator dataset and it does not call a model.

## Expected gains

Published numbers, then what they imply here.

CHASE-SQL on BIRD dev with Gemini 1.5 Pro (Pourreza et al. 2024, arXiv:2410.01943): a single prompt 63.01%, self-consistency by execution result 68.84% (+5.8), the full system with a pairwise selector 73.01% (+4.2 over self-consistency). Replacing the pairwise selector with one prompt that ranks every candidate at once drops the score to 65.51% (−7.5 versus the pairwise system). The candidate upper bound is 82.79%. Pools past about 20 candidates did not help. Removing any one generator costs less than removing the selector (divide-and-conquer −1.24, synthetic examples −0.85, query-plan −0.65). The generators partly substitute for each other. The selector does not.

XiYan-SQL votes among five candidates and reports 75.63% BIRD test. Agentar-Scale-SQL's tournament reads the execution result, not only the SQL text. Alpha-SQL gets diversity from search instead of temperature; it is the wrong cost shape on a ten-view schema where one prompt is already in the 80s.

The caution is the 2026 open-model study cited in the literature review: five-sample self-consistency on Qwen2.5-Coder-32B moved accuracy by −0.07 and +0.13 points at about 5× tokens, and p95 latency went from 10.6 s to 50 s. Majority vote among correlated samples is noise. A selector helps only when the candidates actually differ and at least one of them is right.

On this repo, screen 1's best Haiku configs are 86–97% and Sonnet is at the ceiling (12 of 16 configs at or above 97%). Held-out hard questions sit lower (Haiku base 74%, screen-1 winner 85%; Sonnet base 83%, winner 87%), on 27 questions, with intervals around ±15 points. The plausible gain for A04 is a few points on Haiku on questions where grain, population, or time basis can be read more than one way, and only if the upper bound is clearly above the single-prompt arm. On Sonnet, if the upper bound sits within a couple of points of one prompt, the prompts are not diverse and the factor should be dropped. That is the pre-registered drop rule in the literature review. Do not expect CHASE's +10. Those models were weaker, the schema was BIRD's, and the pool was much larger than three.

What a screen should report, per model, against the single-prompt base: accuracy, abstain rate, selector-call rate, and the fraction of questions where at least one of the three candidates was right. Pass^2 needs a second repeat. The decision file is there so the upper bound does not require another generation pass.

## Cost multiplier

| Path | Model calls | What is billed |
|---|---|---|
| Agreement or sanity pick | 3 | Three generations. The system prefix is identical. |
| Disagreement | 4 | The three generations plus one selector call. |
| CHASE's published pool | on the order of 20, plus fixer and selector | Not this design. |

Worst case is 4 calls, not N for a large sample. Output tokens scale about 3× because each generator writes its own SQL and notes. Input tokens scale by the three short user suffixes plus, on disagreement, the two result digests (20 rows each). The shared prefix is one cacheable prompt, not three full copies, if the backend caches it. This code does not cache anything itself.

Latency in this implementation is the sum of the calls, because they run one after another. The calls do not depend on each other, so a backend that issues the three generations together should land near one generation plus the selector when it runs. The literature review's planning number for that shape is about 1–1.3× the latency of one prompt, plus the selector. The 2026 self-consistency latency (p95 10.6 s to 50 s at 5 serial samples) is the number to expect if the three calls are left serial and the model is slow.

There is no repair call, no second selector call, and no tournament. A three-way split spends its one selector call on two of the three results.

## Failure modes

- **Correlated errors.** If all three prompts miss `is_latest_submission` or the primary-payer filter the same way, the majority is confident and wrong. The selector never runs. Screen 1's residual errors were exactly those shared misreadings. Soft warnings annotate that SQL; they do not outvote two candidates that agree. A scripted test locks this in so it is not "fixed" by accident.
- **The third result is invisible.** On a 1–1–1 split, one cluster is dropped before the selector sees it. The dropped strategy is stored on the decision. A full CHASE tournament would compare it too, at another model call. This design follows the one-call budget.
- **Empty-result abstain.** A question whose true answer is no rows abstains when every candidate returns no rows, and a non-empty minority wins when the others are empty. That is ReFoRCE's "empty is not a successful answer." It is wrong for a real empty population.
- **Cosmetic splits.** Different column order, or an extra supporting count, does not cluster, even when `bench/compare.py` would accept both. The selector then chooses, and it can choose the worse one. The other direction is safe: a false agreement is harder than a needless selector call.
- **Rate-name false alarms.** A column whose name looks like a rate and whose value is above 100 does not vote. A legitimate count that was named `denial_rate` would be thrown out. Values of 0 and 100 stay eligible.
- **Selector judgment.** The selector sees rows, which is the Agentar / CHASE finding that a text-only ranker is worse (−7.5 in CHASE when the pairwise model is replaced by one ranking prompt). It can still prefer fluent SQL. An unparseable reply abstains. Position `a` is the earlier strategy; the prompt says not to prefer it for being first. Nothing checks that the model obeys.
- **Refusal veto.** Two refusals discard a third SQL that might have been right. One refusal does not stop two wrong queries from agreeing. PHI questions depend on at least two generators refusing.
- **No repair.** A syntax error or a guard rejection is a wasted candidate. CHASE loses 3.78 points without its query fixer. That fixer is the previous stage-2 factor, not part of A04. Adding it here would confound the contrast.
- **Low diversity on a small schema.** Ten views and a strong model can make the three prompts return the same SQL. The upper bound then equals the single prompt, the selector never helps, and the 3× generation cost buys nothing. Drop the factor in that case.
- **Abstain is unscored as a product property.** Accuracy treats abstain as wrong. A gain that is entirely "refuse the hard ones" will not show up as accuracy, and the bench does not yet report a reliability metric. Report the abstain rate beside accuracy so that trade is visible.
- **Grounding.** A constant `SELECT 2` can pass the guard. It is rejected before it votes, because the harness would score it as not grounded. The query-session executor never runs it. `Tools.sql` may already have logged it as a successful call on the bench-agent path; the decision still does not use the rows.

## Related work

- Pourreza et al. 2024. CHASE-SQL. https://arxiv.org/abs/2410.01943. Multiple generators, execution clustering, pairwise selection. The ablation table is the reason the selector is a separate step from the generators, and the reason one ranking prompt is not used.
- Liu et al. 2024. XiYan-SQL. https://arxiv.org/abs/2411.08599. Five candidates, a selector over groups that share an execution result.
- Wang et al. 2025. Agentar-Scale-SQL. https://arxiv.org/abs/2509.24403. Pairwise tournament on the question, a light schema, and the execution result, after grouping identical results.
- Li et al. 2025. Alpha-SQL. https://arxiv.org/abs/2502.17248. Diversity by search. Not built.
- Deng et al. 2025. ReFoRCE. https://arxiv.org/abs/2502.00675. Empty results are failures. Voting across threads is worth about 2 points on their Spider 2.0 setup; the part used here is the empty-result check.
- Talaei et al. 2024. CHESS. https://arxiv.org/abs/2405.16755. Unit-test the SQL against a restatement. Not built. The pairwise call is the only model judgment on a result.
- EHRSQL 2024 (arXiv:2405.06673). The second-place team abstained unless three models agreed. A04's abstain is the selector declining, not a three-model vote. Two of three refusals do refuse.
- The 2026 corrected BIRD study cited in the literature review (arXiv:2606.29733). Self-consistency at 5× tokens is about zero on a strong open model. Execution repair, which A04 does not do, is the gain that survived in that study.
- Local evidence: `research/screening_results_1.md`, `research/heldout_results_1.md`, and `research/sota_refresh_2.md` (branch `cursor/sota-refresh-2-c33c`). Screen 1's measured levers were the pitfalls glossary and, on Haiku, procedural steps. Held-out did not reproduce the Haiku pitfalls gain. Multi-candidate was not in that screen.

## What this does not change

The generator, the parser, and the lake are untouched. No paid API client is constructed. The module does not add a technique flag to `bench/agent_cli.py` or `bench/eval_subagents.py`.
