"""Compaction checks against a running AWS Emulator, one per process (the emulator's environment variables must
not leak into other tests):

    python -m claim_clarity_pipeline.evaluation.compaction_checks <check name>

Each check onboards a fresh customer's silver tables, commits small exports the way the parser task does (a
Parquet file per table, added with add_files, edi.source_file appended last), compacts, and asserts.
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import uuid

from ..emulator_connection import configure_process_for_emulator

configure_process_for_emulator(int(os.environ.get("CLAIM_CLARITY_EMULATOR_PORT", "9297")))   # before pyarrow

import boto3                                                                         # noqa: E402
import pyarrow as pa                                                                 # noqa: E402
from pyiceberg.expressions import In                                                 # noqa: E402

from .. import compaction_task                                                       # noqa: E402
from ..compaction_task import (COMPACTION_RUN_ID_SNAPSHOT_PROPERTY, live_data_files_in_added_order,  # noqa: E402
                               run_compaction)
from ..customer_settings import CustomerPipelineSettings                             # noqa: E402
from ..run_emulation import REPOSITORY_ROOT                                          # noqa: E402
from ..silver_lake import (COMMIT_RECORD_TABLE, EXPORT_ID_SNAPSHOT_PROPERTY, EXPORT_JOURNAL_PREFIX,  # noqa: E402
                           LOSSLESS_ARROW_SCHEMAS, SEGMENT_TABLE, SilverLake, conform_to_lossless_schema,
                           iceberg_table_name)
from .silver_fingerprint import (current_data_file_uris, fingerprints_of_every_table,  # noqa: E402
                                 unreferenced_data_file_uris)

SEGMENTS_PER_FILE = 50


def new_customer(**settings_overrides) -> tuple[CustomerPipelineSettings, SilverLake]:
    """Small thresholds so that a few tiny exports make groups: 3 files per group, a 64 KB target."""
    settings = CustomerPipelineSettings(f"compaction{uuid.uuid4().hex[:8]}", **({
        "compaction_target_file_bytes": 64 * 1024, "compaction_minimum_files_per_group": 3} | settings_overrides))
    boto3.client("s3", region_name=settings.aws_region).create_bucket(Bucket=settings.bucket_name)
    lake = SilverLake(settings)
    lake.create_tables()
    return settings, lake


def identifier(settings: CustomerPipelineSettings, lossless_table: str) -> str:
    return f"{settings.glue_database_name}.{iceberg_table_name(lossless_table)}"


def export_files(lake: SilverLake, file_ids: list[str], segments_per_file: int = SEGMENTS_PER_FILE,
                 with_commit_record: bool = True) -> str:
    """What a slot export of these raw files commits: their edi.segment rows as one Parquet file added to the
    table, then their edi.source_file rows (the commit record). Returns the export id."""
    export_id = f"export_{uuid.uuid4().hex[:12]}"
    segments = conform_to_lossless_schema(SEGMENT_TABLE, pa.table({
        "file_id": [file_id for file_id in file_ids for _ in range(segments_per_file)],
        "seg_ord": [number for _ in file_ids for number in range(segments_per_file)],
        "segment_id": [uuid.uuid4().hex for _ in file_ids for _ in range(segments_per_file)]}))
    data_file_uri = f"{lake.table(SEGMENT_TABLE).location()}/data/{export_id}.parquet"
    lake.write_parquet_file(data_file_uri, LOSSLESS_ARROW_SCHEMAS[SEGMENT_TABLE], segments.to_batches())
    lake.add_data_file(SEGMENT_TABLE, data_file_uri, {EXPORT_ID_SNAPSHOT_PROPERTY: export_id})
    if with_commit_record:
        lake.append(COMMIT_RECORD_TABLE, conform_to_lossless_schema(COMMIT_RECORD_TABLE, pa.table({
            "file_id": file_ids, "s3_key": [f"input/{file_id}.x12" for file_id in file_ids],
            "sha256": [uuid.uuid4().hex for _ in file_ids], "segment_count": [segments_per_file] * len(file_ids),
            "ingest_run_id": [export_id] * len(file_ids)})), {EXPORT_ID_SNAPSHOT_PROPERTY: export_id})
    return export_id


def export_days(lake: SilverLake, day_count: int) -> list[str]:
    """One export per day of two raw files each, as the daily runs commit them. Returns the file ids."""
    file_ids = []
    for day in range(day_count):
        day_file_ids = [f"day{day:02d}_file{number}" for number in range(2)]
        export_files(lake, day_file_ids)
        file_ids += day_file_ids
    return file_ids


def data_file_count(lake: SilverLake, settings: CustomerPipelineSettings, lossless_table: str) -> int:
    return len(current_data_file_uris(lake, identifier(settings, lossless_table)))


# --- checks ------------------------------------------------------------------------------------------------------
def small_files_are_rewritten_with_identical_rows():
    """Groups of consecutive small files become one file each; a large file and a short tail are left alone; every
    table's rows are the same multiset; the commit is one 'overwrite' snapshot that removes exactly the group's
    files; dedup's view of the lake (loaded_file_ids_by_sha256) is unchanged; a second run finds nothing to do."""
    settings, lake = new_customer()
    export_files(lake, ["large_file"], segments_per_file=20000)             # well over the 64 KB target
    export_days(lake, 10)
    segment_table = identifier(settings, SEGMENT_TABLE)
    files_before = {entry.data_file.file_path for entry in
                    live_data_files_in_added_order(lake.catalog.load_table(segment_table))}
    large_file = next(path for path in files_before if lake.filesystem.get_file_info(
        path.removeprefix("s3://")).size >= settings.compaction_target_file_bytes)
    fingerprints, loaded_files = fingerprints_of_every_table(lake), lake.loaded_file_ids_by_sha256()

    summary = run_compaction(settings)
    assert fingerprints_of_every_table(lake) == fingerprints, "rows changed"
    assert lake.loaded_file_ids_by_sha256() == loaded_files
    for lossless_table in (SEGMENT_TABLE, COMMIT_RECORD_TABLE):           # 11 files: 1 large + 10 small, 3 per group
        table_summary = summary["tables"][identifier(settings, lossless_table)]
        assert table_summary["data_files_before"] == 11 and table_summary["groups_abandoned"] == 0, table_summary
        assert table_summary["data_files_after"] < table_summary["data_files_before"], table_summary
    table = lake.catalog.load_table(segment_table)
    files_after = {entry.data_file.file_path for entry in live_data_files_in_added_order(table)}
    assert large_file in files_after, "a file at the target size was rewritten"
    compaction_snapshots = [snapshot for snapshot in table.snapshots()
                            if snapshot.summary.additional_properties.get(COMPACTION_RUN_ID_SNAPSHOT_PROPERTY)]
    assert compaction_snapshots and all(snapshot.summary.operation.value == "overwrite"
                                        for snapshot in compaction_snapshots)
    removed, added = files_before - files_after, files_after - files_before
    assert len(added) == len(compaction_snapshots)                         # one new file per group commit
    for snapshot in compaction_snapshots:                                   # each removes only its group's files
        assert int(snapshot.summary["added-data-files"]) == 1
        assert int(snapshot.summary["deleted-data-files"]) >= settings.compaction_minimum_files_per_group
    assert sum(int(s.summary["deleted-data-files"]) for s in compaction_snapshots) == len(removed)
    assert all(lake.filesystem.get_file_info(path.removeprefix("s3://")).size for path in removed), \
        "a replaced file was deleted before its snapshots expired"

    again = run_compaction(settings)
    assert all(t["groups_rewritten"] == 0 for t in again["tables"].values()), again
    assert fingerprints_of_every_table(lake) == fingerprints
    export_files(lake, ["after_compaction"])                                 # ingestion carries on as before
    assert "after_compaction" in lake.loaded_file_ids_by_sha256().values()


