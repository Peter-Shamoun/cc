"""In-process stand-ins for the AWS services in docs/data-backend/design-doc.md.

Everything is local and file-backed, but each call goes through the same checks AWS would make,
so the policies written here are the ones that move to Terraform:
  - IAM identity policies on roles + an S3 bucket policy (explicit Deny wins)
  - a KMS key per customer; reading an object needs kms:Decrypt on the key it was written with
  - Object Lock on raw/ (bronze is write-once)
  - STS temp credentials that expire, and are revoked when an ECS task stops
  - S3 -> EventBridge "Object Created" events, rules, ECS RunTask targets
  - Entra ID + Cognito: a login maps a user to exactly one customer
  - CloudTrail: every decision (allowed or denied) is appended to audit.jsonl

Client method names and arguments follow boto3 (put_object(Bucket=, Key=, Body=) ...), so code
written against these clients ports to boto3 by swapping the factory.
"""
from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import os
import secrets
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ACCOUNT = "111122223333"
REGION = "us-west-2"


class AccessDenied(PermissionError):
    pass


class ExpiredToken(PermissionError):
    pass


class NoSuchKey(KeyError):
    pass


# --- IAM policy evaluation -----------------------------------------------------------------------
def _as_list(v):
    return v if isinstance(v, list) else [v]


def _matches(stmt: dict, action: str, resource: str, principal: str | None) -> bool:
    if not any(fnmatch.fnmatchcase(action, a) for a in _as_list(stmt.get("Action", []))):
        return False
    if not any(fnmatch.fnmatchcase(resource, r) for r in _as_list(stmt.get("Resource", "*"))):
        return False
    if "Principal" in stmt and principal is not None:
        if not any(fnmatch.fnmatchcase(principal, p) for p in _as_list(stmt["Principal"])):
            return False
    if "NotPrincipal" in stmt and principal is not None:
        if any(fnmatch.fnmatchcase(principal, p) for p in _as_list(stmt["NotPrincipal"])):
            return False
    return True


def evaluate(identity_policies: list[dict], resource_policies: list[dict], action: str, resource: str,
             principal: str) -> bool:
    """Same-account evaluation: explicit Deny anywhere -> deny; else any Allow -> allow; else deny."""
    stmts = [(s, None) for p in identity_policies for s in p["Statement"]]
    stmts += [(s, principal) for p in resource_policies for s in p["Statement"]]
    if any(s["Effect"] == "Deny" and _matches(s, action, resource, pr) for s, pr in stmts):
        return False
    return any(s["Effect"] == "Allow" and _matches(s, action, resource, pr) for s, pr in stmts)


@dataclass
class Role:
    name: str
    trust: list[str]                      # principals allowed to assume it ("ecs-tasks", "app-server", ...)
    policies: list[dict]

    @property
    def arn(self):
        return f"arn:aws:iam::{ACCOUNT}:role/{self.name}"


@dataclass
class Credentials:
    AccessKeyId: str
    SecretAccessKey: str
    SessionToken: str
    Expiration: float
    role_arn: str
    session_name: str


@dataclass
class KmsKey:
    key_id: str
    alias: str
    policy: dict

    @property
    def arn(self):
        return f"arn:aws:kms:{REGION}:{ACCOUNT}:key/{self.key_id}"


@dataclass
class Rule:
    name: str
    pattern: dict
    target: Callable[[dict], Any]


@dataclass
class TaskDefinition:
    family: str
    task_role_arn: str
    entrypoint: Callable[["TaskContext"], Any]


@dataclass
class TaskContext:
    aws: "LocalAWS"
    creds: Credentials
    overrides: dict
    task_arn: str

    def client(self, service: str):
        return self.aws.client(service, self.creds)


