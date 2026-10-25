# Typed metric compiler

A question is parsed into a `QuestionSpec`. The spec is checked against the metric catalog, then compiled to one read-only statement over `sem.*`. Free SQL runs only after the compiler returns `cannot_express`. The SQL for ordinary metrics, view choice, default filters, and ratio arithmetic is the existing compiler in `claude/schema/04_agent_interface.py` (`SemanticAPI.compile`). This variant does not copy that compiler. It adds the date basis, the cohort population, the payer-received overlay, the projection the matcher scores, and the gate in front of free SQL.

Code: `analyst/variants/typed_metric_compiler/`. Screen arm: `screen2.yaml`. Prompts and tool schemas: `prompts.tool_spec()`, for a later `bench/eval_subagents.py` screen. Tests use `ScriptedLLM`. No model API is called.

## What the spec is

The model submits `submit_spec`. It does not name a view or write SQL on that turn.

| Field | Role |
|---|---|
| `metrics` | Catalog names from `03_semantic_model.yaml` (36 metrics), or `cohort_claims` for the size of a cohort. |
| `group_by`, `filters`, `order_by`, `limit` | Catalog dimensions only. A comparison on a measure (`paid_amount = 0`) is not a filter. |
| `time_grain`, `time_start`, `time_end` | Grain is day, week, month, quarter, or year. The window is inclusive. |
| `date_basis` | Required. `none`, `payment`, `service`, `submission`, `cohort_submission`, `cohort_service`, `payer_received`, or `file_received`. |
| `min_group_size` | Set only when the question states a floor. Omitted means 0. The catalog default of 20 is not applied. |
| `intent` | `aggregate`, `refuse`, or `cannot_express`. |

`SpecError` (unknown metric, unknown dimension, missing basis) is returned to the model for one repair turn. `CannotExpress` ends the typed lane.

Date bases that the base compiler does not distinguish:

- `payment`, `service`, `submission` slice the fact row by `payment_date`, `service_date` / `service_date_from`, or that 837 row's `submission_date`.
- `cohort_submission` and `cohort_service` build the population first: `claim_key`s whose `min(submission_date)` or `min(service_date_from)` falls in the window. A catalog metric is then restricted to those keys. Later payments on those claims still count. This is not "filter the latest row's submission date."
- `payer_received` is an overlay on `payer_received_date` (DTM\*050). The column is on `sem.remit_claims`. It is not in the yaml catalog, so the arm registers it on the API before compile.
- `file_received` is the raw file arrival date. It is not a column of any public `sem.*` view. The compiler returns `cannot_express` and does not substitute `payment_date`.

`claim_key`, `check_or_eft_number`, and `payer_claim_control_number` cannot be a `group_by`. The catalog itself says `claim_key` is for drill-down, not grouping.

## Two lanes

1. **Typed.** `compile_spec` calls `SemanticAPI.compile`, inlines parameters, and projects only the requested group columns and metrics. Ratio components stay inside the ratio. The session that runs the SQL is the one the harness already opened (`ccsim/query.py` in the dry run, `bench.guard.guard` in tests).
2. **Fallback.** Only when the arm is `interface: typed_spec_then_sql` and `fallback: guarded_sem_sql`, and only after `CannotExpress`. The model may submit one `SELECT`. The same identifier columns may not appear as a bare projected column. Aggregates over them are allowed. `interface: typed_only` with `fallback: abstain` returns the `cannot_express` reason and does not execute SQL.

PHI, writes, and other customers are refused by a lexical check before either lane. The check is code, not a prompt instruction. It matches patient names, dates of birth, member or subscriber ids, SSNs, phone numbers, bank accounts, a write against files or tables, and the other customer names configured for the session. A question that avoids those phrases and still asks for a row-level identifier reaches the spec, where grouping by `claim_key` is rejected, and then the fallback projection check.

## Coverage

Measured by reading each gold query with `sqlglot` and accepting it only when it is a shape `compile_spec` emits: one `sem.*` view, catalog metrics (or the documented `count(*)` grain on current remittance claims), dimension filters and groups, the compiler's default filter, or a `sem.claims` cohort counted from `min(submission_date)` or `min(service_date_from)`. Joins, unions, windows, medians, measure predicates, and subqueries that are not that cohort are fallback. Refusals are `intent=refuse`, not metrics. The classifier does not read `wrong_sql`, failure notes, or the pitfalls text.

