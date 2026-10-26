# ClaimClarity chat API — stream contract

**Version: 1.11.0** (semver) · machine-readable schema: [`api/contract/v1.schema.json`](contract/v1.schema.json) ·
TypeScript: generated [`web/src/contract/v1.gen.ts`](../web/src/contract/v1.gen.ts) (`python -m api.contract.gen_ts`) +
client mirror [`web/src/api/contract.ts`](../web/src/api/contract.ts) (pinned to the generated types at compile time)

This is the interface between the analyzer (whichever bundle wins the search) and the chat UI.
The frontend builds against this document only; the analyzer adapts to it.

## 0. Rules for changing this contract

1. **Contract first.** A new event, part, or field is added here, in the schema and in the TS types
   *before* any backend emits it or any UI depends on it. The PR that changes the contract changes all
   three files and adds a changelog line.
2. **Semver.**
   * patch: wording, clarifications, tighter docs; no shape change.
   * minor: additive only: new event types, new part types, new optional fields, new enum values for
     fields documented as *open* (marked "open" below).
   * major: removing or renaming anything, making an optional field required, changing meaning.
     Majors get a new schema file (`v2.schema.json`) and the server must keep serving v1 until the UI moves.
3. Every stream announces its version in `message.start.contractVersion`; `GET /api/contract` returns
   `{version, schema}`.
4. **One source of truth.** `v1.schema.json` is canonical. `web/src/contract/v1.gen.ts` is generated from it
   (`python -m api.contract.gen_ts`; `--check` exits 1 on drift, run by `api/tests/test_contract_ts.py`), and
   `web/src/contract/compat.test.ts` asserts at compile time that every hand-written type in `web/src/api/contract.ts`
   / `web/src/lib/contract.ts` is mutually assignable with its generated twin, so `npm run build` fails on drift.
5. The contract test suite (`api/tests/test_contract.py`) validates every event emitted by every demo
   scenario against the schema. A backend change that emits something off-contract fails CI.

## 1. Transport

* `POST /api/chat` with a `ChatRequest` JSON body → `text/event-stream`.
* `GET /api/chat/{messageId}/stream` (+ optional `Last-Event-ID`) → the same stream, replayed then live (§8). Works
  with a native `EventSource` (cookie auth), whose automatic reconnect sends `Last-Event-ID` for you.
* Each SSE frame is `id: <messageId>:<seq>` + `data: <one JSON StreamEvent>`. Heartbeats have no `id:`.
* Auth: httpOnly `cc_session` cookie (prod) or `Authorization: Bearer demo:<customer>:<user>` (demo mode).
  Credentials are never accepted in query parameters, because URLs end up in logs.
