"""Real-analyzer adapter (contract 1.12.0+): analyzer/analyst outputs → contract stream events.

Spec: ``docs/frontend-analyzer-adapter.md``. This module is the **stub**. It does not change how the server runs.

* ``events_for_recording(recording)`` is a pure, offline replay. A *recording* is what a real analyzer run produces:
  each model call (model, candidate index, the ``tool_result`` blocks it was sent, and the ``Completion`` it returned)
  plus the final ``AnalyzerAnswer``. It drives the same ``LiveTap`` → ``outcome_parts`` → ``emit_parts`` path as
  ``POST /api/chat``, and returns the full contract stream with ``seq``. Tests feed it **fixtures only**
  (``api/tests/fixtures/analyzer_runs/*.json``), and the output must pass ``api.contract.conformance.check``.
* ``build_real_llm(kind)`` sits behind the **paid-API switch**, which is **off by default**. Unless
  ``CC_API_ALLOW_PAID_API`` equals ``PAID_SWITCH_VALUE`` *and* the caller passes ``enable=True``, it raises the
  analyzer's ``PaidAPIDisabled`` before any client is built. Nothing in ``api.app`` calls it. Wiring it in is a
  separate, reviewed change (see the spec, "Rollout").
* Reasoning text never streams as-is (spec R2). Every replay/run goes through ``api.reasoning_policy.ReasoningPolicy``:
  ``"drop"`` (default) or ``"summary"`` (labels and counts only).
* ``strategy_config`` (``api.strategy_config``) maps a contract strategy, model choice and 384-grid cell to
  ``AnalyzerConfig`` overrides (spec R4). ``api.analyst_shim`` adapts the analyst agents' model interfaces to the
  analyzer's ``complete(**kw) -> Completion`` so ``LiveTap`` can observe them (spec R7).
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from analyzer.answer import AnalyzerAnswer
from analyzer.llm import Completion, PaidAPIDisabled, ToolCall

from api.capabilities import DEFAULT_STRATEGY, STRATEGY_IDS
from api.contract import CONTRACT_VERSION
from api.live import Cancelled, LiveTap
from api.mapping import outcome_parts
from api.reasoning_policy import DEFAULT_POLICY, ReasoningPolicy
from api.sql_mask import DEFAULT_POLICY as SQL_MASK_DEFAULT, mask_part, masking_emit
from api.store import DownloadRegistry, Principal
from api.plot import gate_parts
from api.stream_parts import emit_parts

PAID_SWITCH_ENV = "CC_API_ALLOW_PAID_API"
#: A deliberate phrase, not "1"/"true", so a copied .env or a typo can't turn it on.
PAID_SWITCH_VALUE = "i-understand-this-calls-a-paid-model-api"
REAL_KINDS = ("anthropic", "bedrock")


def paid_api_enabled(env: Mapping[str, str] | None = None) -> bool:
    """The paid-API switch. Off unless the env var holds the exact phrase."""
    return (os.environ if env is None else env).get(PAID_SWITCH_ENV) == PAID_SWITCH_VALUE


def build_real_llm(kind: str, *, enable: bool = False, env: Mapping[str, str] | None = None,
                   factories: Mapping[str, Callable[..., Any]] | None = None):
    """A real model client for the analyzer, or ``PaidAPIDisabled``.

    Both ``enable=True`` (code) and the env switch (deployment) are required. ``factories`` lets tests inject
    fakes; the defaults are the analyzer's ``AnthropicLLM``/``BedrockLLM``, which are imported lazily."""
    if kind not in REAL_KINDS:
        raise ValueError(f"unknown real llm backend {kind!r}")
    if not (enable and paid_api_enabled(env)):
        raise PaidAPIDisabled(f"Paid model APIs are disabled (set {PAID_SWITCH_ENV} and enable=True to opt in).")
    if factories is None:
        from analyzer.llm import AnthropicLLM, BedrockLLM
        factories = {"anthropic": AnthropicLLM, "bedrock": BedrockLLM}
    return factories[kind](allow_paid_api=True)


