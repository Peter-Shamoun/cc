"""The ECS parser task: SQS -> download -> parse -> stage in a DuckDB slot -> export to the Iceberg silver lake.

    SQS manager thread   receives S3 event messages, keeps them hidden while their files are in flight, deletes
                         them once their files are committed
      -> URI queue           (in memory, bounded)
    M downloader threads write each raw file to ephemeral storage; they wait while the disk is over its cap
      -> raw file queue      (in memory, bounded; holds a pointer to the file on disk)
    N parser processes   N = vCPUs - 1. Parse raw files into staged units: a directory of .arrow files (one per
                         lossless table, in the lake's column types, NOT NULL columns checked) holding the rows
                         of several small files, or of one large file. Then delete the raw files. A large file is
                         read from disk and its rows written to disk a piece at a time, and the small files
                         gathered for a unit are capped in size, so a parser's memory does not grow with the size
                         of a file
      -> parsed unit queue   (in memory, bounded; holds a pointer to the unit's directory, and its files)
    1 stager thread      moves a unit's directory into the filling slot
    2 slots              directories of staged units on ephemeral storage. A slot is exported when it reaches its
                         size limit or no more files are left to stage; staging carries on in the other slot
    1 exporter thread    dedup, write Parquet, Iceberg commit, tell the SQS manager which files are committed,
                         wipe the slot

Rows are written once: the exporter builds each table's Parquet straight from the slot's .arrow files. (The design
staged rows in a DuckDB database per slot; inserting them there and reading them back was 80% of the task's time.)

A file that fails (download, parse or staging) is not deleted from SQS. The manager stops extending its
visibility, SQS redelivers it, and after the queue's maxReceiveCount SQS moves it to the dead-letter queue.
A slot export that fails is retried inside the task instead: that failure is not the files'.

Everything on disk is under the task's ephemeral storage directory, which Fargate tears down with the task.
"""
from __future__ import annotations

import gc
import hashlib
import json
import logging
import mmap
import ctypes
import multiprocessing
import os
import queue
import shutil
import signal
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote_plus

import boto3
import pyarrow as pa

from ccsim.parser import TableRows, parse_file
from ccsim.silver_staging import FLUSH_SEGMENTS, _drain, file_id_of

from .customer_settings import CustomerPipelineSettings
from .silver_lake import REQUIRED_COLUMNS, SEGMENT_TABLE, SilverLake, conform_to_lossless_schema

log = logging.getLogger("claim_clarity_pipeline.ingestion_task")
QUEUE_POLL_SECONDS = 0.5
# Parsed rows wait on ephemeral storage until their slot is exported. Uncompressed they take about 20 times the
# raw file; LZ4 is cheap to write and to read back a batch at a time.
PARSED_FILE_OPTIONS = pa.ipc.IpcWriteOptions(compression="lz4")
# Small files are staged together (StagedUnitBuilder). The limits bound a parser's memory and how long a parsed
# file can wait for its unit.
STAGED_UNIT_RAW_BYTES_LIMIT = 1024 ** 2
STAGED_UNIT_MAX_AGE_SECONDS = 2.0
PARSER_IDLE_SECONDS_BEFORE_UNIT_IS_WRITTEN = 0.2
RAW_BYTES_BETWEEN_COLLECTIONS = 16 * 1024 ** 2
EXPORT_RETRY_FIRST_SECONDS = 2.0
EXPORT_RETRY_MAX_SECONDS = 60.0
SQS_LONG_POLL_SECONDS = 2
EPHEMERAL_STORAGE_DIRECTORY_VARIABLE = "CLAIM_CLARITY_EPHEMERAL_STORAGE_DIRECTORY"
TASK_VCPU_COUNT_VARIABLE = "CLAIM_CLARITY_TASK_VCPU_COUNT"


@dataclass
class IngestFile:
    """One raw file on its way through the task. Passed along the in-memory queues (and to parser processes)."""
    sqs_message_id: str
    s3_bucket: str
    s3_key: str
    raw_file_path: str | None = None                 # set by the downloader
    source_file_provenance: dict = field(default_factory=dict)   # the S3 columns of edi.source_file
    failure: str | None = None
    stage_seconds: dict = field(default_factory=dict)            # download / parse / stage time spent on this file


