# Named metric views (sem.metric_*): B1 vs B1 + named metrics on VAL. Not adopted

Run `nm_val`, 2026-10-26, validation split (129 runs per repeat), snapshot `50054399f815`, Haiku, effort low,
two repeats per arm, both arms in one manifest (interleaved, same day, same `claude -p` runner). Configs:
`research/named_metrics_configs.json`. Design frozen at commit `c9b51a7` before VAL was prepared.

## What was added (code stays on branch `analyzer-named-metrics`, parameter `named_metric_views`, default off)

Seven views over sem.* only (`claude/schema/06_named_metric_views.sql`), documented for the agent in
`claude/schema/06_named_metric_views.yaml` (briefing block with grain and measure formulas, `dictionary` columns):

| view | grain / definition |
|---|---|
| sem.metric_claim_payer_outcomes | one current adjudication per claim+payer, with the payer's billed role (P/S over all submissions), CO-45 (claim+line), latest-submission service month, service-to-payment gaps |
| sem.metric_claim_payer_billing_timeline | one billed claim+payer pair: first submission to the payer, that payer's first 835 (any status), current adjudication, day gaps |
| sem.metric_cob_claims | one claim billed to a secondary whose primary 837 is in the data: primary and secondary current outcomes, patient share left, secondary paid, primary-to-secondary timing |
| sem.metric_remit_line_outcomes | one current 835 line: billed role, priced-line flag, allowed per unit, payer median allowed per unit and variance from it, CO-45, modifier 59 |
| sem.metric_procedure_claim_outcomes | current adjudication x billed CPT on the latest submission: claim-basis denial by CPT (claim-level denials included) |
| sem.metric_monthly_by_payer | payer x payment month: adjudications, denial rate, paid, shares of the month, previous calendar month and change |
| sem.metric_monthly_by_payer_class | the same per payer class |

The views are created at every session open (`bench.dataset.open_snapshot`, `ccsim.query.open_session`) because
they add no data: the dataset id and snapshot do not change. With the parameter off they are hidden from
`list`, `profile` and `dictionary` and absent from the briefing; the B1 briefing is byte-identical to the
historical B1 except the product rename. Scoring re-runs answers on the same views.

Verification (`tests/test_named_metric_views.py`): each view checked against SQL over the generator ledger
(`answer_key.duckdb`) on the bench snapshot and on a freshly generated 30-day dataset; documentation must name
exactly the view columns; 16 TRAIN gold answers reproduced from the views.

Leakage: designed from TRAIN questions, gold SQL and train_b0 misses, the playbook, glossary, dictionary and the
generator's pricing logic. No VAL/TEST question text, answer or per-question result was read. One exposure: while
looking for ledger SQL examples I saw the generator's ledger definition of the `secondary_paid_for_primary` family
(its split is not TRAIN); the COB view was not changed after that.

## Result

| arm | accuracy | pass^2 | answered and wrong | tool calls | $/question | $/correct |
|---|---|---|---|---|---|---|
| B1 (rerun, `nm_val`) | 85.7% | 82.2% | 14.0% | 2.50 | 0.0069 | 0.0080 |
| B1 + named metrics | 86.8% | 83.7% | 13.2% | 2.21 | 0.0070 | 0.0081 |
| B1 historical (`s1b` + `s1r2`) | 85.7% | 82.2% | 13.6% | 2.57 | 0.0068 | 0.0079 |

Paired by question (10,000 bootstrap resamples): accuracy **+1.2 pp, 95% CI [-2.7, +5.4]** (better on 10
questions, worse on 10); answered-and-wrong **-0.8 pp, CI [-4.7, +3.1]**. Rerun B1 vs historical B1: +0.0 pp
[-3.5, +3.5], so the rerun is a stable control.

The treatment's final SQL read a sem.metric_* view in 19 of 258 runs (17 correct); the other 93% answered from
the base views as B1 does.

**Decision: not adopted.** The ship rule (accuracy CI excludes zero, or answered-and-wrong down at least 3 pp
with no accuracy loss) is not met. B1 stays the production configuration; `named_metric_views` stays off.

Caveats: VAL was already used to tune B1 (stages 1-3), so it is weaker evidence than a sealed split, and the
TEST split is spent. Two repeats on 129 questions cannot resolve a 1-2 pp effect. The views are correct but rarely
chosen at effort low; a stronger push (or making them the default views for these categories) might change usage,
but that would be a new design and needs a fresh held-out set to evaluate.
