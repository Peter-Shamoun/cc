"""The Claim Clarity pipeline against a running AWS Emulator (skipped when none is running).

The emulation runs in its own process: it points boto3 and Arrow at the emulator through environment variables,
which must not leak into the other tests. run_emulation.check_report holds the assertions: every delivered file
is in the lake exactly once, duplicate deliveries are skipped, the corrupt file ends in the dead-letter queue, the
task and its ephemeral storage are gone after the idle alarm, and a second candidate run selects nothing new.
"""
import hashlib
import json
import multiprocessing
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pytest

from ccsim import parser, silver_staging
from ccsim.edi_mock import Scenario, generate
from ccsim.silver_staging import file_id_of
from claim_clarity_pipeline import ingestion_task
from claim_clarity_pipeline.customer_settings import CustomerPipelineSettings
from claim_clarity_pipeline.ingestion_task import IngestFile, ParsedUnit, StagedUnitBuilder
from claim_clarity_pipeline.silver_lake import (INGEST_RUN_TABLE, LOSSLESS_ARROW_SCHEMAS, SILVER_TABLES,
                                                arrow_file_batches, file_ids_to_load_by_unit, read_arrow_file,
                                                rows_of_files)

EMULATOR_PORT = 9297
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def test_settings_round_trip_through_a_task_environment(monkeypatch):
    settings = CustomerPipelineSettings("acme_labs", slot_size_limit_bytes=123, slot_idle_export_seconds=1.5)
    for name, value in settings.to_environment().items():
        monkeypatch.setenv(name, value)
    assert CustomerPipelineSettings.from_environment() == settings
    assert settings.bucket_name == "claimclarity-prod-acme_labs"


def test_whole_design_end_to_end_on_the_emulator(tmp_path):
    if not (Path.home() / ".aws" / "emulator" / str(EMULATOR_PORT) / "emulator_credentials").exists():
        pytest.skip(f"no AWS Emulator running on port {EMULATOR_PORT} (start one: aws-emulator --telemetry off)")
    working_directory = tmp_path / "emulation"
    customer_code = f"test{uuid.uuid4().hex[:8]}"         # the emulator keeps earlier runs' customers
    completed = subprocess.run(
        [sys.executable, "-m", "claim_clarity_pipeline.run_emulation", "--working-directory", str(working_directory),
         "--customer-code", customer_code, "--mock-days", "20", "--emulator-port", str(EMULATOR_PORT)],
        cwd=REPOSITORY_ROOT, capture_output=True, text=True, timeout=1500)
    assert completed.returncode == 0, completed.stderr[-4000:]
    report = json.loads((working_directory / "emulation_report.json").read_text())
    assert report["silver_lake"]["files_in_lake"] > 100
    assert [event["event"] for event in report["fargate_events"]].count("task started") >= 3   # parser x2, candidates


# --- staged units: small files staged together (no emulator needed) --------------------------------------------------
@pytest.fixture(scope="module")
def raw_file_bodies(tmp_path_factory) -> list[bytes]:
    """Distinct small 837P / 835 files that parse."""
    out = tmp_path_factory.mktemp("mock-edi")
    truth = generate(out, Scenario.stress(days=3, volume_scale=.3), ["acme_labs"], return_claims=False)
    bodies = {}
    for f in truth["tenants"]["acme_labs"]["files"]:
        if f["expect"] == "parsed":
            body = (out / f"acme_labs/raw/received_date={f['day']}/{f['kind']}/{f['name']}").read_bytes()
            bodies[hashlib.sha256(body).hexdigest()] = body
    assert len(bodies) >= 6
    return list(bodies.values())


def _downloaded(directory: Path, s3_key: str, body: bytes, s3_version_id: str = "v1") -> IngestFile:
    """The file as a downloader thread hands it to a parser."""
    raw_file_path = directory / uuid.uuid4().hex
    raw_file_path.write_bytes(body)
    now = datetime(2026, 1, 1)
    return IngestFile("message-1", "bucket", s3_key, raw_file_path=str(raw_file_path), source_file_provenance=dict(
        s3_uri=f"s3://bucket/{s3_key}", s3_bucket="bucket", s3_key=s3_key, s3_version_id=s3_version_id,
        s3_etag="etag", s3_last_modified=now, s3_region="us-east-1", kms_key_arn=None,
        original_filename=s3_key.rsplit("/", 1)[-1], source_system="sftp", size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(), received_at=now, character_encoding="ascii"))


def _staged_units(tmp_path: Path, files: list[IngestFile]) -> list[ParsedUnit]:
    units = []
    builder = StagedUnitBuilder("acme_labs", str(tmp_path / "parsed"), units.append)
    for file in files:
        builder.add_raw_file(file)
    builder.write_unit()
    assert not builder.gathered_files and not any(rows for rows in builder.rows.rows.values())
    assert not any(Path(file.raw_file_path).exists() for file in files)          # parsed or failed: off the disk
    return units


