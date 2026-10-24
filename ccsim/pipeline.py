"""The Fargate parser task: bronze -> staging -> silver (quarantine on failure) -> gold.

Written against stock boto3 and DuckDB httpfs, configured only by environment variables and the task-role
credentials, so the same function body runs in the container in prod. Temp files live in a
TemporaryDirectory (the task's ephemeral storage), which is removed when the task ends.

Guarantees, each covered by a test:
  - Idempotent: a re-delivered _SUCCESS event (EventBridge is at-least-once) loads nothing twice. Raw
    objects already recorded in silver edi.source_file (same key + version) are skipped.
  - Atomic gold: readers follow CURRENT, which is flipped with a conditional write only after every gold
    file is written, and only if no newer build has been published in the meantime.
  - PHI boundary: gold holds the sem.* views plus PHI-stripped copies of the lossless tables (for agent
    escalation). Nothing in gold carries patient names, member ids, birth dates, addresses or bank accounts.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
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
PARSER_VERSION = "ccsim-0.2"
PERSON_ENTITY_CODES = ("IL", "QC", "74", "QD")

# Lossless tables the agent may escalate to, as PHI-stripped projections (materialized into gold)
ESCALATION = {
    "c837.claim": "SELECT * FROM c837.claim",
    "c837.service_line": "SELECT * FROM c837.service_line",
    "c837.claim_code": "SELECT * FROM c837.claim_code",
    "c837.hl": ("SELECT * EXCLUDE (pat05_date_format, pat06_death_date, pat07_weight_unit, pat08_weight, pat09_pregnancy) "
                "FROM c837.hl"),
    "r835.claim_payment": "SELECT * FROM r835.claim_payment",
    "r835.service_payment": "SELECT * FROM r835.service_payment",
    "r835.remark": "SELECT * FROM r835.remark",
    "r835.provider_adjustment": "SELECT * FROM r835.provider_adjustment",
    "edi.adjustment": "SELECT * FROM edi.adjustment",
    "edi.date_time": "SELECT * FROM edi.date_time",
    "edi.amount": "SELECT * FROM edi.amount",
    "edi.reference": ("SELECT r.* FROM edi.reference r LEFT JOIN edi.entity e ON e.entity_uid = r.owner_uid "
                      f"WHERE e.entity_id_code IS NULL OR e.entity_id_code NOT IN {PERSON_ENTITY_CODES}"),
    "edi.source_file": "SELECT * EXCLUDE (original_filename) FROM edi.source_file",
    "r835.payment": ("SELECT * EXCLUDE (bpr07_sender_dfi_id, bpr09_sender_account, bpr13_receiver_dfi_id, "
                     "bpr15_receiver_account) FROM r835.payment"),
    "edi.entity": ("SELECT * EXCLUDE (name_last_or_org, name_first, name_middle, name_prefix, name_suffix, id_value, "
                   "addr_line1, addr_line2, city, postal_code, dmg_birth_date, dmg_gender), "
                   f"CASE WHEN entity_id_code IN {PERSON_ENTITY_CODES} THEN NULL ELSE name_last_or_org END AS org_name, "
                   f"CASE WHEN entity_id_code IN {PERSON_ENTITY_CODES} THEN NULL ELSE id_value END AS org_id "
                   "FROM edi.entity"),
}


def seed_ref(con: duckdb.DuckDBPyConnection):
    """ref.* is configuration, versioned in git next to the view SQL (it is not customer data)."""
    con.executemany("INSERT INTO ref.denial_carc VALUES (?, 'v3-placeholder', 'mock', DATE '2026-10-01', NULL)",
                    [[c] for c in edi_mock.DENIAL_CARCS])
    con.execute("INSERT INTO ref.tenant_setting VALUES ('status4_is_recorded_denial', 'true', 'default profile')")
    for p in edi_mock.PAYERS:                                 # the payer resolver's reviewed output
        for src, name in (("837_2010BB", p.name), ("835_1000A", p.name_835 or p.name)):
            con.execute("INSERT INTO ref.payer_alias VALUES (?, ?, ?, ?, ?, ?, 'exact_id')",
                        [src, p.payer_id, name, p.name.replace(" ", "_"), p.name.title(), p.payer_class])
    con.executemany("INSERT INTO ref.carc (code, description) VALUES (?, ?)", list(edi_mock.CARC_DESCRIPTIONS.items()))
    for code, cpt in edi_mock.CPTS.items():
        con.execute("INSERT INTO ref.procedure_code VALUES (?, 'CPT', ?, 'lab')", [code, cpt.description])


def _insert(con, table: str, rows: list[dict]):
    if not rows:
        return
    # Columns are the union of every row's keys: pa.Table.from_pylist infers them from the first row only and
    # would silently drop fields the first row lacks (an HL-20 has no SBR, a submitter NM1 has no address).
    keys = list(dict.fromkeys(k for r in rows for k in r))
    con.register("_rows", pa.table({k: [r.get(k) for r in rows] for k in keys}))
    con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM _rows")
    con.unregister("_rows")


def _clients(env):
    """In prod: boto3.client("s3") from the task role, and DuckDB's credential_chain provider."""
    return env.aws.boto3_client("s3", env.credentials)


