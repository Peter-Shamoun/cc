"""Transcript store selection and Design B (persisted, envelope-encrypted) for the chat API.

``CC_TRANSCRIPT_MODE`` (resolved by ``ccsim.transcripts.resolve_transcript_mode``, so the API and the
simulator agree):

* unset / ``ephemeral`` -> ``InMemoryTranscriptStore`` (Design A, the recommended default).
* ``persisted`` -> ``SealedTranscriptStore`` (Design B), the same shape as docs/data-backend/chat-transcripts.md:

  ``{customer}/transcripts/users/{user_hash}/meta/{conversation}.json``   no text: ids, times, counts
  ``{customer}/transcripts/users/{user_hash}/content/{conversation}.bin`` AES-GCM(title + messages)
  ``{customer}/transcripts/users/{user_hash}/dek.bin``                   per-user data key, wrapped

  The data key is wrapped by the customer's transcript key (``Keyring``; in production the KMS CMK
  ``alias/cc/{customer}/transcripts``, here a local stand-in). Each AES-GCM seal binds
  customer, user and conversation as associated data, so a blob moved to another user or chat fails
  to open. Deleting all of a user's chats destroys their wrapped data key; ``crypto_shred_customer``
  destroys the customer key, after which every copy of that customer's content is unreadable.

  Retention: ``TRANSCRIPT_RETENTION_DAYS`` (90) unless the user sets a shorter one (persisted as
  ``retention.json``); enforced on access and by ``purge_expired`` (the app's background sweep).
  ``CC_TRANSCRIPT_S3_BUCKET`` keeps blobs in that S3 bucket (``S3Blobs``; boto3 reads the endpoint and
  credentials from the usual AWS settings). Otherwise ``CC_TRANSCRIPT_DIR`` keeps them on disk (files 0600); unset
  keeps them in memory (tests, demo).

  No DynamoDB: the conversation object is the record. Every change to it is a read, change and conditional write
  (``If-Match`` on the ETag it was read with, ``If-None-Match: *`` for a new one), retried when another API task wrote
  first. A run's stream events go in numbered, write-once chunk objects
  ``{customer}/transcripts/users/{user_hash}/runs/{conversation}/{message}/{chunk}.bin`` (sealed like the content),
  so any API task can replay a run from ``Last-Event-ID``; they are deleted with their conversation.

Nothing here logs text. Audit events carry ids and counts only (``AuditLog`` allowlist).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import random
import re
import threading
import time
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from api.feedback import apply as apply_feedback
from api.store import (DEFAULT_TITLE, InMemoryTranscriptStore, Principal, _iso, apply_update, clean_title, flags, page,
                       unarchive_on_user_message)

TRANSCRIPT_RETENTION_DAYS = 90
CONDITIONAL_WRITE_ATTEMPTS = 8
CONDITIONAL_WRITE_BACKOFF_SECONDS = 0.02
#: always fullmatch (`$` would accept a trailing "\n"). Starts alphanumeric: "." / ".." would be path segments.
_SAFE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_:.-]{0,127}")
_USER_PREFIX = re.compile(r"^[A-Za-z0-9_:.-]{1,128}/transcripts/users/[0-9a-f]{32}/")


def resolve_mode(mode: str | None = None) -> str:
    try:
        from ccsim.transcripts import resolve_transcript_mode
    except ImportError:  # pragma: no cover - api/ deployed without ccsim
        chosen = os.environ.get("CC_TRANSCRIPT_MODE", "ephemeral") if mode is None else mode
        if chosen not in ("ephemeral", "persisted"):
            raise ValueError(f"transcript mode must be 'ephemeral' or 'persisted'; got {chosen!r}")
        return chosen
    return resolve_transcript_mode(mode)


def make_store(mode: str | None = None, audit=None):
    """The store for ``mode`` (or ``CC_TRANSCRIPT_MODE``). Ephemeral unless explicitly persisted."""
    if resolve_mode(mode) == "persisted":
        bucket = os.environ.get("CC_TRANSCRIPT_S3_BUCKET")
        root = os.environ.get("CC_TRANSCRIPT_DIR")
        if bucket:
            import boto3
            blobs = S3Blobs(boto3.client("s3"), bucket)
        else:
            blobs = DirBlobs(Path(root)) if root else MemoryBlobs()
        keys_dir = os.environ.get("CC_TRANSCRIPT_KEYS_DIR")
        return SealedTranscriptStore(blobs, Keyring(Path(keys_dir) if keys_dir else None), audit=audit)
    return InMemoryTranscriptStore(audit=audit)


class PreconditionFailed(Exception):
    """A conditional write lost: the object changed since it was read (If-Match) or already exists (If-None-Match)."""


class ConcurrentUpdateConflict(RuntimeError):
    """A conversation kept changing under every retry of a conditional write."""


def etag_of(body: bytes) -> str:
    """The ETag S3 gives a single-part upload without SSE-KMS; local stand-ins use it for compare-and-set."""
    return hashlib.md5(body).hexdigest()


# Blob stores: put / get / get_with_etag / delete / list. ``put`` takes ``if_match`` (an ETag from get_with_etag) or
# ``if_none_match=True`` (create only) and raises PreconditionFailed when the condition does not hold.

class MemoryBlobs:
    def __init__(self):
        self._data: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def put(self, key: str, body: bytes, *, if_match: str | None = None, if_none_match: bool = False) -> None:
        with self._lock:
            _check_condition(self._data.get(key), if_match, if_none_match)
            self._data[key] = bytes(body)

    def get(self, key: str) -> bytes | None:
        with self._lock:
            return self._data.get(key)

    def get_with_etag(self, key: str) -> tuple[bytes, str] | None:
        body = self.get(key)
        return None if body is None else (body, etag_of(body))

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def list(self, prefix: str) -> list[str]:
        with self._lock:
            return sorted(k for k in self._data if k.startswith(prefix))


def _check_condition(current: bytes | None, if_match: str | None, if_none_match: bool) -> None:
    if if_none_match and current is not None:
        raise PreconditionFailed("object exists")
    if if_match is not None and (current is None or etag_of(current) != if_match):
        raise PreconditionFailed("object changed")


class DirBlobs:
    """Local-disk stand-in for the S3 prefix. Files are 0600, directories 0700, writes are atomic. Conditional writes
    hold an exclusive lock on ``<root>/.lock``, so they are atomic across processes sharing the directory."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock_path = self.root / ".lock"

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("key escapes the transcript root")
        return path

    def put(self, key: str, body: bytes, *, if_match: str | None = None, if_none_match: bool = False) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if if_match is None and not if_none_match:
            self._write(path, body)
            return
        fd = os.open(self._lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            _check_condition(self.get(key), if_match, if_none_match)
            self._write(path, body)
        finally:
            os.close(fd)   # releases the lock

    @staticmethod
    def _write(path: Path, body: bytes) -> None:
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
        os.replace(tmp, path)

    def get(self, key: str) -> bytes | None:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            return None

    def get_with_etag(self, key: str) -> tuple[bytes, str] | None:
        body = self.get(key)
        return None if body is None else (body, etag_of(body))

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass

    def list(self, prefix: str) -> list[str]:
        base = self.root
        return sorted(str(p.relative_to(base)) for p in base.rglob("*")
                      if p.is_file() and not p.name.endswith(".tmp") and p != self._lock_path
                      and str(p.relative_to(base)).startswith(prefix))


class S3Blobs:
    """The blob interface on an S3 bucket, through any boto3-shaped client (boto3, or ccsim.aws's in-process one).
    Conditional writes are S3's own (If-Match / If-None-Match on PutObject). Encryption at rest is the bucket's
    default SSE-KMS, on top of the store's own envelope encryption."""

    _LOST_RACE_CODES = ("PreconditionFailed", "ConditionalRequestConflict", "NoSuchKey")

    def __init__(self, client, bucket: str):
        self.client = client
        self.bucket = bucket

    def put(self, key: str, body: bytes, *, if_match: str | None = None, if_none_match: bool = False) -> None:
        condition = {"IfMatch": f'"{if_match}"'} if if_match is not None else {"IfNoneMatch": "*"} if if_none_match else {}
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=bytes(body), **condition)
        except Exception as exc:  # noqa: BLE001 - botocore ClientError and ccsim AWSError share .response
            if condition and _error_code(exc) in self._LOST_RACE_CODES:
                raise PreconditionFailed(_error_code(exc)) from None
            raise

    def get(self, key: str) -> bytes | None:
        found = self.get_with_etag(key)
        return None if found is None else found[0]

    def get_with_etag(self, key: str) -> tuple[bytes, str] | None:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:  # noqa: BLE001
            if _error_code(exc) == "NoSuchKey":
                return None
            raise
        return response["Body"].read(), response["ETag"].strip('"')

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def list(self, prefix: str) -> list[str]:
        keys, token = [], None
        while True:
            page_kwargs = {"ContinuationToken": token} if token else {}
            response = self.client.list_objects_v2(Bucket=self.bucket, Prefix=prefix, **page_kwargs)
            keys += [item["Key"] for item in response.get("Contents") or []]
            if not response.get("IsTruncated"):
                return sorted(keys)
            token = response["NextContinuationToken"]


