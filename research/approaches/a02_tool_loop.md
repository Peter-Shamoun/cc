# A02 — tool loop with repair

One model, five tools, and a repair budget the code enforces. The model explores `sem.*`, samples a few rows, drafts one SELECT, and may revise that SELECT at most twice. A revision is required when the draft errors, returns no rows, or trips a programmatic suspicion check. The model then submits that SQL, or refuses.

This is a workflow with a short agent inside it. The code decides the tool list, the phase order, the row cap, and the repair cap. The model decides which view to sample, what SQL to write, and when a request must be refused. That split is the one in Anthropic's "Building effective agents" (19 Dec 2024): add a workflow before adding a multi-agent system, and put the check in code when the check is crisp.

No live model was called to write this note. The numbers in the cost section are from `documented_example_costs()` and from the scripted fixture, not from a Haiku or Sonnet bill.

## Design

```text
describe_schema
    -> sample_rows          (0 to 3 calls, code builds the SELECT)
    -> propose_sql          (the draft; the protocol executes it)
         error | empty | suspicious
              -> propose_sql     repair 1 of 2
              -> propose_sql     repair 2 of 2
         ok, or the budget is spent
    -> answer               (the last proposed SQL, re-executed by the scorer)
refuse is legal at any step, including before describe_schema.
```

A clean result ends the loop. The model is not offered another rewrite once the checks pass. A result that is still bad after two repairs is submitted anyway. A02 does not abstain. The bench scores a wrong number as wrong, and a missing `final.json` as no answer; submitting the last SQL keeps the failure visible to `eval_subagents score`.

### What the code owns

| Rule | Where |
|---|---|
| Describe before any sample or draft | `protocol.ToolLoop` |
| At most 3 samples, 6 columns, 8 rows, and only before the first draft | `protocol.py` |
| Sample SQL is built from catalog identifiers, then passed through the guard | `build_sample_sql` |
| At most 2 repairs. The draft is not a repair | `MAX_REPAIRS` |
| Repair on execution error, zero rows, or a suspicion signal | `classify_execution` |
| Answer SQL must be the last proposal (whitespace and one trailing semicolon ignored) | `same_sql` |
| Refusal needs no query | `refuse` |
| 20 rows shown to the model; the scorer re-executes the submitted SQL | protocol + `bench` scorer |

Suspicion signals, in `checks.py`, are the same family as the warnings in `bench/agent_cli.py`:

- `sem.claims` or `sem.claim_lines` without `is_latest_submission`
- `sem.remit_claims` without `is_current_adjudication`
- a rate-like column outside 0..100
- a column that is NULL in every returned row
- `denied_claims` greater than `remitted_claims`, or `numerator` greater than `denominator`
- the first column repeating when the result has more than one column

Comments and string literals are stripped before the filter scan, so a comment that merely names `is_latest_submission` does not silence the check. Empty results still carry those filter signals in the tool text, so one repair can fix a missing predicate and a bad literal together. The status stays `empty`, which is what the repair counter keys on.

The system prompt is goal-shaped: grain, population, time basis, and the tool contracts. It includes the basic glossary from `bench/agents.py` and does not include the screen-1 pitfalls list. Held-out 1 did not support that list for Haiku. The invariants we are willing to enforce sit in `checks.py`.

### Tools

`tool_spec.py` defines `describe_schema`, `sample_rows`, `propose_sql`, `answer`, and `refuse`. One SQL tool, not a separate draft tool and repair tool. OpenAI's data-agent note (29 Jan 2026) is that overlapping tools confuse the agent. The protocol tells the model which phase a `propose_sql` call was.

A live client, when one is wired, has to send `tool_choice: auto`. Forced tool choice returns HTTP 400 on the models this bench evaluates (`research/technique_factors.yaml`). This package does not contain that client.

### How a session executes SQL

Two executors, one protocol.

- `ToolsExecutor` is the in-process path. `bench.agents.Tools.sql` calls `bench.guard.guard` before DuckDB. `A02ToolLoopAgent.answer` uses it, so `python -m bench.eval run --agent ...` can host the loop once a backend is passed in. The no-argument constructor does not call a model. It raises, unless `A02_SCRIPTED_STEPS` points at a local JSON script.
- `AgentCliExecutor` is the path `eval_subagents` scores. Every describe, sample, draft, repair, answer, and refusal is `python -m bench.agent_cli --session <dir> ...`. The CLI writes `calls.jsonl` and `final.json`. `drive_session` also writes `a02_trace.json` (phase, status, repairs) beside them. The scorer ignores that file.

