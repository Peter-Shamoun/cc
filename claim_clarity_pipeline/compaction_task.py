"""Silver compaction: a one-shot Fargate task per customer, launched nightly by EventBridge Scheduler.

Every slot export writes one Parquet file per silver table, so a customer delivering a day at a time adds a small
file to every table every day. For each Iceberg table in the customer's Glue database:

    1. list the current snapshot's data files in the order they were added (received order)
    2. leave alone every file at or above compaction_target_file_bytes; pack the small ones, consecutively, into
       groups that reach the target; rewrite a group only if it has compaction_minimum_files_per_group files
    3. rewrite = stream the group's rows, in order, into ONE new Parquet file; then ONE Iceberg commit that removes
       exactly the group's files and adds the new one (an 'overwrite' snapshot marked with the run id)
    4. expire snapshots older than snapshot_retention_seconds, then delete the table's data files that no
       retained snapshot references, once they are older than compaction_unreferenced_file_minimum_age_seconds

Ingestion is not paused. If another writer committed first, the commit is tried again on the table as it now is,
as long as every file of the group is still live (an export's append does not touch them); if one is gone (an
interrupted export's rows were deleted), the group is abandoned and its new file deleted. A crash between writing
the new file and committing it leaves only that file, which step 4 of a later run deletes.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

from pyiceberg.exceptions import CommitFailedException, ValidationException
from pyiceberg.io.pyarrow import parquet_file_to_data_file
from pyiceberg.manifest import ManifestEntry, clear_manifest_cache
from pyiceberg.table import Table

import pyarrow as pa
import pyarrow.parquet as pq

from .customer_settings import CustomerPipelineSettings
from .silver_lake import SilverLake

log = logging.getLogger("claim_clarity_pipeline.compaction_task")
COMPACTION_RUN_ID_SNAPSHOT_PROPERTY = "claim-clarity.compaction-run-id"
MAXIMUM_COMMIT_ATTEMPTS_PER_GROUP = 10


def live_data_files_in_added_order(table: Table) -> list[ManifestEntry]:
    """The current snapshot's data files, oldest commit first. Exports commit in received order, so this is
    received order: neighbours in this list hold neighbouring days."""
    snapshot = table.current_snapshot()
    if snapshot is None:
        return []
    entries = [entry for manifest in snapshot.manifests(table.io)
               for entry in manifest.fetch_manifest_entry(table.io, discard_deleted=True)]
    return sorted(entries, key=lambda entry: (entry.sequence_number, entry.data_file.file_path))


def groups_of_small_files_to_rewrite(entries_in_added_order: list[ManifestEntry], target_file_bytes: int,
                                     minimum_files_per_group: int) -> list[list[ManifestEntry]]:
    """Consecutive small files packed into groups that reach the target size (the last group may not); only the
    groups with at least minimum_files_per_group files."""
    groups, group, group_bytes = [], [], 0
    for entry in entries_in_added_order:
        if entry.data_file.file_size_in_bytes >= target_file_bytes:
            continue                                      # large enough: never rewritten
        group.append(entry)
        group_bytes += entry.data_file.file_size_in_bytes
        if group_bytes >= target_file_bytes:
            groups.append(group)
            group, group_bytes = [], 0
    groups.append(group)
    return [group for group in groups if len(group) >= minimum_files_per_group]


def _entry_identity(entry: ManifestEntry) -> tuple[str, int]:
    """A data file as it is in the table: its path and the snapshot that added it. An export retried after its
    rows were deleted can add a file again under the same path; that is another file."""
    return entry.data_file.file_path, entry.snapshot_id


def replace_data_files(table: Table, data_files_to_remove: list, new_data_file_uri: str, compaction_run_id: str):
    """One atomic commit: an 'overwrite' snapshot that deletes exactly these data files and adds the new one;
    every other data file of the table is carried over. pyiceberg refuses it (ValidationException) if one of the
    files is no longer in the table."""
    with table.transaction() as transaction:
        new_data_file = parquet_file_to_data_file(table.io, transaction.table_metadata, new_data_file_uri)
        snapshot_properties = {COMPACTION_RUN_ID_SNAPSHOT_PROPERTY: compaction_run_id}
        with transaction.update_snapshot(snapshot_properties=snapshot_properties).overwrite() as overwrite:
            for data_file in data_files_to_remove:
                overwrite.delete_data_file(data_file)
            overwrite.append_data_file(new_data_file)


def commit_group_rewrite(lake: SilverLake, table_identifier: str, group: list[ManifestEntry],
                         new_data_file_uri: str, compaction_run_id: str) -> tuple[bool, int]:
    """Commit the rewritten group, retrying on the table as it now is while every file of the group is still in
    it. Returns (committed, failed commit attempts). An abandoned group's new file is deleted."""
    group_identities = {_entry_identity(entry) for entry in group}
    for attempt in range(MAXIMUM_COMMIT_ATTEMPTS_PER_GROUP):
        table = lake.catalog.load_table(table_identifier)
        live_data_files = {_entry_identity(entry): entry.data_file for entry in live_data_files_in_added_order(table)}
        if not group_identities <= live_data_files.keys():
            log.warning("%s: a file of the group left the table before the commit: group abandoned", table_identifier)
            break
        try:
            replace_data_files(table, [live_data_files[identity] for identity in group_identities],
                               new_data_file_uri, compaction_run_id)
            return True, attempt
        except (CommitFailedException, ValidationException) as error:
            log.warning("%s: commit attempt %d failed (%s); trying again", table_identifier, attempt + 1, error)
    lake.filesystem.delete_file(new_data_file_uri.removeprefix("s3://"))
    return False, attempt + 1


