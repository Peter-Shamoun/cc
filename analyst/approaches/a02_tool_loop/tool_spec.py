"""Tool definitions for the A02 loop.

Shape matches the messages API tools used by bench/agents.py: name, description,
input_schema. A live client must send tool_choice \"auto\". Forced tool_choice
returns HTTP 400 on the models this bench evaluates. This package does not call
that API.
"""

from __future__ import annotations

TOOLS: list[dict] = [
    {
        "name": "describe_schema",
        "description": (
            "List the views this session can read, with column names and types. "
            "Call once before sample_rows or propose_sql. Does not return data rows."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "sample_rows",
        "description": (
            "Return a few rows from one view so a later filter uses a stored literal. "
            "The SQL is built by the protocol from the view and column names; do not send SQL. "
            "At most three calls, and only before the first propose_sql. Not an answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "relation": {
                    "type": "string",
                    "description": "Schema-qualified view from describe_schema, for example sem.remit_claims.",
                },
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Column names to return. Omit to take the first columns of the view.",
                },
                "limit": {
                    "type": "integer",
                    "description": "How many rows to return, from 1 to 8. Omit for 5.",
                },
            },
            "required": ["relation"],
        },
    },
    {
        "name": "propose_sql",
        "description": (
            "Draft or repair one read-only SELECT over the views describe_schema listed. "
            "The protocol executes it and reports error, empty, suspicious, or ok. "
            "The first call is the draft. At most two later calls are repairs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "One SELECT statement."},
                "rationale": {
                    "type": "string",
                    "description": "Grain, population, and time basis in one or two sentences.",
                },
            },
            "required": ["sql"],
        },
    },
    {
        "name": "answer",
        "description": (
            "Submit the last proposed SQL and a one-paragraph answer. "
            "The SQL must be the last propose_sql text. It is re-executed for the score."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "text": {"type": "string", "description": "Grain, population, time basis, and the result in words."},
            },
            "required": ["sql", "text"],
        },
    },
    {
        "name": "refuse",
        "description": "Decline patient identifiers, another customer, or any write. Say why in one sentence.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
]

TOOL_NAMES = tuple(tool["name"] for tool in TOOLS)
