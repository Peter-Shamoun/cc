"""The whole backend, end to end: mock EDI -> SFTP copy -> EventBridge -> Fargate parser -> silver -> gold ->
per-request DuckDB. Gold is checked against the generator's ledger; every security layer is checked on its own."""
import re
from collections import Counter, defaultdict

import duckdb
import pytest
from botocore.exceptions import ClientError

from ccsim import edi_mock
from ccsim.aws.errors import AccessDenied, AuthError
from ccsim.edi_mock import Scenario
from ccsim.infra import BUCKET, export, names, role_arn
from ccsim.query import login, open_session
from ccsim.world import add_user, build_world


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    w = build_world(tmp_path_factory.mktemp("world"),
                    Scenario(days=15, volume_scale=.25, p_corrupt_file=.3, p_duplicate_835_file=.2, p_secondary_coverage=.3))
    yield w
    w.aws.shutdown()


def adjudications(w, c="acme_labs"):
    return [(pcn, a) for pcn, v in w.truth["tenants"][c]["claims"].items() for a in v["adjudications"].values()]


def session(w, upn="ana@xifin.example", **kw):
    return open_session(w.aws, w.dep, login(w.aws, upn), **kw)


def q(s, sql, *p):
    return s.con.execute(sql, list(p)).fetchall()


# --- pipeline ------------------------------------------------------------------------------------
def test_every_task_succeeded(world):
    bad = [(t.environment.get("SUCCESS_KEY"), t.stopped_reason) for t in world.tasks() if t.exit_code]
    assert world.tasks() and not bad


def test_files_parsed_flagged_or_quarantined_as_expected(world):
    for c in ("acme_labs", "acme_diagnostics"):
        files = world.truth["tenants"][c]["files"]
        s = session(world, "ana@xifin.example" if c == "acme_labs" else "dev@xifin.example", escalate=True)
        got = Counter(r[0] for r in q(s, "SELECT CASE WHEN parse_status = 'REJECTED' THEN 'rejected' "
                                         "WHEN duplicate_of_file_id IS NOT NULL THEN 'duplicate' ELSE 'parsed' END "
                                         "FROM lossless.edi_source_file"))
        assert got == Counter(f["expect"] for f in files)
        parser = world.aws.sts.assume_role(world.aws.sts.sessions and __import__("ccsim.aws.sts", fromlist=["x"]).service_principal(
            "ecs-tasks.amazonaws.com"), None, role_arn(names(c)["parser"]), "inspect",
            SourceContext={"aws:SourceAccount": "111122223333", "aws:SourceArn": "arn:aws:ecs:us-west-2:111122223333:task/cc/x"})
        quarantined = world.aws.client("s3", parser).list_objects_v2(Bucket=BUCKET, Prefix=f"{c}/quarantine/")["Contents"]
        assert len(quarantined) == got["rejected"]


@pytest.mark.parametrize("c,upn", [("acme_labs", "ana@xifin.example"), ("acme_diagnostics", "dev@xifin.example")])
def test_gold_totals_match_ledger(world, c, upn):
    s = session(world, upn)
    ad = adjudications(world, c)
    got = q(s, "SELECT count(*), count(*) FILTER (WHERE is_denied), round(sum(paid_amount), 2), "
               "count(*) FILTER (WHERE is_denied_recorded), count(*) FILTER (WHERE payer_sequence = 'secondary') "
               "FROM sem.remit_claims WHERE is_current_adjudication")[0]
    want = (sum(a["remitted_current"] for _, a in ad), sum(a["denied"] for _, a in ad),
            round(sum(a["paid_current"] for _, a in ad), 2), sum(a["denied_recorded"] for _, a in ad),
            sum(a["remitted_current"] and a["seq"] == 1 for _, a in ad))
    assert (got[0], got[1], float(got[2] or 0), got[3], got[4]) == want


def test_denial_rate_by_payer_matches_ledger(world):
    s = session(world)
    agg = defaultdict(lambda: [0, 0])
    for _, a in adjudications(world):
        if a["remitted_current"]:
            agg[a["payer"].title()][0] += 1
            agg[a["payer"].title()][1] += a["denied"]
    got = dict(q(s, "SELECT payer_name, round(avg(is_denied::INT), 6) FROM sem.remit_claims WHERE is_current_adjudication "
                    "GROUP BY 1"))
    assert got == {p: round(d / n, 6) for p, (n, d) in agg.items()}


