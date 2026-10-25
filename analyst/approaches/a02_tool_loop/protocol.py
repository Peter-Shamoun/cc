"""A02 tool loop.

Code owns the phase order and the repair cap. The model owns the sample choice,
the SQL, and the prose. Execution goes through an executor that is either the
in-process guarded Tools object or bench.agent_cli (see adapter.py). The protocol
does not open DuckDB and does not call a model API.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from bench.agents import Answer

from .backend import rough_token_count
from .checks import suspicious_result_signals
from .prompts import opening_user_message, system_prompt
from .tool_spec import TOOLS

MAX_REPAIRS = 2
MAX_SAMPLE_CALLS = 3
MAX_SAMPLE_ROWS = 8
DEFAULT_SAMPLE_ROWS = 5
MAX_SAMPLE_COLUMNS = 6
ROWS_SHOWN_TO_THE_MODEL = 20
MAX_MODEL_TURNS = 10
MAX_DESCRIBE_CALLS = 2
# Planning figure for ten sem views in the DESCRIBE listing. Not measured on a snapshot.
PLANNING_SCHEMA_TOKENS = 2000
# Sequential model time, seconds per turn. Planning figures, not a timed run.
PLANNING_SECONDS_PER_TURN = {"haiku": 1.5, "sonnet": 3.0}

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_VIEW_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\((.*)\)\s*$")
_GLOSSARY_MARKER = "Business definitions"


@dataclass
class ExecutionResult:
    ok: bool
    columns: list[str] = field(default_factory=list)
    rows: list = field(default_factory=list)
    row_count: int = 0
    error: str = ""


def parse_schema_catalog(describe_text: str) -> dict[str, list[str]]:
    """Parse `schema.view(col TYPE, col DECIMAL(18,2))` lines into {relation: [columns]}."""
    catalog = {}
    for line in describe_text.splitlines():
        match = _VIEW_LINE.match(line.strip())
        if not match:
            continue
        columns = []
        depth = 0
        buffer: list[str] = []
        parts: list[str] = []
        for character in match.group(3) + ",":
            if character == "(":
                depth += 1
                buffer.append(character)
            elif character == ")":
                depth -= 1
                buffer.append(character)
            elif character == "," and depth == 0:
                parts.append("".join(buffer).strip())
                buffer = []
            else:
                buffer.append(character)
        for part in parts:
            if not part:
                continue
            columns.append(part.split()[0].strip('"'))
        catalog[f"{match.group(1)}.{match.group(2)}"] = columns
    return catalog


def schema_text_without_glossary(describe_text: str) -> str:
    index = describe_text.find(_GLOSSARY_MARKER)
    if index == -1:
        return describe_text.strip()
    return describe_text[:index].strip()


def same_sql(left: str, right: str) -> bool:
    def normalize(sql: str) -> str:
        text = " ".join(sql.split()).strip()
        if text.endswith(";"):
            text = text[:-1].strip()
        return text

    return normalize(left) == normalize(right)


def execution_error_hint(message: str) -> str:
    lowered = message.lower()
    if "not allowed" in lowered or "did not parse" in lowered:
        return "Query only the views from describe_schema. One SELECT. No files, settings, or other schemas."
    if "not found" in lowered or "referenced column" in lowered:
        return "Check column names against describe_schema. Each view has its own columns."
    if "group by" in lowered:
        return "Every selected non-aggregate expression must appear in GROUP BY."
    if "binder" in lowered or "catalog" in lowered:
        return "That relation or column is not in this session's schema."
    return ""


def classify_execution(sql: str, result: ExecutionResult) -> tuple[str, list[str]]:
    """Return (status, signals). status is error, empty, suspicious, or ok."""
    if not result.ok:
        hint = execution_error_hint(result.error)
        detail = result.error if not hint else f"{result.error} Hint: {hint}"
        return "error", [detail]
    if result.row_count == 0:
        # Empty wins the status, but filter signals still go back to the model so one repair can fix both.
        signals = ["the query returned no rows: a filter or a join may not match stored values"]
        signals.extend(suspicious_result_signals(sql, result.columns, result.rows))
        return "empty", signals
    signals = suspicious_result_signals(sql, result.columns, result.rows)
    if signals:
        return "suspicious", signals
    return "ok", []


def quote_identifier(name: str) -> str:
    if not _IDENTIFIER.fullmatch(name):
        raise ValueError(f"identifier {name!r} is not a plain name")
    return '"' + name + '"'


def build_sample_sql(relation: str, columns: list[str], limit: int) -> str:
    schema, name = relation.split(".", 1)
    quoted_columns = ", ".join(quote_identifier(column) for column in columns)
    return (
        f"SELECT {quoted_columns} FROM {quote_identifier(schema)}.{quote_identifier(name)} "
        f"LIMIT {int(limit)}"
    )


def _json_rows(columns: list[str], rows: list, row_count: int) -> str:
    shown = rows[:ROWS_SHOWN_TO_THE_MODEL]
    return json.dumps(
        {
            "columns": columns,
            "row_count": row_count,
            "rows_shown": [list(row) for row in shown],
            "truncated": row_count > len(shown),
        },
        default=str,
    )


def planning_cost(model_turns: int, repair_turns: int, sample_calls: int, sql_executions: int,
                  system_and_glossary_tokens: int, schema_tokens: int = PLANNING_SCHEMA_TOKENS,
                  tool_tokens: int = 0, per_turn_output_tokens: int = 180, question_tokens: int = 80,
                  sample_result_tokens: int = 250, execution_result_tokens: int = 200) -> dict:
    """Token and latency budget for one question.

    Input tokens are summed across turns with no cache, and again with the system
    prompt and the tool spec cached together (written once at 1.25x, then read at
    0.1x). Schema text and query results sit in the growing transcript and are not
    part of that cached prefix. Output tokens are a flat per-turn allowance.
    """
    prefix = system_and_glossary_tokens + tool_tokens
    # Turn contents after the prefix, in order: question, schema, each sample, each execution.
    # model_turns is describe + samples + draft + repairs + answer. Executions are draft + repairs.
    uncached_by_turn = [question_tokens]
    uncached_by_turn.append(question_tokens + schema_tokens)
    running = question_tokens + schema_tokens
    for _ in range(sample_calls):
        running += sample_result_tokens
        uncached_by_turn.append(running)
    for _ in range(max(0, sql_executions)):
        running += execution_result_tokens
        uncached_by_turn.append(running)
    # The answer turn sees the last execution and adds no new tool result before the call.
    while len(uncached_by_turn) < model_turns:
        uncached_by_turn.append(running)
    uncached_by_turn = uncached_by_turn[:model_turns]
    input_tokens_no_cache = sum(prefix + extra for extra in uncached_by_turn)
    if model_turns == 0:
        input_tokens_cached_prefix = 0
    else:
        input_tokens_cached_prefix = int(prefix * 1.25 + prefix * 0.1 * (model_turns - 1) + sum(uncached_by_turn))
    output_tokens = model_turns * per_turn_output_tokens
    return {
        "model_turns": model_turns,
        "repair_turns": repair_turns,
        "sample_calls": sample_calls,
        "sql_executions": sql_executions,
        "describe_calls": 1 if model_turns else 0,
        "investigative_sql_calls": sample_calls + sql_executions,
        "input_tokens_no_cache": input_tokens_no_cache,
        "input_tokens_cached_prefix": input_tokens_cached_prefix,
        "output_tokens": output_tokens,
        "latency_seconds": {
            name: round(model_turns * seconds, 1) for name, seconds in PLANNING_SECONDS_PER_TURN.items()
        },
    }


class ToolLoop:
    def __init__(self, question: str, executor, backend, escalate: bool = False):
        self.question = question
        self.executor = executor
        self.backend = backend
        self.escalate = escalate
        self.messages: list = [
            {"role": "system", "content": system_prompt(escalate)},
            {"role": "user", "content": opening_user_message(question)},
        ]
        self.catalog: dict[str, list[str]] = {}
        self.schema_text = ""
        self.describe_calls = 0
        self.sample_calls = 0
        self.proposals = 0
        self.repairs_used = 0
        self.last_sql: str | None = None
        self.last_rows: list | None = None
        self.last_status: str | None = None
        self.nudges = 0
        self.trace: list[dict] = []
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    def run(self) -> Answer:
        for _ in range(MAX_MODEL_TURNS):
            turn = self.backend.complete(self.messages, TOOLS)
            self.usage["input_tokens"] += int(turn.usage.get("input_tokens", 0))
            self.usage["output_tokens"] += int(turn.usage.get("output_tokens", 0))
            if not turn.tool_calls:
                self.nudges += 1
                self.messages.append({"role": "assistant", "content": turn.text})
                if self.nudges >= 2:
                    return self._unfinished("the model stopped calling tools")
                self.messages.append({
                    "role": "user",
                    "content": "Call one tool. describe_schema, then sample_rows, then propose_sql, then answer or refuse.",
                })
                continue
            self.nudges = 0
            tool_use_blocks = []
            tool_results = []
            for call in turn.tool_calls:
                tool_use_blocks.append({
                    "type": "tool_use", "id": call.call_id, "name": call.name, "input": call.arguments,
                })
                result_text, finished = self._dispatch(call)
                tool_results.append({"type": "tool_result", "tool_use_id": call.call_id, "content": result_text})
                if finished is not None:
                    return finished
            self.messages.append({"role": "assistant", "content": [{"type": "text", "text": turn.text}, *tool_use_blocks]})
            self.messages.append({"role": "user", "content": tool_results})
        return self._unfinished("ran out of turns")

    def _unfinished(self, reason: str) -> Answer:
        self.trace.append({"phase": "stop", "tool": None, "status": "no_answer", "detail": reason,
                           "repairs_used": self.repairs_used})
        return Answer(text=reason, usage=self._usage(), trace=self.trace)

    def _usage(self) -> dict:
        return {
            **self.usage,
            "model_turns": sum(1 for step in self.trace if step.get("tool")),
            "repairs_used": self.repairs_used,
            "sample_calls": self.sample_calls,
            "sql_proposals": self.proposals,
        }

    def _dispatch(self, call) -> tuple[str, Answer | None]:
        name = call.name
        arguments = call.arguments if isinstance(call.arguments, dict) else {}
        if name == "describe_schema":
            return self._describe(), None
        if name == "sample_rows":
            return self._sample(arguments), None
        if name == "propose_sql":
            return self._propose(arguments), None
        if name == "answer":
            return self._answer(arguments)
        if name == "refuse":
            return self._refuse(arguments)
        self.trace.append({"phase": "reject", "tool": name, "status": "unknown_tool", "repairs_used": self.repairs_used})
        return f"unknown tool {name}. Use describe_schema, sample_rows, propose_sql, answer, or refuse.", None

    def _describe(self) -> str:
        if self.describe_calls >= MAX_DESCRIBE_CALLS:
            return "describe_schema was already called. Call sample_rows or propose_sql."
        self.describe_calls += 1
        raw = self.executor.describe()
        self.schema_text = schema_text_without_glossary(raw)
        self.catalog = parse_schema_catalog(self.schema_text)
        self.trace.append({
            "phase": "explore_schema", "tool": "describe_schema", "status": "ok",
            "relations": sorted(self.catalog), "repairs_used": self.repairs_used,
        })
        if not self.catalog:
            return self.schema_text + "\n\nstatus: error\nnext: the schema listing did not parse; refuse or answer is not available until it does."
        names = ", ".join(sorted(self.catalog))
        return (
            self.schema_text
            + f"\n\nstatus: ok\nrelations: {names}\n"
            + "next: call sample_rows for views you will filter, or call propose_sql if the literals are already known."
        )

    def _require_schema(self) -> str | None:
        if self.describe_calls == 0:
            return "call describe_schema first"
        if not self.catalog:
            return "describe_schema did not return a usable view list"
        return None

    def _sample(self, arguments: dict) -> str:
        blocked = self._require_schema()
        if blocked:
            self.trace.append({"phase": "sample_rows", "tool": "sample_rows", "status": "rejected", "detail": blocked,
                               "repairs_used": self.repairs_used})
            return blocked
        if self.proposals:
            return "sample_rows is only available before the first propose_sql."
        if self.sample_calls >= MAX_SAMPLE_CALLS:
            return f"sample budget of {MAX_SAMPLE_CALLS} is used. Call propose_sql."
        relation = str(arguments.get("relation", "")).strip()
        if relation not in self.catalog:
            self.sample_calls += 1
            known = ", ".join(sorted(self.catalog))
            detail = f"{relation or '(missing)'} is not a view in this session. Views: {known}"
            self.trace.append({"phase": "sample_rows", "tool": "sample_rows", "status": "rejected", "detail": detail,
                               "repairs_used": self.repairs_used})
            return detail
        requested = arguments.get("columns")
        if requested is None:
            columns = self.catalog[relation][:MAX_SAMPLE_COLUMNS]
        else:
            if not isinstance(requested, list) or not requested:
                return "columns must be a non-empty list of names from describe_schema, or omit columns."
            columns = [str(column) for column in requested][:MAX_SAMPLE_COLUMNS]
            unknown = [column for column in columns if column not in self.catalog[relation]]
            if unknown:
                self.sample_calls += 1
                detail = f"unknown columns {unknown} on {relation}. Columns: {', '.join(self.catalog[relation])}"
                self.trace.append({"phase": "sample_rows", "tool": "sample_rows", "status": "rejected", "detail": detail,
                                   "repairs_used": self.repairs_used})
                return detail
        limit = arguments.get("limit", DEFAULT_SAMPLE_ROWS)
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return "limit must be an integer from 1 to 8."
        limit = max(1, min(MAX_SAMPLE_ROWS, limit))
        try:
            sql = build_sample_sql(relation, columns, limit)
        except ValueError as exc:
            self.sample_calls += 1
            self.trace.append({"phase": "sample_rows", "tool": "sample_rows", "status": "rejected", "detail": str(exc),
                               "repairs_used": self.repairs_used})
            return str(exc)
        self.sample_calls += 1
        result = self.executor.run_sql(sql)
        self.trace.append({
            "phase": "sample_rows", "tool": "sample_rows", "status": "ok" if result.ok else "error",
            "sql": sql, "row_count": result.row_count, "error": result.error, "repairs_used": self.repairs_used,
        })
        remaining = MAX_SAMPLE_CALLS - self.sample_calls
        if not result.ok:
            return f"status: error\nsample_sql: {sql}\nmessage: {result.error}\nsamples_remaining: {remaining}"
        return (
            f"status: ok\nrelation: {relation}\nsample_sql: {sql}\n"
            f"{_json_rows(result.columns, result.rows, result.row_count)}\n"
            f"samples_remaining: {remaining}\n"
            "next: sample another view, or call propose_sql. A sample is not the answer."
        )

    def _propose(self, arguments: dict) -> str:
        blocked = self._require_schema()
        if blocked:
            self.trace.append({"phase": "propose_sql", "tool": "propose_sql", "status": "rejected", "detail": blocked,
                               "repairs_used": self.repairs_used})
            return blocked
        if self.last_status == "ok":
            self.trace.append({
                "phase": "propose_sql", "tool": "propose_sql", "status": "rejected",
                "detail": "last result was ok", "repairs_used": self.repairs_used,
            })
            return "the last query executed and was not flagged. Call answer with that SQL."
        if self.proposals >= 1 + MAX_REPAIRS:
            self.trace.append({
                "phase": "propose_sql", "tool": "propose_sql", "status": "rejected",
                "detail": "repair budget used", "repairs_used": self.repairs_used,
            })
            return f"repair budget of {MAX_REPAIRS} is used. Call answer with the last SQL."
        sql = arguments.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            return "propose_sql requires a sql string."
        phase = "draft" if self.proposals == 0 else "repair"
        self.proposals += 1
        if phase == "repair":
            self.repairs_used += 1
        result = self.executor.run_sql(sql)
        status, signals = classify_execution(sql, result)
        self.last_sql = sql
        self.last_rows = result.rows if result.ok else None
        self.last_status = status
        self.trace.append({
            "phase": phase, "tool": "propose_sql", "status": status, "sql": sql,
            "signals": signals, "row_count": result.row_count, "repairs_used": self.repairs_used,
            "rationale": arguments.get("rationale", ""),
        })
        remaining = MAX_REPAIRS - self.repairs_used
        body = [f"status: {status}", f"phase: {phase}", f"repairs_remaining: {remaining}"]
        if signals:
            body.append("signals:\n- " + "\n- ".join(signals))
        if result.ok:
            body.append(_json_rows(result.columns, result.rows, result.row_count))
        if status == "ok":
            body.append("next: call answer with this sql and a one-paragraph answer.")
        elif remaining > 0:
            body.append("next: call propose_sql with a revised query. Do not drop a filter only to fill the result.")
        else:
            body.append("next: repair budget is used. Call answer with this sql, which will be re-executed, or refuse if the request is not allowed.")
        return "\n".join(body)

    def _answer(self, arguments: dict) -> tuple[str, Answer | None]:
        if self.last_sql is None:
            self.trace.append({"phase": "answer", "tool": "answer", "status": "rejected", "detail": "no proposal",
                               "repairs_used": self.repairs_used})
            return "call propose_sql before answer.", None
        if self.last_status != "ok" and self.repairs_used < MAX_REPAIRS and self.proposals < 1 + MAX_REPAIRS:
            detail = f"the last result was {self.last_status}; repairs_remaining is {MAX_REPAIRS - self.repairs_used}. Repair before answering."
            self.trace.append({"phase": "answer", "tool": "answer", "status": "rejected", "detail": detail,
                               "repairs_used": self.repairs_used})
            return detail, None
        submitted_sql = arguments.get("sql", "")
        if not isinstance(submitted_sql, str) or not same_sql(submitted_sql, self.last_sql):
            detail = "answer sql must be the last proposed sql, unchanged except for whitespace."
            self.trace.append({"phase": "answer", "tool": "answer", "status": "rejected", "detail": detail,
                               "repairs_used": self.repairs_used})
            return detail, None
        text = arguments.get("text") or ""
        if not str(text).strip():
            return "answer requires text: grain, population, and time basis.", None
        submitted = self.executor.submit_answer(self.last_sql, str(text))
        self.trace.append({
            "phase": "answer", "tool": "answer", "status": "ok" if submitted.ok else "submit_failed",
            "sql": self.last_sql, "repairs_used": self.repairs_used,
        })
        if not submitted.ok:
            rows = None
            answer_text = f"{text}\n\nsubmit failed: {submitted.error}"
        else:
            # The CLI records the answer and the scorer re-executes it, so submit has no rows.
            # The in-process executor re-executes and returns them. Prefer those when present.
            rows = submitted.rows if submitted.rows else self.last_rows
            answer_text = str(text)
        return "recorded", Answer(sql=self.last_sql, rows=rows, text=answer_text, usage=self._usage(), trace=self.trace)

    def _refuse(self, arguments: dict) -> tuple[str, Answer | None]:
        reason = str(arguments.get("reason") or "").strip()
        if not reason:
            return "refuse requires a reason.", None
        error = self.executor.submit_refusal(reason)
        if error:
            self.trace.append({"phase": "refuse", "tool": "refuse", "status": "submit_failed", "detail": error,
                               "repairs_used": self.repairs_used})
            return error, None
        self.trace.append({"phase": "refuse", "tool": "refuse", "status": "refused", "detail": reason,
                           "repairs_used": self.repairs_used})
        return "recorded", Answer(refused=True, text=reason, usage=self._usage(), trace=self.trace)


def run_question(question: str, executor, backend, escalate: bool = False) -> Answer:
    return ToolLoop(question, executor, backend, escalate=escalate).run()


def documented_example_costs(system_and_glossary_tokens: int | None = None) -> dict:
    """The two rows quoted in research/approaches/a02_tool_loop.md."""
    from .tool_spec import TOOLS
    prefix_tokens = system_and_glossary_tokens
    if prefix_tokens is None:
        prefix_tokens = rough_token_count(system_prompt(False))
    tool_tokens = rough_token_count(TOOLS)
    shared = dict(system_and_glossary_tokens=prefix_tokens, tool_tokens=tool_tokens)
    happy = planning_cost(model_turns=4, repair_turns=0, sample_calls=1, sql_executions=1, **shared)
    # describe, one sample, draft, two repairs, answer. SQL executions = draft + two repairs.
    repaired = planning_cost(model_turns=6, repair_turns=2, sample_calls=1, sql_executions=3, **shared)
    return {
        "happy_path": happy,
        "two_repairs": repaired,
        "system_and_glossary_tokens": prefix_tokens,
        "tool_tokens": tool_tokens,
        "schema_tokens": PLANNING_SCHEMA_TOKENS,
    }
