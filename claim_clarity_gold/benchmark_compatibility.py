"""The benchmark's queries are written against the semantic relations, whose event rows carry "is the current
adjudication" and "is the latest submission". Gold v2 keeps those in its state tables. To ask gold the benchmark's
questions unchanged, these views put the flags back by joining each event relation to the state table that knows.

The benchmark is a check on gold (does it cover the question, is the answer the same, how fast), not its source.
"""
from __future__ import annotations

import duckdb

from .gold_relations import EVENT_RELATIONS

_CURRENT = "(SELECT current_remit_claim_id FROM gold.claim_payers WHERE current_remit_claim_id IS NOT NULL)"
_LATEST = "(SELECT latest_claim_id FROM gold.claims WHERE latest_claim_id IS NOT NULL)"
COMPATIBILITY_VIEWS = {
    "claims": f"SELECT *, claim_id IN {_LATEST} AS is_latest_submission FROM gold.claim_submissions",
    "claim_lines": f"SELECT *, claim_id IN {_LATEST} AS is_latest_submission FROM gold.claim_lines",
    "remit_claims": f"""SELECT *, remit_claim_id IN {_CURRENT} AS is_current_adjudication,
        CASE WHEN is_reversal THEN 'reversal' WHEN remit_claim_id IN {_CURRENT} THEN 'current'
             ELSE 'superseded' END AS adjudication_state FROM gold.remit_claims""",
    "remit_lines": f"SELECT *, remit_claim_id IN {_CURRENT} AS is_current_adjudication FROM gold.remit_lines",
    "adjustments": f"SELECT *, remit_claim_id IN {_CURRENT} AS is_current_adjudication FROM gold.adjustments",
    "claim_acknowledgments": "SELECT * FROM gold.claim_acknowledgments",
    "payments": "SELECT * FROM gold.payments",
    "provider_adjustments": "SELECT * FROM gold.provider_adjustments",
}
assert set(COMPATIBILITY_VIEWS) == {relation.semantic_relation for relation in EVENT_RELATIONS.values()}


def create_compatibility_views(gold: duckdb.DuckDBPyConnection) -> dict[str, list[str]]:
    """Creates schema benchmark with a view per semantic relation gold covers. Returns each view's columns."""
    gold.execute("CREATE SCHEMA IF NOT EXISTS benchmark")
    columns = {}
    for name, select_sql in COMPATIBILITY_VIEWS.items():
        gold.execute(f"CREATE OR REPLACE VIEW benchmark.{name} AS {select_sql}")
        columns[name] = [row[0] for row in gold.execute(f"DESCRIBE benchmark.{name}").fetchall()]
    return columns
