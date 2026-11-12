"""A silver data set to build gold from: the lossless tables in a DuckDB file, parsed from mock EDI.

Gold is developed apart from the ingestion pipeline, so its silver is built here directly with the parser.
The file also holds ref.* (seeded) and the semantic views, which define what each gold relation means.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import duckdb

from ccsim.pipeline import DDL_SEMANTIC, seed_ref
from ccsim.silver_staging import RawFile, SilverBatch, file_id_of


def build_silver_database(raw_directory: Path, database_path: Path, parser_process_count: int = 4) -> dict:
    """Parse every file under raw_directory (received_date=YYYY-MM-DD/<kind>/<file>) into database_path."""
    def raw_files():
        for path in sorted(p for p in raw_directory.rglob("*") if p.is_file()):
            body = path.read_bytes()
            received_date = path.parent.parent.name.removeprefix("received_date=")
            key = path.relative_to(raw_directory).as_posix()
            yield RawFile(key, body, file_id_of(key, None), dict(
                s3_uri=key, s3_bucket="mock", s3_key=key, size_bytes=len(body),
                sha256=hashlib.sha256(body).hexdigest(), received_at=datetime.fromisoformat(received_date)))

    batch = SilverBatch("silver_dataset", datetime(2026, 1, 1), triggered_by="claim_clarity_gold", notes=None,
                        parser_version="1", workers=parser_process_count)
    known_sha256: dict[str, str] = {}
    loaded = sum(was_loaded for _, was_loaded in batch.add_raw_files(raw_files(), "acme_labs", known_sha256))
    staged = batch.finish()
    staged.execute("SET memory_limit = '8GB'")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    staged.execute(f"ATTACH '{database_path}' AS silver_file")
    for schema, table in staged.execute("SELECT schema_name, table_name FROM duckdb_tables() "
                                        "WHERE database_name = 'memory'").fetchall():
        staged.execute(f"CREATE SCHEMA IF NOT EXISTS silver_file.{schema}")
        staged.execute(f"CREATE TABLE silver_file.{schema}.{table} AS SELECT * FROM memory.{schema}.{table}")
    staged.execute("DETACH silver_file")
    silver = duckdb.connect(str(database_path))
    seed_ref(silver)
    silver.execute(DDL_SEMANTIC)
    silver.close()
    return {"files_loaded": loaded}


def open_silver(database_path: Path, read_only: bool = True) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(database_path), read_only=read_only)
