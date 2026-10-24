"""S3 / KMS / STS / EventBridge / ECS / Entra+Cognito / SigV4 endpoint, one behaviour per test."""
import base64
import json
import time
from datetime import datetime, timedelta, timezone

import duckdb
import pytest
from botocore.exceptions import ClientError

from ccsim.aws import LocalAWS, Clock
from ccsim.aws.errors import (AccessDenied, ExpiredToken, InvalidAccessKeyId, InvalidIdentityToken, KMSDisabled,
                              NoSuchKey, PreconditionFailed, InvalidRange, ValidationError, AuthError)
from ccsim.aws.events import matches, Target
from ccsim.aws.sts import service_principal

ACCT = "111122223333"
V = "2012-10-17"


def code(e):
    return e.value.response["Error"]["Code"]


@pytest.fixture
def acct(tmp_path):
    clock = Clock()
    aws = LocalAWS(tmp_path, clock)
    aws.s3.create_bucket("b", versioning="Enabled", object_lock=True, eventbridge=True)
    aws.iam.create_role("app", {"Version": V, "Statement": [
        {"Effect": "Allow", "Principal": {"Service": "ecs-tasks.amazonaws.com"}, "Action": "sts:AssumeRole"}]})
    aws.iam.put_role_policy("app", "p", {"Version": V, "Statement": [
        {"Effect": "Allow", "Action": ["s3:*"], "Resource": ["arn:aws:s3:::b", "arn:aws:s3:::b/*"]}]})
    aws.iam.create_role("other", {"Version": V, "Statement": [
        {"Effect": "Allow", "Principal": {"Service": "ecs-tasks.amazonaws.com"}, "Action": "sts:AssumeRole"}]})
    aws.iam.put_role_policy("other", "p", {"Version": V, "Statement": [
        {"Effect": "Allow", "Action": ["s3:*", "kms:*"], "Resource": "*"}]})
    key = aws.kms.create_key({"Version": V, "Statement": [
        {"Effect": "Allow", "Principal": {"AWS": f"arn:aws:iam::{ACCT}:role/app"}, "Action": ["kms:Decrypt", "kms:GenerateDataKey"],
         "Resource": "*"}]}, alias="alias/test")
    s = aws.sts.assume_role(service_principal("ecs-tasks.amazonaws.com"), None, f"arn:aws:iam::{ACCT}:role/app", "s1")
    o = aws.sts.assume_role(service_principal("ecs-tasks.amazonaws.com"), None, f"arn:aws:iam::{ACCT}:role/other", "o1")
    yield aws, key, s, o, clock
    aws.shutdown()


def fresh(aws, role="app"):
    return aws.sts.assume_role(service_principal("ecs-tasks.amazonaws.com"), None, f"arn:aws:iam::{ACCT}:role/{role}", "re")


def sha(body):
    import hashlib
    return base64.b64encode(hashlib.sha256(body).digest()).decode()


def kput(aws, s, key, k, body=b"x", **kw):
    return aws.client("s3", s).put_object(Bucket="b", Key=k, Body=body, ServerSideEncryption="aws:kms",
                                          SSEKMSKeyId=key.arn, ChecksumSHA256=sha(body), **kw)


# --- S3 ------------------------------------------------------------------------------------------
def test_bytes_on_disk_are_ciphertext(acct):
    aws, key, s, _, _ = acct
    kput(aws, s, key, "p/secret.txt", b"PATIENT NULLMAN DOB 19700101")
    blobs = list((aws.root / "s3data").iterdir())
    assert blobs and all(b"NULLMAN" not in p.read_bytes() for p in blobs)
    assert aws.client("s3", s).get_object(Bucket="b", Key="p/secret.txt")["Body"].read() == b"PATIENT NULLMAN DOB 19700101"


def test_kms_key_policy_blocks_role_with_kms_star(acct):
    aws, key, s, o, _ = acct
    kput(aws, s, key, "p/a")
    with pytest.raises(AccessDenied):
        aws.client("s3", o).get_object(Bucket="b", Key="p/a")            # s3:* and kms:* are not enough
    assert aws.client("s3", o).head_object(Bucket="b", Key="p/a")["ContentLength"] == 1   # HEAD needs no Decrypt


