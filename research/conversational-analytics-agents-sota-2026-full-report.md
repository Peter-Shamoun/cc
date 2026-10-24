# Conversational Analytics Agents in October 2026: State of the Art, Best Practices, and a Gap Analysis for ClaimClarity

ClaimClarity's core design follows the pattern the field has settled on for high-stakes "talk to your data" systems. The LLM plans and chooses from a governed vocabulary. Deterministic code compiles and runs the queries. A separate, constrained step writes the prose. The biggest risks are not in this core. They are in four things around it: no measured evaluation harness, a hard coverage cliff where the registry ends, an unwired free-form SQL lane, and multi-tenant and PHI controls that still need to be engineered before the pilot becomes a product.

## TL;DR

- **The field has converged on "LLM plans, code compiles."** In dbt Labs' April 2026 benchmark, Claude Sonnet 4.6 scored 90.0% with raw text-to-SQL and 98.2% through a semantic layer. GPT-5.3-Codex scored 84.1% and 100%. A 2026 arXiv study of a bounded planner plus deterministic compiler reported 97.4% vs. 55.3% for direct SQL generation, with zero wrong-but-executed runs vs. 29. ClaimClarity's typed-task registry is this pattern.
- **Benchmarks overstate readiness for enterprise data.** Leaderboards have saturated: 96.70 on Spider 2.0-Snow and 81.67% on BIRD. Enterprise and interactive benchmarks tell a different story. GPT-5 succeeds on only 8.67% of BIRD-Interact c-Interact tasks, and ReFoRCE with Claude-4.5-Sonnet reaches 11.4% on BEAVER. Context, verified examples, clarification, and domain evals matter more than which model you pick.
- **What ClaimClarity should do next, in order:** (1) build a golden-question eval harness with regression gates; (2) add explicit out-of-scope handling and a question-to-registry backlog loop; (3) wire ADR-009 as a labeled, sandboxed escalation lane, shadowed first, never as a silent fallback; (4) choose per-tenant isolation (embedded DuckDB per customer dataset) over a shared central DuckDB server; (5) harden PHI controls: complementary small-cell suppression, prompt-injection hygiene for EDI free text, and PHI-safe OpenTelemetry tracing.

## Key Findings

### 1. The architectural landscape and where industry converged

There are five families of architecture. They differ mainly in **who writes the query and what the model is allowed to name**.

| Pattern | Who authors the query | Accuracy on governed metrics | Flexibility | Safety / auditability | Latency | Representative systems |
|---|---|---|---|---|---|---|
| Direct text-to-SQL / text-to-Cypher | LLM writes full SQL | Lowest and least stable; can execute "successfully" with wrong joins or grain | Highest | Weakest; SQL is code | Medium | Uber QueryGPT, LinkedIn SQL Bot, Pinterest Querybook, Vanna, Wren AI |
| Semantic/metrics layer (MetricFlow, Cube, LookML, Snowflake semantic views) | LLM picks metrics/dimensions/filters; engine compiles SQL | Highest within modeled scope | Bounded by model coverage | Strong; deterministic, reproducible | Low | dbt Semantic Layer, Looker Conversational Analytics, Cortex Analyst |
| Typed tool/function calling over query templates ("trusted assets") | LLM fills parameters of vetted queries | Highest for covered intents | Narrowest | Strongest | Lowest | Databricks Genie trusted assets, Power BI verified answers, ClaimClarity |
| Code-interpreter / sandbox agents (pandas, DuckDB in a container) | LLM writes code iteratively | Strong on multi-step analysis when context is rich | Very high | Requires OS-level sandboxing | High | OpenAI's internal data agent, DABstep leaders, Hex/Julius-style notebooks |
| Multi-agent pipelines (classify → retrieve → generate → fix → synthesize) | Mix | Improves robustness | Medium–high | Depends on the generator | Highest | Snowflake Cortex Analyst, LinkedIn SQL Bot, CHASE-SQL |

**Where things converged in 2025–2026: a layered hybrid.** Mature products combine three layers:
- a governed, deterministic path for known metrics (semantic layer or trusted assets);
- retrieval of verified question→SQL examples to steer generation;
- a free-form generation path for everything else, wrapped in validation and labeled as less trusted.

