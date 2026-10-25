# CC analyst (analyzer v1)

The analyst answers one customer's billing question by querying the lake. It is a Python package (`analyzer/`) with its own tool loop. The bench harness can screen it, and it does not require Claude Code.

## Architecture

```
question
  -> Analyzer.answer
       -> one or more trajectories (candidates, then an optional Haiku -> Sonnet cascade)
            -> system prompt (refusal policy, glossary, optional dictionary and profile)
            -> LLM.complete  (Anthropic, Bedrock, or a scripted replay)
            -> tools
                 list / describe / sample / profile / dictionary / glossary
                 query_metrics / lookup_values / lookup_examples   (only if that flag is on)
                 run_sql  -> bench.guard.guard -> session DuckDB, row limit and time limit
                 request_escalation -> audit -> opener.open(escalate=True) -> new session
            -> final_answer re-executes the SQL through the same guard
            -> sanity checks (empty result, rate out of range, grain warnings)
  -> AnalyzerAnswer
       for_judge()     cand_sql, cand_rows   (bench.judge.judge)
       to_final_json() kind answer|refusal|clarification|abstain
```

The session does not come from the model. Production code passes a `QueryPathOpener`, which calls `ccsim.query.open_session`: Cognito id token, STS `AssumeRoleWithWebIdentity`, the customer's `CURRENT` manifest, a fresh DuckDB, per-relation S3 secrets and `allowed_directories`. The tool layer never builds that connection itself and never takes a customer id from the question.

Model-written SQL is executed only inside `run_sql` and `sample_rows`, and only after `bench.guard.guard`. The guard allows one read-only SELECT, `sem.*` by default, and `lossless.*` only after escalation. File scans, settings, and other schemas are rejected before DuckDB sees them. Catalog calls (`list_relations`, `DESCRIBE`) are tool-written and limited to those same schemas. Profile aggregates are also passed through the guard.

Escalation is not a boolean the model can flip on the existing connection. `request_escalation(reason)` writes an audit record (customer, user, version, reason), asks the opener for a new session with `escalate=True`, and only then lets the guard name `lossless.*`. The gold lossless relations are the PHI-stripped projections the pipeline already materializes. Identifier columns are redacted again on the way out if one is ever present.

## Config flags

`AnalyzerConfig()` is the v1 analyst: pitfalls glossary, procedural steps, dictionary, profile, SQL error hints, result checks, and verification. That is the screen-1 winner plus the repair loop.

`AnalyzerConfig.from_technique(dict)` starts from the screen baseline (the off levels in `bench/agent_cli.py`) and then applies the dict. A screening config that only sets `glossary: basic` stays basic. Unknown keys raise.

| Screen factor | Config |
|---|---|
| T01 glossary | `glossary`: `none`, `basic`, `pitfalls` (aliases `glossary`, `glossary+invariants+pitfalls`) |
| T02 dictionary | `data_dictionary` |
| T03 profile | `data_profile` |
| T06 context | `context_style`: `goal`, `procedural`, `procedural_minimal` |
| T08 semantic layer | `enable_metrics` (compiler) and `enable_lookup` |
| T11 error hints | `sql_error_hints` |
| T13 examples | `enable_examples` plus an example library on the loop |
| T15 self-correction | `self_correct`: `none`, `errors_only`, `errors+result_signals`; `max_repairs` |
| T16 result checks | `result_checks`; `verify` bounces a final answer on an empty result or a rate outside 0..100 |
| T17 candidates | `candidates` (1–5), `candidate_selection`: `execution_vote`, `first_passing`, `agree_or_abstain` |
| T19 effort | `effort`: `default`, `low`, `medium`, `high` (sent as `output_config.effort`; no temperature) |
| T23 refusal copy | `refusal_prompt`: `explicit` or `minimal`. The code path does not change |
| T25 cascade | `cascade`: `off` or `haiku_then_sonnet` |
| T26 cache | `prompt_cache` marks the system prompt cacheable on a paid call |
| T27 budgets | `max_turns`, `max_tool_calls`, `row_limit`, `rows_shown_to_model`, `time_limit_seconds` |

Candidates each run a full trajectory. Selection compares executed row multisets, not the SQL text. A tie goes to the result with fewer warnings, then the earlier candidate. `agree_or_abstain` returns kind `abstain` when the executed results differ. Abstain is not a policy refusal.

The cascade runs the next model only when the trajectory has no answer, abstains, or accepts a result that still failed a blocking check. A refusal (PHI, another customer, a write) is kept and is not retried on Sonnet. There is no temperature sampling; candidates differ by the instruction they are given (direct grain vs an alternate join shape).

## LLM backends

