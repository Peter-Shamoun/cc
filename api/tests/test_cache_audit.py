"""Cache-control audit of every API route (platform; docs/frontend_security.md "Caching and offline").

Every response from api/ can carry PHI (answers, tables, CSVs, titles, shared views) or reveal account state, so
nothing may be stored by the browser, a proxy or a CDN: ``Cache-Control: no-store`` (+ ``Pragma: no-cache``) on
every route, success and error alike, plus the full SECURITY_HEADERS set, and no validators (ETag/Last-Modified)
that would invite conditional revalidation. The route table below must match ``app.routes`` exactly, so a new
endpoint fails this test until it is added here.
"""
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api.app import create_app
from api.security import SECURITY_HEADERS
from api.app import Settings, create_app
from api.tests.conftest import LABS, events

COLLEAGUE = {"Authorization": "Bearer demo:acme_labs:bo@xifin.example"}
MISSING = "c_missing00000000"


def _seed(c):
    """One synthetic answer with a table (CSV file part) and a share link."""
    r = c.post("/api/chat", headers=LABS, json={"messages": [{"id": "u1", "role": "user", "text": "list all claims"}]})
    evs = events(r)
    cid, mid = evs[0]["conversationId"], evs[0]["messageId"]
    file_id = next(e["part"]["fileId"] for e in evs if e["type"] == "part" and e["part"]["type"] == "file")
    share = c.put(f"/api/conversations/{cid}/share", headers=LABS, json={}).json()["shareId"]
    return {"cid": cid, "mid": mid, "file": file_id, "share": share}


def _chat(c, s):
    return [c.post("/api/chat", headers=LABS, json={"messages": [{"id": "u1", "role": "user", "text": "How many claims?"}]}),
            c.post("/api/chat", headers=LABS, json={"messages": "nope"}),
            c.post("/api/chat", json={"messages": []})]


ROUTES = {
    ("GET", "/api/health"): lambda c, s: [c.get("/api/health")],
    ("GET", "/api/capabilities"): lambda c, s: [c.get("/api/capabilities", headers=LABS), c.get("/api/capabilities")],
    ("GET", "/api/contract"): lambda c, s: [c.get("/api/contract")],
    ("POST", "/api/auth/sign-out"): lambda c, s: [c.post("/api/auth/sign-out", headers=COLLEAGUE),
                                                 c.post("/api/auth/sign-out", headers={"cookie": "cc_session=demo:acme_labs:x"})],  # 403 CSRF
    ("POST", "/api/chat"): _chat,
    ("GET", "/api/chat/{message_id}/stream"): lambda c, s: [c.get(f"/api/chat/{s['mid']}/stream", headers=LABS),
                                                           c.get("/api/chat/m_missing/stream", headers=LABS)],
    ("POST", "/api/chat/{message_id}/stop"): lambda c, s: [c.post(f"/api/chat/{s['mid']}/stop", headers=LABS),
                                                          c.post("/api/chat/m_missing/stop", headers=LABS)],
    ("GET", "/api/download/{download_id}"): lambda c, s: [c.get(f"/api/download/{s['file']}", headers=LABS),
                                                         c.get("/api/download/d_missing", headers=LABS),
                                                         c.get(f"/api/download/{s['file']}")],
    ("GET", "/api/transcripts"): lambda c, s: [c.get("/api/transcripts", headers=LABS), c.get("/api/transcripts")],
    ("PUT", "/api/transcripts/retention"): lambda c, s: [c.put("/api/transcripts/retention", headers=LABS, json={"days": 30}),
                                                        c.put("/api/transcripts/retention", headers=LABS, json={"days": -1})],
    ("GET", "/api/conversations"): lambda c, s: [c.get("/api/conversations", headers=LABS), c.get("/api/conversations")],
    ("POST", "/api/conversations/search"): lambda c, s: [c.post("/api/conversations/search", headers=LABS, json={"q": "claims"}),
                                                        c.post("/api/conversations/search", headers=LABS, json={"q": "x" * 999})],
    ("DELETE", "/api/conversations"): lambda c, s: [c.delete("/api/conversations", headers=COLLEAGUE), c.delete("/api/conversations")],
    ("GET", "/api/conversations/{conversation_id}"): lambda c, s: [c.get(f"/api/conversations/{s['cid']}", headers=LABS),
                                                                  c.get(f"/api/conversations/{MISSING}", headers=LABS)],
    ("PATCH", "/api/conversations/{conversation_id}"): lambda c, s: [c.patch(f"/api/conversations/{s['cid']}", headers=LABS, json={"title": "Synthetic"}),
                                                                    c.patch(f"/api/conversations/{MISSING}", headers=LABS, json={"title": "x"})],
    ("GET", "/api/conversations/{conversation_id}/export"): lambda c, s: [c.get(f"/api/conversations/{s['cid']}/export", headers=LABS),
                                                                         c.get(f"/api/conversations/{MISSING}/export", headers=LABS)],
    ("PUT", "/api/conversations/{conversation_id}/messages/{message_id}/feedback"): lambda c, s: [
        c.put(f"/api/conversations/{s['cid']}/messages/{s['mid']}/feedback", headers=LABS, json={"rating": "up"}),
        c.put(f"/api/conversations/{s['cid']}/messages/{s['mid']}/feedback", headers=LABS, json={"rating": "meh"})],
    ("DELETE", "/api/conversations/{conversation_id}/messages/{message_id}/feedback"): lambda c, s: [
        c.delete(f"/api/conversations/{s['cid']}/messages/{s['mid']}/feedback", headers=LABS),
        c.delete(f"/api/conversations/{MISSING}/messages/m_x/feedback", headers=LABS)],
    ("GET", "/api/conversations/{conversation_id}/share"): lambda c, s: [c.get(f"/api/conversations/{s['cid']}/share", headers=LABS),
                                                                        c.get(f"/api/conversations/{MISSING}/share", headers=LABS)],
    ("PUT", "/api/conversations/{conversation_id}/share"): lambda c, s: [c.put(f"/api/conversations/{s['cid']}/share", headers=LABS, json={"includeData": False}),
                                                                        c.put(f"/api/conversations/{MISSING}/share", headers=LABS, json={})],
    ("GET", "/api/shared/{share_id}"): lambda c, s: [c.get(f"/api/shared/{s['share']}", headers=COLLEAGUE),
                                                    c.get(f"/api/shared/{s['share']}"), c.get("/api/shared/s_missing", headers=COLLEAGUE)],
    # Destructive routes last: each removes what it is given.
    ("DELETE", "/api/conversations/{conversation_id}/share"): lambda c, s: [c.delete(f"/api/conversations/{s['cid']}/share", headers=LABS),
                                                                           c.delete(f"/api/conversations/{s['cid']}/share", headers=LABS)],
    ("DELETE", "/api/conversations/{conversation_id}"): lambda c, s: [c.delete(f"/api/conversations/{s['cid']}", headers=LABS),
                                                                     c.delete(f"/api/conversations/{MISSING}", headers=LABS)],
}


