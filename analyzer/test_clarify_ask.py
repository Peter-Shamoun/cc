"""ask: one plain question (unchanged) or 1-4 form questions with options (contract 1.23.0 clarify, CONTRACT.md §6.1)."""

import contextlib
import io
import json

import pytest

from analyzer import cli
from analyzer.config import AnalyzerConfig
from analyzer.harness import build_subagent_prompt
from analyzer.tools import ASK_DEFAULT_LEAD_IN, ToolError, clarification_request, tool_spec

QUESTION = {"id": "q_clarify", "question": "What was our denial rate in August 2026?"}
TIME_BASIS = {"header": "Time basis", "question": "Which date puts a claim in August?",
              "options": [{"label": "Payment month", "description": "835 payment date in August"},
                          {"label": "Service month", "description": "Date of service in August"}]}
PAYERS = {"header": "Payers", "question": "Which payers?", "multiSelect": True,
          "options": [{"label": "All payers", "description": "Every payer together"},
                      {"label": "Medicare only", "description": "Payer class medicare"},
                      {"label": "Commercial only", "description": "Payer class commercial"}]}


def _session(snapshot, technique, question=QUESTION):
    from analyzer.harness import stamp_session
    from bench.agent_session import create_session
    session = create_session(snapshot, question, technique, "haiku", "pytest-clarify")
    stamp_session(session)
    return session


def _run(session, *args):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli.main(["--session", str(session), *args])
    return code, buffer.getvalue()


def _calls(session):
    return [json.loads(line) for line in (session / "calls.jsonl").read_text().splitlines()]


# ---- spec -----------------------------------------------------------------------------------------------------------

def test_ask_is_in_the_tool_spec_only_when_enabled():
    assert "ask" not in {tool["name"] for tool in tool_spec(AnalyzerConfig(enable_ask=False))}
    ask = next(tool for tool in tool_spec(AnalyzerConfig(enable_ask=True)) if tool["name"] == "ask")
    properties = ask["input_schema"]["properties"]
    assert set(properties) == {"question", "questions"}
    item = properties["questions"]["items"]
    assert properties["questions"]["maxItems"] == 4 and item["properties"]["header"]["maxLength"] == 12
    assert item["properties"]["options"]["minItems"] == 2 and item["properties"]["options"]["maxItems"] == 4
    assert item["properties"]["options"]["items"]["required"] == ["label", "description"]


def test_a_plain_question_is_unchanged():
    assert clarification_request({"question": "  Which  rate do you mean? "}) == {
        "text": "Which rate do you mean?", "questions": []}


def test_a_single_question_without_options_is_a_plain_question():
    out = clarification_request({"questions": [{"header": "Rate", "question": "Which rate?"}]})
    assert out == {"text": "Which rate?", "questions": []}


def test_several_questions_get_contract_ids_and_a_default_lead_in():
    out = clarification_request({"questions": [TIME_BASIS, PAYERS]})
    assert out["text"] == ASK_DEFAULT_LEAD_IN
    assert [q["id"] for q in out["questions"]] == ["q1", "q2"]
    assert [o["id"] for o in out["questions"][1]["options"]] == ["o1", "o2", "o3"]
    assert out["questions"][1]["multiSelect"] is True and "multiSelect" not in out["questions"][0]
    assert out["questions"][0]["options"][0] == {"id": "o1", "label": "Payment month",
                                                 "description": "835 payment date in August"}


def test_one_form_question_leads_in_with_its_own_text_unless_a_lead_in_is_given():
    assert clarification_request({"questions": [TIME_BASIS]})["text"] == TIME_BASIS["question"]
    assert clarification_request({"question": "Before I run this:", "questions": [TIME_BASIS]})["text"] == \
        "Before I run this:"


