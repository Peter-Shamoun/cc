"""In-process multi-candidate analyst.

``answer(question, tools)`` matches ``bench.agents`` so the harness can load
``analyst.variants.multi_candidate.agent:MultiCandidateAgent``. Each candidate is a short tool loop
on a different prompt. Final SQL is executed through ``tools.sql`` (guarded). Clustering and
selection are deterministic given those rows and, when the pool disagrees, one selector call.

Candidates run one after another on the single connection the harness passes in. The latency model
in ``cost.py`` is what N parallel sessions would cost on the 120-day lake; this class does not open
extra sessions.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from bench.agent_session import _reads_a_table
from bench.agents import Answer

from analyst.variants.multi_candidate.equivalence import cluster_spec_for_question
from analyst.variants.multi_candidate.llm import LLMResponse, ToolCall, UnconfiguredLLM
from analyst.variants.multi_candidate.prompts import (
    ROWS_SHOWN_TO_MODEL,
    STRATEGY_ORDER,
    TOOL_SPEC,
    shared_system,
    strategies_for,
    user_message,
)
from analyst.variants.multi_candidate.screen import SCREEN_ARMS
from analyst.variants.multi_candidate.selector import (
    Candidate,
    Decision,
    LLMSelector,
    LLMVerifier,
    select,
)

DEFAULTS = {
    "n_candidates": 3,
    "strategies": None,
    "selection": "majority_then_pairwise",
    "selector_model": "claude-sonnet-5-5",
    "generator_model": "scripted",
    "max_turns_per_candidate": 4,
    "refusal_policy": "any_veto",
    "min_successes": 1,
    "repair_on_error": 0,
}


def _usage_zero() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }


def _add_usage(total: dict, part: dict | None) -> None:
    for key in _usage_zero():
        total[key] += int((part or {}).get(key, 0))


class _UsageTap:
    """Adds every ``complete`` call's token usage to one bucket, including the selector turn."""

    def __init__(self, inner, bucket: dict):
        self.inner = inner
        self.bucket = bucket

    def complete(self, **kwargs) -> LLMResponse:
        response = self.inner.complete(**kwargs)
        _add_usage(self.bucket, response.usage)
        return response


def resolved_config(**overrides) -> dict:
    config = dict(DEFAULTS)
    arm_name = os.environ.get("CC_MC_ARM")
    if arm_name:
        if arm_name not in SCREEN_ARMS:
            raise KeyError(f"unknown CC_MC_ARM {arm_name!r}")
        config.update(SCREEN_ARMS[arm_name])
    config_path = os.environ.get("CC_MC_CONFIG")
    if config_path:
        config.update(json.loads(Path(config_path).read_text()))
    config.update({k: v for k, v in overrides.items() if v is not None or k in overrides})
    # Explicit overrides win, including those passed positionally by tests. Environment fills only
    # what the caller left at the default. ``overrides`` already applied last, so a test that sets
    # n_candidates is not replaced by the environment. Callers that want the arm pass nothing.
    return config


def _preview(columns, rows) -> str:
    shown = [list(row) for row in rows[:ROWS_SHOWN_TO_MODEL]]
    return json.dumps(
        {"columns": columns, "row_count": len(rows), "rows": shown, "truncated": len(rows) > ROWS_SHOWN_TO_MODEL},
        default=str,
    )


