"""Chat transcript store for the two designs in docs/data-backend/chat-transcripts.md.

Design A (`mode="ephemeral"`, the default, also `CC_TRANSCRIPT_MODE`): message text lives in this process
only. The store still writes non-content session metadata under the customer's transcript prefix and a
CloudTrail-style query audit under `audit/queries/`. Design B (`mode="persisted"`) also writes message
bodies, envelope-encrypted with a per-user data key that the customer transcript CMK wraps.

The future API server calls this. It does not go through the DuckDB reader role. The agent SQL session
cannot read or write these prefixes.

Nothing in the persisted bytes is the question, the answer, or the SQL text except inside the Design B
content objects, and those are ciphertext on disk (application AES-GCM, then SSE-KMS).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from .aws.errors import AccessDenied, NoSuchKey, PreconditionFailed
from .aws.iam import ACCOUNT, decide
from .aws.sts import service_principal
from .infra import (API_ROLE, AUDIT_QUERIES, AUDIT_RETENTION_DAYS, BREAKGLASS_EXTERNAL_ID, BREAKGLASS_ROLE,
                    BREAKGLASS_SESSION_SECONDS, BUCKET, EVAL_TEAM_ROLE, SESSION_SECONDS, SUPPORT_ROLE, TRANSCRIPTS,
                    Deployment, role_arn, transcript_names)
from .aws.kms import seal, unseal

EPHEMERAL = "ephemeral"
PERSISTED = "persisted"
_MODES = (EPHEMERAL, PERSISTED)

_SAFE_ID = re.compile(r"^[A-Za-z0-9+=,.@_-]{1,128}$")
_RELATION = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")
_METRIC = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_MODEL = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_ROLES = ("user", "assistant", "tool")
_MAX_TEXT = 200_000
_MAX_SQL = 100_000

# Best-effort screen before an export leaves the content prefix. Not a HIPAA Safe Harbor determination:
# a patient's name in free text can survive. Exports stay inside the customer boundary.
_REDACTIONS = (
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b(?:MRN|MBR|MEMBER|NPI|SSN|DOB)[:#\s-]*[A-Za-z0-9-]+\b", re.IGNORECASE),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"),
    re.compile(r"\b\d{9,}\b"),
)

_META_FIELDS = ("session_id", "customer", "user_sub", "started_at", "updated_at", "turn_count", "turn_ids",
                "models", "input_tokens", "output_tokens", "request_ids")
_AUDIT_FIELDS = ("request_id", "session_id", "customer", "user_sub", "at", "metric", "sql_sha256", "tables",
                 "row_count", "duration_ms", "error_code")


class TranscriptError(Exception):
    pass


class TranscriptNotFound(TranscriptError):
    pass


class TranscriptModeError(TranscriptError):
    """The running mode does not keep the bytes this call asked for."""


@dataclass
class Turn:
    role: str
    text: str
    request_id: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class QueryAudit:
    """What both designs keep about a query. `sql` is hashed and dropped; it is never stored."""

    request_id: str
    sql: str | None = None
    metric: str | None = None
    tables: tuple | list = ()
    row_count: int | None = None
    duration_ms: int | None = None
    error_code: str | None = None


def resolve_transcript_mode(mode: str | None = None) -> str:
    """Explicit `mode` wins. Otherwise `CC_TRANSCRIPT_MODE`, otherwise Design A (ephemeral)."""
    chosen = os.environ.get("CC_TRANSCRIPT_MODE", EPHEMERAL) if mode is None else mode
    chosen = chosen.strip().lower()
    if chosen not in _MODES:
        raise ValueError(f"transcript mode must be 'ephemeral' (Design A) or 'persisted' (Design B); got {chosen!r}")
    return chosen


def redact_phi_text(text: str) -> str:
    out = text
    for pattern in _REDACTIONS:
        out = pattern.sub("[REDACTED]", out)
    return out


def _require_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise TranscriptError(f"{name} must be an id of letters, digits, and +=,.@_-")
    return value


def _count(name: str, value) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise TranscriptError(f"{name} must be a non-negative int")
    return value


def _dek_context(customer: str, user_sub: str) -> dict:
    return {"customer": customer, "user": user_sub, "purpose": "transcript-dek"}


def _zero(buf: bytearray | None) -> None:
    if buf:
        for i in range(len(buf)):
            buf[i] = 0


def _sha256_b64(body: bytes) -> str:
    import base64
    return base64.b64encode(hashlib.sha256(body).digest()).decode()


class TranscriptStore:
    def __init__(self, aws, dep: Deployment, mode: str | None = None):
        self.aws, self.dep = aws, dep
        self.mode = resolve_transcript_mode(mode)
        self._memory: dict[tuple[str, str, str], list[dict]] = {}
        self._deks: dict[tuple[str, str], bytearray] = {}

    # -- public API, for the future server ----------------------------------------------------------
    def open_chat(self, id_token: str) -> str:
        """Start a chat. Writes metadata only. Returns the session id."""
        claims = self._claims(id_token)
        customer, user_sub = self._customer_sub(claims)
        session_id = uuid.uuid4().hex
        now = self.aws.clock()
        doc = {"session_id": session_id, "customer": customer, "user_sub": user_sub, "started_at": now,
               "updated_at": now, "turn_count": 0, "turn_ids": [], "models": [], "input_tokens": 0,
               "output_tokens": 0, "request_ids": []}
        self._put_json(self._assume_user(id_token, claims), self._meta_key(customer, user_sub, session_id), doc,
                       self._transcript_arn(customer))
        self._memory[(customer, user_sub, session_id)] = []
        return session_id

    def append_turn(self, id_token: str, session_id: str, turn: Turn, query: QueryAudit | None = None) -> str:
        """Record one turn. Design A keeps `turn.text` in memory. Design B also writes ciphertext.
        A query audit, when passed, is written in both modes and does not contain the SQL or the text."""
        _require_id(session_id, "session_id")
        payload = self._turn_payload(turn)
        claims = self._claims(id_token)
        customer, user_sub = self._customer_sub(claims)
        user_session = self._assume_user(id_token, claims)
        meta = self._load_meta(user_session, customer, user_sub, session_id)
        if query is not None:
            self.record_query_audit(id_token, session_id, query)
        turn_id = uuid.uuid4().hex
        if self.mode == PERSISTED:
            dek = self._ensure_dek(user_session, customer, user_sub)
            body = seal(bytes(dek), json.dumps(payload, separators=(",", ":")).encode())
            self._put_bytes(user_session, self._content_key(customer, user_sub, session_id, turn_id), body,
                            self._transcript_arn(customer))
        meta = self._with_turn(meta, turn_id, payload, self.aws.clock())
        self._put_json(user_session, self._meta_key(customer, user_sub, session_id), meta, self._transcript_arn(customer))
        self._memory.setdefault((customer, user_sub, session_id), []).append(payload)
        return turn_id

    def record_query_audit(self, id_token: str, session_id: str | None, query: QueryAudit) -> str:
        """Append one query-audit object. The SQL string is hashed inside this function and not stored.
        The end user cannot assume the writer role; the API role does, after the token is verified."""
        claims = self._claims(id_token)
        customer, user_sub = self._customer_sub(claims)
        if session_id is not None:
            _require_id(session_id, "session_id")
        doc = self._audit_document(customer, user_sub, session_id, query)
        key = self._audit_object_key(customer, doc["request_id"])
        try:
            self._put_bytes(self._assume_audit_writer(customer, user_sub), key,
                            json.dumps(doc, separators=(",", ":"), sort_keys=True).encode(),
                            self.dep.audit_key.arn, content_type="application/json", lock_days=AUDIT_RETENTION_DAYS)
        except PreconditionFailed as e:
            raise TranscriptError(f"query audit {doc['request_id']} is already recorded") from e
        return key

    def read_chat(self, id_token: str, session_id: str) -> dict:
        claims = self._claims(id_token)
        customer, user_sub = self._customer_sub(claims)
        _require_id(session_id, "session_id")
        if self.mode == EPHEMERAL:
            return self._read_memory(customer, user_sub, session_id)
        return self._read_persisted(self._assume_user(id_token, claims), customer, user_sub, session_id)

    def list_chats(self, id_token: str) -> list[dict]:
        """Metadata only. Safe to call in either mode."""
        claims = self._claims(id_token)
        customer, user_sub = self._customer_sub(claims)
        session = self._assume_user(id_token, claims)
        prefix = f"{customer}/{TRANSCRIPTS}/users/{user_sub}/meta/"
        return [self._get_json(session, key) for key in self._list_keys(session, prefix)]

    def close_chat(self, id_token: str, session_id: str) -> None:
        """Drop this process's copy of the text. Design B objects in S3 are unchanged. Design A text is gone."""
        claims = self._claims(id_token)
        customer, user_sub = self._customer_sub(claims)
        _require_id(session_id, "session_id")
        self._memory.pop((customer, user_sub, session_id), None)

    def delete_my_chats(self, id_token: str) -> dict:
        """Delete this user's transcript objects (every version) and wipe the process copy.
        Query-audit objects are not deleted; the bucket policy would refuse it anyway."""
        claims = self._claims(id_token)
        customer, user_sub = self._customer_sub(claims)
        return self._delete_user(self._assume_user(id_token, claims), customer, user_sub)

    def delete_user_chats(self, admin_id_token: str, user_sub: str) -> dict:
        claims = self._claims(admin_id_token)
        customer = claims["custom:customer"]
        _require_id(user_sub, "user_sub")
        _require_id(customer, "customer")
        return self._delete_user(self._assume_admin(admin_id_token, claims), customer, user_sub)

    def read_chat_as_admin(self, admin_id_token: str, user_sub: str, session_id: str) -> dict:
        if self.mode != PERSISTED:
            raise TranscriptModeError("Design A has no stored message text for an admin to read")
        claims = self._claims(admin_id_token)
        customer = claims["custom:customer"]
        _require_id(user_sub, "user_sub")
        _require_id(session_id, "session_id")
        return self._read_persisted(self._assume_admin(admin_id_token, claims), customer, user_sub, session_id)

    def read_query_audit(self, admin_id_token: str, request_id: str) -> dict:
        claims = self._claims(admin_id_token)
        customer = claims["custom:customer"]
        _require_id(request_id, "request_id")
        session = self._assume_audit_reader(admin_id_token, claims)
        suffix = f"/{request_id}.json"
        keys = [k for k in self._list_keys(session, f"{customer}/{AUDIT_QUERIES}/") if k.endswith(suffix)]
        if len(keys) != 1:
            raise TranscriptNotFound(request_id)
        return self._get_json(session, keys[0])

    def export_deidentified(self, admin_id_token: str) -> str:
        """Write a redacted copy under transcripts/exports/ for the eval role. The HMAC salt stays under
        transcripts/admin/, which the eval role cannot read."""
        if self.mode != PERSISTED:
            raise TranscriptModeError("Design A does not keep message text, so there is nothing to export")
        claims = self._claims(admin_id_token)
        customer = claims["custom:customer"]
        admin = self._assume_admin(admin_id_token, claims)
        export_id = uuid.uuid4().hex
        salt = os.urandom(32)
        sessions = []
        prefix = f"{customer}/{TRANSCRIPTS}/users/"
        meta_keys = [k for k in self._list_keys(admin, prefix) if "/meta/" in k and k.endswith(".json")]
        for meta_key in sorted(meta_keys):
            meta = self._get_json(admin, meta_key)
            user_sub, session_id = meta["user_sub"], meta["session_id"]
            chat = self._read_persisted(admin, customer, user_sub, session_id)
            sessions.append({"session_ref": hmac.new(salt, session_id.encode(), hashlib.sha256).hexdigest()[:32],
                             "turn_count": meta["turn_count"], "models": list(meta["models"]),
                             "input_tokens": meta["input_tokens"], "output_tokens": meta["output_tokens"],
                             "turns": [{"role": t["role"], "text": redact_phi_text(t["text"])} for t in chat["turns"]]})
        doc = {"export_id": export_id, "customer": customer, "created_at": self.aws.clock(), "sessions": sessions}
        arn = self._transcript_arn(customer)
        self._put_bytes(admin, f"{customer}/{TRANSCRIPTS}/admin/salts/{export_id}.bin", salt, arn)
        self._put_json(admin, f"{customer}/{TRANSCRIPTS}/exports/{export_id}.json", doc, arn)
        return export_id

    def read_export(self, customer: str, export_id: str) -> dict:
        """Eval role. One customer per call, which is the tenant boundary on an export."""
        _require_id(customer, "customer")
        _require_id(export_id, "export_id")
        if customer not in self.dep.customers:
            raise TranscriptError(f"unknown customer {customer}")
        session = self._assume_eval(customer)
        return self._get_json(session, f"{customer}/{TRANSCRIPTS}/exports/{export_id}.json")

    def assume_breakglass(self, customer: str, ticket: str, operator: str):
        """Support staff, one customer, one ticket, 15 minutes. Returns the STS session.
        AWS will not let MaxSessionDuration go below 3600; this requests 900 and read_chat_as_breakglass
        refuses anything longer."""
        _require_id(customer, "customer")
        _require_id(operator, "operator")
        if customer not in self.dep.customers:
            raise TranscriptError(f"unknown customer {customer}")
        if not ticket.startswith("TICKET-") or not _SAFE_ID.fullmatch(ticket):
            raise TranscriptError("break-glass ticket must look like TICKET-<id>")
        support = self.aws.sts.assume_role(service_principal("ec2.amazonaws.com"), None, role_arn(SUPPORT_ROLE),
                                           "support-host")
        return self.aws.sts.assume_role(
            support.principal(), support, role_arn(BREAKGLASS_ROLE), f"bg-{operator}"[:64],
            BREAKGLASS_SESSION_SECONDS, ExternalId=BREAKGLASS_EXTERNAL_ID,
            Tags=[{"Key": "customer", "Value": customer}, {"Key": "reason", "Value": ticket},
                  {"Key": "operator", "Value": operator}])

    def read_chat_as_breakglass(self, session, user_sub: str, session_id: str) -> dict:
        if self.mode != PERSISTED:
            raise TranscriptModeError("Design A has no stored message text for support to read")
        if getattr(session, "role_name", None) != BREAKGLASS_ROLE:
            raise AccessDenied("not a break-glass session")
        if session.expiration - session.issued_at > BREAKGLASS_SESSION_SECONDS + 1:
            raise AccessDenied("break-glass credentials must be requested for 900 seconds")
        customer = session.principal().tags.get("customer")
        _require_id(user_sub, "user_sub")
        _require_id(session_id, "session_id")
        if not isinstance(customer, str):
            raise AccessDenied("break-glass session has no customer tag")
        return self._read_persisted(session, customer, user_sub, session_id)

    def crypto_shred_customer(self, customer: str) -> None:
        """Disable the customer transcript CMK. SSE ciphertext and wrapped data keys become undecryptable,
        including copies that left the bucket. The lake key and the query-audit key are not touched.
        In prod this is the key-admin role, which the key policy allows to DisableKey and not to Decrypt."""
        if customer not in self.dep.transcript_keys:
            raise TranscriptError(f"unknown customer {customer}")
        self.aws.kms.disable_key(self.dep.transcript_keys[customer].arn)
        self._wipe_customer_memory(customer)

    # -- keys ----------------------------------------------------------------------------------------
    def _transcript_arn(self, customer: str) -> str:
        return self.dep.transcript_keys[customer].arn

    def _meta_key(self, customer: str, user_sub: str, session_id: str) -> str:
        return f"{customer}/{TRANSCRIPTS}/users/{user_sub}/meta/{session_id}.json"

    def _content_key(self, customer: str, user_sub: str, session_id: str, turn_id: str) -> str:
        return f"{customer}/{TRANSCRIPTS}/users/{user_sub}/content/{session_id}/{turn_id}.bin"

    def _dek_key(self, customer: str, user_sub: str) -> str:
        return f"{customer}/{TRANSCRIPTS}/users/{user_sub}/dek.bin"

    def _audit_object_key(self, customer: str, request_id: str) -> str:
        day = datetime.fromtimestamp(self.aws.clock(), timezone.utc).strftime("%Y-%m-%d")
        return f"{customer}/{AUDIT_QUERIES}/{day}/{request_id}.json"

    # -- identity ------------------------------------------------------------------------------------
    def _claims(self, id_token: str) -> dict:
        return self.aws.cognito.verify_id_token(id_token)

    def _customer_sub(self, claims: dict) -> tuple[str, str]:
        customer, user_sub = claims["custom:customer"], claims["sub"]
        return _require_id(customer, "customer"), _require_id(user_sub, "user_sub")

    def _assume_user(self, id_token: str, claims: dict):
        customer, user_sub = self._customer_sub(claims)
        return self.aws.sts.assume_role_with_web_identity(
            RoleArn=role_arn(transcript_names(customer)["user"]), RoleSessionName=f"tx-{user_sub}"[:64],
            WebIdentityToken=id_token, DurationSeconds=SESSION_SECONDS)

    def _assume_admin(self, id_token: str, claims: dict):
        customer, user_sub = self._customer_sub(claims)
        return self.aws.sts.assume_role_with_web_identity(
            RoleArn=role_arn(transcript_names(customer)["admin"]), RoleSessionName=f"txa-{user_sub}"[:64],
            WebIdentityToken=id_token, DurationSeconds=SESSION_SECONDS)

    def _assume_audit_reader(self, id_token: str, claims: dict):
        customer, user_sub = self._customer_sub(claims)
        return self.aws.sts.assume_role_with_web_identity(
            RoleArn=role_arn(transcript_names(customer)["audit_reader"]), RoleSessionName=f"aud-{user_sub}"[:64],
            WebIdentityToken=id_token, DurationSeconds=SESSION_SECONDS)

    def _assume_audit_writer(self, customer: str, user_sub: str):
        api = self.aws.sts.assume_role(service_principal("ec2.amazonaws.com"), None, role_arn(API_ROLE), "api")
        return self.aws.sts.assume_role(
            api.principal(), api, role_arn(transcript_names(customer)["audit_writer"]), f"audit-{user_sub}"[:64],
            SESSION_SECONDS, Tags=[{"Key": "customer", "Value": customer}, {"Key": "user", "Value": user_sub}])

    def _assume_eval(self, customer: str):
        team = self.aws.sts.assume_role(service_principal("ec2.amazonaws.com"), None, role_arn(EVAL_TEAM_ROLE),
                                        "eval-team")
        return self.aws.sts.assume_role(team.principal(), team, role_arn(transcript_names(customer)["eval"]),
                                        f"eval-{customer}"[:64], SESSION_SECONDS)

    def _require_session_customer(self, session, customer: str) -> None:
        tag = session.principal().tags.get("customer")
        if tag != customer:
            raise AccessDenied(f"session customer {tag!r} cannot access {customer}")

    # -- S3 / KMS ------------------------------------------------------------------------------------
    def _authorize_kms(self, session, action: str, key, context: dict) -> None:
        principal = session.principal()
        ctx = {"kms:CallerAccount": ACCOUNT, "aws:CurrentTime": self.aws.clock(),
               **{f"kms:EncryptionContext:{k}": v for k, v in context.items()},
               "kms:EncryptionContextKeys": list(context)}
        role = self.aws.iam.role_by_arn(principal.role_arn)
        decision = decide(principal, action, key.arn, ctx, role.policies if role else [], key.policy,
                          session.session_policies, role.boundary if role else None, require_resource_allow=True)
        self.aws.trail.record("kms.amazonaws.com", action.split(":")[1], principal,
                              {"keyId": key.arn, "encryptionContext": context}, resources=[key.arn],
                              error=None if decision else ("AccessDenied", decision.reason))
        if not decision:
            raise AccessDenied(f"{principal.arn} is not authorized to perform: {action} on {key.arn}")

    def _put_bytes(self, session, key: str, body: bytes, kms_arn: str, content_type: str = "application/octet-stream",
                   write_once: bool = False, lock_days: int | None = None) -> None:
        customer = key.split("/", 1)[0]
        self._require_session_customer(session, customer)
        kw = dict(Bucket=BUCKET, Key=key, Body=body, ContentType=content_type,
                  ServerSideEncryption="aws:kms", SSEKMSKeyId=kms_arn)
        if write_once or lock_days is not None:
            kw["IfNoneMatch"] = "*"
        if lock_days is not None:
            kw["ObjectLockMode"] = "COMPLIANCE"
            kw["ObjectLockRetainUntilDate"] = self.aws.clock() + lock_days * 86400
            kw["ChecksumSHA256"] = _sha256_b64(body)
        self.aws.client("s3", session).put_object(**kw)

    def _put_json(self, session, key: str, doc: dict, kms_arn: str) -> None:
        self._put_bytes(session, key, json.dumps(doc, separators=(",", ":"), sort_keys=True).encode(), kms_arn,
                        content_type="application/json")

    def _get_bytes(self, session, key: str) -> bytes:
        customer = key.split("/", 1)[0]
        self._require_session_customer(session, customer)
        return self.aws.client("s3", session).get_object(Bucket=BUCKET, Key=key)["Body"].read()

    def _get_json(self, session, key: str) -> dict:
        try:
            return json.loads(self._get_bytes(session, key))
        except NoSuchKey as e:
            raise TranscriptNotFound(key) from e

    def _list_keys(self, session, prefix: str) -> list[str]:
        customer = prefix.split("/", 1)[0]
        self._require_session_customer(session, customer)
        s3 = self.aws.client("s3", session)
        keys, token = [], None
        while True:
            kw = dict(Bucket=BUCKET, Prefix=prefix)
            if token:
                kw["ContinuationToken"] = token
            page = s3.list_objects_v2(**kw)
            keys.extend(item["Key"] for item in page.get("Contents", []))
            if not page.get("IsTruncated"):
                return keys
            token = page["NextContinuationToken"]

    # -- metadata, content, deletion -----------------------------------------------------------------
    def _turn_payload(self, turn: Turn) -> dict:
        if turn.role not in _ROLES:
            raise TranscriptError("turn role must be user, assistant, or tool")
        if not isinstance(turn.text, str) or len(turn.text) > _MAX_TEXT:
            raise TranscriptError(f"turn text must be a string of at most {_MAX_TEXT} characters")
        if turn.request_id is not None:
            _require_id(turn.request_id, "request_id")
        if turn.model is not None and not _MODEL.fullmatch(turn.model):
            raise TranscriptError("model must be a model id, not free text")
        return {"role": turn.role, "text": turn.text, "request_id": turn.request_id, "model": turn.model,
                "input_tokens": _count("input_tokens", turn.input_tokens),
                "output_tokens": _count("output_tokens", turn.output_tokens), "at": self.aws.clock()}

    def _load_meta(self, session, customer: str, user_sub: str, session_id: str) -> dict:
        try:
            meta = self._get_json(session, self._meta_key(customer, user_sub, session_id))
        except TranscriptNotFound as e:
            raise TranscriptNotFound(session_id) from e
        if meta.get("customer") != customer or meta.get("user_sub") != user_sub or meta.get("session_id") != session_id:
            raise TranscriptError("transcript metadata does not match this caller")
        return {field: meta[field] for field in _META_FIELDS}

    def _with_turn(self, meta: dict, turn_id: str, payload: dict, now: float) -> dict:
        """New metadata document. The text is not a field on the document, so it cannot be written here."""
        doc = {field: meta[field] for field in _META_FIELDS}
        doc["updated_at"] = now
        doc["turn_count"] = int(doc["turn_count"]) + 1
        doc["turn_ids"] = list(doc["turn_ids"]) + [turn_id]
        doc["input_tokens"] = int(doc["input_tokens"]) + int(payload["input_tokens"] or 0)
        doc["output_tokens"] = int(doc["output_tokens"]) + int(payload["output_tokens"] or 0)
        if payload["model"] and payload["model"] not in doc["models"]:
            doc["models"] = list(doc["models"]) + [payload["model"]]
        if payload["request_id"]:
            doc["request_ids"] = list(doc["request_ids"]) + [payload["request_id"]]
        return {field: doc[field] for field in _META_FIELDS}

    def _audit_document(self, customer: str, user_sub: str, session_id: str | None, query: QueryAudit) -> dict:
        request_id = _require_id(query.request_id, "request_id")
        if query.metric is not None and not _METRIC.fullmatch(query.metric):
            raise TranscriptError("metric must be a metric id, not free text")
        if query.error_code is not None and not _CODE.fullmatch(query.error_code):
            raise TranscriptError("error_code must be a short code")
        tables = []
        for name in query.tables:
            if not isinstance(name, str) or not _RELATION.fullmatch(name):
                raise TranscriptError("table must be a relation name")
            tables.append(name)
        sql = query.sql
        if sql is not None:
            if not isinstance(sql, str) or len(sql) > _MAX_SQL:
                raise TranscriptError("sql must be a string")
            sql_sha256 = hashlib.sha256(sql.encode()).hexdigest()
        else:
            sql_sha256 = None
        doc = {"request_id": request_id, "session_id": session_id, "customer": customer, "user_sub": user_sub,
               "at": self.aws.clock(), "metric": query.metric, "sql_sha256": sql_sha256, "tables": tables,
               "row_count": _count("row_count", query.row_count), "duration_ms": _count("duration_ms", query.duration_ms),
               "error_code": query.error_code}
        return {field: doc[field] for field in _AUDIT_FIELDS}

    def _read_memory(self, customer: str, user_sub: str, session_id: str) -> dict:
        turns = self._memory.get((customer, user_sub, session_id))
        if turns is None:
            raise TranscriptNotFound(session_id)
        return {"session_id": session_id, "customer": customer, "user_sub": user_sub,
                "turns": [dict(t) for t in turns]}

    def _read_persisted(self, session, customer: str, user_sub: str, session_id: str) -> dict:
        self._require_session_customer(session, customer)
        meta = self._load_meta(session, customer, user_sub, session_id)
        dek = self._load_dek(session, customer, user_sub)
        turns = []
        for turn_id in meta["turn_ids"]:
            blob = self._get_bytes(session, self._content_key(customer, user_sub, session_id, turn_id))
            turns.append(json.loads(unseal(bytes(dek), blob)))
        return {"session_id": session_id, "customer": customer, "user_sub": user_sub, "turns": turns}

    def _load_dek(self, session, customer: str, user_sub: str) -> bytearray:
        cached = self._deks.get((customer, user_sub))
        if cached:
            return cached
        return self._cache_unwrapped(session, customer, user_sub, self._get_bytes(session, self._dek_key(customer, user_sub)))

    def _ensure_dek(self, session, customer: str, user_sub: str) -> bytearray:
        cached = self._deks.get((customer, user_sub))
        if cached:
            return cached
        try:
            blob = self._get_bytes(session, self._dek_key(customer, user_sub))
        except NoSuchKey:
            return self._create_dek(session, customer, user_sub)
        return self._cache_unwrapped(session, customer, user_sub, blob)

    def _create_dek(self, session, customer: str, user_sub: str) -> bytearray:
        key = self.dep.transcript_keys[customer]
        context = _dek_context(customer, user_sub)
        self._authorize_kms(session, "kms:GenerateDataKey", key, context)
        plaintext, blob, _ = self.aws.kms.generate_data_key(key.key_id, context)
        try:
            self._put_bytes(session, self._dek_key(customer, user_sub), blob, key.arn, write_once=True)
        except PreconditionFailed:
            blob = self._get_bytes(session, self._dek_key(customer, user_sub))
            return self._cache_unwrapped(session, customer, user_sub, blob)
        return self._cache_plaintext(customer, user_sub, plaintext)

    def _cache_unwrapped(self, session, customer: str, user_sub: str, blob: bytes) -> bytearray:
        key = self.aws.kms.key_for_blob(blob)
        context = _dek_context(customer, user_sub)
        self._authorize_kms(session, "kms:Decrypt", key, context)
        return self._cache_plaintext(customer, user_sub, self.aws.kms.decrypt(blob, context))

    def _cache_plaintext(self, customer: str, user_sub: str, plaintext: bytes) -> bytearray:
        buf = bytearray(plaintext)
        self._deks[(customer, user_sub)] = buf
        return buf

    def _delete_user(self, session, customer: str, user_sub: str) -> dict:
        self._require_session_customer(session, customer)
        dropped = self._wipe_user_memory(customer, user_sub)
        deleted = self._purge_prefix(session, f"{customer}/{TRANSCRIPTS}/users/{user_sub}/")
        return {"versions_deleted": deleted, "memory_sessions_dropped": dropped}

    def _purge_prefix(self, session, prefix: str) -> int:
        s3 = self.aws.client("s3", session)
        deleted = 0
        for _ in range(20):
            listed = s3.list_object_versions(Bucket=BUCKET, Prefix=prefix)
            entries = [(e["Key"], e["VersionId"]) for e in listed.get("Versions", [])]
            entries += [(e["Key"], e["VersionId"]) for e in listed.get("DeleteMarkers", [])]
            if not entries:
                return deleted
            for key, version_id in entries:
                s3.delete_object(Bucket=BUCKET, Key=key, VersionId=version_id)
                deleted += 1
        raise TranscriptError(f"prefix {prefix} still has objects after delete")

    def _wipe_user_memory(self, customer: str, user_sub: str) -> int:
        keys = [k for k in self._memory if k[0] == customer and k[1] == user_sub]
        for key in keys:
            for turn in self._memory.pop(key):
                turn["text"] = ""
        _zero(self._deks.pop((customer, user_sub), None))
        return len(keys)

    def _wipe_customer_memory(self, customer: str) -> None:
        for user_sub in {k[1] for k in list(self._memory) + list(self._deks) if k[0] == customer}:
            self._wipe_user_memory(customer, user_sub)
