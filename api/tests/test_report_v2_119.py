"""1.19.0 (QA-C1): a follow-up report revises the earlier artifacts in place (same artifactId, version + 1) on the
same branch, like Claude's artifacts and ChatGPT's canvas. Edits and regenerates only see their own branch."""
from __future__ import annotations

from api.contract.conformance import check
from api.mapping import prior_artifacts, report_artifacts
from api.tests.conftest import LABS, events


def _arts(evs):
    return {p["kind"]: p for e in evs if e["type"] == "part" and (p := e["part"])["type"] == "artifact"}


def _chat(client, text, cid=None, parent=None, mid=None, **extra):
    msg = {"id": mid or f"u_{abs(hash(text)) % 10**8}", "role": "user", "text": text}
    if parent:
        msg["parentId"] = parent
    body = {"messages": [msg], **extra}
    if cid:
        body["conversationId"] = cid
    r = client.post("/api/chat", json=body, headers=LABS)
    assert r.status_code == 200, r.text
    evs = events(r)
    assert check(evs) == []
    start = evs[0]
    return start["conversationId"], start["messageId"], evs


def test_follow_up_report_is_v2_of_the_same_artifacts(client):
    cid, m1, evs1 = _chat(client, "Build a report on the paid amount trend by month", mid="u1")
    a1 = _arts(evs1)
    assert a1["report"]["version"] == 1 and a1["chart"]["version"] == 1
    _, m2, evs2 = _chat(client, "Update the report to show the monthly trend for 2026", cid, parent=m1, mid="u2")
    a2 = _arts(evs2)
    for kind in ("report", "chart"):
        assert a2[kind]["artifactId"] == a1[kind]["artifactId"] and a2[kind]["version"] == 2
        assert a2[kind]["id"] != a1[kind]["id"]  # a new part, a new version (CONTRACT §16)
    _, _, evs3 = _chat(client, "Revise the report again", cid, parent=m2, mid="u3")
    assert _arts(evs3)["report"]["version"] == 3


def test_qa_c1_repro_denial_drivers(client):
    cid, m1, evs1 = _chat(client, "Build a report on our denial drivers", mid="u1")
    _, _, evs2 = _chat(client, "Update the report to focus on CO-45", cid, parent=m1, mid="u2")
    r1, r2 = _arts(evs1)["report"], _arts(evs2)["report"]
    assert (r2["artifactId"], r2["version"]) == (r1["artifactId"], 2)


def test_a_report_in_a_new_conversation_is_v1(client):
    _chat(client, "Build a report on our denial drivers", mid="u1")
    _, _, evs = _chat(client, "Build a report on payers", mid="u9")
    assert _arts(evs)["report"]["version"] == 1


def test_regenerate_does_not_bump_its_own_version(client):
    cid, m1, evs1 = _chat(client, "Build a report on our denial drivers", mid="u1")
    _, _, evs = _chat(client, "", cid, mid="u1x", regenerate={"messageId": m1})
    a = _arts(evs)
    assert a["report"]["version"] == 1 and a["report"]["artifactId"] != _arts(evs1)["report"]["artifactId"]


def test_prior_artifacts_follows_the_branch():
    def art(kind, aid, v):
        return {"type": "artifact", "id": f"p_{aid}{v}", "artifactId": aid, "version": v, "kind": kind,
                "title": "t", "content": {"markdown": "x"}}
    msgs = [{"id": "u1", "role": "user"},
            {"id": "a1", "role": "assistant", "parentId": "u1", "parts": [art("report", "af_r", 1)]},
            {"id": "u2", "role": "user", "parentId": "a1"},
            {"id": "a2", "role": "assistant", "parentId": "u2", "parts": [art("report", "af_r", 2)]},
            {"id": "u2b", "role": "user", "parentId": "a1", "editOf": "u2"},  # an edit: sibling of u2
            {"id": "a3", "role": "assistant", "parts": [art("report", "af_x", 7), {"type": "artifact", "kind": "report",
                                                                                   "artifactId": 5, "version": "2"}]}]
    assert prior_artifacts(msgs, "a2") == {"report": {"artifactId": "af_r", "version": 2}}
    assert prior_artifacts(msgs, "a1") == {"report": {"artifactId": "af_r", "version": 1}}  # the edit's branch
    assert prior_artifacts(msgs, None, before_id="u2b") == {"report": {"artifactId": "af_r", "version": 2}}
    assert prior_artifacts(msgs, None) == {"report": {"artifactId": "af_x", "version": 7}}  # malformed one skipped
    assert prior_artifacts(msgs, "missing") == {}
    cyclic = [{"id": "a", "role": "assistant", "parentId": "b", "parts": []}, {"id": "b", "role": "user", "parentId": "a"}]
    assert prior_artifacts(cyclic, "a") == {}


def test_report_artifacts_uses_prior_ids():
    parts = [{"type": "table", "id": "t", "columns": [{"key": "n", "label": "N", "type": "integer"}], "rows": [[1]],
              "rowCount": 1}]
    out = report_artifacts(parts, {"report": {"artifactId": "af_r", "version": 4}})
    assert out[-1]["artifactId"] == "af_r" and out[-1]["version"] == 5
