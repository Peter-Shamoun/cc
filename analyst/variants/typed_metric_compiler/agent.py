"""Bench agent. `answer(question, tools) -> Answer`, same interface as bench.agents.

The model submits a spec. This module validates and compiles it. Free SQL runs only after
CannotExpress, and only when the arm's fallback flag says so.
"""
from __future__ import annotations

import os

from bench.agents import Answer

from .compiler import compile_spec, identifier_projected, load_api
from .config import CompilerConfig, config_from_mapping
from .llm import ScriptedLLM
from .phi import lexical_refusal
from .prompts import FALLBACK_SYSTEM, FALLBACK_TOOLS, PARSER_TOOLS, parser_system
from .spec import CannotExpress, SpecError, spec_from_dict


class TypedCompilerAgent:
    def __init__(self, llm=None, config: CompilerConfig | dict | None = None):
        if isinstance(config, dict):
            config = config_from_mapping(config)
        self.config = config or CompilerConfig()
        if llm is not None:
            self.llm = llm
        elif os.environ.get("CC_COMPILER_SCRIPTS"):
            self.llm = ScriptedLLM.from_json(os.environ["CC_COMPILER_SCRIPTS"])
        else:
            self.llm = ScriptedLLM({})
        self.system_prompt = parser_system()
        self.fallback_prompt = FALLBACK_SYSTEM

    def answer(self, question, tools) -> Answer:
        reason = lexical_refusal(question, self.config)
        if reason:
            return Answer(refused=True, text=reason, trace=[{"lane": "refuse", "by": "phi_guard"}])
        module, api = load_api(tools._con, self.config)
        error = None
        trace = []
        for turn in range(self.config.repair_catalog_errors + 1):
            call = self.llm.complete(question=question, phase="parse", turn=turn,
                                     tools=PARSER_TOOLS, error=error)
            trace.append({"phase": "parse", "call": call, "error_in": error})
            if call["tool"] == "refuse":
                return Answer(refused=True, text=call["input"].get("reason", ""), trace=trace)
            if call["tool"] != "submit_spec":
                return Answer(text=f"parser returned {call['tool']}", trace=trace)
            try:
                spec = spec_from_dict(call["input"])
            except CannotExpress as e:
                return self._fallback(question, tools, str(e), e.code, trace)
            except SpecError as e:
                error = str(e)
                continue
            if spec.intent == "refuse":
                return Answer(refused=True, text=spec.reason or "", trace=trace)
            try:
                compiled = compile_spec(module, api, spec, self.config)
            except SpecError as e:
                error = str(e)
                continue
            except CannotExpress as e:
                return self._fallback(question, tools, str(e), e.code, trace)
            try:
                _cols, rows = tools.sql(compiled.sql)
            except Exception as e:  # noqa: BLE001 - surface the compiler SQL failure, do not invent another query
                return Answer(sql=compiled.sql, text=f"compiled SQL failed: {e}", trace=trace + [
                    {"lane": compiled.lane, "view": compiled.view, "notes": compiled.notes}])
            text = (f"lane={compiled.lane} view={compiled.view} date_basis={compiled.date_basis} "
                    f"rows={len(rows)}")
            return Answer(sql=compiled.sql, rows=rows, text=text, trace=trace + [
                {"lane": compiled.lane, "view": compiled.view, "notes": compiled.notes, "basis": compiled.date_basis}])
        return Answer(text=f"spec rejected: {error}", trace=trace)

    def _fallback(self, question, tools, reason: str, code: str, trace: list) -> Answer:
        trace = trace + [{"lane": "cannot_express", "code": code, "reason": reason}]
        if not self.config.fallback_enabled():
            return Answer(text=f"cannot_express: {reason}", trace=trace)
        call = self.llm.complete(question=question, phase="fallback", turn=0,
                                 tools=FALLBACK_TOOLS, error=reason)
        trace.append({"phase": "fallback", "call": {"tool": call["tool"]}})
        if call["tool"] == "refuse":
            return Answer(refused=True, text=call["input"].get("reason", ""), trace=trace)
        if call["tool"] != "submit_sql":
            return Answer(text=f"fallback returned {call['tool']}", trace=trace)
        sql = call["input"]["sql"]
        if self.config.block_identifier_projection:
            bad = identifier_projected(sql)
            if bad:
                return Answer(text=f"fallback SQL projects identifier {bad}", trace=trace)
        try:
            _cols, rows = tools.sql(sql)
        except Exception as e:  # noqa: BLE001
            return Answer(sql=sql, text=f"fallback SQL failed: {e}", trace=trace)
        return Answer(sql=sql, rows=rows, text=f"lane=fallback_sql reason={code} rows={len(rows)}", trace=trace)
