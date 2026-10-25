# Variant: context retrieval

**Verdict.** Ship the value index as tenant-scoped infrastructure. Do not expect schema linking or dynamic few-shot to move accuracy on a 10-view `sem.*` catalog. Screen 1 already measured lexical few-shot at about zero. The only arm with a non-zero prior that was never isolated is value retrieval (`cr_value` vs `cr0_base`). Schema linking probably will not help. Few-shot probably will not help. Value retrieval might help only when the user names a payer or a reason in words instead of the stored code; on the current train questions it mostly confirms codes the question already states.

Code: `analyst/variants/context_retrieval/`. Flags: `analyst/variants/context_retrieval/screen2_arms.json`. Tests: `tests/test_variant_context_retrieval.py`.

## What this variant does

Retrieval runs in Python before the model writes SQL. The model sees the result and returns one `SELECT`, which is executed through the guarded session (`tools.sql` → `bench/guard.py` → the tenant-scoped DuckDB connection). There is no paid client. `ContextRetrievalAgent` defaults to an empty `ScriptedLLM`, which raises `LLMNotScripted` rather than calling a network API. Prompts and `TOOL_SPEC` (`link_schema`, `lookup_values`, `similar_examples`, `sql`, `answer`, `refuse`) are in `prompts.py` so `bench/eval_subagents.py` can screen the same briefing later. This PR does not edit that harness or `DEFAULT_TECHNIQUE_CONFIG`.

`ContextRetrievalAgent()` with no arguments turns the value index on, retrieves k=3 skeleton examples, and sets `hard_refuse=True`. That default is for a local `bench.eval --agent` run. The screen arms below keep `hard_refuse` false so a regex refusal does not change the safety items relative to the held-out base.

### Schema linking

`link_schema` reads `information_schema.columns` on the session connection (the model cannot `DESCRIBE`; the guard rejects `information_schema`). Column notes come from `claude/schema/03_semantic_model.yaml` dimensions. Intent phrases boost views (denial → remit claims and adjustments, CPT → claim and remit lines, interest / PLB → provider adjustments).

| `schema_mode` | Behavior |
| --- | --- |
| `full` | Every `sem.*` view and column. Recommended. Stays in the cached system prefix. |
| `linked_recall` | Drop views with score 0. If fewer than `recall_floor_views` (default 2) survive, fall back to the full catalog. Never drop columns. |
| `linked_columns` | Also drop non-grain columns with no token overlap. This is the Maamari failure mode. It is implemented so it can be demonstrated. It is not a screen arm. |

Bound value kinds force their views back in (`carc` → `sem.adjustments`, and so on), so a value hit cannot be pruned away.

### Value index

`ValueIndex.build(connection, tenant_id)` runs five fixed aggregate queries over `sem.payers`, `sem.claim_lines` ∪ `sem.remit_lines`, `sem.adjustments`, `sem.remarks`, and `sem.diagnoses`. The SQL is an allowlist, checked with sqlglot before execute. `SELECT *` is rejected. Forbidden columns include claim keys, NPIs, member ids, check numbers, control numbers, and file ids. Descriptions that look like an SSN, member id, or date of birth are scrubbed. The index stores kind, value, optional description and category, and an aggregate row count. It does not store `payer_key`. The tenant id is a session argument, not SQL and not a tool argument. `lookup` raises `TenantScopeError` if the caller’s tenant does not match the index.

Resolution never interpolates user text into SQL. A code binds only when the best score is at least 0.99 and unique at that level, or at least 0.90 with a margin of 0.08 over the second hit. Otherwise the prompt lists the candidates and says not to filter. Phrase match requires the full normalized description or category (underscores become spaces, so category `timely_filing` matches “timely filing”), or every distinctive token of length ≥ 6. One shared word is not enough. Several codes that share a phrase become one ambiguous item. CPT codes keep leading zeros. CARC values may drop leading zeros so “01” matches “1”. Diagnosis codes are stored without dots (`E11.9` → `E119`).

Payer mention: full name or alias containment, overlap of non-generic tokens of length ≥ 5, plus a speculative lookup of question tokens of length ≥ 5 when the best score is at least 0.85. The generic stoplist is compared case-insensitively, which stops “CLAIMS” inside legal names (“Hollister Claims Administration”) from looking like a payer mention.

### Train-only few-shot

`load_train_library` reads `bench/questions.yaml` and `bench/authored_questions.json`. Split rules, fail-closed:

