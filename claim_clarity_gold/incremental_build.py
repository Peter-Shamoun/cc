"""Keep gold current one silver batch at a time, at a cost that does not grow with silver.

A batch is the silver rows of newly ingested files. Almost every gold row is fixed once its file is in silver; the
exceptions are two flags that a later file can change:

    is_current_adjudication / adjudication_state   on a claim payment (and its service lines and adjustments): the
                                                    top-ranked event of its claim and payer, unless a reversal
    is_latest_submission                            on a claim submission (and its service lines)

So applying a batch is:

    1. compute the batch's gold rows from the batch's silver rows alone, with the semantic views        O(batch)
    2. look up, in a key index, the top-ranked event each claim and payer (each claim) had before      O(k log n)
    3. demote whichever loses: the batch's own row before it is written, or the older row with an
       UPDATE confined to the month that row is in                                                      O(month)
    4. insert the batch's rows, and update the key index                                                O(batch)

Gold is stored in a merge-on-read table format: an UPDATE writes the changed rows and a delete marker, it does not
rewrite the data file. Here that is DuckLake (Parquet files plus a catalog, read and written by DuckDB); Iceberg
with merge-on-read deletes has the same economics. Tables are partitioned by month, so an UPDATE and most queries
open only the months they name. compact() merges the small files daily inserts leave behind.

    <gold>/catalog.ducklake, <gold>/data/     the gold tables: gold.remit_claims, gold.claims, ...
    <gold>/_key_index.sqlite                  build state, not for queries: the top-ranked event per key
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import duckdb
import pyarrow as pa

from ccsim.pipeline import DDL_SEMANTIC, seed_ref
from ccsim.silver_staging import STAGING_DDL

from .gold_schema import GOLD_RELATIONS

REMIT_RELATIONS = ("remit_claims", "remit_lines", "adjustments")      # carry is_current_adjudication
SUBMISSION_RELATIONS = ("claims", "claim_lines")                      # carry is_latest_submission
KEY_INDEX_DDL = """
CREATE TABLE IF NOT EXISTS top_remit_event (
    claim_key TEXT NOT NULL, payer_key TEXT NOT NULL, remit_claim_id TEXT NOT NULL, rank_key TEXT NOT NULL,
    is_reversal INTEGER NOT NULL, payment_date TEXT, PRIMARY KEY (claim_key, payer_key)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS top_submission (
    claim_key TEXT NOT NULL PRIMARY KEY, claim_id TEXT NOT NULL, rank_key TEXT NOT NULL, submission_date TEXT
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
# Where an older row is, for the UPDATE that demotes it: the predicate names the row's partition.
DEMOTE_REMIT_SQL = {
    "remit_claims": "UPDATE gold.remit_claims SET is_current_adjudication = false, adjudication_state = CASE WHEN "
                    "adjudication_state = 'current' THEN 'superseded' ELSE adjudication_state END "
                    "WHERE payment_month IS NOT DISTINCT FROM date_trunc('month', ?::DATE)::DATE "
                    "AND remit_claim_id IN (SELECT id FROM demoted)",
    "remit_lines": "UPDATE gold.remit_lines SET is_current_adjudication = false "
                   "WHERE payment_month IS NOT DISTINCT FROM date_trunc('month', ?::DATE)::DATE "
                   "AND remit_claim_id IN (SELECT id FROM demoted)",
    "adjustments": "UPDATE gold.adjustments SET is_current_adjudication = false "
                   "WHERE payment_month IS NOT DISTINCT FROM date_trunc('month', ?::DATE)::DATE "
                   "AND remit_claim_id IN (SELECT id FROM demoted)",
}
DEMOTE_SUBMISSION_SQL = {
    "claims": "UPDATE gold.claims SET is_latest_submission = false "
              "WHERE submission_month IS NOT DISTINCT FROM date_trunc('month', ?::DATE)::DATE "
              "AND claim_id IN (SELECT id FROM demoted)",
    "claim_lines": "UPDATE gold.claim_lines SET is_latest_submission = false "
                   "WHERE date_trunc('month', submission_date)::DATE IS NOT DISTINCT FROM date_trunc('month', ?::DATE)::DATE "
                   "AND claim_id IN (SELECT id FROM demoted)",
}


def attach_gold(con: duckdb.DuckDBPyConnection, gold_directory: Path, read_only: bool = False):
    con.execute("INSTALL ducklake; LOAD ducklake")
    con.execute(f"ATTACH 'ducklake:{gold_directory / 'catalog.ducklake'}' AS gold "
                f"(DATA_PATH '{gold_directory / 'data'}'{', READ_ONLY' if read_only else ''})")


class IncrementalGold:
    def __init__(self, gold_directory: Path):
        self.gold_directory = gold_directory
        (gold_directory / "data").mkdir(parents=True, exist_ok=True)
        self.key_index = sqlite3.connect(gold_directory / "_key_index.sqlite")
        self.key_index.executescript(KEY_INDEX_DDL)
        # One working session for every batch: the lossless tables (emptied per batch), ref.*, the semantic views,
        # and the gold tables.
        self.work = duckdb.connect()
        self.work.execute(STAGING_DDL)
        seed_ref(self.work)
        self.work.execute(DDL_SEMANTIC)
        attach_gold(self.work, gold_directory)
        self.work.execute("CALL gold.set_option('parquet_compression', 'zstd')")     # the default is half as dense
        self.tables_exist = False

    def _create_tables(self, rows: dict[str, pa.Table]):
        for name, relation in GOLD_RELATIONS.items():
            self.work.register("first_rows", rows[name])
            self.work.execute(f"CREATE TABLE IF NOT EXISTS gold.{name} AS SELECT * FROM first_rows LIMIT 0")
            self.work.unregister("first_rows")
            if relation.partition_by:
                self.work.execute(f"ALTER TABLE gold.{name} SET PARTITIONED BY ({relation.partition_by})")
        self.tables_exist = True

    def apply_batch(self, batch_id: str, silver_table_files: dict[str, list[Path]]) -> dict:
        """silver_table_files: for each lossless table, the Parquet files holding this batch's rows (and only
        them). Returns timings and counts."""
        clock, seconds = time.monotonic(), {}

        def lap(step: str):
            nonlocal clock
            seconds[step] = round(time.monotonic() - clock, 3)
            clock = time.monotonic()

        # 1. the batch's gold rows, from the batch's silver rows alone
        work = self.work
        for table, files in silver_table_files.items():
            work.execute(f"TRUNCATE {table}")
            if files:
                work.execute(f"INSERT INTO {table} BY NAME SELECT * FROM read_parquet({[str(f) for f in files]}, "
                             "hive_partitioning = false)")
        rows = {name: work.sql(f"SELECT {', '.join(relation.columns)} FROM sem.{name} "
                               f"ORDER BY {relation.sort_columns}").to_arrow_table()
                for name, relation in GOLD_RELATIONS.items()}
        batch_top_remits = work.execute(
            f"SELECT claim_key, payer_key, claim_payment_uid, {REMIT_RANK_KEY_SQL}, is_reversal, "
            "strftime(payment_date, '%Y-%m-%d') FROM sem_int.remit_event WHERE event_rank_desc = 1").fetchall()
        batch_top_submissions = work.execute(
            f"SELECT clm01_patient_control_number, claim_uid, {SUBMISSION_RANK_KEY_SQL}, "
            "strftime(submission_date, '%Y-%m-%d') FROM sem_int.submission WHERE submission_rank_desc = 1").fetchall()
        if not self.tables_exist:
            self._create_tables(rows)
        lap("compute_batch_rows")

        # 2. what the key index held for these keys, and 3. which side loses
        demoted_in_batch: dict[str, set[str]] = {"remit": set(), "submission": set()}
        demoted_older: dict[str, dict[str | None, set[str]]] = {"remit": {}, "submission": {}}   # by the row's month
        index = self.key_index
        for claim_key, payer_key, remit_claim_id, rank_key, is_reversal, payment_date in batch_top_remits:
            before = index.execute("SELECT remit_claim_id, rank_key, is_reversal, payment_date FROM top_remit_event "
                                   "WHERE claim_key = ? AND payer_key = ?", (claim_key, payer_key)).fetchone()
            if before is not None and before[1] > rank_key:
                demoted_in_batch["remit"].add(remit_claim_id)
                continue
            if before is not None and not before[2]:      # a reversal was never current: nothing to change in it
                month = before[3][:7] + "-01" if before[3] else None
                demoted_older["remit"].setdefault(month, set()).add(before[0])
            index.execute("INSERT OR REPLACE INTO top_remit_event VALUES (?, ?, ?, ?, ?, ?)",
                          (claim_key, payer_key, remit_claim_id, rank_key, int(is_reversal), payment_date))
        for claim_key, claim_id, rank_key, submission_date in batch_top_submissions:
            before = index.execute("SELECT claim_id, rank_key, submission_date FROM top_submission WHERE claim_key = ?",
                                   (claim_key,)).fetchone()
            if before is not None and before[1] > rank_key:
                demoted_in_batch["submission"].add(claim_id)
                continue
            if before is not None:
                month = before[2][:7] + "-01" if before[2] else None
                demoted_older["submission"].setdefault(month, set()).add(before[0])
            index.execute("INSERT OR REPLACE INTO top_submission VALUES (?, ?, ?, ?)",
                          (claim_key, claim_id, rank_key, submission_date))
        lap("key_index")

        # 3a. the batch's own rows that an older event outranks, then 4. insert
        for name in REMIT_RELATIONS:
            rows[name] = _demote_remit_rows(rows[name], demoted_in_batch["remit"])
        for name in SUBMISSION_RELATIONS:
            rows[name] = _demote_submission_rows(rows[name], demoted_in_batch["submission"])
        work.execute("BEGIN")
        for name, table in rows.items():
            if table.num_rows:
                work.register("batch_rows", table)
                work.execute(f"INSERT INTO gold.{name} SELECT * FROM batch_rows")
                work.unregister("batch_rows")
        lap("insert_batch_rows")

        # 3b. older rows this batch outranks: one UPDATE per relation per month they are in
        for side, statements in (("remit", DEMOTE_REMIT_SQL), ("submission", DEMOTE_SUBMISSION_SQL)):
            for row_date, ids in demoted_older[side].items():
                work.register("demoted", pa.table({"id": sorted(ids)}))
                for statement in statements.values():
                    work.execute(statement, [row_date])
                work.unregister("demoted")
        work.execute("COMMIT")
        index.commit()
        lap("demote_older_rows")
        return {"batch_id": batch_id, "seconds": seconds, "seconds_total": round(sum(seconds.values()), 3),
                "gold_rows_added": sum(table.num_rows for table in rows.values()),
                "older_rows_demoted": sum(len(ids) for side in demoted_older.values() for ids in side.values()),
                "batch_rows_demoted": sum(len(ids) for ids in demoted_in_batch.values())}

    def compact(self) -> dict:
        """Merge the small files of earlier inserts, fold delete markers into their data files, and drop the
        files no snapshot needs any more."""
        started = time.monotonic()
        # a file is rewritten if any of its rows has a delete marker: by default only when most have, which leaves
        # a file and its marker file per daily insert
        self.work.execute("CALL ducklake_rewrite_data_files('gold', delete_threshold => 0.0)")
        self.work.execute("CALL ducklake_merge_adjacent_files('gold')")
        self.work.execute("CALL ducklake_expire_snapshots('gold', older_than => now())")
        self.work.execute("CALL ducklake_cleanup_old_files('gold', cleanup_all => true)")
        return {"seconds": round(time.monotonic() - started, 3)}

    def storage(self) -> dict:
        data_files = [p for p in (self.gold_directory / "data").rglob("*.parquet")]
        return {"data_files": len(data_files),
                "data_megabytes": round(sum(p.stat().st_size for p in data_files) / 1e6, 1),
                "catalog_megabytes": round(sum(p.stat().st_size for p in self.gold_directory.glob("catalog.ducklake*")) / 1e6, 1),
                "key_index_megabytes": round((self.gold_directory / "_key_index.sqlite").stat().st_size / 1e6, 1)}

    def close(self):
        self.work.close()
        self.key_index.close()


def _with_column(table: pa.Table, name: str, values: pa.Array) -> pa.Table:
    return table.set_column(table.schema.get_field_index(name), name, values)


def _demote_remit_rows(table: pa.Table, remit_claim_ids: set[str]) -> pa.Table:
    """The rows of these claim payments are not the current adjudication."""
    if not remit_claim_ids or not table.num_rows:
        return table
    demoted = pa.compute.is_in(table["remit_claim_id"], value_set=pa.array(sorted(remit_claim_ids)))
    table = _with_column(table, "is_current_adjudication", pa.compute.if_else(
        demoted, pa.scalar(False), table["is_current_adjudication"]))
    if "adjudication_state" in table.schema.names:        # 'current' -> 'superseded'; a reversal stays a reversal
        table = _with_column(table, "adjudication_state", pa.compute.if_else(
            pa.compute.and_(demoted, pa.compute.equal(table["adjudication_state"], "current")),
            pa.scalar("superseded"), table["adjudication_state"]))
    return table


def _demote_submission_rows(table: pa.Table, claim_ids: set[str]) -> pa.Table:
    """The rows of these submissions are not the latest submission."""
    if not claim_ids or not table.num_rows:
        return table
    demoted = pa.compute.is_in(table["claim_id"], value_set=pa.array(sorted(claim_ids)))
    return _with_column(table, "is_latest_submission", pa.compute.if_else(
        demoted, pa.scalar(False), table["is_latest_submission"]))


def open_incremental_gold(gold_directory: Path) -> duckdb.DuckDBPyConnection:
    """A DuckDB session with every gold relation as gold.<name>."""
    con = duckdb.connect()
    attach_gold(con, gold_directory, read_only=True)
    return con
