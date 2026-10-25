"""The SSE stream matches web/src/lib/contract.ts and spec section 5."""
import re

from api.tests.conftest import LABS, ask, parts

KNOWN = {"text", "reasoning", "status", "tool_call", "table", "file", "image", "chart", "code", "callout",
         "artifact", "error"}
EVENT_TYPES = {"message.start", "text.start", "text.delta", "text.end", "reasoning.delta", "reasoning.end",
               "code.delta", "part", "part.patch", "status", "message.end", "heartbeat"}


def reduce(evs):
    """Python port of contract.ts ``reduce`` for the events we emit."""
    out, order = {}, []
    for ev in evs:
        if ev["type"] == "text.start":
            out.setdefault(ev["partId"], {"type": "text", "id": ev["partId"], "text": ""})
            order.append(ev["partId"]) if ev["partId"] not in order else None
        elif ev["type"] == "text.delta":
            out[ev["partId"]]["text"] += ev["delta"]
        elif ev["type"] == "part":
            if ev["part"]["id"] not in order:
                order.append(ev["part"]["id"])
            out[ev["part"]["id"]] = ev["part"]
    return [out[i] for i in order]


def test_envelope_and_order(client):
    response, evs = ask(client, "Paid amount trend by month")
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    assert all(ev["type"] in EVENT_TYPES for ev in evs)
    assert evs[0]["type"] == "message.start" and evs[0]["conversationId"]
    assert evs[-1]["type"] == "message.end" and evs[-1]["finishReason"] == "stop"
    assert evs[-1]["messageId"] == evs[0]["messageId"]
    assert set(evs[-1]["usage"]) == {"queries", "elapsedMs"}
    ids = re.findall(r"^id: (\S+)$", response.text, re.M)
    assert ids and all(i.startswith(evs[0]["messageId"] + ":") for i in ids)
    assert all(p["type"] in KNOWN and p["id"] for p in parts(evs))


def test_time_series_maps_to_text_sql_table_chart_assumptions(client):
    _, evs = ask(client, "Paid amount trend by month")
    message = reduce(evs)
    kinds = [p["type"] for p in message]
    assert kinds[0] == "tool_call"
    assert kinds.index("text") < kinds.index("code") < kinds.index("table") < kinds.index("chart")
    text = next(p for p in message if p["type"] == "text")
    assert text["text"] == "Paid amount by service month."
    code = next(p for p in message if p["type"] == "code")
    assert code["language"] == "sql" and code["role"] == "query" and "sem.claims" in code["code"]
    table = next(p for p in message if p["type"] == "table")
    assert table["sqlPartId"] == code["id"]
    assert [c["type"] for c in table["columns"]] == ["date", "currency"]
    assert table["rowCount"] == len(table["rows"]) and table["csvFileId"]
    assert table["source"]["views"] == ["sem.claims"]
    chart = next(p for p in message if p["type"] == "chart")
    assert chart["library"] == "vega-lite" and chart["spec"]["mark"]["type"] == "line"
    assert "url" not in chart["spec"]["data"] and chart["alt"]
    assert chart["spec"]["encoding"]["x"]["type"] == "temporal"
    assert len(chart["data"]["values"]) == table["rowCount"]
    callout = next(p for p in message if p["type"] == "callout")
    assert callout["variant"] == "assumption" and len(callout["items"]) == 2


def test_categorical_gets_bar_chart(client):
    _, evs = ask(client, "What is the denial rate by payer?")
    chart = next(p for p in parts(evs) if p["type"] == "chart")
    assert chart["spec"]["mark"]["type"] == "bar"
    assert chart["spec"]["encoding"]["x"]["field"] == "payer_name"
    table = next(p for p in parts(evs) if p["type"] == "table")
    assert {c["key"]: c["type"] for c in table["columns"]}["denial_rate"] == "percent"


def test_large_result_is_capped_with_download_id(client):
    _, evs = ask(client, "list all claims")
    table = next(p for p in parts(evs) if p["type"] == "table")
    assert table["rowCount"] == 1200 and len(table["rows"]) == 50
    file = next(p for p in parts(evs) if p["type"] == "file")
    assert file["fileId"] == table["csvFileId"] and file["mime"] == "text/csv" and file["rowCount"] == 1200
    assert file["name"] == "result.csv"
    assert not any(p["type"] == "chart" for p in parts(evs)), "row-level listings are not charted"
    for ev in evs:
        assert "\n" not in str(ev.get("part", {}).get("rows", "")), "no inline CSV"


def test_tool_steps_carry_sql_but_no_rows(client):
    _, evs = ask(client, "How many claims?")
    steps = [p for p in parts(evs) if p["type"] == "tool_call"]
    assert [s["tool"] for s in steps] == ["describe_view", "run_sql"]
    assert all(s["state"] == "ok" for s in steps)
    assert "sem.claims" in steps[1]["input"]["sql"]
    assert all("preview" not in s.get("output", {}) for s in steps)


def test_refusal_is_plain_text(client):
    _, evs = ask(client, "Give me the patient names on denied claims")
    message = reduce(evs)
    assert [p["type"] for p in message] == ["text"]
    assert "can't" in message[0]["text"]
    assert evs[-1]["usage"]["queries"] == 0


def test_validation_and_auth(client):
    assert client.post("/api/chat", json={"messages": []}, headers=LABS).status_code == 422
    assert client.post("/api/chat", json={"messages": [{"role": "user", "text": "hi"}]}).status_code == 401
    bad = {"Authorization": "Bearer demo:evil_corp:x"}
    assert client.post("/api/chat", json={"messages": [{"role": "user", "text": "hi"}]}, headers=bad).status_code == 401


def test_health(client):
    body = client.get("/api/health").json()
    assert body == {"status": "ok", "mode": "demo", "llm": "scripted", "paidApis": False}


def test_resume_with_last_event_id(client):
    response, evs = ask(client, "How many claims?")
    ids = re.findall(r"^id: (\S+)$", response.text, re.M)
    resumed = client.post("/api/chat", json={"messages": []}, headers={**LABS, "Last-Event-ID": ids[2]})
    replay = [line for line in resumed.text.splitlines() if line.startswith("id: ")]
    assert [line[4:] for line in replay] == ids[3:]
