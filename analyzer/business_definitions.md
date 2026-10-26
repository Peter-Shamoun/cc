Business definitions (CC semantic layer):
- claim: one patient control number (claim_key). sem.claims has one row per 837 submission; is_latest_submission
  marks the latest one for each claim_key across all payers.
- adjudication event: one 835 claim payment (CLP) in sem.remit_claims. A claim can have several per payer
  (original, reversal, correction). The current adjudication for a claim+payer is the latest non-reversal event:
  is_current_adjudication. A claim+payer whose latest event is a reversal has no current adjudication. A duplicate-
  claim denial of an accidental resend (is_duplicate_claim_denial, CARC 18) does not replace the original adjudication.
- denied (is_denied): recorded denial (claim status 4, is_denied_recorded) OR signal v3 (is_denied_signal_v3:
  $0 paid, not a reversal, and a CO adjustment with a reviewed denial CARC). $0 paid alone is not a denial.
- denial rate: denied current adjudications / current adjudications, counting each claim once per payer.
- primary payer: the payer billed with payer_responsibility = 'P' on sem.claims. payer_sequence on sem.remit_claims
  comes from the claim status code and is 'unknown' for denials and reversals; do not use it to find primary payers.
- 835 lines (sem.remit_lines) are missing when a payer denies a whole claim at claim level; billed lines are in
  sem.claim_lines.
- The data starts with the lab's first delivered file. Claims billed before that appear only through their 835s
  (sem.remit_claims rows with no sem.claims rows), and some claims show only a later secondary or corrected
  submission; "first submission" and "billed lines" refer to what is in the data.
- Never return patient names, member ids, birth dates or addresses (they are not in these views) and never
  answer about another customer: the session can only see the logged-in customer's data. Data is read-only.

Invariants:
- sem.claims and sem.claim_lines have one row per 837 submission: resubmissions and claims sent on to a secondary
  payer are extra rows. is_latest_submission keeps exactly one row per claim_key, but that row may be the secondary
  payer's submission; for primary-payer questions filter payer_responsibility = 'P' and count DISTINCT claim_key.
- Money paid, patient responsibility and denials come from current adjudications only (is_current_adjudication);
  summing every 835 event double-counts reversals and superseded payments.
- Re-delivered 835 files are already de-duplicated in the sem views; do not de-duplicate again.
- sem.remit_lines has no rows for claims denied at claim level; billed codes and units are in sem.claim_lines.
  Joining remit_lines to remit_claims repeats claim amounts once per line: aggregate lines first.
- Time basis matters: payment_date/payment_month (835), service_date/service_month (date of service) and
  submission_date (837) give different answers; use the one the question names.
