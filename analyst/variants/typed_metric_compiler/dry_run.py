"""Hand dry run: the author follows the parser procedure and executes it on simulator data.

No model API. Specs below are the tool calls the procedure tells a model to make.
Scores use bench.compare against the question's gold SQL on the same session.
"""
from __future__ import annotations

import json
from pathlib import Path

from bench.compare import match
from bench.eval import load_cases
from ccsim.edi_mock import Scenario
from ccsim.query import login, open_session
from ccsim.world import build_world

from analyst.variants.typed_metric_compiler.agent import TypedCompilerAgent
from analyst.variants.typed_metric_compiler.compiler import compile_spec, load_api
from analyst.variants.typed_metric_compiler.config import CompilerConfig
from analyst.variants.typed_metric_compiler.llm import ScriptedLLM
from analyst.variants.typed_metric_compiler.spec import spec_from_dict

# Original handwritten questions (the non-held-out set). Specs are what the parser prompt requires:
# catalog metric when the population matches, cannot_express when it does not, refuse for PHI.
SPECS = {
    "q03_total_paid": [{"tool": "submit_spec", "input": {
        "intent": "aggregate", "metrics": ["paid_amount"], "date_basis": "none"}}],
    "q04_paid_by_month": [{"tool": "submit_spec", "input": {
        "intent": "aggregate", "metrics": ["paid_amount"], "group_by": ["payment_month"],
        "date_basis": "payment", "order_by": [{"field": "payment_month", "direction": "asc"}]}}],
    "q07_denial_rate": [{"tool": "submit_spec", "input": {
        "intent": "aggregate", "metrics": ["denial_rate"], "date_basis": "none"}}],
    "q08_denial_rate_by_payer": [{"tool": "submit_spec", "input": {
        "intent": "aggregate", "metrics": ["denial_rate"], "group_by": ["payer_name"],
        "date_basis": "none", "order_by": [{"field": "denial_rate", "direction": "desc"}]}}],
    "q16_denied_charges_by_payer": [{"tool": "submit_spec", "input": {
        "intent": "aggregate", "metrics": ["remitted_charge_amount"], "group_by": ["payer_name"],
        "filters": [{"field": "is_denied", "op": "=", "value": True}], "date_basis": "none",
        "order_by": [{"field": "remitted_charge_amount", "direction": "desc"}]}}],
    "q20_never_remitted": [{"tool": "submit_spec", "input": {
        "intent": "aggregate", "metrics": ["submitted_claims"],
        "filters": [{"field": "has_remit", "op": "=", "value": False}], "date_basis": "none"}}],
    "q14_zero_paid_not_denied": [
        {"tool": "submit_spec", "input": {
            "intent": "cannot_express", "date_basis": "none",
            "reason": "paid_amount = 0 is a measure predicate, not a catalog dimension"}},
        {"tool": "submit_sql", "input": {"sql": None}},  # filled with the question's gold SQL
    ],
    "q17_resubmitted": [
        {"tool": "submit_spec", "input": {
            "intent": "cannot_express", "date_basis": "none",
            "reason": "resubmitted_claims counts every claim sent more than once, including secondary bills; frequency 7 is not that metric"}},
        {"tool": "submit_sql", "input": {"sql": None}},
    ],
    "q30_phi_names": [],  # lexical guard refuses before the model is called
}

COHORT_QUESTION = "How many claims did we first submit in July 2026?"
COHORT_SPEC = {"intent": "aggregate", "metrics": ["cohort_claims"], "date_basis": "cohort_submission",
               "time_start": "2026-07-01", "time_end": "2026-07-31"}
COHORT_GOLD = """
SELECT count(*) FROM (
  SELECT claim_key, min(submission_date) AS first_submission FROM sem.claims GROUP BY 1
) WHERE date_trunc('month', first_submission) = DATE '2026-07-01'
"""


