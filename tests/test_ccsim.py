"""End-to-end checks on the local backend: gold matches the generator's ledger; tenant, KMS, Object Lock,
credential-expiry and DuckDB sandbox boundaries hold; no PHI reaches the agent's views."""
import importlib.util
import sys
from collections import defaultdict
from pathlib import Path

import duckdb
import pytest

from ccsim.aws import AccessDenied, ExpiredToken
from ccsim.edi_mock import Scenario, LAST
from ccsim.infra import BUCKET
from ccsim.query import open_session
from ccsim.world import build_world

ROOT = Path(__file__).resolve().parent.parent


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("world")
    aws, idp, truth = build_world(root, Scenario(days=25, volume_scale=0.2, p_duplicate_835_file=.3))
    return aws, idp, truth


def _claims(truth, c="acme_labs"):
    return truth["tenants"][c]["claims"]


def test_every_parser_task_succeeded_and_creds_were_revoked(world):
    aws, _, _ = world
    assert aws.tasks and all(t["exit_code"] == 0 for t in aws.tasks), [t.get("error") for t in aws.tasks]
    assert not any(s.session_name.startswith(t["task_arn"].rsplit("/", 1)[-1]) for t in aws.tasks for s in aws.sessions.values())


def test_gold_matches_ledger(world):
    aws, idp, truth = world
    for user, c in (("ana@xifin.example", "acme_labs"), ("dev@xifin.example", "acme_diagnostics")):
        _, con = open_session(aws, idp, idp.login(user))
        cl = _claims(truth, c)
        got = con.execute("SELECT count(*), sum(is_denied::INT), round(sum(paid_amount), 2) FROM sem.remit_claims "
                      "WHERE is_current_adjudication").fetchone()
        want = (sum(v["remitted_current"] for v in cl.values()), sum(v["denied"] for v in cl.values()),
                round(sum(v["paid_current"] for v in cl.values()), 2))
        assert (got[0], got[1], float(got[2])) == want
        n_claims = con.execute("SELECT count(DISTINCT claim_key) FROM sem.claims").fetchone()[0]
        assert n_claims == len(cl)


def test_payer_denial_rates_match_ledger(world):
    aws, idp, truth = world
    _, con = open_session(aws, idp, idp.login("ana@xifin.example"))
    agg = defaultdict(lambda: [0, 0])
    for v in _claims(truth).values():
        if v["remitted_current"]:
            agg[v["payer"].title()][0] += 1
            agg[v["payer"].title()][1] += v["denied"]
    got = dict(con.execute("SELECT payer_name, round(avg(is_denied::INT), 4) FROM sem.remit_claims "
                       "WHERE is_current_adjudication GROUP BY 1").fetchall())
    assert got == {p: round(d / n, 4) for p, (n, d) in agg.items()}


def test_redelivered_835_is_flagged_not_double_counted(world):
    aws, idp, _ = world
    s3 = aws.client("s3", idp.credentials_for(idp.login("ana@xifin.example"), "t")[1])
    silver = s3.local_prefix(BUCKET, "acme_labs/parquet/")
    dups = duckdb.execute(f"SELECT count(*) FROM read_parquet('{silver}/edi.source_file/**/*.parquet', hive_partitioning=false) "
                      "WHERE duplicate_of_file_id IS NOT NULL").fetchone()[0]
    assert dups > 0


# --- identity -------------------------------------------------------------------------------------
def test_entra_and_cognito_gates(world):
    _, idp, _ = world
    idp.entra_users["outsider@other.example"] = {"tenant": "other.example", "groups": ["claimclarity-users"]}
    idp.entra_users["nogroup@xifin.example"] = {"tenant": "xifin.example", "groups": []}
    idp.entra_users["unmapped@xifin.example"] = {"tenant": "xifin.example", "groups": ["claimclarity-users"]}
    for u in ("outsider@other.example", "nogroup@xifin.example", "unmapped@xifin.example", "nobody@xifin.example"):
        with pytest.raises(AccessDenied):
            idp.login(u)
    tok = idp.login("ana@xifin.example")
    forged = tok[:-4] + ("0000" if not tok.endswith("0000") else "1111")
    with pytest.raises(AccessDenied):
        idp.verify(forged)


# --- tenant isolation -----------------------------------------------------------------------------
def test_reader_cannot_touch_other_customer_or_write(world):
    aws, idp, _ = world
    _, creds = idp.credentials_for(idp.login("ana@xifin.example"), "t")
    s3 = aws.client("s3", creds)
    other = s3.list_objects_v2  # noqa
    with pytest.raises(AccessDenied):
        s3.get_object(Bucket=BUCKET, Key="acme_diagnostics/semantic_layer/CURRENT")
    with pytest.raises(AccessDenied):
        s3.local_prefix(BUCKET, "acme_diagnostics/semantic_layer/")
    with pytest.raises(AccessDenied):
        s3.put_object(Bucket=BUCKET, Key="acme_labs/semantic_layer/CURRENT", Body=b"v=evil")
    with pytest.raises(AccessDenied):
        s3.list_objects_v2(Bucket=BUCKET, Prefix="acme_labs/raw/")       # raw is not readable by the app


