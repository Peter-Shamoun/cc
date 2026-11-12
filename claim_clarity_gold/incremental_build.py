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

Batches that are old enough are merged, a month at a time, into one file per relation (compact): queries then read
tens of files, not thousands, and each row is rewritten by compaction once.

Layout:  <gold>/<relation>/batch=<file id>.parquet      a batch's rows, or a compacted month's; immutable except
                                                        for step 3
         <gold>/_key_index.sqlite                       build state, not for queries: the top-ranked event per
                                                        key, and which file each batch's rows are in
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
CREATE TABLE IF NOT EXISTS file_of_batch (batch_id TEXT NOT NULL PRIMARY KEY, file_id TEXT NOT NULL) WITHOUT ROWID;
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
        # One working session for every batch: the lossless tables (emptied per batch), ref.* and the semantic views.
        self.work = duckdb.connect()
        self.work.execute(STAGING_DDL)
        seed_ref(self.work)
        self.work.execute(DDL_SEMANTIC)

    def _file(self, relation: str, file_id: str) -> Path:
        return self.gold_directory / relation / f"batch={file_id}.parquet"

    def _file_id_of_batch(self, batch_id: str) -> str:
        """The file a batch's rows are in: its own, until its month is compacted."""
        compacted = self.key_index.execute("SELECT file_id FROM file_of_batch WHERE batch_id = ?", (batch_id,)).fetchone()
        return compacted[0] if compacted else batch_id

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
            f"SELECT claim_key, payer_key, claim_payment_uid, {REMIT_RANK_KEY_SQL}, is_reversal "
            "FROM sem_int.remit_event WHERE event_rank_desc = 1").fetchall()
        batch_top_submissions = work.execute(
            f"SELECT clm01_patient_control_number, claim_uid, {SUBMISSION_RANK_KEY_SQL} "
            "FROM sem_int.submission WHERE submission_rank_desc = 1").fetchall()
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
        rewritten_files = rewritten_bytes = 0
        for side, relations, demote in (("remit", REMIT_RELATIONS, _demote_remit_rows),
                                        ("submission", SUBMISSION_RELATIONS, _demote_submission_rows)):
            ids_by_file: dict[str, set[str]] = {}
            for older_batch_id, ids in demoted_in_older_batches[side].items():
                ids_by_file.setdefault(self._file_id_of_batch(older_batch_id), set()).update(ids)
            for file_id, ids in ids_by_file.items():
                for name in relations:
                    size = self._rewrite(name, file_id, demote, ids)
                    rewritten_files += bool(size)
                    rewritten_bytes += size
        index.commit()
        lap("rewrite_older_files")
        return {"batch_id": batch_id, "seconds": seconds, "seconds_total": round(sum(seconds.values()), 3),
                "gold_rows_added": sum(table.num_rows for table in rows.values()),
                "older_rows_demoted": sum(len(ids) for side in demoted_in_older_batches.values() for ids in side.values()),
                "batch_rows_demoted": sum(len(ids) for ids in demoted_in_batch.values()),
                "older_files_rewritten": rewritten_files, "older_bytes_rewritten": rewritten_bytes,
                "batch_bytes_written": sum(self._file(name, batch_id).stat().st_size for name in rows),
                "older_rows_demoted_by_batch": {older: len(ids) for side in demoted_in_older_batches.values()
                                                for older, ids in side.items()}}

    def _rewrite(self, relation: str, file_id: str, demote, ids: set[str]) -> int:
        """Returns the bytes rewritten: 0 if none of the ids are in this relation's file."""
        path = self._file(relation, file_id)
        table = pq.read_table(path)
        changed = demote(table, ids)
        if changed is table:
            return 0
        temporary = path.with_suffix(".rewriting")
        pq.write_table(changed, temporary, compression="zstd")
        temporary.replace(path)
        return path.stat().st_size

    def compact(self, batch_ids: list[str], compacted_file_id: str) -> dict:
        """Merge these batches' files into one file per relation. Their rows stay as they are."""
        started = time.monotonic()
        bytes_written = 0
        for name, relation in GOLD_RELATIONS.items():
            sources = [str(self._file(name, self._file_id_of_batch(batch_id))) for batch_id in batch_ids]
            target = self._file(name, compacted_file_id)
            self.work.execute(f"COPY (SELECT * FROM read_parquet({sources}, hive_partitioning = false) "
                              f"ORDER BY {relation.sort_columns}) TO '{target}' (FORMAT parquet, COMPRESSION zstd)")
            bytes_written += target.stat().st_size
            for source in sources:
                Path(source).unlink()
        self.key_index.executemany("INSERT OR REPLACE INTO file_of_batch VALUES (?, ?)",
                                   [(batch_id, compacted_file_id) for batch_id in batch_ids])
        self.key_index.commit()
        return {"compacted_file_id": compacted_file_id, "batches": len(batch_ids),
                "seconds": round(time.monotonic() - started, 3), "bytes_written": bytes_written}


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
