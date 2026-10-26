"""The parser's speed-ups must not change a row: chunked text, mid-file flushes, rejections after a flush and the
worker pool all stage exactly what a plain run stages.
(bench/parser_bench.py proves the same against the pre-optimisation parser on a production-size corpus.)"""
import hashlib
from datetime import datetime

import duckdb
import pytest

from ccsim import parser, silver_staging
from ccsim.edi_mock import Scenario, generate
from ccsim.parser import DDL_LOSSLESS, TABLES
from ccsim.silver_staging import RawFile, SilverBatch, file_id_of

STARTED = datetime(2026, 1, 1)


def _meta(key, body):
    return dict(s3_uri=f"s3://b/{key}", s3_bucket="b", s3_key=key, size_bytes=len(body),
                sha256=hashlib.sha256(body).hexdigest())


@pytest.fixture(scope="module")
def raw_files(tmp_path_factory):
    out = tmp_path_factory.mktemp("parser")
    truth = generate(out, Scenario.stress(days=12, volume_scale=.3), ["acme_labs"], return_claims=False)
    files = []
    for f in truth["tenants"]["acme_labs"]["files"]:
        key = f"acme_labs/raw/received_date={f['day']}/{f['kind']}/{f['name']}"
        body = (out / key).read_bytes()
        files.append(RawFile(key, body, file_id_of(key, "v1"), _meta(key, body)))
    assert any(f["expect"] == "rejected" for f in truth["tenants"]["acme_labs"]["files"])
    big = b"".join(f.body for f in files if f.body.startswith(b"ISA*") and b"\x00" not in f.body)
    truncated = big[: big.rindex(b"IEA")]                    # rejected only at its very end, after flushes
    files.append(RawFile("truncated", truncated, "truncatedfile000", _meta("truncated", truncated)))
    return files


def _staged(files, workers=1) -> dict:
    batch = SilverBatch("r1", STARTED, triggered_by="test", notes=None, parser_version="test", workers=workers)
    loaded = [ok for _, ok in batch.add_raw_files(files, "acme_labs", {})]
    con = batch.finish()
    return dict(loaded=loaded,
                tables={t: con.sql(f"SELECT * FROM {t}").to_arrow_table() for t in ["edi.ingest_run"] + TABLES})


@pytest.fixture(scope="module")
def plain(raw_files):
    return _staged(raw_files)


def _assert_same(a, b):
    assert a["loaded"] == b["loaded"]
    for t, data in a["tables"].items():
        assert data.equals(b["tables"][t]), t


def test_keys_are_unique_and_foreign_keys_hold_under_the_full_ddl(raw_files, plain):
    """Staging has no key indexes (speed); the constrained DDL accepts every staged row."""
    assert plain["loaded"][-1] is False and plain["loaded"].count(False) >= 2
    con = duckdb.connect()
    con.execute(DDL_LOSSLESS)
    for t, data in plain["tables"].items():
        con.register("staged", data)
        con.execute(f"INSERT INTO {t} SELECT * FROM staged")
        con.unregister("staged")
    assert con.execute("SELECT count(*) FROM edi.segment WHERE file_id = 'truncatedfile000'").fetchone()[0] == 0


def test_small_text_chunks_and_frequent_flushes_change_nothing(raw_files, plain, monkeypatch):
    monkeypatch.setattr(parser, "TEXT_CHUNK_CHARS", 997)
    monkeypatch.setattr(silver_staging, "FLUSH_SEGMENTS", 300)
    monkeypatch.setattr(silver_staging, "FLUSH_MIN_ROWS", 3)
    _assert_same(plain, _staged(raw_files))


def test_worker_pool_changes_nothing(raw_files, plain, monkeypatch):
    monkeypatch.setattr(silver_staging, "POOL_MAX_FILE_BYTES", 200_000)   # big files still go in-process
    _assert_same(plain, _staged(raw_files, workers=2))
