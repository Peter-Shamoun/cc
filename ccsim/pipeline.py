"""Fargate parser task (bronze -> staging -> silver, quarantine on failure) and the gold rebuild.

Runs entirely with the task's customer-scoped credentials; temp files live in a TemporaryDirectory
that is gone when the task ends (stand-in for the task's ephemeral storage)."""
from __future__ import annotations

import hashlib
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa

from . import edi_mock
from .parser import TABLES, ParseError, parse_file

ROOT = Path(__file__).resolve().parent.parent
DDL_LOSSLESS = (ROOT / "claude/schema/01_lossless_edi_schema.sql").read_text()
DDL_SEMANTIC = (ROOT / "claude/schema/02_semantic_layer_views.sql").read_text()
SEM_TABLES = ["remit_claims", "remit_lines", "claims", "claim_lines", "adjustments", "remarks", "payments",
              "provider_adjustments", "diagnoses", "payers"]
SILVER_TABLES = ["edi.ingest_run"] + TABLES
PARSER_VERSION = "ccsim-0.1"


def seed_ref(con: duckdb.DuckDBPyConnection):
    """ref.* is config, versioned in git with the view SQL (not customer data)."""
    con.executemany("INSERT INTO ref.denial_carc VALUES (?, 'v3-placeholder', 'mock', DATE '2026-10-01', NULL)",
                    [[c] for c in edi_mock.DENIAL_CARCS])
    con.execute("INSERT INTO ref.tenant_setting VALUES ('status4_is_recorded_denial', 'true', 'default profile')")
    for name, pid, _, cls, *_ in edi_mock.PAYERS:
        key = name.replace(" ", "_")
        for src in ("835_1000A", "837_2010BB"):
            con.execute("INSERT INTO ref.payer_alias VALUES (?, ?, ?, ?, ?, ?, 'exact_id')",
                        [src, pid, name, key, name.title(), cls])
    con.executemany("INSERT INTO ref.carc (code, description) VALUES (?, ?)", [
        ["1", "Deductible"], ["2", "Coinsurance"], ["3", "Copay"], ["45", "Charge exceeds fee schedule"],
        ["16", "Claim lacks information"], ["50", "Not medically necessary"], ["197", "Precert/authorization absent"],
        ["27", "Expenses after coverage terminated"], ["96", "Non-covered charge"], ["109", "Not covered by this payer"],
        ["167", "Diagnosis not covered"], ["204", "Service not covered under benefit plan"], ["11", "Dx inconsistent with procedure"],
        ["4", "Modifier inconsistent"]])
    for code, (_, _, _, _, desc) in edi_mock.CPTS.items():
        con.execute("INSERT INTO ref.procedure_code VALUES (?, 'CPT', ?, 'lab')", [code, desc])


def _insert(con, table: str, rows: list[dict]):
    if not rows:
        return
    con.register("_rows", pa.Table.from_pylist(rows))
    con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM _rows")
    con.unregister("_rows")