def test_bucket_and_key_policy_stop_a_broad_role(world):
    aws, _, _ = world
    aws.create_role("data-eng-admin", ["sso"], [{"Statement": [
        {"Effect": "Allow", "Action": ["s3:*", "kms:*"], "Resource": "*"}]}])
    creds = aws.assume_role("sso", "arn:aws:iam::111122223333:role/data-eng-admin", "admin")
    with pytest.raises(AccessDenied):
        aws.client("s3", creds).get_object(Bucket=BUCKET, Key="acme_labs/semantic_layer/CURRENT")
    assert any(r["decision"] == "Deny" and "data-eng-admin" in r["principal"] for r in aws.audit_log())


def test_raw_is_write_once(world):
    aws, _, _ = world
    creds = aws.assume_role("it-sftp-script", "arn:aws:iam::111122223333:role/cc-sftp-copy-acme_labs", "x")
    s3 = aws.client("s3", creds)
    key = next(k["Key"] for k in [{"Key": str(p.relative_to(aws.root / "s3" / BUCKET))}
                                  for p in (aws.root / "s3" / BUCKET / "acme_labs" / "raw").rglob("*.x12")])
    with pytest.raises(AccessDenied):
        s3.put_object(Bucket=BUCKET, Key=key, Body=b"tampered")
    with pytest.raises(AccessDenied):
        s3.delete_object(Bucket=BUCKET, Key=key)


def test_parser_role_cannot_write_raw_or_cross_tenant(world):
    aws, _, _ = world
    creds = aws.assume_role("ecs-tasks.amazonaws.com", "arn:aws:iam::111122223333:role/cc-parser-acme_labs", "p")
    s3 = aws.client("s3", creds)
    with pytest.raises(AccessDenied):
        s3.put_object(Bucket=BUCKET, Key="acme_labs/raw/received_date=2099-01-01/x.x12", Body=b"x")
    with pytest.raises(AccessDenied):
        s3.list_objects_v2(Bucket=BUCKET, Prefix="acme_diagnostics/")
    aws.revoke(creds)
    with pytest.raises(ExpiredToken):
        s3.list_objects_v2(Bucket=BUCKET, Prefix="acme_labs/")


def test_credentials_and_tokens_expire(world):
    aws, idp, _ = world
    clock = Clock()
    real = aws.clock
    aws.clock = clock
    try:
        tok = idp.login("ana@xifin.example")
        _, creds = idp.credentials_for(tok, "t", duration=900)
        s3 = aws.client("s3", creds)
        s3.get_object(Bucket=BUCKET, Key="acme_labs/semantic_layer/CURRENT")
        clock.t += 901
        with pytest.raises(ExpiredToken):
            s3.get_object(Bucket=BUCKET, Key="acme_labs/semantic_layer/CURRENT")
        idp.credentials_for(tok, "t2")                     # token still valid -> fresh creds
        clock.t += 3600
        with pytest.raises(ExpiredToken):
            idp.credentials_for(tok, "t3")                 # token expired -> log in again
    finally:
        aws.clock = real


# --- DuckDB sandbox -------------------------------------------------------------------------------
def test_duckdb_session_is_sandboxed(world):
    aws, idp, _ = world
    _, con = open_session(aws, idp, idp.login("ana@xifin.example"))
    other = aws.root / "s3" / BUCKET / "acme_diagnostics"
    for sql in (f"SELECT * FROM read_parquet('{other}/**/*.parquet')",
                f"SELECT * FROM read_csv('{aws.root}/audit.jsonl')",
                f"COPY (SELECT 1) TO '{aws.root}/leak.csv'",
                "SET enable_external_access = true",
                "SET allowed_directories = ['/']",
                f"ATTACH '{aws.root}/x.db'",
                "INSTALL httpfs"):
        with pytest.raises(duckdb.Error):
            con.execute(sql)
    assert con.execute("SELECT count(*) FROM sem.claims").fetchone()[0] > 0


def test_agent_sql_guard_blocks_file_functions_and_non_sem():
    spec = importlib.util.spec_from_file_location("agent_interface", ROOT / "claude/schema/04_agent_interface.py")
    ai = importlib.util.module_from_spec(spec)
    sys.modules["agent_interface"] = ai
    spec.loader.exec_module(ai)
    for sql in ("SELECT * FROM read_parquet('/etc/passwd')", "SELECT * FROM edi.entity", "DROP VIEW sem.claims",
                "SELECT * FROM sem.claims; DELETE FROM sem.claims"):
        with pytest.raises(Exception):
            ai.guard_semantic_sql(sql)


# --- PHI ------------------------------------------------------------------------------------------
def test_no_patient_identifiers_in_agent_views(world):
    aws, idp, _ = world
    for escalate in (False, True):
        _, con = open_session(aws, idp, idp.login("ana@xifin.example"), escalate=escalate)
        rels = con.execute("SELECT schema_name || '.' || view_name FROM duckdb_views() WHERE NOT internal").fetchall()
        names = set(LAST)
        for (rel,) in rels:
            cols = con.execute(f"DESCRIBE {rel}").fetchall()
            colnames = {c[0] for c in cols}
            assert not colnames & {"name_first", "dmg_birth_date", "bpr09_sender_account", "raw_text", "elements"}, rel
            for c in (c[0] for c in cols if c[1] == "VARCHAR"):
                hit = con.execute(f'SELECT count(*) FROM {rel} WHERE "{c}" IN (SELECT unnest(?))', [list(names)]).fetchone()[0]
                assert hit == 0, f"patient surname in {rel}.{c}"
                mbr = con.execute(f'SELECT count(*) FROM {rel} WHERE "{c}" LIKE \'MBR%\'').fetchone()[0]
                assert mbr == 0, f"member id in {rel}.{c}"
