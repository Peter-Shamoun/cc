"""Session openers, auth and the offline model backend.

Two modes (``CC_API_MODE``):

* ``sim``: the real path. The bearer token is a Cognito id token from the ``ccsim`` mock; it is
  verified by ``aws.cognito.verify_id_token`` and every analyzer session is opened through
  ``ccsim.query.open_session`` (STS AssumeRoleWithWebIdentity, 900 s, scoped secrets,
  allowed_directories) via ``analyzer.opener.QueryPathOpener``.
* ``demo`` (default for local UI work): a synthetic, PHI-free ``sem.*`` fixture in DuckDB, one per
  customer, with tokens of the form ``demo:<customer>:<user>``. No AWS, no network.

In both modes escalation is never granted: ``NoEscalateOpener`` refuses ``open(escalate=True)``,
so ``lossless.*`` is unreachable from chat.

The model is ``ScriptedLLM`` driven by ``demo_turn`` (offline replay). Paid APIs stay disabled:
``AnthropicLLM``/``BedrockLLM`` are only built when ``CC_API_LLM`` names them and still raise
``PaidAPIDisabled`` unless the analyzer's own ``allow_paid_api`` is set in code.
"""
from __future__ import annotations

import datetime as dt
import random
import re
from dataclasses import dataclass
from typing import Protocol

import duckdb

from analyzer.llm import AnthropicLLM, BedrockLLM, ScriptedLLM
from analyzer.opener import QueryPathOpener, SessionHandle
from analyzer.tools import ToolError

from api.store import Principal


class AuthError(Exception):
    pass


class NoEscalateOpener:
    """Wrap any opener; the chat surface never escalates to lossless.*."""

    def __init__(self, inner):
        self.inner = inner

    def open(self, escalate: bool) -> SessionHandle:
        if escalate:
            raise ToolError("escalation is not available from chat; ask a data steward")
        return self.inner.open(False)


@dataclass
class AuthContext:
    principal: Principal
    opener: object


class AuthBackend(Protocol):
    """What ``create_app(auth=...)`` needs from the real backend. DemoAuth and SimAuth implement it.

    ``token`` is the bearer token, or the ``cc_session`` cookie value (api.app._token). It is never read from a
    URL. Raise AuthError for anything invalid or expired; the API answers 401 ``auth_expired``. The token is never
    logged, and neither is the principal's user beyond the audit allowlist. ``opener`` must refuse escalation
    (wrap it in NoEscalateOpener)."""

    mode: str

    def authenticate(self, token: str, request_id: str) -> AuthContext: ...


class SimAuth:
    """Cognito id token -> principal; sessions through ccsim.query.open_session only."""

    mode = "sim"

    def __init__(self, aws, deployment):
        self.aws = aws
        self.deployment = deployment

    def authenticate(self, token: str, request_id: str) -> AuthContext:
        try:
            claims = self.aws.cognito.verify_id_token(token)
        except Exception as exc:  # noqa: BLE001 - any verification failure is a 401
            raise AuthError("invalid or expired session") from exc
        customer = claims.get("custom:customer")
        if not customer:
            raise AuthError("token has no customer")
        principal = Principal(customer, claims.get("email") or claims["sub"])
        opener = NoEscalateOpener(QueryPathOpener(self.aws, self.deployment, token, request_id=request_id))
        return AuthContext(principal, opener)


_PAYERS = ["Aetna", "BCBS", "Cigna", "Humana", "Medicare", "Medicaid", "UnitedHealthcare"]
_STATUS = ["paid", "paid", "paid", "denied", "partial", "pending"]
_CARC = ["CO-45", "CO-97", "PR-1", "CO-16", "CO-50", "PR-2"]


