"""Run the A07 decomposition: plan, check each CTE, repair once, then compose.

The executor is any connection that already enforces the per-request sandbox in
``ccsim.query.open_session`` (fresh DuckDB, ``sem.*`` only unless escalated,
external access off, configuration locked). ``GuardedDuckDbExecutor`` adds the
same statement guard the bench uses, so a step is one read-only SELECT.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from bench.guard import GuardError, guard

from .checks import (
    SUM_TOLERANCE,
    CheckError,
    CheckFailure,
    CheckReport,
    aggregate_select_list,
    monotonic_failure,
    quote_identifier,
    report_from_aggregate,
    require_identifier,
    sum_alias,
)
from .prompts import STEP_ROLES

_FINAL_SELECT = re.compile(r"^\s*select\b", re.IGNORECASE)


class ProtocolError(ValueError):
    """The plan is not a legal decomposition. This is a backend bug, not a failed check."""


@dataclass
class Step:
    role: str
    cte_name: str
    sql: str
    purpose: str = ""
    grain_keys: tuple[str, ...] = ()
    preserve_sums: tuple[str, ...] = ()
    expect_nonempty: bool | None = None
    max_rows: int | None = None
    order_by: str | None = None
    order_direction: str = "desc"
    max_fanout: float | None = None


@dataclass
class Plan:
    steps: list[Step]
    final_select: str | None = None
    refuse: bool = False
    refusal_reason: str = ""


@dataclass
class PlanRequest:
    question: str
    schema_text: str
    escalate: bool


@dataclass
class RepairRequest:
    question: str
    schema_text: str
    step: Step
    report: CheckReport
    prior_steps: list[Step]


@dataclass
class StepTrace:
    step: Step
    report: CheckReport
    repairs: int


@dataclass
class DecompositionResult:
    question: str
    sql: str | None
    text: str
    refused: bool
    steps: list[StepTrace] = field(default_factory=list)
    guarded: bool = False
    guard_error: str | None = None

    @property
    def checks_passed(self) -> bool:
        return (not self.refused and self.guarded and bool(self.steps)
                and all(trace.report.ok for trace in self.steps))


class GuardedDuckDbExecutor:
    """Execute one guarded SELECT on a connection the caller already sandboxed.

    Production passes ``QuerySession.con`` from ``ccsim.query.open_session``.
    The bench passes the snapshot connection from ``bench.dataset.open_snapshot``,
    which sets the same external-access and lock flags. This wrapper does not
    open a session and does not read credentials.
    """

    def __init__(self, connection, *, escalate: bool = False, row_limit: int = 1000):
        self.connection = connection
        self.escalate = escalate
        self.row_limit = row_limit
        self.statements: list[str] = []

    def execute(self, sql: str):
        safe = guard(sql, self.row_limit, self.escalate)
        self.statements.append(sql)
        cursor = self.connection.execute(safe)
        columns = [descriptor[0] for descriptor in cursor.description]
        return columns, cursor.fetchall()


class Backend:
    """Plan and repair. A scripted backend returns stored plans. A model backend is not included."""

    def plan(self, request: PlanRequest) -> Plan:
        raise NotImplementedError

    def repair(self, request: RepairRequest) -> Step:
        raise NotImplementedError


def plan_from_json(payload: dict) -> Plan:
    """Parse the planner JSON described in ``prompts.PLANNER_INSTRUCTIONS``."""
    if not isinstance(payload, dict):
        raise ProtocolError("plan JSON must be an object")
    if payload.get("refuse"):
        reason = payload.get("reason") or payload.get("refusal_reason") or ""
        return Plan(steps=[], refuse=True, refusal_reason=str(reason))
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list):
        raise ProtocolError("plan JSON has no steps list")
    steps = [step_from_json(raw) for raw in raw_steps]
    final_select = payload.get("final_select")
    if final_select is not None:
        final_select = str(final_select)
    return Plan(steps=steps, final_select=final_select)


def step_from_json(raw: dict) -> Step:
    if not isinstance(raw, dict):
        raise ProtocolError("a step must be a JSON object")
    try:
        return Step(
            role=str(raw["role"]),
            cte_name=str(raw["cte_name"]),
            sql=str(raw["sql"]),
            purpose=str(raw.get("purpose") or ""),
            grain_keys=tuple(raw.get("grain_keys") or ()),
            preserve_sums=tuple(raw.get("preserve_sums") or ()),
            expect_nonempty=raw.get("expect_nonempty"),
            max_rows=raw.get("max_rows"),
            order_by=raw.get("order_by"),
            order_direction=str(raw.get("order_direction") or "desc"),
            max_fanout=raw.get("max_fanout"),
        )
    except KeyError as error:
        raise ProtocolError(f"step JSON is missing {error.args[0]}") from None


def run(
    question: str,
    backend: Backend,
    executor: GuardedDuckDbExecutor,
    *,
    schema_text: str = "",
    max_repairs: int = 1,
    tolerance: float = SUM_TOLERANCE,
) -> DecompositionResult:
    """Decompose `question`, check every CTE, and return the composed SQL.

    A failed check is handed back to the backend at most `max_repairs` times for
    that step. The step that remains is kept either way, so a later step can
    still be attempted and the bench receives a query to score. Structural
    mistakes in the plan raise ``ProtocolError`` instead of being repaired.
    Escalation follows the executor: the same flag is used for every check and
    for the final guard.
    """
    if max_repairs < 0:
        raise ProtocolError("max_repairs must be >= 0")
    escalate = executor.escalate
    plan = backend.plan(PlanRequest(question=question, schema_text=schema_text, escalate=escalate))
    validate_plan(plan)
    if plan.refuse:
        return DecompositionResult(question=question, sql=None, text=plan.refusal_reason, refused=True, guarded=False)

    traces: list[StepTrace] = []
    accepted: list[Step] = []
    for original in plan.steps:
        step = normalize_step(original)
        report = _check_step(accepted, step, executor, tolerance)
        repairs = 0
        while not report.ok and repairs < max_repairs:
            repaired = backend.repair(RepairRequest(
                question=question, schema_text=schema_text, step=step, report=report, prior_steps=list(accepted),
            ))
            _require_same_slot(step, repaired)
            step = normalize_step(repaired)
            repairs += 1
            report = _check_step(accepted, step, executor, tolerance)
        accepted.append(step)
        traces.append(StepTrace(step=step, report=report, repairs=repairs))

    sql = compose(accepted, plan.final_select)
    guard_error = None
    try:
        guard(sql, executor.row_limit, escalate)
        guarded = True
    except GuardError as error:
        guarded = False
        guard_error = str(error)
    text = _trace_text(traces)
    if guard_error:
        text = f"{text}; final SQL rejected by the guard: {guard_error}"
    return DecompositionResult(
        question=question, sql=sql, text=text, refused=False, steps=traces, guarded=guarded, guard_error=guard_error,
    )


def validate_plan(plan: Plan) -> None:
    if plan.refuse:
        if not str(plan.refusal_reason).strip():
            raise ProtocolError("a refusal needs a reason")
        return
    if not plan.steps:
        raise ProtocolError("a plan needs a population step")
    roles = [step.role for step in plan.steps]
    if roles[0] != "population":
        raise ProtocolError("the first step must be population")
    canonical = [role for role in STEP_ROLES if role in roles]
    if roles != canonical:
        raise ProtocolError(f"steps must follow {STEP_ROLES} with no duplicates; got {roles}")
    names = [normalize_step(step).cte_name for step in plan.steps]
    if len(set(names)) != len(names):
        raise ProtocolError("CTE names must be unique")
    for step in plan.steps:
        normalize_step(step)
    if plan.final_select is not None:
        _validate_final_select(plan.final_select, names[-1])


def normalize_step(step: Step) -> Step:
    """Check the step shape and fill the population default (non-empty)."""
    if step.role not in STEP_ROLES:
        raise ProtocolError(f"unknown step role {step.role!r}")
    try:
        cte_name = require_identifier(step.cte_name, "CTE name")
        grain_keys = tuple(require_identifier(key, "grain key") for key in step.grain_keys)
        preserve_sums = tuple(require_identifier(column, "sum column") for column in step.preserve_sums)
        order_by = require_identifier(step.order_by, "order column") if step.order_by else None
    except CheckError as error:
        raise ProtocolError(str(error)) from None
    if step.role == "population" and not grain_keys:
        raise ProtocolError("population must declare grain_keys")
    if step.order_direction not in ("asc", "desc"):
        raise ProtocolError(f"order_direction must be asc or desc, got {step.order_direction!r}")
    max_rows = step.max_rows
    if step.role == "ranking":
        if order_by is None or max_rows is None:
            raise ProtocolError("ranking requires order_by and max_rows")
        if not isinstance(max_rows, int) or isinstance(max_rows, bool) or max_rows < 1:
            raise ProtocolError("ranking max_rows must be a positive integer")
    elif max_rows is not None:
        raise ProtocolError("max_rows is only valid on a ranking step")
    if step.max_fanout is not None and step.max_fanout < 1:
        raise ProtocolError("max_fanout must be >= 1 when it is set")
    expect_nonempty = step.expect_nonempty
    if expect_nonempty is None:
        expect_nonempty = step.role == "population"
    if not isinstance(expect_nonempty, bool):
        raise ProtocolError("expect_nonempty must be a boolean")
    _validate_step_sql(step.sql)
    return Step(
        role=step.role, cte_name=cte_name, sql=step.sql.strip().rstrip(";"), purpose=step.purpose,
        grain_keys=grain_keys, preserve_sums=preserve_sums, expect_nonempty=expect_nonempty,
        max_rows=max_rows, order_by=order_by, order_direction=step.order_direction, max_fanout=step.max_fanout,
    )


def compose(steps: list[Step], final_select: str | None) -> str:
    """One WITH query. `final_select` reads the last CTE, or every column of it is returned."""
    if not steps:
        raise ProtocolError("nothing to compose")
    last = steps[-1].cte_name
    tail = f"SELECT * FROM {last}" if final_select is None else _validate_final_select(final_select, last)
    bodies = [f"{quote_identifier(step.cte_name)} AS (\n{step.sql.strip().rstrip(';')}\n)" for step in steps]
    return "WITH " + ",\n".join(bodies) + "\n" + tail


def _check_step(prior: list[Step], step: Step, executor: GuardedDuckDbExecutor, tolerance: float) -> CheckReport:
    chain = prior + [step]
    select_list = aggregate_select_list(step.grain_keys, step.preserve_sums, step.cte_name)
    try:
        columns, rows = executor.execute(compose(chain, f"SELECT {select_list} FROM {quote_identifier(step.cte_name)}"))
    except (GuardError, Exception) as error:  # noqa: BLE001 - the failure is the check result
        return CheckReport(
            role=step.role, cte_name=step.cte_name, ok=False,
            failures=[CheckFailure("execution_error", _short(error))],
        )

    parent_sums: dict[str, float | None] = {}
    parent_error = None
    if step.preserve_sums and prior:
        parent_list = ", ".join(
            f"sum({quote_identifier(column)}) AS {sum_alias(column)}" for column in step.preserve_sums
        )
        parent_tail = f"SELECT {parent_list} FROM {quote_identifier(prior[-1].cte_name)}"
        try:
            parent_columns, parent_rows = executor.execute(compose(prior, parent_tail))
            parent_sums = _sums_from_row(step.preserve_sums, parent_columns, parent_rows)
        except (GuardError, Exception) as error:  # noqa: BLE001
            parent_error = _short(error)

    parent_row_count = None
    if step.max_fanout is not None and prior:
        parent_row_count = _parent_row_count(prior, executor)
    report = report_from_aggregate(
        role=step.role, cte_name=step.cte_name, columns=columns, rows=rows, grain_keys=step.grain_keys,
        preserve_sums=step.preserve_sums, expect_nonempty=bool(step.expect_nonempty), max_rows=step.max_rows,
        max_fanout=step.max_fanout, parent_row_count=parent_row_count,
        parent_sums=parent_sums, tolerance=tolerance,
    )
    if parent_error:
        report.failures.append(CheckFailure("parent_sum_failed", parent_error))
        report.ok = False
    clause_failure = _ranking_clause_failure(step) if step.role == "ranking" else None
    if clause_failure is not None:
        report.failures.append(clause_failure)
        report.ok = False
    if step.role == "ranking" and step.order_by and report.row_count:
        failure = _ranking_order_failure(chain, step, executor)
        if failure is not None:
            report.failures.append(failure)
            report.ok = False
    return report


def _parent_row_count(prior: list[Step], executor: GuardedDuckDbExecutor) -> int | None:
    """Recompute the previous CTE's row count. The previous report may have failed closed."""
    tail = f"SELECT count(*) AS row_count FROM {quote_identifier(prior[-1].cte_name)}"
    try:
        columns, rows = executor.execute(compose(prior, tail))
    except (GuardError, Exception):  # noqa: BLE001
        return None
    if len(rows) != 1:
        return None
    folded = {column.lower(): index for index, column in enumerate(columns)}
    index = folded.get("row_count")
    if index is None or rows[0][index] is None:
        return None
    return int(rows[0][index])