def test_disabling_a_key_crypto_shreds_its_objects(acct):
    aws, key, s, _, _ = acct
    kput(aws, s, key, "p/a")
    aws.kms.disable_key(key.arn)
    with pytest.raises(KMSDisabled):
        aws.client("s3", s).get_object(Bucket="b", Key="p/a")
    aws.kms.enable_key(key.arn)
    assert aws.client("s3", s).get_object(Bucket="b", Key="p/a")["Body"].read() == b"x"


def test_encryption_context_binds_blob_to_object(acct):
    aws, key, s, _, _ = acct
    _, blob, _ = aws.kms.generate_data_key(key.key_id, {"aws:s3:arn": "arn:aws:s3:::b/one"})
    from ccsim.aws.errors import KMSInvalidCiphertext
    with pytest.raises(KMSInvalidCiphertext):
        aws.kms.decrypt(blob, {"aws:s3:arn": "arn:aws:s3:::b/two"})


def test_key_rotation_keeps_old_objects_readable(acct):
    aws, key, s, _, _ = acct
    kput(aws, s, key, "p/old", b"old")
    aws.kms.rotate_key_on_demand(key.arn)
    kput(aws, s, key, "p/new", b"new")
    c = aws.client("s3", s)
    assert c.get_object(Bucket="b", Key="p/old")["Body"].read() == b"old"
    assert c.get_object(Bucket="b", Key="p/new")["Body"].read() == b"new"


def test_versioning_overwrite_and_delete_marker(acct):
    aws, key, s, _, _ = acct
    c = aws.client("s3", s)
    v1 = kput(aws, s, key, "p/k", b"one")["VersionId"]
    kput(aws, s, key, "p/k", b"two")
    assert c.get_object(Bucket="b", Key="p/k")["Body"].read() == b"two"
    assert c.get_object(Bucket="b", Key="p/k", VersionId=v1)["Body"].read() == b"one"
    assert c.delete_object(Bucket="b", Key="p/k")["DeleteMarker"]
    with pytest.raises(NoSuchKey):
        c.get_object(Bucket="b", Key="p/k")
    assert c.get_object(Bucket="b", Key="p/k", VersionId=v1)["Body"].read() == b"one"
    vers = c.list_object_versions(Bucket="b", Prefix="p/k")
    assert len(vers["Versions"]) == 2 and len(vers["DeleteMarkers"]) == 1


def test_object_lock_compliance_blocks_version_delete_until_expiry(acct):
    aws, key, s, _, clock = acct
    c = aws.client("s3", s)
    vid = kput(aws, s, key, "raw/f", b"edi", ObjectLockMode="COMPLIANCE",
               ObjectLockRetainUntilDate=clock() + 86400)["VersionId"]
    with pytest.raises(AccessDenied):
        c.delete_object(Bucket="b", Key="raw/f", VersionId=vid)
    assert c.delete_object(Bucket="b", Key="raw/f")["DeleteMarker"]      # a delete marker is still allowed...
    assert c.get_object(Bucket="b", Key="raw/f", VersionId=vid)["Body"].read() == b"edi"   # ...the data survives
    clock.advance(86401)
    aws.client("s3", fresh(aws)).delete_object(Bucket="b", Key="raw/f", VersionId=vid)


def test_governance_mode_needs_bypass_permission(acct):
    aws, key, s, _, clock = acct
    vid = kput(aws, s, key, "g/f", ObjectLockMode="GOVERNANCE", ObjectLockRetainUntilDate=clock() + 3600)["VersionId"]
    c = aws.client("s3", s)
    with pytest.raises(AccessDenied):
        c.delete_object(Bucket="b", Key="g/f", VersionId=vid)
    c.delete_object(Bucket="b", Key="g/f", VersionId=vid, BypassGovernanceRetention=True)   # role has s3:*


def test_legal_hold(acct):
    aws, key, s, _, _ = acct
    vid = kput(aws, s, key, "h/f", ObjectLockLegalHoldStatus="ON")["VersionId"]
    with pytest.raises(AccessDenied):
        aws.client("s3", s).delete_object(Bucket="b", Key="h/f", VersionId=vid)


