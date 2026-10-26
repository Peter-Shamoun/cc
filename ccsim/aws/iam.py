"""IAM policy language and evaluation (same-account subset, faithful where it matters for this design).

Supported: Statement as dict or list; Sid; Effect; Action / NotAction; Resource / NotResource;
Principal / NotPrincipal ("*", {"AWS": ...}, {"Service": ...}, {"Federated": ...}); Condition with
String*, Arn*, Bool, Numeric*, Date*, Null, IfExists, ForAnyValue: / ForAllValues:; policy variables
${aws:PrincipalTag/k}, ${aws:username}, ${aws:userid}, ${aws:PrincipalAccount}, ${*}, ${?}, ${$}.

Decision logic (https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_evaluation-logic.html):
  1. explicit Deny in any applicable policy                       -> Deny
  2. resource policy Allow naming the principal (same account)    -> Allow (session policies still apply
     unless the resource policy names the session ARN itself)
  3. identity policy Allow (AND every session policy Allow, AND the permissions boundary Allow) -> Allow
  4. otherwise                                                     -> implicit Deny
KMS is different: the key policy is authoritative, identity policies count only when the key policy
delegates to the account (`"Principal": {"AWS": "arn:aws:iam::<acct>:root"}`). Use `require_resource_allow`.
"""
from __future__ import annotations

import fnmatch
import ipaddress
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

ACCOUNT = "111122223333"
REGION = "us-west-2"


def statements(policy: dict | None) -> list[dict]:
    if not policy:
        return []
    st = policy.get("Statement", [])
    return [st] if isinstance(st, dict) else list(st)


def _list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _glob(pattern: str, value: str, ignore_case: bool = False) -> bool:
    """IAM wildcard: * any run, ? one char. No character classes (fnmatch's [] would be wrong)."""
    rx = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern)
    return re.fullmatch(rx, value, re.IGNORECASE | re.DOTALL if ignore_case else re.DOTALL) is not None


_VAR = re.compile(r"\$\{([^}]+)\}")


class _Unresolved(Exception):
    pass


def substitute(text: str, ctx: dict) -> str:
    """Policy variables. An unresolvable variable makes the element not match (AWS behaviour)."""
    def rep(m):
        name = m.group(1)
        if name in ("*", "?", "$"):
            return {"*": "*", "?": "?", "$": "$"}[name]          # escaped literals (approximation)
        if "," in name:                                          # ${aws:x, 'default'}
            name, default = (s.strip() for s in name.split(",", 1))
            v = ctx.get(name)
            return str(v) if v is not None else default.strip("'")
        v = ctx.get(name)
        if v is None or isinstance(v, list):
            raise _Unresolved(name)
        return str(v)
    return _VAR.sub(rep, text)


@dataclass
class Principal:
    """Who is calling. `arns` lists every ARN that identifies the caller: the role ARN and the
    assumed-role session ARN for STS sessions, the service name for AWS service principals."""
    arn: str                                   # display / CloudTrail ARN
    arns: tuple[str, ...]
    kind: str                                  # AssumedRole | Service | Root | WebIdentityUser | Anonymous
    account: str = ACCOUNT
    tags: dict = field(default_factory=dict)
    role_arn: str | None = None
    session_name: str | None = None
    token_issue_time: float | None = None
    service: str | None = None
    federated: str | None = None

    def matches(self, principal_element, for_not_principal: bool = False) -> bool:
        """Principal elements match exactly: "*", an account id / root ARN, or a full ARN (IAM rejects partial
        wildcards such as role/cc-* in Principal). For NotPrincipal an assumed-role session is exempt only if
        its session ARN is listed; listing the role ARN alone does not exempt its sessions."""
        if principal_element == "*":
            return True
        if isinstance(principal_element, str):
            principal_element = {"AWS": principal_element}
        own = (self.arn,) if (for_not_principal and self.kind == "AssumedRole") else self.arns
        for typ, vals in principal_element.items():
            for v in _list(vals):
                if v == "*":
                    return True
                if typ == "AWS":
                    if self.account and v in (self.account, f"arn:aws:iam::{self.account}:root") \
                            and self.kind not in ("Service", "Anonymous"):
                        return True
                    if v in own:
                        return True
                elif typ == "Service" and self.service == v:
                    return True
                elif typ == "Federated" and self.federated == v:
                    return True
        return False


def _action_match(patterns, action: str) -> bool:
    return any(_glob(p, action, ignore_case=True) for p in _list(patterns))


def _resource_match(patterns, resource: str, ctx: dict) -> bool:
    for p in _list(patterns):
        try:
            if _glob(substitute(p, ctx), resource):
                return True
        except _Unresolved:
            continue
    return False


