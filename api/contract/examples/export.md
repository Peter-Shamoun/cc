# Paid amount by payer name

_Exported from ClaimClarity on 2026-10-25T20:00:00Z._

## Question

Paid amount by payer

## Answer

Paid amount by payer.

**SQL**

```sql
SELECT payer_name, SUM(paid_amount) AS paid_amount, COUNT(*) AS claims
FROM sem.claims
GROUP BY 1
ORDER BY paid_amount DESC
```

**Result**

| Payer name | Paid amount | Claims |
| --- | --- | --- |
| UnitedHealthcare | 40676.93 | 175 |
| Medicaid | 34856.59 | 174 |
| Cigna | 33726.04 | 178 |
| Humana | 33696.19 | 189 |
| Medicare | 33068.16 | 170 |
| BCBS | 32990.90 | 158 |
| Aetna | 31179.18 | 156 |

_Chart: Bar chart of paid amount by payer name, 7 points._

**Assumptions**
- All service dates in the current gold version.

**Check before using**
- sem.claims/claim_lines used without is_latest_submission: resubmitted claims count more than once

**Sources**

- Claims (`sem.claims`)
