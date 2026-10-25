# A07 decomposition

25 Oct 2026. One candidate in the agent-analyzer solution space. It does not change the generator, the parser, the lake, or the bench harness. It adds a self-contained approach under `analyst/approaches/a07_decompose/` and this note.

No accuracy number is claimed. Nothing here called a model API. The scripted tests are the evidence that the protocol does what it says.

## What it is

The model does not write the answer as one SQL statement. It breaks the question into a subsequence of five roles, in this order:

1. **population** — the rows that belong in the answer, already filtered, one row per grain key.
2. **join** — the next view (lines, adjustments, claims). Omit when the question stays on one view.
3. **metric** — row-level measures. No grouping.
4. **aggregation** — the grain the question asks for.
5. **ranking** — `ORDER BY` the measure and the stated tie-break, `LIMIT` to the N asked for.

Each role is one `SELECT` with no `WITH` of its own. The protocol wraps the chain in a `WITH`, runs an aggregate check, and only then asks for the next role. A failed check is repaired once, on that step. The composed query is what gets submitted. The bench still scores it by re-executing it.

Population is required. The other roles are skipped when the question does not need them. The order is not skipped: a join never comes before the population it filters.

This is a workflow in the sense of Anthropic's "Building effective agents" (19 Dec 2024): the code owns the step order, the repair cap, and the gate. The model owns the SQL inside each step. It is not a multi-agent split, and it is not the screen-1 "procedural" briefing. That briefing was five sentences of advice. Here a step that fails its check does not get to pretend it passed.

## Why this shape, given the two results we have

Screen 1 (`research/screening_results_1.md`), generator 3.0, 36 questions, one repeat. Procedural steps were +4.5 points on Haiku, interval [+0.4, +9.0], and +1.7 on Sonnet with an interval through 0. The residual errors the write-up names are the latest-submission trap, fan-out joins over per-submission rows, and claim grain versus line grain.

Held-out 1 (`research/heldout_results_1.md`), 27 harder questions authored without the pitfalls text, generator 3.1, two repeats. Pitfalls plus procedural steps were +1.9 on Haiku, interval [−13.0, +18.5], and 0.0 on Sonnet, interval [−5.6, +5.6]. The pitfalls text itself did not transfer to Haiku (−1.9, interval through 0) and still helped Sonnet (+7.4, interval from 0). The screen-1 winner, which bundled the dictionary and the profile with that text, was the best Haiku cell (+11.1, interval through 0).

Two consequences for this design:

- The glossary is the basic glossary. The pitfalls text is not part of A07. Held-out 1 is the reason. A decomposition experiment that pastes the pitfalls text back in cannot tell which one moved the number.
- A numbered procedure in the prompt is not the same lever as executing each CTE. Screen 1's procedural arm and held-out's null are about the prompt. They are a reason to measure A07 on its own, not a prediction of its effect.

`research/sota_refresh_2.md` (branch `cursor/sota-refresh-2-c33c`) reads the same two results and recommends a goal briefing, with must-do rules in code. A07 puts the must-do rules in the check, and leaves a goal-shaped tool surface in the technique config (`context_style` is `goal`, so a run that forgets to install the A07 prompt does not silently become the screen-1 procedural arm). OpenAI's in-house data agent note (29 Jan 2026) says a fixed procedure pushed the agent down the wrong path. The mitigation here is that roles can be omitted, and that a check failure is data rather than another paragraph of instructions. The risk is real and is listed under failure modes. It is the reason not to ship this on the easy set without a paired comparison.

## The checks

Each check is one aggregate `SELECT` over the CTE chain so far. The sandbox (`bench.guard.guard`, used by `bench.agent_cli`) wraps every statement in `LIMIT 1000` and shows the model 20 rows. A row count inferred from a preview is wrong past those caps. The aggregate is one row, so the count is the real count.

| Check | Pass | Failure code |
|---|---|---|
| Population is non-empty | `count(*) > 0` | `empty` |
| Grain | `count(DISTINCT keys) = count(*)`, and no NULL key | `duplicate_grain`, `null_grain` |
| Preserved measure | `sum(column)` within 0.02 of the previous CTE | `sum_mismatch` |
| Ranking size | `count(*) <= max_rows`, and the step SQL has `LIMIT max_rows` | `too_many_rows` |
| Ranking order | the step SQL leads `ORDER BY` with the measure and the requested direction; the values come back monotonic, ties allowed | `missing_order_by`, `not_monotonic` |
| Optional fan-out cap | `row_count / previous row_count <= max_fanout` when the planner set one | `fanout` |

