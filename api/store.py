"""Transcript storage and download registry.

``TranscriptStore`` is the spec 6.4 interface plus the chat-history operations the UI needs (rename,
search, delete-all). Two designs from docs/data-backend/chat-transcripts.md, chosen by
``CC_TRANSCRIPT_MODE`` (see ``api.transcripts.make_store``):

* ``ephemeral`` (Design A, the default): ``InMemoryTranscriptStore``. Nothing at rest, gone on restart.
* ``persisted`` (Design B): ``api.transcripts.SealedTranscriptStore``. Bodies and titles envelope-encrypted
  under a per-user data key wrapped by a per-customer key; metadata carries no text.

Conversations are tenant- and user-scoped; another principal sees nothing (404, never 403).
Titles are generic until the user renames them: a question can hold PHI, so it is never echoed as a title.
"""
from __future__ import annotations

import csv
import datetime
import io
import re
import threading
import time
from dataclasses import dataclass
from typing import Protocol

from api.feedback import apply as apply_feedback
from api.ids import new_id

DEFAULT_TITLE = "New analysis"
ARCHIVED_FILTERS = ("include", "exclude", "only")  # 1.17.0: GET /api/conversations?archived=
MAX_TITLE = 120
SNIPPET_RADIUS = 48


@dataclass(frozen=True)
class Principal:
    customer: str
    user: str


class TranscriptStore(Protocol):
    mode: str
    def list_conversations(self, principal: Principal, cursor: str | None = None, query: str | None = None,
                           archived: str = "include") -> dict: ...
    def get_conversation(self, principal: Principal, conversation_id: str) -> dict | None: ...
    def append_message(self, principal: Principal, conversation_id: str, message: dict) -> None: ...
    def rename_conversation(self, principal: Principal, conversation_id: str, title: str) -> dict | None: ...
    def update_conversation(self, principal: Principal, conversation_id: str, *, title: str | None = None,
                            pinned: bool | None = None, archived: bool | None = None) -> dict | None: ...
    def auto_title(self, principal: Principal, conversation_id: str, title: str) -> bool: ...
    def set_feedback(self, principal: Principal, conversation_id: str, message_id: str, feedback: dict | None) -> str: ...
    def delete_conversation(self, principal: Principal, conversation_id: str) -> bool: ...
    def delete_all(self, principal: Principal) -> int: ...
    def set_retention(self, principal: Principal, days: int) -> None: ...
    def retention_days(self, principal: Principal) -> int | None: ...
    def purge_expired(self, now: float | None = None) -> int: ...
    def owned_by_other(self, principal: Principal, conversation_id: str) -> bool: ...


def clean_title(title: str) -> str:
    """User-chosen title: one line, trimmed, bounded. Empty means back to the generic title."""
    text = " ".join(str(title or "").split())
    if any(ord(ch) < 32 for ch in text):
        raise ValueError("title has control characters")
    return text[:MAX_TITLE] or DEFAULT_TITLE


def message_text(message: dict) -> str:
    """Searchable text of a stored message: user text plus assistant text/markdown parts (no table cells)."""
    chunks = [message.get("text") or ""]
    for part in message.get("parts") or []:
        if part.get("type") in ("text", "reasoning") and isinstance(part.get("text"), str):
            chunks.append(part["text"])
        elif part.get("type") in ("callout",):
            chunks.extend(str(i.get("text", "")) for i in part.get("items") or [] if isinstance(i, dict))
    return "\n".join(c for c in chunks if c)


def find_snippet(conv: dict, query: str) -> str | None:
    """First match of ``query`` (case-insensitive) in the title or any message, with some context."""
    q = query.casefold()
    if q in conv["title"].casefold():
        return conv["title"][:2 * SNIPPET_RADIUS]
    for message in conv["messages"]:
        text = " ".join(message_text(message).split())
        at = text.casefold().find(q)
        if at >= 0:
            start, end = max(0, at - SNIPPET_RADIUS), min(len(text), at + len(query) + SNIPPET_RADIUS)
            return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")
    return None


def flags(conv: dict) -> dict:
    """1.17.0: the pin/archive flags every summary carries (always present, false by default)."""
    return {"pinned": bool(conv.get("pinned")), "archived": bool(conv.get("archived"))}


def apply_update(conv: dict, title: str | None, pinned: bool | None, archived: bool | None) -> dict:
    """PATCH semantics (1.17.0), shared by both stores. Pin/archive never touch ``updatedAt`` (the sidebar order of
    unpinned chats is recency). Archiving drops the pin; pinning an archived chat brings it back (unarchives it).
    ``pinned`` and ``archived`` both true is rejected by the caller. Returns the RenameResponse body."""
    if title is not None:
        conv["title"] = clean_title(title)
    if archived is not None:
        conv["archived"] = archived
        if archived:
            conv["pinned"], conv["pinnedAt"] = False, None
    if pinned is not None:
        if pinned and not conv.get("pinned"):
            conv["pinnedAt"] = time.time()
        elif not pinned:
            conv["pinnedAt"] = None
        conv["pinned"] = pinned
        if pinned:
            conv["archived"] = False
    return {"id": conv["id"], "title": conv["title"], "updatedAt": _iso(conv["updatedAt"]), **flags(conv)}


