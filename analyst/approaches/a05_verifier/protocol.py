"""Plan, then SQL, execute, then one fresh-context critic. Revise once if the critic objects.

Steps, in order, with no extra loop:
  1. The analyst returns a plan, or a refusal. Any SQL in that reply is ignored.
  2. The analyst returns one SELECT for that plan.
  3. The executor runs the SELECT through the guard. A failure gets one analyst repair.
  4. After a result exists, the critic runs in a new context. It sees the question, the
     plan fields, the SQL that produced the result, and a digest. It does not see the
     analyst's raw completion.
  5. Accept: submit that SQL. Object: the analyst revises the plan and the SQL once, the
     new SQL is executed, and it is submitted if it runs. The critic is not called again.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from analyst.approaches.a05_verifier.backend import LanguageModel
from analyst.approaches.a05_verifier.checklist import checklist_sha256
from analyst.approaches.a05_verifier.executor import QueryExecutor, QueryResult
from analyst.approaches.a05_verifier.prompts import (
    AnalystTurn,
    ProtocolParseError,
    analyst_plan_user_message,
    analyst_repair_user_message,
    analyst_revision_user_message,
    analyst_sql_user_message,
    critic_user_message,
    parse_analyst_turn,
    parse_critic_verdict,
    parse_plan_response,
    parse_sql_response,
    result_digest,
    system_for_analyst,
    system_for_critic,
)


@dataclass
class ProtocolTrace:
    outcome: str = "no_answer"
    revised: bool = False
    refused: bool = False
    execution_repairs: int = 0
    plan: dict | None = None
    submitted_sql: str | None = None
    prose: str = ""
    attempts: list[dict] = field(default_factory=list)
    critic: dict | None = None
    model_calls: list[dict] = field(default_factory=list)
    checklist_sha256: str = ""

    def to_dict(self) -> dict:
        return {
            "approach": "a05_verifier",
            "outcome": self.outcome,
            "revised": self.revised,
            "refused": self.refused,
            "execution_repairs": self.execution_repairs,
            "plan": self.plan,
            "submitted_sql": self.submitted_sql,
            "prose": self.prose,
            "attempts": self.attempts,
            "critic": self.critic,
            "model_calls": self.model_calls,
            "checklist_sha256": self.checklist_sha256,
        }


def run_protocol(question: str, model: LanguageModel, executor: QueryExecutor) -> ProtocolTrace:
    trace = ProtocolTrace(checklist_sha256=checklist_sha256())
    schema_context = executor.schema_context()
    analyst_system = system_for_analyst(schema_context)
    plan_turn = _analyst_turn(
        model, analyst_system, analyst_plan_user_message(question), "plan", trace, parse_plan_response)
    if plan_turn is None:
        return trace
    if plan_turn.refuse:
        executor.submit_refusal(plan_turn.refuse)
        trace.refused = True
        trace.outcome = "refused"
        trace.prose = plan_turn.refuse
        return trace
    if plan_turn.plan is None:
        return trace

    sql_turn = _analyst_turn(
        model, analyst_system, analyst_sql_user_message(question, plan_turn.plan), "sql", trace, parse_sql_response)
    if sql_turn is None or sql_turn.refuse or sql_turn.sql is None:
        return trace
    drafted = AnalystTurn(plan=plan_turn.plan, sql=sql_turn.sql, prose=sql_turn.prose, refuse=None)
    result = _execute_with_one_repair(question, model, executor, analyst_system, drafted, trace)
    if result is None:
        return trace

    digest = result_digest(result.columns, result.rows)
    critic_user = critic_user_message(question, trace.plan, trace.submitted_sql, digest)
    critic_text = _complete(model, "critic", system_for_critic(schema_context), critic_user, "critic", trace)
    verdict = parse_critic_verdict(critic_text)
    trace.critic = verdict
    if verdict["accept"]:
        executor.submit_answer(trace.submitted_sql, trace.prose)
        trace.outcome = "accepted"
        return trace

    revised = _analyst_turn(
        model, analyst_system,
        analyst_revision_user_message(question, trace.plan, trace.submitted_sql, digest, verdict["objections"]),
        "revision", trace, parse_analyst_turn)
    if revised is None or revised.refuse or revised.sql is None or revised.plan is None:
        trace.submitted_sql = None
        trace.outcome = "no_answer"
        return trace
    revised_result = _execute(executor, revised, "revision", trace)
    if not revised_result.ok:
        trace.submitted_sql = None
        trace.outcome = "no_answer"
        return trace
    trace.plan = revised.plan
    trace.submitted_sql = revised.sql
    trace.prose = revised.prose
    trace.revised = True
    executor.submit_answer(revised.sql, revised.prose)
    trace.outcome = "revised"
    return trace


def _execute_with_one_repair(question, model, executor, analyst_system, turn: AnalystTurn, trace: ProtocolTrace):
    """Run the analyst SQL. On failure, one repair, then stop. The critic sees only a result."""
    result = _execute(executor, turn, "sql", trace)
    if result.ok:
        trace.plan = turn.plan
        trace.submitted_sql = turn.sql
        trace.prose = turn.prose
        return result
    repair = _analyst_turn(
        model, analyst_system,
        analyst_repair_user_message(question, turn.plan, turn.sql, result.error or ""),
        "execution_repair", trace, parse_analyst_turn)
    trace.execution_repairs += 1
    if repair is None or repair.refuse or repair.sql is None or repair.plan is None:
        return None
    repaired = _execute(executor, repair, "execution_repair", trace)
    if not repaired.ok:
        return None
    trace.plan = repair.plan
    trace.submitted_sql = repair.sql
    trace.prose = repair.prose
    return repaired


def _execute(executor: QueryExecutor, turn: AnalystTurn, phase: str, trace: ProtocolTrace) -> QueryResult:
    result = executor.execute(turn.sql or "")
    trace.attempts.append({
        "phase": phase,
        "sql": turn.sql,
        "ok": result.ok,
        "error": result.error,
        "row_count_returned": len(result.rows),
    })
    return result


def _analyst_turn(model, system: str, user: str, phase: str, trace: ProtocolTrace, parse) -> AnalystTurn | None:
    text = _complete(model, "analyst", system, user, phase, trace)
    try:
        return parse(text)
    except ProtocolParseError:
        return None


def _complete(model, role: str, system: str, user: str, phase: str, trace: ProtocolTrace) -> str:
    completion = model.complete(role=role, system=system, user=user, phase=phase)
    trace.model_calls.append({
        "role": role,
        "phase": phase,
        "input_chars": len(system) + len(user),
        "output_chars": len(completion.text),
    })
    return completion.text
