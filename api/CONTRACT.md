# ClaimClarity chat API — stream contract

**Version: 1.1.0** (semver) · machine-readable schema: [`api/contract/v1.schema.json`](contract/v1.schema.json) ·
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
* Non-stream failures (401, 404, 409, 422) return `HttpError` JSON: `{"error": {"code", "message"}}`. Codes the UI
  should branch on: `auth_expired` (401), `access_denied` (404), `stream_gone` (404: nothing to resume, the run
  finished > 60 s ago or never existed), `clarification_expired` (409), `validation` (422).
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
| `message.start` | first | `messageId`, `conversationId`, `contractVersion`, `model?`, `strategy?`, `parentId?` (user turn), `resumeOf?` `{clarificationId, messageId}`, `regenerateOf?` |
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
| `part` | a complete part (see §5) | `part` |
| `part.patch` | update a part | `partId`, `patch` |
| `message.end` | last | `messageId`, `finishReason`, `usage` (§7) |
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
`latency? {firstEventMs?, firstStepMs?, firstTokenMs?, modelMs?, sqlMs?, queueMs?, totalMs}`;
`models?[] {model, role?, inputTokens, outputTokens, costUsd?, calls?}` (one row per model, so an escalated
answer shows both the small and the large model).

## 8. Cancel, resume-after-disconnect, regenerate

* **Cancel:** `POST /api/chat/{messageId}/stop` → `{"stopped": true}` (idempotent; 404 `{"stopped": false}` for
  another user's or an unknown run). The stream finishes with `message.end {finishReason: "aborted"}` within 1 s.
  Before that `message.end`, the server **closes everything still in flight**: every `step.sql_running` without a result
  gets `step.sql_result {ok: false, error: {code: "cancelled"}}`, an open reasoning part gets `reasoning.end`, an open
  text part gets `text.end`. So a UI never has to guess which spinners to stop. Partial parts are not persisted.
* **Disconnect ≠ cancel (1.1).** If the last attached client drops, the run keeps going for
  `CC_API_DISCONNECT_GRACE_SECONDS` (default 10 s). If any client re-attaches in that window the run continues;
  otherwise it is cancelled exactly as if `/stop` had been called. Several tabs may be attached to one run.
* **Reconnect:** `GET /api/chat/{messageId}/stream` with header `Last-Event-ID: <messageId>:<seq>` (preferred), or
  re-`POST /api/chat` with that header (1.0 behaviour, body ignored). The server replays every event with a larger
  seq, then follows the run live with heartbeats; available until 60 s after the run ends. Without `Last-Event-ID`
  the GET replays from seq 0. Clients still drop `seq <= lastSeq`, so overlapping replays are harmless.
  Recommended client loop: on a network error before `message.end`, retry the GET with the last seen id after
  250 ms, 1 s, 2 s, 4 s; on `stream_gone`, reload the conversation from `GET /api/conversations/{id}`.
* **Regenerate:** `ChatRequest.regenerate = {messageId}` re-runs the user turn that produced `messageId`; the new
  message has `regenerateOf` set and is a sibling branch in the transcript.
* **Edit:** send the edited text as a user message with `parentId` = the parent of the edited message; the server
  stores it as a sibling branch.

## 9. Demo scenarios (demo mode only)

`ChatRequest.scenario` (or keywords in the question) forces a scripted run on synthetic data:
`table`, `chart`, `repair`, `abstain`, `clarify` (then resume), `escalation`, `candidates`, `error`, `slow`.
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
  `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`,
  `Permissions-Policy` locked down, `Cross-Origin-Opener-Policy`/`Cross-Origin-Resource-Policy: same-origin`,
  HSTS; no `Server` header; no `/openapi.json` or `/docs`.
* Web app (`web/src/lib/securityHeaders.ts`, applied by `vite dev` and `vite preview`; production hosting must
  send the same): `script-src 'self'` (no inline script, no eval; charts use Vega's CSP-safe interpreter,
  `ast: true`), `style-src 'self' 'unsafe-inline'` (style attributes from Shiki/Vega/KaTeX), `connect-src 'self'`,
  `img-src 'self' data: blob:`, `object-src 'none'`, `frame-ancestors 'none'`, plus the same no-store and
  hardening headers. Dev only adds `'unsafe-inline'` scripts (React refresh preamble) and `ws:` for HMR.
  Don't add inline `<script>` to index.html; put it in `web/public/` instead.
* `error.details` and `step.*.error.message` are scrubbed (quoted literals and long digit runs masked) and ≤500 chars.

## Changelog

* **1.1.0** (2026-10-25) — transport + tooling, additive. `GET /api/chat/{messageId}/stream` resume (EventSource
  friendly); a dropped connection no longer cancels immediately (10 s re-attach grace); cancel closes in-flight steps
  (`step.sql_result` `error.code: "cancelled"`) and reasoning; `stream_gone` error code; TS types generated from the
  schema with a compile-time drift gate. Schema fixes to match the documented shapes: `artifact.content` is one of
  table/chart/code/image part or `{markdown}`; `ChatRequest.scenario` is the documented enum.

* **1.0.0** (2026-10-25) — first versioned contract. Adds `seq`, `contractVersion`, `step.*` events,
  `escalation`, `abstain` and `clarification` parts, `finishReason` `abstain`/`needs_input`, rich `usage`,
  `ChatRequest.resume`/`regenerate`/`scenario`, `stepId` links. Fully backwards compatible with the pre-v1
  UI reducer (it ignores the new events).
