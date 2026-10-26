"""Retrieve the k most similar solved questions from the train pool.

Two rankers, both local, neither calls a network:

- ``tfidf``: cosine similarity of sparse TF-IDF vectors (raw term frequency,
  smoothed idf ``log((1 + n) / (1 + df)) + 1``). This is the embedding arm
  without a hosted embedding model.
- ``bm25``: Okapi BM25 (k1=1.5, b=0.75) with a non-negative idf.

The corpus is the train-legal pool from ``leakage.pool_block_reason``. IDF and
document length are fit on that pool only, so a validation or test question
does not move the weights. The target's text is the query. The target's
reference SQL is not in the query.

Scores at or below zero are dropped. Ties break by question id, ascending, so
a repeat of the same target returns the same three examples.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from analyzer.example_leakage import (
    LeakageError,
    SplitAssignment,
    exclusion_reason,
    pool_block_reason,
    question_text,
    reference_sql,
    tokenize,
)

DEFAULT_K = 3
METHODS = ("tfidf", "bm25")
_BM25_K1 = 1.5
_BM25_B = 0.75


@dataclass(frozen=True)
class RetrievedExample:
    """What the prompt is allowed to contain from one solved question."""

    question_id: str
    question: str
    gold_sql: str
    score: float
    method: str


@dataclass
class _Document:
    question: Mapping
    question_id: str
    text: str
    sql: str
    tokens: list[str]


class ExampleIndex:
    """A train-pool index. Build once per manifest, then retrieve per target."""

    def __init__(self, documents: list[_Document], assignment: SplitAssignment, method: str):
        if method not in METHODS:
            raise ValueError(f"unknown method {method!r}; expected one of {METHODS}")
        self.documents = documents
        self.assignment = assignment
        self.method = method
        self._tfidf: list[dict[str, float]] | None = None
        self._idf: dict[str, float] | None = None
        self._bm25_df: Counter | None = None
        self._avgdl = 0.0
        if method == "tfidf":
            self._fit_tfidf()
        else:
            self._fit_bm25()

    @classmethod
    def build(
        cls,
        questions: Sequence[Mapping],
        assignment: SplitAssignment,
        method: str = "tfidf",
    ) -> ExampleIndex:
        documents = []
        seen = set()
        for question in questions:
            if pool_block_reason(question, assignment) is not None:
                continue
            question_id = str(question["id"])
            if question_id in seen:
                raise ValueError(f"duplicate train-pool id {question_id}")
            seen.add(question_id)
            sql = reference_sql(question)
            if sql is None:
                continue
            documents.append(_Document(
                question=question,
                question_id=question_id,
                text=question_text(question),
                sql=sql,
                tokens=tokenize(question_text(question)),
            ))
        return cls(documents, assignment, method)

    def retrieve(self, target: Mapping, k: int = DEFAULT_K) -> list[RetrievedExample]:
        if k < 1:
            raise ValueError(f"k must be >= 1, got {k}")
        question_id = target.get("id")
        if not question_id or self.assignment.split_of(str(question_id)) is None:
            raise LeakageError(f"target {question_id} has no split assignment")
        query = question_text(target)
        query_tokens = tokenize(query)
        ranked: list[tuple[float, _Document]] = []
        for index, document in enumerate(self.documents):
            if exclusion_reason(target, document.question, self.assignment) is not None:
                continue
            score = self._score(query_tokens, index, document)
            if score <= 0:
                continue
            ranked.append((score, document))
        ranked.sort(key=lambda item: (-item[0], item[1].question_id))
        return [
            RetrievedExample(
                question_id=document.question_id,
                question=document.text,
                gold_sql=document.sql,
                score=round(score, 6),
                method=self.method,
            )
            for score, document in ranked[:k]
        ]

    def _fit_tfidf(self) -> None:
        document_count = len(self.documents)
        df: Counter = Counter()
        frequencies = []
        for document in self.documents:
            counts = Counter(document.tokens)
            frequencies.append(counts)
            df.update(counts.keys())
        idf = {
            term: math.log((1 + document_count) / (1 + freq)) + 1.0
            for term, freq in df.items()
        }
        self._idf = idf
        self._tfidf = [
            {term: count * idf[term] for term, count in counts.items()}
            for counts in frequencies
        ]

    def _fit_bm25(self) -> None:
        df: Counter = Counter()
        total_length = 0
        for document in self.documents:
            df.update(set(document.tokens))
            total_length += len(document.tokens)
        self._bm25_df = df
        self._avgdl = (total_length / len(self.documents)) if self.documents else 0.0

    def _score(self, query_tokens: list[str], index: int, document: _Document) -> float:
        if self.method == "tfidf":
            return _cosine(_query_tfidf(query_tokens, self._idf or {}, len(self.documents)), self._tfidf[index])
        return _bm25(query_tokens, document.tokens, self._bm25_df or Counter(), len(self.documents), self._avgdl)


def retrieve(
    target: Mapping,
    questions: Sequence[Mapping],
    assignment: SplitAssignment,
    *,
    k: int = DEFAULT_K,
    method: str = "tfidf",
) -> list[RetrievedExample]:
    """Top-k train examples for one target. Convenience wrapper around ``ExampleIndex``."""
    return ExampleIndex.build(questions, assignment, method=method).retrieve(target, k=k)


def _query_tfidf(tokens: list[str], idf: dict[str, float], document_count: int) -> dict[str, float]:
    if not tokens:
        return {}
    default_idf = math.log(1 + document_count) + 1.0 if document_count else 1.0
    counts = Counter(tokens)
    return {term: count * idf.get(term, default_idf) for term, count in counts.items()}


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(weight * right.get(term, 0.0) for term, weight in left.items())
    left_norm = math.sqrt(sum(weight * weight for weight in left.values()))
    right_norm = math.sqrt(sum(weight * weight for weight in right.values()))
    if left_norm == 0 or right_norm == 0 or dot <= 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _bm25(
    query_tokens: list[str],
    document_tokens: list[str],
    df: Counter,
    document_count: int,
    average_length: float,
) -> float:
    if not query_tokens or not document_tokens or average_length <= 0 or document_count <= 0:
        return 0.0
    frequencies = Counter(document_tokens)
    length = len(document_tokens)
    score = 0.0
    for term in set(query_tokens):
        frequency = frequencies.get(term, 0)
        if frequency == 0:
            continue
        document_frequency = df.get(term, 0)
        idf = math.log(1 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5))
        denominator = frequency + _BM25_K1 * (1 - _BM25_B + _BM25_B * length / average_length)
        score += idf * (frequency * (_BM25_K1 + 1)) / denominator
    return score
