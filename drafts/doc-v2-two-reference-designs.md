# Conversational Analytics Agents: SOTA (2025–2026) and CC Gap Analysis

CC's core already matches the 2026 consensus: the LLM plans from a governed vocabulary, code compiles the queries, and a constrained step writes the prose. What it lacks is what the best systems are built around: an eval suite, a way to grow coverage, and tenant-level isolation for PHI.

## Two reference designs

The two strongest 2026 designs sit at opposite ends, and CC sits with the governed one.

**OpenAI's in-house data agent** (Jan 2026) is the flexible end. It serves 3.5k+ internal users over 70k datasets, writing its own SQL against a rich context stack: lineage, human annotations, meaning read from pipeline code, team docs, and saved user corrections. Its named lessons: less is more, guide the goal not the path, meaning lives in code.

**The bounded-planning compiler** (arXiv, Aug 2026) is the governed end. The planner may only pick from retrieved semantic options and can never name a table, column or join; code builds the SQL. It was correct on every run for 97.4% of questions vs. 55.3% for direct SQL generation, with zero wrong-but-executed runs vs. 29.

| | OpenAI data agent | Bounded planner | CC |
| --- | --- | --- | --- |
| Who writes the query | Model | Compiler | Compiler (typed registry) |
| Unknown questions | Answers anything in scope of its context | Refuses | No defined path |
| Ambiguity | Asks, else applies a stated default | Refuses | Payer card pauses the turn |
| Learning from use | Saves confirmed corrections | Analysts extend the semantic model | None yet |
| Checks itself | Zero rows = suspect a bad join | Governance checks in code | Writer bound to server placeholders |
| Measured by | Golden Q&A pairs graded on results | Adjudicated set, 3 runs each | Not measured |

The benchmarks agree that governance beats raw generation on real data. On BEAVER's private enterprise warehouses the best free-form agent reaches 11.4%, and GPT-5 completes 8.67% of BIRD-Interact's conversational tasks.

## What to change in CC

Keep the compiler core; borrow OpenAI's learning loop around it, and fill the last row of the table first.

1. **Eval harness (P0).** 50–150 golden questions from the implementations team, each with the expected task plan and expected numbers on frozen LabCorp fixtures, run 3–5 times. Track correct, refused and wrong-but-answered separately. Include traps (duplicate CLP rows, reversals, multiple remits per claim, payer aliases). Any regression fails CI.
2. **A path for unknown questions (P0).** Reply "not answerable with current analyses" with in-scope suggestions, and log every miss to a ranked backlog that decides which registry tasks to add next.
3. **ADR-009 as a labeled escalation (P1).** This is OpenAI's model-writes-SQL mode, so add it only behind the registry: curated views, an AST allowlist (one SELECT, no `read_parquet` or `glob`), a locked DuckDB connection (`enable_external_access=false`, `lock_configuration=true`), the same suppression filter, an "exploratory" badge. Run in shadow mode against the eval set before users see it.
4. **Per-tenant isolation (P1).** DuckDB has no privilege boundary, so a shared central server leaves isolation to query strings. Embedded DuckDB per request on Fargate with per-customer, IAM-scoped Parquet prefixes favors ADR-011 over ADR-010.
5. **Small cells and injection (P1).** Suppress cells that can be derived from totals or percentages, not just those under 20. Strip EDI free text (NTE notes) from digests, since stored text can steer the model.
6. **Governed corrections loop (P2).** OpenAI's saved corrections, but reviewed: an implementer's fix becomes a versioned registry or glossary change, not hidden model memory.

## Plan

| Weeks | Work |
| --- | --- |
| 0–3 | Eval harness and CI gate |
| 2–5 | Unknown-question path and backlog; derived-cell suppression |
| 4–8 | Per-tenant DuckDB decision; tenant-keyed conversation store |
| 6–12 | ADR-009 in shadow mode, then "exploratory" release |

## Sources

- [OpenAI: Inside our in-house data agent](https://openai.com/index/inside-our-in-house-data-agent/)
- [Bounded Semantic Planning and Deterministic Compilation (arXiv, Aug 2026)](https://arxiv.org/pdf/2608.16663)
- [BEAVER results (arXiv, 2026)](https://arxiv.org/pdf/2608.27796) · [BIRD-Interact](https://www.alphaxiv.org/abs/2510.05318)
- [DuckDB: Securing DuckDB](https://duckdb.org/docs/current/operations_manual/securing_duckdb/overview)
- [ResDAC: CMS cell size suppression](https://www.resdac.org/articles/cms-cell-size-suppression-policy)
