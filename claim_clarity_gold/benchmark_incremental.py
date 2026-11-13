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
from pathlib import Path

from .benchmark_gold import MOCK_EDI_CACHE, comparable_rows, timed
from .build_gold import build_gold_from_scratch, open_gold
from .gold_schema import GOLD_RELATIONS
from .incremental_build import IncrementalGold, open_incremental_gold
from .query_workload import load_workload
from .silver_dataset import batch_table_files, build_silver_database, export_silver_batches, open_silver


def run(working_directory: Path, cache_directory: Path, days: int, compact_every_batches: int) -> dict:
    silver_path = cache_directory / f"silver-{days}d.duckdb"
    if not silver_path.exists():
        build_silver_database(MOCK_EDI_CACHE / f"acme_labs-{days}d-5mb" / "raw", silver_path)
    batches_directory = cache_directory / f"silver-{days}d-batches"
    batch_ids = (sorted(p.name.removeprefix("batch=") for p in (batches_directory / "edi.source_file").iterdir())
                 if batches_directory.exists() else export_silver_batches(silver_path, batches_directory))

    gold = IncrementalGold(working_directory / "gold-incremental")
    batches, compactions, gold_rows_so_far = [], [], 0
    for number, batch_id in enumerate(batch_ids, start=1):
        result = gold.apply_batch(batch_id, batch_table_files(batches_directory, batch_id))
        result["gold_rows_before"] = gold_rows_so_far
        gold_rows_so_far += result["gold_rows_added"]
        batches.append(result)
        if number % compact_every_batches == 0:
            compactions.append(gold.compact() | {"after_batch": batch_id})
    storage_before_final_compaction = gold.storage()
    compactions.append(gold.compact() | {"after_batch": batch_ids[-1]})
    storage = gold.storage()
    gold.close()

    silver = open_silver(silver_path)
    silver.execute("SET memory_limit = '8GB'")
    started = time.monotonic()
    build_gold_from_scratch(silver, working_directory / "gold-full")
    full_build_seconds = round(time.monotonic() - started, 2)
    incremental, full = open_incremental_gold(working_directory / "gold-incremental"), open_gold(working_directory / "gold-full")
    incremental.execute(f"ATTACH '{working_directory / 'compare.duckdb'}' AS compare")
    differing_rows = {}
    for name in [*GOLD_RELATIONS, "claim_payers"]:
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
            "compact_every_batches": compact_every_batches, "compactions": len(compactions),
            "compaction_seconds_total": round(sum(c["seconds"] for c in compactions), 2),
            "compaction_seconds_max": max(c["seconds"] for c in compactions),
            "storage_before_final_compaction": storage_before_final_compaction, "storage": storage,
            "step_seconds": {step: round(sum(b["seconds"][step] for b in batches), 2) for step in batches[0]["seconds"]},
        },
        "3_fast": {"queries": len(query_seconds), "queries_with_other_rows_than_full_rebuild": other_rows,
                   "gold_seconds_median": round(statistics.median(query_seconds), 4),
                   "gold_seconds_total": round(sum(query_seconds), 2),
                   "gold_files": storage["data_files"]},
        "per_batch": batches,
    }
    (working_directory / "incremental_scorecard.json").write_text(json.dumps(scorecard, indent=2, default=str))
    return scorecard


def main():
    arguments = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    arguments.add_argument("--working-directory", type=Path, required=True)
    arguments.add_argument("--cache-directory", type=Path, default=Path.home() / ".cache" / "claim-clarity-gold")
    arguments.add_argument("--days", type=int, default=30)
    arguments.add_argument("--compact-every-batches", type=int, default=30,
                           help="merge small files and fold in delete markers after this many batches")
    options = arguments.parse_args()
    options.working_directory.mkdir(parents=True, exist_ok=False)
    scorecard = run(options.working_directory, options.cache_directory, options.days, options.compact_every_batches)
    print(json.dumps({k: v for k, v in scorecard.items() if k != "per_batch"}, indent=2, default=str))
    print([(b["batch_id"], b["seconds_total"], b["older_rows_demoted"]) for b in scorecard["per_batch"]][-12:])


if __name__ == "__main__":
    main()
