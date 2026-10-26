"""Briefing blocks for the subagent route, one per parameter level, reusing the approaches' text.

- #7 minimal style: ``analyzer.prompts.procedure_text`` (one line, no procedure).
- #8 decompose: A07's roles and check rules (``analyst/approaches/a07_decompose/prompts.py``), with the commands
  renamed to analyzer.cli. #8 compiler: A03's spec contract and catalog, through ``spec`` / ``answer --spec``.
- #9 probes: A06's probe protocol (``harness_protocol``), run through the uncharged ``probe`` command.
- #10 plan / replan, #11 assumptions, #25 restate: one interpret record (``INTERPRETATION_SCHEMA``) whose parts are
  A05's plan fields (restate), A09's axes and checklist (assumptions) and ordered steps (plan). The Sonnet planner
  role (#18) writes the same record, so the executor reads exactly what a self-planned session writes.
- #15 candidate shapes: A04's direct / query-plan / divide-and-conquer generator text.
- #14 verifier and #15 selector role briefings: A05's critic rubric and A04's selector rules.
"""

from __future__ import annotations

import json

from analyst.approaches.a04_multi_candidate.prompts import generation_user, selector_system
from analyst.approaches.a05_verifier.checklist import CHECK_NAMES, render_checklist as render_critic_checklist
from analyst.approaches.a05_verifier.prompts import CRITIC_SYSTEM_PREFIX, PLAN_FIELDS
from analyst.approaches.a06_explore_first.probes import maximum_probe_statements
from analyst.approaches.a06_explore_first.prompts import harness_protocol
from analyst.approaches.a07_decompose.prompts import CHECK_RULES, STEP_ROLES
from analyst.approaches.a09_assumptions_first.checklist import render_checklist as render_assumption_checklist
from analyst.approaches.a10_dynamic_fewshot.prompts import render_examples

from analyzer.config import AnalyzerConfig

ASSUMPTION_AXES = ("population", "which_claims", "date_basis", "units", "ties")
CONFIDENCE_LEVELS = ("high", "medium", "low")
MAXIMUM_PROBES = maximum_probe_statements()


class InterpretationError(ValueError):
    """An interpret record that does not have the parts this session requires."""


def required_interpretation_parts(config: AnalyzerConfig, *, planner: bool = False) -> list[str]:
    """Which parts of the interpret record this session must write. The planner writes all three (B9)."""
    if planner:
        return ["restatement", "assumptions", "steps"]
    parts = []
    if config.restate_question:
        parts.append("restatement")
    if config.state_assumptions:
        parts.append("assumptions")
    if config.plan_mode in {"plan", "plan_and_replan"}:
        parts.append("steps")
    return parts


def validate_interpretation(record: dict, required_parts: list[str]) -> dict:
    """Fail closed: every required part present and well formed. Returns the record with only known parts."""
    if not isinstance(record, dict):
        raise InterpretationError("interpret takes one JSON object")
    unknown = set(record) - {"restatement", "assumptions", "steps"}
    if unknown:
        raise InterpretationError(f"unknown interpret parts {sorted(unknown)}; use restatement, assumptions, steps")
    missing = [part for part in required_parts if part not in record]
    if missing:
        raise InterpretationError(f"interpret is missing {', '.join(missing)}")
    if "restatement" in record:
        restatement = record["restatement"]
        if not isinstance(restatement, dict):
            raise InterpretationError("restatement must be an object")
        empty = [name for name in PLAN_FIELDS if not isinstance(restatement.get(name), str) or not restatement[name].strip()]
        if empty:
            raise InterpretationError(f"restatement needs non-empty {', '.join(empty)}")
    if "assumptions" in record:
        assumptions = record["assumptions"]
        if not isinstance(assumptions, list):
            raise InterpretationError("assumptions must be a list (empty when the question pins every axis)")
        for item in assumptions:
            if not isinstance(item, dict) or item.get("axis") not in ASSUMPTION_AXES:
                raise InterpretationError(f"each assumption needs an axis in {', '.join(ASSUMPTION_AXES)}")
            considered = item.get("considered")
            if not isinstance(considered, list) or not considered or item.get("chosen") not in considered:
                raise InterpretationError(f"assumption {item['axis']}: list the readings considered and choose one")
            if not isinstance(item.get("sentence"), str) or not item["sentence"].strip():
                raise InterpretationError(f"assumption {item['axis']}: give the sentence the answer will state")
    if "steps" in record:
        steps = record["steps"]
        if not isinstance(steps, list) or not steps or not all(isinstance(step, str) and step.strip() for step in steps):
            raise InterpretationError("steps must be a non-empty list of strings")
    return {part: record[part] for part in ("restatement", "assumptions", "steps") if part in record}


