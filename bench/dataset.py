"""Benchmark datasets: built once through the full simulated backend, then frozen as a snapshot.

A dataset id hashes everything that determines the data: scenario, generator version, parser, view SQL and
ref seeds. `snapshot()` builds the world (SFTP -> EventBridge -> Fargate parser -> gold) and copies the
reader's view of gold (sem.* plus the PHI-stripped lossless.* tables) to local Parquet, with the ledger.
Agent sessions then open a sandboxed DuckDB on that snapshot: the same views, the same restrictions
(allowed_directories, no external access, locked config), with no HTTP in the timing. The answer key
(truth.json + truth/<customer>/claims.jsonl, read with `load_truth`) sits outside data/.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".ccsim" / "bench"
SHIPPED = ROOT / "mock-data" / "bench"   # the default snapshot, committed so a fresh clone needn't rebuild it
DEFAULT = dict(days=45, volume_scale=.6, seed=2026, p_corrupt_file=.1)   # enough quarantined files for q29


def dataset_id(scenario) -> str:
    from ccsim import edi_mock
    h = hashlib.sha256()
    h.update(json.dumps({k: str(v) for k, v in asdict(scenario).items()}, sort_keys=True).encode())
    h.update(edi_mock.GENERATOR_VERSION.encode() + b"layout-3")
    for f in ("ccsim/edi_mock.py", "ccsim/data/clfs_2026q4.csv", "ccsim/parser.py", "ccsim/pipeline.py",
              "claude/schema/01_lossless_edi_schema.sql",
              "claude/schema/02_semantic_layer_views.sql"):
        h.update((ROOT / f).read_bytes())
    return h.hexdigest()[:12]


def snapshot(scenario=None, customer_user="ana@xifin.example", rebuild=False, cache: Path | None = None) -> Path:
    from ccsim.edi_mock import Scenario
    from ccsim.pipeline import ESCALATION, SEM_TABLES
    from ccsim.query import login, open_session
    from ccsim.world import build_world
    scenario = scenario or Scenario(**DEFAULT)
    did = dataset_id(scenario)
    cache = Path(cache or CACHE)
    out = cache / did
    if (out / "meta.json").exists() and not rebuild:
        return out
    if not rebuild and (SHIPPED / did / "meta.json").exists():   # same id = same generator, parser and schema
        shutil.copytree(SHIPPED / did, out, dirs_exist_ok=True)
        return out
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    w = build_world(cache / f"world-{did}", scenario)
    failed = [t.stopped_reason for t in w.tasks() if t.exit_code]
    if failed:
        raise RuntimeError(f"pipeline failed while building the dataset: {failed[:2]}")
    s = open_session(w.aws, w.dep, login(w.aws, customer_user), escalate=True)
    rels = [f"sem.{t}" for t in SEM_TABLES] + [f"lossless.{t.replace('.', '_')}" for t in ESCALATION]
    import pyarrow.parquet as pq
    for rel in rels:                       # data/<schema>/<rel>.parquet; the ledger and meta.json stay outside data/
        d = out / "data" / rel.split(".")[0]
        d.mkdir(parents=True, exist_ok=True)
        pq.write_table(s.con.sql(f"SELECT * FROM {rel}").to_arrow_table(), d / f"{rel}.parquet")
    from ccsim.edi_mock import write_truth  # truth.json + truth/<customer>/claims.jsonl
    write_truth(out, dict(w.truth, customer=s.customer, tenants={s.customer: w.truth["tenants"][s.customer]}))
    from bench.truth_db import build_truth_db                         # answer keys at scale: SQL over the ledger
    build_truth_db(out, s.customer, out / "answer_key.duckdb")
    import duckdb as _duckdb
    from bench.question_families import build_questions               # questions generated for this dataset
    with _duckdb.connect(str(out / "answer_key.duckdb"), read_only=True) as answer_key:
        (out / "generated_questions.json").write_text(json.dumps(build_questions(answer_key), indent=1))
    (out / "meta.json").write_text(json.dumps({"dataset_id": did, "customer": s.customer, "scenario": {
        k: str(v) for k, v in asdict(scenario).items()}, "gold_version": s.version, "relations": rels}, indent=1))
    w.aws.shutdown()
    shutil.rmtree(cache / f"world-{did}", ignore_errors=True)
    return out


def open_snapshot(path: Path, escalate: bool = False) -> duckdb.DuckDBPyConnection:
    """Sandboxed connection: only data/sem/ (and data/lossless/ when escalating) is readable; the answer key
    (truth.json, truth/) and meta.json sit outside every allowed directory."""
    meta = json.loads((path / "meta.json").read_text())
    con = duckdb.connect(config={"autoinstall_known_extensions": False, "autoload_known_extensions": False})
    schemas = ("sem", "lossless") if escalate else ("sem",)
    for schema in schemas:
        con.execute(f"CREATE SCHEMA {schema}")
    for rel in meta["relations"]:
        if rel.split(".")[0] in schemas:
            con.execute(f"CREATE VIEW {rel} AS SELECT * FROM read_parquet('{path / 'data' / rel.split('.')[0] / (rel + '.parquet')}')")
    con.execute("SET allowed_directories = ?", [[str(path / "data" / sch) + "/" for sch in schemas]])
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


def load_truth(path: Path) -> dict:
    """The snapshot's answer key: {denial_carcs, customer, tenant: that customer's ledger}."""
    from ccsim import edi_mock
    t = edi_mock.load_truth(path)
    return {"denial_carcs": t["denial_carcs"], "customer": t["customer"], "tenant": t["tenants"][t["customer"]]}
