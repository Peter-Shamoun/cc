"""Share a conversation inside the organization (contract 1.11.0, CONTRACT.md §17).

A share is a read-only, **org-scoped** link: only signed-in users of the same customer can open it, and there are
no public links. Result data is withheld unless the owner opts in (`includeData`, default false). The owner can
revoke it at any time. A conversation has at most one active share, so sharing again returns it (with
`includeData` updated).

What a viewer gets (`SharedConversation`):
* the user questions, the answer prose, SQL, steps, callouts and sources;
* with `includeData: false`: tables keep their columns and `rowCount`, but `rows` are empty and `dataWithheld` is
  true. Charts and table/chart/report/image artifacts (which carry rows) are left out;
* with `includeData: true`: the stored preview rows (at most 50 per table), never the CSV (`csvFileId` is removed:
  downloads stay with the owner);
* never: feedback (the rating and comment are personal), the owner's user id, or file ids.

The demo keeps shares in memory. A real backend must persist them next to the transcript and must revoke them
when the conversation is deleted or the customer key is destroyed.
"""
from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass

DATA_ARTIFACT_KINDS = {"table", "chart", "report", "image"}


@dataclass
class Share:
    share_id: str
    customer: str
    user: str
    conversation_id: str
    include_data: bool
    created_at: str


class ShareRegistry:
    def __init__(self):
        self._by_id: dict[str, Share] = {}
        self._lock = threading.Lock()

    def put(self, customer: str, user: str, conversation_id: str, include_data: bool, now: str) -> Share:
        with self._lock:
            for s in self._by_id.values():
                if (s.customer, s.user, s.conversation_id) == (customer, user, conversation_id):
                    s.include_data = include_data
                    return s
            # 144 random bits: the link is a capability inside the org, so it must not be guessable
            share = Share("sh_" + secrets.token_urlsafe(18), customer, user, conversation_id, include_data, now)
            self._by_id[share.share_id] = share
            return share

    def for_conversation(self, customer: str, user: str, conversation_id: str) -> Share | None:
        with self._lock:
            return next((s for s in self._by_id.values()
                         if (s.customer, s.user, s.conversation_id) == (customer, user, conversation_id)), None)

    def revoke(self, customer: str, user: str, conversation_id: str) -> bool:
        with self._lock:
            hit = [k for k, s in self._by_id.items() if (s.customer, s.user, s.conversation_id) == (customer, user, conversation_id)]
            for k in hit:
                del self._by_id[k]
            return bool(hit)

    def get(self, share_id: str, customer: str) -> Share | None:
        """None for an unknown, revoked or other-organization share: the caller answers 404 for all three."""
        with self._lock:
            s = self._by_id.get(share_id)
        return s if s is not None and secrets.compare_digest(s.customer, customer) else None


def link(share: Share) -> dict:
    return {"shareId": share.share_id, "conversationId": share.conversation_id, "url": f"/shared/{share.share_id}",
            "scope": "organization", "includeData": share.include_data, "createdAt": share.created_at}


def _part(p: dict, include_data: bool) -> dict | None:
    p = {k: v for k, v in p.items() if k != "csvFileId"}
    if include_data:
        return p
    t = p.get("type")
    if t == "table":
        return {**p, "rows": [], "dataWithheld": True}
    if t == "chart" or (t == "artifact" and p.get("kind") in DATA_ARTIFACT_KINDS):
        return None
    return p


def shared_view(share: Share, conv: dict) -> dict:
    messages = []
    for m in conv.get("messages", []):
        m = {k: v for k, v in m.items() if k != "feedback"}
        if "parts" in m:
            m["parts"] = [q for q in (_part(p, share.include_data) for p in m["parts"]) if q is not None]
        messages.append(m)
    return {"shareId": share.share_id, "title": conv.get("title"), "updatedAt": conv.get("updatedAt"),
            "includeData": share.include_data, "createdAt": share.created_at, "messages": messages}