* **CSRF (1.10 docs, #198):** a cookie-authenticated (`cc_session`, no `Authorization` header) `POST`/`PUT`/`PATCH`/
  `DELETE` to `/api/*` must send the header **`x-cc-csrf: 1`**. Without it the API answers 403
  `{"error":{"code":"access_denied","message":"Missing CSRF header."}}`, before auth or body parsing. Bearer/demo
  requests are exempt. `GET` (including `EventSource` resume) needs nothing. The header is safe to send always, so
  adapters should add it to every write.
* Non-stream failures return `HttpError` JSON: `{"error": {"code", "message"}}`. Statuses and the codes the UI
  should branch on:

  | Status | `code` | When | Client |
  |---|---|---|---|
  | 401 | `auth_expired` | session gone | sign in again |
  | 404 | `access_denied` / `not_found` | unknown or another user's conversation (never 403) | "not available" |
  | 404 | `stream_gone` | nothing to resume: the run finished > 60 s ago or never existed | reload the conversation |
  | 404 | `validation` | `regenerate` / `editOf` points at nothing | |
  | 409 | `clarification_expired` | the clarification can no longer be answered | ask again |
  | 410 | `transcripts_unavailable` | the customer key was destroyed (§11) | history gone |
  | 413 | `payload_too_large` (1.5; was `validation`, still sent as `legacyCode`) | request body over **1 MiB** (`CC_API_MAX_BODY_BYTES`; `maxBytes` in the body), rejected before parsing | "message too long"; not retryable |
  | 403 | `access_denied` | cookie-session write without `x-cc-csrf: 1` (see CSRF above) | client bug: add the header |
  | 422 | `validation` / `invalid_title` | bad body, bad `options` (§12), bad title | show `message` |
  | 429 | `rate_limited` | more than **3 unfinished runs** per user (`CC_API_MAX_CONCURRENT_RUNS`) | wait `Retry-After` seconds (sent, currently 3), then retry |

  **Every 422 uses this envelope (#186):** request-validation failures are
  `{"error":{"code":"validation","message":"Invalid request: <field paths>."}}`, e.g. `Invalid request:
  regenerate.style.`. FastAPI's `{"detail":[...]}` is never sent, and the message names fields, never values.
  Every error response still carries the security headers in §10 (the 413 included). When a code gets more specific,
  the old one is kept as `error.legacyCode` for at least one minor version. Branch on an unknown `code` by HTTP status.
* Every response carries `Cache-Control: no-store` and the security headers in §10.

## 2. Ids and ordering guarantees

* All ids are opaque, non-PHI, `^[A-Za-z0-9_:.-]+$`, ≤128 chars. Never derived from data values.
* `seq` is present on every event except `heartbeat`; it starts at 0 and is gapless and strictly
  increasing within one message. Clients drop events with `seq <= lastSeq` (resume safety).
* Exactly one `message.start` (seq 0) and exactly one `message.end` (last event). Nothing follows `message.end`.
* `stepId` is unique within a message. For a SQL step, `step.sql_running` precedes its `step.sql_result`
  (same `stepId`). Steps may be in flight concurrently (e.g. candidates), so a UI keys by `stepId`, not position.
* `step.repair_attempt.failedStepId` always refers to an earlier `step.sql_result` with `ok: false`.
  The revised SQL then runs as a **new** step: `step.sql_running {purpose: "repair", repairOf: <repair stepId>}`.
* `step.check.targetStepId`, `step.candidates.candidates[].stepId` refer to earlier steps.
* `text.start` → `text.delta`* → `text.end` per `partId`. A `part` whose `id` already exists replaces it;
  `part.patch` merges fields into an existing part (never `id`/`type`).
* Tool steps stream **during** the run: the analyzer emits each step as it happens, not batched at the end.
* Answer parts (answer `text`, the SQL `code` part, `table`, `chart`/`image`, `callout`s) are emitted after the
  last step that produced the chosen result. Steps after the answer begin are not allowed in 1.x except
  `step.check` on the answer itself.
* `escalation` may appear anywhere between `message.start` and the first answer part.

## 3. Forward compatibility (client obligations)

* Ignore unknown event types and unknown fields.
* Render unknown part types as an "Unsupported content" chip; never crash.
* Enum fields marked *open* may gain values in a minor release: fall back to a generic rendering.

## 4. Events

| type | when | key fields |
|---|---|---|
| `message.start` | first | `messageId`, `conversationId`, `contractVersion`, `model?`, `strategy?`, `parentId?` (user turn), `resumeOf?` `{clarificationId, messageId}`, `regenerateOf?`, `regenerateStyle?` (1.10), `editOf?` (1.2) |
| `status` | any time | `label` (short present-tense phrase, e.g. "Running query") or `null` to clear |
| `step.explore` | schema/data probe | `stepId`, `probe` (*open*: `list_views`, `describe_view`, `sample_values`, `lookup_glossary`, `lookup_dictionary`, `search_examples`, `profile`), `target?`, `summary`, `elapsedMs?` |
| `step.sql_running` | a query starts | `stepId`, `sql` (full text), `purpose` (`explore`·`answer`·`candidate`·`repair`·`check`), `attempt` (1-based), `candidateId?`, `repairOf?`, `label?` |
| `step.sql_result` | that query ends | `stepId`, `ok`, `elapsedMs`, `rowCount?`, `columns?` (`{name, type?}` only), `truncated?`, `error?` `{code, message}` |
| `step.repair_attempt` | a failed query is being fixed | `stepId`, `failedStepId`, `attempt`, `maxAttempts?`, `error`, `revisedSql`, `explanation?` |
| `step.check` | a verifier/sanity check fires | `stepId`, `check` (*open*: `verifier`, `row_count`, `null_share`, `join_fanout`, `date_range`, `definition`…), `verdict` (`pass`·`warn`·`fail`), `message`, `targetStepId?`, `action?` (`none`·`repair`·`caveat`·`abstain`·`escalate`·`clarify`) |
| `step.candidates` | multi-candidate comparison done | `stepId`, `candidates[]` `{candidateId, label?, sql, ok, rowCount?, score?, agreesWith?, stepId?}`, `chosenId` (null = none chosen), `method` (*open*: `vote`, `execution_agreement`, `verifier`, `judge`), `rationale?` |
| `escalation` | handed to a bigger model | `from` / `to` `{model, tier?, provider?}`, `reason`, `trigger` (`verifier_fail`·`low_confidence`·`repair_exhausted`·`complexity`·`disagreement`·`user`), `stepId?` |
| `reasoning.delta` / `reasoning.end` | optional thinking summary | `partId`, `delta` / `durationMs` |
| `text.start` / `text.delta` / `text.end` | streamed markdown | `partId`, `delta` |
| `code.delta` | streamed code part | `partId`, `delta` |
| `table.start` | a table starts streaming (1.9, §5.1) | `partId`, `columns`, `title?`, `sensitivity?`, `stepId?`, `sqlPartId?` |
| `table.rows` | preview rows for it | `partId`, `offset`, `rows` |
| `part` | a complete part (see §5) | `part` |
| `part.patch` | update a part | `partId`, `patch` |
| `message.end` | last | `messageId`, `finishReason`, `usage` (§7), `conversationTitle?` (§11) |
| `heartbeat` | every ≤15 s of silence | `at?`; no `seq`, no SSE `id` |

`finishReason`:

| value | meaning | required content |
|---|---|---|
| `stop` | answered | ≥1 `text` part; a `table` when the answer came from a query |
| `abstain` | can't / won't answer — a first-class **outcome, not an error** | exactly one `abstain` part, no `table` |
| `needs_input` | waiting for the user | exactly one `clarification` part |
| `error` | failed | exactly one `error` part |
| `aborted` | cancelled by the user (§8) | whatever streamed so far; partial parts are not persisted |

## 5. Parts (persisted message content)

Unchanged from the pre-v1 spec (docs/frontend-spec.md §5): `text`, `reasoning`, `status`, `tool_call`
(legacy, still valid; v1 backends prefer `step.*` events), `table`, `file`, `image`, `chart` (Vega-Lite,
inline `data.values`, never `data.url`), `code`, `callout`, `artifact`, `error`.

v1 adds `stepId?` to `tool_call`, `table` and `code` (link a result to the step that produced it), `truncated?` to `table`, and two parts:

* **`abstain`** — `category` (`out_of_scope`·`phi_request`·`data_unavailable`·`ambiguous`·`low_confidence`·`unsupported`·`policy`),
  `reason` (markdown), `details?`, `suggestions[]` `{id, text}` (≤6 rephrasings; clicking one sends it as a new question).
* **`clarification`** — `clarificationId`, `question`, `choices?[]` `{id, label, description?}` (≤8),
  `allowFreeText`, `multiSelect?`, `expiresAt?`, `answered?` (set on reload once replied).

**Final answer** = answer `text` + `code {role: "query", language: "sql"}` (the SQL used, collapsed by default,
available for inspection) + `table {columns, rows (preview ≤50), rowCount, csvFileId, sqlPartId}` (+ `file` chip when
`rowCount` > preview) + optional `chart` / `image` + optional `callout`s (assumptions, caveats, PHI redaction).
CSV: `GET /api/download/{csvFileId}` (auth, no-store, opaque id, short-lived).

### 5.1 Partial-table streaming (1.9.0)

A table's preview rows can stream before the table is complete: `table.start {partId, columns, title?, …}`, then
`table.rows {partId, offset, rows}` in order (10 rows each in demo), then the **complete `part`** with the same id.
That `part` is canonical (`rowCount`, `csvFileId`, `source`, …) and replaces the progressive one, which the client
upserts by id.

* `offset` is the index of the first row, so a replayed chunk after a `Last-Event-ID` resume overwrites and never
  duplicates.
* If `message.end` arrives before the complete part (aborted), drop the partial table. Partial tables are never
  persisted; a reload only ever sees complete parts.
* Clients older than 1.9 ignore both events (§3) and render the final `part` as before, so this is additive.
* Reference reducer: `reduceV1` in `web/src/api/contract.ts`, where `AssistantViewV1.streamingTables` lists the ids
  still streaming, for a "loading rows…" hint.
* A real backend can stream rows as the database returns them, with the same events. The preview cap (≤ 50 rows)
  still applies; the full result stays the CSV.

## 6. Clarifying questions and resume

1. The run ends with a `clarification` part and `finishReason: "needs_input"`. The server keeps the pending
   clarification (scoped to principal + conversation) for 30 minutes.
2. The user answers (a choice click or free text). The client sends a normal `POST /api/chat` with the same
   `conversationId`, the reply as the last user message (`text` = the choice label or typed text), and
   `resume: {clarificationId, choiceIds?, text?}`.
3. The server starts a **new** assistant message whose `message.start.resumeOf = {clarificationId, messageId}`
   (the message that asked). It continues the analysis with the original question + the answer.
4. Unknown/expired/already-answered `clarificationId` → HTTP 409 `{"error": {"code": "clarification_expired"}}`;
   the UI then just sends the text as a fresh question.

## 7. Usage, cost and latency (`message.end.usage`)

`queries`, `elapsedMs` (required); `tokens? {input, output, cacheRead?, cacheWrite?}`; `costUsd?` with
`costIsEstimate?` (always `true` in demo mode — computed from a static price table, never billed);
`latency? {firstEventMs?, firstStepMs?, firstTokenMs?, modelMs?, sqlMs?, queueMs?, totalMs, stages?}`;
`models?[] {model, role?, inputTokens, outputTokens, costUsd?, calls?}` (one row per model, so an escalated
answer shows both the small and the large model).

**Per-stage breakdown (1.6.0):** `latency.stages {plan, sql, repair, checks, render}` (`LatencyStages`, all integers
in ms, all five always present). It is wall clock: every instant of the run belongs to exactly one stage, so the
stages add up to about `totalMs`, and a stacked bar is safe to draw.

| stage | from → to |
|---|---|
| `plan` | run start, and each time a query returns OK → the next `step.sql_running` (model turns + schema exploration) |
| `sql` | `step.sql_running` (not a repair) → its `step.sql_result` |
| `repair` | a failed `step.sql_result` or a `step.check {verdict: fail}` → the repaired query's result (model + SQL) |
| `checks` | the final query's result → the checks are done (the `step.check`/`step.candidates` emitted after it) |
| `render` | `status "Writing answer"` → `message.end` |

Escalation time lands in the stage that was running (usually `repair`). With parallel candidates (`candidates`
strategy) the attribution follows the latest stage change, so treat the split as approximate. `modelMs`/`sqlMs`
remain *summed* durations and can exceed `totalMs` when candidates overlap. Example (`tool_steps.jsonl`, demo pace):
`{"plan": 1057, "sql": 8, "repair": 356, "checks": 1, "render": 61}` for `totalMs: 1481`. Unknown extra keys may
appear later (e.g. `queue`); clients ignore them.

## 8. Cancel, resume-after-disconnect, regenerate

* **Cancel:** `POST /api/chat/{messageId}/stop` → `{"stopped": true}` (idempotent; 404 `{"stopped": false}` for
  another user's or an unknown run). The stream finishes with `message.end {finishReason: "aborted"}` within 1 s.
  Before that `message.end`, the server **closes everything still in flight**: every `step.sql_running` without a result
  gets `step.sql_result {ok: false, error: {code: "cancelled"}}`, an open reasoning part gets `reasoning.end`, an open
  text part gets `text.end`. So a UI never has to guess which spinners to stop. Partial parts are not persisted.
* **Cancel while a query runs: needs analyzer support (documented 1.6.0; not built yet).** Today the server checks
  for a stop between model turns and during model latency, but it cannot interrupt a SQL statement that is already
  running: the analyzer runs it synchronously (`analyzer/tools.py: execute_with_time_limit`). Its watchdog can call
  `connection.interrupt()`, but only on the time limit (`AnalyzerConfig.time_limit_seconds`, default 25 s); the API
  has no hook into it. So during a SQL step, `message.end {aborted}` arrives when the query returns, at the latest
  after the time limit, not within 1 s. Clients should show "Stopping…" after `/stop` until `message.end`, and must
  not assume the 1 s bound. **Intended semantics, once the analyzer exposes a cancel hook** (e.g. a `cancelled`
  event checked by the watchdog, which then calls `connection.interrupt()`), which needs no client change:
  1. `/stop` interrupts the running statement immediately. Its step closes with
     `step.sql_result {ok: false, elapsedMs, error: {code: "cancelled", message: "Stopped by the user."}}`, the
     same shape as today's close-out.
  2. A `cancelled` error never triggers `step.repair_attempt`, `escalation` or a verifier `step.check`. It is not a
     query failure, and it does not count toward `maxAttempts`.
  3. Other candidates' statements are interrupted the same way, and each gets its own `cancelled` result.
  4. `message.end {finishReason: "aborted"}` follows within 1 s of `/stop`. `usage.queries` counts the interrupted
     statement, and `latency.stages.sql` includes its time up to the interrupt.
  5. A timeout keeps its own code (`timeout`, repairable); only a user stop is `cancelled`.
* **Disconnect ≠ cancel (1.1).** If the last attached client drops, the run keeps going for
  `CC_API_DISCONNECT_GRACE_SECONDS` (default 10 s). If any client re-attaches in that window the run continues;
  otherwise it is cancelled exactly as if `/stop` had been called. Several tabs may be attached to one run.
* **Reconnect:** `GET /api/chat/{messageId}/stream` with header `Last-Event-ID: <messageId>:<seq>` (preferred), or
  re-`POST /api/chat` with that header (1.0 behaviour, body ignored). The server replays every event with a larger
  seq, then follows the run live with heartbeats; available until 60 s after the run ends. Without `Last-Event-ID`
  the GET replays from seq 0. Clients still drop `seq <= lastSeq`, so overlapping replays are harmless.
  Recommended client loop: on a network error before `message.end`, retry the GET with the last seen id after
  250 ms, 1 s, 2 s, 4 s; on `stream_gone`, reload the conversation from `GET /api/conversations/{id}`.
* **Regenerate:** `ChatRequest.regenerate = {messageId, style?}` re-runs the user turn that produced `messageId`.
  The new message has `regenerateOf` set and is a sibling branch in the transcript, i.e. the next version of that
  answer.

### 8.1 Regenerate styles (1.10.0)

`regenerate.style` is `"default" | "concise" | "detailed"` (`RegenerateStyle`) and backs the "Retry / More concise /
More detailed" menu. The result **replaces the answer in place as a new version**, not as a follow-up turn:
`messages: []`, no new user message, and the reply is a sibling with `regenerateOf`.

| style | what changes | always kept |
|---|---|---|
| `default` (or absent) | nothing; a plain re-run | everything |
| `concise` | prose cut to its first sentence; a `chart` that plots the same rows as a `table` is dropped | tables, SQL, artifacts, clarifications, errors, **all assumption/caveat callouts** |
| `detailed` | adds a **"How this was computed."** paragraph to the last text part: result shape (rows, column labels), where the SQL is, the number of assumptions and caveats | every part of the default answer |

* `message.start.regenerateStyle` and `StoredMessage.regenerateStyle` echo a non-default style, so the version
  switcher can label it ("Concise").
* The paragraph is built from part metadata only (labels, counts), never from row values. Abstain, clarify and error
  answers are not restyled.
* An unknown style is a 422 `validation` (`Invalid request: regenerate.style.`). Servers older than 1.10 ignore
  `style` and do a plain re-run, so the request is safe to send.
* It runs on the server for every backend (demo, sim, prod; `api/styles.py`). A model backend may also use the
  style as a prompt hint later; the shape above is what's promised.
* Client: `regenerateRequest(conversationId, messageId, style)` and `REGENERATE_STYLES` in
  `web/src/contract/regenerate.ts`. Example: `examples/regenerate_concise.jsonl`, "More concise" on `answer.jsonl`.
* **Edit (1.2):** send the edited text as the user message with `editOf: <id of the user message being edited>`.
  The server stores it as a sibling of that message: it copies the edited message's `parentId`, and when there is none
  (editing the **first** question) it stores no `parentId`, which makes it a new root. The stored user message keeps
  `editOf`, and the answer's `message.start.editOf` echoes it. An unknown `editOf` in this conversation returns 404
  `validation`. Folding rule for clients: a stored message with `editOf` and no `parentId` is a root; any other message
  without `parentId` follows the message before it. The 1.0 form, a user message with `parentId` = the parent of the
  edited message, still works for non-first questions.

## 9. Demo scenarios (demo mode only)

`ChatRequest.scenario` (or keywords in the question) forces a scripted run on synthetic data:
`table`, `chart`, `repair`, `abstain`, `clarify` (then resume), `escalation`, `candidates`, `error`, `slow`,
`report` (1.9: "report"/"artifact" in the question; chart + report artifacts, §16).
Ignored outside demo mode. No paid API is ever called.

Keywords (demo): "denial/reason codes, CARC" → repair · "appeals, prior auth, forecast" or patient identifiers → abstain ·
"how bad are…" → clarify · "net collection rate" → escalation · "average paid" → candidates · "trend/by month" → chart ·
"simulate an error" → error · "slow" → slow. Everything runs through the real `Analyzer` loop with `ScriptedLLM`
(`api/backends.py:scenario_turn`); steps are observed live by `api/live.py:LiveTap`, which wraps the LLM, so the real
analyzer will stream the same events when it plugs in.

Realistic timing for UI work: `CC_API_MODEL_DELAY_SECONDS=0.6 CC_API_STREAM_DELAY_SECONDS=0.03 uvicorn api.app:app --port 8101`.

Canonical example streams for every outcome (answer, tool steps/repair, abstain, clarify + resume, escalation,
chart, candidates, error) live in [`api/contract/examples/`](contract/examples/README.md), one event per line, regenerated
from the demo backend by `python -m api.contract.gen_examples` and checked by `api/tests/test_contract_examples.py`.

## 10. PHI and security rules

* Step events never carry row values: `step.sql_result` has counts and column names/types only.
  Row values appear only in `table.rows` (preview), `chart.data.values` and the CSV download.
* No row data, question text or SQL in server logs; the audit log is an allowlist of ids, counts and timings.
* No PHI in URLs: only opaque ids in paths; never query strings with data.
* The client never writes rows, SQL results, step payloads or message text to `localStorage`, `sessionStorage`
  or IndexedDB. Transcripts persist only through the server transcript store. UI prefs (theme, density) are fine.
* API responses (every route, errors and SSE included; `api/security.py`, tested in `test_security_headers.py`):
  `Cache-Control: no-store`, `Pragma: no-cache`,
  `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; sandbox`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`,
  `Permissions-Policy` locked down, `Cross-Origin-Opener-Policy`/`Cross-Origin-Resource-Policy: same-origin`,
  HSTS; no `Server` header; no `/openapi.json` or `/docs`.
* Web app CSP (`web/src/security/headers.ts`; applied by `vite dev` and `vite preview`, and production hosting must
  send the same) is a **per-response nonce** policy: `default-src 'self'`; `script-src 'self' 'nonce-<n>'` (no
  `'unsafe-inline'`, no `'unsafe-eval'`, no `'wasm-unsafe-eval'`: Vega runs on its AST interpreter and Shiki on its JS
  regex engine); `style-src` / `style-src-elem 'self' 'nonce-<n>'`; `style-src-attr 'unsafe-inline'` only (style
  *attributes* from Shiki/KaTeX/Vega); `img-src 'self' data: blob:`; `font-src 'self' data:`; `connect-src 'self'`
  (dev adds `ws: wss:` for HMR); `worker-src`/`media-src 'self' blob:`; `object-src`/`frame-src 'none'`;
  `base-uri 'none'`; `form-action 'self'`; `frame-ancestors 'none'`; `upgrade-insecure-requests` outside dev.
  The server replaces `__CC_CSP_NONCE__` in `index.html` with a fresh nonce per HTML response; dev uses one nonce per
  server start. No inline `<script>` or `<style>` without the nonce. Tests that inject CSS must use constructed
  stylesheets (`document.adoptedStyleSheets`), not `addStyleTag`.
* `error.details` and `step.*.error.message` are scrubbed (quoted literals and long digit runs masked) and ≤500 chars.

## 11. Conversation history (REST)

All JSON, `Cache-Control: no-store`, scoped to the signed-in user: another user's conversation is a 404, never a 403.
Shapes are schema `$defs`, so the TS types are generated and gated like the stream (`web/src/contract/compat.test.ts`
checks `web/src/adapters/conversationsApi.ts` against them).

| Route | Body | Response |
|---|---|---|
| `GET /api/transcripts` | | `TranscriptsInfo {mode: "ephemeral"\|"persisted", persisted, retentionDays}` |
| `PUT /api/transcripts/retention` | `{days}` | `{mode, retentionDays}` |
| `GET /api/conversations?cursor=` | | `ConversationPage {items: ConversationSummary[], next?}` |
| `POST /api/conversations/search` | `SearchRequest {q, cursor?}` (POST: the text may be PHI) | `ConversationPage`, items carry `snippet` |
| `GET /api/conversations/{id}` | | `Conversation {id, title, updatedAt, messages: StoredMessage[]}` |
| `PATCH /api/conversations/{id}` | `RenameRequest {title}` | `RenameResponse {id, title, updatedAt}`; 422 `invalid_title` |
| `DELETE /api/conversations/{id}` | | `{deleted: true}`, or 404 `{deleted: false}` |
| `DELETE /api/conversations` | | `{deleted: <count>}` |

Errors (`HttpError`): `not_found` (404), `invalid_title` (422), `transcripts_unavailable` (410: the customer key was destroyed, so the history is gone).

**`StoredMessage`.** User messages carry `text`, `parentId?` and `editOf?`. Assistant messages carry `parts`,
`finishReason`, `usage`, `regenerateOf?` and `steps`. **`steps` are the flat `step.*` / `escalation` events in stream
order, and that form is canonical**: rebuild the timeline with the same reducer as the live stream (`reduceV1`), so
live and reloaded views cannot diverge. An aborted answer is stored with no parts and no steps.

**Titles.** A conversation starts as `"New analysis"`. A question can hold PHI, so the server never echoes it as a
title. On the first finished answer (`finishReason: "stop"`) of a conversation that still has the generic title, the
server derives one from the result's **column identifiers** only (`api/titles.py`: "Paid amount by payer",
"Claims by CARC code"), stores it, and sends it as `message.end.conversationTitle`. The derivation fails closed: a
PHI column, a non-identifier, or any word outside a fixed analytics vocabulary means no title. A user rename always
wins and is never overwritten. Clients show `conversationTitle` right away and can keep their own local title only
while the server title is still the generic one.

## 12. Capabilities: strategies and models (`GET /api/capabilities`)

A picker reads what the server can run. The body is `Capabilities` (no user data, `no-store`, fetch once per session):

```json
{"contractVersion": "1.4.0", "demo": true, "defaults": {"strategy": "tool_loop", "model": "auto"},
 "strategies": [{"id": "tool_loop", "label": "Standard", "description": "…", "default": true, "costTier": "low",
                 "latencyTier": "fast", "models": ["auto", "claude-haiku-5-5", "claude-sonnet-5-5"], "available": true}, …],
 "models": [{"id": "auto", "label": "Auto", "default": true, "costTier": "low"},
            {"id": "claude-haiku-5-5", "label": "Haiku", "tier": "small", "provider": "anthropic", "costTier": "low", "default": false}, …]}
```

* Send the choice as `ChatRequest.options.strategy` / `options.model`. Omit them for the defaults. An unknown id, or a
  model that is not in the strategy's `models`, is a 422 `validation` before anything runs. `message.start.strategy`
  echoes the strategy; `message.start.model` stays the model that actually ran.
* **Per organization (1.5.0).** Signed in, the response has `scope: "organization"` and each strategy and model has
  `available` for the caller's organization, plus `unavailableReason` when it is false: show those disabled, with
  the reason as a tooltip. Sending one anyway is a 422 `validation` with the same reason. Signed out:
  `scope: "public"`, everything shows available. Demo policy (`api/capabilities.py:ORG_POLICY`, or
  `CC_API_ORG_POLICY` as JSON): `acme_diagnostics` has no Cross-check and no Sonnet; other orgs get everything.
* `costTier` (`low|medium|high`) and `latencyTier` (`fast|medium|slow`) are relative badges, not prices; the real cost
  of an answer is `message.end.usage.costUsd` (§7). `available: false` means show it disabled.
* Demo strategies (`api/capabilities.py`): `tool_loop` (Standard), `cascade` (Escalate when stuck: Haiku, then Sonnet),
  `candidates` (Cross-check: 3 candidate queries, execution vote). Each maps onto real `AnalyzerConfig` overrides, so
  the real analyzer plugs in by reading that table. With the scripted demo LLM (`demo: true`) the choice is validated
  and echoed, but the demo scenario (§9) still drives the run.
* Offline / mock mode: `web/src/contract/capabilities.ts` has `loadCapabilities()` (falls back to `DEMO_CAPABILITIES`,
  kept equal to `api/contract/examples/capabilities.json` by a test), `modelsFor()`, `defaultStrategy()` and
  `runOptions()` (builds `options`, omitting defaults).

## 13. Answer feedback (1.7.0)

Thumbs up/down on an **assistant** message, with optional reason chips and a comment.

* `PUT /api/conversations/{conversationId}/messages/{messageId}/feedback` with body `FeedbackRequest
  {rating: "up"|"down", reasons?: string[], comment?: string ≤ 1000}`. It returns `Feedback {messageId, rating,
  reasons, comment?, updatedAt}`. A later PUT replaces the earlier one, so changing your mind is just another PUT.
* `DELETE` on the same path clears it: `{"cleared": true}`.
* The stored value comes back on `StoredMessage.feedback` in `GET /api/conversations/{id}`, so a reloaded
  conversation shows the thumbs state.
* Reason chips: `Capabilities.feedbackReasons[] {id, label, rating}` (demo: up `correct`, `clear`, `fast`; down
  `wrong_numbers`, `wrong_query`, `misunderstood`, `missing_data`, `too_slow`, `other`). Ids are stable; show the
  label. Each reason must belong to the rating you send.
* Errors:
  * 422 `validation`: unknown reason, a reason of the other rating, a comment over 1000 characters, or a user
    message id.
  * 404 `not_found`: unknown message or conversation, or someone else's.
  * 401 when signed out.
  * 410 when the org's transcripts are shredded (same as §11).
* **PHI:** the comment is user text, treated exactly like a message. It is stored (sealed) with the transcript,
  never logged, and never in a URL. The audit event `answer_feedback` carries only the rating (`kind`) and the reason
  ids (`reason`). Ephemeral transcript mode keeps feedback only as long as the conversation.
* Example: `examples/feedback.json`. Client: `web/src/contract/feedback.ts` (`sendFeedback`, `feedbackUrl`) and
  `feedbackReasonsFor(capabilities, rating)`.

## 14. Citations: data sources (1.8.0)

`message.end.sources` (and the same list on `StoredMessage.sources`) names the data an answer used. It is built from
what the run already emitted (SQL steps, explore steps, the answer's SQL and table), so it costs nothing extra. It
holds schema identifiers only, never row values.

```json
{"id": "src_1", "kind": "view", "name": "sem.claims", "label": "Claims", "role": "answer",
 "description": "One row per claim submission with billed, allowed and paid amounts, payer and status.",
 "stepIds": ["s_1", "s_2"], "partIds": ["p_2", "p_3", "p_4"]}
```

* `role: "answer"`: the final SQL/table read it. `"explored"`: only looked at while planning. Answer sources come
  first. An abstain or clarify can carry explored sources only; aborted runs carry none. The field is omitted when
  empty.
* `partIds` link to the `code` (SQL), `table` and `chart` parts it fed, so each table can show its own "Sources"
  chips. `stepIds` link to step rows (`step.sql_running`, `step.explore`).
* `kind` is an *open* enum: `view` today; `definition` (metric definitions) and `document` are reserved. Show an
  unknown kind as a plain chip with `label`.
* `TablePart.source.views` (1.0) stays; `sources` is the richer, message-level list.
* Inline citation markers inside text (e.g. `[1]`) are **not** in this version. When added, they will reference
  `Source.id`.
* Client helpers: `web/src/contract/sources.ts` (`answerSources`, `sourcesForPart`, `sourcesForStep`).

## 15. Export (1.8.0)

`GET /api/conversations/{id}/export?format=markdown|json` (default `markdown`) downloads your own conversation.

* `markdown` (`text/markdown`): the title, then each question and answer: the text, the SQL (fenced), table
  previews (≤ 50 rows, cells escaped, with a "Showing n of N rows" note), a chart's alt text, callouts,
  abstain/clarify text, the answer's sources, and your feedback. Reasoning, status and tool parts are left out.
* `json` (`ConversationExport`): `{format: "claimclarity.conversation", contractVersion, exportedAt, conversation}`,
  where `conversation` is exactly `GET /api/conversations/{id}`.
* Both are attachments with `Content-Disposition: attachment; filename="claimclarity_conversation_<yyyy-mm-dd>.<md|json>"`.
  The title never appears in the filename, because a user rename could hold anything. `Cache-Control: no-store`.
* Errors: 422 `validation` for any other format; 404 for an unknown or someone else's conversation; 401 when
  signed out; 410 when shredded. Audit event `conversation_exported` carries the format only.
* Fetch it with the auth header and save the blob (`exportUrl`, `exportFilename` in `web/src/contract/sources.ts`).
  Never put the token in the URL. Example: `examples/export.md`.
* The full table result is still the CSV download (`csvFileId`). Sharing is a separate, later version.

## 16. Artifacts and plots (1.9.0)

**Plots** are `chart` parts: Vega-Lite only, with inline `data.values` (never `data.url`) and a required `alt`. The
server never sends script. `chart.tablePartId` (1.9) names the table part it was drawn from, so "show data" can
scroll to it and sources (§14) can be shared. `staticImageBlobId?` is a PNG for export and print. A client may
re-encode a chart (bar ↔ line, sort) locally from `data.values`; that is a view change, not a new version.

**Artifacts** are `artifact` parts: openable objects shown in the side panel.
`{artifactId, version, kind, title, content, autoOpen?}`:

* `kind` is `table`, `chart`, `report` or `code`/`image`. `content` is the matching part, or `{markdown}` for a
  `report`.
* **Lifecycle.** An artifact arrives complete, as one `part` event. A **new version** is a new `artifact` part (new
  part id) with the same `artifactId` and `version + 1`, in the same message or a later one, for example after "make
  it a line chart". The panel shows the highest version. Earlier versions stay in the transcript as "v1, v2" chips.
  `part.patch` is only for metadata (`title`, `autoOpen`), never for `content`.
* `autoOpen: true` asks the client to open the panel once, when the part first arrives live, not on reload. Never
  steal focus while the user is typing.
* PHI: artifact content follows the same rules as the part it wraps (`sensitivity`, redacted identifier columns).
  Reports are built from the aggregate preview only.
* Streaming artifact content (e.g. a long report as deltas) is **reserved** and not in 1.9. When added, it will use
  `artifact.delta {partId, delta}`, ignorable under §3.
* Demo: the `report` scenario returns the table, a `chart` artifact (`autoOpen: false`) and a `report` artifact
  (markdown summary, `autoOpen: true`). Example: `examples/report.jsonl`.

## 17. Share inside the organization (1.11.0)

A share is a **read-only, org-scoped, revocable** link to one conversation. There are no public links: the viewer
must be signed in to the same organization (customer). A conversation has at most one active share.

| Request | Body | Response |
|---|---|---|
| `GET /api/conversations/{id}/share` | | `ShareLink`, or 404 `not_found` when not shared |
| `PUT /api/conversations/{id}/share` | `ShareRequest {includeData?: false}` | `ShareLink {shareId, conversationId, url, scope: "organization", includeData, createdAt}`. Idempotent: it returns the same `shareId` and updates `includeData` |
| `DELETE /api/conversations/{id}/share` | | `{revoked: true}`, or 404 when there was none |
| `GET /api/shared/{shareId}` | | `SharedConversation {shareId, title, updatedAt, includeData, createdAt, messages: StoredMessage[]}` |

* Only the owner can manage a share. Anyone else gets a 404, like any other user's conversation (§1).
* The viewer gets **404 `not_found`** for an unknown, revoked, other-organization or deleted-conversation share
  alike, so a link reveals nothing. 401 when signed out, 410 `transcripts_unavailable` after crypto-shredding.
  Deleting the conversation revokes its share.
* **Data is withheld by default.** With `includeData: false`, tables keep `columns` and `rowCount`, but `rows: []`
  and `dataWithheld: true` (`TablePart.dataWithheld`, 1.11). `chart` parts and `table`/`chart`/`report`/`image`
  artifacts are left out. Prose, SQL, steps, callouts and sources stay. With `includeData: true`, the stored preview
  rows (≤ 50 per table) are shared. **Never shared:** the CSV (`csvFileId` is removed), `feedback`, and the owner's
  identity.
* `url` is the app path `/shared/<shareId>`. The share id has 144 random bits and is a capability inside the org:
  don't log it. The response is `no-store` with `Referrer-Policy: no-referrer`.
* UI: the share dialog defaults to "Without result data", with an opt-in toggle. Show `rowCount` and "data not
  shared" for withheld tables. The view is read-only, with no composer and no regenerate.
* Audit: `conversation_shared` / `conversation_share_revoked` / `shared_conversation_viewed`, with `kind` =
  `with_data`|`no_data` and never the share id.
* Demo: shares are kept in memory (they are lost on restart). A real backend persists them with the transcript.
* Client: `getShare`, `setShare`, `revokeShare`, `loadShared` and `shareIdFromPath` in `web/src/contract/share.ts`.
  Example: `examples/share.json`.

## Changelog

* **1.11.0** (2026-10-25) — Org-scoped share links (§17): `ShareRequest`, `ShareLink`, `SharedConversation`,
  `TablePart.dataWithheld`, and GET/PUT/DELETE `/api/conversations/{id}/share` plus `GET /api/shared/{shareId}`.
  `includeData` defaults to false. Additive.

* **1.10.0** (2026-10-25) — `ChatRequest.regenerate.style` (`RegenerateStyle`), echoed as
  `message.start.regenerateStyle` and `StoredMessage.regenerateStyle` (§8.1). Titles (QA-P6): a dimension ending in
  `name` reads as its entity in `conversationTitle` and in chart `title`/`alt` ("Paid amount by payer", not "by payer
  name"). Column and axis labels keep "Payer name", and a lone `name` stays. Example streams regenerated. Docs: CSRF
  header `x-cc-csrf: 1` for cookie-session writes (#198), and 422 always uses the `HttpError` envelope (#186) (§1).
  Additive.

* **1.9.0** (2026-10-25) — Partial-table streaming: `table.start` / `table.rows` (§5.1; the complete `part` stays
  canonical). Artifact and plot spec (§16): `ChartPart.tablePartId`, versioning, `autoOpen`; demo `report`
  scenario. Additive.

* **1.8.0** (2026-10-25) — Citations: `Source`, `message.end.sources`, `StoredMessage.sources` (§14). Export:
  `GET /api/conversations/{id}/export?format=markdown|json`, `ConversationExport` (§15). Additive.

* **1.7.0** (2026-10-25) — Answer feedback (§13): the `PUT`/`DELETE …/messages/{id}/feedback` endpoints,
  `FeedbackRequest`, `Feedback`, `StoredMessage.feedback`, `Capabilities.feedbackReasons`. Additive.

* **1.6.0** (2026-10-25) — `usage.latency.stages` (`LatencyStages`: plan/sql/repair/checks/render, wall-clock ms;
  §7). §8 documents that cancelling a running SQL statement needs analyzer support, and gives the intended event
  semantics. Additive.

* **1.5.0** (2026-10-25) — 413 has its own code `payload_too_large` (+ `maxBytes`; `legacyCode: "validation"` kept);
  `HttpError.error.legacyCode` convention. Capabilities are per organization when signed in (`scope`,
  `available` on models, `unavailableReason`), and the chat request enforces them. Additive.

* **1.4.0** (2026-10-25) — `GET /api/capabilities` (`Capabilities`, `StrategyOption`, `ModelOption`, `CostTier`) and
  typed `ChatRequest.options.{strategy, model}` with 422 validation (§12). Additive; requests without options are
  unchanged. Docs: §1 error table now lists 410, 413 (1 MiB body cap) and 429 `rate_limited` (3 concurrent runs,
  `Retry-After`); §10 describes the nonce CSP from #98/#139. Example streams regenerated (upper-case acronym labels).

* **1.3.0** (2026-10-25) — `message.end.conversationTitle` (PHI-free server title, §11); conversation REST shapes as
  schema `$defs` (`ConversationSummary`, `ConversationPage`, `Conversation`, `StoredMessage`, `TranscriptsInfo`,
  `RenameRequest`, `RenameResponse`, `SearchRequest`) with a TS drift gate; flat stored `steps` documented as
  canonical. Additive.

* **1.2.0** (2026-10-25) — `editOf` on the ChatRequest user message and on `message.start`, so an edit of the first
  question (no parent) can be expressed. Additive.

* **1.1.0** (2026-10-25) — transport + tooling, additive. `GET /api/chat/{messageId}/stream` resume (EventSource
  friendly); a dropped connection no longer cancels immediately (10 s re-attach grace); cancel closes in-flight steps
  (`step.sql_result` `error.code: "cancelled"`) and reasoning; `stream_gone` error code; TS types generated from the
  schema with a compile-time drift gate. Schema fixes to match the documented shapes: `artifact.content` is one of
  table/chart/code/image part or `{markdown}`; `ChatRequest.scenario` is the documented enum.

* **1.0.0** (2026-10-25) — first versioned contract. Adds `seq`, `contractVersion`, `step.*` events,
  `escalation`, `abstain` and `clarification` parts, `finishReason` `abstain`/`needs_input`, rich `usage`,
  `ChatRequest.resume`/`regenerate`/`scenario`, `stepId` links. Fully backwards compatible with the pre-v1
  UI reducer (it ignores the new events).
