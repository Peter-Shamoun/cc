"""Every event every demo scenario emits validates against api/contract/v1.schema.json, plus the ordering
guarantees of CONTRACT.md §2 and the scenario semantics of §4-§8."""
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from api.app import Settings, create_app
from api.contract import validate_event
from api.tests.conftest import LABS, events, parts

SCENARIOS = {
    "table": "Paid amount by payer",
    "chart": "Paid amount trend by month",
    "repair": "Top denial reason codes",
    "abstain": "How many appeals did we win?",
    "clarify": "How bad are denials?",
    "escalation": "What's our net collection rate by payer?",
    "candidates": "Average paid per claim by payer",
    "error": "simulate an error please",
    "slow": "How many claims? (slow)",
}
FINISH = {"table": "stop", "chart": "stop", "repair": "stop", "abstain": "abstain", "clarify": "needs_input",
          "escalation": "stop", "candidates": "stop", "error": "error", "slow": "stop"}


def run(client, text=None, **body):
    payload = {"messages": [{"id": "u1", "role": "user", "text": text}] if text else [], **body}
    response = client.post("/api/chat", json=payload, headers=LABS)
    assert response.status_code == 200, response.text
    return events(response)


def check_invariants(evs):
    for ev in evs:
        validate_event(ev)
    assert evs[0]["type"] == "message.start" and evs[-1]["type"] == "message.end"
    assert sum(e["type"] == "message.start" for e in evs) == 1 and sum(e["type"] == "message.end" for e in evs) == 1
    seqs = [e["seq"] for e in evs if e["type"] != "heartbeat"]
    assert seqs == list(range(len(seqs))), "seq gapless from 0"
    seen, results = {}, {}
    for e in evs:
        if e["type"] == "step.sql_running":
            assert e["stepId"] not in seen
            seen[e["stepId"]] = e
        elif e["type"] == "step.sql_result":
            assert e["stepId"] in seen, "result after running"
            results[e["stepId"]] = e
        elif e["type"] == "step.repair_attempt":
            assert results[e["failedStepId"]]["ok"] is False
        elif e["type"] == "step.check" and "targetStepId" in e:
            assert e["targetStepId"] in seen
    assert set(seen) == set(results), "every SQL step finishes"
    first_answer = next((i for i, e in enumerate(evs) if e["type"] in ("text.start", "part")), len(evs))
    assert not any(e["type"] == "escalation" for e in evs[first_answer:])
    assert not any(e["type"].startswith("step.") and e["type"] != "step.check" for e in evs[first_answer:])
    assert evs[0]["contractVersion"] == "1.0.0"


@pytest.fixture
def client():
    return TestClient(create_app(settings=Settings(heartbeat_seconds=5)))


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_scenario_by_keyword_is_on_contract(client, scenario):
    evs = run(client, SCENARIOS[scenario])
    check_invariants(evs)
    assert evs[-1]["finishReason"] == FINISH[scenario]


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_scenario_forced_is_on_contract(client, scenario):
    evs = run(client, "How many claims?", scenario=scenario)
    check_invariants(evs)
    if scenario not in ("table", "chart", "slow"):
        assert evs[-1]["finishReason"] == FINISH[scenario]


def test_table_answer_has_text_sql_table_and_csv(client):
    evs = run(client, SCENARIOS["table"])
    ps = parts(evs)
    code = next(p for p in ps if p["type"] == "code")
    table = next(p for p in ps if p["type"] == "table")
    assert code["role"] == "query" and table["sqlPartId"] == code["id"] and table["csvFileId"]
    assert any(e["type"] == "text.delta" for e in evs)
    assert client.get(f"/api/download/{table['csvFileId']}", headers=LABS).status_code == 200
    usage = evs[-1]["usage"]
    assert usage["costIsEstimate"] is True and usage["costUsd"] > 0 and usage["tokens"]["input"] > 0
    assert usage["latency"]["totalMs"] >= usage["latency"].get("firstStepMs", 0)
    assert any(e["type"] == "step.check" and e["check"] == "verifier" for e in evs)


