"""The incremental gold computation, shared by the table-format prototypes. It is pipeline._load_closure +
_gold_incremental with the storage taken out: the same unchanged view SQL, the same CLOSURE filters and the same
payers merge, but rows are handed to the table format as "replace these claims" / "append these rows" instead of
being written into cohort partitions. There are no cohort partitions, so no retiering and no full rebuild for a
late batch: a claim is replaced by key wherever its rows live.

A source object gives SQL for silver:
    src.pending(table)          the rows of the runs gold has not seen yet (None if the table has none)
    src.older(table, months)    every silver row of the table, optionally only these received months
    src.old_gold(relation)      the current gold relation (for the payers merge; None on the first build)
"""
from __future__ import annotations

import re

from ccsim.pipeline import (
    APPEND_RELATIONS,
    CLAIM_RELATIONS,
    CLOSURE,
    GOLD_SORT,
    SMALL_RELATIONS,
    VIEW_INPUTS,
    _payers_merge_sql,
    _view_columns,
    _working_catalog,
)

APPEND_GOLD = ["sem.payments", "sem.provider_adjustments"] + list(APPEND_RELATIONS)   # rows of the pending files only
assert set(SMALL_RELATIONS) == {"sem.payments", "sem.provider_adjustments", "sem.payers"}


def temp_name(relation: str) -> str:
    return "new__" + relation.replace(".", "__")


def view_columns(con, table: str) -> str:
    """pipeline._view_columns restricted to the in-memory working catalog (an attached lake has the same names)."""
    cols = _view_columns(con, table)
    if cols == "*":
        return "* EXCLUDE (received_month)"
    seen = list(dict.fromkeys(c.strip() for c in cols.split(",")))
    return ", ".join(seen)


def compute(con, src) -> dict:
    """Fill temp tables new__<relation> in `con`; returns build stats. Claim relations: every row of every touched
    claim (the caller deletes those claims' rows and inserts these). Append relations: the pending files' rows.
    sem.payers: the whole merged relation."""
    _working_catalog(con)
    for t in VIEW_INPUTS:
        sql = src.pending(t)
        if sql:
            con.execute(f"INSERT INTO memory.{t} BY NAME SELECT * EXCLUDE (received_month) FROM {sql}")
    con.execute("CREATE TEMP TABLE touched_claim AS SELECT clm01_patient_control_number AS claim_key FROM memory.c837.claim "
                "UNION SELECT clp01_patient_control_number FROM memory.r835.claim_payment")
    con.execute("CREATE TEMP TABLE pending_file AS SELECT file_id FROM memory.edi.source_file")
    touched_files, months = set(), set()
    for t, col in (("c837.claim", "clm01_patient_control_number"), ("r835.claim_payment", "clp01_patient_control_number")):
        older = src.older(t, None)
        if older:
            for fid, month in con.execute(
                    f"SELECT DISTINCT file_id, received_month FROM {older} WHERE {col} IN (SELECT claim_key FROM touched_claim) "
                    "AND file_id NOT IN (SELECT file_id FROM pending_file)").fetchall():
                touched_files.add(fid)
                months.add(month)
    if touched_files:
        assert all(re.fullmatch(r"[0-9a-f]{16}", f) for f in touched_files)
        in_files = ", ".join(f"'{f}'" for f in sorted(touched_files))
        for t, closure_filter in CLOSURE:
            older = src.older(t, sorted(months))
            if older:
                con.execute(f"INSERT INTO memory.{t} BY NAME SELECT {view_columns(con, t)} FROM {older} "
                            f"WHERE file_id IN ({in_files}) AND ({closure_filter})")
    for rel in CLAIM_RELATIONS:
        con.execute(f"CREATE TEMP TABLE {temp_name(rel)} AS SELECT * FROM memory.{rel} ORDER BY {GOLD_SORT[rel]}")
    for t in VIEW_INPUTS:                    # from here on only the pending files' rows
        con.execute(f"DELETE FROM memory.{t} WHERE file_id NOT IN (SELECT file_id FROM pending_file)")
    for rel in APPEND_GOLD:
        sql = f"({APPEND_RELATIONS[rel]})" if rel in APPEND_RELATIONS else f"memory.{rel}"
        con.execute(f"CREATE TEMP TABLE {temp_name(rel)} AS SELECT * FROM {sql}")
    old = src.old_gold("sem.payers")
    con.execute(f"CREATE TEMP TABLE {temp_name('sem.payers')} AS " +
                (_payers_merge_sql(old).replace("sem.payers n", "memory.sem.payers n") if old else "SELECT * FROM memory.sem.payers"))
    return dict(touched_claims=con.execute("SELECT count(*) FROM touched_claim").fetchone()[0],
                touched_files=len(touched_files), touched_months=sorted(months))