def _as_number(v):
    return float(v)


def _as_date(v):
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v)
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def _ip_match(pattern: str, value: str) -> bool:
    try:
        return ipaddress.ip_address(value) in ipaddress.ip_network(pattern, strict=False)
    except ValueError:
        return False


_OPS = {
    "StringEquals": lambda p, v: str(v) == p,
    "StringNotEquals": lambda p, v: str(v) == p,             # negation applied below
    "StringEqualsIgnoreCase": lambda p, v: str(v).lower() == p.lower(),
    "StringNotEqualsIgnoreCase": lambda p, v: str(v).lower() == p.lower(),
    "StringLike": lambda p, v: _glob(p, str(v)),
    "StringNotLike": lambda p, v: _glob(p, str(v)),
    "ArnEquals": lambda p, v: str(v) == p,
    "ArnNotEquals": lambda p, v: str(v) == p,
    "ArnLike": lambda p, v: _glob(p, str(v)),
    "ArnNotLike": lambda p, v: _glob(p, str(v)),
    "Bool": lambda p, v: str(v).lower() == str(p).lower(),
    "NumericEquals": lambda p, v: _as_number(v) == _as_number(p),
    "NumericNotEquals": lambda p, v: _as_number(v) == _as_number(p),
    "NumericLessThan": lambda p, v: _as_number(v) < _as_number(p),
    "NumericLessThanEquals": lambda p, v: _as_number(v) <= _as_number(p),
    "NumericGreaterThan": lambda p, v: _as_number(v) > _as_number(p),
    "NumericGreaterThanEquals": lambda p, v: _as_number(v) >= _as_number(p),
    "DateEquals": lambda p, v: _as_date(v) == _as_date(p),
    "DateNotEquals": lambda p, v: _as_date(v) == _as_date(p),
    "DateLessThan": lambda p, v: _as_date(v) < _as_date(p),
    "DateLessThanEquals": lambda p, v: _as_date(v) <= _as_date(p),
    "DateGreaterThan": lambda p, v: _as_date(v) > _as_date(p),
    "DateGreaterThanEquals": lambda p, v: _as_date(v) >= _as_date(p),
    "IpAddress": lambda p, v: _ip_match(p, str(v)),
    "NotIpAddress": lambda p, v: _ip_match(p, str(v)),
}
_NEGATED = {"StringNotEquals", "StringNotEqualsIgnoreCase", "StringNotLike", "ArnNotEquals", "ArnNotLike",
            "NumericNotEquals", "DateNotEquals", "NotIpAddress"}
_SUBSTITUTES = {"StringEquals", "StringNotEquals", "StringEqualsIgnoreCase", "StringNotEqualsIgnoreCase",
                "StringLike", "StringNotLike", "ArnEquals", "ArnNotEquals", "ArnLike", "ArnNotLike"}


def _lookup(ctx: dict, key: str):
    if key in ctx:
        return ctx[key]
    low = key.lower()                                          # condition keys are case-insensitive
    for k, v in ctx.items():
        if k.lower() == low:
            return v
    return None


def condition_ok(cond: dict | None, ctx: dict) -> bool:
    for raw_op, pairs in (cond or {}).items():
        op, set_op = raw_op, None
        if ":" in op:
            set_op, op = op.split(":", 1)
        if_exists = op.endswith("IfExists")
        if if_exists:
            op = op[: -len("IfExists")]
        for key, wanted in pairs.items():
            have = _lookup(ctx, key)
            wanted = [str(w) if not isinstance(w, bool) else str(w).lower() for w in _list(wanted)]
            if op == "Null":
                if (have is None) != (wanted[0].lower() == "true"):
                    return False
                continue
            if op not in _OPS:
                return False                                   # unknown operator: never matches
            if have is None or have == []:
                if set_op == "ForAnyValue":
                    return False                               # ForAnyValue over no values is false
                if if_exists or op in _NEGATED or set_op == "ForAllValues":
                    continue
                return False
            subs = []
            for w in wanted:
                if op in _SUBSTITUTES:
                    try:
                        subs.append(substitute(w, ctx))
                    except _Unresolved:
                        continue
                else:
                    subs.append(w)
            values = have if isinstance(have, list) else [have]
            fn = _OPS[op]

            def one(v):
                try:
                    hit = any(fn(p, v) for p in subs)
                except (ValueError, TypeError):
                    hit = False
                return not hit if op in _NEGATED else hit
            if set_op == "ForAllValues":
                ok = all(one(v) for v in values)
            elif set_op == "ForAnyValue" or len(values) > 1:
                ok = any(one(v) for v in values)
            else:
                ok = one(values[0])
            if not ok:
                return False
    return True