`bench.agent_cli` opens the snapshot with the same sandbox as a reader session: `sem.*` unless the question escalates, `allowed_directories`, external access off, configuration locked (`bench/dataset.py` `open_snapshot`). That is the bench form of `ccsim/query.py` `open_session`, which is the production shape: per-request DuckDB, `sem.*` always, `lossless.*` only when the session is escalated, secrets and `allowed_directories` scoped to those prefixes, configuration locked. A02 does not open a warehouse connection of its own and does not add a tool that bypasses that session.

### Running it as an eval_subagents config

`eval_subagents prepare` accepts only the technique flags in `bench/agent_cli.py`. Repair-on-suspicion is not one of those flags. Screen 1's factor E turned on error hints and result warnings as text the model could ignore, and the main effect was about zero. A02 uses those flags so the CLI surface matches the loop, and the driver enforces the repair.

`analyst/approaches/a02_tool_loop/eval_subagents_config.json`:

| Flag | Value | Why |
|---|---|---|
| glossary | basic | Held-out 1. The pitfalls text is not in this arm. |
| context_style | goal | The phase order is in the tool results. |
| effort | low | Screen 1's high effort did not pay. |
| enable_sql | true | The loop writes SQL. |
| sql_error_hints, result_checks | true | The CLI annotates the same failures the protocol classifies. |
| metrics, lookup, examples, ask | false | Other arms. Lookup in particular is a different approach. |
| max_tool_calls | 8 | 3 samples + 1 draft + 2 repairs = 6 investigative `sql` calls. `context` and `answer` do not count. Two extra slots cover a call that fails and still counts. |

```bash
python3 -m pytest analyst/approaches/a02_tool_loop/test_scripted_backend.py -q

python3 -m bench.eval_subagents prepare RUN \
  --configs analyst/approaches/a02_tool_loop/eval_subagents_config.json \
  --models haiku,sonnet --repeats 1

python3 -m analyst.approaches.a02_tool_loop drive \
  .ccsim/agent_sessions/RUN/manifest.json --script steps.json

python3 -m bench.eval_subagents score RUN
```

`drive` requires `--script`. There is no default model, so the command cannot spend API quota. A real screen needs a backend that implements `complete(messages, tools) -> ModelTurn` and returns one tool call per turn. `drive_manifest` skips entries whose config name is not `a02_tool_loop`, so a mixed manifest can hold other arms.

`prepare` by itself does not run A02. The manifest prompt is the generic briefing from `bench/agent_session.py`. The repair policy runs only when `drive_manifest` (or `A02ToolLoopAgent`) executes the protocol.

## Expected strengths

- The cost follows failures. A question whose draft executes and looks clean is four model turns. A question whose draft errors is one or two more. Beskorovainyi's 2026 BIRD study (arXiv:2606.29733) found execution-guided repair the only consistent gain across the open models they tested, at about 1.1–1.5× tokens on average, because the extra tokens land on drafts that did not execute.
- The screen-1 residual traps (latest-submission, current adjudication, grain fan-out) are checks, not a paragraph. Held-out 1 is why that distinction matters: the pitfalls text was +16 points for Haiku on screen 1 and about zero, with a worse pass^2, on questions written afterward.
- Samples are real rows, and the model does not write the sample SQL. ReFoRCE's transferable piece is "look at the column before you filter it." The part that does not transfer, DDL compression, is left out. Ten views fit.
- Empty results are a failed answer, which is ReFoRCE's rule and OpenAI's zero-row retry, with a hard cap of two. The cap is the difference from a loop that repairs until the result repeats.
- One context, one model. MAC-SQL's refiner is a role, and the catalog notes it can live in the same transcript. A02 keeps it there. CHESS's four agents exist to cut a catalog we do not have.

The prior for the errors-only slice is about +1 to +4 points on open models (Beskorovainyi; CHASE-SQL's query fixer was −3.78 points when removed, from 73.01 to 69.23 on BIRD dev; MAC-SQL's refiner was −4.63 points when removed). Screen 1's null on factor E does not measure this arm. That factor was hints plus warnings as text, aliased with other two-factor pairs, on a set where most SQL already ran and Sonnet was near the ceiling. A02's extra triggers (empty, suspicious) have no measured effect on this bench yet. The scripted tests lock the policy. They do not lock an accuracy number.