def _unit_table(unit: ParsedUnit, table: str):
    return read_arrow_file(Path(unit.directory) / f"{table}.arrow")


def _file_id(file: IngestFile) -> str:
    return file_id_of(file.s3_key, file.source_file_provenance["s3_version_id"])


def _assert_unit_holds_exactly_its_files(unit: ParsedUnit):
    """One edi.source_file row per file with its S3 provenance; every table's rows belong to the unit's files;
    each file's segments are all there."""
    source_files = {row["file_id"]: row for row in _unit_table(unit, "edi.source_file").to_pylist()}
    assert sorted(source_files) == sorted(_file_id(file) for file in unit.files)
    for file in unit.files:
        row = source_files[_file_id(file)]
        assert (row["s3_key"], row["sha256"]) == (file.s3_key, file.source_file_provenance["sha256"])
    for arrow_file in Path(unit.directory).glob("*.arrow"):
        assert set(read_arrow_file(arrow_file)["file_id"].to_pylist()) <= set(source_files), arrow_file.name
    segments_by_file = _unit_table(unit, "edi.segment").group_by("file_id").aggregate([([], "count_all")])
    assert dict(zip(segments_by_file["file_id"].to_pylist(), segments_by_file["count_all"].to_pylist())) == {
        file_id: row["segment_count"] for file_id, row in source_files.items()}


def test_small_files_share_a_unit_and_a_file_that_fails_leaves_nothing_in_it(tmp_path, raw_file_bodies):
    good = [_downloaded(tmp_path, f"input/good_{n}.x12", body) for n, body in enumerate(raw_file_bodies[:4])]
    bad = _downloaded(tmp_path, "input/corrupt.x12", raw_file_bodies[4][:700])   # ends inside its envelope
    failed, unit = _staged_units(tmp_path, good[:2] + [bad] + good[2:])
    assert failed.directory is None and failed.files == [bad] and bad.failure.startswith("parse: ")
    assert unit.files == good and all(file.failure is None for file in good)
    _assert_unit_holds_exactly_its_files(unit)
    for file in good:                                     # what the evaluation sums per stage
        assert file.stage_seconds["parse"] > 0 and file.stage_seconds["wait_in_unit"] >= 0
        assert "queued_at" in file.stage_seconds and "gathered_at" not in file.stage_seconds


def test_every_lossless_table_carries_file_id():
    """What lets the exporter drop one file's rows from a unit (and recovery delete an interrupted export's)."""
    assert all("file_id" in LOSSLESS_ARROW_SCHEMAS[table].names for table in SILVER_TABLES if table != INGEST_RUN_TABLE)


def test_a_unit_is_written_at_its_size_limit_and_a_large_file_gets_its_own(tmp_path, raw_file_bodies, monkeypatch):
    bodies = sorted(raw_file_bodies, key=len)
    small, large = bodies[:3], bodies[-1]
    monkeypatch.setattr(ingestion_task, "STAGED_UNIT_RAW_BYTES_LIMIT", len(large))
    monkeypatch.setattr(parser, "TEXT_CHUNK_CHARS", 997)                # the large file is written in pieces
    monkeypatch.setattr(ingestion_task, "FLUSH_SEGMENTS", 50)
    monkeypatch.setattr(silver_staging, "FLUSH_MIN_ROWS", 3)
    files = [_downloaded(tmp_path, f"input/small_{n}.x12", body) for n, body in enumerate(small)]
    files.insert(1, _downloaded(tmp_path, "input/large.x12", large))
    units = _staged_units(tmp_path, files)
    assert [[file.s3_key for file in unit.files] for unit in units][:2] == [["input/small_0.x12"], ["input/large.x12"]]
    assert sorted(file.s3_key for unit in units for file in unit.files) == sorted(file.s3_key for file in files)
    for unit in units:
        _assert_unit_holds_exactly_its_files(unit)
    with pa.memory_map(str(Path(units[1].directory) / "edi.segment.arrow")) as large_file_segments:
        assert pa.ipc.open_file(large_file_segments).num_record_batches > 1

    truncated = _downloaded(tmp_path, "input/large_truncated.x12", large[:large.rindex(b"IEA")])
    monkeypatch.setattr(ingestion_task, "STAGED_UNIT_RAW_BYTES_LIMIT", 1000)   # fails after pieces were written
    (failed,) = _staged_units(tmp_path, [truncated])
    assert failed.directory is None and truncated.failure.startswith("parse: ")
    assert sorted(path.name for path in (tmp_path / "parsed").iterdir()) == sorted(Path(u.directory).name for u in units)


def test_a_unit_is_written_once_its_oldest_file_has_waited_long_enough(tmp_path, raw_file_bodies, monkeypatch):
    monkeypatch.setattr(ingestion_task, "STAGED_UNIT_MAX_AGE_SECONDS", 0.0)
    units = _staged_units(tmp_path, [_downloaded(tmp_path, f"input/{n}.x12", body)
                                     for n, body in enumerate(raw_file_bodies[:3])])
    assert [len(unit.files) for unit in units] == [1, 1, 1]