@dataclass
class ParsedUnit:
    """What a parser process hands to the stager: the files whose rows it wrote together into one directory of
    .arrow files, or one file that failed to parse (no directory)."""
    directory: str | None
    files: list[IngestFile]


@dataclass
class _InFlightMessage:
    receipt_handle: str
    files_not_yet_resolved: int
    any_file_failed: bool = False


class PipelineProgress:
    """Files received from SQS that have not reached a slot (or failed) yet. Zero means nothing more will arrive
    at the writer until SQS delivers again, so a partly filled slot can be exported."""

    def __init__(self):
        self._lock = threading.Lock()
        self._files_awaiting_staging = 0

    def add(self, count: int):
        with self._lock:
            self._files_awaiting_staging += count

    @property
    def files_awaiting_staging(self) -> int:
        with self._lock:
            return self._files_awaiting_staging


# --- SQS manager thread ----------------------------------------------------------------------------------------
class SqsManager(threading.Thread):
    def __init__(self, settings: CustomerPipelineSettings, uri_queue: queue.Queue, resolved_files: queue.Queue,
                 progress: PipelineProgress, stop_requested: threading.Event):
        super().__init__(name="sqs-manager", daemon=True)
        self.settings, self.uri_queue, self.resolved_files = settings, uri_queue, resolved_files
        self.progress, self.stop_requested = progress, stop_requested
        self.sqs = boto3.client("sqs", region_name=settings.aws_region)
        self.queue_url = self.sqs.get_queue_url(QueueName=settings.ingestion_queue_name)["QueueUrl"]
        self.in_flight: dict[str, _InFlightMessage] = {}
        self.visibility_extended_at = time.monotonic()
        self.seconds = {"receive": 0.0, "delete": 0.0, "extend_visibility": 0.0, "uri_queue_full": 0.0}
        self.receive_calls = self.messages_received = 0

    def _timed(self, step: str, action):
        started = time.monotonic()
        action()
        self.seconds[step] += time.monotonic() - started

    def run(self):
        while not self.stop_requested.is_set():
            self._timed("delete", self._handle_resolved_files)
            self._timed("extend_visibility", self._extend_visibility_of_in_flight_messages)
            self._receive()
        log.info("sqs manager totals: %s", json.dumps({
            "receive_calls": self.receive_calls, "messages_received": self.messages_received,
            "seconds": {step: round(seconds, 1) for step, seconds in self.seconds.items()}}))

    def _receive(self):
        free_slots = self.settings.in_memory_queue_max_files - self.uri_queue.qsize()
        if free_slots <= 0:                               # downstream is busy: leave the messages in SQS
            time.sleep(QUEUE_POLL_SECONDS)
            self.seconds["uri_queue_full"] += QUEUE_POLL_SECONDS
            return
        started = time.monotonic()
        messages = self.sqs.receive_message(QueueUrl=self.queue_url, MaxNumberOfMessages=min(10, free_slots),
                                            WaitTimeSeconds=SQS_LONG_POLL_SECONDS).get("Messages", [])
        self.seconds["receive"] += time.monotonic() - started
        self.receive_calls += 1
        self.messages_received += len(messages)
        for message in messages:
            files = [IngestFile(message["MessageId"], record["s3"]["bucket"]["name"],
                                unquote_plus(record["s3"]["object"]["key"]))
                     for record in json.loads(message["Body"]).get("Records", [])
                     if record.get("eventName", "").startswith("ObjectCreated")]
            if not files:                                 # s3:TestEvent, sent when the notification is configured
                log.info("deleting a message that names no new object: %s", message["Body"][:200])
                self.sqs.delete_message(QueueUrl=self.queue_url, ReceiptHandle=message["ReceiptHandle"])
                continue
            self.in_flight[message["MessageId"]] = _InFlightMessage(message["ReceiptHandle"], len(files))
            self.progress.add(len(files))
            for file in files:
                file.stage_seconds["queued_at"] = time.monotonic()
                self.uri_queue.put(file)

    def _extend_visibility_of_in_flight_messages(self):
        timeout = self.settings.queue_visibility_timeout_seconds
        if time.monotonic() - self.visibility_extended_at < timeout / 3:
            return
        self.visibility_extended_at = time.monotonic()
        healthy = [(message_id, m) for message_id, m in self.in_flight.items() if not m.any_file_failed]
        for start in range(0, len(healthy), 10):
            self.sqs.change_message_visibility_batch(QueueUrl=self.queue_url, Entries=[
                {"Id": message_id, "ReceiptHandle": m.receipt_handle, "VisibilityTimeout": timeout}
                for message_id, m in healthy[start:start + 10]])

    def _handle_resolved_files(self):
        """A resolved file is committed to the lake (failure is None) or failed. A message is deleted once all its
        files are committed; a message with a failed file is left for SQS to redeliver."""
        to_delete = []
        while True:
            try:
                file: IngestFile = self.resolved_files.get_nowait()
            except queue.Empty:
                break
            message = self.in_flight.get(file.sqs_message_id)
            if message is None:
                continue
            if file.failure is not None:
                log.error("file failed, its message stays in SQS: s3://%s/%s: %s", file.s3_bucket, file.s3_key,
                          file.failure)
                message.any_file_failed = True
            message.files_not_yet_resolved -= 1
            if message.files_not_yet_resolved == 0:
                del self.in_flight[file.sqs_message_id]
                if not message.any_file_failed:
                    to_delete.append({"Id": file.sqs_message_id, "ReceiptHandle": message.receipt_handle})
        for start in range(0, len(to_delete), 10):
            response = self.sqs.delete_message_batch(QueueUrl=self.queue_url, Entries=to_delete[start:start + 10])
            for failed in response.get("Failed", []):     # e.g. the receipt handle expired: SQS redelivers the
                log.warning("could not delete message %s: %s", failed["Id"], failed.get("Message"))   # file, a duplicate