def _sums_from_row(columns_to_sum: tuple[str, ...], columns: list[str], rows: list[tuple]) -> dict[str, float | None]:
    if len(rows) != 1:
        raise ProtocolError(f"parent sum query returned {len(rows)} rows")
    folded = {column.lower(): index for index, column in enumerate(columns)}
    sums = {}
    for column in columns_to_sum:
        index = folded.get(sum_alias(column).lower())
        if index is None:
            raise ProtocolError(f"parent sum query has no {column}")
        value = rows[0][index]
        sums[column] = None if value is None else float(value)
    return sums


def _ranking_clause_failure(step: Step) -> CheckFailure | None:
    """The ranking SELECT itself must ORDER BY the measure and LIMIT to max_rows.

    The sandbox wraps every statement, so an ORDER BY that exists only in a
    comment would not be checked by reading the values back. The clause is the
    contract the scorer's ordered comparison depends on.
    """
    if step.order_by is None or step.max_rows is None:
        return None
    tree = sqlglot.parse_one(step.sql, read="duckdb")
    order = tree.args.get("order")
    expressions = list(order.expressions) if order is not None else []
    if not expressions:
        return CheckFailure("missing_order_by", "ranking SQL has no ORDER BY")
    first = expressions[0].sql(dialect="duckdb").lower()
    if step.order_by.lower() not in first:
        return CheckFailure("missing_order_by", f"ORDER BY does not lead with {step.order_by}")
    if step.order_direction == "desc" and " desc" not in f" {first}":
        return CheckFailure("missing_order_by", f"ORDER BY {step.order_by} is not DESC")
    if step.order_direction == "asc" and " desc" in f" {first}":
        return CheckFailure("missing_order_by", f"ORDER BY {step.order_by} is not ASC")
    limit = tree.args.get("limit")
    literal = limit.expression if limit is not None else None
    if not isinstance(literal, exp.Literal) or literal.is_string or int(literal.this) != step.max_rows:
        return CheckFailure("too_many_rows", f"ranking SQL must LIMIT {step.max_rows}")
    return None


