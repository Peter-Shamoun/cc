# A01 single-shot baseline

One SQL statement, written from the question, the basic glossary, and a generated data dictionary, then executed once. That execution is the answer. There is no repair and no exploration.

This is the floor for the agent-analyzer solution map. Other approaches are worth adopting only when they beat this arm on a held-out question set by the pre-registered rule in `research/sota_refresh_2.md` (paired question-level bootstrap interval entirely above 0, per model). The code is `analyst/approaches/a01_single_shot/`. It does not change the generator, the parser, the lake, or `bench/`.

## Design

The model sees a fixed prefix and the question. It must call exactly one tool: `submit_sql` with one DuckDB `SELECT`, or `refuse`. The protocol then stops.

- `submit_sql` is checked with `bench/guard.py` (one read-only statement, `sem.*` only unless the question is marked escalate, no file or secret functions) and executed once on the session. The rows of that execution are the answer text. The model does not see them.
- `refuse` executes nothing. It is for PHI, another customer, or a write.
- Any other completion (no tool call, two tool calls, an unknown tool, a missing statement) executes nothing and is not sent back.
- A guard rejection or a database error is terminal. The statement is kept. The model is not shown the error and is not called again.

The scored artifact is that same statement. `bench/eval_subagents.py` re-executes it in a fresh sandbox, which is the harness, not a second attempt by the model. A query that reads no table is still rejected as ungrounded, the same rule as every other arm.

Execution uses the secure session. In production that session is `ccsim.query.open_session`: the customer's reader role, `sem.*` unless the session is escalated, `allowed_directories` limited to those prefixes, external access off, configuration locked. The bench freezes that session and reopens it with `bench.dataset.open_snapshot`, which sets the same restrictions on local Parquet. A01 opens one session per question and does not open another to explore.

### What is in the prefix

The system prompt is stable across questions, so it is the cache breakpoint. The user turn is only the question.

1. **Goal, not a procedure.** One short briefing: call `submit_sql` or `refuse`. No numbered working steps. Screen 1's procedural gain did not hold up once the pitfalls text was in the prompt (held-out: pitfalls + procedural was +1.9 for Haiku with an interval of about ±16, and 0.0 for Sonnet), and OpenAI's data-agent note measured prescriptive prompting as a regression.
2. **Basic glossary.** `bench.agents.GLOSSARY`, including the data-start population fact added after held-out 1. Not `bench/agent_cli.py`'s pitfalls addendum. That addendum was +15.6 points for Haiku on screen 1 and −1.9 on the held-out set. It is a trained artifact and does not belong in the baseline.
3. **Data dictionary, generated.** For every `sem.*` relation in the session: column name and type from `DESCRIBE`, grain, what the view is for, and the metric compiler's default filter, stated as not applied to a `SELECT`. A column that is a dimension or a time dimension gets the description from `claude/schema/03_semantic_model.yaml`. Sample values are not read. The view SQL is not copied; the dictionary only names `claude/schema/02_semantic_layer_views.sql`.

The column list is in the prefix because A01 has no `context` tool. In the existing CLI, `context` always prints columns, and the `data_dictionary` flag adds the metric catalog from `describe_semantic_layer()`. A01 does not add that catalog. The catalog is the typed-metrics surface (screen factor A), and this arm does not call the compiler. A single `SELECT` needs the columns. The eval config still sets `data_dictionary: true` so the arm is identifiable as the dictionary arm; the text the model actually sees is the column dictionary above.

Not in the prefix: pitfalls, the enum profile, few-shot examples, payer-name lists, lossless tables.

### Tools

| Tool | What it does | What it returns to the model |
|---|---|---|
| `submit_sql` `{sql}` | One guarded `SELECT` | Nothing. The protocol does not continue. |
| `refuse` `{reason}` | Record a refusal | Nothing. No query runs. |

There is no `run_sql`, `context`, `lookup`, `metrics`, `examples`, or `ask`.

## Adapter

`bench/eval_subagents.py` prepares a run from a technique-config dict and scores `final.json` by re-executing SQL. It does not know about approaches. The adapter fits that shape without editing it.

`technique_config()` is a legal config (unknown keys are rejected by `create_session`):

| Setting | A01 |
|---|---|
| glossary | `basic` |
| data_dictionary | true |
| data_profile, metrics, lookup, examples, ask, error hints, result checks | off |
| context_style | `goal` |
| effort | `low` |
| max_tool_calls | 1 |

`max_tool_calls: 1` is only a backstop. A CLI agent with that config can still call `context`, look at rows, and submit a different statement. That run is not A01. `prepare_run` stamps the manifest so the stored prompt says not to run `bench/agent_cli.py` and names the driver instead.

```text
python -m pytest analyst/approaches/a01_single_shot/test_a01_single_shot.py -q

# after a snapshot exists; this does not build one and does not call a model
python -c "from analyst.approaches.a01_single_shot import prepare_run; \
print(prepare_run('a01', ['haiku','sonnet'], repeats=2, snapshot_path='PATH'))"
# then, per manifest entry, drive_session(session_dir, backend)
python -m bench.eval_subagents score a01
```