def a_concurrent_append_is_kept_and_the_group_still_commits():
    """An export commits to the table between the rewrite and the compaction commit: the commit is retried on
    the new table state, and both the export's file and the group's rows are in the table once."""
    settings, lake = new_customer()
    export_days(lake, 6)
    fingerprints_before = fingerprints_of_every_table(lake)
    concurrent_lake = SilverLake(settings)                 # another writer: the parser task
    replace = compaction_task.replace_data_files
    calls = []

    def export_commits_first(table, *arguments):
        if not calls:
            calls.append(export_files(concurrent_lake, ["concurrent_file"]))
        return replace(table, *arguments)
    compaction_task.replace_data_files = export_commits_first
    summary = run_compaction(settings)
    compaction_task.replace_data_files = replace
    segment_summary = summary["tables"][identifier(settings, SEGMENT_TABLE)]
    snapshots = lake.catalog.load_table(identifier(settings, SEGMENT_TABLE)).snapshots()
    (export_snapshot,) = [s for s in snapshots if s.summary.additional_properties.get(EXPORT_ID_SNAPSHOT_PROPERTY) == calls[0]]
    (compaction_snapshot,) = [s for s in snapshots if COMPACTION_RUN_ID_SNAPSHOT_PROPERTY in s.summary.additional_properties]
    assert export_snapshot.sequence_number < compaction_snapshot.sequence_number   # committed on top of the export
    assert segment_summary["groups_rewritten"] >= 1 and segment_summary["groups_abandoned"] == 0, segment_summary
    assert "concurrent_file" in lake.loaded_file_ids_by_sha256().values()
    segments = lake.read(SEGMENT_TABLE, ("file_id",))["file_id"].to_pylist()
    assert segments.count("concurrent_file") == SEGMENTS_PER_FILE
    expected_rows = fingerprints_before[identifier(settings, SEGMENT_TABLE)][0] + SEGMENTS_PER_FILE
    assert fingerprints_of_every_table(lake)[identifier(settings, SEGMENT_TABLE)][0] == expected_rows


