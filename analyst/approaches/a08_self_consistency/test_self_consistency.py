"""A08 self-consistency: normalizer, vote, confidence, and the eval adapter.

No model calls. The adapter test re-executes SQL against a tiny snapshot.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import pytest

from analyst.approaches.a08_self_consistency.normalize import canonical_key
from analyst.approaches.a08_self_consistency.protocol import (
    ATTEMPT_COUNT,
    CONFIG_NAME,
    base_technique_config,
    configs_for_eval_subagents,
    cost_multiplier,
    prompt_shape_configs,
)
from analyst.approaches.a08_self_consistency.vote import Attempt, calibrate, decide
from bench.compare import match


SPEC = {"tol": 0.005}


def _write_toy_parquet(path: Path, rows: list[tuple[str, int]]) -> None:
    connection = duckdb.connect()
    connection.execute("CREATE TABLE toy (payer VARCHAR, n INTEGER)")
    connection.executemany("INSERT INTO toy VALUES (?, ?)", rows)
    connection.execute(f"COPY toy TO '{path}' (FORMAT PARQUET)")


def _rows(*attempts: tuple | list) -> list[Attempt]:
    return [Attempt(attempt_index=index, status="executed", rows=tuple(tuple(row) for row in rows), sql=f"sql-{index}")
            for index, rows in enumerate(attempts)]


def _executed(index: int, rows, sql: str | None = None) -> Attempt:
    return Attempt(attempt_index=index, status="executed", rows=tuple(tuple(row) for row in rows), sql=sql or f"sql-{index}")


def test_column_order_and_case_share_a_key_swapped_measures_do_not():
    payer_rate = [("A", 0.5), ("B", 0.2)]
    reordered = [(0.2, "b"), (0.5, "a")]
    assert canonical_key(payer_rate, SPEC) == canonical_key(reordered, SPEC)
    assert canonical_key([(225, 141)], SPEC) != canonical_key([(141, 225)], SPEC)
    assert canonical_key([("80053", 10)], SPEC) == canonical_key([(10, "80053")], SPEC)


def test_rounding_bins_within_tolerance_and_keeps_a_real_difference():
    loose = {"tol": 0.01}
    assert canonical_key([(1.0,)], loose) == canonical_key([(1.004,)], loose)
    assert canonical_key([(1.0,)], loose) != canonical_key([(1.02,)], loose)
    assert canonical_key([(0.25,)], SPEC) == canonical_key([("0.250",)], SPEC)
    assert canonical_key([(1,)], SPEC) == canonical_key([(True,)], SPEC)
    assert canonical_key([(date(2026, 7, 1), 5)], SPEC) == canonical_key([("2026-07", 5)], SPEC)


def test_percent_and_sign_follow_the_question_spec():
    percent = {"tol": 0.0005, "percent_cols": [0]}
    assert canonical_key([(0.25,)], percent) == canonical_key([(25.0,)], percent)
    assert canonical_key([(0.25,)], SPEC) != canonical_key([(25.0,)], SPEC)
    rate_and_count = {"tol": 0.0005, "percent_cols": [1]}
    assert canonical_key([("Z", 4.65, 86)], rate_and_count) == canonical_key([("Z", 0.0465, 86)], rate_and_count)
    # An integer beside another number is a count, not a percent, so it is not divided by 100.
    assert canonical_key([("Z", 0.25, 25)], rate_and_count) != canonical_key([("Z", 0.25, 0.25)], rate_and_count)
    signed = {"tol": 0.01, "sign_cols": [0]}
    assert canonical_key([(-5.0,)], signed) == canonical_key([(5.0,)], signed)
    assert canonical_key([(-5.0,)], SPEC) != canonical_key([(5.0,)], SPEC)


def test_topn_tie_breaks_share_a_key_and_a_missing_winner_does_not():
    spec = {"order": "topn", "n": 5, "metric_col": 1, "tol": 0.005}
    gold = [("a", 10)] + [(f"p{i}", 5) for i in range(13)]
    first_ties = [("a", 10)] + [(f"p{i}", 5) for i in range(4)]
    other_ties = [("a", 10)] + [(f"p{i}", 5) for i in range(8, 12)]
    reordered = [(10, "a")] + [(5, f"p{i}") for i in range(4)]
    missing_winner = [(f"p{i}", 5) for i in range(5)]
    assert match(gold, first_ties, spec)[0]
    assert match(gold, other_ties, spec)[0]
    assert not match(gold, missing_winner, spec)[0]
    assert canonical_key(first_ties, spec) == canonical_key(other_ties, spec)
    assert canonical_key(first_ties, spec) == canonical_key(reordered, spec)
    assert canonical_key(first_ties, spec) != canonical_key(missing_winner, spec)


def test_strict_order_keeps_row_sequence_and_extra_columns_do_not_collapse():
    rows = [("a", 1), ("b", 2)]
    assert canonical_key(rows, {"order": "strict"}) != canonical_key(list(reversed(rows)), {"order": "strict"})
    assert canonical_key(rows, SPEC) == canonical_key(list(reversed(rows)), SPEC)
    assert canonical_key([("A", 0.5)], SPEC) != canonical_key([("A", "extra", 0.5)], SPEC)


def test_normalizer_agrees_with_the_matcher_on_cosmetic_differences():
    pairs = [
        ([("A", 0.5), ("B", 0.2)], [(0.2, "b"), (0.5, "a")], {}),
        ([(0.25,)], [(0.2504,)], {"tol": 0.005}),
        ([(date(2026, 7, 1), 5.0)], [("2026-07", 5)], {}),
        ([(1,)], [(True,)], {}),
        ([(0.25,)], [(25.0,)], {"percent_cols": [0]}),
        ([(-5.0,)], [(5.0,)], {"sign_cols": [0]}),
    ]
    for gold, candidate, spec in pairs:
        assert match(gold, candidate, spec)[0]
        assert canonical_key(gold, spec) == canonical_key(candidate, spec)
    assert not match([(225, 141)], [(141, 225)], {})[0]


def test_unanimous_vote_accepts_with_full_margin():
    decision = decide(_rows(*([[("A", 1)]] * 5)), gold_rows=[("A", 1)])
    assert decision["action"] == "accept"
    assert decision["confidence"] == 1.0
    assert decision["margin"] == 1.0
    assert decision["winner_votes"] == 5
    assert decision["voted_correct"] is True
    assert decision["protocol_complete"] is True


def test_four_to_one_accepts_and_three_to_two_escalates():
    majority = [("A", 1)]
    minority = [("B", 9)]
    four = decide(_rows(majority, majority, majority, majority, minority), gold_rows=majority)
    assert four["action"] == "accept"
    assert four["margin"] == 0.6
    assert four["confidence"] == 0.6
    assert four["chosen_attempt_index"] == 0

    split = decide(_rows(majority, majority, majority, minority, minority), gold_rows=majority)
    assert split["action"] == "escalate"
    assert split["margin"] == 0.2
    assert split["has_strict_majority"] is True
    assert split["voted_correct"] is True
    assert "cascade" in split["reason"]

    three_way = decide(_rows(majority, majority, majority, [("C", 3)], [("D", 4)]), gold_rows=majority)
    assert three_way["action"] == "escalate"
    assert three_way["margin"] == 0.4
    assert three_way["cluster_sizes"] == [3, 1, 1]


def test_tied_plurality_abstains_and_errors_are_not_ballots():
    tie = decide(_rows([("A", 1)], [("A", 1)], [("B", 2)], [("B", 2)], [("C", 3)]), gold_rows=[("A", 1)])
    assert tie["action"] == "abstain"
    assert tie["confidence"] == 0.0
    assert tie["chosen_attempt_index"] is None
    assert tie["voted_correct"] is False
    assert tie["any_attempt_correct"] is True

    attempts = [
        _executed(0, [("A", 1)]),
        _executed(1, [("A", 1)]),
        _executed(2, [("A", 1)]),
        Attempt(attempt_index=3, status="error", error="Binder Error"),
        Attempt(attempt_index=4, status="error", error="Binder Error"),
    ]
    crashed = decide(attempts, gold_rows=[("A", 1)])
    assert crashed["ballots"] == 3
    assert crashed["margin"] == 0.6
    assert crashed["action"] == "accept"
    assert crashed["non_ballots"][0]["status"] == "error"


def test_incomplete_schedule_cannot_accept_and_a_single_attempt_abstains():
    four = decide(_rows(*([[("A", 1)]] * 4)), gold_rows=[("A", 1)])
    assert four["protocol_complete"] is False
    assert four["margin"] == 0.8
    assert four["action"] == "escalate"

    one = decide(_rows([("A", 1)]), gold_rows=[("A", 1)])
    assert one["action"] == "abstain"
    assert one["has_strict_majority"] is False


def test_unanimous_wrong_answer_has_full_confidence_and_is_not_correct():
    decision = decide(_rows(*([[("wrong", 1)]] * 5)), gold_rows=[("right", 2)])
    assert decision["action"] == "accept"
    assert decision["confidence"] == 1.0
    assert decision["voted_correct"] is False
    assert decision["any_attempt_correct"] is False


def test_weak_majority_keeps_the_upper_bound_visible():
    right = [("right", 1)]
    wrong = [("wrong", 2)]
    decision = decide(_rows(wrong, wrong, wrong, right, right), gold_rows=right)
    assert decision["action"] == "escalate"
    assert decision["voted_correct"] is False
    assert decision["any_attempt_correct"] is True


def test_refusals_vote_as_one_answer_and_match_an_expect_refusal_question():
    attempts = [Attempt(attempt_index=index, status="refusal", text=f"reason {index}") for index in range(5)]
    decision = decide(attempts, expect_refusal=True)
    assert decision["action"] == "accept"
    assert decision["chosen_status"] == "refusal"
    assert decision["voted_correct"] is True
    assert decision["cluster_sizes"] == [5]


def test_margin_bins_track_accuracy_and_still_count_unanimous_mistakes():
    right = [("right", 1)]
    wrong = [("wrong", 2)]
    panel = []
    for _ in range(8):
        panel.append(decide(_rows(*([right] * 5)), gold_rows=right))
    for _ in range(2):
        panel.append(decide(_rows(*([wrong] * 5)), gold_rows=right))
    for _ in range(8):
        panel.append(decide(_rows(wrong, wrong, wrong, right, right), gold_rows=right))
    for _ in range(2):
        panel.append(decide(_rows(right, right, right, wrong, wrong), gold_rows=right))
    report = calibrate(panel)
    by_bin = {row["bin"]: row for row in report["bins"]}
    assert by_bin["accept"]["questions"] == 10
    assert by_bin["accept"]["accuracy"] == 0.8
    assert by_bin["escalate"]["questions"] == 10
    assert by_bin["escalate"]["accuracy"] == 0.2
    assert report["accuracy_gap_high_minus_low"] == 0.6
    assert report["monotonic_in_margin"] is True
    assert report["unanimous_wrong"] == 2
    assert report["selective_accuracy_on_accept"] == 0.8
    # Escalated questions are not shipped, so policy accuracy is only the accepted hits.
    assert report["accuracy_counting_abstain_and_escalate_wrong"] == pytest.approx(8 / 20)


def test_correlated_panel_is_unanimous_and_wrong():
    wrong = [("wrong", 2)]
    panel = [decide(_rows(*([wrong] * 5)), gold_rows=[("right", 1)]) for _ in range(4)]
    report = calibrate(panel)
    assert report["unanimous_wrong"] == 4
    assert report["bins"][2]["accuracy"] == 0.0
    assert all(row["confidence"] == 1.0 and row["voted_correct"] is False for row in panel)


def test_repeat_config_is_one_legal_config_and_prompt_shapes_are_five():
    from bench.agent_cli import DEFAULT_TECHNIQUE_CONFIG

    single = configs_for_eval_subagents()
    assert list(single) == [CONFIG_NAME]
    assert single[CONFIG_NAME] == base_technique_config()
    assert single[CONFIG_NAME]["glossary"] == "basic"
    assert single[CONFIG_NAME]["effort"] == "low"

    diverse = configs_for_eval_subagents(diverse_prompts=True)
    assert len(diverse) == ATTEMPT_COUNT
    assert len(prompt_shape_configs()) == ATTEMPT_COUNT
    assert {config["context_style"] for config in diverse.values()} == {"goal", "procedural"}
    assert all(config["glossary"] == "basic" for config in diverse.values())
    for config in list(diverse.values()) + list(single.values()):
        assert set(config) <= set(DEFAULT_TECHNIQUE_CONFIG)

    cost = cost_multiplier()
    assert cost["model_calls"] == 5
    assert cost["output_tokens"] == 5
    assert cost["input_tokens_if_prefix_cached"] == 1.4
    assert cost["selector_model_calls"] == 0


def test_create_session_accepts_the_configs(tmp_path, monkeypatch):
    import bench.agent_session as session_module

    monkeypatch.setattr(session_module, "SESSIONS_ROOT", tmp_path)
    case = {"id": "q_probe", "question": "how many claims were submitted"}
    for config in configs_for_eval_subagents(diverse_prompts=True).values():
        session_module.create_session(tmp_path, case, config, "haiku", "a08-legal")
    session_module.create_session(tmp_path, case, base_technique_config(), "sonnet", "a08-legal-base")


def test_vote_run_reexecutes_and_clusters_column_order(tmp_path, monkeypatch):
    import bench.agent_session as session_module
    from analyst.approaches.a08_self_consistency.adapter import main, vote_run, write_configs

    snapshot = tmp_path / "snapshot"
    data_dir = snapshot / "data" / "sem"
    data_dir.mkdir(parents=True)
    _write_toy_parquet(data_dir / "sem.toy.parquet", [("A", 10), ("B", 5)])
    (snapshot / "meta.json").write_text(json.dumps({
        "dataset_id": "toy", "customer": "acme_labs", "relations": ["sem.toy"],
    }))
    sessions_root = tmp_path / "sessions"
    monkeypatch.setattr(session_module, "SESSIONS_ROOT", sessions_root)
    run_dir = sessions_root / "a08run"
    run_dir.mkdir(parents=True)
    sqls = [
        "SELECT payer, n FROM sem.toy",
        "SELECT payer, n FROM sem.toy",
        "SELECT payer, n FROM sem.toy",
        "SELECT n, payer FROM sem.toy",
        "SELECT payer, n + 9 AS n FROM sem.toy",
    ]
    entries = []
    for index, sql in enumerate(sqls):
        session_dir = run_dir / f"q_toy-haiku-{index}"
        session_dir.mkdir()
        (session_dir / "session.json").write_text(json.dumps({
            "question_id": "q_toy", "question": "counts by payer", "escalate": False,
            "technique_config": base_technique_config(), "model": "haiku",
            "snapshot_path": str(snapshot),
        }))
        (session_dir / "final.json").write_text(json.dumps({
            "kind": "answer", "sql": sql, "text": "counted", "via": "sql",
        }))
        entries.append({
            "session": str(session_dir), "model": "haiku", "config": CONFIG_NAME,
            "question_id": "q_toy", "repeat": index, "effort": "low", "prompt": "ignored",
        })
    (run_dir / "manifest.json").write_text(json.dumps({
        "run": "a08run", "snapshot": str(snapshot),
        "configs": configs_for_eval_subagents(), "entries": entries,
    }))

    report = vote_run("a08run", sessions_root=sessions_root)
    question = report["questions"][0]
    assert question["action"] == "accept"
    assert question["margin"] == 0.6
    assert question["cluster_sizes"] == [4, 1]
    assert question["chosen_sql"] == "SELECT payer, n FROM sem.toy"
    assert question["ballots"] == 5
    saved = json.loads((run_dir / "a08_vote.json").read_text())
    assert saved["approach"] == "a08_self_consistency"
    assert saved["cost_multiplier"]["model_calls"] == 5
    assert saved["calibration"]["questions_with_gold"] == 0

    config_path = tmp_path / "a08.json"
    assert main(["write-configs", str(config_path)]) == 0
    written = json.loads(config_path.read_text())
    assert written == configs_for_eval_subagents()
    diverse_path = tmp_path / "a08-diverse.json"
    write_configs(diverse_path, diverse_prompts=True)
    assert len(json.loads(diverse_path.read_text())) == 5


def test_ungrounded_sql_and_a_failed_query_do_not_vote(tmp_path):
    from analyst.approaches.a08_self_consistency.adapter import _load_attempt

    snapshot = tmp_path / "snapshot"
    data_dir = snapshot / "data" / "sem"
    data_dir.mkdir(parents=True)
    _write_toy_parquet(data_dir / "sem.toy.parquet", [("A", 1)])
    (snapshot / "meta.json").write_text(json.dumps({"relations": ["sem.toy"]}))
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / "session.json").write_text(json.dumps({
        "snapshot_path": str(snapshot), "escalate": False,
    }))
    (session_dir / "final.json").write_text(json.dumps({"kind": "answer", "sql": "SELECT 1", "text": "no"}))
    loaded = _load_attempt(0, {"session": str(session_dir), "config": CONFIG_NAME, "repeat": 0})
    assert loaded.status == "ungrounded"

    (session_dir / "final.json").write_text(json.dumps({
        "kind": "answer", "sql": "SELECT * FROM sem.missing", "text": "no",
    }))
    failed = _load_attempt(1, {"session": str(session_dir), "config": CONFIG_NAME, "repeat": 1})
    assert failed.status == "error"
