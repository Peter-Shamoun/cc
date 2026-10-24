"""KMS: customer managed keys, key policies, envelope encryption, key states, rotation.

Data keys are real AES-256-GCM keys. A ciphertext blob carries the key id, the backing-key version and
the wrapped data key; the encryption context is bound as AAD, so a blob only decrypts with the same
context (S3 uses {"aws:s3:arn": "arn:aws:s3:::bucket/key"}). Disabling a key, or scheduling it for
deletion, makes every object written under it unreadable: per-customer crypto-shredding.
Backing-key material is kept in memory only; it never touches disk.
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .errors import KMSDisabled, KMSInvalidCiphertext, KMSInvalidState, ValidationError
from .iam import ACCOUNT, REGION


@dataclass
class Key:
    key_id: str
    policy: dict
    description: str = ""
    state: str = "Enabled"                                     # Enabled | Disabled | PendingDeletion
    deletion_date: float | None = None
    rotation_enabled: bool = False
    backing: list[bytes] = field(default_factory=list)         # index = backing-key version
    aliases: list[str] = field(default_factory=list)
    grants: list[dict] = field(default_factory=list)

    @property
    def arn(self) -> str:
        return f"arn:aws:kms:{REGION}:{ACCOUNT}:key/{self.key_id}"


def _aad(context: dict | None) -> bytes:
    return json.dumps(context or {}, sort_keys=True, separators=(",", ":")).encode()


class KMS:
    def __init__(self, clock):
        self.clock = clock
        self.keys: dict[str, Key] = {}
        self.aliases: dict[str, str] = {}                      # alias/name -> key id

    iam = None                                                 # set by LocalAWS: validates principals in key policies

    def create_key(self, policy: dict, description: str = "", alias: str | None = None) -> Key:
        from .iam import validate_policy
        validate_policy(policy, resource_policy=True, principal_exists=self.iam.role_exists if self.iam else None)
        k = Key(str(uuid.uuid4()), policy, description, backing=[AESGCM.generate_key(256)])
        self.keys[k.key_id] = k
        if alias:
            if not alias.startswith("alias/") or alias.startswith("alias/aws/"):
                raise ValidationError("alias must start with alias/ and not alias/aws/")
            self.aliases[alias] = k.key_id
            k.aliases.append(alias)
        return k

    def resolve(self, key_ref: str) -> Key:
        """Key id, key ARN, alias name or alias ARN -> Key."""
        ref = key_ref
        if ref.startswith("arn:aws:kms:"):
            ref = ref.split(":", 5)[5]
            if ref.startswith("key/"):
                ref = ref[4:]
        if ref.startswith("alias/"):
            ref = self.aliases.get(ref, "")
        k = self.keys.get(ref)
        if k is None:
            raise KMSInvalidState(f"key {key_ref} does not exist", code="KMS.NotFoundException")
        return k

    def _usable(self, k: Key):
        if k.state == "Disabled":
            raise KMSDisabled(f"{k.arn} is disabled")
        if k.state == "PendingDeletion":
            raise KMSInvalidState(f"{k.arn} is pending deletion")

    # control plane (Terraform / console): not IAM-checked here, recorded by the caller
    def disable_key(self, key_ref):
        self.resolve(key_ref).state = "Disabled"

    def enable_key(self, key_ref):
        k = self.resolve(key_ref)
        if k.state == "PendingDeletion":
            raise KMSInvalidState("cancel deletion first")
        k.state = "Enabled"

    def schedule_key_deletion(self, key_ref, pending_days: int = 30):
        if not 7 <= pending_days <= 30:
            raise ValidationError("PendingWindowInDays must be 7-30")
        k = self.resolve(key_ref)
        k.state, k.deletion_date = "PendingDeletion", self.clock() + pending_days * 86400

    def cancel_key_deletion(self, key_ref):
        k = self.resolve(key_ref)
        k.state, k.deletion_date = "Disabled", None

    def rotate_key_on_demand(self, key_ref):
        """New backing key for new encryptions; old versions still decrypt existing blobs."""
        self.resolve(key_ref).backing.append(AESGCM.generate_key(256))

    def put_key_policy(self, key_ref, policy: dict):
        self.resolve(key_ref).policy = policy

    # -- calls a principal makes (authorized against the key policy, like any KMS API) ---------------
    def _authorize(self, principal, session, iam, action, key: Key):
        from .errors import AccessDenied
        from .iam import decide
        role = iam.role_by_arn(principal.role_arn) if principal.role_arn else None
        d = decide(principal, action, key.arn, {"kms:CallerAccount": ACCOUNT}, role.policies if role else [], key.policy,
                   session.session_policies if session else (), role.boundary if role else None,
                   require_resource_allow=True)
        if not d:
            raise AccessDenied(f"{principal.arn} is not authorized to perform: {action} on resource: {key.arn}")

    def put_key_policy_as(self, principal, session, iam, key_ref: str, policy: dict):
        from .iam import MalformedPolicy, validate_policy
        k = self.resolve(key_ref)
        self._authorize(principal, session, iam, "kms:PutKeyPolicy", k)
        validate_policy(policy, resource_policy=True, principal_exists=iam.role_exists)
        k.policy = policy

    def create_grant_as(self, principal, session, iam, key_ref: str, grantee: str, operations: list[str],
                        constraints: dict | None = None) -> str:
        """A grant lets `grantee` use the key for `operations` regardless of the key policy's conditions."""
        k = self.resolve(key_ref)
        self._authorize(principal, session, iam, "kms:CreateGrant", k)
        gid = uuid.uuid4().hex
        k.grants.append(dict(id=gid, grantee=grantee, operations=list(operations), constraints=constraints or {}))
        return gid

    def grant_allows(self, key: Key, principal, operation: str, context: dict) -> bool:
        for g in key.grants:
            if g["grantee"] in principal.arns and operation in g["operations"]:
                eq = g["constraints"].get("EncryptionContextEquals")
                if eq is None or eq == context:
                    return True
        return False

    # data plane (callers authorize with iam.decide(..., require_resource_allow=True) first)
    def generate_data_key(self, key_ref: str, context: dict | None) -> tuple[bytes, bytes, Key]:
        k = self.resolve(key_ref)
        self._usable(k)
        plaintext = AESGCM.generate_key(256)
        ver = len(k.backing) - 1
        nonce = os.urandom(12)
        wrapped = AESGCM(k.backing[ver]).encrypt(nonce, plaintext, _aad(context))
        blob = json.dumps({"k": k.key_id, "v": ver, "n": base64.b64encode(nonce).decode(),
                           "c": base64.b64encode(wrapped).decode()}).encode()
        return plaintext, blob, k

    def key_for_blob(self, blob: bytes) -> Key:
        try:
            return self.resolve(json.loads(blob)["k"])
        except (ValueError, KeyError) as e:
            raise KMSInvalidCiphertext("malformed ciphertext blob") from e

    def decrypt(self, blob: bytes, context: dict | None) -> bytes:
        d = json.loads(blob)
        k = self.resolve(d["k"])
        self._usable(k)
        try:
            return AESGCM(k.backing[d["v"]]).decrypt(base64.b64decode(d["n"]), base64.b64decode(d["c"]), _aad(context))
        except Exception as e:                                 # wrong context or tampered blob
            raise KMSInvalidCiphertext("ciphertext does not match key or encryption context") from e


def seal(data_key: bytes, body: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(data_key).encrypt(nonce, body, None)


def unseal(data_key: bytes, sealed: bytes) -> bytes:
    return AESGCM(data_key).decrypt(sealed[:12], sealed[12:], None)