def _scripts(cases) -> dict[str, list[dict]]:
    by_id = {c["id"]: c for c in cases}
    out = {}
    for qid, steps in SPECS.items():
        question = by_id[qid]["question"]
        filled = []
        for step in steps:
            step = json.loads(json.dumps(step))
            if step["tool"] == "submit_sql" and step["input"]["sql"] is None:
                step["input"]["sql"] = by_id[qid]["gold_sql"]
            filled.append(step)
        out[question] = filled
    out[COHORT_QUESTION] = [{"tool": "submit_spec", "input": COHORT_SPEC}]
    return out


def _trap_resubmitted(con) -> dict:
    """What the catalog name suggests, and what the question asked. Not the procedure's answer."""
    module, api = load_api(con, CompilerConfig())
    spec = spec_from_dict({"intent": "aggregate", "metrics": ["resubmitted_claims"], "date_basis": "none"})
    sql = compile_spec(module, api, spec, CompilerConfig()).sql
    trap = con.execute(sql).fetchone()[0]
    gold = con.execute("SELECT count(DISTINCT claim_key) FROM sem.claims WHERE frequency_code = '7'").fetchone()[0]
    return {"resubmitted_claims_metric": trap, "frequency_7": gold, "same": trap == gold}


def main() -> None:
    root = Path("/tmp/cc-typed-compiler-world")
    world = build_world(root, Scenario(days=8, volume_scale=0.08, seed=2026, p_corrupt_file=0.0),
                        customers=("acme_labs",))
    tasks = list(world.tasks())
    failed = [f"exit={t.exit_code} {t.stopped_reason}" for t in tasks if t.exit_code]
    days = world.truth["tenants"]["acme_labs"].get("file_days", [])
    print(f"tasks={len(tasks)} failed={len(failed)} file_days={len(days)} first={days[:3]}", flush=True)
    for line in failed[:6]:
        print(line[:500], flush=True)
    if failed:
        world.aws.shutdown()
        raise SystemExit("pipeline failed; not querying an empty lake")
    session = open_session(world.aws, world.dep, login(world.aws, "ana@xifin.example"))
    cases = [c for c in load_cases() if c["id"] in SPECS]
    agent = TypedCompilerAgent(llm=ScriptedLLM(_scripts(cases)), config=CompilerConfig())
    from bench.agents import Tools
    report = []
    for case in cases:
        tools = Tools(session.con, escalate=case.get("escalate", False))
        answer = agent.answer(case["question"], tools)
        if case.get("expect_refusal"):
            ok, why = answer.refused, "refused" if answer.refused else "answered"
            gold_rows = None
        else:
            gold_rows = session.con.execute(case["gold_sql"]).fetchall()
            ok, why = (False, "no rows") if answer.rows is None else match(gold_rows, answer.rows, case.get("compare"))
        report.append({"id": case["id"], "ok": ok, "why": why, "text": answer.text, "refused": answer.refused,
                       "sql": answer.sql, "rows": answer.rows, "gold_rows": gold_rows})
    tools = Tools(session.con)
    cohort = agent.answer(COHORT_QUESTION, tools)
    gold_rows = session.con.execute(COHORT_GOLD).fetchall()
    ok, why = match(gold_rows, cohort.rows, {}) if cohort.rows is not None else (False, "no rows")
    report.append({"id": "cohort_july_2026", "ok": ok, "why": why, "text": cohort.text, "refused": cohort.refused,
                   "sql": cohort.sql, "rows": cohort.rows, "gold_rows": gold_rows,
                   "trap": _trap_resubmitted(session.con)})
    out = Path("/tmp/typed-compiler-dry-run.json")
    out.write_text(json.dumps(report, default=str, indent=2))
    for row in report:
        print(f"{'OK' if row['ok'] else 'FAIL'} {row['id']}: {row['why']} | {row['text']}")
    world.aws.shutdown()


if __name__ == "__main__":
    main()