def interpretation_example(required_parts: list[str]) -> str:
    example = {
        "restatement": {name: f"<{name}>" for name in PLAN_FIELDS},
        "assumptions": [{"axis": "date_basis", "considered": ["paid", "service"], "chosen": "paid",
                         "sentence": "<the assumption sentence the answer states>"}],
        "steps": ["<step 1>", "<step 2>"],
    }
    return json.dumps({part: example[part] for part in required_parts})


def interpretation_block(config: AnalyzerConfig, cli: str, *, planner: bool = False) -> str:
    """#10 / #11 / #25 in one record. Empty when none of them is on."""
    parts = required_interpretation_parts(config, planner=planner)
    if not parts:
        return ""
    if planner:
        lines = ["Plan record, your deliverable (the executor follows it and states its assumption sentences):"]
    else:
        lines = ["Interpretation record, required before the first sql" + (
            ", spec" if config.generation_mode.startswith("compiler") else "") + " or answer command:"]
    if "restatement" in parts:
        lines.append("- restatement: restate the question as " + ", ".join(PLAN_FIELDS)
                     + " (grain = one row per what; time_basis = payment, service, submission, or none; units = "
                       "claims, submissions, lines, payments, dollars, fraction, or percent).")
    if "assumptions" in parts:
        lines.append("- assumptions: for every axis below that the question leaves open, list the readings you "
                     "considered, lock the standard one, and write the sentence the answer will state. Copy those "
                     "sentences into the answer text.")
    if "steps" in parts:
        lines.append("- steps: the ordered query steps you will take (which view, which filters, which join, which "
                     "aggregation), short.")
    if config.plan_mode == "plan_and_replan" and not planner:
        lines.append("- replan: after an answer is rejected for repair, record a revised interpret before the next "
                     "sql or answer. The CLI rejects them until you do.")
    lines.append(f"{cli} interpret '{interpretation_example(parts)}'")
    if "assumptions" in parts:
        lines.append("")
        lines.append(render_assumption_checklist())
    return "\n".join(lines) + "\n"


def external_plan_block(plan: dict) -> str:
    """B9 executor: the Sonnet planner's interpret record, followed as given."""
    return ("Plan written for this question by a planner (restatement, locked assumptions, steps). Follow it. "
            "Deviate only if the data contradicts it, and say so in the answer. State its assumption sentences in "
            "the answer text.\n" + json.dumps(plan, indent=1) + "\n")


def probe_block(cli: str) -> str:
    """#9: A06's protocol, through the probe command (not charged to the command budget)."""
    protocol = harness_protocol()
    original = "explore with the sql command"
    if original not in protocol:
        raise RuntimeError("A06 harness_protocol changed; update analyzer.protocols.probe_block")
    protocol = protocol.replace(original, f"explore with the probe command ({cli} probe \"SELECT ...\"); probes do "
                                          f"not count against the command budget, at most {MAXIMUM_PROBES} probes")
    return protocol


def decompose_block(cli: str) -> str:
    """#8 decompose: A07's roles and checks, on analyzer.cli commands."""
    return (
        "Build the answer as a chain of checked CTEs. Roles, in this order, skipping any the question does not "
        f"need: {', '.join(STEP_ROLES)}. Population is required and comes first. For each step, write it as one "
        f"SELECT with no WITH of its own, then run the chain so far as one check query with {cli} sql \"WITH ... "
        "SELECT count(*) AS row_count, ...\" and read the single result row before the next step. The submitted "
        "answer is one WITH of every step and a final SELECT that reads the last CTE.\n\n" + CHECK_RULES)


