"""Gold v2 built from scratch: the definition the incremental build has to equal. One Parquet file per relation."""
from __future__ import annotations

import time
from pathlib import Path

import duckdb

from .gold_relations import (CLAIM_PAYERS_SQL, CLAIMS_SQL, EVENT_RELATIONS, STATE_RELATIONS, STATE_SORT_COLUMNS)


def build_gold_from_scratch(silver: duckdb.DuckDBPyConnection, gold_directory: Path) -> dict:
    """silver has the lossless tables and the semantic views. Returns seconds, bytes and rows per relation."""
    gold_directory.mkdir(parents=True, exist_ok=True)
    report = {}

    def write(name: str, select_sql: str, sort_columns: str):
        started = time.monotonic()
        path = gold_directory / f"{name}.parquet"
        silver.execute(f"COPY (SELECT * FROM ({select_sql}) ORDER BY {sort_columns}) "
                       f"TO '{path}' (FORMAT parquet, COMPRESSION zstd)")
        report[name] = {"seconds": round(time.monotonic() - started, 2), "bytes": path.stat().st_size,
                        "rows": silver.execute(f"SELECT count(*) FROM '{path}'").fetchall()[0][0]}

    for name, relation in EVENT_RELATIONS.items():
        write(name, f"SELECT {', '.join(relation.columns)} FROM sem.{relation.semantic_relation}",
              relation.sort_columns)
    claim_payers_file = f"'{gold_directory / 'claim_payers.parquet'}'"
    write("claim_payers", CLAIM_PAYERS_SQL.format(
              claims="sem.claims", claim_acknowledgments="sem.claim_acknowledgments", remit_claims="sem.remit_claims"),
          STATE_SORT_COLUMNS["claim_payers"])
    write("claims", CLAIMS_SQL.format(latest_submissions="(SELECT * FROM sem.claims WHERE is_latest_submission)",
                                   claim_payers=claim_payers_file),
          STATE_SORT_COLUMNS["claims"])
    return report


def open_gold(gold_directory: Path) -> duckdb.DuckDBPyConnection:
    """A DuckDB session with every gold relation as gold.<name>."""
    con = duckdb.connect()
    con.execute("CREATE SCHEMA gold")
    for name in [*EVENT_RELATIONS, *STATE_RELATIONS]:
        con.execute(f"CREATE VIEW gold.{name} AS SELECT * FROM '{gold_directory / (name + '.parquet')}'")
    return con