def test_claim_level_submission_facts_match_ledger(world):
    s = session(world)
    claims = world.truth["tenants"]["acme_labs"]["claims"]
    n, resub, never = q(s, "SELECT count(DISTINCT claim_key), count(DISTINCT claim_key) FILTER (WHERE frequency_code = '7'), "
                           "count(DISTINCT claim_key) FILTER (WHERE NOT has_remit AND is_latest_submission) FROM sem.claims")[0]
    assert n == len(claims)
    assert resub == sum(v["resubmitted"] for v in claims.values())
    assert never == sum(not v["adjudications"] for v in claims.values())


def test_provider_adjustments_match_ledger(world):
    s = session(world)
    got = dict(q(s, "SELECT plb_reason_code, round(sum(provider_adjustment_amount), 2) FROM sem.provider_adjustments GROUP BY 1"))
    want = defaultdict(float)
    for p in world.truth["tenants"]["acme_labs"]["plb"]:
        want[p["code"]] += p["amount"]
    assert {k: float(v) for k, v in got.items()} == {k: round(v, 2) for k, v in want.items()}


def test_every_835_balances(world):
    s = session(world)
    assert q(s, "SELECT count(*) FILTER (WHERE NOT is_balanced) FROM sem.payments")[0][0] == 0


def test_line_denials_match_ledger(world):
    s = session(world)
    want = Counter()
    for _, a in adjudications(world):
        if a["remitted_current"]:
            cur = ([e for e in a["events"] if not e["duplicate_denial"]] or a["events"])[-1]   # business rule
            for ln in cur["lines"]:
                want[(ln["adjudicated_cpt"], ln["denied"])] += 1
    got = Counter({(cpt, bool(d)): n for cpt, d, n in q(
        s, "SELECT procedure_code_adjudicated, line_is_denied, count(*) FROM (SELECT coalesce(adjudicated_procedure_code, "
           "procedure_code) AS procedure_code_adjudicated, line_is_denied FROM sem.remit_lines WHERE is_current_adjudication) "
           "GROUP BY ALL")})
    assert got == want


def test_redelivered_success_event_is_a_noop(world):
    aws, c = world.aws, "acme_labs"
    day = world.truth["tenants"][c]["file_days"][3]
    before = session(world).version
    n_tasks = len(world.tasks())
    aws.events.put_event("aws.s3", "Object Created", {"bucket": {"name": BUCKET},
                         "object": {"key": f"{c}/raw/received_date={day}/_SUCCESS"}})
    aws.events.drain()
    t = world.tasks()[-1]
    assert len(world.tasks()) == n_tasks + 1 and t.exit_code == 0 and t.result["status"] == "noop"
    assert session(world).version == before


def test_current_never_moves_backwards(world):
    from ccsim.pipeline import build_gold
    from ccsim.aws.ecs import TaskEnv
    from ccsim.aws.sts import service_principal
    c = "acme_labs"
    creds = world.aws.sts.assume_role(service_principal("ecs-tasks.amazonaws.com"), None, role_arn(names(c)["parser"]),
                                      "stale", SourceContext={"aws:SourceAccount": "111122223333",
                                                              "aws:SourceArn": "arn:aws:ecs:us-west-2:111122223333:task/cc/x"})
    env = TaskEnv({}, creds, "stale-task", world.aws)
    from ccsim.pipeline import _clients, _duck
    before = session(world).version
    published = build_gold(env, _clients(env), _duck(env, f"s3://{BUCKET}/{c}/"), BUCKET, c,
                           world.dep.keys[c].arn, "run_19990101T000000000000_19990101")
    assert published is False and session(world).version == before


# --- identity ------------------------------------------------------------------------------------
def test_login_gates(world):
    aws = world.aws
    aws.entra.add_user("nogroup@xifin.example", [])
    with pytest.raises(AuthError):
        login(aws, "nogroup@xifin.example")
    add_user(aws, "unmapped@xifin.example", None)
    with pytest.raises(AuthError):
        login(aws, "unmapped@xifin.example")
    assert session(world).customer == "acme_labs"
    assert session(world, "dev@xifin.example").customer == "acme_diagnostics"


def test_token_for_one_customer_cannot_assume_another_customers_role(world):
    tok = login(world.aws, "ana@xifin.example")
    with pytest.raises(AccessDenied):
        world.aws.sts.assume_role_with_web_identity(world.dep.reader_role("acme_diagnostics"), "steal", tok, 900)