# --- downloader threads ----------------------------------------------------------------------------------------
def directory_size_bytes(directory: Path) -> int:
    total = 0
    for path in directory.rglob("*"):
        try:
            total += path.stat().st_size if path.is_file() else 0
        except FileNotFoundError:                         # deleted by a parser or the writer meanwhile
            pass
    return total


class EphemeralDiskUsage(threading.Thread):
    """Measures the ephemeral storage directory once a second. The downloaders check the cap against the last
    measurement: walking the directory before every download made each file wait seconds for a downloader."""

    def __init__(self, ephemeral_storage: Path, stop_requested: threading.Event):
        super().__init__(name="ephemeral-disk-usage", daemon=True)
        self.ephemeral_storage, self.stop_requested = ephemeral_storage, stop_requested
        self.bytes_used = 0

    def run(self):
        while not self.stop_requested.wait(1.0):
            self.bytes_used = directory_size_bytes(self.ephemeral_storage)


def downloader_thread(settings: CustomerPipelineSettings, ephemeral_storage: Path, disk_usage: EphemeralDiskUsage,
                      uri_queue: queue.Queue, raw_file_queue, resolved_files: queue.Queue,
                      progress: PipelineProgress, stop_requested: threading.Event):
    s3 = boto3.client("s3", region_name=settings.aws_region)
    raw_directory = ephemeral_storage / "raw"
    while not stop_requested.is_set():
        try:
            file: IngestFile = uri_queue.get(timeout=QUEUE_POLL_SECONDS)
        except queue.Empty:
            continue
        while disk_usage.bytes_used > settings.ephemeral_disk_cap_bytes and not stop_requested.is_set():
            time.sleep(QUEUE_POLL_SECONDS)                # the parsers and the exporter free disk
        started = time.monotonic()
        file.stage_seconds["wait_for_downloader"] = started - file.stage_seconds.pop("queued_at")
        try:
            s3_object = s3.get_object(Bucket=file.s3_bucket, Key=file.s3_key)
            raw_file_path = raw_directory / uuid.uuid4().hex
            sha256, size_bytes = hashlib.sha256(), 0
            with open(raw_file_path, "wb") as raw_file:
                for chunk in s3_object["Body"].iter_chunks(1 << 20):
                    raw_file.write(chunk)
                    sha256.update(chunk)
                    size_bytes += len(chunk)
            last_modified = s3_object["LastModified"].astimezone(UTC).replace(tzinfo=None)
            file.raw_file_path = str(raw_file_path)
            file.source_file_provenance = dict(
                s3_uri=f"s3://{file.s3_bucket}/{file.s3_key}", s3_bucket=file.s3_bucket, s3_key=file.s3_key,
                s3_version_id=s3_object.get("VersionId"), s3_etag=s3_object["ETag"].strip('"'),
                s3_last_modified=last_modified, s3_region=settings.aws_region,
                kms_key_arn=s3_object.get("SSEKMSKeyId"), original_filename=file.s3_key.rsplit("/", 1)[-1],
                source_system="sftp", size_bytes=size_bytes, sha256=sha256.hexdigest(), received_at=last_modified,
                character_encoding="ascii")
        except Exception as error:                        # noqa: BLE001 - any download failure is a failed file
            file.failure = f"download: {type(error).__name__}: {error}"
            progress.add(-1)
            resolved_files.put(file)
            continue
        file.stage_seconds["download"] = time.monotonic() - started
        file.stage_seconds["queued_at"] = time.monotonic()
        raw_file_queue.put(file)


