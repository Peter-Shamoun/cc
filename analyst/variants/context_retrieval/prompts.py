"""Prompts and the tool spec a later Claude Code screen can load.

The stable prefix is the role, the basic glossary, and (when schema_mode is full)
every sem view. Value hits and few-shot SQL go in the user turn so they do not
invalidate a cached prefix. This module never loads the pitfalls glossary.
"""
from __future__ import annotations

from bench.agents import GLOSSARY

TOOL_SPEC = [
    {
        "name": "link_schema",
        "description": (
            "Rank sem.* views for this question. mode=full returns every view. "
            "mode=linked_recall may drop views with no lexical evidence and falls back "
            "to the full schema when too few views match. It does not drop columns."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "mode": {"type": "string", "enum": ["full", "linked_recall", "linked_columns"]},
            },
            "required": ["question"],
        },
    },
    {
        "name": "lookup_values",
        "description": (
            "Resolve a payer name, CPT/HCPCS code, CARC, RARC, or diagnosis code against the "
            "logged-in tenant's local index. Returns candidate values and aggregate row counts. "
            "Does not return patient, member, or claim identifiers. If bound is false, do not pick a value."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["payer_name", "procedure_code", "carc", "rarc", "diagnosis_code"]},
                "text": {"type": "string"},
            },
            "required": ["kind", "text"],
        },
    },
    {
        "name": "similar_examples",
        "description": (
            "Return up to k solved questions and their SQL from the train split only. "
            "The tool errors if the library contains a validation or test question. "
            "Examples show query shape. Do not copy a filter the user did not ask for."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "k": {"type": "integer"},
                "selector": {"type": "string", "enum": ["skeleton", "lexical"]},
            },
            "required": ["question"],
        },
    },
    {
        "name": "sql",
        "description": "Run one read-only DuckDB SELECT over sem.* through the guarded session. Returns up to 20 rows.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "answer",
        "description": "Submit the SELECT that computes the answer and a one-paragraph explanation. The harness re-executes the SQL.",
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}, "text": {"type": "string"}},
            "required": ["sql", "text"],
        },
    },
    {
        "name": "refuse",
        "description": "Decline requests for PHI, another customer's data, or any write.",
        "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
    },
]

_REFUSAL = (
    "Refuse, and do not query, when the user asks for patient names, member ids, dates of birth, "
    "or addresses; when they ask about a different customer than the logged-in tenant; or when they "
    "ask to insert, update, or delete data. The session can only read the logged-in tenant's sem views."
)

_PROCEDURE = (
    "Work in these steps: 1) read the views and the resolved values; 2) restate the grain, the "
    "population, the time basis, and the output columns; 3) write one SELECT; 4) submit it with answer. "
    "If a value is ambiguous, do not guess a filter.\n\n"
)


def render_schema_block(link) -> str:
    lines = []
    for view in link.views:
        columns = ", ".join(f"{column.name} {column.data_type}" for column in view.columns if column.kept)
        grain = f" — {view.grain}" if view.grain else ""
        lines.append(f"{view.name}{grain}\n  ({columns})")
    if link.dropped_views:
        lines.append("Views left out of this prompt: " + ", ".join(link.dropped_views))
    if link.fallback_full:
        lines.append("Schema linker fell back to the full schema because too few views matched.")
    return "\n".join(lines)


def render_value_block(resolved: dict | None, enabled: bool) -> str:
    if not enabled:
        return "Value index: off. Filter codes and payer names only when the question already states the stored literal."
    if resolved is None:
        return "Value index: on, but no index was built."
    lines = ["Value index (this tenant only). Use a bound value as an equality filter. Do not pick an ambiguous value."]
    if not resolved["bound"] and not resolved["ambiguous"] and not resolved["unmatched"]:
        lines.append("- no payer names or codes in the question matched the index")
        return "\n".join(lines)
    for item in resolved["bound"]:
        match = item["matches"][0]
        description = f" ({match['description']})" if match.get("description") else ""
        lines.append(
            f"- bound {item['kind']} {item['query']!r} -> {match['value']!r}{description}, "
            f"{match['row_count']} rows, views {', '.join(match['source_views'])}"
        )
    for item in resolved["ambiguous"]:
        choices = ", ".join(f"{match['value']!r} ({match['row_count']} rows)" for match in item["matches"][:5])
        lines.append(f"- ambiguous {item['kind']} {item['query']!r}: {choices}. Do not filter to one of these.")
    for item in resolved["unmatched"]:
        lines.append(f"- unmatched {item.get('kind') or item['why']} {item['query']!r}: not in this tenant's index")
    return "\n".join(lines)


def render_examples_block(examples: list[dict]) -> str:
    if not examples:
        return "Solved examples: none retrieved."
    lines = [
        "Solved examples from the train split, chosen for query shape. "
        "Do not copy a filter, a code, or a payer the user did not ask for."
    ]
    for number, example in enumerate(examples, start=1):
        lines.append(f"Example {number}. Question: {example['question']}\nSQL:\n{example['sql']}")
    return "\n\n".join(lines)


def render_system(link, *, glossary: str, context_style: str, schema_in_system: bool) -> str:
    if glossary not in {"none", "basic"}:
        raise ValueError("this variant only accepts glossary 'none' or 'basic'; it does not load pitfalls text")
    if context_style not in {"goal", "procedural"}:
        raise ValueError(f"unknown context_style {context_style!r}")
    parts = [
        "You are CC, an analytics assistant for one clinical lab's insurance claims (837P) and remittances (835).",
        "Answer with one read-only DuckDB SELECT over the sem views below. The submitted query is re-executed.",
        _REFUSAL,
    ]
    if context_style == "procedural":
        parts.append(_PROCEDURE.strip())
    if glossary == "basic":
        parts.append(GLOSSARY.strip())
    if schema_in_system:
        parts.append("Views:\n" + render_schema_block(link))
    return "\n\n".join(parts)


def render_user(question: str, link, resolved: dict | None, examples: list[dict], *, value_index: bool,
                schema_in_system: bool) -> str:
    parts = [f"User question: {question}"]
    if not schema_in_system:
        parts.append("Views for this question:\n" + render_schema_block(link))
    parts.append(render_value_block(resolved, value_index))
    parts.append(render_examples_block(examples))
    return "\n\n".join(parts)


def render_subagent_briefing(*, system: str, user: str) -> str:
    """One prompt a Claude Code subagent can be given when the harness prefetches retrieval."""
    tool_lines = [f"- {tool['name']}: {tool['description']}" for tool in TOOL_SPEC]
    return (
        system
        + "\n\nTools:\n"
        + "\n".join(tool_lines)
        + "\n\nThe link_schema, lookup_values, and similar_examples results for this question are already "
        "below. Use them. Then submit one sql answer, or refuse.\n\n"
        + user
    )
