"""Lake benchmark: what does each batch and each agent query cost at production volume?

Streams N calendar days of mock EDI at a real volume (default 5 MB raw per business day per customer) through the
real path: IT's SFTP copy -> EventBridge -> Fargate parser (bronze -> staging -> silver -> gold) -> CURRENT. The
generator hands over one day at a time and the local SFTP copy is deleted after each delivery, so disk stays flat.

Per parser batch it records wall time (parse / silver / gold split), HTTP requests by method, bytes read and
written, and the task's peak RSS. After the run it inventories silver and gold (files, bytes per table) and runs
the agent workload (every gold_sql in bench/questions.yaml) cold (fresh session) and warm (same session),
recording requests and bytes per query, then the same queries on a local-disk copy of gold (no HTTP), which
separates the Parquet layout's compute cost from the simulated endpoint's overhead.

The local endpoint is one Python process (AES-GCM + GIL), so its wall-clock is not S3's. Request counts and
bytes are the portable numbers; `s3_model_s` turns them into an S3 estimate:
    requests x first-byte latency / parallelism + bytes / bandwidth.

    python -m bench.lake_bench --days 30 --label baseline
    python -m bench.lake_bench --days 365 --customers acme_labs,acme_diagnostics --label winner --purge-raw
Output: .ccsim/bench/lake/<label>-<days>d.json and .md
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import shutil
import statistics
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ccsim import edi_mock  # noqa: E402
from ccsim.aws import LocalAWS, reset  # noqa: E402
from ccsim.aws.sts import service_principal  # noqa: E402
from ccsim.infra import BUCKET, names, provision, role_arn, sftp_drop  # noqa: E402
from ccsim.pipeline import parse_batch  # noqa: E402
from ccsim.query import login, open_session  # noqa: E402
from ccsim.world import DEFAULT_USERS, add_user  # noqa: E402

OUT = ROOT / ".ccsim" / "bench" / "lake"
S3_FIRST_BYTE_S = 0.025          # same-region S3 GET first-byte latency (20-30 ms)
S3_BANDWIDTH_BPS = 100e6         # one Fargate task / app server stream to S3, bytes per second
QUERY_PARALLELISM = 8            # concurrent range requests one DuckDB query keeps in flight


def s3_model_s(requests: int, nbytes: int, parallelism: int = 1) -> float:
    """S3 time estimate from portable counters (requests serialised `parallelism` at a time, plus transfer)."""
    return round(requests * S3_FIRST_BYTE_S / parallelism + nbytes / S3_BANDWIDTH_BPS, 2)


def diff(after: dict, before: dict) -> dict:
    return {k: v - before.get(k, 0) for k, v in after.items()}


def _reset_peak_rss():
    try:
        Path("/proc/self/clear_refs").write_text("5")
    except OSError:
        pass


def _peak_rss_mb() -> float:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmHWM:"):
            return round(int(line.split()[1]) / 1024, 1)
    return 0.0


def parser_session(aws, c):
    return aws.sts.assume_role(service_principal("ecs-tasks.amazonaws.com"), None, role_arn(names(c)["parser"]), "bench",
                               SourceContext={"aws:SourceAccount": "111122223333",
                                              "aws:SourceArn": "arn:aws:ecs:us-west-2:111122223333:task/claimclarity/bench"})


def purge_raw_blobs(aws, c: str, day: str):
    """Benchmark-only disk saver: drop the ciphertext of one ingested raw day from the simulator's disk (the object
    rows stay). Raw is Object-Locked in S3; this models it living in S3, not on the bench machine."""
    rows = aws.s3.db.execute("SELECT blob FROM objects WHERE bucket=? AND key LIKE ?",
                             (BUCKET, f"{c}/raw/received_date={day}/%")).fetchall()
    for (blob,) in rows:
        if blob:
            (aws.s3.root / "s3data" / blob).unlink(missing_ok=True)


def inventory(aws, c: str) -> dict:
    """Live (latest, non-deleted) objects per layer and table: files and bytes."""
    rows = aws.s3.db.execute("SELECT key, size FROM objects WHERE bucket=? AND is_latest=1 AND delete_marker=0 AND key LIKE ?",
                             (BUCKET, f"{c}/%")).fetchall()
    out: dict = {}
    for key, size in rows:
        parts = key.split("/")
        layer = parts[1]
        table = parts[2] if layer == "parquet" else parts[3] if layer == "semantic_layer" and len(parts) > 3 else "-"
        if layer == "semantic_layer" and parts[2].startswith("v="):
            table = parts[3]
        t = out.setdefault(layer, {}).setdefault(table, [0, 0])
        t[0] += 1
        t[1] += size
    return {layer: {"files": sum(v[0] for v in ts.values()), "bytes": sum(v[1] for v in ts.values()),
                    "tables": {k: {"files": v[0], "bytes": v[1]} for k, v in sorted(ts.items())}} for layer, ts in out.items()}


def workload() -> list[dict]:
    cases = yaml.safe_load((ROOT / "bench" / "questions.yaml").read_text())
    return [dict(id=c["id"], sql=c["gold_sql"], escalate=c.get("escalate", False)) for c in cases if c.get("gold_sql")]


def run_queries(aws, dep, upn: str) -> list[dict]:
    """Cold = one agent request as production serves it: open a fresh session (STS, CURRENT, views; a fresh DuckDB
    instance has no file or metadata cache) and run the query. Warm = median of 3 repeats in that session."""
    ep = aws.endpoint
    tok = login(aws, upn)
    out = []
    for q in workload():
        c0, t0 = ep.counters(), time.perf_counter()
        s = open_session(aws, dep, tok, escalate=q["escalate"])
        open_ms = (time.perf_counter() - t0) * 1000
        n = len(s.con.execute(q["sql"]).fetchall())
        cold_ms = (time.perf_counter() - t0) * 1000
        cold = diff(ep.counters(), c0)
        ts = []
        c1 = ep.counters()
        for _ in range(3):
            t0 = time.perf_counter()
            s.con.execute(q["sql"]).fetchall()
            ts.append((time.perf_counter() - t0) * 1000)
        warm = diff(ep.counters(), c1)
        out.append(dict(id=q["id"], rows=n, open_ms=round(open_ms, 1), cold_ms=round(cold_ms, 1),
                        cold_requests=cold["requests"], cold_bytes=cold["bytes_out"], warm_ms=round(statistics.median(ts), 1),
                        warm_requests_per_run=round(warm["requests"] / 3, 1),
                        cold_s3_model_s=s3_model_s(cold["requests"], cold["bytes_out"], QUERY_PARALLELISM)))
        s.con.close()
    return out


def run_queries_local(aws, dep, upn: str, tmp: Path) -> list[dict]:
    """Copy the reader's current gold files to local disk (plaintext) and time the workload with no HTTP at all."""
    s = open_session(aws, dep, login(aws, upn), escalate=True)
    views = s.con.execute("SELECT schema_name, view_name, sql FROM duckdb_views() WHERE NOT internal").fetchall()
    s3 = aws.client("s3", s.credentials)
    import re
    con = duckdb.connect()
    for schema, view, sql in views:
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        uris = re.findall(r"'(s3://[^']+)'", sql)
        local = []
        for uri in uris:
            pattern = uri.removeprefix(f"s3://{BUCKET}/")
            keys = [pattern]
            if "*" in pattern:
                listed = s3.list_objects_v2(Bucket=BUCKET, Prefix=pattern.split("*", 1)[0]).get("Contents", [])
                keys = [o["Key"] for o in listed if fnmatch.fnmatch(o["Key"], pattern)]
            for key in keys:
                p = tmp / key
                if not p.exists():
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
                local.append(str(p))
        if not uris:                                     # glob form: s3://.../*.parquet is not in sql literally
            raise RuntimeError(f"cannot localise view {schema}.{view}: {sql[:200]}")
        con.execute(f"CREATE VIEW {schema}.{view} AS SELECT * FROM read_parquet({local!r})")
    out = []
    for q in workload():
        ts = []
        for i in range(4):
            t0 = time.perf_counter()
            con.execute(q["sql"]).fetchall()
            ts.append((time.perf_counter() - t0) * 1000)
        out.append(dict(id=q["id"], first_ms=round(ts[0], 1), warm_ms=round(statistics.median(ts[1:]), 1)))
    return out


