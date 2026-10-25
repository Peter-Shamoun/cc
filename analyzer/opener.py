"""How the tool layer obtains a DuckDB session.

Production uses ``QueryPathOpener``, which calls ``ccsim.query.open_session``
(Cognito token, STS, manifest, per-relation secret and allowed_directories).
Screening uses ``SnapshotOpener`` over a frozen bench snapshot. Both return the
same handle. The tool layer never constructs a connection itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass
class SessionHandle:
    customer: str
    user: str
    version: str
    con: Any


class SessionOpener(Protocol):
    def open(self, escalate: bool) -> SessionHandle: ...


class QueryPathOpener:
    """Open a per-request session through ``ccsim.query.open_session`` only."""

    def __init__(self, aws, deployment, id_token: str, request_id: str | None = None):
        self.aws = aws
        self.deployment = deployment
        self.id_token = id_token
        self.request_id = request_id

    def open(self, escalate: bool) -> SessionHandle:
        from ccsim.query import open_session
        session = open_session(self.aws, self.deployment, self.id_token, escalate=escalate,
                               request_id=self.request_id)
        return SessionHandle(customer=session.customer, user=session.user, version=session.version, con=session.con)


class SnapshotOpener:
    """Open the bench snapshot sandbox. Escalation reopens it with lossless views mounted."""

    def __init__(self, snapshot_path: str | Path, user: str = "analyst"):
        self.snapshot_path = Path(snapshot_path)
        import json
        meta = json.loads((self.snapshot_path / "meta.json").read_text())
        self.customer = meta["customer"]
        self.version = str(meta.get("gold_version", ""))
        self.user = user

    def open(self, escalate: bool) -> SessionHandle:
        from bench.dataset import open_snapshot
        return SessionHandle(self.customer, self.user, self.version,
                             open_snapshot(self.snapshot_path, escalate=escalate))