# --- the account ---------------------------------------------------------------------------------
class LocalAWS:
    def __init__(self, root: str | os.PathLike, clock: Callable[[], float] = time.time):
        self.root = Path(root)
        (self.root / "s3").mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.roles: dict[str, Role] = {}
        self.keys: dict[str, KmsKey] = {}
        self.sessions: dict[str, Credentials] = {}
        self.bucket_policies: dict[str, dict] = {}
        self.object_lock: dict[str, list[str]] = {}        # bucket -> key globs that are write-once
        self.default_kms: dict[str, list[tuple[str, str]]] = {}   # bucket -> [(key glob, kms key id)]
        self.rules: list[Rule] = []
        self.task_defs: dict[str, TaskDefinition] = {}
        self.tasks: list[dict] = []
        self.event_buckets: set[str] = set()

    # CloudTrail
    def audit(self, principal: str, action: str, resource: str, allowed: bool, **extra):
        rec = dict(t=round(self.clock(), 3), principal=principal, action=action, resource=resource,
                   decision="Allow" if allowed else "Deny", **extra)
        with open(self.root / "audit.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")

    def audit_log(self) -> list[dict]:
        p = self.root / "audit.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    # IAM / KMS / bucket admin (what Terraform would create)
    def create_role(self, name: str, trust: list[str], policies: list[dict]) -> Role:
        self.roles[name] = Role(name, trust, policies)
        return self.roles[name]

    def create_key(self, alias: str, policy: dict) -> KmsKey:
        k = KmsKey(str(uuid.uuid4()), alias, policy)
        self.keys[k.key_id] = k
        return k

    def create_bucket(self, bucket: str, policy: dict | None = None, object_lock_globs: list[str] = (),
                      eventbridge: bool = True):
        (self.root / "s3" / bucket).mkdir(parents=True, exist_ok=True)
        self.bucket_policies[bucket] = policy or {"Statement": []}
        self.object_lock[bucket] = list(object_lock_globs)
        self.default_kms.setdefault(bucket, [])
        if eventbridge:
            self.event_buckets.add(bucket)

    def set_default_encryption(self, bucket: str, key_glob: str, kms_key_id: str):
        self.default_kms[bucket].append((key_glob, kms_key_id))

    # STS
    def assume_role(self, caller: str, role_arn: str, session_name: str, duration: int = 900) -> Credentials:
        role = self.roles.get(role_arn.rsplit("/", 1)[-1])
        ok = role is not None and caller in role.trust
        self.audit(caller, "sts:AssumeRole", role_arn, ok, session=session_name)
        if not ok:
            raise AccessDenied(f"{caller} may not assume {role_arn}")
        c = Credentials("ASIA" + secrets.token_hex(8).upper(), secrets.token_hex(20), secrets.token_urlsafe(32),
                        self.clock() + duration, role.arn, session_name)
        self.sessions[c.AccessKeyId] = c
        return c

    def revoke(self, creds: Credentials):
        self.sessions.pop(creds.AccessKeyId, None)

    def _principal(self, creds: Credentials) -> Role:
        live = self.sessions.get(creds.AccessKeyId)
        if live is None or live.SessionToken != creds.SessionToken:
            raise ExpiredToken("credentials revoked or unknown")
        if self.clock() >= live.Expiration:
            raise ExpiredToken("credentials expired")
        return self.roles[live.role_arn.rsplit("/", 1)[-1]]

    def authorize(self, creds: Credentials, action: str, resource: str, resource_policies=(), **extra) -> Role:
        role = self._principal(creds)
        ok = evaluate(role.policies, list(resource_policies), action, resource, role.arn)
        self.audit(role.arn, action, resource, ok, session=creds.session_name, **extra)
        if not ok:
            raise AccessDenied(f"{role.name} denied {action} on {resource}")
        return role

    def kms_check(self, creds: Credentials, key_id: str, action: str):
        key = self.keys[key_id]
        self.authorize(creds, action, key.arn, [key.policy])

    # EventBridge
    def put_rule(self, name: str, pattern: dict, target: Callable[[dict], Any]):
        self.rules.append(Rule(name, pattern, target))

    def put_events(self, event: dict):
        for r in self.rules:
            if _event_matches(r.pattern, event):
                self.audit("events.amazonaws.com", "events:InvokeTarget", r.name, True, detail=event["detail"])
                r.target(event)

    # ECS (Fargate): assume the task role, run, then revoke - no credentials outlive the task
    def register_task_definition(self, family: str, task_role_arn: str, entrypoint):
        self.task_defs[family] = TaskDefinition(family, task_role_arn, entrypoint)

    def run_task(self, family: str, overrides: dict) -> dict:
        td = self.task_defs[family]
        arn = f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/{uuid.uuid4().hex[:12]}"
        creds = self.assume_role("ecs-tasks.amazonaws.com", td.task_role_arn, arn.rsplit("/", 1)[-1], 3600)
        rec = dict(task_arn=arn, family=family, overrides=overrides, status="RUNNING", started=self.clock())
        self.tasks.append(rec)
        try:
            rec["result"] = td.entrypoint(TaskContext(self, creds, overrides, arn))
            rec["status"] = "STOPPED"
            rec["exit_code"] = 0
        except Exception as e:  # task failure is recorded, not raised into EventBridge
            rec.update(status="STOPPED", exit_code=1, error=f"{type(e).__name__}: {e}")
        finally:
            self.revoke(creds)
            rec["stopped"] = self.clock()
        return rec

    def client(self, service: str, creds: Credentials):
        if service == "s3":
            return S3Client(self, creds)
        if service == "kms":
            return KmsClient(self, creds)
        raise ValueError(service)