Population must declare a grain. That is the claim key, the claim-payer key, or the remit id, whichever the question counts. A join that multiplies rows has to name the new grain. If it also carries a dollar amount forward, it lists that amount in `preserve_sums` only when the total must not change. A line-level join that repeats a claim charge fails `sum_mismatch` until the step aggregates or drops the amount from the preserved list.

The sum tolerance is 0.02 absolute, so a doubled charge fails and a binary-float wobble of a cent does not. There is no relative tolerance. A one-cent population error is not this check's job.

Ranking is checked twice. The SQL text must contain the `ORDER BY` and the `LIMIT`, because a comment does not order a result. The values are read back through the same guard the scorer uses. On DuckDB 2.0, an `ORDER BY` inside the CTE was still visible after `guard()` wrapped the statement (probed with a tie-break, including after the insert order changed). That preservation is a property of this engine, not of the SQL standard. The text check does not depend on it. The value check does.

A check that fails is an `execution_error` when the statement does not run: the guard, a missing column, a missing parent measure. The guard is the same one the bench uses (one read-only `SELECT`, `sem.*` unless the executor is escalated, no `read_parquet`, no second statement). The connection itself is the per-request session from `ccsim.query.open_session`: fresh DuckDB, `sem.*` views over the manifest files, `enable_external_access` off, `allowed_directories` limited to that customer's prefixes, configuration locked. The bench snapshot applies the same lock. `GuardedDuckDbExecutor` does not open a session and does not see credentials. The caller passes the connection. The scripted test builds a tiny in-memory `sem` and sets the external-access flag and the lock, then sends every statement through `guard()`.

## What the runner returns

`run` asks the backend for a plan, checks each step, repairs at most once per step, and composes:

```sql
WITH population AS ( ... ),
joined AS ( ... )
SELECT ... FROM <last cte>
```

The last CTE's name has to appear in the final `SELECT`. The final `SELECT` is not allowed to carry its own `WITH`. `guard` is applied to the whole statement before it is returned. A step that still fails after its repair is kept. Later steps still run, and the SQL is still returned. The bench would otherwise score a no-answer, which hides the query. `checks_passed` is false, and the trace says which role failed.

A plan that refuses (PHI, another customer, a write) returns no SQL and runs no statement. A plan with a join before the population, a duplicated role, a grain key that is not a plain identifier, or a repair that changes the role or the CTE name raises `ProtocolError`. Those are backend bugs. They are not repaired.

The planner JSON is specified in `prompts.PLANNER_INSTRUCTIONS` and parsed by `plan_from_json`. The scripted test round-trips a one-step count through that parser. There is no model client in this package. `plan_messages` and `repair_messages` are the requests a later backend would send. The scripted backend replays a stored plan and stored repairs.

## How the bench runs it

`bench.eval_subagents.prepare` accepts a technique config and rejects unknown keys. A07 does not add a key. The decomposition is the prompt stored on the manifest, which is the text the orchestrator sends. `prepare_run` calls `prepare` with one config, `a07_decompose`, then `install_prompts` replaces each of those entries' prompts. `python -m bench.eval_subagents score` is unchanged: the subagent still finishes with `answer --sql`, and the scorer re-executes that SQL.

```python
from analyst.approaches.a07_decompose import prepare_run
prepare_run("a07-1", ["haiku", "sonnet"], repeats=2)
```

The technique settings that go into the session:

| Setting | Value | Why |
|---|---|---|
| glossary | basic | Do not confound the approach with the pitfalls text. |
| data_dictionary | on | Grain and column names come from the semantic model, not from memory. |
| data_profile | off | Not this factor. |
| enable_sql | on | The CTEs are SQL. |
| metrics, lookup, examples, ask | off | One factor. The compiler is a different approach. |
| sql_error_hints | on | A repair needs the error text. |
| result_checks | on | The existing warnings (zero rows, fan-out on the first column) sit next to the CTE checks. They do not gate the next step. The CTE checks do. |
| context_style | goal | The procedure lives in the manifest prompt. |
| effort | low | Screen 1's low-to-high effect was inside noise, and the refresh leaves effort at low. |
| max_tool_calls | 18 | Five checks, plus one preview and one recheck on a failed step, plus a little room. `context` does not count. |

The subagent is told not to run `agent_cli briefing`. That subcommand rebuilds the standard prompt from the technique settings and would throw the procedure away. The orchestrator has to send the manifest prompt. That is a harness gap. Closing it would mean editing `bench/agent_cli.py`, which this change does not do.

`pytest -q tests/` does not collect the scripted test. `pyproject.toml` sets `testpaths` to `tests/`, and this change does not edit that file. Run:

```bash
pytest -q analyst/approaches/a07_decompose/test_scripted.py
```