def parse_batch(ctx) -> dict:
    """ECS task entrypoint. overrides = {customer, received_date, bucket}."""
    c, day, B = ctx.overrides["customer"], ctx.overrides["received_date"], ctx.overrides["bucket"]
    run_id = f"run_{day.replace('-', '')}_{uuid.uuid4().hex[:6]}"
    s3 = ctx.client("s3")
    started = datetime.now(UTC).replace(tzinfo=None)
    raw_keys = [o["Key"] for o in s3.list_objects_v2(Bucket=B, Prefix=f"{c}/raw/received_date={day}/")["Contents"]
                if o["Key"].endswith(".x12")]

    known = {}                                            # sha256 -> file_id already in silver
    silver = s3.local_prefix(B, f"{c}/parquet/")
    sf_dir = silver / "edi.source_file"
    if sf_dir.exists():
        for sha, fid in duckdb.execute(f"SELECT sha256, file_id FROM read_parquet('{sf_dir}/**/*.parquet', hive_partitioning=false) "
                                   "WHERE duplicate_of_file_id IS NULL").fetchall():
            known[sha] = fid

    con = duckdb.connect()
    con.execute(DDL_LOSSLESS)
    _insert(con, "edi.ingest_run", [dict(ingest_run_id=run_id, started_at=started, status="SUCCEEDED",
                                          parser_name="ccsim.parser", parser_version=PARSER_VERSION, schema_version="1",
                                          triggered_by=ctx.task_arn)])
    loaded = rejected = 0
    for key in raw_keys:
        obj = s3.get_object(Bucket=B, Key=key)
        body = obj["Body"]
        sha = hashlib.sha256(body).hexdigest()
        file_id = hashlib.sha1(key.encode()).hexdigest()[:16]
        s3meta = dict(s3_uri=f"s3://{B}/{key}", s3_bucket=B, s3_key=key, size_bytes=len(body), sha256=sha,
                      s3_region="us-west-2", kms_key_arn=obj["kms_key_id"], original_filename=key.rsplit("/", 1)[-1],
                      source_system="sftp", received_at=datetime.fromisoformat(day), character_encoding="ascii")
        try:
            rows = parse_file(body.decode("ascii"), file_id, c, s3meta, run_id, started)
        except (ParseError, UnicodeDecodeError, IndexError, ValueError) as e:
            rejected += 1
            s3.put_object(Bucket=B, Key=f"{c}/quarantine/{run_id}/{key.rsplit('/', 1)[-1]}", Body=body)
            _insert(con, "edi.source_file", [dict(file_id=file_id, tenant_code=c, file_kind="UNKNOWN", ingest_run_id=run_id,
                                                  ingested_at=started, parse_status="REJECTED", parse_messages=str(e)[:500],
                                                  **s3meta)])
            continue
        if sha in known:
            rows["edi.source_file"][0]["duplicate_of_file_id"] = known[sha]
        else:
            known[sha] = file_id
        for t in TABLES:
            _insert(con, t, rows.get(t, []))
        loaded += 1
    con.execute("UPDATE edi.ingest_run SET finished_at = ?, files_seen = ?, files_loaded = ?, files_rejected = ? "
                "WHERE ingest_run_id = ?", [datetime.now(UTC).replace(tzinfo=None), len(raw_keys), loaded, rejected, run_id])
    month = day[:7]
    with tempfile.TemporaryDirectory() as tmp:
        for t in SILVER_TABLES:                           # staging: one file per table for this run
            p = Path(tmp) / f"{t}.parquet"
            con.execute(f"COPY (SELECT * FROM {t}) TO '{p}' (FORMAT parquet)")
            s3.put_object(Bucket=B, Key=f"{c}/staging/{run_id}/{t}.parquet", Body=p.read_bytes())
    for t in SILVER_TABLES:                               # promote, then flush staging
        sk = f"{c}/staging/{run_id}/{t}.parquet"
        s3.copy_object(Bucket=B, Key=f"{c}/parquet/{t}/received_month={month}/part-{run_id}.parquet",
                       CopySource={"Bucket": B, "Key": sk})
        s3.delete_object(Bucket=B, Key=sk)
    build_semantic_layer(s3, B, c, run_id)
    return dict(run_id=run_id, files_seen=len(raw_keys), loaded=loaded, rejected=rejected)


def load_silver(con: duckdb.DuckDBPyConnection, silver: Path):
    con.execute(DDL_LOSSLESS)
    seed_ref(con)
    for t in SILVER_TABLES:
        d = silver / t
        if d.exists() and any(d.rglob("*.parquet")):
            con.execute(f"INSERT INTO {t} BY NAME SELECT * FROM read_parquet('{d}/**/*.parquet', union_by_name=true, hive_partitioning=false)")
    con.execute(DDL_SEMANTIC)


def build_semantic_layer(s3, B: str, c: str, run_id: str, keep_previous: int = 1):
    """Full gold rebuild from silver: run the git-versioned view SQL, write every sem.* view to
    semantic_layer/v=<run_id>/, then flip CURRENT (readers never see a half-built version)."""
    con = duckdb.connect()
    load_silver(con, s3.local_prefix(B, f"{c}/parquet/"))
    with tempfile.TemporaryDirectory() as tmp:
        for v in SEM_TABLES:
            p = Path(tmp) / f"{v}.parquet"
            con.execute(f"COPY (SELECT * FROM sem.{v}) TO '{p}' (FORMAT parquet)")
            s3.put_object(Bucket=B, Key=f"{c}/semantic_layer/v={run_id}/sem.{v}/part-0.parquet", Body=p.read_bytes())
    s3.put_object(Bucket=B, Key=f"{c}/semantic_layer/CURRENT", Body=f"v={run_id}")
    versions = sorted({o["Key"].split("/")[2] for o in s3.list_objects_v2(Bucket=B, Prefix=f"{c}/semantic_layer/v=")["Contents"]})
    for old in versions[:-(keep_previous + 1)]:
        for o in s3.list_objects_v2(Bucket=B, Prefix=f"{c}/semantic_layer/{old}/")["Contents"]:
            s3.delete_object(Bucket=B, Key=o["Key"])
