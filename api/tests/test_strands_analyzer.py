"""The analyzer path: B1 on a Strands agent with one ``cc(command)`` tool, driven by a scripted Strands model (no
network): prompt-cache layout, command lines (with a quoting error the model fixes), finals, Stop, the Bedrock model
settings and their fail-fast checks, and the smoke command."""
import io
import threading
from datetime import date, datetime
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from analyzer.b1_commands import B1_CONFIG
from analyzer.scripted_model import ScriptedModel, ScriptedTurn, scripted_turns, tool_results
from analyzer.strands_agent import CACHE_POINT, COMMAND_TOOL_SPEC, RunCancelled, answer_question
from api.app import Settings, create_app
from api.backends import DemoAuth
from api.contract.conformance import check
from api.live import LiveTap
from api.strands_analyzer import (PAID_SWITCH_VALUE, AnalyzerConfigurationError, AnalyzerSettings, StrandsAnalyzer,
                                  aws_session, bedrock_model_factory, build_analyzer)
from api.tests.conftest import DX, LABS, ask, cc, parts

SQL = "SELECT payer_name, COUNT(*) AS claims FROM sem.claims GROUP BY 1 ORDER BY 1"


def _opener(token="demo:acme_labs:ana@xifin.example"):
    return DemoAuth().authenticate(token, "r1").opener


def _run(model, question="Claims by payer?", token="demo:acme_labs:ana@xifin.example", today=date(2026, 10, 26),
         cancelled=None, observer=None):
    return answer_question(question, opener=_opener(token), model=model, model_label="scripted",
                           observer=observer or LiveTap(lambda event: None), cancelled=cancelled or threading.Event(),
                           today=today)


def _answering_model():
    return ScriptedModel(scripted_turns(cc("sql", SQL), cc("answer", "--sql", SQL, "--text", "Claims by payer.")))


# ---- prompt caching ------------------------------------------------------------------------------------------------

def test_static_prefix_is_byte_identical_across_questions_and_days_and_varying_parts_follow_it():
    first, second = _answering_model(), _answering_model()
    _run(first, "Claims by payer?", today=date(2026, 10, 26))
    _run(second, "Denied claims last month?", today=date(2026, 11, 2))
    system_first, system_second = (model.requests[0]["system_prompt_content"] for model in (first, second))
    assert system_first == system_second and system_first[-1] == CACHE_POINT and len(system_first) == 2
    static = system_first[0]["text"]
    assert "Gold schema" in static and "sem.claims" in static and "resolve relative dates" in static
    for varying in ("Claims by payer", "Denied claims", "2026-10-26", "2026-11-02", "Today is", "data covers "):
        assert varying not in static
    message = first.requests[0]["messages"][0]["content"][0]["text"]
    assert message.startswith("Today is 2026-10-26. This customer's data covers ")
    assert message.rstrip().endswith("User question: Claims by payer?")
    # the agent loop's history grows after it: each request repeats the previous one as its prefix
    assert first.requests[1]["messages"][:1] == first.requests[0]["messages"]


def test_tool_spec_is_static_and_identical_for_every_customer():
    labs, dx = _answering_model(), _answering_model()
    _run(labs, token="demo:acme_labs:ana@xifin.example")
    _run(dx, token="demo:acme_diagnostics:dev@xifin.example")
    assert labs.requests[0]["tool_names"] == dx.requests[0]["tool_names"] == ["cc"]
    spec_text = str(COMMAND_TOOL_SPEC)
    assert "acme" not in spec_text and "session" not in spec_text and "/" not in spec_text


def test_bedrock_request_has_cache_point_one_after_tools_and_system_and_a_moving_one_on_the_newest_turn():
    model = _answering_model()
    _run(model)
    bedrock = bedrock_model_factory(AnalyzerSettings(model_kind="bedrock", aws_region="us-east-1"),
                                    session_factory=lambda **kw: mock.MagicMock(region_name=kw.get("region_name")))()
    from strands.tools.registry import ToolRegistry  # noqa: F401 - the spec is what the agent registers
    for request in model.requests:
        formatted = bedrock.format_request(request["messages"], [COMMAND_TOOL_SPEC],
                                            system_prompt_content=request["system_prompt_content"])
        assert formatted["system"][-1] == {"cachePoint": {"type": "default"}}
        assert formatted["toolConfig"]["tools"][0]["toolSpec"]["name"] == "cc"
        user_turns = [m for m in formatted["messages"] if m["role"] == "user"]
        assert any("cachePoint" in block for block in user_turns[-1]["content"])          # moving cache point 2
        assert not any("cachePoint" in block for m in user_turns[:-1] for block in m["content"])


# ---- commands --------------------------------------------------------------------------------------------------------

