"""Storage-layout benchmark: does a 100-day silver tree (one part file per table per day) slow things down?

Simulates N days of daily drops through the real pipeline, then measures:
  1. per-batch Fargate time as history grows (the gold rebuild re-reads all of silver every batch)
  2. agent-query latency on gold (one file per sem table) vs silver read directly (N files per table)
     vs silver after compaction (one file per table per month)
  3. file counts / bytes per layer

  python bench/perf.py --days 100 --scale 0.5
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ccsim.edi_mock import Scenario  # noqa: E402
from ccsim.infra import BUCKET  # noqa: E402
from ccsim.pipeline import load_silver, SILVER_TABLES  # noqa: E402
from ccsim.query import open_session  # noqa: E402
from ccsim.world import build_world  # noqa: E402

QUERIES = {
    "denial_rate_by_payer": "SELECT payer_name, avg(is_denied::INT) FROM sem.remit_claims WHERE is_current_adjudication GROUP BY 1",
    "cpt_denials": "SELECT procedure_code, count(DISTINCT claim_payer_key) FILTER (WHERE claim_is_denied) FROM sem.remit_lines "
                   "WHERE is_current_adjudication GROUP BY 1",
    "paid_by_month": "SELECT payment_month, sum(paid_amount) FROM sem.remit_claims WHERE is_current_adjudication GROUP BY 1",
}


def timeit(con, sql, reps=5):
    ts = []
    for _ in range(reps):
        t = time.perf_counter()
        con.execute(sql).fetchall()
        ts.append(time.perf_counter() - t)
    return round(statistics.median(ts) * 1000, 1)


def tree_stats(path: Path):
    files = [p for p in path.rglob("*.parquet")]
    return dict(files=len(files), mb=round(sum(p.stat().st_size for p in files) / 1e6, 2))


def main(days, scale, out):
    t0 = time.time()
    aws, idp, truth = build_world(ROOT / ".ccsim" / "perf-world", Scenario(days=days, volume_scale=scale),
                                  customers=("acme_labs",), users={"ana@xifin.example": "acme_labs"})
    build_s = round(time.time() - t0, 1)
    tasks = [dict(day=t["overrides"]["received_date"], s=round(t["stopped"] - t["started"], 2)) for t in aws.tasks]
    base = aws.root / "s3" / BUCKET / "acme_labs"
    res = dict(days=days, scale=scale, claims=len(truth["tenants"]["acme_labs"]["claims"]), wall_build_s=build_s,
               batch_seconds_first5=[t["s"] for t in tasks[:5]], batch_seconds_last5=[t["s"] for t in tasks[-5:]],
               silver=tree_stats(base / "parquet"), gold=tree_stats(base / "semantic_layer"))

    # (a) gold, through the real per-request session
    _, con = open_session(aws, idp, idp.login("ana@xifin.example"))
    t = time.perf_counter()
    _, con2 = open_session(aws, idp, idp.login("ana@xifin.example"))
    res["session_open_ms"] = round((time.perf_counter() - t) * 1000, 1)
    res["gold_ms"] = {k: timeit(con, q) for k, q in QUERIES.items()}

    # (b) views computed live over the raw silver tree (what you'd get without a materialized gold layer)
    live = duckdb.connect()
    t = time.perf_counter()
    load_silver(live, base / "parquet")
    res["silver_load_into_duckdb_ms"] = round((time.perf_counter() - t) * 1000, 1)
    res["silver_live_views_ms"] = {k: timeit(live, q, 3) for k, q in QUERIES.items()}

    # (c) scan cost of the many-small-files tree vs a compacted one (one file per table)
    comp = ROOT / ".ccsim" / "perf-compacted"
    comp.mkdir(parents=True, exist_ok=True)
    scan = {}
    for tname in ("edi.segment", "r835.service_payment", "edi.adjustment"):
        src = f"read_parquet('{base}/parquet/{tname}/**/*.parquet', hive_partitioning=false)"
        dst = comp / f"{tname}.parquet"
        duckdb.execute(f"COPY (SELECT * FROM {src}) TO '{dst}' (FORMAT parquet)")
        many = timeit(duckdb.connect(), f"SELECT count(*), max(file_id) FROM {src}")
        one = timeit(duckdb.connect(), f"SELECT count(*), max(file_id) FROM read_parquet('{dst}')")
        scan[tname] = dict(daily_files_ms=many, compacted_ms=one,
                           files=len(list((base / "parquet" / tname).rglob("*.parquet"))))
    res["scan_daily_vs_compacted"] = scan
    Path(out).write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=100)
    ap.add_argument("--scale", type=float, default=0.5)
    ap.add_argument("--out", default=str(ROOT / ".ccsim" / "perf-results.json"))
    a = ap.parse_args()
    main(a.days, a.scale, a.out)
