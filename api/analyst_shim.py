"""Analyst-agent model shims (adapter spec R7). **Stub**: not wired into ``api.app``; tests use fixtures only.

The analyst agents under ``analyst/`` each define their own model interface, and none of them is the analyzer's
``complete(**kw) -> analyzer.llm.Completion`` that ``LiveTap`` observes. A shim implements the *agent's* interface on top
of a ``LiveTap`` (or any object with the analyzer's ``complete``), so every model call of an analyst run produces the
same ``step.*`` / ``escalation`` / ``reasoning.*`` events as an analyzer run, and the run can be framed with
``api.analyzer_adapter.run_to_events``. The shims only translate; they never pick a backend, so whether a paid model is
behind the tap is decided by ``build_real_llm`` and its switch, which stays off.

Interfaces covered (read from analyst/ without changing it):

==========================  ===============================================================  ===================
agent                        model interface                                                  shim
==========================  ===============================================================  ===================
variants/multi_candidate     ``complete(*, system, messages, tools, model, strategy)``        ``ToolLoopShim``
                             → ``LLMResponse(text, tool_calls[ToolCall(name, arguments,
                             call_id)], usage, stop_reason)``
approaches/a05_verifier      ``complete(*, role, system, user, phase) → ModelCompletion(text)``  ``TextRoleShim``
variants/context_retrieval   ``complete(LLMRequest(question_id, question, system, user,       ``SingleShotShim``
                             tools)) → LLMResponse(sql, text, refused, usage)``
==========================  ===============================================================  ===================

Candidate indices: ``LiveTap`` treats a model change *within one candidate index* as an escalation and only streams
candidate 0's text as reasoning, so judge/critic roles get their own index (after the generator candidates) and are
never mistaken for an escalation or for the analyst's reasoning.

The agent's response classes are imported lazily from ``analyst`` (read-only); tests may inject their own.
"""
from __future__ import annotations

from typing import Any, Callable

DEFAULT_MAX_TOKENS = 4096


def _analyzer_tool_calls(completion) -> list:
    return list(getattr(completion, "tool_calls", None) or [])


class ToolLoopShim:
    """``analyst.variants.multi_candidate`` → analyzer ``complete``.

    ``strategy`` (the agent's candidate id) becomes ``candidate_index`` = its position in ``strategies``; the selector
    and verifier judges (``__selector__`` / ``__verifier__``) get ``len(strategies)`` and ``len(strategies) + 1``.
    Final SQL is executed inside the agent, so the shim remembers each candidate's ``final_answer`` SQL:
    ``candidate_for_sql(answer.sql)`` gives the ``candidate_index`` to put on the ``AnalyzerAnswer`` so ``LiveTap.finish``
    attaches the row count to the chosen candidate's final step."""

    JUDGES = ("__selector__", "__verifier__")

    def __init__(self, tap: Any, strategies: tuple[str, ...] | list[str], *, max_tokens: int = DEFAULT_MAX_TOKENS,
                 response_cls: Callable[..., Any] | None = None, tool_call_cls: Callable[..., Any] | None = None):
        if response_cls is None or tool_call_cls is None:
            from analyst.variants.multi_candidate.llm import LLMResponse, ToolCall
            response_cls, tool_call_cls = response_cls or LLMResponse, tool_call_cls or ToolCall
        self.tap = tap
        self.strategies = tuple(strategies)
        self.max_tokens = max_tokens
        self._response = response_cls
        self._tool_call = tool_call_cls
        self.final_sql: dict[int, str] = {}

    def index_for(self, strategy: str) -> int:
        if strategy in self.strategies:
            return self.strategies.index(strategy)
        if strategy in self.JUDGES:
            return len(self.strategies) + self.JUDGES.index(strategy)
        raise ValueError(f"unknown candidate strategy {strategy!r}")

    def complete(self, *, system: str, messages: list, tools: list, model: str, strategy: str):
        index = self.index_for(strategy)
        completion = self.tap.complete(model=model, system=system, messages=messages, tools=tools,
                                       max_tokens=self.max_tokens, candidate_index=index)
        calls = []
        for call in _analyzer_tool_calls(completion):
            if call.name == "final_answer" and index < len(self.strategies):
                self.final_sql[index] = str((call.arguments or {}).get("sql") or "")
            calls.append(self._tool_call(call.name, dict(call.arguments or {}), call.id))
        return self._response(text=completion.text or "", tool_calls=calls, usage=dict(completion.usage or {}),
                              stop_reason=completion.stop_reason or "end_turn")

    def candidate_for_sql(self, sql: str | None) -> int:
        """The candidate whose final SQL is ``sql`` (first match), else 0."""
        norm = " ".join(str(sql or "").split())
        return next((i for i, s in sorted(self.final_sql.items()) if " ".join(s.split()) == norm), 0)