def rewrite_group(lake: SilverLake, table: Table, group: list[ManifestEntry], new_data_file_uri: str) -> int:
    """Stream the group's rows, file after file in group order, into one new Parquet file. Returns its rows."""
    schema = table.schema().as_arrow()                    # with field ids: readable with or without a name mapping

    def batches():
        for entry in group:
            with lake.filesystem.open_input_file(entry.data_file.file_path.removeprefix("s3://")) as source:
                for batch in pq.ParquetFile(source).iter_batches():
                    if batch.num_rows:
                        yield from pa.Table.from_batches([batch]).cast(schema).to_batches()
    return lake.write_parquet_file(new_data_file_uri, schema, batches())


def expire_snapshots_and_delete_unreferenced_data_files(lake: SilverLake, table_identifier: str,
                                                        settings: CustomerPipelineSettings) -> dict:
    """Expire snapshots past the retention window (the current one always stays), then delete the data files no
    retained snapshot references and that are older than the minimum age: files replaced by compaction or by a
    recovery delete once their snapshots expire, and files of a crashed compaction or export."""
    now = datetime.now(UTC)
    table = lake.catalog.load_table(table_identifier)
    expired = [snapshot.snapshot_id for snapshot in table.snapshots()
               if snapshot.timestamp_ms < (now - timedelta(seconds=settings.snapshot_retention_seconds)).timestamp() * 1000
               and snapshot.snapshot_id != table.metadata.current_snapshot_id]
    if expired:
        try:
            table.maintenance.expire_snapshots().by_ids(expired).commit()
        except CommitFailedException as error:            # another writer committed first: expire next night
            log.warning("%s: snapshot expiry skipped (%s)", table_identifier, error)
            expired = []
        table = lake.catalog.load_table(table_identifier)
    referenced_uris, manifests_read = set(), set()
    for snapshot in table.snapshots():
        for manifest in snapshot.manifests(table.io):
            if manifest.manifest_path not in manifests_read:
                manifests_read.add(manifest.manifest_path)
                referenced_uris |= {entry.data_file.file_path
                                    for entry in manifest.fetch_manifest_entry(table.io, discard_deleted=True)}
    data_prefix = f"{table.location()}/data/".removeprefix(f"s3://{settings.bucket_name}/")
    old_enough = now - timedelta(seconds=settings.compaction_unreferenced_file_minimum_age_seconds)
    unreferenced = [listed["Key"] for page in lake.s3.get_paginator("list_objects_v2").paginate(
                        Bucket=settings.bucket_name, Prefix=data_prefix) for listed in page.get("Contents", [])
                    if f"s3://{settings.bucket_name}/{listed['Key']}" not in referenced_uris
                    and listed["LastModified"] < old_enough]
    for start in range(0, len(unreferenced), 1000):
        lake.s3.delete_objects(Bucket=settings.bucket_name, Delete={
            "Objects": [{"Key": key} for key in unreferenced[start:start + 1000]], "Quiet": True})
    return {"snapshots_expired": len(expired), "unreferenced_data_files_deleted": len(unreferenced)}


def compact_table(lake: SilverLake, table_identifier: str, settings: CustomerPipelineSettings,
                  compaction_run_id: str) -> dict:
    started = time.monotonic()
    table = lake.catalog.load_table(table_identifier)
    entries = live_data_files_in_added_order(table)
    groups = groups_of_small_files_to_rewrite(entries, settings.compaction_target_file_bytes,
                                              settings.compaction_minimum_files_per_group)
    summary = {"data_files_before": len(entries), "groups_rewritten": 0, "groups_abandoned": 0,
               "failed_commit_attempts": 0, "rows_rewritten": 0, "bytes_read": 0, "bytes_written": 0}
    for group_number, group in enumerate(groups, start=1):
        table_name = table_identifier.rsplit(".", 1)[-1]
        new_data_file_uri = f"{table.location()}/data/{compaction_run_id}-{table_name}-{group_number:03d}.parquet"
        rows = rewrite_group(lake, table, group, new_data_file_uri)
        committed, failed_attempts = commit_group_rewrite(lake, table_identifier, group, new_data_file_uri,
                                                          compaction_run_id)
        summary["failed_commit_attempts"] += failed_attempts
        summary["bytes_read"] += sum(entry.data_file.file_size_in_bytes for entry in group)
        if committed:
            summary["groups_rewritten"] += 1
            summary["rows_rewritten"] += rows
            summary["bytes_written"] += lake.filesystem.get_file_info(
                new_data_file_uri.removeprefix("s3://")).size
        else:
            summary["groups_abandoned"] += 1
    summary["data_files_after"] = len(live_data_files_in_added_order(lake.catalog.load_table(table_identifier)))
    summary |= expire_snapshots_and_delete_unreferenced_data_files(lake, table_identifier, settings)
    summary["seconds"] = round(time.monotonic() - started, 2)
    clear_manifest_cache()                                # what this table's manifests loaded is not needed again
    return summary


def run_compaction(settings: CustomerPipelineSettings) -> dict:
    lake = SilverLake(settings)
    compaction_run_id = f"compaction_{datetime.now(UTC):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    tables = {".".join(identifier): compact_table(lake, ".".join(identifier), settings, compaction_run_id)
              for identifier in sorted(lake.catalog.list_tables(settings.glue_database_name))}
    return {"compaction_run_id": compaction_run_id, "seconds": round(time.monotonic() - started, 1),
            "tables": tables}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("pyiceberg").setLevel(logging.ERROR)
    summary = run_compaction(CustomerPipelineSettings.from_environment())
    log.info("compaction done: %s", json.dumps(summary))


if __name__ == "__main__":
    main()
