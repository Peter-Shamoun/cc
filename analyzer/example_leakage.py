"""Leakage guard for A10 dynamic few-shot.

The retriever may show a solved question only when every check below passes.
Ranking never gets a vote on these. A near match that fails a check is dropped,
not down-weighted.

Priority, first match wins:

1. ``same_question`` — the question being answered.
2. ``unassigned`` — the split manager has no label for this id.
3. ``split:<name>`` — the label is not ``train``. Validation, test, and any
   other label fail closed.
4. ``held_out`` — question set ``heldout_hard_1`` (the held-out 27) or an id
   the caller listed. The set tag is excluded even when the manager labeled
   the question train.
5. ``refusal`` — not a solved SQL example.
6. ``no_reference_sql`` — nothing to show as a reference query.
7. ``parametric_family`` — same ``family``, or the same family encoded in an
   id ``f_<family>__<params>`` when the field was dropped.
8. ``template_sibling`` — shared ``template_id``, ``template``, or
   ``template_family``.
9. ``same_reference_sql`` — the reference SQL matches after whitespace and case
   normalization.
10. ``sql_template_sibling`` — the reference SQL matches after literals are
    masked. This is the untagged parametric sibling: same template, different
    payer or month.
11. ``near_duplicate`` — masked question-token Jaccard at or above
    ``NEAR_DUPLICATE_JACCARD``. Catches a paraphrase whose SQL was rewritten
    enough to miss the skeleton check.

The target's reference SQL is read only by checks 9 and 10. It is not a
retrieval feature. See ``retriever.py``.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

HELD_OUT_QUESTION_SET = "heldout_hard_1"
NEAR_DUPLICATE_JACCARD = 0.85
TRAIN_SPLIT = "train"

_SPLIT_ALIASES = {
    "train": TRAIN_SPLIT,
    "training": TRAIN_SPLIT,
    "validation": "validation",
    "val": "validation",
    "dev": "validation",
    "test": "test",
}

_TEMPLATE_FIELDS = ("template_id", "template", "template_family")

_STOPWORDS = frozenset(
    "a an the of for to in on and or by with from at as into over "
    "what which who when where why how is are was were be been being "
    "do does did we our you your me my that this these those it its".split()
)

_LITERAL = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b"
    r"|'[^']*'"
    r"|\"[^\"]*\""
    r"|\b\d+(?:\.\d+)?\b"
)

_TOKEN = re.compile(r"[a-z0-9]+")


class LeakageError(ValueError):
    """A retrieval or a target split would show, or score, a forbidden example."""


def mask_literals(text: str) -> str:
    """Replace dates, quoted strings, and numbers with a single placeholder."""
    return _LITERAL.sub(" ?", text)


def tokenize(text: str, *, mask: bool = True, drop_stopwords: bool = True) -> list[str]:
    """Question tokens used for similarity. Masking is on for retrieval so a
    payer name or a month does not dominate the match."""
    lowered = text.lower()
    if mask:
        lowered = mask_literals(lowered)
    tokens = _TOKEN.findall(lowered)
    if drop_stopwords:
        tokens = [token for token in tokens if token not in _STOPWORDS and len(token) > 1]
    return tokens


def sql_skeleton(sql: str) -> str:
    """Reference SQL with case, whitespace, and literals removed."""
    return mask_literals(" ".join(sql.lower().split()))


def normalized_sql(sql: str) -> str:
    return " ".join(sql.lower().split())


def token_jaccard(left: str, right: str) -> float:
    """Surface Jaccard on masked tokens, stopwords kept.

    Stopwords stay so a one-token swap inside a long shared template scores
    high. Retrieval uses a different tokenization (stopwords dropped) and does
    not call this function.
    """
    left_tokens = set(tokenize(left, mask=True, drop_stopwords=False))
    right_tokens = set(tokenize(right, mask=True, drop_stopwords=False))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def family_of(question: Mapping) -> str | None:
    """Parametric family: the ``family`` field, else the id prefix ``f_<family>__``."""
    family = question.get("family")
    if family:
        return str(family)
    question_id = str(question.get("id", ""))
    if question_id.startswith("f_") and "__" in question_id:
        return question_id[2:].split("__", 1)[0]
    return None


def template_keys(question: Mapping) -> frozenset[str]:
    keys = []
    for field in _TEMPLATE_FIELDS:
        value = question.get(field)
        if value:
            keys.append(f"{field}:{value}")
    return frozenset(keys)


def question_text(question: Mapping) -> str:
    text = question.get("question")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"question {question.get('id')!r} has no question text")
    return text.strip()


def reference_sql(question: Mapping) -> str | None:
    sql = question.get("gold_sql")
    if sql is None:
        return None
    if not isinstance(sql, str):
        raise ValueError(f"question {question.get('id')!r} gold_sql is not a string")
    stripped = sql.strip()
    return stripped or None


@dataclass(frozen=True)
class SplitAssignment:
    """Read-only view of the split manager's assignment.

    The manager (separate PR) decides which id is train, validation, or test.
    A10 does not assign splits and does not retrieve until an assignment is
    passed in. Build one with ``from_mapping``, ``from_json``, or
    ``from_manager``.

    JSON shape the manager can write:

    .. code-block:: json

        {
          "version": 1,
          "splits": {"q01_claims_submitted": "train", "a_9a8aa51689": "test"},
          "held_out_ids": ["a_9a8aa51689"],
          "held_out_question_sets": ["heldout_hard_1"]
        }

    ``heldout_hard_1`` is always excluded, including when the file omits it or
    sets the list to empty. Extra set names are added, never substituted.

    Manager object: ``split_of(question_id) -> str | None``. Optional
    ``held_out_ids`` and ``held_out_question_sets`` are unioned in.
    ``from_manager`` snapshots the labels for the ids it is given so later
    retrieval does not call back into a live manager.

    Labels ``val`` and ``dev`` normalize to validation, ``training`` to train.
    Any other label is kept and is therefore not train.
    """

    splits: Mapping[str, str]
    held_out_ids: frozenset[str] = frozenset()
    extra_held_out_question_sets: frozenset[str] = frozenset()

    def split_of(self, question_id: str) -> str | None:
        raw = self.splits.get(question_id)
        if raw is None:
            return None
        label = str(raw).strip().lower()
        return _SPLIT_ALIASES.get(label, label)

    @classmethod
    def from_mapping(
        cls,
        splits: Mapping[str, str],
        held_out_ids: Sequence[str] = (),
        extra_held_out_question_sets: Sequence[str] = (),
    ) -> SplitAssignment:
        return cls(
            splits=MappingProxyType(dict(splits)),
            held_out_ids=frozenset(held_out_ids),
            extra_held_out_question_sets=frozenset(extra_held_out_question_sets),
        )

    @classmethod
    def from_json(cls, path: Path | str) -> SplitAssignment:
        data = json.loads(Path(path).read_text())
        if not isinstance(data, dict) or not isinstance(data.get("splits"), dict):
            raise ValueError("split file needs an object with a 'splits' object of id -> split")
        extra = data.get("held_out_question_sets", [])
        ids = data.get("held_out_ids", [])
        if not isinstance(extra, list) or not isinstance(ids, list):
            raise ValueError("held_out_ids and held_out_question_sets must be lists")
        return cls.from_mapping(data["splits"], held_out_ids=ids, extra_held_out_question_sets=extra)

    @classmethod
    def from_manager(cls, manager, question_ids: Sequence[str]) -> SplitAssignment:
        """Snapshot ``manager.split_of`` for these ids.

        Ids the manager returns ``None`` for are left unassigned, which the
        guard then rejects. Extra held-out ids and question sets advertised on
        the manager are unioned with the built-in held-out set.
        """
        splits = {}
        for question_id in question_ids:
            label = manager.split_of(question_id)
            if label is not None:
                splits[question_id] = label
        extra_ids = getattr(manager, "held_out_ids", ()) or ()
        extra_sets = getattr(manager, "held_out_question_sets", ()) or ()
        return cls.from_mapping(splits, held_out_ids=extra_ids, extra_held_out_question_sets=extra_sets)


def _held_out(question: Mapping, assignment: SplitAssignment) -> bool:
    if str(question.get("id", "")) in assignment.held_out_ids:
        return True
    question_set = question.get("question_set")
    if not question_set:
        return False
    if question_set == HELD_OUT_QUESTION_SET:
        return True
    return question_set in assignment.extra_held_out_question_sets


def pool_block_reason(question: Mapping, assignment: SplitAssignment) -> str | None:
    """Why this question cannot be an example for anyone. None if it is train-legal."""
    question_id = question.get("id")
    if not question_id:
        return "unassigned"
    split = assignment.split_of(str(question_id))
    if split is None:
        return "unassigned"
    if split != TRAIN_SPLIT:
        return f"split:{split}"
    if _held_out(question, assignment):
        return "held_out"
    if question.get("expect_refusal"):
        return "refusal"
    if reference_sql(question) is None:
        return "no_reference_sql"
    return None


def pair_block_reason(target: Mapping, candidate: Mapping, assignment: SplitAssignment) -> str | None:
    """Why this candidate cannot be shown for this target. Ignores pool rules."""
    if str(candidate.get("id")) == str(target.get("id")):
        return "same_question"
    target_family = family_of(target)
    candidate_family = family_of(candidate)
    if target_family and candidate_family and target_family == candidate_family:
        return "parametric_family"
    shared = template_keys(target) & template_keys(candidate)
    if shared:
        return "template_sibling"
    target_sql = reference_sql(target)
    candidate_sql = reference_sql(candidate)
    if target_sql and candidate_sql:
        if normalized_sql(target_sql) == normalized_sql(candidate_sql):
            return "same_reference_sql"
        if sql_skeleton(target_sql) == sql_skeleton(candidate_sql):
            return "sql_template_sibling"
    if token_jaccard(question_text(target), question_text(candidate)) >= NEAR_DUPLICATE_JACCARD:
        return "near_duplicate"
    return None


def exclusion_reason(target: Mapping, candidate: Mapping, assignment: SplitAssignment) -> str | None:
    """None when ``candidate`` may be shown while answering ``target``."""
    if str(candidate.get("id")) == str(target.get("id")):
        return "same_question"
    blocked = pool_block_reason(candidate, assignment)
    if blocked:
        return blocked
    return pair_block_reason(target, candidate, assignment)


def assert_no_leakage(target: Mapping, selected: Sequence[Mapping], assignment: SplitAssignment) -> None:
    """Raise ``LeakageError`` if any selected question is ineligible for ``target``."""
    for candidate in selected:
        reason = exclusion_reason(target, candidate, assignment)
        if reason is not None:
            raise LeakageError(
                f"{candidate.get('id')} cannot be an example for {target.get('id')}: {reason}"
            )


def require_target_split(target: Mapping, assignment: SplitAssignment, allowed: Sequence[str]) -> str:
    """Return the target's normalized split, or raise when it is missing or closed."""
    question_id = target.get("id")
    if not question_id:
        raise LeakageError("target question has no id")
    split = assignment.split_of(str(question_id))
    if split is None:
        raise LeakageError(f"target {question_id} has no split assignment")
    allowed_norm = {_SPLIT_ALIASES.get(name.strip().lower(), name.strip().lower()) for name in allowed}
    if split not in allowed_norm:
        raise LeakageError(f"target {question_id} is in split {split}, not in {sorted(allowed_norm)}")
    return split
