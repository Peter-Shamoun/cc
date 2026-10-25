"""Prompt block for A10 examples.

The block is per question. ``render_prompt`` inserts it immediately before the
harness marker ``User question:``, which ``bench.agent_session.build_prompt``
places at the end of the briefing. The briefing stays the prefix; the examples
travel with the question.

The block contains the example question and its reference SQL. It does not
contain scores, answer-key SQL, interpretations, or expected numbers. Those
fields on a bench question often state the gold result.
"""
from __future__ import annotations

from collections.abc import Sequence

USER_QUESTION_MARK = "\n\nUser question: "

_HEADER = (
    "Similar solved questions from other analyses. "
    "Use them as patterns for grain, filters, and which view holds the measure. "
    "Do not copy their literals, payer names, codes, or dates onto the question below. "
    "Each SQL is the reference query for that example, not the answer to the user's question."
)


def render_examples(examples: Sequence) -> str:
    """Format retrieved examples. An empty selection yields an empty string."""
    if not examples:
        return ""
    parts = [_HEADER, ""]
    for number, example in enumerate(examples, start=1):
        parts.append(
            f"Example {number} ({example.question_id})\n"
            f"Question: {example.question.strip()}\n"
            f"Reference SQL:\n{example.gold_sql.strip()}\n"
        )
    return "\n".join(parts).rstrip() + "\n"


def render_prompt(base_prompt: str, examples: Sequence) -> str:
    """Insert the example block in front of the user question.

    Refuses to append when the harness marker is missing, so examples cannot
    land after the question or on an unrelated prompt. An empty selection
    returns ``base_prompt`` unchanged.
    """
    block = render_examples(examples)
    if not block:
        return base_prompt
    if USER_QUESTION_MARK not in base_prompt:
        raise ValueError(
            "prompt has no 'User question:' marker; refusing to attach examples "
            "after the question or to an unrelated prompt"
        )
    head, tail = base_prompt.rsplit(USER_QUESTION_MARK, 1)
    return head.rstrip() + "\n\n" + block + "\n" + USER_QUESTION_MARK.lstrip("\n") + tail