def unarchive_on_user_message(conv: dict, message: dict) -> None:
    """1.17.0: a new user message in an archived chat brings it back to the sidebar (assistant appends don't)."""
    if message.get("role") == "user" and conv.get("archived"):
        conv["archived"] = False


def page(convs: list[dict], cursor: str | None, size: int, query: str | None, archived: str = "include") -> dict:
    """``convs`` newest first, each {id, title, updatedAt(float), messages, pinned?, archived?}. Returns the list
    endpoint body. 1.17.0: ``archived`` filters (include / exclude / only); the plain list (no query) puts pinned
    chats first, most recently pinned on top; search stays newest first."""
    if query:
        query = query.strip()[:200]
    if archived == "exclude":
        convs = [c for c in convs if not c.get("archived")]
    elif archived == "only":
        convs = [c for c in convs if c.get("archived")]
    if not query:
        convs = sorted(convs, key=lambda c: (not c.get("pinned"), -(c.get("pinnedAt") or 0) if c.get("pinned") else 0))
    rows = []
    for c in convs:
        item = {"id": c["id"], "title": c["title"], "updatedAt": _iso(c["updatedAt"]),
                "messageCount": len(c["messages"]), **flags(c)}
        if query:
            snippet = find_snippet(c, query)
            if snippet is None:
                continue
            item["snippet"] = snippet
        rows.append(item)
    try:
        start = max(0, int(cursor or 0))
    except ValueError:
        start = 0
    out = {"items": rows[start:start + size]}
    if start + size < len(rows):
        out["next"] = str(start + size)
    return out


class InMemoryTranscriptStore:
    """Design A (ephemeral). Message text exists only in this process."""

    mode = "ephemeral"
    page_size = 50

    def __init__(self, audit=None):
        self._lock = threading.RLock()
        self._items: dict[str, dict] = {}
        self._owners: dict[str, Principal] = {}
        self._retention_days: dict[Principal, int] = {}
        self._audit = audit or (lambda event, **kw: None)

    def list_conversations(self, principal, cursor=None, query=None, archived="include"):
        self._expire(principal)
        with self._lock:
            mine = sorted((c for cid, c in self._items.items() if self._owners[cid] == principal),
                          key=lambda c: c["updatedAt"], reverse=True)
            snapshot = [{**c, "messages": list(c["messages"])} for c in mine]
        return page(snapshot, cursor, self.page_size, query, archived)

    def get_conversation(self, principal, conversation_id):
        self._expire(principal)
        with self._lock:
            conv = self._mine(principal, conversation_id)
            if conv is None:
                return None
            return {"id": conv["id"], "title": conv["title"], "updatedAt": _iso(conv["updatedAt"]), **flags(conv),
                    "messages": [dict(m) for m in conv["messages"]]}

    def append_message(self, principal, conversation_id, message):
        with self._lock:
            conv = self._items.get(conversation_id)
            if conv is None:
                conv = {"id": conversation_id, "title": DEFAULT_TITLE, "updatedAt": time.time(), "messages": []}
                self._items[conversation_id] = conv
                self._owners[conversation_id] = principal
            elif self._owners[conversation_id] != principal:
                raise PermissionError("conversation belongs to another principal")
            unarchive_on_user_message(conv, message)
            conv["messages"].append(dict(message))
            conv["updatedAt"] = time.time()

    def rename_conversation(self, principal, conversation_id, title):
        return self.update_conversation(principal, conversation_id, title=title)

    def update_conversation(self, principal, conversation_id, *, title=None, pinned=None, archived=None):
        if title is not None:
            clean_title(title)  # validate before touching anything
        with self._lock:
            conv = self._mine(principal, conversation_id)
            if conv is None:
                return None
            return apply_update(conv, title, pinned, archived)

    def auto_title(self, principal, conversation_id, title):
        """Server-derived title (api.titles). Only replaces the generic title, so a user rename always wins."""
        with self._lock:
            conv = self._mine(principal, conversation_id)
            if conv is None or conv["title"] != DEFAULT_TITLE:
                return False
            conv["title"] = clean_title(title)
            return True

    def set_feedback(self, principal, conversation_id, message_id, feedback):
        """'ok' | 'missing' (no such conversation/message for this user) | 'not_assistant' (api.feedback.apply)."""
        with self._lock:
            conv = self._mine(principal, conversation_id)
            return "missing" if conv is None else apply_feedback(conv, message_id, feedback)

    def owner_of(self, conversation_id):
        with self._lock:
            return self._owners.get(conversation_id)

    def owned_by_other(self, principal, conversation_id) -> bool:
        owner = self.owner_of(conversation_id)
        return owner is not None and owner != principal

    def delete_conversation(self, principal, conversation_id):
        with self._lock:
            if self._mine(principal, conversation_id) is None:
                return False
            del self._items[conversation_id], self._owners[conversation_id]
        self._audit("conversation_deleted", customer=principal.customer, user=principal.user,
                    conversation_id=conversation_id)
        return True

    def delete_all(self, principal):
        with self._lock:
            ids = [cid for cid, owner in self._owners.items() if owner == principal]
            for cid in ids:
                del self._items[cid], self._owners[cid]
        self._audit("conversations_deleted", customer=principal.customer, user=principal.user, row_count=len(ids))
        return len(ids)

    def set_retention(self, principal, days):
        if days < 0:
            raise ValueError("days must be >= 0")
        with self._lock:
            self._retention_days[principal] = days
        self._expire(principal)

    def retention_days(self, principal):
        return self._retention_days.get(principal)

    def purge_expired(self, now: float | None = None) -> int:
        """Sweep every principal that set a retention, not just the ones who come back."""
        now = time.time() if now is None else now
        with self._lock:
            gone = [cid for cid, c in self._items.items()
                    if (days := self._retention_days.get(self._owners[cid])) is not None
                    and c["updatedAt"] < now - days * 86400]
            for cid in gone:
                del self._items[cid], self._owners[cid]
        if gone:
            self._audit("transcripts_expired", row_count=len(gone))
        return len(gone)

    def _mine(self, principal, conversation_id):
        conv = self._items.get(conversation_id)
        if conv is None or self._owners.get(conversation_id) != principal:
            return None
        return conv

    def _expire(self, principal):
        days = self._retention_days.get(principal)
        if days is None:
            return
        cutoff = time.time() - days * 86400
        with self._lock:
            for cid in [cid for cid, c in self._items.items()
                        if self._owners[cid] == principal and c["updatedAt"] < cutoff]:
                del self._items[cid], self._owners[cid]


