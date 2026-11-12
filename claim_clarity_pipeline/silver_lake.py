"""The customer's silver layer: the lossless EDI schema as Iceberg tables in the customer's bucket.

One Iceberg table per lossless table (edi.source_file -> edi_source_file), in a Glue database. A slot export
appends to each table; edi_source_file is appended last and is the commit record: a file is in the lake once its
edi_source_file row is.

An Iceberg commit covers one table, so an export of 25 tables is not atomic. Before its first append an export
writes a journal object naming its files, and removes it after the last. A journal found later means that export
was interrupted: its rows are deleted from every table (recover_interrupted_exports), and the files, whose SQS
messages were never deleted, are loaded again.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

import boto3
import duckdb
import pyarrow as pa
import pyarrow.dataset
import pyarrow.fs
from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pyiceberg.expressions import EqualTo, In
from pyiceberg.table import Table

from ccsim.parser import TABLES as PARSED_TABLES
from ccsim.silver_staging import STAGING_DDL

from .customer_settings import SILVER_PREFIX, CustomerPipelineSettings

log = logging.getLogger(__name__)

COMMIT_RECORD_TABLE = "edi.source_file"
INGEST_RUN_TABLE = "edi.ingest_run"
# Append order: the commit record last.
SILVER_TABLES = [INGEST_RUN_TABLE] + [t for t in PARSED_TABLES if t != COMMIT_RECORD_TABLE] + [COMMIT_RECORD_TABLE]
EXPORT_JOURNAL_PREFIX = f"{SILVER_PREFIX}/_export_journal/"
EXPORT_ID_SNAPSHOT_PROPERTY = "claim-clarity.export-id"

CANDIDATE_SELECTION_TABLE = "candidate_selection"            # one row per claim ever selected, with its run id
CANDIDATE_EXPORT_TABLE = "candidate_export"                  # one row per run whose CSV was written
CANDIDATE_TABLE_SCHEMAS = {
    CANDIDATE_SELECTION_TABLE: pa.schema([
        ("candidate_selection_run_id", pa.string()), ("claim_key", pa.string()),
        ("selected_at", pa.timestamp("us")), ("preset_name", pa.string()), ("preset_s3_version_id", pa.string()),
        ("candidate_row_json", pa.string())]),               # the selected row, exactly as it goes into the CSV
    CANDIDATE_EXPORT_TABLE: pa.schema([
        ("candidate_selection_run_id", pa.string()), ("exported_at", pa.timestamp("us")),
        ("candidate_count", pa.int64()), ("csv_s3_uri", pa.string())]),
}


def iceberg_table_name(lossless_table: str) -> str:
    return lossless_table.replace(".", "_")


def _lossless_arrow_schemas() -> dict[str, pa.Schema]:
    con = duckdb.connect()
    con.execute(STAGING_DDL)
    return {t: con.sql(f"SELECT * FROM {t} LIMIT 0").to_arrow_table().schema for t in SILVER_TABLES}


class SilverLake:
    def __init__(self, settings: CustomerPipelineSettings):
        self.settings = settings
        properties = {"type": "glue", "warehouse": settings.silver_warehouse_uri,
                      "glue.region": settings.aws_region, "s3.region": settings.aws_region}
        self.endpoint_url = os.environ.get("AWS_ENDPOINT_URL")
        if self.endpoint_url:                                 # Arrow's S3 client does not read AWS_ENDPOINT_URL
            properties |= {"glue.endpoint": self.endpoint_url, "s3.endpoint": self.endpoint_url}
        self.catalog: Catalog = load_catalog("glue", **properties)
        self.s3 = boto3.client("s3", region_name=settings.aws_region)
        self._tables: dict[str, Table] = {}

    # --- tables --------------------------------------------------------------------------------------------------
    def create_tables(self):
        """Idempotent. Run once when the customer is onboarded."""
        try:
            self.catalog.create_namespace(self.settings.glue_database_name)
        except NamespaceAlreadyExistsError:
            pass
        schemas = {iceberg_table_name(t): s for t, s in _lossless_arrow_schemas().items()} | CANDIDATE_TABLE_SCHEMAS
        for name, schema in schemas.items():
            self.catalog.create_table_if_not_exists(f"{self.settings.glue_database_name}.{name}", schema=schema)

    def table(self, name: str) -> Table:
        """The Iceberg table for a lossless table ('edi.segment') or a candidate table, refreshed to its latest
        snapshot."""
        name = iceberg_table_name(name)
        if name not in self._tables:
            self._tables[name] = self.catalog.load_table(f"{self.settings.glue_database_name}.{name}")
        return self._tables[name].refresh()

    def append(self, name: str, rows: pa.Table, snapshot_properties: dict[str, str] | None = None):
        table = self.table(name)
        table.append(rows.cast(table.schema().as_arrow()), snapshot_properties=snapshot_properties or {})

    def read(self, name: str, columns: tuple[str, ...] = ("*",), row_filter=None) -> pa.Table:
        scan = self.table(name).scan(selected_fields=columns, **({"row_filter": row_filter} if row_filter else {}))
        return scan.to_arrow()

    def lazy_dataset(self, name: str) -> pa.dataset.Dataset | pa.Table:
        """The table's current data files as an Arrow dataset, which DuckDB scans with projection and filter
        pushdown. The lake has no delete files (deletes rewrite data files), so the data files are the table."""
        table = self.table(name)
        paths = [task.file.file_path.removeprefix("s3://") for task in table.scan().plan_files()]
        if not paths:
            return table.schema().as_arrow().empty_table()
        endpoint = urlparse(self.endpoint_url) if self.endpoint_url else None
        filesystem = pa.fs.S3FileSystem(region=self.settings.aws_region,
                                        **({"endpoint_override": endpoint.netloc, "scheme": endpoint.scheme}
                                           if endpoint else {}))
        return pa.dataset.dataset(paths, filesystem=filesystem, format="parquet")

    # --- slot export ---------------------------------------------------------------------------------------------
    def loaded_file_ids_by_sha256(self) -> dict[str, str]:
        rows = self.read(COMMIT_RECORD_TABLE, ("sha256", "file_id"))
        return dict(zip(rows["sha256"].to_pylist(), rows["file_id"].to_pylist(), strict=True))

    def export_slot(self, slot: duckdb.DuckDBPyConnection, export_id: str) -> dict:
        """Dedup, write Parquet, commit. slot holds staged rows under STAGING_DDL. Returns counts; afterwards every
        file that was in the slot is in the lake (loaded by this export, or a duplicate of a loaded file).

        The slot is left as it was found, whether the export succeeds or raises, so a failed export can simply be
        called again: its partial appends are undone through the journal first."""
        slot.execute("BEGIN")
        try:
            return self._export_slot(slot, export_id)
        finally:
            slot.execute("ROLLBACK")

    def _export_slot(self, slot: duckdb.DuckDBPyConnection, export_id: str) -> dict:
        clock = time.monotonic()
        seconds = {}

        def lap(step: str):
            nonlocal clock
            seconds[step] = round(seconds.get(step, 0) + time.monotonic() - clock, 2)
            clock = time.monotonic()
        self.recover_interrupted_exports()
        lap("recover_interrupted_exports")
        files_seen = slot.execute(f"SELECT count(*) FROM {COMMIT_RECORD_TABLE}").fetchall()[0][0]
        duplicate_file_ids = self._remove_duplicate_files(slot)
        lap("dedup")
        file_ids = [r[0] for r in slot.execute(f"SELECT file_id FROM {COMMIT_RECORD_TABLE}").fetchall()]
        result = {"export_id": export_id, "files_seen": files_seen, "files_loaded": len(file_ids),
                  "duplicate_files_skipped": len(duplicate_file_ids), "rows_by_table": {},
                  "export_step_seconds": seconds}
        if not file_ids:
            return result
        now = datetime.now(UTC).replace(tzinfo=None)
        slot.execute(f"UPDATE {COMMIT_RECORD_TABLE} SET ingest_run_id = ?, ingested_at = ?", [export_id, now])
        slot.execute(f"INSERT INTO {INGEST_RUN_TABLE} (ingest_run_id, started_at, finished_at, status, parser_name, "
                     "parser_version, schema_version, files_seen, files_loaded, files_rejected, triggered_by, notes) "
                     "VALUES (?, ?, ?, 'SUCCEEDED', 'ccsim.parser', '1', '1', ?, ?, 0, 'ecs-parser-task', ?)",
                     [export_id, now, now, files_seen, len(file_ids),
                      f"skipped {len(duplicate_file_ids)} files whose bytes are already in the lake"])
        journal_key = f"{EXPORT_JOURNAL_PREFIX}{export_id}.json"
        self.s3.put_object(Bucket=self.settings.bucket_name, Key=journal_key,
                           Body=json.dumps({"export_id": export_id, "file_ids": file_ids}).encode())
        lap("journal")
        for table in SILVER_TABLES:
            rows = slot.sql(f"SELECT * FROM {table}").to_arrow_table()
            lap("read_slot")
            if rows.num_rows:
                self.append(table, rows, {EXPORT_ID_SNAPSHOT_PROPERTY: export_id})
                result["rows_by_table"][table] = rows.num_rows
                lap("write_parquet_and_commit")
        self.s3.delete_object(Bucket=self.settings.bucket_name, Key=journal_key)
        lap("journal")
        return result

    def _remove_duplicate_files(self, slot: duckdb.DuckDBPyConnection) -> list[str]:
        """Delete from the slot every file whose bytes are already in the lake, or earlier in this slot."""
        in_lake = pa.table({"sha256": list(self.loaded_file_ids_by_sha256())})
        slot.register("sha256_in_lake", in_lake)
        duplicates = [r[0] for r in slot.execute(
            f"SELECT file_id FROM (SELECT file_id, sha256, row_number() OVER (PARTITION BY sha256 ORDER BY s3_key, "
            f"file_id) AS delivery_number FROM {COMMIT_RECORD_TABLE}) "
            "WHERE delivery_number > 1 OR sha256 IN (SELECT sha256 FROM sha256_in_lake)").fetchall()]
        slot.unregister("sha256_in_lake")
        if duplicates:
            slot.register("duplicate_file", pa.table({"file_id": duplicates}))
            for table in SILVER_TABLES:
                if table != INGEST_RUN_TABLE:
                    slot.execute(f"DELETE FROM {table} WHERE file_id IN (SELECT file_id FROM duplicate_file)")
            slot.unregister("duplicate_file")
        return duplicates

    def recover_interrupted_exports(self) -> list[str]:
        """Undo every export that left its journal behind. Returns their export ids."""
        listed = self.s3.list_objects_v2(Bucket=self.settings.bucket_name, Prefix=EXPORT_JOURNAL_PREFIX)
        recovered = []
        for journal_object in listed.get("Contents", []):
            journal = json.loads(self.s3.get_object(Bucket=self.settings.bucket_name,
                                                    Key=journal_object["Key"])["Body"].read())
            file_ids = journal["file_ids"]
            committed = self.read(COMMIT_RECORD_TABLE, ("file_id",), In("file_id", file_ids)).num_rows
            if committed != len(file_ids):                    # the commit record was never appended
                log.warning("export %s was interrupted: removing its rows", journal["export_id"])
                for table in SILVER_TABLES:
                    row_filter = (EqualTo("ingest_run_id", journal["export_id"]) if table == INGEST_RUN_TABLE
                                  else In("file_id", file_ids))
                    self.table(table).delete(row_filter)
                recovered.append(journal["export_id"])
            self.s3.delete_object(Bucket=self.settings.bucket_name, Key=journal_object["Key"])
        return recovered
