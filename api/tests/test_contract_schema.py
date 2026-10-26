"""The v1 schema is valid, versions agree across the three contract files, and one example per event validates."""
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from api.contract import CONTRACT_VERSION, schema, validate_event

ROOT = Path(__file__).resolve().parents[2]

SQL = "SELECT payer_name, COUNT(*) AS claims FROM sem.claims GROUP BY 1"
EXAMPLES = [
    {"type": "message.start", "seq": 0, "messageId": "m_1", "conversationId": "c_1", "contractVersion": "1.0.0",
     "model": "scripted", "strategy": "tool_loop"},
    {"type": "status", "seq": 1, "label": "Exploring the schema"},
    {"type": "status", "seq": 2, "label": None},
    {"type": "step.explore", "seq": 3, "stepId": "s1", "probe": "describe_view", "target": "sem.claims",
     "summary": "8 columns", "elapsedMs": 4},
    {"type": "step.sql_running", "seq": 4, "stepId": "s2", "sql": SQL, "purpose": "answer", "attempt": 1},
    {"type": "step.sql_result", "seq": 5, "stepId": "s2", "ok": False, "elapsedMs": 3,
     "error": {"code": "binder_error", "message": "column 'payer' not found"}},
    {"type": "step.repair_attempt", "seq": 6, "stepId": "s3", "failedStepId": "s2", "attempt": 1, "maxAttempts": 2,
     "error": {"code": "binder_error", "message": "column not found"}, "revisedSql": SQL},
    {"type": "step.sql_running", "seq": 7, "stepId": "s4", "sql": SQL, "purpose": "repair", "attempt": 2,
     "repairOf": "s3"},
    {"type": "step.sql_result", "seq": 8, "stepId": "s4", "ok": True, "elapsedMs": 9, "rowCount": 7,
     "columns": [{"name": "payer_name", "type": "string"}, {"name": "claims", "type": "integer"}]},
    {"type": "step.check", "seq": 9, "stepId": "s5", "check": "verifier", "verdict": "pass",
     "message": "Row count matches distinct payers.", "targetStepId": "s4", "action": "none"},
    {"type": "step.candidates", "seq": 10, "stepId": "s6", "method": "execution_agreement", "chosenId": "k1",
     "candidates": [{"candidateId": "k1", "sql": SQL, "ok": True, "rowCount": 7, "agreesWith": ["k2"]},
                    {"candidateId": "k2", "sql": SQL, "ok": True, "rowCount": 7}]},
    {"type": "escalation", "seq": 11, "from": {"model": "claude-haiku", "tier": "small"},
     "to": {"model": "claude-sonnet", "tier": "medium"}, "reason": "Verifier failed twice.", "trigger": "verifier_fail"},
    {"type": "table.start", "seq": 11, "partId": "p_tb", "columns": [{"key": "n", "label": "Payers", "type": "integer"}],
     "title": "Payers"},
    {"type": "table.rows", "seq": 11, "partId": "p_tb", "offset": 0, "rows": [[7]]},
    {"type": "reasoning.delta", "seq": 12, "partId": "p_r", "delta": "Thinking"},
    {"type": "reasoning.end", "seq": 13, "partId": "p_r", "durationMs": 1200},
    {"type": "text.start", "seq": 14, "partId": "p_t"},
    {"type": "text.delta", "seq": 15, "partId": "p_t", "delta": "**7** payers"},
    {"type": "text.end", "seq": 16, "partId": "p_t"},
    {"type": "code.delta", "seq": 17, "partId": "p_c", "delta": "SELECT"},
    {"type": "part", "seq": 18, "part": {"type": "code", "id": "p_c", "language": "sql", "code": SQL,
                                         "role": "query", "collapsed": True, "stepId": "s4"}},
    {"type": "part", "seq": 19, "part": {"type": "table", "id": "p_tb", "columns": [
        {"key": "payer_name", "label": "Payer name", "type": "string"},
        {"key": "claims", "label": "Claims", "type": "integer"}], "rows": [["Aetna", 3]], "rowCount": 7,
        "csvFileId": "d_1", "sqlPartId": "p_c", "source": {"views": ["sem.claims"]}}},
    {"type": "part", "seq": 20, "part": {"type": "chart", "id": "p_ch", "library": "vega-lite", "alt": "Bar chart",
        "spec": {"mark": "bar", "data": {"name": "result"}}, "data": {"name": "result", "values": [{"a": 1}]}}},
    {"type": "part", "seq": 21, "part": {"type": "abstain", "id": "p_a", "category": "data_unavailable",
        "reason": "The data has no **appeal** outcomes.", "suggestions": [{"id": "g1", "text": "Denial rate by payer"}]}},
    {"type": "part", "seq": 22, "part": {"type": "clarification", "id": "p_q", "clarificationId": "q_1",
        "question": "Which date?", "allowFreeText": True,
        "choices": [{"id": "service", "label": "Service date"}, {"id": "paid", "label": "Payment date"}]}},
    {"type": "part", "seq": 23, "part": {"type": "error", "id": "p_e", "code": "timeout", "title": "Timed out",
        "message": "The query took too long.", "retryable": True}},
    {"type": "part.patch", "seq": 24, "partId": "p_tb", "patch": {"title": "Claims by payer"}},
    {"type": "heartbeat"},
    {"type": "message.end", "seq": 25, "messageId": "m_1", "finishReason": "stop", "usage": {
        "queries": 2, "elapsedMs": 2400, "tokens": {"input": 5200, "output": 410}, "costUsd": 0.0071,
        "costIsEstimate": True, "latency": {"firstEventMs": 2, "firstTokenMs": 1800, "modelMs": 2100, "sqlMs": 12,
                                            "totalMs": 2400},
        "models": [{"model": "claude-haiku", "role": "analyst", "inputTokens": 5200, "outputTokens": 410}]}},
]

