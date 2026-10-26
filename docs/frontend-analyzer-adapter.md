# Real analyzer adapter: spec (contract 1.18.0)

**Status:** a stub, for review by Claude. `api/analyzer_adapter.py`, `api/reasoning_policy.py` (R2), `api/strategy_config.py` (R4) and `api/analyst_shim.py` (R7), plus fixture-only tests in
`api/tests/test_analyzer_adapter.py`. The server is **not** wired to it. No paid API is called, and nothing under
`analyzer/`, `analyst/`, `bench/` or `ccsim/` was changed (they were read only).

## 1. The pipeline

```
Analyzer.answer(question)                       analyzer/loop.py
  ├─ llm.complete(model, messages, candidate_index) ──► LiveTap.complete   api/live.py   → step.* / escalation / reasoning.*
  │     (the messages' last user turn carries the tool_result blocks of the previous turn's tool calls)
  └─ returns AnalyzerAnswer                     analyzer/answer.py
LiveTap.finish(answer)                          → closes the final SQL steps, step.check (verifier), step.candidates
outcome_parts(answer, …) → (parts, finishReason) api/mapping.py
emit_parts(parts, emit)                         api/stream_parts.py (shared with api/app.py) → text.* / table.* / part
message.end {finishReason, usage = LiveTap.usage(), sources?, conversationTitle?}
```

`POST /api/chat` already runs this pipeline with `ScriptedLLM` in demo mode. For a real backend, the **only**
change is the LLM object passed into `LiveTap`. The adapter pins that down. `events_for_recording()` replays a
recorded real run through the same `LiveTap` → `outcome_parts` → `emit_parts` path, so the event mapping can be
tested from fixtures without a model, a network or a database.

## 2. Mapping: analyzer and analyst outputs → contract events

### 2.1 Model calls (`analyzer.llm.Completion`)

| analyzer output | contract event |
|---|---|
| `Completion.text` (candidate 0) | `reasoning.delta` (first: the text; later: " " + text), then `reasoning.end{durationMs}` in `finish`/`abort` |
| model differs from the previous call of the same candidate | `escalation{from,to:{model,tier}}`, trigger `repair_exhausted` if the candidate has an unrepaired failure, else `verifier_fail` |
| tool call `run_sql` / `final_answer` with `sql` | `step.sql_running{sql, attempt, purpose, label}` + `status "Running query"`. `purpose` is `candidate` with `candidateId:"k{n+1}"` when `candidates>1`, `repair` after a failure |
| …after a failed query in the same candidate | first `step.repair_attempt{failedStepId, attempt:n, maxAttempts:3, error, revisedSql}`, then the `sql_running` with `repairOf` and `attempt:n+1` |
| any other tool call (`describe_relation`, `list_relations`, `sample_rows`, `lookup_*`, `profile`, `query_metrics`, `request_escalation`) | `status "Exploring <relation>"`, then on its result `step.explore{probe: _EXPLORE[name], target, summary, elapsedMs}` |
| tool call `ask` / `refuse` | no event. Their arguments become the clarification choices / abstain category |

### 2.2 Tool results (`tool_result` blocks in the next call's messages)

| content | contract event |
|---|---|
| JSON `{row_count, columns, truncated?}` | `step.sql_result{ok:true, rowCount, columns:[{name}], truncated?}`, names only, **never row values** |
| `ERROR: …` / `final SQL failed: …` | `step.sql_result{ok:false, error:{code:_error_code(msg), message:scrub(msg)}}` (quoted literals and 4+ digit runs masked) |
| `result failed verification: …` | `step.sql_result{ok:true}` + `step.check{check:"verifier", verdict:"fail", action:"repair", targetStepId}` |
| an explore payload | `summary` from counts: `N columns`, `N views`, `N rows sampled`, `N similar questions`, or `Done` / `Failed: …` |

The final answer's own SQL result is consumed inside the analyzer, so `LiveTap.finish` closes that step with the
answer's `rows`/`columns` counts.

### 2.3 The final result (`AnalyzerAnswer` → parts, via `api/mapping.py: outcome_parts`)