`AnthropicLLM` and `BedrockLLM` implement `complete(...)`. Both raise `PaidAPIDisabled` unless constructed with `allow_paid_api=True`. This package does not read or add API keys. Tests and local runs use `ScriptedLLM`, which replays a list of tool-call turns (or a dict keyed by model and candidate) and records what it was asked.

```python
from analyzer import Analyzer, AnalyzerConfig, QueryPathOpener, ScriptedLLM
from ccsim.query import login

opener = QueryPathOpener(aws, deployment, login(aws, "ana@xifin.example"))
llm = ScriptedLLM([{"tool_calls": [{"name": "final_answer",
    "arguments": {"sql": "SELECT count(DISTINCT claim_key) FROM sem.claims", "answer": "..."}}]}])
result = Analyzer(opener, llm, AnalyzerConfig()).answer("How many distinct claims have we submitted?")
result.for_judge()       # cand_sql, cand_rows
result.to_final_json()   # bench score_session final.json
```

A paid run, when someone explicitly turns it on:

```python
from analyzer import Analyzer, AnthropicLLM, QueryPathOpener
Analyzer(opener, AnthropicLLM(allow_paid_api=True)).answer(question)
```

Bedrock is the same with `BedrockLLM(allow_paid_api=True, region_name="us-west-2")`. Invocation logging stays an environment concern (the data-backend design: Bedrock under a BAA, logging off or inside the PHI boundary).

## Screening with the existing harness

`python -m bench.eval_subagents prepare RUN --configs configs.json --models haiku,sonnet --agent analyzer`

Each manifest entry then has:

- `prompt`: a Bash-only briefing that drives `python -m analyzer.cli --session <dir> ...`
- `tool_spec`: the Anthropic tool list for those flags
- `analyzer_config`: the full flag dict, including `candidates` and `cascade`

The default `--agent cli` is unchanged (`bench.agent_cli`). Scoring is still `python -m bench.eval_subagents score RUN`: the CLI writes the same `final.json`, and `score_session` re-executes the SQL in a fresh sandbox.

The subagent briefing is one trajectory. The manifest already crosses Haiku and Sonnet. `candidates` and `cascade` are applied by `Analyzer.answer` in-process; they are recorded on the manifest so an API driver can run them, and they are not multiplied again inside the shell agent.

`analyzer.cli` opens the bench snapshot (`SnapshotOpener`), not a second implementation of the guard. `escalate "<reason>"` writes `escalation.json` and `audit.jsonl` in the session directory. Later commands in that session reopen the snapshot with lossless views mounted. The model has no flag that skips the audit record.

## Tests

```bash
pytest -q tests/test_analyzer.py tests/test_analyzer_sim.py
```

`tests/test_analyzer.py` is the fast suite: guard and row limit, PHI redaction, audited escalation, SQL-error and empty-result repair, candidate voting, cascade, judge/score format, the prepare adapter, and the CLI. `tests/test_analyzer_sim.py` builds a small two-customer world and checks tenant prefixes, PHI column absence, and escalation through `open_session`.

## Decisions

- The package is `analyzer/`, not a change to the generator (frozen at 3.3), the parser, or the lake. `ccsim/query.py` is called, not rewritten.
- The guard stays in `bench/guard.py` so the analyst and the harness reject the same SQL.
- Glossary and pitfall text stay in `bench/agents.py` and `bench/agent_cli.py` so screening and the analyst cannot drift.
- Verification bounces only empty results and rates outside 0..100. Grain warnings (missing `is_current_adjudication`, repeated keys) are returned to the model and stored on the answer, and they do not by themselves reject a query that asked for every event.
- Candidate votes ignore row order. Ranking questions that differ only by order can tie. `candidate_selection` can be switched to `first_passing` when order is the answer.
- Profile and dictionary are inlined into the in-process system prompt when the flags are on, because the screen's effect came from the model actually seeing them. The subagent path exposes the same facts as CLI commands, matching today's `context` command.
- Paid clients exist so the interface is real. Nothing in tests, prepare, or the default constructor calls them.

## Not in v1

- No live Haiku/Sonnet screen. That spends the API, and the scripted backend is what this package uses to prove the loop.
- No typed-metric requirement. `enable_metrics` is off unless a screen config turns it on. The compiler is the existing `SemanticAPI`.
- No multi-turn memory, clarifying-question user simulator, or agree-or-abstain reliability score (RS). The `ask` tool and `agree_or_abstain` selection are the hooks.
- Profile scans each column. It is appropriate for a bench snapshot, not yet for a multi-year customer without a cached `profile.txt`.
- The shell adapter and the in-process loop share tools and the guard. They do not share a process, so a subagent screen does not itself run N candidates.