The test uses a scripted backend and an in-memory `sem` of a few adjudications. It covers a distinct-claim count, a top-2 CARC question whose join double-counts a charge and repeats a line until the repair collapses it, a wrong population that passes every check, an empty population, a refusal that does not query, illegal plans, `read_parquet`, and `lossless.*` with and without escalation. It also checks that `prepare_run` asks `eval_subagents.prepare` for this config and that the manifest prompt is no longer the standard briefing. Twelve tests, no network.

## Cost

Not measured on a model. The structure is:

- **Subagent path (the eval).** One session. `context` once. One aggregate `sql` per role. On a failure, one 20-row preview and one rewritten check. A four-role question with one bad join is about six `sql` calls. A population-only count is one. The goal-style baseline on screen 1 was often one to three. Call this roughly 1.5× to 2.5× the tool calls on a question that uses four roles, and about the same on a one-view count. The cap is 18.
- **Tokens.** The catalog's plan-then-execute factor (T14) is +1 call or +300–800 output tokens for a plan and nothing else. A07 writes a `SELECT` per role and reads a one-row check back. The extra output is the SQL, not a second model. Input grows by the check rows, which are small, and by the dictionary, which is the same dictionary a dictionary arm would pay. No token log exists for this approach.
- **Programmatic runner.** One plan request, plus one repair request per failed step. That client is not implemented, so this change spent nothing.

The expensive literature moves the refresh tells us to skip (high effort, five-sample self-consistency, schema pruning, a multi-agent split) are not in this approach. The cost that is in it is the extra `sql` calls, and they scale with the number of roles rather than with a fixed multiplier on every question. Easy questions should omit join, metric, and ranking.

## Failure modes