def export_gold(aws, dep, customer: str, dest: Path):
    """Plaintext copy of the customer's current gold version (manifest + every file it lists), for layout_bench."""
    upn = next(u for u, c in DEFAULT_USERS.items() if c == customer)
    s = open_session(aws, dep, login(aws, upn))
    s3 = aws.client("s3", s.credentials)
    prefix = f"{customer}/semantic_layer/"
    manifest = s3.get_object(Bucket=BUCKET, Key=f"{prefix}{s.version}/manifest.json")["Body"].read()
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    (dest / "manifest.json").write_bytes(manifest)
    for parts in json.loads(manifest)["relations"].values():
        for key in (k for keys in parts.values() for k in keys):
            p = dest / key.removeprefix(prefix)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())


def claim_lookup_probe(aws, c: str) -> dict:
    """The per-batch claim-key lookup (which older silver files hold the touched claims), measured alone: as the
    pipeline does it (claim key + file_id columns of every live c837.claim / r835.claim_payment file), and against
    a claim-key -> file index (one file sorted by claim key) built from the same rows."""
    from ccsim.lake import Store, silver_state
    from ccsim.pipeline import _scan
    creds = parser_session(aws, c)
    st = Store(aws.boto3_client("s3", creds), BUCKET, c, aws.kms.resolve(f"alias/claimclarity/{c}").arn)
    silver = silver_state(st)
    newest = max(silver.committed_runs)
    cols = {"c837.claim": "clm01_patient_control_number", "r835.claim_payment": "clp01_patient_control_number"}
    con = duckdb.connect(config={"autoinstall_known_extensions": False, "autoload_known_extensions": False})
    con.execute("LOAD httpfs")
    con.execute(aws.duckdb_secret_sql(creds, f"s3://{BUCKET}/{c}/"))
    con.execute("CREATE TABLE k AS " + " UNION ".join(
        f"SELECT {col} AS claim_key FROM {_scan(st, silver.parts_of(t, [newest]))}" for t, col in cols.items()
        if silver.parts_of(t, [newest])))
    ep = aws.endpoint

    def measure(sql_by_table: dict) -> dict:
        c0, cpu0 = ep.counters(), time.process_time()
        hits = sum(len(con.execute(sql).fetchall()) for sql in sql_by_table.values())
        d = diff(ep.counters(), c0)
        return dict(requests=d["requests"], mb=round(d["bytes_out"] / 1e6, 2), cpu_s=round(time.process_time() - cpu0, 2),
                    hits=hits)
    files = {t: [k for k in silver.files(t) if k not in set(silver.parts_of(t, [newest]))] for t in cols}
    scan = measure({t: f"SELECT DISTINCT file_id, filename FROM {_scan(st, files[t], ', filename=true')} "
                       f"WHERE {col} IN (SELECT claim_key FROM k)" for t, col in cols.items()})
    index_key = f"{c}/parquet/_claim_index/received_month=0000-00/compacted-run_0.parquet"
    with tempfile.TemporaryDirectory(dir=ROOT / ".ccsim") as tmp:
        p = Path(tmp) / "index.parquet"
        con.execute("COPY (" + " UNION ALL ".join(f"SELECT {col} AS claim_key, file_id FROM {_scan(st, files[t])}"
                                                  for t, col in cols.items()) +
                    f") TO '{p}' (FORMAT parquet, COMPRESSION zstd)")
        con.execute(f"COPY (SELECT * FROM '{p}' ORDER BY claim_key) TO '{p}.sorted' (FORMAT parquet, COMPRESSION zstd)")
        st.put(index_key, Path(f"{p}.sorted").read_bytes())
        index_mb = round(Path(f"{p}.sorted").stat().st_size / 1e6, 2)
    index = measure({"index": f"SELECT DISTINCT file_id FROM {_scan(st, [index_key])} WHERE claim_key IN (SELECT claim_key FROM k)"})
    st.delete([index_key])
    return dict(touched_keys=con.execute("SELECT count(*) FROM k").fetchone()[0], lookup_files=sum(map(len, files.values())),
                scan=scan, index=dict(index, index_mb=index_mb))