def _duck(env, scope: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(config={"autoinstall_known_extensions": False, "autoload_known_extensions": False})
    con.execute("LOAD httpfs")
    con.execute(env.aws.duckdb_secret_sql(env.credentials, scope))
    return con


def _list(s3, bucket, prefix):
    keys, token = [], None
    while True:
        kw = dict(Bucket=bucket, Prefix=prefix, **({"ContinuationToken": token} if token else {}))
        r = s3.list_objects_v2(**kw)
        keys += [o["Key"] for o in r.get("Contents", [])]
        if not r.get("IsTruncated"):
            return keys
        token = r["NextContinuationToken"]


def _now(env) -> datetime:
    return datetime.fromtimestamp(env.aws.clock(), UTC).replace(tzinfo=None)


def parse_batch(env) -> dict:
    """ECS entrypoint. env.environment: CUSTOMER, BUCKET, KMS_KEY_ARN, SUCCESS_KEY (from the event)."""
    e = env.environment
    c, B, kms = e["CUSTOMER"], e["BUCKET"], e["KMS_KEY_ARN"]
    day_prefix = e["SUCCESS_KEY"].rsplit("/", 1)[0] + "/"            # <c>/raw/received_date=YYYY-MM-DD/
    day = day_prefix.rstrip("/").rsplit("=", 1)[1]
    started = _now(env)
    run_id = f"run_{started:%Y%m%dT%H%M%S%f}_{day.replace('-', '')}"
    s3 = _clients(env)
    put = lambda key, body: s3.put_object(Bucket=B, Key=key, Body=body, ServerSideEncryption="aws:kms", SSEKMSKeyId=kms)

    raw = [k for k in _list(s3, B, day_prefix) if not k.endswith("/_SUCCESS")]
    silver = _duck(env, f"s3://{B}/{c}/")
    loaded, known_sha = set(), {}
    if _list(s3, B, f"{c}/parquet/edi.source_file/"):
        for key, vid, sha, fid, dup in silver.execute(
                f"SELECT s3_key, s3_version_id, sha256, file_id, duplicate_of_file_id FROM read_parquet("
                f"'s3://{B}/{c}/parquet/edi.source_file/**/*.parquet', hive_partitioning=false)").fetchall():
            loaded.add((key, vid))
            if dup is None:
                known_sha.setdefault(sha, fid)

    con = duckdb.connect()
    con.execute(DDL_LOSSLESS)
    stats = dict(run_id=run_id, files_seen=len(raw), loaded=0, rejected=0, skipped=0)
    _insert(con, "edi.ingest_run", [dict(ingest_run_id=run_id, started_at=started, status="SUCCEEDED",
                                          parser_name="ccsim.parser", parser_version=PARSER_VERSION, schema_version="1",
                                          triggered_by=env.task_arn, notes=e.get("EVENT_ID"))])
    for key in raw:
        head = s3.head_object(Bucket=B, Key=key)
        vid = head.get("VersionId")
        if (key, vid) in loaded:
            stats["skipped"] += 1
            continue
        obj = s3.get_object(Bucket=B, Key=key, VersionId=vid) if vid else s3.get_object(Bucket=B, Key=key)
        body = obj["Body"].read()
        sha = hashlib.sha256(body).hexdigest()
        file_id = hashlib.sha1(f"{key}#{vid}".encode()).hexdigest()[:16]
        meta = dict(s3_uri=f"s3://{B}/{key}", s3_bucket=B, s3_key=key, s3_version_id=vid, s3_etag=head["ETag"].strip('"'),
                    s3_last_modified=datetime.fromtimestamp(head["LastModified"].timestamp(), UTC).replace(tzinfo=None),
                    s3_region="us-west-2", kms_key_arn=head.get("SSEKMSKeyId"), original_filename=key.rsplit("/", 1)[-1],
                    source_system="sftp", size_bytes=len(body), sha256=sha, received_at=datetime.fromisoformat(day),
                    character_encoding="ascii")
        try:
            rows = parse_file(body.decode("ascii"), file_id, c, meta, run_id, started)
        except (ParseError, UnicodeDecodeError, IndexError, ValueError, KeyError, TypeError) as err:
            stats["rejected"] += 1
            put(f"{c}/quarantine/{run_id}/{key.rsplit('/', 1)[-1]}", body)
            _insert(con, "edi.source_file", [dict(file_id=file_id, tenant_code=c, file_kind="UNKNOWN",
                                                  ingest_run_id=run_id, ingested_at=started, parse_status="REJECTED",
                                                  parse_messages=[f"{type(err).__name__}: {err}"[:500]], **meta)])
            continue
        if sha in known_sha:
            rows["edi.source_file"][0]["duplicate_of_file_id"] = known_sha[sha]
        else:
            known_sha[sha] = file_id
        for t in TABLES:
            _insert(con, t, rows.get(t, []))
        stats["loaded"] += 1
    if stats["loaded"] == 0 and stats["rejected"] == 0:
        return dict(stats, status="noop")                        # duplicate delivery: nothing new
    con.execute("UPDATE edi.ingest_run SET finished_at = ?, files_seen = ?, files_loaded = ?, files_rejected = ? "
                "WHERE ingest_run_id = ?", [_now(env), len(raw), stats["loaded"], stats["rejected"], run_id])

    month = day[:7]
    with tempfile.TemporaryDirectory() as tmp:
        for t in SILVER_TABLES:
            p = Path(tmp) / f"{t}.parquet"
            con.execute(f"COPY (SELECT * FROM {t}) TO '{p}' (FORMAT parquet)")
            put(f"{c}/staging/{run_id}/{t}.parquet", p.read_bytes())
    for t in SILVER_TABLES:                                       # promote, then flush staging
        sk = f"{c}/staging/{run_id}/{t}.parquet"
        s3.copy_object(Bucket=B, Key=f"{c}/parquet/{t}/received_month={month}/part-{run_id}.parquet",
                       CopySource={"Bucket": B, "Key": sk}, ServerSideEncryption="aws:kms", SSEKMSKeyId=kms)
        s3.delete_object(Bucket=B, Key=sk)
    stats["published"] = build_gold(env, s3, silver, B, c, kms, run_id)
    return dict(stats, status="ok")


def load_silver(con: duckdb.DuckDBPyConnection, silver_uri: str, present: set[str]):
    """Silver -> in-memory tables the view SQL can run on. The DDL runs in a scratch catalog only for its
    column types and ref.* seeds; the working tables are created without PK/FK checks (silver was
    validated against the constrained DDL when it was parsed), which is ~5x faster on a large history."""
    con.execute("ATTACH ':memory:' AS ddl")
    con.execute("USE ddl")
    con.execute(DDL_LOSSLESS)
    seed_ref(con)
    con.execute("USE memory")
    tables = con.execute("SELECT schema_name, table_name FROM duckdb_tables() WHERE database_name = 'ddl'").fetchall()
    for schema in sorted({sch for sch, _ in tables}):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    for schema, name in tables:
        t = f"{schema}.{name}"
        if t in present:
            con.execute(f"CREATE TABLE {t} AS SELECT * FROM read_parquet('{silver_uri}/{t}/**/*.parquet', "
                        "union_by_name=true, hive_partitioning=false)")
        else:
            con.execute(f"CREATE TABLE {t} AS SELECT * FROM ddl.{t}")
    con.execute("DETACH ddl")
    con.execute(DDL_SEMANTIC)


def build_gold(env, s3, silver_con, B: str, c: str, kms: str, run_id: str, keep_previous: int = 1) -> bool:
    """Full rebuild of gold from silver, then compare-and-set CURRENT. Returns True if published."""
    present = {k.split("/")[2] for k in _list(s3, B, f"{c}/parquet/")}
    load_silver(silver_con, f"s3://{B}/{c}/parquet", present)
    put = lambda key, body, **kw: s3.put_object(Bucket=B, Key=key, Body=body, ServerSideEncryption="aws:kms",
                                                SSEKMSKeyId=kms, **kw)
    base = f"{c}/semantic_layer/v={run_id}"
    with tempfile.TemporaryDirectory() as tmp:
        outputs = [(f"sem.{v}", f"SELECT * FROM sem.{v}") for v in SEM_TABLES]
        outputs += [(f"lossless.{t.replace('.', '_')}", sql) for t, sql in ESCALATION.items()]
        for name, sql in outputs:
            p = Path(tmp) / f"{name}.parquet"
            silver_con.execute(f"COPY ({sql}) TO '{p}' (FORMAT parquet)")
            put(f"{base}/{name}/part-0.parquet", p.read_bytes())
    # compare-and-set: only move CURRENT forward
    from botocore.exceptions import ClientError
    for _ in range(5):
        try:
            cur = s3.get_object(Bucket=B, Key=f"{c}/semantic_layer/CURRENT")
            cur_run, etag = cur["Body"].read().decode().removeprefix("v="), cur["ETag"]
        except ClientError as err:
            if err.response["Error"]["Code"] not in ("NoSuchKey", "404"):
                raise
            cur_run, etag = None, None
        if cur_run is not None and cur_run >= run_id:
            return False                                         # a newer build already published
        try:
            put(f"{c}/semantic_layer/CURRENT", f"v={run_id}".encode(), **({"IfMatch": etag} if etag else {"IfNoneMatch": "*"}))
            break
        except ClientError as err:
            if err.response["Error"]["Code"] != "PreconditionFailed":
                raise
    else:
        raise RuntimeError("could not publish CURRENT after 5 attempts")
    versions = sorted({k.split("/")[2] for k in _list(s3, B, f"{c}/semantic_layer/v=")})
    for old in versions[:-(keep_previous + 1)]:
        for k in _list(s3, B, f"{c}/semantic_layer/{old}/"):
            s3.delete_object(Bucket=B, Key=k)                     # delete markers; lifecycle expires the data
    return True