def _ranking_order_failure(chain: list[Step], step: Step, executor: GuardedDuckDbExecutor) -> CheckFailure | None:
    measure = quote_identifier(step.order_by or "")
    tail = f"SELECT {measure} AS measure FROM {quote_identifier(step.cte_name)}"
    try:
        _columns, rows = executor.execute(compose(chain, tail))
    except (GuardError, Exception) as error:  # noqa: BLE001
        return CheckFailure("execution_error", f"ranking order check failed: {_short(error)}")
    return monotonic_failure([row[0] for row in rows], step.order_direction, step.order_by or "")


def _require_same_slot(current: Step, repaired: Step) -> None:
    if repaired.role != current.role or repaired.cte_name != current.cte_name:
        raise ProtocolError(
            f"repair must keep role {current.role!r} and CTE {current.cte_name!r}, "
            f"got {repaired.role!r} / {repaired.cte_name!r}"
        )


def _validate_step_sql(sql: str) -> None:
    if not str(sql).strip():
        raise ProtocolError("a step has empty SQL")
    try:
        statements = [statement for statement in sqlglot.parse(sql, read="duckdb") if statement is not None]
    except sqlglot.errors.ParseError as error:
        raise ProtocolError(f"step SQL did not parse: {str(error).splitlines()[0]}") from None
    if len(statements) != 1:
        raise ProtocolError("a step is exactly one SELECT")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise ProtocolError("a step must be a SELECT")
    if isinstance(tree.args.get("with_") or tree.args.get("with"), exp.With):
        raise ProtocolError("a step must not contain WITH; the protocol owns the CTE chain")


