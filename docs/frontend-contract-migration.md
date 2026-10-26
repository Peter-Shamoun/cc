# CC chat contract: changelog and migration guide (v1.0 → 1.12.0)

**For review by Claude.** This is a reading guide to `api/CONTRACT.md`, which stays the normative text, and
`api/contract/v1.schema.json`. It covers three things:

1. what changed in each version and what a client or server must do about it;
2. what a **real analyzer backend** must emit for each event;
3. which fields are **demo-only or stubbed** today.

The adapter that produces these events from analyzer and analyst outputs is specified in
[`frontend-analyzer-adapter.md`](frontend-analyzer-adapter.md).

Every version from 1.0 to 1.12 is **additive**. A 1.0 client keeps working against a 1.12 server, because it ignores
unknown events, fields and part types (§3). No field was removed or renamed. The one wire-visible tightening is
1.12's error envelope on 404 and 405 (below).

## 1. Version-by-version

| ver | what changed | server must | client must / may |
|---|---|---|---|
| 1.0 | `seq`, `contractVersion`, `step.*`, `escalation`, `abstain`/`clarification` parts, `finishReason` `abstain`/`needs_input`, rich `usage`, `ChatRequest.resume/regenerate/scenario` | number every non-heartbeat event `seq` 0,1,2… gapless; put `contractVersion` on `message.start` | drop `seq <= lastSeq`; treat abstain/needs_input as outcomes, not errors |
| 1.1 | `GET /api/chat/{id}/stream` resume, 10 s disconnect grace, cancel closes in-flight steps, `stream_gone`, generated TS types | replay by `Last-Event-ID`; on cancel emit `step.sql_result{ok:false,error.code:"cancelled"}` for open SQL steps and `reasoning.end` for open reasoning | reconnect with backoff; on `stream_gone`, reload the conversation |
| 1.2 | `editOf` on the user message and `message.start` | store an edit as a sibling (root when the first question is edited) | send `editOf` to edit |
| 1.3 | `message.end.conversationTitle`, conversation REST `$defs` | the title is PHI-free, built from part metadata | show the title if it is present |
| 1.4 | `GET /api/capabilities`, `options.{strategy,model}` with 422 | validate options against capabilities | build pickers from capabilities |
| 1.5 | 413 `payload_too_large` (+`legacyCode`), per-org capabilities | enforce per-org availability | read `error.code`, falling back to `legacyCode` |
| 1.6 | `usage.latency.stages` (plan/sql/repair/checks/render) | wall-clock stages that sum to about `totalMs` | a stacked bar is safe |
| 1.7 | answer feedback endpoints, `Capabilities.feedbackReasons` | store per message | optional |
| 1.8 | `message.end.sources`, export endpoint | sources come from the views read by steps and tables | optional citations UI |
| 1.9 | `table.start` / `table.rows`, `ChartPart.tablePartId`, artifacts spec | the complete `part` after the rows is canonical | upsert by id; drop the partial table on abort |
| 1.10 | `regenerate.style`, "by payer" titles, CSRF and 422 docs | styles are applied server-side (`api/styles.py`) | `x-cc-csrf: 1` on cookie-session writes |
| 1.11 | org-scoped share links, `TablePart.dataWithheld` | shares are org-scoped; data is withheld by default | render "Data not shared (N rows)" |
| 1.12 | ordering rules as data (`stream_rules.json`) with py/ts runners; fail-safe SSE decode and reducer; 404/405 envelope | every stream must pass `check()`; unknown routes return `{"error":{code:"not_found"}}` | use `readSse`/`SseDecoder`; `reduceV1` drops malformed events |

**Migration notes**
* **1.12 errors.** Code that looked for FastAPI's `{"detail": …}` on a 404 or 405 must read `error.code`
  (`not_found`, `method_not_allowed`). `DELETE /api/conversations/{id}` keeps its documented 404 `{deleted:false}`.
* **1.12 SSE.** `web/src/adapters/http.ts` `parseSse` still `JSON.parse`s each frame and throws on a bad one. An ask
  is in NOTES.md to switch it to `web/src/contract/sse.ts`.
* **Pre-v1 → v1 UI.** `tool_call` parts are legacy. A v1 server sends `step.*` events and only adds `tool_call`
  parts when `legacy_tools` is set.

## 2. What a real analyzer must emit, event by event

The **Conformance** column names the rule id in `api/contract/stream_rules.json`. `check(events) == []` is the
acceptance test for any backend: `api.contract.conformance.check` in Python, `checkStream` in TS.