| `answer.kind` | parts | `finishReason` |
|---|---|---|
| `answer` | `text` (prose before "Assumptions:"), `code{role:"query"}`, `table` (preview ≤50, `csvFileId`, `sqlPartId`, `sensitivity`), `file` if >50 rows, `chart` (when it fits), and `callout`s for assumptions, caveats (`warnings`) and PHI redaction. Also `step.check{verifier, pass|warn}` before them | `stop` |
| `clarification` | one `clarification{question, choices from ask args (≤8)}` | `needs_input` |
| `refusal` | one `abstain{category from refuse args, else phi_request/out_of_scope by pattern}` | `abstain` |
| `abstain` (candidates disagreed) | one `abstain{category:"low_confidence", details}` | `abstain` |
| anything else (`no_answer`, …) | one retryable `error{code:"query_failed"}` | `error` |
| exception | `error{code:"internal"}`, retryable | `error` |
| cancelled (`LiveTap` raises `Cancelled`) | open SQL steps get `sql_result{ok:false, error.code:"cancelled"}`, then `reasoning.end` | `aborted` |

Multi-candidate runs: `answer.candidate_results` + `answer.selection` → `step.candidates{candidates[k1…], chosenId,
method:"execution_agreement" (vote/agree_or_abstain), rationale}`. `agreesWith` means the same row count and the
same normalized SQL.

### 2.4 `analyst/*` agents (`bench.agents.Answer`)

The analyst variants (e.g. `analyst/variants/multi_candidate.MultiCandidateAgent`) return `bench.agents.Answer`
`{sql, rows, text, refused, usage, trace}`. `answer_from_bench(answer, columns=…)` normalizes that into an
`AnalyzerAnswer`, without importing bench/:

* `refused` → `refusal`;
* `rows is None` → `abstain` (with `usage.selection` kept);
* otherwise `answer`.

`bench.Answer` has **no column names**, so the caller must pass them from the executor's cursor description.
Otherwise they are `col_1…n`. Steps from analyst agents only appear if their LLM is wrapped in `LiveTap` the same
way, through the same `complete(model=…, messages=…, candidate_index=…)` interface. Agents with their own LLM
protocol need a small shim to `Completion`: see §6 (R7) and `api/analyst_shim.py`.

## 3. Requirements for a real backend

* **R1 Conformance.** Every stream passes `api.contract.conformance.check(events) == []` and every event validates
  against `StreamEvent` in the schema. The adapter tests enforce this for 9 recorded run shapes and for Stop
  mid-run.
* **R2 PHI in reasoning (closed in the adapter path).** `reasoning.delta` is the model's raw `Completion.text`, and a
  real model can quote row values. The adapter never forwards it: `run_to_events`/`events_for_recording` wrap `emit`
  in `api.reasoning_policy.ReasoningPolicy(policy)`:
  * `"drop"` (**default**): every `reasoning.delta`/`reasoning.end` is withheld. Reasoning parts are optional in the
    contract, so the stream stays conformant and the UI simply shows no "Thinking" disclosure.
  * `"summary"`: model text is still withheld. When the tap closes the reasoning part, one `reasoning.delta` is built
    **only from labels and counts** of step events already sent (known probe labels, queries / failed / repaired,
    candidate count, last row count, check verdict counts, escalation count), e.g. *"Explored the data: sampled values.
    Ran 3 queries (1 failed, 1 repaired). The last successful query returned one row."* No model text, SQL, error text
    or table name is copied. The summary then passes `phi_findings()` (names, MRNs, DOBs, member IDs, NPIs, SSNs,
    phones, e-mails, long digit runs) or is dropped (fail closed).
  * There is no "raw" mode. Showing model text needs a PHI review and a contract change.
  * `scrub()` (error text in `step.sql_result.error` / explore summaries) now also masks dates, e-mail addresses and
    IDs that mix letters and digits, as well as quoted literals and runs of 4+ digits.
  * Tests (`api/tests/test_adapter_r2_r4_r7.py`) use the synthetic `phi_reasoning` recording, whose model text, sampled
    rows and DB error quote an invented patient name, MRN, DOB, member ID and NPI. None of them may appear anywhere in
    the stream under either policy, including Stop mid-run. The demo path (`api.app` + ScriptedLLM) is unchanged.
  * Shown SQL (closed in 1.14.0, **needs sign-off**): before 1.14, `step.sql_running.sql` and the `code` part showed
    the model's SQL verbatim, so a `WHERE` literal was visible. Now every shown SQL goes through `api.sql_mask`
    (`masking_emit` + `mask_part`, in `run_to_events` and in `api.app`): policy `phi` by default masks literals
    compared with PHI-bearing columns and identifier-like values, and sets `maskedLiterals` (CONTRACT.md §10). The
    executed SQL is unchanged. Remaining risk under `phi`: a name in free text against a non-PHI column; `all`
    closes it at the cost of readability. Proposed for production: `phi` everywhere, `all` for orgs that ask.
