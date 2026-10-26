"""Session openers, auth and the offline model backend.

Two modes (``CC_API_MODE``):

* ``sim``: the real path. The bearer token is a Cognito id token from the ``ccsim`` mock; it is
  verified by ``aws.cognito.verify_id_token`` and every analyzer session is opened through
  ``ccsim.query.open_session`` (STS AssumeRoleWithWebIdentity, 900 s, scoped secrets,
  allowed_directories) via ``analyzer.opener.QueryPathOpener``.
* ``demo`` (default for local UI work): a synthetic, PHI-free ``sem.*`` fixture in DuckDB, one per
  customer, with tokens of the form ``demo:<customer>:<user>``. No AWS, no network.

Escalation to ``lossless.*`` (the PHI-stripped EDI tables) is the analyzer's own choice, with a stated reason: the
app wraps every request's opener in ``ReasonedEscalationOpener``, which opens the escalated session for the same
signed-in customer only through ``open_escalated(reason)`` and audits the grant (customer, user, masked reason).

The model is ``ScriptedLLM`` driven by ``demo_turn`` (offline replay). Paid APIs stay disabled:
``AnthropicLLM``/``BedrockLLM`` are only built when ``CC_API_LLM`` names them and still raise
``PaidAPIDisabled`` unless the analyzer's own ``allow_paid_api`` is set in code.
"""
from __future__ import annotations

import datetime as dt
import random
import re
import threading
from dataclasses import dataclass
from typing import Protocol

import duckdb

from analyzer.llm import AnthropicLLM, BedrockLLM, ScriptedLLM
from analyzer.opener import QueryPathOpener, SessionHandle
from analyzer.tools import ToolError

from api.store import Principal


class AuthError(Exception):
    pass


#: what a stated escalation reason may be at most, after masking (it is shown in the step timeline and audited)
ESCALATION_REASON_MAX_CHARACTERS = 300


class ReasonedEscalationOpener:
    """Chat's only way to ``lossless.*``. ``open_escalated(reason)`` needs a non-empty reason; it re-opens the session
    through the inner opener (the same signed-in customer: the inner opener holds the verified token) with
    ``lossless.*`` mounted and calls ``on_escalation_granted(masked_reason)`` for the audit. A bare
    ``open(escalate=True)`` is refused until that grant: restoring an escalated session (a later command of the same
    run) is fine, starting one without a reason is not."""

    def __init__(self, inner, on_escalation_granted=None):
        self.inner = inner
        self.on_escalation_granted = on_escalation_granted
        self.escalation_reason: str | None = None

    @property
    def escalated(self) -> bool:
        return self.escalation_reason is not None

    def open(self, escalate: bool) -> SessionHandle:
        if escalate and not self.escalated:
            raise ToolError("lossless.* needs a stated reason: call request_escalation with one")
        return self.inner.open(escalate)

    def open_escalated(self, reason: str) -> SessionHandle:
        from api.live import masked_escalation_reason
        masked_reason = masked_escalation_reason(reason, ESCALATION_REASON_MAX_CHARACTERS)
        if not masked_reason:
            raise ToolError("request_escalation requires a reason")
        handle = self.inner.open(True)
        self.escalation_reason = masked_reason
        if self.on_escalation_granted is not None:
            self.on_escalation_granted(masked_reason)
        return handle


@dataclass
class AuthContext:
    principal: Principal
    opener: object


class AuthBackend(Protocol):
    """What ``create_app(auth=...)`` needs from the real backend. DemoAuth and SimAuth implement it.

    ``token`` is the bearer token, or the ``cc_session`` cookie value (api.app._token). It is never read from a
    URL. Raise AuthError for anything invalid or expired; the API answers 401 ``auth_expired``. The token is never
    logged, and neither is the principal's user beyond the audit allowlist. ``opener.open(escalate=True)`` must open
    the same customer's session with ``lossless.*`` mounted (or raise); the app wraps the opener in
    ``ReasonedEscalationOpener``, so escalation happens only with a stated, audited reason."""

    mode: str

    def authenticate(self, token: str, request_id: str) -> AuthContext: ...

    def sign_out(self, token: str) -> None:
        """Revoke the server-side session for ``token`` (e.g. Cognito GlobalSignOut / delete the session row), so a
        copied cookie stops working. Best effort: called by ``POST /api/auth/sign-out`` with whatever token the
        request carried (possibly expired or empty). Must not raise for an unknown token, and never log it. The route
        itself always clears the browser side: ``Clear-Site-Data: "cache", "storage"`` and an expired
        ``cc_session`` cookie (docs/frontend_security.md, "Sign-out")."""
        ...


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
        # keyed by the immutable subject: an email address can be reassigned to another person, who must not
        # inherit the previous holder's conversations
        principal = Principal(customer, claims["sub"])
        return AuthContext(principal, QueryPathOpener(self.aws, self.deployment, token, request_id=request_id))

    def sign_out(self, token: str) -> None:
        """The ccsim mock has no revocation; id tokens expire on their own (the real backend calls GlobalSignOut)."""
        return None


