"""Sign-in: Microsoft Entra ID (gate 1) federated into a Cognito user pool (gate 2).

Tokens are real RS256 JWTs signed with per-issuer RSA keys, verified against each issuer's JWKS, so
the app-server and STS code paths are the ones prod runs. The Cognito id token carries the user's
customer in `custom:customer` and as STS session tags (`https://aws.amazon.com/tags`), which the
customer read role's trust policy checks, so a token for customer A can never assume customer B's role.
"""
from __future__ import annotations

import base64
import json
import secrets
import uuid
from dataclasses import dataclass, field

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .errors import AuthError, InvalidIdentityToken
from .iam import REGION


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Issuer:
    def __init__(self, url: str, clock):
        self.url, self.clock = url, clock
        self.kid = uuid.uuid4().hex[:16]
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def sign(self, claims: dict) -> str:
        header = {"alg": "RS256", "typ": "JWT", "kid": self.kid}
        signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}"
        sig = self._key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{signing_input}.{_b64(sig)}"

    def jwks(self) -> dict:
        return {"keys": [{"kid": self.kid, "kty": "RSA", "alg": "RS256", "use": "sig",
                          "pem": self._key.public_key().public_bytes(
                              serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()}]}


def verify_jwt(token: str, jwks: dict, issuer: str, audience: str | list[str] | None, now: float,
               leeway: int = 0) -> dict:
    try:
        h64, c64, s64 = token.split(".")
        header, claims = json.loads(_unb64(h64)), json.loads(_unb64(c64))
    except (ValueError, json.JSONDecodeError) as e:
        raise InvalidIdentityToken("malformed JWT") from e
    if header.get("alg") != "RS256":
        raise InvalidIdentityToken("unsupported alg")              # blocks alg=none / HS256 confusion
    key = next((k for k in jwks["keys"] if k["kid"] == header.get("kid")), None)
    if key is None:
        raise InvalidIdentityToken("unknown kid")
    pub = serialization.load_pem_public_key(key["pem"].encode())
    try:
        pub.verify(_unb64(s64), f"{h64}.{c64}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except Exception as e:
        raise InvalidIdentityToken("bad signature") from e
    if claims.get("iss") != issuer:
        raise InvalidIdentityToken("wrong issuer")
    auds = audience if isinstance(audience, list) else [audience] if audience else []
    tok_aud = claims.get("aud")
    tok_auds = tok_aud if isinstance(tok_aud, list) else [tok_aud]
    if auds and not set(auds) & set(tok_auds):
        raise InvalidIdentityToken("wrong audience")
    if now >= claims.get("exp", 0) + leeway:
        raise InvalidIdentityToken("token expired", code="ExpiredTokenException")
    if claims.get("nbf") and now + leeway < claims["nbf"]:
        raise InvalidIdentityToken("token not yet valid")
    return claims


@dataclass
class EntraUser:
    upn: str
    oid: str
    groups: list[str]
    enabled: bool = True


class EntraID:
    """One Entra tenant with one app registration for CC. `assignment_required` mirrors
    "Assignment required?" on the enterprise app: only members of the assigned groups get a token."""

    def __init__(self, tenant_id: str, client_id: str, assigned_groups: list[str], clock, token_ttl: int = 3600):
        self.tenant_id, self.client_id, self.assigned_groups = tenant_id, client_id, list(assigned_groups)
        self.issuer = Issuer(f"https://login.microsoftonline.com/{tenant_id}/v2.0", clock)
        self.clock, self.ttl = clock, token_ttl
        self.users: dict[str, EntraUser] = {}
        self.sign_in_log: list[dict] = []

    def add_user(self, upn: str, groups: list[str], enabled: bool = True) -> EntraUser:
        self.users[upn.lower()] = EntraUser(upn.lower(), str(uuid.uuid4()), list(groups), enabled)
        return self.users[upn.lower()]

    def sign_in(self, upn: str) -> str:
        u = self.users.get(upn.lower())
        def fail(code, msg):
            self.sign_in_log.append(dict(t=self.clock(), upn=upn, result=code))
            raise AuthError(msg, code=code)
        if u is None:
            fail("AADSTS50034", "user account does not exist in this tenant")
        if not u.enabled:
            fail("AADSTS50057", "user account is disabled")
        if not set(u.groups) & set(self.assigned_groups):
            fail("AADSTS50105", "user is not assigned to a role for the application")
        now = int(self.clock())
        self.sign_in_log.append(dict(t=now, upn=u.upn, result="success"))
        return self.issuer.sign({"iss": self.issuer.url, "aud": self.client_id, "tid": self.tenant_id, "oid": u.oid,
                                 "sub": u.oid, "preferred_username": u.upn, "groups": u.groups,
                                 "iat": now, "nbf": now, "exp": now + self.ttl, "ver": "2.0"})


@dataclass
class CognitoUser:
    username: str
    attributes: dict
    enabled: bool = True
    refresh_tokens: set = field(default_factory=set)


class CognitoUserPool:
    """User pool with Entra as its only (OIDC) identity provider. First sign-in creates the user
    (just-in-time); an admin sets `custom:customer`. The pre-authentication check refuses users with no
    customer; the pre-token-generation step (v2) adds the customer + user as STS session tags."""

    def __init__(self, entra: EntraID, clock, id_token_ttl: int = 3600, refresh_ttl: int = 30 * 86400):
        self.pool_id = f"{REGION}_{secrets.token_hex(4).upper()}"
        self.client_id = secrets.token_hex(13)
        self.issuer = Issuer(f"https://cognito-idp.{REGION}.amazonaws.com/{self.pool_id}", clock)
        self.entra, self.clock = entra, clock
        self.id_ttl, self.refresh_ttl = id_token_ttl, refresh_ttl
        self.users: dict[str, CognitoUser] = {}
        self.refresh: dict[str, tuple[str, float]] = {}            # token -> (username, expiry)

    def _username(self, oid: str) -> str:
        return f"EntraID_{oid}"

    def federated_sign_in(self, entra_id_token: str) -> dict:
        c = verify_jwt(entra_id_token, self.entra.issuer.jwks(), self.entra.issuer.url, self.entra.client_id,
                       self.clock())
        if c.get("tid") != self.entra.tenant_id:
            raise AuthError("token from another Entra tenant")
        name = self._username(c["oid"])
        u = self.users.setdefault(name, CognitoUser(name, {"email": c["preferred_username"], "sub": str(uuid.uuid4())}))
        u.attributes["email"] = c["preferred_username"]
        return self._issue(u)

    def _issue(self, u: CognitoUser) -> dict:
        if not u.enabled:
            raise AuthError("User is disabled.")
        customer = u.attributes.get("custom:customer")
        if not customer:                                          # pre-authentication trigger
            raise AuthError("PreAuthentication failed: user is not mapped to a customer", code="UserNotMapped")
        now = int(self.clock())
        principal_tags = {"customer": [customer], "user": [u.attributes["sub"]]}
        transitive = ["customer", "user"]
        # Customer admins carry an extra session tag. Reader and transcript-user trusts ignore it; the
        # transcript-admin trust requires it, so a normal user token cannot assume that role.
        if u.attributes.get("custom:transcript_admin") == "true":
            principal_tags["transcript_admin"] = ["true"]
            transitive.append("transcript_admin")
            claims_admin = "true"
        else:
            claims_admin = None
        claims = {"iss": self.issuer.url, "aud": self.client_id, "token_use": "id", "sub": u.attributes["sub"],
                  "cognito:username": u.username, "email": u.attributes["email"], "custom:customer": customer,
                  "auth_time": now, "iat": now, "exp": now + self.id_ttl,
                  "https://aws.amazon.com/tags": {"principal_tags": principal_tags, "transitive_tag_keys": transitive}}
        if claims_admin:
            claims["custom:transcript_admin"] = claims_admin
        rt = secrets.token_urlsafe(32)
        self.refresh[rt] = (u.username, now + self.refresh_ttl)
        u.refresh_tokens.add(rt)
        return {"IdToken": self.issuer.sign(claims), "RefreshToken": rt, "ExpiresIn": self.id_ttl, "TokenType": "Bearer"}

    def refresh_tokens(self, refresh_token: str) -> dict:
        entry = self.refresh.get(refresh_token)
        if entry is None or self.clock() >= entry[1]:
            raise AuthError("Refresh Token has expired or been revoked")
        out = self._issue(self.users[entry[0]])
        rt = out.pop("RefreshToken")                              # Cognito keeps the caller's refresh token
        self.refresh.pop(rt, None)
        self.users[entry[0]].refresh_tokens.discard(rt)
        return out

    # admin API
    def admin_update_user_attributes(self, username: str, attrs: dict):
        self.users[username].attributes.update(attrs)

    def admin_disable_user(self, username: str):
        self.users[username].enabled = False

    def admin_user_global_sign_out(self, username: str):
        """Revokes refresh tokens. Already-issued id tokens stay valid until they expire (as in Cognito)."""
        for rt in self.users[username].refresh_tokens:
            self.refresh.pop(rt, None)
        self.users[username].refresh_tokens.clear()

    def username_for_email(self, email: str) -> str | None:
        return next((u.username for u in self.users.values() if u.attributes.get("email") == email.lower()), None)

    def verify_id_token(self, token: str) -> dict:
        c = verify_jwt(token, self.issuer.jwks(), self.issuer.url, self.client_id, self.clock())
        if c.get("token_use") != "id":
            raise InvalidIdentityToken("not an id token")
        return c
