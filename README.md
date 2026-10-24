# CC: design, data model and diagrams

Design work for CC, a conversational analytics agent over 837P claims and 835 remittances. The app itself (Investigator -> typed tasks -> Neo4j/DuckDB -> Writer, main @ `ca5df10`) lives elsewhere; this repo holds the data model, architecture diagrams, tooling and research behind it.

## Layout

| Path | What it is |
|---|---|
| `claude/schema/` | Current data model (not loaded yet; the X12 parser isn't built): lossless EDI schema, `sem.*` semantic layer, semantic model YAML, agent tools and prompt, synthetic test. See its README. |
| `docs/data-backend/` | Per-customer S3 layout and the per-request DuckDB read path |
| `docs/diagrams/duckdb-schema-access.excalidraw` | Sketch: semantic layer first, lossless schema only when needed |
| `docs/plan/cc-build-week.html` | Build-week plan, Fri 10/23 - Thu 10/29 |
| `diagrams/data-backend-canvas/` | Canonical data-backend architecture diagram (source of truth); `_blob/` holds its icons |
| `diagrams/cc_turn_flow_*.svg` | Turn flow of the app as built, plain and with the Northwind example |
| `diagrams/CC_Backend_Lucid_*` | draw.io exports of the canvas for Lucidchart import (variants A and B, plain source, preview renders) |
| `cc-knowledge/` | Mermaid copies of the current-state flowchart and the Northwind sequence |
| `tools/drawio/`, `tools/lucid/` | Canvas -> draw.io generator and the draw.io/Lucid conversion and validation pipeline (see `tools/lucid/README.md`) |
| `research/` | Agent/chatbot best-practices research (`agent-chatbot-best-practices.md` is the final version) and citations |
| `prompts/` | Prompt used for the research report |
| `drafts/` | Slack drafts and earlier versions of the research doc |
| `chat-notes/` | Per-discussion notes: what was asked, decided, built and left open |
| `memory-and-preferences.md` | Current-state note and working preferences |
| `archive/` | Superseded designs: v0 schema pages and ER diagrams, early sketches, old notes |

## Superseded files outside `archive/`

- `diagrams/cc_northwind_denial_rate_sequence.svg` -> `diagrams/cc_turn_flow_northwind_annotated.svg`
- `diagrams/CC_Data_Backend{,_uncompressed}.drawio` and `.lucid` -> the Lucid A/B draw.io files (Lucid's UI rejects `.lucid`; it is API-only)
- `research/conversational-analytics-agents-sota-2026-full-report.md`, `research/agent-chatbot-best-practices.rebuilt-from-chat-edits.md`, `drafts/doc-v*.md` -> `research/agent-chatbot-best-practices.md`

## Open issues

- `claude/schema` says only `sem_int.remit_event` and `sem_int.line_match` get materialized and calls `edi.segment` "silver"; the newer data-backend plan materializes every `sem.*` view into `semantic_layer/` (gold) and calls the `parquet/` lossless tables "silver". The data-backend plan wins.
- `run_semantic_sql` only allows `sem.*`, while the plan lets the agent escalate to the lossless schema. Unresolved.
- The built turn flow uses Neo4j/DuckDB typed tasks; moving the agent onto `query_metrics` over `sem.*` is scheduled in the build-week plan, not done.
- The turn-flow diagrams still label the Payer Resolver "(AI)".
- Lucid import of the draw.io files is still rejected.
- `ref.denial_carc` must be loaded with the 23 reviewed CARCs; the test uses placeholders.
