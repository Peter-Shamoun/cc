# A08 self-consistency

Five independent end-to-end attempts, one voted answer, and a confidence score that is the vote margin. The margin is what an abstain rule or a cascade to a larger model would read. Nothing here calls a model, and nothing here changes the generator, the parser, the lake, or `bench/`.

Code: `analyst/approaches/a08_self_consistency/`. The harness entry point is a config that `python -m bench.eval_subagents prepare` already accepts.

## What was asked

Screen 1 never turned on multiple candidates (T17 in `research/technique_factors.yaml`). Held-out 1, 27 hard questions, still has Sonnet at 83.3–90.7% and Haiku at 72.2–85.2%. On the base config, pass^2 sits 3.7 points under Haiku's mean and 1.8 under Sonnet's, so a second identical run is not a photocopy and it is not an independent draw either. One cell moves more than that: Haiku with pitfalls and procedural steps is 75.9% accuracy and 63.0% pass^2. The literature review (`research/sota_refresh_2.md` on `cursor/sota-refresh-2-c33c`) treats execution-result voting as the large pipeline effect this repo has not measured, and it says the samples have to actually differ.

This module is that vote. It is not the pairwise selector. CHASE's selector beat execution-majority by about 4 points; that is a different arm, and it is another model call. Here the only model calls are the five attempts. Disagreement becomes a number, and the number decides accept, escalate, or abstain.

## Protocol

An attempt is one subagent session: its own briefing, its own tool trace, its own submitted SQL. N = 5. Temperature, top_p, and top_k return HTTP 400 on the models in `technique_factors.yaml`, so an attempt is not a second sample from one conversation.

Two schedules, both legal technique configs (`bench.agent_cli.DEFAULT_TECHNIQUE_CONFIG` keys only, which is the check `create_session` enforces):

| Schedule | configs.json | prepare flag | What varies |
|---|---|---|---|
| Repeats (the default) | one config, `a08_self_consistency` | `--repeats 5` | Nothing in the prompt. Diversity is whatever nondeterminism the model still has. |
| Prompt shapes | `a08_self_consistency__1` .. `__5` | `--repeats 1` | Briefing and tool feedback. Not the glossary. |

The base config is the screen-2 base from the literature review, not the screen-1 winner: basic glossary, goal briefing, free SQL over `sem.*`, metrics off, lookup off, examples off, result checks off, effort low.

The five prompt shapes are: that base; the same base with procedural steps; the base at high effort; the base with SQL error hints and programmatic result checks; procedural steps plus those checks. The screen-1 pitfalls text is not one of them. Held-out 1 did not support it for Haiku (−1.9 points, interval about ±14), and a knowledge change is not a sample. Mixing it into the vote would confound self-consistency with a glossary experiment.

Repeats are the definition of self-consistency. Prompt shapes are the substitute the review allows, because five identical prompts on a strong model are the setting where Beskorovainyi measured ~0 points at 5× tokens. The module does not pick a winner between them. A run can do either, and the voter groups both: one config across repeats, or every config whose name is `a08_self_consistency` or starts with `a08_self_consistency__`.

## Result normalizer

Voting is over executed rows, not SQL text. Two queries that return the same answer must land in one cluster, and two queries that swap two counts must not. The key is built in `normalize.py`. Correctness of the winner is still `bench.compare.match` on the chosen attempt's raw rows, so the normalizer cannot grade itself.

What the key erases:

- **Column order.** Text columns are identified by the multiset of their values, so a label and a rate can arrive in either order. Fractional measures keep their relative order. Whole-number measures keep theirs. `(225, 141)` and `(141, 225)` stay different, which is the same rule as the matcher. Integer-like strings such as a CPT code stay text, so they reorder with labels; a decimal string such as `"0.250"` is a measure and is rounded with the numbers.
- **Rounding.** Each number is binned onto the question's `tol` grid (default 0.005, the matcher default). Binning is transitive. Absolute tolerance is not: 0 matches 0.005 and 0.005 matches 0.010, but 0 does not match 0.010. A vote needs an equivalence, so a pair that sits on a bin boundary can fail to cluster even though the matcher would accept it. Questions should pass their own `tol`. Rate questions in `bench/questions.yaml` use 0.0005, not 0.005.
- **Ties.** For `compare.order = topn`, rows strictly better than the binned cutoff stay in the key, and the slots at the cutoff become a count. Two legal top-5s that pick different tied payers share a key. A top-5 that drops the strict winner does not. `metric_col` is used when that column is numeric. If the attempt reordered columns so `metric_col` lands on text, and exactly one numeric column remains, that column is the metric. Two numeric columns swapped under a stale `metric_col` are not repaired.
- **Declared percent and sign.** Only when the question spec sets `percent_cols` or `sign_cols`. A fractional column whose values sit in (1, 100] is divided by 100, so 4.65 and 0.0465 cluster. A whole-number column is rescaled only when it is the only numeric column (25 versus 0.25). Next to a count, an integer stays an integer, so a supporting count of 86 is not turned into 0.86. A column that is entirely zero or negative is flipped when `sign_cols` is set, so a credit and its absolute value cluster. Mixed signs inside one column are left alone.