def statement_applies(st: dict, action: str, resource: str, ctx: dict, principal: Principal | None,
                      is_resource_policy: bool) -> bool:
    if "Action" in st and not _action_match(st["Action"], action):
        return False
    if "NotAction" in st and _action_match(st["NotAction"], action):
        return False
    if "Resource" in st and not _resource_match(st["Resource"], resource, ctx):
        return False
    if "NotResource" in st and _resource_match(st["NotResource"], resource, ctx):
        return False
    if is_resource_policy:
        if "Principal" in st and not (principal and principal.matches(st["Principal"])):
            return False
        if "NotPrincipal" in st and principal and principal.matches(st["NotPrincipal"], for_not_principal=True):
            return False
        if "Principal" not in st and "NotPrincipal" not in st:
            return False                                       # resource policy statements must name principals
    return condition_ok(st.get("Condition"), ctx)


@dataclass
class Decision:
    allowed: bool
    reason: str                                                # explicitDeny | implicitDeny | allowed
    by: str = ""                                               # which policy/Sid decided

    def __bool__(self):
        return self.allowed


def _scan(policies, effect, action, resource, ctx, principal, is_resource):
    for label, pol in policies:
        for i, st in enumerate(statements(pol)):
            if st.get("Effect") == effect and statement_applies(st, action, resource, ctx, principal, is_resource):
                return f"{label}:{st.get('Sid', i)}"
    return None


def _names_directly(policy, action, resource, ctx, principal) -> bool:
    """True if an Allow statement matches the principal by something other than the account root."""
    roots = (principal.account, f"arn:aws:iam::{principal.account}:root")
    for st in statements(policy):
        if st.get("Effect") != "Allow" or not statement_applies(st, action, resource, ctx, principal, True):
            continue
        pe = st.get("Principal")
        if pe == "*":
            return True
        pe = {"AWS": pe} if isinstance(pe, str) else pe
        for typ, vals in pe.items():
            for v in _list(vals):
                if v not in roots and Principal.matches(principal, {typ: v}):
                    return True
    return False


def decide(principal: Principal, action: str, resource: str, ctx: dict, identity_policies=(),
           resource_policy: dict | None = None, session_policies=(), boundary: dict | None = None,
           require_resource_allow: bool = False) -> Decision:
    ctx = dict(ctx)
    if principal.kind in ("Service", "Anonymous"):
        if principal.kind == "Service":
            ctx.setdefault("aws:PrincipalIsAWSService", True)
            ctx.setdefault("aws:PrincipalServiceName", principal.service)
    else:
        ctx.setdefault("aws:PrincipalArn", principal.role_arn or principal.arn)
        ctx.setdefault("aws:PrincipalAccount", principal.account)
        ctx.setdefault("aws:PrincipalIsAWSService", False)
    ctx.setdefault("aws:PrincipalType", principal.kind)
    if principal.token_issue_time is not None:
        ctx.setdefault("aws:TokenIssueTime", principal.token_issue_time)
    for k, v in principal.tags.items():
        ctx.setdefault(f"aws:PrincipalTag/{k}", v)
    ident = [(f"identity[{i}]", p) for i, p in enumerate(identity_policies)]
    sess = [(f"session[{i}]", p) for i, p in enumerate(session_policies)]
    bnd = [("boundary", boundary)] if boundary else []
    res = [("resource", resource_policy)] if resource_policy else []

    deny = (_scan(ident + sess + bnd, "Deny", action, resource, ctx, principal, False)
            or _scan(res, "Deny", action, resource, ctx, principal, True))
    if deny:
        return Decision(False, "explicitDeny", deny)

    res_allow = _scan(res, "Allow", action, resource, ctx, principal, True)
    if require_resource_allow and not res_allow:               # KMS: the key policy must allow
        return Decision(False, "implicitDeny", "key policy")
    if res_allow and _names_directly(resource_policy, action, resource, ctx, principal):
        # A resource policy naming the principal grants on its own (same account). Session policies still
        # cap it unless the statement names the session ARN itself.
        names_session = any(principal.arn == a for st in statements(resource_policy) if st.get("Effect") == "Allow"
                            and isinstance(st.get("Principal"), dict)
                            for a in _list(st["Principal"].get("AWS")))
        if names_session:
            return Decision(True, "allowed", res_allow)
        if not all(_scan([s], "Allow", action, resource, ctx, principal, False) for s in sess):
            return Decision(False, "implicitDeny", "session policy")
        if bnd and not _scan(bnd, "Allow", action, resource, ctx, principal, False):
            return Decision(False, "implicitDeny", "permissions boundary")
        return Decision(True, "allowed", res_allow)
    # No resource policy, or it only delegates to the account root: identity policies decide.
    id_allow = _scan(ident, "Allow", action, resource, ctx, principal, False)
    if not id_allow:
        return Decision(False, "implicitDeny", "no identity allow")
    if any(not _scan([s], "Allow", action, resource, ctx, principal, False) for s in sess):
        return Decision(False, "implicitDeny", "session policy")
    if bnd and not _scan(bnd, "Allow", action, resource, ctx, principal, False):
        return Decision(False, "implicitDeny", "permissions boundary")
    return Decision(True, "allowed", id_allow)


