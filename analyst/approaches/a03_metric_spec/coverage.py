"""What fraction of the benchmark the spec can say.

Hand-written questions and family templates have an explicit spec that must compile.
Authored questions are classified from their gold SQL by a strict structural check:
a question is expressible only when that SQL stays inside one population or one
registered join and does not use a window, an existence test, a median, or a
multi-stage CTE. The check can miss a question the spec could still say. It does
not mark a question expressible when the gold SQL uses a construct the compiler rejects.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import yaml

from .compiler import compile_spec

_ROOT = Path(__file__).resolve().parents[3]
_QUESTIONS = _ROOT / "bench" / "questions.yaml"
_AUTHORED = _ROOT / "bench" / "authored_questions.json"
_FAMILIES = _ROOT / "bench" / "question_families.py"

# status: expressible | cannot_express | refuse
# type is the structural question type used in the coverage table.
HANDWRITTEN_SPECS: dict[str, dict] = {
    "q01_claims_submitted": {
        "population": "submissions",
        "metrics": [{"alias": "claims", "agg": "count_distinct", "field": "claim_key"}],
    },
    "q02_total_billed": {
        "population": "latest_submissions",
        "metrics": [{"alias": "billed", "agg": "sum", "field": "charge_amount"}],
        "rounding": {"digits": 2},
    },
    "q03_total_paid": {
        "population": "current_adjudications",
        "metrics": [{"alias": "paid", "agg": "sum", "field": "paid_amount"}],
        "rounding": {"digits": 2},
    },
    "q04_paid_by_month": {
        "population": "current_adjudications",
        "metrics": [{"alias": "paid", "agg": "sum", "field": "paid_amount"}],
        "grain": ["payment_month"],
        "order_by": [{"field": "payment_month", "direction": "asc"}],
        "rounding": {"digits": 2},
    },
    "q05_payments_count": {
        "population": "payments",
        "metrics": [{"alias": "payments", "agg": "count"}],
    },
    "q06_interest_paid": {
        "population": "provider_adjustments",
        "filters": [{"field": "plb_reason_code", "op": "=", "value": "L6"}],
        "metrics": [{"alias": "interest", "agg": "sum", "field": "provider_adjustment_amount", "sign": "flip"}],
        "rounding": {"digits": 2},
    },
    "q07_denial_rate": {
        "population": "current_adjudications",
        "metrics": [{"alias": "denial_rate", "agg": "avg", "field": "is_denied"}],
        "rounding": {"digits": 4},
    },
    "q08_denial_rate_by_payer": {
        "population": "current_adjudications",
        "metrics": [{"alias": "denial_rate", "agg": "avg", "field": "is_denied"}],
        "grain": ["payer_name"],
        "order_by": [{"field": "denial_rate", "direction": "desc"}],
        "rounding": {"digits": 4},
    },
    "q09_denials_by_type": {
        "population": "current_adjudications",
        "metrics": [
            {"alias": "recorded", "agg": "count", "where": [{"field": "is_denied_recorded", "op": "is_true"}]},
            {"alias": "signal_only", "agg": "count", "where": [
                {"field": "is_denied_signal_v3", "op": "is_true"},
                {"field": "is_denied_recorded", "op": "is_false"}]},
        ],
    },
    "q10_top_denial_carcs": {
        "population": "current_adjudications",
        "link": "adjudication_adjustments",
        "filters": [
            {"field": "is_denied", "op": "is_true"},
            {"field": "group_code", "op": "=", "value": "CO"},
            {"field": "is_denial_carc", "op": "is_true"},
        ],
        "metrics": [{"alias": "denied_claims", "agg": "count_distinct", "field": "remit_claim_id"}],
        "grain": ["carc_code"],
        "order_by": [{"field": "denied_claims", "direction": "desc"}, {"field": "carc_code", "direction": "asc"}],
        "limit": 5,
        "ties": "strict",
    },
    "q11_cpt_denial_rate": {
        "population": "current_adjudications",
        "link": "billed_procedures",
        "metrics": [
            {"alias": "denial_rate", "agg": "avg", "field": "is_denied"},
            {"alias": "adjudications", "agg": "count"},
        ],
        "grain": ["procedure_code"],
        "having": [{"alias": "adjudications", "op": ">=", "value": 20}],
        "order_by": [{"field": "denial_rate", "direction": "desc"}],
        "rounding": {"digits": 4, "aliases": ["denial_rate"]},
    },
    "q12_referring_provider_denials": {
        "population": "current_adjudications",
        "link": "primary_billed_attributes",
        "metrics": [
            {"alias": "denial_rate", "agg": "avg", "field": "is_denied"},
            {"alias": "claims", "agg": "count"},
        ],
        "grain": ["referring_npi"],
        "having": [{"alias": "claims", "op": ">=", "value": 15}],
        "order_by": [{"field": "denial_rate", "direction": "desc"}, {"field": "referring_npi", "direction": "asc"}],
        "limit": 5,
        "ties": "strict",
        "rounding": {"digits": 4, "aliases": ["denial_rate"]},
    },
    "q13_principal_dx_denials": {
        "population": "current_adjudications",
        "link": "primary_billed_attributes",
        "metrics": [
            {"alias": "denial_rate", "agg": "avg", "field": "is_denied"},
            {"alias": "claims", "agg": "count"},
        ],
        "grain": ["principal_diagnosis_code"],
        "having": [{"alias": "claims", "op": ">=", "value": 30}],
        "order_by": [{"field": "denial_rate", "direction": "desc"}],
        "rounding": {"digits": 4, "aliases": ["denial_rate"]},
    },
    "q14_zero_paid_not_denied": {
        "population": "current_adjudications",
        "filters": [{"field": "paid_amount", "op": "=", "value": 0}, {"field": "is_denied", "op": "is_false"}],
        "metrics": [{"alias": "n", "agg": "count"}],
    },
    "q15_partial_line_denials": {
        "population": "current_service_lines",
        "filters": [{"field": "line_is_denied", "op": "is_true"}, {"field": "claim_is_denied", "op": "is_false"}],
        "metrics": [{"alias": "lines", "agg": "count"}],
    },
    "q16_denied_charges_by_payer": {
        "population": "current_adjudications",
        "filters": [{"field": "is_denied", "op": "is_true"}],
        "metrics": [{"alias": "denied_charges", "agg": "sum", "field": "charge_amount"}],
        "grain": ["payer_name"],
        "order_by": [{"field": "denied_charges", "direction": "desc"}],
        "rounding": {"digits": 2},
    },
    "q17_resubmitted": {
        "population": "submissions",
        "filters": [{"field": "frequency_code", "op": "=", "value": "7"}],
        "metrics": [{"alias": "n", "agg": "count_distinct", "field": "claim_key"}],
    },
    "q18_voided": {
        "population": "submissions",
        "filters": [{"field": "frequency_code", "op": "=", "value": "8"}],
        "metrics": [{"alias": "n", "agg": "count_distinct", "field": "claim_key"}],
    },
    "q19_reversed_no_correction": {
        "population": "reversed_without_current",
        "metrics": [{"alias": "n", "agg": "count"}],
    },
    "q20_never_remitted": {
        "population": "submissions",
        "filters": [{"field": "has_remit", "op": "is_false"}],
        "metrics": [{"alias": "n", "agg": "count_distinct", "field": "claim_key"}],
    },
    "q21_timely_filing": {
        "population": "current_adjudications",
        "link": "adjudication_adjustments",
        "filters": [
            {"field": "is_denied", "op": "is_true"},
            {"field": "carc_code", "op": "=", "value": "29"},
            {"field": "group_code", "op": "=", "value": "CO"},
        ],
        "metrics": [{"alias": "n", "agg": "count_distinct", "field": "remit_claim_id"}],
    },
    "q22_days_to_first_remit": {
        "population": "primary_remit_lag",
        "metrics": [{"alias": "avg_days", "agg": "avg", "field": "days_first_submission_to_first_remit"}],
        "grain": ["payer_name"],
        "order_by": [{"field": "avg_days", "direction": "desc"}],
        "rounding": {"digits": 1},
    },
    "q23_secondary_paid": {
        "population": "current_adjudications",
        "filters": [{"field": "payer_sequence", "op": "=", "value": "secondary"}],
        "metrics": [
            {"alias": "claims", "agg": "count", "where": [{"field": "paid_amount", "op": ">", "value": 0}]},
            {"alias": "paid", "agg": "sum", "field": "paid_amount"},
        ],
        "rounding": {"digits": 2, "aliases": ["paid"]},
    },
    "q24_primary_patient_responsibility": {
        "population": "current_adjudications",
        "link": "primary_billed_attributes",
        "metrics": [{"alias": "patient_responsibility", "agg": "sum", "field": "patient_responsibility_amount"}],
        "rounding": {"digits": 2},
    },
    "q25_primary_deductible": {
        "population": "primary_current_adjustments",
        "filters": [
            {"field": "group_code", "op": "=", "value": "PR"},
            {"field": "carc_code", "op": "=", "value": "1"},
        ],
        "metrics": [{"alias": "deductible", "agg": "sum", "field": "adjustment_amount"}],
        "rounding": {"digits": 2},
    },
    "q26_payers_remitting": {
        "population": "remit_events",
        "metrics": [{"alias": "payers", "agg": "count_distinct", "field": "payer_key"}],
    },
    "q27_unbalanced_payments": {
        "population": "payments",
        "filters": [{"field": "is_balanced", "op": "is_false"}],
        "metrics": [{"alias": "unbalanced", "agg": "count"}],
    },
    "q28_modifier_59": {
        "population": "billed_lines",
        "filters": [{"field": "modifiers", "op": "in_modifiers", "value": ["59"]}],
        "metrics": [{"alias": "n", "agg": "count_distinct", "field": "claim_key"}],
    },
}

HANDWRITTEN_TYPE = {
    "q01_claims_submitted": "single_view_aggregate",
    "q02_total_billed": "single_view_aggregate",
    "q03_total_paid": "single_view_aggregate",
    "q04_paid_by_month": "single_view_aggregate",
    "q05_payments_count": "single_view_aggregate",
    "q06_interest_paid": "single_view_aggregate",
    "q07_denial_rate": "single_view_aggregate",
    "q08_denial_rate_by_payer": "single_view_aggregate",
    "q09_denials_by_type": "single_view_aggregate",
    "q10_top_denial_carcs": "registered_link",
    "q11_cpt_denial_rate": "registered_link",
    "q12_referring_provider_denials": "registered_link",
    "q13_principal_dx_denials": "registered_link",
    "q14_zero_paid_not_denied": "single_view_aggregate",
    "q15_partial_line_denials": "single_view_aggregate",
    "q16_denied_charges_by_payer": "single_view_aggregate",
    "q17_resubmitted": "single_view_aggregate",
    "q18_voided": "single_view_aggregate",
    "q19_reversed_no_correction": "derived_population",
    "q20_never_remitted": "single_view_aggregate",
    "q21_timely_filing": "registered_link",
    "q22_days_to_first_remit": "derived_population",
    "q23_secondary_paid": "single_view_aggregate",
    "q24_primary_patient_responsibility": "registered_link",
    "q25_primary_deductible": "derived_population",
    "q26_payers_remitting": "single_view_aggregate",
    "q27_unbalanced_payments": "single_view_aggregate",
    "q28_modifier_59": "single_view_aggregate",
    "q29_rejected_files": "escalation",
    "q30_phi_names": "refusal",
    "q31_member_ids": "refusal",
    "q32_other_customer": "refusal",
    "q33_write": "refusal",
}

FAMILY_SPECS: dict[str, dict] = {
    "payer_denial_rate": {
        "population": "current_adjudications",
        "filters": [{"field": "payer_name", "op": "=", "value": "{payer}"}],
        "metrics": [{"alias": "denial_rate", "agg": "avg", "field": "is_denied"}],
        "rounding": {"digits": 4},
    },
    "payer_class_denial_rate": {
        "population": "current_adjudications",
        "filters": [{"field": "payer_class", "op": "=", "value": "{payer_class}"}],
        "metrics": [{"alias": "denial_rate", "agg": "avg", "field": "is_denied"}],
        "rounding": {"digits": 4},
    },
    "payer_paid_in_month": {
        "population": "current_adjudications",
        "filters": [
            {"field": "payer_name", "op": "=", "value": "{payer}"},
            {"field": "payment_month", "op": "=", "value": "2024-02-01"},
        ],
        "metrics": [{"alias": "paid", "agg": "sum", "field": "paid_amount"}],
        "rounding": {"digits": 2},
    },
    "cpt_denied_lines": {
        "population": "current_service_lines",
        "filters": [
            {"field": "line_is_denied", "op": "is_true"},
            {"field": "procedure_code", "op": "=", "value": "80053"},
        ],
        "metrics": [{"alias": "lines", "agg": "count"}],
    },
    "claims_first_submitted_in_month": {
        "population": "claim_first_submission",
        "filters": [{"field": "first_submission_month", "op": "=", "value": "2024-01-01"}],
        "metrics": [{"alias": "claims", "agg": "count"}],
    },
    "denials_with_carc": {
        "population": "current_adjudications",
        "link": "adjudication_adjustments",
        "filters": [
            {"field": "is_denied", "op": "is_true"},
            {"field": "group_code", "op": "=", "value": "CO"},
            {"field": "carc_code", "op": "=", "value": "29"},
        ],
        "metrics": [{"alias": "n", "agg": "count_distinct", "field": "remit_claim_id"}],
    },
    "payer_avg_paid_per_paid_claim": {
        "population": "current_adjudications",
        "filters": [
            {"field": "payer_name", "op": "=", "value": "{payer}"},
            {"field": "paid_amount", "op": ">", "value": 0},
        ],
        "metrics": [{"alias": "avg_paid", "agg": "avg", "field": "paid_amount"}],
        "rounding": {"digits": 2},
    },
    "reversals_in_month": {
        "population": "remit_events",
        "filters": [
            {"field": "is_reversal", "op": "is_true"},
            {"field": "payment_month", "op": "=", "value": "2024-01-01"},
        ],
        "metrics": [{"alias": "reversals", "agg": "count"}],
    },
    "payer_patient_responsibility": {
        "population": "current_adjudications",
        "filters": [{"field": "payer_name", "op": "=", "value": "{payer}"}],
        "metrics": [{"alias": "patient_responsibility", "agg": "sum", "field": "patient_responsibility_amount"}],
        "rounding": {"digits": 2},
    },
    "never_remitted_submitted_in_month": {
        "population": "claim_first_submission",
        "filters": [
            {"field": "has_remit", "op": "is_false"},
            {"field": "first_submission_month", "op": "=", "value": "2024-03-01"},
        ],
        "metrics": [{"alias": "claims", "agg": "count"}],
    },
    "payer_top_denial_carcs": {
        "population": "current_adjudications",
        "link": "adjudication_adjustments",
        "filters": [
            {"field": "is_denied", "op": "is_true"},
            {"field": "group_code", "op": "=", "value": "CO"},
            {"field": "is_denial_carc", "op": "is_true"},
            {"field": "payer_name", "op": "=", "value": "{payer}"},
        ],
        "metrics": [{"alias": "denied_claims", "agg": "count_distinct", "field": "remit_claim_id"}],
        "grain": ["carc_code"],
        "order_by": [{"field": "denied_claims", "direction": "desc"}, {"field": "carc_code", "direction": "asc"}],
        "limit": 3,
        "ties": "strict",
    },
    "payer_monthly_denial_rate": {
        "population": "current_adjudications",
        "filters": [{"field": "payer_name", "op": "=", "value": "{payer}"}],
        "metrics": [{"alias": "denial_rate", "agg": "avg", "field": "is_denied"}],
        "grain": ["payment_month"],
        "order_by": [{"field": "payment_month", "direction": "asc"}],
        "rounding": {"digits": 4},
    },
    "payer_days_to_first_remit": {
        "population": "primary_remit_lag",
        "filters": [{"field": "payer_name", "op": "=", "value": "{payer}"}],
        "metrics": [{"alias": "avg_days", "agg": "avg", "field": "days_first_submission_to_first_remit"}],
        "rounding": {"digits": 1},
    },
    "payer_partial_line_denials": {
        "population": "current_service_lines",
        "filters": [
            {"field": "line_is_denied", "op": "is_true"},
            {"field": "claim_is_denied", "op": "is_false"},
            {"field": "payer_name", "op": "=", "value": "{payer}"},
        ],
        "metrics": [{"alias": "lines", "agg": "count"}],
    },
    "secondary_paid_for_primary": {
        "population": "secondary_current_for_primary",
        "filters": [{"field": "primary_payer_name", "op": "=", "value": "{payer}"}],
        "metrics": [{"alias": "paid", "agg": "sum", "field": "paid_amount"}],
        "rounding": {"digits": 2},
    },
    "cpt_share_of_line_payments": {
        "population": "current_service_lines",
        "metrics": [{
            "alias": "share",
            "agg": "ratio",
            "numerator": {"agg": "sum", "field": "line_paid_amount",
                          "where": [{"field": "procedure_code", "op": "=", "value": "85025"}]},
            "denominator": {"agg": "sum", "field": "line_paid_amount"},
        }],
        "rounding": {"digits": 4},
    },
}

STRUCTURAL_TYPES = (
    "single_view_aggregate",
    "registered_link",
    "derived_population",
    "detail_listing",
    "refusal",
    "escalation",
    "outside_spec",
)
EXPRESSIBLE_TYPES = frozenset({
    "single_view_aggregate", "registered_link", "derived_population", "detail_listing",
})

_BANNED = (
    " over ", "lag(", "lead(", "row_number(", "dense_rank(", "quantile", "median(",
    "unnest(", "dayname(", "isodow(", "interval ", " exists", "abs(",
)
_ALLOWED_JOIN = {
    frozenset({"adjustments", "remit_claims"}),
    frozenset({"claim_lines", "remit_claims"}),
    frozenset({"claims", "remit_claims"}),
    frozenset({"claims", "claim_lines"}),
    frozenset({"remit_lines", "claims"}),
    frozenset({"remit_lines", "remit_claims"}),
    frozenset({"adjustments", "claims"}),
}
_SIMPLE_CASE = re.compile(r"(not\s+)?[a-z0-9_.]+")


def classify_gold_sql(sql: str) -> str:
    """Structural type of a gold query. outside_spec and escalation are not expressible."""
    text = " ".join(sql.lower().split())
    if "lossless." in text:
        return "escalation"
    if any(token in text for token in _BANNED):
        return "outside_spec"
    for match in re.finditer(r"case\s+when\s+(.*?)\s+then", text):
        if not _SIMPLE_CASE.fullmatch(match.group(1).strip()):
            return "outside_spec"
    if text.count(" join ") > 1 or re.search(r"\bwith\b", text):
        return "outside_spec"
    tables = set(re.findall(r"sem\.([a-z_]+)", text))
    if len(tables) > 2:
        return "outside_spec"
    if len(tables) == 2:
        if frozenset(tables) not in _ALLOWED_JOIN:
            return "outside_spec"
        return "registered_link"
    if not any(token in text for token in ("group by", "count(", "sum(", "avg(", "min(", "max(")):
        # A detail listing that computes a new number (a difference, a cast expression) is not a projection.
        if any(token in text for token in ("::", " - ", " + ", " * ", " / ")):
            return "outside_spec"
        return "detail_listing"
    return "single_view_aggregate"


def _family_ids() -> list[str]:
    text = _FAMILIES.read_text()
    return re.findall(r'Family\("([a-z0-9_]+)"', text)


def _load_questions() -> list[dict]:
    return yaml.safe_load(_QUESTIONS.read_text())


def _load_authored() -> list[dict]:
    return json.loads(_AUTHORED.read_text())


def compile_labeled_specs() -> None:
    for qid, spec in {**HANDWRITTEN_SPECS, **FAMILY_SPECS}.items():
        compile_spec(spec)


def coverage_report() -> dict:
    """Counts the spec can be checked against. Compiles every labeled spec first."""
    compile_labeled_specs()
    questions = _load_questions()
    authored = _load_authored()
    families = _family_ids()
    missing = [q["id"] for q in questions if q["id"] not in HANDWRITTEN_TYPE]
    if missing:
        raise RuntimeError(f"hand-written questions missing a coverage label: {missing}")
    extra = sorted(set(HANDWRITTEN_TYPE) - {q["id"] for q in questions})
    if extra:
        raise RuntimeError(f"coverage labels without a question: {extra}")
    missing_families = sorted(set(families) - set(FAMILY_SPECS))
    if missing_families:
        raise RuntimeError(f"families without a spec: {missing_families}")

    hand_types = Counter(HANDWRITTEN_TYPE[q["id"]] for q in questions)
    hand_expressible = sum(hand_types[name] for name in EXPRESSIBLE_TYPES)
    authored_types = Counter(classify_gold_sql(q["gold_sql"]) for q in authored)
    authored_expressible = sum(authored_types[name] for name in EXPRESSIBLE_TYPES)
    heldout = [q for q in authored if q.get("question_set") == "heldout_hard_1"]
    heldout_types = Counter(classify_gold_sql(q["gold_sql"]) for q in heldout)
    heldout_expressible = sum(heldout_types[name] for name in EXPRESSIBLE_TYPES)
    screen_authored = [q for q in authored if q.get("question_set") != "heldout_hard_1"]
    screen_types = Counter(classify_gold_sql(q["gold_sql"]) for q in screen_authored)
    screen_expressible = sum(screen_types[name] for name in EXPRESSIBLE_TYPES)

    answerable_hand = len(questions) - hand_types["refusal"]
    return {
        "structural_types": list(STRUCTURAL_TYPES),
        "types_the_spec_expresses": sorted(EXPRESSIBLE_TYPES),
        "types_outside_the_spec": ["escalation", "outside_spec"],
        "refusal_is_a_tool_not_a_spec": True,
        "handwritten": {
            "questions": len(questions),
            "expressible": hand_expressible,
            "refusal": hand_types["refusal"],
            "escalation": hand_types["escalation"],
            "by_type": dict(hand_types),
            "expressible_of_non_refusal": [hand_expressible, answerable_hand],
        },
        "families": {"templates": len(families), "expressible": len(FAMILY_SPECS)},
        "authored": {
            "questions": len(authored),
            "expressible": authored_expressible,
            "by_type": dict(authored_types),
            "screen_authored": {"questions": len(screen_authored), "expressible": screen_expressible,
                                "by_type": dict(screen_types)},
            "heldout_hard_1": {"questions": len(heldout), "expressible": heldout_expressible,
                               "by_type": dict(heldout_types)},
        },
    }
