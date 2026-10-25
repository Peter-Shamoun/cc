"""The analyst's final result, in the shape the benchmark scorer and judge consume.

``bench.judge.judge`` takes candidate SQL and the rows that SQL returned.
``bench.agent_session.score_session`` reads ``final.json`` with kind
``answer`` (sql + text), ``refusal``, or ``clarification``. Abstention is a
separate kind so a low-confidence run is not recorded as a policy refusal.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AnalyzerAnswer:
    kind: str
    text: str = ""
    sql: str | None = None
    rows: list | None = None
    columns: list | None = None
    assumptions: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    verification_blocked: bool = False
    lane: str = "sem"
    refused: bool = False
    model: str = ""
    candidate_index: int | None = None
    usage: dict = field(default_factory=dict)
    trace: list = field(default_factory=list)
    audit: list = field(default_factory=list)
    selection: dict | None = None
    candidate_results: list | None = None

    def for_judge(self) -> dict:
        """Arguments that line up with ``bench.judge.judge``'s candidate side.

        Refusals and abstentions still return the dict; the judge is only
        meaningful when ``kind == "answer"`` and ``cand_sql`` reads a table.
        """
        return {
            "cand_sql": self.sql or "",
            "cand_rows": self.rows or [],
            "cand_text": self.text,
            "kind": self.kind,
        }

    def to_final_json(self) -> dict:
        """The object ``bench.agent_session.score_session`` reads from final.json."""
        if self.kind == "refusal":
            return {"kind": "refusal", "text": self.text}
        if self.kind == "clarification":
            return {"kind": "clarification", "text": self.text}
        if self.kind == "abstain":
            return {"kind": "abstain", "text": self.text}
        if self.kind != "answer":
            return {"kind": self.kind, "text": self.text}
        return {"kind": "answer", "sql": self.sql, "text": self.text, "via": "sql", "lane": self.lane}
