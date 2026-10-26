# Analyzer bundles v1: ten multi-start points over the 26 parameters

26 Oct 2026. Companion to `research/analyzer_parameters_v1.md` (parameters are cited by number, #1–#26).
The plan is fixed: about ten bundles, each a full setting of all 26 parameters, run on ~120 hard validation
questions × 2 repeats on `claude-haiku-5-5`. The winner is taken apart by leave-one-out (LOO), unused parameters
are then added one at a time (coordinate descent), and one sealed-test run confirms the result. This note
designs the bundles and the stages after them. It does not change any code.

Evidence tags used below:
- **ours/S1**: screen 1, 36 questions, 1 repeat, generator 3.0, Haiku and Sonnet (`screening_results_1.md`).
- **ours/H1**: held-out 1, 27 hard questions, 2 repeats, generator 3.1, Haiku and Sonnet (`heldout_results_1.md`).
- **ours/grid**: Grok 4.7 role-playing the analyst on DEV_HARD_v1, near ceiling (`grid/SUMMARY.md`). Weak evidence.
- **theirs**: other people's models and benchmarks, as summarized in `sota_refresh_2.md` and the parameter file.
  Not re-checked here unless stated.

## 0. The base, and what "off" means for each parameter

B0 is the all-off bundle. Each parameter's off level is below. Two choices need a reason:

- #1 is off at **basic**, not none. The basic glossary carries the data-start population fact that H1's three
  constant failures exposed, and the VAL questions are authored against it. Removing it measures question wording,
  not technique. "None" is not tested anywhere (stated reason; it can be run once as a floor check if wanted).
- #13 is off at **none**: no hints, no forced repair turn on the final SQL, `max_repairs` 0. A tool loop still
  shows the model DuckDB errors from `run_sql`; that is inherent to the loop and is the same in every bundle.

| # | Off level (B0) | # | Off level (B0) |
|---|---|---|---|
| 1 | basic glossary (with data-start fact) | 14 | off |
| 2 | off | 15 | 1 candidate |
| 3 | types only (`describe_relation`) | 16 | prompt only (current refusal text; no abstain path) |
| 4 | none | 17 | Haiku |
| 5 | off | 18 | single agent |
| 6 | k = 0 | 19 | raw errors, raw rows, current tool descriptions |
| 7 | goal briefing | 20 | 8 turns, 20 rows shown |
| 8 | free SQL | 21 | never ask; transcript state |
| 9 | off | 22 | off |
| 10 | none | 23 | off |
| 11 | off | 24 | one route |
| 12 | low | 25 | off |
| 13 | none | 26 | n/a while #17 is Haiku; with #17 on, off = current analyzer trigger (no answer, abstain, blocking warning) |

Fixed in every bundle: prompt caching on (note: `AnalyzerConfig.prompt_cache` defaults to False and the runner must
set it), PHI/injection guards in code, no schema pruning, `claude-haiku-5-5` as the executor.

## 1. The bundles

### 1.1 Full settings

Cells show the level; "·" means the B0 level. Abbreviations: inv = + invariants, pit = + pitfalls,
desc = + descriptions/grain, deriv = + derivations, H→S = Haiku then Sonnet on a trigger, sig = + result signals.

| # | B0 base | B1 know | B2 compute | B3 typed | B4 explore | B5 deliberate | B6 lean | B7 traps-in-code | B8 cascade | B9 planner | B10 anchor |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 glossary | basic | inv | · | · | · | · | · | inv | · | · | pit |
| 2 playbook | off | on | · | · | · | · | · | · | · | · | on |
| 3 dictionary | types | deriv | · | deriv | desc | desc | · | desc | desc | desc | deriv |
| 4 profile | none | enums | · | · | full | · | · | · | · | · | full |
| 5 lookup | off | on | · | mandatory | on | · | · | · | · | · | mandatory |
| 6 few-shot | 0 | k3 BM25 | · | · | · | k3 BM25 | · | · | · | · | k5 BM25 |
| 7 style | goal | · | · | · | · | procedural | minimal | · | · | · | procedural |
| 8 generation | free SQL | · | · | compiler, SQL fallback | · | decompose CTE | · | · | · | · | compiler, decompose fallback |
| 9 explore | off | · | · | · | probe | · | · | · | · | · | probe |
| 10 plan | none | · | · | · | · | plan + replan | · | · | · | plan (by Sonnet) | plan + replan |
| 11 assumptions | off | · | · | · | · | on | · | · | · | on (in plan) | on |
| 12 effort | low | · | low→high on failure | · | · | medium | · | · | · | · | high |
| 13 repair | none | · | errors+hints+sig, max 4 | errors+hints, max 2 | errors, max 2 | errors+hints, max 2 | errors, max 1 | errors+hints+sig, max 2 | errors+hints, max 2 | errors, max 2 | errors+hints+sig, max 4 |
| 14 checks | off | · | rules + Haiku verifier | · | · | · | · | rules | rules | · | rules + Haiku verifier |
| 15 candidates | 1 | · | 3 + Sonnet selector | · | · | · | · | · | · | · | 3 + Sonnet selector |
| 16 abstention | prompt | · | · | · | · | · | · | · | · | · | · |
| 17 tier | Haiku | · | · | · | · | · | · | · | H→S | · | H→S |
| 18 multi-agent | single | · | · | · | · | · | · | · | · | Sonnet planner → Haiku | Sonnet planner → Haiku |
| 19 tool design | raw | · | · | actionable errors, descriptions | · | · | actionable, digest, descriptions | actionable errors | · | · | actionable, descriptions |
| 20 budgets | 8 / 20 | · | · | · | 8 / 50 | · | 4 / 20 | · | · | · | 8 / 50 |
| 21 clarification | never | · | · | · | · | · | · | · | · | · | · |
| 22 domain lint | off | · | · | · | · | · | · | on → repair | on → repair, trigger | · | on → repair, trigger |
| 23 reconciliation | off | · | · | · | · | · | · | on | · | · | on |
| 24 two routes | one | · | · | compiler + free SQL | · | · | · | · | · | · | compiler + free SQL |
| 25 restate | off | · | · | · | · | on | · | · | · | on (in plan) | on |
| 26 trigger | n/a | · | · | · | · | · | · | · | lint after repair, failed repair, low self-confidence | · | all four, incl. candidate/route disagreement |
| parameters on | 0 | 6 | 4 | 6 | 6 | 9 | 4 | 7 | 6 | 6 | 24 |

Contradicting pairs, designed so the test can falsify a prior:

| Pair | Prior A | Prior B |
|---|---|---|
| B1 vs B2 | Haiku fails because it lacks domain knowledge | Haiku has enough knowledge; it fails by slips that search and checking catch |
| B3 vs B4 | Constrain what the model may emit | Let the model look at the data and write whatever it needs |
| B5 vs B6 | Think and structure before SQL | Smallest context, goal not path; structure dilutes Haiku |
| B8 vs B9 | Spend Sonnet only after a detectable failure | Spend Sonnet up front, once, on interpretation |
| B10 vs B6 | More techniques add up | More techniques interfere; lean is as good |

### 1.2 Cost model

Multipliers are per Haiku session, relative to B0 (= 1.0 unit). Assumptions: the repo's price constants
(`analyst/variants/multi_candidate/cost.py`: Haiku $0.10/$0.50 per M tokens, Haiku output price marked assumed;
Sonnet $2/$10), so a Sonnet call costs about R = 20× the same Haiku call; cache reads 0.1×; a B0 session is about
5 turns, ~6.5k cached prefix, ~15k uncached transcript, ~1.5k output, roughly $0.003–0.005. A full Sonnet session is
about 20 units before thinking tokens. A short Sonnet selector or planner call is 2.5–6 units. Every number in this
section is a planning estimate, not a measurement; the runner must log real usage per session.

### 1.3 Bundle by bundle

**B0 — Base.** The paired denominator. Basic glossary, goal briefing, free SQL over `sem.*`, one Haiku trajectory.
It is the H1 `base` config with repair turned fully off. Ours/H1: Haiku 74.1% (pass^2 70.4%), Sonnet 83.3% on the
27-question set; VAL is meant to be harder (Sonnet 60–80%). Cost 1.0. Build: the runner (item 1).

**B1 — Knowledge only.**
*Theory:* Haiku's errors are semantic (wrong population, grain, date basis, payer literal). Give it everything it
does not know, as text and lookups, and change nothing about how it answers. All of group A is on; groups B–E stay
at base, including no repair.
*On:* #1 + invariants, #2 TRAIN playbook, #3 + derivations, #4 enums, #5 lookup, #6 k=3 BM25 from TRAIN.
*For:* theirs, Rumiantsau & Fokeev 2026: a 4 KB conventions document +17 to +23 pts, Opus 4.7 / Sonnet 4.6 /
GPT-5.4, Contoso 100 q, single shot. Theirs, BIRD evidence and DAIL-SQL few-shot +3 to +10 (GPT-4 era). Ours/H1:
screen-1 winner (pitfalls + dictionary + profile + procedural) +11.1 Haiku [−3.7, +27.8], probably carried by
dictionary/profile. Ours/S1: glossary pitfalls +15.6 Haiku.
*Against:* ours/H1: pitfalls −1.9 Haiku on new questions (overfit), Haiku pass^2 fell. Ours/S1: dictionary+profile
+2.4 n.s., few-shot −0.3, lookup inside "semantic layer" ≈ 0. Theirs (Anthropic context engineering): context rot as
the prefix grows. Theirs, ACE: realistic gain from a learned playbook on BIRD is +5 (DeepSeek-V3.1, 1,000 train
examples), not +16.
*Predicted vs B0:* +7 pp (range +2 to +12). Lower wrong-but-confident than B0 by a similar margin.
*Cost:* 1.3 (1.2–1.5): bigger cached prefix, three uncached examples, one lookup call.
*Build:* invariants text split from v001 (item 2), dictionary and profile levels (item 2), BM25 retriever (item 3),
playbook (item 10). B1 is the last single-theory bundle to become runnable because the playbook needs a human review.

**B2 — Compute only.**
*Theory:* `sem.*` plus the basic glossary already tell Haiku enough; its errors are slips that differ between
attempts. Diverse attempts, checking and repair catch them. No added knowledge of any kind (no lint: that encodes
domain traps).
*On:* #12 low then high on a failure signal, #13 errors + hints + result signals max 4, #14 generic rules + a Haiku
verifier call, #15 three prompt-shape candidates (direct, query-plan, divide-and-conquer from A04) with a Sonnet
pairwise selector called only when executed results disagree.
*For:* theirs, CHASE-SQL: selector +4.2 over execution majority (Gemini, BIRD); query fixer −3.78 when removed.
Theirs, Beskorovainyi 2026: repair of non-executing SQL +1.2 to +4.4 (Qwen, Llama, CodeLlama, BIRD dev) at 1.1–1.5×
tokens. Theirs, DS-STAR verifier/router +5.3 (DABStep hard). Ours/grid: repair +2.1, candidates3 +1.5 (Grok, weak).
*Against:* theirs, Beskorovainyi: 5-sample self-consistency −0.07 / +0.13 at 5× (Qwen2.5-Coder-32B). No temperature
on the 2026 API, so diversity rests on three prompts to one model, which may be correlated. Ours/S1: hints + checks
−0.3 Haiku; effort low→high −2.4 Haiku. Ours/grid: verifier −0.8.
*Predicted:* +3 pp (0 to +6); the oracle upper bound (any candidate right) will be the most informative number.
*Cost:* 5 (4–6): three trajectories at 1.2–1.4 each, three short Haiku verifier calls, a Sonnet selector on the
30–40% of questions whose candidates disagree.
*Build:* candidates + selector + verifier (item 5), low→high effort retry (item 2).

**B3 — Typed route.**
*Theory:* Haiku's worst errors are plausible wrong numbers from free SQL. A compiler that owns population
predicates and joins turns them into errors; a second free-SQL route checks the compiler where it can express the
question and covers it where it cannot.
*On:* #3 + derivations (the spec vocabulary needs them), #5 lookup mandatory before a filter literal, #8 A03
compiler first with SQL only after `cannot_express`, #13 errors + hints max 2 (compile errors are actionable), #19
actionable errors and rewritten descriptions, #24 compiler route + free-SQL route: agree → answer; disagree → the
compiler's answer, logged as low confidence (no abstention until #16 is scored).
*For:* theirs, dbt 2026: Sonnet 4.6 90.0% text-to-SQL vs 98.2% through the semantic layer, GPT-5.3 Codex 84.1% vs
100% (11 questions × 20 repeats, no intervals); text-to-SQL fails as a wrong number, the compiler as an error.
Ours/grid: metric_spec +1.0, +2.2 on clean cells (Grok, weak). Ours/S1: typed metrics + lookup +3.1 Haiku n.s.
Theirs, CHASE: value retrieval −2.9 when removed.
*Against:* ours/S1: −1.0 Sonnet. Theirs, dbt: 0% on questions the layer cannot express (coverage cliff). Theirs,
OpenAI data agent: overlapping tools confused the agent.
*Predicted:* +4 pp (−3 to +9), with the clearest fall in wrong-but-confident of any bundle. Report compiler
coverage on VAL beside accuracy.
*Cost:* 1.8 (1.5–2.2): the compiler route is shorter than a base session; the free-SQL route is a full one.
*Build:* compiler tool, lookup gate, route orchestration (item 7).

**B4 — Explore freely.**
*Theory:* Haiku fails because it guesses what the data looks like. Let it see the data: question-conditioned
probes before SQL, a full profile, value lookup, more rows per result. No compiler, no structure.
*On:* #3 + descriptions, #4 full profile, #5 lookup, #9 A06 probes, #13 errors max 2, #20 50 rows shown.
*For:* theirs, ReFoRCE: about −2 without column exploration (Spider 2.0). Theirs: profiling-only system #1 on BIRD
(2025). Ours/grid: explore +0.6, and +21.2 on the ceiling C-series (noise).
*Against:* ours/grid: explore × assumptions −6.4 (−9.2 clean): exploring may replace thinking about the question.
Ours/S1: dictionary + profile +2.4 n.s. Theirs, Anthropic: context rot; 50 rows per result inflates the transcript.
*Predicted:* +1 pp (−4 to +5).
*Cost:* 1.5 (1.3–1.8). Probes are code; their notes and the larger results are tokens.
*Build:* probes (item 6), full-profile level (item 2).

**B5 — Deliberate workflow (Haiku thinks first).**
*Theory:* errors are interpretation errors. Make Haiku restate the question, lock the readings of grain, date basis
and population, write a plan, then build the SQL in checked CTE steps, with worked examples to show the shape.
*On:* #3 + descriptions, #6 k=3 BM25 (tests the grid's few-shot × assumptions lead), #7 procedural, #8 A07 decompose,
#10 plan + replan on failure, #11 A09 assumptions, #12 medium, #13 errors + hints max 2, #25 restate.
*For:* theirs, CHASE plan +6 and MAC-SQL decomposer +3.9 (as cited in the parameter file). Ours/grid: assumptions
+0.8; few-shot × assumptions +9.2 [+1.8, +16.6] (one of ~30 interactions screened). Ours/S1: procedural +4.5 Haiku.
*Against:* theirs, OpenAI data agent: prescriptive prompting degraded results. Ours/H1: procedural on top of pitfalls
+1.9 Haiku, 0.0 Sonnet. Ours/grid: decompose −0.8; decompose × few-shot −7.0. Four overlapping "think first"
mechanisms in one bundle.
*Predicted:* +2 pp (−5 to +6).
*Cost:* 2.2 (1.8–3): 3–5 decompose steps, plan and replan turns, medium effort output.
*Build:* interpretation layer (item 8), BM25 retriever (item 3).

**B6 — Lean.**
*Theory:* every added token and tool dilutes Haiku's attention ("smallest high-signal context"; "guide the goal,
not the path"). A minimal briefing, a well-designed tool surface, tight budgets and one cheap repair are as accurate
as anything bigger and much cheaper.
*On:* #7 minimal, #13 errors max 1, #19 actionable errors + digest results + rewritten descriptions, #20 4 turns.
*For:* theirs, Anthropic context-engineering and tool-design guidance; theirs, OpenAI "less is more" (both
qualitative). Ours/S1: effort, examples, checks, dictionary all ≈ 0 on Haiku. Ours/grid: between-cell spread small
relative to question difficulty.
*Against:* ours/S1 and H1: knowledge text and procedure helped Haiku at least on some sets; B6 has none of it.
Ours/S1: procedural +4.5 Haiku (B6 goes the other way).
*Predicted:* 0 pp (−4 to +3) at the lowest cost. It wins on cost per correct answer if accuracy holds.
*Cost:* 0.7 (0.6–0.8).
*Build:* minimal style, digest results, descriptions (item 2). Runnable second, after B0.

**B7 — Traps in code.**
*Theory:* Haiku's residual errors are a small, known family (latest submission landing on the secondary payer,
missing current adjudication, fan-out over submission rows, claim vs line grain, wrong date column). State the
invariants once in prose, then detect violations in code with certainty and force a repair. Code does not get
ignored the way prose does.
*On:* #1 + invariants, #3 + descriptions, #13 errors + hints + result signals max 2, #14 generic rules, #19
actionable errors, #22 domain lint feeding repair, #23 reconciliation (835 balancing BPR02 = ΣCLP04 − ΣPLB; rate
numerator ≤ denominator; group shares sum to the total).
*For:* ours/S1: the residual-failure analysis names exactly these traps. Theirs: OpenAI zero-row check; ReFoRCE
"empty is not an answer"; DS-STAR +5.3. Ours/grid: repair +2.1, repair × assumptions +4.8.
*Against:* ours/S1: hints + result checks −0.3 Haiku (aliased, easy set, most SQL ran). A lint catches only the
patterns it lists; false positives push a correct query into a wrong repair. Theirs, Beskorovainyi: repair gains
come from non-executing SQL, not second opinions on SQL that ran.
*Predicted:* +5 pp (+1 to +9), with the largest fall in wrong-but-confident among the single-model bundles.
*Cost:* 1.3 (1.2–1.5): checks are free; repairs land on the 20–40% of questions that trip a rule.
*Build:* invariants text (item 2), lint + reconciliation (item 4). Runnable third.

