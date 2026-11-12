"""Keep gold current one silver batch at a time, at a cost that does not grow with silver.

A batch is the silver rows of newly ingested files. Almost every gold row is fixed once its file is in silver; the
exceptions are two flags that a later file can change:

    is_current_adjudication / adjudication_state   on a claim payment (and its service lines and adjustments): the
                                                    top-ranked event of its claim and payer, unless a reversal
    is_latest_submission                            on a claim submission (and its service lines)

So applying a batch is:

    1. compute the batch's gold rows from the batch's silver rows alone, with the semantic views        O(batch)
    2. look up, in a key index, the top-ranked event each claim and payer (each claim) had before      O(k log n)
    3. demote whichever loses: the batch's own row, or the older row, in the one small file it is in    O(batch)
    4. append the batch's rows as one new file per relation, and update the key index                   O(batch)

Layout:  <gold>/<relation>/batch=<batch id>.parquet     immutable except for step 3
         <gold>/_key_index.sqlite                       build state, not for queries
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from ccsim.pipeline import DDL_SEMANTIC, seed_ref
from ccsim.silver_staging import STAGING_DDL

from .gold_schema import GOLD_RELATIONS

REMIT_RELATIONS = ("remit_claims", "remit_lines", "adjustments")      # carry is_current_adjudication
SUBMISSION_RELATIONS = ("claims", "claim_lines")                      # carry is_latest_submission
KEY_INDEX_DDL = """
CREATE TABLE IF NOT EXISTS top_remit_event (
    claim_key TEXT NOT NULL, payer_key TEXT NOT NULL, remit_claim_id TEXT NOT NULL, rank_key TEXT NOT NULL,
    is_reversal INTEGER NOT NULL, batch_id TEXT NOT NULL, PRIMARY KEY (claim_key, payer_key)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS top_submission (
    claim_key TEXT NOT NULL PRIMARY KEY, claim_id TEXT NOT NULL, rank_key TEXT NOT NULL, batch_id TEXT NOT NULL
) WITHOUT ROWID;
"""
# The semantic views rank events with these keys; rendered as text that sorts the same way, to compare a batch's
# top event with the one the key index holds. NULL dates rank last, as NULLS LAST under DESC does.
REMIT_RANK_KEY_SQL = ("concat_ws('|', CASE WHEN is_duplicate_claim_denial THEN '0' ELSE '1' END, "
                      "coalesce(strftime(payment_date, '%Y%m%d'), '00000000'), "
                      "coalesce(strftime(file_received_at, '%Y%m%d%H%M%S'), '00000000000000'), file_id, "
                      "lpad(split_part(claim_payment_uid, ':', 2), 10, '0'))")
SUBMISSION_RANK_KEY_SQL = ("concat_ws('|', coalesce(strftime(submission_date, '%Y%m%d'), '00000000'), "
                           "coalesce(strftime(file_received_at, '%Y%m%d%H%M%S'), '00000000000000'), file_id, "
                           "lpad(seg_ord::VARCHAR, 10, '0'))")


class IncrementalGold:
    def __init__(self, gold_directory: Path):
        self.gold_directory = gold_directory
        for name in GOLD_RELATIONS:
            (gold_directory / name).mkdir(parents=True, exist_ok=True)
        self.key_index = sqlite3.connect(gold_directory / "_key_index.sqlite")
        self.key_index.executescript(KEY_INDEX_DDL)

    def _file(self, relation: str, batch_id: str) -> Path:
        return self.gold_directory / relation / f"batch={batch_id}.parquet"

    def apply_batch(self, batch_id: str, silver_table_files: dict[str, list[Path]]) -> dict:
        """silver_table_files: for each lossless table, the Parquet files holding this batch's rows (and only
        them). Returns timings and counts."""
        clock, seconds = time.monotonic(), {}

        def lap(step: str):
            nonlocal clock
            seconds[step] = round(time.monotonic() - clock, 3)
            clock = time.monotonic()

        # 1. the batch's gold rows, from the batch's silver rows alone
        work = duckdb.connect()
        work.execute(STAGING_DDL)
        for table, files in silver_table_files.items():
            if files:
                work.execute(f"INSERT INTO {table} BY NAME SELECT * FROM read_parquet({[str(f) for f in files]}, "
                             "hive_partitioning = false)")
        seed_ref(work)
        work.execute(DDL_SEMANTIC)
        rows = {name: work.sql(f"SELECT {', '.join(relation.columns)} FROM sem.{name} "
                               f"ORDER BY {relation.sort_columns}").to_arrow_table()
                for name, relation in GOLD_RELATIONS.items()}
        batch_top_remits = work.execute(
            f"SELECT claim_key, payer_key, claim_payment_uid, {REMIT_RANK_KEY_SQL}, is_reversal "
            "FROM sem_int.remit_event WHERE event_rank_desc = 1").fetchall()
        batch_top_submissions = work.execute(
            f"SELECT clm01_patient_control_number, claim_uid, {SUBMISSION_RANK_KEY_SQL} "
            "FROM sem_int.submission WHERE submission_rank_desc = 1").fetchall()
        work.close()
        lap("compute_batch_rows")

        # 2. what the key index held for these keys, and 3. which side loses
        demoted_in_batch: dict[str, set[str]] = {"remit": set(), "submission": set()}
        demoted_in_older_batches: dict[str, dict[str, set[str]]] = {"remit": {}, "submission": {}}
        index = self.key_index
        for claim_key, payer_key, remit_claim_id, rank_key, is_reversal in batch_top_remits:
            before = index.execute("SELECT remit_claim_id, rank_key, is_reversal, batch_id FROM top_remit_event "
                                   "WHERE claim_key = ? AND payer_key = ?", (claim_key, payer_key)).fetchone()
            if before is not None and before[1] > rank_key:
                demoted_in_batch["remit"].add(remit_claim_id)
                continue
            if before is not None and not before[2]:      # a reversal was never current: nothing to change in it
                demoted_in_older_batches["remit"].setdefault(before[3], set()).add(before[0])
            index.execute("INSERT OR REPLACE INTO top_remit_event VALUES (?, ?, ?, ?, ?, ?)",
                          (claim_key, payer_key, remit_claim_id, rank_key, int(is_reversal), batch_id))
        for claim_key, claim_id, rank_key in batch_top_submissions:
            before = index.execute("SELECT claim_id, rank_key, batch_id FROM top_submission WHERE claim_key = ?",
                                   (claim_key,)).fetchone()
            if before is not None and before[1] > rank_key:
                demoted_in_batch["submission"].add(claim_id)
                continue
            if before is not None:
                demoted_in_older_batches["submission"].setdefault(before[2], set()).add(before[0])
            index.execute("INSERT OR REPLACE INTO top_submission VALUES (?, ?, ?, ?)",
                          (claim_key, claim_id, rank_key, batch_id))
        lap("key_index")

        # 3a. the batch's own rows that an older event outranks
        for name in REMIT_RELATIONS:
            rows[name] = _demote_remit_rows(rows[name], demoted_in_batch["remit"])
        for name in SUBMISSION_RELATIONS:
            rows[name] = _demote_submission_rows(rows[name], demoted_in_batch["submission"])
        # 4. append
        for name, table in rows.items():
            pq.write_table(table, self._file(name, batch_id), compression="zstd")
        lap("write_batch_files")

        # 3b. older rows this batch outranks: rewrite only the files they are in
        rewritten_files = 0
        for older_batch_id, remit_claim_ids in demoted_in_older_batches["remit"].items():
            for name in REMIT_RELATIONS:
                rewritten_files += self._rewrite(name, older_batch_id, _demote_remit_rows, remit_claim_ids)
        for older_batch_id, claim_ids in demoted_in_older_batches["submission"].items():
            for name in SUBMISSION_RELATIONS:
                rewritten_files += self._rewrite(name, older_batch_id, _demote_submission_rows, claim_ids)
        index.commit()
        lap("rewrite_older_files")
        return {"batch_id": batch_id, "seconds": seconds, "seconds_total": round(sum(seconds.values()), 3),
                "gold_rows_added": sum(table.num_rows for table in rows.values()),
                "older_rows_demoted": sum(len(ids) for side in demoted_in_older_batches.values() for ids in side.values()),
                "batch_rows_demoted": sum(len(ids) for ids in demoted_in_batch.values()),
                "older_files_rewritten": rewritten_files}

    def _rewrite(self, relation: str, batch_id: str, demote, ids: set[str]) -> int:
        path = self._file(relation, batch_id)
        table = pq.read_table(path)
        changed = demote(table, ids)
        if changed is table:
            return 0
        temporary = path.with_suffix(".rewriting")
        pq.write_table(changed, temporary, compression="zstd")
        temporary.replace(path)
        return 1


def _with_column(table: pa.Table, name: str, values: pa.Array) -> pa.Table:
    return table.set_column(table.schema.get_field_index(name), name, values)


def _demote_remit_rows(table: pa.Table, remit_claim_ids: set[str]) -> pa.Table:
    """The rows of these claim payments are no longer the current adjudication."""
    if not remit_claim_ids or not table.num_rows:
        return table
    demoted = pa.compute.is_in(table["remit_claim_id"], value_set=pa.array(sorted(remit_claim_ids)))
    if not pa.compute.any(demoted).as_py():
        return table
    table = _with_column(table, "is_current_adjudication", pa.compute.if_else(
        demoted, pa.scalar(False), table["is_current_adjudication"]))
    if "adjudication_state" in table.schema.names:        # 'current' -> 'superseded'; a reversal stays a reversal
        table = _with_column(table, "adjudication_state", pa.compute.if_else(
            pa.compute.and_(demoted, pa.compute.equal(table["adjudication_state"], "current")),
            pa.scalar("superseded"), table["adjudication_state"]))
    return table


def _demote_submission_rows(table: pa.Table, claim_ids: set[str]) -> pa.Table:
    """The rows of these submissions are no longer the latest submission."""
    if not claim_ids or not table.num_rows:
        return table
    demoted = pa.compute.is_in(table["claim_id"], value_set=pa.array(sorted(claim_ids)))
    if not pa.compute.any(demoted).as_py():
        return table
    return _with_column(table, "is_latest_submission", pa.compute.if_else(
        demoted, pa.scalar(False), table["is_latest_submission"]))


def open_incremental_gold(gold_directory: Path) -> duckdb.DuckDBPyConnection:
    """A DuckDB session with every gold relation as gold.<name>, over the batch files."""
    con = duckdb.connect()
    con.execute("CREATE SCHEMA gold")
    for name in GOLD_RELATIONS:
        con.execute(f"CREATE VIEW gold.{name} AS SELECT * FROM read_parquet('{gold_directory / name}/batch=*.parquet', "
                    "hive_partitioning = false)")
    return con
