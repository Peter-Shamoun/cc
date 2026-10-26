"""Train / validation / test labels for solved examples.

Validation and test questions are never retrieval candidates. An unrecognized
question_set fails closed: it is not silently treated as train. The held-out
sets authored after screen 1 (`heldout_hard_1`, `step0_hard_1`, `grokbot_r1`) are validation. No test set is
authored yet; a question_set that starts with ``test`` is reserved for it.
"""
from __future__ import annotations

VALIDATION_QUESTION_SETS = frozenset({"heldout_hard_1", "step0_hard_1", "grokbot_r1"})
SPLITS = frozenset({"train", "validation", "test"})


class SplitLabelError(ValueError):
    """The question's split cannot be decided, or a non-train item was offered as an example."""


class SplitLeakError(SplitLabelError):
    """A validation or test question was present in a retrieval pool."""


def assign_split(question: dict) -> str:
    """Label one question from its metadata. Does not look at the question text."""
    explicit = question.get("split")
    if explicit is not None:
        if explicit not in SPLITS:
            raise SplitLabelError(f"unknown split {explicit!r} on {question.get('id')}")
        return explicit
    question_set = question.get("question_set") or ""
    if not question_set:
        return "train"
    if question_set in VALIDATION_QUESTION_SETS or question_set.startswith(("validation", "heldout", "val_")):
        return "validation"
    if question_set.startswith("test"):
        return "test"
    raise SplitLabelError(
        f"unrecognized question_set {question_set!r} on {question.get('id')}; "
        "register it before it can enter a retrieval library"
    )


def assert_train_pool(examples: list[dict]) -> None:
    """Raise if any candidate is not an explicit train example. Do not drop it quietly."""
    for example in examples:
        split = example.get("split")
        question_set = example.get("question_set") or ""
        example_id = example.get("id")
        if split != "train":
            raise SplitLeakError(
                f"example {example_id} has split {split!r}; retrieval accepts only split 'train'"
            )
        if question_set in VALIDATION_QUESTION_SETS or question_set.startswith(("validation", "heldout", "val_", "test")):
            raise SplitLeakError(
                f"example {example_id} carries question_set {question_set!r}; "
                "validation and test questions cannot be retrieved"
            )
        if example.get("expect_refusal"):
            raise SplitLeakError(f"example {example_id} is a refusal item, not a solved SQL example")