def answer_from_bench(answer: Any, *, columns: list[str] | None = None, model: str = "") -> AnalyzerAnswer:
    """Normalize an ``analyst/*`` agent result (``bench.agents.Answer``: sql, rows, text, refused, usage, trace) into
    the ``AnalyzerAnswer`` the mapping consumes. Duck-typed, so nothing under bench/ or analyst/ is imported.

    refused → ``refusal``; no rows (abstained or no verified result) → ``abstain`` with the reason as details;
    otherwise ``answer``. ``bench.Answer`` carries no column names: pass them from the executor (cursor description).
    Without them the columns are ``col_1..n``, which is valid but unlabelled."""
    text = str(getattr(answer, "text", "") or "")
    usage = dict(getattr(answer, "usage", None) or {})
    trace = list(getattr(answer, "trace", None) or [])
    if getattr(answer, "refused", False):
        return AnalyzerAnswer(kind="refusal", text=text, refused=True, model=model, usage=usage, trace=trace)
    rows = getattr(answer, "rows", None)
    if rows is None:
        return AnalyzerAnswer(kind="abstain", text=text, model=model, usage=usage, trace=trace,
                              selection=usage.get("selection"))
    rows = [list(r) for r in rows]
    width = max((len(r) for r in rows), default=len(columns or []))
    cols = list(columns) if columns and len(columns) == width else [f"col_{i + 1}" for i in range(width)]
    return AnalyzerAnswer(kind="answer", text=text, sql=getattr(answer, "sql", None), rows=rows, columns=cols,
                          model=model, usage=usage, trace=trace, selection=usage.get("selection"))


# -- recordings -----------------------------------------------------------------------------------------------

@dataclass
class RecordedCall:
    """One ``llm.complete`` call of a real run."""
    model: str
    completion: dict                     # {text, tool_calls: [{name, arguments, id}], stop_reason?, usage?}
    candidate: int = 0
    tool_results: list = field(default_factory=list)  # [{tool_use_id, content}] sent with this call


@dataclass
class Recording:
    question: str
    calls: list[RecordedCall]
    answer: dict                         # AnalyzerAnswer fields
    multi_candidate: bool = False
    strategy: str | None = None          # the capabilities strategy id the run used, when recorded

    @classmethod
    def from_json(cls, data: dict) -> "Recording":
        return cls(question=data["question"], calls=[RecordedCall(**c) for c in data["calls"]],
                   answer=data["answer"], multi_candidate=bool(data.get("multiCandidate")),
                   strategy=data.get("strategy"))

    @property
    def escalated(self) -> bool:
        """A model change within one candidate (how ``LiveTap`` detects an escalation)."""
        seen: dict[int, str] = {}
        for c in self.calls:
            if seen.setdefault(c.candidate, c.model) != c.model:
                return True
        return False


def adapter_strategy(strategy: str | None = None, *, multi_candidate: bool = False, escalated: bool = False) -> str:
    """The ``message.start.strategy`` for an adapter run: always a ``GET /api/capabilities`` strategy id.

    An explicit id (the request's ``options.strategy``) wins and must be known. Otherwise it is inferred from the
    run's shape: several candidates → ``candidates``; a model change mid-run → ``cascade``; else the default."""
    if strategy is not None:
        if strategy not in STRATEGY_IDS:
            raise ValueError(f"unknown strategy {strategy!r}; expected one of {sorted(STRATEGY_IDS)}")
        return strategy
    if multi_candidate:
        return "candidates"
    return "cascade" if escalated else DEFAULT_STRATEGY


class _ReplayLLM:
    """Returns the recorded completions in order. No network, no model."""

    def __init__(self, calls: list[RecordedCall]):
        self._calls = iter(calls)
        self.calls: list[dict] = []

    def complete(self, **kw) -> Completion:
        rec = next(self._calls)
        c = rec.completion
        self.calls.append({"model": kw.get("model"), "candidate_index": kw.get("candidate_index")})
        return Completion(text=c.get("text") or "", stop_reason=c.get("stop_reason") or "tool_use",
                          usage=dict(c.get("usage") or {}), model=rec.model,
                          tool_calls=[ToolCall(name=t["name"], arguments=dict(t.get("arguments") or {}), id=t["id"])
                                      for t in c.get("tool_calls") or []])