| Set | Expressible | Share |
|---|---|---|
| Handwritten analytic (29) | 10 | 34.5% |
| Authored analytic (66) | 1 | 1.5% |
| **Concrete analytic (handwritten + authored, 95)** | **11** | **11.6%** |
| Question families (16 templates) | 8 | 50.0% |
| All analytic, families included (111) | 19 | 17.1% |
| Concrete questions including 4 refusals (99) | 15 | 15.2% |

The headline number is **11.6%**. Families are templates, not additional scored questions, so they are not in that denominator. Counting them raises the share to 17.1% because half of the 16 templates are single-view catalog shapes.

Of the analytic questions the spec cannot express, 62 fail because the gold query joins, unions, or uses a window. The rest are a measure that is not a catalog metric (11), a predicate that is not a dimension (9), or a subquery that is not the supported cohort (7). The authored set is almost entirely the first of those. The typed lane is a governed fragment of the benchmark, not a paraphrase of it.

Expressible handwritten questions: total submitted claims, total billed, total paid, paid by month, payment count, denial rate, denial rate by payer, denied charges by payer, claims with no remit, unbalanced payments. The one expressible authored question is a single catalog metric. Expressible families include payer denial rate, payer paid in a month, CPT denied lines, first-submitted cohort, and never-remitted within a submission cohort.

## Correctness, ambiguity, PHI, versus free SQL

Free SQL over `sem.*` can express every analytic question in the benchmark. It can also return a plausible wrong number: the wrong grain, the latest submission instead of the first, `payment_date` where the question meant the file arrival date, a ratio with its components still in the result, or a silent floor of 20 groups. Screen 1 turned the existing `metrics` tool on in front of an agent that could still write SQL. The main effect was +3.1 points on Haiku (interval includes 0) and −1.0 on Sonnet. That is not evidence that the views are optional. The views already encode grain, current adjudication, and latest submission. The remaining question is whether forcing a spec reduces wrong-but-executed answers on the questions the spec covers. dbt's 2026 ACME rerun is the same comparison on a tiny set (11 questions × 20 repeats, intervals not reported): after the models closed the coverage gap, Sonnet 4.6 scored 90.0% with text-to-SQL on the modeled tables and 98.2% through the semantic layer, and the questions the layer could not express scored 0% because the layer refused instead of inventing a join (Ganz and Perigaud, 7 Apr 2026). Before that extra modeling, both 2026 models scored 64.5% text-to-SQL and 72.7% through the layer on the full 11.

What this compiler guarantees when the spec is the right spec:

- The view, the default filter (`is_current_adjudication` or `is_latest_submission`), and the ratio arithmetic come from the catalog.
- The date basis is a named field. A missing basis is `cannot_express`, not a default of `payment_date`.
- A cohort is the first anchor date, computed with `HAVING min(...)`, then applied as a `claim_key` restriction. Filtering `is_latest_submission` and `submission_date` is a different population, and the compiler does not emit it for `cohort_*`.
- The outer select list is the requested metric, so the matcher does not see numerator and denominator as extra numbers.
- A group smaller than 20 is kept unless the question set `min_group_size`.

What it does not guarantee:

- The model can still pick the wrong catalog metric. `resubmitted_claims` counts every claim sent more than once, including secondary bills. "Frequency code 7" is a different count. On the dry-run world those two numbers were 0 and 1.
- Coverage is 11.6% of concrete analytic questions. A typed-only arm abstains on the other 88.4%. Overall accuracy would then be dominated by abstention, which is the coverage cliff dbt reported before they modeled the missing questions.
- Ambiguous time language ("received") is only safe if the model chooses `payer_received` or `file_received` instead of guessing `payment`. The prompt says to abstain when the question does not name the clock. The compiler cannot detect a confident wrong basis.
- The lexical PHI check is a phrase list. It stops the four handwritten safety questions (names and dates of birth, member ids, another customer, a delete). It does not understand a paraphrase that avoids those words. The structural checks then block identifier grouping and identifier projection. Columns that are not on `sem.*` still cannot be selected, because both lanes are confined to those views and the guard.

## Expected accuracy, cost, latency

On the covered subset, a model that selects the metric the question names should land near the ceiling. The compiler, not the model, writes the join-free SQL, and the dry run below matched gold on every hand-authored spec. The errors that remain are selection errors: wrong metric, wrong basis, or a filter on a measure that should have been `cannot_express`. One repair turn is enough for an unknown name. It does not fix a wrong but valid name.

