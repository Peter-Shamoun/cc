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
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import boto3
import duckdb
import pyarrow as pa
import pyarrow.dataset
import pyarrow.fs
import pyarrow.parquet as pq
from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pyiceberg.expressions import EqualTo, In
from pyiceberg.io.pyarrow import _pyarrow_to_schema_without_ids, schema_to_pyarrow
from pyiceberg.schema import assign_fresh_schema_ids
from pyiceberg.table import Table

from ccsim.parser import TABLES as PARSED_TABLES
from ccsim.silver_staging import STAGING_DDL

from .customer_settings import SILVER_PREFIX, CustomerPipelineSettings

log = logging.getLogger(__name__)

COMMIT_RECORD_TABLE = "edi.source_file"
SEGMENT_TABLE = "edi.segment"
INGEST_RUN_TABLE = "edi.ingest_run"
# Append order: the commit record last.
SILVER_TABLES = [INGEST_RUN_TABLE] + [t for t in PARSED_TABLES if t != COMMIT_RECORD_TABLE] + [COMMIT_RECORD_TABLE]
EXPORT_JOURNAL_PREFIX = f"{SILVER_PREFIX}/_export_journal/"
EXPORT_ID_SNAPSHOT_PROPERTY = "claim-clarity.export-id"
PARQUET_ROW_GROUP_BYTES = 128 * 1024 ** 2                    # uncompressed rows gathered before a row group is written

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


def _lossless_schemas() -> tuple[dict[str, pa.Schema], dict[str, list[str]]]:
    """Per lossless table: its Arrow schema, and the columns the DDL declares NOT NULL."""
    con = duckdb.connect()
    con.execute(STAGING_DDL)
    # In the Arrow types Iceberg reads and writes (large_string, ...): a slot's .arrow files are then appended to
    # the lake as they are. A cast from string to large_string copies every value into memory.
    schemas = {t: schema_to_pyarrow(assign_fresh_schema_ids(_pyarrow_to_schema_without_ids(
        con.sql(f"SELECT * FROM {t} LIMIT 0").to_arrow_table().schema)), include_field_ids=False)
        for t in SILVER_TABLES}
    required = {t: [] for t in SILVER_TABLES}
    for table, column in con.execute("SELECT table_schema || '.' || table_name, column_name FROM "
                                     "information_schema.columns WHERE is_nullable = 'NO'").fetchall():
        if table in required:
            required[table].append(column)
    required[SEGMENT_TABLE] += ["raw_text", "separator_after"]      # nullable in the DDL for the older pipeline
    required[COMMIT_RECORD_TABLE] += ["leading_bytes"]
    return schemas, required


LOSSLESS_ARROW_SCHEMAS, REQUIRED_COLUMNS = _lossless_schemas()


def conform_to_lossless_schema(table: str, rows: pa.Table) -> pa.Table:
    """rows with the table's columns in DDL order and types, matched by name; a column rows lacks is NULL, and
    a column the table lacks is an error."""
    schema = LOSSLESS_ARROW_SCHEMAS[table]
    unknown = set(rows.schema.names) - set(schema.names)
    if unknown:
        raise ValueError(f"{table}: no such column(s) {sorted(unknown)}")
    def column(field: pa.Field):
        if field.name not in rows.schema.names:
            return pa.nulls(rows.num_rows, field.type)
        values = rows[field.name]
        if (pa.types.is_list(field.type) or pa.types.is_large_list(field.type)) and pa.types.is_string(values.type):
            # the parser writes lists as JSON
            return pa.array([None if v is None else json.loads(v) for v in values.to_pylist()], field.type)
        return values.cast(field.type)
    return pa.table([column(field) for field in schema], schema=schema)


def read_arrow_file(path: Path) -> pa.Table:
    with pa.memory_map(str(path)) as source:
        return pa.ipc.open_file(source).read_all()