What the key keeps, on purpose:

- Extra columns, including the supporting count the screen-1 matcher bug was about. If three attempts add the count and two do not, the vote splits and a 3–2 escalates. Dropping the column would hide a real difference between results. The matcher can still accept the chosen SQL against gold later.
- Row order, when the question says `order: strict`. Otherwise rows are a multiset.
- Refusal text. Every refusal is one cluster, and every clarification is another. The reasons are not compared.

Empty results share one key. A SQL error, a missing `final.json`, a query `guard` rejects, or a final query that reads no table (`bench.agent_session._reads_a_table`, the same grounding check as the scorer) is not a ballot. It still occupies one of the five scheduled slots, so it pulls the margin down.

## Voting and the confidence score

```
margin = (votes for the unique leader − votes for the runner-up) / scheduled attempts
```

The denominator is 5, not the number of attempts that happened to execute. A crash disagrees with everyone. It cannot be ignored by two successes.

A strict majority is more than half of the scheduled attempts: `winner_votes * 2 > scheduled`. For five attempts that means at least three, and the leader has to be alone at the top. A 2–2–1 tie has margin 0 and no chosen SQL.

`confidence` is that margin. The action reads the margin and the majority bit:

| Outcome | Margin | Action |
|---|---|---|
| 5–0 | 1.0 | accept |
| 4–1 | 0.6 | accept |
| 4 ballots agreeing, one non-ballot | 0.8 | accept |
| 3 agreeing, two non-ballots | 0.6 | accept |
| 3–1–1 | 0.4 | escalate |
| 3–2 | 0.2 | escalate |
| 2–1–1–1 | 0.2, but only two votes | abstain (no strict majority; the plurality SQL is kept on the record) |
| 2–2–1 | 0 | abstain, no chosen SQL |
| Four agreeing attempts when five were scheduled | 0.8 | escalate, not accept. An incomplete schedule cannot look unanimous. |

Thresholds are `ACCEPT_MARGIN = 0.6` and `ESCALATE_MARGIN = 0.2` in `protocol.py`. 0.6 is a 4–1. A 3–2 is a majority, and it is the case the cascade is for: ship the tentative SQL to a larger model instead of to the user. Abstain is the case with no majority worth confirming, or a majority so thin it sits under 0.2 (that does not arise at N = 5 for a unique leader; the weakest unique majority is the 3–2).

Escalate and abstain both count as wrong if the score is execution accuracy. The refresh says the same thing about a selector that declines. The vote file also reports selective accuracy on the accepts, the abstain rate, and the escalate rate, so the reliability trade can be read next to the accuracy loss.

The chosen SQL is the lowest attempt index inside the winning cluster. Ties inside a cluster do not depend on dict order.

`any_attempt_correct` is the CHASE upper bound: did any of the five raw results match gold? If that bound sits on top of the single-attempt accuracy, the five prompts are not diverse and neither a vote nor a selector can help. The module records the bound whenever the question has gold. It does not compute it by normalizing gold into the same key; it uses `match`, including top-N tie legality.

Refusals are the correct cluster on `expect_refusal` questions. A majority refusal is an accept, not an abstain.

## Adapter

`bench/eval_subagents.py` is unchanged. The adapter writes a config file and, after the orchestrator has run the sessions, re-executes each submitted SQL.

```bash
python -m analyst.approaches.a08_self_consistency.adapter write-configs a08.json
python -m bench.eval_subagents prepare RUN --configs a08.json --models haiku,sonnet --repeats 5
# the orchestrator runs one subagent per manifest entry; this module does not
python -m bench.eval_subagents score RUN          # optional; the vote does not read results.json
python -m analyst.approaches.a08_self_consistency.adapter vote RUN
```

Prompt shapes: add `--diverse-prompts` to `write-configs` and pass `--repeats 1`.

`vote` writes `<run>/a08_vote.json`. Each question and model gets one decision: action, margin, cluster sizes, the chosen SQL, `voted_correct` when the question is in the bench, `any_attempt_correct`, and a calibration block over the labeled questions.

Re-execution goes through `bench.dataset.open_snapshot` and `bench.guard.guard`. That snapshot sandbox is the bench's copy of the per-request DuckDB restrictions in `ccsim/query.py`: `sem.*` only unless the question escalates, `allowed_directories` limited to those relations, external access off, configuration locked. The voter does not assume a role, does not open S3, and does not trust the prose in `final.json`. The agent's text is ignored. The rows are whatever the SQL returns under the guard.

