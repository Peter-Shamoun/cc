# Conversational Analytics Agents: SOTA (2025–2026)

The 2026 consensus for chat-over-data agents: the model decides *what* to compute, governed code decides *how*, and accuracy comes mostly from curated context and a measured eval loop, not from the model choice.

## Two running examples

The two strongest 2026 designs sit at opposite ends of the flexibility–governance range; each section below shows how both handle it.

**OpenAI's in-house data agent** (Jan 2026) is the flexible end. It serves 3.5k+ internal users over 600+ PB and 70k datasets, and writes its own SQL against a deep context stack. Its named lessons: less is more, guide the goal not the path, meaning lives in code.

**The bounded-planning compiler** (arXiv, Aug 2026) is the governed end. A planner picks only from retrieved semantic options and never names a table, column or join; deterministic code builds the SQL. Over 38 questions × 3 runs it was always correct on 97.4% vs. 55.3% for direct SQL, with 0 wrong-but-executed runs vs. 29.

| | OpenAI data agent | Bounded planner |
| --- | --- | --- |
| Who writes the query | Model | Compiler |
| Best for | Exploratory questions over huge, varied data | Governed metrics where a wrong number is costly |
| Questions outside scope | Attempts them with context | Refuses |
| Ambiguity | Asks, else applies a stated default | Refuses or asks |
| Learns from use | Saves user-confirmed corrections | Analysts extend the semantic model |
| Main failure | Plausible query, wrong join or grain | Coverage cliff |

## Architecture: a layered hybrid

Products converged on tiers of autonomy, each labeled for the user and evaluated on its own:

1. **Trusted queries:** vetted, parameterized templates the model only fills (Databricks Genie "trusted assets", Power BI verified answers).
2. **Semantic layer:** the model picks metrics, dimensions and filters; an engine compiles SQL (dbt Semantic Layer, Looker, Snowflake semantic views). This is the bounded planner's tier.
3. **Generated SQL:** the model writes SQL over curated views, then it is validated and sandboxed. This is OpenAI's tier.
4. **Code execution:** the model writes Python or SQL iteratively in a sandbox, for analysts.

The evidence for putting governed tiers first is consistent. In dbt Labs' April 2026 benchmark, Claude Sonnet 4.6 scored 90.0% writing raw SQL and 98.2% through the semantic layer; GPT-5.3-Codex went from 84.1% to 100%. The price is coverage: a semantic layer answers only what was modeled.

Two patterns connect the tiers:

- **Escalate, never silently fall back.** A question drops to a lower tier only when the higher one can't express it, and the answer is badged (Genie marks "trusted" answers).
- **Promote what recurs.** Generated queries that keep coming back correct get turned into trusted ones. NVIDIA's DABstep winner (89.95 on hard tasks with Claude Haiku 4.5) did this offline: it distilled reusable tools first, then answered fast with them.

**Agent loops stay bounded:** caps on turns, tool calls and wall-clock time. MCP is now the standard way to expose data tools (OpenAI's agent, Snowflake, Databricks and DuckDB all ship MCP servers); in governed settings it exposes operations, not raw SQL.

## Context engineering: the main lever

Every team that published 2026 results treats curated context as the product. Model upgrades move accuracy less than context does.

**OpenAI's six context layers:**

1. Table usage and lineage: which tables people actually query and join.
2. Human annotations on tables and columns.
3. Meaning read from the pipeline code that builds each table.
4. Institutional knowledge from docs and chat, respecting permissions.
5. Memory of user-confirmed corrections, at global or personal scope.
6. Live schema inspection at query time.

**The bounded planner** reaches the same goal from the other side: the context *is* the semantic model, so the planner sees only valid choices, retrieved per question.

What the rest of the field adds:

- **Verified examples beat descriptions.** One Databricks Genie team plateaued at 54% after a full metadata pass (names, descriptions, join keys, sample values); instructions, verified examples and trusted queries closed the gap. Microsoft Fabric retrieves the 3 most similar verified queries per question and validates each against the schema.
- **Retrieve literals, not just schema.** Match the user's words to actual stored values (a payer's alias, a status code) before generating; wrong literals are a common silent failure.
- **Keep each agent's domain small.** Fabric and Genie both advise one focused subject area per agent over one agent for the warehouse.
- **Less is more.** OpenAI found that pruning context and giving goals rather than step-by-step paths improved results.

## Ambiguity and multi-turn conversation

Conversation is where agents fail most. On BIRD-Interact (ICLR 2026), which tests ambiguity, clarification and follow-ups, GPT-5 completes 8.67% of tasks in conversational mode and 17.00% in agentic mode.

- **Clarify only when it changes the answer.** OpenAI's agent asks, but if no reply comes it applies a sensible default (e.g. the last 7 or 30 days) and states it. Minor ambiguity gets a stated assumption, not a blocking question.
- **Out of scope is an answer.** The bounded planner refuses rather than guesses; good products pair the refusal with answerable alternatives and log the miss as a coverage gap.
- **The plan is the state.** Follow-ups ("same, by quarter") modify the structured plan from the last turn rather than re-deriving from the transcript.
- **Let users steer.** OpenAI's agent can be interrupted and redirected mid-analysis, and shows its work so users can spot a wrong assumption.

## Execution safety and grounded answers

The dangerous failure isn't an error; it's a query that runs and returns a plausible wrong number. The bounded planner's 29 → 0 wrong-but-executed runs is the clearest measure of what governance buys.

**Before running generated SQL:**

