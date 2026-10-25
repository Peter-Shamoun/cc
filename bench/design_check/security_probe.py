"""Security-fit probes for the table-format prototypes, on the unchanged infra (IAM roles, ABAC bucket policy,
per-customer KMS keys). Loads 3 business days into each prototype, then checks:

  1. writes without the customer's KMS key are refused (stock pyiceberg FileIO; DuckDB secret without KMS_KEY_ID)
  2. the reader role can open gold (catalog + data) and cannot open silver data or the silver catalog / metadata
  3. what the catalogs and table metadata hold: PHI in column min/max statistics, rows inlined into the catalog
  4. crypto-shredding: with the customer's key disabled, nothing of that customer (data, catalog file, metadata) reads
  5. raw/ is untouched (the prototypes write only under parquet/ and semantic_layer/)

    python -m bench.design_check.security_probe DATA_DIR WORLD_DIR     (iceberg parts need pyiceberg 0.9.1)
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench.design_check.simenv import Sim
from ccsim.infra import BUCKET

PHI_COLUMNS = ("name_last_or_org", "name_first", "dmg_birth_date", "addr_line1", "id_value")


def attempt(fn) -> str:
    try:
        r = fn()
        return f"allowed{'' if r is None else ': ' + str(r)[:120]}"
    except Exception as err:  # noqa: BLE001
        return f"denied ({type(err).__name__}: {str(err).splitlines()[0][:110]})"


def probe_ducklake(sim: Sim, data: Path, days: list, work: Path) -> dict:
    from bench.design_check.ducklake_lake import DuckLakeSystem
    dl = DuckLakeSystem(sim, work)
    for d in days:
        dl.batch(data / "parsed" / d["day"], d["run_id"], d["day"], compact=False)
    out = {}
    # 3. what the silver catalog holds
    con = duckdb.connect()
    con.execute(f"ATTACH '{work / 'silver.ducklake'}' AS m (READ_ONLY)")
    out["silver_catalog_phi_stats"] = con.execute(
        "SELECT c.column_name, s.min_value, s.max_value FROM m.ducklake_file_column_stats s "
        "JOIN m.ducklake_column c USING (table_id, column_id) JOIN m.ducklake_table t USING (table_id) "
        f"WHERE t.table_name = 'entity' AND c.column_name IN {PHI_COLUMNS} AND c.end_snapshot IS NULL "
        "AND t.end_snapshot IS NULL LIMIT 5").fetchall()
    con.close()
    # default inlining: a fresh catalog with DuckLake's default settings stores small inserts in the catalog itself
    c2 = duckdb.connect()
    c2.execute("LOAD ducklake")
    p = work / "inline-demo.ducklake"
    c2.execute(f"ATTACH 'ducklake:{p}' AS x (DATA_PATH '{work / 'inline-data'}/')")
    c2.execute(f"CREATE TABLE x.entity AS SELECT * FROM read_parquet('{data / 'parsed' / days[0]['day'] / 'edi.entity.parquet'}') LIMIT 5")
    c2.execute("INSERT INTO x.entity SELECT * FROM x.entity LIMIT 3")
    c2.execute("DETACH x")
    c2.execute(f"ATTACH '{p}' AS m (READ_ONLY)")
    inl = [t for (t,) in c2.execute("SELECT table_name FROM duckdb_tables() WHERE database_name = 'm' AND "
                                     "table_name LIKE 'ducklake_inlined_data%'").fetchall()]
    out["default_inlining_tables"] = inl
    out["default_inlining_rows"] = sum(c2.execute(f"SELECT count(*) FROM m.{t}").fetchone()[0] for t in inl)
    c2.close()
    # 2. reader reach
    creds = sim.reader_credentials()
    r = duckdb.connect()
    r.execute("LOAD httpfs")
    r.execute("LOAD ducklake")
    r.execute(sim.secret_sql(creds, f"s3://{BUCKET}/{sim.customer}/", "r"))
    out["reader_attach_gold_catalog"] = attempt(lambda: r.execute(
        f"ATTACH 'ducklake:s3://{BUCKET}/{dl.keys['gold']}' AS g (READ_ONLY)") and
        r.execute("SELECT count(*) FROM g.sem.claims").fetchone()[0])
    out["reader_get_silver_catalog"] = attempt(lambda: sim.aws.boto3_client("s3", creds).get_object(
        Bucket=BUCKET, Key=dl.keys["silver"])["ContentLength"])
    silver_file = sim.aws.s3.db.execute("SELECT key FROM objects WHERE key LIKE ? AND key LIKE '%.parquet' LIMIT 1",
                                        (f"{sim.customer}/parquet/ducklake/edi/entity/%",)).fetchone()[0]
    out["reader_read_silver_file"] = attempt(lambda: r.execute(
        f"SELECT count(*) FROM read_parquet('s3://{BUCKET}/{silver_file}')").fetchone()[0])
    # gold catalog: what a reader learns about silver from it (nothing: separate catalog)
    out["gold_catalog_tables"] = sorted(f"{s}.{t}" for s, t in r.execute(
        "SELECT schema_name, table_name FROM duckdb_tables() WHERE database_name = 'g'").fetchall())
    r.close()
    # 1. write without the customer's key
    w = duckdb.connect()
    w.execute("LOAD httpfs")
    w.execute(sim.secret_sql(sim.parser_session(), f"s3://{BUCKET}/{sim.customer}/", "w"))
    out["write_without_customer_key"] = attempt(lambda: w.execute(
        f"COPY (SELECT 1 x) TO 's3://{BUCKET}/{sim.customer}/semantic_layer/ducklake/probe.parquet'"))
    return out


def probe_iceberg(sim: Sim, data: Path, days: list, work: Path) -> dict:
    import pyarrow as pa
    from pyiceberg.catalog.sql import SqlCatalog

    from bench.design_check.iceberg_lake import IcebergSystem
    ice = IcebergSystem(sim, work)
    for d in days:
        ice.batch(data / "parsed" / d["day"], d["run_id"], d["day"], compact=False)
    out = {}
    session = sim.parser_session()
    cat = ice._catalog(session)
    ent = cat.load_table("silver.edi__entity")
    files = ent.inspect.files().to_pylist()
    bounds = files[0]["readable_metrics"]
    out["silver_manifest_phi_bounds"] = {c: (str(bounds[c]["lower_bound"])[:20], str(bounds[c]["upper_bound"])[:20])
                                         for c in PHI_COLUMNS if c in bounds}
    out["silver_metadata_location"] = ent.metadata_location
    # 1. stock pyiceberg FileIO (no SSE-KMS option in 0.9.1): the bucket policy refuses its writes
    creds = session.credentials()
    stock = SqlCatalog("stock", **{"uri": f"sqlite:///{work / 'stock.db'}", "warehouse": f"s3://{BUCKET}/{sim.customer}/",
                                   "s3.endpoint": sim.ep.url, "s3.region": "us-west-2",
                                   "s3.access-key-id": creds["AccessKeyId"], "s3.secret-access-key": creds["SecretAccessKey"],
                                   "s3.session-token": creds["SessionToken"]})
    stock.create_namespace_if_not_exists("probe")
    out["stock_pyiceberg_write"] = attempt(lambda: stock.create_table(
        "probe.t", schema=pa.schema([("x", pa.int64())]),
        location=f"s3://{BUCKET}/{sim.customer}/semantic_layer/iceberg/probe").append(pa.table({"x": [1]})))
    # 2. reader reach: gold metadata yes, silver metadata / manifests no
    rc = sim.reader_credentials()
    r = duckdb.connect()
    r.execute("LOAD httpfs")
    r.execute("LOAD iceberg")
    r.execute(sim.secret_sql(rc, f"s3://{BUCKET}/{sim.customer}/", "r"))
    gold_loc = cat.load_table("gold.sem__claims").metadata_location
    out["reader_scan_gold"] = attempt(lambda: r.execute(f"SELECT count(*) FROM iceberg_scan('{gold_loc}')").fetchone()[0])
    out["reader_scan_silver"] = attempt(lambda: r.execute(f"SELECT count(*) FROM iceberg_scan('{ent.metadata_location}')").fetchone()[0])
    r.close()
    return out


def crypto_shred(sim: Sim, dl_work: Path) -> dict:
    """Disable the customer's key: the parser role can read nothing of the customer any more."""
    out = {}
    sim.aws.kms.disable_key(sim.kms_arn)
    s3 = sim.aws.boto3_client("s3", sim.parser_session())
    for name, key in (("ducklake gold catalog file", f"{sim.customer}/semantic_layer/_ducklake/gold.ducklake"),):
        out[name] = attempt(lambda key=key: s3.get_object(Bucket=BUCKET, Key=key)["ContentLength"])
    for name, like in (("silver data file", f"{sim.customer}/parquet/%.parquet"),
                       ("iceberg metadata.json", f"{sim.customer}/semantic_layer/iceberg/%metadata.json")):
        row = sim.aws.s3.db.execute("SELECT key FROM objects WHERE key LIKE ? LIMIT 1", (like,)).fetchone()
        if row:
            out[name] = attempt(lambda k=row[0]: s3.get_object(Bucket=BUCKET, Key=k)["ContentLength"])
    sim.aws.kms.enable_key(sim.kms_arn)
    return out


def main(data: Path, world: Path, iceberg: bool):
    shutil.rmtree(world, ignore_errors=True)
    days = json.loads((data / "days.json").read_text())[:3]
    sim = Sim(world / "sim")
    raw_before = sim.aws.s3.db.execute("SELECT count(*) FROM objects WHERE key LIKE ?", (f"{sim.customer}/raw/%",)).fetchone()[0]
    res = {"ducklake": probe_ducklake(sim, data, days, world / "dl")}
    if iceberg:
        res["iceberg"] = probe_iceberg(sim, data, days, world / "ice")
    res["crypto_shred"] = crypto_shred(sim, world / "dl")
    res["raw_objects_before_after"] = [raw_before, sim.aws.s3.db.execute(
        "SELECT count(*) FROM objects WHERE key LIKE ?", (f"{sim.customer}/raw/%",)).fetchone()[0]]
    res["prefixes_written"] = sorted({k.split("/")[1] + "/" + k.split("/")[2] for (k,) in sim.aws.s3.db.execute(
        "SELECT key FROM objects WHERE key LIKE ?", (f"{sim.customer}/%",)).fetchall()})
    sim.shutdown()
    shutil.rmtree(world, ignore_errors=True)
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), iceberg="--no-iceberg" not in sys.argv)