No paid API call sits on this path. `vote` fails if the run has no `a08_self_consistency` sessions.

## Cost multiplier

Five attempts are five model calls. The vote adds none.

| | Repeat mode | Prompt-shape mode |
|---|---|---|
| Model calls | 5× | 5× |
| Output tokens | 5× | 5× |
| Input tokens, no cache | 5× | 5× |
| Input tokens, prefix cached | about 1.4× | about 5× |
| Latency, serial | 5× | 5× |
| Latency, parallel | about 1.3× | about 1.3× |

The 1.4× figure is one cache write plus four reads at 0.1×, from the prices in `technique_factors.yaml`, and only if the cached prefix is essentially the whole prompt. Repeat mode has that prefix: the briefing is identical. Prompt-shape mode changes the briefing (`context_style`, and the tool list once checks are on), so the calls do not share a prefix and input is closer to 5×. Output is 5× either way. The 1.3× latency is the top of the "1–1.3× if parallel" range in the technique catalog; it is not a measurement on this harness.

A cascade is not included. It fires only when the action is escalate (margin below 0.6 with a strict majority, or an incomplete schedule that would otherwise accept). Those questions pay for a sixth call on a larger model. Questions that accept or abstain do not.

Relative to one Haiku call, five Haiku calls cost more than one Sonnet call on output if Sonnet is the usual multiple of Haiku, and they cost less than five Sonnet calls. This repo does not have a priced token log for a screened session, so the multiplier stays in calls and tokens, not dollars. The 2026 open-model study cited below spent 4.99× tokens for a change of −0.07 points. That is the prior for repeat mode. It is not a result from this bench.

## Does confidence predict correctness?

Not measured on Haiku or Sonnet. Held-out 1 stored two repeats, not five, and it did not keep the executed rows. Screen 1 stored one repeat. This session made no model calls. The calibration function is in `vote.calibrate` and runs as part of `adapter.vote_run` once a real run exists. Until that file exists, the only evidence is a planted panel in `test_self_consistency.py` and the shape of the earlier runs.

What the planted panel does. Twenty questions, gold known, N = 5. Ten of them have margin 1.0: eight are actually right, two are unanimous and wrong. Ten have a 3–2: eight of those majorities are wrong (and both minority attempts are right), two majorities are right. Bins are the policy cut points.

| Bin | Questions | Accuracy of the voted rows |
|---|---|---|
| margin ≥ 0.6 (accept) | 10 | 0.80 |
| 0.2 ≤ margin < 0.6 (escalate) | 10 | 0.20 |
| margin < 0.2 | 0 | — |

The gap, high bin minus low populated bin, is 0.60, and accuracy does not fall as the margin rises. Selective accuracy on the accepts is 0.80. Execution accuracy of the policy, counting every escalate as wrong, is 0.40, because the eight correct unanimous answers are the only ones shipped. The margin ranks the rate. It does not certify an item: `unanimous_wrong` is 2, and those two have confidence 1.0.

A second planted panel is four questions, each a 5–0 on the wrong rows. Every confidence is 1.0 and every voted answer is wrong. That is the correlated-error failure the review names: all five attempts share the misreading, the cluster is unanimous, and the cascade never fires. Confidence predicts correctness only when the wrong attempts disagree with the right ones. When they agree, the score is sure and useless.

What the existing runs can say, which is not a calibration. Held-out base pass^2 is 70.4% against 74.1% accuracy for Haiku, and 81.5% against 83.3% for Sonnet. Identical prompts already agree most of the time. Five repeats will often be 5–0, including on the questions every config missed (three questions in held-out 1 were failed by essentially every model and config, for a population reason that has since been written into the question text). On those, the margin would have been 1 and the answer wrong. The upper bound is the number that tells the two cases apart, and it is not computable from the published tables because the raw rows were not kept.

So: use the margin to decide whether to ship, escalate, or abstain. Do not read a high margin as a probability the rows are right until a real run shows a gap and a small `unanimous_wrong`. Report both. If `any_attempt_correct` is barely above the single-attempt accuracy, drop the arm; the prompts are not diverse and the 5× spend is the Beskorovainyi result.

## Related work

The method is execution clustering, not a vote over SQL strings.