def test_chart_answer(client):
    ps = parts(run(client, SCENARIOS["chart"]))
    assert next(p for p in ps if p["type"] == "chart")["spec"]["mark"]["type"] == "line"


def test_repair_flow(client):
    evs = run(client, SCENARIOS["repair"])
    kinds = [e["type"] for e in evs if e["type"].startswith("step.")]
    assert "step.repair_attempt" in kinds
    failed = next(e for e in evs if e["type"] == "step.sql_result" and not e["ok"])
    assert failed["error"]["code"] == "binder_error"
    repair = next(e for e in evs if e["type"] == "step.repair_attempt")
    assert repair["failedStepId"] == failed["stepId"] and "carc_code" in repair["revisedSql"]
    rerun = next(e for e in evs if e["type"] == "step.sql_running" and e.get("repairOf") == repair["stepId"])
    assert rerun["purpose"] == "repair" and rerun["attempt"] == 2
    table = next(p for p in parts(evs) if p["type"] == "table")
    assert table["rowCount"] == 6, "six synthetic CARC codes"
    assert "'" not in failed["error"]["message"].replace("'…'", "")


def test_abstain_is_an_outcome_not_an_error(client):
    evs = run(client, SCENARIOS["abstain"])
    ps = parts(evs)
    assert [p["type"] for p in ps] == ["abstain"]
    assert ps[0]["category"] == "data_unavailable" and len(ps[0]["suggestions"]) == 3
    assert not any(p["type"] in ("error", "table") for p in ps)


def test_clarify_then_resume(client):
    evs = run(client, SCENARIOS["clarify"])
    clar = next(p for p in parts(evs) if p["type"] == "clarification")
    assert [c["id"] for c in clar["choices"]] == ["count", "dollars"] and clar["allowFreeText"]
    cid, asked_mid = evs[0]["conversationId"], evs[0]["messageId"]
    resumed = run(client, "By billed dollars", conversationId=cid,
                  resume={"clarificationId": clar["clarificationId"], "choiceIds": ["dollars"]})
    check_invariants(resumed)
    assert resumed[0]["resumeOf"] == {"clarificationId": clar["clarificationId"], "messageId": asked_mid}
    assert resumed[-1]["finishReason"] == "stop"
    table = next(p for p in parts(resumed) if p["type"] == "table")
    assert "denied_dollar_rate" in [c["key"] for c in table["columns"]]
    # a clarification can be answered once
    again = client.post("/api/chat", headers=LABS, json={"conversationId": cid, "messages": [
        {"role": "user", "text": "count"}], "resume": {"clarificationId": clar["clarificationId"]}})
    assert again.status_code == 409 and again.json()["error"]["code"] == "clarification_expired"


def test_resume_free_text_and_wrong_conversation(client):
    evs = run(client, SCENARIOS["clarify"])
    clar = next(p for p in parts(evs) if p["type"] == "clarification")
    wrong = client.post("/api/chat", headers=LABS, json={"conversationId": "c_other", "messages": [
        {"role": "user", "text": "by count"}], "resume": {"clarificationId": clar["clarificationId"]}})
    assert wrong.status_code == 409
    ok = run(client, "just count the claims", conversationId=evs[0]["conversationId"],
             resume={"clarificationId": clar["clarificationId"], "text": "claim count"})
    table = next(p for p in parts(ok) if p["type"] == "table")
    assert "denial_rate" in [c["key"] for c in table["columns"]]


def test_escalation_haiku_to_sonnet(client):
    evs = run(client, SCENARIOS["escalation"])
    esc = [e for e in evs if e["type"] == "escalation"]
    assert len(esc) == 1
    assert esc[0]["from"]["tier"] == "small" and esc[0]["to"]["tier"] == "medium"
    assert esc[0]["trigger"] == "repair_exhausted"
    models = {m["model"] for m in evs[-1]["usage"]["models"]}
    assert len(models) == 2