# --- parser processes ------------------------------------------------------------------------------------------
class _RawFileOnDisk:
    """The raw file as the bytes-like object ccsim.parser reads, backed by a memory map: the operating system
    pages the file in as the parser walks it, so a parser's memory does not grow with the file's size."""

    def __init__(self, mapped: mmap.mmap):
        self._mapped = mapped

    def __len__(self):
        return len(self._mapped)

    def __getitem__(self, key):
        return self._mapped[key]

    def startswith(self, prefix: bytes) -> bool:
        return self._mapped[:len(prefix)] == prefix

    def isascii(self) -> bool:
        return all(self._mapped[start:start + (1 << 20)].isascii() for start in range(0, len(self._mapped), 1 << 20))


class _StagedUnitRows(TableRows):
    """Where a parser process puts rows, for all the files it parses. Rows wait in memory until write_unit writes
    them as one staged unit: a directory with one .arrow file per lossless table, in the lake's column types, NOT
    NULL columns checked. With stream_rows_within_file set (a large file, alone in its unit) rows are also written
    every FLUSH_SEGMENTS segments while the file is being parsed, and dropped from memory."""

    def __init__(self, parsed_root: Path):
        super().__init__()
        self.parsed_root = parsed_root
        self.stream_rows_within_file = False
        self.unit_directory: Path | None = None           # made when the first rows of a unit are written
        self.writers: dict[str, tuple[pa.OSFile, pa.ipc.RecordBatchFileWriter]] = {}

    def after_chunk(self):
        if self.stream_rows_within_file and len(self.rows[SEGMENT_TABLE]) >= FLUSH_SEGMENTS:
            for table, rows in _drain(self.rows, keep_latest_rows=True):
                self._write(table, rows)

    def _write(self, table: str, rows: pa.Table):
        rows = conform_to_lossless_schema(table, rows)
        missing = [c for c in REQUIRED_COLUMNS[table] if rows[c].null_count]
        if missing:
            raise ValueError(f"{table}: NULL in NOT NULL column(s) {missing}")
        if self.unit_directory is None:
            self.unit_directory = self.parsed_root / f"unit_{uuid.uuid4().hex}"
            self.unit_directory.mkdir(parents=True)
        if table not in self.writers:
            sink = pa.OSFile(str(self.unit_directory / f"{table}.arrow"), "wb")
            self.writers[table] = (sink, pa.ipc.new_file(sink, rows.schema, options=PARSED_FILE_OPTIONS))
        self.writers[table][1].write_table(rows)

    def buffered_row_counts(self) -> dict[str, int]:
        return {table: len(rows) for table, rows in self.rows.items()}

    def discard_rows_buffered_after(self, row_counts: dict[str, int]):
        for table, row_count in row_counts.items():
            del self.rows[table][row_count:]              # in place: the parser's adders append to these lists

    def write_unit(self) -> Path:
        """Write the rows still in memory and close the unit's files. Returns the unit's directory."""
        for table, rows in _drain(self.rows, keep_latest_rows=False):
            self._write(table, rows)
        unit_directory = self.unit_directory
        self._close_unit_files()
        return unit_directory

    def discard_unit(self):
        """Drop the rows in memory and remove what the unit has written."""
        self.discard_rows_buffered_after({table: 0 for table in self.rows})
        unit_directory = self.unit_directory
        self._close_unit_files()
        if unit_directory is not None:
            shutil.rmtree(unit_directory, ignore_errors=True)

    def _close_unit_files(self):
        for sink, writer in self.writers.values():
            writer.close()
            sink.close()
        self.writers, self.unit_directory = {}, None