The vendors say this plainly:
- **dbt Labs** recommends "Text-to-SQL for ad hoc analyses and smaller datasets. Semantic Layer for enterprise use where accuracy is critical and datasets are large, complex, or messy." They also note the trade-off: "the Semantic Layer can only answer questions that fall within the scope of what's been modeled."
- **Google** says Looker's semantic layer "reduces data errors in gen AI natural language queries by as much as two thirds" (internal testing). Its 2026 Conversational Analytics material describes "Golden Queries" so agents "retrieve verified business logic rather than guessing at SQL joins."
- **Databricks Genie** uses trusted assets, which are parameterized queries or UDFs it executes directly and badges as "trusted" in the UI. Its SQL examples only guide generation.

**The strongest recent evidence for ClaimClarity's design** is an August 2026 arXiv paper, "Bounded Semantic Planning and Deterministic Compilation for Reliable Enterprise Text-to-SQL." In it:
- A multi-turn planner may only select from "dynamically retrieved, question-specific semantic alternatives." It "cannot name a table, column, join predicate, role predicate, or SQL fragment."
- Code does graph traversal, grain handling, SQL construction, and governance checks.
- On a 38-question adjudicated set (three runs each), the system was correct on every run for 37 questions (97.4%), vs. 21 (55.3%) for direct DDL-to-SQL.
- It produced one refusal and no wrong-but-executed runs. The baseline produced 29 wrong-but-executed runs.
- The authors note that the comparison is not causal: the compiled system also received governed semantic artifacts.

This is essentially ClaimClarity's Investigator/registry/compiler split, published with numbers.

**Anthropic's "Building effective agents" guidance** points the same way. Workflows (predefined code paths) "offer predictability and consistency for well-defined tasks." Agents fit when "flexibility and model-driven decision-making are needed at scale." The advice is to find "the simplest solution possible."

### 2. What production teams learned

**OpenAI's in-house data agent** (engineering blog, January 29, 2026):
- **Scale:** serves 3.5k+ internal users over 600+ PB and 70k datasets, on GPT-5.2.
- **Six context layers:** table usage and lineage; human annotations; "Codex enrichment," which derives table meaning from pipeline code; institutional knowledge from Slack, Docs, and Notion with permissions; memory; and runtime inspection of live schemas.
- **Memory:** saves user corrections, with user confirmation, at global or personal scope. This keeps "non-obvious corrections, filters, and constraints" from being re-learned on every question.
- **Clarification:** asks clarifying questions, but "if no response is provided, it applies sensible defaults" (e.g., last 7 or 30 days).
- **Workflows:** packages recurring analyses into reusable instruction sets.
- **Self-correction:** treats a zero-row result as a signal of a bad join or filter, not as proof that no data exists.
- **Access:** offered via Slack, web, IDE, and MCP.
- **Stated lessons:** "Less is More," "Guide the Goal, Not the Path," and "Meaning Lives in Code."
- **Evaluation:** secondary summaries say OpenAI evaluates against curated question/answer pairs, comparing both the SQL and the resulting data with an Evals grader, without requiring exact SQL match.

**Snowflake Cortex Analyst** (engineering blog) uses a multi-agent pipeline:
- A **classification agent** rejects ambiguous or non-SQL questions and suggests answerable alternatives instead of guessing.
- **Feature extraction** detects time-series, period-over-period, or ranking questions so it can use tailored prompts.
- **Context enrichment** retrieves verified queries and **relevant literals**, closing gaps like "USA" vs. "United States of America." Snowflake calls incorrect literal generation "a common failure mode."
- **Several LLMs** generate SQL against a simplified logical schema, which is then post-processed to physical SQL.
- An **error-correction agent** uses the SQL compiler.
- A **synthesizer** produces the final answer.
- Snowflake claims "90%+ SQL accuracy on real-world use cases." This is vendor-reported.
- Its 2026 Evaluations feature uses verified queries as the test set, holding the selected queries out of the model so it can't simply copy them. It reports accuracy and **regressions** (previously correct queries that now fail). Snowflake's best-practice guide recommends starting with 10–20 verified queries.