On the full concrete set, expect overall accuracy close to the free-SQL base, because about 88% of analytic questions take the fallback lane. Held-out 1's base on 27 hard questions was 74.1% Haiku and 83.3% Sonnet. Those questions were written to be hard, so they are a poor estimate of the easy covered subset and a reasonable prior for the fallback. The arm is worth adopting only if, on the covered subset, wrong-but-executed drops relative to SQL-only, and the fallback subset does not get worse. A gain only on the covered subset is still a gain. A loss on the complement means the typed tool is distracting, and the tool should go back off. That is the comparison screen 2's metric arm already asks for, with one difference: this arm forces the spec first instead of leaving `metrics` optional.

Cost is one structured tool call, plus a second call only on `cannot_express`, plus at most one repair call on `SpecError`. There is no sample, no selector, and no temperature. The catalog digest is a stable prefix (metric names, one-line descriptions, dimensions), so it sits in front of the cache breakpoint. Cube's paired benchmark put a 4 KB semantic document at about +2–5k tokens and moved Sonnet from 9.6 s to 11.4 s (Rumiantsau and Fokeev, arXiv:2604.25149); that document raised Sonnet 4.6 from 46.5% to 68.7% on 100 Contoso questions, which is a glossary effect, not a compiler. dbt saw no accuracy change across Anthropic effort levels on semantic-layer questions, and extra effort did move latency (about 8 s at `high` to more than 20 s at `xhigh` on the GPT side of that write-up). This arm should stay at low effort. The catalog text is longer than Cube's 4 KB note because it lists all 36 metrics, so the cached prefix is the cost to watch, not the SQL.

Beskorovainyi (arXiv:2606.29733) found self-correction from execution errors the only component with a consistent gain on open models, +1.0 to +4.4 points at 1.1–1.5× tokens, and no significant gain on the strongest model. This arm repairs catalog errors, not SQL errors. SQL repair belongs to the fallback lane and is left off in the screen definition so the factor stays the spec.

## Dry run

Procedure: author the spec the parser prompt requires, execute `TypedCompilerAgent` through `ccsim.query.open_session` on a generated lake, score with `bench.compare.match` against the question's gold SQL on the same connection. No model API. World: `build_world`, 8 days, `volume_scale=0.08`, seed 2026, `p_corrupt_file=0`, customer `acme_labs`. Five pipeline tasks, none failed, five file days from 2026-07-01. Nine original handwritten questions plus one cohort question that the handwritten set does not contain. The two fallback specs used the question's own gold SQL as the `submit_sql` call, so those two rows show that the gate opens and the guard runs. They are not an estimate of model-written SQL.

| Question | Lane | Result |
|---|---|---|
| q03 total paid | typed, `sem.remit_claims`, basis `none` | Match. 5400.48. Current adjudication applied. |
| q04 paid by month | typed, basis `payment` | Match. One month, 2026-07-01, 5400.48. |
| q07 denial rate | typed | Match within tolerance. Compiled 0.049180 vs gold rounded 0.0492. Outer column is `denial_rate` only. |
| q08 denial rate by payer | typed | Match. 16 payers. Zero-rate payers are ordered differently from gold; the matcher accepts that. No floor of 20. |
| q16 denied charges by payer | typed, filter `is_denied` | Match. Three payers, same order and amounts. |
| q20 never remitted | typed, `sem.claims`, `has_remit = false` | Match. 50. `is_latest_submission` applied. |
| q14 zero paid, not denied | `cannot_express` (measure predicate), then gold SQL | Match. 8. The typed lane correctly refused `paid_amount = 0` as a dimension. |
| q17 frequency-code 7 replacements | `cannot_express`, then gold SQL | Match. 1. Compiling the lookalike metric `resubmitted_claims` on this world returns 0. The prompt tells the model not to use it here. A model that trusts the name would be wrong and the SQL would still run. |
| q30 patient names and dates of birth | lexical refusal, no SQL | Refused. |
| Cohort: claims first submitted in July 2026 | `typed_cohort` | Match. 50. Population is `min(submission_date)` in the window, not the latest row. |

Failure modes worth screening:

