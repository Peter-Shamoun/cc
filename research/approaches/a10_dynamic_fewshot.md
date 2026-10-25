# A10 dynamic few-shot

25 Oct 2026. A train-pool retriever for the analyst. It selects three solved questions and puts each question plus its reference SQL in front of the question being answered. Similarity is TF-IDF cosine or BM25, computed locally. Nothing in this approach calls a paid API, opens `ccsim/query.py`, or changes the generator, the parser, or the lake.

Screen 1 already ran a few-shot arm (`research/screening_results_1.md`, factor D). That arm turned on `enable_examples` in `bench/agent_cli.py`: Jaccard overlap against the snapshot's `generated_questions.json`, dropping only the question id and its `family`. The model had to call the tool. The main effect was Haiku −0.3 [−3.1, +2.4] and Sonnet +1.7 [−0.4, +4.2], not distinguishable from zero, on a set where Sonnet was near the ceiling. `research/sota_refresh_2.md` (PR #1) parks few-shot until the knowledge screen is frozen, and only then with demonstrations taken from TRAIN and dropped when they are near-duplicates of a validation question. A10 is that retriever, built now so the solution map has a guarded implementation. It is not a screen-2 arm.

Code: `analyst/approaches/a10_dynamic_fewshot/`. The harness files are unchanged. The adapter writes into a manifest that `bench/eval_subagents.py` already produces.

## Design

Four pieces.

**Retriever** (`retriever.py`). The corpus is the train-legal pool after the guard's pool checks. IDF and BM25 document frequency are fit on that pool only, so a validation or test question cannot move the weights. The query is the target question's text, with dates, quoted strings, and numbers masked, and with function words removed. Masking is the XiYan idea applied without an embedding API: a payer name or a month should not decide the neighbor. The target's reference SQL is not a feature. Using it would rank examples by how close they are to the answer.

- `tfidf` (default): cosine of sparse TF-IDF. Term frequency is the raw count. IDF is `log((1 + n) / (1 + df)) + 1`. This is the embedding arm with no model weights and no network.
- `bm25`: Okapi BM25, k1 = 1.5, b = 0.75, non-negative IDF.

`k` is 3. Scores at or below zero are dropped, so an empty overlap returns fewer than three examples rather than a random neighbor. Ties break by question id. Repeats of the same target therefore get the same examples. The object returned to the prompt is the id, the question text, and `gold_sql`. Interpretation text and `answer_key_sql` are not on that object. Authored interpretations often state the expected numbers.

**Leakage guard** (`leakage.py`). A candidate is dropped, not down-weighted, on the first matching reason:

| Reason | What it blocks |
|---|---|
| `same_question` | The question being answered |
| `unassigned` | No split label. Missing labels fail closed |
| `split:<name>` | Anything other than `train`, including `validation`, `val`, `dev`, and `test` |
| `held_out` | `question_set == heldout_hard_1` (the held-out 27), plus any id the assignment lists, even if the manager labeled it train |
| `refusal` | `expect_refusal` |
| `no_reference_sql` | No `gold_sql` to show |
| `parametric_family` | Same `family`, or an id `f_<family>__<params>` when the field was stripped |
| `template_sibling` | Shared `template_id`, `template`, or `template_family` |
| `same_reference_sql` | Reference SQL equal after case and whitespace normalization |
| `sql_template_sibling` | Reference SQL equal after literals are masked. Same template, different payer or month, including when nobody set `family` |
| `near_duplicate` | Masked question-token Jaccard ≥ 0.85, stopwords kept. A paraphrase whose SQL was rewritten enough to miss the skeleton check |

Checks that read the target's SQL are exclusions only. They do not affect the score. A target with no split raises `LeakageError` before ranking, so an unlabeled question cannot quietly draw train examples.

The held-out set is hard-coded as `heldout_hard_1` and cannot be turned off by an empty list in the split file. There are 27 authored questions with that tag (`bench/authored_questions.json`); the leakage-guard test locks that count. Extra question-set names and extra ids from the split manager are unioned in.

