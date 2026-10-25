"""Chat transcripts: Design A keeps no message text on disk; Design B isolates, deletes, and crypto-shreds it.

The lake, the generator, and the parser are not involved. Provision stands up the account the same way
the rest of the suite does, minus EDI.
"""
import json
from pathlib import Path

import pytest

from ccsim.aws import LocalAWS
from ccsim.aws.errors import AccessDenied, KMSDisabled
from ccsim.aws.iam import ACCOUNT, REGION
from ccsim.aws.sts import service_principal
from ccsim.infra import (BUCKET, BREAKGLASS_EXTERNAL_ID, API_ROLE, TRANSCRIPT_RETENTION_DAYS, TRANSCRIPTS,
                         names, provision, role_arn, transcript_names)
from ccsim.query import login
from ccsim.transcripts import (EPHEMERAL, PERSISTED, QueryAudit, TranscriptError, TranscriptModeError,
                               TranscriptNotFound, TranscriptStore, Turn, resolve_transcript_mode)
from ccsim.world import add_user

SENTINEL = "PHI-SENTINEL-Jane-Doe-DOB-1970-01-01-SSN-999-99-9999"
ANA = "ana@xifin.example"
BOB = "bob@xifin.example"
CARA = "cara@xifin.example"
ADA = "ada@xifin.example"   # customer admin, acme_labs
ED = "ed@xifin.example"     # customer admin, acme_diagnostics
ADMIN_GROUPS = ("claimclarity-users", "claimclarity-customer-admins")


@pytest.fixture
def env(tmp_path):
    aws = LocalAWS(tmp_path / "aws")
    dep = provision(aws, ["acme_labs", "acme_diagnostics"], lambda task_env: None)
    add_user(aws, ANA, "acme_labs")
    add_user(aws, BOB, "acme_labs")
    add_user(aws, CARA, "acme_diagnostics")
    add_user(aws, ADA, "acme_labs", groups=ADMIN_GROUPS)
    add_user(aws, ED, "acme_diagnostics", groups=ADMIN_GROUPS)
    yield aws, dep
    aws.shutdown()


def disk_hits(root: Path, needle: bytes) -> list[str]:
    hits = []
    for path in root.rglob("*"):
        if path.is_file() and needle in path.read_bytes():
            hits.append(str(path.relative_to(root)))
    return hits


def sub(aws, token: str) -> str:
    return aws.cognito.verify_id_token(token)["sub"]


def test_mode_defaults_to_ephemeral(monkeypatch):
    monkeypatch.delenv("CC_TRANSCRIPT_MODE", raising=False)
    assert resolve_transcript_mode(None) == EPHEMERAL
    monkeypatch.setenv("CC_TRANSCRIPT_MODE", PERSISTED)
    assert resolve_transcript_mode(None) == PERSISTED
    assert resolve_transcript_mode(EPHEMERAL) == EPHEMERAL  # explicit argument wins over the environment
    with pytest.raises(ValueError):
        resolve_transcript_mode("both")


def test_ephemeral_mode_leaves_no_content_on_disk(env):
    aws, dep = env
    store = TranscriptStore(aws, dep, mode=EPHEMERAL)
    token, admin = login(aws, ANA), login(aws, ADA)
    session_id = store.open_chat(token)
    request_id = "req-ephemeral-1"
    sql = f"SELECT * FROM sem.remit_claims WHERE patient_note = '{SENTINEL}'"
    store.append_turn(token, session_id, Turn("user", f"why was this denied for {SENTINEL}", request_id=request_id),
                      QueryAudit(request_id, sql=sql, metric="denial_rate", tables=["sem.remit_claims"],
                                 row_count=4, duration_ms=12))
    assert SENTINEL in store.read_chat(token, session_id)["turns"][0]["text"]
    assert disk_hits(aws.root, SENTINEL.encode()) == []
    assert "SELECT" not in json.dumps(store.read_query_audit(admin, request_id))
    assert SENTINEL not in json.dumps(store.list_chats(token))
    user = store._assume_user(token, store._claims(token))
    prefix = f"acme_labs/{TRANSCRIPTS}/users/{sub(aws, token)}/"
    assert store._list_keys(user, prefix + "content/") == []
    assert store._list_keys(user, prefix + "dek.bin") == []
    with pytest.raises(TranscriptError):
        store.record_query_audit(token, session_id, QueryAudit(request_id, sql="SELECT 1", tables=["sem.claims"]))
    with pytest.raises(TranscriptModeError):
        store.export_deidentified(admin)
    with pytest.raises(TranscriptModeError):
        store.read_chat_as_admin(admin, sub(aws, token), session_id)

    store.close_chat(token, session_id)
    with pytest.raises(TranscriptNotFound):
        store.read_chat(token, session_id)
    restarted = TranscriptStore(aws, dep, mode=EPHEMERAL)
    with pytest.raises(TranscriptNotFound):
        restarted.read_chat(token, session_id)
    assert restarted.list_chats(token)[0]["turn_count"] == 1
    assert disk_hits(aws.root, SENTINEL.encode()) == []

    store.delete_my_chats(token)
    assert store.list_chats(token) == []
    assert store.read_query_audit(admin, request_id)["row_count"] == 4
    writer = store._assume_audit_writer("acme_labs", sub(aws, token))
    with pytest.raises(AccessDenied):
        aws.client("s3", writer).delete_object(Bucket=BUCKET, Key=f"acme_labs/audit/queries/1970-01-01/{request_id}.json")


