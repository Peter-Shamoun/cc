"""Bundles B0-B10: every parameter accounted for, configs prepare() accepts, orchestration decisions, smoke."""

import contextlib
import io
import json
from pathlib import Path

import pytest

from analyzer import cli
from analyzer.bundles import (
    B0_LEVELS,
    BUNDLES,
    PARAMETER_NAMES,
    PARAMETER_SETTINGS,
    RUNNABLE_ORDER,
    configs_document,
    orchestration_document,
)
from analyzer.config import AnalyzerConfig
from analyzer.orchestration import DECISION_RULES, next_actions
from analyzer.smoke_bundles import play_answerer, play_role, run_smoke, smoke_questions
from bench.agent_cli import DEFAULT_TECHNIQUE_CONFIG


def test_every_bundle_accounts_for_all_26_parameters():
    """A level that differs from B0 must move one of its settings, be an orchestration role, or be listed as an
    approximation. Nothing is dropped silently."""
    assert set(BUNDLES) == {f"B{i}" for i in range(11)} and set(RUNNABLE_ORDER) == set(BUNDLES)
    assert set(PARAMETER_NAMES) == set(B0_LEVELS) == set(PARAMETER_SETTINGS) == set(range(1, 27))
    for bundle in BUNDLES.values():
        assert set(bundle.parameter_levels) == set(range(1, 27)), bundle.bundle_id
        assert set(bundle.approximations) <= set(range(1, 27))
        technique = {**DEFAULT_TECHNIQUE_CONFIG, **bundle.technique_config}
        for number, level in bundle.parameter_levels.items():
            if level == B0_LEVELS[number]:
                continue
            settings = PARAMETER_SETTINGS[number]
            moved = any(name != "orchestration" and technique[name] != DEFAULT_TECHNIQUE_CONFIG[name]
                        for name in settings)
            orchestrated = "orchestration" in settings and bundle.orchestration is not None
            assert moved or orchestrated or number in bundle.approximations, (bundle.bundle_id, number, level)


def test_waiting_bundles_are_the_playbook_ones():
    assert {bundle_id for bundle_id, bundle in BUNDLES.items() if bundle.waiting_on} == {"B1", "B10"}
    assert all(BUNDLES[b].technique_config.get("playbook") for b in ("B1", "B10"))


def test_configs_are_legal_technique_settings():
    for bundle_id, technique in configs_document().items():
        assert set(technique) <= set(DEFAULT_TECHNIQUE_CONFIG), bundle_id
        config = AnalyzerConfig.from_technique({**DEFAULT_TECHNIQUE_CONFIG, **technique})
        assert (config.orchestration_bundle is not None) == (BUNDLES[bundle_id].orchestration is not None)
        if config.orchestration_bundle:
            assert config.orchestration_bundle == bundle_id
    assert configs_document(["B0"]) == {"B0": {"effort": "low", "self_correct": "none", "max_repairs": 0}}


def test_orchestration_specs_are_well_formed():
    for bundle_id, bundle in BUNDLES.items():
        spec = bundle.orchestration
        if spec is None:
            assert orchestration_document(bundle_id) is None
            continue
        assert spec.decision_rule in DECISION_RULES
        names = [role.role_name for role in spec.roles]
        assert len(names) == len(set(names))
        assert {role.kind for role in spec.roles} <= {"answerer", "planner", "verifier", "selector"}
        assert {role.model for role in spec.roles} <= {"haiku", "sonnet"}
        assert set(orchestration_document(bundle_id)) >= {"roles", "decision_rule", "driver", "contract"}


# ---- decision rules with scripted roles ---------------------------------------------------------------------

def _run(session, *args):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        return cli.main(["--session", str(session), *args]), buffer.getvalue()


def _scored_session(bench_snapshot, bundle_id, question):
    from bench import eval_subagents
    manifest = eval_subagents.prepare(f"pytest-{bundle_id}", configs_document([bundle_id]), ["haiku"], 1,
                                      only=question["id"], snapshot_path=bench_snapshot, agent="analyzer",
                                      question_split="train")
    entry = json.loads(Path(manifest).read_text())["entries"][0]
    assert entry["orchestration"]["bundle"] == bundle_id and "analyzer.orchestration next" in entry["prompt"]
    return Path(entry["session"])


@pytest.fixture
def train_question(bench_snapshot):
    return smoke_questions(bench_snapshot, 1)[0]


ALTERNATIVE_SQL = "SELECT count(*) AS n FROM sem.claims WHERE is_latest_submission"