def arrow_file_batches(paths: list[Path]):
    """The record batches of the files, one in memory at a time."""
    for path in paths:
        with pa.memory_map(str(path)) as source:
            reader = pa.ipc.open_file(source)
            for number in range(reader.num_record_batches):
                yield reader.get_batch(number)


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
        endpoint = urlparse(self.endpoint_url) if self.endpoint_url else None
        self.filesystem = pa.fs.S3FileSystem(region=settings.aws_region, **(
            {"endpoint_override": endpoint.netloc, "scheme": endpoint.scheme} if endpoint else {}))
        self._tables: dict[str, Table] = {}

    # --- tables --------------------------------------------------------------------------------------------------
    def create_tables(self):
        """Idempotent. Run once when the customer is onboarded."""
        try:
            self.catalog.create_namespace(self.settings.glue_database_name)
        except NamespaceAlreadyExistsError:
            pass
        schemas = {iceberg_table_name(t): s for t, s in LOSSLESS_ARROW_SCHEMAS.items()} | CANDIDATE_TABLE_SCHEMAS
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
        lake_schema = table.schema().as_arrow()
        if not rows.schema.equals(lake_schema, check_metadata=False):
            rows = rows.cast(lake_schema)
        table.append(rows, snapshot_properties=snapshot_properties or {})

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
        return pa.dataset.dataset(paths, filesystem=self.filesystem, format="parquet")

    # --- slot export ---------------------------------------------------------------------------------------------
    def loaded_file_ids_by_sha256(self) -> dict[str, str]:
        rows = self.read(COMMIT_RECORD_TABLE, ("sha256", "file_id"))
        return dict(zip(rows["sha256"].to_pylist(), rows["file_id"].to_pylist(), strict=True))

    def export_slot(self, parsed_directories: list[Path], export_id: str) -> dict:
        """Dedup, write Parquet, commit. A slot is the parsed directories of its files: one .arrow file per lossless
        table, already in the lake's column types. Returns counts; afterwards every file of the slot is in the lake
        (loaded by this export, or a duplicate of a loaded file).

        Nothing in the slot is changed, so a failed export can simply be called again: its partial appends are
        undone through the journal first."""
        clock = time.monotonic()
        seconds = {}

        def lap(step: str):
            nonlocal clock
            seconds[step] = round(seconds.get(step, 0) + time.monotonic() - clock, 2)
            clock = time.monotonic()
        self.recover_interrupted_exports()
        lap("recover_interrupted_exports")
        commit_records = {directory: read_arrow_file(directory / f"{COMMIT_RECORD_TABLE}.arrow")
                          for directory in parsed_directories}
        # Skip every file whose bytes are already in the lake, or earlier in this slot (by S3 key).
        sha256_seen = set(self.loaded_file_ids_by_sha256())
        kept = []
        for directory in sorted(parsed_directories, key=lambda d: commit_records[d]["s3_key"][0].as_py()):
            sha256 = commit_records[directory]["sha256"][0].as_py()
            if sha256 not in sha256_seen:
                sha256_seen.add(sha256)
                kept.append(directory)
        lap("dedup")
        result = {"export_id": export_id, "files_seen": len(parsed_directories), "files_loaded": len(kept),
                  "duplicate_files_skipped": len(parsed_directories) - len(kept), "rows_by_table": {},
                  "export_step_seconds": seconds}
        if not kept:
            return result
        now = datetime.now(UTC).replace(tzinfo=None)
        file_ids = [commit_records[directory]["file_id"][0].as_py() for directory in kept]
        journal_key = f"{EXPORT_JOURNAL_PREFIX}{export_id}.json"
        self.s3.put_object(Bucket=self.settings.bucket_name, Key=journal_key,
                           Body=json.dumps({"export_id": export_id, "file_ids": file_ids}).encode())
        lap("journal")
        def append_table(table: str) -> tuple[str, int]:
            if table == INGEST_RUN_TABLE:
                rows = pa.Table.from_pylist([dict(
                    ingest_run_id=export_id, started_at=now, finished_at=now, status="SUCCEEDED",
                    parser_name="ccsim.parser", parser_version="1", schema_version="1",
                    files_seen=len(parsed_directories), files_loaded=len(kept), files_rejected=0,
                    triggered_by="ecs-parser-task",
                    notes=f"skipped {len(parsed_directories) - len(kept)} files whose bytes are already in the lake")],
                    schema=LOSSLESS_ARROW_SCHEMAS[INGEST_RUN_TABLE])
                self.append(table, rows, {EXPORT_ID_SNAPSHOT_PROPERTY: export_id})
                return table, rows.num_rows
            if table == COMMIT_RECORD_TABLE:                  # small: one row per file, with the run that committed it
                rows = pa.concat_tables(commit_records[directory] for directory in kept)
                rows = rows.set_column(rows.schema.get_field_index("ingest_run_id"), "ingest_run_id",
                                       pa.array([export_id] * rows.num_rows, rows.schema.field("ingest_run_id").type))
                rows = rows.set_column(rows.schema.get_field_index("ingested_at"), "ingested_at",
                                       pa.array([now] * rows.num_rows, pa.timestamp("us")))
                self.append(table, rows, {EXPORT_ID_SNAPSHOT_PROPERTY: export_id})
                return table, rows.num_rows
            paths = [path for directory in kept if (path := directory / f"{table}.arrow").exists()]
            if not paths:
                return table, 0
            # Stream the slot's .arrow files into one Parquet file in the lake, a row group at a time: the
            # export's memory does not grow with the slot. Then one Iceberg commit adds the file to the table.
            iceberg_table = self.table(table)
            data_file_uri = f"{iceberg_table.location()}/data/{export_id}.parquet"
            row_count = 0
            with pq.ParquetWriter(data_file_uri.removeprefix("s3://"), LOSSLESS_ARROW_SCHEMAS[table],
                                  filesystem=self.filesystem, compression="zstd") as writer:
                row_group, row_group_bytes = [], 0
                for batch in arrow_file_batches(paths):
                    row_group.append(batch)
                    row_group_bytes += batch.nbytes
                    row_count += batch.num_rows
                    if row_group_bytes >= PARQUET_ROW_GROUP_BYTES:
                        writer.write_table(pa.Table.from_batches(row_group))
                        row_group, row_group_bytes = [], 0
                if row_group:
                    writer.write_table(pa.Table.from_batches(row_group))
            iceberg_table.add_files([data_file_uri], snapshot_properties={EXPORT_ID_SNAPSHOT_PROPERTY: export_id})
            return table, row_count

        # The tables are independent Iceberg tables: their appends run side by side. The commit record goes last,
        # alone, once every other table has committed.
        with ThreadPoolExecutor(self.settings.export_table_thread_count) as pool:
            appended = list(pool.map(append_table, [t for t in SILVER_TABLES if t != COMMIT_RECORD_TABLE]))
        lap("write_parquet_and_commit_tables")
        appended.append(append_table(COMMIT_RECORD_TABLE))
        lap("write_parquet_and_commit_record")
        result["rows_by_table"] = {table: rows for table, rows in appended if rows}
        self.s3.delete_object(Bucket=self.settings.bucket_name, Key=journal_key)
        lap("journal")
        return result

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
