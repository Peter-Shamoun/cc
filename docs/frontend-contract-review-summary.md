# CC chat contract — review summary for Claude (v1.0 → v1.15.0)

One page. Sources: `api/CONTRACT.md` (spec and changelog), `api/contract/v1.schema.json` (machine truth),
`docs/frontend-contract-migration.md` (per-version migration), `docs/frontend-analyzer-adapter.md` (real analyzer).
State on 2026-10-25: **1.15.0**, all changes additive, demo mode only (ScriptedLLM), **no paid API calls**.

## What the contract is
An SSE stream (`POST /api/chat`) of `message.start` → `step.*` / `part` / `table.*` / `artifact` → `message.end`, with
`seq`, `contractVersion` and §2 ordering rules (machine-checkable as `stream_rules.json`, with Python + TS runners),
plus REST for conversations, capabilities, feedback, export and share. TS types are generated from the schema
(`gen_ts --check` is a drift gate); every example stream is conformance-checked.

## v1.0 → v1.15 at a glance
| Ver | Theme | Ver | Theme |
|---|---|---|---|
| 1.0 | versioned stream, steps, abstain/clarify/escalation | 1.8 | citations (`sources`), export |
| 1.1 | resume stream, 10 s re-attach grace, cancel semantics, TS gen | 1.9 | partial tables, artifacts/plots |
| 1.2 | `editOf` | 1.10 | regenerate styles, PHI-free titles |
| 1.3 | conversation REST shapes, server titles | 1.11 | org-scoped share links (data off by default) |
| 1.4 | capabilities, strategy/model options | 1.12 | rules as data, fail-safe SSE decoding, error envelope everywhere |
| 1.5 | 413 code, per-org capabilities | 1.13 | review audit: open enums machine-readable, `ErrorCode`, `Timestamp` |
| 1.6–1.7 | latency stages, feedback | 1.14 | shown-SQL literal masking, `ArtifactPart.kind` open, exact latency |
| | | 1.15 | share expiry (7/30/90, default 30), new id on widening data, SQL withheld on data-off shares |

## Open decisions that need your sign-off
1. **Shown-SQL masking default.** Proposed `phi` (mask literals compared with PHI-like columns or that look like
   identifiers, and fail closed on unparseable SQL). `all` masks every literal; we suggest `all` per organization for
   strict tenants. Residual risk under `phi`: a free-text name compared with a column whose name does not look PHI-bearing.
   Env: `CC_API_SQL_LITERAL_MASKING`. Executed SQL is never altered; only the displayed copy is.
2. **Analyzer adapter wiring.** Built, tested, **unwired and off** behind the paid-API switch. Wiring is a later
   decision for Peter and you; it is in the backlog, not in flight.
3. **Cancel of a running SQL statement (R6).** Needs an analyzer cancel hook; until then Stop ends the stream and
   closes steps, but the DB statement finishes. Event semantics are specified in §8.
4. **Reasoning text policy (R2).** Default `drop` (no model prose reaches the client); `summary` is opt-in.
5. **Share links (decided by Peter, shipped in 1.15).** `includeData` false by default; widening issues a new id
   and revokes the old one (S1); links expire after 7/30/90 days, default 30 (S4); data-off views withhold SQL.
   For you: confirm 90 days as the maximum and that expiry is "gone" (404), not "expired" (a distinct code).
6. **Semantic identifiers (`sem.*`)** and strategy ids (`tool_loop`, `cascade`, `candidates`) become public API once
   the adapter is wired; please confirm the names.

## What is stubbed / demo-only
ScriptedLLM answers and demo `scenario`s; synthetic data only; usage cost figures; the adapter's executor tap
(a05); capabilities per org come from config, not billing.

## Risks
Shown SQL is still the biggest PHI surface (mitigated by 1.14 masking); client-side guards (withholding SQL,
PHI-free logs) are a safety net, not the control; open enums require clients to render fallbacks (enforced by tests).