**Prompts** (`prompts.py`). The block quotes the three questions and their reference SQL, and tells the model the SQL is a pattern for grain and filters, not the answer, and not a source of literals. `render_prompt` inserts the block immediately before the `User question:` marker that `bench.agent_session.build_prompt` puts at the end of the briefing. The briefing stays the prefix. Examples are per question, which is where `research/sota_refresh_2.md` puts material that must not sit in the cached glossary. An empty selection leaves the prompt unchanged. A prompt without the marker raises, so the block cannot be stuck on after the question.

**Adapter** (`adapter.py`). `technique_config()` is a `configs.json` entry that `python -m bench.eval_subagents prepare` accepts. It matches the screen-2 base in the refresh: basic glossary, goal briefing, effort low, SQL on, metrics and lookup off. `enable_examples` is forced off. Turning it on raises. That tool is the screen-1 retriever; its library is the whole snapshot and its filter is id plus family only.

`attach_to_manifest` rewrites prompts on entries whose config name is `a10_dynamic_fewshot`, records `a10_example_ids` on each entry, and writes an `a10` report (policy `a10-leakage-1`, method, k, ids per question) onto the manifest. Other configs in the same file are untouched, so one prepare can carry A10 next to a base arm. `score` does not read the prompt. It re-executes the submitted SQL in a fresh sandbox, which is unchanged.

`target_splits` defaults to train, validation, and test, so a leave-one-out call on train works. A validation screen should pass `("validation",)`. The confirmatory run should pass `("test",)` and should be the first time those ids are targets. The pool rules do not depend on this knob. Validation and test rows are never examples, whatever the knob says.

```bash
python -m bench.eval_subagents prepare RUN --configs configs.json --models haiku,sonnet --repeats 2
python -m analyst.approaches.a10_dynamic_fewshot \
    .ccsim/agent_sessions/RUN/manifest.json \
    --questions pool.json \
    --splits splits.json \
    --target-splits validation
```

`pool.json` is a JSON list of question objects from every split, with `question_set` and `family` still on them. The orchestrator must send the manifest prompt. `agent_cli briefing` rebuilds the prompt from the session and will not contain the examples, because this change does not edit the sandbox.

The production query path is still `ccsim/query.py`: per-request DuckDB, `sem.*` always, `lossless.*` only when the session is escalated, `allowed_directories` and `lock_configuration` on. A10 never opens a session. The SQL the model submits is still checked by `bench/guard.py` and re-executed by the harness. Example SQL is text in the prompt. This module does not run it.

## Split manager

A teammate is building the train / validation / test assignment in a separate PR (`P1_splits` in `research/technique_factors.yaml`, and the split table in `research/sota_refresh_2.md` §5). A10 does not invent that assignment. It reads one.

The manager owns:

- which id is `train`, `validation`, or `test`
- the inner-dev slice carved out of train, if an optimizer is used later
- when test ids are allowed to become targets

A10 owns the refusal to retrieve from anything that is not train, and the family / template / held-out rules, which the manager cannot waive.

Three ways to hand the assignment over, all in `SplitAssignment`:

1. `from_mapping({question_id: split})`, plus optional `held_out_ids` and extra question-set names.
2. `from_json(path)` reading the file below.
3. `from_manager(manager, question_ids)` where `manager.split_of(question_id)` returns a label or `None`. Optional attributes `held_out_ids` and `held_out_question_sets` are snapshotted and unioned with the built-in held-out set. Retrieval does not call the manager again.

```json
{
  "version": 1,
  "splits": {"q01_claims_submitted": "train", "a_9a8aa51689": "test"},
  "held_out_ids": ["a_9a8aa51689"],
  "held_out_question_sets": ["heldout_hard_1"]
}
```

`val` and `dev` normalize to validation. `training` normalizes to train. Any other label is kept and is not train, so a typo fails closed. An id absent from `splits` is unassigned and cannot be an example. An unassigned target raises.

What the manager should do, given the refresh:

| Split | Example source? | Target of A10? |
|---|---|---|
| TRAIN | Yes, leave-one-out (self, family, template siblings, near-duplicates removed) | Only for a debug run. Not an effect estimate |
| Inner dev, if carved out of TRAIN | Only if the manager leaves it labeled `train`. Label it anything else to keep it out of the pool | Not an effect estimate |
| VALIDATION, about 120 questions | No | Yes, `--target-splits validation`, after the retriever is frozen |
| TEST, authored after the freeze | No | Yes, once, `--target-splits test` |
| `heldout_hard_1` | No, even if mislabeled train | Only if the manager also put those ids in validation or test and the caller opened that split |