def test_a_quoting_error_is_answered_with_the_error_and_the_model_fixes_it():
    model = ScriptedModel(scripted_turns(
        ScriptedTurn(tool_calls=[("cc", {"command": 'sql "SELECT count(*) FROM sem.claims'})]),
        ScriptedTurn(tool_calls=[("cc", {"command": "frobnicate sem.claims"})]),
        cc("sql", SQL), cc("answer", "--sql", SQL, "--text", "Claims by payer.")))
    events = []
    answer = _run(model, observer=LiveTap(events.append))
    results = tool_results(model.requests[-1]["messages"])
    assert results[0].startswith("ERROR: could not split the command line: No closing quotation")
    assert results[1].startswith("ERROR: argument command: invalid choice: 'frobnicate'")
    assert answer.kind == "answer" and answer.columns == ["payer_name", "claims"] and answer.rows
    assert [e["type"] for e in events if e["type"].startswith("step.")][:2] == ["step.sql_running", "step.sql_result"]


def test_gold_lookups_are_gone_and_lossless_lookups_need_an_escalation():
    model = ScriptedModel(scripted_turns(cc("describe", "sem.claims"), cc("list"), cc("refuse", "nothing to do")))
    answer = _run(model)
    results = tool_results(model.requests[-1]["messages"])
    assert all(result.startswith("ERROR:") and "in your instructions" in result for result in results)
    assert answer.kind == "refusal"


def test_multi_question_ask_and_refusal_through_the_app():
    form = ('[{"header": "Measure", "question": "Count or dollars?", "options": [{"label": "Count", "description": '
            '"Claims"}, {"label": "Dollars", "description": "Billed charges"}]}, {"header": "Window", "question": '
            '"Which period?", "options": [{"label": "90 days", "description": "Recent"}, {"label": "All", '
            '"description": "Everything"}]}]')
    from api.tests.conftest import scripted_analyzer
    client = TestClient(create_app(settings=Settings(heartbeat_seconds=5),
                                   analyzer=scripted_analyzer(cc("ask", "Two things first:", "--questions", form))))
    _, evs = ask(client, "Denials?")
    assert evs[-1]["finishReason"] == "needs_input" and check(evs) == []
    part = next(p for p in parts(evs) if p["type"] == "clarification")
    assert [q["id"] for q in part["questions"]] == ["q1", "q2"] and part["question"] == "Two things first:"
    client = TestClient(create_app(settings=Settings(heartbeat_seconds=5),
                                   analyzer=scripted_analyzer(cc("refuse", "No patient names."))))
    _, evs = ask(client, "Give me patient names")
    assert evs[-1]["finishReason"] == "abstain" and check(evs) == []


def test_customers_are_isolated_whatever_the_command_says():
    from api.tests.conftest import scripted_analyzer
    first_claim = "SELECT MIN(claim_key) AS first_claim FROM sem.claims"
    client = TestClient(create_app(settings=Settings(heartbeat_seconds=5), analyzer=scripted_analyzer(
        cc("sql", "SELECT * FROM acme_labs.sem.claims"), cc("escalate", "need acme_labs data"),
        cc("answer", "--sql", first_claim, "--text", "First claim."))))
    _, evs = ask(client, "First claim?", headers=DX)
    table = next(p for p in parts(evs) if p["type"] == "table")
    assert table["rows"][0][0].startswith("AD")                        # acme_diagnostics' own data only
    _, evs = ask(client, "First claim?", headers=LABS)
    assert next(p for p in parts(evs) if p["type"] == "table")["rows"][0][0].startswith("AL")


# ---- Stop --------------------------------------------------------------------------------------------------------------

def test_stop_mid_run_cancels_before_the_next_command():
    cancelled = threading.Event()
    ran = []

    class StopAfterFirstCommand(LiveTap):
        def tool_call_finished(self, step_id, result_text):
            super().tool_call_finished(step_id, result_text)
            ran.append(result_text)
            cancelled.set()

    model = ScriptedModel(scripted_turns(cc("sql", SQL), cc("sql", SQL), cc("answer", "--sql", SQL, "--text", "x")))
    with pytest.raises(RunCancelled):
        _run(model, cancelled=cancelled, observer=StopAfterFirstCommand(lambda event: None, cancelled=cancelled))
    assert len(ran) == 1 and len(model.requests) <= 2


# ---- the Bedrock model and its settings ------------------------------------------------------------------------------

def _bedrock_settings(**overrides):
    values = dict(model_kind="bedrock", snapshot_path=None, paid_api_allowed=True, aws_region=None)
    values.update(overrides)
    return AnalyzerSettings(**values)


def test_defaults_are_the_apiservice_profile_and_the_haiku_inference_profile():
    settings = AnalyzerSettings()
    assert settings.aws_profile == "apiservice" and settings.strands_model_id == "us.anthropic.claude-haiku-5-5"
    assert settings.model_kind == "scripted" and not settings.paid