def test_persisted_transcripts_are_isolated_across_customers_and_users(env):
    aws, dep = env
    store = TranscriptStore(aws, dep, mode=PERSISTED)
    ana, bob, cara = login(aws, ANA), login(aws, BOB), login(aws, CARA)
    ada, ed = login(aws, ADA), login(aws, ED)
    ana_session = store.open_chat(ana)
    turn_id = store.append_turn(ana, ana_session, Turn("assistant", f"the claim for {SENTINEL} was denied",
                                                      model="claude", output_tokens=20))
    bob_session = store.open_chat(bob)
    store.append_turn(bob, bob_session, Turn("user", "bob-only-marker"))
    cara_session = store.open_chat(cara)
    store.append_turn(cara, cara_session, Turn("user", "cara-only-marker"))

    assert SENTINEL in store.read_chat(ana, ana_session)["turns"][0]["text"]
    assert disk_hits(aws.root, SENTINEL.encode()) == []
    # A restarted process can still read Design B, because the bytes are in S3. The other user's token
    # only looks under his own prefix, and his credentials are denied on hers.
    assert SENTINEL in TranscriptStore(aws, dep, mode=PERSISTED).read_chat(ana, ana_session)["turns"][0]["text"]
    with pytest.raises(TranscriptNotFound):
        store.read_chat(bob, ana_session)
    ana_key = f"acme_labs/{TRANSCRIPTS}/users/{sub(aws, ana)}/content/{ana_session}/{turn_id}.bin"
    bob_session_creds = store._assume_user(bob, store._claims(bob))
    with pytest.raises(AccessDenied):
        aws.client("s3", bob_session_creds).get_object(Bucket=BUCKET, Key=ana_key)
    with pytest.raises(AccessDenied):
        aws.sts.assume_role_with_web_identity(role_arn(transcript_names("acme_labs")["user"]), "steal", cara, 900)
    cara_creds = store._assume_user(cara, store._claims(cara))
    with pytest.raises(AccessDenied):
        aws.client("s3", cara_creds).get_object(Bucket=BUCKET, Key=ana_key)
    assert SENTINEL in store.read_chat_as_admin(ada, sub(aws, ana), ana_session)["turns"][0]["text"]
    with pytest.raises(AccessDenied):
        aws.sts.assume_role_with_web_identity(role_arn(transcript_names("acme_labs")["admin"]), "steal", ed, 900)
    with pytest.raises(TranscriptNotFound):
        store.read_chat_as_admin(ed, sub(aws, ana), ana_session)
    assert store.read_chat(bob, bob_session)["turns"][0]["text"] == "bob-only-marker"
    assert store.read_chat(cara, cara_session)["turns"][0]["text"] == "cara-only-marker"


