"""Adapter R2 (reasoning PHI policy), R4 (strategy → AnalyzerConfig) and R7 (analyst model shims).

Fixtures and synthetic data only: no model, network or paid API. analyzer/, analyst/ and bench/ are imported
read-only. Every PHI-looking value here is invented (see fixtures/analyzer_runs/phi_reasoning.json)."""
import json
import pathlib

import pytest

from analyzer.config import AnalyzerConfig
from analyzer.llm import Completion, ToolCall
from api.analyst_shim import SingleShotShim, TextRoleShim, ToolLoopShim
from api.analyzer_adapter import Recording, answer_from_bench, events_for_recording, run_to_events
from api.capabilities import STRATEGIES
from api.contract.conformance import check
from api.live import scrub
from api.reasoning_policy import DEFAULT_POLICY, POLICIES, ReasoningPolicy, phi_findings
from api.strategy_config import (FACTORS, FLAGS, GENERATIONS, all_grid_cells, grid_overrides, normalize_cell,
                                 strategy_config)
from api.tests.test_titles import _validator

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "analyzer_runs"
NAMES = sorted(p.stem for p in FIXTURES.glob("*.json"))
EVENT = _validator("StreamEvent")

# Synthetic PHI (names, MRNs, DOBs, member IDs, NPIs) as they appear in the phi_reasoning fixture.
PHI_LITERALS = ["Jane", "Testperson", "Ramona", "Fakename", "00123456", "01/02/1980", "1980-01-02",
                "XYZ123456789", "123456789", "1234567893"]
PHI_SAMPLES = {
    "name": ["Patient Jane Testperson", "Testperson, Jane", "Dr. Ramona Fakename", "member: Jane Q Testperson"],
    "mrn": ["MRN 00123456", "MRN: A1234567", "medical record number 77881234"],
    "dob": ["DOB 01/02/1980", "date of birth 1980-01-02", "born 3/4/77", "1980-01-02", "01/02/1980"],
    "member_id": ["member ID XYZ123456789", "subscriber id W12345678", "MBI 1EG4TE5MK73", "XYZ123456789"],
    "npi": ["NPI 1234567893", "npi: 1234567893", "1234567893"],
}


def replay(name, **kw):
    return events_for_recording(Recording.from_json(json.loads((FIXTURES / f"{name}.json").read_text())), **kw)


def dump(evs):
    return json.dumps(evs, ensure_ascii=False)


def reasoning_text(evs):
    return "".join(e["delta"] for e in evs if e["type"] == "reasoning.delta")


def valid(evs):
    for e in evs:
        EVENT.validate(e)
    assert check(evs) == []
    assert [e["seq"] for e in evs] == list(range(len(evs)))


# -- R2: reasoning policy -----------------------------------------------------------------------------------------

def test_default_policy_is_drop():
    assert DEFAULT_POLICY == "drop" and POLICIES == ("drop", "summary")
    with pytest.raises(ValueError):
        ReasoningPolicy(lambda e: None, "raw")


@pytest.mark.parametrize("label", sorted(PHI_SAMPLES))
def test_detector_flags_every_synthetic_phi_pattern(label):
    for sample in PHI_SAMPLES[label]:
        assert label in phi_findings(sample), (label, sample)


@pytest.mark.parametrize("text", ["Checking the claims view.", "Ran 3 queries (1 failed, 1 repaired).",
                                  "Net collection rate by payer for Q3 2024", "SELECT payer_name FROM sem.claims",
                                  "Synthetic Payer A paid $15,234.50 on 120 claims."])
def test_detector_passes_ordinary_analysis_text(text):
    assert phi_findings(text) == []


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("policy", POLICIES)
def test_every_fixture_is_valid_under_every_policy(name, policy):
    valid(replay(name, reasoning=policy))


def test_drop_removes_all_reasoning_and_no_phi_reaches_the_stream():
    evs = replay("phi_reasoning")
    valid(evs)
    assert not [e for e in evs if e["type"].startswith("reasoning.")]
    out = dump(evs)
    for literal in PHI_LITERALS:
        assert literal not in out, literal
    assert evs[-1]["finishReason"] == "stop"