class StagedUnitBuilder:
    """One parser process's work: parse raw files and hand them on in staged units.

    The rows of consecutive small files are gathered in memory and written together as one unit. Written alone, a
    file costs about 25 ms beyond its bytes (its rows become up to 16 small Arrow tables, each written to its own
    file); in a unit that is paid once for all its files. Every row carries its file_id, so the exporter can still
    tell the files of a unit apart.

    A unit is written when its files reach STAGED_UNIT_RAW_BYTES_LIMIT or FLUSH_SEGMENTS segments (the parser's
    memory), when its oldest file has waited STAGED_UNIT_MAX_AGE_SECONDS, or when the caller finds no further file
    to parse (write_unit). A file of STAGED_UNIT_RAW_BYTES_LIMIT or more is not gathered: it gets a unit of its
    own, written a piece at a time while it is parsed.

    A file that fails to parse leaves no row in any unit and is handed on alone, with its failure. A raw file
    stays on disk until its unit is written: if the unit cannot be written (a NULL in a NOT NULL column shows only
    then), its files are parsed again one unit each, and only the bad file fails."""

    def __init__(self, customer_code: str, parsed_root: str, hand_on_unit):
        self.customer_code, self.hand_on_unit = customer_code, hand_on_unit
        self.rows = _StagedUnitRows(Path(parsed_root))
        self.gathered_files: list[IngestFile] = []
        self.gathered_file_ids: set[str] = set()
        self.gathered_raw_bytes = 0

    def add_raw_file(self, file: IngestFile):
        """Parse the raw file on disk into the unit being gathered, or fail it (file.failure) and hand it on."""
        file_id = file_id_of(file.s3_key, file.source_file_provenance["s3_version_id"])
        is_large_file = file.source_file_provenance["size_bytes"] >= STAGED_UNIT_RAW_BYTES_LIMIT
        # A second delivery of the same object has the same file_id: in one unit the exporter could not tell the
        # two copies' rows apart, so it goes into the next unit.
        if is_large_file or file_id in self.gathered_file_ids:
            self.write_unit()
        started = time.monotonic()                        # CLOCK_MONOTONIC is the same clock in every process
        row_counts_before_file = self.rows.buffered_row_counts()
        self.rows.stream_rows_within_file = is_large_file
        try:
            with open(file.raw_file_path, "rb") as raw_file, \
                    mmap.mmap(raw_file.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
                # ingest_run_id and ingested_at are set by the exporter, when the file's slot is committed
                parse_file(_RawFileOnDisk(mapped), file_id, self.customer_code, file.source_file_provenance,
                           "not-exported-yet", datetime.now(UTC).replace(tzinfo=None), sink=self.rows)
        except Exception as error:                        # noqa: BLE001 - any parse failure is a failed file
            if is_large_file:                             # alone in its unit, part of it already written
                self.rows.discard_unit()
            else:
                self.rows.discard_rows_buffered_after(row_counts_before_file)
            self._hand_on_failed_file(file, f"parse: {type(error).__name__}: {error}", started)
            return
        gathered_at = time.monotonic()
        file.stage_seconds |= {"parse": gathered_at - started, "gathered_at": gathered_at}
        self.gathered_files.append(file)
        self.gathered_file_ids.add(file_id)
        self.gathered_raw_bytes += file.source_file_provenance["size_bytes"]
        if (self.gathered_raw_bytes >= STAGED_UNIT_RAW_BYTES_LIMIT
                or len(self.rows.rows[SEGMENT_TABLE]) >= FLUSH_SEGMENTS
                or gathered_at - self.gathered_files[0].stage_seconds["gathered_at"] >= STAGED_UNIT_MAX_AGE_SECONDS):
            self.write_unit()

    def write_unit(self):
        """Write the gathered files' rows as one unit and hand it on. Nothing happens when no file is gathered."""
        if not self.gathered_files:
            return
        files, raw_bytes = self.gathered_files, self.gathered_raw_bytes
        self.gathered_files, self.gathered_file_ids, self.gathered_raw_bytes = [], set(), 0
        started = time.monotonic()
        try:
            unit_directory = self.rows.write_unit()
        except Exception as error:                        # noqa: BLE001 - some file's rows cannot be staged
            self.rows.discard_unit()
            if len(files) == 1:
                self._hand_on_failed_file(files[0], f"parse: {type(error).__name__}: {error}", started)
            else:                                         # which file's? Each again, in a unit of its own
                for file in files:
                    del file.stage_seconds["gathered_at"]
                    self.add_raw_file(file)
                    self.write_unit()
            return
        written_at = time.monotonic()
        for file in files:
            Path(file.raw_file_path).unlink()             # staged: the raw file leaves the disk
            # writing the unit is parse time, shared by size; before it the file's rows waited for the unit
            write_seconds = (written_at - started) * file.source_file_provenance["size_bytes"] / raw_bytes
            file.stage_seconds["parse"] += write_seconds
            file.stage_seconds["wait_in_unit"] = written_at - file.stage_seconds.pop("gathered_at") - write_seconds
            file.stage_seconds["queued_at"] = written_at
        self.hand_on_unit(ParsedUnit(str(unit_directory), files))

    def _hand_on_failed_file(self, file: IngestFile, failure: str, started: float):
        Path(file.raw_file_path).unlink()
        file.failure = failure
        file.stage_seconds["parse"] = time.monotonic() - started
        file.stage_seconds["queued_at"] = time.monotonic()
        self.hand_on_unit(ParsedUnit(None, [file]))


def parser_process(customer_code: str, parsed_root: str, raw_file_queue, parsed_unit_queue):
    """Runs in its own process until it reads None. Raw files on disk -> staged units of .arrow files."""
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_IGN)          # the task process decides when the parsers stop
    gc.disable()                                          # parsing allocates millions of short-lived, acyclic rows
    builder = StagedUnitBuilder(customer_code, parsed_root, parsed_unit_queue.put)
    raw_bytes_since_collection = 0
    while True:
        try:                                              # gathered files do not wait long for a file to join them
            file = raw_file_queue.get(
                timeout=PARSER_IDLE_SECONDS_BEFORE_UNIT_IS_WRITTEN if builder.gathered_files else None)
        except queue.Empty:
            builder.write_unit()
            continue
        if file is None:                                  # the task is stopping: gathered files are still in SQS
            return
        file.stage_seconds["wait_for_parser"] = time.monotonic() - file.stage_seconds.pop("queued_at")
        builder.add_raw_file(file)
        # What the paused collector left behind. A collection takes about 30 ms whatever the file's size, so not
        # after every small file.
        raw_bytes_since_collection += file.source_file_provenance["size_bytes"]
        if raw_bytes_since_collection >= RAW_BYTES_BETWEEN_COLLECTIONS:
            gc.collect()
            raw_bytes_since_collection = 0