def test_candidates_comparison(client):
    evs = run(client, SCENARIOS["candidates"])
    comp = next(e for e in evs if e["type"] == "step.candidates")
    assert len(comp["candidates"]) == 3 and comp["chosenId"] in {"k1", "k2"}
    assert any(e["type"] == "step.sql_running" and e.get("candidateId") == "k3" for e in evs)


def test_error_is_a_part_and_scrubbed(client):
    evs = run(client, SCENARIOS["error"])
    err = next(p for p in parts(evs) if p["type"] == "error")
    assert err["retryable"] is True and "outage" not in json.dumps(evs)


def test_regenerate(client):
    evs = run(client, SCENARIOS["table"])
    cid, mid = evs[0]["conversationId"], evs[0]["messageId"]
    again = run(client, conversationId=cid, regenerate={"messageId": mid})
    check_invariants(again)
    assert again[0]["regenerateOf"] == mid and again[0]["parentId"] == evs[0]["parentId"]
    conv = client.get(f"/api/conversations/{cid}", headers=LABS).json()
    assistants = [m for m in conv["messages"] if m["role"] == "assistant"]
    assert len(assistants) == 2 and assistants[1]["regenerateOf"] == mid
    assert len([m for m in conv["messages"] if m["role"] == "user"]) == 1
    assert client.post("/api/chat", headers=LABS, json={"conversationId": cid, "messages": [],
                                                        "regenerate": {"messageId": "m_nope"}}).status_code == 404


def test_persisted_message_keeps_steps_usage_finish(client):
    evs = run(client, SCENARIOS["repair"])
    conv = client.get(f"/api/conversations/{evs[0]['conversationId']}", headers=LABS).json()
    msg = conv["messages"][-1]
    assert msg["finishReason"] == "stop" and msg["usage"]["queries"] >= 3
    assert any(s["type"] == "step.repair_attempt" for s in msg["steps"])
    assert msg["parts"][0]["type"] == "reasoning"


def test_steps_stream_during_the_run_not_at_the_end():
    app = create_app(settings=Settings(heartbeat_seconds=5, model_delay_seconds=0.15))
    client = TestClient(app)
    stamps = []
    with client.stream("POST", "/api/chat", headers=LABS,
                       json={"messages": [{"role": "user", "text": SCENARIOS["repair"]}]}) as response:
        for line in response.iter_lines():
            if line.startswith("data: "):
                stamps.append((time.perf_counter(), json.loads(line[6:])))
    first_step = next(t for t, e in stamps if e["type"].startswith("step."))
    end = next(t for t, e in stamps if e["type"] == "message.end")
    assert end - first_step > 0.3, "steps arrive well before the answer"
    for _, e in stamps:
        validate_event(e)


def test_cancel_mid_run_ends_with_aborted():
    app = create_app(settings=Settings(heartbeat_seconds=5, model_delay_seconds=0.3))
    client = TestClient(app)
    got = []
    with client.stream("POST", "/api/chat", headers=LABS,
                       json={"messages": [{"role": "user", "text": "How many claims?"}], "scenario": "slow"}) as response:
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            got.append(ev)
            if ev["type"] == "message.start":
                mid = ev["messageId"]
                threading.Timer(0.2, lambda: client.post(f"/api/chat/{mid}/stop", headers=LABS)).start()
    check_invariants(got)
    assert got[-1]["finishReason"] == "aborted"
    assert not any(e["type"] == "part" and e["part"]["type"] == "table" for e in got)


def test_heartbeat_validates():
    app = create_app(settings=Settings(heartbeat_seconds=0.1, model_delay_seconds=0.15))
    client = TestClient(app)
    response = client.post("/api/chat", headers=LABS, json={"messages": [{"role": "user", "text": "How many claims?"}]})
    beats = [e for e in events(response) if e["type"] == "heartbeat"]
    assert beats
    for b in beats:
        validate_event(b)
    assert "id: " not in response.text.split('"heartbeat"')[0].rsplit("\n\n", 1)[-1]


def test_contract_endpoint(client):
    body = client.get("/api/contract").json()
    assert body["version"] == "1.0.0" and "StreamEvent" in body["schema"]["$defs"]
