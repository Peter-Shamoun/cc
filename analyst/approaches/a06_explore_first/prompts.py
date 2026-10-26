"""Prompts for the two model calls: choose columns, then write the final SQL.

The probe SQL itself is not a model call. Templates in probes.py build it.
"""
from __future__ import annotations

from .catalog import render_catalog
from .probes import (
    DISTINCT_LIST_LIMIT,
    MAX_COLUMNS_PER_VIEW,
    MAX_JOINS,
    MAX_LITERALS,
    MAX_VIEWS,
)

PROTOCOL_HEADER = "Explore-first protocol (A06):"


def render_plan_prompt(question: str) -> str:
    return f"""You plan column exploration for one lab-billing question. Do not write the answer SQL.

The schema below is the whole semantic layer. Pick the columns whose stored values, nulls, dates, or grain the question depends on. Exploration runs only on what you pick. The later SQL step still sees every view.

{render_catalog()}

Question:
{question}

Return one JSON object and nothing else:
{{"views": ["sem.claims"], "columns": {{"sem.claims": ["claim_key"]}}, "literals": [{{"view": "sem.claims", "column": "payer_name", "text": "exact text from the question"}}], "refuse": null}}

Rules:
- At most {MAX_VIEWS} views and {MAX_COLUMNS_PER_VIEW} columns on each view.
- Prefer flags, enums, dates, measures, and codes the question filters or aggregates. Name a key when the answer joins on it.
- Do not pick identifier columns in order to read their values. Keys and identifiers are counted, not listed.
- literals: at most {MAX_LITERALS} payer names, procedure codes, or other stored strings the question uses as a filter. Copy the text. Leave the list empty when the question names no stored string.
- If the question asks for patient names, member ids, birth dates, addresses, another customer, or any change to the data, set refuse to a short reason and leave views empty.
"""


def render_write_prompt(question: str, notes: str) -> str:
    return f"""You write one read-only DuckDB SELECT over sem.* that answers the question. The SELECT is re-executed and must compute the answer. Do not assume a number you did not query.

{render_catalog()}

{notes}

How to read the notes:
- Probes are unfiltered. A flag or enum distribution is the real split; filtering keeps only the values you name.
- A date range is the data that exists.
- Join lines are key cardinalities. When a join multiplies left rows, aggregate the finer grain before you sum money or count claims.
- "values not listed" means the column was a key, an identifier, or too wide to list. Copy a spelling from a literal line or a distribution when you filter. Do not invent one.
- At most {MAX_JOINS} joins were measured, and only between views the planner selected.

Question:
{question}

Return one JSON object and nothing else:
{{"sql": "SELECT ...", "text": "one paragraph for the user"}}
"""


def harness_protocol() -> str:
    """Instructions inserted into a bench/eval_subagents.py prompt.

    The subagent loop has no probe stage, so this text asks the model to run the
    same four shapes through the sql command and to compress the results itself.
    The in-process protocol (protocol.run) compresses in code and does not use this text.
    """
    return f"""{PROTOCOL_HEADER}
Before the final SQL, explore with the sql command. Do not SELECT *. Stop after the probes below; do not profile every view.

1. Summary, one per relevant view (null rates, distinct counts, date and number ranges, and histograms of flags and enums):
SELECT count(*) AS n_rows,
       count(*) FILTER (WHERE "col" IS NULL) AS "col__nulls",
       count(DISTINCT "col") AS "col__distinct",
       min("date_col") AS "date_col__min", max("date_col") AS "date_col__max",
       histogram(CAST("flag_col" AS VARCHAR)) AS "flag_col__hist"
FROM sem.view

2. Distinct values, only for a code or name whose distinct count is at most {DISTINCT_LIST_LIMIT}. Never list keys, NPIs, check numbers, patient control numbers, or file ids:
SELECT histogram(CAST("col" AS VARCHAR)) AS "col__hist" FROM sem.view

3. Join cardinality. Aggregate each side by key first. Read joined_rows against left_rows: a larger joined_rows means the join multiplies.
WITH left_keys AS (
  SELECT "key" AS join_key, count(*) AS n FROM sem.left_view GROUP BY 1
),
right_keys AS (
  SELECT "key" AS join_key, count(*) AS n FROM sem.right_view GROUP BY 1
)
SELECT count(l.join_key) AS left_distinct_keys, count(r.join_key) AS right_distinct_keys,
       count(*) FILTER (WHERE l.join_key IS NOT NULL AND r.join_key IS NOT NULL) AS matched_keys,
       coalesce(sum(l.n), 0) AS left_rows, coalesce(sum(r.n), 0) AS right_rows,
       coalesce(sum(CASE WHEN l.join_key IS NOT NULL AND r.join_key IS NOT NULL THEN l.n * r.n END), 0) AS joined_rows,
       coalesce(max(r.n), 0) AS max_right_rows_per_key
FROM left_keys l FULL OUTER JOIN right_keys r ON l.join_key = r.join_key

4. When the question names a payer, a code, or another stored string, list the stored values that contain it:
SELECT "col" AS value, count(*) AS n FROM sem.view
WHERE lower(CAST("col" AS VARCHAR)) LIKE '%text%' ESCAPE '\\'
GROUP BY 1 ORDER BY n DESC, value LIMIT {DISTINCT_LIST_LIMIT}

Write a few lines of notes from those results: distinct values, enum distributions, null rates, date ranges, join cardinalities. Then submit one SELECT that uses the spellings and the grain those notes show. Probe at most {MAX_VIEWS} views, {MAX_JOINS} joins, and {MAX_LITERALS} literal checks.
"""
