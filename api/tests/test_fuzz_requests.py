"""Request-parser fuzz (contract 1.12.0): malformed bodies, params and headers never produce a 500.

Every response is 200/204 or a 4xx with the standard error envelope (CONTRACT.md §1). Seeded, so failures
are reproducible. The data is synthetic only. Stream bodies, when a fuzzed chat happens to be valid, must still
pass the conformance checker.
"""
import json
import random

import pytest
from fastapi.testclient import TestClient

from api.contract.conformance import check
from api.tests.conftest import LABS, ask, events

JUNK = [None, True, False, 0, -1, 2**63, 1.5, float("1e308"), "", "x" * 9000, "../../etc", "<script>", "\u0000",
        "²", "🙂", [], [None], {}, {"a": {"b": {"c": []}}}, "m_" + "a" * 300]


def junk(r: random.Random, depth=0):
    roll = r.random()
    if depth < 3 and roll < 0.15:
        return [junk(r, depth + 1) for _ in range(r.randint(0, 4))]
    if depth < 3 and roll < 0.3:
        return {r.choice(["id", "role", "text", "type", "messageId", r.choice(JUNK) if False else "k"]): junk(r, depth + 1)
                for _ in range(r.randint(0, 4))}
    return r.choice(JUNK)


def mutate(r: random.Random, body):
    """Replace, drop or junk a random node of a valid body."""
    if not isinstance(body, (dict, list)) or not body or r.random() < 0.2:
        return junk(r)
    body = json.loads(json.dumps(body))
    if isinstance(body, dict):
        k = r.choice(list(body))
        if r.random() < 0.3:
            del body[k]
        else:
            body[k] = mutate(r, body[k])
    else:
        i = r.randrange(len(body))
        body[i] = mutate(r, body[i])
    return body


VALID = {
    ("post", "/api/chat"): {"messages": [{"id": "u1", "role": "user", "text": "Denials by payer last quarter"}],
                            "options": {}, "regenerate": None, "resume": None},
    ("put", "/api/conversations/{c}/messages/{m}/feedback"): {"rating": "down", "reasons": ["wrong_numbers"], "comment": "x"},
    ("patch", "/api/conversations/{c}"): {"title": "Renamed"},
    ("post", "/api/conversations/search"): {"q": "denials", "cursor": None},
    ("put", "/api/transcripts/retention"): {"days": 30},
}


def ok(resp):
    assert resp.status_code < 500, (resp.status_code, resp.text[:300])
    if resp.status_code >= 400:
        body = resp.json()
        # 1.13.0: no exceptions left. Stop/delete keep their boolean next to the envelope.
        extra = set(body) - {"error"}
        assert extra <= {"deleted", "stopped"} and all(body[k] is False for k in extra), body
        assert "error" in body and isinstance(body["error"].get("code"), str) \
            and isinstance(body["error"].get("message"), str), body


@pytest.fixture
def fz(app):
    client = TestClient(app, raise_server_exceptions=False)
    _, evs = ask(client, "Denials by payer last quarter")
    start = evs[0]
    return client, start["conversationId"], start["messageId"]


@pytest.mark.parametrize("route", list(VALID), ids=lambda r: r[1])
def test_bodies_never_500(fz, route):
    client, cid, mid = fz
    method, path = route
    url = path.format(c=cid, m=mid)
    r = random.Random(hash(path) & 0xFFFF)
    for i in range(60 if path == "/api/chat" else 120):
        body = mutate(r, VALID[route])
        resp = client.request(method, url, json=body, headers=LABS)
        ok(resp)
        if path == "/api/chat" and resp.status_code == 200:
            assert check(events(resp)) == [], (i, body)


@pytest.mark.parametrize("raw", [b"", b"{", b"null", b"[]", b"\xff\xfe", b'{"messages": "x"}', b"1e999999",
                                 b'{"messages": [' * 200, b'{"a":' * 1000 + b"1" + b"}" * 1000])
def test_raw_bytes_never_500(fz, raw):
    client, cid, mid = fz
    for method, path in VALID:
        ok(client.request(method, path.format(c=cid, m=mid), content=raw,
                          headers={**LABS, "content-type": "application/json"}))


def test_path_params_never_500(fz):
    client, cid, mid = fz
    r = random.Random(7)
    ids = ["", "x", "c_" + "z" * 400, "%00", "..%2F..", "²", "c_🙂", cid, mid]
    paths = ["/api/conversations/{c}", "/api/conversations/{c}/export", "/api/conversations/{c}/share",
             "/api/shared/{c}", "/api/chat/{c}/stream", "/api/download/{c}",
             "/api/conversations/{c}/messages/{m}/feedback"]
    for _ in range(150):
        url = r.choice(paths).format(c=r.choice(ids), m=r.choice(ids))
        for method in ("get", "delete"):
            ok(client.request(method, url, headers=LABS))


@pytest.mark.parametrize("last", ["", ":", "x:", ":5", "m_x:", "m_x:²", "m_x:-1", "m_x:99999999999999999999",
                                  "m_x:1:2", "\u00b2:\u00b2"])
def test_last_event_id_never_500(fz, last):
    client, _, mid = fz
    for value in (last, f"{mid}:{last.rpartition(':')[2]}"):
        header = {**LABS, "last-event-id": value.encode("latin-1", "replace")}
        ok(client.post("/api/chat", json={"messages": []}, headers=header))
        ok(client.get(f"/api/chat/{mid}/stream", headers=header))


def test_query_params_never_500(fz):
    client, *_ = fz
    for q in ["?limit=-1", "?limit=abc", "?limit=99999999999", "?cursor=" + "z" * 500, "?cursor=%00", "?format=nope"]:
        for path in ("/api/conversations", "/api/transcripts"):
            ok(client.get(path + q, headers=LABS))


def test_unknown_routes_and_methods_use_the_envelope(fz):
    client, *_ = fz
    for method, path, status in [("get", "/api/nope", 404), ("post", "/api/health", 405), ("put", "/api/chat", 405)]:
        resp = client.request(method, path, headers=LABS)
        assert resp.status_code == status
        assert resp.json()["error"]["code"] in ("not_found", "method_not_allowed")
        assert resp.headers["cache-control"] == "no-store"