def _validate_final_select(final_select: str, last_cte: str) -> str:
    text = str(final_select).strip().rstrip(";")
    if not _FINAL_SELECT.match(text):
        raise ProtocolError("final_select must be a single SELECT and must not contain WITH")
    try:
        statements = [statement for statement in sqlglot.parse(text, read="duckdb") if statement is not None]
    except sqlglot.errors.ParseError as error:
        raise ProtocolError(f"final_select did not parse: {str(error).splitlines()[0]}") from None
    if len(statements) != 1 or isinstance(statements[0].args.get("with_") or statements[0].args.get("with"), exp.With):
        raise ProtocolError("final_select must be a single SELECT and must not contain WITH")
    if not re.search(rf"\b{re.escape(last_cte)}\b", text):
        raise ProtocolError(f"final_select does not read CTE {last_cte}")
    return text


def _trace_text(traces: list[StepTrace]) -> str:
    parts = []
    for trace in traces:
        status = "ok" if trace.report.ok else "failed"
        count = "unknown" if trace.report.row_count is None else str(trace.report.row_count)
        parts.append(f"{trace.step.role} {status} ({count} rows, {trace.repairs} repairs)")
    return "; ".join(parts)


def _short(error: BaseException) -> str:
    return str(error).splitlines()[0][:400]