`held_out_ids` should list the 27 even when `question_set` is present, so a pool that dropped the tag still cannot serve them. The guard checks both.

A10 does not know whether TEST has been frozen. `target_splits` is the caller's statement of that. The function will attach examples for a test target if asked. The screen driver has to not ask until the approach is frozen. Selecting which of the three neighbors to keep by a validation score would burn validation; this module does not do that selection. A later optimizer (MIPROv2 or GEPA, per the refresh) has to carve its selection set out of TRAIN.

## Risk of overfitting

Dynamic few-shot is the screen-1 pitfalls failure at the level of a single question. The pitfalls list named the traps in the questions it was written against, and Haiku's +16 points became −1.9 on the held-out 27 (`research/heldout_results_1.md`). An example whose SQL is the answer with a different literal does the same thing with less text. The model copies the filter, the payer, or the month, and the harness scores a fluent wrong number. The guard exists because that copy is indistinguishable from a real gain if the sibling was in the pool.

Concrete ways this approach still overfits, and what the code does about each:

1. **Family and template copy.** Two phrasings of `payer_denial_rate` differ by a name and share a SQL skeleton. Showing one while scoring the other is showing the answer. The family field, the `f_<family>__` id, the template fields, and the masked-SQL skeleton all drop those pairs. The skeleton check also drops an untagged pair. It can drop a legitimate neighbor whose SQL happens to mask to the same string, and then k falls below 3. That is the intended trade.

2. **Near-duplicate prose.** A validation question reworded from a train question, with the SQL edited enough to miss the skeleton, still teaches the answer. Jaccard ≥ 0.85 on the masked question drops it. A one-token swap inside a short question can land under 0.85. The family and template tags are the primary control; the threshold is a backstop, not a replacement for tags. The split manager should keep those tags on the pool objects.

3. **Split leakage.** If the pool includes validation, the measured effect is in-sample. The guard refuses every label but `train`. An optimizer that then picks shots by the validation score re-opens the leak one level up. The refresh's rule is the one to keep: VAL is touched once, as a paired contrast, not as a selection set.

4. **The held-out 27.** They were authored after screen 1, by people who had not seen the pitfalls text, to test whether that text generalized. Using them as examples spends the only confirmation set this repo has. The tag excludes them even when the assignment says train. The test reads `bench/authored_questions.json` and requires all 27 to be blocked under a train label.

5. **Copying a wrong example.** The catalog's risk for T13. The three nearest train questions can share words and not share grain (`payment_date` versus `service_date`, claim versus line, latest submission versus current adjudication). The prompt says not to copy literals. It cannot stop the copy. Screen 1's near-zero result is the prior: on ten views, with a glossary, similar SQL is often a distractor. A gain that shows up only when the neighbor is a skeleton-sibling is this failure, and the guard is there so that gain cannot be reported.

6. **Answer text in the example.** `interpretation` on authored questions states expected totals. `answer_key_sql` is a second answer. Neither is rendered. A future change that "adds context" from those fields would leak the result into the prompt.

7. **IDF fit on the wrong corpus.** Fitting on the full bank would let test wording change which train question looks rare. The index fits on the train-legal pool only.

8. **Target SQL as a query.** Skeleton similarity against a preliminary SQL is what DAIL-SQL does, and it needs a first guess from a model. Ranking by the gold SQL instead would select neighbors of the answer. A10 uses gold SQL only to reject siblings.

9. **Small train pool.** With a few dozen train questions the top three are whoever shares a noun. The refresh's TRAIN size is 40. Below that, k=3 is a large fraction of the pool and the overfitting risk goes up, not down. Do not fill the pool with parametric siblings of the validation questions to make k=3 look confident.

10. **Prompt injection.** The pool is bench questions, not live user text. Example SQL is still model-visible text. A later pool of user-written questions would be an injection channel (catalog T24). This module does not sanitize SQL because it does not execute it; the harness guard still rejects anything the model submits.

