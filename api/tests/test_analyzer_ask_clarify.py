"""The analyzer's ask (analyzer.tools.clarification_request) maps to contract 1.23.0's clarification part exactly, on
the live CLI route (calls.jsonl -> LiveTap.ask_args -> api.mapping.outcome_parts), and the user's form reply comes
back as the canonical ``Clarification:`` line."""
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from analyzer.tools import ASK_DEFAULT_LEAD_IN, clarification_request
from api.clarify import normalize_questions, summary, validate_answers
from api.contract import schema
from api.mapping import outcome_parts
from api.store import DownloadRegistry, Principal

TIME_BASIS = {"header": "Time basis", "question": "Which date puts a claim in August?",
              "options": [{"label": "Payment month", "description": "835 payment date in August"},
                          {"label": "Service month", "description": "Date of service in August"}]}
PAYERS = {"header": "Payers", "question": "Which payers?", "multiSelect": True,
          "options": [{"label": "All payers", "description": "Every payer together"},
                      {"label": "Medicare only", "description": "Payer class medicare"},
                      {"label": "Commercial only", "description": "Payer class commercial"}]}
ASKS = {
    "plain": {"question": "Which rate do you mean?"},
    "one form question": {"questions": [TIME_BASIS]},
    "two questions, Q2 multi-select": {"questions": [TIME_BASIS, PAYERS]},
    "multi-select first": {"question": "Two things first:", "questions": [PAYERS, TIME_BASIS]},
    "four questions": {"questions": [TIME_BASIS, PAYERS, {**TIME_BASIS, "header": "Basis 2"},
                                     {**PAYERS, "header": "Payers 2"}]},
}


def _validator(name):
    return Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": schema()["$defs"]})


def _part_from_cli(model_arguments):
    """What the CLI route produces: the CLI logs the parsed request (analyzer.cli._clarification_argument), the
    driver's LiveTap keeps it as ask_args, and final.json's text becomes the answer text."""
    request = clarification_request(model_arguments)
    logged_argument = {"question": request["text"], "questions": request["questions"]}
    answer = SimpleNamespace(kind="clarification", text=request["text"], trace=[])
    parts, finish = outcome_parts(answer, Principal("acme_labs", "u"), DownloadRegistry(), ask_args=logged_argument)
    return request, parts[-1], finish


@pytest.mark.parametrize("name", ASKS)
def test_analyzer_ask_maps_to_a_valid_clarification_part(name):
    request, part, finish = _part_from_cli(ASKS[name])
    assert finish == "needs_input"
    _validator("ClarificationPart").validate(part)
    if not request["questions"]:
        assert part["question"] == request["text"] and "questions" not in part and "choices" not in part
        return
    # the analyzer's questions are already contract-normalized: the form shows exactly what the model asked
    assert normalize_questions(request["questions"]) == request["questions"]
    assert part["questions"] == request["questions"]
    for question in part["questions"]:
        _validator("ClarifyQuestion").validate(question)
    assert part["question"] == request["text"]
    first = request["questions"][0]
    assert part["choices"] == first["options"]                         # pre-1.23 clients answer question 1
    assert part.get("multiSelect", False) == first.get("multiSelect", False)


def test_lead_in_defaults():
    assert _part_from_cli({"questions": [TIME_BASIS, PAYERS]})[1]["question"] == ASK_DEFAULT_LEAD_IN
    assert _part_from_cli({"questions": [TIME_BASIS]})[1]["question"] == TIME_BASIS["question"]


def test_form_reply_becomes_the_clarification_line_the_analyzer_reads():
    request, part, _ = _part_from_cli({"questions": [TIME_BASIS, PAYERS]})
    answers = [{"questionId": "q1", "choiceIds": ["o1"]}, {"questionId": "q2", "choiceIds": ["o2", "o3"]}]
    assert validate_answers(part["questions"], answers) is None
    assert summary(part["questions"], answers) == \
        "Time basis: Payment month; Payers: Medicare only, Commercial only"
    assert summary(part["questions"], []) == "Time basis: no preference; Payers: no preference"


def test_questions_the_form_cannot_show_stay_readable_in_the_lead_in():
    answer = SimpleNamespace(kind="clarification", text="A few quick questions first:", trace=[])
    bad = {"questions": [{"header": "H", "question": "Count or dollars?", "options": []}]}
    parts, _ = outcome_parts(answer, Principal("acme_labs", "u"), DownloadRegistry(), ask_args=bad)
    assert "questions" not in parts[-1] and parts[-1]["question"].endswith("Count or dollars?")
    _validator("ClarificationPart").validate(parts[-1])