* **R3 Paid-API switch.** `build_real_llm(kind, enable=True)` returns `AnthropicLLM`/`BedrockLLM(allow_paid_api=True)`
  only when `CC_API_ALLOW_PAID_API` equals the exact phrase `i-understand-this-calls-a-paid-model-api` **and** the
  caller passes `enable=True`. Anything else raises the analyzer's `PaidAPIDisabled`, which `api.app` already maps to
  an `error` part ("Paid model APIs are disabled on this server"). It is off by default. A test asserts that `api/app.py`
  does not reference the adapter, the switch or `allow_paid_api=True`.
* **R4 Strategy (mapping done; not wired).** `api.strategy_config.strategy_config(strategy, model, *, grid=None,
  customer=None, strict=False)` is a pure function that returns `StrategyConfig{overrides, fidelity, notes}`. The
  `overrides` are valid `AnalyzerConfig(**overrides)` / `AnalyzerConfig.from_technique(overrides)`. The strategy gives
  `capabilities.STRATEGIES[*].config`, and a non-`auto` model adds `model`. Unknown, incompatible or org-disabled
  options raise `ValueError`, using the same rule as the API's 422. An optional 384-grid cell (factor names from
  `research/grid/GRID_PROTOCOL.md`, read-only) refines it:

  | grid factor | level → `AnalyzerConfig` | fidelity |
  |---|---|---|
  | `generation` | `direct_sql` → (none); `metric_spec` → `enable_metrics`; `decompose_cte` → (none) | exact / approximate / **unsupported** |
  | `fewshot` | 1 → `enable_examples: true` (lookup tool, not pasted examples); 0 → `false` | approximate / exact |
  | `explore` | 1 → `data_profile`, `enable_lookup` true; 0 → both false | exact |
  | `candidates3` | 1 → `candidates: 3, candidate_selection: execution_vote`; 0 → `candidates: 1` | exact |
  | `verifier` | 1 → `verify`, `result_checks` true (deterministic bounce, not a critic model); 0 → `verify: false` | approximate / exact |
  | `repair` | 1 → `self_correct: errors+result_signals, max_repairs: 2`; 0 → `none`, `0` | exact |
  | `assumptions` | no switch; `final_answer.assumptions` is always offered | approximate |
  | `multiagent` | 1 → no planner/writer split exists | **unsupported** (0: exact) |

  `verifier=1` with `repair=0` is flagged in `notes`: the analyzer's bounce needs `self_correct=errors+result_signals`.
  `strategy="candidates"` with `candidates3=0` is a contradiction and raises. `strict=True` rejects unsupported
  factors; otherwise they are reported in `fidelity`, so a caller never silently runs a different cell. `api.app` still
  only echoes the strategy.
  `message.start.strategy` from the adapter is always a `GET /api/capabilities` id (`adapter_strategy`): the request's
  `options.strategy` when given (an unknown id raises), else inferred from the run: several candidates →
  `candidates`, a model change mid-run → `cascade`, otherwise the default `tool_loop`. Recordings may carry
  `"strategy"`. (Before this, the stub sent `"analyst"`, which is not a capability id.)
* **R5 Usage.** Use `Completion.usage` tokens. `LiveTap` falls back to an approximation when they are missing.
  `costIsEstimate` stays true.
* **R6 Cancel during SQL.** This needs an analyzer cancel hook (CONTRACT §8). Until then, Stop takes effect between
  model turns.
* **R7 Analyst model shims (stub).** See §6.

## 4. Recordings (fixtures)

