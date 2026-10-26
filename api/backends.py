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

The model is chosen in ``api.strands_analyzer`` (the scripted demo model, or Bedrock behind the paid switch).
"""
from __future__ import annotations

import datetime as dt
import random
import re
import threading
from dataclasses import dataclass
from typing import Protocol

import duckdb

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