- explicit `split` if present
- `question_set` in `{heldout_hard_1}`, or a name starting with `validation`, `heldout`, or `val_`, → validation
- a name starting with `test` → test
- empty `question_set` → train
- any other `question_set` → `SplitLabelError`

`retrieve_examples` returns before the guard when `k=0`, so a dirty library is not read when few-shot is off. For `k>0` the whole pool must be `split=="train"` or the call raises `SplitLeakError`. It also drops the same id, an exact normalized question, raw Jaccard ≥ `near_duplicate_jaccard` (0.92), and the same family when `exclude_same_family` is true. Rows with `expect_refusal` or without `gold_sql` are not loaded. Extra non-train questions passed to `load_train_library` raise.

`lexical` is raw token Jaccard, the screen-1 selector. `skeleton` masks string and code-shaped literals (and any literals the value index just bound) and drops stopwords, so “Aetna denial rate” can match “{payer} denial rate” instead of every question that says “Aetna”.

## Why this is not the screen-1 example factor

Screen 1 factor D was already dynamic: Jaccard over raw tokens of `generated_questions.json`, k=3, own id and same family excluded. It measured −0.3 [−3.1, +2.4] on Haiku and +1.7 [−0.4, +4.2] on Sonnet (`research/screening_results_1.md`). Factor A (typed metrics plus lookup, bundled) was +3.1 / −1.0 and not measurable. The refresh parks few-shot k and schema pruning out of screen 2 for that reason, and notes that value lookup was never isolated from the metrics compiler (`research/sota_refresh_2.md` on `cursor/sota-refresh-2-c33c`).

Retrieval can still differ from those static or lexical examples, for four reasons:

1. The static `verified_requests` in `03_semantic_model.yaml` are metric-compiler JSON. They are identical on every question, and some contain placeholders. They are not tenant SQL.
2. Screen-1 Jaccard latches onto shared words (“denial”, “payer”). The library was family instances, and same-family examples were excluded, so the closest shape was often the one removed. Skeleton masking (XiYan-SQL, DAIL-SQL) matches query shape after entities are masked.
3. Value retrieval is tenant-specific literals, not examples. Another customer’s payer spellings would be the wrong filter. That cannot be replaced by a static prompt.
4. A hard train-only and near-duplicate guard can only lower leakage-inflated accuracy. It does not create new signal.

