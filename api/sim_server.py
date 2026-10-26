"""``python -m api.sim_server``: the chat API on the simulated real login and data path (dev and mock runs).

Builds (first start) or reloads (every later start) a ccsim world: Cognito + Entra, STS, IAM and S3 with the
gold lake of every customer in ``ccsim.world.DEFAULT_USERS``, then serves ``create_app(auth=SimAuth(...))`` with
uvicorn. The first build uses the bench's default scenario (``bench.dataset.DEFAULT``), so acme_labs' lake holds
the same data as the bench snapshot and its answer keys apply. The world is saved under ``--world-dir`` after the
build (``bench.lake_bench.save_snapshot``) and restored on the next start, so an API restart keeps the same
customers, users and signing keys: tokens and stored conversations stay valid.

``--tokens-file`` writes a fresh Cognito id token per simulated user (0600), standing in for the sign-in front door
the web app's ``VITE_AUTH=cookie`` build redirects to; the mock-run driver puts one in the ``cc_session`` cookie.
These are simulated users' identity tokens. No AWS credential is written anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path

import uvicorn

WORLD_SNAPSHOT_NAME = "world-snapshot"
WORLD_ROOT_NAME = "root"


def build_or_load_world(world_dir: Path, rebuild: bool = False):
    """(aws, deployment): restored from ``world_dir`` when it holds a saved world, else built and saved."""
    from bench.lake_bench import _pickle_world_types, load_snapshot, save_snapshot
    _pickle_world_types()
    snapshot_dir = world_dir / WORLD_SNAPSHOT_NAME
    aws_root = world_dir / WORLD_ROOT_NAME / "aws"
    if rebuild:
        shutil.rmtree(world_dir, ignore_errors=True)
    if not (snapshot_dir / "world.dill").exists():
        from bench.dataset import DEFAULT
        from ccsim.edi_mock import Scenario
        from ccsim.world import build_world
        started = time.monotonic()
        world = build_world(world_dir / WORLD_ROOT_NAME, Scenario(**DEFAULT))
        failed = [task.stopped_reason for task in world.tasks() if task.exit_code]
        if failed:
            raise RuntimeError(f"pipeline failed while building the world: {failed[:2]}")
        (world_dir / "truth.json").write_text(json.dumps({"customers": sorted(world.truth["tenants"])}))
        save_snapshot(snapshot_dir, world.aws, world.dep, [], [])
        print(f"world built in {time.monotonic() - started:.0f} s", flush=True)
    aws, deployment, _batches, _checkpoints = load_snapshot(snapshot_dir, aws_root)
    aws.serve()
    return aws, deployment


def write_tokens(aws, tokens_path: Path) -> None:
    from ccsim.query import login
    from ccsim.world import DEFAULT_USERS
    tokens = {upn: {"customer": customer, "idToken": login(aws, upn)} for upn, customer in DEFAULT_USERS.items()}
    tokens_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(tokens_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as tokens_file:
        json.dump(tokens, tokens_file)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m api.sim_server")
    parser.add_argument("--world-dir", type=Path, required=True)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--tokens-file", type=Path)
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--host", default=os.environ.get("CC_API_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CC_API_PORT", "8787")))
    args = parser.parse_args(argv)
    aws, deployment = build_or_load_world(args.world_dir, rebuild=args.rebuild)
    if args.tokens_file:
        write_tokens(aws, args.tokens_file)
    if args.build_only:
        aws.shutdown()
        return
    from api.app import Settings, create_app   # api.app's module-level demo app is built (and unused) on import
    from api.backends import SimAuth
    app = create_app(auth=SimAuth(aws, deployment), settings=Settings(auth_mode="sim"))
    uvicorn.run(app, host=args.host, port=args.port, server_header=False, proxy_headers=False, log_level="info",
                access_log=False)


if __name__ == "__main__":
    main()