@pytest.mark.parametrize("arguments, message", [
    ({}, "needs a question"),
    ({"questions": [TIME_BASIS] * 5}, "at most 4"),
    ({"questions": [{**TIME_BASIS, "header": "A header that is long"}, PAYERS]}, "header must be 1-12"),
    ({"questions": [TIME_BASIS, {**PAYERS, "options": PAYERS["options"][:1]}]}, "give 2-4 options"),
    ({"questions": [TIME_BASIS, {**PAYERS, "options": PAYERS["options"] * 2}]}, "give 2-4 options"),
    ({"questions": [TIME_BASIS, {"header": "Payers", "question": "Which payers?"}]}, "give 2-4 options"),
    ({"questions": [{**TIME_BASIS, "options": [{"label": "a"}, {"label": "b", "description": "d"}]}]},
     "option 1: needs a label"),
    ({"questions": [{**TIME_BASIS, "options": [{"label": "a", "description": "x"}, {"label": "A", "description": "y"}]}]},
     "labels must differ"),
    ({"questions": "not a list"}, "must be a list"),
])
def test_malformed_asks_fail_with_a_message_the_model_can_fix(arguments, message):
    with pytest.raises(ToolError, match=message):
        clarification_request(arguments)


# ---- briefing -------------------------------------------------------------------------------------------------------

def test_the_when_to_ask_block_and_command_appear_only_with_ask_on(bench_snapshot, sessions_root):
    off = build_subagent_prompt(_session(bench_snapshot, {"glossary": "invariants"}))
    on = build_subagent_prompt(_session(bench_snapshot, {"glossary": "invariants", "enable_ask": True}))
    assert "When to ask" not in off and " ask " not in off
    assert "When to ask instead of answering" in on and "ask --questions '[" in on
    assert "Clarification: Header: choice" in on


# ---- CLI ------------------------------------------------------------------------------------------------------------

def test_cli_plain_ask_records_the_old_final(bench_snapshot, sessions_root):
    session = _session(bench_snapshot, {"enable_ask": True})
    code, _ = _run(session, "ask", "Which rate do you mean?")
    assert code == 0
    assert json.loads((session / "final.json").read_text()) == {"kind": "clarification",
                                                                "text": "Which rate do you mean?"}


def test_cli_multi_question_ask_records_questions_and_logs_them_parsed(bench_snapshot, sessions_root):
    session = _session(bench_snapshot, {"enable_ask": True})
    code, out = _run(session, "ask", "--questions", json.dumps([TIME_BASIS, PAYERS]))
    assert code == 0 and json.loads(out) == {"recorded": True, "questions": 2}
    final = json.loads((session / "final.json").read_text())
    assert final["kind"] == "clarification" and final["text"] == ASK_DEFAULT_LEAD_IN
    assert [q["header"] for q in final["questions"]] == ["Time basis", "Payers"]
    logged = _calls(session)[-1]
    assert logged["command"] == "ask" and logged["ok"]
    assert logged["argument"] == {"question": ASK_DEFAULT_LEAD_IN, "questions": final["questions"]}


def test_cli_rejects_a_malformed_ask_and_lets_the_model_retry(bench_snapshot, sessions_root):
    session = _session(bench_snapshot, {"enable_ask": True})
    code, out = _run(session, "ask", "--questions", "[{'header': 'x'}]")
    assert code == 1 and "not valid JSON" in out and not (session / "final.json").exists()
    code, out = _run(session, "ask", "--questions", json.dumps([{**TIME_BASIS, "options": []}, PAYERS]))
    assert code == 1 and "options" in out and not (session / "final.json").exists()
    assert _run(session, "ask", "--questions", json.dumps([TIME_BASIS]))[0] == 0
    assert _run(session, "ask", "Again?")[0] == 1   # one final per session


def test_cli_ask_is_refused_when_off(bench_snapshot, sessions_root):
    session = _session(bench_snapshot, {})
    code, out = _run(session, "ask", "Which rate?")
    assert code == 1 and "not enabled" in out


# ---- in-process loop ------------------------------------------------------------------------------------------------

def test_loop_ask_repairs_a_malformed_call_then_returns_the_questions(bench_snapshot):
    from analyzer.llm import ScriptedLLM
    from analyzer.loop import Analyzer
    from analyzer.opener import SnapshotOpener
    llm = ScriptedLLM([{"tool_calls": [{"name": "ask", "arguments": {"questions": [TIME_BASIS, {"header": "P"}]}}]},
                       {"tool_calls": [{"name": "ask", "arguments": {"questions": [TIME_BASIS, PAYERS]}}]}])
    config = AnalyzerConfig.from_technique({"enable_ask": True, "max_turns": 4})
    answer = Analyzer(SnapshotOpener(bench_snapshot), llm, config).answer(QUESTION["question"])
    assert answer.kind == "clarification" and answer.text == ASK_DEFAULT_LEAD_IN
    assert [q["id"] for q in answer.questions] == ["q1", "q2"]
    assert answer.to_final_json() == {"kind": "clarification", "text": ASK_DEFAULT_LEAD_IN,
                                      "questions": answer.questions}