`api/tests/fixtures/analyzer_runs/*.json` hold `{question, multiCandidate?, calls:[{model, candidate, tool_results:
[{tool_use_id, content}], completion:{text, tool_calls:[{name, arguments, id}], usage?}}], answer:{AnalyzerAnswer
fields}}`. All data is synthetic ("Synthetic Payer A"). Shapes: `answer`, `repair`, `escalation`, `candidates`,
`abstain`, `clarify`, `refusal`, `no_answer`, and `phi_reasoning` (synthetic PHI in model text, sampled rows and a DB
error, for R2). To record a real run later, wrap the LLM so each `complete()` call
appends `{model, candidate_index, tool_results of messages[-1], completion}`, and store `asdict(answer)`. Then
**scrub row values out of tool results** before committing it as a fixture.

## 5. Rollout (not done here; each step is its own reviewed PR)

1. R2 is closed in the adapter path. Wiring must call `run_to_events(..., reasoning="drop")` (or `"summary"`).
2. Add `Settings.llm in ("anthropic","bedrock")` → `build_real_llm(settings.llm, enable=settings.allow_paid_api)`
   in `api.app`. Keep the demo default `scripted`. The test that forbids wiring then changes in the same PR.
3. Apply R4: `AnalyzerConfig(**{**settings.analyzer_overrides, **strategy_config(strategy, model,
   customer=principal.customer).overrides})`.
4. Shadow runs with recordings, and gate on `check()` == [] in CI.

## 6. Analyst-agent model shims (R7, stub)

`api/analyst_shim.py` implements each analyst agent's *own* model interface on top of a `LiveTap`, or anything with
the analyzer's `complete(**kw) -> Completion`. An analyst run then streams the same events as an analyzer run.
Frame it with `run_to_events(question, run, inner_llm, ...)`, where `run(tap)` builds the agent with a shim around
`tap` and returns `answer_from_bench(result, columns=…)`. The shims only translate. The backend behind the tap is
chosen elsewhere: `ScriptedLLM`/fixtures today, and `build_real_llm` behind the off switch later.

| agent | its interface | shim | candidate index |
|---|---|---|---|
| `analyst/variants/multi_candidate` | `complete(*, system, messages, tools, model, strategy) -> LLMResponse(text, tool_calls[ToolCall(name, arguments, call_id)], usage, stop_reason)` | `ToolLoopShim(tap, strategies)` | position in `strategies`; `__selector__`/`__verifier__` get `n`, `n+1` |
| `analyst/approaches/a05_verifier` | `complete(*, role, system, user, phase) -> ModelCompletion(text)` | `TextRoleShim(tap, {"analyst": m1, "critic": m2})` | analyst 0, critic 1 |
| `analyst/variants/context_retrieval` | `complete(LLMRequest) -> LLMResponse(sql, text, refused, usage)` | `SingleShotShim(tap, model)` | 0 |

Rules:

* **Separate indices per role.** `LiveTap` reads a model change *within one index* as an escalation, and it streams
  only index 0's text as reasoning. Judges and critics therefore never cause a false `escalation`, and their text
  never becomes the analyst's reasoning.
* **Final SQL runs inside the agent** (multi_candidate executes `final_answer` itself). `ToolLoopShim` records each
  candidate's final SQL, and `candidate_for_sql(answer.sql)` gives the `candidate_index` for the `AnalyzerAnswer`.
  `LiveTap.finish` then attaches the row count and columns to the chosen candidate's final step.
* **Tool results** keep the agent's own `{"type": "tool_result", "tool_use_id": call_id}` blocks. These are what
  `LiveTap` consumes, so `ToolCall.id` ↔ `call_id` round-trips.
* **a05** runs SQL through its own executor, not through model tool calls. Its shim yields reasoning (subject to R2)
  and usage only. Steps for a05 need an executor tap (open).
* **context_retrieval**: `final_answer{sql, answer}` → `LLMResponse(sql, text)`; `refuse{reason}` → `refused=True`;
  anything else → text only.
* Response classes are imported lazily from `analyst` (read-only) and can be injected. Nothing under analyst/ changes.

Tests (fixtures only): a real `MultiCandidateAgent(n_candidates=3)` runs through `ToolLoopShim` with a scripted
analyzer-protocol fake and fake tools. It must be schema-valid and conformant, with `k1..k3` candidate steps, rows on
the chosen candidate, and no PHI from the model text under either policy. There are also round-trip tests for each
shim, and a test that the shims reference no real backend and that `api.app` does not import them.