def _error_code(exc: Exception) -> str | None:
    return (getattr(exc, "response", None) or {}).get("Error", {}).get("Code")


class CustomerKeyDisabled(Exception):
    """The customer's transcript key was crypto-shredded."""


class Keyring:
    """Per-customer key-encryption keys (stand-in for the transcript CMK). ``disable`` is the crypto-shred."""

    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory) if directory else None
        self._keys: dict[str, bytes] = {}
        self._disabled: set[str] = set()
        self._lock = threading.Lock()
        if self.directory:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _file(self, customer: str) -> Path | None:
        return self.directory / f"{customer}.kek" if self.directory else None

    def key(self, customer: str) -> bytes:
        with self._lock:
            if customer in self._disabled or (self._file(customer) and self._file(customer).with_suffix(".disabled").exists()):
                raise CustomerKeyDisabled(customer)
            if customer not in self._keys:
                self._keys[customer] = self._load_or_create(customer)
            return self._keys[customer]

    def _load_or_create(self, customer: str) -> bytes:
        """The key file wins: a new key is cached only once it is the one on disk (another process may have
        created the file first, and caching a key that lost that race would seal data nobody else can open)."""
        path = self._file(customer)
        if path is None:
            return AESGCM.generate_key(256)
        if path.exists():
            return path.read_bytes()
        new_key = AESGCM.generate_key(256)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return path.read_bytes()
        with os.fdopen(fd, "wb") as fh:
            fh.write(new_key)
        return new_key

    def disable(self, customer: str) -> None:
        with self._lock:
            self._disabled.add(customer)
            self._keys.pop(customer, None)
            path = self._file(customer)
            if path:
                path.with_suffix(".disabled").touch(mode=0o600)
                if path.exists():
                    path.unlink()


