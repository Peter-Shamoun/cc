<!-- STATUS: REFERENCE - index of every file in this bundle with its status | generated 2026-10-24 -->
# Bundle index: what is current vs legacy

Labels (also on the first comment line of every file, so `grep "STATUS: LEGACY"` works):
- CURRENT (built): describes the ClaimClarity app as it runs today (repo main @ ca5df10: Investigator -> typed tasks -> Neo4j/DuckDB -> Writer).
- CURRENT (design): the latest agreed design that is not built or wired in yet (claude/schema semantic layer + lossless schema, data-backend AWS plan).
- CURRENT (tooling) / CURRENT (plan): live helper tools and the active build-week plan.
- LEGACY: replaced; the note names what replaced it.
- REFERENCE: history or context (research, prompts, drafts, citations, chat notes, previews), not a design to follow.
- PLACEHOLDER: the original could not be recovered.

Known conflicts inside the CURRENT files:
- claude/schema (2026-10-22/23) says only sem_int.remit_event and sem_int.line_match get materialized and calls edi.segment "silver"; the later data-backend plan (2026-10-23) materializes every sem.* view into semantic_layer/ (gold) and calls parquet/ lossless tables "silver". The data-backend plan is newer.
- run_semantic_sql only allows sem.*, while the plan lets the agent escalate to the lossless schema (lane 2). Unresolved.
- The built turn-flow diagrams use Neo4j/DuckDB typed tasks; the claude/schema design moves the agent onto query_metrics over sem.* (scheduled in the build-week plan, not done).


## CURRENT (built) (5)

- `diagrams/claimclarity_turn_flow_current_state.svg`: turn flow of the app as built (main @ ca5df10). Open: Payer Resolver still labeled "(AI)"; suggested label fixes not applied
- `diagrams/claimclarity_turn_flow_northwind_annotated.svg`: same flow with the Northwind example; latest diagram. Same open labels
- `claimclarity-knowledge/current-state-flowchart.mmd`: your repo copy of the current-state flowchart
- `claimclarity-knowledge/agent-example.mmd`: your repo copy of the Northwind sequence example
- `memory-and-preferences.md`: live ClaimClarity current-state note (2026-10-24) + preferences; the account-level note at the end is an older 2026-10-02 copy

## CURRENT (design) (29)

- `claude/schema/README.md`: latest agreed data model, not yet loaded (parser not built). Partly outdated: header says only remit_event + line_match get materialized; superseded by the data-backend plan (every sem.* view materialized to semantic_layer/ each batch); open: run_semantic_sql only allows sem.*, but the plan lets the agent escalate to the lossless schema (lane 2); not reconciled yet
- `claude/schema/01_lossless_edi_schema.sql`: the lossless (silver) schema; replaces archive/v0-schema/claimclarity-lossless-parquet-schema.md. Open: how superseded files are recorded on edi.source_file
- `claude/schema/02_semantic_layer_views.sql`: the semantic layer (gold); replaces the 7-table analytics layer in archive/v0-schema. Partly outdated: header says only remit_event + line_match get materialized; superseded by the data-backend plan (every sem.* view materialized to semantic_layer/ each batch)
- `claude/schema/03_semantic_model.yaml`: metrics/dimensions/verified requests for the semantic layer
- `docs/diagrams/duckdb-schema-access.excalidraw`: your latest sketch (2026-10-23): semantic layer first, lossless schema only when needed
- `diagrams/data-backend-canvas/canvas.json`: canonical data-backend architecture diagram (live canvas); source of truth for the backend design
- `diagrams/data-backend-canvas/Main.dc.html`: canonical data-backend architecture diagram (live canvas); source of truth for the backend design
- `diagrams/data-backend-canvas/Z00-Storage.dc.html`: canonical data-backend architecture diagram (live canvas); source of truth for the backend design
- `diagrams/data-backend-canvas/Z01-Trigger.dc.html`: canonical data-backend architecture diagram (live canvas); source of truth for the backend design
- `diagrams/data-backend-canvas/Z02-Login.dc.html`: canonical data-backend architecture diagram (live canvas); source of truth for the backend design
- `diagrams/data-backend-canvas/Z03-Query.dc.html`: canonical data-backend architecture diagram (live canvas); source of truth for the backend design
- `docs/data-backend/s3-folder-layout.txt`: per-customer S3 layout (raw/ parquet/ semantic_layer/ staging/ quarantine/)
- `docs/data-backend/duckdb_per_request_connection.py`: read-path pattern; illustrative fragment
- `claude/schema/04_agent_interface.py`: agent tools + compiler for the semantic layer; not wired into the app yet (build week Sun). open: run_semantic_sql only allows sem.*, but the plan lets the agent escalate to the lossless schema (lane 2); not reconciled yet
- `claude/schema/05_agent_system_prompt.md`: not wired into the app yet
- `claude/schema/tests/test_semantic_layer.py`: passing synthetic test; uses placeholder denial CARCs
- `diagrams/data-backend-canvas/_blob/0307734e654b2b21efe0a5f291c6dfaa.svg`: icon used by the data-backend canvas
- `diagrams/data-backend-canvas/_blob/2aba17cac2e021d111c9e8a76f960597.svg`: icon used by the data-backend canvas
- `diagrams/data-backend-canvas/_blob/2f1afea2b3d8e0a44fafa5a8324c57db.svg`: icon used by the data-backend canvas
- `diagrams/data-backend-canvas/_blob/4b81f87287652a4832b8c6813ec0647b.svg`: icon used by the data-backend canvas
- `diagrams/data-backend-canvas/_blob/57e1a6d91fe6ae830b46750e959a1bd1.svg`: icon used by the data-backend canvas
- `diagrams/data-backend-canvas/_blob/62a0be681826d7fd7af5704ec5856d1b.svg`: icon used by the data-backend canvas
- `diagrams/data-backend-canvas/_blob/6e038333961238dc32615f2321031d6b.svg`: icon used by the data-backend canvas
- `diagrams/data-backend-canvas/_blob/80f4cb7eb08196246c6dae715a9d9bb2.svg`: icon used by the data-backend canvas
- `diagrams/data-backend-canvas/_blob/81ad13358e9c59bc95ce5d434eb643cd.svg`: icon used by the data-backend canvas
- `diagrams/data-backend-canvas/_blob/a6cb923d6bcc23b04eda7f19823277b1.svg`: icon used by the data-backend canvas
- `diagrams/data-backend-canvas/_blob/c1c3dd0df209ce6a2a22dc79ce1d31d8.svg`: icon used by the data-backend canvas
- `diagrams/data-backend-canvas/_blob/cf9147ca114aa08a8971861a3ee7271a.svg`: icon used by the data-backend canvas
- `diagrams/data-backend-canvas/_blob/dac9691696bb585add1fed0038adb112.svg`: icon used by the data-backend canvas

