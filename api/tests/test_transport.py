"""Transport robustness (CONTRACT.md §8, 1.1.0): resume by Last-Event-ID over GET or POST, disconnect grace,
cancel closes every in-flight step, heartbeats on resumed streams."""
import json
import re
import threading
import time

from fastapi.testclient import TestClient

from api.app import Settings, create_app
from api.contract import validate_event
from api.live import LiveTap, _Pending
from api.tests.conftest import DX, LABS, events
from api.tests.test_contract import check_invariants
from api.tests.timing import budget


def _ids(text):
    return re.findall(r"^id: (\S+)$", text, re.M)


def test_get_stream_replays_after_last_event_id():
    client = TestClient(create_app(settings=Settings(heartbeat_seconds=5)))
    first = client.post("/api/chat", headers=LABS, json={"messages": [{"role": "user", "text": "How many claims?"}]})
    ids = _ids(first.text)
    mid = ids[0].split(":")[0]
    again = client.get(f"/api/chat/{mid}/stream", headers={**LABS, "Last-Event-ID": ids[4]})
    assert again.status_code == 200 and again.headers["cache-control"] == "no-store"
    assert _ids(again.text) == ids[5:]
    for ev in events(again):
        validate_event(ev)
    # no header -> full replay; it is the same stream byte for byte (minus heartbeats)
    full = client.get(f"/api/chat/{mid}/stream", headers=LABS)
    assert _ids(full.text) == ids
    check_invariants(events(full))


def test_get_stream_is_owner_scoped_and_404s_cleanly():
    client = TestClient(create_app(settings=Settings(heartbeat_seconds=5)))
    first = client.post("/api/chat", headers=LABS, json={"messages": [{"role": "user", "text": "How many claims?"}]})
    mid = _ids(first.text)[0].split(":")[0]
    other = client.get(f"/api/chat/{mid}/stream", headers=DX)
    assert other.status_code == 404 and other.json()["error"]["code"] == "stream_gone"
    gone = client.get("/api/chat/m_nope/stream", headers=LABS)
    assert gone.status_code == 404 and gone.json()["error"]["code"] == "stream_gone"
    assert client.get(f"/api/chat/{mid}/stream").status_code == 401
    post = client.post("/api/chat", json={"messages": []}, headers={**LABS, "Last-Event-ID": "m_nope:3"})
    assert post.status_code == 404 and post.json()["error"]["code"] == "stream_gone"


def _start_and_drop(client, scenario="slow"):
    """Open a stream, read until the first step, then drop the connection. Returns (messageId, last id)."""
    mid = last = None
    with client.stream("POST", "/api/chat", headers=LABS,
                       json={"messages": [{"role": "user", "text": "How many claims?"}], "scenario": scenario}) as r:
        for line in r.iter_lines():
            if line.startswith("id: "):
                last = line[4:]
                mid = last.split(":")[0]
            if line.startswith("data: ") and json.loads(line[6:])["type"].startswith("step."):
                break
    return mid, last


def _wait_done(app, mid, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        run = app.state.runs.get(mid)
        if run is not None and run.done:
            return run
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_disconnect_within_grace_keeps_the_run_alive_and_resume_gets_the_rest():
    app = create_app(settings=Settings(heartbeat_seconds=5, model_delay_seconds=0.1, disconnect_grace_seconds=20))
    client = TestClient(app)
    mid, last = _start_and_drop(client)
    assert mid and last
    rest = client.get(f"/api/chat/{mid}/stream", headers={**LABS, "Last-Event-ID": last})
    tail = events(rest)
    assert tail[0]["seq"] == int(last.rsplit(":", 1)[1]) + 1
    assert tail[-1]["type"] == "message.end" and tail[-1]["finishReason"] == "stop"
    run = _wait_done(app, mid)
    check_invariants([p for _, p in run.events])


def test_disconnect_without_grace_cancels_and_closes_open_steps():
    app = create_app(settings=Settings(heartbeat_seconds=5, model_delay_seconds=0.1, disconnect_grace_seconds=0))
    client = TestClient(app)
    mid, _ = _start_and_drop(client)
    run = _wait_done(app, mid)
    evs = [p for _, p in run.events]
    check_invariants(evs)
    assert evs[-1]["finishReason"] == "aborted"


def test_grace_expiry_cancels_orphaned_run():
    app = create_app(settings=Settings(heartbeat_seconds=5, model_delay_seconds=0.15, disconnect_grace_seconds=0.2))
    client = TestClient(app)
    mid, _ = _start_and_drop(client)
    run = _wait_done(app, mid)
    assert run.events[-1][1]["finishReason"] == "aborted"


def test_abort_closes_inflight_sql_and_reasoning():
    emitted = []
    tap = LiveTap(object(), emitted.append)
    tap.reasoning_started = time.perf_counter()
    tap.pending["t1"] = _Pending("s_run", "run_sql", {"sql": "SELECT 1"}, time.perf_counter(), 0)
    tap.pending["t2"] = _Pending("s_fin", "final_answer", {"sql": "SELECT 2"}, time.perf_counter(), 0)
    tap.pending["t3"] = _Pending("s_exp", "describe_view", {"relation": "sem.claims"}, time.perf_counter(), 0)
    tap.abort()
    closed = {e["stepId"]: e for e in emitted if e["type"] == "step.sql_result"}
    assert set(closed) == {"s_run", "s_fin"}
    assert all(e["ok"] is False and e["error"]["code"] == "cancelled" for e in closed.values())
    assert [e["type"] for e in emitted][-1] == "reasoning.end"
    assert not tap.pending
    for i, e in enumerate(emitted):
        validate_event({**e, "seq": i})


def test_stop_is_idempotent_and_owner_scoped():
    app = create_app(settings=Settings(heartbeat_seconds=5, model_delay_seconds=0.3))
    client = TestClient(app)
    got = []
    with client.stream("POST", "/api/chat", headers=LABS,
                       json={"messages": [{"role": "user", "text": "How many claims?"}], "scenario": "slow"}) as r:
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            got.append(ev)
            if ev["type"] == "message.start":
                mid = ev["messageId"]
                assert client.post(f"/api/chat/{mid}/stop", headers=DX).status_code == 404
                t0 = time.perf_counter()
                threading.Timer(0.05, lambda: [client.post(f"/api/chat/{mid}/stop", headers=LABS) for _ in range(2)]).start()
    assert got[-1]["finishReason"] == "aborted"
    assert time.perf_counter() - t0 < budget(3.0)
    check_invariants(got)


def test_resumed_stream_heartbeats():
    app = create_app(settings=Settings(heartbeat_seconds=0.1, model_delay_seconds=0.15, disconnect_grace_seconds=20))
    client = TestClient(app)
    mid, last = _start_and_drop(client)
    rest = client.get(f"/api/chat/{mid}/stream", headers={**LABS, "Last-Event-ID": last})
    beats = [e for e in events(rest) if e["type"] == "heartbeat"]
    assert beats and all("at" in b for b in beats)
