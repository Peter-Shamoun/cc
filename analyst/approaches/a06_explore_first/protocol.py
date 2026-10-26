"""Explore-first protocol: plan, probe, compress, then one final SELECT.

The connection is not opened here. Production passes a ccsim.query.QuerySession,
whose DuckDB is already limited to that customer's sem.* (and lossless.* only
when the session was escalated). Every statement still goes through bench.guard
before it runs. Two model calls are the whole language-model budget: a plan,
and the final SQL. Probe SQL is templated.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from bench.guard import guard

from .catalog import LITERAL_ROLES, VIEW_BY_NAME
from .probes import (
    LITERAL_MAX_LENGTH,
    MAX_COLUMNS_PER_VIEW,
    MAX_LITERALS,
    MAX_VIEWS,
    ExplorationPlan,
    LiteralRequest,
    Probe,
    ProbeExecution,
    build_join_probe,
    build_literal_probe,
    build_summary_probe,
    build_values_probe,
    compress,
    followup_column_names,
    maximum_probe_statements,
    selected_joins,
)
from .prompts import render_plan_prompt, render_write_prompt

PLAN_STAGE = "plan"
WRITE_STAGE = "write_sql"


class Backend:
    """Two completions. stage is 'plan' or 'write_sql'. No network lives in this class."""

    def complete(self, stage: str, prompt: str) -> str:
        raise NotImplementedError


@dataclass
class ScriptedBackend(Backend):
    """Test double: returns a fixed plan and a fixed final answer. Records both prompts."""

    plan: dict
    sql: str
    text: str = ""
    prompts: list[tuple[str, str]] = field(default_factory=list)

    def complete(self, stage: str, prompt: str) -> str:
        if stage not in (PLAN_STAGE, WRITE_STAGE):
            raise ValueError(f"unknown stage {stage!r}")
        self.prompts.append((stage, prompt))
        if stage == PLAN_STAGE:
            return json.dumps(self.plan)
        return json.dumps({"sql": self.sql, "text": self.text})


@dataclass
class GuardedExecutor:
    """Run one guarded SELECT on a connection the secure path already opened."""

    connection: object
    escalate: bool = False
    row_limit: int = 1000
    attempted_sql: list[str] = field(default_factory=list)

    def execute(self, sql: str) -> tuple[list[str], list[tuple]]:
        self.attempted_sql.append(sql)
        safe = guard(sql, self.row_limit, self.escalate)
        cursor = self.connection.execute(safe)
        columns = [description[0] for description in cursor.description] if cursor.description else []
        return columns, cursor.fetchall()


def executor_for_query_session(query_session, escalate: bool = False) -> GuardedExecutor:
    """Bind exploration to a session from ccsim.query.open_session.

    Does not open a connection, assume a role, or read a file. escalate must
    match the session: lossless.* is on that connection only when open_session
    was called with escalate=True.
    """
    connection = getattr(query_session, "con", None)
    if connection is None:
        raise TypeError("expected a ccsim.query.QuerySession with a .con connection")
    return GuardedExecutor(connection, escalate=escalate)


@dataclass
class ExplorationResult:
    question: str
    plan: dict
    notes: str
    probe_sql: list[str]
    final_sql: str | None
    final_text: str
    refused: bool
    refusal_reason: str | None
    columns: list[str] | None
    rows: list | None
    final_error: str | None


def run(question: str, executor: GuardedExecutor, backend: Backend) -> ExplorationResult:
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")
    plan = parse_plan(backend.complete(PLAN_STAGE, render_plan_prompt(question)))
    if plan.refusal_reason:
        return ExplorationResult(
            question=question, plan=plan.as_dict(), notes="", probe_sql=[], final_sql=None, final_text="",
            refused=True, refusal_reason=plan.refusal_reason, columns=None, rows=None, final_error=None)
    executions = _explore(plan, executor)
    notes = compress(plan, executions)
    final_sql, final_text = parse_final_answer(backend.complete(WRITE_STAGE, render_write_prompt(question, notes)))
    columns, rows, final_error = _final(executor, final_sql)
    return ExplorationResult(
        question=question, plan=plan.as_dict(), notes=notes,
        probe_sql=[item.probe.sql for item in executions],
        final_sql=final_sql, final_text=final_text, refused=False, refusal_reason=None,
        columns=columns, rows=rows, final_error=final_error,
    )


def parse_plan(raw: str) -> ExplorationPlan:
    data = parse_json_object(raw)
    refusal = data.get("refuse")
    if refusal is not None and not isinstance(refusal, str):
        raise ValueError("refuse must be a string or null")
    if isinstance(refusal, str) and refusal.strip():
        return ExplorationPlan([], {}, [], refusal.strip(), [])
    raw_views = data.get("views") or []
    raw_columns = data.get("columns") or {}
    if not isinstance(raw_views, list) or not all(isinstance(name, str) for name in raw_views):
        raise ValueError("views must be a list of strings")
    if not isinstance(raw_columns, dict):
        raise ValueError("columns must be an object")
    dropped: list[str] = []
    views: list[str] = []
    for name in raw_views:
        if name not in VIEW_BY_NAME:
            dropped.append(name)
            continue
        if name in views:
            continue
        if len(views) >= MAX_VIEWS:
            dropped.append(name)
            continue
        views.append(name)
    columns: dict[str, list[str]] = {}
    for view_name in views:
        requested = raw_columns.get(view_name) or []
        if not isinstance(requested, list) or not all(isinstance(name, str) for name in requested):
            raise ValueError(f"columns for {view_name} must be a list of strings")
        kept: list[str] = []
        view = VIEW_BY_NAME[view_name]
        for name in requested:
            if view.column(name) is None:
                dropped.append(f"{view_name}.{name}")
                continue
            if name in kept:
                continue
            if len(kept) >= MAX_COLUMNS_PER_VIEW:
                dropped.append(f"{view_name}.{name}")
                continue
            kept.append(name)
        columns[view_name] = kept
    views = [view_name for view_name in views if columns[view_name]]
    columns = {view_name: columns[view_name] for view_name in views}
    literals = _literals(data.get("literals") or [], columns, dropped)
    return ExplorationPlan(views, columns, literals, None, dropped)


def parse_final_answer(raw: str) -> tuple[str, str]:
    data = parse_json_object(raw)
    sql = data.get("sql")
    text = data.get("text", "")
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("final answer needs a sql string")
    if not isinstance(text, str):
        raise ValueError("final answer text must be a string")
    return sql.strip(), text


def parse_json_object(raw: str) -> dict:
    if not isinstance(raw, str):
        raise ValueError("model output must be a string")
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"model output is not JSON: {error.msg}") from None
    if not isinstance(value, dict):
        raise ValueError("model output must be a JSON object")
    return value


def _literals(raw_literals, columns: dict[str, list[str]], dropped: list[str]) -> list[LiteralRequest]:
    if not isinstance(raw_literals, list):
        raise ValueError("literals must be a list")
    literals: list[LiteralRequest] = []
    for item in raw_literals:
        if not isinstance(item, dict):
            raise ValueError("each literal must be an object")
        view_name, column_name, text = item.get("view"), item.get("column"), item.get("text")
        if not isinstance(view_name, str) or not isinstance(column_name, str) or not isinstance(text, str):
            raise ValueError("literal view, column, and text must be strings")
        text = text.strip()
        view = VIEW_BY_NAME.get(view_name)
        role = view.column(column_name).role if view and view.column(column_name) else None
        kept = (
            view_name in columns and role in LITERAL_ROLES and 0 < len(text) <= LITERAL_MAX_LENGTH
            and not any(existing.view == view_name and existing.column == column_name and existing.text == text
                        for existing in literals)
        )
        if not kept or len(literals) >= MAX_LITERALS:
            dropped.append(f"literal {view_name}.{column_name}")
            continue
        literals.append(LiteralRequest(view_name, column_name, text))
    return literals


def _explore(plan: ExplorationPlan, executor: GuardedExecutor) -> list[ProbeExecution]:
    executions: list[ProbeExecution] = []
    for view_name in plan.views:
        _run(executor, executions, build_summary_probe(view_name, plan.columns[view_name]))
    for view_name in plan.views:
        summary = next(item for item in executions if item.probe.kind == "summary" and item.probe.view == view_name)
        followup = followup_column_names(view_name, plan.columns[view_name], summary)
        if followup:
            _run(executor, executions, build_values_probe(view_name, followup))
    for edge in selected_joins(plan.views):
        _run(executor, executions, build_join_probe(edge))
    for literal in plan.literals:
        _run(executor, executions, build_literal_probe(literal))
    return executions


def _run(executor: GuardedExecutor, executions: list[ProbeExecution], probe: Probe) -> None:
    if len(executions) >= maximum_probe_statements():
        raise RuntimeError(f"probe budget exceeded: {len(executions)} statements")
    try:
        columns, rows = executor.execute(probe.sql)
        executions.append(ProbeExecution(probe, columns, rows, None))
    except Exception as error:  # noqa: BLE001 - a failed probe is a note, not the end of the question
        executions.append(ProbeExecution(probe, [], [], str(error).splitlines()[0][:300]))


def _final(executor: GuardedExecutor, sql: str):
    try:
        columns, rows = executor.execute(sql)
        return columns, rows, None
    except Exception as error:  # noqa: BLE001 - the caller scores a final that did not run
        return None, None, str(error).splitlines()[0][:300]