## CURRENT (tooling) (10)

- `tools/drawio/gen.py`: generates the draw.io version of the data-backend canvas. Open: Lucid import still rejected
- `tools/lucid/validate_lucid.py`: Lucid pipeline; Lucid import still unresolved
- `tools/lucid/render_lucid.py`: Lucid pipeline; Lucid import still unresolved
- `tools/lucid/drawio2lucid.py`: Lucid pipeline; Lucid import still unresolved
- `tools/lucid/lucid2drawio.py`: Lucid pipeline; Lucid import still unresolved
- `tools/lucid/check_drawio_for_lucid.py`: Lucid pipeline; Lucid import still unresolved
- `tools/lucid/README.md`: how the draw.io/Lucid pipeline runs
- `diagrams/ClaimClarity_Backend_Lucid_B_compressed.drawio`: latest Lucid deliverable (variant B); Lucid import still unresolved. Canvas is the source of truth
- `diagrams/ClaimClarity_Backend_Lucid_safe_uncompressed.drawio`: plain source of the Lucid-safe diagram
- `diagrams/ClaimClarity_Backend_Lucid_A_drawio_export.drawio`: latest Lucid deliverable (variant A); Lucid import still unresolved. Canvas is the source of truth

## CURRENT (plan) (1)

- `docs/plan/claimclarity-build-week.html`: active plan Fri 10/23 - Thu 10/29; goes stale after the 10/29 ship date

## LEGACY (22)