def a_group_whose_file_was_deleted_meanwhile_is_abandoned():
    """Recovery deletes an interrupted export's rows between the rewrite and the compaction commit: the group is
    abandoned (the deleted rows must not come back), its new file is removed, every other row stays."""
    settings, lake = new_customer(compaction_minimum_files_per_group=3, compaction_target_file_bytes=1024 ** 3)
    file_ids = export_days(lake, 4)
    concurrent_lake = SilverLake(settings)
    replace = compaction_task.replace_data_files
    calls = []

    def recovery_deletes_first(table, *arguments):
        if not calls:
            calls.append(1)
            concurrent_lake.table(SEGMENT_TABLE).delete(In("file_id", file_ids[:2]))     # the first day's files
        return replace(table, *arguments)
    compaction_task.replace_data_files = recovery_deletes_first
    summary = run_compaction(settings)
    compaction_task.replace_data_files = replace
    segment_summary = summary["tables"][identifier(settings, SEGMENT_TABLE)]
    assert segment_summary["groups_abandoned"] == 1 and segment_summary["groups_rewritten"] == 0, segment_summary
    segments = lake.read(SEGMENT_TABLE, ("file_id",))["file_id"].to_pylist()
    assert sorted(set(segments)) == sorted(file_ids[2:]) and len(segments) == SEGMENTS_PER_FILE * len(file_ids[2:])
    data_prefix = lake.table(SEGMENT_TABLE).location().removeprefix("s3://") + "/data"
    left = [info.path for info in lake.filesystem.get_file_info(pa.fs.FileSelector(data_prefix))]
    assert not [path for path in left if summary["compaction_run_id"] in path], "the abandoned file was left"