- Parse to an AST and allow exactly one SELECT over allowlisted views and columns.
- Check names exist, compile, and EXPLAIN as a dry run.
- Cap rows, time and memory; require an aggregate or LIMIT.
- Sandbox the engine itself. DuckDB's 2026 security docs state it has no internal privilege boundary, so untrusted SQL needs `enable_external_access=false`, `allowed_directories`, extension autoload off and `lock_configuration=true`, plus container isolation. Prepared statements protect values, not query structure.

**After running:**

- **Distrust surprising results.** OpenAI's agent treats zero rows as a sign of a bad join or filter and retries before reporting "no data".
- **Never let the model type numbers.** Bind figures from results into the prose rather than letting the writer restate them.
- **Draw charts from typed results** with deterministic specs, not model-written plotting code.
- **Show provenance.** Display the plan or query used and badge verified vs. generated answers.

## Evaluation and observability

A domain-specific eval set, run on every change, is the backbone of every system above. Public benchmarks don't substitute for it.

**Why not leaderboards:** scores are near-saturated (96.70 on Spider 2.0-Snow, 81.67% on BIRD), and a Jan 2026 audit found annotation errors in 52.8% of BIRD Mini-Dev and 62.8% of Spider 2.0-Snow; re-scoring moved agents up to 9 ranks. Meanwhile the best agent reaches 11.4% on BEAVER's real enterprise warehouses.

**What to measure:**

1. **Answer correctness** on golden question → expected-result pairs, compared by result, not exact SQL (OpenAI grades both the query and its output).
2. **Wrong-but-executed rate**, tracked apart from refusals.
3. **Refusal and clarification rate.**
4. **Regressions:** previously correct questions that now fail (Snowflake Cortex Evaluations, 2026, which holds verified queries out of the model's context while testing).
5. **Online feedback**, fed back into curation.

**How:** run each question several times (the bounded planner used 3 runs; dbt used 20), since single runs hide nondeterminism. Add perturbed fixtures, because one dataset can make a wrong query look right. Block releases on regressions.

**Tracing:** the OpenTelemetry GenAI conventions are the 2026 standard (`invoke_agent`, `execute_tool`, `chat` spans, conversation id, token usage), ingested by Langfuse, Arize Phoenix, Braintrust and LangSmith. Prompt and response content sits in droppable span events. Pin a convention version, as parts are still experimental.

## Regulated data

For health or financial data, the consensus is that privacy rules live in infrastructure and server code, never in prompts.

- **Isolate tenants in infrastructure.** Engines without a privilege boundary (like DuckDB) should get per-tenant data paths and scoped credentials, so a bad query can't reach another customer's data.
- **Suppress small cells, including derived ones.** The CMS data use agreement bars cells of 10 or fewer and any percentage or formula that reveals one, and requires complementary suppression when totals would expose a hidden cell.
- **Treat stored text as untrusted input.** The P2SQL paper (ICSE 2025) showed prompt fragments planted in database rows hijack the model for other users' queries; prompt-level rules alone didn't stop it. Strip or delimit free text, and validate every model choice in code.
- **Minimize what reaches the model.** Send the model schemas, plans and result summaries rather than raw rows wherever the task allows.
- **Know the model host's data handling.** AWS documents Bedrock as HIPAA eligible under a BAA, without storing or training on prompts; optional invocation logging to S3 or CloudWatch creates a new PHI store if enabled. Drop prompt content from traces before they reach third-party tools.

## Sources

- [OpenAI: Inside our in-house data agent (Jan 2026)](https://openai.com/index/inside-our-in-house-data-agent/)
- [Bounded Semantic Planning and Deterministic Compilation (arXiv, Aug 2026)](https://arxiv.org/pdf/2608.16663)
- [dbt Labs: Semantic Layer vs. text-to-SQL (2026)](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026)
- [Databricks: Production-ready Genie spaces](https://www.databricks.com/blog/how-build-production-ready-genie-spaces-and-build-trust-along-way)
- [Microsoft Fabric: Data agent example queries](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-example-queries)
- [Snowflake: Cortex Analyst Evaluations](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst-evaluations)
- [NVIDIA: DABstep first place](https://huggingface.co/blog/nvidia/nemo-agent-toolkit-data-explorer-dabstep-1st-place)
- [BIRD-Interact](https://www.alphaxiv.org/abs/2510.05318) · [BEAVER results (arXiv, 2026)](https://arxiv.org/pdf/2608.27796)
- [Spider 2.0 leaderboard audit](https://www.nl2sql.ai/news/spider2-live-leaderboard-public-repo-audit-gap) · [Genloop: Spider 2.0 (Mar 2026)](https://genloop.ai/blogs/genloop-is-1-on-spider-2.0)
- [OpenTelemetry GenAI conventions in 2026](https://dev.to/gabrielanhaia/opentelemetry-genai-semantic-conventions-your-llm-traces-should-look-like-this-in-2026-3ff6)
- [DuckDB: Securing DuckDB](https://duckdb.org/docs/current/operations_manual/securing_duckdb/overview)
- [P2SQL: prompt-to-SQL injection (ICSE 2025)](https://arxiv.org/pdf/2308.01990v4)
- [ResDAC: CMS cell size suppression](https://www.resdac.org/articles/cms-cell-size-suppression-policy)
- [AWS: Bedrock data protection](https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html)

Some figures are vendor-run (dbt's benchmark, Genloop's score), and the bounded-planning authors note their comparison isn't causal.