def test_user_deletion_and_customer_key_crypto_shred(env):
    aws, dep = env
    store = TranscriptStore(aws, dep, mode=PERSISTED)
    ana, bob, cara = login(aws, ANA), login(aws, BOB), login(aws, CARA)
    admin = login(aws, ADA)
    ana_sub = sub(aws, ana)
    ana_session = store.open_chat(ana)
    store.append_turn(ana, ana_session, Turn("user", SENTINEL),
                      QueryAudit("req-shred-1", sql=f"SELECT '{SENTINEL}'", metric="denial_rate",
                                 tables=["sem.remit_claims"], row_count=1, duration_ms=3))
    bob_session = store.open_chat(bob)
    store.append_turn(bob, bob_session, Turn("user", "bob-survives-deletion"))
    cara_session = store.open_chat(cara)
    store.append_turn(cara, cara_session, Turn("user", "cara-other-customer"))

    ana_creds = store._assume_user(ana, store._claims(ana))
    dek_key = f"acme_labs/{TRANSCRIPTS}/users/{ana_sub}/dek.bin"
    stolen_wrapped_dek = aws.client("s3", ana_creds).get_object(Bucket=BUCKET, Key=dek_key)["Body"].read()
    prefix = f"acme_labs/{TRANSCRIPTS}/users/{ana_sub}/"
    blobs_before = [row[0] for row in aws.s3.db.execute(
        "SELECT blob FROM objects WHERE bucket=? AND substr(key,1,?)=? AND blob IS NOT NULL AND delete_marker=0",
        (BUCKET, len(prefix), prefix)).fetchall()]
    assert blobs_before

    deleted = store.delete_my_chats(ana)
    assert deleted["versions_deleted"] >= 1
    with pytest.raises(TranscriptNotFound):
        store.read_chat(ana, ana_session)
    assert aws.client("s3", ana_creds).list_object_versions(Bucket=BUCKET, Prefix=prefix)["Versions"] == []
    for blob in blobs_before:
        assert not (aws.root / "s3data" / blob).exists()
    assert store.read_chat(bob, bob_session)["turns"][0]["text"] == "bob-survives-deletion"
    assert store.read_query_audit(admin, "req-shred-1")["sql_sha256"]
    assert SENTINEL not in json.dumps(store.read_query_audit(admin, "req-shred-1"))
    # Deleting the account's only copy of the wrapped data key does not help someone who already copied it.
    # Disabling the customer transcript CMK does.
    context = {"customer": "acme_labs", "user": ana_sub, "purpose": "transcript-dek"}
    assert aws.kms.decrypt(stolen_wrapped_dek, context)
    store.crypto_shred_customer("acme_labs")
    with pytest.raises(KMSDisabled):
        aws.kms.decrypt(stolen_wrapped_dek, context)
    with pytest.raises(KMSDisabled):
        store.read_chat(bob, bob_session)
    assert store.read_chat(cara, cara_session)["turns"][0]["text"] == "cara-other-customer"
    assert store.read_query_audit(admin, "req-shred-1")["tables"] == ["sem.remit_claims"]
    assert dep.keys["acme_labs"].state == "Enabled"
    assert dep.audit_key.state == "Enabled"
    assert dep.transcript_keys["acme_labs"].state == "Disabled"

    parser = aws.sts.assume_role(
        service_principal("ecs-tasks.amazonaws.com"), None, role_arn(names("acme_labs")["parser"]), "probe",
        SourceContext={"aws:SourceAccount": ACCOUNT,
                       "aws:SourceArn": f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/claimclarity/x"})
    aws.client("s3", parser).put_object(Bucket=BUCKET, Key="acme_labs/staging/probe.txt", Body=b"lake-still-here",
                                        ServerSideEncryption="aws:kms", SSEKMSKeyId=dep.keys["acme_labs"].arn)
    assert aws.client("s3", parser).get_object(Bucket=BUCKET, Key="acme_labs/staging/probe.txt")["Body"].read() == b"lake-still-here"


def test_transcript_lifecycle_expires_content_but_not_the_query_audit(env):
    aws, dep = env
    store = TranscriptStore(aws, dep, mode=PERSISTED)
    ana, admin = login(aws, ANA), login(aws, ADA)
    session_id = store.open_chat(ana)
    store.append_turn(ana, session_id, Turn("user", SENTINEL),
                      QueryAudit("req-life-1", sql="SELECT 1", metric="denial_rate", tables=["sem.claims"],
                                 row_count=0, duration_ms=1))
    prefix = f"acme_labs/{TRANSCRIPTS}/"
    blobs = [row[0] for row in aws.s3.db.execute(
        "SELECT blob FROM objects WHERE bucket=? AND substr(key,1,?)=? AND blob IS NOT NULL AND delete_marker=0 "
        "AND is_latest=1",
        (BUCKET, len(prefix), prefix)).fetchall()]
    assert blobs and all((aws.root / "s3data" / b).exists() for b in blobs)

    aws.clock.advance(TRANSCRIPT_RETENTION_DAYS * 86400)
    aws.s3.run_lifecycle()
    assert all((aws.root / "s3data" / b).exists() for b in blobs)  # delete marker only; bytes are now noncurrent
    aws.clock.advance(86400 + 1)
    aws.s3.run_lifecycle()
    assert all(not (aws.root / "s3data" / b).exists() for b in blobs)
    aws.s3.run_lifecycle()
    admin = login(aws, ADA)  # the previous id token expired with the clock
    listed = aws.client("s3", store._assume_admin(admin, store._claims(admin))).list_object_versions(
        Bucket=BUCKET, Prefix=prefix)
    assert listed["Versions"] == [] and listed["DeleteMarkers"] == []
    assert store.read_query_audit(admin, "req-life-1")["metric"] == "denial_rate"


def test_roles_without_access_are_denied(env):
    aws, dep = env
    store = TranscriptStore(aws, dep, mode=PERSISTED)
    ana, admin = login(aws, ANA), login(aws, ADA)
    ana_sub = sub(aws, ana)
    session_id = store.open_chat(ana)
    text = "what is the denial rate for SSN 123-45-6789 dob 1970-01-01 email pat@lab.example member 12345678901"
    turn_id = store.append_turn(ana, session_id, Turn("user", text))
    content_key = f"acme_labs/{TRANSCRIPTS}/users/{ana_sub}/content/{session_id}/{turn_id}.bin"
    export_id = store.export_deidentified(admin)
    export_key = f"acme_labs/{TRANSCRIPTS}/exports/{export_id}.json"
    exported = json.dumps(store.read_export("acme_labs", export_id))
    for secret in ("123-45-6789", "1970-01-01", "pat@lab.example", "12345678901"):
        assert secret not in exported
    assert "denial rate" in exported
    assert disk_hits(aws.root, b"123-45-6789") == []

    def denied(session, key):
        with pytest.raises(AccessDenied):
            aws.client("s3", session).get_object(Bucket=BUCKET, Key=key)

    reader = aws.sts.assume_role_with_web_identity(dep.reader_role("acme_labs"), "reader", ana, 900)
    denied(reader, content_key)
    user = store._assume_user(ana, store._claims(ana))
    denied(user, "acme_labs/semantic_layer/CURRENT")
    denied(user, export_key)
    denied(store._assume_eval("acme_labs"), content_key)
    denied(store._assume_eval("acme_labs"), f"acme_labs/{TRANSCRIPTS}/users/{ana_sub}/dek.bin")
    denied(store._assume_eval("acme_diagnostics"), export_key)
    support = aws.sts.assume_role(service_principal("ec2.amazonaws.com"), None, role_arn("cc-support"), "support")
    denied(support, content_key)
    api = aws.sts.assume_role(service_principal("ec2.amazonaws.com"), None, role_arn(API_ROLE), "api")
    denied(api, content_key)
    with pytest.raises(AccessDenied):
        aws.sts.assume_role_with_web_identity(role_arn(transcript_names("acme_labs")["audit_writer"]), "forge", ana, 900)
    with pytest.raises(AccessDenied):
        aws.sts.assume_role(support.principal(), support, role_arn("cc-support-breakglass"), "bg-sam", 900,
                            ExternalId="wrong", Tags=[{"Key": "customer", "Value": "acme_labs"},
                                                      {"Key": "reason", "Value": "TICKET-1"},
                                                      {"Key": "operator", "Value": "sam"}])
    with pytest.raises(AccessDenied):
        aws.sts.assume_role(support.principal(), support, role_arn("cc-support-breakglass"), "bg-sam", 900,
                            ExternalId=BREAKGLASS_EXTERNAL_ID, Tags=[{"Key": "customer", "Value": "acme_labs"},
                                                                    {"Key": "reason", "Value": "not-a-ticket"},
                                                                    {"Key": "operator", "Value": "sam"}])

    glass = store.assume_breakglass("acme_labs", "TICKET-1042", "sam")
    assert glass.expiration - glass.issued_at <= 900
    assert "123-45-6789" in store.read_chat_as_breakglass(glass, ana_sub, session_id)["turns"][0]["text"]
    denied(glass, "acme_labs/raw/received_date=2026-01-01/837/x.x12")
    denied(glass, export_key)
    other = store.assume_breakglass("acme_diagnostics", "TICKET-1043", "sam")
    denied(other, content_key)
