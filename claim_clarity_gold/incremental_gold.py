"""Gold v2 kept current one silver batch at a time, as Iceberg tables.

    event tables   the batch's rows are appended. Nothing written earlier is touched.
    state tables   the rows of the claims the batch names are recomputed from their previous state and the batch,
                   and the month partitions those rows are in are replaced.

A batch's cost follows the batch and the size of a month, not the size of silver.

    <catalog>                       the Iceberg tables <namespace>.<relation>, partitioned by month
    <gold>/_key_index.sqlite        build state, not for queries: for each claim, and each claim and payer, the
                                    month partition its state row is in and the rank of its top-ranked event (to
                                    tell whether the batch's event outranks it)
"""
from __future__ import annotations

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.dataset
import pyarrow.fs
from pyiceberg.catalog import Catalog
from pyiceberg.expressions import EqualTo, In, IsNull, Or
from pyiceberg.transforms import IdentityTransform

from ccsim.pipeline import DDL_SEMANTIC, seed_ref
from ccsim.silver_staging import STAGING_DDL

from .gold_relations import (CLAIM_PAYERS_SQL, CLAIMS_SQL, EVENT_RELATIONS, LATEST_SUBMISSION_COLUMNS,
                             SUPERSEDED_REMIT_CLAIMS_COLUMNS, SUPERSEDED_REMIT_CLAIMS_SORT_COLUMNS,
                             STATE_MONTH_COLUMN, STATE_SORT_COLUMNS)

# The semantic views rank events with these keys; as text that sorts the same way. NULL dates rank last.
REMIT_RANK_KEY_SQL = ("concat_ws('|', CASE WHEN is_duplicate_claim_denial THEN '0' ELSE '1' END, "
                      "coalesce(strftime(payment_date, '%Y%m%d'), '00000000'), "
                      "coalesce(strftime(file_received_at, '%Y%m%d%H%M%S'), '00000000000000'), file_id, "
                      "lpad(split_part(claim_payment_uid, ':', 2), 10, '0'))")
SUBMISSION_RANK_KEY_SQL = ("concat_ws('|', coalesce(strftime(submission_date, '%Y%m%d'), '00000000'), "
                           "coalesce(strftime(file_received_at, '%Y%m%d%H%M%S'), '00000000000000'), file_id, "
                           "lpad(seg_ord::VARCHAR, 10, '0'))")
STATE_KEY_COLUMNS = {"claim_payers": ["claim_key", "payer_key"], "claims": ["claim_key"]}
KEY_INDEX_DDL = """
CREATE TABLE IF NOT EXISTS claim_payers (claim_key TEXT NOT NULL, payer_key TEXT NOT NULL, month TEXT,
    top_remit_rank_key TEXT, PRIMARY KEY (claim_key, payer_key)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS claims (claim_key TEXT NOT NULL PRIMARY KEY, month TEXT, latest_submission_rank_key TEXT)
    WITHOUT ROWID;
"""
# claim_payers columns that describe the current adjudication: they follow the top-ranked remittance event
CURRENT_ADJUDICATION_COLUMNS = [
    "has_current_adjudication", "current_remit_claim_id", "current_payment_date", "current_claim_status_code",
    "current_charge_amount", "current_paid_amount", "current_patient_responsibility_amount", "current_co_amount",
    "current_is_denied"]