- `diagrams/claimclarity_northwind_denial_rate_sequence.svg`: replaced by diagrams/claimclarity_turn_flow_northwind_annotated.svg
- `research/conversational-analytics-agents-sota-2026-full-report.md`: original research report; replaced by research/agent-chatbot-best-practices.md
- `archive/v0-schema/claimclarity-schema.html`: final "Schema v1" page (edi + 7-table analytics layer); replaced by claude/schema/* (sem.* views + semantic model)
- `archive/v0-schema/claimclarity-er-diagram.html`: ER diagram of the 9-table design; never redrawn for later designs
- `archive/v0-schema/claimclarity-lossless-parquet-schema.md`: first lossless schema design; replaced by claude/schema/01_lossless_edi_schema.sql
- `archive/superseded/claimclarity_turn_flow_current_state.mermaid-widget.html`: replaced by diagrams/claimclarity_turn_flow_current_state.svg
- `archive/superseded/memory-as-written/implementations-neo4j-chatbot.md`: replaced by the live note in memory-and-preferences.md
- `archive/superseded/memory-as-written/preferences.md`: replaced by the live note in memory-and-preferences.md
- `archive/superseded/data-backend-flow-sketch.txt`: replaced by diagrams/data-backend-canvas/*
- `research/agent-chatbot-best-practices.rebuilt-from-chat-edits.md`: duplicate rebuilt from chat edits; the exact export is research/agent-chatbot-best-practices.md
- `archive/v0-schema/superseded/schema-page-v1-48table.html`: rejected 48-table agent surface; replaced by claude/schema/*
- `archive/v0-schema/superseded/schema-page-v1b-48table-audience-edits.html`: rejected 48-table agent surface; replaced by claude/schema/*
- `archive/v0-schema/superseded/schema-page-v2-8table.html`: rejected file-shaped 8-table subset; replaced by claude/schema/*
- `archive/v0-schema/superseded/schema-page-v3-9table.html`: rejected file-shaped 9-table subset; replaced by claude/schema/*
- `archive/v0-schema/superseded/schema-page-v4-two-layer.html`: first two-layer draft; replaced by archive/v0-schema/claimclarity-schema.html, then by claude/schema/*
- `archive/v0-schema/superseded/er-diagram-v1.html`: first ER diagram; replaced by archive/v0-schema/claimclarity-er-diagram.html
- `drafts/doc-v1-sota-2025-2026-trimmed.md`: earlier doc version; replaced by research/agent-chatbot-best-practices.md
- `drafts/doc-v2-two-reference-designs.md`: earlier doc version; replaced by research/agent-chatbot-best-practices.md
- `drafts/doc-v3-general-consensus.md`: earlier doc version; replaced by research/agent-chatbot-best-practices.md
- `diagrams/ClaimClarity_Data_Backend.drawio`: first draw.io conversion (still opens in diagrams.net); for Lucid, replaced by the Lucid A/B files. Canvas is the source of truth
- `diagrams/ClaimClarity_Data_Backend_uncompressed.drawio`: first draw.io conversion; for Lucid, replaced by the Lucid A/B files
- `diagrams/ClaimClarity_Data_Backend.lucid.b64`: Lucid UI rejects .lucid (API-only); replaced by the Lucid-safe draw.io files

## REFERENCE (23)

- `project-instructions.md`: project has no instructions set
- `research/agent-chatbot-best-practices.md`: final version of the best-practices research doc
- `prompts/deep-research-conversational-analytics-agents.md`: prompt that produced the research report
- `drafts/slack-semantic-layer-proposal.md`: Slack draft; whether/which version was sent is unknown
- `drafts/slack-lossless-curated-layer.md`: Slack draft; whether/which version was sent is unknown
- `archive/v0-schema/prompts/research-task-command.txt`: prompt that produced the v0 schema research
- `archive/v0-schema/superseded/research-citations.txt`: citation data for the v0 schema research
- `archive/v0-schema/superseded/oracle-paste-msg4.txt`: your pasted AI_APPEAL_SFTP Oracle table definitions (input)
- `archive/v0-schema/superseded/oracle-paste-msg5.txt`: your pasted AI_APPEAL_SFTP Oracle table definitions (input)
- `research/research-report-citations.txt`: citation data for the research report
- `drafts/slack-draft-user-original-turn12.txt`: your original Slack draft
- `drafts/slack-paragraph-fix-turn15.txt`: one-paragraph fix for your Slack draft
- `diagrams/ClaimClarity_Data_Backend.png.b64`: preview render
- `diagrams/ClaimClarity_Backend_Lucid_safe.png.b64`: preview render
- `diagrams/ClaimClarity_Backend_Lucid_preview_render_lucid.png.b64`: preview render
- `chat-notes/Mermaid flowchart of AI pipeline.md`: your messages + what was decided/built/left open
- `chat-notes/SOTA agent chatbot best practices.md`: your messages + what was decided/built/left open
- `chat-notes/Data pipeline plan and diagram.md`: your messages + what was decided/built/left open
- `chat-notes/Draw.io arrows from Zoom.md`: your messages + what was decided/built/left open
- `chat-notes/Build guide and project calendar.md`: your messages + what was decided/built/left open
- `chat-notes/835-837p EDI analytics schema.md`: your messages + what was decided/built/left open
- `chat-notes/EDI 835-837 parquet schema.md`: your messages + what was decided/built/left open
- `memory-other-areas.md`: memory unrelated to ClaimClarity

## PLACEHOLDER (8)

- `diagrams/not-regenerated-items.MISSING.md`: original not recoverable; see body
- `attachments/flowchart-chat-message6-diagram.MISSING.md`: original not recoverable; see body
- `attachments/data-backend-original-diagram.html.MISSING.md`: original not recoverable; see body
- `attachments/zoom-in-canvas-examples.html.MISSING.md`: original not recoverable; see body
- `docs/data-backend-design-doc.md.MISSING.md`: original not recoverable; see body
- `tools/lucid/lucid_preflight.zip.MISSING.md`: original not recoverable; see body
- `tools/drawio-desktop.AppImage.MISSING.md`: original not recoverable; see body
- `claude/schema/claimclarity_schema.zip.MISSING.md`: original not recoverable; see body
