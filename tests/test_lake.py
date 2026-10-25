"""The lake layout (ccsim/lake.py) and incremental gold (ccsim/pipeline.py).

Incremental gold must equal a full rebuild from silver, through month closes, quarter folds, duplicate and corrupt
deliveries, a batch that crashed after its silver commit on the last day of a month, a late batch into an already
compacted month, and a rules change. Every published version must list only files that exist and are not retired,
readers on an older version must keep working until retention expires, and the batch lock must fence a holder
that overran its lease."""
import shutil
from datetime import UTC, date, datetime

import duckdb
import pytest

from ccsim import pipeline
from ccsim.aws.ecs import TaskEnv
from ccsim.aws.sts import service_principal
from ccsim.edi_mock import Scenario
from ccsim.infra import BUCKET, names, role_arn
from ccsim.lake import BatchLock, LockLost, Store, check_manifest, read_current, read_manifest, silver_state
from ccsim.query import login, open_session
from ccsim.world import build_world

C = "acme_labs"
UPN = "ana@xifin.example"
DEFAULT_OPEN_MONTHS = pipeline.OPEN_MONTHS
SCENARIO = Scenario(start=date(2026, 7, 13), days=36, volume_scale=.15, p_duplicate_835_file=.2, p_corrupt_file=.3,
                    p_secondary_coverage=.3, p_reversal_correction=.08, p_void=.03)
# default OPEN_MONTHS=3 with two settled quarters: March folds into Q1 and April into Q2 by July
QUARTERS_SCENARIO = Scenario(start=date(2026, 3, 2), days=127, volume_scale=.04)


def parser_env(w):
    creds = w.aws.sts.assume_role(service_principal("ecs-tasks.amazonaws.com"), None, role_arn(names(C)["parser"]), "lake-test",
                                  SourceContext={"aws:SourceAccount": "111122223333",
                                                 "aws:SourceArn": "arn:aws:ecs:us-west-2:111122223333:task/claimclarity/t"})
    return TaskEnv({}, creds, "test-task", w.aws)


def store(w):
    return Store(pipeline._clients(parser_env(w)), BUCKET, C, w.dep.keys[C].arn)


def current_manifest(w):
    st = store(w)
    return read_manifest(st, read_current(st)[0])


def deliver_all(w, crash_day=None) -> list[dict]:
    """Deliver every file day; the crash_day batch commits silver and then dies before gold. Checks every version
    as it is published and returns all of them."""
    versions = []
    real_build_gold = pipeline.build_gold
    for day in w.truth["tenants"][C]["file_days"]:
        if day == crash_day:
            def crash(*a, **kw):
                raise RuntimeError("task killed after the silver commit")
            pipeline.build_gold = crash
        try:
            w.deliver(C, day)
        finally:
            pipeline.build_gold = real_build_gold
        m = current_manifest(w)
        if not versions or versions[-1]["version"] != m["version"]:
            check_manifest(store(w), m)
            versions.append(m)
    return versions


def snapshot(w, out: duckdb.DuckDBPyConnection, schema: str):
    """Every relation the reader sees (sem.* and lossless.*), copied into schema `schema` of a local DuckDB."""
    s = open_session(w.aws, w.dep, login(w.aws, UPN), escalate=True)
    out.execute(f"CREATE SCHEMA {schema}")
    for rel in pipeline.RELATIONS:
        rows = s.con.sql(f"SELECT * FROM {rel}").to_arrow_table()
        out.execute(f"CREATE TABLE {schema}.{rel.replace('.', '__')} AS SELECT * FROM rows")


def next_run_id(w) -> str:
    """A run id after every published version (run ids are task start times on the account clock)."""
    return f"run_{datetime.fromtimestamp(w.aws.clock(), UTC):%Y%m%dT%H%M%S%f}_20260831"


def full_rebuild(w, run_id: str, **kw) -> dict:
    env = parser_env(w)
    report = {}
    assert pipeline.build_gold(env, pipeline._clients(env), pipeline._duck(env, f"s3://{BUCKET}/{C}/"), BUCKET, C,
                               w.dep.keys[C].arn, run_id, report=report, **kw)
    return report