def checkpoint(aws, dep, customers: list[str], n_batches: int, queries: bool) -> dict:
    out = dict(batches=n_batches, inventory={c: inventory(aws, c) for c in customers},
               claim_lookup={c: claim_lookup_probe(aws, c) for c in customers})
    if queries:
        upn = next(u for u, c in DEFAULT_USERS.items() if c == customers[0])
        out["queries"] = run_queries(aws, dep, upn)
        with tempfile.TemporaryDirectory(dir=ROOT / ".ccsim") as tmp:
            out["queries_local"] = run_queries_local(aws, dep, upn, Path(tmp))
    return out


def stream(root: Path, customers: list[str], scenario: edi_mock.Scenario, pipeline_env: dict, purge_raw: bool,
           checkpoints: set[int], queries: bool, progress_every: int = 10, partial_path: Path | None = None) -> tuple:
    """checkpoints: business-day counts (of the first customer) after which to inventory and run the workload."""
    reset(root)
    aws = LocalAWS(root / "aws")
    ep = aws.serve()
    dep = provision(aws, customers, parse_batch, pipeline_env=pipeline_env)
    aws.events.auto_drain = False
    for upn, c in DEFAULT_USERS.items():
        if c in customers:
            add_user(aws, upn, c)
    batches, cps = [], []
    t_start = time.perf_counter()
    for dd in edi_mock.generate_days(root / "sftp", scenario, customers):
        day = dd.day.isoformat()
        sftp_drop(aws, dep, dd.tenant, dd.path, day)
        shutil.rmtree(dd.path)
        _reset_peak_rss()
        c0, t0, cpu0, thread0 = ep.counters(), time.perf_counter(), time.process_time(), time.thread_time()
        n_tasks = len(aws.ecs.tasks)
        aws.events.drain()
        d = diff(ep.counters(), c0)
        cpu_s, thread_cpu_s = round(time.process_time() - cpu0, 2), round(time.thread_time() - thread0, 2)
        tasks = aws.ecs.tasks[n_tasks:]
        if not tasks or any(t.exit_code for t in tasks):
            raise RuntimeError(f"parser task failed on {dd.tenant} {day}: "
                               f"{[t.result for t in tasks if t.exit_code] or 'no task'}")
        r = tasks[-1].result
        batches.append(dict(customer=dd.tenant, day=day, raw_bytes=dd.bytes, s=round(time.perf_counter() - t0, 2),
                            cpu_s=cpu_s, task_thread_cpu_s=thread_cpu_s,
                            peak_rss_mb=_peak_rss_mb(), requests=d["requests"], bytes_read=d["bytes_out"],
                            bytes_written=d["bytes_in"], ops={k[3:]: v for k, v in d.items() if k.startswith("op_")},
                            s3_model_s=s3_model_s(d["requests"], d["bytes_out"] + d["bytes_in"]),
                            **{k: r.get(k) for k in ("status", "loaded", "rejected", "parse_s", "silver_s", "gold_s",
                                                     "compact_s", "gold_mode", "gold")}))
        if purge_raw:
            purge_raw_blobs(aws, dd.tenant, day)
        if dd.tenant == customers[-1]:              # a business day passes: S3 lifecycle expires noncurrent versions
            aws.clock.advance(86400)                # (deleted gold / silver files), as the bucket's rules say
            aws.s3.run_lifecycle()
        n_first = sum(b["customer"] == customers[0] for b in batches)
        if dd.tenant == customers[-1] and n_first in checkpoints:
            cps.append(checkpoint(aws, dep, customers, n_first, queries))
            print(render_checkpoint(cps[-1]), flush=True)
        if partial_path and len(batches) % progress_every == 0:   # long runs: keep what we have if killed later
            partial_path.write_text(json.dumps(dict(batches=batches, checkpoints=cps), indent=1))
        if len(batches) % progress_every == 0:
            b = batches[-1]
            print(f"[{time.perf_counter() - t_start:7.0f}s] batch {len(batches)} {b['customer']} {day}: {b['s']} s, "
                  f"{b['requests']} req, read {b['bytes_read'] / 1e6:.1f} MB, write {b['bytes_written'] / 1e6:.1f} MB, "
                  f"rss {b['peak_rss_mb']} MB, gold {b['gold_s']} s", flush=True)
    n_first = sum(b["customer"] == customers[0] for b in batches)
    if n_first not in {cp["batches"] for cp in cps}:
        cps.append(checkpoint(aws, dep, customers, n_first, queries))
        print(render_checkpoint(cps[-1]), flush=True)
    return aws, dep, batches, cps