def test_summary_is_built_from_labels_and_counts_only():
    evs = replay("phi_reasoning", reasoning="summary")
    valid(evs)
    text = reasoning_text(evs)
    assert text == ("Explored the data: sampled values. Ran 3 queries (1 failed, 1 repaired). "
                    "The last successful query returned one row.")
    assert phi_findings(text) == []
    assert [e["type"] for e in evs].count("reasoning.delta") == 1
    out = dump(evs)
    for literal in PHI_LITERALS:
        assert literal not in out, literal


@pytest.mark.parametrize("name", NAMES)
def test_summary_never_copies_model_text(name):
    rec = json.loads((FIXTURES / f"{name}.json").read_text())
    model_text = [c["completion"].get("text") for c in rec["calls"] if c["completion"].get("text")]
    text = reasoning_text(replay(name, reasoning="summary"))
    for t in model_text:
        assert t not in text
    assert phi_findings(text) == []


def test_summary_counts_candidates_escalations_and_checks():
    out = []
    pol = ReasoningPolicy(out.append, "summary")
    for k in ("k1", "k2", "k3"):
        pol({"type": "step.sql_running", "stepId": f"s_{k}", "sql": "SELECT 1", "candidateId": k})
    pol({"type": "step.sql_result", "stepId": "s_k1", "ok": True, "rowCount": 7})
    pol({"type": "escalation", "from": {"model": "a"}, "to": {"model": "b"}})
    pol({"type": "step.check", "stepId": "s_c", "verdict": "warn", "message": "Patient Jane"})
    pol({"type": "reasoning.delta", "partId": "p_1", "delta": PHI_TEXT})
    pol({"type": "reasoning.end", "partId": "p_1", "durationMs": 3})
    text = out[-2]["delta"]
    assert text == ("Ran 3 queries. Compared 3 candidate queries. The last successful query returned 7 rows. "
                    "Checks: 1 warn. Escalated to a larger model once.")
    assert out[-1]["type"] == "reasoning.end" and "Jane" not in dump(out[-2:])


def test_summary_fails_closed_when_the_guard_matches():
    out = []
    pol = ReasoningPolicy(out.append, "summary")
    pol._counts.summary = lambda: "Patient Jane Testperson"   # a summary that somehow picked up PHI
    pol({"type": "reasoning.delta", "partId": "p_1", "delta": "raw"})
    pol({"type": "reasoning.end", "partId": "p_1", "durationMs": 1})
    assert out == [] and pol.dropped_deltas == 1


def test_summary_ignores_unknown_probe_labels_and_free_text():
    out = []
    pol = ReasoningPolicy(out.append, "summary")
    pol({"type": "step.explore", "stepId": "s_1", "probe": "Jane Testperson", "summary": "MRN 00123456",
         "target": "XYZ123456789"})
    pol({"type": "reasoning.end", "partId": "p_1", "durationMs": 1})
    assert [e["type"] for e in out] == ["step.explore"]   # nothing countable → no reasoning part at all


def test_stop_mid_run_under_summary_still_closes_cleanly():
    evs = replay("phi_reasoning", reasoning="summary", cancel_after_call=2)
    valid(evs)
    assert evs[-1]["finishReason"] == "aborted"
    assert "Jane" not in dump(evs)


def test_scrub_masks_dates_ids_emails_and_quoted_values():
    msg = scrub("no match for 01/02/1980 near 'Jane Testperson' member XYZ123456789 mail j.t@example.org id 00123456")
    for literal in ["01/02/1980", "Jane", "XYZ123456789", "j.t@example.org", "00123456"]:
        assert literal not in msg
    assert scrub("Binder Error: Referenced column net_paid not found") == \
        "Binder Error: Referenced column net_paid not found"


def test_demo_app_path_is_unchanged():
    src = (ROOT / "api" / "app.py").read_text()
    assert "reasoning_policy" not in src and "ReasoningPolicy" not in src


# -- R4: strategy → AnalyzerConfig ---------------------------------------------------------------------------------

def test_grid_has_384_cells_and_known_factor_names():
    cells = all_grid_cells()
    assert len(cells) == 384 == len(GENERATIONS) * 2 ** len(FLAGS)
    assert len({json.dumps(c, sort_keys=True) for c in cells}) == 384
    assert set(FACTORS) == {"generation", *FLAGS}
    assert FLAGS == ("fewshot", "explore", "candidates3", "verifier", "repair", "assumptions", "multiagent")


