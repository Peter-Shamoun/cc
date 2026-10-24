# Conversational Analytics Agents: SOTA (2025–2026) and ClaimClarity Gap Analysis

## TL;DR

ClaimClarity's core matches where the field converged in 2026: the LLM plans from a governed vocabulary, code compiles and runs the queries, and a constrained step writes the prose. The risks sit around that core: no eval harness, a hard coverage cliff, an unwired free-form SQL lane, and multi-tenant and PHI controls not yet engineered.

- **"LLM plans, code compiles" wins on governed accuracy.** dbt Labs (April 2026): Claude Sonnet 4.6 scored 90.0% raw text-to-SQL vs. 98.2% through a semantic layer; GPT-5.3-Codex 84.1% vs. 100%. An August 2026 arXiv study of a bounded planner plus deterministic compiler: 97.4% vs. 55.3% for direct SQL, with zero wrong-but-executed runs vs. 29.
- **Benchmarks overstate enterprise readiness.** Spider 2.0-Snow tops out at 96.70, but on BEAVER (real private warehouses) the best agent reaches 11.4%, and GPT-5 completes 8.67% of BIRD-Interact conversational tasks.
- **Next, in order:** eval harness with regression gates; out-of-scope handling plus a registry backlog; ADR-009 as a shadowed, labeled escalation lane; per-tenant embedded DuckDB; complementary small-cell suppression, injection hygiene and PHI-safe tracing.