_PAYERS = ["Aetna", "BCBS", "Cigna", "Humana", "Medicare", "Medicaid", "UnitedHealthcare"]
_STATUS = ["paid", "paid", "paid", "denied", "partial", "pending"]
_CARC = ["CO-45", "CO-97", "PR-1", "CO-16", "CO-50", "PR-2"]


#: The demo's stand-in for the identity provider plus each customer's semantic-layer manifest. In production the
#: Cognito id token's ``custom:customer`` claim names the tenant and ``ccsim.query.open_session`` builds the session
#: from that customer's manifest (sem.* only; lossless.* only when the analyzer escalates with a reason). Here the demo
#: token ``demo:<customer>:<user>`` plays the id token and this table plays the manifest. Nothing in a request body,
#: query string or header other than the session token can choose the customer or the relations (CONTRACT §2.1).
DEMO_DIRECTORY: dict[str, tuple[str, ...]] = {
    "acme_labs": ("sem.claims",),
    "acme_diagnostics": ("sem.claims",),
}
DEMO_RELATIONS = frozenset({"sem.claims"})  # the relations demo_connection knows how to synthesise
#: what an escalated demo session adds: the received EDI files and their parse status (like ccsim's
#: lossless.edi_source_file, minus the S3 and envelope columns)
DEMO_LOSSLESS_RELATIONS = ("lossless.edi_source_file",)


@dataclass(frozen=True)
class DemoIdentity:
    """What the demo token proves, like a verified id token's claims: who, which tenant, which relations."""
    customer: str
    user: str
    relations: tuple[str, ...]


def demo_connection(customer: str, n_claims: int = 1200, seed: int = 11,
                    relations: tuple[str, ...] = ("sem.claims",), escalated: bool = False) -> duckdb.DuckDBPyConnection:
    """Synthetic sem.* views, exactly ``relations`` (from the identity), plus DEMO_LOSSLESS_RELATIONS when
    ``escalated``, nothing else. Every value is generated; ``name_first`` exists only to exercise redaction. Like the production session, the connection can't read files
    or the network and its configuration is locked once built."""
    unknown = set(relations) - DEMO_RELATIONS
    if unknown or not relations:
        raise ValueError(f"no demo data for {sorted(unknown) or 'an empty relation list'}")
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
    if escalated:
        con.execute("CREATE SCHEMA lossless")
        con.execute("""CREATE TABLE lossless.edi_source_file (file_id VARCHAR, file_kind VARCHAR, received_at TIMESTAMP,
                       parse_status VARCHAR, segment_count INTEGER, transaction_count INTEGER)""")
        files = [(f"F{prefix}{i:04d}", rng.choice(["837", "835"]),
                  dt.datetime(2025, 11, 1) + dt.timedelta(hours=rng.randrange(360 * 24)),
                  "REJECTED" if rng.random() < 0.08 else "PARSED", rng.randrange(40, 4000), rng.randrange(1, 60))
                 for i in range(90)]
        con.executemany("INSERT INTO lossless.edi_source_file VALUES (?, ?, ?, ?, ?, ?)", files)
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


class DemoOpener:
    """Opens a session for the identity's customer only (the open_session analogue): the handle's customer and
    user come from the token, and the cursor sees only that customer's relations. An escalated session is a cursor
    on a second connection that also holds DEMO_LOSSLESS_RELATIONS, so a non-escalated cursor cannot reach them."""

    def __init__(self, con, principal: Principal, relations: tuple[str, ...] = ("sem.claims",),
                 escalated_connection=None):
        self.con = con
        self.principal = principal
        self.relations = relations
        self.escalated_connection = escalated_connection

    def open(self, escalate: bool) -> SessionHandle:
        if escalate and self.escalated_connection is None:
            raise ToolError("this demo session has no lossless.* data")
        con = self.escalated_connection() if escalate else self.con
        return SessionHandle(self.principal.customer, self.principal.user, "demo", con.cursor())