# --- tenant isolation, one layer at a time ----------------------------------------------------------
OTHER_GOLD = f"s3://{BUCKET}/acme_diagnostics/semantic_layer/"


def _other_version(world):
    return session(world, "dev@xifin.example").version


def test_layer_iam_and_bucket_policy_alone(world):
    """Secret unscoped and allowed_directories off: only IAM + bucket policy + KMS stand in the way."""
    s = session(world, enforce_allowed_directories=False, scope_secret=False)
    with pytest.raises(duckdb.Error, match="403"):
        s.con.execute(f"SELECT count(*) FROM read_parquet('{OTHER_GOLD}{_other_version(world)}/sem.claims/*.parquet')").fetchall()


def test_layer_allowed_directories_alone(world):
    s = session(world)
    with pytest.raises(duckdb.Error, match="Permission Error"):
        s.con.execute(f"SELECT count(*) FROM read_parquet('{OTHER_GOLD}{_other_version(world)}/sem.claims/*.parquet')").fetchall()
    for sql in ("SELECT * FROM read_csv('/etc/passwd')", "INSTALL spatial", "SET enable_external_access = true",
                "SET allowed_directories = ['s3://']", f"ATTACH 's3://{BUCKET}/x.db'"):
        with pytest.raises(duckdb.Error):
            s.con.execute(sql)


def test_reader_is_read_only_and_gold_only(world):
    s = session(world)
    c = world.aws.boto3_client("s3", s.credentials)
    for kw in (dict(Key="acme_labs/semantic_layer/CURRENT"), dict(Key="acme_labs/parquet/x")):
        with pytest.raises(ClientError) as e:
            c.put_object(Bucket=BUCKET, Body=b"x", ServerSideEncryption="aws:kms", SSEKMSKeyId=world.dep.keys["acme_labs"].arn, **kw)
        assert e.value.response["Error"]["Code"] == "AccessDenied"
    for prefix in ("acme_labs/parquet/", "acme_labs/raw/", "acme_diagnostics/", ""):
        with pytest.raises(ClientError):
            c.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    with pytest.raises(duckdb.Error):
        s.con.execute(f"COPY (SELECT 1) TO '{s.gold_uri}evil.csv'")          # inside allowed dir, but IAM says no


def test_admin_with_star_permissions_still_cannot_read_phi(world):
    aws = world.aws
    from ccsim.aws.sts import service_principal
    aws.iam.create_role("data-eng-admin", {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]})
    aws.iam.put_role_policy("data-eng-admin", "all", {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "*", "Resource": "*"}]})
    admin = aws.sts.assume_role(service_principal("ec2.amazonaws.com"), None, role_arn("data-eng-admin"), "admin")
    with pytest.raises(AccessDenied):
        aws.client("s3", admin).get_object(Bucket=BUCKET, Key="acme_labs/semantic_layer/CURRENT")
    # ...and cannot remove the guards: bucket policy, key policy, grants
    from ccsim.infra import bucket_policy
    with pytest.raises(AccessDenied):
        aws.client("s3", admin).delete_bucket_policy(Bucket=BUCKET)
    with pytest.raises(AccessDenied):
        aws.client("s3", admin).put_bucket_policy(Bucket=BUCKET, Policy={"Version": "2012-10-17", "Statement": []})
    key = world.dep.keys["acme_labs"].arn
    with pytest.raises(AccessDenied):
        aws.kms.put_key_policy_as(admin.principal(), admin, aws.iam, key, {"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Principal": {"AWS": role_arn("data-eng-admin")}, "Action": "kms:*", "Resource": "*"}]})
    with pytest.raises(AccessDenied):
        aws.kms.create_grant_as(admin.principal(), admin, aws.iam, key, role_arn("data-eng-admin"), ["Decrypt"])
    assert aws.s3.bucket(BUCKET).policy == bucket_policy()
    denied = [e for e in aws.trail.denied() if "data-eng-admin" in e["userIdentity"].get("arn", "")]
    assert denied and denied[-1]["errorCode"] == "AccessDenied"