def test_factor_names_match_the_research_grid_read_only():
    design = ROOT / "research" / "grid" / "design.json"
    if not design.exists():
        pytest.skip("research grid not checked out")
    rows = json.loads(design.read_text())
    for row in rows:
        assert set(row) - {"id"} == set(FACTORS)
        normalize_cell(row)
    sample = ROOT / "research" / "grid" / "C000.json"
    if sample.exists():
        normalize_cell(json.loads(sample.read_text())["config"])


@pytest.mark.parametrize("strategy", [s["id"] for s in STRATEGIES])
def test_every_cell_builds_a_valid_analyzer_config(strategy):
    for cell in all_grid_cells():
        if strategy == "candidates" and not cell["candidates3"]:
            with pytest.raises(ValueError, match="contradicts"):
                strategy_config(strategy, grid=cell)
            continue
        cfg = strategy_config(strategy, grid=cell)
        AnalyzerConfig(**cfg.overrides)
        AnalyzerConfig.from_technique(cfg.overrides)
        assert set(cfg.fidelity) == set(FACTORS)


@pytest.mark.parametrize("strategy", [s["id"] for s in STRATEGIES])
def test_strategies_without_grid_match_capabilities(strategy):
    spec = next(s for s in STRATEGIES if s["id"] == strategy)
    cfg = strategy_config(strategy)
    assert cfg.overrides == spec["config"] and cfg.fidelity == {}
    AnalyzerConfig(**cfg.overrides)


def test_model_choice_and_validation():
    assert strategy_config("tool_loop", "claude-sonnet-5-5").overrides == {"model": "claude-sonnet-5-5"}
    assert "model" not in strategy_config("tool_loop", "auto").overrides
    with pytest.raises(ValueError):
        strategy_config("nope")
    with pytest.raises(ValueError):
        strategy_config("cascade", "claude-haiku-5-5")      # cascade picks its own models
    with pytest.raises(ValueError):
        strategy_config("candidates", customer="acme_diagnostics")   # disabled by org policy


def test_factor_levels_map_to_documented_fields():
    base = {"generation": "direct_sql", **{f: False for f in FLAGS}}
    o, fid, _ = grid_overrides(base)
    assert o == {"enable_examples": False, "data_profile": False, "enable_lookup": False, "candidates": 1,
                 "verify": False, "self_correct": "none", "max_repairs": 0}
    assert set(fid.values()) <= {"exact", "approximate"}
    on = {"generation": "metric_spec", **{f: True for f in FLAGS}}
    o, fid, notes = grid_overrides(on)
    assert o["enable_metrics"] is True and o["candidates"] == 3 and o["candidate_selection"] == "execution_vote"
    assert o["self_correct"] == "errors+result_signals" and o["max_repairs"] == 2 and o["verify"] is True
    assert fid["multiagent"] == "unsupported" and fid["generation"] == "approximate"
    assert set(notes) == {k for k, v in fid.items() if v != "exact"}


def test_unsupported_factors_are_reported_or_rejected():
    cell = {"generation": "decompose_cte", **{f: False for f in FLAGS}, "multiagent": 1}
    cfg = strategy_config("tool_loop", grid=cell)
    assert cfg.unsupported == ("generation", "multiagent")
    with pytest.raises(ValueError, match="not supported"):
        strategy_config("tool_loop", grid=cell, strict=True)


def test_verifier_without_repair_is_flagged_as_inert():
    cell = {"generation": "direct_sql", **{f: False for f in FLAGS}, "verifier": True}
    assert "inert" in strategy_config("tool_loop", grid=cell).notes["verifier"]


@pytest.mark.parametrize("bad", [{"generation": "sql"}, {"fewshot": 2}, {"bogus": 1}, {"repair": 0.5}])
def test_bad_cells_are_rejected(bad):
    cell = {"generation": "direct_sql", **{f: False for f in FLAGS}, **bad}
    with pytest.raises(ValueError):
        normalize_cell(cell)
    with pytest.raises(ValueError, match="missing"):
        normalize_cell({"generation": "direct_sql"})


def test_mapping_is_pure():
    cell = {"generation": "direct_sql", **{f: True for f in FLAGS}}
    before = json.dumps(cell)
    a, b = strategy_config("tool_loop", grid=cell), strategy_config("tool_loop", grid=cell)
    assert a == b and json.dumps(cell) == before
    a.overrides["candidates"] = 5
    assert next(s for s in STRATEGIES if s["id"] == "tool_loop")["config"] == {}


