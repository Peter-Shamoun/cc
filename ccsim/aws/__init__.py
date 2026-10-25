"""LocalAWS: one simulated AWS account (us-west-2, 111122223333) for ClaimClarity.

    aws = LocalAWS(root)                       # control plane: aws.iam, aws.kms, aws.s3, aws.events, aws.ecs ...
    ep = aws.serve(latency_ms=20)              # S3 + STS over HTTP with SigV4: point boto3 / DuckDB here
    s3 = aws.boto3_client("s3", session)       # stock boto3 client bound to a session's credentials
    s3 = aws.client("s3", session)             # same API in-process (no HTTP), same enforcement

Control-plane calls (create_role, create_key, create_bucket ...) stand for Terraform: they are not
IAM-checked. Data-plane calls always are, and every one is written to CloudTrail (aws.trail).
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from ..audit_chain import HashChainLog
from .cloudtrail import CloudTrail
from .ecs import ECS
from .errors import AWSError
from .events import EventBridge
from .http import Endpoint
from .iam import ACCOUNT, IAM, REGION
from .identity import CognitoUserPool, EntraID
from .kms import KMS
from .s3 import S3, RequestContext
from .sts import STS, Session, service_principal

__all__ = ["LocalAWS", "AWSError", "Session", "ACCOUNT", "REGION", "service_principal"]


class Clock:
    """Wall clock by default; tests `advance()` it to cross expiry and retention boundaries."""

    def __init__(self, start: float | None = None):
        self.offset = 0.0 if start is None else start - time.time()

    def __call__(self) -> float:
        return time.time() + self.offset

    def advance(self, seconds: float):
        self.offset += seconds


class StreamingBody:
    def __init__(self, data: bytes):
        self._data, self._pos = data, 0

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            n = len(self._data) - self._pos
        out = self._data[self._pos:self._pos + n]
        self._pos += len(out)
        return out

    def close(self):
        pass


class _InProcessS3:
    def __init__(self, aws: "LocalAWS", session: Session | None):
        self.aws, self.session = aws, session

    def _rc(self):
        if self.session is None:
            return RequestContext(None, None)
        s = self.aws.sts.lookup(self.session.access_key_id, self.session.session_token)
        return RequestContext(s.principal(), s)

    def __getattr__(self, op):
        fn = getattr(self.aws.s3, op, None)
        if fn is None or op.startswith("_") or op in ("create_bucket", "bucket", "run_lifecycle", "db"):
            raise AttributeError(op)

        def call(**kw):
            out = fn(self._rc(), **kw) or {}
            if "Body" in out:
                out = dict(out, Body=StreamingBody(out["Body"]))
            return out
        return call


class _InProcessSTS:
    def __init__(self, aws, session):
        self.aws, self.session = aws, session

    def assume_role(self, **kw):
        s = self.aws.sts.lookup(self.session.access_key_id, self.session.session_token)
        new = self.aws.sts.assume_role(s.principal(), s, **kw)
        return {"Credentials": new.credentials(), "AssumedRoleUser": {"Arn": new.arn}}

    def assume_role_with_web_identity(self, **kw):
        new = self.aws.sts.assume_role_with_web_identity(**kw)
        return {"Credentials": new.credentials(), "AssumedRoleUser": {"Arn": new.arn}}

    def get_caller_identity(self):
        return self.aws.sts.get_caller_identity(self.aws.sts.lookup(self.session.access_key_id, self.session.session_token))


class LocalAWS:
    def __init__(self, root: str | Path, clock: Clock | None = None, seed: int = 0):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock or Clock()
        self.trail = CloudTrail(self.root / "cloudtrail.jsonl", self.clock)
        self.agent_audit = HashChainLog(self.root / "agent-audit.jsonl", self.clock)
        self.iam = IAM()
        self.kms = KMS(self.clock)
        self.kms.iam = self.iam
        self.sts = STS(self.iam, self.clock, self.trail)
        self.events = EventBridge(self.clock, self._service_assume("events.amazonaws.com"), self.trail, seed)
        self.s3 = S3(self.root, self.clock, self.iam, self.kms, self.events, self.trail)
        self.ecs = ECS(self.iam, self.sts, self.clock, self.trail, account=self)
        self.endpoint: Endpoint | None = None
        self.entra: EntraID | None = None
        self.cognito: CognitoUserPool | None = None

    def _service_assume(self, service: str):
        def assume(role_arn, name):
            return self.sts.assume_role(service_principal(service), None, role_arn, name, 900,
                                        SourceContext={"aws:SourceAccount": ACCOUNT})
        return assume

    # -- identity plane -------------------------------------------------------------------------------
    def create_identity(self, tenant_id: str, assigned_groups: list[str], id_token_ttl: int = 3600):
        self.entra = EntraID(tenant_id, "claimclarity-app", assigned_groups, self.clock)
        self.cognito = CognitoUserPool(self.entra, self.clock, id_token_ttl)
        provider_arn = self.iam.create_open_id_connect_provider(self.cognito.issuer.url, [self.cognito.client_id],
                                                                self.cognito.issuer.jwks)
        return provider_arn

    # -- clients -------------------------------------------------------------------------------------
    def client(self, service: str, session: Session | None = None):
        if service == "s3":
            return _InProcessS3(self, session)
        if service == "sts":
            return _InProcessSTS(self, session)
        raise ValueError(service)

    def serve(self, latency_ms: float = 0.0, bandwidth_mbps: float | None = None, secure_transport: bool = True) -> Endpoint:
        """Start the HTTP endpoint (idempotent). secure_transport=True reports aws:SecureTransport=true, as if a
        TLS-terminating proxy sat in front: DuckDB and boto3 then need no certificates."""
        if self.endpoint is None:
            self.endpoint = Endpoint(self, latency_ms=latency_ms, bandwidth_mbps=bandwidth_mbps,
                                     secure_transport=secure_transport)
        else:
            self.endpoint.set_latency(latency_ms, bandwidth_mbps)
        return self.endpoint

    def boto3_client(self, service: str, session: Session | dict | None = None):
        import boto3
        from botocore import UNSIGNED
        from botocore.config import Config
        ep = self.serve()
        creds = session.credentials() if isinstance(session, Session) else session
        cfg = Config(signature_version=UNSIGNED if creds is None else "s3v4" if service == "s3" else "v4",
                     s3={"addressing_style": "path"}, retries={"max_attempts": 1, "mode": "standard"},
                     request_checksum_calculation="when_required", response_checksum_validation="when_required")
        kw = dict(region_name=REGION, endpoint_url=ep.url, config=cfg)
        if creds:
            kw.update(aws_access_key_id=creds["AccessKeyId"], aws_secret_access_key=creds["SecretAccessKey"],
                      aws_session_token=creds["SessionToken"])
        return boto3.client(service, **kw)

    def duckdb_secret_sql(self, session: Session, scope: str, name: str = "s3_scoped") -> str:
        """CREATE SECRET for DuckDB httpfs, scoped to one prefix (docs/data-backend/duckdb_per_request_connection.py)."""
        ep = self.serve()
        q = lambda s: s.replace("'", "''")
        return (f"CREATE OR REPLACE SECRET {name} (TYPE s3, KEY_ID '{q(session.access_key_id)}', "
                f"SECRET '{q(session.secret_access_key)}', SESSION_TOKEN '{q(session.session_token)}', REGION '{REGION}', "
                f"ENDPOINT '{ep.host_port}', URL_STYLE 'path', USE_SSL false, SCOPE '{q(scope)}')")

    def shutdown(self):
        if self.endpoint:
            self.endpoint.stop()
            self.endpoint = None


def reset(root: str | Path):
    shutil.rmtree(root, ignore_errors=True)