**B8 — Cascade.**
*Theory:* most questions are easy for Haiku; failures are detectable; route only those to Sonnet and get close to
Sonnet accuracy near Haiku cost.
*On:* #3 + descriptions, #13 errors + hints max 2, #14 rules, #17 Haiku then Sonnet, #22 lint (as repair input and
trigger), #26 trigger = lint still firing after repair, repair budget spent, or self-rated confidence low (a field in
`final_answer`).
*For:* theirs, EllieSQL: about −40% tokens at equal accuracy with routing (BIRD; cited in the parameter file, not
re-checked). Ours/H1: Sonnet base is 9 pts above Haiku base (83.3 vs 74.1), so there is a gap to recover.
*Against:* the gain is capped at (Sonnet − Haiku gap) × trigger recall, and wrong-but-confident answers trip no
trigger. At R = 20, an escalation rate of 25% already costs 5× base. Untested by us.
*Predicted:* +6 pp (+2 to +10).
*Cost:* 1.3 + 20·e; for e = 0.15–0.30, 4–7 (say 5). Report e.
*Build:* lint (item 4), trigger + confidence field (item 9). The cascade itself exists (`analyzer/loop.py`).

**B9 — Sonnet planner, Haiku executor.**
*Theory:* the hard part is reading the question; SQL is easy once the reading is right. Spend Sonnet once, up front,
on a structured plan (restatement, locked assumptions, steps); Haiku executes it with the tools.
*On:* #3 + descriptions, #10 structured plan written by Sonnet, #11 and #25 inside that plan, #13 errors max 2,
#18 Sonnet planner → Haiku executor.
*For:* theirs, MAC-SQL / CHESS role splits; theirs, CHASE plan +6. Ours/H1: Sonnet gains most where interpretation
matters (pitfalls +7.4 Sonnet vs −1.9 Haiku).
*Against:* theirs, Anthropic: multi-agent ≈15× tokens, failures at the handoff. Ours/grid: multiagent −0.8 (−1.8
clean). sota_refresh_2 recommends against building it. A planner that cannot run SQL plans blind.
*Predicted:* +4 pp (−2 to +9).
*Cost:* 6 (5–8): one Sonnet planner call (~4–6 units with thinking) plus a Haiku executor session.
*Build:* interpretation schema (item 8), planner handoff (item 11).