class TextRoleShim:
    """``analyst.approaches.a05_verifier`` (``LanguageModel``) → analyzer ``complete``.

    ``role_models`` maps the protocol role ('analyst', 'critic') to a model id; ``roles`` fixes the candidate index of
    each role (analyst 0, critic 1). The a05 protocol runs its SQL through its own executor, not through model tool
    calls, so this shim yields only reasoning text (subject to the R2 policy) and usage; step events for a05 need an
    executor tap (spec R7, open)."""

    def __init__(self, tap: Any, role_models: dict[str, str], *, roles: tuple[str, ...] = ("analyst", "critic"),
                 max_tokens: int = DEFAULT_MAX_TOKENS, completion_cls: Callable[..., Any] | None = None):
        if completion_cls is None:
            from analyst.approaches.a05_verifier.backend import ModelCompletion
            completion_cls = ModelCompletion
        self.tap = tap
        self.role_models = dict(role_models)
        self.roles = tuple(roles)
        self.max_tokens = max_tokens
        self._completion = completion_cls

    def complete(self, *, role: str, system: str, user: str, phase: str):
        if role not in self.roles or role not in self.role_models:
            raise ValueError(f"unknown role {role!r}")
        completion = self.tap.complete(model=self.role_models[role], system=system,
                                       messages=[{"role": "user", "content": user}], tools=[],
                                       max_tokens=self.max_tokens, candidate_index=self.roles.index(role))
        return self._completion(text=completion.text or "")


class SingleShotShim:
    """``analyst.variants.context_retrieval`` → analyzer ``complete``.

    The agent wants one ``LLMResponse(sql, text, refused, usage)`` per question. The analyzer-protocol model answers
    with a ``final_answer {sql, answer}`` or ``refuse {reason}`` tool call; either becomes the response. A completion
    without either is returned as text only (``sql=None``), which the agent treats as no answer."""

    def __init__(self, tap: Any, model: str, *, max_tokens: int = DEFAULT_MAX_TOKENS,
                 response_cls: Callable[..., Any] | None = None):
        if response_cls is None:
            from analyst.variants.context_retrieval.llm import LLMResponse
            response_cls = LLMResponse
        self.tap = tap
        self.model = model
        self.max_tokens = max_tokens
        self._response = response_cls

    def complete(self, request: Any):
        completion = self.tap.complete(model=self.model, system=request.system,
                                       messages=[{"role": "user", "content": request.user}],
                                       tools=list(request.tools or []), max_tokens=self.max_tokens, candidate_index=0)
        usage = dict(completion.usage or {})
        for call in _analyzer_tool_calls(completion):
            args = dict(call.arguments or {})
            if call.name == "refuse":
                return self._response(sql=None, text=str(args.get("reason") or ""), refused=True, usage=usage)
            if call.name == "final_answer":
                return self._response(sql=str(args.get("sql") or "") or None, text=str(args.get("answer") or ""),
                                      refused=False, usage=usage)
        return self._response(sql=None, text=completion.text or "", refused=False, usage=usage)
