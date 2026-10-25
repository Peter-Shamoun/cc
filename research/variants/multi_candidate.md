# Variant: multiple candidates with execution-based selection

Code: `analyst/variants/multi_candidate/`. Tests: `tests/test_analyst_multi_candidate.py` (21 passing). Dry-run record: `analyst/variants/multi_candidate/dry_run_record.json`.

This is the screen-2 factor in `research/sota_refresh_2.md` (stage 2, item 2), built so the clustering and the selector exist now and a model can be plugged in later. No paid API is called. The generator, parser, lake, question keys, and `bench/eval_subagents.py` are unchanged.

## Design

Generate N SQL candidates from different prompt shapes, execute every one through the secure reader, cluster the result tables the way `bench.compare` scores two answers as the same, then pick a cluster.

Diversity is the prompt. `temperature`, `top_p`, and `top_k` return HTTP 400 on these models (`research/technique_factors.yaml`). The five strategies, in order, are:

| Strategy | What the user turn asks for |
|---|---|
| `direct` | One SELECT. Project the columns the question asks for. |
| `grain_and_date` | State grain, population, and time basis, then the SELECT. |
| `filter_then_aggregate` | One `WITH`: a filter CTE, then the aggregate. |
| `unit_of_analysis` | Name the unit (submission, adjudication, line, payment, adjustment) and query at that unit. |
| `literal_projection` | Select-list is exactly the columns the question names. |

N=3 is the first three. That is the pre-registered arm. N=5 adds the last two so the cost check is a different procedure, not the same three prompts repeated. All five share one system prefix (instructions plus `tools.describe()`, which already includes the basic glossary). The strategy and the question sit in the user turn so a warm cache can hold the prefix. This module does not add a pitfalls list.

Tools, in `prompts.TOOL_SPEC`: `run_sql`, `final_answer`, `refuse`. The submitted SELECT is executed again; the paragraph is not the source of the numbers. SQL that does not read a table is rejected. `SecureQueryTools` opens the lake with `ccsim.query.open_session` and runs each statement through `bench.guard.guard`. The in-process agent holds one connection, so its candidates run one after another. The cost model below also has a parallel column, which is what N separate subagent sessions would do.

The LLM is a protocol (`llm.LLM`). Tests and the dry run use `ScriptedLLM`. The default `UnconfiguredLLM` raises on the first call, so a missing adapter cannot spend money. `prompts.subagent_prompt` is the briefing shape `bench.agent_session.build_prompt` uses, one strategy per session, for a later `eval_subagents` wiring. That harness still rejects unknown technique keys; this PR does not edit it.

### Clustering

`equivalence.results_equivalent` reuses `bench.compare`. Column names are ignored. Row order is ignored unless the question is a ranking. Numeric cells match within an absolute tolerance. A rate may match the same rate written as a percentage when the question says rate, percent, or share. A credit may flip sign when the question says "positive". For a top-N question, two results that differ only by which tied row they kept at the cutoff are the same answer: each is a valid top-N of their union (`bench.compare._match_topn`).

The spec comes from the question string (`cluster_spec_for_question`). No answer key is read. Percent scaling is applied only to columns that are not whole numbers, so a count of 1 does not cluster with 100. Tolerance is not transitive (0 matches 0.004, 0.004 matches 0.008, 0 may not match 0.008). Union-find closes that chain so a vote is not split across a rounding bridge. Execution errors and refusals are not a result cluster.

Inside a cluster the representative is the widest result, then the earliest strategy, then the shortest SQL. Extra text labels and a whole-number supporting count beside a fractional measure are acceptable to the judge in one direction. The narrower projection can omit a count the question asked for and still match the wider one. The vote submits the wider projection. A regression covers the dry-run case: `(5, 65.47)` stays chosen over `(65.47,)`.

### Selection

`selector.select` modes:

| Mode | Rule |
|---|---|
| `majority` | Largest cluster. A tie among cluster sizes abstains. |
| `majority_then_pairwise` | A strict majority (size × 2 > successful row-results) is taken and the selector is not called. Otherwise one selector call. A decline abstains. |
| `pairwise` | The selector is always called and may override a majority. |
| `verifier` | `majority_then_pairwise`, then a verifier must accept. A rejection abstains. |
| `agree_or_abstain` | Take the result only when every successful candidate is in one cluster of size at least 2. |

Strict majority is the screen-2 rule. The selector sees the question, each cluster's SQL, and a result digest (`prompts.selector_user_message`). The parser takes the last `CLUSTER <id>` or `ABSTAIN`. Anything else is a decline. An abstention is submitted as no rows and is incorrect on the current bench, which has no reliability score. The decision still records it.

