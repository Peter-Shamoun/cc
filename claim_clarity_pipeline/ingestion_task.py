"""The ECS parser task: SQS -> download -> parse -> stage in a DuckDB slot -> export to the Iceberg silver lake.

    SQS manager thread   receives S3 event messages, keeps them hidden while their files are in flight, deletes
                         them once their files are committed
      -> URI queue           (in memory, bounded)
    M downloader threads write each raw file to ephemeral storage; they wait while the disk is over its cap
      -> raw file queue      (in memory, bounded; holds a pointer to the file on disk)
    N parser processes   N = vCPUs - 1. Parse a raw file into .arrow files (one per lossless table, in the lake's
                         column types, NOT NULL columns checked), then delete the raw file
      -> parsed file queue   (in memory, bounded; holds a pointer to the .arrow directory)
    1 stager thread      moves a parsed file's directory into the filling slot
    2 slots              directories of parsed files on ephemeral storage. A slot is exported when it reaches its
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

import hashlib
import json
import logging
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

from ccsim.silver_staging import _parse_in_worker, arrow_table, file_id_of

from .customer_settings import CustomerPipelineSettings
from .silver_lake import (COMMIT_RECORD_TABLE, REQUIRED_COLUMNS, SEGMENT_TABLE, SilverLake,
                          conform_to_lossless_schema)

log = logging.getLogger("claim_clarity_pipeline.ingestion_task")
QUEUE_POLL_SECONDS = 0.5
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
    parsed_directory: str | None = None              # set by the parser
    source_file_provenance: dict = field(default_factory=dict)   # the S3 columns of edi.source_file
    failure: str | None = None
    stage_seconds: dict = field(default_factory=dict)            # download / parse / insert time spent on this file


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
def parser_process(customer_code: str, parsed_root: str, raw_file_queue, parsed_file_queue):
    """Runs in its own process until it reads None. Raw file -> one .arrow file per lossless table."""
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_IGN)          # the task process decides when the parsers stop
    while (file := raw_file_queue.get()) is not None:
        started = time.monotonic()                        # CLOCK_MONOTONIC is the same clock in every process
        file.stage_seconds["wait_for_parser"] = started - file.stage_seconds.pop("queued_at")
        try:
            provenance = file.source_file_provenance
            file_id = file_id_of(file.s3_key, provenance["s3_version_id"])
            body = Path(file.raw_file_path).read_bytes()
            # ingest_run_id and ingested_at are set by the exporter, when the file's slot is committed
            outcome = _parse_in_worker(body, file_id, customer_code, provenance, "not-exported-yet",
                                       datetime.now(UTC).replace(tzinfo=None))
            if isinstance(outcome, str):
                file.failure = f"parse: {outcome}"
            else:
                tables, source_file_row = outcome
                parsed_directory = Path(parsed_root) / f"{file_id}-{uuid.uuid4().hex[:8]}"
                parsed_directory.mkdir(parents=True)
                tables = {t: pa.concat_tables(parts, promote_options="default") for t, parts in tables.items()}
                # Silver is the source of truth: it keeps every byte of the file. Each segment row gets its text
                # as received and the bytes up to the next segment; the file's row gets the bytes before the first.
                segments = tables[SEGMENT_TABLE]
                offsets = segments["byte_offset"].to_pylist()
                ends = [offset + length for offset, length in zip(offsets, segments["byte_length"].to_pylist(),
                                                                 strict=True)]
                next_offsets = offsets[1:] + [len(body)]
                tables[SEGMENT_TABLE] = segments.append_column("raw_text", pa.array(
                    [body[offset:end].decode("ascii") for offset, end in zip(offsets, ends, strict=True)])
                ).append_column("separator_after", pa.array(
                    [body[end:following].decode("ascii") for end, following in zip(ends, next_offsets, strict=True)]))
                source_file_row["leading_bytes"] = body[:offsets[0]]
                tables[COMMIT_RECORD_TABLE] = arrow_table(COMMIT_RECORD_TABLE, [source_file_row])
                for table_name, rows in tables.items():
                    rows = conform_to_lossless_schema(table_name, rows)
                    missing = [c for c in REQUIRED_COLUMNS[table_name] if rows[c].null_count]
                    if missing:
                        raise ValueError(f"{table_name}: NULL in NOT NULL column(s) {missing}")
                    with pa.OSFile(str(parsed_directory / f"{table_name}.arrow"), "wb") as sink, \
                            pa.ipc.new_file(sink, rows.schema) as writer:
                        writer.write_table(rows)
                file.parsed_directory = str(parsed_directory)
        except Exception as error:                        # noqa: BLE001 - any parse failure is a failed file
            file.failure = f"parse: {type(error).__name__}: {error}"
        file.stage_seconds["parse"] = time.monotonic() - started
        file.stage_seconds["queued_at"] = time.monotonic()
        Path(file.raw_file_path).unlink(missing_ok=True)  # parsed (or failed): the raw file leaves the disk
        parsed_file_queue.put(file)


# --- slots, stager, exporter -----------------------------------------------------------------------------------
class Slot:
    """A directory on ephemeral storage holding the parsed files staged for one export."""

    def __init__(self, name: str, directory: Path):
        self.name, self.directory = name, directory / f"slot_{name}"
        self.is_free = threading.Event()
        self._open()

    def _open(self):
        self.directory.mkdir()
        self.files: list[IngestFile] = []
        self.staged_bytes = 0
        self.is_free.set()

    def stage_parsed_file(self, file: IngestFile):
        staged_directory = self.directory / Path(file.parsed_directory).name
        Path(file.parsed_directory).rename(staged_directory)
        file.parsed_directory = str(staged_directory)
        self.staged_bytes += directory_size_bytes(staged_directory)
        self.files.append(file)

    def wipe(self):
        shutil.rmtree(self.directory)
        self._open()


def stager_thread(settings: CustomerPipelineSettings, slots: list[Slot], parsed_file_queue,
                  slots_to_export: queue.Queue, resolved_files: queue.Queue, progress: PipelineProgress,
                  stop_requested: threading.Event):
    filling = slots[0]
    last_file_at = time.monotonic()

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
            file: IngestFile = parsed_file_queue.get(timeout=QUEUE_POLL_SECONDS)
        except queue.Empty:
            no_more_files_left = (progress.files_awaiting_staging == 0
                                  and time.monotonic() - last_file_at >= settings.slot_idle_export_seconds)
            if filling.files and no_more_files_left:
                export_filling_slot_and_switch()
            continue
        last_file_at = time.monotonic()
        file.stage_seconds["wait_for_stager"] = last_file_at - file.stage_seconds.pop("queued_at", last_file_at)
        if file.failure is None:
            started = time.monotonic()
            try:
                filling.stage_parsed_file(file)
            except Exception as error:                    # noqa: BLE001 - any staging failure is a failed file
                file.failure = f"stage: {type(error).__name__}: {error}"
                shutil.rmtree(file.parsed_directory, ignore_errors=True)
            file.stage_seconds["stage"] = time.monotonic() - started
        progress.add(-1)
        if file.failure is not None:
            resolved_files.put(file)
        elif filling.staged_bytes >= settings.slot_size_limit_bytes:
            export_filling_slot_and_switch()


def exporter_thread(lake: SilverLake, slots_to_export: queue.Queue, resolved_files: queue.Queue,
                    stop_requested: threading.Event):
    while not stop_requested.is_set():
        try:
            slot: Slot = slots_to_export.get(timeout=QUEUE_POLL_SECONDS)
        except queue.Empty:
            continue
        export_id = f"export_{datetime.now(UTC):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}"
        started, failed_attempts = time.monotonic(), 0
        # A failed export is the lake's fault (S3, the catalog), not the files': it is retried here for as long as
        # the task runs, while the SQS manager keeps the files' messages hidden. Releasing them instead would
        # count a receive against every file of the slot and dead-letter good files after three failures.
        while not stop_requested.is_set():
            try:
                result = lake.export_slot([Path(f.parsed_directory) for f in slot.files],
                                          export_id)              # 1 dedup, 2 write Parquet, 3 Iceberg commit
            except Exception:                             # noqa: BLE001 - retried
                failed_attempts += 1
                retry_in_seconds = min(EXPORT_RETRY_MAX_SECONDS, EXPORT_RETRY_FIRST_SECONDS * 2 ** (failed_attempts - 1))
                log.exception("export of slot %s failed (attempt %d); retrying in %.0f s", slot.name, failed_attempts,
                              retry_in_seconds)
                stop_requested.wait(retry_in_seconds)
                continue
            result |= {"slot": slot.name, "slot_staged_bytes": slot.staged_bytes, "failed_attempts": failed_attempts,
                       "export_seconds": round(time.monotonic() - started, 2),
                       "raw_bytes": sum(f.source_file_provenance["size_bytes"] for f in slot.files),
                       # time spent on this slot's files before the export, summed over the workers of each stage
                       "stage_seconds": {stage: round(sum(f.stage_seconds.get(stage, 0) for f in slot.files), 2)
                                         for stage in ("wait_for_downloader", "download", "wait_for_parser", "parse",
                                                       "wait_for_stager", "stage")}}
            log.info("committed slot: %s", json.dumps(result))
            for file in slot.files:                       # 4 the SQS manager deletes the committed files' messages
                resolved_files.put(file)
            slot.wipe()                                   # 5
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
    raw_file_queue, parsed_file_queue = spawn.Queue(bound), spawn.Queue(bound)
    progress = PipelineProgress()
    slots = [Slot(name, ephemeral_storage / "slots") for name in ("a", "b")]

    parsers = [spawn.Process(target=parser_process, name=f"parser-{n}", daemon=True, args=(
        settings.customer_code, str(ephemeral_storage / "parsed"), raw_file_queue, parsed_file_queue))
        for n in range(parser_process_count)]
    disk_usage = EphemeralDiskUsage(ephemeral_storage, stop_requested)
    threads = [SqsManager(settings, uri_queue, resolved_files, progress, stop_requested), disk_usage]
    threads += [threading.Thread(target=downloader_thread, name=f"downloader-{n}", daemon=True, args=(
        settings, ephemeral_storage, disk_usage, uri_queue, raw_file_queue, resolved_files, progress,
        stop_requested))
        for n in range(settings.downloader_thread_count)]
    threads.append(threading.Thread(target=stager_thread, name="stager", daemon=True, args=(
        settings, slots, parsed_file_queue, slots_to_export, resolved_files, progress, stop_requested)))
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
