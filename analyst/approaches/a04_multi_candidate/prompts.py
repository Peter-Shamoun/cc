"""Prompt text for A04.

Three generators share one system prompt (the cacheable prefix: role, output contract, schema,
glossary). Only the user message changes, because current Claude models reject temperature
sampling, so diversity has to come from the prompt. The three shapes are the ones the stage-2
note in the literature review names for this harness: a direct question, a query plan that
fixes grain and time basis, and a divide-and-conquer split into filter then aggregation.
Those correspond to CHASE-SQL's direct, query-plan, and divide-and-conquer generators, with
the online-synthetic-example generator left out (few-shot examples are a separate factor and
are off in the screen-2 base).

The pairwise selector is a different call. It sees two executed candidates, including the rows
they returned, and may abstain.
"""
from __future__ import annotations

STRATEGIES = ("direct", "query_plan", "divide_and_conquer")

PURPOSE_SELECT = "select"


def generation_purpose(strategy: str) -> str:
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}")
    return f"generate:{strategy}"


def shared_system(schema_text: str, escalate: bool) -> str:
    """Identical for every generator. Callers should pass the same schema text each time."""
    scope = "You may query sem.* and lossless.*." if escalate else "You may query sem.* only."
    return (
        "You are CC, an analyst for a clinical lab's insurance claims (837P) and remittances (835).\n"
        "Write one read-only DuckDB SELECT over the views below. "
        f"{scope} Do not read files, change settings, or query any other schema.\n"
        "Never return patient names, member ids, birth dates, or addresses, and never answer about another customer. "
        'If the question asks for those, or asks you to change data, reply with {"refuse": "the reason"} and no SQL.\n'
        "Otherwise reply with JSON only, no markdown, in this shape:\n"
        '{"sql": "SELECT ...", "notes": "short reasoning"}\n\n'
        "Schema and business definitions:\n"
        f"{schema_text.rstrip()}\n"
    )


def generation_user(strategy: str, question: str) -> str:
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}")
    if strategy == "direct":
        body = (
            "Strategy: direct.\n"
            "Write one DuckDB SELECT that answers the question. Put a one-line restatement of the "
            "population and the measure in \"notes\".\n"
        )
    elif strategy == "query_plan":
        body = (
            "Strategy: query plan.\n"
            "In \"notes\", name the grain (one row per what), the population, and the time basis the question "
            "is using (payment_date, service_date, or submission_date). If the question does not use a date, say so. "
            "Then write one DuckDB SELECT that implements that plan.\n"
        )
    else:
        body = (
            "Strategy: divide and conquer.\n"
            "Split the question into two parts and put the split in \"notes\":\n"
            "1. Filter: which rows belong in the population (payer responsibility, latest submission, "
            "current adjudication, time window).\n"
            "2. Aggregation: the measure, and the grain of the result.\n"
            "Then write one DuckDB SELECT that applies the filter in a CTE or subquery and aggregates after it.\n"
        )
    return f"{body}\nQuestion: {question}\n"


def selector_system() -> str:
    return (
        "You choose which executed SQL answer to return for a clinical-lab billing question.\n"
        "You see two candidates, each with the SQL it ran and the rows that SQL actually returned.\n"
        "Prefer the result that uses the right population, grain, and time basis.\n"
        "Ignore column names and row order unless the question asks for a ranking or a sort.\n"
        "Treat an empty result, a rate outside 0..100, or a join that repeats a claim once per line as a suspect answer.\n"
        "Abstain if neither result answers the question. A wrong number is worse than no answer.\n"
        "Do not prefer candidate a just because it is listed first.\n"
        'Reply with JSON only: {"choice": "a" or "b" or "abstain", "reason": "one sentence"}\n'
    )


def selector_user(question: str, left, right) -> str:
    """`left` and `right` are candidates. `a` is whichever the caller put first, not a quality rank."""
    return (
        f"Question: {question}\n\n"
        f"{_candidate_block('a', left)}\n\n"
        f"{_candidate_block('b', right)}\n"
    )


def _candidate_block(label: str, candidate) -> str:
    warnings = ", ".join(candidate.warnings) if candidate.warnings else "none"
    preview = candidate.rows[:20]
    truncated = len(candidate.rows) - len(preview)
    note = f", {truncated} more rows not shown" if truncated else ""
    return (
        f"Candidate {label} (strategy {candidate.strategy}), {len(candidate.rows)} rows{note}, warnings: {warnings}\n"
        f"SQL:\n{candidate.sql}\n"
        f"Columns: {list(candidate.columns)}\n"
        f"Rows: {preview}\n"
    )