BAD = [
    {"type": "message.start", "seq": 0, "messageId": "m", "conversationId": "c"},  # no contractVersion
    {"type": "step.sql_running", "seq": 1, "stepId": "s", "sql": "x", "purpose": "guess", "attempt": 1},
    {"type": "step.sql_result", "stepId": "s", "ok": True, "elapsedMs": 1},  # no seq
    {"type": "message.end", "seq": 2, "messageId": "m", "finishReason": "done", "usage": {"queries": 0, "elapsedMs": 0}},
    {"type": "part", "seq": 3, "part": {"type": "chart", "id": "x", "library": "vega-lite", "alt": "a",
                                        "spec": {"data": {"url": "https://evil/rows.csv"}}}},
    {"type": "part.patch", "seq": 4, "partId": "p", "patch": {"type": "table"}},
    {"type": "part", "seq": 5, "part": {"type": "abstain", "id": "a", "category": "nope", "reason": "r", "suggestions": []}},
    {"type": "message.start", "seq": 0, "messageId": "m/../x", "conversationId": "c", "contractVersion": "1.0.0"},
]


def test_schema_is_valid_2020_12():
    Draft202012Validator.check_schema(schema())


def test_versions_agree():
    assert schema()["version"] == CONTRACT_VERSION
    md = (ROOT / "api" / "CONTRACT.md").read_text()
    assert f"**Version: {CONTRACT_VERSION}**" in md
    ts = (ROOT / "web" / "src" / "api" / "contract.ts").read_text()
    assert f"CONTRACT_VERSION = '{CONTRACT_VERSION}'" in ts


def test_event_lists_agree_with_typescript():
    ts = (ROOT / "web" / "src" / "api" / "contract.ts").read_text()
    block = re.search(r"EVENT_TYPES = \[(.*?)\]", ts, re.S).group(1)
    ts_types = set(re.findall(r"'([a-z_.]+)'", block))
    schema_types = {schema()["$defs"][r["$ref"].split("/")[-1]]["properties"]["type"]["const"]
                    for r in schema()["$defs"]["StreamEvent"]["oneOf"]}
    assert ts_types == schema_types
    md = (ROOT / "api" / "CONTRACT.md").read_text()
    for t in schema_types:
        assert f"`{t}`" in md, f"{t} undocumented in CONTRACT.md"


def test_every_event_type_has_an_example():
    covered = {e["type"] for e in EXAMPLES}
    assert covered == {schema()["$defs"][r["$ref"].split("/")[-1]]["properties"]["type"]["const"]
                       for r in schema()["$defs"]["StreamEvent"]["oneOf"]}


@pytest.mark.parametrize("event", EXAMPLES, ids=lambda e: e["type"])
def test_examples_validate(event):
    validate_event(event)


@pytest.mark.parametrize("event", BAD, ids=lambda e: e["type"])
def test_off_contract_rejected(event):
    with pytest.raises(ValidationError):
        validate_event(event)
