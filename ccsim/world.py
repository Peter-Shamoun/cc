"""One call to stand the whole thing up locally: provision, generate mock EDI, drop day by day."""
from __future__ import annotations

from pathlib import Path

from . import edi_mock
from .aws import LocalAWS, reset
from .infra import provision, sftp_drop
from .pipeline import parse_batch


def build_world(root: str | Path, scenario: edi_mock.Scenario | None = None, customers=("acme_labs", "acme_diagnostics"),
                users=None):
    root = Path(root)
    reset(root)
    aws = LocalAWS(root / "aws")
    idp = provision(aws, list(customers), parse_batch)
    for upn, customer in (users or {"ana@xifin.example": "acme_labs", "dev@xifin.example": "acme_diagnostics"}).items():
        idp.entra_users[upn] = {"tenant": "xifin.example", "groups": ["cc-users"]}
        idp.cognito_map[upn] = customer
    truth = edi_mock.generate(root / "sftp", scenario, list(customers))
    for c in customers:
        for day in truth["tenants"][c]["file_days"]:
            sftp_drop(aws, c, root / "sftp" / c / "raw" / f"received_date={day}", day)
    return aws, idp, truth