**Uber QueryGPT:**
- Moved from simple RAG to a pipeline of workspaces (curated domain SQL samples and schemas), an intent agent, a table agent with human confirmation, and a column-prune agent.
- Built an eval set from real logged questions with manually verified intent, tables, and golden SQL.
- Measured in two modes: "Vanilla" (end-to-end) and "Decoupled" (with human-in-the-loop table selection).
- Uber's own QueryGPT post (September 19, 2024) gives the inputs behind the savings: "approximately 1.2 million interactive queries each month," about 36% of them from Operations, with authoring time cut from "around 10 minutes" to "about 3 minutes." The widely repeated ~140,000 hours/month figure comes from third-party write-ups (Wren AI, Sequel.sh), not Uber's post, so treat it as secondary.

**LinkedIn SQL Bot** (Trino Summit 2024 deck) gives five strategies: quality metadata, query validation (do the tables and columns exist? will it run?), interactive chat UX, self-serve customization, and ongoing benchmarking. The same deck (December 11, 2024) says integrating with existing analytics platforms such as DARWIN "Drove 5-10X increase in user adoption." On its benchmark, adding example queries raised table recall by 16% and field recall by 14%, and adding a self-correction agent cut hallucination by 14%.

**Pinterest:**
- First-shot SQL acceptance rose from 20% to over 40%.
- Table-retrieval hit rate was 40% without table documentation in embeddings and rose "linearly ... up to 90%" as documentation weight increased.
- A later post indexes historical queries with validated join keys and success rates as "unified context-intent embeddings."

**Microsoft Fabric data agent:**
- Retrieves the **top 3** most similar example queries per question by vector search.
- Validates every example against the schema and drops invalid ones.
- Allows up to 100 examples per data source.
- Ships an SDK that scores examples (clarity, relatedness, mapping), detects conflicting examples, and runs evaluations against ground-truth Q&A pairs.

**Databricks Genie's** production guide reports a team reaching only 54% benchmark accuracy after the "foundational" metadata iterations (names, descriptions, join keys, example values). More gains came from instructions, examples, and trusted assets, re-run against benchmarks after every change.

**The common lesson:** every successful team treats context curation and verified examples as the product, and measures them with a benchmark suite.

### 3. Benchmarks and SOTA techniques

**Public leaderboards are near-saturated and hard to interpret:**
- Genloop reported **96.70 on Spider 2.0-Snow** (March 2026). An independent audit notes the leaderboard's public site history stops in July 2026, so "96.70 is a verified current display value, not a complete provenance record."
- Oracle reports SOMA-SQL at **72.02% EX on Spider 2.0-Lite**.
- On BIRD, CHASE-SQL (73.0%), XiYan-SQL (75.63%), and Agentar-Scale-SQL (81.67%) lead with multi-candidate generation plus learned selection.
- A January 2026 UIUC audit (Jin, Choi, Zhu & Kang, arXiv:2601.08778) found annotation error rates of 52.8% in BIRD Mini-Dev and 62.8% in Spider 2.0-Snow. Re-scoring 16 open-source BIRD agents on a corrected subset changed their performance by −7% to +31% (relative) and moved their ranks by up to 9 places either way. Treat leaderboard deltas of a few points as noise.

**Enterprise and interactive benchmarks are far harder:**
- **BEAVER** (real private warehouses; ~101.5 tables and 869.4 columns per database): the strongest agentic method, ReFoRCE with Claude-4.5-Sonnet, reaches only **11.4%** execution accuracy.
- **BIRD-Interact** (ICLR 2026; ambiguity, clarification, follow-ups, CRUD): GPT-5 completes **8.67%** of tasks in conversational mode and **17.00%** in agentic mode.
- **DABstep** (450+ multi-step financial-analytics tasks from Adyen): the best agent originally scored ~14.55% on hard tasks. NVIDIA's KGMON Data Explorer later reached **89.95 on hard tasks** with Claude Haiku 4.5. It got there by building reusable, generalized tools in an offline learning phase and then running fast inference with them, which is effectively growing a function registry.
- **Text2Cypher:** Neo4j's 2024 dataset shows fine-tuning improves Cypher generation. A 2026 arXiv study found Neo4j's fine-tuned Gemma-3-27B text2cypher model "not satisfactory" on a complex graph because it "consistently selected incorrect relationship and node labels." XiYan-SQL scores 41.20% on NL2GQL. Text-to-graph-query remains less mature than text-to-SQL.