def test_raw_is_write_once_and_undeletable(world):
    from ccsim.infra import sftp_session
    aws, c = world.aws, "acme_labs"
    sftp = sftp_session(aws, c)
    key = next(f for f in world.truth["tenants"][c]["files"] if f["kind"] == "837")
    k = f"{c}/raw/received_date={key['day']}/837/{key['name']}"
    s3 = aws.client("s3", sftp)
    import base64
    import hashlib
    ck = base64.b64encode(hashlib.sha256(b"tampered").digest()).decode()
    seven_years = aws.clock() + 2555 * 86400
    kw = dict(ServerSideEncryption="aws:kms", SSEKMSKeyId=world.dep.keys[c].arn, ObjectLockMode="COMPLIANCE",
              ObjectLockRetainUntilDate=seven_years, ChecksumSHA256=ck)
    with pytest.raises(AccessDenied):
        s3.put_object(Bucket=BUCKET, Key=k, Body=b"tampered", **kw)                     # no If-None-Match
    from ccsim.aws.errors import PreconditionFailed
    with pytest.raises(PreconditionFailed):
        s3.put_object(Bucket=BUCKET, Key=k, Body=b"tampered", IfNoneMatch="*", **kw)    # exists
    with pytest.raises(AccessDenied):
        s3.put_object(Bucket=BUCKET, Key=k + ".new", Body=b"tampered", IfNoneMatch="*",
                      ServerSideEncryption="aws:kms", SSEKMSKeyId=world.dep.keys[c].arn)  # no retention
    with pytest.raises(AccessDenied):                                                      # retention too short
        s3.put_object(Bucket=BUCKET, Key=k + ".new", Body=b"tampered", IfNoneMatch="*", **dict(
            kw, ObjectLockRetainUntilDate=aws.clock() + 30 * 86400))
    with pytest.raises(AccessDenied):                                                      # another customer's key
        s3.put_object(Bucket=BUCKET, Key=k + ".new", Body=b"tampered", IfNoneMatch="*", **dict(
            kw, SSEKMSKeyId=world.dep.keys["acme_diagnostics"].arn))
    with pytest.raises(AccessDenied):
        s3.delete_object(Bucket=BUCKET, Key=k)


def test_customer_offboarding_by_disabling_its_key(world):
    aws = world.aws
    aws.kms.disable_key(world.dep.keys["acme_diagnostics"].arn)
    try:
        with pytest.raises(ClientError) as e:
            session(world, "dev@xifin.example")
        assert e.value.response["Error"]["Code"] == "KMS.DisabledException"
        assert session(world).con.execute("SELECT count(*) FROM sem.claims").fetchone()[0] > 0   # others unaffected
    finally:
        aws.kms.enable_key(world.dep.keys["acme_diagnostics"].arn)


# --- PHI -------------------------------------------------------------------------------------------
def _member_ids(world) -> list[str]:
    """Every subscriber member id the 837s carried (NM1*IL ... MI <id>), read from the delivered files."""
    ids = set()
    for p in world.sftp_root.rglob("*.x12"):
        if "/837/" in str(p):
            ids |= {m.decode() for m in re.findall(rb"NM1[*|]IL[*|]1[*|][^~]*?[*|]MI[*|]([^~*|]+)~", p.read_bytes())}
    assert len(ids) > 100
    return sorted(ids)


def test_gold_has_no_direct_patient_identifiers(world):
    names_ = set(edi_mock.LAST) | set(edi_mock.FIRST)
    members = _member_ids(world)
    for escalate in (False, True):
        s = session(world, escalate=escalate)
        rels = [f"{a}.{b}" for a, b in q(s, "SELECT schema_name, view_name FROM duckdb_views() WHERE NOT internal")]
        assert rels
        for rel in rels:
            cols = q(s, f"DESCRIBE {rel}")
            colnames = {c[0] for c in cols}
            assert not colnames & {"name_first", "name_last_or_org", "dmg_birth_date", "addr_line1", "postal_code",
                                   "bpr09_sender_account", "bpr15_receiver_account", "raw_text", "elements",
                                   "pat06_death_date"}, rel
            for c, typ, *_ in cols:
                if typ != "VARCHAR":
                    continue
                hits = q(s, f'SELECT count(*) FROM {rel} WHERE "{c}" IN (SELECT unnest(?)) OR "{c}" IN (SELECT unnest(?))',
                         list(names_), members)[0][0]
                assert hits == 0, f"patient name or member id in {rel}.{c}"


def test_policy_export_is_valid_json(tmp_path):
    import json
    files = export(tmp_path)
    assert "s3_bucket_policy.json" in files
    for f in files:
        json.loads((tmp_path / f).read_text())


def test_bucket_policy_is_constant_size_for_any_number_of_customers():
    import json
    from ccsim.infra import bucket_policy
    assert len(json.dumps(bucket_policy(), separators=(",", ":"))) < 5 * 1024
