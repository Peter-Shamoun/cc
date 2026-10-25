"""Dynamic few-shot retrieval from the train split only.

``lexical`` is the screen-1 selector: Jaccard over raw question tokens. ``skeleton``
masks payer names and code-shaped tokens first, so shared entities do not outrank
a question with the same query shape. Either selector raises if the pool contains
a validation or test item. It does not drop that item and continue.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from analyst.variants.context_retrieval.splits import SplitLeakError, assert_train_pool, assign_split

ROOT = Path(__file__).resolve().parents[3]
QUESTIONS_PATH = ROOT / "bench" / "questions.yaml"
AUTHORED_PATH = ROOT / "bench" / "authored_questions.json"

_TOKEN = re.compile(r"[a-z0-9]+")
_CODE = re.compile(r"\b(?:[a-z]\d{2,}[a-z0-9]*|\d{4,5}[a-z]?|\d{1,3})\b", re.IGNORECASE)
_STOP = frozenset(
    "a an the of to for by on in and or with from each per our their what which how many is are was were "
    "do does did show give list total number amount much using its at least once".split()
)


def _raw_tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _normalize_question(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def skeleton_tokens(text: str, mask_literals: list[str] | None = None) -> set[str]:
    """Tokens that describe the query shape, with entities and codes removed."""
    masked = text.lower()
    for literal in sorted({item for item in (mask_literals or []) if item}, key=len, reverse=True):
        if len(literal) >= 2:
            masked = re.sub(re.escape(literal.lower()), " ", masked)
    masked = _CODE.sub(" ", masked)
    return {token for token in _raw_tokens(masked) if token not in _STOP and len(token) > 2}


def _example_record(question: dict, split: str) -> dict | None:
    if question.get("expect_refusal") or not question.get("gold_sql"):
        return None
    return {
        "id": question["id"],
        "split": split,
        "question_set": question.get("question_set"),
        "family": question.get("family"),
        "question": question["question"],
        "sql": question["gold_sql"].strip(),
        "expect_refusal": False,
    }


def load_train_library(questions_path: Path | None = None, authored_path: Path | None = None,
                       extra_questions: list[dict] | None = None) -> list[dict]:
    """Solved train questions only. A validation, test, or unlabeled-set question never enters the list.

    An unrecognized question_set raises rather than being filed as train.
    """
    library = []
    handwritten = yaml.safe_load((questions_path or QUESTIONS_PATH).read_text())
    for question in handwritten:
        record = _example_record(question, assign_split(question))
        if record and record["split"] == "train":
            library.append(record)
    authored_file = authored_path if authored_path is not None else AUTHORED_PATH
    if authored_file.exists():
        for question in json.loads(authored_file.read_text()):
            split = assign_split(question)
            if split != "train":
                continue
            record = _example_record(question, split)
            if record:
                library.append(record)
    for question in extra_questions or []:
        split = assign_split(question)
        if split != "train":
            raise SplitLeakError(
                f"extra example {question.get('id')} is split {split!r}; load_train_library accepts only train"
            )
        record = _example_record(question, split)
        if record:
            library.append(record)
    assert_train_pool(library)
    return library


def retrieve_examples(library: list[dict], question: str, *, question_id: str | None = None,
                      family: str | None = None, k: int = 3, selector: str = "skeleton",
                      near_duplicate_jaccard: float = 0.92, exclude_same_family: bool = True,
                      mask_literals: list[str] | None = None) -> list[dict]:
    """Top-k train examples. Raises SplitLeakError if the pool is not entirely train."""
    if k <= 0:
        return []
    if selector not in {"skeleton", "lexical"}:
        raise ValueError(f"unknown fewshot_selector {selector!r}")
    assert_train_pool(library)
    target_raw = _raw_tokens(question)
    target_skeleton = skeleton_tokens(question, mask_literals)
    normalized = _normalize_question(question)
    scored = []
    for example in library:
        if question_id and example.get("id") == question_id:
            continue
        if _normalize_question(example["question"]) == normalized:
            continue
        if exclude_same_family and family and example.get("family") and example["family"] == family:
            continue
        raw_score = _jaccard(target_raw, _raw_tokens(example["question"]))
        if raw_score >= near_duplicate_jaccard:
            continue
        if selector == "lexical":
            score = raw_score
        else:
            score = _jaccard(target_skeleton, skeleton_tokens(example["question"], mask_literals))
        scored.append((score, example))
    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    chosen = []
    for score, example in scored[:k]:
        if example.get("split") != "train":
            raise SplitLeakError(f"refusing to return non-train example {example.get('id')}")
        chosen.append({
            "id": example["id"],
            "question": example["question"],
            "sql": example["sql"],
            "score": round(score, 4),
            "selector": selector,
        })
    return chosen
