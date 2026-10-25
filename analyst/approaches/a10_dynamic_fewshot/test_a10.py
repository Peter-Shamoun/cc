"""A10 retriever, leakage guard, prompt, and eval_subagents adapter."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from analyst.approaches.a10_dynamic_fewshot.adapter import attach_to_manifest, technique_config
from analyst.approaches.a10_dynamic_fewshot.leakage import (
    HELD_OUT_QUESTION_SET,
    LeakageError,
    SplitAssignment,
    exclusion_reason,
    sql_skeleton,
)
from analyst.approaches.a10_dynamic_fewshot.prompts import render_prompt
from analyst.approaches.a10_dynamic_fewshot.retriever import ExampleIndex, RetrievedExample, retrieve

ROOT = Path(__file__).resolve().parents[3]


def _question(question_id, text, sql=None, **extra):
    if sql is None:
        sql = f"SELECT count(*) AS c_{question_id} FROM sem.claims"
    row = {"id": question_id, "question": text, "gold_sql": sql}
    row.update(extra)
    return row


def _assignment(labels, **kwargs):
    return SplitAssignment.from_mapping(labels, **kwargs)


def _prompt(question_text):
    return (
        "You are ClaimClarity, an analytics assistant.\n\n"
        "Commands:\n"
        "python3 bench/agent_cli.py --session /tmp/s sql \"SELECT ...\"\n\n"
        f"User question: {question_text}\n"
    )


def test_leakage_guard():
    """Every pool exclusion the approach promises, including the held-out 27."""
    target = _question(
        "f_payer_denial_rate__aetna",
        "What is the denial rate for Aetna? Count each claim once on its current adjudication.",
        "SELECT round(avg(is_denied::INT), 4) FROM sem.remit_claims "
        "WHERE is_current_adjudication AND payer_name = 'Aetna'",
        family="payer_denial_rate",
        template_id="denial_rate_one_payer",
    )
    family_sibling = _question(
        "f_payer_denial_rate__cigna",
        "What is the denial rate for Cigna? Count each claim once on its current adjudication.",
        "SELECT round(avg(is_denied::INT), 4) FROM sem.remit_claims "
        "WHERE is_current_adjudication AND payer_name = 'Cigna'",
        family="payer_denial_rate",
    )
    family_from_id_only = _question(
        "f_payer_denial_rate__uhc",
        "Using current adjudications, what share of claims did Uhc deny?",
        "SELECT avg(is_denied::INT) FROM sem.remit_claims WHERE payer_name = 'Uhc' AND is_current_adjudication",
    )
    template_sibling = _question(
        "authored_sibling",
        "How often does Blue Shield deny a claim under the current adjudication?",
        "SELECT count(*) AS denied_blue FROM sem.remit_claims WHERE payer_name = 'Blue Shield'",
        template_id="denial_rate_one_payer",
    )
    same_sql = _question(
        "same_sql_copy",
        "Unrelated wording about payments and months and checks that should not matter.",
        target["gold_sql"],
    )
    skeleton_sibling = _question(
        "month_sibling",
        "Count something else entirely about reversals and interest and adjustments.",
        "SELECT round(avg(is_denied::INT), 4) FROM sem.remit_claims "
        "WHERE is_current_adjudication AND payer_name = 'Humana'",
    )
    paraphrase = _question(
        "paraphrase",
        "What is the denial rate for Aetna? Count each claim once on its current adjudication. Thanks.",
        "SELECT payer_class, sum(paid_amount) FROM sem.payments GROUP BY 1",
    )
    held_out = _question(
        "a_held_1",
        "What is the denial rate by payer class on current adjudications?",
        "SELECT payer_class, avg(is_denied::INT) FROM sem.remit_claims GROUP BY 1",
        question_set=HELD_OUT_QUESTION_SET,
    )
    held_out_by_id = _question(
        "a_held_stripped",
        "Which payer class has the highest denial rate this quarter?",
        "SELECT payer_class FROM sem.remit_claims ORDER BY 1 LIMIT 3",
    )
    validation_item = _question(
        "val_q",
        "What is the denial rate for each payer on current adjudications?",
        "SELECT payer_name, avg(is_denied::INT) FROM sem.remit_claims GROUP BY payer_name",
    )
    test_item = _question(
        "test_q",
        "Show the denial rate for each payer class using current adjudications.",
        "SELECT payer_class, avg(is_denied::INT) FROM sem.remit_claims GROUP BY payer_class",
    )
    unlabeled = _question(
        "unlabeled_q",
        "What is the denial rate overall on current adjudications?",
        "SELECT avg(is_denied::INT) FROM sem.remit_claims WHERE is_current_adjudication",
    )
    refusal = _question(
        "refuse_q",
        "What is the member id on the highest paid claim?",
        "SELECT member_id FROM sem.claims",
        expect_refusal=True,
    )
    no_sql = {"id": "no_sql_q", "question": "What is the denial rate with no stored query?"}
    dev_alias = _question(
        "dev_q",
        "What is the denial rate among medicare payers on current adjudications?",
        "SELECT avg(is_denied::INT) FROM sem.remit_claims WHERE payer_class = 'medicare'",
    )
    legal = _question(
        "train_paid",
        "How much did payers pay in total on current adjudications?",
        "SELECT round(sum(paid_amount), 2) FROM sem.remit_claims WHERE is_current_adjudication",
    )

    labels = {
        target["id"]: "train",
        family_sibling["id"]: "train",
        family_from_id_only["id"]: "training",
        template_sibling["id"]: "train",
        same_sql["id"]: "train",
        skeleton_sibling["id"]: "train",
        paraphrase["id"]: "train",
        held_out["id"]: "train",
        held_out_by_id["id"]: "train",
        validation_item["id"]: "validation",
        test_item["id"]: "test",
        refusal["id"]: "train",
        no_sql["id"]: "train",
        dev_alias["id"]: "dev",
        legal["id"]: "train",
        "target_for_heldout_file": "validation",
    }
    assignment = _assignment(labels, held_out_ids=["a_held_stripped"])

    cases = {
        target["id"]: "same_question",
        family_sibling["id"]: "parametric_family",
        family_from_id_only["id"]: "parametric_family",
        template_sibling["id"]: "template_sibling",
        same_sql["id"]: "same_reference_sql",
        skeleton_sibling["id"]: "sql_template_sibling",
        paraphrase["id"]: "near_duplicate",
        held_out["id"]: "held_out",
        held_out_by_id["id"]: "held_out",
        validation_item["id"]: "split:validation",
        test_item["id"]: "split:test",
        unlabeled["id"]: "unassigned",
        refusal["id"]: "refusal",
        no_sql["id"]: "no_reference_sql",
        dev_alias["id"]: "split:validation",
        legal["id"]: None,
    }
    pool = [
        target, family_sibling, family_from_id_only, template_sibling, same_sql,
        skeleton_sibling, paraphrase, held_out, held_out_by_id, validation_item,
        test_item, unlabeled, refusal, no_sql, dev_alias, legal,
    ]
    by_id = {row["id"]: row for row in pool}
    for question_id, reason in cases.items():
        assert exclusion_reason(target, by_id[question_id], assignment) == reason, question_id

    assert sql_skeleton(target["gold_sql"]) == sql_skeleton(skeleton_sibling["gold_sql"])
    picked = retrieve(target, pool, assignment, k=3, method="tfidf")
    assert [example.question_id for example in picked] == ["train_paid"]
    picked_bm25 = retrieve(target, pool, assignment, k=3, method="bm25")
    assert [example.question_id for example in picked_bm25] == ["train_paid"]

    authored = json.loads((ROOT / "bench" / "authored_questions.json").read_text())
    held = [row for row in authored if row.get("question_set") == HELD_OUT_QUESTION_SET]
    assert len(held) == 27
    held_labels = {row["id"]: "train" for row in authored}
    held_labels["target_for_heldout_file"] = "validation"
    held_assignment = _assignment(held_labels)
    file_target = _question(
        "target_for_heldout_file",
        "What is the overall denial rate on current adjudications?",
        "SELECT avg(is_denied::INT) AS rate FROM sem.remit_claims WHERE is_current_adjudication",
    )
    for row in held:
        assert exclusion_reason(file_target, row, held_assignment) == "held_out"
    index = ExampleIndex.build(authored + [file_target], held_assignment, method="tfidf")
    assert not ({row["id"] for row in held} & {document.question_id for document in index.documents})


def test_retriever_ranks_question_text_not_reference_sql():
    target = _question(
        "target",
        "What is the denial rate for each payer on current adjudications?",
        "SELECT 'paid interest reversal month check' AS decoy FROM sem.payments",
    )
    decoy = _question(
        "decoy_sql",
        "How many distinct 835 checks were received last quarter?",
        "SELECT payer_name, avg(is_denied) FROM sem.remit_claims WHERE is_current_adjudication GROUP BY payer_name",
    )
    match = _question(
        "denial_match",
        "Show the denial rate grouped by payer name using current adjudications only.",
        "SELECT count(*) AS checks FROM sem.payments",
    )
    other = _question(
        "paid_month",
        "Total dollars paid by payment month on current adjudications.",
        "SELECT payment_month, sum(paid_amount) FROM sem.remit_claims GROUP BY 1",
    )
    carc = _question(
        "carc",
        "Count denied claims by CARC reason code on current adjudications.",
        "SELECT carc_code, count(*) FROM sem.adjustments GROUP BY carc_code",
    )
    pool = [target, decoy, match, other, carc]
    labels = {row["id"]: "train" for row in pool}
    labels[target["id"]] = "validation"
    assignment = _assignment(labels)

    for method in ("tfidf", "bm25"):
        picked = retrieve(target, pool, assignment, k=3, method=method)
        ids = [example.question_id for example in picked]
        assert ids[0] == "denial_match", method
        assert "decoy_sql" not in ids
        assert len(ids) == 3
        assert exclusion_reason(target, match, assignment) is None
    again = retrieve(target, pool, assignment, k=3, method="tfidf")
    assert [example.question_id for example in again] == [
        example.question_id for example in retrieve(target, pool, assignment, k=3, method="tfidf")
    ]


def test_ties_break_on_question_id_and_nonpositive_scores_are_dropped():
    target = _question("target", "What is the denial rate for each payer on current adjudications?")
    twin_text = "Show denial rate by payer name using current adjudications and group the rows."
    twin_b = _question("b_twin", twin_text)
    twin_a = _question("a_twin", twin_text)
    unrelated = _question("unrelated", "List provider-level adjustment reason codes and their amounts.")
    pool = [target, twin_b, twin_a, unrelated]
    assignment = _assignment({row["id"]: "validation" if row["id"] == "target" else "train" for row in pool})
    picked = retrieve(target, pool, assignment, k=3, method="tfidf")
    assert [example.question_id for example in picked] == ["a_twin", "b_twin"]
    assert all(example.score > 0 for example in picked)


def test_unassigned_target_raises_and_unknown_method_raises():
    target = _question("target", "What is the denial rate?")
    pool = [target, _question("other", "How much was paid on current adjudications?")]
    assignment = _assignment({"other": "train"})
    with pytest.raises(LeakageError, match="no split"):
        retrieve(target, pool, assignment, k=3)
    with pytest.raises(ValueError, match="unknown method"):
        ExampleIndex.build(pool, _assignment({"other": "train", "target": "validation"}), method="openai")


def test_prompt_contains_question_and_reference_sql_only():
    examples = [
        RetrievedExample("q_train", "How many claims were submitted?", "SELECT count(*) FROM sem.claims", 0.5, "tfidf"),
    ]
    rendered = render_prompt(_prompt("What is the denial rate?"), examples)
    assert "How many claims were submitted?" in rendered
    assert "SELECT count(*) FROM sem.claims" in rendered
    assert rendered.index("Example 1") < rendered.index("User question:")
    assert "What is the denial rate?" in rendered.split("User question:", 1)[1]
    assert render_prompt(_prompt("What is the denial rate?"), []) == _prompt("What is the denial rate?")
    with pytest.raises(ValueError, match="User question"):
        render_prompt("no marker here", examples)


def test_adapter_rewrites_only_the_a10_config(tmp_path):
    target = _question("q_val", "What is the denial rate for each payer on current adjudications?")
    train = _question(
        "q_train",
        "Show the denial rate grouped by payer name using current adjudications only.",
    )
    train["interpretation"] = "SECRET_NUMBER_42 expected rows"
    train["answer_key_sql"] = "SELECT secret FROM ledger.adjudications"
    held = _question(
        "a_held",
        "What is the denial rate for each payer on current adjudications today?",
        question_set=HELD_OUT_QUESTION_SET,
    )
    questions = [target, train, held]
    assignment = _assignment({
        "q_val": "validation",
        "q_train": "train",
        "a_held": "train",
    })
    base_prompt = _prompt(target["question"])
    other_prompt = _prompt(target["question"])
    manifest = {
        "run": "a10test",
        "entries": [
            {"session": "/tmp/a", "model": "haiku", "config": "a10_dynamic_fewshot",
             "question_id": "q_val", "repeat": 0, "prompt": base_prompt},
            {"session": "/tmp/b", "model": "haiku", "config": "a10_dynamic_fewshot",
             "question_id": "q_val", "repeat": 1, "prompt": base_prompt},
            {"session": "/tmp/c", "model": "haiku", "config": "base",
             "question_id": "q_val", "repeat": 0, "prompt": other_prompt},
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    report = attach_to_manifest(path, questions, assignment, target_splits=("validation",))
    written = json.loads(path.read_text())
    assert report["examples"] == {"q_val": ["q_train"]}
    assert report["policy"] == "a10-leakage-1"
    a10_entries = [entry for entry in written["entries"] if entry["config"] == "a10_dynamic_fewshot"]
    assert all(entry["a10_example_ids"] == ["q_train"] for entry in a10_entries)
    for entry in a10_entries:
        assert "SELECT count(*) AS c_q_train FROM sem.claims" in entry["prompt"]
        assert "SECRET_NUMBER_42" not in entry["prompt"]
        assert "ledger.adjudications" not in entry["prompt"]
        assert entry["prompt"].index("Example 1") < entry["prompt"].index("User question:")
    base_entry = next(entry for entry in written["entries"] if entry["config"] == "base")
    assert base_entry["prompt"] == other_prompt
    assert "a10_example_ids" not in base_entry

    with pytest.raises(ValueError, match="examples tool"):
        technique_config(enable_examples=True)
    config = technique_config()
    assert config["enable_examples"] is False
    assert config["glossary"] == "basic"
    assert config["context_style"] == "goal"
    assert config["effort"] == "low"


def test_adapter_rejects_closed_target_split_and_mismatched_prompt(tmp_path):
    target = _question("q_test", "What is the denial rate for each payer?")
    train = _question("q_train", "Show denial rate by payer name.")
    assignment = _assignment({"q_test": "test", "q_train": "train"})
    manifest = {
        "entries": [{
            "config": "a10_dynamic_fewshot",
            "question_id": "q_test",
            "prompt": _prompt(target["question"]),
        }],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(LeakageError, match="not in"):
        attach_to_manifest(path, [target, train], assignment, target_splits=("validation",))
    path.write_text(json.dumps(manifest))
    bad = json.loads(path.read_text())
    bad["entries"][0]["prompt"] = _prompt("A different question than the pool.")
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="does not contain"):
        attach_to_manifest(path, [target, train], assignment, target_splits=("test",))


def test_split_assignment_from_manager_and_json(tmp_path):
    class Manager:
        held_out_ids = ["extra_held"]
        held_out_question_sets = ["later_heldout"]

        def split_of(self, question_id):
            return {"q_train": "train", "q_val": "val", "q_test": "test"}.get(question_id)

    assignment = SplitAssignment.from_manager(Manager(), ["q_train", "q_val", "q_test", "q_missing"])
    assert assignment.split_of("q_train") == "train"
    assert assignment.split_of("q_val") == "validation"
    assert assignment.split_of("q_missing") is None
    target = _question("q_val", "What is the denial rate?")
    extra = _question("extra_held", "What is the denial rate by class?", question_set="not_the_builtin")
    later = _question("later_q", "What is paid by month?", question_set="later_heldout")
    labels_needed = SplitAssignment.from_mapping(
        {"q_val": "validation", "extra_held": "train", "later_q": "train"},
        held_out_ids=assignment.held_out_ids,
        extra_held_out_question_sets=assignment.extra_held_out_question_sets,
    )
    assert exclusion_reason(target, extra, labels_needed) == "held_out"
    assert exclusion_reason(target, later, labels_needed) == "held_out"

    path = tmp_path / "splits.json"
    path.write_text(json.dumps({
        "version": 1,
        "splits": {"q_val": "validation", "q_train": "train"},
        "held_out_ids": [],
        "held_out_question_sets": [],
    }))
    loaded = SplitAssignment.from_json(path)
    held = _question("a_real", "Hard question.", question_set=HELD_OUT_QUESTION_SET)
    train_labels = SplitAssignment.from_mapping(
        {**dict(loaded.splits), "a_real": "train"},
        held_out_ids=loaded.held_out_ids,
        extra_held_out_question_sets=loaded.extra_held_out_question_sets,
    )
    assert exclusion_reason(target, held, train_labels) == "held_out"


def test_technique_config_is_legal_for_eval_subagents():
    from bench.agent_cli import DEFAULT_TECHNIQUE_CONFIG
    assert set(technique_config()) <= set(DEFAULT_TECHNIQUE_CONFIG)


def test_no_paid_api_client_is_imported():
    import sys
    banned = {"anthropic", "openai", "cohere", "voyageai"}
    loaded = {name.split(".", 1)[0] for name in sys.modules}
    assert not (loaded & banned)


def test_cli_attaches_examples(tmp_path):
    from analyst.approaches.a10_dynamic_fewshot.__main__ import main

    target = _question("q_val", "What is the denial rate for each payer on current adjudications?")
    train = _question("q_train", "Show the denial rate grouped by payer name using current adjudications only.")
    questions_path = tmp_path / "questions.json"
    splits_path = tmp_path / "splits.json"
    manifest_path = tmp_path / "manifest.json"
    questions_path.write_text(json.dumps([target, train]))
    splits_path.write_text(json.dumps({"version": 1, "splits": {"q_val": "validation", "q_train": "train"}}))
    manifest_path.write_text(json.dumps({
        "entries": [{
            "config": "a10_dynamic_fewshot",
            "question_id": "q_val",
            "repeat": 0,
            "prompt": _prompt(target["question"]),
        }],
    }))
    assert main([
        str(manifest_path),
        "--questions", str(questions_path),
        "--splits", str(splits_path),
        "--target-splits", "validation",
        "--method", "bm25",
    ]) == 0
    written = json.loads(manifest_path.read_text())
    assert written["a10"]["examples"]["q_val"] == ["q_train"]
    assert written["a10"]["method"] == "bm25"
    assert "Reference SQL:" in written["entries"][0]["prompt"]
