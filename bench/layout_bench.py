"""Gold file-layout micro-benchmark: the same gold rows, written different ways, under the agent workload.

Takes a plaintext export of a lake's current gold (`python -m bench.lake_bench ... --export-gold DIR`), materialises every
relation the reader sees, and rewrites it as layout variants (file split, compression, sort order, row-group
size). Each variant is served by a plain ranged-HTTP file server that counts requests and bytes (no SigV4, no AES,
no IAM: only what the Parquet layout makes DuckDB fetch), and the workload from bench/questions.yaml runs cold
(fresh DuckDB per query, as production opens one per request) and warm on local disk.

    python -m bench.layout_bench DIR
Output: .ccsim/bench/lake/layout-<DIR name>.json and .md
"""
from __future__ import annotations

import json
import shutil
import statistics
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ThreadingHTTPServer.request_queue_size = 256      # no SYN drops (1 s retransmits) under DuckDB's parallel opens
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench.lake_bench import OUT, QUERY_PARALLELISM, s3_model_s, workload  # noqa: E402


class _RangeHandler(SimpleHTTPRequestHandler):
    """Static files with single-range GETs (what DuckDB httpfs issues), counting requests and body bytes."""

    def log_message(self, *a):
        pass

    def send_head(self):
        self.server.requests += 1
        path = Path(self.translate_path(self.path))
        if not path.is_file():
            self.send_error(404)
            return None
        size = path.stat().st_size
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng:
            a, b = rng.removeprefix("bytes=").split("-")
            start, end = int(a), min(int(b) if b else size - 1, size - 1)
        f = path.open("rb")
        f.seek(start)
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Last-Modified", self.date_time_string(int(path.stat().st_mtime)))
        self.send_header("ETag", f'"{size:x}-{path.stat().st_mtime_ns:x}"')    # S3 sends one; DuckDB's cache validates by it
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            f.close()
            return None
        self.server.bytes_out += end - start + 1
        self._remaining = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        outputfile.write(source.read(self._remaining))


def serve(directory: Path) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_RangeHandler, directory=str(directory)))
    httpd.daemon_threads = True
    httpd.requests = httpd.bytes_out = 0
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def relation_files(export: Path) -> dict[str, list[Path]]:
    manifest = json.loads((export / "manifest.json").read_text())
    return {rel: [export / k.split("/semantic_layer/", 1)[1] for part in sorted(parts, key=lambda p: (p != "schema", p))
                  for k in parts[part]] for rel, parts in manifest["relations"].items()}   # as ccsim/query.py lists them


SORTS = {
    "claim_key": "claim_key",
    "payer_then_claim": "payer_key, claim_key",
    "insertion": None,
}


def build_variant(con, files: dict[str, list[Path]], out: Path, split: str, codec: str, sort: str | None,
                  row_group_size: int) -> dict[str, list[Path]]:
    """split: 'as_published' (keep the partition files) or 'single' (one file per relation)."""
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    opts = f"FORMAT parquet, COMPRESSION {codec.split(':')[0]}, ROW_GROUP_SIZE {row_group_size}"
    if ":" in codec:
        opts += f", COMPRESSION_LEVEL {codec.split(':')[1]}"
    res = {}
    for rel, paths in files.items():
        groups = [paths[:1], paths[1:]] if split == "single" else [[p] for p in paths]   # keep the schema file first
        res[rel] = []
        for i, group in enumerate(groups):
            src = f"read_parquet({[str(p) for p in group]!r}, hive_partitioning=false)"
            cols = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()}
            order = SORTS[sort] if sort else None
            if order and not all(c.strip() in cols for c in order.split(",")):
                order = None
            p = out / f"{rel}-{i}.parquet"
            con.execute(f"COPY (SELECT * FROM {src}{' ORDER BY ' + order if order else ''}) TO '{p}' ({opts})")
            res[rel].append(p)
    return res