# -- R7: analyst shims ---------------------------------------------------------------------------------------------

SQL = "SELECT payer_name, SUM(paid_amount) AS paid_amount FROM sem.claims GROUP BY 1"
PHI_TEXT = "Patient Jane Testperson MRN 00123456 DOB 01/02/1980 looks relevant."


class Inner:
    """Analyzer-protocol fake: per candidate, first a probe query, then the final answer. Records every call."""

    def __init__(self, final_sql=None):
        self.n: dict = {}
        self.calls: list = []
        self.final_sql = final_sql or {}

    def complete(self, **kw):
        c = kw["candidate_index"]
        self.calls.append((kw["model"], c))
        k = self.n[c] = self.n.get(c, 0) + 1
        if k == 1:
            return Completion(text=PHI_TEXT, tool_calls=[ToolCall("run_sql", {"sql": "SELECT COUNT(*) FROM sem.claims"},
                                                                  f"r{c}")], stop_reason="tool_use", usage={},
                              model=kw["model"])
        return Completion(text="", stop_reason="tool_use", usage={"input_tokens": 10, "output_tokens": 5},
                          model=kw["model"], tool_calls=[ToolCall("final_answer", {"sql": self.final_sql.get(c, SQL),
                                                                                    "answer": "Paid by payer."}, f"f{c}")])


class Tools:
    def describe(self):
        return "sem.claims(payer_name, paid_amount)"

    def sql(self, sql):
        if "COUNT" in sql:
            return ["n"], [(249,)]
        return ["payer_name", "paid_amount"], [("Synthetic Payer A", 10.0), ("Synthetic Payer B", 5.0)]


def _mc_run(inner_final=None, policy="drop"):
    from analyst.variants.multi_candidate.agent import MultiCandidateAgent
    holder = {}

    def run(tap):
        agent = MultiCandidateAgent(n_candidates=3, selection="majority")
        shim = holder["shim"] = ToolLoopShim(tap, agent.strategies)
        agent.llm = shim
        result = agent.answer("Paid by payer", Tools())
        answer = answer_from_bench(result, columns=["payer_name", "paid_amount"], model="scripted")
        answer.candidate_index = shim.candidate_for_sql(result.sql)
        return answer

    inner = Inner(inner_final)
    evs = run_to_events("Paid by payer", run, inner, model="scripted", multi_candidate=True, reasoning=policy)
    return evs, inner, holder["shim"]


@pytest.mark.parametrize("policy", POLICIES)
def test_multi_candidate_agent_through_the_shim_is_conformant(policy):
    evs, inner, shim = _mc_run(policy=policy)
    valid(evs)
    assert evs[-1]["finishReason"] == "stop"
    assert sorted({c for _, c in inner.calls}) == [0, 1, 2]
    runs = [e for e in evs if e["type"] == "step.sql_running"]
    assert {r["candidateId"] for r in runs} == {"k1", "k2", "k3"} and all(r["purpose"] == "candidate" for r in runs)
    assert "Jane" not in dump(evs) and "00123456" not in dump(evs)
    table = next(e["part"] for e in evs if e["type"] == "part" and e["part"]["type"] == "table")
    assert table["rowCount"] == 2 and [c["key"] for c in table["columns"]] == ["payer_name", "paid_amount"]
    if policy == "summary":
        assert "Compared 3 candidate queries." in reasoning_text(evs)


def test_tool_loop_shim_attaches_rows_to_the_chosen_candidate():
    other = "SELECT payer_name, SUM(paid_amount) AS paid_amount FROM sem.claims WHERE paid_amount > 0 GROUP BY 1"
    evs, _, shim = _mc_run({0: other})
    assert shim.candidate_for_sql(SQL) == 1 and shim.candidate_for_sql(other) == 0
    finals = {e["stepId"]: e["candidateId"] for e in evs if e["type"] == "step.sql_running" and e["label"] == "Final query"}
    with_rows = [e for e in evs if e["type"] == "step.sql_result" and e["stepId"] in finals and "rowCount" in e]
    assert len(with_rows) == 1 and finals[with_rows[0]["stepId"]] in {"k1", "k2"}
    valid(evs)