# --- slots, stager, exporter -----------------------------------------------------------------------------------
class Slot:
    """A directory on ephemeral storage holding the staged units of one export."""

    def __init__(self, name: str, directory: Path):
        self.name, self.directory = name, directory / f"slot_{name}"
        self.is_free = threading.Event()
        self._open()

    def _open(self):
        self.directory.mkdir()
        self.units: list[ParsedUnit] = []
        self.staged_bytes = 0
        self.is_free.set()

    @property
    def files(self) -> list[IngestFile]:
        return [file for unit in self.units for file in unit.files]

    def stage_parsed_unit(self, unit: ParsedUnit):
        staged_directory = self.directory / Path(unit.directory).name
        Path(unit.directory).rename(staged_directory)
        unit.directory = str(staged_directory)
        self.staged_bytes += directory_size_bytes(staged_directory)
        self.units.append(unit)

    def wipe(self):
        shutil.rmtree(self.directory)
        self._open()


def stager_thread(settings: CustomerPipelineSettings, slots: list[Slot], parsed_unit_queue,
                  slots_to_export: queue.Queue, resolved_files: queue.Queue, progress: PipelineProgress,
                  stop_requested: threading.Event):
    filling = slots[0]
    last_unit_at = time.monotonic()

    def export_filling_slot_and_switch():
        nonlocal filling
        filling.is_free.clear()
        slots_to_export.put(filling)
        filling = slots[(slots.index(filling) + 1) % len(slots)]
        while not filling.is_free.wait(QUEUE_POLL_SECONDS):   # every slot is exporting: ingestion waits here
            if stop_requested.is_set():
                return

    while not stop_requested.is_set():
        try:
            unit: ParsedUnit = parsed_unit_queue.get(timeout=QUEUE_POLL_SECONDS)
        except queue.Empty:
            no_more_files_left = (progress.files_awaiting_staging == 0
                                  and time.monotonic() - last_unit_at >= settings.slot_idle_export_seconds)
            if filling.units and no_more_files_left:
                export_filling_slot_and_switch()
            continue
        last_unit_at = time.monotonic()
        for file in unit.files:
            file.stage_seconds["wait_for_stager"] = last_unit_at - file.stage_seconds.pop("queued_at")
        if unit.directory is not None:
            try:
                filling.stage_parsed_unit(unit)
            except Exception as error:                    # noqa: BLE001 - any staging failure fails the unit's files
                for file in unit.files:
                    file.failure = f"stage: {type(error).__name__}: {error}"
                shutil.rmtree(unit.directory, ignore_errors=True)
            for file in unit.files:
                file.stage_seconds["stage"] = (time.monotonic() - last_unit_at) / len(unit.files)
        progress.add(-len(unit.files))
        for file in unit.files:
            if file.failure is not None:
                resolved_files.put(file)
        if filling.staged_bytes >= settings.slot_size_limit_bytes:
            export_filling_slot_and_switch()


