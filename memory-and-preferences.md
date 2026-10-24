## /projects/01a164c7-7161-7219-b75d-d1f0dbd461c6/areas/implementations-neo4j-chatbot.md  (updated 2026-10-24T17:42:42Z)

---
name: implementations-neo4j-chatbot
description: CC — user's Xifin chat assistant over Labcorp 835/837 data (Neo4j/DuckDB); living current-state architecture, ADRs, open items
sources: [chat]
aliases: [CC, Neo4j chatbot, implementations chatbot, customer conversion analysis project, AskClaimIQ]
---

- [stated] developing a chatbot service for Xifin's implementations team that uses Neo4j as its data source; Neo4j holds 835/837 EDI files (claim PCN CLM01 links to remittance CLP01), currently only LabCorp's
- [stated] goal: make it available for any customer so implementations can analyze a new customer's claim/remittance data before pitch conversations
- [stated] part of a broader new-customer conversion intake/analysis project (3 phases: data collection, conversion file prep, analysis for implementation team); writing a 5-paragraph summary as development ground truth

## Current state (as of 2026-10-24 — LIVING SECTION: update, revert or edit as the project moves)
- [stated] migrating this claude.ai project's work into a Claude Code repo (2026-10-24)
- [stated] name CC; came from the NBA repo (MG1, 2026-10-13), inherits its Neo4j claims graph, not its scoring engine; internal pilot in dev, not deployed to end users; rule since 2026-10-16: "build for many customers, heading to production"
- [stated] turn flow: Investigator (Sonnet 5.5, one tool run_tasks, ≤4 model turns/8 tasks/25 s, sees only ≤20-row digests) plans typed tasks → server validates against registry, resolves scope/payer, compiles parameterized Cypher or DuckDB SQL (no model-written query on live path) → evidence → server builds findings/tables/charts → Writer (one streamed call, 6 s) writes prose only via {{placeholders}} the server binds and checks per sentence → SQLite commit before `completion`; turn deadline 120 s
- [stated] payer settles without user only on exact or single normalized match; otherwise turn pauses on a payer card (awaiting_input); resume reruns stored tasks with no new Investigator call. Open question: whether Payer Resolver is an AI call (aggregate says payer card path is "Investigator only"; resolver = lexical+vector+initials search)
- [stated] denial measures: recorded denial = 835 CLP02 status 4 (LABCORP profile only); denial signal v3 = non-reversal 835, $0 paid, CO-group CAS in 23 reviewed CARCs; denied = either; CPT-grouped rates default min 20 owners
- [stated] backends: Neo4j couldn't finish overview (MG3b) → ADR-008 DuckDB over Parquet; DuckDB mode still uses Neo4j for payer search; first full Parquet load (2026-10-20) failed keys.remittances (8,294 dup keys), no CURRENT pointer
- [stated] new task (2026-10-22): design a Parquet table schema optimized for agentic querying of all Labcorp 837P/835 data; user's direction: standardized/atomic, simple and intuitive for the agent, lossless to full EDI unless strongly argued otherwise, carry S3 provenance for engineers, no per-interaction limits in the schema (hooks/blocks later); schema duplicated per customer in separate buckets/folders for multitenancy; settled on two schemas: (1) a lossless storage schema (the original ~37-table normalized "edi" design is likely fine) and (2) a curated semantic layer / view subset built from it for agent interaction; user found the file-shaped subsets (8–9 table file→batch→claim→line version modeled on their AI_APPEAL_SFTP tables) unconvincing as a semantic layer — semantic layer is being revisited; agent queries the semantic layer by default and may escalate to the lossless layer when it can't answer from the semantic layer
- [stated] data backend / multitenancy plan (2026-10-23, user's draft): customer SFTP drops (out of scope) copied on a daily/weekly schedule into a PHI-approved prod S3 bucket, one subfolder per customer with its own SSE-KMS key and IAM permissions; bronze = raw EDI by date, then a success marker fires EventBridge to launch a parser that writes edi.segment and the lossless Parquet schema; per-customer folders: raw/ (bronze), parquet/ (lossless tables, silver), semantic_layer/ (gold: the whole semantic layer — all sem.* views materialized as Parquet, not just remit_event + line_match; renamed views/ → materialized/ → semantic_layer/), plus staging/ and quarantine/; parser = short-lived Fargate task per customer batch running with that customer's role, destroyed after; semantic_layer/ rebuilt once per batch, not per file; view SQL lives in GitHub (code only) and is run by the Fargate parser each batch to materialize semantic_layer/; DuckDB reads the materialized tables; read side: Cognito + MS Entra ID login, user mapped to a customer, DuckDB connection opened per request with that customer's short-lived creds; security checks drawn as AWS-enforced gates; user accepted the proposed revisions to this plan in general and definitely wants a bronze/silver/gold hierarchy; diagrammed as an overview plus zoom-in subdiagrams (zoom-in canvas style modeled on their NBA Project Canvas); user's written design doc (2026-10-23) adds: SFTP→S3 copy reuses LabCorp's existing IT script, 277 files come later, no Parquet compaction job for now (maybe later), chat logs not saved / no telemetry assumed for now (still undecided)
- [stated] **up-to-date data backend (confirmed by user 2026-10-24):** the canvas `diagrams/data-backend-canvas/` (Main overview + zoom-ins 00 Storage, 01 Ingestion & parse, 02 Login, 03 Query), bundled as `docs/data-backend/canvas.html` and published at https://claude.ai/artifact/Q4XqDXcMvkXBbatExDweK4; the user's own design doc "CC backend data design: How it works" is saved verbatim in `docs/data-backend/design-doc.md` (primary write-up; `docs/data-backend/README.md` is Claude's board-by-board companion). Rebuild with `python tools/canvas/build_canvas_page.py` after editing the canvas, then republish to the same URL
- [stated] agent-analyst research (reviewed 2026-10-24): final doc `research/agent-chatbot-best-practices.md` ("Conversational Analytics Agents: SOTA (2025–2026)", Oct 22: OpenAI in-house data agent vs Aug 2026 bounded-planning paper, 4 control levels, dbt 90.0%→98.2%, 14 sources); CC gap analysis, Lane 2 safety design and sequenced recommendations live only in the original report `research/conversational-analytics-agents-sota-2026-full-report.md`. Gaps: no cost-reduction research (only build-week levers: prompt caching, Haiku routing, per-turn token budget, cost logged per turn); the paper vs Cortex/Genie/Fabric/dbt/DABstep refresher was chat-only and not saved; sources are links only; Slack semantic-layer pitch sent/version and the 15-min team talk unknown; research predates the S3 backend (its per-tenant Fargate DuckDB recommendation differs from the canvas's EC2 API with embedded DuckDB, same question as ADR-010 vs 011)
- [stated] delivery plan (2026-10-23): finish by end of October — Fri 10/23 diagram + finalize silver/gold schemas + port Appeals parser to parse LCA S3 files locally + handoff meeting with Jaewon (status, his todo, what stakeholders/Sharon liked, MVP vs improve) + scope terraform; weekend: multitenancy/PHI and scale review (rate limiting, queues), improvements across cost/accuracy/safety (OpenAI example + paper, plus cost reduction), OpenAI-style frontend (clarifying questions, text, plots, markdown tables, CSV download), EDI-wide internal knowledge base with admin role, semantic layer + premade queries; Mon 10/26 design review + terraform with Owen; Tue hosted and testable EOD; Wed testing (Haiku swarm); Thu 10/29 formal deployment, demo, architecture breakdown, Confluence docs; Fri 10/30 off
- [stated] lane 2 (ADR-009, AI-written SQL over registry views): P2a built and merged (PR #21), unreachable; P2b not started
- [stated] ADRs: 010 Accepted (central DuckDB on EC2 over Quack) vs 011 Proposed (interim two-EC2 dev, long-term ECS Fargate embedded DuckDB) — conflict; 010 stands until user accepts 011
- [stated] governance open: awaiting Cognito approval (CoE 10/19); DuckDB/Quack/SQLite not in XiFin AI Standard Platform catalog; Neo4j hosting undecided; DEV PHI-bucket policy undecided
- [stated] docs: aggregate written 2026-10-22 over main @ ca5df10, kept outside repo; current-state flowchart and Northwind running-example sequence saved as .mmd in cc-knowledge/ (agent-example.mmd); payer names in examples are made up per repo rule

## /projects/01a164c7-7161-7219-b75d-d1f0dbd461c6/preferences.md  (updated 2026-10-23T15:53:17Z)

---
name: preferences
description: How the user wants Claude to work in this project — keep the CC current-state memory live
sources: [chat]
---

- [stated] treat the "Current state" section of [[implementations-neo4j-chatbot]] (CC) as living: whenever a conversation changes the architecture, status, ADRs, data loads or open items, update, revert or edit that section to match (and bump its "as of" date) rather than appending history
- [stated] keep responses concise and to the point: no repetition, no jargon or filler added for quantity over quality

## /projects/01a164c7-7161-7219-b75d-d1f0dbd461c6/index.md  (updated 2026-10-22T18:29:15Z)

---
name: "ClaimClairity"
description: ClaimClairity
---

## /areas/implementations-neo4j-chatbot.md  (account-level, outside the project; updated 2026-10-02T18:12:50Z)

---
name: implementations-neo4j-chatbot
description: User's Xifin implementations-team chatbot over a Neo4j graph of 835/837 EDI files (LabCorp only so far); part of new-customer conversion analysis project
sources: [chat]
aliases: [Neo4j chatbot, implementations chatbot, customer conversion analysis project]
---

- [stated] developing a chatbot service for Xifin's implementations team that uses Neo4j as its data source; Neo4j holds 835/837 EDI files (claim PCN CLM01 links to remittance CLP01), currently only LabCorp's
- [stated] goal: make it available for any customer so implementations can analyze a new customer's claim/remittance data before pitch conversations
- [stated] part of a broader new-customer conversion intake/analysis project (3 phases: data collection, conversion file prep, analysis for implementation team); writing a 5-paragraph summary as development ground truth