**Scope of this version:** sources dated 2026 lead throughout. Pre-2025 case studies and baselines (Uber QueryGPT, LinkedIn SQL Bot, Pinterest, CHASE-SQL, XiYan, early Cortex Analyst and Looker posts, Anthropic's Dec 2024 agents essay) are removed. 2025 material stays only where it is still the current reference (Agentar-Scale-SQL on BIRD). One older paper (P2SQL) stays because it is the security evidence, not a SOTA claim.

## Architecture landscape

The industry converged on a layered hybrid: a governed deterministic path for known metrics, retrieval of verified question-to-query examples, and a labeled free-form path for the rest. The families differ in who writes the query and what the model may name.

| Pattern | Who authors the query | Governed accuracy | Flexibility | Safety | 2026 examples |
| --- | --- | --- | --- | --- | --- |
| Typed tools over vetted query templates | LLM fills parameters | Highest within coverage | Narrowest | Strongest | Databricks Genie trusted assets, Power BI verified answers, ClaimClarity |
| Semantic / metrics layer | LLM picks metrics, dimensions, filters; engine compiles | Highest within modeled scope | Bounded by the model | Strong, reproducible | dbt Semantic Layer, Looker golden queries, Snowflake semantic views |
| Direct text-to-SQL | LLM writes full SQL | Lowest; can run with wrong joins or grain | Highest | Weakest | Free-form lanes in most products |
| Code-interpreter / sandbox agents | LLM writes pandas or DuckDB code iteratively | Strong on multi-step analysis with rich context | Very high | Needs OS-level sandbox | OpenAI's internal data agent, DABstep leaders |

- **dbt Labs (2026):** semantic layer for enterprise accuracy, text-to-SQL for ad hoc work; the layer only answers what has been modeled.
- **Google Looker (Q3 2026):** Golden Queries let agents retrieve verified business logic instead of guessing joins.
- **Bounded Semantic Planning (arXiv, Aug 2026):** the planner may only choose among retrieved semantic alternatives and cannot name a table, column, join or SQL fragment. 37 of 38 questions correct on every run (97.4%) vs. 21 (55.3%) for direct DDL-to-SQL; one refusal, no wrong-but-executed runs vs. 29. The authors note the comparison is not causal, since the compiled arm also got governed semantic artifacts. This is ClaimClarity's Investigator / registry / compiler split, published with numbers.

## What production teams learned

Every current reference system treats context curation and verified examples as the product, and measures them with a benchmark suite.

**OpenAI's in-house data agent** (Jan 29, 2026; 3.5k+ users, 600+ PB, 70k datasets, GPT-5.2):

- Six context layers: usage and lineage, human annotations, meaning derived from pipeline code, permissioned institutional knowledge, memory, and live schema inspection.
- Memory saves user-confirmed corrections at global or personal scope.
- Asks clarifying questions, but applies stated defaults (e.g. last 7 or 30 days) when no answer comes.
- Treats a zero-row result as a sign of a bad join or filter, not proof of no data.
- Packages recurring analyses as reusable workflows; reachable via Slack, web, IDE and MCP.
- Lessons it names: less is more, guide the goal not the path, meaning lives in code.

**Snowflake Cortex Analyst Evaluations (2026):** verified queries become the test set, held out so the model can't copy them. Reports accuracy and regressions (previously correct queries that now fail). The best-practice guide starts with 10–20 verified queries.

**Microsoft Fabric data agent:** retrieves the top 3 similar example queries per question, validates each against the schema, allows up to 100 per source, and ships an SDK that scores examples, flags conflicts and runs ground-truth evals.

**Databricks Genie:** one production team hit only 54% after the metadata pass (names, descriptions, join keys, sample values). Further gains came from instructions, examples and trusted assets, re-benchmarked after every change. Trusted-asset answers carry a "trusted" badge in the UI.

## Benchmarks and SOTA techniques

Public leaderboards are near-saturated and noisy; enterprise and interactive benchmarks remain far from solved.

| Benchmark | What it tests | Current top result |
| --- | --- | --- |
| Spider 2.0-Snow | Enterprise-style warehouse SQL | 96.70, Genloop (Mar 2026); site history stops Jul 2026 |
| Spider 2.0-Lite | Same, lighter setting | 72.02% EX, Oracle SOMA-SQL |
| BIRD | Single-DB text-to-SQL | 81.67%, Agentar-Scale-SQL |
| BEAVER | Real private warehouses (~100 tables, ~870 columns per DB) | 11.4%, ReFoRCE + Claude 4.5 Sonnet |
| BIRD-Interact (ICLR 2026) | Ambiguity, clarification, follow-ups | GPT-5: 8.67% conversational, 17.00% agentic |
| DABstep | Multi-step financial analytics | 89.95 on hard tasks, NVIDIA KGMON Data Explorer + Claude Haiku 4.5 |

- **Labels are wrong more often than assumed.** A Jan 2026 UIUC audit found 52.8% annotation errors in BIRD Mini-Dev and 62.8% in Spider 2.0-Snow. Re-scoring 16 agents moved ranks by up to 9 places. Treat few-point leaderboard gaps as noise.
- **DABstep's winner grew a tool library.** NVIDIA built reusable, generalized tools in an offline phase, then answered fast with them. That is a self-growing function registry.
- **Text-to-Cypher lags text-to-SQL.** A 2026 arXiv study found Neo4j's fine-tuned Gemma-3-27B text2cypher model unsatisfactory on a complex graph, consistently picking wrong relationship and node labels.
- **What top systems do:** schema linking, value/literal retrieval, verified-example retrieval, several diverse candidate generators, execution-guided repair, learned candidate selection, agentic database exploration. These spend test-time compute to make up for missing semantics, which a governed layer removes for known metrics.

## Key techniques

The consensus toolkit is context first, validation second, and constrained output third.

- **Context engineering:** table and column descriptions, synonyms, glossary and metric definitions; sample and low-cardinality values; verified question-to-query pairs retrieved by similarity; code-derived semantics (OpenAI); one small, focused domain per agent (Fabric, Genie).
- **Entity and value resolution:** search literals before generating. Clarify only when the ambiguity changes the answer; otherwise apply and state a default.
- **Validation and guardrails:** parse to an AST, allow one SELECT over allowlisted objects, check names exist, compile and EXPLAIN first, enforce row, time and memory limits.
- **Grounded numeric generation:** the model never types a number it wasn't handed. Placeholder binding with per-sentence checks is stricter than any vendor documents publicly.
- **Visualization:** deterministic chart specs from typed results, not LLM plotting code, on the governed path.
- **Multi-turn state:** the structured plan, not the transcript, is the state a follow-up modifies. Users can interrupt and redirect.
- **UX:** stream progress, show the plan or query used, badge verified vs. generated answers.

## Evaluation and observability

Measure five things, over several runs per question:

1. **Answer correctness** on golden question-to-expected-result pairs, compared by result set, not exact SQL.
2. **Wrong-but-executed rate**, the dangerous failure, tracked apart from refusals.
3. **Refusal / out-of-scope rate.**
4. **Regression count** across releases (Cortex Evaluations).
5. **Online acceptance and feedback** feeding curation (Genie).

Run each question several times: dbt used 11 questions × 20 runs; the bounded-planning paper used 3 runs each. One database instance can accept a wrong query that happens to match, so add perturbed fixtures to catch grain and fan-out errors.

**Tracing:** the OpenTelemetry GenAI semantic conventions are the vendor-neutral schema in 2026. They define `invoke_agent`, `execute_tool` and `chat` spans plus `gen_ai.conversation.id` and token usage. Prompt and response content sits in span events that can be dropped at the collector, which matters for PHI. Langfuse, Arize Phoenix, Braintrust and LangSmith all ingest them. Pin a convention version; parts are still marked experimental.

## Security and governance for PHI

Isolation must come from infrastructure, and privacy rules must live in server code, not prompts.

- **SQL is code.** DuckDB's 2026 security docs: no internal privilege boundary, and running untrusted SQL is unsafe by design. Mitigations: `enable_external_access = false`, `allowed_directories`, extension autoload off, `lock_configuration = true` set after tenant data loads, plus container sandboxing. Prepared statements protect values only, not model-written query structure.
- **Prompt injection through data.** The P2SQL paper (ICSE 2025; first posted 2023, kept as the core evidence) showed prompt fragments planted in database rows change the LLM's behavior for other users' queries, and prompt-level restrictions alone didn't stop it. Defenses that worked: permission hardening, query rewriting, an auxiliary validator, and preloading data into the prompt.
- **Small-cell suppression must cover derived values.** CMS's DUA (Form CMS-R-0235) bars cells of 10 or fewer and bars percentages or formulas that reveal them. Its guidance requires complementary suppression when totals would expose a suppressed cell. ClaimClarity's threshold of 20 is conservative; the threshold alone isn't enough.
- **LLM data handling.** AWS documents Bedrock as HIPAA eligible under a BAA, and says it does not store or log prompts and completions or train on them. Optional invocation logging to CloudWatch or S3 creates a new PHI store if enabled. Third-party claims of per-model exclusions couldn't be verified on AWS pages; confirm against the live HIPAA reference.

## Harness design and storage choice

**Tiered autonomy** is the 2026 norm, each tier labeled in the UI and evaluated separately:

1. Trusted templated queries.
2. Semantic-layer composition.
3. LLM-written SQL over curated views, validated and sandboxed.
4. Code-interpreter exploration, analysts only.

Loops are bounded by turns, tool calls and wall-clock time. MCP is the standard way to expose data tools (OpenAI's agent, DuckDB/MotherDuck, Snowflake and Databricks all ship MCP servers). In regulated settings the MCP surface exposes governed operations, not raw SQL.

**Adding a flexible lane safely:** route to it only when the registry can't cover the intent; restrict it to curated views with documented grain; validate the AST against an allowlist; run in a locked-down, tenant-scoped engine; apply the same privacy filters; badge the answer "generated"; log for review; promote recurring verified queries into the registry. Cortex, Genie and the DABstep leader all run this flywheel.

**Graph vs. columnar:** ClaimClarity's workload is filter, group and aggregate over a one-hop join (837 CLM01 to 835 CLP01) with CARC breakdowns. That is OLAP, and DuckDB over Parquet fits; ADR-008 matches the evidence. Graphs earn their keep on variable-length multi-hop traversal, and text-to-Cypher still trails text-to-SQL. If payer search is Neo4j's only live use, DuckDB full-text search plus a small vector index would remove a second PHI store.

## Gap analysis for ClaimClarity

The core is at or ahead of 2026 practice; the gaps are evaluation, coverage, the flexible lane, and tenant and PHI controls.

**Already strong**

| Practice | ClaimClarity | Assessment |
| --- | --- | --- |
| LLM plans, code compiles | Typed task registry, server-compiled parameterized SQL/Cypher | Matches dbt SL, Genie trusted assets, Aug 2026 bounded-planning paper |
| Bounded loop | ≤4 model turns, 8 tasks, 25 s | Stricter than most |
| Data minimization | Investigator sees ≤20-row digests | Better than most published systems |
| Grounded prose | Writer limited to server-bound placeholders, checked per sentence | Ahead of public implementations |
| Entity resolution + HITL | Lexical + vector + initials payer search; payer card pauses the turn | Matches BIRD-Interact's lesson that clarification matters |
| Centralized metric definitions | Denial definitions, min group size 20 | A semantic layer in all but name |
| Deterministic charts | Server-built | Correct |

**Gaps, by priority**

- **P0 — Eval harness (missing).** 50–150 golden questions written with the implementations team, each with an expected task plan and expected numbers on frozen LabCorp fixtures. 3–5 runs per question. Track correct, refused/clarified and wrong-but-answered separately. Add counterfactual fixtures (duplicate CLP rows, reversals, multiple remits per claim, payer aliases). Test the payer resolver and Writer binding separately. CI fails on any regression.
- **P0 — Coverage cliff.** Add an explicit "not answerable with current analyses" outcome with in-scope suggestions. Log every unanswered question into a ranked backlog that drives registry growth, the way DABstep's leader grew its tool library.
- **P1 — ADR-009 as escalation.** Route only on no registry fit; curated views with documented grain plus top-k verified examples; sqlglot AST check, single SELECT, no `read_parquet`/`read_csv`/`glob`, mandatory aggregate or LIMIT, EXPLAIN dry run; separate locked-down DuckDB connection scoped to the tenant prefix; same suppression filter; badge "exploratory". Ship in shadow mode first.
- **P1 — Multi-tenancy.** A central DuckDB server serving many customers leaves isolation to query strings and one blast radius. Prefer embedded DuckDB per request on ECS Fargate with per-customer Parquet prefixes and IAM-scoped credentials (favors ADR-011 over ADR-010). Benchmark Fargate cold start against the 25 s budget. Move conversations off SQLite to a managed, tenant-keyed store before multi-customer production.
- **P1 — Derived small cells.** Complementary suppression for totals and shares; same check on chart series and Writer placeholders; the "other" bucket must meet the threshold too.
- **P1 — Injection via EDI text.** Strip free-text segments (NTE notes, names) from digests unless needed, delimit data as data, never let digest content pick task types without server validation, add injected-record fixtures to evals. Critical once ADR-009 is live.
- **P2 — PHI-safe tracing.** OTel GenAI spans per turn and task, content events dropped at the collector, plans and result fingerprints kept in an internal audit table.
- **P2 — Corrections loop.** Reviewed, versioned glossary or registry changes from implementer corrections, per customer where needed (OpenAI's memory layer, but governed). Thumbs up/down with a reason on every answer.
- **P2 — Clarification policy.** Extend the payer-card pattern to other material ambiguities (service vs. remit vs. paid date; denial definition). For minor ones, apply and display the default.
- **P3 — Latency and UX.** Stream progress inside the 120 s deadline, add a "how this was computed" panel and a verified/exploratory badge, cache results by tenant, dataset version and task signature.
- **P3 — Neo4j footprint.** Evaluate collapsing payer search into DuckDB; don't adopt text-to-Cypher for this workload.

## Recommendations

1. **Weeks 0–3:** golden-set eval harness and CI regression gates; OTel GenAI tracing with content dropped at the collector.
2. **Weeks 2–5:** out-of-scope classification and unanswered-question backlog; complementary small-cell suppression across tables, charts and placeholders.
3. **Weeks 4–8:** decide per-tenant embedded DuckDB on Fargate with IAM-scoped Parquet prefixes; move conversations to a managed tenant-keyed store.
4. **Weeks 6–12:** ADR-009 in shadow mode behind the sandbox and validator; surface as "exploratory" only when held-out wrong-but-answered rate is acceptable; promote recurring queries into the registry.
5. **Ongoing:** reviewed corrections loop, verified-example curation, quarterly re-check of Bedrock HIPAA scope and logging with platform and security teams.

## Caveats

- Several figures are vendor-reported or vendor-run: Genloop's 96.70 and dbt's 11-question benchmark. The bounded-planning paper's compiler was built while inspecting its benchmark.
- Spider 2.0 results differ by variant and over time, and leaderboard provenance has been questioned.
- Some OpenAI evaluation details come from secondary summaries of its post.
- Fargate latency at LabCorp scale has to be measured.

## Sources

- [dbt Labs: Semantic Layer vs. text-to-SQL (2026)](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026)
- [Bounded Semantic Planning and Deterministic Compilation (arXiv, Aug 2026)](https://arxiv.org/pdf/2608.16663)
- [Google: Conversational Analytics in Data Cloud, Q3 2026](https://cloud.google.com/blog/products/data-analytics/conversational-analytics-in-google-data-cloud-in-q326)
- [OpenAI: Inside our in-house data agent (Jan 2026)](https://openai.com/index/inside-our-in-house-data-agent/)
- [Snowflake: Cortex Analyst Evaluations](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst-evaluations)
- [Snowflake: Semantic views best practices](https://www.snowflake.com/en/developers/guides/best-practices-semantic-views-cortex-analyst/)
- [Microsoft Fabric: Data agent example queries](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-example-queries)
- [Microsoft Fabric: Data agent configuration best practices](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-configuration-best-practices)
- [Databricks: Production-ready Genie spaces](https://www.databricks.com/blog/how-build-production-ready-genie-spaces-and-build-trust-along-way)
- [Genloop: #1 on Spider 2.0 (Mar 2026)](https://genloop.ai/blogs/genloop-is-1-on-spider-2.0)
- [Spider 2.0 leaderboard audit](https://www.nl2sql.ai/news/spider2-live-leaderboard-public-repo-audit-gap)
- [Oracle: OCI Gen AI tops Spider 2.0-Lite](https://blogs.oracle.com/cloud-infrastructure/oci-gen-ai-tops-spider-2-lite)
- [BEAVER results (arXiv, 2026)](https://arxiv.org/pdf/2608.27796)
- [BIRD-Interact](https://www.alphaxiv.org/abs/2510.05318)
- [NVIDIA: DABstep first place](https://huggingface.co/blog/nvidia/nemo-agent-toolkit-data-explorer-dabstep-1st-place)
- [Text-to-Cypher on complex graphs (arXiv, 2026)](https://arxiv.org/pdf/2603.22340)
- [OpenTelemetry GenAI conventions in 2026](https://dev.to/gabrielanhaia/opentelemetry-genai-semantic-conventions-your-llm-traces-should-look-like-this-in-2026-3ff6)
- [DuckDB: Security](https://duckdb.org/security) and [Securing DuckDB](https://duckdb.org/docs/current/operations_manual/securing_duckdb/overview)
- [P2SQL: prompt-to-SQL injection (ICSE 2025)](https://arxiv.org/pdf/2308.01990v4)
- [ResDAC: CMS cell size suppression policy](https://www.resdac.org/articles/cms-cell-size-suppression-policy)
- [AWS: Bedrock data protection](https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html)