def compiler_block(config: AnalyzerConfig, cli: str) -> str:
    """#8 compiler: A03's contract. The CLI rejects sql and answer --sql until a spec returned cannot_express."""
    from analyst.approaches.a03_metric_spec.catalog import catalog_text
    fallback = ("write the answer as checked CTEs (decomposition below)" if config.generation_mode ==
                "compiler_decompose_fallback" else "write one SELECT")
    example = ('{"population":"current_adjudications","metrics":[{"alias":"denial_rate","agg":"avg","field":'
               '"is_denied"}],"grain":["payer_name"],"order_by":[{"field":"denial_rate","direction":"desc"}],'
               '"rounding":{"digits":4,"aliases":["denial_rate"]}}')
    return (
        "Answer by writing a metric spec first. A compiler turns it into SQL over sem.*; it owns the population "
        "predicate, the joins, fan-out and rounding.\n"
        f"{cli} spec '<json>'   # compiles and runs the spec, shows the rows\n"
        f"{cli} answer --spec '<json>' --text \"<one paragraph>\"   # submits the compiled query\n"
        "A message starting 'spec:' means the JSON shape is wrong; fix it. 'cannot_express:' means the language "
        "cannot say the question (you may also send {\"status\": \"cannot_express\", \"because\": \"...\"}). Only "
        f"after cannot_express are sql and answer --sql accepted; then {fallback}.\n"
        "Rates are fractions unless the question says percent. Round only what the question asks to round.\n"
        f"Example spec (denial rate by payer, fraction to 4 digits):\n{example}\n\n{catalog_text()}\n")


def candidate_shape_block(shape: str) -> str:
    """#15: the A04 generator text for this candidate's shape, without its JSON output contract."""
    text = generation_user(shape, "<the user question below>")
    body = text.split("\nQuestion:", 1)[0].strip()
    return ("This session is one of several independent candidates. " + body.replace('"notes"', "your reasoning")
            + "\n")


def confidence_block() -> str:
    return ("Add --confidence high, medium or low to the answer command: how sure you are that the SQL computes what "
            "the question asked (population, grain, time basis, units). low sends the question to a stronger "
            "model.\n")


def examples_block(examples: list) -> str:
    """#6: A10's example block over TRAIN-split questions retrieved by analyzer.examples."""
    return render_examples(examples)


def playbook_block(text: str) -> str:
    return "Playbook (frozen, distilled from training-split runs):\n" + text.strip() + "\n"


def verifier_briefing(question: str, schema_text: str, reviewed: dict) -> str:
    """#14 Haiku verifier: A05's critic rubric over the reviewed session's SQL and result digest."""
    reply_line = "Reply with one JSON object and nothing else:"
    if reply_line not in CRITIC_SYSTEM_PREFIX:
        raise RuntimeError("A05 CRITIC_SYSTEM_PREFIX changed; update analyzer.protocols.verifier_briefing")
    prefix = CRITIC_SYSTEM_PREFIX.replace(reply_line, "Your verdict is one JSON object, passed to the verdict command:")
    return (prefix + render_critic_checklist() + "\n--- schema and glossary ---\n" + schema_text
            + "\n\nQuestion:\n" + question + "\n\nInterpretation the analyst recorded:\n"
            + json.dumps(reviewed.get("interpretation"), default=str) + "\n\nSQL:\n" + str(reviewed.get("sql"))
            + "\n\nResult digest:\n" + json.dumps(reviewed.get("digest"), default=str) + "\n")


VERDICT_EXAMPLE = json.dumps({"verdict": "accept", "checks": {name: {"ok": True, "note": "<one sentence>"}
                                                              for name in CHECK_NAMES}, "objections": []})


def selector_briefing(question: str, candidates: list[dict]) -> str:
    """#15 Sonnet selector: A04's selection rules over every distinct executed result, labelled a, b, c."""
    rules = selector_system().replace("You see two candidates", "You see the candidates that disagreed")
    rules = rules.split("Reply with JSON only")[0]
    blocks = []
    for candidate in candidates:
        blocks.append(
            f"Candidate {candidate['label']} ({candidate['role']}), {candidate['row_count']} rows, "
            f"warnings: {', '.join(candidate['warnings']) or 'none'}, verifier: {candidate.get('verifier') or 'none'}\n"
            f"SQL:\n{candidate['sql']}\nColumns: {candidate['columns']}\nRows: {candidate['rows']}\n")
    return rules + f"\nQuestion: {question}\n\n" + "\n".join(blocks)