1. **Metric-name collision.** A valid spec for the wrong population compiles and executes. `resubmitted_claims` versus frequency code 7 differed on this world (0 vs 1). The repair loop does not catch it, because the name is in the catalog.
2. **Measure predicates.** `paid_amount = 0` cannot be a filter. The hand procedure abstained. A model that picks `denied_claims` or `paid_amount` with `is_denied = false` would answer a different question and the typed lane would accept it.
3. **Date basis is trusted.** The compiler will filter `payment_date` if the spec says `payment`. Ambiguous "received" wording is a model error, not a compiler error. `file_received` is the one basis the compiler refuses even when the model names it.
4. **Coverage cliff.** Six of nine handwritten questions were typed hits because the handwritten set is heavy on single-metric totals. The authored set is 1.5% expressible. A screen that reports only overall accuracy will call this arm a loss if the fallback is weak, and will hide a real gain on the covered slice.
5. **Cohort shape is narrow.** A cohort that also groups by month, or that sums dollars inside the population, is `cannot_express` unless the dollars are a catalog metric restricted by `claim_key`. The family template `claims_first_submitted_in_month` is the shape that is covered.
6. **Environment, not the compiler.** The first lake build failed every task because DuckDB's `httpfs` extension was not installed. An empty lake would have made every count look like a compiler bug. The dry run aborts when any pipeline task fails.

Unit tests, nine, all passing, cover the catalog floor, the payer-received overlay, the file-received refusal, the cohort versus latest-row distinction, identifier grouping and projection, the PHI refusals, one repair turn, and the typed-only abstain. They use an in-memory stub of `sem.*`, not the simulator.

## Screen-2 arm

Contrast with the held-out base: free SQL over `sem.*`, metrics off, lookup off, examples off, basic glossary, goal briefing, effort low. Do not start from the screen-1 winner. One factor: the typed spec and this compiler. Pre-register the same rule as the other screen-2 arms: accept only if the paired question-level bootstrap interval lies entirely above 0, per model. Report four numbers beside overall accuracy: share of questions the typed lane compiles, accuracy on that subset, accuracy on the complement, and wrong-but-executed on each.

`bench/eval_subagents.py` rejects unknown technique keys today. Wire the arm by pointing the subagent at `parser_system()` and `PARSER_TOOLS` for the first turn, and at `FALLBACK_SYSTEM` and `FALLBACK_TOOLS` only after `cannot_express`. The flags below are the arm.

```yaml
arm: typed_metric_compiler
flags:
  interface: typed_spec_then_sql
  date_basis_required: true
  cohort_compiler: true
  payer_received_basis: true
  file_received: cannot_express
  min_group_size_when_unspecified: 0
  project_requested_only: true
  phi_lexical: true
  block_identifier_groupby: true
  block_identifier_projection: true
  repair_catalog_errors: 1
  fallback: guarded_sem_sql
  on_ambiguous_basis: abstain
  default_limit: 500
```

Ablations, each one flag, after the arm itself has a result:

| Id | Change | Question |
|---|---|---|
| `typed_only` | `interface: typed_only`, `fallback: abstain` | What does the lane score when it must abstain on the 88% it cannot express? |
| `no_cohort` | `cohort_compiler: false` | How much of the covered set is the cohort extension rather than `SemanticAPI.compile`? |
| `catalog_min_group` | `min_group_size_when_unspecified: 20` | Does the catalog's default floor drop groups the question did not exclude? |

## Verdict

Extend the compiler that is already in the repo. Do not replace free SQL with it.

The typed lane is the right way to answer a catalog question: the grain, the default filter, the ratio, and the date basis are fixed by code, and a wrong shape becomes `cannot_express` instead of a plausible number. On a real simulated lake the hand-authored specs matched gold, including a first-submission cohort that a latest-row filter would have gotten wrong, and the PHI question never reached SQL.

It covers **11.6%** of concrete analytic benchmark questions (11 of 95). With family templates included, 17.1% (19 of 111). The other questions are joins, windows, measure predicates, and populations the catalog does not name. Forcing the spec and then falling back to guarded SQL is the arm to screen. Typed-only would abstain on most of the benchmark and lose on overall accuracy even if every covered question were perfect. Screen 1's null result on `metrics` does not answer this arm: that factor left SQL available and did not require a date basis. Score the covered subset and the complement separately, and treat a wrong-but-valid metric name as the failure mode the compiler cannot see.
