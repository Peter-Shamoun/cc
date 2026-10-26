"""B1 run by a Strands ``Agent``: the only way the analyzer reaches a model.

One question is one ``B1Run`` (``analyzer.b1_commands``) over the request's opener and one Strands agent with one tool,
``cc(command)``: a command line in the syntax B1 was benchmarked on (``analyzer.command_line``), run in this process
against the run, one call at a time (``SequentialToolExecutor``: the run's DuckDB connection is not shared between
threads). The guard, the command budget, the query-time budget, the escalation audit and PHI redaction are the tool
layer's own. Each command is reported to an observer when it starts and when it finishes (the chat API's ``LiveTap``
turns them into step events as they happen); a line that does not parse is answered with the error and no step.

Prompt layout (Bedrock prompt caching, ``CacheConfig(strategy="anthropic")``):
1. the tool spec (static), then the static system prompt (``analyzer.briefing.static_system_prompt``, with the generated sem.*
   schema), then a cache point: the prefix every question of a customer and gold version shares;
2. the first user message: today's date, the customer's data coverage, the question's BM25 examples and the question;
3. the agent loop's growing history (assistant tool calls, tool results); Strands places the second, rolling cache
   point on the newest user message of each request, so each step re-reads the prefix the previous step wrote.

Stop: the caller's ``cancelled`` event is the agent's ``cancel_signal`` (Strands aborts the in-flight Bedrock stream
and stops before the next tool call); a tool call that starts after Stop runs nothing. The run also ends right after a
final command (answer, refuse, ask) is recorded, without another model call.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import date
from typing import Any, Protocol

from strands import Agent
from strands.tools.executors import SequentialToolExecutor
from strands.tools.tools import PythonAgentTool

from bench.guard import GuardError

from analyzer.answer import AnalyzerAnswer
from analyzer.b1_commands import B1_CONFIG, FINAL_COMMANDS, B1Run
from analyzer.briefing import question_message, static_system_prompt
from analyzer.command_line import parse_command_line
from analyzer.config import AnalyzerConfig
from analyzer.gold_schema import data_coverage_line, gold_schema_block
from analyzer.tools import ToolError

#: a runaway guard on model turns: the command budget, the final command and the DONE reply, with room for retries
MAXIMUM_AGENT_TURNS = 2 * B1_CONFIG.max_tool_calls + 6
CACHE_POINT = {"cachePoint": {"type": "default"}}

COMMAND_TOOL_NAME = "cc"
#: the one tool's spec: the same bytes for every question, customer and session (it sits in cache point 1)
COMMAND_TOOL_SPEC = {
    "name": COMMAND_TOOL_NAME,
    "description": "Run one analyzer command line (the commands are listed in your instructions), e.g. "
                   "sql \"SELECT ...\". Returns the command's JSON result or ERROR: <message>.",
    "inputSchema": {"json": {"type": "object", "properties": {"command": {
        "type": "string", "description": "One command line, quoted as in a shell"}}, "required": ["command"],
        "additionalProperties": False}},
}


class ToolCallObserver(Protocol):
    def tool_call_started(self, command: str, arguments: dict) -> Any: ...

    def tool_call_finished(self, started_call: Any, result_text: str) -> None: ...


class RunCancelled(Exception):
    """The user stopped the run."""


def _result(tool_use: dict, text: str, ok: bool) -> dict:
    return {"toolUseId": tool_use["toolUseId"], "status": "success" if ok else "error", "content": [{"text": text}]}


def command_tool(run: B1Run, observer: ToolCallObserver, cancelled: threading.Event,
                 on_final_recorded) -> PythonAgentTool:
    """``cc(command)`` over ``run``. The model sees what the CLI printed: the JSON result, or ``ERROR: <message>``."""
    def call(tool_use: dict, **_invocation_state) -> dict:
        if cancelled.is_set():
            return _result(tool_use, "ERROR: the user stopped this run", ok=False)
        line = (tool_use.get("input") or {}).get("command")
        try:
            if not isinstance(line, str):
                raise ToolError("cc needs one string argument, command")
            command, arguments = parse_command_line(line)
        except ToolError as exc:
            return _result(tool_use, f"ERROR: {exc}", ok=False)
        started_call = observer.tool_call_started(command, arguments)
        try:
            result = run.run_command(command, dict(arguments))
        except (ToolError, GuardError) as exc:
            text = f"ERROR: {exc}"
            observer.tool_call_finished(started_call, text)
            return _result(tool_use, text, ok=False)
        text = json.dumps(result, default=str)
        observer.tool_call_finished(started_call, text)
        if command in FINAL_COMMANDS:
            on_final_recorded()
        return _result(tool_use, text, ok=True)

    return PythonAgentTool(COMMAND_TOOL_NAME, COMMAND_TOOL_SPEC, call)


def build_agent(model, run: B1Run, observer: ToolCallObserver, cancelled: threading.Event,
                config: AnalyzerConfig = B1_CONFIG) -> Agent:
    agent_holder: dict[str, Agent] = {}
    agent = Agent(model=model, tools=[command_tool(run, observer, cancelled, lambda: agent_holder["agent"].cancel())],
                  system_prompt=[{"text": static_system_prompt(config, gold_schema_block(run.tools))}, CACHE_POINT],
                  tool_executor=SequentialToolExecutor(), callback_handler=None)
    agent_holder["agent"] = agent
    return agent


def answer_question(question: str, *, opener, model, model_label: str, observer: ToolCallObserver,
                    cancelled: threading.Event, today: date, snapshot_path=None, config: AnalyzerConfig = B1_CONFIG
                    ) -> AnalyzerAnswer:
    """Run B1 on ``question`` against ``opener``'s session to its final answer. ``today`` is the server's date in the
    deployment's time zone. Raises ``RunCancelled`` on Stop and ``AnalyzerRunFailed`` when the agent ends without a
    final command."""
    run = B1Run(opener, config)
    agent = build_agent(model, run, observer, cancelled, config)
    started = time.perf_counter()
    first_message = question_message(question, config, snapshot_path, today, data_coverage_line(run.tools))
    result = agent([{"text": first_message}], cancel_signal=cancelled,
                   limits={"turns": MAXIMUM_AGENT_TURNS})
    if cancelled.is_set():
        raise RunCancelled()
    answer = run.final_answer(model_label)
    answer.usage = run_usage(result, time.perf_counter() - started)
    return answer


def run_usage(result, wall_seconds: float) -> dict:
    """Token counts (with Bedrock's prompt-cache reads and writes), model time and turns from the agent's metrics."""
    usage = dict(result.metrics.accumulated_usage or {})
    return {"input_tokens": int(usage.get("inputTokens") or 0), "output_tokens": int(usage.get("outputTokens") or 0),
            "cache_read_input_tokens": int(usage.get("cacheReadInputTokens") or 0),
            "cache_write_input_tokens": int(usage.get("cacheWriteInputTokens") or 0),
            "model_ms": int((result.metrics.accumulated_metrics or {}).get("latencyMs") or 0),
            "turns": int(result.metrics.cycle_count), "wall_seconds": round(wall_seconds, 3),
            "stop_reason": str(result.stop_reason)}
