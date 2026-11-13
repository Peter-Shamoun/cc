"""What proves that compaction loses and duplicates nothing: an order-independent fingerprint of a table's rows.

(row count, sum of the hash of every whole row), computed by DuckDB over Parquet data files. Equal fingerprints
mean the same multiset of rows (up to a 64-bit hash collision); the sums of disjoint sets of files add up.
"""
from __future__ import annotations

import duckdb
import pyarrow.dataset
import pyarrow.fs

from ..silver_lake import EXPORT_ID_SNAPSHOT_PROPERTY, SilverLake


def fingerprint_of_data_files(lake: SilverLake, data_file_uris: list[str]) -> tuple[int, int]:
    if not data_file_uris:
        return 0, 0
    rows = pyarrow.dataset.dataset([uri.removeprefix("s3://") for uri in data_file_uris],
                                   filesystem=lake.filesystem, format="parquet")
    connection = duckdb.connect()
    connection.register("rows_of_files", rows)
    row_count, hash_sum = connection.execute(
        "SELECT count(*), coalesce(sum(hash(rows_of_files)), 0) FROM rows_of_files").fetchone()
    return int(row_count), int(hash_sum)


def current_data_file_uris(lake: SilverLake, table_identifier: str) -> list[str]:
    table = lake.catalog.load_table(table_identifier)
    return [task.file.file_path for task in table.scan().plan_files()]


def fingerprints_of_every_table(lake: SilverLake) -> dict[str, tuple[int, int]]:
    """Per Iceberg table of the customer's Glue database ('claimclarity_x_silver.edi_segment'): its fingerprint."""
    identifiers = [".".join(identifier) for identifier in lake.catalog.list_tables(lake.settings.glue_database_name)]
    return {identifier: fingerprint_of_data_files(lake, current_data_file_uris(lake, identifier))
            for identifier in sorted(identifiers)}


def data_file_uris_added_by_exports_since(lake: SilverLake, table_identifier: str, since_milliseconds: int) -> list[str]:
    """The data files the slot exports committed to the table after the given time (each export's snapshot
    carries its export id), whether or not compaction has since replaced them."""
    table = lake.catalog.load_table(table_identifier)
    uris = []
    for snapshot in table.snapshots():
        if (snapshot.timestamp_ms >= since_milliseconds
                and EXPORT_ID_SNAPSHOT_PROPERTY in snapshot.summary.additional_properties):
            uris += [entry.data_file.file_path for manifest in snapshot.manifests(table.io)
                     if manifest.added_snapshot_id == snapshot.snapshot_id
                     for entry in manifest.fetch_manifest_entry(table.io, discard_deleted=True)
                     if entry.snapshot_id == snapshot.snapshot_id]
    return uris


def unreferenced_data_file_uris(lake: SilverLake, table_identifier: str) -> list[str]:
    """Objects under the table's data/ that no snapshot of the table references."""
    table = lake.catalog.load_table(table_identifier)
    referenced = {entry.data_file.file_path for snapshot in table.snapshots()
                  for manifest in snapshot.manifests(table.io)
                  for entry in manifest.fetch_manifest_entry(table.io, discard_deleted=True)}
    prefix = table.location().removeprefix("s3://") + "/data"
    return [f"s3://{info.path}" for info in lake.filesystem.get_file_info(
        pyarrow.fs.FileSelector(prefix, allow_not_found=True)) if f"s3://{info.path}" not in referenced]
