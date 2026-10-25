# A05 — plan, execute, then a fresh-context critic

25 Oct 2026. A candidate arm for the agent analyzer. Code: `analyst/approaches/a05_verifier/`.
It does not change the generator, the parser, the lake, or `bench/`.

This is the factor the catalog calls plan-then-execute (T14) plus an isolated verifier (T16, T18).
Screen 1 did not test it. `research/sota_refresh_2.md` (PR #1) leaves an LLM verifier and multi-agent
splits out of screen 2, because the measured gains on a single loop are not yet exhausted and a second
model context is a cost multiplier. A05 is the arm that measures that factor on its own, against the
held-out base, when a later screen has room for it.

## What it does

One question, two model contexts, a code path around both.

1. The analyst returns a plan (grain, population, time basis, measure, units), or a refusal. A SQL
   string in that reply is discarded and is not executed. Extra fields (`rationale`, scratchpads,
   chain of thought) are dropped. Execution does not start until the plan parses.
2. A second analyst turn, which sees only the committed plan fields, returns one DuckDB `SELECT`.
3. The SELECT runs through `bench.guard` and then the session. On a bench run that session is
   `bench.agent_cli` over the snapshot sandbox (the harness copy of `ccsim/query.py`: `sem.*` unless
   the question escalates, external access off, configuration locked). On a live `QuerySession`,
   `execute_on_query_session` uses the same guard on `session.con` and does not open another connection.
4. If the statement fails the guard or DuckDB, the analyst gets one repair, which returns a new plan
   and a new SELECT. The critic is not called on a statement that returned no rows because it did not run.
5. The critic is a new completion. Its system prompt is the checklist plus the same schema and glossary.
   Its user message is only the question, the five plan fields, the SQL that produced the result, and a
   digest (columns, row count, up to 20 rows). The analyst's raw completion is not in that message.
6. The critic returns a verdict and four checks: semantics, population, units, invariants. A check that
   is missing or not `true`, or a verdict that will not parse, is an objection. An `accept` with a
   failed check is overridden. Any `sql` the critic writes is discarded.
7. Accept: submit that SQL. Object: the analyst revises the plan and the SQL once, the new SQL is
   executed, and it is submitted if it runs. The critic is not called again. A revision that fails to
   execute submits nothing. The original SQL is not submitted after an objection.

Refusal (PHI, another customer, a write) happens in step 1. The critic does not see those questions.

The checklist lives in `checklist.py`. The rendered text is hashed (`checklist_sha256`) into the run
manifest. The bullets are properties of the views and the basic glossary: submission grain versus
`claim_key`, `payer_responsibility` versus `payer_sequence`, `is_current_adjudication`, the denial
definition, line fan-out, date basis, `100.0 * n / d` for percentages, the data-start population.
They are not the screen-1 pitfalls essay (`Invariants and common traps`), and they do not name a
question id.

## Why the critic is a second context

A self-check in the same transcript agrees with the reasoning that produced the SQL. Screen 1's
programmatic result checks, which are a weaker form of "look at the result again," were about zero
on that set (−0.3 Haiku, +1.7 Sonnet, both intervals covering 0) and were aliased with other factors.
The catalog's reading of DS-STAR is that the verifier/router loop is worth +5.3 points on hard DABStep
tasks, with no verifier-only ablation. Putting the critic in a fresh context is the part that can
break that agreement: it sees the question and the artifact, not the argument for the artifact.

The held-out result bounds what a rubric is allowed to be. The screen-1 pitfalls text was +15.6 points
for Haiku on the set it was written beside, and −1.9 (interval about ±14) on independently authored
hard questions. A05 therefore keeps the held-out base underneath the protocol: basic glossary, goal
briefing, free SQL, low effort, examples off, metrics off, result checks off. The checklist is a
frozen property of the views. A later edit is a trained artifact under the split rule in
`research/heldout_results_1.md`: write it from TRAIN failures, hash it, then open VAL. This file does
not claim the current bullets have passed that gate.

## Adapter

`bench/eval_subagents.py` scores whatever `final.json` the session submitted. It builds the prompt
from technique settings only, and `create_session` rejects unknown keys, so the protocol cannot be a
new setting.

`adapter.prepare(run, models)` calls `eval_subagents.prepare` with `config.json` / `configs()`, then
rewrites each `a05_verifier` manifest entry. The prompt's first line is `A05_VERIFIER_RUNNER` plus
`adapter.run_session`. The orchestrator dispatches those entries to `run_session(session_dir, model)`.
`python -m bench.eval_subagents score RUN` is unchanged.

Passing `config.json` to `eval_subagents.prepare` by itself creates ordinary base sessions. The critic
runs only through this adapter. `install_protocol` is safe to call twice; the second call does not wrap
the briefing again.

`max_tool_calls` is 3: the initial SELECT, the execution repair, and the revision. `context` is not an
investigative call. A fourth SQL fails the sandbox budget.

Scored runs do not hold STS credentials, so they use the snapshot sandbox rather than
`open_session`. The production function is `execute_on_query_session`. Both paths reject a statement
in the guard before DuckDB sees it.

## Failure modes

1. Shared blind spot. A fresh context removes the writer's scratchpad. It does not remove a definition
   both models get wrong. The checklist states the glossary's definitions so the critic is not free to
   invent a second denial rule. If the glossary is wrong, both sides are wrong together.
2. A false objection replaces a correct answer. The revision is submitted without a second critic.
   That is the cost of the one-revision cap. The trace records `outcome: revised` and the objections
   so a screen can count revised-then-wrong separately from accepted-and-wrong.
3. A revision that does not execute becomes `no_answer`, including when the first SQL was right and
   the critic was wrong. The first SQL is not kept.
4. An unparseable critic forces a revision of a query that might have been right. Fail-closed is the
   choice a wrong number is worse than a wasted revision; it will also burn some right answers.
5. The analyst can buy the execution-repair turn with a deliberately bad first statement. The cap is
   one repair, then the critic. It is not an open retry loop.
6. The critic does not see abandoned SQL. A lucky repair is invisible to it. It reviews the statement
   that returned rows.
7. `row_count_returned` stops at the guard's 1000-row limit. The digest says when the limit was hit.
   A critic that treats that number as the population is wrong; the checklist says so. The model can
   ignore the checklist.
8. The plan can be vague and the SQL right. The rubric says that is not an objection. A model that
   objects anyway spends the one revision. The override rule does the opposite as well: a plan that
   contradicts the SQL is an objection even if the verdict says accept.
9. Questions the bench still leaves ambiguous (the data-start population, before a question states it)
   will split the critic the same way they split the analyst. The checklist tells the critic to follow
   the population the question stated. It does not add a population the question forgot.
10. Prescriptive rubrics. OpenAI's in-house agent report found highly prescriptive prompting degraded
    results. This checklist is a list of objections, not a SQL template. If a later screen shows the
    critic rewriting every query into one shape, the rubric is too narrow and should be cut, not extended.

## Cost

Calls on the path that matters:

| Path | Analyst calls | Critic calls | SQL executions |
|---|---|---|---|
| Accept, SQL ran | 2 (plan, then SQL) | 1 | 1 |
| SQL failed once, then accept | 3 | 1 | 2 |
| Critic objects, revision runs | 3 | 1 | 2 |
| SQL failed once and the critic objects | 4 | 1 | 3 |
| Both statements fail to execute | 3 | 0 | 2 |
| Refusal, or the plan reply will not parse | 1 | 0 | 0 |
| SQL reply will not parse | 2 | 0 | 0 |

The critic is always one call, and only after a result exists. There is no second critic and no
candidate pool. That is the catalog's "verifier-only" level, not Anthropic's multi-agent research
system (on the order of 15× chat tokens) and not CHASE's 21-candidate selector.

The two system prompts are stable per snapshot: role instructions, the checklist, and the schema
context from `context`. They do not share a conversation cache with each other. A backend that caches
system prompts can cache each role across questions. The user message is the question plus a small
digest. Token totals belong to the first live run; this module records `input_chars` and `output_chars`
per call in `a05_trace.json` and does not call an API.

Catalog prices used as the budget, not as a measurement of this arm (from
`research/sota-techniques-catalog-2026.md`): Haiku 5.5 is $0.10 / $0.50 per million input/output
tokens, Sonnet 5.5 is $2 / $10, cache reads at 0.1× input. The catalog's cost line for an LLM verifier
is one extra Haiku-sized call. A05 spends that call on every question that returns rows, including the
easy ones where the first SQL was already right. Expected accuracy, from the same catalog, is about
+0 to +3 points for a verifier-only split and about +2 to +6 for result checks on a free-SQL lane,
with the 2026 BIRD repair study closer to +1 to +4 and concentrated on SQL that did not execute.
Those intervals are priors. Screen 1's null on programmatic checks is the reason not to treat them
as a forecast for this bench.

Effort stays low. The refresh found low→high at −2.4 Haiku and +1.7 Sonnet, both intervals covering 0.

## Related work

- CHASE-SQL query-plan chain of thought and the query fixer (Pourreza et al. 2024,
  https://arxiv.org/abs/2410.01943). Removing the fixer cost 3.78 points on BIRD dev. The fixer
  repairs execution errors. A05's single repair is that, capped at one, and it is not the critic.
- MAC-SQL selector / decomposer / refiner (https://arxiv.org/abs/2312.11242). Ablations on GPT-4
  BIRD dev: −2.11 / −3.85 / −4.63 when each role is removed. The paper's roles can share a context.
  A05 keeps one extra context, the critic, and does not add a selector or a decomposer.
- CHESS's LLM unit tester (Talaei et al. 2024, https://arxiv.org/abs/2405.16755) checks SQL against
  a natural-language restatement. A05's critic checks the executed result as well as the SQL, which
  is the difference between a query that parses and a query that counted the wrong rows.
- DS-STAR planner / coder / verifier / router (https://arxiv.org/abs/2509.21825). The router ablation
  is +5.3 points on DABStep hard tasks. There is no verifier-only number. About 98% of those hard
  tasks needed a refinement round, which is a different workload from a single billing question.
- Beskorovainyi 2026 (https://arxiv.org/abs/2606.29733). Execution-error repair is the component that
  survives a corrected comparison, +1.0 to +4.4 points, 1.1–1.5× tokens, and not significant on the
  strongest model in that study. Self-consistency at 5 samples was about zero at 5× tokens.
- OpenAI's in-house data agent (Xu, Suresh, Tang, 29 Jan 2026,
  https://openai.com/index/inside-our-in-house-data-agent/). Empty results are investigated; the
  grader scores executed SQL against a golden query. `bench/judge.py` already does the grading, and
  only after the matcher says no. A05's critic runs before submission, inside the agent, and never
  sees the gold SQL.
- Anthropic, "Building effective agents" (19 Dec 2024,
  https://www.anthropic.com/engineering/building-effective-agents). An evaluator-optimizer loop is a
  workflow when the check is clear. A multi-agent split is what you add after the simple loop has
  stopped moving. A05 is that workflow with the evaluator in its own context, which is the smallest
  split the catalog's T18 lists (`single + separate verifier context`).
- MAST (https://arxiv.org/abs/2503.13657). Multi-agent failures cluster on specification and
  handoff. A05's handoff is one JSON verdict. The trace keeps the objections so a bad handoff is
  visible in `a05_trace.json`.
- NL2SQL-Bugs detection around 75%, and post-hoc detectors catching 69–83% of silent text-to-SQL
  errors, as cited in the catalog from Cube 2026. Detection is not a correction. A05 spends the
  detection on one revision, which can miss.

Local results this design is accountable to: `research/screening_results_1.md`,
`research/heldout_results_1.md`, `research/sota_refresh_2.md`, `research/technique_factors.yaml`
(T14, T15, T16, T18), `bench/eval_subagents.py`, `bench/agent_cli.py`, `bench/judge.py`,
`ccsim/query.py`.

## How to run the scripted test

No API key. The model is `ScriptedBackend`. The end-to-end test builds a one-view snapshot, calls
`adapter.prepare` for `q01_claims_submitted`, runs the manifest, and scores it with
`bench.eval_subagents.score`.

```bash
python -m pytest analyst/approaches/a05_verifier/test_a05_verifier.py -q
```

A live screen, after a backend exists, is `adapter.prepare`, then `run_manifest(run_dir, backend)`,
then `python -m bench.eval_subagents score RUN`. This package does not construct an API client.
