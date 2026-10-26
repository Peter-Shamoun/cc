Playbook (learned from training-question misses; general rules about these views and the answer format)

Answer shape
1. Return exactly the columns the question asks for, in the order it asks for them, labels first. Do not add
   supporting counts, totals or helper columns: an extra column makes the answer wrong.
2. A rate, share or ratio is a fraction between 0 and 1 unless the question says percent or %.
3. When the question names a single payer, month or code, filter to it and return its row; do not group across
   similar values.

Names and categories
4. Match a payer on its exact payer_name with =, never ILIKE '%...%': several payers share words in their names.
   Use lookup when unsure of the spelling.
5. Each payer_class value is its own category. Filter on the exact class asked for; do not fold related classes in.

Grain and joins
6. sem.remit_claims has one row per 835 claim payment. A claim's outcome with a payer is its row with
   is_current_adjudication; count those rows rather than building DISTINCT workarounds.
7. Join 837 rows to 835 rows on both claim_key and payer_key. claim_key alone mixes payers.
8. A claim's primary payer is the payer_key on its sem.claims rows with payer_responsibility = 'P', over all of its
   submissions (SELECT DISTINCT claim_key, payer_key). Adding is_latest_submission there drops claims whose latest
   submission went to another payer or replaced an earlier one.
9. Billed attributes of a claim (lines, charges, modifiers, service month) come from its latest submission.
   sem.claim_lines already has is_latest_submission; do not add a second latest-submission join through sem.claims.
10. Line-level 835 questions (denied lines, paid or allowed by procedure) come straight from sem.remit_lines on
    current adjudications; do not rebuild them from 837 lines.
11. A payer's first remittance for a claim is the earliest payment_date over all of that payer's 835 rows for the
    claim, not only its current adjudication.
12. sem.payments has one row per check or EFT; count its rows.

Dates
13. Use the precomputed month columns: payment_month for "by payment date", service_month for service dates. A
    month filter is payment_month = DATE 'YYYY-MM-01'.

Before answering
14. Re-read the question: every stated filter (payer, class, month, population) must be in the final SQL, and the
    row count must fit what was asked (one row, one per month, top N).