def test_conditional_writes(acct):
    aws, key, s, _, _ = acct
    kput(aws, s, key, "c/CURRENT", b"v=1", IfNoneMatch="*")
    with pytest.raises(PreconditionFailed):
        kput(aws, s, key, "c/CURRENT", b"v=2", IfNoneMatch="*")
    etag = aws.client("s3", s).head_object(Bucket="b", Key="c/CURRENT")["ETag"]
    kput(aws, s, key, "c/CURRENT", b"v=2", IfMatch=etag)
    with pytest.raises(PreconditionFailed):
        kput(aws, s, key, "c/CURRENT", b"v=3", IfMatch=etag)              # stale etag: lost update prevented
    with pytest.raises(NoSuchKey):
        kput(aws, s, key, "c/missing", b"x", IfMatch=etag)


def test_bucket_policy_conditions_on_put(acct):
    aws, key, s, _, _ = acct
    aws.s3.bucket("b").policy = {"Version": V, "Statement": [
        {"Effect": "Deny", "Principal": "*", "Action": "s3:PutObject", "Resource": "arn:aws:s3:::b/raw/*",
         "Condition": {"Null": {"s3:if-none-match": "true"}}},
        {"Effect": "Deny", "Principal": "*", "Action": "s3:PutObject", "Resource": "arn:aws:s3:::b/*",
         "Condition": {"StringNotEquals": {"s3:x-amz-server-side-encryption-aws-kms-key-id": key.arn}}}]}
    with pytest.raises(AccessDenied):
        kput(aws, s, key, "raw/x")                                        # no If-None-Match
    kput(aws, s, key, "raw/x", IfNoneMatch="*")
    with pytest.raises(AccessDenied):
        aws.client("s3", s).put_object(Bucket="b", Key="other/x", Body=b"x")   # bucket default, not the required key


def test_ranges(acct):
    aws, key, s, _, _ = acct
    kput(aws, s, key, "r", b"0123456789")
    c = aws.client("s3", s)
    assert c.get_object(Bucket="b", Key="r", Range="bytes=2-4")["Body"].read() == b"234"
    assert c.get_object(Bucket="b", Key="r", Range="bytes=7-")["Body"].read() == b"789"
    assert c.get_object(Bucket="b", Key="r", Range="bytes=-3")["Body"].read() == b"789"
    assert c.get_object(Bucket="b", Key="r", Range="bytes=8-100")["Body"].read() == b"89"
    with pytest.raises(InvalidRange):
        c.get_object(Bucket="b", Key="r", Range="bytes=10-12")
    assert c.get_object(Bucket="b", Key="r", Range="bytes=5-2")["Body"].read() == b"0123456789"   # ignored, not 416


def test_list_pagination_delimiter_and_utf8_order(acct):
    aws, key, s, _, _ = acct
    for k in ["d/a/1", "d/a/2", "d/b/1", "d/c", "d/é", "d/z"]:
        kput(aws, s, key, k)
    c = aws.client("s3", s)
    r = c.list_objects_v2(Bucket="b", Prefix="d/", Delimiter="/")
    assert [p["Prefix"] for p in r["CommonPrefixes"]] == ["d/a/", "d/b/"]
    assert [o["Key"] for o in r["Contents"]] == ["d/c", "d/z", "d/é"]     # UTF-8 byte order puts é last
    keys, token = [], None
    while True:
        r = c.list_objects_v2(Bucket="b", Prefix="d/", MaxKeys=2, **({"ContinuationToken": token} if token else {}))
        keys += [o["Key"] for o in r["Contents"]]
        if not r["IsTruncated"]:
            break
        token = r["NextContinuationToken"]
    assert keys == ["d/a/1", "d/a/2", "d/b/1", "d/c", "d/z", "d/é"]


