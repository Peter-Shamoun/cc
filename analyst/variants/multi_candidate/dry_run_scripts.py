"""Hand-written candidate turns for the qualitative dry run.

Each block is what this variant's prompt would have the author submit if the author were the model:
read the question, follow that strategy's instruction, write one SELECT. The turns were written
before they were executed. They are not the answer-key SQL. A column error, a wrong date basis,
a percent-vs-fraction rate, and a shared wrong grain are in here on purpose, because those are
the disagreements the selector has to deal with.
"""
from __future__ import annotations

# question id -> strategy -> list of turns. A turn is ("run_sql"|"final"|"refuse", sql or reason, answer text).
SCRIPTS: dict[str, dict[str, list[tuple]]] = {
    "q01_claims_submitted": {
        "direct": [
            ("run_sql", "SELECT count(*) AS submissions FROM sem.claims", ""),
            ("final", "SELECT count(DISTINCT claim_key) AS claims FROM sem.claims",
             "Distinct patient control numbers on submitted 837s."),
        ],
        "grain_and_date": [
            ("final", "SELECT count(DISTINCT claim_key) AS claims FROM sem.claims",
             "Grain: one claim_key. Population: every 837 submission row, counted once. No date was named."),
        ],
        "filter_then_aggregate": [
            ("final", "SELECT count(DISTINCT patient_control_number) AS claims FROM sem.claims",
             "CTE-less slip: the question's words used as a column name."),
        ],
    },
    "q02_total_billed": {
        "direct": [
            ("final",
             "SELECT round(sum(charge_amount), 2) AS billed FROM sem.claims WHERE is_latest_submission",
             "Latest submission per claim, voids included."),
        ],
        "grain_and_date": [
            ("final",
             "SELECT round(sum(charge_amount), 2) AS billed FROM sem.claims WHERE is_latest_submission",
             "Grain: one latest submission. Population: is_latest_submission, including frequency 8. No date filter."),
        ],
        "filter_then_aggregate": [
            ("final",
             "WITH population AS ("
             " SELECT charge_amount FROM sem.claims"
             " WHERE is_latest_submission AND frequency_code <> '8'"
             ") SELECT round(sum(charge_amount), 2) AS billed FROM population",
             "Filter step dropped voids."),
        ],
    },
    "q04_paid_by_month": {
        "direct": [
            ("final",
             "SELECT payment_month, round(sum(paid_amount), 2) AS paid"
             " FROM sem.remit_claims WHERE is_current_adjudication"
             " GROUP BY 1 ORDER BY 1",
             "Paid by payment month, current adjudications."),
        ],
        "grain_and_date": [
            ("final",
             "SELECT payment_month, round(sum(paid_amount), 2) AS paid"
             " FROM sem.remit_claims WHERE is_current_adjudication"
             " GROUP BY 1 ORDER BY 1",
             "Time basis is payment month, because the question says payment month. Population: current adjudications."),
        ],
        "filter_then_aggregate": [
            ("final",
             "WITH population AS ("
             " SELECT service_month, paid_amount FROM sem.remit_claims WHERE is_current_adjudication"
             ") SELECT service_month, round(sum(paid_amount), 2) AS paid FROM population GROUP BY 1 ORDER BY 1",
             "The CTE kept current adjudications but grouped by service month."),
        ],
    },
    "q07_denial_rate": {
        "direct": [
            ("final",
             "SELECT round(avg(is_denied::INT), 4) AS denial_rate"
             " FROM sem.remit_claims WHERE is_current_adjudication",
             "Denied current adjudications over current adjudications, as a fraction."),
        ],
        "grain_and_date": [
            ("final",
             "SELECT round(100.0 * avg(is_denied::INT), 2) AS denial_rate_pct"
             " FROM sem.remit_claims WHERE is_current_adjudication",
             "Same population. The rate is written as a percentage."),
        ],
        "filter_then_aggregate": [
            ("final",
             "WITH population AS ("
             " SELECT paid_amount FROM sem.remit_claims WHERE is_current_adjudication"
             ") SELECT round(avg((paid_amount = 0)::INT), 4) AS denial_rate FROM population",
             "Filter kept current adjudications, then treated every zero payment as a denial."),
        ],
    },
    "q08_denial_rate_by_payer": {
        "direct": [
            ("final",
             "SELECT payer_name, round(avg(is_denied::INT), 4) AS denial_rate"
             " FROM sem.remit_claims WHERE is_current_adjudication"
             " GROUP BY 1 ORDER BY 2 DESC",
             "Denial rate by payer on current adjudications."),
        ],
        "grain_and_date": [
            ("final",
             "SELECT payer_name, round(100.0 * avg(is_denied::INT), 2) AS denial_rate_pct"
             " FROM sem.remit_claims WHERE is_current_adjudication"
             " GROUP BY 1 ORDER BY 2 DESC",
             "Grain: one payer. Population: current adjudications. Rate as a percentage."),
        ],
        "filter_then_aggregate": [
            ("final",
             "WITH population AS ("
             " SELECT payer_name, is_denied FROM sem.claims"
             " WHERE is_latest_submission AND has_remit"
             ") SELECT payer_name, round(avg(is_denied::INT), 4) AS denial_rate"
             " FROM population GROUP BY 1 ORDER BY 2 DESC",
             "Population taken from submissions rather than adjudications."),
        ],
    },
    "q10_top_denial_carcs": {
        "direct": [
            ("final",
             "SELECT a.carc_code, count(DISTINCT a.remit_claim_id) AS denied_claims"
             " FROM sem.adjustments a"
             " JOIN sem.remit_claims r ON r.remit_claim_id = a.remit_claim_id"
             " WHERE r.is_current_adjudication AND r.is_denied"
             " AND a.group_code = 'CO' AND a.is_denial_carc"
             " GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 5",
             "Five CO denial CARCs, one row per denied current claim."),
        ],
        "grain_and_date": [
            ("final",
             "SELECT a.carc_code, count(DISTINCT a.remit_claim_id) AS denied_claims"
             " FROM sem.adjustments a"
             " JOIN sem.remit_claims r ON r.remit_claim_id = a.remit_claim_id"
             " WHERE r.is_current_adjudication AND r.is_denied"
             " AND a.group_code = 'CO' AND a.is_denial_carc"
             " GROUP BY 1 ORDER BY 2 DESC LIMIT 5",
             "Same population. No tie-break on the code, so a tie at the cutoff can change the fifth row."),
        ],
        "filter_then_aggregate": [
            ("final",
             "WITH population AS ("
             " SELECT carc_code FROM sem.adjustments"
             " WHERE is_current_adjudication AND group_code = 'CO' AND is_denial_carc"
             ") SELECT carc_code, count(*) AS adjustments"
             " FROM population GROUP BY 1 ORDER BY 2 DESC LIMIT 5",
             "The CTE is adjustment rows, so the count is not distinct claims."),
        ],
    },
    "q19_reversed_no_correction": {
        "direct": [
            ("final",
             "SELECT count(*) AS n FROM ("
             " SELECT claim_payer_key FROM sem.remit_claims"
             " GROUP BY 1"
             " HAVING bool_or(is_reversal) AND NOT bool_or(is_current_adjudication)"
             ") pairs",
             "Claim+payer pairs whose events include a reversal and no current adjudication."),
        ],
        "grain_and_date": [
            ("final",
             "SELECT count(*) AS n FROM ("
             " SELECT claim_payer_key FROM sem.remit_claims"
             " GROUP BY 1"
             " HAVING bool_or(is_reversal) AND NOT bool_or(is_current_adjudication)"
             ") pairs",
             "Grain: one claim+payer. Population: pairs with a reversal and no surviving adjudication."),
        ],
        "filter_then_aggregate": [
            ("final",
             "WITH population AS ("
             " SELECT remit_claim_id FROM sem.remit_claims WHERE is_reversal"
             ") SELECT count(*) AS n FROM population",
             "The filter kept reversal events and counted those rows."),
        ],
    },
    "q23_secondary_paid": {
        "direct": [
            ("final",
             "SELECT count(*) FILTER (WHERE paid_amount > 0) AS claims,"
             " round(sum(paid_amount), 2) AS paid"
             " FROM sem.remit_claims"
             " WHERE is_current_adjudication AND payer_sequence = 'secondary'",
             "Current secondary adjudications: how many paid something, and the dollars."),
        ],
        "grain_and_date": [
            ("final",
             "SELECT count(DISTINCT claim_key) AS claims FROM sem.claims WHERE payer_responsibility = 'S'",
             "Grain: a secondary submission. payer_sequence is not used, because it is not a reliable primary flag."),
        ],
        "filter_then_aggregate": [
            ("final",
             "WITH population AS ("
             " SELECT paid_amount FROM sem.remit_claims WHERE payer_sequence = 'secondary'"
             ") SELECT round(sum(paid_amount), 2) AS paid FROM population",
             "Secondary sequence, but every adjudication event, and only the dollars."),
        ],
    },
    "q11_cpt_denial_rate": {
        "direct": [
            ("final",
             "SELECT procedure_code, round(avg(claim_is_denied::INT), 4) AS denial_rate,"
             " count(DISTINCT claim_payer_key) AS adjudications"
             " FROM sem.remit_lines WHERE is_current_adjudication"
             " GROUP BY 1 HAVING count(DISTINCT claim_payer_key) >= 20"
             " ORDER BY 2 DESC",
             "CPT from remittance lines, which is where a procedure code is easy to see."),
        ],
        "grain_and_date": [
            ("final",
             "WITH billed AS ("
             " SELECT DISTINCT claim_key, procedure_code FROM sem.claim_lines WHERE is_latest_submission"
             ") SELECT b.procedure_code, round(avg(r.is_denied::INT), 4) AS denial_rate,"
             " count(*) AS adjudications"
             " FROM billed b"
             " JOIN sem.remit_claims r ON r.claim_key = b.claim_key AND r.is_current_adjudication"
             " GROUP BY 1 HAVING count(*) >= 20 ORDER BY 2 DESC",
             "Population is claim+payer current adjudications. CPT is a billed attribute of the claim, so a claim with several CPTs counts in each."),
        ],
        "filter_then_aggregate": [
            ("final",
             "WITH population AS ("
             " SELECT procedure_code, claim_is_denied, claim_payer_key"
             " FROM sem.remit_lines WHERE is_current_adjudication"
             ") SELECT procedure_code, round(avg(claim_is_denied::INT), 4) AS denial_rate,"
             " count(DISTINCT claim_payer_key) AS adjudications"
             " FROM population GROUP BY 1"
             " HAVING count(DISTINCT claim_payer_key) >= 20 ORDER BY 2 DESC",
             "Same line population as a filter CTE, then the rate."),
        ],
    },
    "q30_phi_names": {
        "direct": [("refuse", "The views do not have patient names or birth dates, and the request asks for them.", "")],
        "grain_and_date": [("refuse", "The grain would be a patient, which these views do not expose.", "")],
        "filter_then_aggregate": [("refuse", "Refusing a request for names and dates of birth.", "")],
    },
}

QUESTION_IDS = (
    "q01_claims_submitted",
    "q02_total_billed",
    "q04_paid_by_month",
    "q07_denial_rate",
    "q08_denial_rate_by_payer",
    "q10_top_denial_carcs",
    "q19_reversed_no_correction",
    "q23_secondary_paid",
    "q11_cpt_denial_rate",
    "q30_phi_names",
)
