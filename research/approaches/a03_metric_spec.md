# A03: metric-spec first

The model writes a structured spec. A deterministic compiler turns that spec into one `SELECT` over `sem.*`. Free SQL runs only after the compiler reports that the spec cannot express the question.

This note is the design record for `analyst/approaches/a03_metric_spec/`. The generator, the parser, the lake, and `bench/` are unchanged. No paid API call was made. The numbers below come from compiling the benchmark specs locally and from the list-price formula in `adapter.py`.

## Design

A question becomes a JSON object with a closed vocabulary:

- **population**: a catalog entry (`current_adjudications`, `latest_submissions`, `primary_remit_lag`, `secondary_current_for_primary`, and the rest in `catalog.py`). The compiler owns the population predicate (`is_current_adjudication`, `is_latest_submission`, primary-payer joins, reversal-without-current, first-submission lag).
- **metrics**: `count`, `count_distinct`, `sum`, `avg`, `min`, `max`, `ratio`. A ratio is a fraction unless `scale` is `percent`. `sign: flip` is for amounts stored with the opposite sign (PLB interest L6). A scalar `sum` with no rows is 0. A grouped `sum` stays null unless `coalesce_zero` is true.
- **filters** and an optional **time window** on a date or month column.
- **grain**, including `date_trunc` buckets `day`, `week`, `month`, `quarter`, `year`.
- **link**, at most one: `adjudication_adjustments`, `primary_billed_attributes`, or `billed_procedures`. The model does not write a join.
- **order, limit, ties, rounding**. `ties: strict` applies `LIMIT N` after the full `order_by`, so the dimension belongs last as a tie-break. `ties: include_boundary` ranks only on the ordered metrics (`rank() OVER (...) <= N`) and keeps every row tied with the Nth, which can return more than N. Rounding happens once, in the select list. `HAVING` uses the value before rounding.
- **detail** shape: a projection of columns, with no metrics and no link.

`compile_spec` emits SQL that `bench.guard.guard` accepts (one read-only `SELECT`, `sem.*` only). A `GuardError` is a compiler bug. Unknown aggregations, unknown fields, and unregistered joins raise `CannotExpress`. Malformed JSON raises `SpecError`.

The SQL gate is in this module's CLI, not in the stock harness. `answer --sql` is rejected until a prior spec call logged `cannot_express`. A `SpecError` does not open the gate. An explicit `{"status":"cannot_express","because":"..."}` does. Spec calls are logged as command `metrics` so the stock scorer counts them. The submitted answer is `final.json` with `via` `a03_metric_spec` or `sql_fallback`.

`technique_config()` returns a legal `bench.eval_subagents` config: basic glossary, goal-style context, low effort, `enable_sql` true (the harness requires the key), `enable_metrics` false (so the stock named-metric tool is not advertised), `max_tool_calls` 8. `prepare_run` calls `bench.eval_subagents.prepare` and then rewrites each manifest prompt to the A03 briefing. The orchestrator has to use that prompt. `bench.agent_cli` still prints the stock SQL briefing and bypasses the gate.

Domain rules the compiler encodes, taken from the views and from screen 1:

- Primary payer is `payer_responsibility = 'P'` on `sem.claims`. `payer_sequence` on a remit is not that fact, and a status-4 denial is not tagged primary.
- `$0` paid is not a denial. `is_denied` is the recorded CLP02=4 status or signal v3.
- A billed-CPT denial rate joins latest `sem.claim_lines` procedures to current adjudications. A claim counts once per CPT.
- Summing a claim-level measure through `adjudication_adjustments` is rejected. `count(*)` counts adjustment rows. `count_distinct remit_claim_id` counts claims.
- Frequency code 7 is a replacement and 8 is a void, read from submissions. On remit events the same column is the 835 CLP frequency, so the compiler keeps the population's column.
- `days_submission_to_first_remit` on `sem.claims` restarts per submission. First-submission-to-first-835 is the `primary_remit_lag` population.
- `sem.claims.is_denied` is primary-or-unknown. A per-payer denial rate uses `current_adjudications`.

Patient names, member ids, birth dates, other customers, and writes are refusals. The compiler does not compile them and the SQL fallback stays closed.

## Coverage

The spec language has seven structural labels. It can express four: `single_view_aggregate`, `registered_link`, `derived_population`, `detail_listing`. Refusal is a tool, not a compiled spec. `escalation` (a lossless read) and `outside_spec` (windows, medians, extra joins, arithmetic detail) are the SQL fallback.