def test_tool_loop_shim_judges_get_their_own_index_and_types_roundtrip():
    from analyst.variants.multi_candidate.llm import LLMResponse, ToolCall as AToolCall
    inner = Inner()
    shim = ToolLoopShim(inner, ("direct", "grain_and_date"))
    resp = shim.complete(system="s", messages=[{"role": "user", "content": "q"}], tools=[], model="m",
                         strategy="grain_and_date")
    assert isinstance(resp, LLMResponse) and isinstance(resp.tool_calls[0], AToolCall)
    assert resp.tool_calls[0].call_id == "r1" and resp.text == PHI_TEXT
    shim.complete(system="s", messages=[], tools=[], model="m", strategy="__selector__")
    shim.complete(system="s", messages=[], tools=[], model="m", strategy="__verifier__")
    assert [c for _, c in inner.calls] == [1, 2, 3]
    with pytest.raises(ValueError):
        shim.complete(system="s", messages=[], tools=[], model="m", strategy="unknown")


def test_text_role_shim_maps_roles_to_indices_without_false_escalation():
    from analyst.approaches.a05_verifier.backend import ModelCompletion

    class TextInner:
        def __init__(self):
            self.calls = []

        def complete(self, **kw):
            self.calls.append(kw)
            return Completion(text=f"{kw['candidate_index']}:ok", tool_calls=[], stop_reason="end_turn", usage={},
                              model=kw["model"])

    def run(tap):
        shim = TextRoleShim(tap, {"analyst": "claude-haiku-5-5", "critic": "claude-sonnet-5-5"})
        a = shim.complete(role="analyst", system="s", user=PHI_TEXT, phase="draft")
        c = shim.complete(role="critic", system="s", user="check", phase="critique")
        assert isinstance(a, ModelCompletion) and a.text == "0:ok" and c.text == "1:ok"
        with pytest.raises(ValueError):
            shim.complete(role="planner", system="s", user="x", phase="p")
        return answer_from_bench(type("A", (), {"sql": SQL, "rows": [["Synthetic Payer A", 1.0]], "text": "Done.",
                                                "refused": False, "usage": {}, "trace": []})(),
                                 columns=["payer_name", "paid_amount"])

    inner = TextInner()
    evs = run_to_events("q", run, inner, model="claude-haiku-5-5", reasoning="summary")
    valid(evs)
    assert "escalation" not in [e["type"] for e in evs]
    assert [k["candidate_index"] for k in inner.calls] == [0, 1]
    assert inner.calls[0]["messages"] == [{"role": "user", "content": PHI_TEXT}]


@pytest.mark.parametrize("call,expect", [
    (ToolCall("final_answer", {"sql": SQL, "answer": "Paid by payer."}, "t1"), (SQL, "Paid by payer.", False)),
    (ToolCall("refuse", {"reason": "Patient identifiers are not available."}, "t1"),
     (None, "Patient identifiers are not available.", True)),
    (None, (None, "just text", False)),
])
def test_single_shot_shim(call, expect):
    from analyst.variants.context_retrieval.llm import LLMRequest, LLMResponse

    class One:
        def complete(self, **kw):
            assert kw["candidate_index"] == 0 and kw["messages"] == [{"role": "user", "content": "u"}]
            return Completion(text="just text" if call is None else "", tool_calls=[call] if call else [],
                              stop_reason="end_turn", usage={"input_tokens": 3}, model=kw["model"])

    resp = SingleShotShim(One(), "claude-haiku-5-5").complete(
        LLMRequest(question_id="q1", question="q", system="s", user="u", tools=[]))
    assert isinstance(resp, LLMResponse) and (resp.sql, resp.text, resp.refused) == expect
    assert resp.usage == {"input_tokens": 3}


def test_shims_are_not_wired_and_do_not_build_backends():
    src = (ROOT / "api" / "app.py").read_text()
    assert "analyst_shim" not in src and "strategy_config" not in src
    import ast
    tree = ast.parse((ROOT / "api" / "analyst_shim.py").read_text())
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
        {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | \
        {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names} | \
        {k.arg for n in ast.walk(tree) if isinstance(n, ast.Call) for k in n.keywords if k.arg}
    assert not used & {"build_real_llm", "AnthropicLLM", "BedrockLLM", "allow_paid_api", "analyzer_adapter"}
