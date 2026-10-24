"""STS: AssumeRole, AssumeRoleWithWebIdentity, GetCallerIdentity, session tags, session policies,
duration limits, role chaining, and revocation the AWS way (an inline Deny on aws:TokenIssueTime)."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field

from .errors import AccessDenied, ExpiredToken, InvalidAccessKeyId, InvalidIdentityToken, ValidationError
from .iam import ACCOUNT, IAM, Principal, decide
from .identity import verify_jwt

_SESSION_NAME = re.compile(r"[\w+=,.@-]{2,64}")


@dataclass
class Session:
    access_key_id: str
    secret_access_key: str
    session_token: str
    expiration: float
    issued_at: float
    role_arn: str
    session_name: str
    tags: dict = field(default_factory=dict)
    transitive_tag_keys: tuple = ()
    session_policies: tuple = ()
    chained: bool = False
    source: str = "AssumeRole"

    @property
    def role_name(self) -> str:
        return self.role_arn.rsplit("/", 1)[-1]

    @property
    def arn(self) -> str:
        return f"arn:aws:sts::{ACCOUNT}:assumed-role/{self.role_name}/{self.session_name}"

    role_tags: dict = field(default_factory=dict)

    def principal(self) -> Principal:
        """aws:PrincipalTag/* = the role's tags overlaid with the session's tags (session tags win)."""
        return Principal(self.arn, (self.arn, self.role_arn), "AssumedRole", ACCOUNT, {**self.role_tags, **self.tags},
                         self.role_arn, self.session_name, self.issued_at)

    def credentials(self) -> dict:
        """boto3 shape: Credentials block of an AssumeRole response."""
        return {"AccessKeyId": self.access_key_id, "SecretAccessKey": self.secret_access_key,
                "SessionToken": self.session_token, "Expiration": self.expiration}


def service_principal(service: str) -> Principal:
    return Principal(service, (service,), "Service", None, service=service)


class STS:
    def __init__(self, iam: IAM, clock, audit):
        self.iam, self.clock, self.audit = iam, clock, audit
        self.sessions: dict[str, Session] = {}

    # -- authentication of API callers -------------------------------------------------------------
    def lookup(self, access_key_id: str, session_token: str | None) -> Session:
        s = self.sessions.get(access_key_id)
        if s is None:
            raise InvalidAccessKeyId("The AWS Access Key Id you provided does not exist in our records.")
        if session_token != s.session_token:
            raise InvalidAccessKeyId("The security token included in the request is invalid.", code="InvalidToken")
        if self.clock() >= s.expiration:
            raise ExpiredToken("The provided token has expired.")
        return s

    # -- issuing -------------------------------------------------------------------------------------
    def _issue(self, role, session_name, duration, tags, transitive, policies, chained, source) -> Session:
        now = self.clock()
        s = Session("ASIA" + secrets.token_hex(8).upper(), secrets.token_urlsafe(30), secrets.token_urlsafe(48),
                    now + duration, now, role.arn, session_name, dict(tags), tuple(transitive), tuple(policies),
                    chained, source, dict(role.tags))
        self.sessions[s.access_key_id] = s
        return s

    def _validate(self, role_arn, session_name, duration, policy, chained):
        role = self.iam.role_by_arn(role_arn)
        if role is None:
            raise AccessDenied(f"role {role_arn} does not exist or is not assumable")
        if not _SESSION_NAME.fullmatch(session_name or ""):
            raise ValidationError("RoleSessionName must match [\\w+=,.@-]{2,64}")
        limit = 3600 if chained else role.max_session_duration
        if not 900 <= duration <= limit:
            raise ValidationError(f"DurationSeconds must be 900-{limit}")
        if policy is not None and len(str(policy)) > 2048:
            raise ValidationError("session policy too large")
        return role

    def assume_role(self, caller: Principal, caller_session: Session | None, RoleArn: str, RoleSessionName: str,
                    DurationSeconds: int = 3600, Policy: dict | None = None, Tags: list | None = None,
                    TransitiveTagKeys: list | None = None, ExternalId: str | None = None,
                    SourceContext: dict | None = None) -> Session:
        """SourceContext: aws:SourceAccount / aws:SourceArn that an AWS service passes when it assumes a
        role for a resource (confused-deputy protection in trust policies)."""
        chained = caller_session is not None
        role = self._validate(RoleArn, RoleSessionName, DurationSeconds, Policy, chained)
        tags = {}
        transitive = []
        if caller_session:                                     # transitive tags flow down a role chain
            tags.update({k: caller_session.tags[k] for k in caller_session.transitive_tag_keys if k in caller_session.tags})
            transitive += list(caller_session.transitive_tag_keys)
        new_tags = {t["Key"]: t["Value"] for t in Tags or []}
        if set(new_tags) & set(tags):
            raise ValidationError("cannot override a transitive session tag")
        tags.update(new_tags)
        transitive += list(TransitiveTagKeys or [])
        ctx = {"sts:RoleSessionName": RoleSessionName, "aws:CurrentTime": self.clock(), **(SourceContext or {})}
        if ExternalId:
            ctx["sts:ExternalId"] = ExternalId
        for k, v in new_tags.items():
            ctx[f"aws:RequestTag/{k}"] = v
        if new_tags:
            ctx["aws:TagKeys"] = list(new_tags)
        actions = ["sts:AssumeRole"] + (["sts:TagSession"] if new_tags else [])
        for action in actions:
            if caller_session is not None:
                r = self.iam.role_by_arn(caller_session.role_arn)
                d = decide(caller, action, RoleArn, ctx, r.policies if r else [], role.trust_policy,
                           caller_session.session_policies, r.boundary if r else None)
            else:                                              # AWS service principal
                d = decide(caller, action, RoleArn, ctx, [], role.trust_policy)
            if not d:
                self.audit.record("sts.amazonaws.com", "AssumeRole", caller, {"roleArn": RoleArn,
                                  "roleSessionName": RoleSessionName}, error=("AccessDenied", d.reason), resources=[RoleArn])
                raise AccessDenied(f"{caller.arn} is not authorized to perform: {action} on resource: {RoleArn}")
        s = self._issue(role, RoleSessionName, DurationSeconds, tags, transitive, [Policy] if Policy else [],
                        chained, "AssumeRole")
        self.audit.record("sts.amazonaws.com", "AssumeRole", caller, {"roleArn": RoleArn, "roleSessionName": RoleSessionName,
                          "durationSeconds": DurationSeconds, "tags": new_tags}, resources=[RoleArn],
                          response={"assumedRoleUser": s.arn, "accessKeyId": s.access_key_id})
        return s

    def assume_role_with_web_identity(self, RoleArn: str, RoleSessionName: str, WebIdentityToken: str,
                                      DurationSeconds: int = 3600, Policy: dict | None = None) -> Session:
        role = self._validate(RoleArn, RoleSessionName, DurationSeconds, Policy, False)
        try:
            payload = WebIdentityToken.split(".")[1]
            unverified_iss = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("iss", "")
        except Exception as e:
            raise InvalidIdentityToken("malformed token") from e
        prov_arn, prov = next(((a, p) for a, p in self.iam.oidc_providers.items() if p["url"] == unverified_iss),
                              (None, None))
        if prov is None:
            raise InvalidIdentityToken("No OpenIDConnect provider found in your account for " + unverified_iss)
        claims = verify_jwt(WebIdentityToken, prov["jwks"](), prov["url"], prov["client_ids"], self.clock())
        hp = prov["host_path"]
        aws_tags = claims.get("https://aws.amazon.com/tags", {})
        tags = {k: (v[0] if isinstance(v, list) else v) for k, v in aws_tags.get("principal_tags", {}).items()}
        ctx = {f"{hp}:aud": claims["aud"], f"{hp}:sub": claims["sub"], "sts:RoleSessionName": RoleSessionName,
               "aws:CurrentTime": self.clock()}
        for k, v in tags.items():
            ctx[f"aws:RequestTag/{k}"] = v
        if tags:
            ctx["aws:TagKeys"] = list(tags)
        caller = Principal(f"{prov_arn}/{claims['sub']}", (claims["sub"],), "WebIdentityUser", federated=prov_arn)
        for action in ["sts:AssumeRoleWithWebIdentity"] + (["sts:TagSession"] if tags else []):
            d = decide(caller, action, RoleArn, ctx, [], role.trust_policy)
            if not d:
                self.audit.record("sts.amazonaws.com", "AssumeRoleWithWebIdentity", caller,
                                  {"roleArn": RoleArn, "roleSessionName": RoleSessionName},
                                  error=("AccessDenied", d.reason), resources=[RoleArn])
                raise AccessDenied(f"Not authorized to perform {action} on {RoleArn}")
        s = self._issue(role, RoleSessionName, DurationSeconds, tags, aws_tags.get("transitive_tag_keys", []),
                        [Policy] if Policy else [], False, "AssumeRoleWithWebIdentity")
        self.audit.record("sts.amazonaws.com", "AssumeRoleWithWebIdentity", caller,
                          {"roleArn": RoleArn, "roleSessionName": RoleSessionName, "tags": tags}, resources=[RoleArn],
                          response={"assumedRoleUser": s.arn, "accessKeyId": s.access_key_id})
        return s

    def get_caller_identity(self, session: Session) -> dict:
        return {"UserId": "AROA" + hashlib.sha1(session.role_arn.encode()).hexdigest()[:17].upper() + f":{session.session_name}",
                "Account": ACCOUNT, "Arn": session.arn}

    # -- revocation: what the console's "Revoke active sessions" does ------------------------------
    def revoke_older_sessions(self, role_name: str):
        self.iam.put_role_policy(role_name, "AWSRevokeOlderSessions", {"Version": "2012-10-17", "Statement": [{
            "Effect": "Deny", "Action": "*", "Resource": "*",
            "Condition": {"DateLessThan": {"aws:TokenIssueTime": self.clock() + 1e-6}}}]})