#: Routes that answer signed-out callers with no user data (contract 1.5.0 for capabilities); still no-store.
PUBLIC = {("GET", "/api/health"), ("GET", "/api/contract"), ("GET", "/api/capabilities")}


def _app_routes(app):
    return {(m, r.path) for r in app.routes if isinstance(r, APIRoute) for m in r.methods if m != "HEAD"}


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(settings=Settings(heartbeat_seconds=5)))


def test_route_table_covers_every_api_route(client):
    assert _app_routes(client.app) == set(ROUTES), "add the new route to ROUTES in test_cache_audit.py"


def assert_not_cacheable(r, where):
    for name, value in SECURITY_HEADERS.items():
        assert r.headers.get(name) == value, (where, r.status_code, name, r.headers.get(name))
    cc = r.headers.get("cache-control", "")
    assert cc == "no-store", (where, cc)
    for validator in ("etag", "last-modified", "expires", "age"):
        assert validator not in r.headers, (where, validator)
    assert "public" not in cc and "max-age" not in cc and "immutable" not in cc


@pytest.mark.parametrize("route", list(ROUTES), ids=lambda r: f"{r[0]} {r[1]}")
def test_every_route_is_no_store_on_success_and_error(client, route):
    responses = ROUTES[route](client, _seed(client))
    statuses = {r.status_code for r in responses}
    assert any(s < 400 for s in statuses) or route[1] == "/api/chat/{message_id}/stop", (route, statuses)
    assert any(s >= 400 for s in statuses) or route in PUBLIC, (route, statuses)
    for r in responses:
        assert_not_cacheable(r, f"{route[0]} {route[1]}")


@pytest.mark.parametrize("method,path,status", [
    ("get", "/api/nope", 404), ("put", "/api/health", 405), ("options", "/api/chat", 405), ("get", "/", 404),
    ("head", "/api/health", None),
])
def test_framework_level_responses_are_no_store(client, method, path, status):
    r = client.request(method.upper(), path, headers=LABS)
    if status is not None:
        assert r.status_code == status
    assert_not_cacheable(r, f"{method} {path}")


def test_conditional_requests_never_revalidate(client):
    """No validators are issued, and a forged If-None-Match / If-Modified-Since cannot produce a 304."""
    s = _seed(client)
    for path in ("/api/conversations", f"/api/conversations/{s['cid']}", f"/api/download/{s['file']}"):
        r = client.get(path, headers={**LABS, "If-None-Match": "*", "If-Modified-Since": "Thu, 01 Jan 2099 00:00:00 GMT"})
        assert r.status_code == 200, path
        assert_not_cacheable(r, path)
