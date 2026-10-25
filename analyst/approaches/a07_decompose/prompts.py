"""Prompts for A07. The same rules are in the planner text and the subagent briefing.

A live model is not called from this package. ``plan_messages`` is the request a
future backend would send. The eval adapter puts ``subagent_briefing`` into the
manifest that ``bench.eval_subagents`` already scores.
"""

from __future__ import annotations

# Canonical order. A plan uses a subsequence. Population is required.
STEP_ROLES = ("population", "join", "metric", "aggregation", "ranking")

CHECK_RULES = """\
Checks on each CTE, before the next step is written. Run them as one aggregate
SELECT over the CTE chain so far (the sandbox caps a raw SELECT at 1000 rows, so
do not infer a row count from a preview):

- row_count: count(*). The population step must be non-empty. A ranking step must
  have row_count <= the limit the question asked for.
- uniqueness: count of DISTINCT grain keys equals row_count. A NULL grain key is a
  failure. Population always declares a grain (claim_key, claim_payer_key,
  remit_claim_id, or the grain the question names).
- sums: if the step says a measure is preserved (a dollar amount that must not be
  repeated by a join), its sum equals the previous CTE's sum within 0.02. A join
  that fans out must aggregate or DISTINCT before it carries that measure, and
  must not list it as preserved.
- ranking order: the measure is monotonic in the requested direction. Ties are
  allowed. An unordered or reversed ranking is a failure.

A failed check is repaired once, on that step only, and the check is run again.
Do not drop a filter just to make a check pass. A check that passes is not proof
the population is the one the question asked for.
"""

PLANNER_INSTRUCTIONS = f"""\
You break one analytics question into a short chain of SQL CTEs over the sem.*
semantic layer, then one final SELECT. You do not answer from memory. You do not
write a second WITH. Each step is a single SELECT. Earlier steps are referenced
by CTE name.

Roles, in this order. Omit a role the question does not need. Do not reorder and
do not repeat a role. The first step is always population.

- population: the rows that belong in the answer, already filtered (current
  adjudication, primary payer, latest submission, the date basis the question
  names). One row per grain key.
- join: attach the next view (lines, adjustments, claims). Declare the new grain.
  If the join multiplies rows, DISTINCT or aggregate before you carry a sum.
- metric: row-level measures only (a denial flag, a dollar amount). Do not group.
- aggregation: GROUP BY the grain the question asks for.
- ranking: ORDER BY the measure and the stated tie-break, LIMIT to the N asked for.

{CHECK_RULES}
Grain, money, and dates follow the semantic layer. sem.claims and sem.claim_lines
have one row per 837 submission. sem.remit_claims has one row per 835 event;
is_current_adjudication is the current one. is_latest_submission can be the
secondary payer's row; a primary-payer question also filters payer_responsibility
= 'P'. Claim-level amounts summed after a line or adjustment join are double
counts. Denied claims can lack remit lines. Payment month, service month, and
submission date are different. Only sem.* is readable, unless the session is an
escalation, in which case lossless.* is readable too. No files, no other schemas,
no writes.

Reply with JSON only, no markdown, of this shape:
{{
  "refuse": false,
  "reason": "",
  "steps": [
    {{
      "role": "population",
      "cte_name": "population",
      "purpose": "one line",
      "sql": "SELECT ...",
      "grain_keys": ["claim_key"],
      "preserve_sums": [],
      "expect_nonempty": true,
      "max_rows": null,
      "order_by": null,
      "order_direction": "desc",
      "max_fanout": null
    }}
  ],
  "final_select": "SELECT ... FROM <last cte name>"
}}
Set refuse to true and steps to [] when the question asks for PHI, another
customer, or a write. final_select is one SELECT that reads the last CTE. It
must not contain WITH. cte_name and every grain key are plain identifiers.
Ranking sets order_by, order_direction, and max_rows. preserve_sums lists
measures whose total must survive this step unchanged.
"""


def plan_messages(question: str, schema_text: str, *, escalate: bool = False) -> list[dict]:
    """The planner request. Schema text is whatever `context` would have returned."""
    scope = "This session may read lossless.* as well as sem.*." if escalate else "This session may read sem.* only."
    return [
        {"role": "system", "content": PLANNER_INSTRUCTIONS},
        {"role": "user", "content": f"{scope}\n\nSchema and definitions:\n{schema_text}\n\nQuestion: {question}\n"},
    ]


def repair_messages(question: str, step_role: str, step_sql: str, failure_text: str, schema_text: str) -> list[dict]:
    """One repair of the step that just failed its check. Same JSON object as a single step."""
    return [
        {"role": "system", "content": PLANNER_INSTRUCTIONS},
        {"role": "user", "content": (
            f"Question: {question}\n\nSchema and definitions:\n{schema_text}\n\n"
            f"The {step_role} step failed its check. Rewrite only that step. Keep the role and the CTE name. "
            f"Do not drop a filter that the question requires in order to silence the check.\n\n"
            f"Current SQL:\n{step_sql}\n\nCheck failures:\n{failure_text}\n\n"
            "Reply with JSON only: one step object, same fields as a step inside the plan."
        )},
    ]


def subagent_procedure() -> str:
    """Working procedure pasted into the eval briefing. The tool surface stays agent_cli."""
    return f"""\
Work by decomposition. Do not run the briefing subcommand; the instructions in this
message are the whole procedure. Do not read or write files, and do not run any
program other than the commands listed above.

1. Run context once. Use the view grain and the column names it prints. Do not invent columns.
2. Break the question into the roles population, then join, then metric, then aggregation,
   then ranking. Skip a role the question does not need. Population is required and comes first.
3. For each step, in order, write that step as one SELECT with no WITH of its own. Execute
   the chain so far as one sql command whose body is the check below. Read the one result row.
   Only if the check fails, run a preview sql of the same chain (the tool shows 20 rows) and
   rewrite that step once. Check again. Then go to the next role.
4. Submit exactly one answer. The submitted SQL is a single WITH of every step, then a final
   SELECT that reads the last CTE. The scorer re-executes that SQL. It must read sem.* (and
   lossless.* only if this session says the question is an escalation).

{CHECK_RULES}
Check query shape, for a step named joined that preserves paid_amount and is unique on
remit_claim_id and carc_code:

WITH population AS (
  SELECT ...
),
joined AS (
  SELECT ...
)
SELECT count(*) AS row_count,
       (SELECT count(*) FROM (SELECT DISTINCT remit_claim_id, carc_code FROM joined)) AS distinct_grain,
       count(*) FILTER (WHERE remit_claim_id IS NULL OR carc_code IS NULL) AS null_grain_rows,
       sum(paid_amount) AS sum_paid_amount
FROM joined

Compare sum_paid_amount to the same sum on the previous CTE. A ranking check is the same
aggregate plus the ordered measure: row_count at or under the requested N, and the measure
monotonic, ties allowed.

Finish with the answer command and then the single word DONE.
"""
