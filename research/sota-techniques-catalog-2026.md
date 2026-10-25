# Techniques for LLM Data Agents (2024–2026): A Catalog of Experimental Factors for ClaimClarity

Oct 25, 2026 · research catalog · companion file: `research/technique_factors.yaml`

**Scope.** This catalog covers techniques for agents that answer natural-language analytics questions over a relational/Parquet warehouse: text-to-SQL, semantic-layer "typed task" agents, and conversational data agents. For each technique it gives the measured effect, the benchmark it was measured on, the cost, a concrete implementation in the ClaimClarity harness (`bench/` + `claude/schema/04_agent_interface.py`), interactions, and risks. The target is an internal assistant for a clinical lab's 837P/835 data in DuckDB, reached through `sem.*` views with a lossless layer for escalation, running on **Claude Sonnet and Haiku only**.

**What this adds to the earlier reports.** `agent-chatbot-best-practices.md` and `conversational-analytics-agents-sota-2026-full-report.md` set out the architecture (governed lanes, verified queries, PHI controls, evaluation backbone). They did not break the field into separable techniques with effect sizes. This catalog does that, so each technique can become an ablation factor. It also adds evidence those reports lacked: component ablations (CHASE-SQL, ReFoRCE, MAC-SQL, DAIL-SQL, DS-STAR), a 2026 null-result study on schema linking and self-consistency, the Cube 2026 paired benchmark on Claude Sonnet 4.6, BIRD-Interact per-model numbers including Claude, EHRSQL's reliability metric, and the 2026 Claude API constraints that change how some techniques can be built.

---

## 1. The conclusions that matter most