def assert_same_gold(db, a: str, b: str):
    for rel in pipeline.RELATIONS:
        t = rel.replace(".", "__")
        diff = lambda x, y: db.execute(f"SELECT count(*) FROM (SELECT * FROM {x}.{t} EXCEPT ALL SELECT * FROM {y}.{t})").fetchone()[0]
        n = db.execute(f"SELECT count(*) FROM {a}.{t}").fetchone()[0]
        assert (diff(a, b), diff(b, a)) == (0, 0), f"{rel} ({n} rows) differs"
    assert db.execute(f"SELECT count(*) FROM {a}.sem__remit_claims").fetchone()[0] > 100


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    open_months = pipeline.OPEN_MONTHS
    pipeline.OPEN_MONTHS = 1                       # so July folds into the 2026-Q3 file when August opens
    w = build_world(tmp_path_factory.mktemp("lake"), SCENARIO, customers=(C,), users={UPN: C}, deliver=False)
    days = w.truth["tenants"][C]["file_days"]
    w.crash_day = max(d for d in days if d.startswith("2026-07"))   # crash on the month's last batch
    w.versions = deliver_all(w, w.crash_day)
    w.incremental = duckdb.connect()
    snapshot(w, w.incremental, "incremental")
    w.old_reader = open_session(w.aws, w.dep, login(w.aws, UPN))   # holds this version through later publishes
    w.old_reader_count = w.old_reader.con.execute("SELECT count(*) FROM sem.claim_lines").fetchone()[0]
    yield w
    w.aws.shutdown()
    pipeline.OPEN_MONTHS = open_months


def test_batches_ran_incrementally_and_the_crash_at_month_close_was_caught_up(world):
    tasks = world.tasks()
    failed = [t for t in tasks if t.exit_code]
    assert len(failed) == 1 and world.crash_day in failed[0].environment["SUCCESS_KEY"]
    ok = [t.result for t in tasks if not t.exit_code]
    caught_up = next(i for i, t in enumerate(tasks) if t.exit_code) + 1   # the next batch: pending spans July and August
    full = [i for i, t in enumerate(tasks) if not t.exit_code and t.result["gold_full"]]
    assert full == [0, caught_up], "only the first build and the month-spanning catch-up are full rebuilds"
    assert all(r["published"] for r in ok)
    assert current_manifest(world)["silver_through"] == max(silver_state(store(world)).committed_runs)


def test_every_published_version_lists_only_existing_unretired_files(world):
    assert len(world.versions) > 10
    for m in world.versions:                      # still true now: retired files outlive reader sessions
        check_manifest(store(world), m)


def test_closed_months_are_compacted_and_settled_months_fold_into_quarters(world):
    s = silver_state(store(world))
    assert {m for by_month in s.live.values() for m in by_month} == {"2026-07", "2026-08"}
    for t, by_month in s.live.items():
        assert len(by_month.get("2026-07", [])) <= 1 and all("/compacted-" in k for k in by_month.get("2026-07", [])), t
    m = current_manifest(world)
    for rel in pipeline.CLAIM_RELATIONS:
        assert set(m["relations"][rel]) == {"schema", "cohort=2026-Q3", "cohort=2026-08"}, rel
    for rel in pipeline.APPEND_RELATIONS:
        parts = m["relations"][rel]
        assert len(parts.get("received=2026-Q3", [])) <= 1 and set(parts) <= {"schema", "received=2026-Q3", "received=2026-08"}, rel
    assert len(m["relations"]["lossless.c837_claim"]["received=2026-Q3"]) == 1


def test_incremental_gold_equals_a_rules_change_rebuild(world, monkeypatch):
    monkeypatch.setattr(pipeline, "GOLD_LAYOUT", pipeline.GOLD_LAYOUT + "-changed")   # e.g. a view SQL change
    report = full_rebuild(world, next_run_id(world), full=None)
    assert report["full"] is True
    snapshot(world, world.incremental, "rebuilt")
    assert_same_gold(world.incremental, "rebuilt", "incremental")