def demo_connection(customer: str, n_claims: int = 1200, seed: int = 11) -> duckdb.DuckDBPyConnection:
    """Synthetic sem.* views. Every value is generated; ``name_first`` exists only to exercise redaction."""
    rng = random.Random(f"{customer}:{seed}")
    prefix = "".join(word[0] for word in customer.split("_") if word).upper()
    con = duckdb.connect()
    con.execute("CREATE SCHEMA sem")
    con.execute("""CREATE TABLE sem.claims (claim_key VARCHAR, payer_name VARCHAR, service_date DATE,
                   status VARCHAR, charge_amount DOUBLE, paid_amount DOUBLE, carc_code VARCHAR, name_first VARCHAR)""")
    start = dt.date(2025, 11, 1)
    rows = []
    for i in range(n_claims):
        charge = round(rng.uniform(40, 900), 2)
        status = rng.choice(_STATUS)
        paid = {"paid": round(charge * rng.uniform(0.55, 0.95), 2), "partial": round(charge * rng.uniform(0.1, 0.5), 2)}.get(status, 0.0)
        rows.append((f"{prefix}{100000 + i}", rng.choice(_PAYERS), start + dt.timedelta(days=rng.randrange(360)), status,
                     charge, paid, rng.choice(_CARC) if status in ("denied", "partial") else None, f"Synthetic{i % 37}"))
    con.executemany("INSERT INTO sem.claims VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return con


class DemoOpener:
    def __init__(self, con, principal: Principal):
        self.con = con
        self.principal = principal

    def open(self, escalate: bool) -> SessionHandle:
        assert not escalate
        return SessionHandle(self.principal.customer, self.principal.user, "demo", self.con.cursor())


class DemoAuth:
    mode = "demo"
    _TOKEN = re.compile(r"demo:([a-z0-9_]{2,40}):([A-Za-z0-9_.@+-]{1,80})")

    def __init__(self, customers=("acme_labs", "acme_diagnostics")):
        self.customers = set(customers)
        self._cons: dict[str, duckdb.DuckDBPyConnection] = {}

    def authenticate(self, token: str, request_id: str) -> AuthContext:
        m = self._TOKEN.fullmatch(token or "")
        if not m or m.group(1) not in self.customers:
            raise AuthError("invalid or expired session")
        principal = Principal(m.group(1), m.group(2))
        con = self._cons.get(principal.customer)
        if con is None:
            con = self._cons[principal.customer] = demo_connection(principal.customer)
        return AuthContext(principal, NoEscalateOpener(DemoOpener(con, principal)))


# ---------------------------------------------------------------- offline model

_REFUSE = re.compile(r"\b(patient names?|member names?|ssn|social security|date of birth|dob|home address|who is the patient)\b", re.I)

DEMO_QUERIES = [
    (re.compile(r"\b(trend|over time|monthly|by month|per month)\b", re.I),
     "SELECT date_trunc('month', service_date)::DATE AS service_month, SUM(paid_amount) AS paid_amount\n"
     "FROM sem.claims\nGROUP BY 1\nORDER BY 1",
     "Paid amount by service month.",
     ["Month is the claim's service date, not the remittance date.", "Pending and denied claims count as $0 paid."]),
    (re.compile(r"\b(denial|denied)\b.*\bpayer\b|\bpayer\b.*\b(denial|denied)\b", re.I),
     "SELECT payer_name, AVG(CASE WHEN status = 'denied' THEN 1.0 ELSE 0.0 END) AS denial_rate, COUNT(*) AS claims\n"
     "FROM sem.claims\nGROUP BY 1\nORDER BY denial_rate DESC",
     "Denial rate by payer.",
     ["Denial means the whole claim was denied; partial payments are not denials."]),
    (re.compile(r"\bpayer\b", re.I),
     "SELECT payer_name, SUM(paid_amount) AS paid_amount, COUNT(*) AS claims\nFROM sem.claims\nGROUP BY 1\nORDER BY paid_amount DESC",
     "Paid amount by payer.", ["All service dates in the current gold version."]),
    (re.compile(r"\b(list|show|export|all|every|download|csv)\b.*\bclaims?\b", re.I),
     "SELECT claim_key, payer_name, service_date, status, charge_amount, paid_amount, carc_code\n"
     "FROM sem.claims\nORDER BY service_date DESC, claim_key",
     "Here are the claims, newest service date first. The table shows the first rows; download the CSV for all of them.",
     ["Row-level claim list; no patient identifiers are included."]),
    (re.compile(r"\b(name|names)\b.*\bclaims?\b", re.I),
     "SELECT claim_key, name_first, paid_amount FROM sem.claims ORDER BY claim_key LIMIT 20",
     "Claims with the identifier column (it is redacted in this session).", []),
]
DEFAULT_QUERY = ("SELECT COUNT(*) AS claims, SUM(charge_amount) AS charge_amount, SUM(paid_amount) AS paid_amount\n"
                 "FROM sem.claims", "Overall claim volume and dollars.", ["Counts every claim in the current gold version."])


def _question(messages: list) -> str:
    first = messages[0]["content"] if messages else ""
    return first if isinstance(first, str) else ""


# ---------------------------------------------------------------- demo scenarios (contract v1 §9)

SCENARIOS = ("table", "chart", "repair", "abstain", "clarify", "escalation", "candidates", "error", "slow", "report")
_SCENARIO_WORDS = [
    ("error", re.compile(r"\b(simulate (an )?(error|outage)|fail on purpose)\b", re.I)),
    ("clarify", re.compile(r"\b(how (bad|big) (are|is)|ambiguous|clarify)\b", re.I)),
    ("abstain", re.compile(r"\b(appeals?|prior auth\w*|forecast|predict\w*|next (month|quarter|year))\b", re.I)),
    ("escalation", re.compile(r"\b(escalat\w*|net collection rate|hard question)\b", re.I)),
    ("candidates", re.compile(r"\b(candidates?|double.?check|cross.?check|average paid)\b", re.I)),
    ("repair", re.compile(r"\b(carc|denial codes?|reason codes?|repair)\b", re.I)),
    ("slow", re.compile(r"\bslow(ly)?\b", re.I)),
    ("report", re.compile(r"\b(report|artifact)\b", re.I)),
]
#: AnalyzerConfig overrides a scenario needs (the analyzer itself does the candidates/cascade/ask work).
SCENARIO_CONFIG = {
    "clarify": {"enable_ask": True},
    "escalation": {"cascade": "haiku_then_sonnet"},
    "candidates": {"candidates": 3, "candidate_selection": "execution_vote"},
}
CLARIFY_QUESTION = "Do you want the denial rate by **claim count** or by **billed dollars**?"
CLARIFY_CHOICES = [
    {"id": "count", "label": "By claim count", "description": "Share of claims that were fully denied"},
    {"id": "dollars", "label": "By billed dollars", "description": "Share of charges on denied claims"},
]
ABSTAIN_REASON = ("This data doesn't include **appeals, prior authorizations or forecasts**, so I can't answer "
                  "that reliably. I can work with claims, payers, service dates, charges, payments and denial codes.")


def detect_scenario(question: str, forced: str | None = None) -> str:
    """``forced`` (ChatRequest.scenario, demo only) wins; otherwise keywords; otherwise ``table``/``chart``."""
    if forced in SCENARIOS:
        return forced
    if _REFUSE.search(question):
        return "abstain"
    for name, pattern in _SCENARIO_WORDS:
        if pattern.search(question) and not (name == "clarify" and "Clarification:" in question):
            return name
    return "chart" if DEMO_QUERIES[0][0].search(question) else "table"


_CANDIDATE_SQL = [
    "SELECT payer_name, AVG(paid_amount) AS avg_paid_amount, COUNT(*) AS claims\nFROM sem.claims\nGROUP BY 1\nORDER BY 2 DESC",
    "SELECT payer_name, AVG(paid_amount) AS avg_paid_amount, COUNT(*) AS claims\nFROM sem.claims\nGROUP BY 1\nORDER BY 2 DESC",
    "SELECT payer_name, AVG(paid_amount) AS avg_paid_amount, COUNT(*) AS claims\nFROM sem.claims\nWHERE status = 'paid'\nGROUP BY 1\nORDER BY 2 DESC",
]
_NCR_BAD = "SELECT payer_name, SUM(net_paid) / SUM(allowed_amount) AS net_collection_rate\nFROM sem.claims\nGROUP BY 1"
_NCR_BAD2 = "SELECT payer_name, SUM(net_paid) / SUM(charge_amount) AS net_collection_rate\nFROM sem.claims\nGROUP BY 1"
_NCR = ("SELECT payer_name, SUM(paid_amount) / NULLIF(SUM(charge_amount), 0) AS net_collection_rate, COUNT(*) AS claims\n"
        "FROM sem.claims\nGROUP BY 1\nORDER BY net_collection_rate DESC")
_SQL_CARC_BAD = ("SELECT denial_code, COUNT(*) AS claims\nFROM sem.claims\nWHERE status IN ('denied', 'partial')\n"
             "GROUP BY 1\nORDER BY claims DESC")
_SQL_CARC = ("SELECT carc_code, COUNT(*) AS claims, SUM(charge_amount) AS charge_amount\nFROM sem.claims\n"
         "WHERE status IN ('denied', 'partial')\nGROUP BY 1\nORDER BY claims DESC")


def _explore():
    return {"text": "Checking the claims view.", "tool_calls": [
        {"name": "describe_relation", "arguments": {"relation": "sem.claims"}}]}


def _final(sql, answer, assumptions=()):
    return {"tool_calls": [{"name": "final_answer", "arguments": {"sql": sql, "answer": answer,
                                                                 "assumptions": list(assumptions)}}]}


def scenario_turn(scenario: str):
    """A ``ScriptedLLM`` turn function for one scenario. Offline; never calls a network."""
    def turn(model, messages, candidate_index):
        question = _question(messages)
        step = sum(1 for m in messages if m["role"] == "assistant")
        if scenario == "abstain":
            if _REFUSE.search(question):
                return {"tool_calls": [{"name": "refuse", "arguments": {"category": "phi_request", "reason":
                        "I can't return patient-identifying details. I can give counts, rates and dollars instead."}}]}
            if step == 0:
                return _explore()
            return {"tool_calls": [{"name": "refuse", "arguments": {"category": "data_unavailable",
                                                                    "reason": ABSTAIN_REASON}}]}
        if scenario == "error":
            if step == 0:
                return _explore()
            raise RuntimeError("simulated model outage")
        if scenario == "clarify":
            m = re.search(r"Clarification:\s*(.+)", question)
            if m is None:
                if step == 0:
                    return _explore()
                return {"tool_calls": [{"name": "ask", "arguments": {"question": CLARIFY_QUESTION,
                                                                     "choices": CLARIFY_CHOICES}}]}
            dollars = re.search(r"dollar|charge|amount", m.group(1), re.I)
            sql = ("SELECT payer_name, SUM(CASE WHEN status = 'denied' THEN charge_amount ELSE 0 END) / SUM(charge_amount)"
                   " AS denied_dollar_rate, SUM(charge_amount) AS charge_amount\nFROM sem.claims\nGROUP BY 1\nORDER BY 2 DESC"
                   if dollars else
                   "SELECT payer_name, AVG(CASE WHEN status = 'denied' THEN 1.0 ELSE 0.0 END) AS denial_rate, COUNT(*) AS claims\n"
                   "FROM sem.claims\nGROUP BY 1\nORDER BY denial_rate DESC")
            if step == 0:
                return {"tool_calls": [{"name": "run_sql", "arguments": {"sql": sql}}]}
            return _final(sql, "Denial rate by payer, " + ("weighted by billed dollars." if dollars else "by claim count."),
                          ["Denied means the whole claim was denied; partial payments are not denials."])
        if scenario == "escalation":
            small = "haiku" in model.lower()
            if small:
                if step == 0:
                    return _explore()
                if step == 1:
                    return {"tool_calls": [{"name": "run_sql", "arguments": {"sql": _NCR_BAD}}]}
                if step == 2:
                    return {"tool_calls": [{"name": "run_sql", "arguments": {"sql": _NCR_BAD2}}]}
                return {"text": "I could not find a working definition of net collection rate."}
            if step == 0:
                return {"text": "Net collection rate is paid over charged; this view has no allowed amount.",
                        "tool_calls": [{"name": "run_sql", "arguments": {"sql": _NCR}}]}
            return _final(_NCR, "Net collection rate by payer (paid ÷ charged).",
                          ["There is no allowed-amount column, so this uses charges as the denominator."])
        if scenario == "candidates":
            sql = _CANDIDATE_SQL[candidate_index % 3]
            if step == 0:
                return {"tool_calls": [{"name": "run_sql", "arguments": {"sql": sql}}]}
            return _final(sql, "Average paid amount per claim, by payer.",
                          ["Average over all claims, so denied and pending claims count as $0."])
        if scenario == "repair":
            if step == 0:
                return _explore()
            if step == 1:
                return {"tool_calls": [{"name": "run_sql", "arguments": {"sql": _SQL_CARC_BAD}}]}
            if step == 2:
                return {"text": "The denial reason column is carc_code; retrying.",
                        "tool_calls": [{"name": "run_sql", "arguments": {"sql": _SQL_CARC}}]}
            return _final(_SQL_CARC, "Denied and partially paid claims by CARC code.",
                          ["Includes partial payments, which carry an adjustment reason."])
        return demo_turn(model, messages, candidate_index)
    return turn


def demo_turn(model, messages, candidate_index):
    """Scripted analyst: look at the schema, run one query, then answer. Never calls a network."""
    question = _question(messages)
    step = sum(1 for m in messages if m["role"] == "assistant")
    if _REFUSE.search(question):
        return {"tool_calls": [{"name": "refuse", "arguments": {
            "reason": "I can't return patient-identifying details. I can give counts, rates and dollars instead."}}]}
    sql, prose, assumptions = DEFAULT_QUERY
    for pattern, s, p, a in DEMO_QUERIES:
        if pattern.search(question):
            sql, prose, assumptions = s, p, a
            break
    if step == 0:
        return {"text": "Checking the claims view.", "tool_calls": [
            {"name": "describe_relation", "arguments": {"relation": "sem.claims"}}]}
    if step == 1:
        return {"tool_calls": [{"name": "run_sql", "arguments": {"sql": sql}}]}
    return {"tool_calls": [{"name": "final_answer", "arguments": {"sql": sql, "answer": prose,
                                                                 "assumptions": assumptions}}]}


def make_llm(kind: str = "scripted", scenario: str | None = None):
    """``scripted`` (default) is offline replay. Real models are wired behind config and stay disabled."""
    if kind == "scripted":
        # "report" (1.9.0) runs the chart script; api.mapping.report_artifacts reshapes the answer into artifacts
        scripted = "chart" if scenario == "report" else scenario
        return ScriptedLLM([scenario_turn(scripted) if scripted else demo_turn] * 8)
    if kind == "anthropic":
        return AnthropicLLM()
    if kind == "bedrock":
        return BedrockLLM()
    raise ValueError(f"unknown llm backend {kind!r}")
