# Real analyzer adapter: spec (contract 1.12.0)

**Status:** a stub, for review by Claude. `api/analyzer_adapter.py` plus fixture-only tests in
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
protocol (e.g. `analyst/variants/multi_candidate/llm.py: LLMResponse`) need a small shim to `Completion`. That is
future work, and it is not in the stub.

## 3. Requirements for a real backend

* **R1 Conformance.** Every stream passes `api.contract.conformance.check(events) == []` and every event validates
  against `StreamEvent` in the schema. The adapter tests enforce this for 8 recorded run shapes and for Stop
  mid-run.
* **R2 PHI in reasoning (open; must close before wiring).** `reasoning.delta` is the model's raw `Completion.text`.
  ScriptedLLM text is canned, but a real model can quote row values. Before wiring, either drop reasoning text for
  real models or pass it through `scrub()` plus a PHI-column filter. Steps and errors are already value-free (tested).
* **R3 Paid-API switch.** `build_real_llm(kind, enable=True)` returns `AnthropicLLM`/`BedrockLLM(allow_paid_api=True)`
  only when `CC_API_ALLOW_PAID_API` equals the exact phrase `i-understand-this-calls-a-paid-model-api` **and** the
  caller passes `enable=True`. Anything else raises the analyzer's `PaidAPIDisabled`, which `api.app` already maps to
  an `error` part ("Paid model APIs are disabled on this server"). It is off by default. A test asserts that `api/app.py`
  does not reference the adapter, the switch or `allow_paid_api=True`.
* **R4 Strategy.** Apply `options.strategy` → `capabilities.STRATEGIES[*].config` as `AnalyzerConfig` overrides.
  Today demo mode only echoes it.
* **R5 Usage.** Use `Completion.usage` tokens. `LiveTap` falls back to an approximation when they are missing.
  `costIsEstimate` stays true.
* **R6 Cancel during SQL.** This needs an analyzer cancel hook (CONTRACT §8). Until then, Stop takes effect between
  model turns.

## 4. Recordings (fixtures)

`api/tests/fixtures/analyzer_runs/*.json` hold `{question, multiCandidate?, calls:[{model, candidate, tool_results:
[{tool_use_id, content}], completion:{text, tool_calls:[{name, arguments, id}], usage?}}], answer:{AnalyzerAnswer
fields}}`. All data is synthetic ("Synthetic Payer A"). Shapes: `answer`, `repair`, `escalation`, `candidates`,
`abstain`, `clarify`, `refusal`, `no_answer`. To record a real run later, wrap the LLM so each `complete()` call
appends `{model, candidate_index, tool_results of messages[-1], completion}`, and store `asdict(answer)`. Then
**scrub row values out of tool results** before committing it as a fixture.

## 5. Rollout (not done here; each step is its own reviewed PR)

1. Close R2.
2. Add `Settings.llm in ("anthropic","bedrock")` → `build_real_llm(settings.llm, enable=settings.allow_paid_api)`
   in `api.app`. Keep the demo default `scripted`. The test that forbids wiring then changes in the same PR.
3. Apply R4.
4. Shadow runs with recordings, and gate on `check()` == [] in CI.
