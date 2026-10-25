"""Pick one executed cluster: strict majority, pairwise selector, verifier, or agree-or-abstain.

The screen-2 rule (sota refresh 2, stage 2): if one result-group is a strict majority of the
successful executions, take it. Otherwise one selector call sees the question, the SQL, and the
result digest. A decline abstains. Abstention is an incorrect answer on the current bench, which
has no reliability score; the decision records it anyway.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from analyst.variants.multi_candidate.equivalence import cluster_indices
from analyst.variants.multi_candidate.prompts import (
    SELECTOR_STRATEGY,
    VERIFIER_STRATEGY,
    selector_user_message,
    verifier_user_message,
)

_CHOICE = re.compile(r"\bABSTAIN\b|\bCLUSTER\s+([A-Za-z0-9_\-]+)\b", re.IGNORECASE)
_VERDICT = re.compile(r"\b(?:ACCEPT|REJECT)\b", re.IGNORECASE)


@dataclass
class Candidate:
    strategy: str
    sql: str | None = None
    columns: list | None = None
    rows: list | None = None
    text: str = ""
    refused: bool = False
    error: str | None = None
    exploratory_sql: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)

    @property
    def kind(self) -> str:
        if self.refused:
            return "refusal"
        if self.error or self.rows is None:
            return "error"
        return "rows"


@dataclass
class Cluster:
    cluster_id: str
    members: list[Candidate]
    kind: str
    rows: list | None
    representative: Candidate

    @property
    def size(self) -> int:
        return len(self.members)


@dataclass
class Decision:
    abstained: bool = False
    refused: bool = False
    sql: str | None = None
    rows: list | None = None
    columns: list | None = None
    text: str = ""
    reason: str = ""
    chosen_strategy: str | None = None
    selector_called: bool = False
    verifier_called: bool = False
    cluster_sizes: list[int] = field(default_factory=list)
    n_successful: int = 0
    n_errors: int = 0
    n_refusals: int = 0
    clusters: list[Cluster] = field(default_factory=list)


def representative(members: list[Candidate], strategy_order: tuple[str, ...]) -> Candidate:
    """Widest result, then the earliest strategy, then the shortest SQL.

    Two results share a cluster only when the judge treats them as the same answer. Extra text
    labels and a whole-number supporting count beside a fractional measure are acceptable extras.
    Dropping a column is not: the narrower projection can omit a count the question asked for and
    still match the wider one in one direction. The vote submits the wider projection.
    """
    order = {name: i for i, name in enumerate(strategy_order)}

    def key(candidate: Candidate):
        width = len(candidate.rows[0]) if candidate.rows else 0
        return (-width, order.get(candidate.strategy, 99), len(candidate.sql or ""), candidate.strategy)

    return min(members, key=key)


def build_clusters(candidates: list[Candidate], spec: dict | None, strategy_order: tuple[str, ...]) -> list[Cluster]:
    groups = cluster_indices(candidates, spec)
    clusters = []
    for indexes in groups:
        members = [candidates[i] for i in indexes]
        rep = representative(members, strategy_order)
        clusters.append(Cluster(cluster_id="", members=members, kind="rows", rows=rep.rows, representative=rep))
    clusters.sort(key=lambda cluster: (
        -cluster.size,
        strategy_order.index(cluster.representative.strategy)
        if cluster.representative.strategy in strategy_order else 99,
        cluster.representative.sql or "",
    ))
    for i, cluster in enumerate(clusters):
        cluster.cluster_id = f"c{i}"
    return clusters


def parse_selector_response(text: str, valid_ids: set[str]) -> str | None:
    """Last CLUSTER id or ABSTAIN in the reply. Anything else is a decline."""
    found = list(_CHOICE.finditer(text or ""))
    if not found:
        return None
    last = found[-1]
    if not last.group(1):
        return None
    wanted = last.group(1)
    for cluster_id in valid_ids:
        if cluster_id.lower() == wanted.lower():
            return cluster_id
    return None


def parse_verifier_response(text: str) -> bool | None:
    found = list(_VERDICT.finditer(text or ""))
    if not found:
        return None
    return found[-1].group(0).upper() == "ACCEPT"


class LLMSelector:
    """One model call over the disagreeing clusters. ``choose`` returns a cluster id or None."""

    def __init__(self, llm, model: str):
        self.llm = llm
        self.model = model
        self.calls = 0

    def choose(self, question: str, clusters: list[Cluster]) -> str | None:
        self.calls += 1
        response = self.llm.complete(
            system="You select among executed SQL results. Reply with one line.",
            messages=[{"role": "user", "content": selector_user_message(question, clusters)}],
            tools=[],
            model=self.model,
            strategy=SELECTOR_STRATEGY,
        )
        return parse_selector_response(response.text, {cluster.cluster_id for cluster in clusters})


class LLMVerifier:
    def __init__(self, llm, model: str):
        self.llm = llm
        self.model = model
        self.calls = 0

    def accept(self, question: str, cluster: Cluster) -> bool:
        self.calls += 1
        response = self.llm.complete(
            system="You check one executed SQL result. Reply with one line.",
            messages=[{"role": "user", "content": verifier_user_message(question, cluster)}],
            tools=[],
            model=self.model,
            strategy=VERIFIER_STRATEGY,
        )
        return parse_verifier_response(response.text) is True


def _take(cluster: Cluster, **meta) -> Decision:
    rep = cluster.representative
    return Decision(
        sql=rep.sql,
        rows=list(rep.rows) if rep.rows is not None else None,
        columns=list(rep.columns) if rep.columns is not None else None,
        text=rep.text,
        chosen_strategy=rep.strategy,
        reason="selected",
        **meta,
    )


def _abstain(reason: str, **meta) -> Decision:
    return Decision(abstained=True, reason=reason, text=reason, **meta)


def _refuse(candidate: Candidate, **meta) -> Decision:
    return Decision(refused=True, text=candidate.text, reason="refused", **meta)


def select(
    candidates: list[Candidate],
    *,
    question: str,
    spec: dict | None,
    strategy_order: tuple[str, ...],
    mode: str,
    refusal_policy: str = "any_veto",
    min_successes: int = 1,
    pairwise=None,
    verifier=None,
) -> Decision:
    """``mode`` is ``majority``, ``majority_then_pairwise``, ``pairwise``, ``verifier``, or ``agree_or_abstain``."""
    if mode not in {"majority", "majority_then_pairwise", "pairwise", "verifier", "agree_or_abstain"}:
        raise ValueError(f"unknown selection mode {mode!r}")
    if refusal_policy not in {"any_veto", "majority", "ignore"}:
        raise ValueError(f"unknown refusal policy {refusal_policy!r}")

    clusters = build_clusters(candidates, spec, strategy_order)
    refusals = [c for c in candidates if c.kind == "refusal"]
    errors = [c for c in candidates if c.kind == "error"]
    successful = sum(cluster.size for cluster in clusters)
    meta = dict(
        cluster_sizes=[cluster.size for cluster in clusters],
        n_successful=successful,
        n_errors=len(errors),
        n_refusals=len(refusals),
        clusters=clusters,
    )

    if refusal_policy == "any_veto" and refusals:
        return _refuse(refusals[0], **meta)
    if not clusters:
        if refusals and refusal_policy != "ignore":
            return _refuse(refusals[0], **meta)
        return _abstain("no candidate executed", **meta)
    if successful < min_successes:
        return _abstain(f"only {successful} candidate(s) executed", **meta)

    top = clusters[0]
    strict_majority = top.size * 2 > successful

    if mode == "agree_or_abstain":
        if len(clusters) == 1 and top.size >= 2 and not (refusal_policy == "majority" and refusals):
            return _take(top, **meta)
        if len(clusters) == 1 and top.size < 2:
            return _abstain("fewer than two successes", **meta)
        return _abstain("candidates disagree", **meta)

    if refusal_policy == "majority" and refusals and len(refusals) * 2 > len(refusals) + successful:
        return _refuse(refusals[0], **meta)

    chosen: Cluster | None
    selector_called = False
    if mode == "pairwise" or (mode in {"majority_then_pairwise", "verifier"} and not strict_majority):
        selector_called = True
        if pairwise is None:
            return _abstain("no selector configured", selector_called=True, **meta)
        chosen_id = pairwise.choose(question, clusters)
        if chosen_id is None:
            return _abstain("selector declined", selector_called=True, **meta)
        chosen = next((cluster for cluster in clusters if cluster.cluster_id == chosen_id), None)
        if chosen is None:
            return _abstain("selector returned an unknown cluster", selector_called=True, **meta)
    elif strict_majority:
        chosen = top
    else:
        return _abstain("no strict majority", **meta)

    verifier_called = False
    if mode == "verifier":
        verifier_called = True
        if verifier is None or not verifier.accept(question, chosen):
            return _abstain("verifier rejected", selector_called=selector_called, verifier_called=True, **meta)

    return _take(chosen, selector_called=selector_called, verifier_called=verifier_called, **meta)
