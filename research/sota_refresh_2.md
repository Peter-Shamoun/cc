# SOTA refresh 2: text-to-SQL agents, and learning context without overfitting

25 Oct 2026. Companion to `research/sota-techniques-catalog-2026.md` and `research/technique_factors.yaml`.
This note does not replace that catalog. It re-reads the literature against two results the catalog did not have:
screen 1 (`research/screening_results_1.md`) and held-out validation 1 (`research/heldout_results_1.md`).

Nothing in this note changes the generator (frozen at 3.3), the parser, the lake, or the agent code.
It proposes what the next agent and the next screen should be.

## What this repo already measured

Screen 1 was a 2^(7-3) resolution-IV factorial, 16 configurations × 36 questions × 1 repeat, Haiku and Sonnet
separately, generator 3.0. The agent only saw `sem.*` (ten views) through `bench/agent_cli.py`. Scoring
re-executes the submitted SQL. Main effects, question-level bootstrap:

| Factor | Haiku | Sonnet |
|---|---|---|
| Typed metrics + lookup, on top of free SQL over `sem.*` | +3.1 [−1.7, +8.7] | −1.0 [−3.1, +1.0] |
| Glossary: basic → invariants and pitfalls | **+15.6 [+5.2, +26.7]** | **+5.9 [+0.4, +12.9]** |
| Data dictionary + profile | +2.4 [−1.7, +7.6] | −1.0 [−4.2, +2.8] |
| Dynamic few-shot, k=3 | −0.3 [−3.1, +2.4] | +1.7 [−0.4, +4.2] |
| SQL error hints + programmatic result checks | −0.3 [−3.1, +2.4] | +1.7 [−1.0, +4.5] |
| Effort low → high | −2.4 [−8.3, +2.4] | +1.7 [−3.1, +6.3] |
| Context style goal → procedural steps | **+4.5 [+0.4, +9.0]** | +1.7 [−0.7, +4.2] |

Ten of the sixteen Sonnet configurations scored at least 97.2%, and the other six sit between 86.1% and 94.4%. The set was near a ceiling for Sonnet. The screen-1 write-up says twelve; the table itself has ten.
The pitfalls text was written in the same session as the questions and the views.

Held-out 1 used 27 harder questions authored afterward by people who had seen only the basic glossary,
each with an independent verifier. Generator 3.1. Four configs × two models × two repeats. Paired by
question:

| minus base | Haiku | Sonnet |
|---|---|---|
| Pitfalls text | **−1.9 [−14.8, +13.0]** | **+7.4 [0.0, +16.7]** |
| Pitfalls + procedural steps | +1.9 [−13.0, +18.5] | 0.0 [−5.6, +5.6] |
| Screen-1 winner (pitfalls, dictionary, profile, procedural, low effort) | +11.1 [−3.7, +27.8] | +3.7 [0.0, +11.1] |

Haiku pass^2 fell when the pitfalls text was added (70.4% → 66.7%) even though mean accuracy did not rise.
The screen-1 winner is the best Haiku cell here, and its interval includes 0. Dictionary and profile were
not isolated from the pitfalls text, so that +11 is not yet attributable.

Reading, which the rest of this note is written to serve:

1. A hand-written pitfalls list that names the traps in the questions it was developed against can look
   like a +16 point technique and be nothing on the next set. Sonnet's smaller gain survived. Haiku's did not.
2. Twenty-seven questions gives an interval of about ±15 points. A 10-point effect is not distinguishable
   from noise at that size. A 5-point effect is not distinguishable at several times that size.
3. `sem.*` is already a semantic layer over raw 837/835. The screen factor called "semantic layer" was a
   typed metric compiler plus lookup, added on top of free SQL against those views. A null there is not
   evidence that modeling the grain was useless. That modeling is already in the views.
4. Procedural steps helped Haiku on the easy set and did not replicate once the pitfalls text was present.
   Higher effort did not pay for itself on either set.

## 1. Text-to-SQL and data-analyst agents, 2025–2026

### 1.1 The benchmarks measure different jobs

Public leaderboards are not interchangeable, and none of them is this bench.

