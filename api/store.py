"""Transcript storage and download registry.

``TranscriptStore`` mirrors the interface in the frontend spec (section 6.4). The in-memory
implementation is the ``ephemeral`` design: nothing at rest, gone on restart. Conversations
are tenant- and user-scoped; another principal sees nothing.
"""
from __future__ import annotations

import csv
import io
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol

from api.ids import new_id


@dataclass(frozen=True)
class Principal:
    customer: str
    user: str


class TranscriptStore(Protocol):
    def list_conversations(self, principal: Principal, cursor: str | None = None) -> dict: ...
    def get_conversation(self, principal: Principal, conversation_id: str) -> dict | None: ...
    def append_message(self, principal: Principal, conversation_id: str, message: dict) -> None: ...
    def delete_conversation(self, principal: Principal, conversation_id: str) -> bool: ...
    def set_retention(self, principal: Principal, days: int) -> None: ...


@dataclass
class _Conversation:
    id: str
    owner: Principal
    title: str
    updated_at: float
    messages: list = field(default_factory=list)


class InMemoryTranscriptStore:
    """Ephemeral store. Titles never echo the question (it may hold PHI); they are generic until renamed."""

    page_size = 50

    def __init__(self, audit=None):
        self._lock = threading.Lock()
        self._items: dict[str, _Conversation] = {}
        self._retention_days: dict[Principal, int] = {}
        self._audit = audit or (lambda event, **kw: None)

    def list_conversations(self, principal, cursor=None):
        self._expire(principal)
        with self._lock:
            mine = sorted((c for c in self._items.values() if c.owner == principal),
                          key=lambda c: c.updated_at, reverse=True)
        start = int(cursor or 0)
        page = mine[start:start + self.page_size]
        out = {"items": [{"id": c.id, "title": c.title, "updatedAt": _iso(c.updated_at)} for c in page]}
        if start + self.page_size < len(mine):
            out["next"] = str(start + self.page_size)
        return out

    def get_conversation(self, principal, conversation_id):
        self._expire(principal)
        with self._lock:
            conv = self._items.get(conversation_id)
            if conv is None or conv.owner != principal:
                return None
            return {"id": conv.id, "title": conv.title, "messages": [dict(m) for m in conv.messages]}

    def append_message(self, principal, conversation_id, message):
        with self._lock:
            conv = self._items.get(conversation_id)
            if conv is None:
                conv = _Conversation(conversation_id, principal, "New analysis", time.time())
                self._items[conversation_id] = conv
            elif conv.owner != principal:
                raise PermissionError("conversation belongs to another principal")
            conv.messages.append(dict(message))
            conv.updated_at = time.time()

    def owner_of(self, conversation_id):
        with self._lock:
            conv = self._items.get(conversation_id)
            return conv.owner if conv else None

    def delete_conversation(self, principal, conversation_id):
        with self._lock:
            conv = self._items.get(conversation_id)
            if conv is None or conv.owner != principal:
                return False
            del self._items[conversation_id]
        self._audit("conversation_deleted", customer=principal.customer, user=principal.user,
                    conversation_id=conversation_id)
        return True

    def set_retention(self, principal, days):
        if days < 0:
            raise ValueError("days must be >= 0")
        with self._lock:
            self._retention_days[principal] = days
        self._expire(principal)

    def _expire(self, principal):
        days = self._retention_days.get(principal)
        if days is None:
            return
        cutoff = time.time() - days * 86400
        with self._lock:
            for cid in [c.id for c in self._items.values() if c.owner == principal and c.updated_at < cutoff]:
                del self._items[cid]


@dataclass
class Download:
    id: str
    owner: Principal
    name: str
    columns: list
    rows: list
    expires_at: float

    def csv_bytes(self) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\r\n")
        writer.writerow(self.columns)
        for row in self.rows:
            writer.writerow(["" if v is None else v for v in row])
        return buf.getvalue().encode("utf-8")


class DownloadRegistry:
    """Full results behind opaque ids. Bound to the principal that produced them; expire after ``ttl``."""

    def __init__(self, ttl_seconds: float = 900.0):
        self.ttl = ttl_seconds
        self._lock = threading.Lock()
        self._items: dict[str, Download] = {}

    def put(self, owner: Principal, name: str, columns: list, rows: list) -> Download:
        item = Download(new_id("f_"), owner, name, list(columns), [list(r) for r in rows], time.time() + self.ttl)
        with self._lock:
            self._purge()
            self._items[item.id] = item
        return item

    def get(self, owner: Principal, download_id: str) -> Download | None:
        with self._lock:
            self._purge()
            item = self._items.get(download_id)
        if item is None or item.owner != owner:
            return None
        return item

    def _purge(self):
        now = time.time()
        for key in [k for k, v in self._items.items() if v.expires_at < now]:
            del self._items[key]


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