| event | real analyzer must emit | source in a real run | conformance |
|---|---|---|---|
| `message.start` | first, once: `messageId`, `conversationId`, `contractVersion`. Echo `parentId`/`editOf`/`resumeOf`/`regenerateOf`/`regenerateStyle` and `strategy`. `model` = the first model actually used | API layer, not the analyzer | start.first, start.once, message.same_id |
| `status` | short labels such as "Thinking", "Running query", "Writing answer"; `null` clears | LiveTap / API | — |
| `step.explore` | one per schema or data probe tool call: `probe` (mapped tool name), `target` (relation, ≤120 chars), a `summary` built from **counts only** ("8 columns", "20 rows sampled"), `elapsedMs` | `describe_relation`, `list_relations`, `sample_rows`, `lookup_*`, `profile`, … tool results | step.unique |
| `step.sql_running` | before every executed query, **including the final answer's re-execution**: full `sql`, `purpose` (answer/candidate/repair), `attempt`, `candidateId` (multi), `repairOf` (repair) | `run_sql` / `final_answer` tool calls | step.unique, sql.paired, repair.of |
| `step.sql_result` | exactly one per `sql_running`: `ok`, `elapsedMs`, `rowCount`, `columns` (**names only**), `truncated`, `error{code,message}` with literals masked (`scrub`) | tool result JSON (`row_count`, `columns`, `truncated`), or `ERROR:` text | sql.paired |
| `step.repair_attempt` | when a query is retried after an error: `failedStepId`, `attempt`, `maxAttempts`, `error`, `revisedSql`. Precedes the repair `sql_running` that names it in `repairOf` | the next `run_sql`/`final_answer` after a failed one | repair.target, repair.of |
| `step.check` | the verifier verdict for the chosen final query (`pass`/`warn` + caveats), and `fail` + `action:"repair"` when verification sends it back. `targetStepId` must name an earlier SQL step | `final_answer` "result failed verification:" results; `AnalyzerAnswer.warnings` | check.target, answer.after_steps (allowed after the answer) |
| `step.candidates` | multi-candidate only: per-candidate `sql`, `ok`, `rowCount`, `agreesWith`, `stepId`; `chosenId` (null = abstain); `method`; `rationale` | `AnalyzerAnswer.candidate_results` + `selection` | — |
| `escalation` | when the model changes mid-run: `from`/`to` `{model, tier}`, `trigger` (`repair_exhausted` / `verifier_fail`), `reason` | the cascade (`AnalyzerConfig.cascade`) | answer.after_steps (forbidden after the answer) |
| `reasoning.delta` / `.end` | optional model text, candidate 0 only, closed before the answer | `Completion.text` | — (see PHI note R2 in the adapter spec) |
| `text.*` | the answer prose, streamed. `text.end` for every `text.start`, even when aborted | `outcome_parts` → `emit_parts` | text.span |
| `table.start` / `table.rows` | for a table with rows: columns, then ≤10-row chunks with `offset`, then the complete `part` with the same id | `emit_parts` | table.span |
| `part` | code (SQL, `role:"query"`), table (preview ≤50 rows, `csvFileId`), file (>50 rows), chart, callouts, abstain, clarification, error | `outcome_parts` | answer.after_steps, sql_part.target |
| `part.patch` / `code.delta` | **not emitted** by any backend today; reserved | — | patch.target |
| `message.end` | last, once: `finishReason` and its required content (§4), `usage`, plus `sources`/`conversationTitle` when they apply | API layer | end.last, end.once, finish.known, clarify.needs_input |
| `heartbeat` | on silence ≥15 s, with no `seq` and no SSE id | API layer | excluded from every rule |

**Ordering in one line.** `message.start` → (`status` | `step.*` | `escalation` | `reasoning.*`)* → the answer
(`text.*` / `table.*` / `part`) → `message.end`. After the first answer content, no further `step.*` or
`escalation` is allowed, except `step.check`.

## 3. Demo-only and stubbed fields

| field / behaviour | today (demo) | what real mode needs |
|---|---|---|
| `ChatRequest.scenario`, demo keywords (§9) | forces a scripted `ScriptedLLM` run | ignored outside demo; remove the keyword routing |
| `message.start.model` | `settings.llm` (`"scripted"`) | the model id of the first call (the adapter uses `calls[0].model`) |
| `message.start.strategy` / `options.strategy` | validated and echoed; **not applied** to `AnalyzerConfig` (the scenario drives the run) | apply `capabilities.STRATEGIES[*].config` as overrides |
| `usage.costUsd`, `costIsEstimate:true` | static price table (`api/live.py: PRICES`); tokens are approximated when the completion has no `usage` | real token usage from the API response; keep `costIsEstimate` until billing reconciles |
| `usage.latency.*` | real wall clock, but model latency is a **scripted pause** (`CC_API_DEMO_PACE`) | real |
| `table.rows` timing | streamed from the finished part after the analysis ends | can stream as the DB returns rows, with the same events |
| `report` artifacts (§16) | `report_artifacts` reshapes the demo answer | a real artifact producer, or none |
| `regenerate.style` | applied server-side to finished parts (`api/styles.py`), for every backend | may also become a prompt hint; the output shape is fixed |
| shares (§17), feedback (§13), downloads | in-memory registries; lost on restart (`ShareRegistry`, `DownloadRegistry` 15-minute TTL) | persistent stores |
| `step.check.check` | only `verifier` is emitted | `row_count`, `null_share`, `join_fanout`, … when the analyzer gains those checks |
| `escalation.trigger` | only `repair_exhausted` / `verifier_fail` | `low_confidence`, `complexity`, `disagreement`, `user` are reserved |
| `step.candidates.score` / `label` | not emitted | optional |
| cancel during SQL | stops between model turns only (§8) | an analyzer cancel hook (§8 "intended semantics") |
| auth `demo:<customer>:<user>` | demo bearer tokens | `cc_session` cookie / Cognito (sim) |
| data | synthetic `sem.claims` in DuckDB (`api/backends.py: demo_connection`) | `ccsim.query.open_session` via `QueryPathOpener` |
| paid model APIs | **never called** | the off-by-default switch in `api/analyzer_adapter.py` (not wired) |
