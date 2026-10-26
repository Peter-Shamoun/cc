"""Stop interrupts the running SQL (api/cancel.py, API side only). Before: a Stop during a long query took effect only
at the next model call, i.e. after the query finished or hit the analyzer's 25 s limit."""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import duckdb
import pytest
from fastapi.testclient import TestClient

from api.app import Settings, create_app
from api.cancel import CancellableOpener
from api.contract.conformance import check
from api.live import Cancelled
from api.tests.conftest import LABS

SLOW = ("SELECT COUNT(*) AS n FROM sem.claims a, sem.claims b, sem.claims c "
        "WHERE a.claim_key <> b.claim_key AND b.claim_key <> c.claim_key")  # >4 s on the demo data
FAST = "SELECT COUNT(*) AS claims FROM sem.claims"
BIG = "SELECT COUNT(*) FROM range(4000000000) t"


class _Opener:
    def __init__(self, con):
        self.con, self.opened = con, 0

    def open(self, escalate):
        self.opened += 1
        return SimpleNamespace(con=self.con.cursor())


def _run(cur, out, key):
    t = time.perf_counter()
    try:
        cur.execute(BIG).fetchall()
        out[key] = ("ok", time.perf_counter() - t)
    except Exception as exc:  # noqa: BLE001
        out[key] = (type(exc).__name__, time.perf_counter() - t)


def test_cancel_interrupts_only_this_runs_cursor():
    con = duckdb.connect()
    cancelled, out = threading.Event(), {}
    other = con.cursor()  # another user's query on the same shared connection
    with CancellableOpener(_Opener(con), cancelled) as opener:
        mine = opener.open(False).con
        a = threading.Thread(target=_run, args=(mine, out, "mine"))
        b = threading.Thread(target=_run, args=(other, out, "other"))
        a.start(), b.start()
        time.sleep(0.2)
        cancelled.set()
        a.join(5)
        assert out["mine"][0] == "InterruptException" and out["mine"][1] < 1.5
        assert b.is_alive() and "other" not in out
    other.interrupt()
    b.join(5)
    assert opener.interrupts >= 1


def test_a_query_started_after_the_first_interrupt_is_stopped_too():
    con = duckdb.connect()
    cancelled, out = threading.Event(), {}
    with CancellableOpener(_Opener(con), cancelled, poll=0.02) as opener:
        cur = opener.open(False).con
        cancelled.set()
        time.sleep(0.1)  # the cursor was idle when interrupted; DuckDB ignores that
        _run(cur, out, "late")
    assert out["late"][0] == "InterruptException" and out["late"][1] < 1


def test_open_after_cancel_raises_and_opens_nothing():
    inner = _Opener(duckdb.connect())
    cancelled = threading.Event()
    cancelled.set()
    with CancellableOpener(inner, cancelled) as opener, pytest.raises(Cancelled):
        opener.open(False)
    assert inner.opened == 0


def test_no_cancel_means_no_interrupt_and_the_watcher_exits():
    cancelled = threading.Event()
    opener = CancellableOpener(_Opener(duckdb.connect()), cancelled).start()
    cur = opener.open(False).con
    assert cur.execute(FAST.replace("sem.claims", "range(10)")).fetchall() == [(10,)]
    opener.stop()
    assert opener.interrupts == 0 and not opener._thread.is_alive()
    cancelled.set()  # after the run: nothing left to interrupt
    time.sleep(0.1)
    assert opener.interrupts == 0


def test_backends_without_interrupt_and_failing_interrupts_are_tolerated():
    class NoInterrupt:
        pass

    class Broken:
        def interrupt(self):
            raise RuntimeError("closed")

    class Cancel:
        calls = 0

        def cancel(self):
            Cancel.calls += 1

    cons = iter([NoInterrupt(), Broken(), Cancel()])
    inner = SimpleNamespace(open=lambda escalate: SimpleNamespace(con=next(cons)))
    opener = CancellableOpener(inner, threading.Event())
    for _ in range(3):
        opener.open(False)
    assert opener.interrupt_all() == 1 and Cancel.calls == 1


def test_handles_without_a_con_are_passed_through():
    inner = SimpleNamespace(open=lambda escalate: "handle")
    opener = CancellableOpener(inner, threading.Event())
    assert opener.open(False) == "handle" and opener.interrupt_all() == 0


def _slow_analyzer():
    from api.tests.conftest import cc, scripted_analyzer
    return scripted_analyzer(cc("sql", SLOW), cc("answer", "--sql", FAST, "--text", "Claims."))


def test_stop_during_a_long_query_aborts_within_a_second():
    app = create_app(settings=Settings(heartbeat_seconds=5), analyzer=_slow_analyzer())
    client = TestClient(app)
    evs, stopped_at = [], None
    body = {"messages": [{"id": "u_1", "role": "user", "text": "How many claims?"}]}
    with client.stream("POST", "/api/chat", json=body, headers=LABS) as r:
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[5:])
            evs.append(ev)
            if ev["type"] == "step.sql_running" and stopped_at is None:
                time.sleep(0.3)  # the query is on the DuckDB thread now
                stopped_at = time.perf_counter()
                assert client.post(f"/api/chat/{evs[0]['messageId']}/stop", headers=LABS).json()["stopped"] is True
            if ev["type"] == "message.end":
                break
    took = time.perf_counter() - stopped_at
    assert evs[-1]["finishReason"] == "aborted" and took < 2.0, took
    assert check(evs) == []
    (rec,) = [e for e in app.state.audit.events if e["event"] == "chat_message"]
    assert rec["finish"] == "aborted" and rec["interrupts"] >= 1
    assert SLOW not in str(app.state.audit.events)


def test_a_normal_answer_is_untouched():
    from api.tests.conftest import cc, scripted_analyzer
    app = create_app(settings=Settings(heartbeat_seconds=5), analyzer=scripted_analyzer(
        cc("sql", FAST), cc("answer", "--sql", FAST, "--text", "Claims.")))
    r = TestClient(app).post("/api/chat", json={"messages": [{"role": "user", "text": "How many claims?"}]},
                             headers=LABS)
    evs = [json.loads(x[5:]) for x in r.text.splitlines() if x.startswith("data:")]
    assert evs[-1]["finishReason"] == "stop" and check(evs) == []
    (rec,) = [e for e in app.state.audit.events if e["event"] == "chat_message"]
    assert "interrupts" not in rec
