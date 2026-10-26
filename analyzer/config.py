"""The analyzer's limits. One configuration ships (bundle B1, ``analyzer.b1_commands.B1_CONFIG``)."""

from __future__ import annotations

from dataclasses import dataclass

HAIKU_MODEL = "claude-haiku-5-5"


@dataclass(frozen=True)
class AnalyzerConfig:
    #: commands the model may run besides the final one (failed commands do not count)
    max_tool_calls: int = 12
    #: rows a query may return (the guard adds the LIMIT)
    row_limit: int = 1000
    #: rows of a result the model is shown
    rows_shown_to_model: int = 20
    #: per query; the whole question also has ``tools.MAX_TOTAL_QUERY_SECONDS_PER_SESSION``
    time_limit_seconds: float = 25.0
    #: solved TRAIN examples in the first user message
    examples_k: int = 3
    model: str = HAIKU_MODEL
