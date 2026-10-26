"""Contract 1.9.0: partial-table streaming (§5.1) and artifacts/plots (§16)."""
from api.tests.conftest import LABS, events
from api.tests.test_titles import _validator


def _chat(client, scenario, question):
    r = client.post("/api/chat", json={"scenario": scenario, "messages": [{"id": "u1", "role": "user", "text": question}]},
                    headers=LABS)
    assert r.status_code == 200
    return events(r)


def test_table_rows_stream_before_the_complete_part(client):
    evs = _chat(client, "chart", "Paid amount by month")
    v = _validator("StreamEvent")
    for e in evs:
        if e["type"] != "heartbeat":
            v.validate(e)
    table = next(e["part"] for e in evs if e["type"] == "part" and e["part"]["type"] == "table")
    i_start = next(i for i, e in enumerate(evs) if e["type"] == "table.start")
    i_part = next(i for i, e in enumerate(evs) if e["type"] == "part" and e["part"]["id"] == table["id"])
    chunks = [e for e in evs[i_start:i_part] if e["type"] == "table.rows"]
    assert evs[i_start]["partId"] == table["id"] and evs[i_start]["columns"] == table["columns"]
    assert all(e["partId"] == table["id"] for e in chunks)
    assert [c["offset"] for c in chunks] == list(range(0, len(table["rows"]), 10))
    assert [r for c in chunks for r in c["rows"]] == table["rows"]
    chart = next(e["part"] for e in evs if e["type"] == "part" and e["part"]["type"] == "chart")
    assert chart["tablePartId"] == table["id"]


def test_report_scenario_returns_versioned_artifacts(client):
    evs = _chat(client, None, "Paid amount by month as a report")
    arts = [e["part"] for e in evs if e["type"] == "part" and e["part"]["type"] == "artifact"]
    kinds = {a["kind"]: a for a in arts}
    assert set(kinds) == {"chart", "report"}
    assert kinds["chart"]["content"]["type"] == "chart" and kinds["chart"]["autoOpen"] is False
    assert kinds["report"]["autoOpen"] is True and kinds["report"]["content"]["markdown"].startswith("# ")
    assert all(a["version"] == 1 and a["artifactId"].startswith("af_") for a in arts)
    for a in arts:
        _validator("ArtifactPart").validate(a)
    assert not any(e["type"] == "part" and e["part"]["type"] == "chart" for e in evs)  # the plot lives in the artifact
    end = evs[-1]
    assert end["finishReason"] == "stop"
    assert {a["id"] for a in arts} <= set(end["sources"][0]["partIds"])


def test_no_table_events_without_rows(client):
    evs = _chat(client, "abstain", "How many appeals did we win?")
    assert not any(e["type"].startswith("table.") for e in evs)