def csv_filename(title: str | None, when: float | None = None) -> str:
    """Same name the web client gives a CSV (web/src/lib/csv.ts csvFilename): claimclarity_<slug>_<yyyy-mm-dd>.csv.
    The slug comes from a part title (e.g. "Result"), never from row values or question text, so there's no PHI in names.
    The date is UTC, like the client's toISOString(). Shared vectors: web/src/lib/csvFilename.vectors.json."""
    slug = re.sub(r"[^a-z0-9]+", "_", (title or "table").lower())
    slug = re.sub(r"^_|_$", "", slug)[:48] or "table"
    day = datetime.datetime.fromtimestamp(time.time() if when is None else when, datetime.timezone.utc).date()
    return f"claimclarity_{slug}_{day.isoformat()}.csv"


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def defuse_cell(value):
    """OWASP CSV injection: text starting with = + - @ TAB CR would run as a spreadsheet formula; prefix it with '.
    Numbers (including negatives) are left numeric."""
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


@dataclass
class Download:
    id: str
    owner: Principal
    name: str
    columns: list
    rows: list
    expires_at: float
    conversation_id: str | None = None

    def csv_bytes(self) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\r\n")
        writer.writerow([defuse_cell(c) for c in self.columns])
        for row in self.rows:
            writer.writerow(["" if v is None else defuse_cell(v) for v in row])
        return buf.getvalue().encode("utf-8")


class DownloadRegistry:
    """Full results behind opaque ids. Bound to the principal that produced them; expire after ``ttl``."""

    def __init__(self, ttl_seconds: float = 900.0):
        self.ttl = ttl_seconds
        self._lock = threading.Lock()
        self._items: dict[str, Download] = {}

    def put(self, owner: Principal, name: str, columns: list, rows: list,
            conversation_id: str | None = None) -> Download:
        item = Download(new_id("f_"), owner, name, list(columns), [list(r) for r in rows], time.time() + self.ttl,
                        conversation_id)
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

    def scoped(self, conversation_id: str | None) -> "_ScopedDownloads":
        """A ``put``-only view that tags each download with its conversation, so deleting it can revoke them."""
        return _ScopedDownloads(self, conversation_id)

    def revoke(self, owner: Principal, conversation_id: str | None = None) -> int:
        """Drop the owner's downloads for one conversation, or all of them when ``conversation_id`` is None."""
        with self._lock:
            keys = [k for k, v in self._items.items()
                    if v.owner == owner and (conversation_id is None or v.conversation_id == conversation_id)]
            for key in keys:
                del self._items[key]
        return len(keys)

    def _purge(self):
        now = time.time()
        for key in [k for k, v in self._items.items() if v.expires_at < now]:
            del self._items[key]


class _ScopedDownloads:
    def __init__(self, registry: DownloadRegistry, conversation_id: str | None):
        self._registry, self._conversation_id = registry, conversation_id

    def put(self, owner: Principal, name: str, columns: list, rows: list) -> Download:
        return self._registry.put(owner, name, columns, rows, self._conversation_id)


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
