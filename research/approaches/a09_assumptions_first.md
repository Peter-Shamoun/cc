# A09 — assumptions first

A text-to-SQL analyst that lists the readings of a lab-billing question, locks the one a revenue-cycle analyst would use, and only then queries `sem.*`. The answer repeats every assumption the question did not already pin. A product UI asks one clarifying question when a listed fork would change the number. The benchmark arm does not ask.

This is a design and a gated loop, not a measured accuracy change. No model was called. The checklist was written from defects this repo has already seen (screen 1, held-out 1, the billed-vs-paid unit fix). It is a hypothesis. It is not a playbook bullet, and it must not be edited against the validation split.

## What failed when the question left the population open

Held-out 1 (`research/heldout_results_1.md`) had three questions that every model and every config missed. Generator 3.1 starts mid-stream, so claims billed before the first file exist only as 835s, and some claims show only a later secondary or corrected 837. The questions did not say which of those counted. The fix was in the question text and in the basic glossary, not in a smarter SQL repair. The contrasts were unaffected because the failure was constant. The absolute accuracy was not.

Screen 1's residual errors were the same family (`research/screening_results_1.md`): `is_latest_submission` keeps the secondary payer's row, a join fans out across submission rows, and a claim is counted as a line. The pitfalls paragraph that names those traps gained about 16 points for Haiku on the screen and did not carry to held-out (Haiku −1.9, interval roughly ±14). SOTA refresh 2 treats that paragraph as a negative control. A09 does not paste it back in.

Two other forks already cost a real answer key. Billed units and paid units were confused often enough that the keys were corrected (`WORKPLAN.md`). Payment month and service month are different groupings; `q04` keeps a wrong query that groups the paid total by service month. `$0` paid is not a denial. `payer_sequence = 'primary'` drops status-4 denials, because that column is unknown on denials and reversals. Primary is `payer_responsibility = 'P'` on the 837.

BIRD's leftover gap is this kind of mistake: wrong population, wrong grain, wrong literal, ambiguous question (Li et al. 2023; the refresh's reading of the leaderboard, human performance still 92.96%). AMBROSIA measured the enumeration failure directly: models produce every valid reading under 2% of the time, and ambiguous recall sits at 27–31% against 63–65% on unambiguous items (catalog T20, `research/sota-techniques-catalog-2026.md`). A prompt that says "be careful" does not make the model list the forks. A09 makes the list a turn the controller can reject.

## Protocol

The loop is a workflow with a programmatic gate, in the sense of Anthropic's "Building effective agents": the path that must not vary is code, and the model writes SQL only after that gate. It is one model, not a planner/executor split. SOTA refresh 2 argues against a multi-agent build here.

Phases:

1. **Interpret, before any SQL.** The turn names every open axis and every reading on that axis's menu, and locks one reading. The controller rejects the turn unless the lock is the standard reading below. A missing reading is an error. An extra reading is an error. SQL submitted in this phase does not reach DuckDB.
2. **Ask, only in `ask_mode="material"`.** If an axis is on the ask list and the question did not pin it, the only legal next turn is one clarifying question that names the axis id. No SQL. The result carries the assumption sentences the analyst would have used, so the UI can show the default beside the question.
3. **Query.** `ask_mode="state"` skips the ask and goes here. So does `material` when nothing on the ask list is open. Every SQL, including a probe, has to implement the lock. The security guard runs before the lock check and before execute.
4. **Answer or refuse.** The answer's SQL is guarded, checked against the lock, and executed. Its text must contain each assumption sentence unchanged. Refusal is legal from the first turn, including on a question that would otherwise open axes (PHI, another customer, a write). A refusal does not query and does not pretend an interpretation was accepted.

`protocol.run` takes the backend as an argument. Nothing in the package imports an SDK or reads an API key. A scripted backend returns canned turns; a live backend would see the same system prompt and the same error messages. The scripted test is `analyst/approaches/a09_assumptions_first/test_scripted_backend.py`.

Materiality is structural. The checklist decides from the question text, using forks that change a number on this schema. It does not run both readings. A contrast query is rejected, because it would be SQL against the reading that was not locked. Two consequences, both accepted for this version: the UI may ask when the two readings happen to agree on this extract, and a fork that is not on the menu never asks.

## The standard reading

`checklist.analyze` is the authority. `render_checklist()` is the same menu, rendered into both prompts. The short form:

| Axis | Standard when the question does not pin it | Product UI asks? |
|---|---|---|
| Population | Submitted / billed / resubmitted / voided → 837 claims in the extract (`submitted_837`). Paid, denial, or adjudication → current adjudications, including remit-only claims. Bare "how many claims" → the 837 population. | Only the bare question. A paid or denial question states the current-adjudication assumption instead. |
| Which claims count | "How many claims" → one `claim_key`. A denial rate or a paid amount → one current adjudication per claim and payer. Payments → one row in `sem.payments`. | No. Grain is stated. "Service line", "claim+payer", "distinct claims", and "835 payments" pin it. |
| Date basis | Paid, denial, or remit → `payment_date` / `payment_month`. Billed or submitted, or a period with no money verb → `submission_date`. A span ("days from … until") keeps both ends. | Yes, whenever a period is present and the question did not name the date. Service month and payment month are different answers (`q04`). |
| Units | Unqualified "units" → `units_billed` on the latest submission. | Yes. Billed units and paid units diverge. |
| Ties | Measure, then the label ascending, cut at N. | No. "At least N" is a threshold, not a ranking. A tie at the cutoff is reported with the answer. |

A pinned axis adds no assumption sentence. The question pins an axis by saying "current adjudication", "latest submission", "payment month", "service month", "submission date", "billed units", "paid units", "distinct claims", "once per payer", "service line", "835 payments", or a tie-break. "Primary" plus a submission locks `primary_submission` with no sentence. "Primary" plus a payment or a denial locks `primary_current`, and the assumption sentence stays unless the question also says current. "No current adjudication" locks `remit_events` rather than the current set. That is the reversal question (`q19`). The negated phrase is matched before the positive one.

Received is not the file ingest timestamp. For a claim the lab sent, received means `submission_date` (837 BHT04). `payer_received_date` is used when the question says the payer received the claim. `file_received_at` is when the file landed.

Denial, when the question says "denial" and does not already say status 4 or the signal rule, adds one sentence: a denial is `is_denied`, not every `$0` payment. `q09` spells out both kinds, so it does not get the sentence. `q14` ("paid $0 but are not denials") keeps `is_denied` in the SQL; the zero-pay-only wrong query is rejected.

Primary comes from `payer_responsibility = 'P'` on `sem.claims`, joined to the current adjudication when the question is about payment or denial. The `q24` wrong query, which sums patient responsibility across every payer, fails that check. `payer_sequence` stays legal on a secondary-payment question, because `q23`'s gold uses it and the glossary's warning is about denials and reversals, where the status code does not say primary.

Voids are in the menu only. A billed total includes a void when the question says so (`q02`). The controller does not yet reject a billed total that forgot the frequency filter.

The handwritten bench (`bench/questions.yaml`, safety items aside) does not open an ask. Those questions already name the population, which is the repair held-out 1 made. The test locks that in: every non-refusal gold query passes `sql_violations`, and none of those questions is on the ask list. That test is a tripwire for the checklist. It is not evidence the checklist is complete.

`sql_violations` is a few string checks aimed at the traps above: missing `is_current_adjudication`, an 837-only query where the lock is adjudications, `is_latest_submission` grouped by `payer_name` without `payer_responsibility`, the wrong date column, `units_paid` where the lock is billed units, `paid_amount = 0` with no `is_denied`. A query can pass them and still be wrong. They are not a second judge and not the semantic compiler.

## How a session queries

`open_secure_gateway` calls `ccsim.query.open_session` and wraps `session.con`. That session is the production path: per-request DuckDB, `sem.*` always, `lossless.*` only when `escalate=True`, directories limited to the customer's prefixes, external access off, configuration locked. The gateway then applies `bench.guard` before `execute`, so the statement has to be one read-only SELECT over the allowed schema. A `read_parquet`, a write, or a second statement raises `ProtocolError` and is not recorded as a call.

The scripted test does not stand up AWS. It monkeypatches `open_session` and checks that the gateway refuses a `DELETE` and runs a `SELECT` on the connection the fake session returned. A caller who passes a raw DuckDB connection to `QueryGateway` directly is outside that boundary. Production code should call `open_secure_gateway`.

Escalation is the caller's flag. `q29` (rejected files in `lossless.*`) opens no claim axis. The gateway needs `escalate=True` or the guard rejects the schema. The checklist does not infer that from the question.

## Eval adapter

`bench.eval_subagents` accepts a config whose keys are the technique settings in `bench.agent_cli.DEFAULT_TECHNIQUE_CONFIG`. Unknown keys raise in `create_session`. There is no field for a protocol. A09 therefore has two entry points, and they are not the same thing.

`eval_config.json` is a legal config, and it is the screen-2 base from SOTA refresh 2 §5: basic glossary, goal briefing, free SQL, metrics off, lookup off, examples off, result checks off, effort low, ask off. Ask is off because the scorer treats a clarification as no answer (`score_session` marks anything other than `kind == "answer"` wrong, unless the case expects a refusal). Running

