"""Prototype (a): silver and gold as two DuckLake catalogs per customer, data files on the simulated S3.

Catalogs: one DuckDB-file catalog per layer, kept in S3 next to its data and encrypted with the customer's key:
    <c>/parquet/_ducklake/silver.ducklake          parser only (its column stats hold PHI min/max values)
    <c>/semantic_layer/_ducklake/gold.ducklake     parser writes, reader reads (gold stats are PHI-free)
The parser task downloads both, ATTACHes them, commits, and uploads them with If-Match on the ETag it read (the
upload is the commit point: a concurrent writer loses the compare-and-set and its data files become orphans that
ducklake_delete_orphaned_files removes). Readers ATTACH the gold catalog READ_ONLY straight from S3.

Silver: every lossless table, plus a received_month column, partitioned by it (the current design keeps the month
in the path). Gold: every sem.* and lossless.* relation as a DuckLake table under schemas sem / lossless; a batch
is one gold transaction (all relations in one snapshot): DELETE the touched claims + INSERT their recomputed rows,
INSERT appended rows, replace sem.payers. The pending rows come from DuckLake's change feed
(ducklake_table_insertions) between the silver snapshot gold last saw and now.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
from botocore.exceptions import ClientError

from ccsim.infra import BUCKET
from ccsim.pipeline import SILVER_TABLES

from . import gold_flow
from .gold_flow import APPEND_GOLD, CLAIM_RELATIONS, temp_name


def _q(names) -> str:
    return ", ".join(f"'{n}'" for n in names)


class DuckLakeSystem:
    name = "ducklake"

    def __init__(self, sim, work: Path):
        self.sim, self.work = sim, work
        c = sim.customer
        self.keys = {"silver": f"{c}/parquet/_ducklake/silver.ducklake",
                     "gold": f"{c}/semantic_layer/_ducklake/gold.ducklake"}
        self.data = {"silver": f"s3://{BUCKET}/{c}/parquet/ducklake/",
                     "gold": f"s3://{BUCKET}/{c}/semantic_layer/ducklake/"}
        self.prefixes = {"silver": f"{c}/parquet/", "gold": f"{c}/semantic_layer/"}
        work.mkdir(parents=True, exist_ok=True)

    # -- writer -------------------------------------------------------------------------------------------
    def _open(self):
        s3 = self.sim.aws.boto3_client("s3", self.sim.parser_session())
        etags = {}
        for layer, key in self.keys.items():
            local = self.work / f"{layer}.ducklake"
            local.unlink(missing_ok=True)
            Path(f"{local}.wal").unlink(missing_ok=True)
            try:
                r = s3.get_object(Bucket=BUCKET, Key=key)
                local.write_bytes(r["Body"].read())
                etags[layer] = r["ETag"]
            except ClientError as err:
                if err.response["Error"]["Code"] not in ("NoSuchKey", "404"):
                    raise
                etags[layer] = None
        con = duckdb.connect()
        con.execute("LOAD httpfs")
        con.execute("LOAD ducklake")
        con.execute(self.sim.secret_sql(self.session, f"s3://{BUCKET}/{self.sim.customer}/", "w", kms=True))
        con.execute("SET ducklake_default_data_inlining_row_limit = 0")   # no table rows inside the catalog file
        con.execute("SET memory_limit = '3GB'")                       # a 4-8 GB Fargate task, like the current parser
        for layer in self.keys:
            con.execute(f"ATTACH 'ducklake:{self.work / (layer + '.ducklake')}' AS {layer} (DATA_PATH '{self.data[layer]}')")
            if etags[layer] is None:                       # the current lake's Parquet settings (DuckLake: Snappy)
                con.execute(f"CALL ducklake_set_option('{layer}', 'parquet_compression', 'zstd')")
                if layer == "gold":
                    con.execute("CALL ducklake_set_option('gold', 'parquet_row_group_size', 1048576)")
        return con, s3, etags

    def _close(self, con, s3, etags) -> dict:
        con.execute("DETACH silver")
        con.execute("DETACH gold")
        con.close()
        sizes = {}
        for layer in ("silver", "gold"):                   # silver first: gold may lag silver, never lead it
            body = (self.work / f"{layer}.ducklake").read_bytes()
            sizes[layer] = len(body)
            cond = {"IfMatch": etags[layer]} if etags[layer] else {"IfNoneMatch": "*"}
            s3.put_object(Bucket=BUCKET, Key=self.keys[layer], Body=body, ServerSideEncryption="aws:kms",
                          SSEKMSKeyId=self.sim.kms_arn, **cond)
        return sizes

    def _tables(self, con, catalog: str) -> set:
        return {f"{s}.{t}" for s, t in con.execute(
            "SELECT schema_name, table_name FROM duckdb_tables() WHERE database_name = ?", [catalog]).fetchall()}

    def batch(self, day_dir: Path, run_id: str, day: str, compact: bool) -> dict:
        self.session = self.sim.parser_session()
        m, phases = self.sim.meter, {}
        snap = m.snap()

        def phase(name):
            nonlocal snap
            now = m.snap()
            phases[name] = m.diff(now, snap)
            snap = now

        con, s3, etags = self._open()
        phase("open")
        # Retention: the previous batch was a day ago, longer than any reader session, so every snapshot but the
        # current one can go, and the files only they reference with it.
        # Silver keeps the snapshots gold has not consumed yet (the change feed reads them).
        con.execute("CALL ducklake_expire_snapshots('gold', older_than => now())")
        if "meta.state" in self._tables(con, "gold"):
            seen = con.execute("SELECT silver_snapshot FROM gold.meta.state").fetchone()[0]
            old = [r[0] for r in con.execute("SELECT snapshot_id FROM ducklake_snapshots('silver') WHERE snapshot_id < ?",
                                             [seen]).fetchall()]
            if old:
                con.execute("CALL ducklake_expire_snapshots('silver', versions => ?)", [old])
        for layer in ("silver", "gold"):
            con.execute(f"CALL ducklake_cleanup_old_files('{layer}', cleanup_all => true)")
        phase("expire")
        month = day[:7]
        con.execute("BEGIN")
        have = self._tables(con, "silver")
        for t in SILVER_TABLES:
            f = day_dir / f"{t}.parquet"
            if not f.exists():
                continue
            src = f"SELECT *, '{month}' AS received_month FROM read_parquet('{f}')"
            if t not in have:
                con.execute(f"CREATE SCHEMA IF NOT EXISTS silver.{t.split('.')[0]}")
                con.execute(f"CREATE TABLE silver.{t} AS {src} LIMIT 0")
                con.execute(f"ALTER TABLE silver.{t} SET PARTITIONED BY (received_month)")
            con.execute(f"INSERT INTO silver.{t} {src}")
        con.execute("COMMIT")
        phase("silver")
        stats = self._gold(con)
        phase("gold")
        if compact:
            self.compact(con)
            phase("compact")
        sizes = self._close(con, s3, etags)
        phase("close")
        return dict(phases=phases, catalog_bytes=sizes, **stats)

    def _gold(self, con) -> dict:
        silver_now = con.execute("SELECT id FROM ducklake_current_snapshot('silver')").fetchone()[0]
        gold_tables = self._tables(con, "gold")
        seen = con.execute("SELECT silver_snapshot FROM gold.meta.state").fetchone()[0] if "meta.state" in gold_tables else None
        silver_tables = self._tables(con, "silver")

        class Src:
            def pending(_, t):
                if t not in silver_tables:
                    return None
                if seen is None:
                    return f"silver.{t}"
                s, n = t.split(".")
                return f"ducklake_table_insertions('silver', '{s}', '{n}', {seen + 1}, {silver_now})"

            def older(_, t, months):
                if t not in silver_tables or seen is None:
                    return None
                return f"(SELECT * FROM silver.{t} WHERE received_month IN ({_q(months)}))" if months else f"silver.{t}"

            def old_gold(_, rel):
                return f"gold.{rel}" if rel in gold_tables else None

        stats = gold_flow.compute(con, Src())
        con.execute("BEGIN")
        for rel in CLAIM_RELATIONS + APPEND_GOLD + ["sem.payers"]:
            if rel not in gold_tables:
                con.execute(f"CREATE SCHEMA IF NOT EXISTS gold.{rel.split('.')[0]}")
                con.execute(f"CREATE TABLE gold.{rel} AS SELECT * FROM {temp_name(rel)} LIMIT 0")
            if rel in CLAIM_RELATIONS:
                con.execute(f"DELETE FROM gold.{rel} WHERE claim_key IN (SELECT claim_key FROM touched_claim)")
            elif rel == "sem.payers":
                con.execute(f"DELETE FROM gold.{rel}")
            con.execute(f"INSERT INTO gold.{rel} SELECT * FROM {temp_name(rel)}")
        if "meta.state" not in gold_tables:
            con.execute("CREATE SCHEMA IF NOT EXISTS gold.meta")
            con.execute("CREATE TABLE gold.meta.state (silver_snapshot BIGINT)")
            con.execute(f"INSERT INTO gold.meta.state VALUES ({silver_now})")
        else:
            con.execute(f"UPDATE gold.meta.state SET silver_snapshot = {silver_now}")
        con.execute("COMMIT")
        return stats

    def compact(self, con):
        """Month close: merge silver's small daily files per partition; apply gold's delete files and merge its
        small files. The replaced files go at the next batch's expire + cleanup (after reader retention)."""
        con.execute("CALL ducklake_merge_adjacent_files('silver')")
        con.execute("CALL ducklake_rewrite_data_files('gold', delete_threshold => 0)")
        con.execute("CALL ducklake_merge_adjacent_files('gold')")

    def compact_now(self) -> dict:
        self.session = self.sim.parser_session()
        m = self.sim.meter
        a = m.snap()
        con, s3, etags = self._open()
        self.compact(con)
        self._close(con, s3, etags)
        return m.diff(m.snap(), a)

    # -- reader -------------------------------------------------------------------------------------------
    def open_reader(self, escalate: bool = False):
        creds = self.sim.reader_credentials()
        con = duckdb.connect(config={"autoinstall_known_extensions": False, "autoload_known_extensions": False})
        con.execute("LOAD httpfs")
        con.execute("LOAD ducklake")
        gold = f"s3://{BUCKET}/{self.prefixes['gold']}"
        con.execute(self.sim.secret_sql(creds, gold, "r"))
        con.execute(f"ATTACH 'ducklake:s3://{BUCKET}/{self.keys['gold']}' AS g (READ_ONLY)")
        con.execute("USE g")
        allowed = [f"{self.data['gold']}sem/", f"s3://{BUCKET}/{self.keys['gold']}"]
        if escalate:
            allowed.append(f"{self.data['gold']}lossless/")
        con.execute("SET allowed_directories = ?", [allowed])
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        return con

    def storage(self) -> dict:
        return {layer: self.sim.inventory(p) for layer, p in self.prefixes.items()}

    def file_counts(self) -> dict:
        """Live data / delete files and stored snapshots, from the catalog files the last batch uploaded."""
        con = duckdb.connect()
        out = {}
        for layer in ("silver", "gold"):
            con.execute(f"ATTACH '{self.work / (layer + '.ducklake')}' AS x (READ_ONLY)")
            out[layer] = {k: con.execute(sql).fetchone()[0] for k, sql in (
                ("data_files", "SELECT count(*) FROM x.ducklake_data_file WHERE end_snapshot IS NULL"),
                ("delete_files", "SELECT count(*) FROM x.ducklake_delete_file WHERE end_snapshot IS NULL"),
                ("snapshots", "SELECT count(*) FROM x.ducklake_snapshot"))}
            con.execute("DETACH x")
        return out
