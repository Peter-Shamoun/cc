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
import re
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa

from . import edi_mock
from .lake import (COMMIT_TABLE, Silver, Store, acquire_lock, gold_key, manifest_files, manifest_key, publish, read_current,
                   read_manifest, release_lock, run_month, silver_state)
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


# --- lake configuration -----------------------------------------------------------------------------------------
# Pipeline configuration (task-definition environment):
#   GOLD_MODE=incremental (default)  recompute only the claims the new batch touched, rewrite only their partitions
#   GOLD_MODE=full                   rebuild all of gold from all of silver every batch (what a rules change does)
PARQUET = "FORMAT parquet, COMPRESSION zstd"         # silver: default row groups keep lookups by file_id selective
GOLD_PARQUET = PARQUET + ", ROW_GROUP_SIZE 1048576"   # gold: fewer, larger row groups = fewer range requests per query
GOLD_LAYOUT = "cohort-tiered-3"
OPEN_MONTHS = 3          # newest months kept as their own gold partition; older months fold into one file per quarter
CLAIM_RELATIONS = [f"sem.{t}" for t in ("remit_claims", "remit_lines", "claims", "claim_lines", "adjustments", "remarks",
                                        "diagnoses")]                    # partitioned by claim cohort, copy-on-write
SMALL_RELATIONS = ["sem.payments", "sem.provider_adjustments", "sem.payers"]   # one file each, rewritten per batch
APPEND_RELATIONS = {f"lossless.{t.replace('.', '_')}": sql for t, sql in ESCALATION.items()}  # per received month
RELATIONS = CLAIM_RELATIONS + SMALL_RELATIONS + list(APPEND_RELATIONS)
GOLD_SORT = {rel: "payer_key, claim_key" for rel in CLAIM_RELATIONS}   # low-cardinality first: ~9% fewer bytes read
VIEW_INPUTS = sorted(set(ESCALATION) | {"edi.transaction_set"})          # the silver tables gold is computed from
assert sorted(CLAIM_RELATIONS + SMALL_RELATIONS) == sorted(f"sem.{t}" for t in SEM_TABLES)

_CP_SP = ("SELECT claim_payment_uid FROM r835.claim_payment UNION ALL SELECT service_payment_uid FROM r835.service_payment")
# A touched claim's rows in older silver files, table by table (each filter may use the tables loaded before it).
CLOSURE = [
    ("c837.claim", "clm01_patient_control_number IN (SELECT claim_key FROM touched_claim)"),
    ("r835.claim_payment", "clp01_patient_control_number IN (SELECT claim_key FROM touched_claim)"),
    ("c837.service_line", "claim_uid IN (SELECT claim_uid FROM c837.claim)"),
    ("c837.claim_code", "claim_uid IN (SELECT claim_uid FROM c837.claim)"),
    ("c837.hl", "hl_uid IN (SELECT subscriber_hl_uid FROM c837.claim UNION ALL SELECT billing_hl_uid FROM c837.claim)"),
    ("edi.transaction_set", "txn_uid IN (SELECT txn_uid FROM c837.claim)"),
    ("r835.payment", "payment_uid IN (SELECT payment_uid FROM r835.claim_payment)"),
    ("r835.service_payment", "claim_payment_uid IN (SELECT claim_payment_uid FROM r835.claim_payment)"),
    ("r835.remark", f"owner_uid IN ({_CP_SP})"),
    ("r835.provider_adjustment", "payment_uid IN (SELECT payment_uid FROM r835.payment)"),
    ("edi.adjustment", f"owner_uid IN ({_CP_SP})"),
    ("edi.amount", "owner_uid IN (SELECT service_payment_uid FROM r835.service_payment)"),
    ("edi.date_time", f"owner_uid IN ({_CP_SP} UNION ALL SELECT payment_uid FROM r835.payment "
                      "UNION ALL SELECT line_uid FROM c837.service_line)"),
    ("edi.entity", "owner_uid IN (SELECT payment_uid FROM r835.payment UNION ALL SELECT hl_uid FROM c837.hl "
                   "UNION ALL SELECT claim_uid FROM c837.claim)"),
    ("edi.reference", "owner_uid IN (SELECT entity_uid FROM edi.entity)"),
    ("edi.source_file", "true"),
]
assert sorted(t for t, _ in CLOSURE) == VIEW_INPUTS


