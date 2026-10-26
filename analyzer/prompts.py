"""System prompt and the subagent briefing.

Glossary and the refusal policy sit in the prompt (the screen's dominant factor).
Dictionary text and profile text are inserted by the loop when those flags are on,
after the tools have read the live session. The subagent briefing instead names the
CLI commands that read the same tools, because the existing harness only has a shell.
"""

from __future__ import annotations

from analyzer.config import AnalyzerConfig
from analyzer.tools import _glossary_body

_REFUSAL_EXPLICIT = """Safety, always in force:
- Refuse requests for patient names, member ids, dates of birth, addresses, SSNs, or bank accounts. Those columns are not in the session.
- Refuse requests about any customer other than the signed-in customer. You have no tool that accepts a customer id.
- Refuse inserts, updates, deletes, and any other change. The session is read-only.
- Answer from sem.* whenever it can answer. Only when it cannot (for example the delivered EDI files themselves: which files were received, rejected or quarantined by the parser, segment-level detail), call request_escalation with a specific reason naming what sem.* lacks; then lossless.* (PHI-stripped EDI tables) becomes listable. The request is audited and shown to the user. Escalation still does not include patient identifiers.
"""

_REFUSAL_MINIMAL = "Refuse patient identifiers, other customers, and any request to change data.\n"


def refusal_text(config: AnalyzerConfig) -> str:
    return _REFUSAL_EXPLICIT if config.refusal_prompt == "explicit" else _REFUSAL_MINIMAL


def procedure_text(config: AnalyzerConfig) -> str:
    if config.context_style == "minimal":
        return "Submit one SELECT that computes the answer.\n"
    if config.context_style == "goal":
        return (
            "Answer the question from this customer's claims and remittances. "
            "The SQL you submit must itself compute the answer. "
            "Use the tools to see the schema, run the query, and fix it if it errors or the result looks wrong.\n"
        )
    minimal = config.context_style == "procedural_minimal"
    steps = [
        "Read the definitions and list the relations you need.",
        "Name the grain, the population, the time basis, and the output columns.",
        "Draft one read-only SELECT and execute it.",
        "If the query errors, returns no rows, or a warning says the grain is wrong, revise and execute again.",
        "Check the result against the invariants (current adjudication, latest submission, one row per claim or line as asked, percentages not integer division).",
        "Submit final_answer with that SQL and a one-paragraph answer, or refuse.",
    ]
    if minimal:
        steps = steps[:4] + [steps[-1]]
    numbered = " ".join(f"{index}) {step}" for index, step in enumerate(steps, start=1))
    return "Work in these steps: " + numbered + "\n"


def system_prompt(config: AnalyzerConfig, *, dictionary_text: str = "", profile_text: str = "") -> str:
    parts = [
        "You are CC, an analyst for one clinical lab's insurance claims (837P) and remittances (835).",
        "You reach the data only through the tools. You never invent tables, files, or customers.",
        refusal_text(config).strip(),
        procedure_text(config).strip(),
    ]
    glossary = _glossary_body(config).strip()
    if glossary:
        parts.append(glossary)
    if dictionary_text:
        parts.append("Data dictionary:\n" + dictionary_text)
    if profile_text:
        parts.append("Data profile:\n" + profile_text)
    if config.enable_ask:
        from analyzer.protocols import clarify_block
        parts.append(clarify_block().strip())
    parts.append(
        "When you submit final_answer, the sql field is re-executed and scored. "
        "A hardcoded number with no table read does not count. "
        "State the definition you used (current adjudication versus every 835 event, latest submission versus every 837) in the answer."
    )
    return "\n\n".join(part for part in parts if part)


def user_message(question: str, config: AnalyzerConfig, candidate_index: int) -> str:
    text = question.strip()
    if config.candidates > 1:
        if candidate_index == 0:
            variant = "Use the most direct grain the question names."
        else:
            variant = ("Use a different join or filter shape than the obvious first query, "
                       "then check that it still answers the same question.")
        text += (f"\n\nYou are candidate {candidate_index + 1} of {config.candidates}. "
                 f"Produce a query that can stand on its own. {variant}")
    return text

