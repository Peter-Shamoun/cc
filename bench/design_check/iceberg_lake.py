"""Prototype (b): silver and gold as Apache Iceberg tables (pyiceberg 0.9.1, SQL catalog on local SQLite standing in
for Glue / S3 Tables), metadata and data files on the simulated S3, read by DuckDB's iceberg extension.

pyiceberg 0.9.1 cannot set an SSE-KMS key on writes (neither its PyArrow nor its fsspec FileIO has the option), and
the bucket policy denies every PUT without the customer's key. KmsS3FileIO below is the smallest fix: PyArrowFileIO
for reads, boto3 put_object with the customer key for every file pyiceberg writes (data, manifests, manifest lists,
metadata.json). pyiceberg 0.12 adds `s3.sse-kms-key-id`, for its fsspec FileIO only.

Silver: one table per lossless table under <c>/parquet/iceberg/, partitioned by an added received_month column.
Gold: one table per sem.* / lossless.* relation under <c>/semantic_layer/iceberg/, unpartitioned. Iceberg commits
are per table (pyiceberg has no multi-table transaction), so each gold table records the silver run it includes in
a table property (`cc.silver_through`, in the same commit as its data) and a re-run skips tables already done.
"""
from __future__ import annotations

import io
from pathlib import Path

import boto3
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pyiceberg.io.pyarrow as _pi_pyarrow
from botocore.config import Config
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.exceptions import NoSuchTableError
from pyiceberg.expressions import AlwaysTrue, EqualTo, In
from pyiceberg.io import InputFile, OutputFile, OutputStream
from pyiceberg.io.pyarrow import PyArrowFileIO

from ccsim.infra import BUCKET
from ccsim.pipeline import SILVER_TABLES

from . import gold_flow
from .gold_flow import APPEND_GOLD, CLAIM_RELATIONS, temp_name


class _ParquetWriter(pq.ParquetWriter):
    """pyiceberg 0.9.1 expects decimal(p <= 18) stored as INT64; pyarrow 26 (outside pyiceberg's pyarrow<20 pin)
    defaults to FIXED_LEN_BYTE_ARRAY and pyiceberg's stats collector rejects the file."""

    def __init__(self, *a, **kw):
        kw.setdefault("store_decimal_as_integer", True)
        super().__init__(*a, **kw)


_pi_pyarrow.pq.ParquetWriter = _ParquetWriter

KMS_PROP = "cc.kms-key-arn"
THROUGH = "cc.silver_through"


class _UploadOnClose(io.BytesIO):
    def __init__(self, on_close):
        super().__init__()
        self._on_close, self._done = on_close, False

    def close(self):
        if not self._done:
            self._done = True
            self._on_close(self.getvalue())
        super().close()


class _KmsOutputFile(OutputFile):
    def __init__(self, location: str, fileio: KmsS3FileIO):
        super().__init__(location)
        self.fileio, self.size = fileio, None

    def __len__(self) -> int:
        return self.size

    def exists(self) -> bool:
        return self.to_input_file().exists()

    def to_input_file(self) -> InputFile:
        return self.fileio.new_input(self.location)

    def create(self, overwrite: bool = False) -> OutputStream:
        bucket, key = self.location.removeprefix("s3://").split("/", 1)

        def upload(body: bytes):
            kw = {} if overwrite else {"IfNoneMatch": "*"}
            self.fileio.s3.put_object(Bucket=bucket, Key=key, Body=body, ServerSideEncryption="aws:kms",
                                      SSEKMSKeyId=self.fileio.properties[KMS_PROP], **kw)
            self.size = len(body)
        return _UploadOnClose(upload)


class KmsS3FileIO(PyArrowFileIO):
    def __init__(self, properties=None):
        super().__init__(properties or {})
        p = self.properties
        self.s3 = boto3.client("s3", endpoint_url=p["s3.endpoint"], region_name=p["s3.region"],
                               aws_access_key_id=p["s3.access-key-id"], aws_secret_access_key=p["s3.secret-access-key"],
                               aws_session_token=p["s3.session-token"],
                               config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 1},
                                             request_checksum_calculation="when_required",
                                             response_checksum_validation="when_required"))

    def new_output(self, location: str) -> OutputFile:
        return _KmsOutputFile(location, self)


def _decimal_counts_only(schema: pa.Schema) -> dict:
    """pyiceberg 0.9.1 fails ("conversion from NoneType to Decimal") collecting min/max of an all-NULL decimal
    column in a file; keep only counts for decimal columns (they lose file pruning, which nothing here needs)."""
    return {f"write.metadata.metrics.column.{f.name}": "counts" for f in schema if pa.types.is_decimal(f.type)}


def _q(names) -> str:
    return ", ".join(f"'{n}'" for n in names)


def _arrow(con, sql: str) -> pa.Table:
    t = con.sql(sql).to_arrow_table()
    # pyiceberg wants plain string / list types; DuckDB hands out string_view on some paths
    return t.cast(pa.schema([pa.field(f.name, pa.string() if pa.types.is_string(f.type) or pa.types.is_large_string(f.type)
                                      or str(f.type) == "string_view" else f.type) for f in t.schema]))