`refusal_policy=any_veto` (the arm default): one refusal refuses the question. `min_successes` defaults to 1, so a lone successful candidate wins after the others error. Raise it when a single survivor should not be trusted.

## Expected accuracy

These are forecasts from published ablations and this repo's screens. The dry run below is a procedure check on hand-written SQL, not an accuracy measurement.

Screen 1, all factors off (`research/screening_results_1.md`): Haiku 72.2%, Sonnet 88.9%. Held-out 1, 27 hard questions, generator 3.1 (`research/heldout_results_1.md`): Haiku base 74.1% (pass² 70.4%), Sonnet base 83.3% (pass² 81.5%). The SOTA refresh says to run this factor only when the stage-1 winner on that model is under about 90%, and to drop it when the upper bound (at least one candidate right) sits within a couple of points of the single candidate.

Published effects, same shape of system:

- CHASE-SQL (Pourreza et al., 2024, [arXiv:2410.01943](https://arxiv.org/abs/2410.01943)), Gemini 1.5 Pro, BIRD dev: a single prompt 63.01%, execution self-consistency 68.84% (+5.8), pairwise selector 73.01% (+4.2 over self-consistency). Replacing the pairwise selector with one prompt that ranks every candidate falls to 65.51% (−7.5 versus the pairwise system). The candidate upper bound in the paper's pool is 82.79% (`research/sota-techniques-catalog-2026.md`, T17). Generators are partial substitutes; the selector is not. Raising temperature widened the upper bound and lowered self-consistency, which is why this variant refuses temperature. Pools past about 20 candidates did not help. Removing the fixer cost 3.78 points in that paper; repair is a separate screen factor and stays off here.
- XiYan-SQL (Liu et al., 2024, [arXiv:2411.08599](https://arxiv.org/abs/2411.08599)): five candidates, a fine-tuned selector over execution groups, BIRD test execution accuracy 75.63%. The prior report records about three points lost without that selector (`research/conversational-analytics-agents-sota-2026-full-report.md`).
- Agentar-Scale-SQL (Wang et al., 2025, [arXiv:2509.24403](https://arxiv.org/abs/2509.24403)): a pairwise tournament on the question, a light schema, and the execution result, after grouping candidates that return the same rows. 81.67% BIRD test.
- Beskorovainyi et al., 2026 ([arXiv:2606.29733](https://arxiv.org/abs/2606.29733)): five-sample self-consistency on Qwen2.5-Coder-32B changed accuracy by −0.07 and +0.13 points at about 5× tokens, with p95 latency 10.6 s to 50 s when the samples ran in series. That is the prior for a strong generator and temperature-style repeats. It is the reason N=5 is a cost arm, not an accuracy claim, until N=3's upper bound actually moves.

Reading those onto this lake: Haiku on a held-out-like set (base about 74%) is the place a few points are available, compressed relative to CHASE because the base is already 74% rather than 63%, and because a shared misreading becomes a majority (see q11). A practical forecast is 0 to +4 points of accuracy, with abstentions counted wrong, so a selector that often declines spends the gain. Sonnet on the easy screen is already at 89–92%; the forecast there is about 0. On held-out (83%) the forecast is 0 to +2 points, and only if the three prompts produce different tables. The number that decides the factor is the upper bound, not the vote. The refresh's drop rule (upper bound within about 2 points of the single prompt) is the right stop.

## Cost and latency

Lake numbers are the published 120-day winner, not a re-measurement (`docs/data-backend/lake-optimization.md`, section 3, agent-workload table). One production request (STS, CURRENT, manifest, view bind, query) at 120 business days: median 50 requests, median 3.34 MB fetched, S3-model median 0.17 s, max 0.51 s. Section 4's adopted gold layout is a different measurement: 35 / 87 range requests per query against the Parquet files alone. The 50-request figure is the one that includes session open, and it is the one used here.

The doc's formula is `requests × 25 ms / parallelism 8 + bytes / 100 MB/s`. Applied to the two medians it gives 0.190 s. That is not the reported 0.17 s, because the median of the per-query models is not the model of the medians. Latency below uses 0.17 s and 0.51 s.

N queries, one per candidate, no exploratory call:

| N | Parallel sessions | Serial (one connection) | Requests | MB |
|---|---|---|---|---|
| 3 | 0.17 s median, 0.51 s max | 0.51 s median, 1.53 s max | 150 | 10.02 |
| 5 | 0.17 s median, 0.51 s max | 0.85 s median, 2.55 s max | 250 | 16.70 |

Parallel wall-clock stays at one query. Request count and bytes still multiply, and that assumes S3 accepts the concurrent sessions. The in-process runner is the serial column. An exploratory `run_sql` before `final_answer` (the arm allows up to 4 turns) multiplies the request count again; the table is one query per candidate.

Token prices (`research/technique_factors.yaml` T26, catalog T19 and T25): Sonnet 5.5 cache reads are $0.20 / MTok, which is 0.1× input, so input is $2.00 / MTok. A 5-minute cache write is 1.25× input. Sonnet output is $10 / MTok. Haiku 5.5 input is about 20× cheaper ($0.10 / MTok). Haiku output is not priced in the repo; `cost.py` applies the same 20× ratio ($0.50 / MTok) and sets `output_price_assumed`.

The dry run measured the shared prefix at about 2,125 tokens (7,930 characters of `describe()`). The sketch then uses 400 uncached input tokens and 500 output tokens per candidate, and a selector of 1,500 input plus 200 output. `warm_cache` is a prefetch or a staggered start: the first request writes the prefix and the other N−1 read it. Firing all N at once on a cold cache misses, and every candidate pays full input on the prefix. Parallelism and cache sharing are different.

Dollars at that measured prefix, warm cache, one query per candidate:

| Arm | Agree (no selector) | One Sonnet selector on disagreement | Multiple of one candidate |
|---|---|---|---|
| Haiku, one candidate | $0.000556 | — | 1× |
| Haiku N=3 | $0.001178 | $0.006178 | 2.12× / 11.1× |
| Haiku N=5 | $0.001801 | $0.006801 | 3.24× / 12.2× |
| Sonnet, one candidate | $0.011113 | — | 1× |
| Sonnet N=3 | $0.023563 | $0.028563 | 2.12× / 2.57× |
| Sonnet N=5 | $0.036013 | $0.041013 | 3.24× / 3.69× |

The selector line is $0.005 and does not touch the lake (it reads digests the candidates already produced). On Haiku that one Sonnet call is the whole bill. On Sonnet it is a small add on top of N generations. The agree path, which is what a strict majority does, stays near 2× at N=3 because the prefix is written once.

## Dry run

Ten original questions from `bench/questions.yaml` (q01, q02, q04, q07, q08, q10, q19, q23, q11, q30). There is no train split in the tree; held-out questions were left untouched. World: start 2026-07-13, 16 days, `volume_scale` 1.0, seed 5, burn-in 21, `p_corrupt_file` 0, customer `acme_labs`, reader `ana@xifin.example`. Pipeline tasks 12, failures 0. The agent ran through `open_session` and `guard`. Gold SQL was used only afterwards, inside `bench.compare.match`.

The candidate SQL was written by hand, one turn list per strategy, then executed. The author had already read `questions.yaml` while learning the harness, so 9/10 is a procedure result. The scripts were written to follow each prompt and to make the mistakes that prompt invites (a missing column, a percent versus a fraction, a line-level denial versus a claim-level denial). They are not a sample of Haiku or Sonnet.

Result: 9 of 10 chosen answers match the key, the upper bound is 10 of 10, and the selector was called 0 times. Cluster sizes were `[2]` or `[2, 1]` on every answered question. `HandSelector` (keep a cluster whose SQL filters `is_current_adjudication` when the question says "current"; keep a two-column result when the question asks both how many and how much; otherwise abstain) therefore never ran against the lake. A 1–1–1 split, a decline, and a selector override of a majority are covered by unit tests only.

What the procedure actually did:

- q01. `filter_then_aggregate` referenced `patient_control_number`, which is not on `sem.claims`. The error left the vote. The other two agreed on 1,447 distinct claims. A lone survivor would also have won under `min_successes=1`.
- q07. Direct returned 0.0392 and grain returned 3.92. The question asks for a rate, so percent scaling put them in one cluster. The filter that treated `paid_amount = 0` as a denial returned 0.1081 and stayed a separate cluster. Majority took the rate.
- q10. Direct ordered by count then CARC and returned `183` in fifth place (count 3). Grain ordered by count only and returned `55` (count 3). Both are valid top-5 under the tie rule, both match the key, and they formed one cluster. The filter counted adjustment rows (97 → 40) and was rejected as not a valid top-N.
- q23. Direct returned `(5 claims, $65.47)` on current secondary adjudications. The filter summed every secondary event, with no `is_current_adjudication` filter, and returned `($65.47,)` — reversals netted to the same dollars and dropped the count. The judge's supporting-count rule clusters those two tables. Gold requires both columns, so the one-column SQL fails the key. The widest-representative rule submits the two-column SQL, and the vote is correct. A narrowest-representative rule would have submitted the dollars-only SQL and been wrong, with the selector still uncalled, because the cluster was a strict majority.
- q11. Direct and filter both queried `sem.remit_lines` and returned 64 procedure rows. Grain joined latest claim lines to current remittances and returned the 6 rows the key wants. The correlated pair is a strict majority, the chosen answer is wrong ("expected 6 rows, got 64"), and the upper bound is true. `majority_then_pairwise` does not call the selector on a 2–1. This is the failure mode that matters: a wrong majority is invisible to the screen-2 selector. `mc_n3_pairwise_always` can override a majority; it is implemented and unit-tested, and it is not the pre-registered arm.
- q30. All three strategies refused a request for patient names. `any_veto` refuses. The key expects a refusal.

## Screen-2 arm

Run this against the stage-1 winner, both models, two repeats. Paired baseline is `mc_n1_direct` on the same prefix. Report accuracy (abstain counts wrong), abstain rate, pass², and the upper bound. Drop the factor when the upper bound is within about 2 points of `mc_n1_direct`. Accept only when the paired question-level bootstrap interval lies entirely above 0, per model, which is the refresh's decision rule.

The harness does not know these keys yet. In-process, once an LLM adapter is injected:

```bash
CC_MC_ARM=mc_n3_majority_then_pairwise \
  python -m bench.eval run \
  --agent analyst.variants.multi_candidate.agent:MultiCandidateAgent
```

`CC_MC_CONFIG` is a JSON object of the same flags and overrides the arm. With no adapter the agent fails on the first question.

`mc_n3_majority_then_pairwise`:

```yaml
n_candidates: 3
strategies: [direct, grain_and_date, filter_then_aggregate]
selection: majority_then_pairwise
selector_model: claude-sonnet-5-5
generator_model: arm_model
temperature: null
repair_on_error: 0
max_turns_per_candidate: 4
refusal_policy: any_veto
min_successes: 1
effort: low
glossary: basic
context_style: goal
cache: warm_prefix_then_parallel
exploratory_sql: true
cluster:
  tol: from_question_text
  allow_percent: if_question_mentions_rate
  allow_sign_flip: if_question_says_positive
  topn: if_question_asks_for_a_top_n
  metric_col: auto
```

`mc_n1_direct` is the same flags with `n_candidates: 1`, `strategies: [direct]`, `selection: majority`. `mc_n5_majority_then_pairwise` adds `unit_of_analysis` and `literal_projection`. Run it as a cost check after N=3, and only if N=3's upper bound clears the single prompt by more than about 2 points. `mc_n3_pairwise_always`, `mc_n3_verifier`, and `mc_n3_agree_or_abstain` exist for a follow-up if the 2–1 miss in the dry run shows up as a high upper bound and a low vote. They are not the first arm.

Subagent path, when the harness grows a technique key: N sessions per question from `prompts.subagent_prompt`, re-execute each submitted SQL, then `selector.select`. Do not turn temperature on.

## Verdict

**Haiku.** Worth a screen-2 arm on the harder set. Held-out base is 74%, under the 90% stop, and the literature that found a real gain (CHASE, XiYan) found it on generators in that range, from prompt diversity plus an execution selector. Keep N=3 and call the Sonnet selector only when there is no strict majority: the agree path is about 2.1× one Haiku call and 0.17 s of parallel lake time (0.51 s on the single connection), while a selector on every question is about 11× because the $0.005 Sonnet call dominates Haiku. Keep the arm only if the measured upper bound beats `mc_n1_direct` by more than about 2 points and the voted accuracy, with abstentions counted wrong, clears the bootstrap rule. N=5 waits on that gap. A wrong majority, as on q11, will not be repaired by this arm; if that pattern dominates the upper-bound gap, the follow-up is `mc_n3_pairwise_always`, not a larger N.

**Sonnet.** Leave the single prompt as the default. Screen 1 is at the ceiling (88.9%, and 91.7% on the best screen-1 cell), and Beskorovainyi's five-sample result on a strong open model is about 0 points at 5× tokens. Held-out at 83% is the only set where N=3 is worth measuring, under the same upper-bound rule, at about 2.1× dollars when the candidates agree and 2.6× when the selector also runs. Parallel lake latency stays 0.17 s median (0.51 s max) at N=3 and at N=5; serial is 0.51 s and 0.85 s. That latency is cheap next to the model. The dollars are not, until the upper bound says the three prompts are actually different.
