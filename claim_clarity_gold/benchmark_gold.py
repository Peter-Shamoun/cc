"""Score gold against its four criteria.

    python -m claim_clarity_gold.benchmark_gold --working-directory /tmp/gold-1 [--days 30]

Applies silver to gold one day's batch at a time (incremental_gold.py, Iceberg tables in a local catalog), then:

    1. subset      every gold relation is defined over the semantic views over silver (true by construction), and
                   the incremental build equals the full build (full_build.py), relation by relation
    2. easy        the size of the schema; the canonical questions (canonical_questions.py) all run
    3. fast        the canonical questions, and the benchmark's queries through the compatibility views
                   (benchmark_compatibility.py): how many gold covers, whether the rows are the full build's (and,
                   with --compare-with-silver, silver's), and how long they take
    4. build cost  seconds per batch early and late, compaction, sizes

Silver is parsed once per --days into --cache-directory from the mock EDI cache of the pipeline's evaluation suite.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from decimal import Decimal
from pathlib import Path

from pyiceberg.catalog.sql import SqlCatalog

from .benchmark_compatibility import create_compatibility_views
from .canonical_questions import CANONICAL_QUESTIONS
from .full_build import build_gold_from_scratch, open_gold
from .gold_relations import EVENT_RELATIONS, STATE_RELATIONS
from .incremental_gold import IncrementalGold, open_incremental_gold
from .query_workload import load_workload
from .silver_dataset import batch_table_files, build_silver_database, export_silver_batches, open_silver

MOCK_EDI_CACHE = Path.home() / ".cache" / "claim-clarity-mock-edi"
NAMESPACE = "gold"


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


def run(working_directory: Path, cache_directory: Path, days: int, compact_every_batches: int,
        compare_with_silver: bool, compare_with_full_build: bool, raw_directory: Path | None = None) -> dict:
    """raw_directory: build silver from these raw files instead of the evaluation suite's mock cache for --days."""
    data_set = f"{raw_directory.parent.parent.name}-{raw_directory.parent.name}" if raw_directory else f"{days}d"
    raw_directory = raw_directory or MOCK_EDI_CACHE / f"acme_labs-{days}d-5mb" / "raw"
    silver_path = cache_directory / f"silver-{data_set}.duckdb"
    if not silver_path.exists():
        build_silver_database(raw_directory, silver_path)
    batches_directory = cache_directory / f"silver-{data_set}-batches"
    batch_ids = (sorted(p.name.removeprefix("batch=") for p in (batches_directory / "edi.source_file").iterdir())
                 if batches_directory.exists() else export_silver_batches(silver_path, batches_directory))

    # 4. build: one batch at a time
    (working_directory / "warehouse").mkdir()
    catalog = SqlCatalog("gold", uri=f"sqlite:///{working_directory}/catalog.sqlite",
                         warehouse=f"file://{working_directory}/warehouse")
    gold = IncrementalGold(working_directory, catalog, NAMESPACE)
    batches, compactions = [], []
    with open(working_directory / "batches.jsonl", "w") as batch_log:      # kept if a later step fails
        for number, batch_id in enumerate(batch_ids, start=1):
            batches.append(gold.apply_batch(batch_id, batch_table_files(batches_directory, batch_id)))
            batch_log.write(json.dumps(batches[-1]) + "\n")
            batch_log.flush()
            if number % compact_every_batches == 0:
                compactions.append(gold.compact())
    compactions.append(gold.compact())
    gold.close()
    data_files = list((working_directory / "warehouse").rglob("*.parquet"))

    # 1. the incremental build against the full build
    silver = open_silver(silver_path)
    # the semantic views over a large silver at once: one thread, no insertion order, spill to disk
    silver.execute(f"SET memory_limit = '5GB'; SET threads = 1; SET preserve_insertion_order = false; "
                   f"SET temp_directory = '{working_directory / 'spill'}'")
    incremental = open_incremental_gold(catalog, NAMESPACE)
    differing_rows, full_build, full = {}, {}, None
    if compare_with_full_build:
        full_build = build_gold_from_scratch(silver, working_directory / "full")
        full = open_gold(working_directory / "full")
    for name in [*EVENT_RELATIONS, *STATE_RELATIONS] if compare_with_full_build else []:
        full_file = working_directory / "full" / f"{name}.parquet"
        columns = ", ".join(row[0] for row in incremental.execute(f"DESCRIBE gold.{name}").fetchall())
        differing_rows[name] = incremental.execute(
            f"SELECT (SELECT count(*) FROM (SELECT {columns} FROM gold.{name} EXCEPT ALL SELECT {columns} FROM '{full_file}'))"
            f" + (SELECT count(*) FROM (SELECT {columns} FROM '{full_file}' EXCEPT ALL SELECT {columns} FROM gold.{name}))"
        ).fetchall()[0][0]

    # 2 and 3. the canonical questions
    as_of = incremental.execute("SELECT max(payment_date) FROM gold.payments").fetchall()[0][0]
    canonical = {}
    for name, sql in CANONICAL_QUESTIONS.items():
        rows, seconds = timed(incremental, sql.format(as_of=as_of), repeats=3)
        canonical[name] = {"seconds": round(seconds, 4), "rows": len(rows)}
    # 3. the benchmark's queries, through the compatibility views
    view_columns = create_compatibility_views(incremental)
    if full is not None:
        create_compatibility_views(full)
    available = {(relation, column) for relation, columns in view_columns.items() for column in columns}
    workload = load_workload(silver)
    covered = [query for query in workload if query.columns_read <= available]
    benchmark_seconds, other_rows_than_full_build, other_rows_than_silver = [], [], []
    for query in covered:
        sql = query.sql.replace("sem.", "benchmark.")
        rows, seconds = timed(incremental, sql, repeats=3)
        benchmark_seconds.append(seconds)
        if full is not None and comparable_rows(rows) != comparable_rows(full.execute(sql).fetchall()):
            other_rows_than_full_build.append(query.query_id)
        if compare_with_silver and comparable_rows(rows) != comparable_rows(silver.execute(query.sql).fetchall()):
            other_rows_than_silver.append(query.query_id)

    third = max(1, len(batches) // 3)
    column_count = sum(len(incremental.execute(f"DESCRIBE gold.{name}").fetchall())
                       for name in [*EVENT_RELATIONS, *STATE_RELATIONS])
    scorecard = {
        "silver_days": days, "batches": len(batches),
        "1_subset": {"incremental_equals_full_build": (not any(differing_rows.values())) if compare_with_full_build
                     else "not checked",
                     "differing_rows": differing_rows},
        "2_easy": {"relations": len(EVENT_RELATIONS) + len(STATE_RELATIONS), "columns": column_count,
                   "canonical_questions": len(canonical)},
        "3_fast": {
            "canonical_question_seconds": canonical,
            "canonical_seconds_median": statistics.median(q["seconds"] for q in canonical.values()),
            "canonical_seconds_max": max(q["seconds"] for q in canonical.values()),
            "benchmark_queries": len(workload), "benchmark_queries_gold_covers": len(covered),
            "benchmark_queries_left_to_silver": sorted(q.query_id for q in workload if q not in covered),
            "benchmark_queries_with_other_rows_than_full_build": other_rows_than_full_build,
            "benchmark_queries_with_other_rows_than_silver": other_rows_than_silver if compare_with_silver else None,
            "benchmark_seconds_median": round(statistics.median(benchmark_seconds), 4),
            "benchmark_seconds_total": round(sum(benchmark_seconds), 2)},
        "4_build_cost": {
            "seconds_per_batch_first_third": round(statistics.mean(b["seconds_total"] for b in batches[:third]), 3),
            "seconds_per_batch_last_third": round(statistics.mean(b["seconds_total"] for b in batches[-third:]), 3),
            "seconds_all_batches": round(sum(b["seconds_total"] for b in batches), 1),
            "step_seconds_first_third": {step: round(statistics.mean(b["seconds"][step] for b in batches[:third]), 3)
                                         for step in batches[0]["seconds"]},
            "step_seconds_last_third": {step: round(statistics.mean(b["seconds"][step] for b in batches[-third:]), 3)
                                        for step in batches[0]["seconds"]},
            "full_build_seconds": round(sum(relation["seconds"] for relation in full_build.values()), 1) or None,
            "compact_every_batches": compact_every_batches, "compactions": len(compactions),
            "compaction_seconds_total": round(sum(c["seconds"] for c in compactions), 1),
            "compaction_seconds_max": max(c["seconds"] for c in compactions),
            "data_files": len(data_files),
            "data_megabytes": round(sum(f.stat().st_size for f in data_files) / 1e6, 1),
            "full_build_megabytes": round(sum(relation["bytes"] for relation in full_build.values()) / 1e6, 1) or None,
            "key_index_megabytes": round((working_directory / "_key_index.sqlite").stat().st_size / 1e6, 1)},
        "per_batch": batches,
    }
    (working_directory / "gold_scorecard.json").write_text(json.dumps(scorecard, indent=2, default=str))
    return scorecard


def main():
    arguments = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    arguments.add_argument("--working-directory", type=Path, required=True)
    arguments.add_argument("--cache-directory", type=Path, default=Path.home() / ".cache" / "claim-clarity-gold")
    arguments.add_argument("--days", type=int, default=30)
    arguments.add_argument("--compact-every-batches", type=int, default=30)
    arguments.add_argument("--compare-with-silver", action="store_true",
                           help="also run each benchmark query on the semantic views over silver (minutes per "
                                "query on a large silver)")
    arguments.add_argument("--raw-directory", type=Path,
                           help="a tenant's raw/ directory written by ccsim.edi_mock (instead of --days' mock cache)")
    arguments.add_argument("--skip-full-build", action="store_true",
                           help="do not build gold from scratch to compare with (about 15 minutes and 5 GB on a year)")
    options = arguments.parse_args()
    options.working_directory.mkdir(parents=True, exist_ok=False)
    scorecard = run(options.working_directory, options.cache_directory, options.days, options.compact_every_batches,
                    options.compare_with_silver, not options.skip_full_build, options.raw_directory)
    print(json.dumps({k: v for k, v in scorecard.items() if k != "per_batch"}, indent=2, default=str))


if __name__ == "__main__":
    main()