What would count as evidence, when this is eventually run: a paired contrast against the same base on validation, question-level bootstrap, per model, interval entirely above 0, with pass^2 reported. A point estimate whose interval includes 0 is not an adoption, which is the screen-1 few-shot result read correctly. Test runs once, after that, and an interval that includes 0 there does not ship. The held-out 27 are too small to be that test (about ±15 points on Haiku) and are already a spent contrast for the pitfalls text.

## Related work

**This repo.** Factor D of screen 1 is the predecessor, and it did not move accuracy. T13 in `research/technique_factors.yaml` is the factor definition: k in {0, 3, 5}, selector in {lexical, embedding, skeleton-aware}, format in {tool-call JSON, SQL, both}, depending on P1 splits, with risks "near-duplicate leakage" and "copying wrong examples", and a catalog cost of about 0.5–2k uncached tokens per question. A10 is the lexical / sparse-embedding, k=3, SQL-format cell, with the split dependency enforced in the guard rather than in a write-up. The catalog's expected effect, +3 to +10 points, is larger than screen 1 measured. The screen is the better prior for this schema. The refresh says few-shot returns only after the K2 playbook is frozen, as a screen-3 candidate, with the near-zero screen-1 prior stated out loud.

**DAIL-SQL** (Gao et al., 2023, [arXiv:2308.15363](https://arxiv.org/abs/2308.15363)). The catalog records GPT-4 on Spider at 72.3 zero-shot and 82.4 with five similar shots, +2.9 over random shots. Their selector mixes question similarity with SQL-skeleton similarity against a preliminary query. A10 keeps the skeleton idea as an exclusion and does not spend a model call on a preliminary query. Spider's schema-linking problem is not this bench: `sem.*` is ten views and already fits.

**XiYan-SQL** (Liu et al., 2024, [arXiv:2411.08599](https://arxiv.org/abs/2411.08599)). The refresh notes that their example selection masks named entities and matches question skeletons, so the retriever follows the query shape instead of the entity. Literal masking in A10 is that step, done with a regex rather than a named-entity model, because a named-entity model is another dependency and another place to leak.

**CHASE-SQL** (Pourreza et al., 2024, [arXiv:2410.01943](https://arxiv.org/abs/2410.01943)). Removing the online synthetic-example generator from their 73.01 BIRD-dev system cost 0.85 points (72.16). The selector and the value retriever moved the number by more. Synthetic examples are a different mechanism (generated, not retrieved) and are out of scope here. The ablation is a reason to expect a small effect, not a reason to skip the leakage checks.

**MIPROv2** (Opsahl-Ong et al., 2024, [arXiv:2406.11695](https://arxiv.org/abs/2406.11695)). Jointly chooses instructions and demonstrations; in their suite the demonstrations mattered most. The selection metric is a validation score. Pointing that metric at our validation split spends the split. If a demonstration optimizer is used later, its mini-batches come from an inner slice of TRAIN. A10's retriever is fixed, not optimized.

**GEPA and ACE**, as summarized in the refresh. Both will overfit a 40-question train split if the selection set is the same split or is the validation split. Not used here.

**Snowflake Cortex Analyst** evaluations, cited from T13, treat verified question-SQL pairs as the library. Same shape, same requirement that the library and the scored questions be disjoint. The Anthropic tool-use figure in the catalog (72 to 90, internal) is a different task. It is not evidence that this bench will move.

## Tests

`pytest -q analyst/approaches/a10_dynamic_fewshot/test_a10.py`

The leakage-guard test covers self, parametric family (field and id prefix), template id, exact SQL, masked SQL skeleton, near-duplicate prose, the held-out tag even under a train label, an explicit held-out id, validation, test, `dev` normalized to validation, unlabeled, refusal, missing SQL, and a legal train neighbor that is the only row returned. It also loads the authored file and requires all 27 `heldout_hard_1` questions to be blocked and absent from the index. A separate test checks that ranking follows the question text when the reference SQL has been swapped to point the other way. The adapter test checks that only the A10 config's prompt changes, that interpretation text and answer-key SQL do not appear, and that a closed target split is refused.
