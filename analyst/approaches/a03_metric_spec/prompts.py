"""Prompt and tool schema for the metric-spec agent.

The contract is a tool contract, not a numbered procedure. The CLI rejects a SQL
answer until a spec has come back cannot_express. Patient identifiers, another
customer, and writes are refusals even then.
"""
from __future__ import annotations

import shlex
from pathlib import Path

from bench.agents import GLOSSARY

from .catalog import catalog_text
from .schema import tool_input_schema

_ROOT = Path(__file__).resolve().parents[3]


def tool_spec() -> dict:
    return {
        "name": "compile_metric_spec",
        "description": (
            "Compile a metric spec into one read-only SELECT over sem.* and run it. "
            "Name the population, metrics, filters, grain, time window, tie rule, and rounding. "
            "Do not write SQL, a table name, or a join. "
            "If the question cannot be said in the spec, send {\"status\": \"cannot_express\", \"because\": \"...\"}. "
            "A SpecError means the JSON shape is wrong; fix it. "
            "cannot_express is the only result that permits a later SQL answer. "
            "Refuse, do not compile, when the question asks for patient identifiers, another customer, or a change to the data."
        ),
        "input_schema": tool_input_schema(),
    }


def cli_path() -> str:
    return str(_ROOT / "analyst/approaches/a03_metric_spec/adapter.py")


def command_lines(session_dir: str) -> str:
    cli = f"python3 {shlex.quote(cli_path())} --session {shlex.quote(session_dir)}"
    return "\n".join([
        f"{cli} catalog",
        f"{cli} spec '<json>'",
        f"{cli} answer --spec '<json>' --text \"<one paragraph, no number that is not in the result>\"",
        f"{cli} answer --sql \"SELECT ...\" --text \"<one paragraph>\"    # only after cannot_express",
        f"{cli} refuse \"<reason>\"",
    ])


def build_prompt(question: str, session_dir: str, escalate: bool) -> str:
    escalation = (
        "This question may read lossless.* as well as sem.*. The spec still compiles only sem.*. "
        "If it returns cannot_express, the SQL fallback may use lossless.*."
        if escalate else
        "This question is answered from sem.* only."
    )
    example = """{"population":"current_adjudications","metrics":[{"alias":"denial_rate","agg":"avg","field":"is_denied"}],"grain":["payer_name"],"order_by":[{"field":"denial_rate","direction":"desc"}],"rounding":{"digits":4,"aliases":["denial_rate"]}}"""
    return (
        "You are CC, an analytics assistant for one clinical lab's insurance claims (837P) and remittances (835).\n"
        "Answer the question by writing a metric spec. A compiler turns that spec into SQL over sem.*. "
        "You do not pick a join. You do not write SQL unless the compiler says the spec cannot express the question.\n\n"
        "The spec states the metric, the population, the filters, the grain, the time window, and the tie and rounding rules. "
        "The compiler owns the population predicate, the joins behind a link, fan-out, and the final rounding.\n\n"
        f"{escalation}\n\n"
        "Refuse, and do not compile a spec, when the question asks for patient names, member ids, birth dates, or addresses, "
        "for another customer's data, or for any change to the data. Those facts are not in sem.*.\n\n"
        "Use the Bash tool only to run the commands below. Do not read files or run any other program. "
        "The answer is scored by re-running the SQL the compiler (or, after cannot_express, you) submit.\n\n"
        "Commands:\n"
        f"{command_lines(session_dir)}\n\n"
        "spec runs the compiled query and shows up to 20 rows. answer --spec submits that query. "
        "answer --sql is rejected until a spec for this session has returned cannot_express. "
        "A message that starts with 'spec:' means the JSON shape is wrong; fix the spec. "
        "A message that starts with 'cannot_express:' means the language cannot say the question. "
        "Only then, and only for an analytics question this session is allowed to answer, write one SELECT.\n\n"
        "Rates are fractions unless the question says percent. Round only the aliases the question asks to round, and only at the end. "
        "A top-N question that does not say to include ties uses ties strict and a deterministic tie-break, usually the dimension ascending, after the metric. "
        "ties include_boundary ranks on the metrics in order_by and keeps every row tied with the Nth, so it can return more rows than the limit. "
        "Grain columns in order_by only order the display.\n\n"
        "Example of a spec, not of an answer: denial rate by payer, as a fraction rounded to 4 digits, "
        "current adjudications, one row per payer.\n"
        f"{example}\n\n"
        f"{catalog_text()}\n\n"
        f"{GLOSSARY}\n"
        f"User question: {question}\n"
    )