def test_missing_key_is_403_without_list_permission(acct):
    aws, key, s, _, _ = acct
    aws.iam.put_role_policy("app", "p", {"Version": V, "Statement": [
        {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"], "Resource": "arn:aws:s3:::b/*"}]})
    with pytest.raises(AccessDenied):
        aws.client("s3", s).get_object(Bucket="b", Key="nope")


def test_lifecycle_expires_noncurrent_versions_but_not_locked_ones(acct):
    aws, key, s, _, clock = acct
    kput(aws, s, key, "gold/x", b"1")
    kput(aws, s, key, "gold/x", b"2")
    kput(aws, s, key, "raw/x", b"1", ObjectLockMode="COMPLIANCE", ObjectLockRetainUntilDate=clock() + 10 * 86400)
    kput(aws, s, key, "raw/x", b"2", ObjectLockMode="COMPLIANCE", ObjectLockRetainUntilDate=clock() + 10 * 86400)
    aws.s3.bucket("b").lifecycle = [{"Prefix": "", "NoncurrentDays": 1}]
    clock.advance(2 * 86400)
    assert aws.s3.run_lifecycle() == 1
    c = aws.client("s3", fresh(aws))
    assert len(c.list_object_versions(Bucket="b", Prefix="gold/")["Versions"]) == 1
    assert len(c.list_object_versions(Bucket="b", Prefix="raw/")["Versions"]) == 2


def test_s3_events_reach_eventbridge(acct):
    aws, key, s, _, _ = acct
    seen = []
    aws.events.put_rule("r", {"source": ["aws.s3"], "detail": {"object": {"key": [{"suffix": "/_SUCCESS"}]}}})
    aws.events.put_targets("r", Target("t", lambda e, sess: seen.append(e["detail"]["object"]["key"])))
    kput(aws, s, key, "x/data")
    kput(aws, s, key, "x/_SUCCESS")
    assert seen == ["x/_SUCCESS"]


# --- STS -----------------------------------------------------------------------------------------
def test_sts_expiry_and_duration_limits(acct):
    aws, key, s, _, clock = acct
    with pytest.raises(ValidationError):
        aws.sts.assume_role(service_principal("ecs-tasks.amazonaws.com"), None, f"arn:aws:iam::{ACCT}:role/app", "x",
                            DurationSeconds=7200)                         # above MaxSessionDuration
    with pytest.raises(ValidationError):
        aws.sts.assume_role(service_principal("ecs-tasks.amazonaws.com"), None, f"arn:aws:iam::{ACCT}:role/app", "x!")
    clock.advance(3601)
    with pytest.raises(ExpiredToken):
        aws.client("s3", s).list_objects_v2(Bucket="b")


def test_trust_policy_and_chaining(acct):
    aws, _, s, _, _ = acct
    aws.iam.create_role("target", {"Version": V, "Statement": [
        {"Effect": "Allow", "Principal": {"AWS": f"arn:aws:iam::{ACCT}:role/app"}, "Action": ["sts:AssumeRole", "sts:TagSession"]}]})
    t = aws.sts.assume_role(s.principal(), s, f"arn:aws:iam::{ACCT}:role/target", "chain", 900,
                            Tags=[{"Key": "customer", "Value": "acme_labs"}], TransitiveTagKeys=["customer"])
    assert t.chained and t.tags == {"customer": "acme_labs"}
    with pytest.raises(ValidationError):
        aws.sts.assume_role(s.principal(), s, f"arn:aws:iam::{ACCT}:role/target", "chain2", 7200)   # chaining caps at 1 h
    with pytest.raises(AccessDenied):
        aws.sts.assume_role(service_principal("ec2.amazonaws.com"), None, f"arn:aws:iam::{ACCT}:role/target", "nope")


def test_revoke_older_sessions(acct):
    aws, key, s, _, clock = acct
    kput(aws, s, key, "p/a")
    clock.advance(1)
    aws.sts.revoke_older_sessions("app")
    with pytest.raises(AccessDenied):
        aws.client("s3", s).get_object(Bucket="b", Key="p/a")
    clock.advance(1)
    fresh = aws.sts.assume_role(service_principal("ecs-tasks.amazonaws.com"), None, f"arn:aws:iam::{ACCT}:role/app", "new")
    assert aws.client("s3", fresh).get_object(Bucket="b", Key="p/a")["Body"].read() == b"x"


def test_unknown_or_tampered_credentials(acct):
    aws, _, s, _, _ = acct
    from ccsim.aws.sts import Session
    with pytest.raises(InvalidAccessKeyId):
        aws.sts.lookup("ASIANOPE", "x")
    with pytest.raises(InvalidAccessKeyId):
        aws.sts.lookup(s.access_key_id, "tampered")


# --- identity ------------------------------------------------------------------------------------
def test_entra_cognito_web_identity_flow(acct):
    aws, _, _, _, clock = acct
    provider = aws.create_identity("xifin.example", ["cc-users"])
    hp = provider.split("oidc-provider/")[1]
    aws.iam.create_role("reader-a", {"Version": V, "Statement": [{"Effect": "Allow", "Principal": {"Federated": provider},
        "Action": ["sts:AssumeRoleWithWebIdentity", "sts:TagSession"],
        "Condition": {"StringEquals": {f"{hp}:aud": aws.cognito.client_id, "aws:RequestTag/customer": "a"}}}]})
    aws.iam.create_role("reader-b", {"Version": V, "Statement": [{"Effect": "Allow", "Principal": {"Federated": provider},
        "Action": ["sts:AssumeRoleWithWebIdentity", "sts:TagSession"],
        "Condition": {"StringEquals": {f"{hp}:aud": aws.cognito.client_id, "aws:RequestTag/customer": "b"}}}]})
    aws.entra.add_user("ana@xifin.example", ["cc-users"])
    aws.entra.add_user("bob@xifin.example", [])
    with pytest.raises(AuthError) as e:
        aws.entra.sign_in("bob@xifin.example")
    assert e.value.code == "AADSTS50105"
    with pytest.raises(AuthError):
        aws.entra.sign_in("nobody@xifin.example")
    entra_tok = aws.entra.sign_in("ana@xifin.example")
    with pytest.raises(AuthError) as e:
        aws.cognito.federated_sign_in(entra_tok)                         # JIT user exists but has no customer yet
    assert e.value.code == "UserNotMapped"
    user = aws.cognito.username_for_email("ana@xifin.example")
    aws.cognito.admin_update_user_attributes(user, {"custom:customer": "a"})
    toks = aws.cognito.federated_sign_in(entra_tok)
    s = aws.sts.assume_role_with_web_identity(f"arn:aws:iam::{ACCT}:role/reader-a", "u1", toks["IdToken"], 900)
    assert s.tags["customer"] == "a"
    with pytest.raises(AccessDenied):                                    # same token, other customer's role
        aws.sts.assume_role_with_web_identity(f"arn:aws:iam::{ACCT}:role/reader-b", "u1", toks["IdToken"], 900)
    # tampering: flip the customer claim -> signature fails
    h, c, sig = toks["IdToken"].split(".")
    claims = json.loads(base64.urlsafe_b64decode(c + "=="))
    claims["custom:customer"] = "b"
    claims["https://aws.amazon.com/tags"]["principal_tags"]["customer"] = ["b"]
    forged = ".".join([h, base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode(), sig])
    with pytest.raises(InvalidIdentityToken):
        aws.sts.assume_role_with_web_identity(f"arn:aws:iam::{ACCT}:role/reader-b", "u1", forged, 900)
    none_alg = ".".join([base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode(), c, ""])
    with pytest.raises(InvalidIdentityToken):
        aws.sts.assume_role_with_web_identity(f"arn:aws:iam::{ACCT}:role/reader-a", "u1", none_alg, 900)
    # refresh works until global sign-out; the id token itself lives until exp
    assert aws.cognito.refresh_tokens(toks["RefreshToken"])["IdToken"]
    aws.cognito.admin_user_global_sign_out(user)
    with pytest.raises(AuthError):
        aws.cognito.refresh_tokens(toks["RefreshToken"])
    aws.sts.assume_role_with_web_identity(f"arn:aws:iam::{ACCT}:role/reader-a", "u2", toks["IdToken"], 900)
    clock.advance(3601)
    with pytest.raises(InvalidIdentityToken):
        aws.sts.assume_role_with_web_identity(f"arn:aws:iam::{ACCT}:role/reader-a", "u3", toks["IdToken"], 900)


# --- EventBridge ---------------------------------------------------------------------------------
@pytest.mark.parametrize("pattern,ok", [
    ({"source": ["aws.s3"]}, True),
    ({"source": ["aws.ec2"]}, False),
    ({"detail": {"object": {"key": [{"prefix": "acme_labs/raw/"}]}}}, True),
    ({"detail": {"object": {"key": [{"suffix": "_SUCCESS"}]}}}, True),
    ({"detail": {"object": {"key": [{"wildcard": "acme_labs/raw/received_date=*/_SUCCESS"}]}}}, True),
    ({"detail": {"object": {"key": [{"wildcard": "acme_diagnostics/*"}]}}}, False),
    ({"detail": {"object": {"key": [{"anything-but": {"prefix": "acme_labs/"}}]}}}, False),
    ({"detail": {"object": {"size": [{"numeric": [">", 0, "<=", 10]}]}}}, False),
    ({"detail": {"object": {"size": [{"numeric": [">=", 0]}]}}}, True),
    ({"detail": {"object": {"version-id": [{"exists": True}]}}}, False),
    ({"detail": {"object": {"version-id": [{"exists": False}]}}}, True),
    ({"detail-type": [{"equals-ignore-case": "object created"}]}, True),
    ({"$or": [{"source": ["x"]}, {"detail-type": ["Object Created"]}]}, True),
    ({"resources": ["arn:aws:s3:::cc-prod"]}, True),           # arrays match if any element matches
])
def test_event_patterns(pattern, ok):
    evt = {"source": "aws.s3", "detail-type": "Object Created", "resources": ["arn:aws:s3:::cc-prod"],
           "detail": {"object": {"key": "acme_labs/raw/received_date=2026-07-01/_SUCCESS", "size": 0}}}
    assert matches(pattern, evt) is ok


def test_failed_targets_retry_then_dead_letter(acct):
    aws, *_ = acct
    calls = []

    def flaky(e, s):
        calls.append(1)
        raise RuntimeError("boom")
    aws.events.put_rule("r", {"source": ["x"]})
    t = Target("t", flaky, max_attempts=3)
    aws.events.put_targets("r", t)
    aws.events.put_event("x", "d", {})
    assert len(calls) == 3 and len(t.dead_letters) == 1


def test_at_least_once_delivery_duplicates(acct):
    aws, *_ = acct
    got = []
    aws.events.duplicate_rate = 1.0
    aws.events.put_rule("r", {"source": ["x"]})
    aws.events.put_targets("r", Target("t", lambda e, s: got.append(e["id"])))
    aws.events.put_event("x", "d", {})
    assert len(got) == 2 and got[0] == got[1]


# --- ECS -----------------------------------------------------------------------------------------
def test_run_task_needs_pass_role_and_creds_outlive_the_task(acct):
    aws, key, s, _, _ = acct
    td = aws.ecs.register_task_definition("job", f"arn:aws:iam::{ACCT}:role/app", lambda env: env.credentials)
    aws.iam.create_role("caller", {"Version": V, "Statement": [
        {"Effect": "Allow", "Principal": {"Service": "events.amazonaws.com"}, "Action": "sts:AssumeRole"}]})
    aws.iam.put_role_policy("caller", "p", {"Version": V, "Statement": [
        {"Effect": "Allow", "Action": "ecs:RunTask", "Resource": "*"}]})
    caller = aws.sts.assume_role(service_principal("events.amazonaws.com"), None, f"arn:aws:iam::{ACCT}:role/caller", "caller")
    with pytest.raises(AccessDenied):
        aws.ecs.run_task(caller, "job")                                   # no iam:PassRole
    aws.iam.put_role_policy("caller", "pass", {"Version": V, "Statement": [
        {"Effect": "Allow", "Action": "iam:PassRole", "Resource": f"arn:aws:iam::{ACCT}:role/app",
         "Condition": {"StringEquals": {"iam:PassedToService": "ecs-tasks.amazonaws.com"}}}]})
    r = aws.ecs.run_task(caller, "job")
    task = aws.ecs.describe(r["tasks"][0]["taskArn"])
    assert task.last_status == "STOPPED" and task.exit_code == 0
    # AWS does not revoke task-role credentials at stop: they work until they expire
    aws.client("s3", task.result).list_objects_v2(Bucket="b")


# --- SigV4 endpoint ------------------------------------------------------------------------------
def test_boto3_over_http_and_signature_checks(acct):
    aws, key, s, o, _ = acct
    c = aws.boto3_client("s3", s)
    c.put_object(Bucket="b", Key="h/a b=c.txt", Body=b"hello", ServerSideEncryption="aws:kms", SSEKMSKeyId=key.arn)
    assert c.get_object(Bucket="b", Key="h/a b=c.txt")["Body"].read() == b"hello"
    r = c.list_objects_v2(Bucket="b", Prefix="h/")
    assert [x["Key"] for x in r["Contents"]] == ["h/a b=c.txt"]
    bad = aws.boto3_client("s3", dict(s.credentials(), SecretAccessKey="wrong"))
    with pytest.raises(ClientError) as e:
        bad.list_objects_v2(Bucket="b")
    assert code(e) == "SignatureDoesNotMatch"
    with pytest.raises(ClientError) as e:
        aws.boto3_client("s3", None).list_objects_v2(Bucket="b")         # anonymous
    assert code(e) == "AccessDenied"
    with pytest.raises(ClientError) as e:
        aws.boto3_client("s3", o).get_object(Bucket="b", Key="h/a b=c.txt")
    assert code(e) == "AccessDenied"
    url = c.generate_presigned_url("get_object", Params={"Bucket": "b", "Key": "h/a b=c.txt"}, ExpiresIn=60)
    import urllib.request
    assert urllib.request.urlopen(url).read() == b"hello"
    assert aws.boto3_client("sts", s).get_caller_identity()["Arn"].endswith("assumed-role/app/s1")


def test_sts_over_http(acct):
    aws, _, s, _, _ = acct
    aws.iam.create_role("t2", {"Version": V, "Statement": [
        {"Effect": "Allow", "Principal": {"AWS": f"arn:aws:iam::{ACCT}:role/app"}, "Action": "sts:AssumeRole"}]})
    sts = aws.boto3_client("sts", s)
    r = sts.assume_role(RoleArn=f"arn:aws:iam::{ACCT}:role/t2", RoleSessionName="viahttp", DurationSeconds=900)
    assert r["AssumedRoleUser"]["Arn"].endswith("assumed-role/t2/viahttp")
    with pytest.raises(ClientError) as e:
        sts.assume_role(RoleArn=f"arn:aws:iam::{ACCT}:role/other", RoleSessionName="nope")
    assert code(e) == "AccessDenied"


def test_duckdb_httpfs_with_scoped_secret(acct, tmp_path):
    aws, key, s, o, _ = acct
    p = tmp_path / "t.parquet"
    duckdb.execute(f"COPY (SELECT range AS i FROM range(50000)) TO '{p}'")
    for part in ("x/t/p1.parquet", "x/t/p2.parquet"):
        kput(aws, s, key, part, p.read_bytes())
    con = duckdb.connect()
    con.execute("LOAD httpfs")
    con.execute(aws.duckdb_secret_sql(s, "s3://b/x/"))
    assert con.execute("SELECT count(*) FROM read_parquet('s3://b/x/t/*.parquet')").fetchone()[0] == 100000
    con2 = duckdb.connect()
    con2.execute("LOAD httpfs")
    con2.execute(aws.duckdb_secret_sql(o, "s3://b/x/"))
    with pytest.raises(duckdb.Error):                                   # other role: S3 ok, KMS key policy says no
        con2.execute("SELECT count(*) FROM read_parquet('s3://b/x/t/*.parquet')").fetchall()


def test_cloudtrail_records_allow_and_deny(acct):
    aws, key, s, o, _ = acct
    kput(aws, s, key, "p/a")
    with pytest.raises(AccessDenied):
        aws.client("s3", o).get_object(Bucket="b", Key="p/a")
    ev = aws.trail.events()
    puts = [e for e in ev if e["eventName"] == "PutObject" and not e.get("errorCode")]
    assert puts and puts[0]["userIdentity"]["arn"].endswith("assumed-role/app/s1")
    kms_denied = [e for e in ev if e["eventSource"] == "kms.amazonaws.com" and e.get("errorCode") == "AccessDenied"]
    assert kms_denied and kms_denied[0]["requestParameters"]["encryptionContext"] == {"aws:s3:arn": "arn:aws:s3:::b/p/a"}


def test_object_lock_parameters_are_validated(acct):
    aws, key, s, _, clock = acct
    from ccsim.aws.errors import InvalidArgument, InvalidRequest
    with pytest.raises(InvalidArgument):
        kput(aws, s, key, "l/a", ObjectLockMode="COMPLIANCE")                       # no retain-until date
    with pytest.raises(InvalidArgument):
        kput(aws, s, key, "l/a", ObjectLockMode="COMPLIANCE", ObjectLockRetainUntilDate=clock() - 1)
    with pytest.raises(InvalidRequest):                                             # no checksum
        aws.client("s3", s).put_object(Bucket="b", Key="l/a", Body=b"x", ServerSideEncryption="aws:kms",
                                       SSEKMSKeyId=key.arn, ObjectLockMode="COMPLIANCE",
                                       ObjectLockRetainUntilDate=clock() + 60)


def test_conditional_copy_is_not_implemented(acct):
    aws, key, s, _, _ = acct
    from ccsim.aws.errors import NotImplementedOp
    kput(aws, s, key, "c/src")
    with pytest.raises(NotImplementedOp):
        aws.client("s3", s).copy_object(Bucket="b", Key="c/dst", CopySource={"Bucket": "b", "Key": "c/src"},
                                        IfNoneMatch="*", ServerSideEncryption="aws:kms", SSEKMSKeyId=key.arn)


def test_kms_grant_bypasses_key_policy_conditions(acct):
    """Why the design must not let admins call kms:CreateGrant: a grant is honored without the key policy."""
    aws, key, s, o, _ = acct
    kput(aws, s, key, "p/a")
    with pytest.raises(AccessDenied):
        aws.kms.create_grant_as(o.principal(), o, aws.iam, key.arn, o.role_arn, ["Decrypt"])   # key policy says no
    key.policy["Statement"].append({"Effect": "Allow", "Principal": {"AWS": o.role_arn}, "Action": "kms:CreateGrant",
                                    "Resource": "*"})
    aws.kms.create_grant_as(o.principal(), o, aws.iam, key.arn, o.role_arn, ["Decrypt"])
    assert aws.client("s3", o).get_object(Bucket="b", Key="p/a")["Body"].read() == b"x"


def test_bucket_policy_changes_are_authorized_and_size_limited(acct):
    aws, key, s, o, _ = acct
    from ccsim.aws.errors import AWSError
    deny_admin = {"Version": V, "Statement": [{"Effect": "Deny", "Principal": "*", "Action": ["s3:PutBucketPolicy",
                  "s3:DeleteBucketPolicy"], "Resource": "arn:aws:s3:::b",
                  "Condition": {"StringNotLike": {"aws:PrincipalArn": f"arn:aws:iam::{ACCT}:role/app"}}}]}
    aws.client("s3", s).put_bucket_policy(Bucket="b", Policy=deny_admin)
    with pytest.raises(AccessDenied):
        aws.client("s3", o).delete_bucket_policy(Bucket="b")                     # role with s3:* still denied
    big = {"Version": V, "Statement": [dict(deny_admin["Statement"][0], Sid=f"S{i}") for i in range(200)]}
    with pytest.raises(AWSError) as e:
        aws.client("s3", s).put_bucket_policy(Bucket="b", Policy=big)
    assert e.value.code == "PolicyTooLarge"
    ghost = {"Version": V, "Statement": [{"Effect": "Allow", "Principal": {"AWS": f"arn:aws:iam::{ACCT}:role/ghost"},
             "Action": "s3:GetObject", "Resource": "arn:aws:s3:::b/*"}]}
    with pytest.raises(AWSError) as e:
        aws.client("s3", s).put_bucket_policy(Bucket="b", Policy=ghost)
    assert e.value.code == "MalformedPolicy"
