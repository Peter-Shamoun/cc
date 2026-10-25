# A06 Explore first

Question-conditioned probes over `sem.*`, then one final SELECT. The shape is the column-exploration step of ReFoRCE (Deng et al. 2025), cut down to a ten-view semantic layer. DDL compression, "same result twice" repair, and cross-thread voting are not in this approach.

This file is the design note for `analyst/approaches/a06_explore_first/`. Nothing under `ccsim/`, `bench/`, or `claude/schema/` was changed.

## What it is for

Screen 1's residual misses were a wrong population, a fan-out join, and the wrong grain (`research/screening_results_1.md`). The pitfalls text that named those traps gained +16 points for Haiku on that set and did not survive held-out questions (`research/heldout_results_1.md`: Haiku −1.9, interval about ±14). Held-out's best Haiku arm was the screen-1 bundle, and the dictionary plus profile is the piece that was never measured alone.

A06 does not add glossary text. It measures the columns the question actually uses, compresses that into notes, and only then writes SQL. A later screen can pair it against the base arm and against `data_profile`.

## Protocol

Two model calls. The probes are code.

1. **Plan.** The model sees every `sem` view (grain, column, role, join edge) and returns JSON: up to 4 views, 8 columns each, and up to 2 literal strings copied from the question. A request for patient names, another customer, or a write returns `refuse` and stops. No query runs.
2. **Probe.** Templates build one SELECT per probe. Each statement goes through `bench.guard.guard` and then through a connection the caller already opened.
3. **Compress.** Code turns the aggregates into a short note. The model does not see the raw histogram maps, and it never sees key or identifier values.
4. **Write and run.** The model sees the full catalog again, plus the notes, and returns one SELECT. That SELECT is guarded and executed. There is no second try. An empty result is returned as an empty result.

`protocol.run(question, executor, backend)` is the entry point. `ScriptedBackend` returns a fixed plan and a fixed SELECT so the probe path can be tested without a model API. This change does not call one.

### Secure path

`ccsim.query.open_session` is the production session: per-request DuckDB, `sem.*` always, `lossless.*` only when the session is escalated, `allowed_directories` and `lock_configuration` on. A06 does not open that session and does not read Parquet. The caller does:

```python
from ccsim.query import login, open_session
from analyst.approaches.a06_explore_first import executor_for_query_session, run

session = open_session(aws, deployment, id_token, escalate=False)
result = run(question, executor_for_query_session(session), backend)
```

`executor_for_query_session` only reads `.con`. The bench equivalent is `GuardedExecutor(open_snapshot(path))`. Both still pass every statement through the same guard `bench/agent_cli.py` uses, so a plan cannot smuggle `read_parquet`, a second statement, or another schema. The guard's wrapper caps the result at 1000 rows. Probe results are aggregates, so that cap does not cut them; the templates also LIMIT literal listings to 12.

## Probe templates

Roles live in `catalog.py`, copied from `claude/schema/02_semantic_layer_views.sql`. A test parses that file with sqlglot and fails if a view's output names drift. Roles are not derived from the SQL. A new column fails the test; a wrong role does not.

| Probe | When | Statement |
|---|---|---|
| Summary | One per selected view | `count(*)`, null counts, `count(DISTINCT …)` except on list columns, `min`/`max` on dates and measures, `histogram` on enums and booleans. One SELECT, meant as one scan. |
| Values | Only if a probed code or text column has 1..12 distinct values | `histogram` of those columns only, one SELECT for the view. |
| Join | Up to 3 declared edges whose both views were selected | Each side `GROUP BY` the key, then a `FULL OUTER JOIN` of those aggregates. `joined_rows` is the sum of the per-key products. |
| Literal | Up to 2 strings on an enum, code, or text column | `lower(col) LIKE` a contains-pattern, `%` and `_` escaped, `LIMIT 12`. |

Keys and identifiers are counted and then omitted (`values not listed`). `min`/`max` is not applied to them, so the first patient-control number in sort order never enters the notes. List columns get a null count only.

Join order in the catalog puts fact-table fan-out ahead of payer-dimension edges. `sem.claims.claim_id = sem.claim_lines.claim_id` is probed before `sem.claims.claim_key = sem.claim_lines.claim_key`. The second edge is the one that multiplies a resubmission by every line of every submission. The cap is 3 edges. A plan that also selects remittances, adjustments, and diagnoses can crowd the `claim_key` edge out. The notes say the probes are unfiltered, so a join that is one-to-one after `is_latest_submission` can still be reported as multiplying.