## Failure modes

- A repair can delete the filter that made the result empty or suspicious and return a broader population that executes cleanly. The checks then pass. `test_empty_result_is_repaired_and_a_dropped_filter_is_accepted` locks that acceptance. The catalog's warning on T15 is this case. Log repaired-then-wrong separately when a screen runs; the trace file records each proposal.
- The filter checks false-alarm on SQL that is right without them. A count of submissions, a reversal history, or a question that deliberately wants every 837 row will be sent back once. The model may "fix" a correct query. `COUNT(DISTINCT claim_key)` with no `is_latest_submission` is flagged even when the distinct count would not change.
- A true empty population (no rows of that kind in the extract) is also a repair trigger. The model may invent a looser predicate to produce rows. The prompt says not to. The code does not stop it.
- Wrong SQL that executes and looks plausible is submitted. Wrong date basis (`payment_month` vs `service_month`), `payer_sequence = 'primary'`, treating every $0 payment as a denial, swapped measures. The 2026 study's gain was repairing SQL that did not execute, not a second opinion on SQL that did. A02's suspicion list catches only the patterns in `checks.py`.
- `COUNT(*)` or `SUM` of a filter that matches nothing returns one row of zeros. That is not an empty result, so it is not repaired. A bad literal can survive.
- A rate in (1, 100] is accepted. A fraction that was multiplied by 100 by mistake, and landed under 100, does not trip the check. A value above 100 does, including a ratio the question wanted as a multiple.
- The fan-out check uses the first column. A result that is supposed to repeat a claim key across lines is flagged.
- After two repairs the last SQL is submitted even if it still errors or still looks wrong. The product would show that number. Abstention is a later arm (the refresh's selector, and T21), and the current questions have no credit for a refusal on an ordinary analytic question.
- Three samples of eight rows can miss the literal the question needs. Payer names are not profiled into the prompt on purpose. This arm has no lookup tool.
- `agent_cli`'s investigative budget counts failed SQL. The config allows 8. A path that samples three views and repairs twice uses 6. Burning the two extra slots on binder errors still leaves `answer` available, because answer is not an investigative call. A draft that never parsed cannot be recorded by the CLI (`guard` runs again before `final.json`). The in-process path returns the SQL with `rows is None`, which `bench.eval` scores as no result.
- The checks are not a security boundary. The guard is. A comment-stripped string scan can be dodged by breaking an identifier apart. That is not a realistic model behavior, and it would still have to pass `guard`.

## Tokens, calls, latency

Calls are fixed by the protocol. Tokens and seconds are estimates. Nothing in this table was billed.

Per question, the turns are sequential. A repair cannot start before the execution returns.

| Path | Model turns | Investigative SQL calls (`eval_subagents` "tool calls") | What the turns are |
|---|---|---|---|
| Refuse | 1 | 0 | `refuse` before any query |
| Happy path | 4 | 2 | describe, one sample, draft, answer |
| Both repairs used | 6 | 4 | describe, one sample, draft, two repairs, answer |

`context` / `describe_schema` is a CLI call and is not in the investigative count. `answer` is not either. A happy path with no sample, which the protocol allows once the schema has been read, is 3 model turns and 1 investigative call.

`documented_example_costs()` builds the token side. Inputs, measured as characters/4 on the current prompt and tool spec:

- system prompt plus basic glossary: 839
- tool spec: 527
- schema allowance: 2,000 (`PLANNING_SCHEMA_TOKENS`). A round figure for ten views in the DESCRIBE listing. It is not a measurement on the frozen snapshot. Replace it with `len(schema text) / 4` before using the table as a screen budget.
- question 80, one sample result 250, one execution result 200, tool-call output 180 per turn. The 180 is the tool call, not a thinking budget.

The cached-prefix column writes the system prompt and the tool spec once at 1.25× and reads them at 0.1× on later turns (`technique_factors.yaml`). Schema text and results stay in the growing transcript and are not part of that prefix.

| Path | Input tokens, no cache | Input tokens, cached prefix | Output tokens, tool calls only | Latency stand-in |
|---|---|---|---|---|
| Happy path | 12,484 | 9,137 | 720 | Haiku 6 s, Sonnet 12 s |
| Both repairs | 20,876 | 15,070 | 1,080 | Haiku 9 s, Sonnet 18 s |

The stand-in is 1.5 s per turn for Haiku and 3 s per turn for Sonnet (`PLANNING_SECONDS_PER_TURN`). Those are planning constants for a short low-effort tool turn, so the table can be compared with itself: the cap costs two extra turns, 1.65× the happy-path input with the prefix cached (15,070 / 9,137) and 1.67× with no cache (20,876 / 12,484). They are not a forecast. Sonnet 5.5 keeps thinking on, and this package did not measure thinking tokens or wall time. SQL time sits inside the turn; it was not timed separately.

A scripted happy path on the 3-view fixture in the test, same character/4 counter, tool spec resent every turn, no cache, recorded about 6.7k input tokens and about 120 output tokens. That fixture schema is much smaller than the 2,000-token allowance, and the scripted tool calls are shorter than the 180-token output allowance, which is why it sits under the table.

The average over a screen is the happy path for every question that does not trip a trigger, plus one or two extra turns for the ones that do. Beskorovainyi's 1.09–1.50× is that average for errors-only repair. A02 also repairs empty and suspicious results, so its average multiplier moves with the false-alarm rate. That rate is unmeasured here. The two-repair row is the per-question cap, not the expected mean.

## Related work

- **Beskorovainyi 2026, arXiv:2606.29733.** Repair of drafts that failed to execute: about +3.9 / +1.5 / +1.3 points on Qwen-7B / 14B / 32B, +4.0 on Llama-8B, +1.0 on Llama-70B (not significant), +3.4 / +4.4 on CodeLlama-7B / 13B, at 1.09–1.50× tokens (higher on CodeLlama, whose drafts fail more often). The gain did not survive as a second opinion on SQL that already ran. A02's error path is this result. The empty and suspicious paths are a stricter policy than the paper's.
- **CHASE-SQL, Pourreza et al. 2024, arXiv:2410.01943.** Removing the query fixer dropped BIRD dev execution accuracy from 73.01 to 69.23 (−3.78). Their generator pool produced more broken SQL than a single strong model will. The selector, not the fixer, was the larger term. A02 does not select among candidates.
- **MAC-SQL, arXiv:2312.11242.** Removing the refiner cost 4.63 points on their GPT-4 BIRD dev run. The refiner is the same role as a repair turn. A02 keeps it in one transcript.
- **ReFoRCE, Deng et al. 2025, arXiv:2502.00675.** Explore columns with small queries, treat an empty result as a failure, self-refine until the same non-empty result appears twice. A02 takes the explore step and the empty-is-failure rule, caps refinement at two, and does not require the result to repeat. DDL compression is omitted; `sem.*` fits.
- **OpenAI, in-house data agent, 29 Jan 2026.** A zero-row result is investigated and retried with the failure still in context. Fewer, non-overlapping tools. Guide the goal, not a numbered path. A02 puts the path in the tool results and the goal in the prompt. Screen 1's procedural-steps gain did not hold up once the pitfalls text was in the prefix, which is the same failure the OpenAI note describes.
- **`research/sota_refresh_2.md` (PR #1), §4 and §5.** The proposed v1 loop is the shape of this package: one model, guarded SQL, a repair cap of two, programmatic checks, basic glossary, low effort, no schema pruning, no few-shot, no `ask`. The refresh's stage-2 repair arm spends the two retries only when DuckDB returns an error, and leaves zero-row and range warnings as text in the tool result. A02 spends a retry on those warnings too. That is the comparison this arm exists to make. The refresh's reasons to be careful are the failure modes above, especially a repair that drops a filter. Run them as two configs, not as one blended agent.
- **Screen 1 factor E** (`research/screening_results_1.md`): SQL error hints plus programmatic result checks, Haiku −0.3 [−3.1, +2.4], Sonnet +1.7 [−1.0, +4.5]. Annotations, not a forced repair, on an easy set. Not an estimate for A02.
- **Held-out 1** (`research/heldout_results_1.md`): the reason the glossary in the prefix is the basic one.

## What this package does not do

It does not change the generator, the parser, or the lake. It does not call a paid API. It does not score a question set, so it does not claim a point estimate. It does not add lookup, the typed metrics compiler, a second candidate, a cascade, or an abstain. Those are other arms. The production query path stays `ccsim/query.py`.
