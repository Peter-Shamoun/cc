"""Benchmark datasets: built once through the full simulated backend, then frozen as a snapshot.

A dataset id hashes everything that determines the data: scenario, generator version, parser, view SQL and
ref seeds. `snapshot()` builds the world (SFTP -> EventBridge -> Fargate parser -> gold) and copies the
reader's view of gold (sem.* plus the PHI-stripped lossless.* tables) to local Parquet, with truth.json.
Agent sessions then open a sandboxed DuckDB on that snapshot: the same views, the same restrictions
(allowed_directories, no external access, locked config), with no HTTP in the timing.
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
DEFAULT = dict(days=45, volume_scale=.6, seed=2026)


def dataset_id(scenario) -> str:
    from ccsim import edi_mock
    h = hashlib.sha256()
    h.update(json.dumps({k: str(v) for k, v in asdict(scenario).items()}, sort_keys=True).encode())
    h.update(edi_mock.GENERATOR_VERSION.encode())
    for f in ("ccsim/edi_mock.py", "ccsim/parser.py", "ccsim/pipeline.py", "claude/schema/01_lossless_edi_schema.sql",
              "claude/schema/02_semantic_layer_views.sql"):
        h.update((ROOT / f).read_bytes())
    return h.hexdigest()[:12]


def snapshot(scenario=None, customer_user="ana@xifin.example", rebuild=False) -> Path:
    from ccsim.edi_mock import Scenario
    from ccsim.pipeline import ESCALATION, SEM_TABLES
    from ccsim.query import login, open_session
    from ccsim.world import build_world
    scenario = scenario or Scenario(**DEFAULT)
    did = dataset_id(scenario)
    out = CACHE / did
    if (out / "meta.json").exists() and not rebuild:
        return out
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    w = build_world(CACHE / f"world-{did}", scenario)
    failed = [t.stopped_reason for t in w.tasks() if t.exit_code]
    if failed:
        raise RuntimeError(f"pipeline failed while building the dataset: {failed[:2]}")
    s = open_session(w.aws, w.dep, login(w.aws, customer_user), escalate=True)
    rels = [f"sem.{t}" for t in SEM_TABLES] + [f"lossless.{t.replace('.', '_')}" for t in ESCALATION]
    import pyarrow.parquet as pq
    for rel in rels:
        pq.write_table(s.con.execute(f"SELECT * FROM {rel}").to_arrow_table(), out / f"{rel}.parquet")
    (out / "truth.json").write_text(json.dumps({"denial_carcs": w.truth["denial_carcs"], "customer": s.customer,
                                                "tenant": w.truth["tenants"][s.customer]}, default=str))
    (out / "meta.json").write_text(json.dumps({"dataset_id": did, "customer": s.customer, "scenario": {
        k: str(v) for k, v in asdict(scenario).items()}, "gold_version": s.version, "relations": rels}, indent=1))
    w.aws.shutdown()
    shutil.rmtree(CACHE / f"world-{did}", ignore_errors=True)
    return out


def open_snapshot(path: Path, escalate: bool = False) -> duckdb.DuckDBPyConnection:
    meta = json.loads((path / "meta.json").read_text())
    con = duckdb.connect(config={"autoinstall_known_extensions": False, "autoload_known_extensions": False})
    for schema in ("sem", "lossless") if escalate else ("sem",):
        con.execute(f"CREATE SCHEMA {schema}")
    for rel in meta["relations"]:
        if rel.startswith("lossless.") and not escalate:
            continue
        con.execute(f"CREATE VIEW {rel} AS SELECT * FROM read_parquet('{path / (rel + '.parquet')}')")
    con.execute("SET allowed_directories = ?", [[str(path) + "/"]])
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


def load_truth(path: Path) -> dict:
    return json.loads((path / "truth.json").read_text())