class DemoAuth:
    mode = "demo"
    _TOKEN = re.compile(r"demo:([a-z0-9_]{2,40}):([A-Za-z0-9_.@+-]{1,80})")

    def __init__(self, customers=tuple(DEMO_DIRECTORY)):
        self.directory = {c: DEMO_DIRECTORY.get(c, ("sem.claims",)) for c in customers}
        self.customers = set(self.directory)
        self._cons: dict[tuple[str, bool], duckdb.DuckDBPyConnection] = {}
        self._cons_lock = threading.Lock()

    def verify(self, token: str) -> DemoIdentity:
        """The id-token check: the customer and its relations come from the token and the directory, never the request."""
        m = self._TOKEN.fullmatch(token or "")
        if not m or m.group(1) not in self.directory:
            raise AuthError("invalid or expired session")
        return DemoIdentity(m.group(1), m.group(2), self.directory[m.group(1)])

    def authenticate(self, token: str, request_id: str) -> AuthContext:
        ident = self.verify(token)
        principal = Principal(ident.customer, ident.user)
        return AuthContext(principal, DemoOpener(self._connection(ident, False), principal, ident.relations,
                                                 escalated_connection=lambda: self._connection(ident, True)))

    def _connection(self, ident: DemoIdentity, escalated: bool) -> duckdb.DuckDBPyConnection:
        """One synthetic connection per customer and escalation level, built on first use."""
        with self._cons_lock:
            con = self._cons.get((ident.customer, escalated))
            if con is None:
                con = self._cons[(ident.customer, escalated)] = demo_connection(
                    ident.customer, relations=ident.relations, escalated=escalated)
            return con

    def sign_out(self, token: str) -> None:
        """Demo tokens are stateless and synthetic; nothing to revoke. The route still clears the browser."""
        return None


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

SCENARIOS = ("table", "chart", "repair", "abstain", "clarify", "escalation", "candidates", "error", "slow", "report",
             # 1.16.0 edge cases (CONTRACT.md §9.1): what the UI must survive, each with an example stream
             "empty", "multi_table", "long_sql", "many_repairs", "clarify_nested", "escalation_abstain",
             "cancel_plan", "cancel_sql", "cancel_repair", "cancel_candidates", "cancel_escalation", "cancel_render",
             "clarify_multi")  # 1.23.0: Claude-style multi-question clarification (§6.1); appended like the schema enum
#: 1.16.0 "cancel at every stage": scenario -> (scripted run it rides on, the event that presses Stop).
#: The server presses Stop itself the moment that event is emitted, so the aborted stream is deterministic.
CANCEL_AT = {
    "cancel_plan": ("table", lambda e: e.get("type") == "status" and e.get("label") == "Thinking"),
    "cancel_sql": ("table", lambda e: e.get("type") == "step.sql_running"),
    "cancel_repair": ("repair", lambda e: e.get("type") == "step.repair_attempt"),
    "cancel_candidates": ("candidates", lambda e: e.get("type") == "step.sql_running" and e.get("candidateId") == "k2"),
    "cancel_escalation": ("escalation", lambda e: e.get("type") == "escalation"),
    "cancel_render": ("table", lambda e: e.get("type") in ("text.start", "part")),
}


def scripted_scenario(scenario: str | None) -> str | None:
    """The ScriptedLLM script a scenario runs (``report`` and the cancel_* scenarios reuse another one)."""
    if scenario == "report":
        return "chart"
    return CANCEL_AT[scenario][0] if scenario in CANCEL_AT else scenario

