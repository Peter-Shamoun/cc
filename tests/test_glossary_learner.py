"""Glossary versions and the validation gate. No model calls."""
import json

import pytest

from bench.agent_cli import PITFALLS, CliError, glossary_text
from bench.agents import GLOSSARY
from bench.glossary_learner import (
    Candidate, GateThresholds, LLMClientMissing, LLMProposer, LearnerError, ScriptedProposer,
    build_llm_prompt, contrasts_from_diffs, gate, read_log, run_learning_round,
)
from bench.glossary_versions import canonical_documents, load_version, render_document, write_canonical_versions


def test_canonical_versions_match_the_sandbox_strings(tmp_path):
    docs = {doc["version_id"]: doc for doc in canonical_documents()}
    assert render_document(docs["v000_basic"]) == GLOSSARY
    assert render_document(docs["v001_screen1_pitfalls"]) == GLOSSARY + "\n" + PITFALLS
    assert glossary_text("basic") == GLOSSARY
    assert glossary_text("pitfalls") == GLOSSARY + "\n" + PITFALLS
    assert glossary_text("version:v000_basic") == GLOSSARY
    assert glossary_text("version:v001_screen1_pitfalls") == GLOSSARY + "\n" + PITFALLS
    with pytest.raises(CliError):
        glossary_text("made-up")
    write_canonical_versions(tmp_path)
    assert render_document(load_version("v000_basic", tmp_path)) == GLOSSARY


def _contrast(effect, low, high, questions=20):
    return dict(effect=effect, ci95=[low, high], questions=questions)


def test_gate_accepts_on_effect_or_on_a_ci_that_clears_zero():
    assert gate({"haiku": _contrast(0.06, -0.01, 0.20)})["accept"] is True
    assert gate({"haiku": _contrast(0.02, 0.001, 0.04)})["accept"] is True
    assert gate({"haiku": _contrast(0.02, 0.0, 0.04)})["accept"] is False
    assert gate({"haiku": _contrast(0.01, -0.10, 0.10)})["accept"] is False
    both = GateThresholds(combine="both")
    assert gate({"haiku": _contrast(0.06, -0.01, 0.20)}, both)["accept"] is False
    assert gate({"haiku": _contrast(0.06, 0.01, 0.20)}, both)["accept"] is True
    mixed = {"haiku": _contrast(0.08, 0.01, 0.20), "sonnet": _contrast(0.01, -0.05, 0.04)}
    assert gate(mixed, GateThresholds(policy="all"))["accept"] is False
    assert gate(mixed, GateThresholds(policy="any"))["accept"] is True


def test_contrast_record_has_no_per_question_detail():
    contrasts = contrasts_from_diffs({"haiku": [1.0, 0.0, 1.0, 0.0]})
    assert set(contrasts["haiku"]) == {"effect", "ci95", "questions"}
    assert contrasts["haiku"]["questions"] == 4
    leaked = {"haiku": dict(contrasts["haiku"], diffs=[1.0, 0.0])}
    with pytest.raises(LearnerError, match="diffs"):
        gate(leaked)


def test_scripted_round_logs_accept_and_reject_and_stores_train_ids_only(tmp_path):
    directory = tmp_path / "glossaries"
    write_canonical_versions(directory)
    failures = [
        dict(question_id="q_grain", error_type="grain", question="How many distinct claims were submitted?"),
        dict(question_id="q_round", error_type="rounding", question="Round the paid ratio to two decimals."),
    ]

    def contrast(trial):
        last = trial["sections"][-1]["entries"][-1]["text"]
        if last.startswith("- Count at the grain"):
            return {"haiku": _contrast(0.08, 0.01, 0.20)}
        return {"haiku": _contrast(0.01, -0.20, 0.05)}

    log_path = tmp_path / "decisions.jsonl"
    decisions = run_learning_round("v000_basic", failures, ScriptedProposer(), contrast,
                                   directory=directory, log_path=log_path)
    assert [d.accept for d in decisions] == [True, False]
    assert decisions[1].parent_id == decisions[0].version_id
    logged = read_log(log_path)
    assert [row["accept"] for row in logged] == [True, False]
    child = load_version(decisions[0].version_id, directory)
    learned = [entry for entry in child["sections"][-1]["entries"] if entry["provenance"].get("origin") == "learned"]
    assert learned[0]["provenance"]["train_failure_ids"] == ["q_grain"]
    blob = json.dumps(child)
    assert "How many distinct claims" not in blob
    assert "Round the paid ratio" not in blob
    assert not list(directory.glob("trial.json"))


def test_question_specific_candidate_never_reaches_validation(tmp_path):
    directory = tmp_path / "glossaries"
    write_canonical_versions(directory)
    question = "acme outpatient laboratory resubmitted denied claims yesterday morning"
    candidate = Candidate("copied", "- acme outpatient laboratory resubmitted denied claims need a special rule",
                          train_failure_ids=("q_one",))
    calls = []

    def contrast(trial):
        calls.append(trial)
        return {"haiku": _contrast(0.20, 0.10, 0.30)}

    decisions = run_learning_round(
        "v000_basic", [dict(question_id="q_one", error_type="other", question=question)],
        ScriptedProposer([candidate]), contrast, directory=directory, log_path=tmp_path / "log.jsonl")
    assert calls == []
    assert decisions[0].accept is False
    assert decisions[0].reason.startswith("rejected before validation")
    assert list(directory.glob("v_*.json")) == []


def test_llm_proposer_uses_only_an_injected_client():
    with pytest.raises(LLMClientMissing):
        LLMProposer().propose([], "glossary")
    seen = {}

    def complete(prompt):
        seen["prompt"] = prompt
        return json.dumps([{"text": "- Aggregate the many side of a join before attaching claim amounts.",
                            "error_type": "join"}])

    failures = [dict(question_id="q_train_only", error_type="join", question="train only wording", note="missed the join")]
    candidates = LLMProposer(complete).propose(failures, "glossary")
    assert candidates[0].text.startswith("- ")
    assert "q_train_only" in seen["prompt"]
    assert "train only wording" in seen["prompt"]
    prompt = build_llm_prompt(failures, "glossary")
    assert "validation" not in prompt
