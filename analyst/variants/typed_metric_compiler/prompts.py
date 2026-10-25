"""Prompts and tool schemas for a later screen.

bench/eval_subagents.py builds its briefing from bench/agent_session.py. It does not import this
module yet. A screen wires the subagent to `parser_system()` and `PARSER_TOOLS` for the first
turn, and to `FALLBACK_SYSTEM` and `FALLBACK_TOOLS` only after the compiler returns cannot_express.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .spec import DATE_BASES, GRAINS

MODEL_PATH = Path(__file__).resolve().parents[3] / "claude" / "schema" / "03_semantic_model.yaml"

PARSER_TOOLS = [
    {
        "name": "submit_spec",
        "description": (
            "Translate the question into a typed spec. The server validates it against the metric "
            "catalog and writes the SQL. Do not write SQL, name a view, or invent a metric."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "enum": ["aggregate", "refuse", "cannot_express"]},
                "reason": {"type": "string",
                           "description": "Required for refuse and cannot_express. Say which population the catalog cannot state."},
                "metrics": {"type": "array", "items": {"type": "string"},
                            "description": "Catalog metric names, or cohort_claims for the size of a cohort population."},
                "group_by": {"type": "array", "items": {"anyOf": [
                    {"type": "string"},
                    {"type": "object", "properties": {
                        "field": {"type": "string"},
                        "grain": {"type": "string", "enum": list(GRAINS)}},
                     "required": ["field", "grain"]}]}},
                "filters": {"type": "array", "items": {"type": "object", "properties": {
                    "field": {"type": "string"},
                    "op": {"type": "string"},
                    "value": {}}, "required": ["field", "op"]}},
                "date_basis": {"type": "string", "enum": list(DATE_BASES)},
                "time_grain": {"type": "string", "enum": list(GRAINS),
                               "description": "Set only to group by a date column. Omit when grouping by payment_month or not grouping by time."},
                "time_start": {"type": "string", "description": "Inclusive ISO date. Cohort window or event-date filter."},
                "time_end": {"type": "string", "description": "Inclusive ISO date."},
                "order_by": {"type": "array", "items": {"type": "object", "properties": {
                    "field": {"type": "string"}, "direction": {"type": "string", "enum": ["asc", "desc"]}},
                    "required": ["field"]}},
                "limit": {"type": "integer"},
                "min_group_size": {"type": "integer",
                                   "description": "Set only when the question states a minimum group size. Omit otherwise."},
                "include_superseded": {"type": "boolean"},
                "assumptions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["intent", "date_basis"],
        },
    },
    {
        "name": "refuse",
        "description": "Decline PHI, another customer's data, or any request to change data.",
        "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
    },
]

FALLBACK_TOOLS = [
    {
        "name": "submit_sql",
        "description": (
            "One read-only DuckDB SELECT over sem.* only. Use this only because the typed compiler "
            "returned cannot_express. Keep the question's grain, current-adjudication or latest-submission "
            "filter, and date basis. Do not project claim_key, check_or_eft_number, or payer_claim_control_number."
        ),
        "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]},
    },
    {
        "name": "refuse",
        "description": "Decline if the fallback would need PHI, another customer, or a write.",
        "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
    },
]

_RULES = """You are the parser for CC's typed metric compiler. You answer questions about one
customer's 837P claims and 835 remittances by submitting a spec. A compiler writes the SQL. You do
not name tables, joins, or SQL fragments on this turn.

date_basis is required. It is how the question places claims in time:
- none: the question has no time window and does not group by a date.
- payment: cut or group by the payer's payment date (payment_date, payment month, check/EFT date).
- service: cut or group by date of service on the fact row.
- submission: cut or group by this 837 row's own submission date. This is not "the first time we sent the claim".
- cohort_submission: the population is claims whose first submission falls in time_start..time_end.
  Later payments still count. Set the window. Do not also filter payment_date.
- cohort_service: the population is claims whose first service date falls in the window.
- payer_received: the date the payer says it received the claim (DTM*050). Use only when the question says that.
- file_received: the day the file arrived at the lab. Name this basis when that is the question.
  The compiler will reject it. Do not substitute payment_date.

If a date would change the answer and the question does not say which date, submit intent=cannot_express
with reason ambiguous_date_basis. Do not guess.

Metrics are catalog names. Read the description and count what it counts.
- Filters narrow a metric to catalog dimensions (payer, CPT, CARC, is_denied, payer_responsibility).
- A comparison on a measure (paid_amount = 0, charge > 100) is not a filter. cannot_express.
- Do not reuse a metric whose population is wider or different from the question. In particular,
  resubmitted_claims counts every claim sent more than once, including secondary bills. It is not
  "frequency code 7". A sign-flipped amount, a percentile, a median, or a join the metric does not
  already encode is cannot_express.
- cohort_claims counts the cohort population itself (one row per claim_key). It is not a catalog metric.
- min_group_size only if the question states a minimum. Do not invent a floor of 20.
- Never group by claim_key, a check/EFT number, or a payer claim control number.

Refuse (intent=refuse, or the refuse tool) for patient names, dates of birth, member or subscriber ids,
any other customer, and any request to change or delete data.

When you submit intent=cannot_express, say which part of the question the catalog cannot state.
"""

FALLBACK_SYSTEM = """The typed compiler could not express the question. Write one DuckDB SELECT over sem.* views
only (remit_claims, remit_lines, claims, claim_lines, adjustments, remarks, payments,
provider_adjustments, diagnoses, payers).

Use the population the question names. Current adjudication is is_current_adjudication on 835 views.
Latest 837 submission is is_latest_submission. Primary payer is payer_responsibility = 'P' on sem.claims,
not payer_sequence. Do not project claim_key, check_or_eft_number, or payer_claim_control_number.
If the question needs a column that is not on sem.*, or needs PHI, refuse instead of guessing a date.
"""


def catalog_digest(model: dict | None = None) -> str:
    """Stable catalog text from the yaml, so a screen can cache it without opening a session."""
    model = model or yaml.safe_load(MODEL_PATH.read_text())
    lines = ["# Metrics (name | unit | meaning)"]
    for name, m in model["metrics"].items():
        lines.append(f"- {name} | {m['unit']} | {m['description']}")
    lines.append("- cohort_claims | claims | Claims in a cohort_submission or cohort_service window, one per claim_key. Not a catalog metric.")
    lines.append("\n# Dimensions")
    dims = {**model.get("dimensions", {}), **model.get("time_dimensions", {})}
    for name, d in dims.items():
        lines.append(f"- {name}: {d['description']}")
    lines.append("- payer_received_date: Date the payer reports it received the claim (DTM*050), on remittance claims. Not the file arrival date.")
    lines.append("\n# Vocabulary")
    for phrase, meaning in model.get("glossary", {}).items():
        lines.append(f"- {phrase}: {meaning}")
    return "\n".join(lines)


def parser_system(model: dict | None = None) -> str:
    return _RULES + "\n" + catalog_digest(model)


def tool_spec() -> dict:
    """What a screen harness should register. Imported by eval code later; not registered here."""
    return {
        "parser_tools": PARSER_TOOLS,
        "fallback_tools": FALLBACK_TOOLS,
        "parser_system": parser_system(),
        "fallback_system": FALLBACK_SYSTEM,
    }