1. **A wrong population passes.** The scripted test filters to superseded adjudications. Grain is unique, the sum matches itself, every check passes, and the answer is CARC 97 instead of 16 and 50. The checks do not know the business predicate. Date basis, primary versus secondary, and the data-start population (held-out 1's three universal misses) are the same hole. The dictionary and the basic glossary are what the model has for those. The checks will not catch a confident wrong filter.

2. **A repair that silences the check.** Dropping `charge_amount` makes `sum_mismatch` go away. Dropping `is_denied` can make a grain unique. The repair prompt says not to drop a filter the question asked for. The code cannot tell a legitimate grain change from a dropped predicate. One repair, then the step is kept. This is the T15 failure the catalog names: a repair that executes and is wrong.

3. **The plan is rigid.** A bad population is not replanned when a later step looks odd. Later checks can pass on the poisoned rows. OpenAI's "guide the goal, not the path" is this risk. Omitting roles reduces it. It does not remove it.

4. **A declared check that does not match the grain.** If the planner marks a claim charge as preserved across a line join, a correct fan-out fails `sum_mismatch`. The repair in the scripted test is the intended response: stop preserving a measure whose grain changed, or aggregate first. A planner that then deletes the measure from the answer, rather than aggregating it, is failure mode 2.

5. **An undeclared fan-out is invisible.** Uniqueness is enforced only for the keys the step names. A preserved sum is enforced only for the columns the step lists. `max_fanout` is off unless set. Population is the only role that must name a grain. A join that names no grain and no preserved sum can multiply rows and pass. The prompt tells the model to name the new grain. The gate does not force it, because a legal join's grain is not knowable without the question.

6. **The final `SELECT` is weakly checked.** It must parse, mention the last CTE, and pass the guard. It can drop the `ORDER BY`, project the wrong columns, or reorder a ranking. The ranking CTE is checked. The statement the scorer runs is the whole query. The scripted plan repeats `ORDER BY` on the final `SELECT` for that reason. A subagent can forget.

7. **Order read-back depends on DuckDB.** The monotonic check reads the ranking CTE through `guard()`. It held on DuckDB 2.0 in the probe above. A engine that discards `ORDER BY` inside a subquery would fail correct ranking plans on `not_monotonic` while the text check still passed. The text check is the portable half.

8. **Budget.** Five roles with a failure each is 15 `sql` calls before any extra exploration. The cap is 18. A model that previews every step, or that ignores the "one repair" rule and keeps querying, ends on the budget error and may submit nothing.

9. **`briefing` throws the procedure away.** Described above. A subagent that runs it follows the goal briefing and the basic glossary, with the dictionary and the result warnings, and never builds a CTE chain. That run is not A07. The manifest prompt says not to do this. The harness will not stop it.

10. **Identifier grain keys only.** Keys and preserved columns have to match `[A-Za-z_][A-Za-z0-9_]*`. That blocks the check SQL from becoming an injection. It also rejects a quoted mixed-case column. The semantic layer's names are lowercase. A model that quotes `"Claim_Key"` will fail the check with an execution error, which is the right failure.

11. **Submitting a query the checks rejected.** By design, so the scorer sees a query. The cost is a wrong-but-executed answer that the trace already knew was suspicious. The trace is in the result text. The scorer does not read it. A later arm could abstain when `checks_passed` is false. This one does not. The refresh parks abstention until there is a split that scores it.

## Related work

Numbers are from `research/sota-techniques-catalog-2026.md` and `research/sota_refresh_2.md` unless noted. They are not measurements on this bench.

- **CHASE-SQL** (Pourreza et al., 2024, [arXiv:2410.01943](https://arxiv.org/abs/2410.01943)). As a single generator on BIRD dev with Gemini 1.5 Pro, divide-and-conquer chain-of-thought goes from 57.75% to 63.92% (+6.2) and query-plan chain-of-thought to 63.62% (+5.9). Inside the full system (73.01%), removing the divide-and-conquer generator is −1.24 and removing the query-plan generator is −0.65. The generators substitute for each other. The selector does not. A07 is one generator with an executed gate, not a pool of generators and not a selector. The +6 is the optimistic single-prompt reading. The −1.24 is what the same idea was worth once other generators were present. This repo cannot get diversity from temperature (the API returns 400). A decomposition prompt is one of the three diversity sources the refresh lists for a later candidate pool. A07 can be that third prompt. It is not, by itself, the selector.

- **MAC-SQL** ([arXiv:2312.11242](https://arxiv.org/abs/2312.11242)). Removing the decomposer costs 3.85 points on BIRD dev with GPT-4, mostly on the challenging questions. Removing the refiner costs 4.63. A07's repair is a bounded refiner on the step that failed, not a second agent. The catalog is explicit that those ablations are roles and could share a context. The eval path here is one subagent context.

- **DIN-SQL** (Pourreza and Rafiei, 2023, [arXiv:2304.11015](https://arxiv.org/abs/2304.11015)) is the earlier sub-question decomposition the catalog names. The catalog does not isolate an effect for executing each sub-query. A07's addition relative to that line of work is the aggregate check between steps, not the idea of splitting the question.

- **DS-STAR** ([arXiv:2509.21825](https://arxiv.org/abs/2509.21825)). The router and replan loop is worth about +5.3 points on DABStep hard tasks, and about 98% of hard tasks needed a refinement. That is a replan of the whole attempt. A07 replans one step, once. It will not reproduce that loop. It also will not cost it.

- **ReFoRCE** (Deng et al., 2025, [arXiv:2502.00675](https://arxiv.org/abs/2502.00675)). Execute a simple query before writing the real one, and treat an empty result as a failure. The population check is that idea, applied to a CTE prefix, with the grain and the sum added. The refresh's reading, which this approach follows, is that the empty-result rule transfers and the schema-compression machinery does not. Ten views fit.

- **Programmatic result checks (catalog T16).** Zero rows, a rate outside range, fan-out against a declared grain. Screen 1's factor E (error hints plus those checks) was about zero, and it was aliased with other two-factor interactions. The refresh still wants the checks, as text on the tool result, and a repair only when the SQL does not execute. A07 runs a stricter, per-CTE version and does repair on a check failure, not only on an execution error. That is a deliberate difference. It is also failure mode 2. Screen 1 is not evidence that the stricter version works, and it is not evidence that it does not: the factor was not this one, and Sonnet was at the ceiling.

- **Screen 1 factor G and held-out 1.** Procedural prose. Cited above. Not this approach, and not a substitute for running it.

The prior this note is willing to state: if A07 moves accuracy, it moves it on questions with a join and a grain change (the CARC question in the scripted test is the shape), and it does little or hurts on a one-view count, where it adds a step and can refuse a legal empty answer only when the population was required to be non-empty. The count question in the test is non-empty. A question whose true answer is zero rows would fail the population check, get one repair, and might be rewritten into a wrong non-empty query. Questions that allow a true zero should set `expect_nonempty` false. The prompt's default for population is non-empty, which matches the bench's dislike of trivial zeros and will be wrong on a real zero.

## What this change does not do

- It does not run Haiku or Sonnet, and it does not edit `bench/eval_subagents.py`. The adapter calls the existing `prepare`.
- It does not change the generator, the parser, the lake, the semantic-layer views, or the guard.
- It does not add a pitfalls glossary, a few-shot library, a metric compiler path, or a second model.
- It does not abstain. A failed check still produces SQL.
- It does not implement the model backend. Adding one is a client in front of `plan_messages` and `plan_from_json`. Until that exists, the live path is the subagent following the manifest prompt, and the tested path is the scripted backend.
