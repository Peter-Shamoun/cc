# CC chat API (`api/`)

FastAPI service that runs the `analyzer` tool loop and streams the result to the React chat
(`web/`) as Server-Sent Events. The wire format is the stream contract in the frontend spec,
section 5, mirrored exactly by `web/src/lib/contract.ts`.

```
pip install -e . && pip install -r api/requirements.txt
uvicorn api.app:app --port 8787            # demo mode, offline scripted model
PYTHONPATH=. pytest api/tests -q           # 22 tests, no network, no paid APIs
```

## Modes

| Mode | Auth | Data path | Use |
|---|---|---|---|
| `demo` (default `app`) | `Authorization: Bearer demo:<customer>:<user>` (customers `acme_labs`, `acme_diagnostics`) | Synthetic, PHI-free `sem.claims` in DuckDB, one per customer | UI development |
| `sim` | `Authorization: Bearer <Cognito id token>` (or `cc_session` httpOnly cookie) from the `ccsim` mock | `ccsim.query.open_session` via `analyzer.opener.QueryPathOpener`: Cognito verify → STS AssumeRoleWithWebIdentity (900 s) → CURRENT → manifest → scoped DuckDB | Integration, `create_app(auth=SimAuth(world.aws, world.dep))` |

The analyzer sees `sem.*` by default. When `sem.*` cannot answer it may call `request_escalation(reason)` (CLI:
`escalate "<reason>"`): `/api/chat` wraps every opener in `ReasonedEscalationOpener`, which refuses a reasonless
escalation, re-opens the session for the token's customer with the PHI-stripped `lossless.*` mounted, and audits
`data_escalation` (customer, user, masked reason). The step timeline shows it as a `step.explore` with probe
`request_escalation` and summary `Reason: <masked reason>` (the web labels it "Used the full EDI data").

## Model backend

`CC_API_LLM=scripted` (default) is `analyzer.llm.ScriptedLLM` driven by `api.backends.demo_turn`:
describe `sem.claims`, run one query chosen by keyword (monthly trend, denial rate by payer,
paid by payer, claim listing, overall totals), answer with assumptions, or refuse requests for
patient identifiers. `anthropic` and `bedrock` construct the real clients, which still raise
`PaidAPIDisabled`; the stream then ends with an `error` part ("Model unavailable") and
`finishReason: "error"`. Wiring a real model means passing `allow_paid_api=True` in code, not config.
`create_app(llm_factory=...)` injects any other backend (tests use it for replay scripts).

Analyzer settings come from `Settings.analyzer_overrides` (default: basic glossary, no
dictionary/profile prompt, `row_limit` 5000, 1 candidate, no cascade).

## Endpoints

### `POST /api/chat` → `text/event-stream`

Body: `{ "conversationId"?: ID, "messages": [{ "id"?: ID, "role": "user", "text": string }], "options"?: { "strategy"?: string } }`.
The last user message is the question. No PHI in the URL. Responses carry `Cache-Control: no-store`.

Each event is `id: <messageId>:<seq>` plus `data: <StreamEvent JSON>`. Heartbeats
(`{"type":"heartbeat"}`, no id) every `CC_API_HEARTBEAT_SECONDS` (15). Reconnect by POSTing again with
`Last-Event-ID: <messageId>:<seq>`; events after `seq` are replayed from an in-memory buffer kept 60 s
after the run ends (same principal only).

Event sequence:

```
message.start {messageId, conversationId, model, strategy}
status {label: "Analyzing"}
part tool_call ...            one per analyzer step (describe_view, run_sql, request_escalation, ...)
text.start / text.delta* / text.end   the answer prose (markdown)
part code                     the final SQL (language sql, role query, collapsed)
part table                    result: columns typed, ≤ 50 preview rows, rowCount, csvFileId/resultId, sqlPartId, source.views
part file                     only when rowCount > 50: text/csv chip with the same opaque id
part chart                    Vega-Lite (data inline under name "result", never data.url) for a time series (line) or ≤ 50-row categorical (bar); not for row-level listings
part callout assumption       the analyzer's stated assumptions (editable)
part callout caveat           result-check warnings
part callout phi_redaction    which identifier columns were redacted
status {label: null}
message.end {messageId, finishReason: stop|error|aborted, usage: {queries, elapsedMs}}
```

Refusals and clarifications are a single `text` part. A failed analysis is an `error` part with a
PHI-scrubbed `details` (quoted literals and long digit runs masked).

Column typing: dates/datetimes from values; `*amount|paid|charge|…` → `currency` (`$,.2f`);
`*rate|pct|share|ratio` → `percent` (`.1%`); `*code|carc|cpt|icd` → `code`; PHI names (the
analyzer's `is_phi_column`) get `phi: true`. Tables holding a PHI column are `sensitivity: "limited"`,
others `"deidentified"`.

### `POST /api/chat/{messageId}/stop`
Marks the run cancelled; remaining parts are dropped and the stream ends with `finishReason: "aborted"`.
(The DuckDB statement itself is bounded by the analyzer's `time_limit_seconds` watchdog.)

### `GET /api/download/{id}` → `text/csv`
The full result for a `csvFileId`/`fileId`. Bound to the principal (customer + user) that produced it,
expires after 15 min, `Content-Disposition: attachment; filename="result.csv"` (never PHI in names).
Every download writes an audit event `{event: "download", customer, user, file_id, row_count}`; misses
write `download_denied`. Redacted columns stay empty in the CSV.

### `GET /api/health`
`{"status": "ok", "mode": "demo"|"sim", "llm": "scripted", "paidApis": false}`.

### Conversations (`TranscriptStore`)
`GET /api/conversations[?cursor=]`, `GET /api/conversations/{id}`, `DELETE /api/conversations/{id}`.
`api.store.TranscriptStore` is the spec 6.4 interface (`list_conversations`, `get_conversation`,
`append_message`, `delete_conversation`, `set_retention`), scoped by principal.
`InMemoryTranscriptStore` is the `ephemeral` design (nothing at rest). Titles are generic
("New analysis") because a question can contain PHI; the scrubbed-title generator and the
persistent designs (bc-af8d3012) plug in behind the same interface.

## PHI and logging

* Rows never reach logs. The only log lines are audit records filtered through an allowlist
  (`customer, user, file_id, row_count, conversation_id, message_id, parts, queries, elapsed_ms, finish, …`);
  no SQL text, question text or cell values. Exceptions log the type name only.
* PHI columns are nulled by the analyzer's `_redact` before mapping and flagged `phi: true`.
* Tool-call parts carry SQL and relation names, never row previews.
* Tests: `api/tests/test_security.py` (redaction, owner-bound audited downloads, tenant isolation,
  no escalation, paid APIs disabled, no data in logs), `test_sim_path.py` (end to end through the
  Cognito/STS mock with a spy proving `escalate` is always false).

## Not yet

Real model behind config with spend limits; streaming tool steps live (parts are emitted after the
loop finishes; heartbeats keep the stream open meanwhile); `image` and `artifact` parts; server-paged
`resultId` access; the persistent transcript stores.