def test_a_reader_on_an_older_version_keeps_working_until_retention_expires(world):
    for _ in range(2):
        full_rebuild(world, next_run_id(world), full=True)     # two quick publishes after the reader opened its version
    assert world.old_reader.con.execute("SELECT count(*) FROM sem.claim_lines").fetchone()[0] == world.old_reader_count
    old = world.versions[-1]
    world.aws.clock.advance(pipeline.READER_RETENTION_SECONDS + 60)
    full_rebuild(world, next_run_id(world), full=True)
    full_rebuild(world, next_run_id(world), full=True)
    keys = {k for k, _ in store(world).list(f"{C}/semantic_layer/")}
    live = pipeline.manifest_files(current_manifest(world))
    assert not (pipeline.manifest_files(old) - live) & keys, "files only the old version used are gone after retention"
    check_manifest(store(world), current_manifest(world))


def test_a_late_batch_into_a_compacted_month_triggers_a_full_rebuild(world):
    raw = world.sftp_root / C / "raw"
    late = raw / "received_date=2026-07-18"                                   # a Saturday: never delivered
    src = next(p for p in sorted(raw.glob("received_date=2026-07-*/835/*.x12")))
    (late / "835").mkdir(parents=True)
    shutil.copy(src, late / "835" / f"late-{src.name}")
    n_files = open_session(world.aws, world.dep, login(world.aws, UPN), escalate=True).con.execute(
        "SELECT count(*) FROM lossless.edi_source_file").fetchone()[0]
    world.deliver(C, "2026-07-18")
    t = world.tasks()[-1]
    assert t.exit_code == 0 and t.result["gold_full"] is True, t.result
    s = open_session(world.aws, world.dep, login(world.aws, UPN), escalate=True)
    assert s.con.execute("SELECT count(*) FROM lossless.edi_source_file").fetchone()[0] == n_files + 1
    july = silver_state(store(world)).live["edi.source_file"]["2026-07"]       # recompacted, through the late run
    assert len(july) == 1 and july[0].endswith(f"compacted-{t.result['run_id']}.parquet")
    check_manifest(store(world), current_manifest(world))


def test_states_a_crash_can_leave_in_silver_are_garbage(world):
    st = store(world)
    s = silver_state(st)
    compacted = s.live["c837.claim"]["2026-07"][0]
    run = compacted.rsplit("compacted-", 1)[1].removesuffix(".parquet")
    month_dir = compacted.rsplit("/", 1)[0]
    body = st.s3.get_object(Bucket=BUCKET, Key=compacted)["Body"].read()
    newer = f"{month_dir}/compacted-{run[:-8]}99999999.parquet"     # recompaction crashed before deleting the old one
    covered = f"{month_dir}/part-{run}.parquet"                     # compaction crashed before deleting its parts
    uncommitted = f"{C}/parquet/c837.claim/received_month=2026-08/part-run_20260815T000000000000_20260815.parquet"
    for key in (newer, covered, uncommitted):
        st.put(key, body)
    s = silver_state(st)
    assert {compacted, covered, uncommitted} <= set(s.garbage)
    assert s.live["c837.claim"]["2026-07"] == [newer] and uncommitted not in s.files("c837.claim")
    st.delete([newer, covered, uncommitted])


def test_a_session_that_is_not_escalated_cannot_open_lossless_files(world):
    m = current_manifest(world)
    lossless = f"s3://{BUCKET}/{m['relations']['lossless.c837_claim']['schema'][0]}"
    plain = open_session(world.aws, world.dep, login(world.aws, UPN))
    with pytest.raises(duckdb.Error):                             # SQL the guard would have refused, run directly
        plain.con.execute(f"SELECT count(*) FROM read_parquet('{lossless}')").fetchall()
    with pytest.raises(duckdb.Error):
        plain.con.execute(f"SELECT count(*) FROM read_parquet('{lossless}')", []).fetchall()
    escalated = open_session(world.aws, world.dep, login(world.aws, UPN), escalate=True)
    assert escalated.con.execute(f"SELECT count(*) FROM read_parquet('{lossless}')").fetchone()[0] == 0