`drive_session` refuses a session whose merged technique config is not the table above, and refuses a session that already has a result, so a caller cannot repair by invoking it twice. The scripted backend used in tests replays queued completions and raises if the protocol asks for another one. No live client is constructed.

`a01_trace.json` in the session records model calls and executions. `score` ignores it. `calls.jsonl` logs the one `sql` command (or `refuse`, or a failed `protocol` call) so the scorer's tool-call count is 1 on a submit and 0 on a refusal or a protocol failure.

## Expected strengths

- **It is the comparison arm.** Accuracy, pass^2, tool calls, and no-answer rate for every later approach are differences from this one. Screen 1's base (Haiku 72.2%, Sonnet 88.9% on 36 questions) and held-out 1's base (Haiku 74.1%, Sonnet 83.3% on 27) are the nearest measured neighbors, and they are a different protocol: an agent loop with the basic glossary and no dictionary. They are not A01's score.
- **The submitted statement is the executed statement.** There is no path where an exploratory query and the answer query disagree.
- **The prefix is cacheable.** Glossary and dictionary do not depend on the question. The catalog's cache note (reads at 0.1× input, 512-token minimum) applies directly: the prefix is far above that minimum.
- **Failures are the first statement.** A wrong grain, a wrong literal, or a syntax error is visible. Nothing in the trace is a repaired query that dropped a filter.
- **Full `sem.*`, no schema linking.** Ten views fit. Maamari et al. (arXiv:2408.07702) found that filtering columns, once the schema fits, loses more from the missing column than it gains from a shorter prompt.
- **It does not carry the pitfalls text.** Held-out 1 is the reason. A baseline that includes that text would launder an overfit prompt into every contrast.
- **Domain definitions are still in the prefix.** BIRD's evidence note moved GPT-4 from 34.88% to 54.89% execution accuracy on the test set. Rumiantsau and Fokeev's paired single-shot benchmark (arXiv:2604.25149, 100 questions) moved three frontier models by +17 to +23 points with a 4 KB document, after which the models sat within a point of each other. The glossary is this repo's version of that document, already written, and the views are already the large semantic-layer intervention. A01 does not repeat it as a typed compiler.
- **Cost scales with questions, not with failures.** Repair's extra tokens happen only on errors in other designs. Here every question costs the same shape, which makes the arm a stable denominator.

## Expected failure modes

These are the reasons a later arm can win. They are not bugs to paper over inside A01.

- **SQL that does not execute is a wrong answer.** Beskorovainyi (arXiv:2606.29733) estimates +1.2 to +3.7 points from repairing drafts that failed to execute, at 1.1–1.5× tokens, and the gain is that repair, not a second opinion on SQL that already ran. CHASE-SQL's query fixer was worth 3.78 points on BIRD dev when removed from a 73.01 system (Pourreza et al., arXiv:2410.01943). A01 forgoes both. Screen 1's error-hint factor was about zero, on a set where most SQL already ran and Sonnet was near the ceiling, so that null is not a reason to put repair into the baseline.
- **A wrong literal is terminal.** No lookup and no value profile. CHASE's value-retrieval ablation was −2.92 points. Payer names and CPT codes the model spells from memory will be wrong silently when the statement still executes.
- **A wrong population that still executes is the main miss.** Screen 1's residual errors were the latest-submission trap (`is_latest_submission` can be the secondary payer's row), fan-out joins over per-submission rows, and claims-versus-lines grain. The basic glossary names the flags. It does not walk the traps; that walk is the pitfalls text. A01 also cannot notice the mistake from a row count, because it never sees the rows. dbt's 2026 rerun is the same failure shape: text-to-SQL fails as a plausible wrong number (Sonnet 4.6 90.0% against modeled tables versus 98.2% through the compiler, on 11 questions × 20 repeats). A01 is the text-to-SQL side of that comparison, over `sem.*`, not over raw 837/835.
- **An empty or all-zero result is accepted.** ReFoRCE treats an empty result as a failure and refines. OpenAI's agent retries a zero-row result that looks like a bad filter. A01 submits it.
- **Escalation questions are expected misses.** The dictionary is `sem.*` only. The guard allows `lossless.*` when the question is marked escalate, but the model was not shown those columns and is not allowed to go look. Lossless retrieval is a later experiment, on questions that actually escalate.
- **A protocol miss is a no-answer, not a retry.** Two tool calls, prose instead of a tool call, or a missing `sql` string score as nothing submitted. An agent loop would get another turn. A01 counts that as wrong. The rate of these misses is part of the arm's no-answer count and should be reported beside accuracy.
- **Rates, ranking limits, and integer division are not coached.** Those rules live in the pitfalls text (`100.0 * x / y`, `ORDER BY` plus `LIMIT`). Questions that need them are expected misses until a playbook that survives validation adds a general rule.
- **Refusal can be right or over-refusal.** The glossary says patient names, member ids, birth dates, and addresses are not in the views. A model that refuses a normal billing question anyway is wrong on this arm, and there is no second turn in which it looks at the schema and recovers.
- **The config is not the protocol.** Scoring a CLI agent that was handed `technique_config()` measures a one-query loop, not A01. Traces without `a01_trace.json`, or with more than one model call, are not this arm.

