"""Questions people ask of claim data, one or more per question group of the design (README), written directly
against gold. They show how each kind of question is asked, and the benchmarks time them. They are written from
the design, not taken from the benchmark's queries.

{as_of} is the date the question is asked on.
"""
CANONICAL_QUESTIONS: dict[str, str] = {
    # --- where is the claim now? -----------------------------------------------------------------------------------
    "submitted_but_not_acknowledged": """
        SELECT payer_name, count(*) AS claims, min(first_submission_date) AS oldest_submission
        FROM gold.claims WHERE first_submission_date IS NOT NULL AND NOT is_acknowledged
          AND first_submission_date <= DATE '{as_of}' - 2 GROUP BY 1 ORDER BY claims DESC""",
    "open_claims_by_age": """
        SELECT CASE WHEN age_days <= 30 THEN '0-30' WHEN age_days <= 60 THEN '31-60' WHEN age_days <= 90 THEN '61-90'
                    ELSE '91+' END AS age_bucket, count(*) AS open_claims, sum(charge_amount) AS billed
        FROM (SELECT *, DATE '{as_of}' - first_submission_date AS age_days FROM gold.claims
              WHERE first_submission_date IS NOT NULL AND NOT is_adjudicated)
        GROUP BY 1 ORDER BY min(age_days)""",
    "days_to_pay_by_payer_class": """
        SELECT payer_class, count(*) AS paid_claims, median(payment_date - first_submission_date) AS median_days
        FROM gold.claims WHERE is_adjudicated AND paid_amount > 0 GROUP BY 1 ORDER BY 1""",
    "claims_awaiting_secondary": """
        SELECT count(*) AS claims, sum(current_patient_responsibility_amount) AS primary_patient_share
        FROM gold.claim_payers p
        WHERE payer_responsibility = 'P' AND has_current_adjudication AND current_patient_responsibility_amount > 0
          AND EXISTS (SELECT 1 FROM gold.claim_payers s WHERE s.claim_key = p.claim_key
                      AND s.payer_responsibility = 'S' AND NOT s.has_current_adjudication)""",
    # --- how much did we get? --------------------------------------------------------------------------------------
    "yield_by_payer_and_service_month": """
        SELECT payer_name, service_month, count(*) AS claims, sum(charge_amount) AS billed, sum(paid_amount) AS paid,
               round(sum(paid_amount) / nullif(sum(charge_amount), 0), 3) AS paid_share_of_billed
        FROM gold.claims WHERE is_adjudicated GROUP BY 1, 2 ORDER BY 1, 2""",
    "allowed_and_paid_by_procedure": """
        SELECT l.procedure_code, count(*) AS lines, sum(l.line_charge_amount) AS billed,
               sum(l.line_allowed_amount) AS allowed, sum(l.line_paid_amount) AS paid
        FROM gold.remit_lines l JOIN gold.claim_payers p ON l.remit_claim_id = p.current_remit_claim_id
        GROUP BY 1 ORDER BY paid DESC LIMIT 25""",
    "billed_by_referring_provider": """
        SELECT referring_npi, count(*) AS claims, sum(charge_amount) AS billed, sum(total_paid_amount) AS paid
        FROM gold.claims WHERE referring_npi IS NOT NULL GROUP BY 1 ORDER BY billed DESC LIMIT 25""",
    # --- why not? --------------------------------------------------------------------------------------------------
    "denial_rate_by_payer": """
        SELECT payer_name, count(*) AS adjudicated, count(*) FILTER (WHERE current_is_denied) AS denied,
               round(count(*) FILTER (WHERE current_is_denied) / count(*), 3) AS denial_rate
        FROM gold.claim_payers WHERE has_current_adjudication GROUP BY 1 ORDER BY adjudicated DESC""",
    "top_denial_reasons": """
        SELECT a.carc_code, a.carc_description, count(DISTINCT a.claim_key) AS claims, sum(a.adjustment_amount) AS amount
        FROM gold.adjustments a JOIN gold.claim_payers p ON a.remit_claim_id = p.current_remit_claim_id
        WHERE a.is_denial_carc GROUP BY 1, 2 ORDER BY claims DESC LIMIT 20""",
    "front_end_rejections_by_payer_and_reason": """
        SELECT payer_name, status_code, count(DISTINCT claim_key) AS claims, sum(total_charge_amount) AS billed
        FROM gold.claim_acknowledgments WHERE is_rejected GROUP BY 1, 2 ORDER BY claims DESC LIMIT 20""",
    "rejected_claims_not_yet_resubmitted": """
        SELECT count(*) AS claims, sum(charge_amount) AS billed
        FROM gold.claims WHERE is_rejected AND last_submission_date <= last_acknowledgment_date""",
    "denial_rate_by_principal_diagnosis": """
        SELECT principal_diagnosis_code, count(*) AS adjudicated, count(*) FILTER (WHERE is_denied) AS denied
        FROM gold.claims WHERE is_adjudicated GROUP BY 1 HAVING count(*) >= 50 ORDER BY denied / count(*) DESC LIMIT 20""",
    # --- what happened along the way? ------------------------------------------------------------------------------
    "resubmitted_claims_by_month": """
        SELECT date_trunc('month', first_submission_date)::DATE AS first_submission_month, count(*) AS claims,
               count(*) FILTER (WHERE submission_count > 1) AS resubmitted
        FROM gold.claims WHERE first_submission_date IS NOT NULL GROUP BY 1 ORDER BY 1""",
    "reversals_by_payer_and_month": """
        SELECT payer_name, payment_month, count(*) AS reversals, sum(paid_amount) AS amount_taken_back
        FROM gold.remit_claims WHERE is_reversal GROUP BY 1, 2 ORDER BY 1, 2""",
    "history_of_one_claim": """
        SELECT 'submitted' AS what, submission_date AS on_date, payer_name, charge_amount AS amount
        FROM gold.claim_submissions WHERE claim_key = (SELECT min(claim_key) FROM gold.claims WHERE submission_count > 1)
        UNION ALL
        SELECT CASE WHEN is_reversal THEN 'reversed' ELSE 'adjudicated' END, payment_date, payer_name, paid_amount
        FROM gold.remit_claims WHERE claim_key = (SELECT min(claim_key) FROM gold.claims WHERE submission_count > 1)
        ORDER BY on_date""",
    # --- does the cash reconcile? ----------------------------------------------------------------------------------
    "cash_by_payer_and_month": """
        SELECT payer_name, payment_month, count(*) AS payments, sum(total_payment_amount) AS cash,
               sum(provider_adjustment_amount) AS provider_adjustments
        FROM gold.payments GROUP BY 1, 2 ORDER BY 1, 2""",
    "payments_that_do_not_balance": """
        SELECT payment_id, payer_name, payment_date, total_payment_amount, claims_paid_amount,
               provider_adjustment_amount
        FROM gold.payments WHERE NOT is_balanced ORDER BY payment_date""",
}
