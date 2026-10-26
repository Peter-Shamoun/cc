# Glossary contamination audit

Compares each bullet of the basic glossary (`v000_basic`) and the screen-1 pitfalls block
(`v001_screen1_pitfalls`) with the benchmark questions those texts can see: 33 hand-written,
66 Opus-authored, and the 16 parametric family templates (both phrasings). Family instances are
the same template, so they are not scored again. Regenerate with `python -m bench.glossary_contamination`.

## Method

Content words drop function words and numbers. Each question has an IDF from that corpus.
An entry's recall on a question is the IDF-weighted share of its content words that the question also uses.
The answer side is the gold SQL plus the authored interpretation (where one exists), scored with the same IDF.
A bullet is **question-specific** when its best-question recall is at least 0.45 and at most
2 questions reach recall 0.3, or when a 5-gram of its content words
occurs in exactly one question and that same support count is at most 2. A 5-gram
that also matches many other questions is a shared clause, listed below, and is not a flag. The cutoffs are
deliberately high: a definition that shares "denial" and "current adjudication" with half the bench is
general, which is what a glossary is for.

Question tags: `screen1` is the 36-question screen (`research/screening_questions.json`), including a family
template when that screen drew an instance of it. `heldout` is `heldout_hard_1` (27 questions authored after
the pitfalls text, by authors who did not see it). Everything else is `other`.

Documents scored: 205 (screen1 36, heldout 27, other 142).

## Screen-1 versus held-out overlap

Mean, over bullets, of the mean per-question recall inside each tag. `screen1 − heldout` is the tuning gap:
positive means the bullet's words show up more on the questions the text was written beside than on the
later held-out set.

| section | bullets | flagged | mean recall screen1 | mean recall heldout | mean recall other | screen1 − heldout |
|---|---|---|---|---|---|---|
| basic | 8 | 0 | 0.106 | 0.143 | 0.119 | -0.037 |
| pitfalls | 7 | 0 | 0.029 | 0.052 | 0.037 | -0.023 |

The pitfalls block's screen−heldout gap is -0.023; the basic glossary's gap is -0.037.
The pitfalls block does not overlap the screen questions more than the held-out set, and it is not more screen-specific than the basic glossary. Word overlap does not explain the missing Haiku gain: the held-out questions use this vocabulary and Haiku's accuracy still did not move. That is a coverage miss (the block names traps those questions do not turn on), not a copied question.

## Bullets

| section | best question | tag | text recall | answer recall | questions ≥ support | flag | bullet |
|---|---|---|---|---|---|---|
| basic | `a_a2e0fda4ea` | other | 0.31 | 0.68 | 1 |  | - claim: one patient control number (claim_key). sem.claims has one row per 837 submissio… |
| basic | `g1_ar_open_30` | other | 0.41 | 0.52 | 9 |  | - adjudication event: one 835 claim payment (CLP) in sem.remit_claims. A claim can have s… |
| basic | `g1_den_zero_pay_month` | other | 0.66 | 0.60 | 6 |  | - denied (is_denied): recorded denial (claim status 4, is_denied_recorded) OR signal v3 (… |
| basic | `family:payer_class_denial_rate` | screen1 | 0.89 | 0.54 | 125 |  | - denial rate: denied current adjudications / current adjudications, counting each claim … |
| basic | `g1_ar_open_30` | other | 0.36 | 0.54 | 1 |  | - primary payer: the payer billed with payer_responsibility = 'P' on sem.claims. payer_se… |
| basic | `a_9cf81e7511` | heldout | 0.32 | 0.77 | 2 |  | - 835 lines (sem.remit_lines) are missing when a payer denies a whole claim at claim leve… |
| basic | `g1_ar_open_30` | other | 0.33 | 0.40 | 1 |  | - The data starts with the lab's first delivered file. Claims billed before that appear o… |
| basic | `q30_phi_names` | screen1 | 0.13 | 0.12 | 0 |  | - Never return patient names, member ids, birth dates or addresses (they are not in these… |
| pitfalls | `g1_mix_secondary` | other | 0.24 | 0.49 | 0 |  | - sem.claims and sem.claim_lines have one row per 837 submission: resubmissions and claim… |
| pitfalls | `a_8b0d9d09d4` | other | 0.29 | 0.37 | 0 |  | - Money paid, patient responsibility and denials come from current adjudications only (is… |
| pitfalls | `q33_write` | other | 0.30 | 0.48 | 0 |  | - Re-delivered 835 files are already de-duplicated in the sem views; do not de-duplicate … |
| pitfalls | `a_32759ae76a` | other | 0.23 | 0.53 | 0 |  | - sem.remit_lines has no rows for claims denied at claim level; billed codes and units ar… |
| pitfalls | `a_5084d5c2f0` | other | 0.09 | 0.33 | 0 |  | - Time basis matters: payment_date/payment_month (835), service_date/service_month (date … |
| pitfalls | `a_a23ce7f1de` | other | 0.18 | 0.18 | 0 |  | - Rates: use 100.0 * x / y for percentages (integer division truncates); round only at th… |
| pitfalls | `a_d29f83c935` | other | 0.21 | 0.17 | 0 |  | - Ranked questions: ORDER BY the measure (and the stated tie-break) and LIMIT to the numb… |

## Question-specific flags

No bullet met the question-specific rule. The overlap above is spread across many questions,
which is what a general definition looks like. That does not clear the block: the held-out
run already showed the Haiku gain failed to replicate, and a general sentence can still be
aimed at the traps in a particular set. The gap table is the quantitative check on that.

## Shared clauses that are not question-specific

These bullets share a 5-word content span with exactly one question and also match enough
other questions that the bullet stays a general definition. The span is listed; it is not a flag.

- `basic-03` shares `current adjudication duplicate claim denial` with `a_19b39fa2c7`, and 9 questions reach recall 0.3.
- `basic-05` shares `denial rate denied current adjudications` with `a_9a8aa51689`, and 125 questions reach recall 0.3.

## Answer leakage

No bullet contains a number of three or more digits, or a decimal, that also appears in one
question's interpretation. Those interpretations carry the expected counts and amounts; the
glossary does not repeat them. Answer-side recall in the table is shared schema vocabulary in
the gold SQL (`is_current_adjudication`, `payer_responsibility`), not a copied result.

## What this does not show

- Per-question agent errors. Those rows were not kept for screen 1 or the held-out run, so this audit
  cannot line a bullet up with the exact item it would have fixed.
- Whether a general bullet is *true*. Overlap is not correctness.
- Round 3. Those questions are not in the repo; when they land, re-run this before they are used as
  validation for a glossary that was fit on train.
