"""Score a gold build against the four criteria.

    python -m claim_clarity_gold.benchmark_gold --working-directory /tmp/gold-1 [--days 30]

    1. subset      every gold column comes from a semantic view over silver (true by construction of the build),
                   and the share of workload queries gold can answer (all the columns the query reads are in gold)
    2. easy        the size of the schema: relations and columns, against silver and the semantic layer
    3. fast        each covered query on gold against the same query on the semantic views over silver, and
                   whether both return the same rows
    4. build cost  seconds to build gold, and gold's size against silver's

Silver is parsed once per --days into --cache-directory from the mock EDI cache of the pipeline's evaluation suite.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from decimal import Decimal
from pathlib import Path

from ccsim.parser import TABLES as SILVER_TABLES

from .build_gold import build_gold_from_scratch, open_gold
from .gold_schema import GOLD_RELATIONS
from .query_workload import column_usage, load_workload, semantic_relation_columns
from .silver_dataset import build_silver_database, open_silver

MOCK_EDI_CACHE = Path.home() / ".cache" / "claim-clarity-mock-edi"


def comparable_rows(rows: list[tuple]) -> list[str]:
    def comparable(value):
        if isinstance(value, (float, Decimal)):
            return round(float(value), 6)
        return value
    return sorted(repr(tuple(comparable(value) for value in row)) for row in rows)


def timed(con, sql: str, repeats: int) -> tuple[list[tuple], float]:
    seconds = []
    for _ in range(repeats):
        started = time.monotonic()
        rows = con.execute(sql).fetchall()
        seconds.append(time.monotonic() - started)
    return rows, min(seconds)


def run(working_directory: Path, cache_directory: Path, days: int) -> dict:
    silver_path = cache_directory / f"silver-{days}d.duckdb"
    if not silver_path.exists():
        build_silver_database(MOCK_EDI_CACHE / f"acme_labs-{days}d-5mb" / "raw", silver_path)
    silver = open_silver(silver_path)
    silver.execute("SET memory_limit = '8GB'")
    workload = load_workload(silver)
    gold_columns = {(name, column) for name, relation in GOLD_RELATIONS.items() for column in relation.columns}
    semantic_columns = semantic_relation_columns(silver)

    build = build_gold_from_scratch(silver, working_directory / "gold")
    gold = open_gold(working_directory / "gold")
    silver_parquet_bytes = 0
    for table in SILVER_TABLES:                           # silver's size the same way gold's is measured
        path = working_directory / "silver-size" / f"{table}.parquet"
        path.parent.mkdir(exist_ok=True)
        silver.execute(f"COPY (SELECT * FROM {table}) TO '{path}' (FORMAT parquet, COMPRESSION zstd)")
        silver_parquet_bytes += path.stat().st_size
    gold_bytes = sum(r["bytes"] for r in build.values())

    covered = [query for query in workload if query.columns_read <= gold_columns]
    per_query, wrong = [], []
    for query in covered:
        gold_rows, gold_seconds = timed(gold, query.sql.replace("sem.", "gold."), repeats=3)
        silver_rows, silver_seconds = timed(silver, query.sql, repeats=1)
        same = comparable_rows(gold_rows) == comparable_rows(silver_rows)
        if not same:
            wrong.append(query.query_id)
        per_query.append({"query_id": query.query_id, "category": query.category, "same_rows": same,
                          "gold_seconds": round(gold_seconds, 4), "silver_seconds": round(silver_seconds, 4)})
    gold_times = sorted(q["gold_seconds"] for q in per_query)
    silver_times = sorted(q["silver_seconds"] for q in per_query)

    def percentile(values, fraction):
        return values[min(len(values) - 1, int(fraction * len(values)))]
    usage = column_usage(workload)
    scorecard = {
        "silver_days": days,
        "1_subset": {
            "every_gold_column_is_a_semantic_view_column_over_silver": all(
                column in semantic_columns[name] for name, column in gold_columns),
            "workload_queries": len(workload), "queries_gold_can_answer": len(covered),
            "share_of_queries_gold_can_answer": round(len(covered) / len(workload), 3),
            "queries_left_to_silver": sorted(q.query_id for q in workload if q not in covered),
        },
        "2_easy": {
            "gold_relations": len(GOLD_RELATIONS), "gold_columns": len(gold_columns),
            "semantic_layer_relations": len(semantic_columns),
            "semantic_layer_columns": sum(len(c) for c in semantic_columns.values()),
            "silver_tables": len(SILVER_TABLES),
            "gold_columns_no_query_reads": sorted(f"{n}.{c}" for n, c in gold_columns if not usage.get((n, c))),
        },
        "3_fast": {
            "covered_queries_with_the_same_rows_as_silver": len(covered) - len(wrong), "queries_with_other_rows": wrong,
            "gold_seconds_median": statistics.median(gold_times), "gold_seconds_p95": percentile(gold_times, 0.95),
            "gold_seconds_total": round(sum(gold_times), 2),
            "silver_views_seconds_median": statistics.median(silver_times),
            "silver_views_seconds_p95": percentile(silver_times, 0.95),
            "silver_views_seconds_total": round(sum(silver_times), 2),
        },
        "4_build_cost": {
            "full_build_seconds": round(sum(r["seconds"] for r in build.values()), 2),
            "gold_megabytes": round(gold_bytes / 1e6, 2), "silver_megabytes": round(silver_parquet_bytes / 1e6, 2),
            "gold_share_of_silver_size": round(gold_bytes / silver_parquet_bytes, 3),
            "per_relation": build,
        },
        "per_query": per_query,
    }
    (working_directory / "gold_scorecard.json").write_text(json.dumps(scorecard, indent=2, default=str))
    return scorecard


def main():
    arguments = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    arguments.add_argument("--working-directory", type=Path, required=True)
    arguments.add_argument("--cache-directory", type=Path, default=Path.home() / ".cache" / "claim-clarity-gold")
    arguments.add_argument("--days", type=int, default=30)
    options = arguments.parse_args()
    options.working_directory.mkdir(parents=True, exist_ok=False)
    scorecard = run(options.working_directory, options.cache_directory, options.days)
    print(json.dumps({k: v for k, v in scorecard.items() if k != "per_query"}, indent=2, default=str))


if __name__ == "__main__":
    main()
