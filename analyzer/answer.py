"""The analyzer's final result: an answer (SQL re-executed into rows), a refusal or a clarification."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AnalyzerAnswer:
    kind: str                                       # answer | refusal | clarification
    text: str = ""
    sql: str | None = None
    rows: list | None = None
    columns: list | None = None
    assumptions: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    lane: str = "sem"
    refused: bool = False
    model: str = ""
    usage: dict = field(default_factory=dict)
    trace: list = field(default_factory=list)
    audit: list = field(default_factory=list)
    questions: list = field(default_factory=list)   # clarification: contract-ready questions (tools.clarification_request)