def _seal(key: bytes, plaintext: bytes, aad: str) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad.encode())


def _open(key: bytes, blob: bytes, aad: str) -> bytes:
    return AESGCM(key).decrypt(blob[:12], blob[12:], aad.encode())


class SealedTranscriptStore:
    """Design B. Same interface as ``InMemoryTranscriptStore``; content at rest is ciphertext only."""

    mode = "persisted"
    page_size = 50

    def __init__(self, blobs, keyring: Keyring, audit=None, default_retention_days: int = TRANSCRIPT_RETENTION_DAYS):
        self.blobs = blobs
        self.keyring = keyring
        self.default_retention_days = default_retention_days
        self._audit = audit or (lambda event, **kw: None)
        self._lock = threading.RLock()
        self._retention: dict[Principal, int] = {}

    # ---- interface -------------------------------------------------------------------------------
    def list_conversations(self, principal, cursor=None, query=None, archived="include"):
        self._expire(principal)
        convs = [c for c in (self._load(principal, cid) for cid in self._ids(principal)) if c]
        convs.sort(key=lambda c: c["updatedAt"], reverse=True)
        return page(convs, cursor, self.page_size, query, archived)

    def get_conversation(self, principal, conversation_id):
        self._expire(principal)
        conv = self._load(principal, conversation_id)
        if conv is None:
            return None
        return {"id": conv["id"], "title": conv["title"], "updatedAt": _iso(conv["updatedAt"]), **flags(conv),
                "messages": conv["messages"]}

    def append_message(self, principal, conversation_id, message):
        _check_id(conversation_id)
        if self.owned_by_other(principal, conversation_id):
            raise PermissionError("conversation belongs to another principal")

        def append(conv):
            unarchive_on_user_message(conv, message)
            conv["messages"].append(dict(message))
            conv["updatedAt"] = time.time()
            return None, True

        self._change(principal, conversation_id, append, create=True)

    def rename_conversation(self, principal, conversation_id, title):
        return self.update_conversation(principal, conversation_id, title=title)

    def update_conversation(self, principal, conversation_id, *, title=None, pinned=None, archived=None):
        """1.17.0: title / pin / archive. The flags are sealed with the transcript like the title."""
        if title is not None:
            clean_title(title)
        return self._change(principal, conversation_id, lambda conv: (apply_update(conv, title, pinned, archived), True))

    def auto_title(self, principal, conversation_id, title):
        """Server-derived title (api.titles). Only replaces the generic title, so a user rename always wins."""
        def retitle(conv):
            if conv["title"] != DEFAULT_TITLE:
                return False, False
            conv["title"] = clean_title(title)
            return True, True

        return bool(self._change(principal, conversation_id, retitle))

    def set_feedback(self, principal, conversation_id, message_id, feedback):
        """Same as the in-memory store; the feedback (and its comment) is sealed with the transcript."""
        def rate(conv):
            result = apply_feedback(conv, message_id, feedback)
            return result, result == "ok"

        return self._change(principal, conversation_id, rate) or "missing"

    def delete_conversation(self, principal, conversation_id):
        if not _SAFE.fullmatch(conversation_id or ""):
            return False
        with self._lock:
            prefix = self._prefix(principal)
            if self.blobs.get(f"{prefix}meta/{conversation_id}.json") is None:
                return False
            self._delete_objects(prefix, conversation_id)
        self._audit("conversation_deleted", customer=principal.customer, user=principal.user,
                    conversation_id=conversation_id)
        return True

    def delete_all(self, principal):
        """``delete_my_chats``: every object under the user's prefix, including the wrapped data key."""
        with self._lock:
            prefix = self._prefix(principal)
            keys = self.blobs.list(prefix)
            count = sum(1 for k in keys if "/meta/" in k)
            for key in keys:
                if key != f"{prefix}retention.json":  # the user's setting, not chat data: keep it
                    self.blobs.delete(key)
        self._audit("conversations_deleted", customer=principal.customer, user=principal.user, row_count=count)
        return count

    def set_retention(self, principal, days):
        if days < 0:
            raise ValueError("days must be >= 0")
        days = min(days, self.default_retention_days)
        with self._lock:
            self._retention[principal] = days
            # Persisted beside the user's objects so it survives restarts and the sweep can read it. Days only.
            self.blobs.put(f"{self._prefix(principal)}retention.json", json.dumps({"days": days}).encode())
        self._expire(principal)

    def retention_days(self, principal):
        if principal not in self._retention:
            self._retention[principal] = self._stored_days(self._prefix(principal))
        return self._retention[principal]

    def purge_expired(self, now: float | None = None) -> int:
        """Sweep every user under every customer, not just the ones who come back. Deletes expired meta and
        content. When a user has nothing left it also deletes the wrapped data key (a per-user crypto-shred; the
        next save makes a new one). Returns the number of conversations removed. The app runs this from its
        lifespan loop (CC_TRANSCRIPT_SWEEP_SECONDS)."""
        now = time.time() if now is None else now
        prefixes = sorted({m.group(0) for k in self.blobs.list("") if (m := _USER_PREFIX.match(k))})
        removed = 0
        for prefix in prefixes:
            removed += self._sweep(prefix, now - self._stored_days(prefix) * 86400, now)
        if removed:
            self._audit("transcripts_expired", row_count=removed)
        return removed

    def owner_of(self, conversation_id):  # kept for callers of the older interface
        return None

    def owned_by_other(self, principal, conversation_id) -> bool:
        if not _SAFE.fullmatch(conversation_id or ""):
            return False
        mine = f"{self._prefix(principal)}meta/{conversation_id}.json"
        return any(k.endswith(f"/meta/{conversation_id}.json") and k != mine for k in self.blobs.list(""))

    def crypto_shred_customer(self, customer: str) -> None:
        self.keyring.disable(customer)
        self._audit("transcripts_shredded", customer=customer)

    # ---- run events (replay from Last-Event-ID on any API task) ----------------------------------
    def put_run_events(self, principal, conversation_id, message_id, chunk_number: int, events: list[dict]) -> None:
        """Chunk ``chunk_number`` of a run's events. Write-once: a second writer of the same chunk is an error."""
        _check_id(conversation_id)
        _check_id(message_id)
        key = f"{self._prefix(principal)}runs/{conversation_id}/{message_id}/{chunk_number:06d}.bin"
        body = json.dumps(events, separators=(",", ":"), default=str).encode()
        sealed = _seal(self._dek(principal, create=True), body, self._aad(principal, f"run:{message_id}:{chunk_number}"))
        self.blobs.put(key, sealed, if_none_match=True)

    def run_events(self, principal, message_id) -> list[dict] | None:
        """Every stored event of the principal's run ``message_id`` in order, or None when it has no chunk."""
        if not _SAFE.fullmatch(message_id or ""):
            return None
        keys = [k for k in self.blobs.list(f"{self._prefix(principal)}runs/") if f"/{message_id}/" in k]
        dek = self._dek(principal, create=False) if keys else None
        if dek is None:
            return None
        events = []
        for key in sorted(keys):
            chunk_number = int(key.rsplit("/", 1)[1][:-4])
            blob = self.blobs.get(key)
            if blob is not None:
                events += json.loads(_open(dek, blob, self._aad(principal, f"run:{message_id}:{chunk_number}")))
        return events

    # ---- internals -------------------------------------------------------------------------------
    def _change(self, principal, conversation_id, change, create: bool = False):
        """Read the conversation object, apply ``change(conv) -> (result, write)`` and write it back with If-Match on
        the ETag it was read with (If-None-Match for a new one). Another writer in between makes the write fail; it
        is then re-read and ``change`` runs again. Returns None when the conversation is missing and not created."""
        for _attempt in range(CONDITIONAL_WRITE_ATTEMPTS):
            with self._lock:
                loaded = self._load_versioned(principal, conversation_id)
                if loaded is None and not create:
                    return None
                now = time.time()
                conv, etag = loaded or ({"id": conversation_id, "title": DEFAULT_TITLE, "createdAt": now,
                                         "updatedAt": now, "messages": []}, None)
                result, write = change(conv)
                if not write:
                    return result
                try:
                    self._save(principal, conv, etag)
                    return result
                except PreconditionFailed:
                    pass
            # another writer won: back off outside the lock with jitter, so writers that keep colliding spread out
            time.sleep(random.uniform(0, CONDITIONAL_WRITE_BACKOFF_SECONDS * (_attempt + 1)))
        raise ConcurrentUpdateConflict(f"conversation changed under {CONDITIONAL_WRITE_ATTEMPTS} conditional writes")

    def _delete_objects(self, prefix: str, conversation_id: str) -> None:
        for key in self.blobs.list(f"{prefix}runs/{conversation_id}/"):
            self.blobs.delete(key)
        self.blobs.delete(f"{prefix}content/{conversation_id}.bin")
        self.blobs.delete(f"{prefix}meta/{conversation_id}.json")

    @staticmethod
    def _user_hash(principal) -> str:
        return hashlib.sha256(f"{principal.customer}\x00{principal.user}".encode()).hexdigest()[:32]

    def _prefix(self, principal) -> str:
        if not _SAFE.fullmatch(principal.customer):
            raise ValueError("bad customer id")
        return f"{principal.customer}/transcripts/users/{self._user_hash(principal)}/"

    def _ids(self, principal) -> list[str]:
        prefix = f"{self._prefix(principal)}meta/"
        return [k[len(prefix):-5] for k in self.blobs.list(prefix) if k.endswith(".json")]

    def _aad(self, principal, what: str) -> str:
        return f"{principal.customer}|{self._user_hash(principal)}|{what}"

    def _dek(self, principal, create: bool) -> bytes | None:
        kek = self.keyring.key(principal.customer)
        key = f"{self._prefix(principal)}dek.bin"
        wrapped = self.blobs.get(key)
        if wrapped is not None:
            return _open(kek, wrapped, self._aad(principal, "transcript-dek"))
        if not create:
            return None
        dek = AESGCM.generate_key(256)
        try:
            self.blobs.put(key, _seal(kek, dek, self._aad(principal, "transcript-dek")), if_none_match=True)
        except PreconditionFailed:  # another task created it first: use that one, never overwrite it
            return _open(kek, self.blobs.get(key), self._aad(principal, "transcript-dek"))
        return dek

    def _save(self, principal, conv: dict, etag: str | None) -> None:
        """The content object conditionally (``etag`` None: create only), then its listing meta (no text)."""
        dek = self._dek(principal, create=True)
        sealed = {"title": conv["title"], "messages": conv["messages"]}
        sealed.update({k: conv[k] for k in ("pinned", "pinnedAt", "archived") if conv.get(k)})  # 1.17.0, sealed too
        body = json.dumps(sealed, separators=(",", ":"), default=str).encode()
        prefix = self._prefix(principal)
        self.blobs.put(f"{prefix}content/{conv['id']}.bin",
                       _seal(dek, body, self._aad(principal, f"content:{conv['id']}")),
                       if_match=etag, if_none_match=etag is None)
        meta = {"id": conv["id"], "customer": principal.customer, "createdAt": conv.get("createdAt", conv["updatedAt"]),
                "updatedAt": conv["updatedAt"], "messageCount": len(conv["messages"])}
        self.blobs.put(f"{prefix}meta/{conv['id']}.json", json.dumps(meta).encode())

    def _load(self, principal, conversation_id) -> dict | None:
        if not _SAFE.fullmatch(conversation_id or "") or self.blobs.get(f"{self._prefix(principal)}meta/{conversation_id}.json") is None:
            return None  # not listed: deleted, or a crash between the content and meta writes
        loaded = self._load_versioned(principal, conversation_id)
        return loaded and loaded[0]

    def _load_versioned(self, principal, conversation_id) -> tuple[dict, str] | None:
        if not _SAFE.fullmatch(conversation_id or ""):
            return None
        prefix = self._prefix(principal)
        found = self.blobs.get_with_etag(f"{prefix}content/{conversation_id}.bin")
        if found is None:
            return None
        blob, etag = found
        dek = self._dek(principal, create=False)
        if dek is None:
            return None
        body = json.loads(_open(dek, blob, self._aad(principal, f"content:{conversation_id}")))
        meta_raw = self.blobs.get(f"{prefix}meta/{conversation_id}.json")
        meta = json.loads(meta_raw) if meta_raw else {"updatedAt": time.time()}
        return {"id": conversation_id, "title": body["title"], "messages": body["messages"],
                "createdAt": meta.get("createdAt"), "updatedAt": meta["updatedAt"],
                "pinned": bool(body.get("pinned")), "pinnedAt": body.get("pinnedAt"),
                "archived": bool(body.get("archived"))}, etag

    def _expire(self, principal) -> None:
        now = time.time()
        self._sweep(self._prefix(principal), now - self.retention_days(principal) * 86400, now, shred_empty=False)

    def _stored_days(self, prefix: str) -> int:
        raw = self.blobs.get(f"{prefix}retention.json")
        try:
            return min(int(json.loads(raw)["days"]), self.default_retention_days) if raw else self.default_retention_days
        except (ValueError, KeyError, TypeError):
            return self.default_retention_days

    def _sweep(self, prefix: str, cutoff: float, now: float, shred_empty: bool = True) -> int:
        removed = 0
        with self._lock:
            metas = [k for k in self.blobs.list(f"{prefix}meta/") if k.endswith(".json")]
            for key in metas:
                raw = self.blobs.get(key)
                if raw and json.loads(raw)["updatedAt"] < cutoff:
                    self._delete_objects(prefix, key[len(f"{prefix}meta/"):-5])
                    removed += 1
            if shred_empty and removed == len(metas) and self.blobs.get(f"{prefix}dek.bin") is not None:
                # Stray content without meta can't be listed or read anyway; drop it with the key.
                for key in self.blobs.list(f"{prefix}content/") + self.blobs.list(f"{prefix}runs/"):
                    self.blobs.delete(key)
                self.blobs.delete(f"{prefix}dek.bin")
        return removed


def _check_id(value: str) -> None:
    if not _SAFE.fullmatch(value or ""):
        raise ValueError("bad conversation id")