def events_for_recording(recording: Recording, *, principal: Principal | None = None,
                         downloads: DownloadRegistry | None = None, message_id: str = "m_rec",
                         conversation_id: str = "c_rec", parent_id: str = "u_rec",
                         cancel_after_call: int | None = None, reasoning: str = DEFAULT_POLICY,
                         sql_masking: str = SQL_MASK_DEFAULT) -> list[dict]:
    """Replay a recorded analyzer run into the contract stream that ``POST /api/chat`` would send.

    ``cancel_after_call`` simulates Stop after N model calls (the stream must finish "aborted").
    ``reasoning`` is the R2 policy for model text: ``"drop"`` (default) or ``"summary"``."""
    cancelled = threading.Event()

    def run(tap: LiveTap) -> AnalyzerAnswer:
        for n, rec in enumerate(recording.calls):
            if cancel_after_call is not None and n >= cancel_after_call:
                cancelled.set()
            blocks = [{"type": "tool_result", "tool_use_id": r["tool_use_id"], "content": r["content"]}
                      for r in rec.tool_results]
            messages = [{"role": "user", "content": blocks if blocks else recording.question}]
            tap.complete(model=rec.model, candidate_index=rec.candidate, messages=messages, system="", tools=[],
                         max_tokens=1)
        return AnalyzerAnswer(**recording.answer)

    return run_to_events(recording.question, run, _ReplayLLM(recording.calls),
                         model=recording.calls[0].model if recording.calls else "none",
                         multi_candidate=recording.multi_candidate, cancelled=cancelled, principal=principal,
                         downloads=downloads, message_id=message_id, conversation_id=conversation_id,
                         parent_id=parent_id, reasoning=reasoning,
                         strategy=adapter_strategy(recording.strategy, multi_candidate=recording.multi_candidate,
                                                   escalated=recording.escalated),
                         sql_masking=sql_masking)


def run_to_events(question: str, run: Callable[[LiveTap], AnalyzerAnswer], inner_llm: Any, *, model: str,
                  multi_candidate: bool = False, cancelled: threading.Event | None = None,
                  principal: Principal | None = None, downloads: DownloadRegistry | None = None,
                  message_id: str = "m_rec", conversation_id: str = "c_rec", parent_id: str = "u_rec",
                  strategy: str | None = None, reasoning: str = DEFAULT_POLICY,
                  sql_masking: str = SQL_MASK_DEFAULT) -> list[dict]:
    """The shared adapter pipeline: ``run(tap)`` drives an analyzer/analyst run whose model calls go through ``tap``
    (a ``LiveTap`` around ``inner_llm``) and returns the ``AnalyzerAnswer``; this function frames it as the full
    contract stream (``message.start`` … ``message.end``, with ``seq``). Model text is filtered by ``reasoning``.
    ``strategy`` is a capabilities id (``adapter_strategy``); ``None`` infers it from ``multi_candidate``."""
    strategy = adapter_strategy(strategy, multi_candidate=multi_candidate)
    events: list[dict] = []

    def raw_emit(payload: dict):
        events.append({**payload, "seq": len(events)})

    emit = masking_emit(ReasoningPolicy(raw_emit, reasoning), sql_masking)
    principal = principal or Principal("acme_labs", "ana@xifin.example")
    downloads = downloads or DownloadRegistry()
    cancelled = cancelled or threading.Event()
    emit({"type": "message.start", "messageId": message_id, "conversationId": conversation_id,
          "contractVersion": CONTRACT_VERSION, "model": model, "parentId": parent_id, "strategy": strategy})
    emit({"type": "status", "label": "Thinking"})
    tap = LiveTap(inner_llm, emit, cancelled=cancelled, multi_candidate=multi_candidate)
    finish = "stop"
    parts: list[dict] = []
    try:
        answer = run(tap)
        tap.finish(answer)
        parts, finish = outcome_parts(answer, principal, downloads.scoped(conversation_id), elapsed_ms=0,
                                      question=question, ask_args=tap.ask_args, refuse_args=tap.refuse_args)
    except Cancelled:
        tap.abort()
        finish = "aborted"
    except Exception:  # noqa: BLE001 - mirrors api.app: never echo internals; the stream still ends cleanly
        tap.finish(None)
        parts = [{"type": "error", "id": "p_adapter_error", "code": "internal", "title": "Something went wrong",
                  "message": "The analysis failed. Try again.", "retryable": True}]
        finish = "error"
    emit({"type": "status", "label": "Writing answer" if finish == "stop" else None})
    parts = [mask_part(p, sql_masking) for p in parts]
    parts, _dropped = gate_parts(parts)  # 1.18.0: validate every PlotSpec before emitting
    if finish != "aborted" and not emit_parts(parts, emit, cancelled=cancelled):
        finish = "aborted"
    emit({"type": "status", "label": None})
    emit({"type": "message.end", "messageId": message_id, "finishReason": finish,
          "usage": tap.usage(0, None, None)})
    return events
