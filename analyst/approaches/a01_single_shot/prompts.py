"""Prompts for A01.

The system prompt is the cached prefix: the goal, the basic glossary, and the generated data
dictionary. The user prompt is only the question, so a question cannot rewrite the tool list
or the glossary. Procedural steps, pitfalls, sample values, and solved examples are not included.
"""

from __future__ import annotations

import json

from bench.agents import GLOSSARY as BASIC_GLOSSARY

ROWS_SHOWN_IN_ANSWER_TEXT = 20

SYSTEM_PROMPT = """You are CC, an analytics assistant for one clinical lab's professional claims (837P) and remittances (835).

Answer the user's question by calling submit_sql with one read-only DuckDB SELECT over the sem.* views in the data dictionary, or by calling refuse.
submit_sql is executed once. Its result is the answer. You will not see the rows, an error, or another turn, so you cannot explore and you cannot repair.
The scored answer is that execution. The SELECT itself must compute every figure from the views. Do not invent numbers.
Call refuse, and do not call submit_sql, only when the question asks for patient names, member ids, birth dates or addresses, for another customer's data, or for any change to the data.

{glossary}

{dictionary}
"""

USER_PROMPT = """Question:
{question}
"""


def render_system_prompt(dictionary: str, glossary: str = BASIC_GLOSSARY) -> str:
    if not dictionary.strip():
        raise ValueError("data dictionary is empty")
    return SYSTEM_PROMPT.format(glossary=glossary.strip(), dictionary=dictionary.strip())


def render_user_prompt(question: str) -> str:
    if not question.strip():
        raise ValueError("question is empty")
    return USER_PROMPT.format(question=question.strip())


def render_answer_text(columns: list[str], rows: list) -> str:
    """Prose stand-in taken from the execution, not from the model."""
    shown = rows[:ROWS_SHOWN_IN_ANSWER_TEXT]
    return json.dumps(
        {
            "columns": columns,
            "row_count": len(rows),
            "rows": [list(row) for row in shown],
            "truncated": len(rows) > ROWS_SHOWN_IN_ANSWER_TEXT,
        },
        default=str,
    )