The writer is shown all ten views. Probes are pruned; the schema is not. Maamari, Abubaker, Lakhani, and Mhedhbi ("The Death of Schema Linking?", arXiv:2408.07702) is the reason: on a schema that fits, dropping a column loses more than a shorter prompt gains.

## Query cost against the lake

Counts are statement counts, not measured bytes. This change does not open a lake session.

In-process, per question:

- **2 model calls** (plan, final SQL), regardless of how many probes run. The probe SQL is not generated by the model.
- **At most 4 summary scans**, one per selected view. Enum and boolean histograms ride in that same SELECT.
- **At most 4 follow-up scans**, and only for a view that has a code or text column with at most 12 distinct values. A wider column is not histogrammed. The scripted test with 13 procedure codes runs one statement and does not contain `histogram`.
- **At most 3 join probes.** Each is a key aggregation of the two sides, not a join of the fact rows. Each CTE is referenced once.
- **At most 2 literal scans**, each filtered and capped at 12 values.
- **1 final SELECT**, model-written, still a single guarded statement.

Ceiling: `4×2 + 3 + 2 = 13` probe statements, plus the final statement (`maximum_probe_statements`). The scripted claims-and-lines question runs 7 probes: two summaries, one value histogram, two joins, two literal checks.

What this is cheaper than, and what it is not:

- `bench/agent_cli.py` `_data_profile` scans every `sem` view and every low-cardinality text column, then caches the text on the snapshot. A06 repeats work on every question and only touches planned columns. It is the cheaper read when a question uses one or two views and the profile cache is cold. It is the more expensive read when the profile is already cached.
- It does not put every payer name into the prompt. The static profile lists values for every varchar with at most 12 distinct entries. A small customer can fall under that cap. The literal probe lists only values that contain the question's text.
- It is not free relative to one-shot SQL. The bet is a handful of extra aggregations against wrong literals, wrong date bounds, and unmeasured fan-out.

The subagent adapter is the expensive form of the same idea. `bench/eval_subagents.py` has no probe stage, so the rewritten prompt asks the model to issue the templates itself, one `sql` call each, and to compress the JSON itself. That is one model turn per probe, under the existing cap of 12 investigative calls. Prefer `run()` when a backend exists. Use the adapter when the loop under test is still "one subagent, the manifest prompt, score the submitted SQL."

## Harness adapter

`technique_config()` is equal to `bench.agent_cli.DEFAULT_TECHNIQUE_CONFIG`, and the test locks that equality.

| Flag | Value | Why it stays there |
|---|---|---|
| `glossary` | `basic` | The pitfalls text failed held-out. A06 does not smuggle it back. |
| `data_profile`, `data_dictionary` | off | So a screen can contrast live probes with the static profile. |
| `context_style` | `goal` | The generic five-step procedure is a different factor. Held-out did not support it. The protocol text is the treatment. |
| `effort` | `default` | Effort is not this factor. Screen 1's low-to-high interval covered zero on both models. |
| `enable_sql` | on | Probes and the answer are SQL. |
| `result_checks`, `sql_error_hints`, examples, metrics, lookup, ask | off | Separate factors. Repair is the screen-2 candidate in `research/sota_refresh_2.md` (branch `cursor/sota-refresh-2-c33c`). |

`configs_document()` returns `{"a06_explore_first": <those flags>}`. Passed to `python -m bench.eval_subagents prepare --configs …` alone, the arm matches the held-out base and measures nothing. The protocol has to ride along. `prepare_eval_run(run_label, models, repeats)` calls `eval_subagents.prepare` and then rewrites each manifest prompt, inserting the four probe shapes immediately before `User question:`. Scoring is untouched: the harness still re-executes the submitted SQL.

```python
from analyst.approaches.a06_explore_first import prepare_eval_run
prepare_eval_run("a06-pilot", ["haiku", "sonnet"], repeats=1)
```

That call uses the existing snapshot machinery. It does not build a new lake path.

## Failure modes