_SCENARIO_WORDS = [
    ("error", re.compile(r"\b(simulate (an )?(error|outage)|fail on purpose)\b", re.I)),
    ("clarify_multi", re.compile(r"\b(denial (rate )?breakdown|ask me (a few|some) questions)\b", re.I)),
    ("clarify", re.compile(r"\b(how (bad|big) (are|is)|ambiguous|clarify)\b", re.I)),
    ("abstain", re.compile(r"\b(appeals?|prior auth\w*|forecast|predict\w*|next (month|quarter|year))\b", re.I)),
    ("escalation", re.compile(r"\b(escalat\w*|net collection rate|hard question)\b", re.I)),
    ("candidates", re.compile(r"\b(candidates?|double.?check|cross.?check|average paid)\b", re.I)),
    ("repair", re.compile(r"\b(carc|denial codes?|reason codes?|repair)\b", re.I)),
    ("slow", re.compile(r"\bslow(ly)?\b", re.I)),
    ("report", re.compile(r"\b(report|artifact)\b", re.I)),
]
# 1.16.0 edge cases are reached with ChatRequest.scenario only (no keywords), so real questions never hit them.
#: AnalyzerConfig overrides a scenario needs (the analyzer itself does the candidates/cascade/ask work).
SCENARIO_CONFIG = {
    "clarify": {"enable_ask": True},
    "escalation": {"cascade": "haiku_then_sonnet"},
    "candidates": {"candidates": 3, "candidate_selection": "execution_vote"},
    # 1.16.0
    "empty": {"verify": False},                      # an empty result is the answer, not a reason to revise
    "many_repairs": {"max_repairs": 6, "max_turns": 10, "max_tool_calls": 14},
    "clarify_nested": {"enable_ask": True},
    "clarify_multi": {"enable_ask": True},
    "escalation_abstain": {"cascade": "haiku_then_sonnet"},
    "cancel_candidates": {"candidates": 3, "candidate_selection": "execution_vote"},
    "cancel_escalation": {"cascade": "haiku_then_sonnet"},
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
        if pattern.search(question) and not (name.startswith("clarify") and "Clarification:" in question):
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
# ---- 1.16.0 edge-case scripts (synthetic data; every query runs on the demo DuckDB)
EMPTY_SQL = ("SELECT payer_name, COUNT(*) AS claims, SUM(paid_amount) AS paid_amount\nFROM sem.claims\n"
             "WHERE payer_name = 'Tricare' AND status = 'denied'\nGROUP BY 1")
MULTI_TABLE_SQL = (
    "SELECT payer_name, COUNT(*) AS claims, SUM(paid_amount) AS paid_amount\nFROM sem.claims\nGROUP BY 1\nORDER BY 3 DESC",
    "SELECT status, COUNT(*) AS claims, SUM(charge_amount) AS charge_amount\nFROM sem.claims\nGROUP BY 1\nORDER BY 2 DESC",
)
MULTI_TABLE_TITLES = ("By payer", "By claim status")


def _long_sql() -> str:
    """A realistic but very long query (~9 KB, 150+ lines): one CTE per payer plus a wide pivot."""
    payers = ["Aetna", "BCBS", "Cigna", "Humana", "Medicare", "Medicaid", "UnitedHealthcare"]
    ctes = []
    for p in payers:
        key = p.lower()
        ctes.append(
            f"{key}_monthly AS (\n"
            f"  SELECT date_trunc('month', service_date)::DATE AS service_month,\n"
            f"         COUNT(*) AS claims,\n"
            f"         SUM(charge_amount) AS charge_amount,\n"
            f"         SUM(paid_amount) AS paid_amount,\n"
            f"         SUM(CASE WHEN status = 'denied' THEN 1 ELSE 0 END) AS denied_claims,\n"
            f"         SUM(CASE WHEN status = 'partial' THEN 1 ELSE 0 END) AS partial_claims\n"
            f"  FROM sem.claims\n"
            f"  WHERE payer_name = '{p}'\n"
            f"  GROUP BY 1\n)")
    cols = ",\n".join(
        f"  COALESCE({p.lower()}_monthly.paid_amount, 0) AS paid_{p.lower()},\n"
        f"  COALESCE({p.lower()}_monthly.denied_claims, 0) AS denied_{p.lower()}" for p in payers)
    joins = "\n".join(f"LEFT JOIN {p.lower()}_monthly USING (service_month)" for p in payers)
    return ("WITH months AS (\n  SELECT DISTINCT date_trunc('month', service_date)::DATE AS service_month\n"
            "  FROM sem.claims\n),\n" + ",\n".join(ctes) +
            "\nSELECT\n  months.service_month,\n" + cols + "\nFROM months\n" + joins + "\nORDER BY 1")


LONG_SQL = _long_sql()
#: many_repairs: four failing attempts, each with a different error, then a working query.
MANY_REPAIRS_BAD = [
    "SELECT denial_reason, COUNT(*) AS claims\nFROM sem.claims\nGROUP BY 1",
    "SELECT carc, COUNT(*) AS claims\nFROM sem.claims\nGROUP BY 1",
    "SELECT carc_code, COUNT(*) AS claims\nFROM sem.claim\nGROUP BY 1",
    "SELECT carc_code, COUNT(*) AS claims\nFROM sem.claims\nGROUP BY carc_code, status ORDER BY claimz",
]
NESTED_CLARIFY = [
    (CLARIFY_QUESTION,
     [{"id": "count", "label": "By claim count"}, {"id": "dollars", "label": "By billed dollars"}]),
    ("Over which **time window**?",
     [{"id": "90d", "label": "Last 90 days", "description": "Service dates in the last 90 days of data"},
      {"id": "all", "label": "All time", "description": "Every service date in the data"}]),
]

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
        if scenario in EDGE_TURNS:
            return EDGE_TURNS[scenario](model, question, step, candidate_index)
        return demo_turn(model, messages, candidate_index)
    return turn


def _run_sql(sql: str, text: str | None = None) -> dict:
    out = {"tool_calls": [{"name": "run_sql", "arguments": {"sql": sql}}]}
    if text:
        out["text"] = text
    return out


def _turn_empty(model, question, step, cand):
    if step == 0:
        return _explore()
    if step == 1:
        return _run_sql(EMPTY_SQL)
    return _final(EMPTY_SQL, "No denied claims from Tricare in this data, so there is nothing to break down.",
                  ["Tricare is matched by exact payer name."])


def _turn_multi_table(model, question, step, cand):
    if step == 0:
        return _explore()
    if step in (1, 2):
        return _run_sql(MULTI_TABLE_SQL[step - 1])
    return _final(MULTI_TABLE_SQL[0], "Claims and paid amount by payer, and claims and charges by status.",
                  ["Each table counts every claim once."])


def _turn_long_sql(model, question, step, cand):
    if step == 0:
        return _explore()
    if step == 1:
        return _run_sql(LONG_SQL, "One monthly series per payer, joined on service month.")
    return _final(LONG_SQL, "Paid amount and denied claims by month, one column pair per payer.",
                  ["Months with no claims for a payer show 0."])


def _turn_many_repairs(model, question, step, cand):
    if step == 0:
        return _explore()
    if 1 <= step <= len(MANY_REPAIRS_BAD):
        return _run_sql(MANY_REPAIRS_BAD[step - 1], "Retrying with a corrected query." if step > 1 else None)
    if step == len(MANY_REPAIRS_BAD) + 1:
        return _run_sql(_SQL_CARC, "The denial reason column is carc_code on sem.claims.")
    return _final(_SQL_CARC, "Denied and partially paid claims by CARC code (after four corrections).",
                  ["Includes partial payments, which carry an adjustment reason."])


def _turn_clarify_nested(model, question, step, cand):
    answered = len(re.findall(r"^Clarification:", question, re.M))
    if answered < len(NESTED_CLARIFY):
        if answered == 0 and step == 0:
            return _explore()
        q, choices = NESTED_CLARIFY[answered]
        return {"tool_calls": [{"name": "ask", "arguments": {"question": q, "choices": choices}}]}
    dollars = re.search(r"Clarification:.*dollar", question, re.I)
    recent = re.search(r"Clarification:.*90 days", question, re.I)
    where = "\nWHERE service_date >= (SELECT MAX(service_date) - INTERVAL 90 DAY FROM sem.claims)" if recent else ""
    sql = (("SELECT payer_name, SUM(CASE WHEN status = 'denied' THEN charge_amount ELSE 0 END) / SUM(charge_amount)"
            " AS denied_dollar_rate, SUM(charge_amount) AS charge_amount\nFROM sem.claims") if dollars else
           ("SELECT payer_name, AVG(CASE WHEN status = 'denied' THEN 1.0 ELSE 0.0 END) AS denial_rate, COUNT(*) AS claims"
            "\nFROM sem.claims")) + where + "\nGROUP BY 1\nORDER BY 2 DESC"
    if step == 0:
        return _run_sql(sql)
    return _final(sql, "Denial rate by payer, " + ("weighted by billed dollars" if dollars else "by claim count")
                  + (", last 90 days." if recent else ", all time."), ["Denied means the whole claim was denied."])


#: 1.23.0 demo (§6.1): three questions in one form, the last one multi-select.
MULTI_CLARIFY_LEAD = "A few quick questions so the numbers match what you need:"
MULTI_CLARIFY = [
    {"id": "metric", "header": "Metric", "question": "How should the denial rate be measured?", "options": [
        {"id": "count", "label": "By claim count", "description": "Share of claims that were fully denied"},
        {"id": "dollars", "label": "By billed dollars", "description": "Share of charges on denied claims"}]},
    {"id": "period", "header": "Period", "question": "Which time window?", "options": [
        {"id": "last_90", "label": "Last 90 days", "description": "Service dates in the last 90 days of data"},
        {"id": "last_12m", "label": "Last 12 months", "description": "Service dates in the last 12 months of data"},
        {"id": "all", "label": "All time", "description": "Every service date in the data"}]},
    {"id": "group", "header": "Group by", "question": "How should the results be broken down?", "multiSelect": True,
     "options": [{"id": "payer", "label": "By payer", "description": "One row per payer"},
                 {"id": "month", "label": "By service month", "description": "One row per month of service"}]},
]


def _turn_clarify_multi(model, question, step, cand):
    m = re.search(r"^Clarification:\s*(.*)$", question, re.M)
    if m is None:
        if step == 0:
            return _explore()
        return {"tool_calls": [{"name": "ask", "arguments": {"question": MULTI_CLARIFY_LEAD,
                                                             "questions": MULTI_CLARIFY}}]}
    picked = dict(kv.split(": ", 1) for kv in m.group(1).split("; ") if ": " in kv)
    dollars = "dollar" in picked.get("Metric", "").lower()
    period = picked.get("Period", "")
    days = 90 if "90 days" in period else 365 if "12 months" in period else None
    groups = picked.get("Group by", "")
    keys = [k for k, on in (("payer_name", "payer" in groups.lower()),
                            ("strftime(service_date, '%Y-%m') AS service_month", "month" in groups.lower())) if on]
    keys = keys or ["payer_name"]
    measure = ("SUM(CASE WHEN status = 'denied' THEN charge_amount ELSE 0 END) / SUM(charge_amount) AS denied_dollar_rate"
               if dollars else "AVG(CASE WHEN status = 'denied' THEN 1.0 ELSE 0.0 END) AS denial_rate")
    where = (f"\nWHERE service_date >= (SELECT MAX(service_date) - INTERVAL {days} DAY FROM sem.claims)" if days else "")
    sql = (f"SELECT {', '.join(keys)}, {measure}, COUNT(*) AS claims\nFROM sem.claims{where}\n"
           f"GROUP BY {', '.join(str(i + 1) for i in range(len(keys)))}\nORDER BY {len(keys) + 1} DESC")
    if step == 0:
        return _run_sql(sql)
    how = "weighted by billed dollars" if dollars else "by claim count"
    when = {90: "last 90 days", 365: "last 12 months"}.get(days, "all time")
    return _final(sql, f"Denial rate {how}, {when}.", ["Denied means the whole claim was denied.",
                                                        "Questions you skipped use the default (claim count, all time, by payer)."])


def _turn_escalation_abstain(model, question, step, cand):
    if "haiku" in model.lower():
        if step == 0:
            return _explore()
        return {"text": "I can't answer this reliably: I could not find appeal outcomes in this data."}
    if step == 0:
        return {"text": "Checking whether any view records appeal outcomes.", "tool_calls": [
            {"name": "describe_relation", "arguments": {"relation": "sem.claims"}}]}
    return {"tool_calls": [{"name": "refuse", "arguments": {"category": "data_unavailable", "reason": ABSTAIN_REASON}}]}


EDGE_TURNS = {"empty": _turn_empty, "multi_table": _turn_multi_table, "long_sql": _turn_long_sql,
              "many_repairs": _turn_many_repairs, "clarify_nested": _turn_clarify_nested,
              "clarify_multi": _turn_clarify_multi,
              "escalation_abstain": _turn_escalation_abstain}


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
        # "report" (1.9.0) runs the chart script; api.mapping.report_artifacts reshapes the answer into artifacts.
        # cancel_* (1.16.0) run another script and press Stop at a stage (api.app; CANCEL_AT).
        scripted = scripted_scenario(scenario)
        return ScriptedLLM([scenario_turn(scripted) if scripted else demo_turn] * 8)
    if kind == "anthropic":
        return AnthropicLLM()
    if kind == "bedrock":
        return BedrockLLM()
    raise ValueError(f"unknown llm backend {kind!r}")