def _event_matches(pattern: dict, event: dict) -> bool:
    for k, want in pattern.items():
        have = event.get(k)
        if isinstance(want, dict):
            if not isinstance(have, dict) or not _event_matches(want, have):
                return False
        elif isinstance(want, list):
            ok = False
            for w in want:
                if isinstance(w, dict) and "suffix" in w:
                    ok |= isinstance(have, str) and have.endswith(w["suffix"])
                elif isinstance(w, dict) and "prefix" in w:
                    ok |= isinstance(have, str) and have.startswith(w["prefix"])
                else:
                    ok |= have == w
            if not ok:
                return False
        elif have != want:
            return False
    return True


# --- S3 ------------------------------------------------------------------------------------------
class S3Client:
    """Objects are files under <root>/s3/<bucket>/<key>; metadata (KMS key, sha256) sits in a sidecar.
    Bodies are stored in clear on local disk: encryption is enforced as an access check, not as bytes."""

    def __init__(self, aws: LocalAWS, creds: Credentials):
        self.aws, self.creds = aws, creds

    def _path(self, bucket, key) -> Path:
        return self.aws.root / "s3" / bucket / key

    def _meta(self, bucket, key) -> Path:
        return self.aws.root / "s3-meta" / bucket / (key + ".json")

    def _arn(self, bucket, key=""):
        return f"arn:aws:s3:::{bucket}/{key}" if key else f"arn:aws:s3:::{bucket}"

    def _bp(self, bucket):
        return [self.aws.bucket_policies.get(bucket, {"Statement": []})]

    def put_object(self, Bucket: str, Key: str, Body: bytes | str, ServerSideEncryption="aws:kms",
                   SSEKMSKeyId: str | None = None):
        self.aws.authorize(self.creds, "s3:PutObject", self._arn(Bucket, Key), self._bp(Bucket))
        p = self._path(Bucket, Key)
        if p.exists() and any(fnmatch.fnmatchcase(Key, g) for g in self.aws.object_lock.get(Bucket, [])):
            self.aws.audit(self.creds.role_arn, "s3:PutObject", self._arn(Bucket, Key), False, reason="ObjectLock")
            raise AccessDenied(f"object lock: {Key} is write-once")
        kms_id = SSEKMSKeyId or next((k for g, k in self.aws.default_kms.get(Bucket, [])
                                      if fnmatch.fnmatchcase(Key, g)), None)
        if kms_id is None:
            raise AccessDenied("bucket policy requires SSE-KMS with the prefix's customer key")
        expected = next((k for g, k in self.aws.default_kms[Bucket] if fnmatch.fnmatchcase(Key, g)), kms_id)
        if kms_id != expected:
            self.aws.audit(self.creds.role_arn, "s3:PutObject", self._arn(Bucket, Key), False, reason="WrongKmsKey")
            raise AccessDenied("object must be encrypted with this customer's KMS key")
        self.aws.kms_check(self.creds, kms_id, "kms:GenerateDataKey")
        body = Body.encode() if isinstance(Body, str) else Body
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
        m = self._meta(Bucket, Key)
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(json.dumps(dict(kms_key_id=kms_id, sha256=hashlib.sha256(body).hexdigest(),
                                     size=len(body), last_modified=self.aws.clock())))
        if Bucket in self.aws.event_buckets:
            self.aws.put_events({"source": "aws.s3", "detail-type": "Object Created",
                                 "detail": {"bucket": {"name": Bucket}, "object": {"key": Key, "size": len(body)}}})
        return {"ETag": hashlib.md5(body).hexdigest(), "SSEKMSKeyId": self.aws.keys[kms_id].arn}

    def head_object(self, Bucket: str, Key: str) -> dict:
        self.aws.authorize(self.creds, "s3:GetObject", self._arn(Bucket, Key), self._bp(Bucket))
        m = self._meta(Bucket, Key)
        if not m.exists():
            raise NoSuchKey(Key)
        return json.loads(m.read_text())

    def get_object(self, Bucket: str, Key: str) -> dict:
        meta = self.head_object(Bucket, Key)
        self.aws.kms_check(self.creds, meta["kms_key_id"], "kms:Decrypt")
        return {"Body": self._path(Bucket, Key).read_bytes(), **meta}

    def list_objects_v2(self, Bucket: str, Prefix: str = "") -> dict:
        self.aws.authorize(self.creds, "s3:ListBucket", self._arn(Bucket, Prefix + "*"), self._bp(Bucket))
        base = self.aws.root / "s3" / Bucket
        start = base / Prefix if (base / Prefix).is_dir() else (base / Prefix).parent
        keys = sorted(str(p.relative_to(base)) for p in start.rglob("*") if p.is_file()) if start.exists() else []
        return {"Contents": [{"Key": k} for k in keys if k.startswith(Prefix)]}

    def delete_object(self, Bucket: str, Key: str):
        self.aws.authorize(self.creds, "s3:DeleteObject", self._arn(Bucket, Key), self._bp(Bucket))
        if any(fnmatch.fnmatchcase(Key, g) for g in self.aws.object_lock.get(Bucket, [])):
            self.aws.audit(self.creds.role_arn, "s3:DeleteObject", self._arn(Bucket, Key), False, reason="ObjectLock")
            raise AccessDenied(f"object lock: {Key} is write-once")
        self._path(Bucket, Key).unlink(missing_ok=True)
        self._meta(Bucket, Key).unlink(missing_ok=True)

    def copy_object(self, Bucket: str, Key: str, CopySource: dict):
        src = self.get_object(CopySource["Bucket"], CopySource["Key"])
        return self.put_object(Bucket, Key, src["Body"])

    def local_prefix(self, Bucket: str, Prefix: str) -> Path:
        """Stand-in for DuckDB's httpfs + an S3 secret scoped to the prefix: verifies the caller could list
        and decrypt everything under Prefix, then returns the on-disk folder for DuckDB to read."""
        self.aws.authorize(self.creds, "s3:ListBucket", self._arn(Bucket, Prefix + "*"), self._bp(Bucket))
        self.aws.authorize(self.creds, "s3:GetObject", self._arn(Bucket, Prefix + "*"), self._bp(Bucket))
        for kid in {k for g, k in self.aws.default_kms.get(Bucket, []) if fnmatch.fnmatchcase(Prefix + "x", g)}:
            self.aws.kms_check(self.creds, kid, "kms:Decrypt")
        return self._path(Bucket, Prefix)