# ---- scoring the probe set ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("case, final, correct", [
    ({"expect_clarification": True}, {"kind": "clarification", "text": "?"}, True),
    ({"expect_clarification": True}, {"kind": "refusal", "text": "no"}, False),
    ({"expect_unanswerable": True}, {"kind": "refusal", "text": "no"}, True),
    ({"expect_unanswerable": True}, {"kind": "clarification", "text": "?"}, True),
    ({"expect_unanswerable": True}, None, False),
])
def test_probe_scoring(bench_snapshot, sessions_root, case, final, correct):
    from bench.agent_session import score_session
    session = _session(bench_snapshot, {"enable_ask": True})
    if final:
        (session / "final.json").write_text(json.dumps(final))
    assert score_session(session, {"id": QUESTION["id"], **case}, None)["correct"] is correct


def test_probe_file_is_outside_the_frozen_splits():
    from pathlib import Path

    from bench.eval_subagents import _load_cases_file
    from bench.splits import load_assignment
    cases = _load_cases_file(Path(__file__).resolve().parent.parent / "bench" / "clarify_probe_questions.yaml")
    assigned = {row["id"] for row in load_assignment()["questions"]}
    assert cases and not {case["id"] for case in cases} & assigned
    assert all(case.get("expect_clarification") or case.get("expect_unanswerable") for case in cases)


# ---- two turns through the contract stream: ask -> form answers -> "Clarification:" line -> answer ---------------

class _RecordingLLM:
    """A ScriptedLLM that keeps the system prompt and messages of every call."""

    def __init__(self, scripts):
        from analyzer.llm import ScriptedLLM
        self.inner, self.seen = ScriptedLLM(scripts), []

    def complete(self, **kwargs):
        self.seen.append({"system": kwargs["system"], "messages": kwargs["messages"]})
        return self.inner.complete(**kwargs)


def _turn(bench_snapshot, question, scripts):
    from analyzer.loop import Analyzer
    from analyzer.opener import SnapshotOpener
    from api.analyzer_adapter import run_to_events
    llm = _RecordingLLM(scripts)
    config = AnalyzerConfig.from_technique({"enable_ask": True, "max_turns": 4})
    events = run_to_events(question, lambda tap: Analyzer(SnapshotOpener(bench_snapshot), tap, config).answer(question),
                           llm, model="scripted")
    return events, llm


def test_ask_then_the_users_answers_come_back_as_the_next_turn(bench_snapshot):
    from api.clarify import summary, validate_answers
    from api.contract.conformance import check
    events, _ = _turn(bench_snapshot, QUESTION["question"],
                      [{"tool_calls": [{"name": "ask", "arguments": {"questions": [TIME_BASIS, PAYERS]}}]}])
    assert check(events) == [] and events[-1]["finishReason"] == "needs_input"
    part = next(e["part"] for e in events if e["type"] == "part" and e["part"]["type"] == "clarification")
    assert [q["header"] for q in part["questions"]] == ["Time basis", "Payers"] and part["question"] == ASK_DEFAULT_LEAD_IN

    answers = [{"questionId": "q1", "choiceIds": ["o1"]}, {"questionId": "q2", "choiceIds": ["o2"]}]
    assert validate_answers(part["questions"], answers) is None
    next_question = f"{QUESTION['question']}\nClarification: {summary(part['questions'], answers)}"  # as api.app resume
    sql = "SELECT COUNT(*) AS claims FROM sem.claims"
    events, llm = _turn(bench_snapshot, next_question,
                        [{"tool_calls": [{"name": "final_answer", "arguments": {"sql": sql, "answer": "Done."}}]}])
    assert check(events) == [] and events[-1]["finishReason"] == "stop"
    first_call = llm.seen[0]
    assert "Clarification: Time basis: Payment month; Payers: Medicare only" in json.dumps(first_call["messages"])
    assert "that is the user's reply to your earlier questions" in first_call["system"]
