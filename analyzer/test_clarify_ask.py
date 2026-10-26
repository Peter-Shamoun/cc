"""ask: one plain question or 1-4 form questions with options (contract 1.23.0 clarify, CONTRACT.md §6.1)."""

import pytest

from analyzer.tools import ASK_DEFAULT_LEAD_IN, ToolError, clarification_request

TIME_BASIS = {"header": "Time basis", "question": "Which date puts a claim in August?",
              "options": [{"label": "Payment month", "description": "835 payment date in August"},
                          {"label": "Service month", "description": "Date of service in August"}]}
PAYERS = {"header": "Payers", "question": "Which payers?", "multiSelect": True,
          "options": [{"label": "All payers", "description": "Every payer together"},
                      {"label": "Medicare only", "description": "Payer class medicare"},
                      {"label": "Commercial only", "description": "Payer class commercial"}]}


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
