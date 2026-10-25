"""Token and call estimates for one A01 question.

The token count is a character estimate (4 characters per token), not a model tokenizer.
Output size is an assumption for one ``submit_sql`` call. Nothing here contacts a model.
"""

from __future__ import annotations

ASSUMED_OUTPUT_TOKENS = 400
ASSUMED_OUTPUT_TOKENS_BASIS = (
    "one submit_sql tool call at effort low, a few hundred tokens of SQL; "
    "thinking tokens at effort low were not measured because this estimate makes no model call"
)


def approximate_tokens(text: str) -> int:
    if not text:
        return 0
    return (len(text) + 3) // 4


def per_question_estimate(system: str, user: str, assumed_output_tokens: int = ASSUMED_OUTPUT_TOKENS) -> dict:
    prefix_tokens = approximate_tokens(system)
    question_tokens = approximate_tokens(user)
    return {
        "model_calls": 1,
        "sql_executions_when_the_model_submits": 1,
        "sql_executions_when_the_model_refuses_or_breaks_the_protocol": 0,
        "repair_rounds": 0,
        "exploratory_queries": 0,
        "prefix_tokens": prefix_tokens,
        "question_tokens": question_tokens,
        "input_tokens_cold": prefix_tokens + question_tokens,
        "cache_read_tokens_on_a_warm_prefix": prefix_tokens,
        "uncached_input_tokens_on_a_warm_prefix": question_tokens,
        "assumed_output_tokens": assumed_output_tokens,
        "assumed_output_tokens_basis": ASSUMED_OUTPUT_TOKENS_BASIS,
        "latency_shape": (
            "one model round trip at effort low, then one guarded DuckDB query on the session "
            "already opened through the ccsim.query path; no further model round trip"
        ),
    }