def recovery_removes_an_interrupted_exports_rows_after_they_were_compacted():
    """Compaction moves an interrupted export's rows into a file with other exports' rows; recovery, which deletes
    by file_id, still removes exactly them."""
    settings, lake = new_customer()
    export_days(lake, 3)
    fingerprints_without_the_interrupted_export = fingerprints_of_every_table(lake)
    interrupted = export_files(lake, ["interrupted_a", "interrupted_b"], with_commit_record=False)
    lake.s3.put_object(Bucket=settings.bucket_name, Key=f"{EXPORT_JOURNAL_PREFIX}{interrupted}.json", Body=json.dumps(
        {"export_id": interrupted, "file_ids": ["interrupted_a", "interrupted_b"]}).encode())
    export_days(lake, 2)
    summary = run_compaction(settings)
    assert summary["tables"][identifier(settings, SEGMENT_TABLE)]["groups_rewritten"] >= 1
    compacted_rows = lake.read(SEGMENT_TABLE, ("file_id",))["file_id"].to_pylist()
    assert compacted_rows.count("interrupted_a") == SEGMENTS_PER_FILE

    assert lake.recover_interrupted_exports() == [interrupted]
    segments = lake.read(SEGMENT_TABLE, ("file_id",))["file_id"].to_pylist()
    assert "interrupted_a" not in segments and "interrupted_b" not in segments
    after = fingerprints_of_every_table(lake)
    for table, (rows, _) in fingerprints_without_the_interrupted_export.items():   # plus the two later days
        later_days = {identifier(settings, SEGMENT_TABLE): 4 * SEGMENTS_PER_FILE,
                      identifier(settings, COMMIT_RECORD_TABLE): 4}.get(table, 0)
        assert after[table][0] == rows + later_days, (table, after[table], rows)


def a_crash_before_the_commit_leaves_only_an_unreferenced_file_which_a_later_run_deletes():
    """The task dies between writing a rewritten file and committing it: the table is unchanged and the file is
    referenced by no snapshot. A later run deletes it once it is old enough, and compacts. With a retention of 0
    the replaced files are deleted too, and the rows are still the same."""
    settings, lake = new_customer()
    export_days(lake, 6)
    fingerprints = fingerprints_of_every_table(lake)
    files_before = data_file_count(lake, settings, SEGMENT_TABLE)
    crashed = subprocess.run([sys.executable, "-m", "claim_clarity_pipeline.evaluation.fault_injecting_compaction_task"],
                             cwd=REPOSITORY_ROOT, env=os.environ | settings.to_environment(), capture_output=True)
    assert crashed.returncode == 137, crashed.stdout[-2000:]
    assert fingerprints_of_every_table(lake) == fingerprints
    assert data_file_count(lake, settings, SEGMENT_TABLE) == files_before

    tables = list(fingerprints)
    assert sum(len(unreferenced_data_file_uris(lake, table)) for table in tables) == 1     # the one rewritten file, never committed

    run_compaction(settings)                                # the default minimum age: the file is too young to go
    assert sum(len(unreferenced_data_file_uris(lake, table)) for table in tables) == 1
    assert fingerprints_of_every_table(lake) == fingerprints
    no_waiting = dataclasses.replace(settings, compaction_unreferenced_file_minimum_age_seconds=0,
                                     snapshot_retention_seconds=0)
    summary = run_compaction(no_waiting)
    assert sum(len(unreferenced_data_file_uris(lake, table)) for table in tables) == 0
    assert summary["tables"][identifier(settings, SEGMENT_TABLE)]["unreferenced_data_files_deleted"] >= 1
    assert fingerprints_of_every_table(lake) == fingerprints
    for table in tables:                                    # only the current snapshot is left, and only its files
        iceberg_table = lake.catalog.load_table(table)
        prefix = iceberg_table.location().removeprefix("s3://") + "/data"
        assert sorted(f"s3://{info.path}" for info in lake.filesystem.get_file_info(pa.fs.FileSelector(
            prefix, allow_not_found=True))) == sorted(current_data_file_uris(lake, table)), table
        assert len(iceberg_table.snapshots()) == (1 if iceberg_table.current_snapshot() else 0), table


CHECKS = {check.__name__: check for check in (
    small_files_are_rewritten_with_identical_rows, a_concurrent_append_is_kept_and_the_group_still_commits,
    a_group_whose_file_was_deleted_meanwhile_is_abandoned,
    recovery_removes_an_interrupted_exports_rows_after_they_were_compacted,
    a_crash_before_the_commit_leaves_only_an_unreferenced_file_which_a_later_run_deletes)}

if __name__ == "__main__":
    CHECKS[sys.argv[1]]()
    print(f"{sys.argv[1]}: passed")
