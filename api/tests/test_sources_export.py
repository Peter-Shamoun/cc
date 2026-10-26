"""Contract 1.8.0: citations (message.end.sources, §14) and conversation export (§15)."""
import json
from pathlib import Path

from api.sources import sources_for
from api.tests.conftest import DX, LABS, events
from api.tests.test_titles import _validator


def _chat(client, scenario, question, cid="c_src"):
    r = client.post("/api/chat", json={"conversationId": cid, "scenario": scenario,
                                       "messages": [{"id": "u1", "role": "user", "text": question}]}, headers=LABS)
    assert r.status_code == 200
    return events(r)


def test_answer_cites_views_and_links_parts_and_steps(client):
    evs = _chat(client, "table", "Paid amount by payer")
    end = evs[-1]
    _validator("MessageEnd").validate(end)
    src = end["sources"][0]
    assert src["name"] == "sem.claims" and src["role"] == "answer" and src["label"] == "Claims"
    part_ids = {e["part"]["id"] for e in evs if e["type"] == "part" and e["part"]["type"] in ("table", "chart", "code")}
    assert set(src["partIds"]) <= part_ids and src["partIds"]
    step_ids = {e["stepId"] for e in evs if e["type"].startswith("step.")}
    assert set(src["stepIds"]) <= step_ids
    stored = client.get("/api/conversations/c_src", headers=LABS).json()["messages"][-1]
    assert stored["sources"] == end["sources"]


def test_abstain_and_clarify_cite_nothing_as_answer(client):
    for scenario, q in (("abstain", "How many appeals did we win?"), ("clarify", "How bad are denials?")):
        end = _chat(client, scenario, q, cid=f"c_{scenario}")[-1]
        assert all(s["role"] == "explored" for s in end.get("sources", []))


def test_sources_for_orders_answer_first_and_ignores_non_views():
    steps = [{"type": "step.explore", "stepId": "s_1", "target": "sem.payers"},
             {"type": "step.explore", "stepId": "s_2", "target": "the schema"},
             {"type": "step.sql_running", "stepId": "s_3", "sql": "select * from sem.claims join sem.payers using (x)"}]
    parts = [{"type": "code", "id": "p_1", "role": "query", "code": "select 1 from sem.claims"},
             {"type": "table", "id": "p_2", "source": {"views": ["sem.claims"]}},
             {"type": "chart", "id": "p_3"}]
    out = sources_for(parts, steps)
    assert [(s["name"], s["role"]) for s in out] == [("sem.claims", "answer"), ("sem.payers", "explored")]
    assert out[0]["partIds"] == ["p_1", "p_2", "p_3"] and out[1]["stepIds"] == ["s_1", "s_3"]
    assert [s["id"] for s in out] == ["src_1", "src_2"]


def test_export_markdown_and_json(client):
    _chat(client, "table", "Paid amount by payer", cid="c_exp")
    r = client.get("/api/conversations/c_exp/export", headers=LABS)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert r.headers["cache-control"] == "no-store"
    disp = r.headers["content-disposition"]
    assert disp.startswith('attachment; filename="claimclarity_conversation_') and disp.endswith('.md"')
    md = r.text
    assert "## Question" in md and "```sql" in md and "| Payer name |" in md and "(`sem.claims`)" in md
    r = client.get("/api/conversations/c_exp/export?format=json", headers=LABS)
    body = r.json()
    _validator("ConversationExport").validate(body)
    assert body["conversation"]["id"] == "c_exp" and r.headers["content-disposition"].endswith('.json"')


def test_export_errors(client):
    _chat(client, "table", "Paid amount by payer", cid="c_exp2")
    assert client.get("/api/conversations/c_exp2/export?format=pdf", headers=LABS).status_code == 422
    assert client.get("/api/conversations/c_exp2/export", headers=DX).status_code == 404
    assert client.get("/api/conversations/nope/export", headers=LABS).status_code == 404
    assert client.get("/api/conversations/c_exp2/export").status_code == 401


def test_markdown_escapes_cells():
    from api.export import as_markdown
    conv = {"title": "T", "messages": [{"role": "assistant", "parts": [
        {"type": "table", "id": "p", "columns": [{"name": "a"}], "rows": [["x|y\nz"], [0.12345], [1234.5678]],
         "rowCount": 80}]}]}
    md = as_markdown(conv, "now")
    assert "| x\\|y z |" in md and "| 0.1235 |" in md and "| 1234.57 |" in md and "Showing 3 of 80 rows" in md


def test_published_export_example_matches_server():
    from api.contract.gen_examples import export_example
    assert (Path(__file__).parents[1] / "contract/examples/export.md").read_text() == export_example()
