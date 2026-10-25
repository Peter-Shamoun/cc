"""The only tools A01 gives the model.

Neither tool returns rows. ``submit_sql`` names the statement that will be executed once.
``refuse`` ends the question with no execution. There is no schema tool, no lookup, and no
second call that could repair a failure.
"""

from __future__ import annotations

TOOL_SPEC: list[dict] = [
    {
        "name": "submit_sql",
        "description": (
            "Submit the one read-only DuckDB SELECT that answers the question. "
            "It is executed once against the sem.* views and that result is the answer. "
            "You will not see the rows, an error, or a chance to submit a different statement."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "Exactly one SELECT statement over the sem.* views in the data dictionary.",
                }
            },
            "required": ["sql"],
        },
    },
    {
        "name": "refuse",
        "description": (
            "Decline the question without querying. Use only when it asks for PHI "
            "(patient names, member ids, birth dates, addresses), another customer's data, or a change to the data."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "One sentence saying what was refused and why.",
                }
            },
            "required": ["reason"],
        },
    },
]

TOOL_NAMES = tuple(tool["name"] for tool in TOOL_SPEC)