def process_heap_megabytes() -> int:
    """This process's anonymous memory: what it has allocated, without the pages of memory-mapped files."""
    with open("/proc/self/status") as status:
        return next(int(line.split()[1]) // 1024 for line in status if line.startswith("RssAnon"))


def return_freed_memory_to_operating_system():
    """An export allocates and frees a few hundred megabytes; the allocators keep most of it for reuse and the
    task's memory then grows by that much per export. Hand it back."""
    pa.default_memory_pool().release_unused()
    ctypes.CDLL("libc.so.6").malloc_trim(0)


def exporter_thread(lake: SilverLake, slots_to_export: queue.Queue, resolved_files: queue.Queue,
                    stop_requested: threading.Event):
    while not stop_requested.is_set():
        try:
            slot: Slot = slots_to_export.get(timeout=QUEUE_POLL_SECONDS)
        except queue.Empty:
            continue
        export_id = f"export_{datetime.now(UTC):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}"
        started, failed_attempts = time.monotonic(), 0
        heap_megabytes_before_export = process_heap_megabytes()
        files = slot.files
        # A failed export is the lake's fault (S3, the catalog), not the files': it is retried here for as long as
        # the task runs, while the SQS manager keeps the files' messages hidden. Releasing them instead would
        # count a receive against every file of the slot and dead-letter good files after three failures.
        while not stop_requested.is_set():
            try:
                result = lake.export_slot([Path(unit.directory) for unit in slot.units],
                                          export_id)              # 1 dedup, 2 write Parquet, 3 Iceberg commit
            except Exception:                             # noqa: BLE001 - retried
                failed_attempts += 1
                retry_in_seconds = min(EXPORT_RETRY_MAX_SECONDS, EXPORT_RETRY_FIRST_SECONDS * 2 ** (failed_attempts - 1))
                log.exception("export of slot %s failed (attempt %d); retrying in %.0f s", slot.name, failed_attempts,
                              retry_in_seconds)
                stop_requested.wait(retry_in_seconds)
                continue
            result |= {"slot": slot.name, "slot_staged_bytes": slot.staged_bytes, "failed_attempts": failed_attempts,
                       "staged_units": len(slot.units),
                       "export_seconds": round(time.monotonic() - started, 2),
                       "task_heap_megabytes_before_export": heap_megabytes_before_export,
                       "raw_bytes": sum(f.source_file_provenance["size_bytes"] for f in files),
                       # time spent on this slot's files before the export, summed over the workers of each stage
                       "stage_seconds": {stage: round(sum(f.stage_seconds.get(stage, 0) for f in files), 2)
                                         for stage in ("wait_for_downloader", "download", "wait_for_parser", "parse",
                                                       "wait_in_unit", "wait_for_stager", "stage")}}
            log.info("committed slot: %s", json.dumps(result))
            for file in files:                            # 4 the SQS manager deletes the committed files' messages
                resolved_files.put(file)
            slot.wipe()                                   # 5
            return_freed_memory_to_operating_system()
            break


# --- the task --------------------------------------------------------------------------------------------------
def run_parser_task(settings: CustomerPipelineSettings, ephemeral_storage: Path, task_vcpu_count: int,
                    stop_requested: threading.Event):
    """Runs until stop_requested is set (ECS stops the task when the service is scaled to zero)."""
    for directory in ("raw", "parsed", "slots"):
        (ephemeral_storage / directory).mkdir(parents=True, exist_ok=True)
    parser_process_count = max(1, task_vcpu_count - 1)
    lake = SilverLake(settings)
    lake.recover_interrupted_exports()

    bound = settings.in_memory_queue_max_files
    spawn = multiprocessing.get_context("spawn")          # no fork of a process that runs threads and DuckDB
    uri_queue, resolved_files, slots_to_export = queue.Queue(bound), queue.Queue(), queue.Queue()
    raw_file_queue, parsed_unit_queue = spawn.Queue(bound), spawn.Queue(bound)
    progress = PipelineProgress()
    slots = [Slot(name, ephemeral_storage / "slots") for name in ("a", "b")]

    parsers = [spawn.Process(target=parser_process, name=f"parser-{n}", daemon=True, args=(
        settings.customer_code, str(ephemeral_storage / "parsed"), raw_file_queue, parsed_unit_queue))
        for n in range(parser_process_count)]
    disk_usage = EphemeralDiskUsage(ephemeral_storage, stop_requested)
    threads = [SqsManager(settings, uri_queue, resolved_files, progress, stop_requested), disk_usage]
    threads += [threading.Thread(target=downloader_thread, name=f"downloader-{n}", daemon=True, args=(
        settings, ephemeral_storage, disk_usage, uri_queue, raw_file_queue, resolved_files, progress,
        stop_requested))
        for n in range(settings.downloader_thread_count)]
    threads.append(threading.Thread(target=stager_thread, name="stager", daemon=True, args=(
        settings, slots, parsed_unit_queue, slots_to_export, resolved_files, progress, stop_requested)))
    threads.append(threading.Thread(target=exporter_thread, name="exporter", daemon=True, args=(
        lake, slots_to_export, resolved_files, stop_requested)))
    for worker in parsers + threads:
        worker.start()
    log.info("parser task up: %d parser processes, %d downloader threads, 2 slots of %d bytes",
             parser_process_count, settings.downloader_thread_count, settings.slot_size_limit_bytes)
    while not stop_requested.wait(QUEUE_POLL_SECONDS):
        dead = [worker.name for worker in parsers + threads if not worker.is_alive()]
        if dead:
            raise RuntimeError(f"task workers died: {dead}")      # the task exits; ECS replaces it
    for _ in parsers:
        raw_file_queue.put(None)
    for worker in threads + parsers:
        worker.join(timeout=10)
    log.info("parser task stopped")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(threadName)s %(message)s")
    stop_requested = threading.Event()
    for stop_signal in (signal.SIGTERM, signal.SIGINT):
        signal.signal(stop_signal, lambda *_: stop_requested.set())
    run_parser_task(CustomerPipelineSettings.from_environment(),
                    Path(os.environ[EPHEMERAL_STORAGE_DIRECTORY_VARIABLE]),
                    int(os.environ[TASK_VCPU_COUNT_VARIABLE]), stop_requested)


if __name__ == "__main__":
    main()