1. **Business context is the largest single lever, and it is cheap.**
   - Adding BIRD's one-sentence "evidence" raises GPT-4 from 34.88% to 54.89% execution accuracy (+20 pp) ([BIRD](https://arxiv.org/abs/2305.03111), test set).
   - A ~2,200-token hand-written semantic document raises Claude Sonnet 4.6 from 46.5% to 68.7% (+22.2 pp, p ≤ 0.0015), and Opus 4.7 and GPT-5.4 by similar margins ([Cube 2026](https://arxiv.org/abs/2604.25149)).
   - With that document, the three models are statistically indistinguishable. Without it, they are also indistinguishable. Context explains the variance; model choice within the tier does not.
   - For ClaimClarity this means the glossary, the definitions that must not be reinterpreted, and the "common mistakes" list are the first factors to ablate. It also means Haiku may be viable once context is right.

2. **Typed tasks over a semantic layer beat free SQL on governed metrics, mainly by removing silent wrong answers.**
   - dbt 2026: Sonnet 4.6 scores 90.0% with text-to-SQL vs 98.2% through the semantic layer ([dbt](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026)).
   - Bounded planning + compiler: 97.4% vs 55.3% for direct SQL, with 0 vs 29 wrong-but-executed runs ([arXiv 2608.16663](https://arxiv.org/abs/2608.16663)).
   - The cost is a coverage cliff: questions the layer does not model score 0%, but as refusals rather than wrong numbers.
   - ClaimClarity already has the typed tool (`query_metrics`). The bench's baseline agent (`claude-sql`) does not use it. **The most informative first experiment is `claude-sql` vs a `query_metrics`-first agent on the same 33+ questions.**

3. **Inference-time compute techniques give small, model-dependent gains, and the gains shrink as models get stronger and context gets better.**
   - **Self-correction from execution errors** is the only component with a consistent gain in a 2026 multiple-comparison-corrected BIRD study: +1.0 to +4.4 pp at 1.1–1.5× tokens. It gave no significant gain on the strongest model tested ([arXiv 2606.29733](https://arxiv.org/abs/2606.29733)).
   - **Self-consistency** gave +0 pp at 5× tokens there.
   - **Schema linking** gave no gain, and significantly hurt Llama-70B (−4.2 pp).
   - Classic pipeline papers report larger gains, but on older or weaker models: CHASE-SQL's selector is worth +4.2 pp over self-consistency (Gemini 1.5), and MAC-SQL's refiner +4.6 pp (GPT-4).
   - Treat these as cheap-to-test, low-prior factors. Most of them matter only on the free-SQL lanes.

4. **Schema pruning is the wrong lever for a small curated layer.** With 10 `sem.*` views, filtering the schema can only lose recall. On BIRD, filtering lowered accuracy by 2.7–11.6 pp for strong models ([Death of Schema Linking](https://arxiv.org/abs/2408.07702)). Pruning or compression pays only where the schema overflows the context or crowds it, which in ClaimClarity means the lossless layer (41 tables), not `sem.*` ([ReFoRCE](https://arxiv.org/abs/2502.00675): −3.65 pp when compression is removed, mainly because of context overflow).

5. **Retrieved worked examples are the second-best-evidenced lever.**
   - On Spider: DAIL-SQL with GPT-4 goes from 72.3% zero-shot to 82.4% with 5 similarity-selected examples. Similarity selection beats random examples by about 3 pp.
   - LinkedIn: examples raised table recall by 16% and field recall by 14%.
   - Anthropic: tool-use examples raised complex-parameter accuracy from 72% to 90% in internal testing.
   - For ClaimClarity, the examples should be **tool calls** (`query_metrics` JSON), not only SQL. They must come from a split disjoint from the test questions, as Cortex Analyst evaluations do.

6. **Ambiguity and abstention are where agents fail most, and the current bench cannot measure them.**
   - BIRD-Interact full set: Claude Sonnet 4 completes 14.17% of conversational follow-up tasks and 12.67% of agentic ones; GPT-5 completes 8.67% conversational ([BIRD-Interact](https://arxiv.org/abs/2510.05318)).
   - AMBROSIA: models produce all valid interpretations of an ambiguous question less than 2% of the time.
   - Every `bench/questions.yaml` item is written to be unambiguous ("one right answer"). Clarification and abstention factors therefore need a new ambiguous / out-of-scope split, scored with a reliability metric that penalizes wrong answers. EHRSQL's RS(c) is the published template.

7. **Model tier and routing.** With a semantic layer or a precomputed function library, cheap models get close to expensive ones:
   - NVIDIA's DABstep winner runs **Claude Haiku 4.5** at inference (89.95 on hard tasks, about 20 s per task), on a helper library and pitfalls list distilled offline by Opus/Sonnet. Claude Code + Opus 4.5 scored 66.93 on the same hard tasks.
   - EllieSQL complexity routing cut tokens by more than 40% with no accuracy loss on BIRD.
   - Recommended production hypothesis: Haiku on the typed lane, Sonnet for escalation and SQL lanes, with "re-run failures at higher effort or tier."

8. **The 2026 Claude API changes several textbook techniques.** These are facts about the API, not research findings; check them against the live docs.
   - `temperature`, `top_p` and `top_k` return a 400 on every model since 2026-10-21. Self-consistency by temperature sampling is gone; candidate diversity has to come from different prompts or generators (CHASE-style), contexts, or models.
   - On Sonnet 5.5, thinking cannot be disabled (`between_tools` is the off switch) and forced `tool_choice` returns a 400. "Force the final_answer tool" designs need `auto` + `strict: true` or structured outputs.
   - Effort (`output_config.effort`) is the reasoning-budget knob.
   - Prompt-cache reads cost 0.1× input (Sonnet 5.5: $0.20/MTok) with a 512-token minimum. Mid-conversation `system` messages let per-turn content (retrieved examples) be added without breaking the cached prefix.
   - **The bench's baseline sets `max_tokens=2000` with adaptive thinking on by default. This can truncate thinking-heavy turns and score them as "no answer". Fix it before measuring anything.**

9. **The evaluation design decides what can be learned.** At 33 questions, no factor with a true effect under about 15 pp can be distinguished from noise. Paired comparisons need roughly 150 or more questions × 3 repeats to detect around 8–10 pp effects. Score with the existing strict matcher first and the calibrated judge second. Report pass^k, the wrong-but-executed rate, refusal rate and RS(10) separately. Public leaderboards are not a guide to effect sizes: 52.8% of BIRD Mini-Dev and 62.8% of Spider 2.0-Snow items carry annotation errors, and re-scoring moved agents by up to 9 ranks ([arXiv 2601.08778](https://arxiv.org/abs/2601.08778)).

10. **PHI guardrails belong in code; the experiments should measure their cost (over-refusal) rather than their necessity.** Prompt-level restrictions do not stop prompt-to-SQL injection ([P2SQL](https://arxiv.org/abs/2308.01990)). The sem layer's PHI minimization, `guard.py`, the sandbox and small-cell suppression are fixed constraints, not factors. Prompt-level refusal wording is a factor, scored on safety questions (must refuse) *and* benign look-alikes (must not refuse).

---

## 2. The setting the factors plug into

**The harness, as it stands.**
- `bench/eval.py` runs agents (`answer(question, tools) -> Answer`) over a frozen, hash-keyed DuckDB snapshot of `sem.*` (plus PHI-stripped `lossless.*` for escalation questions).
- Truth comes from gold SQL that is cross-checked against an independent generator ledger.
- A strict value-based matcher (`compare.py`) scores answers. An optional LLM judge (`judge.py`) has a `calibrate` command.
- Safety questions pass only on refusal. Repeats give pass^k.
- There are 33 questions: denials 10, money 4, rework 4, safety 4, volume 2, lines 2, payers 2, patient 2, cob 1, quality 1, escalation 1.

**Agents.**
- `ClaudeSQLAgent` is the baseline: Sonnet 5.5, three tools (`run_sql`, `final_answer`, `refuse`), the schema plus glossary in the system prompt, at most 8 turns, and no caching, effort or examples.
- The production interface in `04_agent_interface.py` already implements:
  - a metric/dimension compiler with five tools (`query_metrics`, `lookup_values`, `get_claim_timeline`, `describe_semantic_layer`, `run_semantic_sql`);
  - a gated `request_lossless_query`;
  - a lexical `similar_verified_requests(k=3)` over 13 verified requests;
  - "did you mean" suggestions in errors (`_suggest`).

**Implication.** Most techniques in this catalog already exist as code. What is missing is a single **configurable agent** that turns each one on or off. §6 proposes that agent (`bench/agents_factorial.py`, not yet written) and an experiment plan.

**Model facts used below.** These come from the Claude API skill reference (cached 2026-10-06); confirm on the live pricing page before budgeting.

| Model | ID | Input / output $/MTok | Cache read | Thinking | Default effort |
|---|---|---|---|---|---|
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | $2 / $10 | $0.20 | Adaptive, always on (`between_tools` turns it off) | high |
| Claude Haiku 5.5 | `claude-haiku-5-5` | $0.10 / $0.50 (≤100K-token prompts) | ~0.1× | Adaptive, on by default (disable allowed at effort ≤ high) | medium |

---

## 3. How to read the evidence

- **Benchmarks don't transfer.**
  - Spider-1 numbers mostly reward compositional SQL fluency.
  - BIRD rewards value grounding and external knowledge.
  - Spider 2.0, BEAVER and LiveSQLBench reward schema exploration and multi-step reasoning on large schemas (LiveSQLBench tops out in the low 40s%).
  - BIRD-Interact and AMBROSIA reward dialogue.
  - ClaimClarity's typed lane resembles none of them. It is closest to the dbt/ACME, Cube/Contoso and bounded-planning setups: a modeled domain, governed metrics, a small schema.
- **Gains shrink on strong models.** Many technique papers report against GPT-4/Gemini-1.5-era models. The "Death of Schema Linking" study, ReFoRCE and the 2026 on-prem study all find that stronger reasoning models internalize schema selection, so external linking, retrieval and voting add less. Expect the Sonnet 5.5 deltas to be smaller than the published ones, and the Haiku 5.5 deltas to fall somewhere in between.
- **Vendor and self-reported numbers** are labeled as such: dbt, Cube, AtScale, Snowflake, Databricks, NVIDIA, Anthropic and OpenAI internal evals. Treat them as directional.
- **Effect notation in the YAML:** `+x pp` means an absolute percentage-point gain in execution accuracy (EX) unless another metric is named.

---

## 4. Technique catalog

Each entry has the same fields: **What** · **Evidence** (effect, benchmark, model) · **Cost/latency** · **In our harness** · **Interactions** · **Risks**.

### A. Knowledge and context

#### T01. Business glossary and definition injection
- **What:** Put the governed business definitions in the system prompt: what counts as a claim, a current adjudication, a denial or a primary payer, the date bases, and which columns not to use. This is the same thing as BIRD's "evidence" and Cube's semantic document.
- **Evidence:**
  - BIRD (GPT-4, test set): 34.88% → 54.89% with evidence (+20.0 pp). Human experts gain about the same (+20.6 pp) ([BIRD](https://arxiv.org/abs/2305.03111), as reported in [Cube 2026](https://arxiv.org/abs/2604.25149)).
  - Cube 2026 (100 questions, ClickHouse Contoso, paired single-shot): +17 to +23 pp for Opus 4.7, **Sonnet 4.6 (46.5% → 68.7%)** and GPT-5.4, from a 4 KB / ~2.2k-token document.
  - Bayer pharmacovigilance (JAMIA Open 2024, as reported in Cube 2026): GPT-4 goes from 8.3% to 78.3% with a business-context document. **Narrowing the schema without the document only cut failures to 50%.**
  - Databricks Genie case: 53% → 80% with metadata → 100% with metric definitions and example SQL. This is vendor-reported, on 15 questions.
- **Cost/latency:** +2–5k cached tokens. Cube measured Sonnet latency at 9.6 s → 11.4 s and input at 14.4k → 17.3k tokens. With prompt caching (T26) the marginal cost is close to zero.
- **In our harness:** `GLOSSARY` in `bench/agents.py` and `05_agent_system_prompt.md` already exist. Factor levels:
  - `none`: schema only.
  - `glossary`: the current text.
  - `glossary+invariants`: add the "definitions you must not reinterpret" and the 835/837 facts from `claude/schema/README.md` (a PCN is not a key; reversals negate; SVC06 vs SVC01; REF*6R).
  - `glossary+invariants+pitfalls`: add a "common mistakes" list. **Derive it from a training split of failure traces, never from the test `wrong_sql`.**
- **Interactions:** Complementary to T08: the compiler enforces the definitions, the glossary explains them. Under T08 its marginal value drops, but it still guides metric selection. It is a precondition for T25 (cheap model tiers). A longer glossary raises the prompt-cache value (T26).
- **Risks:** Prescriptive glossaries can hurt: OpenAI found "highly prescriptive prompting degraded results" (T06). A stale glossary actively misleads. And a pitfalls list built from the test set is leakage.

#### T02. Data dictionary: column descriptions, grain, keys, units
- **What:** Annotate each view and column with a description, its grain ("one row per claim+payer adjudication event"), keys, join paths, units and nullability semantics. This is distinct from T01, which covers business rules.
- **Evidence:**
  - DAIL-SQL: foreign-key information in the prompt "significantly improves" EX on Spider. The "with no explanation" instruction added 0.6–2.9 pp ([DAIL-SQL](https://arxiv.org/abs/2308.15363)).
  - Pinterest: table-retrieval hit rate rose from 40% toward 90% as documentation weight grew (prior report).
  - Databricks: a team plateaued at 54% after "foundational" metadata alone, so metadata is necessary but not sufficient.
  - OpenAI: "meaning lives in code". Deriving table semantics from pipeline code beat warehouse metadata ([OpenAI data agent](https://openai.com/index/inside-our-in-house-data-agent/), via [ZenML summary](https://www.zenml.io/llmops-database/building-an-ai-data-agent-for-enterprise-scale-analytics)).
- **Cost/latency:** +1–4k cached tokens.
- **In our harness:** `Tools.describe()` currently emits only `name type` per column. Levels:
  - `types_only` (current);
  - `+descriptions+grain` from `03_semantic_model.yaml` `views:`;
  - `+derivation`: a one-line SQL lineage per derived column, taken from `02_semantic_layer_views.sql` (the "meaning lives in code" analog).
- **Interactions:** Overlaps T01. Under T08 the agent sees the catalog (`describe_semantic_layer`) rather than columns, so T02 matters mainly for the SQL lanes.
- **Risks:** Bloat (see T06). Descriptions can drift from the view SQL; generate them from the YAML so they stay in sync.

#### T03. Data profiles and sample values
- **What:** Per-column statistics: null rate, distinct count, top-k values, min/max, value shape. Low-cardinality value lists go in the prompt.
- **Evidence:**
  - AT&T's "Automatic Metadata Extraction" reached #1 on BIRD (March–May 2025) with profiling-derived metadata alone (GPT-4o), beating BIRD's supplied metadata; fused metadata did best ([arXiv 2505.19988](https://arxiv.org/abs/2505.19988)). The AskData system reaches 81.95% on the BIRD test set.
  - CHASE-SQL: removing LSH value retrieval costs −2.92 pp on BIRD dev ([CHASE-SQL](https://arxiv.org/abs/2410.01943)).
  - Snowflake calls wrong literals "a common failure mode" (prior report).
- **Cost/latency:** Built offline (DuckDB `SUMMARIZE` per view per snapshot). +1–3k cached tokens.
- **In our harness:**
  - Add `profile.json` to the dataset snapshot.
  - Levels: `none` / `enums` (claim_status, CARC groups, frequency codes, payer_responsibility) / `full_profile`.
  - Keep payer names *out* of the static profile and resolve them through `lookup_values` (T04), because payer lists are tenant-specific and long.
- **Interactions:** Substitutes partly for T04 on low-cardinality fields. Profiles must be rebuilt per snapshot, so a new snapshot invalidates the cache (T26) once per data version.
- **Risks:** **PHI.** Never profile free-text or identifier columns (NTE notes, member IDs). `sem.*` already excludes them, but lossless-layer profiles must use the PHI-stripped views. Sample values are also an injection channel (T24).

#### T04. Value and entity resolution tool
- **What:** Before filtering, resolve the user's wording ("BCBS", "Medicare part b", "CO-45") to stored values with a search tool. This is `lookup_values` today.
- **Evidence:**
  - CHASE value retrieval: −2.92 pp when removed (above).
  - Cortex Analyst literal retrieval; OpenAI runtime inspection.
  - The earlier ClaimClarity gap analysis judged its payer resolver to match Cortex practice.
- **Cost/latency:** +1 tool call (about 0.5–1.5 s, small tokens) per free-text entity.
- **In our harness:** Levels: `off` (agent must guess literals) / `on` / `on+mandatory`. In `mandatory`, the compiler rejects a free-text filter value that is not an exact `lookup_values` hit, with an actionable error (T11).
- **Interactions:** Pairs with T20: two plausible matches is a clarification trigger. Pairs with T15: the compiler error teaches the retry.
- **Risks:** Over-eager fuzzy matching silently picks the wrong payer. Return match scores and row counts (as now), and treat close ties as ambiguity.

#### T05. Schema linking, pruning and compression
- **What:** Select or compress the subset of tables and columns shown to the model, by retrieval (embeddings), lexical match, LLM selection (CHESS), or summarization (ReFoRCE's "DB info compression").
- **Evidence:**
  - Strong models do better with the full schema when it fits: filtering cost −2.7 to −11.6 pp across fine-tuned GPT-4o, Gemini 1.5 Pro and Llama-405B on a BIRD subset ([Death of Schema Linking](https://arxiv.org/abs/2408.07702)).
  - 2026 corrected-statistics study: lexical linking −4.24 pp on Llama-70B (significant), not significant elsewhere; embedding linking ±0, despite higher gold-table recall ([arXiv 2606.29733](https://arxiv.org/abs/2606.29733)).
  - ReFoRCE (Spider 2.0, o3): removing compression −3.65 pp, because huge schemas overflow the context. Adding external schema linking gave **no gain** for o3 but helped weak models ([ReFoRCE](https://arxiv.org/abs/2502.00675)).
  - CHESS: about +2 pp and 5× fewer tokens ([CHESS](https://arxiv.org/abs/2405.16755)).
  - EDBT 2026 analysis: recall matters much more than precision ([EDBT](https://openproceedings.org/2026/conf/edbt/paper-24.pdf)).
- **Cost/latency:** Saves tokens (about 16% lexical, 5× in CHESS) at the cost of a retrieval or LLM step.
- **In our harness:** `sem.*` is 10 views, and the lossless layer is 41 tables (682 lines of DDL). Levels:
  - `full_sem` (current);
  - `full_sem+full_lossless` vs `full_sem+lossless_on_demand` (a `describe_lossless(topic)` tool) vs `full_sem+lossless_compressed` (table summaries plus columns only for retrieved tables).
  - Do **not** prune `sem.*`.
- **Interactions:** **Conflicts with prompt caching (T26).** Per-question pruned schemas change the prefix and kill cache hits; put dynamic schema after the last breakpoint or in a mid-conversation system message. Under T08 the schema is hidden behind the catalog, which is pruning by design.
- **Risks:** Lost recall produces confident wrong SQL. Weak models (Haiku) may benefit where Sonnet does not, so test per tier.

#### T06. Context structure: size, goal vs procedure, ordering
- **What:** How the system prompt is written: short and goal-oriented vs long and procedural, what goes first, whether a step list is mandated.
- **Evidence:**
  - OpenAI: "Less is more"; "Guide the goal, not the path"; prescriptive prompting degraded results (internal, qualitative).
  - Anthropic context-engineering guidance: the smallest high-signal context ([Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).
  - The Claude migration guide notes prompts written for older models "are often too prescriptive."
  - No controlled numeric study was found. This is a hypothesis to test, not a measured effect.
- **Cost/latency:** Neutral, or saves tokens.
- **In our harness:** `05_agent_system_prompt.md` is a six-step procedure. Levels: `procedural` (current) / `goal` (outcome + definitions + tool affordances, no step order) / `procedural_minimal`.
- **Interactions:** Strongly coupled to T01 and T11; tool descriptions carry much of the "how". Effort (T19) changes how much procedure the model needs.
- **Risks:** Goal-only prompts may drop must-do steps such as calling `lookup_values` first. Keep those as compiler-enforced rules (T04 mandatory) rather than prose.

#### T07. Memory and offline learning (corrections → glossary, examples, pitfalls)
- **What:** Turn confirmed corrections and analyzed failures into durable context: OpenAI's memory layer; NVIDIA's offline "learning" (a helper library + examples) and "reflection" (pitfalls injected into the prompt) phases.
- **Evidence:**
  - NVIDIA KGMON (DABstep): **Haiku 4.5** at inference, with Opus/Sonnet-distilled helpers and pitfalls: 89.95 on hard tasks vs 66.93 for Claude Code + Opus 4.5, about 30× faster per task (vendor-reported, no ablation) ([NVIDIA](https://huggingface.co/blog/nvidia/nemo-agent-toolkit-data-explorer-dabstep-1st-place)).
  - DS-STAR's data-file analyzer: removing it drops DABStep hard-task accuracy from 45.24% to 26.98% ([DS-STAR](https://arxiv.org/abs/2509.21825)).
  - BIRD-History: mining historical SQL logs gives "consistent improvements" across four systems; no effect sizes in the abstract ([arXiv 2608.29345](https://arxiv.org/abs/2608.29345)).
- **Cost/latency:** Offline cost (Sonnet or Opus-class reviewers can be used offline even if production is Sonnet/Haiku; confirm policy). Zero online cost beyond prompt tokens.
- **In our harness:** Run the train split with Sonnet; cluster the failures; have Sonnet write a ≤40-line "pitfalls" section and new verified requests; re-run on the held-out split. Levels: `off` / `pitfalls_only` / `pitfalls+new_verified_requests`.
- **Interactions:** Feeds T01 (pitfalls), T13 (examples) and T10 (templates). Needs the strict train/test split from §5.
- **Risks:** Overfitting to the train questions. Memory entries can encode wrong "facts", so keep human review, as OpenAI requires user confirmation.

### B. Interface: what the model is allowed to emit

#### T08. Semantic layer / metric compiler (typed tasks instead of free SQL)
- **What:** The model chooses metrics, dimensions, filters, grain and time range by name. A compiler owns joins, grain, dedup, reversal rules and minimum group sizes. Examples: dbt MetricFlow, LookML, Cortex Analyst semantic views, ClaimClarity `query_metrics`.
- **Evidence:**
  - dbt 2026 (ACME Insurance, about 11 questions × 20 runs): Sonnet 4.6 90.0% → 98.2%; GPT-5.3-Codex 84.1% → 100%. Raw text-to-SQL on the unmodeled schema: 64.5% (vendor) ([dbt](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026)).
  - Bounded planning + compiler: 97.4% vs 55.3%; wrong-but-executed 0 vs 29 over 114 runs. The authors note the comparison is not causal ([arXiv 2608.16663](https://arxiv.org/abs/2608.16663)).
  - data.world ontology: 16.7% → 54.2%; with ontology query checking about 72% and no hallucinated classes ([Sequeda 2023](https://arxiv.org/abs/2311.07509), [OBQC](https://arxiv.org/abs/2405.11706)).
  - AtScale TPC-DS: 20% → 92.5% (vendor, 40 questions, Gemini 1.5) ([BigDataWire](https://bigdatawire.com/2024/08/15/atscale-claims-text-to-sql-breakthrough-with-semantic-layer)).
  - **Out-of-scope questions: 0% by design**, as refusals.
- **Cost/latency:** Fewer turns and tokens than SQL exploration. The compiler runs in milliseconds.
- **In our harness:** This is the main factor (`interface`):
  - `sql_only` = `ClaudeSQLAgent`;
  - `typed_only` = the five `TOOLS` minus `run_semantic_sql`;
  - `typed+sem_sql` = all five tools (the production design);
  - `typed+sem_sql+lossless`.
  - Wrap `SemanticLayer.dispatch` as a bench `Tools` adapter. The final answer is the compiled SQL plus the result.
- **Interactions:** It reduces the value of T05, T14, T15 and T17 (fewer degrees of freedom to get wrong). It raises the importance of T21 (out-of-scope handling) and T09 (escalation). With T13, the examples must be tool-call JSON.
- **Risks:**
  - The coverage cliff.
  - Bench questions phrased in SQL terms ("count distinct patient control numbers") may not map onto catalog names, so add catalog synonyms rather than rewriting questions.
  - The compiler's own bugs become systematic. The bench's ledger cross-check is the defense.

#### T09. Tiered lanes and explicit escalation
- **What:** An ordered set of lanes: trusted template → typed compiler → guarded SQL over `sem.*` → guarded SQL over PHI-stripped lossless → human-approved request. Escalation is explicit and the answer is labeled with the lane it came from.
- **Evidence:** This is the converged pattern in industry (dbt, Genie, Cortex, Looker; prior report). There are no controlled studies of the routing policy itself. EllieSQL shows learned routing between pipelines saves more than 40% of tokens at equal accuracy on BIRD ([EllieSQL](https://arxiv.org/abs/2503.22402)).
- **Cost/latency:** Escalated questions cost 2–5× more turns.
- **In our harness:** Levels:
  - `model_decides` (all tools visible, prompt says "last resort");
  - `gated` (the SQL tool is hidden until a typed attempt returns `cannot_express`, or until a `request_escalation(reason)` call);
  - `shadow` (SQL lane runs for scoring but its answer is never shown).
  - Record the lane per answer, and per-lane accuracy and wrong-but-executed rate.
- **Interactions:** Depends on T08. T21 decides when not to escalate at all. The lossless lane needs T05 compression.
- **Risks:** `model_decides` lets the model skip the compiler. In a gated design, the model may claim "cannot express" too eagerly. Measure the escalation rate on questions the typed lane covers.

#### T10. Trusted templates (parameterized verified queries executed directly)
- **What:** Pre-approved, parameterized queries that the model only fills in (date range, payer). The answer is badged "trusted." Examples: Databricks Genie trusted assets, Power BI verified answers.
- **Evidence:** Genie documentation describes trusted assets as answering from "verified logic" ([Databricks](https://docs.databricks.com/aws/en/genie/concepts)). No public effect sizes. Logically, accuracy equals template correctness times routing accuracy.
- **Cost/latency:** Lowest of all lanes.
- **In our harness:** Promote the 13 `verified_requests` into callable `run_verified_request(id, params)` templates. Level: `off` / `on`. Measure the template-hit rate and accuracy on hits vs misses.
- **Interactions:** Overlaps T08 heavily. Templates mostly matter for cross-grain or multi-step questions the compiler cannot express. Complements T13 (the same library, used as examples instead of as executables).
- **Risks:** Wrong-template matching on near-miss questions: right template, wrong question. Templates need parameter validation.

#### T11. Tool surface design (count, granularity, descriptions, errors, response format)
- **What:** One `run_sql` tool vs several typed tools; namespacing; descriptions that state when to use each tool; actionable errors with suggestions; concise vs detailed results; truncation.
- **Evidence:**
  - Anthropic (internal evals): concise responses use about one-third of the tokens (72 vs 206 in the example); readable IDs over UUIDs "significantly improved retrieval precision"; tool-description refinement helped reach SOTA on SWE-bench Verified ([writing tools](https://www.anthropic.com/engineering/writing-tools-for-agents)).
  - Tool search over a large tool set: Opus 4 49% → 74%, Opus 4.5 79.5% → 88.1%, with 85% fewer tool-definition tokens ([advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)).
  - OpenAI: overlapping tools "confuse agents," so they consolidated.
  - For ClaimClarity's five tools, tool search is irrelevant. Description quality and errors are the levers.
- **Cost/latency:** Neutral to savings.
- **In our harness:** Levels:
  - error style `raw` (DuckDB exception text) vs `actionable` (`_suggest()` did-you-mean + valid options);
  - result format `full` vs `digest` (≤20 rows + row_count + column stats);
  - descriptions `current` vs `rewritten-after-transcript-review`, following Anthropic's eval loop.
- **Interactions:** Actionable errors amplify T15. The digest format interacts with T16 (a verifier needs to see enough rows) and T23 (data minimization).
- **Risks:** Digests can hide what is needed for verification. Description tuning against the test set is overfitting.

#### T12. Structured final answer and grounded numbers
- **What:** The final answer is a schema-validated object (`sql`, `result_ref`, `assumptions[]`, `lane`), with prose generated from bound placeholders so the model cannot retype numbers. Strict tool use or structured outputs enforce the schema.
- **Evidence:**
  - Cube's survey cites hallucination falling from 10.9% to 1.9% on ambiguous metric names with semantic-layer integration, and structured output as one of four mitigations; combined, the hallucination rate was 1.52% (Rumiantsau 2024, vendor).
  - The earlier ClaimClarity report judged its placeholder binding ahead of the field.
  - No ablation isolates it on a text-to-SQL benchmark.
- **Cost/latency:** Neutral.
- **In our harness:** The bench scores the final SQL re-executed, so number-retyping errors are invisible to it today. Add a prose-numbers check: every number in `answer` text must appear in the result rows (with tolerance). Factor: `free_prose` vs `bound_placeholders`.
- **Interactions:** Required for T16 numeric checks. Note the API constraint: forced `tool_choice` returns a 400 on Sonnet 5.5, so use `strict: true` + `auto` + an instruction, or `output_config.format`.
- **Risks:** Over-constrained prose reads robotic. Placeholder misuse (the wrong placeholder in a sentence) still needs a per-sentence check.

### C. Examples

#### T13. Retrieval of similar solved questions (dynamic few-shot)
- **What:** Insert the k most similar verified question → tool-call (or SQL) pairs per question.
- **Evidence:**
  - DAIL-SQL (Spider dev, GPT-4): 0-shot 72.3% → 5-shot random 79.5% → 5-shot DAIL selection 82.4% (+10.1 pp vs 0-shot; +2.9 pp from selection). The oracle selection upper limit is 84.4% ([DAIL-SQL](https://arxiv.org/abs/2308.15363)).
  - LinkedIn SQL Bot: table recall +16%, field recall +14% (prior report).
  - Microsoft Fabric: top-3 retrieval, invalid examples dropped.
  - Anthropic "tool use examples": 72% → 90% on complex parameters (internal) ([advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)).
  - Cortex Analyst evaluations remove the tested verified queries from the model's copy, the right protocol ([Snowflake](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst-evaluations)).
- **Cost/latency:** +0.5–2k tokens per question, uncached (they vary per question).
- **In our harness:** `similar_verified_requests` (Jaccard, k=3) exists. Levels:
  - `k ∈ {0, 3, 5}`;
  - `selector ∈ {lexical, embedding, skeleton-aware}` (DAIL's question + query-skeleton similarity);
  - `format ∈ {tool_call_json, sql, both}`.
  - **Grow the library from 13 to about 60 requests, disjoint from the bench test questions** (enforce by id plus a similarity threshold).
- **Interactions:**
  - With T08, use tool-call examples.
  - Place them after the cache breakpoint (a mid-conversation system message, or the user turn) to keep T26 hits.
  - Partly substitutes for T01 on the covered intents.
  - With T17, different example sets are a cheap diversity source.
- **Risks:**
  - Near-duplicate leakage inflates scores. A wrong example is copied confidently.
  - DAIL found that skeleton similarity misleads when the preliminary query is structurally wrong.

### D. Inference-time compute

#### T14. Plan-then-execute and decomposition
- **What:** An explicit plan (structured or chain-of-thought) before tool calls: divide-and-conquer CoT, a query-plan CoT, sub-questions (MAC-SQL decomposer, DIN-SQL), a planner/coder loop (DS-STAR).
- **Evidence:**
  - CHASE-SQL single generators (Gemini 1.5 Pro, BIRD dev): baseline 57.75% → divide-and-conquer CoT 63.92% (+6.2) / query-plan CoT 63.62% (+5.9) / online synthetic examples 67.09% (+9.3).
  - MAC-SQL (GPT-4, BIRD dev): removing the decomposer −3.85 pp, with challenging questions most affected ([MAC-SQL](https://arxiv.org/abs/2312.11242)).
  - DS-STAR: the router/re-planning loop is worth +5.3 pp on DABStep hard tasks; about 98% of hard tasks needed at least one refinement round.
  - CoT supervision redistributes gains to Hard/Extra-Hard questions; easy ones slightly drop ([arXiv 2608.15389](https://arxiv.org/abs/2608.15389)).
  - With adaptive-thinking models, much of this happens in thinking. Expect the explicit-plan effect to be small on Sonnet 5.5, larger on Haiku at low effort.
- **Cost/latency:** +1 model call or +300–800 output tokens.
- **In our harness:** Levels:
  - `none`;
  - `structured_plan`: the first call must emit a `plan` object (intent, metrics, grain, date basis, filters, assumptions, lane), via a `submit_plan` tool;
  - `plan+replan_on_failure`.
  - Score plan quality separately against the expected task plan.
- **Interactions:**
  - Overlaps T19 (effort).
  - The plan object is the natural carrier for T20 (assumptions/clarifications) and T22 (multi-turn state).
  - Under T08 the plan is nearly the `query_metrics` call itself.
- **Risks:** Rigid plans prevent recovery. The extra call adds latency to every question, including easy ones; consider gating by difficulty (T25).

#### T15. Execution feedback and self-correction loop
- **What:** Feed compiler or database errors (and, in T16, suspicious results) back to the model to retry, with bounded rounds.
- **Evidence:**
  - 2026 corrected-statistics BIRD study: the only consistent gain. +3.9 (Qwen-7B), +1.5 (Qwen-14B), +1.3 (Qwen-32B), +4.0 (Llama-8B), +1.0 (Llama-70B, not significant), +3.4/+4.4 (CodeLlama). It works mainly by repairing non-executing queries. Cost: 1.09–1.50× tokens ([arXiv 2606.29733](https://arxiv.org/abs/2606.29733)).
  - CHASE-SQL: removing the query fixer −3.78 pp.
  - MAC-SQL: removing the refiner −4.63 pp (GPT-4).
  - LinkedIn: hallucination −14%.
- **Cost/latency:** +0–2 calls, only on errors.
- **In our harness:** The baseline loop already returns `ERROR: ...` text. Levels:
  - `none` (one shot);
  - `errors_only` (current);
  - `errors+actionable_hints` (T11);
  - `errors+empty/suspicious_result_signals` (T16).
  - Cap the rounds: `max_repairs ∈ {1, 2, 4}`.
- **Interactions:** Under T08, compiler errors are already model-readable, so the gain should be largest on the `sql_only` lane. Pairs with T11.
- **Risks:** Repair loops can "fix" an error by dropping the filter that caused it, which yields a wrong answer that executes. Log repaired-then-wrong cases separately.

#### T16. Result verification and sanity checks
- **What:** Programmatic and LLM checks on the *result* before answering:
  - zero rows → suspect a bad join or filter (OpenAI);
  - rates within [0,1];
  - numerator ≤ denominator;
  - totals reconcile (835 balancing: BPR02 = ΣCLP04 − ΣPLB);
  - fan-out detection (`count(*)` vs `count(distinct key)`);
  - grain checks;
  - comparison against a cheap independent query.
- **Evidence:**
  - OpenAI treats zero rows as a likely bug (qualitative).
  - DS-STAR: an LLM verifier/router loop gives +5.3 pp on hard tasks (router ablation); no verifier-only ablation.
  - Hallucination-detection frameworks catch 69–83% of silent text-to-SQL errors post hoc (Yang 2025, as cited in Cube 2026).
  - NL2SQL-Bugs: SOTA LLMs reach 75.16% bug-detection accuracy (via Cube 2026).
  - CHESS uses LLM unit tests.
  - Data-aware candidate selection via separating instances beats baselines when only 2–3 candidates exist ([arXiv 2605.12319](https://arxiv.org/abs/2605.12319); numbers not in abstract).
- **Cost/latency:** Programmatic checks are near zero. An LLM verifier adds +1 call (Haiku-sized).
- **In our harness:** Many `wrong_sql` entries in `questions.yaml` are exactly what checks catch: summing every submission (fan-out), `payer_sequence='primary'`, every $0 payment counted as a denial. Implement `checks.py`, run after each SQL/compile result, and return warnings as tool-result annotations. Levels: `off` / `programmatic` / `programmatic+llm_verifier(haiku)`.
- **Interactions:** Feeds T15. Under T08 most invariants are guaranteed by construction, so the value concentrates on the SQL lanes. Disagreement can trigger T21 (abstain) or T20 (ask).
- **Risks:** False alarms cause needless retries or refusals; measure the false-alarm rate on gold answers. Don't tune the checks against test `wrong_sql`. Derive them from domain invariants (README §835 facts).

#### T17. Multiple candidates + selection (self-consistency, voting, pairwise selector)
- **What:** Generate N candidates and pick one by execution-result voting, an LLM pairwise selector, or a trained ranker. Optionally use disagreement as an uncertainty signal.
- **Evidence:**
  - CHASE-SQL (Gemini 1.5 Pro, BIRD dev): single 63.01% → self-consistency 68.84% (+5.8) → pairwise selector 73.01% (+4.2 over SC). Candidate upper bound 82.79%; pools beyond about 20 candidates gave no gain.
  - 2026 study (Qwen-32B): SC with 5 samples: −0.07 pp at 4.99× tokens; p95 latency 10.6 s → 50.0 s.
  - ReFoRCE (o3, Spider 2.0-Snow): removing majority voting −2.0 pp.
  - Death-of-schema-linking pipeline: "selection" worth −0.7 to −2.7 pp when removed.
  - EHRSQL 2024: the #2 team abstained unless an ensemble of three models (incl. Claude Opus) agreed, a strong reliability tool ([EHRSQL](https://arxiv.org/abs/2405.06673)).
- **Cost/latency:** N× tokens and calls. Parallel calls keep latency at roughly 1–1.3× plus the selector.
- **In our harness:** **Temperature sampling is unavailable on current Claude models** (sampling parameters return a 400), so diversity must come from:
  - different example sets (T13);
  - plan variants (CHASE's three generators);
  - effort levels;
  - Haiku + Sonnet mix.
  - Levels: `N=1` / `N=3 vote-by-result` / `N=3 + Sonnet pairwise selector` / `N=3 agree-or-abstain`.
- **Interactions:**
  - Under T08 the compiled outputs collapse to few distinct results, so voting adds little except as an abstention signal (T21).
  - It multiplies cost and makes caching (T26) matter more; candidates sharing a prefix hit the cache.
- **Risks:** Correlated errors defeat voting: all candidates share the same misreading of "denial." The cost is large relative to the expected gain on strong models.

#### T18. Multi-agent splits (planner / SQL writer / verifier / synthesizer)
- **What:** Separate model contexts with roles (MAC-SQL selector/decomposer/refiner; Cortex classifier → generator → error corrector → synthesizer; DS-STAR planner/coder/verifier/router).
- **Evidence:**
  - MAC-SQL (GPT-4, BIRD dev 59.39%): −2.11 (selector), −3.85 (decomposer), −4.63 (refiner) when each is removed. These are roles, though, and could be run in a single context.
  - Anthropic: multi-agent research beat a single agent by 90.2% on an internal research eval, using about 15× chat tokens (agents use about 4×). It "requires tasks where the value is high enough" ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
  - MAST: failures across 7 multi-agent frameworks stem mostly from specification and inter-agent misalignment, with a verification gap ([MAST](https://arxiv.org/abs/2503.13657)).
  - Anthropic's cost guidance: orchestrators pay only when there is bulk, parallelizable work; for a dependent chain that fits in one context, a single model at lower effort did better.
- **Cost/latency:** 2–15× tokens. Breaks cache sharing; each agent has its own prefix.
- **In our harness:** Most single-question analytics is a dependent chain, so the default hypothesis is a negative or zero effect. Test only two splits:
  - `single`;
  - `single + separate verifier context` (Haiku reviews question, plan, SQL and result, and flags);
  - `planner(Sonnet) → executor(Haiku)`.
- **Interactions:** It is the "multi-model" version of T16/T25. Conflicts with T26 (separate caches) and with preserved-thinking constraints (thinking blocks are not portable across models).
- **Risks:** Error propagation across handoffs, latency, a harder-to-debug harness.

#### T19. Reasoning budget (adaptive thinking + effort)
- **What:** How much the model thinks per turn. On Sonnet 5.5 and Haiku 5.5 this is `output_config.effort ∈ {low, medium, high, xhigh, max}` with adaptive thinking; `budget_tokens` is rejected.
- **Evidence:**
  - Indirect only. Reasoning models dominate the 2025–26 agentic SQL results: ReFoRCE with o3 38.52% vs GPT-4o 24.44% on the Spider 2.0 subset ([ReFoRCE](https://arxiv.org/abs/2502.00675)).
  - BIRD-Interact: o3-mini and Gemini-2.5-Pro lead the conversational mode; Claude Sonnet 4 is strong in agentic mode.
  - No published effort or thinking-budget sweep for text-to-SQL was found.
  - Anthropic's cost-optimization guidance (internal runs): on knowledge work `medium` matched `high`, `low` gave up a few points for a third to a half less cost; "re-run failures at higher effort" held the high-effort pass rate for a little over half the cost.
- **Cost/latency:** Higher effort means more output tokens ($10/MTok on Sonnet 5.5) and seconds per turn. Changing effort mid-conversation invalidates the message cache.
- **In our harness:**
  - **First fix `max_tokens=2000` in `ClaudeSQLAgent`.** With thinking on, it can truncate turns. Set 16k (non-streaming), and treat `stop_reason == "max_tokens"` as a harness error, not "no answer".
  - Levels: `effort ∈ {low, medium, high}` per model, plus `low + retry-failures-at-high` (needs a failure signal from T15/T16).
- **Interactions:**
  - Substitutes for T14 and partly for T17.
  - Lower effort makes fewer, more consolidated tool calls, which interacts with T15 (fewer repairs).
  - Under T08 the task is easier, so lower effort probably suffices: a key hypothesis for cost.
- **Risks:** Thinking can rationalize a wrong definition. Latency for interactive chat.

### E. Dialogue: ambiguity, abstention, follow-ups

#### T20. Clarifying-question policy
- **What:** When to ask vs assume and state the assumption. Material ambiguities in this domain:
  - date basis (service vs remit vs paid date);
  - denial definition variant (recorded vs signal v3);
  - current vs history;
  - payer alias ties;
  - claim vs line grain;
  - "primary" payer.
- **Evidence:**
  - BIRD-Interact full (ambiguity + follow-ups, user simulator): conversational mode success on follow-ups: GPT-5 8.67%, Claude Sonnet 4 14.17%, Gemini-2.5-Pro 16.33%. Agentic mode: Claude Sonnet 4 12.67%, GPT-5 17.00%. In agentic mode, `submit` and `ask` make up 60.87% of actions, so models favor trial-and-error over looking up knowledge. More user patience (turns) improves results ("interaction test-time scaling") ([BIRD-Interact](https://arxiv.org/abs/2510.05318)).
  - AMBROSIA: recall on ambiguous questions is 27–31% vs 63–65% on unambiguous ones (GPT-4o, Llama3-70B). Models produce all interpretations under 2% of the time ([AMBROSIA](https://arxiv.org/abs/2406.19073)).
  - OpenAI: ask, else apply stated defaults (last 7 or 30 days).
  - Cortex: a classifier rejects ambiguous questions and suggests alternatives.
- **Cost/latency:** Each question asked costs a user round-trip. Over-asking is a UX failure.
- **In our harness:**
  - Build an **ambiguous split** (about 30 questions), each with 2+ interpretations and a gold answer per interpretation, plus a simulated user (Haiku, function-driven like BIRD-Interact's) that answers only with the disambiguating fact.
  - Levels: `never_ask_state_assumptions` / `ask_on_listed_ambiguities` (a policy list in the prompt) / `ask_whenever_uncertain`.
  - Score: correct-after-dialogue, number of questions asked, and answers on unambiguous questions where it asked anyway (over-asking).
- **Interactions:** With T14, assumptions go in the plan object. With T04, alias ties are the cleanest trigger. With T17, candidate disagreement is a data-driven trigger.
- **Risks:** Simulator leakage (BIRD-Interact found naive simulators leak ground truth or fail on unanswerables at up to 67.4%). The current bench, built to be unambiguous, will show only the cost of asking, never the benefit.

#### T21. Abstention and out-of-scope handling
- **What:** Decline, or say "not answerable with current analyses," when the question needs data the layer does not have (277CA/999 rejections, patient-level PHI), when it is unanswerable, or when confidence is low. Suggest in-scope alternatives.
- **Evidence:**
  - EHRSQL 2024 reliability score RS(c): +1 correct, 0 abstain on answerable, −c for wrong or for answering an unanswerable question. Top RS(10) was 81.32. Teams with a smaller RS(0)–RS(10) gap ranked higher, i.e., avoiding wrong SQL beat answering more. Abstain-all scores 20 ([EHRSQL](https://arxiv.org/abs/2405.06673)).
  - Semantic-layer failures are refusals, text-to-SQL failures are wrong numbers (dbt; bounded planning: 0 wrong-but-executed).
  - BIRD-Interact: models struggle on unanswerable questions.
- **Cost/latency:** None.
- **In our harness:** The bench already has 4 safety (must-refuse) questions and 1 escalation question. Add about 20 **out-of-scope** items (277CA, eligibility, patient names, other tenants, forecasts) and about 20 **in-scope look-alikes**. Report RS(0), RS(10) and over-refusal. Levels: `prompt_only` / `prompt + agree-or-abstain (T17)` / `prompt + verifier veto (T16)`.
- **Interactions:** Trades off directly with accuracy; T08 raises abstention on long-tail questions; T09 escalation competes with abstaining.
- **Risks:** Over-refusal on legitimate questions, especially with stricter PHI prompts (T23).

#### T22. Multi-turn state ("same thing, by quarter")
- **What:** Carry the previous structured plan or tool call as state, and apply follow-ups as edits to it rather than re-deriving from the transcript.
- **Evidence:**
  - BIRD-Interact: follow-up sub-tasks succeed far less often than first ("priority") sub-tasks. For example, Claude Sonnet 4 in conversational mode: 22.33% on priority vs 14.17% on follow-up.
  - OpenAI: interruptible, visible reasoning so users can correct assumptions.
- **Cost/latency:** Saves tokens versus replaying results.
- **In our harness:** Not exercised by the single-turn bench. Add about 15 two- and three-turn scripted dialogues. Levels: `transcript_only` / `plan_state` (inject the last `query_metrics` args as a mid-conversation system message).
- **Interactions:** Uses T14's plan object. Mid-conversation system messages preserve the T26 cache. Context editing can clear old tool results.
- **Risks:** Stale state when the user changes subject.

### F. Safety and PHI

#### T23. PHI guardrails and refusal policy
- **What:** Fixed code-level controls:
  - `sem.*` PHI minimization;
  - `guard.py` single-SELECT allowlist;
  - DuckDB sandbox (`enable_external_access=false`, `allowed_directories`, `lock_configuration`);
  - tenant isolation by storage;
  - min group size 20 + complementary suppression;
  - PHI-safe tracing.
  - The **variable** part is prompt-level refusal wording and the refusal tool.
- **Evidence:**
  - P2SQL: in-prompt restrictions "may not be fully effective" ([P2SQL](https://arxiv.org/abs/2308.01990)).
  - DuckDB documents that untrusted SQL is unsafe by design without sandboxing (prior report).
  - CMS suppression rules cover derived cells (prior report).
  - No benchmark measures over-refusal for claims analytics. That has to be built.
- **Cost/latency:** Negligible.
- **In our harness:** Not factors: the guard and sandbox are always on. Factor: `refusal_prompt ∈ {minimal, explicit_PHI_policy}`, scored on safety questions (refusal required) and benign look-alikes ("how many distinct patients had a denial?", allowed as a count above the threshold) where refusal is wrong.
- **Interactions:** Stricter refusal lowers wrong-but-answered on safety items and raises over-refusal (T21). Suppression must also apply to the SQL lanes (T09).
- **Risks:** Testing prompt-level guardrails can create a false sense that the prompt is the control.

#### T24. Prompt-injection hygiene for data content
- **What:** Treat values returned from the data (payer names, NTE notes, CARC descriptions) as untrusted. Delimit them, strip free text from digests, and never let data content change lane or permission decisions.
- **Evidence:** P2SQL showed attack success rates of 1.0 on most examples, with records planted in the database hijacking the model when other users' queries return them. Effective defenses were permission hardening, query rewriting, an auxiliary LLM validator and data preloading.
- **Cost/latency:** Negligible.
- **In our harness:** Add 3–5 injected-record fixtures (a payer name or NTE containing instructions) to the dataset generator and assert unchanged behavior. Level: `undelimited` vs `delimited+stripped`.
- **Interactions:** T03 sample values and T04 lookup results are the main channels.
- **Risks:** None to measuring it. Treat it as a regression gate, not as an optimization.

### G. Cost and latency levers

#### T25. Model tier and routing (Haiku vs Sonnet, cascades)
- **What:** Which model runs which lane or step; difficulty routing (EllieSQL); a cheap-first cascade that escalates on a failure signal; a Haiku verifier.
- **Evidence:**
  - Cube 2026: with semantic context, Opus 4.7, Sonnet 4.6 and GPT-5.4 are statistically indistinguishable (67.7–68.7%); Sonnet + semantic layer had the lowest input tokens.
  - NVIDIA DABstep: Haiku 4.5 at inference, top of the hard leaderboard (vendor).
  - EllieSQL: more than 40% token reduction, no accuracy loss, more than 2× token elasticity of performance (BIRD; gpt-4o-mini tiers).
  - Anthropic guidance: compare cost *per completed task*; caches are model-scoped, so cascades forfeit cache reuse.
- **Cost/latency:** At the reference prices, Haiku 5.5 input is about 20× cheaper than Sonnet 5.5, and much faster.
- **In our harness:** `model ∈ {haiku-5-5, sonnet-5-5}` as a **blocking factor**: run every configuration on both. Plus a `cascade` level: Haiku first, Sonnet on (compile error after 2 repairs | verifier flag | abstain).
- **Interactions:** Depends on T01/T08 (cheap tiers need governed context). Conflicts mildly with T26 (cache per model). Interacts with T19 (low effort on Sonnet vs Haiku at high effort).
- **Risks:** Haiku may over-call or under-call tools. Its refusal behavior differs (no server-side refusal fallback on Haiku 5.5).

#### T26. Prompt caching
- **What:** Cache the stable prefix (tools → system: catalog, glossary, data dictionary, profile, policy) and add per-question material after the last breakpoint.
- **Evidence:** API economics from the Claude API reference: reads 0.1× input (Sonnet 5.5 $0.20/MTok); writes 1.25× (5-minute TTL) or 2× (1-hour TTL); 512-token minimum on Sonnet/Haiku 5.5; up to 4 breakpoints. Invalidators: any byte change in the prefix, a different tool set, thinking or top-level effort changes, model switches. Latency reductions for long cached prefixes are documented qualitatively ([prompt caching docs](https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching)).
- **Cost/latency:** For a typical 8–15k-token static prefix reused across a 4–8-call loop, input cost on the cached part falls by about 85–90%.
- **In our harness:** The baseline sends no `cache_control`. Add an explicit breakpoint at the end of `system` and top-level automatic caching for the growing tail. Log `cache_read_input_tokens` in `usage`. Not an accuracy factor: verify that accuracy is unchanged, and report cost both ways.
- **Interactions:**
  - Makes T01/T02/T03 nearly free.
  - Penalizes T05 dynamic pruning and per-question system edits; put T13 examples in the user turn or a mid-conversation system message.
  - Independent configurations in an experiment cache separately, so order runs to keep caches warm.
- **Risks:** Silent invalidators (timestamps in the system prompt, unsorted JSON in `describe()`).

#### T27. Loop budgets and output hygiene
- **What:** Caps on turns, tool calls, rows returned and wall-clock time; parallel tool calls; digest results; streaming; batch API for offline evaluation (50% discount).
- **Evidence:**
  - ReFoRCE: disabling column exploration cut calls to 1.69 per example and about 15k tokens, for −2 pp.
  - Anthropic: a 25k-token cap on tool responses in Claude Code; concise formats use about one-third of the tokens.
  - The prior ClaimClarity design caps the Investigator at ≤4 model turns, 8 tasks and 25 s.
- **Cost/latency:** The main driver of p95 latency.
- **In our harness:** `max_turns ∈ {4, 8}`, `row_cap_to_model ∈ {20, 50}`. Use the Batch API for repeat runs where the loop can be flattened. A multi-turn tool loop cannot be batched as-is, so batches mainly help the judge and verifier calls.
- **Interactions:** Tight caps reduce T15/T17 headroom. Row caps interact with T16.
- **Risks:** Truncation hides the evidence the model needs. Report how often caps are hit.

### H. Evaluation methodology (harness requirements, not factors)

| ID | Practice | Evidence / source | Status in `bench/` |
|---|---|---|---|
| E1 | Execution-result matching, value-based and strict about extra numbers; ledger-derived truth | OpenAI compares SQL and results; FLEX shows EX has false positives and negatives | **Done** (`compare.py`, `ledger.py`, `selftest`) |
| E2 | LLM judge only after a matcher miss, with calibration reported | FLEX: an LLM judge raised Cohen's κ with experts from 62 to 87.04; re-scoring shifted model scores by more than 2.6 pp on average ([FLEX](https://arxiv.org/abs/2409.19014)) | **Done** (`judge.py`, `calibrate`). Add per-factor judge-vs-matcher disagreement |
| E3 | Repeats + pass^k | dbt 20 runs; bounded planning 3; 2026 study shows 0.2–3.7% of items flip between runs | **Done**. Use ≥3 repeats |
| E4 | Separate wrong-but-executed rate, refusal rate, RS(c) | EHRSQL RS(10); bounded planning wrong-but-executed counts | Partial. Add RS(c) and wrong-but-answered |
| E5 | Train / dev / test split; examples, pitfalls and description tuning never touch the test split | Cortex evaluations hold verified queries out | **Missing.** Needed before T07/T13/T11 |
| E6 | Paired tests (McNemar on per-question correctness) with Holm correction; question-clustered bootstrap CIs | 2026 study's method; Cube's paired design | **Missing** |
| E7 | Power: about 150+ questions for ~8–10 pp effects | See §6 | **Missing** (33 questions) |
| E8 | Splits for ambiguity, out-of-scope, multi-turn and injection | BIRD-Interact, AMBROSIA, EHRSQL, P2SQL | **Missing** |
| E9 | Cost and latency per *completed* task (tokens incl. cache reads, wall-clock, calls) | Anthropic cost guidance; ReFoRCE/DS-STAR cost tables | Partial (tokens, seconds). Add cache and cost |
| E10 | Don't calibrate against public leaderboards; label annotation noise | 52.8% / 62.8% annotation errors in BIRD Mini-Dev / Spider 2.0-Snow | N/A |

---

## 5. Key interactions

| Pair | Interaction | Consequence for the design |
|---|---|---|
| T08 × {T05, T14, T15, T17} | The compiler removes most degrees of freedom these fix | Ablate those four **within** each interface level, not pooled |
| T08 × T21 | Typed-only refuses the long tail | Always report accuracy *and* RS(10) |
| T01 × T25 | Context closes the gap between model tiers (Cube) | Expect a glossary × model interaction; test Haiku with full context |
| T13 × T26 | Per-question examples break a cached system prompt | Put examples in the user turn or a mid-conversation system message |
| T05 × T26 | Dynamic pruning breaks the cache | Prune only the lossless lane |
| T17 × API | No temperature, so sampled self-consistency is impossible | Diversity via prompts, examples, effort or model |
| T19 × T14 / T17 | Thinking substitutes for explicit plans and voting | Test T14 and T17 at `low` effort, where they should matter most |
| T18 × T26 / preserved thinking | Separate contexts don't share caches; thinking blocks don't transfer across models | Multi-agent only for a verifier |
| T16 × T15 × T21 | Check results feed repair, or abstention when repair fails | Implement as one "result-signal" factor with three levels |
| T20 × bench design | Unambiguous questions can't reward asking | A new ambiguous split is a precondition |

---

## 6. From catalog to experiments

**Step 0: fix harness confounds.** About 1 day.
- Raise `max_tokens` (thinking).
- Log `stop_reason`, cache tokens and cost.
- Add the T12 prose-number check.
- Wrap `SemanticLayer` as a bench agent.
- Split questions into train/dev/test.
- Expand the questions: about 150 in-scope (grow by reusing the generator ledger; prioritize denials, rework, money), plus 30 ambiguous, 40 out-of-scope / look-alike, 15 multi-turn and 5 injection.

**Step 1: screening experiment.** Seven two-level factors in a 2^(7−3) Resolution IV fractional factorial (16 configurations), run on both Sonnet 5.5 and Haiku 5.5 as a blocking factor, with 3 repeats.

| Factor | Low level | High level |
|---|---|---|
| Interface (T08) | `sql_only` | `typed+sem_sql` |
| Glossary (T01) | `glossary` | `+invariants+pitfalls` |
| Data dictionary + profile (T02+T03) | off | on |
| Examples (T13) | k=0 | k=3, tool-call format |
| Result signals (T15+T16) | errors only | + programmatic checks |
| Effort (T19) | low | high |
| Prompt style (T06) | procedural | goal |

**Step 2: follow-up experiments.** On the winning region: candidates/abstention (T17/T21), clarification policy (T20, on the ambiguous split), cascade (T25), escalation gating (T09), multi-agent verifier (T18).

**Power rule of thumb.** For paired binary outcomes, n ≈ (z_α/2 + z_β)² · p_disc / Δ², where p_disc is the discordant-pair rate and Δ the effect. With p_disc ≈ 0.2, detecting Δ = 10 pp at α = 0.05 and power 0.8 needs about 157 question-runs per arm; Δ = 5 pp needs about 630. With 150 questions × 3 repeats, effects of about 7–10 pp are detectable. Smaller ones are "no evidence of effect," not "no effect."

**Budget sketch.** Order-of-magnitude, at reference prices; confirm with a measured pilot. A loop with a ~10k-token cached prefix, 5 calls and about 2k output tokens costs roughly:
- Sonnet 5.5: about $0.03 uncached-part input + $0.01 cached reads + $0.02 output, roughly $0.05–0.10 per question-run;
- Haiku 5.5: about 20× less.

The Step 1 screen (16 configurations × 2 models × ~200 questions × 3 repeats ≈ 19k runs) is then roughly $500–1,000 on Sonnet and well under $100 on Haiku, plus judge calls.

---

## 7. Caveats

- Many headline effects are vendor-reported or self-reported: dbt, Cube, AtScale, Databricks, Snowflake, NVIDIA, and Anthropic/OpenAI internal evals. The bounded-planning and Cube studies have small question sets (38 and 100).
- Classic component ablations (CHASE-SQL, MAC-SQL, DAIL-SQL) use GPT-4- and Gemini-1.5-era models on Spider or BIRD. Their magnitudes overstate what Sonnet 5.5 will show.
- No public study sweeps thinking or effort for text-to-SQL. The T19 expectations rest on Anthropic's general cost guidance and indirect evidence.
- The OpenAI data-agent details come from the post via a secondary summary; the original page blocked automated retrieval.
- Model IDs, prices and API constraints come from the Claude API skill reference cached 2026-10-06. Re-check the pricing page and the release notes on temperature removal and forced `tool_choice` before running.
- LiveSQLBench and Spider 2.0 leaderboard values from aggregator sites were inconsistent and are not used for effect sizes.

## 8. Sources

**Benchmarks and evaluation**
- BIRD: https://arxiv.org/abs/2305.03111
- Spider 2.0: https://arxiv.org/abs/2411.07763
- BIRD-Interact: https://arxiv.org/abs/2510.05318
- AMBROSIA: https://arxiv.org/abs/2406.19073
- EHRSQL 2024 shared task: https://arxiv.org/abs/2405.06673
- FLEX: https://arxiv.org/abs/2409.19014
- Annotation-error audit: https://arxiv.org/abs/2601.08778
- BIRD-History: https://arxiv.org/abs/2608.29345
- DABstep: https://huggingface.co/spaces/adyen/DABstep
- BEAVER and LiveSQLBench: via the prior report and https://livesqlbench.ai

**Methods**
- CHASE-SQL: https://arxiv.org/abs/2410.01943
- DAIL-SQL: https://arxiv.org/abs/2308.15363
- MAC-SQL: https://arxiv.org/abs/2312.11242
- CHESS: https://arxiv.org/abs/2405.16755
- The Death of Schema Linking?: https://arxiv.org/abs/2408.07702
- ReFoRCE: https://arxiv.org/abs/2502.00675
- On-prem open LLMs, technique frontier on BIRD (2026): https://arxiv.org/abs/2606.29733
- Automatic metadata extraction: https://arxiv.org/abs/2505.19988
- Schema linking analysis (EDBT 2026): https://openproceedings.org/2026/conf/edbt/paper-24.pdf
- EllieSQL: https://arxiv.org/abs/2503.22402
- Agentic-SQL Revisited: https://arxiv.org/abs/2608.15389
- DS-STAR: https://arxiv.org/abs/2509.21825
- Data-aware candidate selection: https://arxiv.org/abs/2605.12319
- MAST: https://arxiv.org/abs/2503.13657

**Semantic layers**
- dbt Labs 2026: https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026
- Cube 2026 paired benchmark: https://arxiv.org/abs/2604.25149
- Bounded semantic planning: https://arxiv.org/abs/2608.16663
- data.world (Sequeda 2023): https://arxiv.org/abs/2311.07509
- Ontology query checking (OBQC): https://arxiv.org/abs/2405.11706
- AtScale: https://bigdatawire.com/2024/08/15/atscale-claims-text-to-sql-breakthrough-with-semantic-layer

**Products and engineering posts**
- OpenAI data agent: https://openai.com/index/inside-our-in-house-data-agent/ (summary: https://www.zenml.io/llmops-database/building-an-ai-data-agent-for-enterprise-scale-analytics)
- Snowflake Cortex Analyst evaluations: https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst-evaluations
- Databricks Genie: https://docs.databricks.com/aws/en/genie/concepts
- NVIDIA DABstep: https://huggingface.co/blog/nvidia/nemo-agent-toolkit-data-explorer-dabstep-1st-place
- Anthropic, writing tools for agents: https://www.anthropic.com/engineering/writing-tools-for-agents
- Anthropic, advanced tool use: https://www.anthropic.com/engineering/advanced-tool-use
- Anthropic, multi-agent research system: https://www.anthropic.com/engineering/multi-agent-research-system
- Anthropic, context engineering: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Anthropic prompt caching docs: https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching

**Security**
- P2SQL: https://arxiv.org/abs/2308.01990
- DuckDB security and CMS suppression: via `research/conversational-analytics-agents-sota-2026-full-report.md`
