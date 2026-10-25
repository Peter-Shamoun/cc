"""The lake layout (ccsim/lake.py) and incremental gold (ccsim/pipeline.py): incremental gold must equal a full
rebuild from silver, across month closes, duplicate and corrupt deliveries, and a batch that crashed after its
silver commit."""
from datetime import date

import duckdb
import pytest

from ccsim import pipeline
from ccsim.aws.ecs import TaskEnv
from ccsim.aws.sts import service_principal
from ccsim.edi_mock import Scenario
from ccsim.infra import BUCKET, names, role_arn
from ccsim.lake import Store, acquire_lock, read_current, read_manifest, release_lock, silver_state
from ccsim.query import login, open_session
from ccsim.world import build_world

C = "acme_labs"
SCENARIO = Scenario(start=date(2026, 7, 13), days=36, volume_scale=.15, p_duplicate_835_file=.2, p_corrupt_file=.3,
                    p_secondary_coverage=.3, p_reversal_correction=.08, p_void=.03)
CRASH_DAY = "2026-07-22"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    open_months = pipeline.OPEN_MONTHS
    pipeline.OPEN_MONTHS = 1                       # so July folds into the 2026 year file when August opens
    w = build_world(tmp_path_factory.mktemp("lake"), SCENARIO, customers=(C,), users={"ana@xifin.example": C},
                    deliver=False)
    real_build_gold = pipeline.build_gold
    for day in w.truth["tenants"][C]["file_days"]:
        if day == CRASH_DAY:                       # this batch commits silver, then dies before publishing gold
            def crash(*a, **kw):
                raise RuntimeError("task killed after the silver commit")
            pipeline.build_gold = crash
        try:
            w.deliver(C, day)
        finally:
            pipeline.build_gold = real_build_gold
    yield w
    w.aws.shutdown()
    pipeline.OPEN_MONTHS = open_months


def parser_env(w):
    creds = w.aws.sts.assume_role(service_principal("ecs-tasks.amazonaws.com"), None, role_arn(names(C)["parser"]), "lake-test",
                                  SourceContext={"aws:SourceAccount": "111122223333",
                                                 "aws:SourceArn": "arn:aws:ecs:us-west-2:111122223333:task/claimclarity/t"})
    return TaskEnv({}, creds, "test-task", w.aws)


def store(w):
    return Store(pipeline._clients(parser_env(w)), BUCKET, C, w.dep.keys[C].arn)


def snapshot(w, out: duckdb.DuckDBPyConnection, schema: str):
    """Every relation the reader sees (sem.* and lossless.*), copied into schema `schema` of a local DuckDB."""
    s = open_session(w.aws, w.dep, login(w.aws, "ana@xifin.example"), escalate=True)
    out.execute(f"CREATE SCHEMA {schema}")
    for rel in pipeline.RELATIONS:
        rows = s.con.sql(f"SELECT * FROM {rel}").to_arrow_table()
        out.execute(f"CREATE TABLE {schema}.{rel.replace('.', '__')} AS SELECT * FROM rows")


def test_batches_ran_incrementally_and_the_crashed_one_was_caught_up(world):
    tasks = world.tasks()
    failed = [t for t in tasks if t.exit_code]
    assert len(failed) == 1 and CRASH_DAY in failed[0].environment["SUCCESS_KEY"]
    ok = [t.result for t in tasks if not t.exit_code]
    assert ok[0]["gold_full"] is True and all(r["gold_full"] is False for r in ok[1:])
    assert all(r["published"] for r in ok)
    m = read_manifest(store(world), read_current(store(world))[0])
    assert m["silver_through"] == max(silver_state(store(world)).committed_runs)


def test_closed_months_are_compacted_and_settled_months_fold_into_years(world):
    s = silver_state(store(world))
    assert {m for by_month in s.live.values() for m in by_month} == {"2026-07", "2026-08"}
    for t, by_month in s.live.items():
        assert len(by_month.get("2026-07", [])) <= 1 and all("/compacted-" in k for k in by_month.get("2026-07", [])), t
    m = read_manifest(store(world), read_current(store(world))[0])
    for rel in pipeline.CLAIM_RELATIONS:
        assert set(m["relations"][rel]) == {"cohort=2026", "cohort=2026-08"}, rel
    for rel in pipeline.APPEND_RELATIONS:
        parts = m["relations"][rel]
        assert len(parts.get("received=2026", [])) <= 1 and set(parts) <= {"received=2026", "received=2026-08", "empty"}, rel
    assert len(m["relations"]["lossless.c837_claim"]["received=2026"]) == 1


def test_incremental_gold_equals_full_rebuild(world):
    db = duckdb.connect()
    snapshot(world, db, "incremental")
    env = parser_env(world)
    run_id = "run_29990101T000000000000_29990101"
    assert pipeline.build_gold(env, pipeline._clients(env), pipeline._duck(env, f"s3://{BUCKET}/{C}/"), BUCKET, C,
                               world.dep.keys[C].arn, run_id, full=True)
    snapshot(world, db, "rebuilt")
    for rel in pipeline.RELATIONS:
        t = rel.replace(".", "__")
        diff = lambda a, b: db.execute(f"SELECT count(*) FROM (SELECT * FROM {a}.{t} EXCEPT ALL SELECT * FROM {b}.{t})").fetchone()[0]
        n = db.execute(f"SELECT count(*) FROM rebuilt.{t}").fetchone()[0]
        assert (diff("rebuilt", "incremental"), diff("incremental", "rebuilt")) == (0, 0), f"{rel} ({n} rows) differs"
    assert db.execute("SELECT count(*) FROM rebuilt.sem__remit_claims").fetchone()[0] > 100


def test_uncommitted_silver_parts_are_garbage(world):
    st = store(world)
    junk = f"{C}/parquet/c837.claim/received_month=2026-08/part-run_20260815T000000000000_20260815.parquet"
    st.put(junk, b"not even parquet")
    s = silver_state(st)
    assert junk in s.garbage and junk not in s.files("c837.claim")
    st.delete([junk])


def test_batch_lock_is_exclusive_and_expires(world):
    st = store(world)
    clock = world.aws.clock
    key = acquire_lock(st, "first", clock)
    with pytest.raises(TimeoutError):
        acquire_lock(st, "second", clock, wait_seconds=0)
    clock.advance(3 * 3600)                                       # the holder crashed: its lease runs out
    st = store(world)                                             # (and so did its credentials)
    assert acquire_lock(st, "second", clock, wait_seconds=0) == key
    release_lock(st, key)
