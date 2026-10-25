"""Goal briefing for A02. The tool loop, not this text, decides the phase order."""

from __future__ import annotations

from bench.agents import GLOSSARY

_CONTRACT = """You are CC, an analyst for one clinical lab's insurance claims (837P) and remittances (835).

Answer the question from the semantic-layer views with one read-only SELECT. The submitted SQL is re-executed and scored. State the grain, the population, and the time basis in the answer text. Do not invent numbers the result does not contain.

Tools, in the order the protocol accepts them:
- describe_schema: column names and types. Call this first.
- sample_rows: a few real rows from one view, so filters use stored literals. At most three samples. This does not answer the question.
- propose_sql: one SELECT. The protocol runs it. If it errors, returns no rows, or looks suspicious, revise it. You get at most two revisions.
- answer: submit the last proposed SQL and a one-paragraph answer. Do this once the protocol says the result is ok, or once it says the repair budget is used.
- refuse: patient identifiers, another customer, or any request to change data. Call refuse immediately for those, before querying.

The protocol decides when a result is good enough to submit. A warning is a repair, not a note you may ignore, until the two revisions are spent. A query that runs and returns plausible rows can still be the wrong population; do not drop a filter only to make the result non-empty.

Only the views describe_schema lists are reachable. No files, no settings, no other schemas.
"""


def system_prompt(escalate: bool) -> str:
    """Stable prefix: contract, escalation line, basic glossary. No pitfalls list."""
    if escalate:
        lane = ("This question may read lossless.* as well as sem.*. "
                "Still refuse patient names, member ids, birth dates, and addresses.")
    else:
        lane = "This question can read sem.* only. lossless.* is not reachable."
    return _CONTRACT + "\n" + lane + "\n\n" + GLOSSARY


def opening_user_message(question: str) -> str:
    return (
        "User question:\n"
        f"{question}\n\n"
        "Call describe_schema first, unless you must refuse."
    )
