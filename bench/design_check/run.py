"""Replay the design-check workload through one lake design and measure every batch and the agent workload.

    python -m bench.design_check.run SYSTEM DATA_DIR OUT_JSON [--days 120] [--checkpoints 20,60,120]
SYSTEM: current | ducklake | iceberg (iceberg needs pyiceberg 0.9.1, e.g. in a venv with system site packages)

Per batch: requests, bytes read / written and client / endpoint CPU, per phase. At each checkpoint: storage
(live and stored-version bytes per layer), then every bench gold_sql query in a fresh reader session (session open
and query measured apart, result fingerprint for cross-design equality); the table formats then compact on demand
and run the workload again. No wall-clock anywhere: the machine is shared.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench.design_check.simenv import Meter, Sim
from bench.lake_bench import workload


def fingerprint(rows) -> str:
    norm = sorted(repr(tuple(round(v, 6) if isinstance(v, float) else v for v in r)) for r in rows)
    return hashlib.sha1("\n".join(norm).encode()).hexdigest()[:12]


def run_workload(sim: Sim, system) -> list[dict]:
    m, out = sim.meter, []
    for q in workload():
        a = m.snap()
        con = system.open_reader(escalate=q["escalate"])
        b = m.snap()
        rows = con.execute(q["sql"]).fetchall()
        c = m.snap()
        con.close()
        out.append(dict(id=q["id"], rows=len(rows), fp=fingerprint(rows), open=Meter.diff(b, a), query=Meter.diff(c, b)))
    return out


class CurrentSystem:
    """(c) the production pipeline, unchanged: IT's SFTP copy -> EventBridge -> parse_batch -> query.open_session."""
    name = "current"

    def __init__(self, sim: Sim, raw_root: Path):
        self.sim, self.raw_root = sim, raw_root

    def batch(self, day_dir, run_id, day, compact) -> dict:
        from ccsim.infra import sftp_drop
        aws = self.sim.aws
        sftp_drop(aws, self.sim.dep, self.sim.customer, self.raw_root / day, day)
        a = self.sim.meter.snap()
        n = len(aws.ecs.tasks)
        aws.events.drain()
        tasks = aws.ecs.tasks[n:]
        if not tasks or any(t.exit_code for t in tasks):
            raise RuntimeError(f"parser task failed on {day}: {[t.result for t in tasks]}")
        r = tasks[-1].result
        from bench.lake_bench import purge_raw_blobs
        purge_raw_blobs(aws, self.sim.customer, day)             # disk only: raw stays listed, as if in S3
        return dict(phases={"task": Meter.diff(self.sim.meter.snap(), a)}, gold_full=r.get("gold_full"),
                    touched_claims=(r.get("gold") or {}).get("touched_claims"))

    def open_reader(self, escalate=False):
        from ccsim.query import login, open_session
        return open_session(self.sim.aws, self.sim.dep, login(self.sim.aws, self.sim.upn), escalate=escalate).con

    def storage(self) -> dict:
        c = self.sim.customer
        return {"silver": self.sim.inventory(f"{c}/parquet/"), "gold": self.sim.inventory(f"{c}/semantic_layer/")}

    def file_counts(self) -> dict:
        return {}


def make(name: str, sim: Sim, data: Path, work: Path):
    if name == "current":
        return CurrentSystem(sim, data / "raw")
    if name == "ducklake":
        from bench.design_check.ducklake_lake import DuckLakeSystem
        return DuckLakeSystem(sim, work)
    if name == "iceberg":
        from bench.design_check.iceberg_lake import IcebergSystem
        return IcebergSystem(sim, work)
    raise ValueError(name)


def main(system_name: str, data: Path, out: Path, n_days: int, checkpoints: set, world: Path):
    days = json.loads((data / "days.json").read_text())[:n_days]
    shutil.rmtree(world, ignore_errors=True)
    sim = Sim(world / "sim")
    system = make(system_name, sim, data, world / "work")
    res = dict(system=system_name, batches=[], checkpoints=[])
    prev_month = None
    for i, d in enumerate(days, 1):
        month_close = prev_month is not None and d["day"][:7] != prev_month
        prev_month = d["day"][:7]
        r = system.batch(data / "parsed" / d["day"], d["run_id"], d["day"], compact=month_close)
        tot = {k: sum(p[k] for p in r["phases"].values()) for k in ("requests", "bytes_read", "bytes_written",
                                                                      "cpu_client_s", "cpu_endpoint_s")}
        res["batches"].append(dict(n=i, day=d["day"], raw_bytes=d["raw_bytes"], parse_cpu_s=d["parse_cpu_s"],
                                   month_close=month_close, **tot, **r))
        print(f"{system_name} batch {i} {d['day']}: {tot['requests']} req, read {tot['bytes_read'] / 1e6:.1f} MB, "
              f"wrote {tot['bytes_written'] / 1e6:.1f} MB, cpu {tot['cpu_client_s']:.1f} s"
              f"{' (month close)' if month_close else ''}", flush=True)
        sim.aws.clock.advance(86400)                # a business day passes: lifecycle expires noncurrent versions
        sim.aws.s3.run_lifecycle()
        if i in checkpoints:
            cp = dict(n=i, storage=system.storage(), files=system.file_counts(), queries=run_workload(sim, system))
            if hasattr(system, "compact_now"):
                cp["compact_now"] = system.compact_now()
                cp["storage_after_compact"] = system.storage()
                cp["files_after_compact"] = system.file_counts()
                cp["queries_after_compact"] = run_workload(sim, system)
            res["checkpoints"].append(cp)
            print(json.dumps({k: v for k, v in cp.items() if not k.startswith("queries")}, default=str)[:1500], flush=True)
        out.write_text(json.dumps(res, indent=0, default=str))
    sim.shutdown()
    shutil.rmtree(world, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("system")
    ap.add_argument("data", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--checkpoints", default="20,60,120")
    ap.add_argument("--world", type=Path, help="scratch directory for the simulated account (deleted at the end)")
    a = ap.parse_args()
    main(a.system, a.data, a.out, a.days, {int(x) for x in a.checkpoints.split(",")},
         a.world or a.out.with_suffix(".world"))