class KmsClient:
    def __init__(self, aws: LocalAWS, creds: Credentials):
        self.aws, self.creds = aws, creds

    def describe_key(self, KeyId: str):
        return {"KeyMetadata": {"KeyId": KeyId, "Arn": self.aws.keys[KeyId].arn}}


# --- Entra ID + Cognito ----------------------------------------------------------------------------
class IdentityPlane:
    """Two gates (design doc "Auth"): Entra ID must accept the account (tenant + group filter), then
    Cognito must map it to exactly one customer. The app server exchanges the Cognito token for that
    customer's read role via STS."""

    def __init__(self, aws: LocalAWS, entra_tenant: str, allowed_group: str, token_ttl: int = 3600):
        self.aws, self.entra_tenant, self.allowed_group, self.ttl = aws, entra_tenant, allowed_group, token_ttl
        self.entra_users: dict[str, dict] = {}            # upn -> {tenant, groups}
        self.cognito_map: dict[str, str] = {}              # upn -> customer
        self.customer_roles: dict[str, str] = {}           # customer -> read-role ARN
        self._secret = secrets.token_bytes(32)

    def login(self, upn: str) -> str:
        u = self.entra_users.get(upn)
        if not u or u["tenant"] != self.entra_tenant or self.allowed_group not in u["groups"]:
            self.aws.audit(upn, "entra:SignIn", self.entra_tenant, False)
            raise AccessDenied("Entra ID: account not approved")
        customer = self.cognito_map.get(upn)
        if customer is None:
            self.aws.audit(upn, "cognito:InitiateAuth", "user-pool", False)
            raise AccessDenied("Cognito: user not mapped to a customer")
        claims = dict(sub=upn, customer=customer, exp=self.aws.clock() + self.ttl)
        body = json.dumps(claims, sort_keys=True).encode().hex()
        sig = hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()
        self.aws.audit(upn, "cognito:InitiateAuth", "user-pool", True, customer=customer)
        return f"{body}.{sig}"

    def verify(self, token: str) -> dict:
        body, _, sig = token.partition(".")
        if not hmac.compare_digest(sig, hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()):
            raise AccessDenied("bad token signature")
        claims = json.loads(bytes.fromhex(body))
        if self.aws.clock() >= claims["exp"]:
            raise ExpiredToken("session token expired; log in again")
        return claims

    def credentials_for(self, token: str, request_id: str, duration: int = 900) -> tuple[str, Credentials]:
        """AssumeRoleWithWebIdentity stand-in: token -> that customer's short-lived read credentials."""
        claims = self.verify(token)
        role_arn = self.customer_roles[claims["customer"]]
        return claims["customer"], self.aws.assume_role("app-server", role_arn,
                                                         f"{claims['sub']}:{request_id}", duration)


def reset(root: str | os.PathLike):
    shutil.rmtree(root, ignore_errors=True)
