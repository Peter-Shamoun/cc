"""Candidate prompts and the tool spec.

Diversity is the prompt, not temperature. Claude sampling parameters return HTTP 400
(``research/technique_factors.yaml``). The three default generators are the screen-2 arm
in ``research/sota_refresh_2.md``: a direct answer, a grain / date-basis plan, and a
filter-then-aggregate split. Two further prompts exist so N=5 is a different procedure,
not three prompts repeated. They share one cached system prefix; the strategy text and
the question sit in the user turn.

The system prefix is the schema text the caller passes in (``tools.describe()``, which
already carries the basic glossary). This module does not add a pitfalls list.
"""
from __future__ import annotations

ROWS_SHOWN_TO_MODEL = 20

# Order is the N=5 pool. N=3 uses the first three, which is the screen-2 arm.
STRATEGY_ORDER = (
    "direct",
    "grain_and_date",
    "filter_then_aggregate",
    "unit_of_analysis",
    "literal_projection",
)

STRATEGY_PROMPTS = {
    "direct": (
        "Write one DuckDB SELECT that answers the question. "
        "Project only the columns the question asks for, in the order it asks for them. "
        "Submit it with final_answer. You may call run_sql first if you need to see a value or a column."
    ),
    "grain_and_date": (
        "Before the SELECT, decide and state in the answer text: "
        "(1) grain, what one row of the answer represents; "
        "(2) population, which source rows are included; "
        "(3) time basis — service date, submission date, or payment date — and only if the question names one. "
        "Then submit the SELECT that implements that plan. The SQL alone must compute the numbers."
    ),
    "filter_then_aggregate": (
        "Split the question into two steps and write them as one WITH query: "
        "(1) a CTE that filters the population; "
        "(2) an outer query that aggregates that CTE into the measures the question asks for. "
        "Submit that SELECT with final_answer."
    ),
    "unit_of_analysis": (
        "First name the source unit the question is counting: an 837 submission, an 835 adjudication, "
        "a service line, a payment, or an adjustment reason. State that unit in the answer text, "
        "then write the SELECT at that unit."
    ),
    "literal_projection": (
        "Read the question and list the output columns it asks for, in that order. "
        "Write a SELECT whose select-list is exactly those columns. "
        "Do not add a label, a share, or a supporting count the question did not ask for."
    ),
}

SYSTEM_PREFIX = (
    "You are CC, an analyst for one clinical lab's insurance claims (837P) and remittances (835).\n"
    "Answer the question from the semantic views below and the definitions that follow them. "
    "The SELECT you submit with final_answer is executed again and is the answer; do not invent numbers, "
    "and do not answer from a query that did not run.\n"
    "Use one read-only DuckDB SELECT over those views. "
    "If the request asks for patient names, member ids, birth dates, addresses, another customer's data, "
    "or any change to the data, call refuse and do not query.\n"
)

TOOL_SPEC = [
    {
        "name": "run_sql",
        "description": "Run one read-only DuckDB SELECT over the views. Returns column names and up to 20 rows.",
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    },
    {
        "name": "final_answer",
        "description": (
            "Submit the SELECT whose result answers the question, plus a one-paragraph answer. "
            "The SELECT is re-executed; the paragraph is not the source of the numbers."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}, "answer": {"type": "string"}},
            "required": ["sql", "answer"],
        },
    },
    {
        "name": "refuse",
        "description": "Decline a request for PHI, another customer's data, or a write.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
]

SELECTOR_STRATEGY = "__selector__"
VERIFIER_STRATEGY = "__verifier__"


def shared_system(schema_text: str) -> str:
    """Cacheable prefix: instructions, tool rules, schema, basic glossary. No question, no strategy."""
    return SYSTEM_PREFIX + "\nSchema and definitions:\n" + schema_text.strip() + "\n"


def user_message(strategy: str, question: str) -> str:
    if strategy not in STRATEGY_PROMPTS:
        raise KeyError(f"unknown strategy {strategy!r}; expected one of {STRATEGY_ORDER}")
    return STRATEGY_PROMPTS[strategy] + "\n\nQuestion: " + question.strip() + "\n"


def strategies_for(n: int) -> tuple[str, ...]:
    if n < 1 or n > len(STRATEGY_ORDER):
        raise ValueError(f"n_candidates must be 1..{len(STRATEGY_ORDER)}, got {n}")
    return STRATEGY_ORDER[:n]