def test_b2_disagreement_goes_to_the_selector(bench_snapshot, sessions_root, train_question):
    scored = _scored_session(bench_snapshot, "B2", train_question)
    assert _run(scored, "briefing")[0] == 1                       # the scored session is never briefed directly
    step = next_actions(scored)
    assert [a["role"] for a in step["actions"]] == ["candidate_direct", "candidate_query_plan",
                                                    "candidate_divide_and_conquer"]
    answers = {"candidate_direct": train_question["gold_sql"], "candidate_query_plan": ALTERNATIVE_SQL,
               "candidate_divide_and_conquer": train_question["gold_sql"]}
    for action in step["actions"]:
        play_answerer(Path(action["session"]), answers[action["role"]])
    step = next_actions(scored)
    assert {a["role"] for a in step["actions"]} == {"verifier_direct", "verifier_query_plan",
                                                    "verifier_divide_and_conquer"}
    for action in step["actions"]:
        play_role(action, "")
    step = next_actions(scored)
    assert [(a["role"], a["model"]) for a in step["actions"]] == [("selector", "sonnet")]
    selector = Path(step["actions"][0]["session"])
    briefing = _run(selector, "briefing")[1]
    assert "Candidate a" in briefing and "Candidate b" in briefing and ALTERNATIVE_SQL in briefing
    assert _run(selector, "select", "b", "--reason", "scripted")[0] == 0
    done = next_actions(scored)
    assert done["status"] == "done" and done["decision"]["chosen_role"] == "candidate_query_plan"
    assert json.loads((scored / "final.json").read_text())["sql"] == ALTERNATIVE_SQL
    assert next_actions(scored) == done                            # idempotent once decided


def test_b2_retries_a_failed_candidate_at_high_effort(bench_snapshot, sessions_root, train_question):
    scored = _scored_session(bench_snapshot, "B2", train_question)
    step = next_actions(scored)
    for action in step["actions"]:
        if action["role"] != "candidate_direct":                  # the direct candidate records nothing
            play_answerer(Path(action["session"]), train_question["gold_sql"])
    step = next_actions(scored)
    assert [(a["role"], a["effort"]) for a in step["actions"]] == [("retry_high_effort_direct", "high")]
    play_answerer(Path(step["actions"][0]["session"]), train_question["gold_sql"])
    step = next_actions(scored)
    assert "verifier_direct" in {a["role"] for a in step["actions"]}
    for action in step["actions"]:
        play_role(action, "")
    done = next_actions(scored)
    assert done["decision"]["agreement"] and "retry_high_effort_direct" in done["decision"]["candidates"]


def test_b8_escalates_low_confidence_to_sonnet(bench_snapshot, sessions_root, train_question):
    scored = _scored_session(bench_snapshot, "B8", train_question)
    primary = Path(next_actions(scored)["actions"][0]["session"])
    assert _run(primary, "answer", "--sql", ALTERNATIVE_SQL, "--confidence", "low")[0] == 0
    step = next_actions(scored)
    assert [(a["role"], a["model"]) for a in step["actions"]] == [("escalation_sonnet", "sonnet")]
    play_answerer(Path(step["actions"][0]["session"]), train_question["gold_sql"])
    done = next_actions(scored)
    assert done["decision"]["chosen_role"] == "escalation_sonnet" and done["decision"]["trigger"]["fired"] == [
        "low_confidence"]


def test_b3_route_disagreement_keeps_the_compiler_route_at_low_confidence(bench_snapshot, sessions_root,
                                                                          train_question):
    scored = _scored_session(bench_snapshot, "B3", train_question)
    step = next_actions(scored)
    sql = {"route_compiler": train_question["gold_sql"], "route_free_sql": ALTERNATIVE_SQL}
    for action in step["actions"]:
        play_answerer(Path(action["session"]), sql[action["role"]])
    done = next_actions(scored)
    assert done["decision"]["chosen_role"] == "route_compiler"
    assert done["decision"]["routes_agree"] is False and done["decision"]["low_confidence"] is True


def test_b9_planner_refusal_is_the_answer(bench_snapshot, sessions_root, train_question):
    scored = _scored_session(bench_snapshot, "B9", train_question)
    planner = Path(next_actions(scored)["actions"][0]["session"])
    assert "You are the planner" in _run(planner, "briefing")[1]
    assert _run(planner, "refuse", "asks for patient names")[0] == 0
    done = next_actions(scored)
    assert done["decision"]["chosen_role"] == "planner"
    assert json.loads((scored / "final.json").read_text())["kind"] == "refusal"


def test_b9_executor_sees_the_plan(bench_snapshot, sessions_root, train_question):
    scored = _scored_session(bench_snapshot, "B9", train_question)
    play_role(next_actions(scored)["actions"][0], "")
    executor = next_actions(scored)["actions"][0]
    assert executor["role"] == "executor" and executor["model"] == "haiku"
    assert "Plan written for this question by a planner" in _run(Path(executor["session"]), "briefing")[1]


# ---- smoke: every bundle, two TRAIN questions, no model -------------------------------------------------------

def test_smoke_every_bundle_on_two_train_questions(bench_snapshot, tmp_path):
    results = run_smoke(list(RUNNABLE_ORDER), 2, bench_snapshot, tmp_path / "smoke")
    assert len(results) == 2 * len(BUNDLES) and all(result["correct"] for result in results)
