"""Parametric question families: one template, many concrete questions drawn from the dataset itself.

Each family has a question template (two phrasings, alternated), gold SQL over sem.* and an independent
answer-key query over the generator's ledger (bench/truth_db.py, schema `ledger`). Parameters (payers, months,
CPTs, denial codes) come from the ledger so every question is answerable and non-trivial on that dataset; the
self-test still checks gold == answer key for each one. Payer names: the semantic layer shows `Title Case`
(ref.payer_alias), the ledger keeps the payer's name as sent in upper case.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb

MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December"]


@dataclass(frozen=True)
class Family:
    family_id: str
    category: str
    difficulty: str
    phrasings: tuple
    gold_sql: str
    answer_key_sql: str
    parameter_sql: str             # over the ledger: rows of parameter values, column names = template fields
    compare: dict


def _sql_text(value: str) -> str:
    return value.replace("'", "''")


FAMILIES = [
    Family("payer_denial_rate", "denials", "easy",
           ("What is the denial rate for {payer_title}? Count each claim once, using its current adjudication.",
            "Using current adjudications, what share of claims did {payer_title} deny?"),
           "SELECT round(avg(is_denied::INT), 4) FROM sem.remit_claims WHERE is_current_adjudication AND payer_name = '{payer_title_sql}'",
           "SELECT round(avg(denied::INT), 4) FROM ledger.adjudications WHERE remitted_current AND payer = '{payer_sql}'",
           "SELECT payer FROM ledger.adjudications WHERE remitted_current GROUP BY 1 HAVING count(*) >= 50 ORDER BY 1",
           {"tol": 0.0005, "percent_cols": [0]}),
    Family("payer_class_denial_rate", "denials", "medium",
           ("What is the denial rate for {payer_class_words} payers, counting each claim once per payer with its current adjudication?",
            "Across all {payer_class_words} payers, what fraction of current claim adjudications are denials?"),
           "SELECT round(avg(is_denied::INT), 4) FROM sem.remit_claims WHERE is_current_adjudication AND payer_class = '{payer_class}'",
           "SELECT round(avg(denied::INT), 4) FROM ledger.adjudications WHERE remitted_current AND payer_class = '{payer_class}'",
           "SELECT payer_class FROM ledger.adjudications WHERE remitted_current AND payer_class IS NOT NULL GROUP BY 1 HAVING count(*) >= 50 ORDER BY 1",
           {"tol": 0.0005, "percent_cols": [0]}),
    Family("payer_paid_in_month", "money", "medium",
           ("How much did {payer_title} pay us in {month_words}, based on current adjudications with a payment date in that month?",
            "What was {payer_title}'s total paid amount for {month_words} (current adjudications, by payment date)?"),
           "SELECT coalesce(round(sum(paid_amount), 2), 0) FROM sem.remit_claims WHERE is_current_adjudication AND payer_name = '{payer_title_sql}' AND payment_month = DATE '{month}'",
           "SELECT coalesce(round(sum(e.paid), 2), 0) FROM ledger.events e JOIN ledger.adjudications a USING (pcn, payer_id) WHERE e.is_current AND a.payer = '{payer_sql}' AND date_trunc('month', e.event_date::DATE) = DATE '{month}'",
           "SELECT a.payer, strftime(date_trunc('month', e.event_date::DATE), '%Y-%m-%d') AS month FROM ledger.events e JOIN ledger.adjudications a USING (pcn, payer_id) WHERE e.is_current GROUP BY 1, 2 HAVING count(*) >= 30 AND sum(e.paid) > 0 ORDER BY 1, 2",
           {"tol": 0.01}),
    Family("cpt_denied_lines", "lines", "medium",
           ("How many {cpt} service lines were denied in current adjudications?",
            "Count the denied service lines billed as CPT {cpt}, using each claim's current adjudication."),
           "SELECT count(*) FROM sem.remit_lines WHERE is_current_adjudication AND line_is_denied AND procedure_code = '{cpt}'",
           "SELECT count(*) FROM ledger.event_lines l JOIN ledger.events e USING (pcn, payer_id, event_number) WHERE e.is_current AND l.denied AND l.cpt = '{cpt}'",
           "SELECT l.cpt FROM ledger.event_lines l JOIN ledger.events e USING (pcn, payer_id, event_number) WHERE e.is_current AND l.denied GROUP BY 1 HAVING count(*) >= 10 ORDER BY count(*) DESC LIMIT 10",
           {}),
    Family("claims_first_submitted_in_month", "volume", "medium",
           ("How many claims did we first submit in {month_words}?",
            "In {month_words}, how many new claims (by first submission date) went out?"),
           "SELECT count(*) FROM (SELECT claim_key, min(submission_date) AS first_submission FROM sem.claims GROUP BY 1) WHERE date_trunc('month', first_submission) = DATE '{month}'",
           "SELECT count(*) FROM ledger.claims WHERE date_trunc('month', submitted::DATE) = DATE '{month}'",
           "SELECT strftime(date_trunc('month', submitted::DATE), '%Y-%m-%d') AS month FROM ledger.claims GROUP BY 1 HAVING count(*) >= 100 ORDER BY 1",
           {}),
    Family("denials_with_carc", "denials", "hard",
           ("How many current claim+payer adjudications are denied with reason code {carc} under group CO?",
            "Using current adjudications, count the denied claims (per payer) that carry a CO-{carc} adjustment."),
           "SELECT count(DISTINCT r.remit_claim_id) FROM sem.remit_claims r JOIN sem.adjustments a ON a.remit_claim_id = r.remit_claim_id WHERE r.is_current_adjudication AND r.is_denied AND a.group_code = 'CO' AND a.carc_code = '{carc}'",
           "SELECT count(*) FROM ledger.adjudications a WHERE a.remitted_current AND a.denied AND EXISTS (SELECT 1 FROM ledger.event_adjustments x JOIN ledger.events e USING (pcn, payer_id, event_number) WHERE e.is_current AND x.pcn = a.pcn AND x.payer_id = a.payer_id AND x.group_code = 'CO' AND x.carc = '{carc}')",
           "SELECT x.carc FROM ledger.event_adjustments x JOIN ledger.events e USING (pcn, payer_id, event_number) JOIN ledger.adjudications a USING (pcn, payer_id) WHERE e.is_current AND a.denied AND x.group_code = 'CO' GROUP BY 1 HAVING count(*) >= 10 ORDER BY count(*) DESC LIMIT 6",
           {}),
    Family("payer_avg_paid_per_paid_claim", "money", "medium",
           ("What is the average amount {payer_title} paid per claim, counting only current adjudications that paid more than $0?",
            "On claims {payer_title} actually paid (current adjudication, paid > 0), what is the average payment?"),
           "SELECT round(avg(paid_amount), 2) FROM sem.remit_claims WHERE is_current_adjudication AND paid_amount > 0 AND payer_name = '{payer_title_sql}'",
           "SELECT round(avg(paid_current), 2) FROM ledger.adjudications WHERE remitted_current AND paid_current > 0 AND payer = '{payer_sql}'",
           "SELECT payer FROM ledger.adjudications WHERE remitted_current AND paid_current > 0 GROUP BY 1 HAVING count(*) >= 50 ORDER BY 1",
           {"tol": 0.01}),
    Family("reversals_in_month", "rework", "medium",
           ("How many claim reversals (claim status 22) did payers send us in {month_words}, by payment date?",
            "Count the 835 reversal events dated in {month_words}."),
           "SELECT count(*) FROM sem.remit_claims WHERE is_reversal AND payment_month = DATE '{month}'",
           "SELECT count(*) FROM ledger.events WHERE status = '22' AND date_trunc('month', event_date::DATE) = DATE '{month}'",
           "SELECT strftime(date_trunc('month', event_date::DATE), '%Y-%m-%d') AS month FROM ledger.events WHERE status = '22' GROUP BY 1 HAVING count(*) >= 5 ORDER BY 1",
           {}),
    Family("payer_patient_responsibility", "patient", "medium",
           ("How much patient responsibility did {payer_title} assign on its current adjudications?",
            "What is the total patient responsibility amount on {payer_title}'s current claim adjudications?"),
           "SELECT coalesce(round(sum(patient_responsibility_amount), 2), 0) FROM sem.remit_claims WHERE is_current_adjudication AND payer_name = '{payer_title_sql}'",
           "SELECT coalesce(round(sum(e.patient_responsibility), 2), 0) FROM ledger.events e JOIN ledger.adjudications a USING (pcn, payer_id) WHERE e.is_current AND a.payer = '{payer_sql}'",
           "SELECT payer FROM ledger.adjudications a JOIN ledger.events e USING (pcn, payer_id) WHERE e.is_current AND e.patient_responsibility > 0 GROUP BY 1 HAVING count(*) >= 20 ORDER BY 1",
           {"tol": 0.01}),
    Family("never_remitted_submitted_in_month", "rework", "hard",
           ("Of the claims first submitted in {month_words}, how many have never received any 835 remittance?",
            "How many claims we first sent in {month_words} still have no remittance at all?"),
           "SELECT count(*) FROM (SELECT claim_key, min(submission_date) AS first_submission, bool_or(has_remit) AS remitted FROM sem.claims GROUP BY 1) WHERE NOT remitted AND date_trunc('month', first_submission) = DATE '{month}'",
           "SELECT count(*) FROM ledger.claims WHERE NOT has_any_remit AND date_trunc('month', submitted::DATE) = DATE '{month}'",
           "SELECT strftime(date_trunc('month', submitted::DATE), '%Y-%m-%d') AS month FROM ledger.claims WHERE NOT has_any_remit GROUP BY 1 HAVING count(*) >= 10 ORDER BY 1",
           {}),
    Family("payer_top_denial_carcs", "denials", "hard",
           ("For {payer_title}, which 3 reviewed denial reason codes (group CO) appear on the most denied claims? Use current adjudications and count each denied claim once per code.",
            "What are {payer_title}'s top 3 denial CARCs (CO group, reviewed denial reasons) by number of denied claims, based on current adjudications?"),
           "SELECT a.carc_code, count(DISTINCT a.remit_claim_id) AS denied_claims FROM sem.adjustments a JOIN sem.remit_claims r ON r.remit_claim_id = a.remit_claim_id WHERE r.is_current_adjudication AND r.is_denied AND a.group_code = 'CO' AND a.is_denial_carc AND r.payer_name = '{payer_title_sql}' GROUP BY 1 ORDER BY 2 DESC, 1",
           "SELECT x.carc, count(DISTINCT x.pcn) AS denied_claims FROM ledger.event_adjustments x JOIN ledger.events e USING (pcn, payer_id, event_number) JOIN ledger.adjudications a USING (pcn, payer_id) WHERE e.is_current AND a.denied AND x.group_code = 'CO' AND x.carc IN (SELECT carc FROM ledger.denial_carcs) AND a.payer = '{payer_sql}' GROUP BY 1 ORDER BY 2 DESC, 1",
           "SELECT payer FROM ledger.adjudications WHERE remitted_current AND denied GROUP BY 1 HAVING count(*) >= 30 ORDER BY 1",
           {"order": "topn", "n": 3, "metric_col": 1, "optional_cols": [1]}),
    Family("payer_monthly_denial_rate", "denials", "medium",
           ("Show {payer_title}'s denial rate by payment month, using current adjudications.",
            "Month by month (by payment date), what share of {payer_title}'s current claim adjudications were denials?"),
           "SELECT payment_month, round(avg(is_denied::INT), 4) FROM sem.remit_claims WHERE is_current_adjudication AND payer_name = '{payer_title_sql}' GROUP BY 1 ORDER BY 1",
           "SELECT date_trunc('month', e.event_date::DATE)::DATE, round(avg(a.denied::INT), 4) FROM ledger.events e JOIN ledger.adjudications a USING (pcn, payer_id) WHERE e.is_current AND a.payer = '{payer_sql}' GROUP BY 1 ORDER BY 1",
           "SELECT payer FROM ledger.adjudications WHERE remitted_current GROUP BY 1 HAVING count(*) >= 100 ORDER BY 1",
           {"tol": 0.0005, "percent_cols": [1]}),
    Family("payer_days_to_first_remit", "payers", "hard",
           ("On average, how many days pass between a claim's first submission to {payer_title} as primary payer and the first 835 from {payer_title} for that claim (any status)? Round to one decimal.",
            "For claims billed to {payer_title} as the primary payer, what is the average number of days from first submission to {payer_title}'s first remittance? One decimal."),
           "WITH s AS (SELECT claim_key, payer_key, min(submission_date) AS first_sub FROM sem.claims WHERE payer_responsibility = 'P' GROUP BY 1, 2), r AS (SELECT claim_key, payer_key, payer_name, min(payment_date) AS first_remit FROM sem.remit_claims GROUP BY 1, 2, 3) SELECT round(avg(date_diff('day', s.first_sub, r.first_remit)), 1) FROM s JOIN r USING (claim_key, payer_key) WHERE r.payer_name = '{payer_title_sql}'",
           "WITH s AS (SELECT pcn, payer_id, min(submission_date::DATE) AS first_sub FROM ledger.submissions WHERE payer_sequence = 0 GROUP BY 1, 2), r AS (SELECT pcn, payer_id, min(event_date::DATE) AS first_remit FROM ledger.events GROUP BY 1, 2) SELECT round(avg(date_diff('day', s.first_sub, r.first_remit)), 1) FROM s JOIN r USING (pcn, payer_id) JOIN (SELECT DISTINCT pcn, payer_id, payer FROM ledger.adjudications) a USING (pcn, payer_id) WHERE a.payer = '{payer_sql}'",
           "SELECT payer FROM ledger.adjudications WHERE payer_sequence = 0 GROUP BY 1 HAVING count(*) >= 50 ORDER BY 1",
           {"tol": 0.05}),
    Family("payer_partial_line_denials", "lines", "hard",
           ("How many service lines did {payer_title} deny on claims whose current adjudication is not a denial overall (partial denials)?",
            "Count {payer_title}'s denied service lines on otherwise-paid claims (current adjudication not denied)."),
           "SELECT count(*) FROM sem.remit_lines WHERE is_current_adjudication AND line_is_denied AND NOT claim_is_denied AND payer_name = '{payer_title_sql}'",
           "SELECT count(*) FROM ledger.event_lines l JOIN ledger.events e USING (pcn, payer_id, event_number) JOIN ledger.adjudications a USING (pcn, payer_id) WHERE e.is_current AND l.denied AND NOT a.denied AND a.payer = '{payer_sql}'",
           "SELECT a.payer FROM ledger.event_lines l JOIN ledger.events e USING (pcn, payer_id, event_number) JOIN ledger.adjudications a USING (pcn, payer_id) WHERE e.is_current AND l.denied AND NOT a.denied GROUP BY 1 HAVING count(*) >= 10 ORDER BY 1",
           {}),
    Family("secondary_paid_for_primary", "cob", "hard",
           ("How much did secondary payers pay, on current adjudications, for claims whose primary payer is {payer_title}?",
            "On claims where {payer_title} was billed as primary, what did the secondary payers pay in total (current adjudications)?"),
           "WITH p AS (SELECT DISTINCT claim_key FROM sem.claims WHERE payer_responsibility = 'P' AND payer_name = '{payer_title_sql}') SELECT coalesce(round(sum(r.paid_amount), 2), 0) FROM sem.remit_claims r JOIN p USING (claim_key) WHERE r.is_current_adjudication AND r.payer_sequence = 'secondary'",
           "WITH names AS (SELECT DISTINCT payer_id, payer FROM ledger.adjudications), p AS (SELECT DISTINCT s.pcn FROM ledger.submissions s JOIN names USING (payer_id) WHERE s.payer_sequence = 0 AND names.payer = '{payer_sql}') SELECT coalesce(round(sum(a.paid_current), 2), 0) FROM ledger.adjudications a JOIN p USING (pcn) WHERE a.remitted_current AND a.payer_sequence = 1",
           "WITH names AS (SELECT DISTINCT payer_id, payer FROM ledger.adjudications) SELECT n.payer FROM ledger.adjudications a JOIN ledger.submissions s ON s.pcn = a.pcn AND s.payer_sequence = 0 JOIN names n ON n.payer_id = s.payer_id WHERE a.payer_sequence = 1 AND a.remitted_current AND a.paid_current > 0 GROUP BY 1 HAVING count(*) >= 5 ORDER BY 1",
           {"tol": 0.01}),
    Family("cpt_share_of_line_payments", "money", "hard",
           ("What share of all service-line payments (current adjudications) came from lines billed as CPT {cpt}?",
            "CPT {cpt} accounts for what fraction of total paid dollars across current service lines?"),
           "SELECT round(sum(line_paid_amount) FILTER (WHERE procedure_code = '{cpt}') / sum(line_paid_amount), 4) FROM sem.remit_lines WHERE is_current_adjudication",
           "SELECT round(sum(l.paid) FILTER (WHERE l.cpt = '{cpt}') / sum(l.paid), 4) FROM ledger.event_lines l JOIN ledger.events e USING (pcn, payer_id, event_number) WHERE e.is_current",
           "SELECT l.cpt FROM ledger.event_lines l JOIN ledger.events e USING (pcn, payer_id, event_number) WHERE e.is_current AND l.paid > 0 GROUP BY 1 ORDER BY sum(l.paid) DESC LIMIT 8",
           {"tol": 0.0005, "percent_cols": [0]}),
]


def _fill(template: str, params: dict) -> str:
    return template.format(**params)


def _parameters(row: dict) -> dict:
    p = dict(row)
    if "payer" in p:
        p.update(payer_title=p["payer"].title(), payer_sql=_sql_text(p["payer"]), payer_title_sql=_sql_text(p["payer"].title()))
    if "month" in p:
        d = date.fromisoformat(p["month"])
        p["month_words"] = f"{MONTH_NAMES[d.month - 1]} {d.year}"
    if "payer_class" in p:
        p["payer_class_words"] = p["payer_class"].replace("_", " ")
    return p


def build_questions(ledger_con: duckdb.DuckDBPyConnection, max_per_family: int = 12) -> list[dict]:
    """Concrete questions for one dataset. Ids are stable for a given dataset (family + parameter values)."""
    out = []
    for fam in FAMILIES:
        cur = ledger_con.execute(fam.parameter_sql)
        names = [d[0] for d in cur.description]
        rows = [dict(zip(names, r)) for r in cur.fetchall()][:max_per_family]
        for i, row in enumerate(rows):
            p = _parameters(row)
            key = "_".join(str(row[n]).lower().replace(" ", "-") for n in names)
            out.append(dict(id=f"f_{fam.family_id}__{key}", family=fam.family_id, category=fam.category,
                            difficulty=fam.difficulty, question=_fill(fam.phrasings[i % len(fam.phrasings)], p),
                            gold_sql=_fill(fam.gold_sql, p), answer_key_sql=_fill(fam.answer_key_sql, p),
                            compare=fam.compare))
    return out
