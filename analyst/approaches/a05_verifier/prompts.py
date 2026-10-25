"""Prompts and parsers for the analyst and the critic.

The critic's message is built from structured fields (question, plan, SQL, result digest).
The analyst's raw completion is never appended to it. That is the fresh context: a second
system prompt, a new message list, and no chain of thought carried over.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from analyst.approaches.a05_verifier.checklist import CHECK_NAMES, render_checklist

PLAN_FIELDS = ("grain", "population", "time_basis", "measure", "units")
ROWS_SHOWN_TO_CRITIC = 20

ANALYST_SYSTEM = """You are the analyst for ClaimClarity, a clinical lab's 837P claims and 835 remittances.
The system message below the divider is the schema and the business glossary for this session. Follow those
definitions. Each turn asks for one JSON object and nothing else.

The runner asks for a plan first, and only then for SQL. A SQL included in the plan response is discarded
and is not executed. The plan you return is the plan the SQL turn and the critic both see.

Refusal, for patient names, member ids, birth dates, addresses, another customer, or any write:
{"refuse": "<one sentence>"}

Plan turn:
{"plan": {"grain": "<row grain>", "population": "<which rows are in>", "time_basis": "<payment, service, submission, or none>",
 "measure": "<the quantity>", "units": "<claims, submissions, lines, payments, dollars, fraction, or percent>"}}

SQL turn, given that plan:
{"sql": "<one read-only SELECT over the listed views>", "prose": "<one paragraph a biller could read>"}

A repair or a revision asks for both a plan and a SQL in one object. Do not add rationale, chain-of-thought,
or any other field. The answer is the query's result, so the query itself has to compute the number.
"""

CRITIC_SYSTEM_PREFIX = """You are the critic for ClaimClarity. You did not write the SQL. You do not see the
analyst's reasoning, only the question, the structured plan, the SQL, and the result digest in the user message.
The schema and glossary are below the divider. Judge the SQL and the result against the question.

Reply with one JSON object and nothing else:
{"verdict": "accept" or "object",
 "checks": {"semantics": {"ok": true or false, "note": "<one sentence>"},
            "population": {"ok": true or false, "note": "<one sentence>"},
            "units": {"ok": true or false, "note": "<one sentence>"},
            "invariants": {"ok": true or false, "note": "<one sentence>"}},
 "objections": ["<one sentence per failed check>"]}

Set verdict to object if any check is not ok. Do not write replacement SQL. A sql field in your reply is discarded.
Accept a result that answers the question with extra label columns or with rounding at the end. Object to a
different population, a different unit, a different grain, or a number the query did not compute from the views.

"""


class ProtocolParseError(ValueError):
    """The model reply is not the JSON object this step requires."""


@dataclass(frozen=True)
class AnalystTurn:
    plan: dict | None
    sql: str | None
    prose: str
    refuse: str | None


def system_for_analyst(schema_context: str) -> str:
    return ANALYST_SYSTEM + "\n--- schema and glossary ---\n" + schema_context


def system_for_critic(schema_context: str) -> str:
    return CRITIC_SYSTEM_PREFIX + render_checklist() + "\n--- schema and glossary ---\n" + schema_context


def analyst_plan_user_message(question: str) -> str:
    return f"Question:\n{question}\n\nReturn the plan JSON object now. Do not include SQL."


def analyst_sql_user_message(question: str, plan: dict) -> str:
    """The plan is already committed. This turn writes the SELECT that will be executed."""
    return (
        f"Question:\n{question}\n\n"
        f"Committed plan:\n{json.dumps({field: plan[field] for field in PLAN_FIELDS})}\n\n"
        "Return the SQL JSON object now."
    )


def analyst_repair_user_message(question: str, plan: dict, sql: str, error: str) -> str:
    """One repair after DuckDB or the guard rejected the SQL. The critic has not run."""
    return (
        f"Question:\n{question}\n\n"
        f"Your previous plan:\n{json.dumps(plan)}\n\n"
        f"Your previous SQL failed before it returned rows:\n{sql}\n\nError: {error}\n\n"
        "Return one corrected plan and one SELECT. This is the only execution repair."
    )


def analyst_revision_user_message(question: str, plan: dict, sql: str, digest: dict, objections: list[str]) -> str:
    """The one revision the critic's objection buys. A second critic pass does not run."""
    return (
        f"Question:\n{question}\n\n"
        f"Plan the critic reviewed:\n{json.dumps(plan)}\n\n"
        f"SQL the critic reviewed:\n{sql}\n\n"
        f"Result digest:\n{json.dumps(digest, default=str)}\n\n"
        "The critic objected:\n" + "\n".join(f"- {item}" for item in objections) + "\n\n"
        "Return one revised plan and one SELECT. You do not get another revision."
    )


