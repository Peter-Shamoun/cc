"""Understand, link the schema, query, repair, verify, answer.

One trajectory is a tool loop. ``candidates`` runs several trajectories and keeps
the result a majority of them actually returned (not the SQL text). ``cascade``
reruns a failed trajectory on the next model. Refusals are not failures: a
decision to refuse is not sent to Sonnet.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict

from analyzer.answer import AnalyzerAnswer
from analyzer.checks import blocking_warnings
from analyzer.config import AnalyzerConfig
from analyzer.prompts import system_prompt, user_message
from analyzer.tools import ToolError, ToolLayer, jsonable, tool_spec

_LOSSLESS = re.compile(r"\blossless\.", re.IGNORECASE)
_TERMINAL = {"final_answer", "refuse", "ask"}


class Analyzer:
    def __init__(self, opener, llm, config: AnalyzerConfig | None = None, *,
                 question_id: str | None = None, question_family: str | None = None,
                 example_library: list | None = None):
        self.opener = opener
        self.llm = llm
        self.config = config or AnalyzerConfig()
        self.question_id = question_id
        self.question_family = question_family
        self.example_library = example_library

    def answer(self, question: str) -> AnalyzerAnswer:
        produced = [self._run_candidate(question, index) for index in range(self.config.candidates)]
        chosen = _select(produced, self.config.candidate_selection)
        if self.config.candidates > 1:
            chosen.candidate_results = [_candidate_summary(item) for item in produced]
            chosen.selection = {
                "mode": self.config.candidate_selection,
                "candidates": len(produced),
                "answered": sum(item.kind == "answer" for item in produced),
            }
        return chosen

    def _run_candidate(self, question: str, candidate_index: int) -> AnalyzerAnswer:
        models = list(self.config.cascade_models) if self.config.cascade == "haiku_then_sonnet" else [self.config.model]
        last = None
        for model in models:
            last = self._trajectory(question, model, candidate_index)
            if not _should_cascade(last, self.config):
                return last
        return last

    def _trajectory(self, question: str, model: str, candidate_index: int) -> AnalyzerAnswer:
        config = self.config
        tools = ToolLayer(self.opener, config, question_id=self.question_id,
                          question_family=self.question_family, example_library=self.example_library)
        dictionary_text = ""
        profile_text = ""
        if config.data_dictionary:
            try:
                dictionary_text = json.dumps(tools.lookup_dictionary(charge=False), default=str)
            except ToolError as exc:
                dictionary_text = f"(dictionary unavailable: {exc})"
        if config.data_profile:
            try:
                profile_text = json.dumps(tools.profile(charge=False), default=str)
            except ToolError as exc:
                profile_text = f"(profile unavailable: {exc})"
        spec = tool_spec(config)
        system = system_prompt(config, dictionary_text=dictionary_text, profile_text=profile_text)
        messages = [{"role": "user", "content": user_message(question, config, candidate_index)}]
        usage = {"input_tokens": 0, "output_tokens": 0}
        trace: list[dict] = []
        repairs = 0
        for _turn in range(config.max_turns):
            completion = self.llm.complete(
                model=model, system=system, messages=messages, tools=spec,
                max_tokens=config.max_tokens, effort=config.effort, prompt_cache=config.prompt_cache,
                candidate_index=candidate_index)
            usage["input_tokens"] += int(completion.usage.get("input_tokens") or 0)
            usage["output_tokens"] += int(completion.usage.get("output_tokens") or 0)
            if not completion.tool_calls:
                return _finish(kind="no_answer", text=completion.text or "no tool call", model=model,
                               candidate_index=candidate_index, usage=usage, trace=trace, audit=tools.audit,
                               warnings=[])
            messages.append({"role": "assistant", "content": _assistant_blocks(completion)})
            results = []
            terminal: AnalyzerAnswer | None = None
            for call in completion.tool_calls:
                if call.name in _TERMINAL:
                    outcome, terminal_answer, repair_note = self._terminal(
                        call, tools, repairs, model, candidate_index, usage, trace)
                    if repair_note:
                        repairs += 1
                        results.append(_tool_result(call.id, repair_note))
                        trace.append({"tool": call.name, "ok": False, "summary": repair_note[:300]})
                        continue
                    terminal = terminal_answer
                    break
                try:
                    payload = tools.dispatch(call.name, call.arguments)
                    if call.name == "run_sql" and config.self_correct == "errors+result_signals" and payload.get("warnings"):
                        payload = {**payload, "revise_if": "a warning means this result does not answer the question"}
                    content = json.dumps(payload, default=str)
                    trace.append({"tool": call.name, "ok": True,
                                  "summary": _summary_payload(payload), "arguments": _short_args(call.arguments)})
                except ToolError as exc:
                    if call.name == "run_sql":
                        repairs += 1
                    content = f"ERROR: {exc}"
                    trace.append({"tool": call.name, "ok": False, "summary": str(exc)[:300],
                                  "arguments": _short_args(call.arguments)})
                results.append(_tool_result(call.id, content))
            if terminal is not None:
                terminal.trace = trace
                terminal.audit = list(tools.audit)
                terminal.usage = usage
                return terminal
            if not results:
                return _finish(kind="no_answer", text="no tool result", model=model, candidate_index=candidate_index,
                               usage=usage, trace=trace, audit=tools.audit, warnings=[])
            messages.append({"role": "user", "content": results})
        return _finish(kind="no_answer", text="ran out of turns", model=model, candidate_index=candidate_index,
                       usage=usage, trace=trace, audit=tools.audit, warnings=[])

    def _terminal(self, call, tools: ToolLayer, repairs: int, model: str, candidate_index: int, usage: dict,
                  trace: list) -> tuple[str, AnalyzerAnswer | None, str | None]:
        """Return ('done', answer, None) or ('repair', None, message)."""
        config = self.config
        if call.name == "refuse":
            reason = str(call.arguments.get("reason") or "refused").strip()
            tools._audit("refusal", reason=reason)
            return "done", _finish(kind="refusal", text=reason, model=model, candidate_index=candidate_index,
                                   usage=usage, trace=trace, audit=tools.audit, warnings=[], refused=True), None
        if call.name == "ask":
            if not config.enable_ask:
                return "repair", None, "ask is not enabled in this session"
            question = str(call.arguments.get("question") or "").strip()
            if not question:
                return "repair", None, "ask requires a question"
            return "done", _finish(kind="clarification", text=question, model=model, candidate_index=candidate_index,
                                   usage=usage, trace=trace, audit=tools.audit, warnings=[]), None
        sql = str(call.arguments.get("sql") or "")
        prose = str(call.arguments.get("answer") or "")
        assumptions = list(call.arguments.get("assumptions") or [])
        if assumptions:
            prose = prose.rstrip() + "\nAssumptions: " + "; ".join(str(item) for item in assumptions)
        try:
            digest = tools.run_sql(sql, charge=False, include_all_rows=True)
        except ToolError as exc:
            if repairs < config.max_repairs and config.self_correct != "none":
                return "repair", None, f"final SQL failed: {exc}. Revise the SQL and call final_answer again."
            failed = _finish(kind="no_answer", text=f"final SQL failed: {exc}", model=model,
                             candidate_index=candidate_index, usage=usage, trace=trace, audit=tools.audit,
                             warnings=[], sql=sql)
            return "done", failed, None
        warnings = list(digest.get("warnings") or [])
        blocked = blocking_warnings(warnings)
        bounce = (config.verify and config.self_correct == "errors+result_signals" and blocked
                  and repairs < config.max_repairs)
        if bounce:
            return "repair", None, ("result failed verification: " + "; ".join(blocked)
                                    + ". Revise the SQL and call final_answer again.")
        rows = digest.get("all_rows", digest["rows"])
        answer = _finish(
            kind="answer", text=prose, sql=sql, rows=rows, columns=digest["columns"], model=model,
            candidate_index=candidate_index, usage=usage, trace=trace, audit=tools.audit, warnings=warnings,
            assumptions=assumptions, verification_blocked=bool(blocked),
            lane="lossless" if _LOSSLESS.search(sql) else "sem")
        return "done", answer, None


def _assistant_blocks(completion) -> list[dict]:
    blocks = []
    if completion.text:
        blocks.append({"type": "text", "text": completion.text})
    for call in completion.tool_calls:
        blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments})
    return blocks


def _tool_result(tool_use_id: str, content: str) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}


def _finish(*, kind: str, text: str, model: str, candidate_index: int, usage: dict, trace: list, audit: list,
            warnings: list, sql: str | None = None, rows=None, columns=None, assumptions=None,
            verification_blocked: bool = False, lane: str = "sem", refused: bool = False) -> AnalyzerAnswer:
    return AnalyzerAnswer(
        kind=kind, text=text, sql=sql, rows=rows, columns=columns, assumptions=assumptions or [],
        warnings=warnings, verification_blocked=verification_blocked, lane=lane, refused=refused,
        model=model, candidate_index=candidate_index, usage=dict(usage), trace=list(trace), audit=list(audit))


def _should_cascade(answer: AnalyzerAnswer, config: AnalyzerConfig) -> bool:
    if config.cascade != "haiku_then_sonnet":
        return False
    if answer.kind == "refusal":
        return False
    if answer.kind in {"no_answer", "abstain"}:
        return True
    if answer.kind == "answer" and answer.verification_blocked:
        return True
    return False


def _row_signature(rows: list) -> tuple:
    normalized = [tuple(jsonable(cell) for cell in row) for row in rows]
    return tuple(sorted(normalized, key=lambda row: json.dumps(row, default=str)))


def _select(answers: list[AnalyzerAnswer], mode: str) -> AnalyzerAnswer:
    if mode == "first_passing":
        for answer in answers:
            if answer.kind == "answer" and not answer.verification_blocked:
                return answer
        for answer in answers:
            if answer.kind == "answer":
                return answer
        return answers[0]
    usable = [answer for answer in answers if answer.kind == "answer" and answer.rows is not None]
    if not usable:
        refusals = [answer for answer in answers if answer.kind == "refusal"]
        if refusals and len(refusals) == len(answers):
            return refusals[0]
        if mode == "agree_or_abstain":
            return _abstain("no candidate produced an executable answer", answers)
        return answers[0]
    groups: dict[tuple, list[AnalyzerAnswer]] = defaultdict(list)
    for answer in usable:
        groups[_row_signature(answer.rows or [])].append(answer)
    if mode == "agree_or_abstain" and len(groups) != 1:
        return _abstain("candidates returned different results", answers)
    ranked = sorted(groups.values(), key=lambda group: (
        -len(group),
        sum(1 for warning in group[0].warnings if warning),
        group[0].candidate_index or 0,
    ))
    return ranked[0][0]


def _abstain(reason: str, answers: list[AnalyzerAnswer]) -> AnalyzerAnswer:
    first = answers[0]
    return AnalyzerAnswer(kind="abstain", text=reason, model=first.model, candidate_index=None,
                          candidate_results=[_candidate_summary(item) for item in answers],
                          selection={"mode": "agree_or_abstain", "candidates": len(answers)})


def _candidate_summary(answer: AnalyzerAnswer) -> dict:
    return {"candidate_index": answer.candidate_index, "kind": answer.kind, "model": answer.model,
            "sql": answer.sql, "row_count": None if answer.rows is None else len(answer.rows),
            "verification_blocked": answer.verification_blocked}


def _summary_payload(payload: dict) -> str:
    if "row_count" in payload:
        return f"rows={payload['row_count']}"
    if "relations" in payload and isinstance(payload["relations"], list):
        return f"relations={len(payload['relations'])}"
    if "escalated" in payload:
        return f"escalated={payload['escalated']}"
    return "ok"


def _short_args(arguments: dict) -> dict:
    short = {}
    for key, value in (arguments or {}).items():
        if isinstance(value, str) and len(value) > 240:
            short[key] = value[:240]
        else:
            short[key] = value
    return short