def run_workload(files: dict[str, list[Path]], base: Path) -> dict:
    httpd = serve(base)
    host = f"http://127.0.0.1:{httpd.server_address[1]}"
    rows = []
    for q in workload():
        con = duckdb.connect()
        r0, b0, t0 = httpd.requests, httpd.bytes_out, time.perf_counter()
        for rel, paths in files.items():
            schema = rel.split(".")[0]
            if schema == "lossless" and not q["escalate"]:
                continue
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            urls = [f"{host}/{p.relative_to(base)}" for p in paths]
            con.execute(f"CREATE VIEW {rel} AS SELECT * FROM read_parquet({urls!r}, hive_partitioning=false)")
        con.execute(q["sql"]).fetchall()
        cold_ms = (time.perf_counter() - t0) * 1000
        req, nbytes = httpd.requests - r0, httpd.bytes_out - b0
        local = duckdb.connect()
        for rel, paths in files.items():
            local.execute(f"CREATE SCHEMA IF NOT EXISTS {rel.split('.')[0]}")
            local.execute(f"CREATE VIEW {rel} AS SELECT * FROM read_parquet({[str(p) for p in paths]!r}, hive_partitioning=false)")
        ts = []
        for _ in range(4):
            t0 = time.perf_counter()
            local.execute(q["sql"]).fetchall()
            ts.append((time.perf_counter() - t0) * 1000)
        rows.append(dict(id=q["id"], cold_ms=round(cold_ms, 1), requests=req, bytes=nbytes,
                         s3_model_s=s3_model_s(req, nbytes, QUERY_PARALLELISM), local_warm_ms=round(statistics.median(ts[1:]), 1)))
    httpd.shutdown()
    return dict(queries=rows, **summary(rows))


def summary(rows) -> dict:
    med = lambda k: round(statistics.median(r[k] for r in rows), 2)
    return dict(median_requests=med("requests"), max_requests=max(r["requests"] for r in rows),
                median_mb=round(med("bytes") / 1e6, 2), total_mb=round(sum(r["bytes"] for r in rows) / 1e6, 1),
                median_s3_model_s=med("s3_model_s"), sum_s3_model_s=round(sum(r["s3_model_s"] for r in rows), 2),
                median_local_warm_ms=med("local_warm_ms"), sum_local_warm_ms=round(sum(r["local_warm_ms"] for r in rows), 1))


VARIANTS = [  # (name, split, codec, sort, row group rows); one knob changes at a time against "published"
    ("published (cohort-month files, zstd, claim_key)", "as_published", "zstd", "claim_key", 122_880),
    ("single file per relation", "single", "zstd", "claim_key", 122_880),
    ("snappy", "as_published", "snappy", "claim_key", 122_880),
    ("zstd level 9", "as_published", "zstd:9", "claim_key", 122_880),
    ("sorted payer_key, claim_key", "as_published", "zstd", "payer_then_claim", 122_880),
    ("unsorted (insertion order)", "as_published", "zstd", "insertion", 122_880),
    ("row groups of 32k rows", "as_published", "zstd", "claim_key", 32_768),
    ("row groups of 1M rows", "as_published", "zstd", "claim_key", 1_048_576),
    ("combined: sorted payer_key, claim_key + 1M-row groups", "as_published", "zstd", "payer_then_claim", 1_048_576),
]


def main(export: Path, label: str):
    files = relation_files(export)
    work = ROOT / ".ccsim" / f"layout-{label}"
    con = duckdb.connect()
    res = dict(export=str(export), variants=[])
    for name, split, codec, sort, rg in VARIANTS:
        t0 = time.perf_counter()
        v = build_variant(con, files, work / "v", split, codec, sort, rg)
        build_s = time.perf_counter() - t0
        sizes = {rel: sum(p.stat().st_size for p in ps) for rel, ps in v.items()}
        r = run_workload(v, work / "v")
        r.update(name=name, build_s=round(build_s, 1), files=sum(len(ps) for ps in v.values()),
                 sem_mb=round(sum(s for rel, s in sizes.items() if rel.startswith("sem.")) / 1e6, 1),
                 lossless_mb=round(sum(s for rel, s in sizes.items() if rel.startswith("lossless.")) / 1e6, 1))
        res["variants"].append(r)
        print(f"{name}: {json.dumps({k: v for k, v in r.items() if k != 'queries'})}", flush=True)
    shutil.rmtree(work, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"layout-{label}.json").write_text(json.dumps(res, indent=1))
    (OUT / f"layout-{label}.md").write_text(render(res))
    print(render(res))


def render(res) -> str:
    lines = [f"# Gold layout variants ({res['export']})", "",
             "| variant | files | sem MB | lossless MB | median / max requests per query | MB fetched (workload) | "
             "S3 model s (workload) | local warm ms (workload) |", "|---|---|---|---|---|---|---|---|"]
    for v in res["variants"]:
        lines.append(f"| {v['name']} | {v['files']} | {v['sem_mb']} | {v['lossless_mb']} | {v['median_requests']} / "
                     f"{v['max_requests']} | {v['total_mb']} | {v['sum_s3_model_s']} | {v['sum_local_warm_ms']} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    export_dir = Path(sys.argv[1])
    main(export_dir, export_dir.name)
