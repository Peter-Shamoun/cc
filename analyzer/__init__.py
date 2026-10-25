"""ClaimClarity analyst agent.

The analyzer answers a billing question by querying one customer's lake through
``ccsim.query`` (token, STS, per-request DuckDB). Model-written SQL always goes
through ``bench.guard``. Escalation to ``lossless.*`` is a separate audited
request that opens a new session; it is not a flag on the guard.
"""

from analyzer.answer import AnalyzerAnswer
from analyzer.config import AnalyzerConfig
from analyzer.llm import AnthropicLLM, BedrockLLM, PaidAPIDisabled, ScriptedLLM
from analyzer.loop import Analyzer
from analyzer.opener import QueryPathOpener, SessionHandle, SnapshotOpener

__all__ = [
    "Analyzer",
    "AnalyzerAnswer",
    "AnalyzerConfig",
    "AnthropicLLM",
    "BedrockLLM",
    "PaidAPIDisabled",
    "QueryPathOpener",
    "ScriptedLLM",
    "SessionHandle",
    "SnapshotOpener",
]