- Wang et al. 2022, Self-Consistency Improves Chain of Thought Reasoning ([arXiv:2203.11171](https://arxiv.org/abs/2203.11171)). Sample several reasoning paths and take a majority. Their gains used temperature. This API does not have it. The margin here is the same idea as their agreement count, normalized by the number of scheduled paths.
- Pourreza et al. 2024, CHASE-SQL ([arXiv:2410.01943](https://arxiv.org/abs/2410.01943)). On BIRD dev with Gemini 1.5 Pro, one prompt is 63.01% execution accuracy, self-consistency over execution results is 68.84%, and the pairwise selector is 73.01%. The technique catalog records the candidate upper bound at 82.79%, and the review says that upper bound is the quantity that matters. The catalog also notes that pools past about 20 candidates added nothing. The review's reading of the same paper: raising temperature widened the upper bound and lowered the lower bound, and self-consistency got worse, so diversity had to come from different generators. That is why prompt shapes exist here, and why the upper bound is a field on every voted question.
- Liu et al. 2024, XiYan-SQL ([arXiv:2411.08599](https://arxiv.org/abs/2411.08599)). Five candidates, grouped by execution result, then a fine-tuned selector. Reported BIRD test execution accuracy 75.63%. The grouping step is the same as this module. The selector is not.
- Deng et al. 2025, ReFoRCE ([arXiv:2502.00675](https://arxiv.org/abs/2502.00675)). Parallel threads, then a vote. The catalog records −2.0 points on Spider 2.0-Snow with o3 when majority voting is removed. Empty results are failures there; here an empty result is a real ballot (it may be the true answer) and an execution error is not.
- Wang et al. 2025, Agentar-Scale-SQL ([arXiv:2509.24403](https://arxiv.org/abs/2509.24403)). Group candidates that return the same rows, then a pairwise tournament on the question, the schema, and the execution result. Again the grouping is in this module and the tournament is not.
- Beskorovainyi 2026 ([arXiv:2606.29733](https://arxiv.org/abs/2606.29733)), as read in the literature review. Five-sample self-consistency on Qwen2.5-Coder-32B changed accuracy by −0.07 and +0.13 points at about 5× tokens. The catalog's companion figure is −0.07 points at 4.99× tokens, with p95 latency 10.6 s to 50.0 s. Majority vote among correlated samples is noise. That is the prior for repeat mode on Sonnet, which was near a ceiling on screen 1 (ten of sixteen configs at or above 97.2%).
- EHRSQL 2024 ([arXiv:2405.06673](https://arxiv.org/abs/2405.06673)). The second-place team abstained unless an ensemble agreed. Top RS(10) was 81.32; teams with a smaller gap between RS(0) and RS(10) ranked higher, because a wrong number cost more than a refusal. The accept / escalate / abstain rule is that idea with a margin instead of a hard unanimous vote. This bench does not yet have the out-of-scope split or the RS(c) metric the catalog asks for (T21), so the module reports the rates and lets execution accuracy count a non-accept as wrong.
- Local results this design is not allowed to ignore. Screen 1 (`research/screening_results_1.md`): pitfalls +15.6 Haiku / +5.9 Sonnet, procedural steps +4.5 Haiku, everything else consistent with zero, Sonnet near the ceiling. Held-out 1 (`research/heldout_results_1.md`): the pitfalls effect did not replicate for Haiku; the screen-1 winner was +11.1 for Haiku with an interval that includes 0, on 27 questions. The review's screen-2 proposal (`sota_refresh_2.md`, stage 2 item 2) is three generators, group by executed rows, take a strict majority, otherwise one selector call, otherwise abstain, and publish the upper bound. This module is that proposal with N = 5, no selector call, and the margin standing where the selector would have stood.

## What a later run should report

Per model, against the single-attempt base, on the validation split only:

- accuracy of the accepted answers, with escalate and abstain counted wrong
- selective accuracy, accept rate, escalate rate, abstain rate
- `any_attempt_correct` minus single-attempt accuracy (the headroom)
- the calibration gap and `unanimous_wrong`
- calls and tokens per question, repeat mode and prompt-shape mode separately

Drop the arm if the upper bound is within a couple of points of one attempt. Keep the margin as a cascade signal only if the escalate bin is actually less accurate than the accept bin on that run. A planted panel cannot make that decision.

## Files

| Path | Role |
|---|---|
| `analyst/approaches/a08_self_consistency/protocol.py` | N, the legal config, the prompt-shape schedule, the cost multiplier |
| `analyst/approaches/a08_self_consistency/normalize.py` | Column order, rounding, ties |
| `analyst/approaches/a08_self_consistency/vote.py` | Majority, margin, accept / escalate / abstain, calibration |
| `analyst/approaches/a08_self_consistency/adapter.py` | `write-configs` and `vote` for `bench.eval_subagents` |
| `analyst/approaches/a08_self_consistency/test_self_consistency.py` | Normalizer against `bench.compare.match`, the margin table, the planted calibration panel, re-execution on a tiny snapshot |

Tests: `python -m pytest analyst/approaches/a08_self_consistency/test_self_consistency.py`. They do not build a dataset and they do not call a model. The repo's `pytest -q tests/` does not collect this file; the test lives next to the module because this change does not touch `tests/`.
