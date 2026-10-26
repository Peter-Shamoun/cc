"""Stand the whole backend up locally: provision the account, create users, generate mock EDI, and
deliver it day by day through IT's SFTP copy, EventBridge and the Fargate parser."""
from __future__ import annotations

from pathlib import Path

from . import edi_mock
from .aws import LocalAWS, reset
from .aws.identity import CognitoUser
from .infra import Deployment, provision, sftp_drop
from .pipeline import parse_batch

DEFAULT_USERS = {"ana@xifin.example": "acme_labs", "dev@xifin.example": "acme_diagnostics"}


def add_user(aws: LocalAWS, upn: str, customer: str | None, groups=("cc-users",)):
    """Entra account + the Cognito user an admin maps to one customer (custom:customer)."""
    u = aws.entra.add_user(upn, list(groups))
    name = f"EntraID_{u.oid}"
    attrs = {"email": u.upn, "sub": f"sub-{u.oid[:8]}"}
    if customer:
        attrs["custom:customer"] = customer
    if "cc-customer-admins" in groups:
        attrs["custom:transcript_admin"] = "true"
    aws.cognito.users[name] = CognitoUser(name, attrs)
    return name


class World:
    def __init__(self, aws: LocalAWS, dep: Deployment, truth: dict, sftp_root: Path):
        self.aws, self.dep, self.truth, self.sftp_root = aws, dep, truth, sftp_root

    def deliver(self, customer: str, day: str):
        sftp_drop(self.aws, self.dep, customer, self.sftp_root / customer / "raw" / f"received_date={day}", day)
        self.aws.events.drain()

    def tasks(self):
        return self.aws.ecs.tasks


def build_world(root, scenario: edi_mock.Scenario | None = None, customers=("acme_labs", "acme_diagnostics"),
                users: dict | None = None, latency_ms: float = 0.0, deliver: bool = True) -> World:
    root = Path(root)
    reset(root)
    aws = LocalAWS(root / "aws")
    aws.serve(latency_ms=latency_ms)
    dep = provision(aws, list(customers), parse_batch)
    aws.events.auto_drain = False                    # S3 -> EventBridge -> ECS is asynchronous, as in AWS
    for upn, c in (users or {u: c for u, c in DEFAULT_USERS.items() if c in customers}).items():
        add_user(aws, upn, c)
    edi_mock.generate(root / "sftp", scenario, list(customers), return_claims=False)
    truth = edi_mock.load_truth(root / "sftp")             # truth.json index + truth/<customer>/claims.jsonl
    w = World(aws, dep, truth, root / "sftp")
    if deliver:
        days = sorted({(d, c) for c in customers for d in truth["tenants"][c]["file_days"]})
        for d, c in days:
            w.deliver(c, d)
    return w