class MultiCandidateAgent:
    def __init__(self, llm=None, *, pairwise=None, verifier=None, **overrides):
        self.config = resolved_config(**overrides)
        self.llm = llm if llm is not None else UnconfiguredLLM()
        self.pairwise_override = pairwise
        self.verifier_override = verifier
        n = int(self.config["n_candidates"])
        named = self.config.get("strategies")
        self.strategies = tuple(named) if named else strategies_for(n)
        if len(self.strategies) != n:
            raise ValueError(f"n_candidates={n} but {len(self.strategies)} strategies were named")
        unknown = [name for name in self.strategies if name not in STRATEGY_ORDER]
        if unknown:
            raise ValueError(f"unknown strategies {unknown}")
        self.model = self.config.get("generator_model") or "scripted"
        self.selector_model = self.config.get("selector_model") or "claude-sonnet-5-5"
        self.max_turns = int(self.config.get("max_turns_per_candidate") or 4)
        if int(self.config.get("repair_on_error") or 0) != 0:
            raise ValueError("repair_on_error must stay 0 in this variant; repair is a separate screen factor")

    def answer(self, question, tools) -> Answer:
        schema = tools.describe()
        system = shared_system(schema)
        usage = _usage_zero()
        llm = _UsageTap(self.llm, usage)
        candidates = [
            self._run_candidate(strategy, question, system, tools, llm) for strategy in self.strategies
        ]
        spec = cluster_spec_for_question(question)
        mode = self.config["selection"]
        pairwise = self.pairwise_override
        verifier = self.verifier_override
        if pairwise is None and mode in {"majority_then_pairwise", "pairwise", "verifier"}:
            pairwise = LLMSelector(llm, self.selector_model)
        if verifier is None and mode == "verifier":
            verifier = LLMVerifier(llm, self.selector_model)
        decision = select(
            candidates,
            question=question,
            spec=spec,
            strategy_order=self.strategies,
            mode=mode,
            refusal_policy=self.config.get("refusal_policy") or "any_veto",
            min_successes=int(self.config.get("min_successes") or 1),
            pairwise=pairwise,
            verifier=verifier,
        )
        usage["selection"] = _selection_usage(decision, candidates, spec)
        trace = [candidate.sql for candidate in candidates if candidate.sql]
        if decision.refused:
            return Answer(refused=True, text=decision.text, usage=usage, trace=trace)
        if decision.abstained or decision.rows is None:
            return Answer(text=decision.reason, usage=usage, trace=trace)
        return Answer(sql=decision.sql, rows=decision.rows, text=decision.text, usage=usage, trace=trace)

    def _run_candidate(self, strategy: str, question: str, system: str, tools, llm) -> Candidate:
        messages = [{"role": "user", "content": user_message(strategy, question)}]
        exploratory: list[str] = []
        candidate_usage = _usage_zero()
        for _turn in range(self.max_turns):
            response = llm.complete(
                system=system, messages=messages, tools=TOOL_SPEC, model=self.model, strategy=strategy,
            )
            _add_usage(candidate_usage, response.usage)
            if not response.tool_calls:
                return Candidate(
                    strategy=strategy, error="model finished without a tool call",
                    text=response.text, exploratory_sql=exploratory, usage=candidate_usage,
                )
            messages.append({"role": "assistant", "content": _assistant_content(response)})
            tool_results = []
            for call in response.tool_calls:
                if call.name == "refuse":
                    return Candidate(
                        strategy=strategy, refused=True, text=str(call.arguments.get("reason", "")),
                        exploratory_sql=exploratory, usage=candidate_usage,
                    )
                if call.name == "final_answer":
                    return self._finalize(strategy, call, tools, exploratory, candidate_usage)
                if call.name == "run_sql":
                    sql = str(call.arguments.get("sql", ""))
                    exploratory.append(sql)
                    try:
                        columns, rows = tools.sql(sql)
                        content = _preview(columns, rows)
                    except Exception as exc:  # noqa: BLE001 - the text goes back to the model
                        content = f"ERROR: {str(exc)[:500]}"
                    tool_results.append({"type": "tool_result", "tool_use_id": call.call_id, "content": content})
                else:
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": call.call_id,
                        "content": f"ERROR: unknown tool {call.name}",
                    })
            if not tool_results:
                break
            messages.append({"role": "user", "content": tool_results})
        return Candidate(
            strategy=strategy, error=f"stopped after {self.max_turns} turns",
            exploratory_sql=exploratory, usage=candidate_usage,
        )

    def _finalize(self, strategy: str, call: ToolCall, tools, exploratory: list[str], usage: dict) -> Candidate:
        sql = str(call.arguments.get("sql", ""))
        text = str(call.arguments.get("answer", ""))
        if not _reads_a_table(sql):
            return Candidate(
                strategy=strategy, sql=sql, text=text, error="final SQL does not read a table",
                exploratory_sql=exploratory, usage=usage,
            )
        try:
            columns, rows = tools.sql(sql)
        except Exception as exc:  # noqa: BLE001 - a bad candidate drops out of the vote
            return Candidate(
                strategy=strategy, sql=sql, text=text, error=str(exc)[:500],
                exploratory_sql=exploratory, usage=usage,
            )
        return Candidate(
            strategy=strategy, sql=sql, columns=columns, rows=[tuple(row) for row in rows], text=text,
            exploratory_sql=exploratory, usage=usage,
        )


def _assistant_content(response: LLMResponse):
    if response.text and not response.tool_calls:
        return response.text
    blocks = []
    if response.text:
        blocks.append({"type": "text", "text": response.text})
    for call in response.tool_calls:
        blocks.append({"type": "tool_use", "id": call.call_id, "name": call.name, "input": call.arguments})
    return blocks or response.text


def _selection_usage(decision: Decision, candidates: list[Candidate], spec: dict) -> dict:
    return {
        "abstained": decision.abstained,
        "refused": decision.refused,
        "reason": decision.reason,
        "chosen_strategy": decision.chosen_strategy,
        "selector_called": decision.selector_called,
        "verifier_called": decision.verifier_called,
        "cluster_sizes": decision.cluster_sizes,
        "n_successful": decision.n_successful,
        "n_errors": decision.n_errors,
        "n_refusals": decision.n_refusals,
        "cluster_spec": {k: v for k, v in spec.items()},
        "candidates": [
            {
                "strategy": candidate.strategy,
                "kind": candidate.kind,
                "sql": candidate.sql,
                "row_count": len(candidate.rows) if candidate.rows is not None else None,
                "error": candidate.error,
            }
            for candidate in candidates
        ],
    }
