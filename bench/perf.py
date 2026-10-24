"""Storage-layout benchmark: what does a growing daily-partition tree cost?

Replays N days of drops through the real pipeline (SFTP copy -> EventBridge -> Fargate parser -> gold), then
measures with injected S3 latency (default 0, 10 and 30 ms per request, the order of same-region S3 first-byte
latency):

  batch          seconds and HTTP requests per daily parser run as history grows (gold is a full rebuild)
  session        opening a per-request DuckDB session (STS + CURRENT + views)
  gold           agent-style queries on the materialized gold layer
  silver_live    the same views computed live from silver (one Parquet per table per day)
  silver_compact the same after compacting silver to one Parquet per table

    python -m bench.perf --days 100 --scale 0.5
Output: .ccsim/bench/perf-<days>d.json and .md
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

from ccsim.aws.sts import service_principal  # noqa: E402
from ccsim.edi_mock import Scenario  # noqa: E402
from ccsim.infra import BUCKET, names, role_arn  # noqa: E402
from ccsim.pipeline import SILVER_TABLES, load_silver  # noqa: E402
from ccsim.query import login, open_session  # noqa: E402
from ccsim.world import build_world  # noqa: E402

QUERIES = {
    "denial_rate_by_payer": "SELECT payer_name, avg(is_denied::INT) FROM sem.remit_claims WHERE is_current_adjudication GROUP BY 1",
    "cpt_lines": "SELECT procedure_code, count(*) FILTER (WHERE line_is_denied) FROM sem.remit_lines WHERE is_current_adjudication GROUP BY 1",
    "paid_by_month": "SELECT payment_month, sum(paid_amount) FROM sem.remit_claims WHERE is_current_adjudication GROUP BY 1",
}
C = "acme_labs"


def timed(fn, reps=3):
    ts = []
    for _ in range(reps):
        t = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t)
    return round(statistics.median(ts) * 1000, 1)


def parser_creds(aws):
    return aws.sts.assume_role(service_principal("ecs-tasks.amazonaws.com"), None, role_arn(names(C)["parser"]), "perf",
                               SourceContext={"aws:SourceAccount": "111122223333",
                                              "aws:SourceArn": "arn:aws:ecs:us-west-2:111122223333:task/cc/perf"})


def silver_con(aws, creds):
    con = duckdb.connect(config={"autoinstall_known_extensions": False, "autoload_known_extensions": False})
    con.execute("LOAD httpfs")
    con.execute(aws.duckdb_secret_sql(creds, f"s3://{BUCKET}/{C}/"))
    return con


def main(days, scale, latencies, out_dir):
    w = build_world(ROOT / ".ccsim" / f"perf-{days}d", Scenario(days=days, volume_scale=scale), customers=(C,),
                    users={"ana@xifin.example": C}, deliver=False)
    aws, ep = w.aws, w.aws.endpoint
    batches = []
    for day in w.truth["tenants"][C]["file_days"]:
        r0, t0 = ep.requests, time.perf_counter()
        w.deliver(C, day)
        t = w.tasks()[-1]
        batches.append(dict(day=day, s=round(time.perf_counter() - t0, 2), requests=ep.requests - r0, ok=t.exit_code == 0))
    creds = parser_creds(aws)
    s3 = aws.boto3_client("s3", creds)
    silver_keys = []
    tok = None
    while True:
        r = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"{C}/parquet/", **({"ContinuationToken": tok} if tok else {}))
        silver_keys += [o["Key"] for o in r.get("Contents", [])]
        if not r.get("IsTruncated"):
            break
        tok = r["NextContinuationToken"]
    # compact silver: one file per table (kept under parquet/_compacted/ so the parser role may write it)
    present = {k.split("/")[2] for k in silver_keys}
    con = silver_con(aws, creds)
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        for t in sorted(present):
            p = Path(tmp) / f"{t}.parquet"
            con.execute(f"COPY (SELECT * FROM read_parquet('s3://{BUCKET}/{C}/parquet/{t}/**/*.parquet', union_by_name=true, "
                        f"hive_partitioning=false)) TO '{p}' (FORMAT parquet)")
            s3.put_object(Bucket=BUCKET, Key=f"{C}/parquet/_compacted/{t}/part-0.parquet", Body=p.read_bytes(),
                          ServerSideEncryption="aws:kms", SSEKMSKeyId=w.dep.keys[C].arn)
    res = dict(days=days, scale=scale, business_days=len(batches), claims=len(w.truth["tenants"][C]["claims"]),
               silver_files=len(silver_keys), silver_tables=len(present), batches=batches, by_latency={})
    for lat in latencies:
        ep.set_latency(lat)
        tok = login(aws, "ana@xifin.example")
        row = {}
        r0 = ep.requests
        row["session_open_ms"] = timed(lambda: open_session(aws, w.dep, tok), reps=2)
        s = open_session(aws, w.dep, tok)
        row["gold_ms"] = {k: timed(lambda q=q: s.con.execute(q).fetchall()) for k, q in QUERIES.items()}
        for label, base in (("silver_live", f"s3://{BUCKET}/{C}/parquet"), ("silver_compact", f"s3://{BUCKET}/{C}/parquet/_compacted")):
            fresh = parser_creds(aws)
            live = silver_con(aws, fresh)
            r0 = ep.requests
            t0 = time.perf_counter()
            load_silver(live, base, present)
            row[f"{label}_load_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            row[f"{label}_load_requests"] = ep.requests - r0
            row[f"{label}_query_ms"] = {k: timed(lambda q=q: live.execute(q).fetchall()) for k, q in QUERIES.items()}
        res["by_latency"][str(lat)] = row
        print(f"latency {lat} ms: {json.dumps(row)}", flush=True)
    ep.set_latency(0)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"perf-{days}d.json").write_text(json.dumps(res, indent=1))
    (out_dir / f"perf-{days}d.md").write_text(render(res))
    aws.shutdown()
    return res


def render(r) -> str:
    b = r["batches"]
    q = lambda xs: f"{xs[0]['s']} s / {xs[0]['requests']} req"
    lines = [f"# Storage benchmark: {r['days']} days ({r['business_days']} business-day batches), {r['claims']:,} claims", "",
             f"Silver after the run: {r['silver_files']:,} Parquet files across {r['silver_tables']} tables.", "",
             "| parser batch | seconds | HTTP requests |", "|---|---|---|"]
    for i in sorted({0, len(b) // 4, len(b) // 2, 3 * len(b) // 4, len(b) - 1}):
        lines.append(f"| #{i + 1} ({b[i]['day']}) | {b[i]['s']} | {b[i]['requests']:,} |")
    lines += ["", "| S3 latency | session open | gold query (median of 3) | silver load: daily files | silver load: compacted |",
              "|---|---|---|---|---|"]
    for lat, row in r["by_latency"].items():
        g = statistics.median(row["gold_ms"].values())
        lines.append(f"| {lat} ms | {row['session_open_ms']} ms | {g} ms | {row['silver_live_load_ms']:,} ms "
                     f"({row['silver_live_load_requests']:,} req) | {row['silver_compact_load_ms']:,} ms "
                     f"({row['silver_compact_load_requests']:,} req) |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=100)
    ap.add_argument("--scale", type=float, default=.5)
    ap.add_argument("--latency", default="0,10,30")
    a = ap.parse_args()
    main(a.days, a.scale, [float(x) for x in a.latency.split(",")], ROOT / ".ccsim" / "bench")