- **The planner skips the column that holds the filter.** The writer still sees the column name in the catalog and does not see its values. A wrong spelling then looks like a normal SQL answer.
- **Contains-match.** The literal probe is `LIKE '%text%'`, with wildcards escaped. A short code can match a longer one. The note lists the stored values that matched; the write prompt says to copy one of them, not to invent one.
- **The distinct cap is 12.** The 13th value is a count, not a spelling. This is deliberate: a full CPT histogram on every question is the cost A06 is refusing.
- **Unfiltered cardinality.** `joined_rows > left_rows` is computed before `is_latest_submission` or `is_current_adjudication`. The note can scare a model off a join that the filter would have made one-to-one. The header says the probes ignore the question's filters.
- **The join graph is closed.** Only the edges in `catalog.JOINS` are measured. A relationship that is not listed is invisible. The cap of 3 can drop the `claim_key` fan-out when the plan selects several fact views, because tighter edges are listed first.
- **Enum role is a bet.** Enums and booleans are histogrammed inside the summary scan without a distinct-count gate. A column marked `enum` that is actually wide builds a large map in that scan. Codes and names are gated. The name test does not check roles.
- **A failed probe is a line in the notes.** The final SELECT still runs. The model can repeat the bad reference.
- **Empty is not a failure.** ReFoRCE repairs until the same non-empty result appears twice. A06 runs the final SELECT once. An empty answer is a wrong answer if the question has rows, and this module will not try again. That retry is the repair factor, which costs queries only on failures. Mixing it in here would confound the two.
- **Catalog drift on roles.** Output names are locked to the view SQL. Roles are hand-assigned. A renamed column fails the test. A column that changes meaning does not.
- **The subagent can ignore the prompt.** The harness cannot force the probe stage. The scored object is still the submitted SQL. A model that skips the probes and writes SQL immediately is a valid run of the adapter arm, and it is indistinguishable from the base arm except for the extra prompt tokens.
- **Notes do not explain the data-start population.** Unmatched keys show up as `matched_keys < left_keys`. They do not say that the extract starts mid-stream. That sentence is already in the basic glossary. Putting it in this prompt would be a glossary change, which held-out treats as a trained artifact.
- **Two calls still need a backend.** `ScriptedBackend` is the only backend in the tree. Pointing `complete()` at a paid model is a later wiring, and it is two completions per question, not one per probe.

## Related work

- **ReFoRCE** (Deng et al. 2025, [arXiv:2502.00675](https://arxiv.org/abs/2502.00675)). Workflow, not an open tool loop: compress DDL, restrict the answer format, explore columns with simple queries, self-refine until the same non-empty result appears twice, vote across threads. The ablation cited in `research/sota-techniques-catalog-2026.md` (T27) is that turning column exploration off cut the run to about 1.69 calls and 15k tokens and cost about 2 points on Spider 2.0. The schema there is a thousand columns. `research/sota_refresh_2.md` (PR #1, branch `cursor/sota-refresh-2-c33c`) is explicit that DDL compression does not transfer to ten `sem` views, and that the part worth keeping is "execute, and do not trust an empty result." A06 keeps the exploration and leaves the repair and the vote for the screen-2 factors in that note (repair first, then three candidates). Doing them inside A06 would multiply lake queries on every question and would make the arm impossible to interpret.
- **Schema linking.** Maamari et al. 2024, [arXiv:2408.07702](https://arxiv.org/abs/2408.07702). Filtering a schema that already fits costs accuracy. A06 prunes probes, not the catalog the writer sees.
- **Value retrieval.** CHASE-SQL (Pourreza et al. 2024, [arXiv:2410.01943](https://arxiv.org/abs/2410.01943)) reports about −2.92 points on BIRD dev when value retrieval is removed. The literal probe is that idea, bounded to two strings and twelve stored values, without a retrieval index.
- **Static profiles.** The catalog's T03 cites profiling-only metadata reaching the top of BIRD with GPT-4o (2025). Screen 1's dictionary-plus-profile factor was about zero and was aliased. Held-out could not separate it from the pitfalls text. A06 is the question-scoped alternative, with `data_profile` left off so the contrast stays clean.
- **This repo's own results.** Procedural steps helped Haiku by about 5 points on screen 1 and not on held-out. The adapter therefore leaves `context_style` at `goal` and puts the probe contract in one block, rather than turning on the generic five-step briefing. The pitfalls text is not included.

## How to test

```bash
python3 -m pytest analyst/approaches/a06_explore_first/test_explore_first.py -q
```

The tests use an in-memory DuckDB and `ScriptedBackend`. They check the notes (enum distribution, null rate, date range, both join cardinalities, literal match, secrets absent), the statement cap, the guard (multi-statement and `read_parquet` never reach DuckDB), a refused plan that issues no SQL, a missing view that becomes a note while the final SELECT still runs, catalog names against the view SQL, and the manifest rewrite. They do not open `ccsim.query.open_session` and they do not spend a model call.