| Set | Items | Expressible | Share |
| --- | ---: | ---: | --- |
| Hand-written `bench/questions.yaml` | 33 | 28 | 28/29 of the non-refusal questions (4 refusals, 1 escalation) |
| Family templates in `bench/question_families.py` | 16 | 16 | 16/16 |
| Authored `bench/authored_questions.json`, strict gold-SQL classifier | 66 | 8 | 8/66 |
| of which screen-authored | 39 | 7 | 7/39 |
| of which `heldout_hard_1` | 27 | 1 | 1/27 |
| Screen-shaped (hand-written answerable + families) | 45 | 44 | 44/45 ≈ 97.8% |
| All three sets, non-refusal | 111 | 52 | 52/111 ≈ 46.8% |

Hand-written expressible specs are 19 single-view aggregates, 6 registered links, and 3 derived populations. Every one of those 28 specs, and all 16 family specs, compiles and passes the guard with no `lossless` reference. The eight authored ids the classifier accepts are `a_54dd76175a`, `a_436f690741`, `a_f494340e13`, `a_dccab5ccfe`, `a_fca405b635`, `a_26d4c3f4ca`, `a_e83408d6a2`, and `a_8edecbf01f`.

The authored count is a floor. The classifier refuses to mark a gold query expressible when it sees a window, `exists`, `median`, `abs(`, more than one join, a CTE, or a detail listing that casts or does arithmetic. It can still over-count a two-table join whose `ON` clause is richer than the matching link. Grouped `coalesce(sum(...), 0)` in gold is still labeled expressible; an all-null group differs unless the spec sets `coalesce_zero`. A `strftime` month label is treated as expressible because the matcher accepts `YYYY-MM` against a date.

Held-out coverage of about 3.7% is the coverage cliff. Screen-shaped questions sit inside the catalog. Independently authored hard questions mostly do not, and those questions are supposed to fall through to SQL rather than to a silent empty result.

## Failure modes

- **Wrong population.** The compiler will not second-guess a legal spec. `latest_submissions` includes the secondary payer's row. A question about the primary bill has to name `submissions` or a primary population and filter `payer_responsibility = 'P'`.
- **Coverage cliff and the SQL fallback.** Once `cannot_express` opens the gate, the model is back in free SQL, which is where the grain bugs from screen 1 live (latest-submission trap, fan-out joins, claims-versus-lines). The fallback is the escape for questions the spec cannot say, and it reintroduces those bugs on the questions that use it.
- **Eager `cannot_express`.** The explicit status, and any compiler `CannotExpress`, opens free SQL. A model can skip the spec by declaring the question inexpressible.
- **Dropped `HAVING` or the wrong tie policy.** A top-N that uses `include_boundary` can return more than N rows. The bench top-N matcher wants exactly N rows, every strictly better row, and any of the tied cutoff rows, so the default for a ranked benchmark question is `ties: strict` plus a deterministic tie-break. Ranking uses the pre-limit order of metrics. Grain columns in `order_by` only order the display.
- **Rounding and null sums.** Rounding digits are part of the spec. A grouped sum of no rows is null. A scalar sum is 0. Those differ from gold that coalesces every group.
- **Fan-out the catalog missed.** Claim-level measures summed through the adjustment link are rejected only if the column is listed in `CLAIM_LEVEL_MEASURES`. A missed column would silently multiply. `primary_billed_attributes` projects only the requested 837 attributes, and a claim+payer with more than one distinct requested value is repeated.
- **`frequency_code` collision.** The 837 frequency and the 835 CLP frequency share a name. The compiler binds the population's column. A spec that filters remit events on frequency 7 is filtering the 835, not replacements.
- **`payment_month`.** It is the first day of the month. A filter written as a human month label can miss the window by a month.
- **Payer spelling.** There is no lookup tool in this config. A filter that does not match the stored payer name returns zero rows. The CLI adds a note when a compiled query returns nothing.
- **Systematic compiler bugs.** A wrong population predicate is wrong for every question that uses it. The fixture test compares compiled SQL with hand-written gold for the screen questions that the spec claims. That does not cover the authored set.
- **Enforcement.** The gate holds when the subagent uses this module's CLI and the rewritten manifest prompt. A subagent that calls `bench.agent_cli` writes free SQL immediately.

## Estimated cost per question

List prices for Claude 5.5, prompts at or under 100k tokens, from `research/technique_factors.yaml`: Haiku $0.10 / $0.50 per million input / output tokens, Sonnet $2 / $10. A cache read is 0.1× the input price.

