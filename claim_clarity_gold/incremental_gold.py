"""Gold v2 kept current one silver batch at a time, as Iceberg tables.

    event tables   the batch's rows are appended. Nothing written earlier is touched.
    state tables   the rows of the claims the batch names are recomputed from their previous state and the batch,
                   and the month partitions those rows are in are replaced.

A batch's cost follows the batch and the size of a month, not the size of silver.

    <catalog>                       the Iceberg tables gold.<relation>, partitioned by month
    <gold>/_build_state.sqlite      build state, not for queries: every state row by key (to read a claim's previous
                                    state, and a month's rows, through an index), and the rank of each key's
                                    top-ranked event (to tell whether the batch's event outranks it)
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import duckdb
import pyarrow as pa
from pyiceberg.catalog import Catalog
from pyiceberg.expressions import EqualTo, IsNull
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.transforms import IdentityTransform

from ccsim.pipeline import DDL_SEMANTIC, seed_ref
from ccsim.silver_staging import STAGING_DDL

from .gold_relations import CLAIM_PAYERS_SQL, CLAIMS_SQL, EVENT_RELATIONS, STATE_MONTH_COLUMN, STATE_SORT_COLUMNS

GOLD_NAMESPACE = "gold"
# The semantic views rank events with these keys; as text that sorts the same way. NULL dates rank last.
REMIT_RANK_KEY_SQL = ("concat_ws('|', CASE WHEN is_duplicate_claim_denial THEN '0' ELSE '1' END, "
                      "coalesce(strftime(payment_date, '%Y%m%d'), '00000000'), "
                      "coalesce(strftime(file_received_at, '%Y%m%d%H%M%S'), '00000000000000'), file_id, "
                      "lpad(split_part(claim_payment_uid, ':', 2), 10, '0'))")
SUBMISSION_RANK_KEY_SQL = ("concat_ws('|', coalesce(strftime(submission_date, '%Y%m%d'), '00000000'), "
                           "coalesce(strftime(file_received_at, '%Y%m%d%H%M%S'), '00000000000000'), file_id, "
                           "lpad(seg_ord::VARCHAR, 10, '0'))")
STATE_KEY_COLUMNS = {"claim_payers": ["claim_key", "payer_key"], "claims": ["claim_key"],
                     "latest_submissions": ["claim_key"]}
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
}


class IncrementalGold:
    def __init__(self, gold_directory: Path, catalog: Catalog):
        gold_directory.mkdir(parents=True, exist_ok=True)
        self.catalog = catalog
        catalog.create_namespace_if_not_exists(GOLD_NAMESPACE)
        self.state = sqlite3.connect(gold_directory / "_build_state.sqlite")
        self.state.execute("CREATE TABLE IF NOT EXISTS top_remit_event (claim_key TEXT NOT NULL, payer_key TEXT "
                           "NOT NULL, rank_key TEXT NOT NULL, PRIMARY KEY (claim_key, payer_key)) WITHOUT ROWID")
        # the batch's silver rows (emptied per batch), ref.*, and the semantic views over them
        self.work = duckdb.connect()
        self.work.execute(STAGING_DDL)
        seed_ref(self.work)
        self.work.execute(DDL_SEMANTIC)
        # the state relations' columns and types, from their definitions
        self.work.execute("CREATE TABLE claim_payers_shape AS SELECT * FROM (" + CLAIM_PAYERS_SQL.format(
            claims="sem.claims", remit_claims="sem.remit_claims") + ") LIMIT 0")
        self.work.execute("CREATE TABLE latest_submissions_shape AS SELECT * FROM sem.claims LIMIT 0")
        self.work.execute("CREATE TABLE claims_shape AS SELECT * FROM (" + CLAIMS_SQL.format(
            latest_submissions="latest_submissions_shape", claim_payers="claim_payers_shape") + ") LIMIT 0")
        self.state_columns = {
            name: self.work.execute(f"DESCRIBE {name}_shape").fetchall()
            for name in ("claim_payers", "claims", "latest_submissions")}
        for name, columns in self.state_columns.items():
            keys = STATE_KEY_COLUMNS[name]
            self.state.execute(
                f"CREATE TABLE IF NOT EXISTS {name} ({', '.join(c[0] + ' TEXT' for c in columns)}"
                f"{', rank_key TEXT' if name == 'latest_submissions' else ''}, PRIMARY KEY ({', '.join(keys)})) "
                "WITHOUT ROWID")
        for name in ("claim_payers", "claims"):
            self.state.execute(f"CREATE INDEX IF NOT EXISTS {name}_by_month ON {name} ({STATE_MONTH_COLUMN})")

    # --- Iceberg ---------------------------------------------------------------------------------------------------
    def _table(self, name: str, rows: pa.Table, month_column: str):
        identifier = f"{GOLD_NAMESPACE}.{name}"
        if not self.catalog.table_exists(identifier):
            table = self.catalog.create_table(identifier, schema=rows.schema)
            with table.update_spec() as spec:
                spec.add_field(month_column, IdentityTransform(), month_column)
            return table
        return self.catalog.load_table(identifier)

    # --- build state -----------------------------------------------------------------------------------------------
    def _typed(self, name: str, text_rows: list[tuple], extra_text_columns: tuple[str, ...] = ()) -> str:
        """Registers state rows read from the build state (all text) under a new name, in their types."""
        columns = self.state_columns[name]
        names = [c[0] for c in columns] + list(extra_text_columns)
        raw = pa.table({n: pa.array([row[i] for row in text_rows], pa.string()) for i, n in enumerate(names)})
        self.work.register("state_text_rows", raw)
        relation = f"previous_{name}"
        self.work.execute(
            f"CREATE OR REPLACE TEMP TABLE {relation} AS SELECT "
            + ", ".join(f"CAST({c[0]} AS {c[1]}) AS {c[0]}" for c in columns)
            + "".join(f", {extra}" for extra in extra_text_columns) + " FROM state_text_rows")
        self.work.unregister("state_text_rows")
        return relation

    def _store(self, name: str, relation: str, extra_text_columns: tuple[str, ...] = ()):
        columns = [c[0] for c in self.state_columns[name]] + list(extra_text_columns)
        rows = self.work.execute(f"SELECT {', '.join(f'CAST({c} AS VARCHAR)' for c in columns)} FROM {relation}").fetchall()
        self.state.executemany(f"INSERT OR REPLACE INTO {name} VALUES ({', '.join('?' * len(columns))})", rows)

    def _previous(self, name: str, keys_relation: str, key_columns: list[str],
                  extra_text_columns: tuple[str, ...] = ()) -> str:
        """The stored rows of `name` matching the keys, read through the primary key index."""
        self.state.execute("CREATE TEMP TABLE IF NOT EXISTS wanted (k1 TEXT, k2 TEXT)")
        self.state.execute("DELETE FROM wanted")
        keys = self.work.execute(f"SELECT DISTINCT {', '.join(key_columns)} FROM {keys_relation}").fetchall()
        self.state.executemany(f"INSERT INTO wanted VALUES (?, {'?' if len(key_columns) == 2 else 'NULL'})", keys)
        columns = [c[0] for c in self.state_columns[name]] + list(extra_text_columns)
        on = " AND ".join(f"s.{column} = wanted.k{position}" for position, column in enumerate(key_columns, 1))
        rows = self.state.execute(f"SELECT {', '.join('s.' + c for c in columns)} FROM wanted "
                                  f"CROSS JOIN {name} s ON {on}").fetchall()
        return self._typed(name, rows, extra_text_columns)

    def _replace_month_partitions(self, name: str, months: set[str | None]):
        """Each of these months of a state table becomes what the build state holds for it."""
        columns = [c[0] for c in self.state_columns[name]]
        for month in sorted(months, key=str):
            rows = self.state.execute(
                f"SELECT {', '.join(columns)} FROM {name} WHERE {STATE_MONTH_COLUMN} IS ?", (month,)).fetchall()
            relation = self._typed(name, rows)
            data = self.work.sql(f"SELECT {', '.join(columns)} FROM {relation} "
                                 f"ORDER BY {STATE_SORT_COLUMNS[name]}").to_arrow_table()
            table = self._table(name, data, STATE_MONTH_COLUMN)
            month_filter = IsNull(STATE_MONTH_COLUMN) if month is None else EqualTo(STATE_MONTH_COLUMN, month)
            if data.num_rows:
                table.overwrite(data, overwrite_filter=month_filter)
            else:
                table.delete(month_filter)

    # --- a batch ---------------------------------------------------------------------------------------------------
    def apply_batch(self, batch_id: str, silver_table_files: dict[str, list[Path]]) -> dict:
        """silver_table_files: for each lossless table, the Parquet files holding this batch's rows (and only
        them). Returns timings and counts."""
        clock, seconds = time.monotonic(), {}

        def lap(step: str):
            nonlocal clock
            seconds[step] = round(time.monotonic() - clock, 3)
            clock = time.monotonic()
        work = self.work
        for table, files in silver_table_files.items():
            work.execute(f"TRUNCATE {table}")
            if files:
                work.execute(f"INSERT INTO {table} BY NAME SELECT * FROM read_parquet({[str(f) for f in files]}, "
                             "hive_partitioning = false)")
        lap("load_batch")

        # event tables: append
        event_rows_added = 0
        for name, relation in EVENT_RELATIONS.items():
            rows = work.sql(f"SELECT {', '.join(relation.columns)} FROM sem.{relation.semantic_relation} "
                            f"ORDER BY {relation.sort_columns}").to_arrow_table()
            if rows.num_rows:
                self._table(name, rows, relation.month_column).append(rows)
                event_rows_added += rows.num_rows
        lap("append_events")

        # the batch's part of the state, from the batch's rows alone
        work.execute("CREATE OR REPLACE TEMP TABLE batch_claim_payers AS " + CLAIM_PAYERS_SQL.format(
            claims="sem.claims", remit_claims="sem.remit_claims"))
        work.execute(f"CREATE OR REPLACE TEMP TABLE batch_top_remit_event AS SELECT claim_key, payer_key, "
                     f"{REMIT_RANK_KEY_SQL} AS rank_key FROM sem_int.remit_event WHERE event_rank_desc = 1")
        work.execute(f"CREATE OR REPLACE TEMP TABLE batch_latest_submissions AS SELECT c.*, r.rank_key FROM sem.claims c "
                     f"JOIN (SELECT claim_uid, {SUBMISSION_RANK_KEY_SQL} AS rank_key FROM sem_int.submission "
                     "WHERE submission_rank_desc = 1) r ON r.claim_uid = c.claim_id")
        work.execute("CREATE OR REPLACE TEMP TABLE touched_claims AS SELECT DISTINCT claim_key FROM batch_claim_payers")
        lap("batch_state")

        # previous state of the claims the batch names
        self._previous("claim_payers", "touched_claims", ["claim_key"])           # every payer of those claims
        self._previous("latest_submissions", "touched_claims", ["claim_key"], ("rank_key",))
        self._previous("claims", "touched_claims", ["claim_key"])
        self.state.execute("DELETE FROM wanted")
        self.state.executemany("INSERT INTO wanted VALUES (?, ?)", work.execute(
            "SELECT claim_key, payer_key FROM batch_top_remit_event").fetchall())
        previous_top = self.state.execute(
            "SELECT s.claim_key, s.payer_key, s.rank_key FROM wanted CROSS JOIN top_remit_event s "
            "ON s.claim_key = wanted.k1 AND s.payer_key = wanted.k2").fetchall()
        work.register("previous_top_remit_event", pa.table(
            {name: pa.array([row[position] for row in previous_top], pa.string())
             for position, name in enumerate(("claim_key", "payer_key", "rank_key"))}))
        lap("read_previous_state")

        # fold: claim_payers
        work.execute("CREATE OR REPLACE TEMP TABLE batch_outranks AS SELECT b.claim_key, b.payer_key, b.rank_key "
                     "FROM batch_top_remit_event b LEFT JOIN previous_top_remit_event p USING (claim_key, payer_key) "
                     "WHERE p.rank_key IS NULL OR b.rank_key > p.rank_key")
        fold = ", ".join(f"{expression} AS {column}" for column, expression in CLAIM_PAYERS_FOLD.items())
        current = ", ".join(f"CASE WHEN w.claim_key IS NOT NULL THEN b.{c} ELSE o.{c} END AS {c}"
                            for c in CURRENT_ADJUDICATION_COLUMNS)
        columns = ", ".join(c[0] for c in self.state_columns["claim_payers"])
        work.execute(f"""CREATE OR REPLACE TEMP TABLE new_claim_payers AS
            SELECT {columns} FROM (
                SELECT claim_key, payer_key, {fold}, {current},
                       date_trunc('month', least(least(o.first_submission_date, b.first_submission_date),
                                                 least(o.first_remit_date, b.first_remit_date)))::DATE
                           AS {STATE_MONTH_COLUMN}
                FROM batch_claim_payers b LEFT JOIN previous_claim_payers o USING (claim_key, payer_key)
                LEFT JOIN batch_outranks w USING (claim_key, payer_key))""")
        work.execute("UPDATE new_claim_payers SET has_current_adjudication = coalesce(has_current_adjudication, false)")
        # fold: the latest submission of each claim
        latest_columns = ", ".join(c[0] for c in self.state_columns["latest_submissions"])
        work.execute(f"""CREATE OR REPLACE TEMP TABLE new_latest_submissions AS
            SELECT {latest_columns}, rank_key FROM batch_latest_submissions b
            WHERE NOT EXISTS (SELECT 1 FROM previous_latest_submissions o
                              WHERE o.claim_key = b.claim_key AND o.rank_key > b.rank_key)""")
        # claims: recomputed for the claims the batch names, from their payers' state and their latest submission
        work.execute(f"""CREATE OR REPLACE TEMP TABLE claim_payers_of_touched_claims AS
            SELECT * FROM new_claim_payers UNION ALL
            SELECT * FROM previous_claim_payers o WHERE NOT EXISTS (
                SELECT 1 FROM new_claim_payers n WHERE n.claim_key = o.claim_key AND n.payer_key = o.payer_key)""")
        work.execute(f"""CREATE OR REPLACE TEMP TABLE latest_submissions_of_touched_claims AS
            SELECT {latest_columns} FROM new_latest_submissions UNION ALL
            SELECT {latest_columns} FROM previous_latest_submissions o WHERE NOT EXISTS (
                SELECT 1 FROM new_latest_submissions n WHERE n.claim_key = o.claim_key)""")
        work.execute("CREATE OR REPLACE TEMP TABLE new_claims AS " + CLAIMS_SQL.format(
            latest_submissions="latest_submissions_of_touched_claims", claim_payers="claim_payers_of_touched_claims"))
        lap("fold_state")

        # which month partitions change, then store the new state
        months = {name: {row[0] for row in work.execute(
            f"SELECT DISTINCT CAST({STATE_MONTH_COLUMN} AS VARCHAR) FROM new_{name} UNION "
            f"SELECT DISTINCT CAST(o.{STATE_MONTH_COLUMN} AS VARCHAR) FROM previous_{name} o SEMI JOIN new_{name} n "
            f"USING ({', '.join(STATE_KEY_COLUMNS[name])})").fetchall()} for name in ("claim_payers", "claims")}
        self._store("claim_payers", "new_claim_payers")
        self._store("latest_submissions", "new_latest_submissions", ("rank_key",))
        self._store("claims", "new_claims")
        self.state.executemany("INSERT OR REPLACE INTO top_remit_event VALUES (?, ?, ?)",
                               work.execute("SELECT claim_key, payer_key, rank_key FROM batch_outranks").fetchall())
        lap("store_state")
        for name in ("claim_payers", "claims"):
            self._replace_month_partitions(name, months[name])
        self.state.commit()
        lap("replace_state_partitions")
        return {"batch_id": batch_id, "seconds": seconds, "seconds_total": round(sum(seconds.values()), 3),
                "event_rows_added": event_rows_added,
                "state_rows_replaced": sum(work.execute(f"SELECT count(*) FROM new_{n}").fetchall()[0][0]
                                           for n in ("claim_payers", "claims")),
                "state_month_partitions_replaced": sum(len(m) for m in months.values())}

    def close(self):
        self.work.close()
        self.state.close()


def open_incremental_gold(catalog: Catalog) -> duckdb.DuckDBPyConnection:
    """A DuckDB session with every gold relation as gold.<name>, over the Iceberg tables' current data files."""
    con = duckdb.connect()
    con.execute("CREATE SCHEMA gold")
    for _, name in catalog.list_tables(GOLD_NAMESPACE):
        files = [task.file.file_path.removeprefix("file://") for task in
                 catalog.load_table(f"{GOLD_NAMESPACE}.{name}").scan().plan_files()]
        con.execute(f"CREATE VIEW gold.{name} AS SELECT * FROM read_parquet({files})")
    return con