None of those reasons predict a large accuracy change on this catalog. DAIL-SQL’s +2.9 over random five-shot was on Spider with GPT-4 and a large example pool (72.3 zero-shot → 82.4 five-shot similar; [arXiv:2308.15363](https://arxiv.org/abs/2308.15363)). XiYan masks entities and matches skeletons ([arXiv:2411.08599](https://arxiv.org/abs/2411.08599)). Here the train pool is the handwritten questions plus authored train rows, the schema already fits, and screen 1’s lexical arm was inside noise. A skeleton selector is the remaining few-shot lever, and the prior on it is near zero.

## Expected accuracy

| Piece | Prior on this bench | Why |
| --- | --- | --- |
| Schema linking | 0 to negative | Ten views fit. Maamari et al. found filtering a schema that fits costs −2.7 to −11.6 pp on strong models ([arXiv:2408.07702](https://arxiv.org/abs/2408.07702)). Lexical linking was −4.24 pp on Llama-3.3-70B ([arXiv:2606.29733](https://arxiv.org/abs/2606.29733)). CHESS gained about +2 pp and ~5× fewer tokens only when the catalog did not fit ([arXiv:2405.16755](https://arxiv.org/abs/2405.16755)). `technique_factors.yaml` T05 says do not prune `sem.*` (0 to −5 pp if pruned) and notes the conflict with prompt caching (T26). |
| Few-shot, lexical | ~0, already measured | Screen 1 factor D. Re-running it (`cr_fewshot_lexical`) is a reproduction, not a new hypothesis. |
| Few-shot, skeleton | ~0, not distinguishable under ~120 validation questions | The literature gain needs a large, shape-diverse pool and a model that copies the wrong join without an example. Neighbors on this pool often share a grain and still teach the wrong population (see dry run). T13’s +3 to +10 pp is the Spider/BIRD prior, not this bench. |
| Value index | 0 to +2 overall; larger only on phrase and name questions | CHASE-SQL lost 2.92 pp when LSH value retrieval was removed (70.09 vs 73.01; [arXiv:2410.01943](https://arxiv.org/abs/2410.01943)). T04 expects +2 to +5 pp on payer/code questions. Most current questions already contain the stored code (`CARC 29`, CPT `80053`). The effect shows up when the user says “Aetna”, “timely filing”, or “deductible” without the literal. |

Do not accept or reject a bundled arm. The refresh’s rule is one factor at a time. `cr_all` is recorded so the flags exist; it is not an accept/reject arm.

Acceptance, if a screen is run: paired difference versus `cr0_base`, both Haiku and Sonnet, validation only, interval entirely above 0 on each model. The prior says `cr_schema_recall` and both few-shot arms will miss that bar. `cr_value` is the only arm worth the spend, and only if stage 1 is under the ceiling. The refresh parked value lookup out of stage 1 because it was bundled with the metric compiler in screen 1.

## Cost and latency

On a 16-day `acme_labs` snapshot (`Scenario(days=16, volume_scale=0.25, seed=11)`, dataset `f914d04a2553`, not in the repo) the index built in 0.038 s: 544 entries, 122273 bytes of JSON, 52 payers, 232 procedure codes, 18 CARC, 12 RARC, 230 diagnosis codes. The serialized JSON contained no `claim_key`, SSN-shaped text, `member_id`, or `date_of_birth`. Build is fixed SQL on the session connection. There is no extra model call if the index is prefetched into the prompt. T04’s “+1 tool call, ~0.5–1.5 s” applies only if the model calls `lookup_values` itself.

Approximate tokens (`len // 4`) on the ten dry-run questions:

- `full`: system ~2338 (schema + basic glossary, cacheable) plus user ~70–100. Cache reads are 0.1× input (T26).
- `linked_recall`: system ~597 (glossary only) plus a user-turn schema of ~1675–1830. Net cut is small, and the schema moves out of the cached prefix. Anthropic’s context-engineering guidance is to keep the stable prefix cached and retrieve the long tail ([effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)). With 10 views the schema *is* the stable prefix. Moving it per question is the expensive direction.
- `linked_columns`: user schema ~941–1354. Still cache-hostile, and it drops required columns on short wording (below).
- Skeleton k=3 adds a few hundred uncached user tokens (T13: +0.5–2k).

`linked_recall` barely prunes. On the dry-run questions, q01 dropped only `sem.payers` and `sem.provider_adjustments`; q06 dropped only `sem.diagnoses`; q08 dropped nothing. Recall of gold relations and real gold columns was complete for `full`, `linked_recall`, and `linked_columns` on those ten wordings. A shorter probe, “Which denial reason codes appear most often?”, dropped `remit_claim_id` under `linked_columns`. The official q10 wording contains “claim” and “payer”, so the token “claim” accidentally keeps the column. That is the Maamari failure, on this catalog, with no model in the loop.

## Dry run

Procedure: build the real index and the real prompts on the snapshot, then write the SQL by hand as the model would, and score it with `bench.guard` and `bench.compare.match`. Questions (train, handwritten): q01, q06, q08, q10, q11, q13, q21, q22, q25, q32.

Contamination caveat: gold SQL in `questions.yaml` was read while surveying the bench, so the hand-written SQL is an upper bound on what a model would emit. The objective results are retrieval recall, bindings, the split guard, and the matcher. Held-out question text and the pitfalls glossary were not used.

Eight of nine scored questions matched gold. q11 and q13 matched and returned 0 rows because this snapshot is too small for `HAVING` 20 and 30. That is a data-size limit, not a retrieval result.

Failure modes:

- **q10 needed an explicit `LIMIT 5`.** The small snapshot has 6 denial CARCs. `topn` requires exactly 5 rows. The first SQL returned all 6 and failed; `LIMIT 5` matched (gold keeps 6 codes on this snapshot; the candidate must return 5). The retrieved examples did not teach the limit. This is a question-reading miss, not a value-index miss. q10 bound nothing.
- **q32 is refused only when `hard_refuse` is on** (“different customer than the logged-in tenant”). The screen arms set `hard_refuse` false, so `cr_value` itself does not refuse. If few-shot is also on, the neighbors are q08, q07, and q13, which would teach the model to answer with this tenant’s denial rate. Keep `hard_refuse` the same on base and treatment. The regex also false-positives if a real question says “update” or “patient names” descriptively.
- **PLB reason codes are not in the index.** q06 (“interest”, provider-level, L6) resolved no value. The linker keeps `sem.provider_adjustments` from the wording, and the question already states L6. Modifiers (for example 59) are unmatched on purpose. The user did not ask for a PLB index.
- **Exact bind can be the wrong population.** “Aetna” binds `Aetna` at 1.0 (35 rows) with `Aetna Medicare` second at 0.95, so the 0.99 rule binds the commercial name. On this snapshot the current-adjudication denial rate was 0.0 on 25 rows for exact `Aetna` and 0.0 on 35 rows for `ILIKE '%Aetna%'`. The rates matched; the populations did not. A user who meant every Aetna product is silently narrowed.
- **Ambiguous names stay ambiguous, and similarity is noisy.** “Blue Cross” is ambiguous between BCBS NC (65 rows) and Healthy Blue (14). “United” is ambiguous across Unitedhealthcare, Unitedhealthcare Medicare Advantage, Unitedhealthcare Community Plan NC, and Umr at 0.86 because of prefix similarity. The prompt says not to guess. Umr is a noisy candidate.
- **Phrase match is the useful case, and it is rare in the train set.** q21 bound CARC `29` (description “The time limit for filing has expired”, category `timely_filing`). q25 bound CARC `1` (“Deductible amount”). A probe that said “timely filing” with no number bound `29`, and the SQL matched q21’s gold (2 rows here). A “deductible” probe bound `1`. Earlier phrase rules false-bound CARC 23, 16, and 11 and RARC N290 on shared words (“adjustments”, “submission”, “diagnosis”); the all-tokens rule stopped that. “Patient responsibility” stays ambiguous across CARCs 1, 2, and 3.
- **Column mode drops join keys on short questions.** See the `remit_claim_id` probe above. Do not ship `linked_columns`.
- **Skeleton neighbors often have the right grain and the wrong filter.** q08 → q07 (0.875, uses `is_current`), then a denial-rate-by-month example, then q03 (paid dollars, not a rate). q11 → q13, q08, q07; q13’s population is not q11’s. q21 → q14, q08, q03, none of which filter a CARC; the value bind carries the code, the examples do not. q22’s nearest authored neighbor is days after a frequency-7 correction, which is a different interval than submission-to-remit. q01’s neighbors are other counts and do not show `is_current` (scores 0.27 / 0.20 / 0.13). Copying a neighbor is as likely to hurt as to help.

Bindings after the generic-case and phrase-token fixes, on the ten questions: only q21 (`carc=29`) and q25 (`carc=1`) bound. q01, q06, q08, q10, q11, q13, and q22 bound nothing and were not ambiguous. q32 was refused before retrieval when `hard_refuse` was on.

## Screen-2 arms

Compare each arm with `cr0_base`. One flag changes, except `cr_all`, which is not an accept/reject arm. `hard_refuse` stays false on every arm. `glossary` stays `basic` (this package rejects `pitfalls`). `context_style` stays `goal`. Effort is a harness flag, not a retrieval flag; the held-out base used low effort.

Load flags with `config_from_flags` on the objects in `SCREEN_ARMS` or `screen2_arms.json`.

**`cr0_base`** (matches the held-out base: glossary basic, goal, SQL on, lookup off, examples off):

```json
{"schema_mode":"full","value_index":false,"fewshot_k":0,"fewshot_selector":"skeleton","near_duplicate_jaccard":0.92,"exclude_same_family":true,"recall_floor_views":2,"glossary":"basic","context_style":"goal","hard_refuse":false,"max_examples_in_prompt":3}
```

**`cr_value`** — primary arm. Same flags, `value_index: true`.

**`cr_fewshot_skeleton`** — negative control. `fewshot_k: 3`, `fewshot_selector: "skeleton"`.

**`cr_fewshot_lexical`** — screen-1 reproduction. `fewshot_k: 3`, `fewshot_selector: "lexical"`.

**`cr_schema_recall`** — negative control. `schema_mode: "linked_recall"`.

**`cr_all`** — do not use to accept or reject. `schema_mode: "linked_recall"`, `value_index: true`, `fewshot_k: 3`, selector skeleton.

`linked_columns` is reachable as `RetrievalConfig(schema_mode="linked_columns")` and should stay out of the screen. A subagent screen can paste `packet.briefing` from `prepare_context` (`render_subagent_briefing`). Prefetch is what the tests run; the tools exist for a later harness that wants the model to call them.

## What was not changed

Generator (frozen at 3.3), parser, lake, question keys, `bench/eval_subagents.py`, `bench/agent_cli.py`, and other agents’ directories. The pitfalls glossary was not opened.
