# Contract example streams (v1)

Canonical `StreamEvent` streams, one JSON object per line, captured from the demo backend
(ScriptedLLM on synthetic data; no paid APIs, no PHI) and normalised to stable ids (`m_1`, `s_3`, `p_2`...).
Regenerate with `python -m api.contract.gen_examples`; `api/tests/test_contract_examples.py` validates every
line against `v1.schema.json`, checks the CONTRACT.md §2 invariants, and fails if the demo backend's event
sequence drifts from these files.

| file | outcome | `finishReason` | shows |
|---|---|---|---|
| `answer.jsonl` | final answer | `stop` | explore + SQL steps, verifier check, streamed text, `code` (SQL, `role: query`), `table` (`sqlPartId`, `csvFileId`), callouts, rich `usage` (tokens, `costUsd` estimate, latency with per-stage `stages`, models) |
| `tool_steps.jsonl` | repair loop | `stop` | `step.sql_result ok:false` (binder error) → `step.repair_attempt` → `step.sql_running {purpose: repair, attempt: 2, repairOf}` → final query → `step.check` (warn → caveat) |
| `abstain.jsonl` | abstain | `abstain` | one `abstain` part (`category`, `reason`, `suggestions[]`); no table, no error |
| `clarify.jsonl` | clarifying question | `needs_input` | one `clarification` part (`choices[]`, `allowFreeText`) |
| `clarify_resume.jsonl` | answer after picking the first choice | `stop` | `message.start.resumeOf = {clarificationId, messageId}` of `clarify.jsonl` |
| `regenerate_concise.jsonl` | "More concise" on `answer.jsonl` (1.10.0, CONTRACT.md §8.1) | `stop` | `message.start.regenerateOf` = the answer's id, `regenerateStyle: "concise"`; first sentence, no chart; table, SQL and callouts kept |
| `escalation.jsonl` | small → large model | `stop` | `escalation {from, to, reason, trigger}`; `usage.models[]` has both models |
| `chart.jsonl` | trend | `stop` | Vega-Lite `chart` part with inline data |
| `candidates.jsonl` | multi-candidate | `stop` | candidate SQL steps + `step.candidates {chosenId, method, rationale}` |
| `error.jsonl` | failure | `error` | one `error` part |

Ids such as `csvFileId` are normalised, so `/api/download/<id>` will 404 when an example is replayed.

`capabilities.json` is `GET /api/capabilities` from the demo backend (contract 1.4.0, CONTRACT.md §12); `web/src/contract/capabilities.ts` mirrors it for offline/mock mode.
`feedback.json` is a `PUT …/messages/{id}/feedback` request and response (contract 1.7.0, CONTRACT.md §13), plus the `DELETE` that clears it.
`export.md` is `GET /api/conversations/{id}/export?format=markdown` for the `answer` question (contract 1.8.0, CONTRACT.md §15). `message.end.sources` (citations, §14) appears in every answer stream.
`report.jsonl` (1.9.0) shows partial-table streaming (`table.start`/`table.rows` before the table `part`) and the artifact spec: a `chart` artifact and an auto-opening `report` artifact (CONTRACT.md §5.1, §16).
`share.json` (1.11.0) shows `PUT .../share` (default `includeData: false`), the colleague's `GET /api/shared/{id}` view (table rows withheld, no chart, no feedback) and revoke (CONTRACT.md §17).