def subagent_prompt(strategy: str, question: str, cli: str) -> str:
    """Briefing for one Claude Code subagent session, in the shape ``bench.agent_session.build_prompt`` uses.

    An orchestrator prepares N of these (one strategy each), lets each session call the same ``agent_cli``,
    then clusters the re-executed finals with ``selector.select``. ``{cli}`` is the session's
    ``python -m bench.agent_cli --session <dir>`` prefix. The harness does not yet accept this variant's
    config keys, so this text is what a later screen wires in.
    """
    if strategy not in STRATEGY_PROMPTS:
        raise KeyError(strategy)
    return (
        "You are CC, an analytics assistant for a clinical lab's insurance claims (837P) and remittances (835).\n"
        "Answer the user's question by querying the data through the command-line tool below. Use the Bash tool ONLY to "
        "run these exact commands; do not read, list, search or open any file or directory, do not run any other program, "
        "and do not write code. The answer is scored by re-running the query you submit, so the submitted query itself "
        "must compute the answer from the data.\n\n"
        f"How to work this time: {STRATEGY_PROMPTS[strategy]}\n\n"
        "Commands:\n"
        f"{cli} context            # views, columns and business definitions: run this first\n"
        f'{cli} sql "SELECT ..."   # read-only DuckDB SQL over the listed views; shows up to 20 rows\n\n'
        "Finish with exactly one of:\n"
        f'{cli} answer --sql "SELECT ..." --text "<one-paragraph answer>"\n'
        f'{cli} refuse "<reason>"   # for requests you must not fulfil\n\n'
        "You may make at most 8 commands besides context. Then reply with the single word DONE.\n\n"
        f"User question: {question.strip()}\n"
    )


def _digest(columns: list[str] | None, rows: list | None) -> str:
    if rows is None:
        return "(no result)"
    shown = rows[:ROWS_SHOWN_TO_MODEL]
    body = "\n".join(str(tuple(r)) for r in shown)
    more = "" if len(rows) <= ROWS_SHOWN_TO_MODEL else f"\n... {len(rows) - ROWS_SHOWN_TO_MODEL} more rows"
    return f"columns: {columns}\nrow_count: {len(rows)}\n{body}{more}"


def selector_user_message(question: str, clusters: list) -> str:
    """Pairwise / group selector. Sees the question, one SQL per result-cluster, and the executed rows.

    CHASE-SQL and Agentar both select on the execution result after grouping identical results.
    The model is not shown other clusters' vote counts as a reason to agree; the count is included
    because it is a fact about the pool, and the instruction says to pick the result that answers
    the question, not the largest group.
    """
    blocks = []
    for cluster in clusters:
        rep = cluster.representative
        strategies = ", ".join(member.strategy for member in cluster.members)
        blocks.append(
            f"CLUSTER {cluster.cluster_id} ({cluster.size} candidate(s); strategies: {strategies})\n"
            f"SQL:\n{rep.sql}\n"
            f"Answer text: {rep.text[:500]}\n"
            f"{_digest(rep.columns, rep.rows)}"
        )
    return (
        "Choose which executed result answers the question. The candidates were grouped by result "
        "equivalence (order ignored unless the question is a ranking, values within the matcher's rounding "
        "tolerance, a rate and the same rate times 100 when the question asks for a rate, tied rows at a "
        "top-N cutoff). Pick the group whose population, grain, and time basis match the question. "
        "Do not pick a group only because it is larger. If none is a defensible answer, abstain.\n"
        "Reply with exactly one line and nothing else: CLUSTER <id> or ABSTAIN.\n\n"
        f"Question: {question.strip()}\n\n" + "\n\n".join(blocks) + "\n"
    )


def verifier_user_message(question: str, cluster) -> str:
    rep = cluster.representative
    return (
        "Check this executed answer against the question. ACCEPT if the SQL's population, grain, and time "
        "basis match what was asked and the result is a plausible answer. REJECT if a filter the question "
        "requires is missing, the grain is wrong, or the result cannot be what was asked. "
        "Reply with exactly one line: ACCEPT or REJECT.\n\n"
        f"Question: {question.strip()}\n\nSQL:\n{rep.sql}\n\n{_digest(rep.columns, rep.rows)}\n"
    )