def test_boto3_session_uses_the_profile_and_its_region_or_the_override():
    factory = mock.MagicMock(return_value=mock.MagicMock(region_name="us-west-2"))
    assert aws_session("apiservice", None, factory).region_name == "us-west-2"
    factory.assert_called_once_with(profile_name="apiservice")
    factory = mock.MagicMock(return_value=mock.MagicMock(region_name="us-east-1"))
    aws_session("apiservice", "us-east-1", factory)
    factory.assert_called_once_with(profile_name="apiservice", region_name="us-east-1")
    with mock.patch("boto3.Session") as boto_session:
        boto_session.return_value.region_name = "us-east-1"
        model = bedrock_model_factory(_bedrock_settings())()
        boto_session.assert_called_once_with(profile_name="apiservice")
        assert model.config["model_id"] == "us.anthropic.claude-haiku-5-5"
        assert "additional_request_fields" not in model.config                  # effort low: no thinking


def test_startup_fails_fast(tmp_path):
    snapshot = tmp_path / "snap"
    snapshot.mkdir()
    (snapshot / "meta.json").write_text("{}")
    cases = [
        (_bedrock_settings(paid_api_allowed=False, snapshot_path=str(snapshot)), True, "CC_API_ALLOW_PAID_API"),
        (_bedrock_settings(), True, "CC_API_ANALYZER_SNAPSHOT"),
        (_bedrock_settings(snapshot_path=str(snapshot)), False, "api.sim_server"),
        (_bedrock_settings(snapshot_path=str(snapshot), strands_model_id=" "), True, "CC_STRANDS_MODEL_ID"),
        (_bedrock_settings(snapshot_path=str(tmp_path)), True, "not a bench snapshot"),
        (AnalyzerSettings(model_kind="openai"), False, "CC_API_ANALYZER_MODEL"),
        (AnalyzerSettings(time_zone="Mars/Olympus"), False, "CC_API_TIME_ZONE"),
    ]
    for settings, signed_in, message in cases:
        with pytest.raises(AnalyzerConfigurationError, match=message):
            settings.check(signed_in_customer_data=signed_in)
    no_region = mock.MagicMock(return_value=mock.MagicMock(region_name=None))
    with pytest.raises(AnalyzerConfigurationError, match="CC_AWS_REGION"):
        build_analyzer(_bedrock_settings(snapshot_path=str(snapshot)), signed_in_customer_data=True,
                       session_factory=no_region)
    with pytest.raises(AnalyzerConfigurationError, match="CC_API_ALLOW_PAID_API"):
        create_app(settings=Settings(analyzer=_bedrock_settings(paid_api_allowed=False)))
    assert PAID_SWITCH_VALUE == "i-understand-this-calls-a-paid-model-api"


def test_health_reports_the_model_and_the_paid_switch():
    analyzer = StrandsAnalyzer(_bedrock_settings(), lambda scenario=None: _answering_model(), "us.anthropic.x")
    body = TestClient(create_app(settings=Settings(heartbeat_seconds=5), analyzer=analyzer)).get("/api/health").json()
    assert body["analyzer"] == "strands" and body["model"] == "us.anthropic.x" and body["paidApis"] is True


def test_today_is_taken_in_the_configured_time_zone():
    analyzer = StrandsAnalyzer(AnalyzerSettings(time_zone="America/Los_Angeles"), None, "scripted",
                               clock=lambda: datetime(2026, 1, 1, 1, 0))
    assert analyzer.today() == date(2026, 1, 1)


# ---- the smoke command -----------------------------------------------------------------------------------------------

def test_smoke_command_prints_steps_and_the_answer(bench_snapshot, monkeypatch):
    from api import strands_smoke
    sql = "SELECT COUNT(*) AS claims FROM sem.claims WHERE is_latest_submission"
    model = ScriptedModel(scripted_turns(cc("sql", sql), cc("answer", "--sql", sql, "--text", "Claim count.")))

    def make_analyzer(settings, signed_in_customer_data):
        assert settings.model_kind == "bedrock" and signed_in_customer_data
        settings.model_kind = "scripted"
        return StrandsAnalyzer(settings, lambda scenario=None: model, "scripted")

    out = io.StringIO()
    assert strands_smoke.main(["How many claims?", "--snapshot", str(bench_snapshot)], out=out,
                              make_analyzer=make_analyzer) == 0
    text = out.getvalue()
    assert "[Running query]" in text and "[Final query]" in text and "answer: Claim count." in text
    assert "usage:" in text and "cache_read_input_tokens" in text
    assert B1_CONFIG.max_tool_calls == 12