## Tokens, calls, latency

Measured by `test_full_catalog_prefix_is_the_glossary_and_dictionary_only` with no model call. Token counts are `ceil(characters / 4)`, not a model tokenizer. The dictionary is built from the ten `sem.*` views' output columns (236 columns, typed `VARCHAR` in the test) plus the semantic-model grains and dimension descriptions.

| Piece | Tokens |
|---|---|
| Basic glossary | 471 |
| Data dictionary | 3,418 |
| System prompt (goal + glossary + dictionary) | **4,072** |
| User turn, the short q01 wording | 20 |
| Hand-written questions (33 in `bench/questions.yaml`, 47–203 characters) | about 15–55 for the user turn |
| Cold input, one question | about 4,090–4,130 |
| Warm uncached input | the user turn only; the 4,072-token prefix is a cache read |
| Output | **not measured.** Budget used for planning: 400 tokens for one `submit_sql` call. Thinking tokens at effort `low` are extra and were not collected. |

Live DuckDB type names (`DATE`, `DECIMAL`, `BOOLEAN`) differ from the test's `VARCHAR`. If every column were `DECIMAL(18,3)` instead, the dictionary would grow by about 350 tokens. Real types move the prefix by less than that. They do not change the shape.

| | Per question |
|---|---|
| Model calls | 1 |
| SQL executions when the model submits | 1, plus the scorer's re-execution of that same statement |
| SQL executions when the model refuses or breaks the protocol | 0 |
| Repair rounds | 0 |
| Exploratory queries (`context`, lookup, a probe `SELECT`) | 0 |
| Effort | low |

Latency was not measured. The shape is one model round trip at low effort, then one guarded query on the session that was already opened. The session open (`ccsim.query.open_session`: STS, `CURRENT`, manifest, view bind) is paid once per question by every arm that uses the secure path. A01 does not pay it again for a probe query, and it does not pay a second model round trip to read rows or to repair. Screen 1's low-versus-high effort effect was −2.4 points for Haiku and +1.7 for Sonnet, both intervals covering 0, which is why this arm does not buy high effort.

## Related work

A01 is the single-prompt arm those papers ablate against, moved onto this schema.

- **BIRD** (Li et al., arXiv:2305.03111). Single-turn text-to-SQL, scored by execution. The evidence sentence is the public analog of the glossary. Closest public task to "the model knows SQL and still picks the wrong population." Spider 2.0 is not: that schema does not fit, and this one does.
- **Rumiantsau and Fokeev, arXiv:2604.25149.** Paired single-shot, 100 questions, three frontier models. The document, not the model choice, moved accuracy. A01 is that setup: one shot, glossary in the prefix, no repair.
- **CHASE-SQL** (Pourreza et al., arXiv:2410.01943). A single prompt scored 63.01% on BIRD dev with Gemini 1.5 Pro. Self-consistency reached 68.84%, the full selector system 73.01%. The pieces A01 leaves out are the ones with the larger ablations (selector, fixer, value retrieval). The generators were partly substitutes for each other. This harness cannot get diversity from temperature (the API returns 400), so a later multi-candidate arm has to diversify the prompt, not the sample.
- **dbt Labs, 7 Apr 2026.** Text-to-SQL versus a metric compiler on a small question set. The compiler's advantage is failing closed instead of returning a plausible wrong number. A01 will produce plausible wrong numbers on governed metrics. That is the gap a metrics arm has to show, on this bench, with coverage reported beside accuracy. Screen 1's typed-metrics factor was +3.1 / −1.0 and is not that result: the agent could already query `sem.*`.
- **Beskorovainyi, arXiv:2606.29733, and ReFoRCE (Deng et al., arXiv:2502.00675).** The repair and empty-result literature. Cited above as the expected gap, not as something this arm implements. ReFoRCE's DDL compression does not transfer to ten views.
- **Maamari et al., arXiv:2408.07702.** Do not prune a schema that fits. A01 shows all ten views.
- **OpenAI's in-house data agent (29 Jan 2026) and Anthropic's "Building effective agents" (19 Dec 2024).** Less is more, guide the goal not the path, start with the simplest workflow that can answer. A01 is that workflow. The zero-row retry and the multi-candidate selector are later arms, each added only if they clear the interval rule against this one.
- **This repo's screen 1 and held-out 1.** Dictionary-plus-profile had no measurable main effect on the easy set (+2.4 Haiku, −1.0 Sonnet, both intervals covering 0) and was not isolated on the held-out set (the +11 Haiku point estimate for the screen-1 winner bundled it with the pitfalls text, and the interval included 0). A01 is how that dictionary gets a clean contrast: in the prefix, without pitfalls, without a loop.

## What this arm does not do

It does not build a dataset, call a paid model, or modify `bench/`, the generator, the parser, or the lake. It does not tune a glossary. It does not claim an accuracy number. A number for A01 has to come from `drive_session` on a frozen generator-3.3 snapshot, scored by `eval_subagents`, with the trace showing one model call.