```bash
python -m bench.eval_subagents prepare RUN \
  --configs analyst/approaches/a09_assumptions_first/eval_config.json \
  --models haiku --repeats 1
```

prepares that base. It does not install the protocol. The subagent can still call `sql` first. The harness has no command for an interpret turn, and this change does not edit the harness.

`prepare_run` calls that prepare, then rewrites every manifest `prompt` with the assumptions-first header, the harness briefing, and the menu. The orchestrator that sends `entry["prompt"]` is the one that sees the protocol. Scoring is untouched: the harness still re-executes the submitted SQL. `apply_approach_to_manifest` is the pure function; the scripted test drives it with a fake `prepare` so it does not build a snapshot.

The code gate and the manifest rewrite can diverge. A subagent that ignores the appendix and queries immediately still gets a score. `protocol.run` is the path that makes "before any SQL" true. An eval that wants the gate has to run the analyst through `run`, with a backend, a `QueryGateway` on a real `open_session`, and `ask_mode="state"`. That runner was not pointed at the frozen bench in this change.

The config deliberately does not turn the screen-1 pitfalls text back on, and it does not switch the harness briefing to procedural steps. The refresh's reading of OpenAI's in-house agent is that a fixed procedure pushed the model down the wrong path, and screen 1's procedural gain did not survive the held-out set. The must-do constraint lives in the controller. The appendix tells the subagent what the controller would have enforced.

## Failure modes

- **Prompt-only on the harness path.** `eval_config.json` by itself does not stop an early SQL. Only `protocol.run` does. A score from the json config is a score of the screen-2 base, not of A09.
- **The menu is a trained artifact.** It was written from screen-1 traps, the held-out population bug, and the unit-key fix. The same failure mode as the pitfalls paragraph applies: a later validation set can disagree. Do not add a reading because a validation question missed it. Train-split failures are the only place a new bullet may come from, and even then the refresh's rule is that a person accepts it only as a property of the views.
- **Structural materiality.** The UI asks from the question text, not from two executed results. It can ask when the extract makes the readings agree, and it stays silent on a fork the menu does not name. Adding a probe that runs the other reading would break the "before any SQL, one locked reading" rule. It is a different design.
- **Verbatim sentences.** The answer must contain the assumption sentence unchanged. A paraphrase comes back as an error the model can fix, and it spends a turn on wording. That is harsh in a chat UI. The sentence is the contract that the number and the prose cannot drift apart. A later UI can render `result.assumptions` itself and stop requiring the model to copy them.
- **String checks on SQL.** Mentioning `is_current_adjudication` in a dead predicate passes. So does a correct token with a wrong date window. A gold-shaped query that the checklist mis-classified would be rejected; the yaml tripwire is there to catch that on the questions we have, not on the next hundred.
- **Obligations bind probes.** Once a date is locked, a date-free `SELECT count(*)` is rejected. The analyst cannot look around and then aggregate. That keeps the lock honest and makes some legitimate reconnaissance impossible.
- **One population per question.** A question that asks for billed and paid in the same sentence locks the paid population. The bench does not ask those. A two-measure question needs a different lock.
- **Voids are not gated.** A billed total can include frequency `void` when the question was silent. The menu says to include voids only when asked.
- **`payer_sequence` on secondary.** The primary check does not fire, so a secondary question may filter `payer_sequence` the way `q23` does. That column is the wrong tool for a denial rate. The denial questions in the yaml pin primary through `payer_responsibility` when they mean primary.
- **Tie policy versus the matcher.** `compare.py` accepts any valid top-N when the cutoff is tied. Stating "measure, then label" can drop a tied row the matcher would have allowed, or the reverse. The sentence is for the user. It is not a way to gain a point on `topn`.
- **Over-asking on a paraphrase.** "DOS" and "date of service" pin the service date. A wording the regex misses falls through to the ask list. The yaml tripwire covers the current questions only.
- **Under-asking where a biller would still argue.** "Total paid by month" assumes payment month and, in the product UI, asks. Many billers would accept payment month without a question. The ask list is the short one: bare population, unstated date, unqualified units. Everything else is stated. That is a product choice, and the benchmark cannot tell us if it is the right one.
- **No turn after the answer.** `material` mode stops at the clarifying question. Applying the user's reply is multi-turn plan state (catalog T22), which this module does not implement. The assumptions are on the result so a later turn can start from them.
- **A connection that is not a `QuerySession`.** `QueryGateway` will guard and execute whatever `.execute` it was given. The directory lock, the customer role, and `lock_configuration` come from `open_session`. Tests use an in-memory schema on purpose.