def result_digest(columns: list[str] | None, rows: list[list] | None, *, error: str | None = None) -> dict:
    materialized = rows or []
    return {
        "columns": columns,
        "row_count_returned": len(materialized),
        "guard_limit": 1000,
        "hit_guard_limit": len(materialized) >= 1000,
        "rows": materialized[:ROWS_SHOWN_TO_CRITIC],
        "error": error,
    }


def critic_user_message(question: str, plan: dict, sql: str, digest: dict) -> str:
    """The critic's entire user message. Callers must pass the stripped plan, not the raw completion."""
    visible = {field: plan[field] for field in PLAN_FIELDS}
    return (
        f"Question:\n{question}\n\n"
        f"Plan:\n{json.dumps(visible)}\n\n"
        f"SQL:\n{sql}\n\n"
        f"Result digest:\n{json.dumps(digest, default=str)}\n"
    )


def extract_json_object(text: str) -> dict:
    start = text.find("{")
    if start < 0:
        raise ProtocolParseError("model reply has no JSON object")
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise ProtocolParseError(f"model reply is not JSON: {exc.msg}") from None
    if not isinstance(value, dict):
        raise ProtocolParseError("model reply JSON must be an object")
    return value


def _refusal(payload: dict) -> str | None:
    refusal = payload.get("refuse")
    if isinstance(refusal, str) and refusal.strip():
        return refusal.strip()
    return None


def _plan_fields(payload: dict) -> dict:
    plan = payload.get("plan")
    if not isinstance(plan, dict):
        raise ProtocolParseError("analyst reply needs a plan object")
    missing = [field for field in PLAN_FIELDS if not isinstance(plan.get(field), str) or not plan[field].strip()]
    if missing:
        raise ProtocolParseError("plan missing " + ", ".join(missing))
    return {field: plan[field].strip() for field in PLAN_FIELDS}


def _sql_and_prose(payload: dict) -> tuple[str, str]:
    sql = payload.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        raise ProtocolParseError("analyst reply needs sql")
    prose = payload.get("prose", "")
    if not isinstance(prose, str):
        raise ProtocolParseError("prose must be a string")
    return sql.strip(), prose.strip()


def parse_plan_response(text: str) -> AnalystTurn:
    """Plan turn. A sql key in this object is ignored and must not be executed by the caller."""
    payload = extract_json_object(text)
    refusal = _refusal(payload)
    if refusal:
        return AnalystTurn(plan=None, sql=None, prose="", refuse=refusal)
    return AnalystTurn(plan=_plan_fields(payload), sql=None, prose="", refuse=None)


def parse_sql_response(text: str) -> AnalystTurn:
    """SQL turn. The committed plan stays the one from the plan turn."""
    payload = extract_json_object(text)
    refusal = _refusal(payload)
    if refusal:
        return AnalystTurn(plan=None, sql=None, prose="", refuse=refusal)
    sql, prose = _sql_and_prose(payload)
    return AnalystTurn(plan=None, sql=sql, prose=prose, refuse=None)


def parse_analyst_turn(text: str) -> AnalystTurn:
    """Repair and revision: a plan and a SQL together, or a refusal."""
    payload = extract_json_object(text)
    refusal = _refusal(payload)
    if refusal:
        return AnalystTurn(plan=None, sql=None, prose="", refuse=refusal)
    sql, prose = _sql_and_prose(payload)
    return AnalystTurn(plan=_plan_fields(payload), sql=sql, prose=prose, refuse=None)


def parse_critic_verdict(text: str) -> dict:
    """Fail closed: unparseable text, a missing check, or ok that is not true becomes an objection."""
    try:
        payload = extract_json_object(text)
    except ProtocolParseError as exc:
        return {"accept": False, "verdict": None, "checks": {}, "objections": [f"unparseable critic reply: {exc}"]}
    checks_in = payload.get("checks")
    if not isinstance(checks_in, dict):
        checks_in = {}
    objections: list[str] = []
    raw = payload.get("objections")
    if isinstance(raw, list):
        objections.extend(str(item).strip() for item in raw if str(item).strip())
    checks: dict[str, dict] = {}
    failed: list[str] = []
    for name in CHECK_NAMES:
        item = checks_in.get(name)
        note = item.get("note") if isinstance(item, dict) and isinstance(item.get("note"), str) else ""
        ok = isinstance(item, dict) and item.get("ok") is True
        checks[name] = {"ok": ok, "note": note}
        if not ok:
            failed.append(name)
            if note and note not in objections:
                objections.append(note)
            elif not note:
                objections.append(f"{name} did not pass")
    verdict = payload.get("verdict")
    if verdict not in ("accept", "object"):
        failed.append("verdict")
        objections.append("critic verdict was missing")
    accept = not failed and verdict == "accept"
    if verdict == "object" and not objections:
        objections.append("critic returned object without a reason")
    if not accept and verdict == "accept":
        objections.append("a failed check overrides the accept verdict")
    return {"accept": accept, "verdict": verdict if verdict in ("accept", "object") else None,
            "checks": checks, "objections": objections}
