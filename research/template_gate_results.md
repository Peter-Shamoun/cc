# Template gate in front of B1: safe and cheap, but VAL cannot show its coverage

Run `gate_val`, 2026-10-27, `python -m bench.gate_bench` (results in `research/runs/gate_val/`). VAL (129
questions x 2 repeats), snapshot `50054399f815`. The gate is Haiku, effort low, no tools, structured output
`{fits, template_id, values, reason}`. Its prompt holds 16 templates (the bench query families, one-line
definitions), the allowed values and the data coverage. fits=true with valid values runs the template SQL. Otherwise
the question falls through to B1, scored with B1's recorded result for the same question and repeat (`nm_val`).

| | runs | B1 alone | gate + templates + B1 | answered by template | template answers correct |
|---|---|---|---|---|---|
| all | 258 | 85.7% | 85.7% | 28 (10.9%) | 28/28 |
| generated (f_) | 30 | 93.3% | 93.3% | 28 (93.3%) | 28/28 |
| hand-written / authored | 228 | 84.6% | 84.6% | 0 | - |

- Wrong template answers: 0. Values rejected by the server check: 0.
- Same decision (template and values) on both repeats: 129/129 questions.
- Same decision for the two wordings of a generated question: 26/30. All 4 misses are one family: "count the
  denied claims (per payer) that carry a CO-n adjustment". The gate read "(per payer)" as "group by payer" and fell
  through to B1. That is the conservative reading of an ambiguous wording, not a wrong answer.
- Cost: $0.126 for 288 calls ($0.0004 per question); 2.1 s per call.

Reading:
- The gate is precise and stable. It never sent a question to a wrong template, and it gave the same decision
  every time it was asked the same question.
- It adds no accuracy on VAL, because B1 already answered all 28 template-matched runs correctly. The gain is
  consistency and governance (fixed SQL for the KPI), not accuracy.
- VAL cannot estimate real coverage. Its hand-written questions are deliberately hard multi-step analyses (cohorts,
  reversal pairing, percentiles, grouped comparisons), and the gate correctly rejected all of them. The generated
  questions come from the same families as the templates, so their 93% is inflated. Real coverage (the 25%
  target) needs real or realistic user questions.
- What the rejections ask for: grouping (by payer class, by CPT, by month) and an optional period (e.g. "never
  remitted" with no month). A `group_by` blank and optional blanks would turn many rejections into template
  answers, and should come before more templates.