class IcebergSystem:
    name = "iceberg"

    def __init__(self, sim, work: Path):
        self.sim, self.work = sim, work
        work.mkdir(parents=True, exist_ok=True)
        c = sim.customer
        self.loc = {"silver": f"s3://{BUCKET}/{c}/parquet/iceberg", "gold": f"s3://{BUCKET}/{c}/semantic_layer/iceberg"}
        self.prefixes = {"silver": f"{c}/parquet/", "gold": f"{c}/semantic_layer/"}
        self.catalog_commits = 0

    def _catalog(self, session) -> SqlCatalog:
        creds = session.credentials()
        cat = SqlCatalog("cc", **{"uri": f"sqlite:///{self.work / 'catalog.db'}", "warehouse": f"s3://{BUCKET}/{self.sim.customer}/",
                                  "py-io-impl": "bench.design_check.iceberg_lake.KmsS3FileIO",
                                  "s3.endpoint": self.sim.ep.url, "s3.region": "us-west-2",
                                  "s3.access-key-id": creds["AccessKeyId"], "s3.secret-access-key": creds["SecretAccessKey"],
                                  "s3.session-token": creds["SessionToken"], KMS_PROP: self.sim.kms_arn})
        for ns in ("silver", "gold"):
            cat.create_namespace_if_not_exists(ns)
        return cat

    @staticmethod
    def ident(layer: str, name: str) -> str:
        return f"{layer}.{name.replace('.', '__')}"

    def _table(self, cat, layer, name):
        try:
            return cat.load_table(self.ident(layer, name))
        except NoSuchTableError:
            return None

    def _duck(self, session, scope):
        con = duckdb.connect()
        con.execute("LOAD httpfs")
        con.execute("LOAD iceberg")
        con.execute(self.sim.secret_sql(session, scope, "w"))
        return con

    def batch(self, day_dir: Path, run_id: str, day: str, compact: bool) -> dict:
        m, phases = self.sim.meter, {}
        snap = m.snap()
        commits0 = self.catalog_commits

        def phase(name):
            nonlocal snap
            now = m.snap()
            phases[name] = m.diff(now, snap)
            snap = now

        session = self.sim.parser_session()
        cat = self._catalog(session)
        month = day[:7]
        for t in [x for x in SILVER_TABLES if x != "edi.source_file"] + ["edi.source_file"]:   # commit record last
            f = day_dir / f"{t}.parquet"
            if not f.exists():
                continue
            data = pq.read_table(f)
            data = data.append_column("received_month", pa.array([month] * data.num_rows, pa.string()))
            tbl = self._table(cat, "silver", t)
            if tbl is None:
                tbl = cat.create_table(self.ident("silver", t), schema=data.schema,
                                       location=f"{self.loc['silver']}/{t}", properties=_decimal_counts_only(data.schema))
                with tbl.update_spec() as u:
                    u.add_identity("received_month")
                self.catalog_commits += 2
            tbl.append(data)
            self.catalog_commits += 1
        phase("silver")
        stats = self._gold(cat, session, run_id)
        phase("gold")
        if compact:
            self.compact(cat, session, before_month=month)
            phase("compact")
        return dict(phases=phases, catalog_commits=self.catalog_commits - commits0, **stats)

    def _gold(self, cat, session, run_id) -> dict:
        gold = {rel: self._table(cat, "gold", rel) for rel in CLAIM_RELATIONS + APPEND_GOLD + ["sem.payers"]}
        throughs = [t.properties.get(THROUGH, "") for t in gold.values() if t is not None]
        through = min(throughs) if len(throughs) == len(gold) else ""
        silver = {t: self._table(cat, "silver", t) for t in SILVER_TABLES}
        con = self._duck(session, f"s3://{BUCKET}/{self.sim.customer}/")
        scan = lambda tbl: f"iceberg_scan('{tbl.metadata_location}')"
        sf = silver["edi.source_file"]
        pend = con.execute(f"SELECT file_id, received_month FROM {scan(sf)} WHERE ingest_run_id > ?", [through]).fetchall()
        pend_ids, pend_months = _q(sorted({f for f, _ in pend})), _q(sorted({m for _, m in pend}))

        class Src:
            def pending(_, t):
                tbl = silver.get(t)
                if tbl is None:
                    return None
                return f"(SELECT * FROM {scan(tbl)} WHERE received_month IN ({pend_months}) AND file_id IN ({pend_ids}))"

            def older(_, t, months):
                tbl = silver.get(t)
                if tbl is None or not through:
                    return None
                return f"(SELECT * FROM {scan(tbl)} WHERE received_month IN ({_q(months)}))" if months else f"{scan(tbl)}"

            def old_gold(_, rel):
                return scan(gold[rel]) if gold.get(rel) is not None else None

        stats = gold_flow.compute(con, Src())
        touched = [k for (k,) in con.execute("SELECT claim_key FROM touched_claim").fetchall()]
        for rel, tbl in gold.items():
            if tbl is not None and tbl.properties.get(THROUGH, "") >= run_id:
                continue                                          # this table already has the batch (a re-run)
            data = _arrow(con, f"SELECT * FROM {temp_name(rel)}")
            if tbl is None:
                tbl = cat.create_table(self.ident("gold", rel), schema=data.schema, location=f"{self.loc['gold']}/{rel}",
                                       properties=_decimal_counts_only(data.schema))
                self.catalog_commits += 1
            tx = tbl.transaction()
            if rel in CLAIM_RELATIONS and through:
                tx.overwrite(data, overwrite_filter=In("claim_key", touched))
            elif rel == "sem.payers":
                tx.overwrite(data)
            else:
                tx.append(data)
            tx.set_properties({THROUGH: run_id})
            tx.commit_transaction()
            self.catalog_commits += 1
        return stats

    def compact(self, cat, session, before_month: str):
        """pyiceberg 0.9.1 has no rewrite_data_files: compaction is a full overwrite per silver month and per gold
        table, read through DuckDB. Snapshot expiry does not exist in 0.9.1 either (0.10+), so replaced files stay."""
        con = self._duck(session, f"s3://{BUCKET}/{self.sim.customer}/")
        for t in SILVER_TABLES:
            tbl = self._table(cat, "silver", t)
            if tbl is None:
                continue
            for (mo,) in con.execute(f"SELECT DISTINCT received_month FROM iceberg_scan('{tbl.metadata_location}') "
                                     f"WHERE received_month < ?", [before_month]).fetchall():
                files = list(tbl.scan(row_filter=EqualTo("received_month", mo)).plan_files())
                if len(files) > 1:
                    data = _arrow(con, f"SELECT * FROM iceberg_scan('{tbl.metadata_location}') WHERE received_month = '{mo}'")
                    data = data.cast(tbl.schema().as_arrow())     # DuckDB results are nullable; silver has required fields
                    tbl.overwrite(data, overwrite_filter=EqualTo("received_month", mo))
                    self.catalog_commits += 1
                    tbl = self._table(cat, "silver", t)
        for rel in CLAIM_RELATIONS + APPEND_GOLD + ["sem.payers"]:
            tbl = self._table(cat, "gold", rel)
            if tbl is not None and len(tbl.inspect.files()) > 1:
                data = _arrow(con, f"SELECT * FROM iceberg_scan('{tbl.metadata_location}')")
                tx = tbl.transaction()
                tx.overwrite(data, overwrite_filter=AlwaysTrue())
                tx.commit_transaction()
                self.catalog_commits += 1

    def compact_now(self) -> dict:
        m = self.sim.meter
        a = m.snap()
        session = self.sim.parser_session()
        self.compact(self._catalog(session), session, before_month="9999-99")
        return m.diff(m.snap(), a)

    # -- reader -------------------------------------------------------------------------------------------
    def open_reader(self, escalate: bool = False):
        """Catalog lookup (one GetTable per relation in Glue / S3 Tables; local SQLite here, not counted), then
        one DuckDB view per relation over iceberg_scan of its current metadata file."""
        from ccsim.pipeline import APPEND_RELATIONS, SEM_TABLES
        creds = self.sim.reader_credentials()
        import sqlite3
        rels = [f"sem.{t}" for t in SEM_TABLES] + (list(APPEND_RELATIONS) if escalate else [])
        db = sqlite3.connect(self.work / "catalog.db")      # the catalog lookup only: table -> current metadata file
        locs = {rel: db.execute("SELECT metadata_location FROM iceberg_tables WHERE table_namespace = 'gold' AND "
                                "table_name = ?", [self.ident("gold", rel).split(".", 1)[1]]).fetchone()[0] for rel in rels}
        db.close()
        con = duckdb.connect(config={"autoinstall_known_extensions": False, "autoload_known_extensions": False})
        con.execute("LOAD httpfs")
        con.execute("LOAD iceberg")
        con.execute(self.sim.secret_sql(creds, f"s3://{BUCKET}/{self.prefixes['gold']}", "r"))
        con.execute("CREATE SCHEMA sem")
        if escalate:
            con.execute("CREATE SCHEMA lossless")
        for rel, loc in locs.items():
            con.execute(f"CREATE VIEW {rel} AS SELECT * FROM iceberg_scan('{loc}')")
        con.execute("SET allowed_directories = ?", [[f"{self.loc['gold']}/{rel}/" for rel in rels]])
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        return con

    def storage(self) -> dict:
        return {layer: self.sim.inventory(p) for layer, p in self.prefixes.items()}

    def file_counts(self) -> dict:
        session = self.sim.parser_session()
        cat = self._catalog(session)
        out = {}
        for layer, names in (("silver", SILVER_TABLES), ("gold", CLAIM_RELATIONS + APPEND_GOLD + ["sem.payers"])):
            n = s = 0
            for t in names:
                tbl = self._table(cat, layer, t)
                if tbl is not None:
                    n += len(tbl.inspect.files())
                    s += len(tbl.metadata.snapshots)
            out[layer] = dict(data_files=n, snapshots=s)
        return out