## UX

In the benchmark and in any `ask_mode="state"` session the user sees one reply: the number, then the assumption sentences for axes the question left open. Axes the question already named stay in `result.interpretations` and stay out of the prose, so the reply does not repeat the question. `result.sql` is the statement that was executed, which is the link OpenAI's data agent exposes next to the assumption.

In `ask_mode="material"` the user sees one question and no number when population, date basis, or units is still open. The question is supposed to name the axis (`population`, `date_basis`, `units`) and the controller's suggested wording names the two readings. `result.assumptions` still holds the default, so the UI can say "I would count claims we submitted, excluding claims that appear only on an 835. Is that the set you want?" That is one question, not an interview. Ties, grain, and the glossary denial do not generate a question. They show up as sentences under the number.

Refusal is unchanged. A request for names, another customer's denial rate, or a delete is a refusal with no SQL and no interpretation trace.

What this UI will feel like on the current bench: almost every handwritten question is fully pinned, so the reply is the number and, for a denial question that did not define `is_denied`, one denial sentence. The assumptions become visible on the questions held-out 1 had to repair, which is the point. If the sentences show up on questions a biller finds obvious, the pin list is too narrow and the yaml tripwire did not notice because those questions were already explicit.

## Related work

SOTA refresh 2 (`research/sota_refresh_2.md` on `cursor/sota-refresh-2-c33c`, PR #1) is the literature pass this design follows.

- **OpenAI's in-house data agent** (Xu, Suresh, Tang, 29 Jan 2026) asks when the instruction is incomplete and otherwise proceeds with a stated default. It shows the assumption and links the executed query. A09's `state` mode is that second behavior, with the default chosen for this schema instead of "last 7 or 30 days". The refresh also records their warning that a highly prescriptive path degraded results. A09 keeps the prescription in the gate (what must be true before SQL) and leaves the SQL itself unscripted.
- **BIRD-Interact** (ICLR 2026, arXiv:2510.05318) is why ask stays off in the eval config. Follow-up success for Claude Sonnet 4 is 14.17% against 22.33% on the priority question, and the full agentic setting is 12.67%. The refresh's recommendation 8 is to park clarifying questions until an ambiguous split exists, with gold per interpretation, and to score over-asking on unambiguous items. This repo has neither the split nor a user simulator. Building an `ask` button the scorer marks wrong would only measure the cost.
- **Catalog T20** has three levels: `never_ask_state_assumptions`, `ask_on_listed_ambiguities`, `ask_whenever_uncertain`. `state` is the first. `material` is the second, and the list is the three ask flags, not "whenever the model is unsure". The third level is the over-asking failure AMBROSIA and the refresh both warn about.
- **Rumiantsau and Fokeev** (arXiv:2604.25149) found that a 4 KB page of measures, conventions, and disambiguation rules added 17 to 23 points, and that model choice inside the frontier tier did not separate once the page was present. The checklist is a small version of that page, restricted to the forks this schema actually has. It is not a substitute for the basic glossary, which stays the harness glossary in the eval config.
- **dbt's semantic-layer rerun** (Ganz and Perigaud, 7 Apr 2026) is the other half of the same lesson: text-to-SQL fails as a plausible wrong number, and a compiler fails as an error. A09 does not force the metrics compiler. Screen 1 found no main effect for turning it on in front of an agent that could already write SQL against `sem.*`. The gate makes a wrong population a protocol error when the string check can see it, which is closer to a refusal than to a silent wrong total. It is not the compiler.
- **Maamari et al.** (arXiv:2408.07702), as the refresh uses them: do not prune a schema that fits. A09 does not add a schema linker. The menu is five axes, not a filtered column list.
- **Held-out 1's method rule.** Knowledge text is a trained artifact. This checklist was written with the screen and the held-out write-up in view, so it is already contaminated relative to a clean validation split. The honest next measurement is a new TEST authored by people who have not read it, or a TRAIN-only edit followed by a frozen VAL. This change does not run that measurement.

## What this change does not do

It does not edit `bench/`, the generator, the parser, or the lake. It does not report an accuracy, a pass^k, or a token cost. It does not run `prepare_run` against a snapshot. It does not call Haiku, Sonnet, or Opus. The 20 scripted tests cover the gate, the secure-session wiring, the yaml tripwire, and the manifest rewrite.

```bash
pytest analyst/approaches/a09_assumptions_first/test_scripted_backend.py
```

`pytest -q tests/` does not collect that file (`testpaths` is `tests/`). The approach stays inside `analyst/approaches/a09_assumptions_first/` so the harness configuration did not have to change to add a technique key.