# how the other claim_payers columns combine a claim and payer's previous state with a batch
CLAIM_PAYERS_FOLD = {
    "payer_name": "coalesce(b.payer_name, o.payer_name)", "payer_class": "coalesce(b.payer_class, o.payer_class)",
    "payer_responsibility": "least(o.payer_responsibility, b.payer_responsibility)",
    "first_submission_date": "least(o.first_submission_date, b.first_submission_date)",
    "last_submission_date": "greatest(o.last_submission_date, b.last_submission_date)",
    "submission_count": "coalesce(o.submission_count, 0) + coalesce(b.submission_count, 0)",
    "first_remit_date": "least(o.first_remit_date, b.first_remit_date)",
    "last_remit_date": "greatest(o.last_remit_date, b.last_remit_date)",
    "remit_count": "coalesce(o.remit_count, 0) + coalesce(b.remit_count, 0)",
    "reversal_count": "coalesce(o.reversal_count, 0) + coalesce(b.reversal_count, 0)",
    "first_acknowledgment_date": "least(o.first_acknowledgment_date, b.first_acknowledgment_date)",
    "last_acknowledgment_date": "greatest(o.last_acknowledgment_date, b.last_acknowledgment_date)",
    "acknowledgment_count": "coalesce(o.acknowledgment_count, 0) + coalesce(b.acknowledgment_count, 0)",
    "rejection_count": "coalesce(o.rejection_count, 0) + coalesce(b.rejection_count, 0)",
}
# claim_payers columns that describe the last acknowledgment: the batch's, when it is later (by date, then id)
LAST_ACKNOWLEDGMENT_COLUMNS = ["last_acknowledgment_id", "last_acknowledgment_is_rejected",
                               "last_acknowledgment_status_code"]
BATCH_ACKNOWLEDGMENT_IS_LATER = (
    "b.last_acknowledgment_id IS NOT NULL AND (o.last_acknowledgment_id IS NULL "
    "OR b.last_acknowledgment_date > o.last_acknowledgment_date "
    "OR (b.last_acknowledgment_date = o.last_acknowledgment_date AND b.last_acknowledgment_id > o.last_acknowledgment_id))")


