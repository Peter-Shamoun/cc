"""Build gold from silver.

Full build: every gold relation is its semantic view's rows, restricted to the gold columns, sorted, in one Parquet
file. Its cost grows with silver; the incremental build that does not is the next step of this work.
"""
from __future__ import annotations

import time
from pathlib import Path

import duckdb

from .gold_schema import GOLD_RELATIONS


def build_gold_from_scratch(silver: duckdb.DuckDBPyConnection, gold_directory: Path) -> dict:
    """silver has the lossless tables and the semantic views. Returns seconds and bytes per relation."""
    gold_directory.mkdir(parents=True, exist_ok=True)
    report = {}
    for name, relation in GOLD_RELATIONS.items():
        started = time.monotonic()
        path = gold_directory / f"{name}.parquet"
        silver.execute(f"COPY (SELECT {', '.join(relation.columns)} FROM sem.{name} ORDER BY {relation.sort_columns}) "
                       f"TO '{path}' (FORMAT parquet, COMPRESSION zstd)")
        report[name] = {"seconds": round(time.monotonic() - started, 2), "bytes": path.stat().st_size,
                        "rows": silver.execute(f"SELECT count(*) FROM '{path}'").fetchall()[0][0]}
    return report


def open_gold(gold_directory: Path) -> duckdb.DuckDBPyConnection:
    """A DuckDB session with every gold relation as gold.<name>."""
    con = duckdb.connect()
    con.execute("CREATE SCHEMA gold")
    for name in GOLD_RELATIONS:
        con.execute(f"CREATE VIEW gold.{name} AS SELECT * FROM '{gold_directory / (name + '.parquet')}'")
    return con