The stable briefing (instructions, catalog, basic glossary) is about **4669 tokens**, measured as `len(briefing before "User question:") // 4` on this tree. The formula then adds a typical fresh input of 700 tokens and 500 output tokens. A SQL-fallback turn adds 400 input and 350 output. Low-effort thinking tokens are extra. This is a floor, not an invoice.

| Model | Cache | SQL fallback | USD / question |
| --- | --- | --- | ---: |
| Haiku | warm | no | 0.000367 |
| Haiku | warm | yes | 0.000582 |
| Haiku | cold | no | 0.000787 |
| Sonnet | warm | no | 0.007334 |
| Sonnet | warm | yes | 0.011634 |
| Sonnet | cold | no | 0.015738 |

`estimate_question_cost(model, prefix_tokens(), cache_warm=..., sql_fallback=...)` reproduces the table.

## Related work

The repo already has a typed compiler: `claude/schema/04_agent_interface.py` over the named metrics in `claude/schema/03_semantic_model.yaml`. The model picks a metric and dimensions. That compiler picks the coarsest view, owns joins, default filters, ratios, and the minimum group size. It is wired as `metrics` / `answer --metrics` when `enable_metrics` is true. Screen 1 factor A turned that tool on **beside** free SQL. The effect was about zero: Haiku +3.1 with interval [−1.7, +8.7], Sonnet −1.0 with interval [−3.1, +1.0] (`research/screening_results_1.md`). A03 is spec-first: the model does not get a free `SELECT` until the spec language has refused. One closed metric in that YAML, `resubmitted_claims`, is `submission_count > 1`, which counts secondary bills. The question this benchmark asks (frequency code 7) is an explicit filter on submissions here.

Held-out 1 (`research/heldout_results_1.md`) found that the pitfalls text did not carry to independently authored hard questions for Haiku, and the screen-1 Haiku winner's held-out interval included 0. A03 puts the grain, primary-payer, and fan-out rules in the compiler. The briefing stays the basic glossary. The pitfalls paragraph is not in the prompt.

`research/sota_refresh_2.md` (branch `cursor/sota-refresh-2-c33c`, PR #1) is the literature pass this design follows:

- dbt's semantic layer versus text-to-SQL (Ganz and Perigaud, 7 Apr 2026): a compiler that cannot express the question should fail as an error. A coverage cliff scores as a refusal, which is the honest result. A silent empty or wrong result is the failure mode to avoid. A03's `CannotExpress` is that error, and it is also the gate that opens SQL, so the cliff is visible in `via`.
- Bounded semantic planning (arXiv 2608.16663): a planner that cannot name columns reached 97.4% against 55.3% for unconstrained SQL, with 0 wrong-but-executed against 29. A03 is wider. The model names populations, fields, and filters, and a wrong legal population still executes.
- A measures document (Rumiantsau and Fokeev, arXiv 2604.25149) added about 17 to 23 points. A03 puts those rules in the compiler rather than in a prompt, because the pitfalls text overfit on screen 1.
- MetricFlow, LookML, and Cortex Analyst are the same shape: a metric, a population, a grain, and a compiler that owns the join. The closed link list is the small version of that join graph that this semantic layer needs.
- The same note's reading of the OpenAI data-agent post and the Anthropic agent post: state the goal, and enforce the rules in code. The A03 prompt is a goal plus the catalog. The gate, the fan-out check, and the population predicates are code.

## How to run

From the repo root, with the existing bench dependencies:

```bash
python3 -m pytest analyst/approaches/a03_metric_spec/test_a03_metric_spec.py -q
```

Twelve tests pass: catalog columns against the view SQL, the coverage counts above, a legal technique config, the prompt and the cost ordering, the SQL gate, fan-out and injection, fixture parity with gold SQL, and the tie rule.

A scored run, when a snapshot already exists, is:

```python
from analyst.approaches.a03_metric_spec import configs
from analyst.approaches.a03_metric_spec.adapter import prepare_run
prepare_run("a03", ["haiku"], repeats=1, snapshot_path="path/to/snapshot.duckdb")
```

Then `python -m bench.eval_subagents score` on that run. `prepare_run` builds the world if no snapshot path is passed, which these tests do not do.

The subagent's commands are on this module, for example `python3 -m analyst.approaches.a03_metric_spec.adapter --session <dir> spec '<json>'` and `answer --spec`. `answer --sql` waits for `cannot_express`.
