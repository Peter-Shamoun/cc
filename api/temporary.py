"""Temporary chats (contract 1.20.0, CONTRACT.md §11.1), like ChatGPT's Temporary chat.

A turn sent with ``temporary: true`` is never written to the transcript store: it is not listed, not searchable, not
exportable, can't be rated, and gets no server title. Its history lives only in this process, so edit,
regenerate and clarify-resume still work inside the open chat, and it is dropped after ``ttl_seconds`` without
activity, on sign-out-all/delete, or when the process restarts. Nothing here touches disk.
"""
from __future__ import annotations

import time

from api.store import InMemoryTranscriptStore

#: idle lifetime of a temporary chat's server-side history (the open tab keeps its own copy)
DEFAULT_TTL_SECONDS = 3600
#: at most this many temporary chats per user; the least recently used one is dropped first
MAX_PER_USER = 20


class TemporaryChats(InMemoryTranscriptStore):
    mode = "temporary"

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS, max_per_user: int = MAX_PER_USER, audit=None):
        super().__init__(audit=audit)
        self.ttl_seconds = ttl_seconds
        self.max_per_user = max_per_user

    def append_message(self, principal, conversation_id, message, *, create_if_missing=True):
        super().append_message(principal, conversation_id, message, create_if_missing=create_if_missing)
        with self._lock:
            mine = sorted((cid for cid, owner in self._owners.items() if owner == principal),
                          key=lambda cid: self._items[cid]["updatedAt"])
            for cid in mine[:-self.max_per_user] if len(mine) > self.max_per_user else []:
                del self._items[cid], self._owners[cid]

    def get_conversation(self, principal, conversation_id):
        conv = super().get_conversation(principal, conversation_id)
        if conv is not None:
            with self._lock:  # reading the history (edit, regenerate) is activity
                if conversation_id in self._items:
                    self._items[conversation_id]["updatedAt"] = time.time()
        return conv

    def auto_title(self, principal, conversation_id, title):
        return False  # a temporary chat has no title, like ChatGPT's

    def set_feedback(self, principal, conversation_id, message_id, feedback):
        return "missing"

    def list_conversations(self, principal, cursor=None, query=None, archived="include"):
        raise RuntimeError("temporary chats are never listed")

    def has(self, conversation_id: str) -> bool:
        with self._lock:
            return conversation_id in self._items

    def purge_expired(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self._lock:
            gone = [cid for cid, c in self._items.items() if c["updatedAt"] < now - self.ttl_seconds]
            for cid in gone:
                del self._items[cid], self._owners[cid]
        return len(gone)

    def _expire(self, principal):
        self.purge_expired()
