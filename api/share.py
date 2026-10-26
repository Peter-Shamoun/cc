"""Share a conversation inside the organization (contract 1.11.0, CONTRACT.md §17).

A share is a read-only, **org-scoped** link: only signed-in users of the same customer can open it, and there are
no public links. Result data is withheld unless the owner opts in (`includeData`, default false). The owner can
revoke it at any time. A conversation has at most one active share, so sharing again returns it (with
`includeData` updated).

1.15.0 (share security review #241):
* **S1, rotate on widening.** Turning `includeData` on issues a NEW share id and revokes the old one, so a link that
  was handed out for a data-free view never starts showing rows. Narrowing (or no change) keeps the id.
* **S4, expiry.** Every share expires: `expiresInDays` is 7, 30 (default) or 90, counted from the PUT that set it.
  A PUT without `expiresInDays` keeps the current expiry (a new or rotated share gets 30 days). An expired share is
  gone: the viewer gets the same 404 as unknown/revoked, and the owner's GET says "not shared".
* **QA-S18, SQL withheld when data is off.** Shown SQL can carry literal values, so a data-free view drops SQL
  `code` parts and blanks SQL in steps and tool calls, and says so with `sqlWithheld: true`.

What a viewer gets (`SharedConversation`):
* the user questions, the answer prose, steps, callouts and sources (SQL only with `includeData: true`);
* with `includeData: false`: tables keep their columns and `rowCount`, but `rows` are empty and `dataWithheld` is
  true. Charts and table/chart/report/image artifacts (which carry rows) are left out, and SQL is withheld;
* with `includeData: true`: the stored preview rows (at most 50 per table), never the CSV (`csvFileId` is removed:
  downloads stay with the owner);
* never: feedback (the rating and comment are personal), the owner's user id, or file ids.

The demo keeps shares in memory. A real backend must persist them next to the transcript and must revoke them
when the conversation is deleted or the customer key is destroyed.
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable

DATA_ARTIFACT_KINDS = {"table", "chart", "report", "image"}
EXPIRY_DAYS = (7, 30, 90)
DEFAULT_EXPIRY_DAYS = 30
_DAY = 86400.0


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


@dataclass
class Share:
    share_id: str
    customer: str
    user: str
    conversation_id: str
    include_data: bool
    created_at: str
    expires_at: float = 0.0            # epoch seconds; 0 = never (pre-1.15 shares only)
    expires_in_days: int = DEFAULT_EXPIRY_DAYS
    rotated: bool = False              # this PUT replaced (and revoked) an earlier id: S1

    def expired(self, now: float) -> bool:
        return bool(self.expires_at) and now >= self.expires_at


class ShareRegistry:
    def __init__(self, clock: Callable[[], float] | None = None):
        self._by_id: dict[str, Share] = {}
        self._lock = threading.Lock()
        self.clock: Callable[[], float] = clock or time.time  # tests swap it to move past an expiry

    def _clock(self) -> float:
        return self.clock()

    def _purge(self, now: float) -> None:
        for k in [k for k, s in self._by_id.items() if s.expired(now)]:
            del self._by_id[k]

    def _new(self, customer: str, user: str, conversation_id: str, include_data: bool, now: float, days: int,
             rotated: bool = False) -> Share:
        # 144 random bits: the link is a capability inside the org, so it must not be guessable
        share = Share("sh_" + secrets.token_urlsafe(18), customer, user, conversation_id, include_data, _iso(now),
                      now + days * _DAY, days, rotated)
        self._by_id[share.share_id] = share
        return share

    def put(self, customer: str, user: str, conversation_id: str, include_data: bool,
            expires_in_days: int | None = None) -> Share:
        """Create or update. Widening (`includeData` false→true) rotates the id and revokes the old one (S1)."""
        if expires_in_days is not None and expires_in_days not in EXPIRY_DAYS:
            raise ValueError("expiresInDays must be one of 7, 30, 90")
        now = self._clock()
        with self._lock:
            self._purge(now)
            old = next((s for s in self._by_id.values()
                        if (s.customer, s.user, s.conversation_id) == (customer, user, conversation_id)), None)
            if old is None:
                return self._new(customer, user, conversation_id, include_data, now,
                                 expires_in_days or DEFAULT_EXPIRY_DAYS)
            if include_data and not old.include_data:  # S1: widening never reuses a link handed out data-free
                del self._by_id[old.share_id]
                return self._new(customer, user, conversation_id, True, now,
                                 expires_in_days or DEFAULT_EXPIRY_DAYS, rotated=True)
            old.include_data, old.rotated = include_data, False
            if expires_in_days is not None:
                old.expires_in_days, old.expires_at = expires_in_days, now + expires_in_days * _DAY
            return old

    def for_conversation(self, customer: str, user: str, conversation_id: str) -> Share | None:
        with self._lock:
            self._purge(self._clock())
            return next((s for s in self._by_id.values()
                         if (s.customer, s.user, s.conversation_id) == (customer, user, conversation_id)), None)

    def revoke(self, customer: str, user: str, conversation_id: str) -> bool:
        with self._lock:
            hit = [k for k, s in self._by_id.items() if (s.customer, s.user, s.conversation_id) == (customer, user, conversation_id)]
            for k in hit:
                del self._by_id[k]
            return bool(hit)

    def get(self, share_id: str, customer: str) -> Share | None:
        """None for an unknown, revoked, expired or other-organization share: the caller answers 404 for all."""
        with self._lock:
            self._purge(self._clock())
            s = self._by_id.get(share_id)
        return s if s is not None and secrets.compare_digest(s.customer, customer) else None


def link(share: Share) -> dict:
    out = {"shareId": share.share_id, "conversationId": share.conversation_id, "url": f"/shared/{share.share_id}",
           "scope": "organization", "includeData": share.include_data, "createdAt": share.created_at}
    if share.expires_at:
        out.update(expiresAt=_iso(share.expires_at), expiresInDays=share.expires_in_days)
    if share.rotated:
        out["rotated"] = True
    return out


SQL_WITHHELD = ""  # blanked SQL strings keep the field (and the step's shape) but carry nothing


def _withhold_sql_step(st: dict) -> dict:
    st = dict(st)
    if "sql" in st and isinstance(st["sql"], str):
        st["sql"] = SQL_WITHHELD
    if "revisedSql" in st and isinstance(st["revisedSql"], str):
        st["revisedSql"] = SQL_WITHHELD
    if isinstance(st.get("candidates"), list):
        st["candidates"] = [{**c, "sql": SQL_WITHHELD} if isinstance(c, dict) and "sql" in c else c
                            for c in st["candidates"]]
    if isinstance(st.get("input"), dict) and "sql" in st["input"]:
        st["input"] = {**st["input"], "sql": SQL_WITHHELD}
    st.pop("maskedLiterals", None)
    return st


def _part(p: dict, include_data: bool) -> dict | None:
    p = {k: v for k, v in p.items() if k != "csvFileId"}
    if include_data:
        return p
    t = p.get("type")
    if t == "table":
        return {**{k: v for k, v in p.items() if k != "sqlPartId"}, "rows": [], "dataWithheld": True}
    if t == "chart" or (t == "artifact" and p.get("kind") in DATA_ARTIFACT_KINDS):
        return None
    if t == "tool_call" and isinstance(p.get("input"), dict) and "sql" in p["input"]:
        return {**p, "input": {**p["input"], "sql": SQL_WITHHELD}}
    if t == "code" and str(p.get("language", "")).lower() == "sql":  # QA-S18: shown SQL can carry literal values
        return None
    if t == "artifact" and p.get("kind") == "code" and isinstance(p.get("content"), dict) \
            and str(p["content"].get("language", "")).lower() == "sql":
        return None
    return p


def shared_view(share: Share, conv: dict) -> dict:
    messages = []
    for m in conv.get("messages", []):
        m = {k: v for k, v in m.items() if k != "feedback"}
        if "parts" in m:
            m["parts"] = [q for q in (_part(p, share.include_data) for p in m["parts"]) if q is not None]
        if not share.include_data and isinstance(m.get("steps"), list):
            m["steps"] = [_withhold_sql_step(st) if isinstance(st, dict) else st for st in m["steps"]]
        messages.append(m)
    out = {"shareId": share.share_id, "title": conv.get("title"), "updatedAt": conv.get("updatedAt"),
           "includeData": share.include_data, "createdAt": share.created_at, "messages": messages}
    if share.expires_at:
        out["expiresAt"] = _iso(share.expires_at)
    if not share.include_data:
        out["sqlWithheld"] = True
    return out
