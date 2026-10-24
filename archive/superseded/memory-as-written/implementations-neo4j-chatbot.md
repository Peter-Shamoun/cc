<!-- STATUS: LEGACY - replaced by the live note in memory-and-preferences.md | source: chat "Mermaid flowchart of AI pipeline" (2026-10-23) | FLAG: SUPERSEDED: memory note as first written in this chat; current version is in memory-and-preferences.md -->
---
name: implementations-neo4j-chatbot
description: CC — user's Xifin chat assistant over Labcorp 835/837 data (Neo4j/DuckDB); living current-state architecture, ADRs, open items
sources: [chat]
aliases: [CC, Neo4j chatbot, implementations chatbot, customer conversion analysis project, AskClaimIQ]
---

- [stated] developing a chatbot service for Xifin's implementations team that uses Neo4j as its data source; Neo4j holds 835/837 EDI files (claim PCN CLM01 links to remittance CLP01), currently only LabCorp's
- [stated] goal: make it available for any customer so implementations can analyze a new customer's claim/remittance data before pitch conversations
- [stated] part of a broader new-customer conversion intake/analysis project (3 phases: data collection, conversion file prep, analysis for implementation team); writing a 5-paragraph summary as development ground truth

## Current state (as of 2026-10-22 — LIVING SECTION: update, revert or edit as the project moves)
- [stated] name CC; came from the NBA repo (MG1, 2026-10-13), inherits its Neo4j claims graph, not its scoring engine; internal pilot in dev, not deployed to end users; rule since 2026-10-16: "build for many customers, heading to production"
- [stated] turn flow: Investigator (Sonnet 5.5, one tool run_tasks, ≤4 model turns/8 tasks/25 s, sees only ≤20-row digests) plans typed tasks → server validates against registry, resolves scope/payer, compiles parameterized Cypher or DuckDB SQL (no model-written query on live path) → evidence → server builds findings/tables/charts → Writer (one streamed call, 6 s) writes prose only via {{placeholders}} the server binds and checks per sentence → SQLite commit before `completion`; turn deadline 120 s
- [stated] payer settles without user only on exact or single normalized match; otherwise turn pauses on a payer card (awaiting_input); resume reruns stored tasks with no new Investigator call. Open question: whether Payer Resolver is an AI call (aggregate says payer card path is "Investigator only"; resolver = lexical+vector+initials search)
- [stated] denial measures: recorded denial = 835 CLP02 status 4 (LABCORP profile only); denial signal v3 = non-reversal 835, $0 paid, CO-group CAS in 23 reviewed CARCs; denied = either; CPT-grouped rates default min 20 owners
- [stated] backends: Neo4j couldn't finish overview (MG3b) → ADR-008 DuckDB over Parquet; DuckDB mode still uses Neo4j for payer search; first full Parquet load (2026-10-20) failed keys.remittances (8,294 dup keys), no CURRENT pointer
- [stated] lane 2 (ADR-009, AI-written SQL over registry views): P2a built and merged (PR #21), unreachable; P2b not started
- [stated] ADRs: 010 Accepted (central DuckDB on EC2 over Quack) vs 011 Proposed (interim two-EC2 dev, long-term ECS Fargate embedded DuckDB) — conflict; 010 stands until user accepts 011
- [stated] governance open: awaiting Cognito approval (CoE 10/19); DuckDB/Quack/SQLite not in XiFin AI Standard Platform catalog; Neo4j hosting undecided; DEV PHI-bucket policy undecided
- [stated] docs: aggregate written 2026-10-22 over main @ ca5df10, kept outside repo; current-state flowchart and Northwind running-example sequence saved as .mmd in cc-knowledge/ (agent-example.mmd); payer names in examples are made up per repo rule
