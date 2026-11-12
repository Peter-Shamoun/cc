"""Criterion 4: does keeping gold current cost the same however large silver already is?

    python -m claim_clarity_gold.benchmark_incremental --working-directory /tmp/gold-inc-1 [--days 30]

Applies silver to gold one day's batch at a time (incremental_build.py) and reports the seconds each batch took
against how much gold already held. Then checks the result against a full rebuild, relation by relation, and runs
the workload on it.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import date
from pathlib import Path

from .benchmark_gold import MOCK_EDI_CACHE, comparable_rows, timed
from .build_gold import build_gold_from_scratch, open_gold
from .gold_schema import GOLD_RELATIONS
from .incremental_build import IncrementalGold, open_incremental_gold
from .query_workload import load_workload
from .silver_dataset import batch_table_files, build_silver_database, export_silver_batches, open_silver


def run(working_directory: Path, cache_directory: Path, days: int, compact_after_days: int) -> dict:
    silver_path = cache_directory / f"silver-{days}d.duckdb"
    if not silver_path.exists():
        build_silver_database(MOCK_EDI_CACHE / f"acme_labs-{days}d-5mb" / "raw", silver_path)
    batches_directory = cache_directory / f"silver-{days}d-batches"
    batch_ids = (sorted(p.name.removeprefix("batch=") for p in (batches_directory / "edi.source_file").iterdir())
                 if batches_directory.exists() else export_silver_batches(silver_path, batches_directory))

    gold = IncrementalGold(working_directory / "gold-incremental")
    batches, compactions, gold_rows_so_far, uncompacted = [], [], 0, []
    demoted_rows_by_age_days: dict[int, int] = {}
    for batch_id in batch_ids:                            # a batch id is the day its files were received
        result = gold.apply_batch(batch_id, batch_table_files(batches_directory, batch_id))
        result["gold_rows_before"] = gold_rows_so_far
        gold_rows_so_far += result["gold_rows_added"]
        for older_batch_id, count in result.pop("older_rows_demoted_by_batch").items():
            age = (date.fromisoformat(batch_id) - date.fromisoformat(older_batch_id)).days
            demoted_rows_by_age_days[age] = demoted_rows_by_age_days.get(age, 0) + count
        batches.append(result)
        uncompacted.append(batch_id)
        # a calendar month is compacted once its last day is compact_after_days old
        months = sorted({b[:7] for b in uncompacted})
        for month in months:
            of_month = [b for b in uncompacted if b[:7] == month]
            newer_exists = any(b[:7] > month for b in uncompacted)
            if newer_exists and (date.fromisoformat(batch_id) - date.fromisoformat(of_month[-1])).days >= compact_after_days:
                compactions.append(gold.compact(of_month, month))
                uncompacted = [b for b in uncompacted if b[:7] != month]

    silver = open_silver(silver_path)
    silver.execute("SET memory_limit = '8GB'")
    started = time.monotonic()
    build_gold_from_scratch(silver, working_directory / "gold-full")
    full_build_seconds = round(time.monotonic() - started, 2)
    incremental, full = open_incremental_gold(working_directory / "gold-incremental"), open_gold(working_directory / "gold-full")
    incremental.execute(f"ATTACH '{working_directory / 'compare.duckdb'}' AS compare")
    differing_rows = {}
    for name in GOLD_RELATIONS:
        full_file = working_directory / "gold-full" / f"{name}.parquet"
        differing_rows[name] = incremental.execute(
            f"SELECT (SELECT count(*) FROM (SELECT * FROM gold.{name} EXCEPT ALL SELECT * FROM '{full_file}')) + "
            f"(SELECT count(*) FROM (SELECT * FROM '{full_file}' EXCEPT ALL SELECT * FROM gold.{name}))").fetchall()[0][0]

    workload = load_workload(silver)
    gold_columns = {(name, column) for name, relation in GOLD_RELATIONS.items() for column in relation.columns}
    query_seconds, other_rows = [], []
    for query in (q for q in workload if q.columns_read <= gold_columns):
        sql = query.sql.replace("sem.", "gold.")
        rows, seconds = timed(incremental, sql, repeats=3)
        query_seconds.append(seconds)
        if comparable_rows(rows) != comparable_rows(full.execute(sql).fetchall()):
            other_rows.append(query.query_id)

    third = max(1, len(batches) // 3)
    per_thousand = [1000 * b["seconds_total"] / b["gold_rows_added"] for b in batches if b["gold_rows_added"]]
    scorecard = {
        "silver_days": days, "batches": len(batches), "gold_rows": gold_rows_so_far,
        "4_build_cost": {
            "incremental_equals_full_rebuild": not any(differing_rows.values()), "differing_rows": differing_rows,
            "seconds_per_batch_first_third": round(statistics.mean(b["seconds_total"] for b in batches[:third]), 3),
            "seconds_per_batch_last_third": round(statistics.mean(b["seconds_total"] for b in batches[-third:]), 3),
            "seconds_per_1000_gold_rows_first_third": round(statistics.mean(per_thousand[:third]), 4),
            "seconds_per_1000_gold_rows_last_third": round(statistics.mean(per_thousand[-third:]), 4),
            "seconds_all_batches": round(sum(b["seconds_total"] for b in batches), 2),
            "full_rebuild_seconds": full_build_seconds,
            "older_rows_demoted": sum(b["older_rows_demoted"] for b in batches),
            "older_files_rewritten": sum(b["older_files_rewritten"] for b in batches),
            "megabytes_written_for_new_rows": round(sum(b["batch_bytes_written"] for b in batches) / 1e6, 1),
            "megabytes_rewritten_to_demote_older_rows": round(sum(b["older_bytes_rewritten"] for b in batches) / 1e6, 1),
            "share_of_demoted_rows_within_days": {
                limit: round(sum(n for age, n in demoted_rows_by_age_days.items() if age <= limit)
                             / max(1, sum(demoted_rows_by_age_days.values())), 3) for limit in (7, 14, 30, 45, 60, 90)},
            "compact_after_days": compact_after_days, "compactions": len(compactions),
            "compaction_seconds": round(sum(c["seconds"] for c in compactions), 2),
            "megabytes_written_by_compaction": round(sum(c["bytes_written"] for c in compactions) / 1e6, 1),
            "step_seconds": {step: round(sum(b["seconds"][step] for b in batches), 2) for step in batches[0]["seconds"]},
        },
        "3_fast": {"queries": len(query_seconds), "queries_with_other_rows_than_full_rebuild": other_rows,
                   "gold_seconds_median": round(statistics.median(query_seconds), 4),
                   "gold_seconds_total": round(sum(query_seconds), 2),
                   "gold_files": sum(1 for _ in (working_directory / "gold-incremental").rglob("*.parquet"))},
        "per_batch": batches,
    }
    (working_directory / "incremental_scorecard.json").write_text(json.dumps(scorecard, indent=2, default=str))
    return scorecard


def main():
    arguments = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    arguments.add_argument("--working-directory", type=Path, required=True)
    arguments.add_argument("--cache-directory", type=Path, default=Path.home() / ".cache" / "claim-clarity-gold")
    arguments.add_argument("--days", type=int, default=30)
    arguments.add_argument("--compact-after-days", type=int, default=45,
                           help="merge a calendar month's batch files once its last day is this old")
    options = arguments.parse_args()
    options.working_directory.mkdir(parents=True, exist_ok=False)
    scorecard = run(options.working_directory, options.cache_directory, options.days, options.compact_after_days)
    print(json.dumps({k: v for k, v in scorecard.items() if k != "per_batch"}, indent=2, default=str))
    print([(b["batch_id"], b["seconds_total"], b["gold_rows_added"], b["older_rows_demoted"]) for b in scorecard["per_batch"]])


if __name__ == "__main__":
    main()
