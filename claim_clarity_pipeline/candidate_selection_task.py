"""Candidate selection: a one-shot Fargate task, launched by EventBridge Scheduler.

    0. re-export any earlier run that committed candidates to the lake but never recorded its CSV as exported
    1. load the customer's preset (a query and its parameters) from S3
    2. DuckDB: run it over silver, on the latest version of each claim, skipping claims already selected
    3. commit the new candidates to the lake's candidate table, with this run's id
    4. write the candidates CSV to the customer's output location, and record the run as exported

The preset query reads the lossless silver tables (edi.*, c837.*, r835.*) and the semantic views over them
(sem.claims with is_latest_submission, sem.remit_claims with is_current_adjudication, ...). The views are computed
from silver at query time; nothing here reads gold. The query must return a claim_key column.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import boto3
import duckdb
import pyarrow as pa
from pyiceberg.expressions import EqualTo

from ccsim.parser import DDL_LOSSLESS
from ccsim.parser import TABLES as PARSED_TABLES
from ccsim.pipeline import DDL_SEMANTIC, seed_ref

from .customer_settings import CANDIDATE_OUTPUT_PREFIX, CANDIDATE_PRESET_KEY, CustomerPipelineSettings
from .ingestion_task import EPHEMERAL_STORAGE_DIRECTORY_VARIABLE
from .silver_lake import CANDIDATE_EXPORT_TABLE, CANDIDATE_SELECTION_TABLE, SilverLake

log = logging.getLogger("claim_clarity_pipeline.candidate_selection_task")


def silver_query_session(lake: SilverLake, spill_directory: Path) -> duckdb.DuckDBPyConnection:
    """DuckDB with every silver table as a lazy view over its Parquet files, ref.* seeded, and the semantic views
    on top. Queries spill to spill_directory (Fargate ephemeral storage) past half the task's memory."""
    # DuckDB sizes itself by the machine, not by the task: left alone, a year of silver takes 4.6 GB here and the
    # need grows with silver. Half the task's memory for DuckDB, the rest for the Parquet readers and Python; a
    # query that needs more spills.
    con = duckdb.connect(config={"temp_directory": str(spill_directory),
                                 "memory_limit": f"{lake.settings.candidate_task_memory_mib // 2}MiB",
                                 "threads": lake.settings.candidate_task_vcpu_count})
    con.execute("ATTACH ':memory:' AS ddl; USE ddl")
    con.execute(DDL_LOSSLESS)
    seed_ref(con)
    con.execute("USE memory")
    for schema, name in con.execute("SELECT schema_name, table_name FROM duckdb_tables() "
                                    "WHERE database_name = 'ddl'").fetchall():
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        lossless_table = f"{schema}.{name}"
        if lossless_table in PARSED_TABLES:
            dataset_name = f"silver_{schema}_{name}"
            con.register(dataset_name, lake.lazy_dataset(lossless_table))
            con.execute(f"CREATE VIEW {lossless_table} AS SELECT * FROM {dataset_name}")
        else:                                             # ref.*, and lossless tables the parser does not fill
            con.execute(f"CREATE TABLE {lossless_table} AS SELECT * FROM ddl.{lossless_table}")
    con.execute("DETACH ddl")
    con.execute(DDL_SEMANTIC)
    return con


def _csv_bytes(candidate_rows: list[dict]) -> bytes:
    text = io.StringIO()
    writer = csv.DictWriter(text, fieldnames=list(candidate_rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(candidate_rows)
    return text.getvalue().encode()


def export_run(settings: CustomerPipelineSettings, lake: SilverLake, s3, run_id: str) -> str:
    """Write the run's candidates CSV from the lake's candidate table, then record the run as exported."""
    rows = lake.read(CANDIDATE_SELECTION_TABLE, ("claim_key", "candidate_row_json"),
                     EqualTo("candidate_selection_run_id", run_id)).sort_by("claim_key")
    candidate_rows = [json.loads(row) for row in rows["candidate_row_json"].to_pylist()]
    csv_key = f"{CANDIDATE_OUTPUT_PREFIX}{run_id}.csv"
    s3.put_object(Bucket=settings.bucket_name, Key=csv_key, Body=_csv_bytes(candidate_rows), ContentType="text/csv")
    csv_uri = f"s3://{settings.bucket_name}/{csv_key}"
    lake.append(CANDIDATE_EXPORT_TABLE, pa.table({
        "candidate_selection_run_id": [run_id], "exported_at": [datetime.now(UTC).replace(tzinfo=None)],
        "candidate_count": [len(candidate_rows)], "csv_s3_uri": [csv_uri]}))
    return csv_uri


def run_candidate_selection(settings: CustomerPipelineSettings, ephemeral_storage: Path) -> dict:
    lake, s3 = SilverLake(settings), boto3.client("s3", region_name=settings.aws_region)
    selected = lake.read(CANDIDATE_SELECTION_TABLE, ("candidate_selection_run_id", "claim_key"))
    exported_run_ids = set(lake.read(CANDIDATE_EXPORT_TABLE, ("candidate_selection_run_id",))[0].to_pylist())
    re_exported = {run_id: export_run(settings, lake, s3, run_id)
                   for run_id in sorted(set(selected["candidate_selection_run_id"].to_pylist()) - exported_run_ids)}

    preset_object = s3.get_object(Bucket=settings.bucket_name, Key=CANDIDATE_PRESET_KEY)
    preset = json.loads(preset_object["Body"].read())
    con = silver_query_session(lake, ephemeral_storage)
    con.register("already_selected_claim", pa.table({"claim_key": selected["claim_key"]}))
    result = con.execute(
        f"SELECT * FROM ({preset['selection_sql']}) AS preset_selection "
        "WHERE claim_key NOT IN (SELECT claim_key FROM already_selected_claim) ORDER BY claim_key",
        preset.get("parameters") or None)
    columns = [d[0] for d in result.description]
    candidate_rows = [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
    claim_keys = [row["claim_key"] for row in candidate_rows]
    if len(set(claim_keys)) != len(claim_keys):
        raise ValueError(f"preset {preset['preset_name']!r} returned a claim more than once")

    run_id = f"candidates_{datetime.now(UTC):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}"
    summary = {"candidate_selection_run_id": run_id, "preset_name": preset["preset_name"],
               "preset_s3_version_id": preset_object.get("VersionId"), "new_candidate_count": len(candidate_rows),
               "claims_already_selected": selected.num_rows, "re_exported_runs": re_exported, "csv_s3_uri": None}
    if candidate_rows:                                    # a run that selects nothing writes no rows and no CSV
        now = datetime.now(UTC).replace(tzinfo=None)
        lake.append(CANDIDATE_SELECTION_TABLE, pa.table({
            "candidate_selection_run_id": [run_id] * len(candidate_rows), "claim_key": claim_keys,
            "selected_at": [now] * len(candidate_rows),
            "preset_name": [preset["preset_name"]] * len(candidate_rows),
            "preset_s3_version_id": [preset_object.get("VersionId")] * len(candidate_rows),
            "candidate_row_json": [json.dumps(row, default=str) for row in candidate_rows]}))
        summary["csv_s3_uri"] = export_run(settings, lake, s3, run_id)
    return summary


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    summary = run_candidate_selection(CustomerPipelineSettings.from_environment(),
                                      Path(os.environ[EPHEMERAL_STORAGE_DIRECTORY_VARIABLE]))
    log.info("candidate selection done: %s", json.dumps(summary))


if __name__ == "__main__":
    main()