class MalformedPolicy(ValueError):
    code = "MalformedPolicyDocument"


_KNOWN_OPS = set(_OPS) | {"Null"}


def validate_policy(doc: dict, resource_policy: bool = False, principal_exists=None):
    """What IAM / S3 / KMS check when a policy is written: structure, known condition operators, principals
    that are exact ARNs (no partial wildcards) and, when `principal_exists` is given, that they exist."""
    if not isinstance(doc, dict) or "Statement" not in doc:
        raise MalformedPolicy("policy must have a Statement")
    for st in statements(doc):
        if st.get("Effect") not in ("Allow", "Deny"):
            raise MalformedPolicy("Effect must be Allow or Deny")
        if ("Action" in st) == ("NotAction" in st):
            raise MalformedPolicy("exactly one of Action / NotAction")
        if resource_policy and ("Principal" in st) == ("NotPrincipal" in st):
            raise MalformedPolicy("resource policies need exactly one of Principal / NotPrincipal")
        if not resource_policy and ("Principal" in st or "NotPrincipal" in st):
            raise MalformedPolicy("identity policies cannot have Principal")
        for raw_op in (st.get("Condition") or {}):
            op = raw_op.split(":", 1)[-1]
            op = op[:-len("IfExists")] if op.endswith("IfExists") else op
            if op not in _KNOWN_OPS:
                raise MalformedPolicy(f"unknown condition operator {raw_op}")
        for key in ("Principal", "NotPrincipal"):
            pe = st.get(key)
            if pe is None or pe == "*":
                continue
            pe = {"AWS": pe} if isinstance(pe, str) else pe
            for typ, vals in pe.items():
                for v in _list(vals):
                    if v != "*" and "*" in v:
                        raise MalformedPolicy(f"invalid principal {v}: partial wildcards are not allowed")
                    if typ == "AWS" and principal_exists and v != "*" and ":role/" in v and not principal_exists(v):
                        raise MalformedPolicy(f"invalid principal in policy: {v}")
    return doc


@dataclass
class Role:
    name: str
    trust_policy: dict
    inline_policies: dict = field(default_factory=dict)        # name -> document
    max_session_duration: int = 3600
    boundary: dict | None = None
    tags: dict = field(default_factory=dict)
    path: str = "/"

    @property
    def arn(self) -> str:
        return f"arn:aws:iam::{ACCOUNT}:role{self.path}{self.name}"

    @property
    def policies(self) -> list[dict]:
        return list(self.inline_policies.values())


class IAM:
    def __init__(self):
        self.roles: dict[str, Role] = {}
        self.oidc_providers: dict[str, dict] = {}              # arn -> {url, client_ids, jwks_fn}

    def create_role(self, name: str, trust_policy: dict, max_session_duration: int = 3600, tags=None,
                    boundary: dict | None = None) -> Role:
        if not 3600 <= max_session_duration <= 43200:
            from .errors import ValidationError
            raise ValidationError("MaxSessionDuration must be between 3600 and 43200 seconds")
        validate_policy(trust_policy, resource_policy=True, principal_exists=self.role_exists)
        self.roles[name] = Role(name, trust_policy, {}, max_session_duration, boundary, dict(tags or {}))
        return self.roles[name]

    def role_exists(self, arn: str) -> bool:
        return self.role_by_arn(arn) is not None

    def put_role_policy(self, role: str, name: str, document: dict):
        validate_policy(document)
        self.roles[role].inline_policies[name] = document

    def tag_role(self, role: str, tags: dict):
        self.roles[role].tags.update(tags)

    def role_by_arn(self, arn: str) -> Role | None:
        name = arn.rsplit("/", 1)[-1]
        r = self.roles.get(name)
        return r if r and r.arn == arn else None

    def create_open_id_connect_provider(self, url: str, client_ids: list[str], jwks_fn) -> str:
        host_path = url.removeprefix("https://")
        arn = f"arn:aws:iam::{ACCOUNT}:oidc-provider/{host_path}"
        self.oidc_providers[arn] = {"url": url, "host_path": host_path, "client_ids": list(client_ids), "jwks": jwks_fn}
        return arn