**What top systems do:**
- schema linking and value/literal retrieval;
- retrieval of similar verified examples;
- multiple diverse candidate generators;
- execution-guided repair loops (compiler errors, empty results);
- learned or pairwise candidate selection, which beats plain self-consistency (XiYan reports ~3 points lost without its selector);
- agentic exploration of the live database.

**The enterprise lesson:** these techniques add test-time compute to compensate for missing semantics. A governed semantic layer removes much of that need for known metrics.

### 4. Key techniques: consensus best practices

- **Context engineering:**
  - curated table and column descriptions, synonyms, business glossary, and metric definitions;
  - sample and low-cardinality values;
  - verified question→SQL pairs retrieved by similarity;
  - lineage and code-derived semantics (OpenAI);
  - a small, focused domain per agent (Fabric, Genie).
- **Entity and value resolution:** semantic search over literals before generation (Cortex). Ask for clarification only when the ambiguity is material, and otherwise apply stated defaults (OpenAI).
- **Validation and guardrails:**
  - parse to an AST and allow only a single SELECT over allowlisted objects;
  - check that tables and columns exist (LinkedIn);
  - compile and EXPLAIN before running;
  - enforce row, time, and memory limits.
- **Grounded numeric generation:** the model never types numbers that weren't bound from results. Placeholder binding with per-sentence checks is stricter than what most vendors document publicly.
- **Visualization:** deterministic chart specs from typed results. Do not let the LLM generate plotting code on the governed path.
- **Multi-turn state:** keep the structured plan, not the transcript, as the state to modify. Users must be able to interrupt and redirect (OpenAI).
- **Latency and UX:** stream progress, show the query or plan used, and badge verified vs. generated answers (Genie's "trusted" label).

### 5. Evaluation and observability

The consensus metric stack:
1. **Answer correctness** on golden question→expected-result pairs. Use result-set comparison, not exact SQL match.
2. **Wrong-but-executed rate.** This is the dangerous failure, and it is distinct from refusals.
3. **Refusal / out-of-scope rate.**
4. **Regression count** across releases (Cortex Evaluations).
5. **Online acceptance and feedback** (Pinterest first-shot acceptance; Genie upvotes feeding curation).

Run each question several times, because nondeterminism hides in single runs. dbt ran 11 questions × 20 runs, and the SPC paper three runs per question. Single-database execution accuracy can accept wrong queries that happen to coincide on one instance. Counterfactual or perturbed fixtures catch grain and fan-out errors.

For tracing, the **OpenTelemetry GenAI semantic conventions** have become the vendor-neutral schema. They define `invoke_agent`, `execute_tool`, and `chat` spans with `gen_ai.conversation.id`, `gen_ai.tool.name`, and token usage. Prompt and response content goes in span events that "can be filtered or dropped at the Collector level." That matters for PHI. Langfuse, Arize Phoenix, Braintrust, and LangSmith all ingest these spans. The conventions were long marked experimental, so pin a version.

### 6. Security and governance for PHI-adjacent data

- **SQL is code.** DuckDB's security model: it "has no internal privilege boundary and no notion of an untrusted user inside the engine," and executing untrusted SQL "is unsafe by design." The mitigations are `enable_external_access = false`, `allowed_directories`, disabling extension autoload/autoinstall, `lock_configuration = true` set *after* loading tenant data, plus OS/container sandboxing. Prepared statements protect values only, not model-authored query structure.
- **Prompt injection through data is real.** The ICSE 2025 P2SQL paper (Pedro et al.) showed that attackers can plant prompt fragments in database records. When other users' queries return those rows, the LLM's behavior changes, with 1.0 success rates on most attack examples. Their finding: "adding restrictions in the prompt may not be fully effective." Effective defenses were database permission hardening, query rewriting, auxiliary LLM validation, and in-prompt data preloading.
- **Small-cell suppression has to cover derived values.** CMS's data use agreement (Form CMS-R-0235) states that "no cell (e.g. admittances, discharges, patients, services) 10 or less may be displayed," and that "no use of percentages or other mathematical formulas may be used if they result in the display of a cell 10 or less." CMS guidance also requires **complementary (counter-)suppression** when totals would let a suppressed cell be recovered. HHS de-identification guidance sets no universal k. For PHI-adjacent customer data, ClaimClarity's threshold of 20 is conservative, which is good. The threshold alone is not sufficient.
- **LLM data handling.** AWS documents that Bedrock is HIPAA eligible (under a BAA) and that it "doesn't store or log your prompts and completions" or use them for training. Optional model-invocation logging to CloudWatch or S3 creates a new PHI store if enabled. Some third-party sources claim model-specific HIPAA exclusions and abuse-detection retention; verify against the live AWS HIPAA reference before go-live.

### 7. Agent harness design: autonomy vs. constraint

The pattern most teams use is **tiered autonomy**:
- **Tier 0:** trusted, templated queries.
- **Tier 1:** semantic-layer composition.
- **Tier 2:** LLM-written SQL over curated views, validated and sandboxed.
- **Tier 3:** code-interpreter exploration, for analysts only.

Each tier is labeled in the UI and has its own evals. Loops are bounded by turns, tool calls, and wall-clock time. MCP has become the standard way to expose data tools: OpenAI's agent is reachable via MCP, and DuckDB/MotherDuck, Snowflake, and Databricks all ship MCP servers. In regulated settings the MCP tool surface should expose the *governed* operations, not raw SQL.

**The safe way to add a "flexible lane":**
1. Route to it only when the registry cannot cover the intent (explicit escalation, never a silent fallback).
2. Restrict generation to curated views with documented grain.
3. Validate the AST and allowlist.
4. Execute in a locked-down, tenant-scoped engine with limits.
5. Apply the same post-execution privacy filters.
6. Badge the answer as "generated."
7. Log it for review.
8. Promote recurring, verified queries into the template registry. This is the flywheel Cortex, Genie, and DABstep's leaders all exploit.

### 8. Graph DB vs. columnar analytics

ClaimClarity's workload is filter/group/aggregate over a one-hop join (837 CLM01 ↔ 835 CLP01) with CARC/group-code breakdowns. That is OLAP, and columnar engines like DuckDB over Parquet are the right fit. ADR-008's move matches the evidence. Graph databases and text-to-Cypher earn their keep on variable-length, multi-hop traversals (referral networks, entity resolution across many identifiers, fraud rings). Even then, text-to-Cypher accuracy trails text-to-SQL and fine-tuned models struggle with complex topologies. Keeping Neo4j only for payer-name search is defensible in the short term. But DuckDB's full-text search plus a small embedding index could remove a second data store and its PHI-handling surface.

## Gap Analysis: ClaimClarity vs. Best Practice

### What ClaimClarity already does well

| Practice | ClaimClarity | Assessment |
|---|---|---|
| LLM plans, code compiles; no model-written queries on the live path | Typed task registry → server-compiled parameterized SQL/Cypher | **Matches SOTA for governed accuracy** (dbt SL, Genie trusted assets, SPC paper) |
| Bounded agent loop | ≤4 model turns / 8 tasks / 25 s | Matches Anthropic's workflow-first guidance; stricter than most |
| Data minimization to the LLM | Investigator sees ≤20-row digests | Better than most published systems |
| Grounded numeric prose | Writer limited to server-bound `{{placeholders}}`, checked per sentence | **Ahead of** most public implementations |
| Entity/value resolution + HITL | Lexical + vector + initials payer search; ambiguity pauses the turn with a payer card; resume reruns stored tasks | Matches Cortex literal retrieval and BIRD-Interact's lesson that clarification is critical |
| Domain metric definitions centralized | Denial definition, min-group-size 20 | This is a semantic layer in all but name |
| Deterministic charts/tables | Server-built | Correct; avoids LLM plotting code |

### What is missing or risky (prioritized)

**P0: Evaluation harness and regression gates (missing).** Every reference system (Cortex Evaluations, Genie benchmarks, Uber, LinkedIn, OpenAI) treats this as the backbone, and nothing in the current design measures accuracy. Build:
- 50–150 golden questions written with the implementations team, each with an expected task plan **and** an expected result (numbers), on frozen LabCorp fixtures;
- 3–5 runs per question;
- separate tracking of correct, refused/clarified, and **wrong-but-answered** outcomes;
- counterfactual fixtures that break plausible-but-wrong logic: duplicate CLP rows, reversals, multiple remits per claim, payer aliases;
- separate tests for the payer resolver (precision/recall on aliases) and the Writer (placeholder-binding violations);
- a CI gate on registry, prompt, or model changes, failing on any regression of a previously passing question.

Grade plans structurally (task type, parameters) and results numerically. Use LLM-as-judge only for prose quality.

**P0: Coverage cliff and out-of-scope behavior.** Like any semantic layer, the registry answers 0% of what it doesn't model. Add:
- an explicit classification outcome ("not answerable with current analyses"), with suggested in-scope questions (Cortex pattern);
- logging of every unanswered or refused question into a ranked backlog that drives registry growth;
- the DABstep lesson: the winning agents *grow a reusable tool library*, and ClaimClarity's registry should grow the same way, from real demand.

**P1: Wire ADR-009 as an escalation lane, safely.** Requirements:
- **Routing:** only when the Investigator signals no registry fit. Show the user it is "exploratory / unverified."
- **Generation scope:** curated registry views only, with documented grain (one row per claim–remit pair), plus retrieved verified examples (top-k, Fabric-style).
- **Validation:** sqlglot AST; exactly one SELECT; allowlisted views and columns; no table functions (`read_parquet`, `read_csv`, `glob`); mandatory aggregation or LIMIT; EXPLAIN dry run.
- **Execution:** a separate DuckDB connection with `enable_external_access=false`, `allowed_directories` scoped to the tenant prefix, extension autoload off, `lock_configuration=true` set after views are created, memory/thread/timeout limits, and a row cap.
- **Post-execution:** the same min-group-size and complementary-suppression filter as the templated lane. The Writer gets only bound placeholders.
- **Rollout:** run in **shadow mode** first (generate and score against the eval set, never shown to users). Promote recurring correct queries into typed registry tasks.

**P1: Multi-tenancy and the DuckDB deployment choice.** DuckDB has no row-level security, no user model, and no internal privilege boundary. A **central DuckDB server on EC2 serving many customers** puts tenant isolation entirely on string-level query construction and creates a single blast radius. **Prefer embedded DuckDB per request on ECS Fargate**, with:
- per-customer Parquet prefixes;
- IAM/S3 bucket policies scoped per tenant;
- task-level credentials that can only read the current tenant's data.

Isolation then comes from infrastructure, not SQL. Fargate cold-start and Parquet-scan latency need benchmarking against the 25 s Investigator budget. Cache hot aggregates per tenant if needed. Persist SQLite conversations with a tenant key and plan for a managed store (e.g., Postgres/Aurora) before multi-customer production. SQLite on ephemeral Fargate storage does not survive tasks.

**P1: Small-cell suppression for derived values.** A threshold of 20 per group is stricter than CMS's 11. The CMS rule in Form CMS-R-0235, which is a useful reference standard, also forbids values that can be *derived* through "percentages or other mathematical formulas," such as total minus the visible groups, or percentages combined with known totals. Implement:
- complementary suppression when a table shows totals or shares;
- the same check on chart series and on Writer placeholders;
- the rule that the "other" bucket must also meet the threshold.

Codify this in the server, not in prompts.

**P1: Prompt injection through EDI content.** Free-text segments in 837/835 (e.g., NTE notes, payer or provider names) reach the Investigator through digests. The Writer's placeholder design already limits damage, and no model-written SQL on the live path removes P2SQL's main impact. The ADR-009 lane brings that exposure back. Mitigations:
- strip or escape free-text fields from digests unless needed;
- delimit data as data in prompts;
- never let digest content alter task-type selection without server validation;
- add injected-record fixtures to the eval set.

**P2: Observability with PHI-safe tracing.** Instrument turns with OTel GenAI spans:
- `invoke_agent` for the Investigator;
- `execute_tool` per task, with task type, compiled-query hash, row count, and latency;
- a `chat` span for the Writer;
- `gen_ai.conversation.id` from SQLite.

Drop prompt/response content events at the collector by default. Keep the structured plan and result fingerprints in an internal audit table instead. That gives auditability (who asked what, which compiled query ran on which tenant data) without shipping PHI to a third-party tracing SaaS. If Bedrock invocation logging is enabled, treat its destination as a PHI store under the bucket policy.

**P2: Learning loop and memory.** Add a reviewed "corrections" channel modeled on OpenAI's memory layer. When implementers correct a definition (e.g., how to treat reversals or a CARC group nuance), it becomes a versioned glossary or registry change, scoped per customer where needed. Don't let it silently enter as free-form model memory. Capture thumbs-up/down plus a reason on every answer, and feed it into the eval backlog.

**P2: Clarification policy and defaults.** Generalize the payer-card pattern to other material ambiguities (date basis: service vs. remit vs. paid date; denial definition variant). Where the ambiguity is minor, state the default assumption in the answer instead of blocking (OpenAI). Display the assumptions the server applied.

**P3: Latency and UX.**
- A 120 s turn deadline is long for chat. Stream progress events (resolving payer → running N analyses → writing).
- Show a "how this was computed" panel (task list, definitions, filters, compiled query) and a verified/exploratory badge.
- Cache task results per tenant, dataset version, and task signature, so follow-ups like "same, but by quarter" re-use work.

**P3: Neo4j footprint.** Keep the graph only where it adds value. If payer search is its sole live use, evaluate DuckDB FTS plus a small vector index to collapse to one engine per tenant. That means one fewer PHI store, one fewer access-policy surface, and simpler isolation. Do not adopt text-to-Cypher for this workload.

## Recommendations (sequenced)

1. **Weeks 0–3:** Build the golden-set eval harness (plans + numbers, multi-run, counterfactual fixtures, wrong-but-answered tracking) and CI regression gates. Add OTel GenAI tracing with content dropped at the collector.
2. **Weeks 2–5:** Ship out-of-scope classification and the unanswered-question backlog. Implement complementary small-cell suppression across tables, charts, and placeholders.
3. **Weeks 4–8:** Decide on per-tenant embedded DuckDB on Fargate with IAM-scoped Parquet prefixes. Move conversation storage to a managed, tenant-keyed store.
4. **Weeks 6–12:** Wire ADR-009 in shadow mode behind the sandbox and validator described above. Promote to user-visible "exploratory" answers only once the eval harness shows an acceptable wrong-but-answered rate on held-out questions. Promote recurring generated queries into registry tasks.
5. **Ongoing:** A reviewed corrections/glossary loop, verified-example curation, and quarterly re-validation of Bedrock HIPAA scope and logging settings with the corporate AI platform and security teams.

## Caveats

- Several headline accuracy figures are vendor-reported or from vendor-run benchmarks: Snowflake's "90%+," Google's "two thirds," Genloop's 96.70, and dbt's results on an 11-question benchmark. The SPC paper's authors note that their compiler was developed while inspecting the benchmark and that semantic knowledge was not held constant between arms.
- Spider 2.0 numbers differ sharply by variant (Snow vs. Lite vs. DBT) and over time. Leaderboard provenance has been questioned.
- OpenAI's evaluation details come partly from secondary summaries of its post.
- AWS HIPAA eligibility for specific Bedrock models, and any abuse-monitoring retention, should be confirmed on the live AWS HIPAA reference and under Xifin's BAA. Third-party claims about model exclusions were not verifiable on AWS pages.
- The DuckDB deployment recommendation rests on DuckDB's documented security model and general isolation principles. Latency on Fargate for LabCorp-scale data has to be measured.
