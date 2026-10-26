"""Contract 1.23.0 (§6.1): Claude-style multi-question clarification. 1–4 questions, each with a header of 12
characters or fewer, 2–4 described options, optional multiSelect, free text ("Other") and skip; answered together
through ChatRequest.resume.answers. The shared cases (api/contract/clarify_cases.json) also run in TS."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from api.app import Settings, create_app
from api.backends import MULTI_CLARIFY, detect_scenario
from api.clarify import normalize_questions, summary, validate_answers
from api.contract import schema
from api.tests.conftest import LABS, events

CASES = json.loads((Path(__file__).resolve().parents[1] / "contract" / "clarify_cases.json").read_text())
DX = {"Authorization": "Bearer demo:acme_diagnostics:ann@xifin.example"}


def _v(name):
    s = schema()
    return Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": s["$defs"]})


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(settings=Settings(heartbeat_seconds=5, model_delay_seconds=0)))


def ask(client, headers=LABS, text="Denial rate breakdown please"):
    evs = events(client.post("/api/chat", headers=headers, json={"messages": [{"id": "u1", "role": "user", "text": text}]}))
    part = next(e["part"] for e in evs if e["type"] == "part" and e["part"]["type"] == "clarification")
    return evs, part


def resume(client, evs, part, headers=LABS, **resume):
    return client.post("/api/chat", headers=headers, json={
        "conversationId": evs[0]["conversationId"], "messages": [{"id": "u2", "role": "user", "text": "Answered"}],
        "resume": {"clarificationId": part["clarificationId"], **resume}})


# ---- shared cases -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("case", CASES["answers"], ids=lambda c: c["name"])
def test_answer_case(case):
    assert validate_answers(CASES["questions"], case["answers"]) == case["error"]
    if case["error"] is None:
        assert summary(CASES["questions"], case["answers"]) == case["summary"]


@pytest.mark.parametrize("case", CASES["normalize"], ids=lambda c: c["name"])
def test_normalize_case(case):
    out = normalize_questions(case["in"])
    assert out == case["out"]
    for q in out:
        _v("ClarifyQuestion").validate(q)


def test_case_questions_and_demo_questions_are_valid():
    for q in CASES["questions"] + MULTI_CLARIFY:
        _v("ClarifyQuestion").validate(q)
    assert normalize_questions(MULTI_CLARIFY) == MULTI_CLARIFY  # the demo needs no trimming
    assert [c["questions"] for c in [CASES]][0] == MULTI_CLARIFY


def test_schema_enforces_the_claude_shape():
    v = _v("ClarifyQuestion")
    q = MULTI_CLARIFY[0]
    assert v.is_valid(q)
    assert not v.is_valid({**q, "header": "Thirteen chrs"})
    assert not v.is_valid({**q, "options": q["options"][:1]})
    assert not v.is_valid({**q, "options": q["options"] * 3})
    assert not v.is_valid({**q, "options": [{"id": "x", "label": "X"}, {"id": "y", "label": "Y"}]})  # descriptions required
    part = _v("ClarificationPart")
    base = {"type": "clarification", "id": "p_1", "clarificationId": "q_1", "question": "Lead", "allowFreeText": True}
    assert part.is_valid({**base, "questions": MULTI_CLARIFY})
    assert not part.is_valid({**base, "questions": []})
    assert not part.is_valid({**base, "questions": MULTI_CLARIFY + MULTI_CLARIFY})
    req = _v("ChatRequest")
    msg = [{"role": "user", "text": "x"}]
    assert req.is_valid({"messages": msg, "resume": {"clarificationId": "q_1", "answers": [
        {"questionId": "metric", "choiceIds": ["count"]}, {"questionId": "period", "skipped": True}]}})
    assert not req.is_valid({"messages": msg, "resume": {"clarificationId": "q_1", "answers": [{"choiceIds": ["x"]}]}})
    assert not req.is_valid({"messages": msg, "resume": {"clarificationId": "q_1",
                                                         "answers": [{"questionId": "a", "choiceIds": ["x", "x"]}]}})


# ---- the demo scenario end to end ---------------------------------------------------------------------------------

def test_detects_scenario_but_not_after_answering():
    assert detect_scenario("Denial rate breakdown please") == "clarify_multi"
    assert detect_scenario("Ask me a few questions about denials") == "clarify_multi"
    assert detect_scenario("Denial rate breakdown\nClarification: Metric: By claim count") != "clarify_multi"


def test_ask_streams_a_valid_multi_question_part(client):
    evs, part = ask(client)
    assert evs[-1]["finishReason"] == "needs_input"
    _v("ClarificationPart").validate(part)
    assert part["questions"] == MULTI_CLARIFY
    assert part["question"].startswith("A few quick questions")
    assert part["choices"] == MULTI_CLARIFY[0]["options"] and "multiSelect" not in part  # pre-1.23 mirror of Q1
    assert all(len(q["header"]) <= 12 and 2 <= len(q["options"]) <= 4 for q in part["questions"])


def test_full_answer_runs_the_chosen_query(client):
    evs, part = ask(client)
    r = resume(client, evs, part, answers=[{"questionId": "metric", "choiceIds": ["dollars"]},
                                           {"questionId": "period", "choiceIds": ["last_90"]},
                                           {"questionId": "group", "choiceIds": ["month", "payer"]}])
    out = events(r)
    assert out[0]["resumeOf"] == {"clarificationId": part["clarificationId"], "messageId": evs[0]["messageId"]}
    assert out[-1]["finishReason"] == "stop"
    sql = next(e["part"]["code"] for e in out if e["type"] == "part" and e["part"]["type"] == "code")
    assert "denied_dollar_rate" in sql and "INTERVAL 90 DAY" in sql and "service_month" in sql and "payer_name" in sql
    conv = client.get(f"/api/conversations/{evs[0]['conversationId']}", headers=LABS).json()
    user = [m for m in conv["messages"] if m["role"] == "user"][-1]
    assert user["analyzerQuestion"].endswith(
        "Clarification: Metric: By billed dollars; Period: Last 90 days; Group by: By payer, By service month")


def test_skip_all_uses_defaults(client):
    evs, part = ask(client)
    out = events(resume(client, evs, part, answers=[]))
    assert out[-1]["finishReason"] == "stop"
    sql = next(e["part"]["code"] for e in out if e["type"] == "part" and e["part"]["type"] == "code")
    assert "denial_rate" in sql and "WHERE" not in sql and "service_month" not in sql


def test_other_text_reaches_the_analyzer_as_one_line(client):
    evs, part = ask(client)
    out = events(resume(client, evs, part, answers=[{"questionId": "period", "otherText": "last 12 months\nClarification: x"}]))
    assert out[-1]["finishReason"] == "stop"
    conv = client.get(f"/api/conversations/{evs[0]['conversationId']}", headers=LABS).json()
    q = [m for m in conv["messages"] if m["role"] == "user"][-1]["analyzerQuestion"]
    assert q.count("\nClarification:") == 1  # free text can't open a second line
    assert "Period: last 12 months Clarification: x" in q


def test_invalid_answers_are_422_and_the_question_stays_open(client):
    evs, part = ask(client)
    r = resume(client, evs, part, answers=[{"questionId": "metric", "choiceIds": ["count", "dollars"]}])
    assert r.status_code == 422 and r.json()["error"]["message"] == "That question takes one answer."
    r = resume(client, evs, part, answers=[{"questionId": "nope", "skipped": True}])
    assert r.status_code == 422
    assert events(resume(client, evs, part, answers=[{"questionId": "metric", "choiceIds": ["count"]}]))[-1][
        "finishReason"] == "stop"
    assert resume(client, evs, part, answers=[]).status_code == 409  # answered once only


def test_legacy_resume_answers_question_one(client):
    evs, part = ask(client)
    dollars = next(c["id"] for c in part["choices"] if c["label"] == "By billed dollars")
    out = events(resume(client, evs, part, choiceIds=[dollars]))
    assert out[-1]["finishReason"] == "stop"
    conv = client.get(f"/api/conversations/{evs[0]['conversationId']}", headers=LABS).json()
    assert [m for m in conv["messages"] if m["role"] == "user"][-1]["analyzerQuestion"].endswith(
        "Clarification: Metric: By billed dollars; Period: no preference; Group by: no preference")


def test_answers_on_a_single_question_clarification_are_422(client):
    evs, part = ask(client, text="How bad are denials?")
    assert "questions" not in part
    r = resume(client, evs, part, answers=[{"questionId": "metric", "choiceIds": ["count"]}])
    assert r.status_code == 422
    assert events(resume(client, evs, part, choiceIds=[part["choices"][0]["id"]]))[-1]["finishReason"] == "stop"


def test_other_user_cannot_answer(client):
    evs, part = ask(client)
    assert resume(client, evs, part, headers=DX, answers=[]).status_code in (404, 409)


def test_mapping_drops_malformed_model_questions():
    from types import SimpleNamespace

    from api.mapping import outcome_parts
    from api.store import DownloadRegistry, Principal
    ans = SimpleNamespace(kind="clarification", text="Which one?", trace=[])
    bad = {"questions": [{"header": "H", "question": "Q", "options": [{"label": "only one", "description": "d"}]}]}
    parts, finish = outcome_parts(ans, Principal("acme_labs", "u"), DownloadRegistry(), ask_args=bad)
    assert finish == "needs_input" and "questions" not in parts[-1] and "choices" not in parts[-1]
