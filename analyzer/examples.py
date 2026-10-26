"""#6 dynamic few-shot for the subagent route: k solved TRAIN-split questions, BM25, A10's leakage guard.

The pool is ``bench.splits.select_cases(cases, "train")``: only questions whose frozen split is train. It is
never the snapshot's whole ``generated_questions.json``, which also holds validation and test families. A10's
``ExampleIndex`` then applies its own pool rules (train only, not held-out, has SQL) and pair rules (not the
target, not its family or template, not the same or a near-duplicate SQL or text), and ``assert_no_leakage``
re-checks the selection. The target must have a frozen split; an unassigned question fails.

A question asked in the chat app (``LIVE_QUESTION_ID``) is in no benchmark split, so nothing it is shown can leak
into a score. It gets the same BM25 pick from the same TRAIN pool under the same pair rules.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from analyst.approaches.a10_dynamic_fewshot.leakage import SplitAssignment, assert_no_leakage
from analyst.approaches.a10_dynamic_fewshot.retriever import ExampleIndex, RetrievedExample

RETRIEVAL_METHOD = "bm25"
#: question id of a session prepared for a question asked live (api/claude_code_cli_driver.py), never a bench id
LIVE_QUESTION_ID = "live_question"
LIVE_QUESTION_SPLIT = "live"


def _assignment_row(case: dict, rows_by_id: dict) -> dict | None:
    row = rows_by_id.get(case["id"])
    if row is None and case.get("family"):
        row = rows_by_id.get(f"family:{case['family']}")
    return row


def split_assignment_for(cases: list[dict]) -> SplitAssignment:
    """Frozen split of every case. A family instance inherits its family's split, as in select_cases."""
    from bench.splits import load_assignment
    rows_by_id = {row["id"]: row for row in load_assignment()["questions"]}
    splits = {}
    for case in cases:
        row = _assignment_row(case, rows_by_id)
        if row is not None:
            splits[case["id"]] = row["split"]
    return SplitAssignment.from_mapping(splits)


def with_link_groups(cases: list[dict]) -> list[dict]:
    """Copy each case with the split manager's link group as A10's template_id, so a question linked to the
    target (same family, template bundle, signature, shared phrase or near-copy) is never its example."""
    from bench.splits import load_assignment
    rows_by_id = {row["id"]: row for row in load_assignment()["questions"]}
    linked = []
    for case in cases:
        row = _assignment_row(case, rows_by_id)
        linked.append({**case, "template_id": row["group_id"]} if row is not None else dict(case))
    return linked


@lru_cache(maxsize=4)
def _index(snapshot_path: str) -> tuple[ExampleIndex, dict, SplitAssignment]:
    from bench.eval import load_cases
    from bench.splits import select_cases
    cases = with_link_groups(load_cases(snapshot_path=Path(snapshot_path)))
    assignment = split_assignment_for(cases)
    assignment = SplitAssignment.from_mapping({**assignment.splits, LIVE_QUESTION_ID: LIVE_QUESTION_SPLIT},
                                              assignment.held_out_ids, assignment.extra_held_out_question_sets)
    train_pool = select_cases(cases, "train")
    index = ExampleIndex.build(train_pool, assignment, method=RETRIEVAL_METHOD)
    return index, {case["id"]: case for case in cases}, assignment


def train_examples_for(snapshot_path: str | Path, question_id: str, k: int) -> list[RetrievedExample]:
    index, cases_by_id, assignment = _index(str(Path(snapshot_path).resolve()))
    if question_id not in cases_by_id:
        raise KeyError(f"question {question_id} is not in the snapshot's question set")
    target = cases_by_id[question_id]
    picked = index.retrieve(target, k=k)
    assert_no_leakage(target, [cases_by_id[example.question_id] for example in picked], assignment)
    return picked


def train_examples_for_live_question(snapshot_path: str | Path, question_text: str, k: int) -> list[RetrievedExample]:
    index, _cases_by_id, _assignment = _index(str(Path(snapshot_path).resolve()))
    return index.retrieve({"id": LIVE_QUESTION_ID, "question": question_text}, k=k)