def main(days: int, customers: list[str], mb_per_day: float, label: str, pipeline_env: dict, purge_raw: bool,
         queries: bool, start: date, keep_world: bool, checkpoints: set[int], export: Path | None = None) -> dict:
    root = ROOT / ".ccsim" / f"lake-{label}-{days}d"
    sc = edi_mock.Scenario(start=start, days=days, target_mb_per_day=mb_per_day)
    t0 = time.perf_counter()
    OUT.mkdir(parents=True, exist_ok=True)
    aws, dep, batches, cps = stream(root, customers, sc, pipeline_env, purge_raw, checkpoints, queries,
                                    partial_path=OUT / f"{label}-{days}d.partial.json")
    res = dict(label=label, days=days, customers=customers, mb_per_day=mb_per_day, pipeline_env=pipeline_env,
               ingest_wall_s=round(time.perf_counter() - t0, 1), batches=batches, checkpoints=cps)
    if export:
        export_gold(aws, dep, customers[0], export)
    aws.shutdown()
    if not keep_world:
        shutil.rmtree(root, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{label}-{days}d.json").write_text(json.dumps(res, indent=1))
    (OUT / f"{label}-{days}d.md").write_text(render(res))
    print(render(res))
    return res


def _pick(batches, n=6):
    idx = sorted({round(i * (len(batches) - 1) / (n - 1)) for i in range(n)})
    return [batches[i] for i in idx]


def render_checkpoint(cp: dict) -> str:
    mb = lambda x: f"{x / 1e6:,.1f}"
    lines = [f"## After {cp['batches']} business days", ""]
    for c, probe in cp.get("claim_lookup", {}).items():
        lines += [f"Claim lookup {c}: {probe['touched_keys']:,} keys over {probe['lookup_files']} files: scan {probe['scan']}, "
                  f"index {probe['index']}", ""]
    for c, inv in cp["inventory"].items():
        lines += [f"Inventory {c}: " + ", ".join(f"{layer} {v['files']:,} files / {mb(v['bytes'])} MB"
                                                for layer, v in sorted(inv.items())), ""]
    if "queries" in cp:
        qs, ql = cp["queries"], {x["id"]: x for x in cp["queries_local"]}
        lines += ["| query | rows | open+query ms | requests | MB read | S3 model s | warm ms | local disk first / warm ms |",
                  "|---|---|---|---|---|---|---|---|"]
        for q in qs:
            lq = ql[q["id"]]
            lines.append(f"| {q['id']} | {q['rows']} | {q['cold_ms']} | {q['cold_requests']} | {mb(q['cold_bytes'])} | "
                         f"{q['cold_s3_model_s']} | {q['warm_ms']} | {lq['first_ms']} / {lq['warm_ms']} |")
        lines += ["", query_summary(cp)]
    return "\n".join(lines) + "\n"


def query_summary(cp: dict) -> str:
    qs, ql = cp["queries"], cp["queries_local"]
    med = lambda k, xs: round(statistics.median(x[k] for x in xs), 1)
    mx = lambda k, xs: round(max(x[k] for x in xs), 1)
    return (f"Workload ({len(qs)} queries), median / max: open+query {med('cold_ms', qs)} / {mx('cold_ms', qs)} ms, "
            f"requests {med('cold_requests', qs)} / {mx('cold_requests', qs)}, MB read {med('cold_bytes', qs) / 1e6:.2f} / "
            f"{mx('cold_bytes', qs) / 1e6:.2f}, S3 model {med('cold_s3_model_s', qs)} / {mx('cold_s3_model_s', qs)} s, "
            f"warm {med('warm_ms', qs)} / {mx('warm_ms', qs)} ms; local disk warm {med('warm_ms', ql)} / {mx('warm_ms', ql)} ms")


def render(r: dict) -> str:
    b = r["batches"]
    mb = lambda x: f"{x / 1e6:,.1f}"
    lines = [f"# Lake benchmark `{r['label']}`: {r['days']} calendar days, {len(b)} batches, {', '.join(r['customers'])}, "
             f"{r['mb_per_day']} MB/day", "", f"Pipeline config: `{json.dumps(r['pipeline_env'])}`; ingest wall "
             f"{r['ingest_wall_s']:,} s", "",
             "| batch | customer | day | raw MB | s (parse/silver/gold) | requests | MB read | MB written | S3 model s | peak RSS MB |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for x in _pick(b, 8):
        lines.append(f"| {b.index(x) + 1} | {x['customer']} | {x['day']} | {mb(x['raw_bytes'])} | {x['s']} ({x['parse_s']}/"
                     f"{x['silver_s']}/{x['gold_s']}) | {x['requests']:,} | {mb(x['bytes_read'])} | {mb(x['bytes_written'])} | "
                     f"{x['s3_model_s']} | {x['peak_rss_mb']:,} |")
    tot = lambda k: sum(x[k] for x in b)
    lines += ["", f"Totals: {tot('requests'):,} requests, {mb(tot('bytes_read'))} MB read, {mb(tot('bytes_written'))} MB "
              f"written, S3 model {sum(x['s3_model_s'] for x in b):,.0f} s, wall {tot('s'):,.0f} s, "
              f"raw {mb(tot('raw_bytes'))} MB", ""]
    for cp in r["checkpoints"]:
        lines.append(render_checkpoint(cp))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="calendar days (about 5/7 are business days)")
    ap.add_argument("--customers", default="acme_labs")
    ap.add_argument("--mb-per-day", type=float, default=5.0)
    ap.add_argument("--label", default="run")
    ap.add_argument("--env", default="", help="pipeline config, KEY=VALUE[,KEY=VALUE]")
    ap.add_argument("--start", default="2026-01-05")
    ap.add_argument("--purge-raw", action="store_true", help="drop ingested raw ciphertext from the sim's disk")
    ap.add_argument("--no-queries", action="store_true")
    ap.add_argument("--keep-world", action="store_true")
    ap.add_argument("--export-gold", type=Path, help="write a plaintext copy of the final gold here (layout_bench)")
    ap.add_argument("--checkpoints", default="20,60,120,250,500,750", help="business days at which to run the workload")
    a = ap.parse_args()
    env = dict(kv.split("=", 1) for kv in a.env.split(",") if kv)
    main(a.days, a.customers.split(","), a.mb_per_day, a.label, env, a.purge_raw, not a.no_queries,
         date.fromisoformat(a.start), a.keep_world, {int(x) for x in a.checkpoints.split(",") if x}, a.export_gold)