def test_batch_lock_is_exclusive_expires_and_fences_the_overrunning_holder(world):
    st = store(world)
    clock = world.aws.clock
    first = BatchLock(st, "first", clock).acquire()
    with pytest.raises(TimeoutError):
        BatchLock(st, "second", clock).acquire(wait_seconds=0)
    clock.advance(3 * 3600)                                       # first overran its lease (or crashed)
    st = store(world)                                             # (and its credentials expired)
    first.store = st
    second = BatchLock(st, "second", clock).acquire(wait_seconds=0)
    with pytest.raises(LockLost):                                 # first must not commit or publish any more
        first.renew()
    first.release()                                               # ...nor release second's lock
    second.renew()
    with pytest.raises(TimeoutError):
        BatchLock(st, "third", clock).acquire(wait_seconds=0)
    second.release()
    BatchLock(st, "third", clock).acquire(wait_seconds=0).release()


def test_a_task_that_lost_the_lock_does_not_commit_silver(world, tmp_path):
    st = store(world)
    lock = BatchLock(st, "stale", world.aws.clock).acquire()
    st.put(lock.key, b'{"holder": "thief", "expires": 9e12, "nonce": "x"}')
    con = duckdb.connect()
    con.execute(pipeline.DDL_LOSSLESS)
    con.execute("INSERT INTO edi.ingest_run (ingest_run_id, started_at, status, parser_name, parser_version, schema_version) "
                "VALUES ('run_x', now(), 'SUCCEEDED', 'p', 'v', '1')")
    with pytest.raises(LockLost):
        pipeline.write_silver(con, st, "run_20260901T000000000000_20260901", "2026-09", lock)
    assert not silver_state(st).files(pipeline.COMMIT_TABLE, {"2026-09"})
    st.put(lock.key, b'{"holder": "nobody", "expires": 0, "nonce": "y"}')     # leave the lock free
    st.delete([k for k, _ in st.list(f"{C}/parquet/") if "received_month=2026-09" in k])


@pytest.fixture(scope="module")
def quarters_world(tmp_path_factory):
    open_months = pipeline.OPEN_MONTHS            # (the `world` fixture may still be alive with OPEN_MONTHS=1)
    pipeline.OPEN_MONTHS = DEFAULT_OPEN_MONTHS
    w = build_world(tmp_path_factory.mktemp("lake-quarters"), QUARTERS_SCENARIO, customers=(C,), users={UPN: C},
                    deliver=False)
    w.versions = deliver_all(w)
    yield w
    w.aws.shutdown()
    pipeline.OPEN_MONTHS = open_months


def test_default_tiering_keeps_three_open_months_and_folds_older_ones_into_quarters(quarters_world):
    w = quarters_world
    assert all(t.exit_code == 0 for t in w.tasks())
    m = current_manifest(w)
    allowed = {"schema", "cohort=2026-Q1", "cohort=2026-Q2", "cohort=2026-05", "cohort=2026-06", "cohort=2026-07"}
    for rel in pipeline.CLAIM_RELATIONS:                # (July claims have no remits yet, so remit_* may lack July)
        parts = set(m["relations"][rel])
        assert {"cohort=2026-Q1", "cohort=2026-Q2"} <= parts <= allowed, (rel, sorted(parts))
        assert all(len(keys) == 1 for keys in m["relations"][rel].values()), rel
    assert set(m["relations"]["sem.claims"]) == allowed
    db = duckdb.connect()
    snapshot(w, db, "incremental")
    full_rebuild(w, next_run_id(w), full=True)
    snapshot(w, db, "rebuilt")
    assert_same_gold(db, "rebuilt", "incremental")