class IncrementalGold:
    def __init__(self, gold_directory: Path, catalog: Catalog, namespace: str):
        """namespace: the catalog database holding this customer's gold tables."""
        gold_directory.mkdir(parents=True, exist_ok=True)
        self.catalog, self.namespace = catalog, namespace
        catalog.create_namespace_if_not_exists(namespace)
        self.key_index = sqlite3.connect(gold_directory / "_key_index.sqlite")
        self.key_index.executescript(KEY_INDEX_DDL)
        self.key_index.execute("CREATE TEMP TABLE wanted (claim_key TEXT PRIMARY KEY) WITHOUT ROWID")
        # the batch's silver rows (emptied per batch), ref.*, and the semantic views over them
        self.work = duckdb.connect()
        self.work.execute(STAGING_DDL)
        seed_ref(self.work)
        self.work.execute(DDL_SEMANTIC)
        # empty relations with the state tables' columns and types, from their definitions
        self.work.execute("CREATE TABLE empty_claim_payers AS SELECT * FROM (" + CLAIM_PAYERS_SQL.format(
            claims="sem.claims", claim_acknowledgments="sem.claim_acknowledgments",
            remit_claims="sem.remit_claims") + ") LIMIT 0")
        self.work.execute("CREATE TABLE empty_claims AS SELECT * FROM (" + CLAIMS_SQL.format(
            latest_submissions="(SELECT * FROM sem.claims WHERE is_latest_submission)",
            claim_payers="empty_claim_payers") + ") LIMIT 0")

    # --- Iceberg ---------------------------------------------------------------------------------------------------
    def _table(self, name: str, rows: pa.Table, month_column: str):
        identifier = f"{self.namespace}.{name}"
        if not self.catalog.table_exists(identifier):
            table = self.catalog.create_table(identifier, schema=rows.schema,
                                              properties={"commit.manifest-merge.enabled": "true"})
            with table.update_spec() as spec:
                spec.add_field(month_column, IdentityTransform(), month_column)
            return table
        return self.catalog.load_table(identifier)

    def _read_state_months(self, name: str, months: set[str | None], into: str):
        """The rows of a state table in these month partitions, as the work relation `into`."""
        self.work.execute(f"CREATE OR REPLACE TEMP TABLE {into} AS SELECT * FROM empty_{name}")
        identifier = f"{self.namespace}.{name}"
        if not months or not self.catalog.table_exists(identifier):
            return
        named = sorted(month for month in months if month is not None)
        month_filter = In(STATE_MONTH_COLUMN, named) if named else None
        if None in months:
            month_filter = Or(month_filter, IsNull(STATE_MONTH_COLUMN)) if month_filter else IsNull(STATE_MONTH_COLUMN)
        rows = self.catalog.load_table(identifier).scan(row_filter=month_filter).to_arrow()
        self.work.register("state_rows_read", rows)
        self.work.execute(f"INSERT INTO {into} BY NAME SELECT * FROM state_rows_read")
        self.work.unregister("state_rows_read")

    def _replace_state_months(self, name: str, months: set[str | None], rows_relation: str):
        """These month partitions of a state table become the rows of rows_relation in them, in one commit."""
        if not months:
            return
        data = self.work.sql(f"SELECT * FROM {rows_relation} ORDER BY {STATE_MONTH_COLUMN}, "
                             f"{STATE_SORT_COLUMNS[name]}").to_arrow_table()
        table = self._table(name, data, STATE_MONTH_COLUMN)
        named = sorted(month for month in months if month is not None)
        month_filter = In(STATE_MONTH_COLUMN, named) if named else None
        if None in months:
            month_filter = Or(month_filter, IsNull(STATE_MONTH_COLUMN)) if month_filter else IsNull(STATE_MONTH_COLUMN)
        if data.num_rows:
            table.overwrite(data, overwrite_filter=month_filter)
        else:
            table.delete(month_filter)

    def _months(self, relation: str) -> set[str | None]:
        return {row[0] for row in self.work.execute(
            f"SELECT DISTINCT CAST({STATE_MONTH_COLUMN} AS VARCHAR) FROM {relation}").fetchall()}

    # --- a batch ---------------------------------------------------------------------------------------------------
    def apply_batch(self, batch_id: str, silver_table_files: dict[str, list[Path]]) -> dict:
        """silver_table_files: for each lossless table, the Parquet files holding this batch's rows (and only
        them). Returns timings and counts."""
        clock, seconds = time.monotonic(), {}

        def lap(step: str):
            nonlocal clock
            seconds[step] = round(time.monotonic() - clock, 3)
            clock = time.monotonic()
        work, index = self.work, self.key_index
        for table, files in silver_table_files.items():
            work.execute(f"TRUNCATE {table}")
            if files:
                work.execute(f"INSERT INTO {table} BY NAME SELECT * FROM read_parquet({[str(f) for f in files]}, "
                             "hive_partitioning = false)")
        lap("load_batch")

        # event tables: append. They are independent Iceberg tables, so their commits run side by side.
        event_rows = {name: work.sql(f"SELECT {', '.join(relation.columns)} FROM sem.{relation.semantic_relation} "
                                     f"ORDER BY {relation.sort_columns}").to_arrow_table()
                      for name, relation in EVENT_RELATIONS.items()}
        # every table exists from the first batch on, empty or not: a customer without 277CA files still has
        # gold.claim_acknowledgments
        with ThreadPoolExecutor(len(EVENT_RELATIONS)) as pool:
            tables = dict(zip(event_rows, pool.map(lambda name: self._table(
                name, event_rows[name], EVENT_RELATIONS[name].month_column), event_rows)))
            list(pool.map(lambda name: tables[name].append(event_rows[name]),
                          [name for name, rows in event_rows.items() if rows.num_rows]))
        event_rows_added = sum(rows.num_rows for rows in event_rows.values())
        lap("append_events")

        # the batch's part of the state, from the batch's rows alone
        work.execute("CREATE OR REPLACE TEMP TABLE batch_claim_payers AS " + CLAIM_PAYERS_SQL.format(
            claims="sem.claims", claim_acknowledgments="sem.claim_acknowledgments",
            remit_claims="sem.remit_claims"))
        work.execute(f"CREATE OR REPLACE TEMP TABLE batch_top_remit_event AS SELECT claim_key, payer_key, "
                     f"{REMIT_RANK_KEY_SQL} AS rank_key FROM sem_int.remit_event WHERE event_rank_desc = 1")
        work.execute(f"CREATE OR REPLACE TEMP TABLE batch_latest_submissions AS "
                     f"SELECT {', '.join('c.' + c for c in LATEST_SUBMISSION_COLUMNS)}, r.rank_key FROM sem.claims c "
                     f"JOIN (SELECT claim_uid, {SUBMISSION_RANK_KEY_SQL} AS rank_key FROM sem_int.submission "
                     "WHERE submission_rank_desc = 1) r ON r.claim_uid = c.claim_id")
        lap("batch_state")

        # where the previous state of the claims the batch names is, and how their top events rank
        index.execute("DELETE FROM wanted")
        index.executemany("INSERT INTO wanted VALUES (?)", work.execute(
            "SELECT DISTINCT claim_key FROM batch_claim_payers").fetchall())
        indexed_pairs = index.execute("SELECT s.claim_key, s.payer_key, s.month, s.top_remit_rank_key "
                                      "FROM wanted CROSS JOIN claim_payers s USING (claim_key)").fetchall()
        indexed_claims = index.execute("SELECT s.claim_key, s.month, s.latest_submission_rank_key "
                                       "FROM wanted CROSS JOIN claims s USING (claim_key)").fetchall()
        for relation, names, rows in (
                ("indexed_claim_payers", ("claim_key", "payer_key", "month", "top_remit_rank_key"), indexed_pairs),
                ("indexed_claims", ("claim_key", "month", "latest_submission_rank_key"), indexed_claims)):
            work.register("index_rows", pa.table({name: pa.array([row[position] for row in rows], pa.string())
                                                  for position, name in enumerate(names)}))
            work.execute(f"CREATE OR REPLACE TEMP TABLE {relation} AS SELECT * FROM index_rows")
            work.unregister("index_rows")
        previous_months = {"claim_payers": {row[2] for row in indexed_pairs},
                           "claims": {row[1] for row in indexed_claims}}
        self._read_state_months("claim_payers", previous_months["claim_payers"], "month_claim_payers")
        self._read_state_months("claims", previous_months["claims"], "month_claims")
        work.execute("CREATE OR REPLACE TEMP TABLE previous_claim_payers AS SELECT * FROM month_claim_payers "
                     "SEMI JOIN indexed_claim_payers USING (claim_key, payer_key)")
        lap("read_previous_state")

        # fold: claim_payers
        work.execute("CREATE OR REPLACE TEMP TABLE batch_outranks AS SELECT b.claim_key, b.payer_key, b.rank_key "
                     "FROM batch_top_remit_event b LEFT JOIN indexed_claim_payers p USING (claim_key, payer_key) "
                     "WHERE p.top_remit_rank_key IS NULL OR b.rank_key > p.top_remit_rank_key")
        fold = ", ".join(f"{expression} AS {column}" for column, expression in CLAIM_PAYERS_FOLD.items())
        current = ", ".join(f"CASE WHEN w.claim_key IS NOT NULL THEN b.{c} ELSE o.{c} END AS {c}"
                            for c in CURRENT_ADJUDICATION_COLUMNS)
        current += ", " + ", ".join(f"CASE WHEN {BATCH_ACKNOWLEDGMENT_IS_LATER} THEN b.{c} ELSE o.{c} END AS {c}"
                                    for c in LAST_ACKNOWLEDGMENT_COLUMNS)
        work.execute(f"""CREATE OR REPLACE TEMP TABLE new_claim_payers AS SELECT * FROM empty_claim_payers;
            INSERT INTO new_claim_payers BY NAME
            SELECT * REPLACE (coalesce(has_current_adjudication, false) AS has_current_adjudication) FROM (
                SELECT claim_key, payer_key, {fold}, {current},
                       date_trunc('month', least(o.first_submission_date, b.first_submission_date,
                                                 o.first_acknowledgment_date, b.first_acknowledgment_date,
                                                 o.first_remit_date, b.first_remit_date))::DATE
                           AS {STATE_MONTH_COLUMN}
                FROM batch_claim_payers b LEFT JOIN previous_claim_payers o USING (claim_key, payer_key)
                LEFT JOIN batch_outranks w USING (claim_key, payer_key))""")
        # fold: each claim's latest submission is the batch's if it outranks the indexed one, else the one its
        # claims row carries
        latest = ", ".join(LATEST_SUBMISSION_COLUMNS)
        work.execute(f"""CREATE OR REPLACE TEMP TABLE new_latest_submissions AS
            SELECT {latest}, b.rank_key FROM batch_latest_submissions b LEFT JOIN indexed_claims i USING (claim_key)
            WHERE i.latest_submission_rank_key IS NULL OR b.rank_key > i.latest_submission_rank_key""")
        work.execute(f"""CREATE OR REPLACE TEMP TABLE latest_submissions_of_touched_claims AS
            SELECT {latest} FROM new_latest_submissions UNION ALL
            SELECT {latest.replace('claim_id', 'latest_claim_id AS claim_id')} FROM month_claims o
            SEMI JOIN indexed_claims USING (claim_key)
            WHERE o.latest_claim_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM new_latest_submissions n WHERE n.claim_key = o.claim_key)""")
        # claims: recomputed for the claims the batch names, from all their payers' state and their latest submission
        work.execute("""CREATE OR REPLACE TEMP TABLE claim_payers_of_touched_claims AS
            SELECT * FROM new_claim_payers UNION ALL BY NAME
            SELECT * FROM previous_claim_payers o WHERE NOT EXISTS (
                SELECT 1 FROM new_claim_payers n WHERE n.claim_key = o.claim_key AND n.payer_key = o.payer_key)""")
        work.execute("CREATE OR REPLACE TEMP TABLE new_claims AS SELECT * FROM empty_claims; "
                     "INSERT INTO new_claims BY NAME " + CLAIMS_SQL.format(
                         latest_submissions="latest_submissions_of_touched_claims",
                         claim_payers="claim_payers_of_touched_claims"))
        # the batch's claim payments that are not current after the fold, and the previous current adjudications
        # the batch replaced
        superseded = work.sql(f"""
            SELECT {', '.join('r.' + c for c in SUPERSEDED_REMIT_CLAIMS_COLUMNS)} FROM sem.remit_claims r
            WHERE r.remit_claim_id NOT IN (SELECT current_remit_claim_id FROM new_claim_payers
                                           WHERE current_remit_claim_id IS NOT NULL)
            UNION ALL
            SELECT o.current_remit_claim_id, o.claim_key, o.payer_key,
                   date_trunc('month', o.current_payment_date)::DATE
            FROM previous_claim_payers o JOIN new_claim_payers n USING (claim_key, payer_key)
            WHERE o.current_remit_claim_id IS NOT NULL
              AND o.current_remit_claim_id IS DISTINCT FROM n.current_remit_claim_id
            ORDER BY {SUPERSEDED_REMIT_CLAIMS_SORT_COLUMNS}""").to_arrow_table()
        superseded_table = self._table("superseded_remit_claims", superseded, "payment_month")
        if superseded.num_rows:
            superseded_table.append(superseded)
        lap("fold_state")

        # replace the month partitions the new rows leave and enter
        state_partitions_replaced = 0
        for name in ("claim_payers", "claims"):
            keys = ", ".join(STATE_KEY_COLUMNS[name])
            entered = self._months(f"new_{name}") - previous_months[name]
            self._read_state_months(name, entered, f"entered_month_{name}")
            work.execute(f"""CREATE OR REPLACE TEMP TABLE replaced_{name} AS
                SELECT * FROM new_{name} UNION ALL BY NAME
                SELECT * FROM (SELECT * FROM month_{name} UNION ALL BY NAME SELECT * FROM entered_month_{name}) o
                ANTI JOIN new_{name} USING ({keys})""")
            months = previous_months[name] | self._months(f"new_{name}")
            self._replace_state_months(name, months, f"replaced_{name}")
            state_partitions_replaced += len(months)
        lap("replace_state_partitions")

        index.executemany("INSERT OR REPLACE INTO claim_payers VALUES (?, ?, ?, ?)", work.execute(
            f"""SELECT n.claim_key, n.payer_key, CAST(n.{STATE_MONTH_COLUMN} AS VARCHAR),
                       coalesce(w.rank_key, i.top_remit_rank_key)
                FROM new_claim_payers n LEFT JOIN batch_outranks w USING (claim_key, payer_key)
                LEFT JOIN indexed_claim_payers i USING (claim_key, payer_key)""").fetchall())
        index.executemany("INSERT OR REPLACE INTO claims VALUES (?, ?, ?)", work.execute(
            f"""SELECT n.claim_key, CAST(n.{STATE_MONTH_COLUMN} AS VARCHAR),
                       coalesce(l.rank_key, i.latest_submission_rank_key)
                FROM new_claims n LEFT JOIN new_latest_submissions l USING (claim_key)
                LEFT JOIN indexed_claims i USING (claim_key)""").fetchall())
        index.commit()
        lap("key_index")
        return {"batch_id": batch_id, "seconds": seconds, "seconds_total": round(sum(seconds.values()), 3),
                "event_rows_added": event_rows_added,
                "state_rows_replaced": sum(work.execute(f"SELECT count(*) FROM new_{n}").fetchall()[0][0]
                                           for n in ("claim_payers", "claims")),
                "state_month_partitions_replaced": state_partitions_replaced}

    def compact(self) -> dict:
        """Daily appends leave a small file per day in each event month, and each replaced state partition leaves
        its previous files behind. Merge every month partition that has several files into one sorted file, keep
        only the current snapshot, and delete the data files it does not use."""
        started, files_merged, files_deleted = time.monotonic(), 0, 0
        sort_columns = ({name: relation.sort_columns for name, relation in EVENT_RELATIONS.items()} | STATE_SORT_COLUMNS
                        | {"superseded_remit_claims": SUPERSEDED_REMIT_CLAIMS_SORT_COLUMNS})
        month_columns = {name: relation.month_column for name, relation in EVENT_RELATIONS.items()} | dict.fromkeys(
            STATE_SORT_COLUMNS, STATE_MONTH_COLUMN) | {"superseded_remit_claims": "payment_month"}
        for _, name in self.catalog.list_tables(self.namespace):
            table = self.catalog.load_table(f"{self.namespace}.{name}")
            files_per_month: dict[int | None, int] = {}     # a month as Iceberg stores it: days since 1970
            for task in table.scan().plan_files():
                month = task.file.partition[0]
                files_per_month[month] = files_per_month.get(month, 0) + 1
            for month, file_count in files_per_month.items():
                if file_count == 1:
                    continue
                month_filter = (IsNull(month_columns[name]) if month is None
                                else EqualTo(month_columns[name], (date(1970, 1, 1) + timedelta(days=month)).isoformat()))
                self.work.register("month_rows", table.scan(row_filter=month_filter).to_arrow())
                merged = self.work.sql(f"SELECT * FROM month_rows ORDER BY {sort_columns[name]}").to_arrow_table()
                self.work.unregister("month_rows")
                table.overwrite(merged, overwrite_filter=month_filter)
                files_merged += file_count
            table.maintenance.expire_snapshots().older_than(datetime.now()).commit()
            table = self.catalog.load_table(f"{self.namespace}.{name}")
            in_use = {task.file.file_path for task in table.scan().plan_files()}
            filesystem, data_path = pa.fs.FileSystem.from_uri(f"{table.location()}/data")
            for file in filesystem.get_file_info(pa.fs.FileSelector(data_path, recursive=True)):
                if file.is_file and not any(used.endswith(file.path) for used in in_use):
                    filesystem.delete_file(file.path)
                    files_deleted += 1
        return {"seconds": round(time.monotonic() - started, 3), "files_merged": files_merged,
                "files_deleted": files_deleted}

    def close(self):
        self.work.close()
        self.key_index.close()


def open_incremental_gold(catalog: Catalog, namespace: str,
                          filesystem: pa.fs.FileSystem | None = None) -> duckdb.DuckDBPyConnection:
    """A DuckDB session with every gold relation as gold.<name>, over the Iceberg tables' current data files.
    filesystem: where the files are when not on local disk (S3, as the silver lake opens it)."""
    con = duckdb.connect()
    con.execute("CREATE SCHEMA gold")
    for _, name in catalog.list_tables(namespace):
        paths = [task.file.file_path for task in catalog.load_table(f"{namespace}.{name}").scan().plan_files()]
        if filesystem is None:
            con.execute(f"CREATE VIEW gold.{name} AS SELECT * FROM read_parquet("
                        f"{[path.removeprefix('file://') for path in paths]})")
        else:
            con.register(f"gold_{name}_files", pa.dataset.dataset(
                [path.removeprefix("s3://") for path in paths], filesystem=filesystem, format="parquet"))
            con.execute(f"CREATE VIEW gold.{name} AS SELECT * FROM gold_{name}_files")
    return con