def test_a_second_delivery_of_the_same_object_goes_into_another_unit(tmp_path, raw_file_bodies):
    first, other, second = (_downloaded(tmp_path, key, body) for key, body in (
        ("input/a.x12", raw_file_bodies[0]), ("input/b.x12", raw_file_bodies[1]), ("input/a.x12", raw_file_bodies[0])))
    units = _staged_units(tmp_path, [first, other, second])
    assert [unit.files for unit in units] == [[first, other], [second]]
    with pytest.raises(ValueError, match="same file_id twice"):
        twice = pa.concat_tables([_unit_table(units[0], "edi.source_file"), _unit_table(units[1], "edi.source_file")])
        file_ids_to_load_by_unit({Path("unit"): twice}, set())


def test_rows_that_cannot_be_staged_fail_only_their_own_file(tmp_path, raw_file_bodies, monkeypatch):
    files = [_downloaded(tmp_path, f"input/{n}.x12", body) for n, body in enumerate(raw_file_bodies[:3])]
    conform = ingestion_task.conform_to_lossless_schema

    def conform_unless_bad_file(table, rows):
        if table == "edi.source_file" and "input/1.x12" in rows["s3_key"].to_pylist():
            raise ValueError("edi.source_file: NULL in NOT NULL column(s) ['injected']")
        return conform(table, rows)
    monkeypatch.setattr(ingestion_task, "conform_to_lossless_schema", conform_unless_bad_file)
    units = _staged_units(tmp_path, files)
    assert [(unit.directory is None, unit.files) for unit in units] == [
        (False, [files[0]]), (True, [files[1]]), (False, [files[2]])]
    assert files[1].failure.startswith("parse: ValueError") and files[0].failure is None and files[2].failure is None
    for unit in (units[0], units[2]):
        _assert_unit_holds_exactly_its_files(unit)
    assert len(list((tmp_path / "parsed").iterdir())) == 2


def test_export_takes_from_a_unit_only_the_rows_of_files_not_loaded_before(tmp_path, raw_file_bodies):
    """Dedup by content, with several files per unit: a copy of a loaded file, and a second copy in the slot."""
    in_lake, twice_in_slot, plain = raw_file_bodies[:3]
    first_unit, second_unit = (_staged_units(tmp_path, files)[0] for files in (
        [_downloaded(tmp_path, "input/b_already_in_lake.x12", in_lake),
         _downloaded(tmp_path, "input/c.x12.redelivered", twice_in_slot),
         _downloaded(tmp_path, "input/a.x12", plain)],
        [_downloaded(tmp_path, "input/c.x12", twice_in_slot)]))
    commit_records = {Path(unit.directory): _unit_table(unit, "edi.source_file") for unit in (first_unit, second_unit)}
    to_load = file_ids_to_load_by_unit(commit_records, {hashlib.sha256(in_lake).hexdigest()})
    assert to_load == {Path(first_unit.directory): [_file_id(first_unit.files[2])],      # a.x12
                       Path(second_unit.directory): [_file_id(second_unit.files[0])]}    # c.x12: first by S3 key
    loaded_from_first_unit = to_load[Path(first_unit.directory)]
    for table in ("edi.segment", "edi.source_file", "edi.interchange"):
        rows = rows_of_files(_unit_table(first_unit, table), loaded_from_first_unit)
        assert set(rows["file_id"].to_pylist()) == set(loaded_from_first_unit)
    (segments,) = arrow_file_batches(Path(first_unit.directory) / "edi.segment.arrow")
    source_file = rows_of_files(commit_records[Path(first_unit.directory)], loaded_from_first_unit).to_pylist()[0]
    assert rows_of_files(segments, loaded_from_first_unit).num_rows == source_file["segment_count"]
    assert source_file["s3_key"] == "input/a.x12"


def test_a_parser_process_does_not_hold_files_back_when_no_more_arrive(tmp_path, raw_file_bodies):
    spawn = multiprocessing.get_context("spawn")
    raw_file_queue, parsed_unit_queue = spawn.Queue(), spawn.Queue()
    parser = spawn.Process(target=ingestion_task.parser_process, daemon=True, args=(
        "acme_labs", str(tmp_path / "parsed"), raw_file_queue, parsed_unit_queue))
    parser.start()
    try:
        for number, body in enumerate(raw_file_bodies[:2]):
            file = _downloaded(tmp_path, f"input/{number}.x12", body)
            file.stage_seconds["queued_at"] = time.monotonic()
            raw_file_queue.put(file)
        files = []
        while len(files) < 2:                             # far below the size limit, and the parser keeps running
            files += parsed_unit_queue.get(timeout=60).files
        assert parser.is_alive() and all(file.failure is None for file in files)
        assert all(file.stage_seconds["wait_in_unit"] < 5 for file in files)
    finally:
        raw_file_queue.put(None)
        parser.join(timeout=30)
    assert parser.exitcode == 0