**BIRD** (Li et al., 2023, [arXiv:2305.03111](https://arxiv.org/abs/2305.03111)) is single-turn text-to-SQL
on 95 SQLite databases, 12,751 question-SQL pairs, 33.4 GB. The hard part is dirty values plus a
one-sentence external-knowledge "evidence" note. GPT-4 on the test set goes from 34.88% execution accuracy
without that evidence to 54.89% with it (+20.0 points; dev 30.90% → 46.35%). Human test-set
performance is 72.37% without the evidence and 92.96% with it. Execution accuracy, not string match, is the metric. This is the closest public analog to
"the model knows SQL and still picks the wrong population."

**Spider 2.0** (Lei et al., 2024, [arXiv:2411.07763](https://arxiv.org/abs/2411.07763),
[leaderboard](https://spider2-sql.github.io/)) is enterprise workflow text-to-SQL: 632 tasks, databases
that often have more than 1,000 columns, BigQuery and Snowflake dialects, project code and dialect docs,
queries that run past 100 lines. The paper's code agent on o1-preview solves 21.3%, against 91.2% on
Spider 1.0 and 73.0% on BIRD. Methods that were strong on Spider 1.0 collapse: on the Spider 2.0-Snow
board, DAIL-SQL + GPT-4o is 2.20 and CHESS + GPT-4o is 1.28. The live board (read 25 Oct 2026) has since
moved a long way, and the project says scores shift when the grader is corrected. ReFoRCE + o3 is listed
at 62.89. Several vendor agents are listed above 80, up to Genloop's Sentinel Agent v2 Pro at 96.70.
Those entries do not publish component ablations. The page excludes methods marked as using ground-truth
tables from the ranking. Treat the top of that board as a existence proof that agentic workflows on a
strong model can clear the 2024 single-digit scores, not as a technique to copy.

**BIRD-Interact** (ICLR 2026 oral, [arXiv:2510.05318](https://arxiv.org/abs/2510.05318),
[proceedings](https://proceedings.iclr.cc/paper_files/paper/2026/file/496b549556509bbb9770bf9d335c5800-Paper-Conference.pdf))
is the multi-turn benchmark. Each database has a knowledge base, metadata, and a function-driven user
simulator. `c-Interact` follows a scripted conversational protocol. `a-Interact` lets the model decide
when to ask, when to query, and when to submit, under a budget. On the full 600-task suite, GPT-5's
overall success is 8.67% in `c-Interact` and 17.00% in `a-Interact`. Claude Sonnet 4's priority-question
success in `c-Interact` is 22.33%, and its follow-up success is 14.17% (normalized reward 18.35, about
$0.29 per task). In `a-Interact` its overall success is 12.67% (reward 23.28, about $0.51). The paper's
own analysis is that the stronger models mix environment exploration with clarifying questions early and
execution later; models that only ask, or only execute, do worse. This bench cannot see that failure.
Every current question is written to have one answer. Clarifying questions (factor T20 in the catalog)
stay parked until there is an ambiguous split.

ClaimClarity's single-turn questions over ten modeled views are closer to BIRD-with-evidence and to the
dbt/Cube setups below than to Spider 2.0 or BIRD-Interact. Spider 2.0's lesson still applies the day a
question leaves `sem.*` for the lossless layer.

### 1.2 What the leaders actually do

Numbers below are from the papers, except where marked as the live board. Leaderboard ranks move;
ablations do not.

**CHESS** (Talaei et al., 2024, [arXiv:2405.16755](https://arxiv.org/abs/2405.16755)). Four agents:
information retriever, schema selector, candidate generator with iterative refinement, and an LLM unit
tester that checks the SQL against a natural-language restatement. On industrial-scale schemas the
selector is worth about +2 points and about 5× fewer tokens. With a large compute budget, CHESS reaches
71.10% on the BIRD test set (dev 68.31% on the current board). The same system scores 1.28 on Spider
2.0-Snow with GPT-4o. Schema selection pays when the catalog does not fit. It is the wrong first move
on ten views.

**CHASE-SQL** (Pourreza et al., 2024, [arXiv:2410.01943](https://arxiv.org/abs/2410.01943)). One model
family, several generators, then a selector. On BIRD dev with Gemini 1.5 Pro, a single prompt scores
63.01%. Self-consistency (cluster by execution result, take the majority) reaches 68.84%. The full
system, with a pairwise selection model, reaches 73.01% dev and 73.0% test in the paper. The current
BIRD board lists CHASE-SQL + Gemini at 74.90 dev / 76.02 test, higher than the paper; use the paper for
the ablations. Removing pieces from the 73.01 system:

| Removed | Execution accuracy | Delta |
|---|---|---|
| Pairwise selector, replaced by self-consistency | 68.84 | −4.17 |
| Pairwise selector, replaced by a single ranker prompt | 65.51 | −7.50 |
| LSH value retrieval | 70.09 | −2.92 |
| Query fixer (execution-error repair) | 69.23 | −3.78 |
| Divide-and-conquer generator | 71.77 | −1.24 |
| Online synthetic-example generator | 72.16 | −0.85 |
| Query-plan chain-of-thought generator | 72.36 | −0.65 |

The generators are partly substitutes for each other. The selector is not. Diversity has to come from
different prompts, because raising temperature widens the upper bound and lowers the lower bound, and
self-consistency then gets worse. That constraint is now absolute in this harness: `technique_factors.yaml`
records that temperature, top_p and top_k return HTTP 400 on these models.

**XiYan-SQL** (Liu et al., 2024, [arXiv:2411.08599](https://arxiv.org/abs/2411.08599)). Multi-generator
ensemble: supervised fine-tunes with different syntactic preferences, plus in-context learning.
Example selection masks named entities and matches question skeletons, so the retriever does not latch
onto the entity instead of the query shape. A refiner rewrites a candidate using the execution result
or the error. A fine-tuned selector picks among candidates grouped by execution result. Reported BIRD
test execution accuracy 75.63% (dev 73.34% on the board), voting among 5 candidates rather than
CHASE's 21. Their M-Schema representation beats raw DDL by about 2 points on average across four
models. On GPT-4o, their schema linker raised execution accuracy by 2.15 points versus showing every
column with random sample values. That gain is real for that model and that schema size. It does not
survive the studies in §1.3 once the model is strong and the schema fits.

**Alpha-SQL** (Li et al., 2025, ICML, [arXiv:2502.17248](https://arxiv.org/abs/2502.17248)).
Monte Carlo tree search over SQL-construction actions, the model as the action generator, a
self-supervised reward. 69.7% on BIRD dev with Qwen2.5-Coder-32B and no task fine-tune (board:
69.70 dev / 70.26 test), about 2.5 points above the previous GPT-4o zero-shot system they compare to,
and about 15–20 points above the same Qwen checkpoints without the search. The search is expensive
relative to one prompt. It is a way to get candidate diversity without temperature. It is not the
first thing to build on a ten-view schema where one prompt already scores in the 80s.

**ReFoRCE** (Deng et al., 2025, [arXiv:2502.00675](https://arxiv.org/abs/2502.00675),
[project write-up](https://haoailab.com/blogs/reforce/)). Aimed at Spider 2.0, not BIRD. The pipeline
is a workflow, not an open ReAct loop: compress table DDL so a 1,000-column schema fits, restrict the
answer format, explore columns by executing simple queries before writing the real one, then
self-refine until the same non-empty result appears twice, and vote across parallel threads. Empty
results are treated as failures. With o1-preview this scored 31.26 on Spider 2.0-Snow and 30.35 on
Spider 2.0-Lite (paper, leaderboard as of 13 Feb 2025), against Spider-Agent + o1-preview at 23.58
and 23.03. The live Snow board lists ReFoRCE + o3 at 62.89 and the original o1-preview run at 31.26.
The paper is frank that compression is lossy and that the method does not add much reasoning beyond
preprocessing and repair. The part that transfers here is small: execute, refuse to trust an empty or
all-zero result, repair a bounded number of times. The part that does not transfer is DDL compression
of `sem.*`.

**Agentar-Scale-SQL** (Wang et al., 2025, [arXiv:2509.24403](https://arxiv.org/abs/2509.24403)).
Test-time scaling in three layers: an RL-tuned reasoning model, iterative refinement, and parallel
candidates chosen by a pairwise tournament on (question, light schema, execution result), after
grouping candidates that return the same rows. 74.90% BIRD dev, 81.67% test, 77.00 R-VES, first on
the board as of 27 Nov 2025. The board has since moved past it (AskData, listed 22 Oct 2026, is at
83.40 test / 78.49 dev; GrainSQL, 26 Sep 2026, is at 82.95 / 77.77). Removing iterative refinement
cost 0.52 points in their ablation. They explicitly skip hand-built schema linking and bet on scale.
The tournament is the same idea as CHASE's selector, with execution results in the comparison.

**AskData** is a leaderboard entry (AT&T CDO, cited there as Shkapenyuk et al. 2025), not a paper this
refresh could read: the candidate arXiv record did not return text. At 83.40% test it is the current
public BIRD leader among systems with a named affiliation. Do not treat a leaderboard row without an
ablation as a design.

Human performance on BIRD remains 92.96%. The gap that is left is not "write more fluent SQL." It is
wrong population, wrong grain, wrong literal, and ambiguous questions.

### 1.3 The techniques, judged against a ten-view schema

**Schema linking.** Maamari, Abubaker, Lakhani, and Mhedhbi, "The Death of Schema Linking?"
([arXiv:2408.07702](https://arxiv.org/abs/2408.07702)), show that once a model is good at SQL and the
full schema fits in the window, filtering columns removes required ones and the accuracy loss from
that missing column exceeds the gain from a shorter prompt. Their no-linking pipeline, using
augmentation, selection, and correction instead, reached 71.83% on BIRD and ranked first at the time.
They are explicit that smaller models still benefit from linking, and that a warehouse schema which
does not fit still needs retrieval. CHESS's +2 points and 5× token cut is that second case.
Beskorovainyi (2026, [arXiv:2606.29733](https://arxiv.org/abs/2606.29733)) reruns the question on
open models with paired McNemar tests and a database-clustered bootstrap, n=1,534 BIRD dev items.
Lexical linking changes Llama-3.3-70B by −4.24 points (37 discordant items one way, 102 the other).
An embedding linker with higher gold-table recall (96.5% vs 94.0%) is indistinguishable from no
linking (−0.4, p=0.56). XiYan's +2.15 for GPT-4o is the opposing data point, on a linker that kept
recall high. The consistent rule is: do not prune a schema that fits; if you must prune, optimize
for recall, not precision.

`sem.*` is ten views (`remit_claims`, `remit_lines`, `claims`, `claim_lines`, `adjustments`,
`remarks`, `payments`, `provider_adjustments`, `diagnoses`, `payers`). It fits. Screen 2 should not
include a schema-pruning factor for it. The lossless escalation schema is the only place a
describe-on-demand tool is worth a later experiment, and only on questions that actually escalate.

**Semantic layer versus raw tables.** Two different comparisons get collapsed into one phrase.

dbt's 2026 rerun of the ACME Insurance benchmark (Ganz and Perigaud, 7 Apr 2026,
[write-up](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026)) uses 11 questions × 20
repeats. That is a tiny question set; the intervals are not reported. On questions the semantic
layer can express, Claude Sonnet 4.6 and GPT-5.3 Codex both sit at or next to 100%, and extra
reasoning effort does not move accuracy (it does move GPT latency, from about 8 s at `high` to over
20 s at `xhigh`). On the full 11, before extra modeling, both 2026 models score 64.5% with
text-to-SQL and 72.7% through the semantic layer, and the questions the layer cannot express score
0% because the layer refuses rather than inventing a join. After three generated models close that
coverage gap, Sonnet 4.6 scores 90.0% text-to-SQL against the modeled tables and 98.2% through the
semantic layer; GPT-5.3 Codex scores 84.1% and 100%. The failure mode is the point they emphasize:
text-to-SQL fails as a plausible wrong number; the compiler fails as an error.

Rumiantsau and Fokeev (28 Apr 2026, [arXiv:2604.25149](https://arxiv.org/abs/2604.25149)) run a
paired single-shot benchmark, 100 questions, Contoso in ClickHouse, Opus 4.7, Sonnet 4.6, and
GPT-5.4. A 4 KB hand-written document of measures, conventions, and disambiguation rules adds
+17 to +23 points. With the document the three models sit between 67.7% and 68.7%. Without it they
sit between 45.5% and 50.5%. Model choice inside that tier does not separate; the document does.
This is BIRD's evidence result, measured again on a frontier tier.

Both results describe giving the model the business semantics it otherwise has to invent. This repo
already did the large version of that by building `sem.*`: one row grain, `is_current_adjudication`,
`is_latest_submission`, payer responsibility, dates. Screen 1's "semantic layer" factor did not
replay the dbt comparison. It turned on `metrics` and `lookup` in front of an agent that could
already write SQL against those views. The +3 / −1 result says the compiler did not beat free SQL
on that easy set. It does not say the views were optional. The remaining question, for a harder set,
is whether the compiler reduces wrong-but-executed answers on the questions it covers, and what
fraction of questions it covers. dbt's coverage cliff is the risk: a compiler that cannot express
the question must return `cannot_express`, not a silent empty result.

**Multiple candidates and selection.** This is the largest pipeline effect that screen 1 did not
test. CHASE's selector is worth 4.2 points over execution-majority, and 7.5 points over stuffing
every candidate into one ranking prompt. XiYan gets to 75.63% with five candidates. Agentar's
tournament reads the execution result, not just the SQL text. Alpha-SQL's tree search is the same
bet in a more expensive shape. The 2026 open-model study is the caution: five-sample
self-consistency on Qwen2.5-Coder-32B changed accuracy by −0.07 and +0.13 points at about 5× the
tokens. Majority vote among correlated samples is cheap noise. A selector only helps when the
candidates are actually different and at least one of them is right. CHASE's Table 1 upper bound
is the quantity that matters, and this harness cannot get diversity from temperature. The available
sources of diversity are prompt shape (a direct generation, a query-plan sketch, a decomposition),
example set, and model (Haiku and Sonnet). Candidates should share the cached prefix and run in
parallel. Group by executed result first; call a selector only when the groups disagree.
Disagreement with no confident winner should abstain. A wrong number is worse than a refusal for
this product, and the current bench does not yet score that tradeoff (no reliability metric, no
out-of-scope split).

**Execution-guided self-correction.** Beskorovainyi's within-run estimate, which does not mix in a
second greedy sample, is that repairing drafts that failed to execute adds 1.2 to 3.7 points for
the Qwen and Llama models, and +3.46 / +4.43 for CodeLlama-7B / -13B, at 1.1–1.5× tokens (1.8–1.9×
for CodeLlama, whose drafts fail more often). The base-versus-correction McNemar test survives
Holm correction for every model except Llama-3.3-70B. The gain is the repair of non-executing SQL,
not a second opinion on SQL that already ran. CHASE's query fixer is larger (−3.78 when removed)
because their generator pool produces more broken SQL. Screen 1's result-check factor was about
zero, on a set where most SQL already executed and Sonnet was at the ceiling, and the factor was
aliased with other two-factor interactions. It is still worth a cheap, isolated rerun on hard
questions: repair at most twice, and only on execution errors. Programmatic checks (zero rows,
a rate outside [0, 1], a fan-out against the view's grain) are the OpenAI zero-row heuristic and
ReFoRCE's "empty is not a successful answer," implemented in the tool result rather than as another
model call. An LLM verifier on top of those checks is a later factor, not part of v1.

**Verification of the answer, as opposed to the SQL.** OpenAI's in-house data agent (Xu, Suresh,
and Tang, 29 Jan 2026,
[write-up](https://openai.com/index/inside-our-in-house-data-agent/)) evaluates by executing both
the agent's SQL and a hand-written golden SQL, then grading the pair of queries and the pair of
result sets with an LLM. Exact SQL string match is rejected because two queries can be the same
question with different syntax, and because an extra column is not a wrong answer. The grader
emits a score and an explanation. Reasoning traces are kept in the eval output so a wrong table
choice can be debugged. `bench/judge.py` already does this, and only when the deterministic
matcher has said no. Keep that order. The matcher is the primary metric. The judge is for
acceptable variation (column order, an extra supporting count, rounding). The screen-1 matcher
bug, where Sonnet was rejected for column order plus an extra count, is the reason the judge
exists; it is not a reason to let the judge override a numeric disagreement.

**Clarifying questions.** OpenAI's agent asks when the instruction is incomplete, and otherwise
proceeds with a stated default (last 7 or 30 days, in their example). BIRD-Interact says the
cost of getting this wrong is most of the task: follow-up success for Claude Sonnet 4 is 14.17%
against 22.33% on the priority question, and the full agentic setting is 12.67%. This repo has
no ambiguous questions and no user simulator, so an `ask` tool cannot be scored. Leave it off
in v1. When it is built, the gold has to be per interpretation, and over-asking on unambiguous
questions is part of the score.

**Memory.** OpenAI's memory stores a correction the user confirms: a filter or a constraint that
is not in the schema, scoped global or personal, editable, and prompted rather than written
silently. The example they give is an experiment-gate string the model cannot fuzzy-match.
That is the same object as this repo's glossary bullet. The difference is who writes it and on
which questions, which is the whole of §2. Online memory that updates from the question just
answered will leak into the next test question. v1 memory is an offline, reviewed, frozen
playbook. It is not a tool the agent calls during a scored session.

**Cost and latency.** The literature's expensive moves are the ones screen 1 already found cheap
to skip, plus one it did not test.

- Higher reasoning effort. dbt saw no accuracy change across Anthropic effort levels on semantic-layer
  questions, and a large latency change for OpenAI's `xhigh`. Screen 1's low→high effect was
  −2.4 on Haiku and +1.7 on Sonnet, both intervals covering 0. Leave effort at low for the screen
  and for v1.
- Self-consistency at 5× tokens for ~0 points on a strong open model (Beskorovainyi). Do not buy
  this. Buy three diverse prompts only if stage 2 says the selector beats one prompt by enough
  to pay for the calls.
- Schema pruning to save tokens. On ten views the token saving is smaller than one glossary, and
  the accuracy risk is the one Maamari measured. Cache the prefix instead
  (`technique_factors.yaml`: cache reads at 0.1× input, 512-token minimum).
- Repair. 1.1–1.5× tokens, and only on the questions whose SQL failed. This is the one
  inference-time technique whose cost scales with failure rather than with every question.
- A second model. BIRD-Interact's Claude Sonnet 4 agentic run is about $0.51 per task against
  GPT-5 at $0.24, for a success rate that is still 12.67%. Cascade (Haiku, then Sonnet only on
  a failure signal) is the cost control that matches this repo's two-model rule. It is untested.
  Screen 2 stage 2 measures it. Do not build a multi-agent split: Anthropic's production note
  is that multi-agent systems cost on the order of 15× a chat, and the failure mode is
  handoff, not SQL (see §1.4). CHESS's four agents exist to cut a huge catalog. We do not have
  one.

### 1.4 OpenAI's data agent and Anthropic's agent guidance

**OpenAI, 29 Jan 2026** ([inside our in-house data agent](https://openai.com/index/inside-our-in-house-data-agent/)).
The system is GPT-5.2 over an internal platform of 70,000 datasets and 600 petabytes, used by about
3,500 people. The context stack has six layers: table usage (schema, types, lineage, historical
queries), human annotations, code-derived descriptions refreshed by a daily Codex crawl ("meaning
lives in code"), institutional documents retrieved with permission checks, user-confirmed memory,
and live warehouse inspection when the stored context is missing or stale. Embeddings are how they
avoid putting 70,000 schemas in the prompt. That retrieval problem is not ours. The lessons at the
end of the post are.

1. Less is more. A tool set with overlapping tools confused the agent. Consolidate.
2. Guide the goal, not the path. Highly prescriptive prompting degraded results. Questions share
   a shape and differ in the details; a fixed procedure pushed the agent down the wrong path.
3. Meaning lives in code. Schema and query history say what a table looks like and how often it
   is used. The pipeline that builds it says what a row means, how fresh it is, and which rows
   are absent.

The self-correction behavior they describe is the zero-row check: if a result is empty because of
a bad join or a bad filter, the agent investigates and retries, with the failure still in context.
They also expose assumptions and link the executed query, so a person can check the number.
`ccsim/query.py` is already the right shape for that transparency: one per-request DuckDB
session, `sem.*` only unless the session is escalated, configuration locked.

What not to copy: RAG over a 70,000-table catalog, Slack and Docs retrieval, personal memory,
a Codex crawl of a monorepo. Our analog of layer 3 is a one-line derivation comment generated
from `claude/schema/02_semantic_layer_views.sql`, not a crawler. Our analog of layer 5 is the
frozen playbook in §2.

**Anthropic, "Building effective agents," 19 Dec 2024**
([engineering post](https://www.anthropic.com/engineering/building-effective-agents)).
Workflows are LLMs and tools on code-defined paths. Agents are LLMs that direct their own tool
use. Start with the simplest thing that works; add a workflow before adding an agent; add an
agent only when the path cannot be written down. The building block is one model with retrieval,
tools, and memory. The workflows that match this product are prompt chaining with a programmatic
gate, routing (Haiku for the common case, Sonnet for the escalated one), and an
evaluator-optimizer loop when there is a clear check (execution error, a grain invariant).
Parallel voting is listed as a workflow, not as an architecture. Their three rules: keep the
design simple, show the plan, and spend as much care on the tool interface as on the prompt.
They report spending more time on tools than on the prompt for their SWE-bench agent, and they
recommend making the wrong argument hard to pass (absolute paths, in their example; for us,
a guard that rejects anything but a single `SELECT`, and a compiler that rejects a filter value
that was not returned by lookup).

The post's update banner says the tooling around it has changed since December 2024. The
distinction between a workflow and an agent has not. A text-to-SQL session over a known,
small, guarded schema is a short agent loop inside a workflow: the code decides the tool
surface, the budget, the repair cap, and the scorer; the model decides which tool to call
next. A planner/executor/verifier multi-agent split is the thing the post says to add only
after the simple loop has stopped improving. Screen 1 and held-out 1 have not reached that
point.

**Anthropic, "Effective context engineering for AI agents"**
([engineering post](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).
Context is a finite attention budget. Context rot is the finding that recall gets worse as the
window fills, across models. The operating rule is the smallest set of high-signal tokens that
still makes the right answer likely. Practical consequences they draw:

- Put the stable, always-relevant context in up front (their example is Claude Code dropping
  `CLAUDE.md` in). Retrieve the long tail just in time. For us the stable context is the ten
  views, the glossary, and the enum profile. The long tail is payer literals, a similar
  solved question, and the lossless schema. Those stay behind tools.
- Compact tool results. Once a tool has returned, the raw payload does not need to sit in the
  transcript forever. The lightest version is already in the harness: `agent_cli.py` shows 20
  rows, not the full result.
- Structured notes, outside the window, are how an agent keeps a fact across a long task.
  That mechanism is for a session that runs hundreds of steps. A billing question is not that
  session. Use it for the offline playbook, not for a NOTES.md the model rewrites while it
  answers one question.
- Multi-agent setups are a context-isolation tool (each worker holds a slice). They are also
  a cost multiplier. Not v1.

OpenAI's "guide the goal, not the path" and Anthropic's "smallest high-signal context" agree
with the held-out result. Procedural steps on top of the pitfalls text were +1.9 for Haiku
with an interval of about ±16, and 0.0 for Sonnet with an interval of about ±6. The steps
are not a free lunch, and they fight the prescriptive-prompt failure OpenAI reported. v1's
briefing states the goal, the definitions, and the tool contracts. Rules we are willing to
enforce (read-only SQL, `sem.*` unless escalated, repair cap, row cap) are code.

## 2. Learning domain knowledge without overfitting

### 2.1 The pitfalls text is the phenomenon, not a surprise

Screen 1's pitfalls list names the traps that dominate that question file: `is_latest_submission`
can land on the secondary payer, fan-out joins over per-submission rows, claim grain versus line
grain. Those are real properties of `sem.*`. They are also the properties the questions were
written to probe, by the same session. On questions written later, by authors who never saw the
list, Haiku's +16 became −2 with an interval that runs from −15 to +13. Sonnet's +6 became +7,
with the interval touching 0 at the bottom and +17 at the top.

Two explanations fit, and 27 questions cannot separate them. The list may have been specific to
screen 1's wording, so Haiku pattern-matched it and Sonnet, which was already close to the
ceiling, had less to gain and less to overfit. Or the list is a true but smaller effect, and
Haiku's +16 was noise plus a few screen-1 items. The Sonnet interval [0.0, +16.7] is compatible
with a real gain and with no gain. Shipping the list because Sonnet "replicated" would repeat
the original mistake at a smaller effect size.

The pass^2 drop for Haiku (70.4% → 66.7%) is a separate reason not to ship it. A context
change that does not raise mean accuracy and lowers the chance that both repeats are correct
is a regression in the metric a user feels.

Held-out 1 already states the rule this refresh adopts. The glossary is a trained artifact.

1. TRAIN. Failures may be read and turned into general rules.
2. VALIDATION. Never read while writing. Used to accept or reject a change.
3. TEST. Authored after the text is frozen. Run once, at the end.

The existing pitfalls file was trained on the screen-1 questions and tested on them. Held-out 1
is the first validation, and it does not accept the Haiku claim. It does not cleanly accept the
Sonnet claim either.

### 2.2 Optimizers

Four systems are the current practical set. All of them will overfit a 40-question train split
if the selection set is the same split, or if it is the validation split we still want to trust.

**MIPRO / MIPROv2.** Opsahl-Ong et al. (2024, [arXiv:2406.11695](https://arxiv.org/abs/2406.11695))
define the problem as jointly choosing the instruction and the few-shot demonstrations of every
module in a language-model program, with no module-level labels. Demonstrations are bootstrapped
by keeping traces that score well on the training metric. Instructions are proposed by another
model, grounded in a description of the program and the data. A Bayesian surrogate (tree-structured
Parzen estimator) picks the combination, using mini-batch scores rather than a full evaluation
each round. Optimizing demonstrations mattered most in their suite; optimizing instructions
mattered when the task had conditional rules; doing both won. DSPy ships this as `MIPROv2`
([optimizer notes](https://dspy.ai/current/diving-deeper/choosing-an-optimizer/)). GEPA and ACE
use that implementation as the baseline they claim to beat. The paper's own selection metric is
a validation score. If that validation set is our VAL, VAL is no longer a confirmation set.

**GEPA** (Agrawal et al., 2025, [arXiv:2507.19457](https://arxiv.org/abs/2507.19457)).
Genetic-Pareto prompt evolution. It samples trajectories (reasoning, tool calls, tool output),
reflects on them in natural language, proposes a prompt edit, and keeps a Pareto frontier of
prompts so one edit cannot delete a different prompt's wins. Across six tasks it beats GRPO by
about 6 points on average and up to 20, at up to 35× fewer rollouts, and beats MIPROv2 by over
10 points (their example is +12 accuracy on AIME-2025). The metric has to return feedback text,
not only a scalar; DSPy's `Prediction(score, feedback)` is the interface. GEPA prefers short
prompts. That preference is a problem for a billing glossary, not a virtue. ACE's AppWorld cost
table shows why the budget goes where it goes: on their run, GEPA spent 139 million of its
204 million adaptation input tokens validating candidate prompts on 57 held-out queries.
Those 57 queries are burned as a selection set. We do not have 57 queries to burn.

**TextGrad** (Yuksekgonul et al., 2024, [arXiv:2406.07496](https://arxiv.org/abs/2406.07496)).
Textual backpropagation. A compound system is a graph; an LLM writes natural-language criticism
of each variable; the variable is rewritten from that criticism. The objective can be an
execution result, a unit test, or another prompt. It is the right tool for a short instruction
or a code snippet, where rewriting the whole variable is the update you wanted. It is the wrong
tool for a glossary. The update replaces the text. Domain rules that the criticism did not
mention do not have to survive.

**ACE** (Zhang et al., 2025, [arXiv:2510.04618](https://arxiv.org/abs/2510.04618)).
Agentic context engineering. The target is a playbook that accumulates, not a prompt that gets
rewritten. Three roles: a generator that produces the trajectory, a reflector that pulls out a
lesson, a curator that merges the lesson as a delta. They name two failure modes that match
this repo.

- Brevity bias. Optimizers that are scored on a validation metric, GEPA included, collapse
  toward a short generic instruction and drop the domain heuristic that only matters on some
  items. Gao et al. are cited for prompt optimization of test generation converging on
  near-duplicate one-liners.
- Context collapse. Asking a model to rewrite the whole context produces a shorter summary
  and a sharp accuracy drop. Their AppWorld case: at step 60 the context is 18,282 tokens and
  accuracy is 66.7; the next rewrite is 122 tokens and accuracy is 57.1, below the 63.7
  baseline with no adaptation.

On AppWorld with DeepSeek-V3.1, offline ACE beats GEPA by 11.9 points and in-context learning
by 12.3, and still gains about 14.8 over ReAct when the reflector does not see ground-truth
labels, using execution success as the signal. On finance tasks the average gain they report
is +8.6. On a BIRD-SQL slice from StreamBench, also DeepSeek-V3.1, offline adaptation on 1,000
training examples moves the average from 47.8 to 52.9 (+5.1), mostly on the simple subset
(+7.1). GEPA on the same 1,000, with a separate 500-example validation set, reaches 52.2.
A +5 point move on BIRD, for a 671B model, with 1,000 labeled training examples, is the
realistic prior for "let an optimizer write our glossary." It is not a +16.

ACE is the procedure to copy, with the splits enforced outside the optimizer. Use the
generator/reflector/curator loop on TRAIN traces. Keep bullets incremental. Do not let the
curator rewrite the basic glossary. Cap the playbook (the catalog's T07 cap of about 40 lines
is the right order of magnitude). A bullet is accepted by a person only if it is still true
after the train questions are deleted: grain, date basis, population, which view holds the
money. A bullet that restates a question's filter, a payer name, or a CPT code is rejected.
GEPA and MIPROv2 stay available later for the instruction wording and for choosing TRAIN
demonstrations, after the playbook is frozen, and only against an inner dev slice carved out
of TRAIN. VAL is touched once.

### 2.3 How many questions

The held-out intervals are the empirical noise estimate. They are question-level bootstrap
intervals on the paired difference, which is the right interval for "does this config beat
base on the next question drawn from this pool."

Haiku pitfalls: effect −1.9, interval [−14.8, +13.0], width 27.8, n=27.
Sonnet pitfalls: effect +7.4, interval [0.0, +16.7], width 16.7, n=27.
The report's "±15 points" is the Haiku half-width, rounded. Sonnet's half-width is about ±8.
Haiku is noisier because more questions flip both ways. A design that has to detect an effect
on both models sizes to Haiku.

Bootstrap interval width scales as 1/√n if the question-level difference distribution stays
similar. Required validation size for a target half-width h, using the observed Haiku width:

n ≈ 27 × (13.9 / h)²

| Target half-width | What it can separate | n (Haiku noise) | n (Sonnet noise) |
|---|---|---|---|
| ±10 points | a 15-point effect, barely a 10-point one | ~50 | ~20 |
| ±7.5 points | a 10-point effect, if the estimate lands near the truth | ~95 | ~35 |
| ±5 points | a 10-point effect with room; a 5-point effect only at the edge | ~210 | ~75 |

"About 80 questions," which held-out 1 asked for, brings Haiku's half-width to roughly ±8 and
Sonnet's to roughly ±5. That is enough to notice another +16. It is not enough to confirm a
+7, and it is not enough to declare a +5 absent.

Power is stricter than interval width, because the interval has to exclude 0 in a future
sample, not merely be narrow around a sample we already have. For paired binary outcomes the
Connor's conservative normal approximation (Connor, 1987, Biometrics 43:207–211,
https://doi.org/10.2307/2531961) is

n = [ z_(α/2) √ψ + z_β √(ψ − δ²) ]² / δ²

where δ is the net difference in accuracy, ψ is the proportion of questions on which the two
arms disagree, α=0.05 two-sided (z=1.96), and power 80% (z_β=0.8416). ψ has to be assumed.
Haiku's held-out interval is consistent with ψ around 0.15–0.20. At ψ=0.20:

| True effect δ | n per arm, 80% power |
|---|---|
| 10 points | ~155 |
| 5 points | ~625 |

At ψ=0.12, closer to Sonnet's quieter interval, a 10-point effect needs about 90 questions.
Beskorovainyi's design is the existence proof that this is not fussy: with n=1,534 they can
see a 1-point repair effect, and they also show that a second greedy pass flips 0.2–3.7% of
items, so differences under about one point are serving noise. We will not have 1,534
hand-verified billing questions. The honest conclusion:

- Screen 2 can be powered for a **10-point** effect on both models at about **120–160**
  validation questions. 120 is the recommended size: under Haiku's observed noise the
  half-width is about ±6.6, and under the ψ=0.20 formula, power against a true 10-point
  effect is in the 70% range rather than 80%. Say that in the write-up of the screen.
  Do not describe a non-significant 4-point difference as "no effect."
- A **5-point** effect is a later confirmatory test, on the order of 200 questions for a
  ±5 interval and about 600 for 80% power. Do not design screen 2 to chase it.
- Repeats are not a substitute for questions. Two repeats cut the within-question sampling
  noise and make pass^2 computable. The bootstrap resamples questions, so the interval is
  still driven by how many questions there are. Held-out's two repeats were correct; its
  27 questions were the limit.
- Three questions that every config failed did not bias the contrasts, because a constant
  failure has paired difference 0. They did bias the absolute accuracy. New questions have
  to state the population, including claims that appear only through an 835 because the
  generator starts mid-stream. That fact now belongs in the basic glossary, as held-out 1
  already recorded, so it is part of every arm rather than a playbook bullet.

Authoring rule, carried forward. VAL and TEST authors see the basic glossary only. They do
not see the playbook, the old pitfalls text, or TRAIN failures. Each question keeps an
independent verifier and the existing three-way agreement check. Opus authors and verifies.
Opus is not an evaluated model.

## 3. Recommendations, in order

1. **Do not adopt the screen-1 pitfalls text.** Haiku's +16 did not replicate (−1.9, interval
   about ±14). Sonnet's +7 has an interval from 0 to +17, and the same text made Haiku's
   pass^2 worse. Keep the file as a negative control, labeled as overfit to screen 1.

2. **Treat every glossary change as a trained artifact with a frozen validation gate.**
   TRAIN failures may be read. VAL is not read while writing. TEST is authored after the
   freeze and run once. This is held-out 1's rule, and it is also the only way MIPROv2,
   GEPA, or ACE can be used here without burning the confirmation set.

3. **Build agent v1 as a short single-agent loop over `sem.*`, with the compiler available
   and not required.** Details in §4. Do not build a multi-agent system, a schema linker, or
   an online memory tool. The production query path stays `ccsim/query.py`.

4. **Run screen 2 as two stages on a harder 120-question validation set, both models,
   questions authored against the basic glossary only.** Stage 1 isolates knowledge. Stage 2
   adds one inference factor at a time, and only if the winner is not back at the ceiling.
   Details in §5. Skip effort, procedural steps, few-shot, and schema pruning until something
   in stage 2 actually moves accuracy by 10 points.

5. **Pre-register the decision rule before the run.** For each model separately: accept an
   arm over base only if the paired question-level bootstrap interval lies entirely above 0.
   Report the point estimate, the interval, pass^2, mean tool calls, and no-answer count.
   A point estimate of +7 with an interval that includes 0 is not an acceptance. Do not pool
   Haiku and Sonnet.

6. **Use the frozen 3.3 snapshot for the next screen.** Screen 1 was generator 3.0 and
   held-out 1 was 3.1. Retuning a playbook against a moving generator is another way to
   overfit. The generator stays frozen.

7. **Keep the judge behind the matcher.** OpenAI's pattern is already implemented. Do not
   let an LLM judge become the number a technique is accepted on.

8. **Park clarifying questions, abstention metrics, and lossless-schema retrieval.** They are
   the BIRD-Interact and Spider 2.0 problems. The current questions cannot measure them.
   Building the tools before the split exists will produce an untested `ask` button.

## 4. Proposed agent architecture v1

One model, a fixed tool list, a turn budget, and a code path around it. Anthropic's agent,
not Anthropic's multi-agent research system. The bench surface is `bench/agent_cli.py`. The
production database session is `ccsim/query.py`: per-request DuckDB, `sem.*` always,
`lossless.*` only when the session is escalated, `allowed_directories` and
`lock_configuration` on. v1 does not add a tool that bypasses that session.

```text
cached prefix                         per question, after the cache breakpoint
-----------------------------         ----------------------------------------
goal briefing                         the question
basic glossary                        (nothing else, until a playbook passes VAL)
generated column dictionary
enum profile

tools: sql, lookup, metrics, answer, refuse
code around the model: guard, execute, row cap, repair cap, programmatic checks, scorer
```

**Briefing.** Goal, definitions, tool contracts. Not a numbered procedure. Screen 1's
procedural gain did not survive contact with the pitfalls text, and OpenAI measured
prescriptive prompting as a regression. Must-do constraints live in the guard and the
compiler.

**Glossary.** The basic glossary, including the data-start population fact held-out 1 added.
Playbook bullets are appended only after they pass the validation rule in §3. The screen-1
pitfalls text is not in the prefix.

**Dictionary and profile.** Generated from `03_semantic_model.yaml` and the view SQL, so they
cannot drift, which is OpenAI's "meaning lives in code" at the size of this schema. Grain,
keys, and a one-line derivation per view. Profile is the low-cardinality enums only
(claim status, frequency code, CARC group, payer responsibility). No payer-name list, no
free text, no identifiers. Payer names go through `lookup`, which returns candidates and
counts and does not silently break a tie.

**`sql`.** Single read-only statement, `sem.*` unless the question is an escalation question.
Return at most 20 rows plus the row count. On a database error, return the error and at most
two repair chances. On success, attach programmatic warnings: zero rows, a rate outside
[0, 1], a numerator greater than a denominator, a join that multiplies the grain the
dictionary declares. Warnings are text in the tool result. They are not a second model.

**`metrics`.** The typed compiler in `claude/schema/04_agent_interface.py`. If the question
is outside the catalog, the tool returns `cannot_express`. It does not invent SQL. v1 leaves
the tool available and does not force it first. Screen 1 gave no reason to force it. Stage 2
measures whether making it available changes accuracy or only changes the share of
wrong-but-executed answers. Coverage (share of VAL questions the compiler can express) is
reported next to accuracy so a coverage cliff is visible.

**`answer` / `refuse`.** The submitted SQL or metric call is re-executed by the harness, not
trusted from the transcript. Refusal is for PHI, another customer, and writes. The model
does not get an `ask` tool until an ambiguous split exists.

**What v1 deliberately leaves out.** Schema pruning of `sem.*`. Temperature sampling.
A pairwise selector (stage 2 may add it). Online memory. A reflector running during the
scored session. Multi-agent handoffs. High effort. Few-shot examples in the prefix (screen 1
effect was about zero, and examples are a leakage channel until they are drawn from TRAIN
and checked against VAL).

**Caching and tier.** The prefix above is stable across questions, so it is the cache
breakpoint. Haiku is the default evaluated tier only if stage 1 puts it within the
pre-registered margin of Sonnet on the accepted context. Until then, both models are
evaluated and neither is assumed to be the production model. A cascade (Haiku, escalate to
Sonnet on a failed repair or a check warning) is a stage-2 arm, not the v1 default.

## 5. Proposed screen 2

Not another 2^(7-3). Resolution IV told us the main effects and could not tell us which
two-factor pair produced them, and the one large main effect failed validation. Screen 2 is
a paired comparison against a fixed base, one factor at a time, on questions hard enough
that Sonnet's base accuracy has room to move. Screen 1's Sonnet ceiling (12 of 16 configs
at or above 97%) made every Sonnet effect look like noise.

### Questions

| Split | n | Who may read it | Role |
|---|---|---|---|
| TRAIN | 40 | Playbook authors and the reflector | Induce bullets. Carve 15 of the 40 out as the optimizer's inner dev if an optimizer is used. Not an effect estimate. |
| VAL | 120 | Nobody, until the arm is frozen | Screen 2. The only accept/reject set. |
| TEST | 40, authored after the winner is frozen | Nobody until the run | One confirmatory contrast, winner versus base. Not used to pick the winner. |

VAL at 120 is the 10-point design in §2.3, with power a bit under 80% if Haiku's
disagreement rate stays near 0.20. If authoring 120 verified questions is the binding
constraint, 80 is the floor, and the write-up must say the screen can detect a 15-point
effect and cannot clear a 10-point one on Haiku. Do not start stage 2 on a 27-question set.

Target difficulty: Sonnet base accuracy around 60–80% on VAL, not 95%. Questions have to
force a choice of grain, date basis (`payment_date` vs `service_date` vs `submission_date`),
and population (primary payer, current adjudication, claims with no 837 because the extract
starts mid-stream). State the population in the question whenever the generator's data-start
would otherwise make two readings defensible. Authors see the basic glossary only.

Dataset: the frozen generator 3.3 bench snapshot. One snapshot for TRAIN, VAL, and TEST.

### Models and repeats

Haiku and Sonnet, blocked, never pooled. Opus authors and verifies only.

Stage 1 runs one repeat (720 sessions: 3 arms × 2 models × 120). The leading arm and the
base then each get a second repeat (480 sessions) so pass^2 exists for the contrast that
might be accepted. If the batch budget allows 1,440 sessions, run two repeats from the start.
Stage 2 uses two repeats on every arm, because pass^2 is part of why the pitfalls text was
rejected.

### Base, held fixed until a stage accepts a change

Basic glossary, goal briefing, free SQL over `sem.*`, lookup off, metrics off, examples off,
result checks off, effort low, one candidate, repair off. This is the held-out `base` config,
not the screen-1 winner. Starting from the winner would launder the overfit text into every
contrast.

### Stage 1 — knowledge only

Three arms. Everything else at the base.

| Arm | What it adds | Why it is here |
|---|---|---|
| K0 | Nothing. The base. | The paired denominator. |
| K1 | Generated dictionary + enum profile. No pitfalls, no playbook. | The screen-1 winner bundled this with the pitfalls text. Held-out's Haiku +11 might be this. Isolate it. |
| K2 | A playbook distilled from TRAIN only, ACE-style deltas, human-reviewed, frozen before any VAL session is opened. | The version of "domain knowledge" that has a chance of being general. |

K2's authoring procedure:

1. Run K0 on TRAIN with both models, two repeats. Cluster the questions that either model
   misses. Read the SQL and the tool trace, not only the score.
2. A reflector model writes a candidate bullet from a cluster. The curator may append it.
   The curator may not rewrite the basic glossary or delete an accepted bullet without a
   person.
3. A person accepts the bullet only if it is a property of the views (grain, date, population,
   which table holds the money, how reversals work) and does not mention a question id, a
   payer name, a code, or a threshold that appears in TRAIN.
4. Cap at 40 lines. If two bullets give opposite instructions, drop both until a person
   rewrites them as one rule.
5. Freeze. Hash the text into the run manifest. Then open VAL.

Optional negative control, only if the session budget remains after K0–K2: the original
screen-1 pitfalls text, on VAL, one repeat, both models. Expected result, if the overfit
reading is right: near 0 on Haiku, a small positive on Sonnet whose interval includes 0.
It is a check, not a candidate for adoption.

Acceptance: per model, K1 or K2 beats K0 on the pre-registered interval rule. If neither
does, v1 ships the basic glossary and stage 2's base stays K0. A Sonnet-only win does not
get copied into the Haiku prefix.

### Stage 2 — one factor at a time, on the accepted prefix

Run a factor only if the stage-1 winner is under about 90% on that model. A return to the
ceiling means stop and spend the next effort on harder questions, not on more techniques.
Each arm is paired against the stage-1 winner. Accept or reject with the same interval rule
before the next factor is added. Do not turn on two new factors in one arm.

Order:

1. **Repair.** At most two retries, and only when DuckDB returns an error. Plus the
   programmatic warnings on successful results, as tool-result text, not as a retry trigger.
   Cheapest literature-backed factor (about +1 to +4 points on open models, concentrated on
   queries that did not execute). Screen 1's null is not decisive: the factor was aliased
   and the SQL mostly ran.
2. **Three candidates, no temperature.** Generators: the ordinary goal prompt, a prompt that
   asks for the grain and the date basis before the SQL, and a prompt that splits the
   question into a filter and an aggregation. Same prefix, parallel calls. Group by executed
   rows. If one group is a strict majority, take it. If not, one pairwise selector call
   (Sonnet, even when the candidates are Haiku's) sees the question, the SQL, and the
   result digests. If the selector declines, the arm abstains and the item is wrong for
   accuracy purposes. Report accuracy, abstain rate, and the fraction of items where at
   least one candidate was right (the upper bound CHASE's Table 1 is about). Drop the factor
   if the upper bound is within a couple of points of the single candidate: the prompts are
   not actually diverse, and a selector cannot help.
3. **Cascade.** Haiku answers. Escalate to Sonnet only if both repairs failed, or a
   programmatic warning fired, or Haiku abstained. Compare accuracy and cost per correct
   answer to Sonnet-always and Haiku-always. This is the routing workflow in Anthropic's
   note, measured.
4. **Metrics and lookup available.** Not forced. Report the fraction of VAL questions the
   compiler can express, accuracy on that subset, accuracy on the complement, and the
   wrong-but-executed rate against SQL-only. A gain that exists only on the covered subset
   is still a gain. A loss on the complement means the tool is distracting, which is
   OpenAI's overlapping-tool failure, and the tool comes back off.

Not in screen 2: effort low versus high, procedural versus goal, few-shot k, schema pruning,
an LLM-as-verifier, multi-agent splits, `ask`. Few-shot returns only after K2 is frozen,
with demonstrations taken from TRAIN and dropped if they are near-duplicates of a VAL
question. That is a screen 3 candidate, and screen 1 gives it a near-zero prior.

### What a finished screen 2 is allowed to change

The accepted prefix (K0, K1, or K2), plus any stage-2 factor that beat that prefix on VAL
for that model. TEST then runs winner versus K0, both models, two repeats, 40 questions.
TEST does not add a new factor. If TEST's interval includes 0, the factor does not ship,
regardless of VAL. Forty questions will only confirm a large effect; a 5-point VAL effect
will look inconclusive on TEST, and the correct action is to leave it unshipped until a
larger TEST exists, not to peek and keep it.

## 6. Sources

Papers and posts actually read for this note. Leaderboard figures are from the pages as
fetched on 25 Oct 2026.

- Li et al. 2023. BIRD. https://arxiv.org/abs/2305.03111
- Lei et al. 2024. Spider 2.0. https://arxiv.org/abs/2411.07763 and https://spider2-sql.github.io/
- BIRD leaderboard. https://bird-bench.github.io/
- BIRD-Interact, ICLR 2026. https://arxiv.org/abs/2510.05318
- Talaei et al. 2024. CHESS. https://arxiv.org/abs/2405.16755
- Pourreza et al. 2024. CHASE-SQL. https://arxiv.org/abs/2410.01943
- Liu et al. 2024. XiYan-SQL. https://arxiv.org/abs/2411.08599
- Li et al. 2025. Alpha-SQL. https://arxiv.org/abs/2502.17248
- Deng et al. 2025. ReFoRCE. https://arxiv.org/abs/2502.00675 and https://haoailab.com/blogs/reforce/
- Wang et al. 2025. Agentar-Scale-SQL. https://arxiv.org/abs/2509.24403
- Maamari et al. 2024. The Death of Schema Linking? https://arxiv.org/abs/2408.07702
- Beskorovainyi 2026. On-premises open models on BIRD, component ablations. https://arxiv.org/abs/2606.29733
- Ganz and Perigaud, 7 Apr 2026. dbt semantic layer vs text-to-SQL. https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026
- Rumiantsau and Fokeev, 28 Apr 2026. Semantic-layer paired benchmark. https://arxiv.org/abs/2604.25149
- Xu, Suresh, and Tang, 29 Jan 2026. OpenAI's in-house data agent. https://openai.com/index/inside-our-in-house-data-agent/
- Anthropic, 19 Dec 2024. Building effective agents. https://www.anthropic.com/engineering/building-effective-agents
- Anthropic. Effective context engineering for AI agents. https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Opsahl-Ong et al. 2024. MIPRO. https://arxiv.org/abs/2406.11695 and https://dspy.ai/current/diving-deeper/choosing-an-optimizer/
- Agrawal et al. 2025. GEPA. https://arxiv.org/abs/2507.19457
- Yuksekgonul et al. 2024. TextGrad. https://arxiv.org/abs/2406.07496
- Zhang et al. 2025. Agentic Context Engineering. https://arxiv.org/abs/2510.04618
- Connor 1987. Sample size for testing differences in proportions for the paired-sample design. Biometrics 43(1):207–211. https://doi.org/10.2307/2531961

Local results this note is accountable to: `research/screening_results_1.md`,
`research/heldout_results_1.md`, `research/screening_design.json`,
`research/heldout_configs_1.json`, `bench/eval_subagents.py`, `bench/agent_cli.py`,
`bench/judge.py`, `ccsim/query.py`.