**B10 — Everything on (anchor).**
*Theory:* the effects are additive. Every parameter at its strongest level except the two that cannot be scored yet
(#16, #21). It bounds what the space can reach and shows whether interference is real.
*On:* 24 parameters as in the table, including + pitfalls (the screen-1 text, a known overfit risk, present only
here), compiler with decompose fallback, high effort, both Sonnet uses. Composition order: Sonnet plan → three
candidates (each: probe, compiler or decompose, lint, reconcile, repair, verifier) → two routes inside each
candidate → disagreement goes to the Sonnet selector → selector decline or lint after repair escalates to Sonnet.
*For:* each component's own evidence above.
*Against:* ours/grid: explore × assumptions −6.4, decompose × few-shot −7.0, candidates3 × verifier −6.4; ours/S1:
the all-on config (94.4% Haiku) was below the best config, which had three factors on (97.2%), a one-question gap on 36. Cost and latency.
*Predicted:* +8 pp (−2 to +14); worst cost per correct answer.
*Cost:* ~25 (15–35).
*Build:* everything, plus an integration pass (item 12).

## 2. Coverage

| # | Parameter | On in | # | Parameter | On in |
|---|---|---|---|---|---|
| 1 | glossary | B1, B7 (inv); B10 (pit) | 14 | checks | B2, B7, B8, B10 |
| 2 | playbook | B1, B10 | 15 | candidates | B2, B10 |
| 3 | dictionary | B1, B3, B4, B5, B7, B8, B9, B10 | 16 | abstention | none: waits (below) |
| 4 | profile | B1, B4, B10 | 17 | tier | B8, B10 (Sonnet-only in the Sonnet stage) |
| 5 | lookup | B1, B4 (on); B3, B10 (mandatory) | 18 | multi-agent | B9, B10 |
| 6 | few-shot | B1, B5, B10 | 19 | tool design | B3, B6, B7, B10 |
| 7 | style | B5, B10 (procedural); B6 (minimal) | 20 | budgets | B4, B10 (50 rows); B6 (4 turns) |
| 8 | generation | B3, B10 (compiler); B5 (decompose) | 21 | clarification | none: waits (below) |
| 9 | explore | B4, B10 | 22 | domain lint | B7, B8, B10 |
| 10 | plan | B5, B9, B10 | 23 | reconciliation | B7, B10 |
| 11 | assumptions | B5, B9, B10 | 24 | two routes | B3, B10 |
| 12 | effort | B2, B5, B10 | 25 | restate | B5, B9, B10 |
| 13 | repair | B2–B10 | 26 | trigger | B8, B10 |

Waiting parameters, with the reason: **#16 abstention** and **#21 clarification**. VAL has no unanswerable or
ambiguous questions and there is no user simulator, so on VAL abstention can only lose accuracy and asking cannot
be scored. Both enter at the end of the descent, on a new split. Levels not used by any bundle, each picked up in the
descent if the winner makes them relevant: #1 none (reason in §0), #6 embedding selector (no embedding model in the
stack; BM25/TF-IDF stand in), #12 low→high inside a non-B2 winner, #15 three-vote and agree-or-abstain (the latter
waits with #16), #18 + verifier context, #20 4 turns × 50 rows.