def _clients(env):
    """In prod: boto3.client("s3") from the task role, and DuckDB's credential_chain provider."""
    return env.aws.boto3_client("s3", env.credentials)


def _duck(env, scope: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(config={"autoinstall_known_extensions": False, "autoload_known_extensions": False})
    con.execute("LOAD httpfs")
    con.execute(env.aws.duckdb_secret_sql(env.credentials, scope))
    return con


def _now(env) -> datetime:
    return datetime.fromtimestamp(env.aws.clock(), UTC).replace(tzinfo=None)


def _scan(store: Store, keys, options: str = "") -> str:
    """read_parquet over exact keys (no globbing, no LIST). Partition directories are layout, not columns."""
    return "read_parquet([" + ", ".join(f"'{store.uri(k)}'" for k in keys) + f"], hive_partitioning=false{options})"


def _copy(con, sql: str, path: Path, options: str = PARQUET) -> int:
    """COPY a query to a local Parquet file; returns the row count."""
    return con.execute(f"COPY ({sql}) TO '{path}' ({options})").fetchone()[0]


# --- parser task ------------------------------------------------------------------------------------------------
def parse_batch(env) -> dict:
    """ECS entrypoint. env.environment: CUSTOMER, BUCKET, KMS_KEY_ARN, SUCCESS_KEY (from the event), and the
    pipeline configuration (GOLD_MODE)."""
    e = env.environment
    c, B, kms = e["CUSTOMER"], e["BUCKET"], e["KMS_KEY_ARN"]
    store = Store(_clients(env), B, c, kms)
    lock = acquire_lock(store, env.task_arn, env.aws.clock)
    try:
        return _parse_batch_locked(env, store)
    finally:
        release_lock(store, lock)


def _parse_batch_locked(env, store: Store) -> dict:
    e = env.environment
    c, B = store.customer, store.bucket
    s3 = store.s3
    day_prefix = e["SUCCESS_KEY"].rsplit("/", 1)[0] + "/"            # <c>/raw/received_date=YYYY-MM-DD/
    day = day_prefix.rstrip("/").rsplit("=", 1)[1]
    started = _now(env)                                              # under the lock: run ids increase per customer
    run_id = f"run_{started:%Y%m%dT%H%M%S%f}_{day.replace('-', '')}"
    stats = dict(run_id=run_id, loaded=0, rejected=0, skipped=0, gold_mode=e.get("GOLD_MODE", "incremental"))
    t0 = time.perf_counter()

    silver = silver_state(store)
    if silver.garbage:                                               # parts of a crashed batch, compacted-over parts
        store.delete(silver.garbage)
    raw = [k for k, _ in store.list(day_prefix) if not k.endswith("/_SUCCESS")]
    stats["files_seen"] = len(raw)
    gold_con = _duck(env, f"s3://{B}/{c}/")
    loaded, known_sha = set(), {}
    if silver.files(COMMIT_TABLE):
        for key, vid, sha, fid, dup in gold_con.execute(
                f"SELECT s3_key, s3_version_id, sha256, file_id, duplicate_of_file_id FROM "
                f"{_scan(store, silver.files(COMMIT_TABLE), ', union_by_name=true')}").fetchall():
            loaded.add((key, vid))
            if dup is None:
                known_sha.setdefault(sha, fid)

    con = duckdb.connect()
    con.execute(DDL_LOSSLESS)
    batch_rows = {t: [] for t in TABLES}
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
            store.put(f"{c}/quarantine/{run_id}/{key.rsplit('/', 1)[-1]}", body)
            batch_rows["edi.source_file"].append(dict(file_id=file_id, tenant_code=c, file_kind="UNKNOWN",
                                                      ingest_run_id=run_id, ingested_at=started, parse_status="REJECTED",
                                                      parse_messages=[f"{type(err).__name__}: {err}"[:500]], **meta))
            continue
        if sha in known_sha:
            rows["edi.source_file"][0]["duplicate_of_file_id"] = known_sha[sha]
        else:
            known_sha[sha] = file_id
        for t in TABLES:
            batch_rows[t] += rows.get(t, [])
        stats["loaded"] += 1
    for t in TABLES:                                  # one insert per table: ~40% less than one per file and table
        _insert(con, t, batch_rows[t])
    if stats["loaded"] == 0 and stats["rejected"] == 0:
        if not _gold_is_behind(store, silver):
            return dict(stats, status="noop")                    # duplicate delivery: nothing new
    else:
        con.execute("UPDATE edi.ingest_run SET finished_at = ?, files_seen = ?, files_loaded = ?, files_rejected = ? "
                    "WHERE ingest_run_id = ?", [_now(env), len(raw), stats["loaded"], stats["rejected"], run_id])
        stats["parse_s"] = round(time.perf_counter() - t0, 2)
        t0 = time.perf_counter()
        write_silver(con, store, run_id, day[:7])
        stats["silver_s"] = round(time.perf_counter() - t0, 2)
    t0 = time.perf_counter()
    silver = silver_state(store)
    gold = {}
    stats["published"] = build_gold(env, s3, gold_con, B, c, store.kms, run_id, silver=silver, report=gold)
    stats["gold_s"] = round(time.perf_counter() - t0, 2)
    if stats["published"]:
        stats.update(gold_full=gold["full"], gold=gold)
        t0 = time.perf_counter()
        compact_silver(gold_con, store, silver, before_month=gold["last_month"])
        stats["compact_s"] = round(time.perf_counter() - t0, 2)
    return dict(stats, status="ok")


def write_silver(con, store: Store, run_id: str, month: str):
    """One part per non-empty table, straight into silver; the commit table (edi.source_file) goes last."""
    order = [t for t in SILVER_TABLES if t != COMMIT_TABLE] + [COMMIT_TABLE]
    with tempfile.TemporaryDirectory() as tmp:
        for t in order:
            p = Path(tmp) / f"{t}.parquet"
            if _copy(con, f"SELECT * FROM {t}", p):
                store.put(f"{store.root}parquet/{t}/received_month={month}/part-{run_id}.parquet", p.read_bytes())


def compact_silver(con, store: Store, silver: Silver, before_month: str):
    """Merge every closed month (< before_month) that still has daily parts into one file per table, sorted by
    source file and segment order (so a lookup by file_id reads few row groups), then delete what it covers."""
    with tempfile.TemporaryDirectory() as tmp:
        covered = []
        for t, by_month in silver.live.items():
            for m, keys in by_month.items():
                if m >= before_month or (len(keys) == 1 and "/compacted-" in keys[0]):
                    continue
                through = max(k.rsplit("/", 1)[1].removesuffix(".parquet").split("-", 1)[1] for k in keys)
                cols = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {_scan(store, keys[:1])}").fetchall()}
                order = ", ".join(x for x in ("file_id", "seg_ord") if x in cols) or "ALL"
                p = Path(tmp) / "c.parquet"
                _copy(con, f"SELECT * FROM {_scan(store, keys, ', union_by_name=true')} ORDER BY {order}", p)
                store.put(f"{store.root}parquet/{t}/received_month={m}/compacted-{through}.parquet", p.read_bytes())
                covered += keys
        store.delete(covered)


# --- gold -------------------------------------------------------------------------------------------------------
def rules_hash() -> str:
    """Everything that decides gold's content and layout. A change forces a full rebuild on the next batch."""
    con = duckdb.connect()
    con.execute(DDL_LOSSLESS)
    seed_ref(con)
    h = hashlib.sha256()
    for part in (DDL_LOSSLESS, DDL_SEMANTIC, repr(sorted(ESCALATION.items())), GOLD_LAYOUT, GOLD_PARQUET, repr(GOLD_SORT),
                 str(OPEN_MONTHS)):
        h.update(part.encode())
    for (t,) in con.execute("SELECT schema_name || '.' || table_name FROM duckdb_tables() WHERE schema_name = 'ref' "
                            "ORDER BY 1").fetchall():
        h.update(repr(con.execute(f"SELECT * FROM {t} ORDER BY ALL").fetchall()).encode())
    return h.hexdigest()[:16]


def _gold_is_behind(store: Store, silver: Silver) -> bool:
    """True if committed silver runs are missing from gold (a batch crashed between its silver commit and gold)."""
    version, _ = read_current(store)
    base = read_manifest(store, version) if version else None
    return bool(silver.committed_runs) and (base is None or max(silver.committed_runs) > base["silver_through"])


def _working_catalog(con):
    """Unconstrained copies of every lossless table (empty) and ref.* (seeded), plus the semantic views. Silver
    was validated against the constrained DDL when it was parsed, and constraints make bulk loads ~5x slower."""
    con.execute("ATTACH ':memory:' AS ddl")
    con.execute("USE ddl")
    con.execute(DDL_LOSSLESS)
    seed_ref(con)
    con.execute("USE memory")
    tables = con.execute("SELECT schema_name, table_name FROM duckdb_tables() WHERE database_name = 'ddl'").fetchall()
    for schema in sorted({sch for sch, _ in tables}):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    for schema, name in tables:
        con.execute(f"CREATE OR REPLACE TABLE {schema}.{name} AS SELECT * FROM ddl.{schema}.{name}")
    con.execute("DETACH ddl")
    con.execute(DDL_SEMANTIC)


def _load_all(con, store: Store, silver: Silver):
    for t in VIEW_INPUTS:
        if silver.files(t):
            con.execute(f"INSERT INTO {t} BY NAME SELECT * FROM {_scan(store, silver.files(t), ', union_by_name=true')}")


def _load_closure(con, store: Store, silver: Silver, pending_runs: list[str]) -> dict:
    """The pending runs' files, whole, plus every older row of the claims they touch (CLOSURE). That is all the
    view SQL needs to compute the touched claims' gold rows exactly as a full rebuild would."""
    pending_parts = set()
    for t in VIEW_INPUTS:
        parts = silver.parts_of(t, pending_runs)
        pending_parts |= set(parts)
        if parts:
            con.execute(f"INSERT INTO {t} BY NAME SELECT * FROM {_scan(store, parts, ', union_by_name=true')}")
    if len(silver.parts_of(COMMIT_TABLE, pending_runs)) != len(pending_runs):
        raise RuntimeError(f"pending runs without silver parts (compacted before gold included them?): {pending_runs}")
    con.execute("CREATE TEMP TABLE touched_claim AS SELECT clm01_patient_control_number AS claim_key FROM c837.claim "
                "UNION SELECT clp01_patient_control_number FROM r835.claim_payment")
    touched_files, months = set(), set()
    for t, col in (("c837.claim", "clm01_patient_control_number"), ("r835.claim_payment", "clp01_patient_control_number")):
        older = [k for k in silver.files(t) if k not in pending_parts]
        if older:
            for fid, fname in con.execute(f"SELECT DISTINCT file_id, filename FROM {_scan(store, older, ', filename=true')} "
                                          f"WHERE {col} IN (SELECT claim_key FROM touched_claim)").fetchall():
                touched_files.add(fid)
                months.add(fname.split("received_month=", 1)[1][:7])
    if touched_files:
        if not all(re.fullmatch(r"[0-9a-f]{16}", f) for f in touched_files):
            raise ValueError("unexpected file_id format")
        in_files = ", ".join(f"'{f}'" for f in sorted(touched_files))   # a literal list prunes row groups
        for t, closure_filter in CLOSURE:
            older = [k for k in silver.files(t, months) if k not in pending_parts]
            if older:
                con.execute(f"INSERT INTO {t} BY NAME SELECT {_view_columns(con, t)} FROM "
                            f"{_scan(store, older, ', union_by_name=true')} WHERE file_id IN ({in_files}) AND ({closure_filter})")
    return dict(touched_files=len(touched_files), touched_months=sorted(months))


def _view_columns(con, table: str) -> str:
    """The columns of `table` the view SQL or the closure filters can mention (any identifier in their text: a
    superset, so it stays right as the views change). Older rows only feed the views, so only these are read."""
    if table in ("c837.claim", "r835.claim_payment"):           # sem_int.submission selects c.*
        return "*"
    schema, name = table.split(".")
    cols = [c for (c,) in con.execute("SELECT column_name FROM duckdb_columns() WHERE schema_name = ? AND table_name = ? "
                                      "ORDER BY column_index", [schema, name]).fetchall() if c in _VIEW_WORDS]
    return ", ".join(cols)


_VIEW_WORDS = set(re.findall(r"[a-z_][a-z0-9_]*", (DDL_SEMANTIC + " ".join(f for _, f in CLOSURE)).lower()))


def build_gold(env, s3, con, B: str, c: str, kms: str, run_id: str, silver: Silver | None = None,
               full: bool | None = None, report: dict | None = None) -> bool:
    """Bring gold up to date with silver as version v=<run_id>, then compare-and-set CURRENT. Returns True if it
    published; False if there was nothing to do or a newer version exists. `report` receives the build summary.

    Incremental (the default): the claims the pending (not yet in gold) runs touch are recomputed from their full
    silver history, and only the gold partitions holding them are rewritten. Full: every relation is rebuilt from
    all of silver. A full rebuild happens on GOLD_MODE=full, on the first build, when the rules hash changes (view
    SQL, ref seeds, layout), or when a batch lands in a month older than gold's newest (claim cohorts could move)."""
    store = Store(s3, B, c, kms)
    version, etag = read_current(store)
    if version is not None and version.removeprefix("v=") >= run_id:
        return False                                             # CURRENT only moves forward
    base = read_manifest(store, version) if version else None
    silver = silver or silver_state(store)
    rules = rules_hash()
    through = base["silver_through"] if base else ""
    pending = sorted(r for r in silver.committed_runs if r > through)
    if base is not None and not pending and base["rules"] == rules and not full:
        return False
    if full is None:
        full = (env.environment.get("GOLD_MODE") == "full" or base is None or base["rules"] != rules
                or not pending or min(map(run_month, pending)) < base["last_month"])
    _working_catalog(con)
    last_month = max(map(run_month, silver.committed_runs))
    out = report if report is not None else {}
    out.update(full=full, written=0)
    clock = _Phases(out)
    with tempfile.TemporaryDirectory() as tmp:
        writer = _GoldWriter(con, store, run_id, Path(tmp))
        if full:
            _load_all(con, store, silver)
            clock("load_s")
            out.update(touched_claims=None)
            relations = _gold_full(con, writer, last_month)
            retired = sorted(manifest_files(base))
        else:
            out.update(_load_closure(con, store, silver, pending))
            clock("load_s")
            relations = {rel: {p: list(keys) for p, keys in base["relations"].get(rel, {}).items()} for rel in RELATIONS}
            retired = _retier(writer, relations, last_month) if last_month > base["last_month"] else []
            clock("retier_s")
            retired += _gold_incremental(con, writer, relations, base, last_month, clock)
            out["touched_claims"] = con.execute("SELECT count(*) FROM touched_claim").fetchone()[0]
            out["touched_cohorts"] = dict(con.execute("SELECT cohort, count(*) FROM cohort GROUP BY 1 ORDER BY 1").fetchall())
        for rel in RELATIONS:          # a zero-row file per relation, listed first: binding a view reads only it
            if "schema" not in relations.setdefault(rel, {}):
                relations[rel]["schema"] = [writer.write(rel, f"SELECT * FROM {_source_sql(rel)} LIMIT 0", "schema")]
        out["written"], out["written_mb"] = writer.count, round(writer.bytes / 1e6, 1)
    manifest = dict(version=f"v={run_id}", run_id=run_id, previous=version, rules=rules,
                    silver_through=max(silver.committed_runs, default=""), last_month=last_month,
                    relations=relations, retired=sorted(set(retired)))
    if not publish(store, manifest, etag):
        return False
    if base is not None:                       # no reader is still on base.previous: drop what only it referenced
        store.delete(base["retired"] + ([manifest_key(store, base["previous"])] if base.get("previous") else []))
    if full or last_month > (base or {}).get("last_month", ""):
        _sweep_gold_orphans(store, manifest, base)
    clock("publish_s")
    out["last_month"] = last_month
    return True


class _Phases:
    """Wall time per build phase, into the build report."""

    def __init__(self, report: dict):
        self.report, self.t = report, time.perf_counter()

    def __call__(self, name: str):
        now = time.perf_counter()
        self.report[name] = round(self.report.get(name, 0) + now - self.t, 2)
        self.t = now


class _GoldWriter:
    def __init__(self, con, store: Store, run_id: str, tmp: Path):
        self.con, self.store, self.run_id, self.tmp, self.count, self.bytes = con, store, run_id, tmp, 0, 0

    def write(self, relation: str, sql: str, partition: str) -> str:
        p = self.tmp / "gold.parquet"
        order = f" ORDER BY {GOLD_SORT[relation]}" if relation in GOLD_SORT else ""
        _copy(self.con, f"SELECT * FROM ({sql}){order}", p, GOLD_PARQUET)
        key = gold_key(self.store, relation, partition, self.run_id)
        body = p.read_bytes()
        self.store.put(key, body)
        self.count += 1
        self.bytes += len(body)
        return key


def _source_sql(relation: str) -> str:
    return f"({APPEND_RELATIONS[relation]})" if relation in APPEND_RELATIONS else relation


def tier(month: str, last_month: str) -> str:
    """Gold partition of a month ('YYYY-MM'): its own while it is one of the OPEN_MONTHS newest, else its quarter
    ('YYYY-Qn'). Recent cohorts change daily and stay small to rewrite; settled ones collapse into fewer files, and
    a late event for a settled claim rewrites one quarter, not a year."""
    age = (int(last_month[:4]) * 12 + int(last_month[5:7])) - (int(month[:4]) * 12 + int(month[5:7]))
    return month if age < OPEN_MONTHS else f"{month[:4]}-Q{(int(month[5:7]) - 1) // 3 + 1}"


def _tier_sql(month_sql: str, last_month: str) -> str:
    m = f"CAST(right({month_sql}, 2) AS INT)"
    age = f"({int(last_month[:4]) * 12 + int(last_month[5:7])} - (CAST(left({month_sql}, 4) AS INT) * 12 + {m}))"
    return f"CASE WHEN {age} < {OPEN_MONTHS} THEN {month_sql} ELSE left({month_sql}, 4) || '-Q' || (({m} - 1) // 3 + 1) END"


def _cohorts(con):
    """A claim's cohort: the month its claim key first arrived (837 or 835). Gold partitions claim rows by it, so
    every row of one claim lives in one partition and old cohorts stop changing once their claims settle."""
    con.execute("CREATE OR REPLACE TEMP TABLE cohort AS SELECT k.claim_key, min(strftime(sf.received_at, '%Y-%m')) AS cohort "
                "FROM (SELECT clm01_patient_control_number AS claim_key, file_id FROM c837.claim UNION ALL "
                "SELECT clp01_patient_control_number, file_id FROM r835.claim_payment) k "
                "JOIN edi.source_file sf USING (file_id) GROUP BY 1")


def _file_months(con):
    con.execute("CREATE OR REPLACE TEMP TABLE file_month AS SELECT file_id, strftime(received_at, '%Y-%m') AS month "
                "FROM edi.source_file")


def _claim_rows(rel: str, last_month: str) -> str:
    return f"SELECT v.*, {_tier_sql('c.cohort', last_month)} AS __part FROM {rel} v JOIN cohort c USING (claim_key)"


def _append_rows(rel: str, last_month: str) -> str:
    return (f"SELECT x.*, {_tier_sql('fm.month', last_month)} AS __part FROM {_source_sql(rel)} x "
            "JOIN file_month fm USING (file_id)")


def _write_partitioned(con, writer: _GoldWriter, relation: str, rows_sql: str, prefix: str, merge_old=None) -> dict:
    """rows_sql has the relation's columns plus __part. One file per partition; merge_old(partition) may return
    SQL for the rows of the previous file to keep."""
    con.execute(f"CREATE OR REPLACE TEMP TABLE part_rows AS {rows_sql}")
    out = {}
    for (part,) in con.execute("SELECT DISTINCT __part FROM part_rows ORDER BY 1").fetchall():
        sql = f"SELECT * EXCLUDE (__part) FROM part_rows WHERE __part = '{part}'"
        old = merge_old(f"{prefix}={part}") if merge_old else None
        if old:
            sql = f"{old} UNION ALL BY NAME {sql}"
        out[f"{prefix}={part}"] = [writer.write(relation, sql, f"{prefix}={part}")]
    con.execute("DROP TABLE part_rows")
    return out


def _gold_full(con, writer: _GoldWriter, last_month: str) -> dict:
    _cohorts(con)
    _file_months(con)
    relations = {}
    for rel in CLAIM_RELATIONS:
        relations[rel] = _write_partitioned(con, writer, rel, _claim_rows(rel, last_month), "cohort")
    for rel in APPEND_RELATIONS:
        relations[rel] = _write_partitioned(con, writer, rel, _append_rows(rel, last_month), "received")
    for rel in SMALL_RELATIONS:
        relations[rel] = {"all": [writer.write(rel, f"SELECT * FROM {rel}", "all")]}
    return relations


def _retier(writer: _GoldWriter, relations: dict, last_month: str) -> list:
    """A month closed: every partition except the new current month becomes one file, and months that left the
    OPEN_MONTHS window fold into their quarter's file. Rows are copied, not recomputed. Returns the retired files."""
    retired = []
    for rel in CLAIM_RELATIONS + list(APPEND_RELATIONS):
        groups = {}
        for part, keys in relations[rel].items():
            if part != "schema":
                prefix, period = part.split("=", 1)
                target = f"{prefix}={period if '-Q' in period else tier(period, last_month)}"
                groups.setdefault(target, []).append((part, keys))
        for target, entries in groups.items():
            keys = [k for _, ks in entries for k in ks]
            if target.endswith(last_month) or (len(keys) == 1 and entries[0][0] == target):
                continue
            for part, _ in entries:
                del relations[rel][part]
            relations[rel][target] = [writer.write(rel, f"SELECT * FROM {_scan(writer.store, keys)}", target)]
            retired += keys
    return retired


def _gold_incremental(con, writer: _GoldWriter, relations: dict, base: dict, last_month: str, clock) -> list:
    """Rewrite the partitions holding the touched claims (their old rows replaced), append the pending files'
    lossless rows, and fold the pending payments and payers into the small relations. Returns the retired files."""
    retired = []
    store = writer.store
    _cohorts(con)
    for rel in CLAIM_RELATIONS:
        def keep_old(part, rel=rel):
            old = relations[rel].get(part, [])
            return f"SELECT * FROM {_scan(store, old)} ANTI JOIN touched_claim USING (claim_key)" if old else None
        for part, keys in _write_partitioned(con, writer, rel, _claim_rows(rel, last_month), "cohort", keep_old).items():
            retired += relations[rel].get(part, [])
            relations[rel][part] = keys
    clock("claims_s")
    # from here on only the pending files' rows: payments, payers and lossless rows are per file
    con.execute("CREATE OR REPLACE TEMP TABLE pending_file AS SELECT file_id FROM edi.source_file "
                f"WHERE ingest_run_id > '{base['silver_through']}'")
    for t in VIEW_INPUTS:
        con.execute(f"DELETE FROM {t} WHERE file_id NOT IN (SELECT file_id FROM pending_file)")
    _file_months(con)
    for rel in APPEND_RELATIONS:
        for part, keys in _write_partitioned(con, writer, rel, _append_rows(rel, last_month), "received").items():
            relations[rel].setdefault(part, []).extend(keys)
    clock("append_s")
    for rel in SMALL_RELATIONS:
        old = relations[rel]["all"]
        sql = _payers_merge_sql(_scan(store, old)) if rel == "sem.payers" else \
            f"SELECT * FROM {_scan(store, old)} UNION ALL BY NAME SELECT * FROM {rel}"
        retired += old
        relations[rel]["all"] = [writer.write(rel, sql, "all")]
    clock("small_s")
    return retired


def _payers_merge_sql(old_scan: str) -> str:
    """sem.payers is an aggregate over every submission and payment: fold the pending files' partial aggregate
    into the previous one (counts add, as-sent lists union, the first-seen name and class are kept)."""
    lists = lambda col: (f"CASE WHEN o.{col} IS NULL AND n.{col} IS NULL THEN NULL ELSE "
                         f"list_sort(list_distinct(COALESCE(o.{col}, []) || COALESCE(n.{col}, []))) END AS {col}")
    return (f"SELECT payer_key, COALESCE(o.payer_name, n.payer_name) AS payer_name, "
            f"COALESCE(o.payer_class, n.payer_class) AS payer_class, {lists('names_as_sent')}, {lists('ids_as_sent')}, "
            f"COALESCE(o.claim_submissions, 0) + COALESCE(n.claim_submissions, 0) AS claim_submissions, "
            f"COALESCE(o.payments, 0) + COALESCE(n.payments, 0) AS payments "
            f"FROM {old_scan} o FULL JOIN sem.payers n USING (payer_key)")


def _sweep_gold_orphans(store: Store, manifest: dict, base: dict | None):
    """Data files no version references: left behind by a build that crashed or lost the CURRENT race. Runs under
    the batch lock (no build in flight), at month close and after full rebuilds."""
    keep = manifest_files(manifest) | manifest_files(base)
    keep |= {manifest_key(store, manifest["version"])} | ({manifest_key(store, base["version"])} if base else set())
    keep.add(f"{store.root}semantic_layer/CURRENT")
    store.delete([k for k, _ in store.list(f"{store.root}semantic_layer/") if k not in keep])
