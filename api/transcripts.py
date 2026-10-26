"""Transcript store selection and Design B (persisted, envelope-encrypted) for the chat API.

``CC_TRANSCRIPT_MODE`` (resolved by ``ccsim.transcripts.resolve_transcript_mode``, so the API and the
simulator agree):

* unset / ``ephemeral`` -> ``InMemoryTranscriptStore`` (Design A, the recommended default).
* ``persisted`` -> ``SealedTranscriptStore`` (Design B), the same shape as docs/data-backend/chat-transcripts.md:

  ``{customer}/transcripts/users/{user_hash}/meta/{conversation}.json``   no text: ids, times, counts
  ``{customer}/transcripts/users/{user_hash}/content/{conversation}.bin`` AES-GCM(title + messages)
  ``{customer}/transcripts/users/{user_hash}/dek.bin``                   per-user data key, wrapped

  The data key is wrapped by the customer's transcript key (``Keyring``; in production the KMS CMK
  ``alias/claimclarity/{customer}/transcripts``, here a local stand-in). Each AES-GCM seal binds
  customer, user and conversation as associated data, so a blob moved to another user or chat fails
  to open. Deleting all of a user's chats destroys their wrapped data key; ``crypto_shred_customer``
  destroys the customer key, after which every copy of that customer's content is unreadable.

  Retention: ``TRANSCRIPT_RETENTION_DAYS`` (90) unless the user sets a shorter one (persisted as
  ``retention.json``); enforced on access and by ``purge_expired`` (the app's background sweep).
  ``CC_TRANSCRIPT_DIR`` keeps blobs on disk (files 0600); unset keeps them in memory (tests, demo).

Nothing here logs text. Audit events carry ids and counts only (``AuditLog`` allowlist).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from api.store import DEFAULT_TITLE, InMemoryTranscriptStore, Principal, _iso, clean_title, page

TRANSCRIPT_RETENTION_DAYS = 90
_SAFE = re.compile(r"^[A-Za-z0-9_:.-]{1,128}$")
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
        root = os.environ.get("CC_TRANSCRIPT_DIR")
        blobs = DirBlobs(Path(root)) if root else MemoryBlobs()
        keys_dir = os.environ.get("CC_TRANSCRIPT_KEYS_DIR")
        return SealedTranscriptStore(blobs, Keyring(Path(keys_dir) if keys_dir else None), audit=audit)
    return InMemoryTranscriptStore(audit=audit)


class MemoryBlobs:
    def __init__(self):
        self._data: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def put(self, key: str, body: bytes) -> None:
        with self._lock:
            self._data[key] = bytes(body)

    def get(self, key: str) -> bytes | None:
        with self._lock:
            return self._data.get(key)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def list(self, prefix: str) -> list[str]:
        with self._lock:
            return sorted(k for k in self._data if k.startswith(prefix))


class DirBlobs:
    """Local-disk stand-in for the S3 prefix. Files are 0600, directories 0700, writes are atomic."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("key escapes the transcript root")
        return path

    def put(self, key: str, body: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_suffix(path.suffix + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
        os.replace(tmp, path)

    def get(self, key: str) -> bytes | None:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            return None

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass

    def list(self, prefix: str) -> list[str]:
        base = self.root
        return sorted(str(p.relative_to(base)) for p in base.rglob("*")
                      if p.is_file() and not p.name.endswith(".tmp") and str(p.relative_to(base)).startswith(prefix))


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
                path = self._file(customer)
                if path and path.exists():
                    self._keys[customer] = path.read_bytes()
                else:
                    self._keys[customer] = AESGCM.generate_key(256)
                    if path:
                        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        with os.fdopen(fd, "wb") as fh:
                            fh.write(self._keys[customer])
            return self._keys[customer]

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
    def list_conversations(self, principal, cursor=None, query=None):
        self._expire(principal)
        convs = [c for c in (self._load(principal, cid) for cid in self._ids(principal)) if c]
        convs.sort(key=lambda c: c["updatedAt"], reverse=True)
        return page(convs, cursor, self.page_size, query)

    def get_conversation(self, principal, conversation_id):
        self._expire(principal)
        conv = self._load(principal, conversation_id)
        if conv is None:
            return None
        return {"id": conv["id"], "title": conv["title"], "updatedAt": _iso(conv["updatedAt"]),
                "messages": conv["messages"]}

    def append_message(self, principal, conversation_id, message):
        _check_id(conversation_id)
        with self._lock:
            if self.owned_by_other(principal, conversation_id):
                raise PermissionError("conversation belongs to another principal")
            conv = self._load(principal, conversation_id) or {
                "id": conversation_id, "title": DEFAULT_TITLE, "createdAt": time.time(), "updatedAt": time.time(),
                "messages": []}
            conv["messages"].append(dict(message))
            conv["updatedAt"] = time.time()
            self._save(principal, conv)

    def rename_conversation(self, principal, conversation_id, title):
        title = clean_title(title)
        with self._lock:
            conv = self._load(principal, conversation_id)
            if conv is None:
                return None
            conv["title"] = title
            self._save(principal, conv)
            return {"id": conv["id"], "title": title, "updatedAt": _iso(conv["updatedAt"])}

    def auto_title(self, principal, conversation_id, title):
        """Server-derived title (api.titles). Only replaces the generic title, so a user rename always wins."""
        with self._lock:
            conv = self._load(principal, conversation_id)
            if conv is None or conv["title"] != DEFAULT_TITLE:
                return False
            conv["title"] = clean_title(title)
            self._save(principal, conv)
            return True

    def delete_conversation(self, principal, conversation_id):
        if not _SAFE.match(conversation_id or ""):
            return False
        with self._lock:
            prefix = self._prefix(principal)
            if self.blobs.get(f"{prefix}meta/{conversation_id}.json") is None:
                return False
            self.blobs.delete(f"{prefix}content/{conversation_id}.bin")
            self.blobs.delete(f"{prefix}meta/{conversation_id}.json")
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
        if not _SAFE.match(conversation_id or ""):
            return False
        mine = f"{self._prefix(principal)}meta/{conversation_id}.json"
        return any(k.endswith(f"/meta/{conversation_id}.json") and k != mine for k in self.blobs.list(""))

    def crypto_shred_customer(self, customer: str) -> None:
        self.keyring.disable(customer)
        self._audit("transcripts_shredded", customer=customer)

    # ---- internals -------------------------------------------------------------------------------
    @staticmethod
    def _user_hash(principal) -> str:
        return hashlib.sha256(f"{principal.customer}\x00{principal.user}".encode()).hexdigest()[:32]

    def _prefix(self, principal) -> str:
        if not _SAFE.match(principal.customer):
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
        self.blobs.put(key, _seal(kek, dek, self._aad(principal, "transcript-dek")))
        return dek

    def _save(self, principal, conv: dict) -> None:
        dek = self._dek(principal, create=True)
        body = json.dumps({"title": conv["title"], "messages": conv["messages"]}, separators=(",", ":"),
                          default=str).encode()
        prefix = self._prefix(principal)
        self.blobs.put(f"{prefix}content/{conv['id']}.bin",
                       _seal(dek, body, self._aad(principal, f"content:{conv['id']}")))
        meta = {"id": conv["id"], "customer": principal.customer, "createdAt": conv.get("createdAt", conv["updatedAt"]),
                "updatedAt": conv["updatedAt"], "messageCount": len(conv["messages"])}
        self.blobs.put(f"{prefix}meta/{conv['id']}.json", json.dumps(meta).encode())

    def _load(self, principal, conversation_id) -> dict | None:
        if not _SAFE.match(conversation_id or ""):
            return None
        prefix = self._prefix(principal)
        meta_raw = self.blobs.get(f"{prefix}meta/{conversation_id}.json")
        blob = self.blobs.get(f"{prefix}content/{conversation_id}.bin")
        if meta_raw is None or blob is None:
            return None
        dek = self._dek(principal, create=False)
        if dek is None:
            return None
        body = json.loads(_open(dek, blob, self._aad(principal, f"content:{conversation_id}")))
        meta = json.loads(meta_raw)
        return {"id": conversation_id, "title": body["title"], "messages": body["messages"],
                "createdAt": meta.get("createdAt"), "updatedAt": meta["updatedAt"]}

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
                    cid = key[len(f"{prefix}meta/"):-5]
                    self.blobs.delete(f"{prefix}content/{cid}.bin")
                    self.blobs.delete(key)
                    removed += 1
            if shred_empty and removed == len(metas) and self.blobs.get(f"{prefix}dek.bin") is not None:
                # Stray content without meta can't be listed or read anyway; drop it with the key.
                for key in self.blobs.list(f"{prefix}content/"):
                    self.blobs.delete(key)
                self.blobs.delete(f"{prefix}dek.bin")
        return removed


def _check_id(value: str) -> None:
    if not _SAFE.match(value or ""):
        raise ValueError("bad conversation id")
