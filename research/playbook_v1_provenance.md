# Playbook v1: where each rule came from

`analyzer/playbook/frozen_playbook.md`, frozen 2026-10-26. Source: B0 on the TRAIN split, Haiku, 2 repeats
(`train_b0`, 226 runs, 80.5% accuracy, pass^2 74.3%), snapshot `50054399f815`. 29 of 113 questions were missed at
least once (15 both times). I read the question, the reference SQL, Haiku's SQL and the scorer's reason for one
representative of each missed family and each missed authored question, then wrote one rule per recurring pattern.

Written by the analysis agent, not a person (the owner chose this). The rules follow sota_refresh_2 §5: properties of
the views and the answer contract only; no question id, payer name, procedure or diagnosis code, status code or
threshold from TRAIN. 14 rules, under the 40-line cap. No two rules conflict.

| Rule | Pattern in the TRAIN misses |
|---|---|
| 1 extra columns | Most "values differ (or extra / missing columns)" misses: totals, counts and an ordering column added next to the asked measures (denials by type, weekday volume, payer-class mix, rate questions) |
| 2 fraction vs percent | Rate families answered as 100 × rate with two decimals where the question asked for a rate |
| 3 single value | A one-payer question answered with one row per matching payer |
| 4 exact payer name | `ILIKE '%name%'` matched more than one payer in the denial-rate and monthly-rate families |
| 5 exact class | A class question answered over two related classes |
| 6 current adjudication rows | `COUNT(DISTINCT claim_payer_key)` workarounds in place of the current-adjudication filter |
| 7 join on both keys | Days-to-first-remit joined 837 and 835 on claim_key only |
| 8 primary payer over all submissions | Referring-provider, principal-diagnosis, modifier and line-count denial questions restricted the primary-payer set to the latest submission |
| 9 latest submission once | Weekday volume added a second latest-submission join through sem.claims |
| 10 lines from remit_lines | Denied-line counts rebuilt from 837 lines and claim-level denials |
| 11 first remittance | First-remit date taken from current adjudications only |
| 12 one row per payment | Payments counted as distinct trace numbers with extra conditions |
| 13 month columns | Payment-month filters rebuilt from payment_date ranges (and a payer filter dropped) |
| 14 re-read filters | Final SQL missing a filter the question stated |

Disclosure: before writing this I had seen two VALIDATION misses during calibration spot checks (a fraction where a
percent was asked, and a primary-payer population mistake). Rules 2 and 8 also match those. Both rules come from
several TRAIN misses each and state the general property, but the exposure is noted so the B1/B10 results can be
read with it in mind.