## 3. After stage 1: picking the winner, leave-one-out, descent

### 3.1 Picking the winner (pre-registered)

Primary: mean accuracy over two repeats (execution match against the verified key). Bundles whose paired
question-level bootstrap interval against the leader includes 0 are tied with it. Among tied bundles, take the
lowest cost per correct answer, unless its wrong-but-confident rate is more than 3 pp above the leader's. Report for
every bundle: accuracy, pass^2, wrong-but-confident (answered and wrong), no-answer rate, cost per session, cost per
correct answer, and for B2/B10 the any-candidate-right upper bound, for B3 compiler coverage, for B8 the escalation
rate.

Then the Sonnet stage: the top three Haiku bundles and B0 on `claude-sonnet-5-5`. If Sonnet B0 beats the Haiku
winner on cost per correct answer, say so; it changes what production should run.

### 3.2 Leave-one-out on the winner W

Each arm is W with one parameter set to its B0 level, 120 questions × 2 repeats, paired against W.

Joint arms, because overlapping parameters can each look useless alone while the pair matters:
- interpretation trio {#10, #11, #25} removed together, if two or more are on;
- check-and-fix chain {#14, #22, #23} removed together (keeping #13), if two or more are on;
- diversity {#15, #24} removed together, if both are on.

Coupled rules: removing #17 also removes #26. Removing #26 with #17 on returns the trigger to the current analyzer
rule. Removing #8 while #24 is on makes the second route a decompose route. If W is B10 (24 parameters), run the
five group arms (A–E) first, then single arms only inside groups whose removal moved accuracy by 3 pp or more, or
cut cost by 30% or more.

Decision per parameter (honest about noise: a single-parameter Haiku contrast at n = 120 has a 95% interval of about
±7 pp, from the H1 paired SD of 37–42 pp in `screen2_power.md`):
- removal costs ≥ 3 pp at point estimate → keep;
- removal costs ≤ 1 pp, or raises accuracy → drop;
- in between → keep if the parameter adds ≤ 10% cost, drop otherwise.

All drops are applied at once, and the pruned W′ gets one confirmation run against W. W′ replaces W unless it is
3 pp or more worse at point estimate.

### 3.3 Coordinate descent on unused parameters

Add one unused parameter (at one level; two levels where the parameter file gives two strong candidates) to W′,
paired against W′. Accept if accuracy rises ≥ 4 pp at point estimate and cost per correct answer rises ≤ 25%, or if
wrong-but-confident falls ≥ 3 pp with no accuracy loss. Accepted additions become the new base for the next step.
With ±7 pp noise, a null parameter clears +4 pp about 14% of the time; the sealed test is the protection.

Order: cheapest and best-evidenced first, multipliers last, so expensive parameters are judged against the
strongest cheap base. Skip any already in W′.

1. #13 repair (errors + hints, max 2)
2. #19 tool design
3. #22 domain lint
4. #14 rules
5. #3 dictionary level, then #4 profile level
6. #1 + invariants (then + pitfalls as a negative control, one repeat)
7. #2 playbook
8. #5 lookup
9. #23 reconciliation
10. #11 assumptions, then #6 few-shot right after it (the grid interaction lead)
11. #25 restate, then #10 plan
12. #8 generation mode (compiler; decompose)
13. #9 explore
14. #7 style
15. #12 effort (low→high on failure before plain high)
16. #24 two routes
17. #15 candidates (3 + selector)
18. #17 + #26 cascade with trigger
19. #18 Sonnet planner
20. #20 budgets, tuned last against the final cost
21. #16 abstention, then #21 clarification, on the new split (40 unanswerable + 40 ambiguous + 40 VAL answerable;
    #21 needs a user simulator and per-interpretation gold)

## 4. Sessions per stage

A session is one question answered once by one configuration (it may contain several model calls).

| Stage | Arithmetic | Sessions | Notes |
|---|---|---|---|
| Prep: base on TRAIN for the playbook | ~49 q × 2 repeats × Haiku and Sonnet | ~200 | TRAIN only; failures may be read |
| Prep: smoke runs of each new bundle | 10 bundles × 10 TRAIN q × 1 | 100 | catches build bugs before VAL is touched |
| Stage 1: bundles on Haiku | 11 × 120 × 2 | 2,640 | B8/B9/B10 include Sonnet calls |
| Stage 1b: Sonnet check | (top 3 + B0) × 120 × 2 | 960 | all-Sonnet sessions, ~20 units each |
| Stage 2: LOO | ~9 arms (7 singles + 2 joint) × 240, + 240 confirm | ~2,400 | ~3,400 if B10 wins |
| Stage 3: descent | ~17 unused × ~1.3 levels × 240 | ~5,300 | fewer if a big bundle wins |
| Stage 3b: #16, #21 | ~6 arms × 120 × 2 on the new split | ~1,440 | needs the new split and a simulator |
| Stage 4: sealed test | (final + B0) × 40 × 2, Haiku; same on Sonnet if the final uses Sonnet | 160–320 | run once |
| Total | | ~13,000–14,000 | |

At the assumed prices, stage 1 averages ~4.6 units per session (mean of the multipliers), about 12,000 B0-units,
tens of dollars. Money is not the constraint; question authoring, rate limits and wall time are.

## 5. Build list

Ordered so the most bundles become runnable soonest. S < 100 lines, M 100–300, L > 300. A gate that is not code:
VAL needs ~120 hard verified questions; `bench/question_splits.json` currently holds 25 validation questions (29 with
family templates). Nothing in stage 1 should start before that set exists and is sealed.

| # | Item | Size | Reuses | Unlocks |
|---|---|---|---|---|
| 1 | Bundle runner: a 26-parameter schema mapped onto `AnalyzerConfig` with validation; in-process runs over a split with `AnthropicLLM`, prompt caching on; session dirs that the existing scorer reads; per-session usage → cost; metrics: accuracy, pass^2, wrong-but-confident, no-answer, cost per correct | M | `analyzer/loop.py`, `config.py`, `harness.py`, `llm.py`; `bench/eval_subagents.py` (score, contrast, bootstrap); `bench/compare.py`; `bench/splits.py`; `analyst/variants/multi_candidate/cost.py` | B0; TRAIN runs for the playbook |
| 2 | Small levels: #7 minimal briefing; #19 digest results + rewritten descriptions; #20 rows shown (exists); #12 low→high retry on a failure signal (reuse the cascade path with one model); #1 + invariants text split out of `v001_screen1_pitfalls` as a new version; #3 three levels (derivations generated from `claude/schema/02_semantic_layer_views.sql`); #4 enums vs full | M | `analyzer/prompts.py`, `tools.py`; `bench/glossary_versions.py`; A01 `dictionary.py` | B6 |
| 3 | BM25/TF-IDF retriever with the leakage guard, replacing the Jaccard ranking in `lookup_examples`; pool = TRAIN | S | `analyst/approaches/a10_dynamic_fewshot` (`retriever.py`, `leakage.py`) | part of B1, B5 |
| 4 | #22 domain lint split out of `_result_warnings` (generic rules stay #14), new rules: wrong date column for the measure, joins that multiply the declared grain; hits feed repair. #23 reconciliation with three identity templates | M + M | `bench/agent_cli._result_warnings`, `analyzer/checks.py`, A02 `checks.py`, A07 `checks.py` | B7 |
| 5 | #15 three prompt-shape candidates + Sonnet pairwise selector (replaces the two-variant `user_message`), group by executed rows; #14 Haiku verifier call | M | `analyst/approaches/a04_multi_candidate` (`prompts.py`, `selector.py`), `analyzer/loop._select`, A05 `checklist.py` | B2 |
| 6 | #9 probes as a pre-SQL step with compressed notes | S–M | `analyst/approaches/a06_explore_first` (`probes.py`, `catalog.py`) | B4 |
| 7 | #8 compiler as a tool with the `cannot_express` gate; #5 mandatory-lookup gate on filter literals; #24 route orchestration and agreement check | L | `analyst/approaches/a03_metric_spec` (compiler, catalog, coverage), `analyst/variants/typed_metric_compiler`, `analyzer/tools.lookup_values` | B3 |
| 8 | Interpretation layer: #25 restate record, #11 assumptions lock, #10 plan + replan, #8 decompose steps; one JSON schema shared with item 11 | L | A09 `checklist.py`, A05 plan step, A07 `protocol.py` | B5 |
| 9 | #26 triggers: lint after repair, repair spent, `confidence` field in `final_answer`, candidate/route disagreement | S | `analyzer/loop._should_cascade` | B8 |
| 10 | #2 playbook: base on TRAIN (both models, 2 repeats), `glossary_learner` with an LLM proposer, human review against the rules in sota_refresh_2 §5, cap 40 lines, freeze and hash before VAL opens | M + human review | `bench/glossary_learner.py`, `bench/glossary_versions.py` | B1 (with 2, 3) |
| 11 | #18 Sonnet planner → Haiku executor handoff using item 8's schema | S–M | item 8; A05 `protocol.py` | B9 |
| 12 | Anchor integration: composition order (§1.3 B10), conflict tests (compiler + decompose fallback, candidates × routes × selector × cascade), budget per sub-step | M | everything above | B10 |

Start item 10's TRAIN runs as soon as item 1 works; the human review is the longest wait and should overlap
items 4–9. Runnable order: B0 → B6 → B7 → B2 → B8 → B4 → B3 → B5 → B9 → B1 → B10 (B8 can move ahead of B2 if
item 9 is done first; it depends on item 4, not item 5).

## 6. Self-critique

**Weakest bundles.** B4: thin evidence, it overlaps with the dictionary/profile it also turns on, and the grid's
only interaction with exploration was negative. B5: four overlapping "think first" mechanisms plus a procedural
prompt that OpenAI's experience and H1 both argue against; if it loses, the loss is unattributable without LOO, and
it will not be the winner that gets LOO. B2: without temperature, three prompts to the same Haiku may produce the
same mistakes; the any-candidate-right bound decides whether it was ever a fair test. B10: interpretable only as a
ceiling, and the most likely to be mis-built.

**Confounds to watch.**
- Noise. Haiku bundle-vs-bundle contrasts at n = 120 carry about ±7 pp intervals. Several bundles will be
  statistically tied; picking among them by point estimate invites a winner's curse. The tie rule in §3.1 helps;
  it does not remove it.
- VAL turns into a selection set. Stage 1, LOO and descent together run ~45 arms on the same 120 questions. Only
  the sealed test is clean, and 40 test questions confirm only large effects.
- Sonnet inside B8, B9, B10. A gain there may be "used Sonnet", not the architecture. Compare them with Sonnet B0
  from stage 1b, on cost per correct answer.
- Build maturity. New code (lint, planner handoff, compiler gate) may lose because it is a first draft. The smoke
  runs catch crashes, not weak prompts.
- Bundles are not contrasts. B1 beating B2 says "this knowledge set beat this compute set", not that knowledge
  beats compute in general.
- Knowledge provenance. The VAL authors saw only the basic glossary; the pitfalls text was written against screen-1
  questions; the playbook is distilled from B0's failures, so it suits B0-like loops more than B3 or B5.
- Cost estimates rest on an assumed Haiku output price and no measured thinking tokens. The runner's measured usage
  replaces §1.2 as soon as it exists.
- Time drift and rate limits: interleave bundles across questions and time, not bundle by bundle.

**Results that would surprise me.** B1 at or below B0: dictionary plus invariants is the strongest prior we have
(ours/H1 +11 Haiku, theirs +17 to +23). B2 above B1: compute beating knowledge on Haiku would contradict both our
screen and the external evidence. B9 below B5: a stronger planner not helping would say the errors are not
interpretation errors. B6 within 2 pp of the winner: that would mean most of this parameter space is noise on this
question set, and the next investment belongs in harder questions, not techniques.
