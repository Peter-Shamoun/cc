"""The CC account, declared the way Terraform will declare it, and applied to LocalAWS.

Every policy below is plain AWS policy JSON. `python -m ccsim.infra --export DIR` writes them out, one file
per resource, ready to paste into aws_iam_role / aws_s3_bucket_policy / aws_kms_key resources.

Layers of protection for customer C's folder s3://cc-prod/C/
  1. IAM roles per customer (parser / reader / sftp-copy), each scoped to C's prefixes and actions.
  2. Bucket policy: explicit Deny for any principal that is not one of C's roles, for any non-TLS request,
     for PUTs not encrypted with C's KMS key, and for raw/ writes that could overwrite or lack retention.
  3. A KMS key per customer whose key policy grants data use only to C's roles via S3, for objects under C/.
     The account root gets key administration but not Decrypt, so an IAM admin with kms:* cannot read PHI.
  4. Raw is write-once: versioning + Object Lock COMPLIANCE retention on every raw object, If-None-Match
     required on raw PUTs, and deletes of raw denied.
"""
from __future__ import annotations

import json
from pathlib import Path

from .aws import ACCOUNT, REGION, LocalAWS
from .aws.events import Target

BUCKET = "cc-prod"
CLUSTER = "cc"
RAW_RETENTION_DAYS = 2555                        # 7 years (HIPAA documentation rules require >= 6)
SESSION_SECONDS = 900                            # per-request reader credentials


def role_arn(name: str) -> str:
    return f"arn:aws:iam::{ACCOUNT}:role/{name}"


def names(c: str) -> dict:
    return {"parser": f"cc-parser-{c}", "reader": f"cc-reader-{c}", "sftp": f"cc-sftp-copy-{c}"}


def obj(prefix: str) -> str:
    return f"arn:aws:s3:::{BUCKET}/{prefix}"


BUCKET_ARN = f"arn:aws:s3:::{BUCKET}"
V = "2012-10-17"


def bucket_policy(customers: dict[str, str]) -> dict:
    """customers: name -> KMS key ARN"""
    st = [
        {"Sid": "DenyInsecureTransport", "Effect": "Deny", "Principal": "*", "Action": "s3:*",
         "Resource": [BUCKET_ARN, obj("*")], "Condition": {"Bool": {"aws:SecureTransport": "false"}}},
        {"Sid": "DenyListOutsideACustomerPrefix", "Effect": "Deny", "Principal": "*", "Action": "s3:ListBucket",
         "Resource": BUCKET_ARN, "Condition": {"StringNotLike": {"s3:prefix": [f"{c}/*" for c in customers]}}},
        {"Sid": "RawIsWriteOnce", "Effect": "Deny", "Principal": "*", "Action": "s3:PutObject",
         "Resource": obj("*/raw/*"), "Condition": {"Null": {"s3:if-none-match": "true"}}},
        {"Sid": "RawNeedsComplianceRetention", "Effect": "Deny", "Principal": "*", "Action": "s3:PutObject",
         "Resource": obj("*/raw/*"), "Condition": {"StringNotEquals": {"s3:object-lock-mode": "COMPLIANCE"}}},
        {"Sid": "RawIsNeverDeleted", "Effect": "Deny", "Principal": "*",
         "Action": ["s3:DeleteObject", "s3:DeleteObjectVersion"], "Resource": obj("*/raw/*")},
    ]
    for c, key_arn in customers.items():
        arns = [role_arn(n) for n in names(c).values()]
        st += [
            {"Sid": f"Only{c}RolesTouch{c}", "Effect": "Deny", "Principal": "*", "Action": "s3:*",
             "Resource": obj(f"{c}/*"), "Condition": {"StringNotLike": {"aws:PrincipalArn": arns}}},
            {"Sid": f"Only{c}RolesList{c}", "Effect": "Deny", "Principal": "*", "Action": "s3:ListBucket",
             "Resource": BUCKET_ARN, "Condition": {"StringLike": {"s3:prefix": [f"{c}/*", c]},
                                                   "StringNotLike": {"aws:PrincipalArn": arns}}},
            {"Sid": f"{c}ObjectsUse{c}Key", "Effect": "Deny", "Principal": "*", "Action": "s3:PutObject",
             "Resource": obj(f"{c}/*"),
             "Condition": {"StringNotEquals": {"s3:x-amz-server-side-encryption-aws-kms-key-id": key_arn}}},
        ]
    return {"Version": V, "Statement": st}


def key_policy(c: str) -> dict:
    arns = [role_arn(n) for n in names(c).values()]
    return {"Version": V, "Statement": [
        {"Sid": "KeyAdministrationOnly", "Effect": "Allow", "Principal": {"AWS": f"arn:aws:iam::{ACCOUNT}:root"},
         "Action": ["kms:Create*", "kms:Describe*", "kms:Enable*", "kms:List*", "kms:Put*", "kms:Update*",
                    "kms:Revoke*", "kms:Disable*", "kms:Get*", "kms:Delete*", "kms:TagResource",
                    "kms:UntagResource", "kms:ScheduleKeyDeletion", "kms:CancelKeyDeletion", "kms:RotateKeyOnDemand"],
         "Resource": "*"},
        {"Sid": f"DataUseBy{c}RolesThroughS3Only", "Effect": "Allow", "Principal": {"AWS": arns},
         "Action": ["kms:Decrypt", "kms:GenerateDataKey"], "Resource": "*",
         "Condition": {"StringEquals": {"kms:ViaService": f"s3.{REGION}.amazonaws.com"},
                       "StringLike": {"kms:EncryptionContext:aws:s3:arn": obj(f"{c}/*")}}},
    ]}


def sftp_policy(c: str) -> dict:
    return {"Version": V, "Statement": [
        {"Sid": "DropRawFiles", "Effect": "Allow", "Action": ["s3:PutObject", "s3:PutObjectRetention"],
         "Resource": obj(f"{c}/raw/*")}]}


def parser_policy(c: str) -> dict:
    return {"Version": V, "Statement": [
        {"Sid": "ListOwnPrefix", "Effect": "Allow", "Action": "s3:ListBucket", "Resource": BUCKET_ARN,
         "Condition": {"StringLike": {"s3:prefix": [f"{c}/*"]}}},
        {"Sid": "ReadRawAndLayers", "Effect": "Allow", "Action": ["s3:GetObject", "s3:GetObjectVersion"],
         "Resource": [obj(f"{c}/{p}/*") for p in ("raw", "staging", "parquet", "semantic_layer")]},
        {"Sid": "WriteDerivedLayers", "Effect": "Allow", "Action": ["s3:PutObject", "s3:DeleteObject"],
         "Resource": [obj(f"{c}/{p}/*") for p in ("staging", "parquet", "semantic_layer", "quarantine")]},
    ]}


def reader_policy(c: str) -> dict:
    """Gold only. The reader never sees silver or raw: PHI-stripped lossless tables are materialized
    into gold for escalation, so no agent SQL can reach a file containing patient identifiers."""
    return {"Version": V, "Statement": [
        {"Sid": "ListGold", "Effect": "Allow", "Action": "s3:ListBucket", "Resource": BUCKET_ARN,
         "Condition": {"StringLike": {"s3:prefix": [f"{c}/semantic_layer/*"]}}},
        {"Sid": "ReadGold", "Effect": "Allow", "Action": "s3:GetObject", "Resource": obj(f"{c}/semantic_layer/*")},
    ]}


def trust_ecs_tasks() -> dict:
    return {"Version": V, "Statement": [{"Effect": "Allow", "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                                         "Action": "sts:AssumeRole",
                                         "Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT},
                                                       "ArnLike": {"aws:SourceArn": f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/{CLUSTER}/*"}}}]}


def trust_reader(c: str, provider_arn: str, client_id: str) -> dict:
    hp = provider_arn.split("oidc-provider/", 1)[1]
    return {"Version": V, "Statement": [{"Effect": "Allow", "Principal": {"Federated": provider_arn},
                                         "Action": ["sts:AssumeRoleWithWebIdentity", "sts:TagSession"],
                                         "Condition": {"StringEquals": {f"{hp}:aud": client_id,
                                                                        "aws:RequestTag/customer": c}}}]}


def trust_role(arn: str) -> dict:
    return {"Version": V, "Statement": [{"Effect": "Allow", "Principal": {"AWS": arn}, "Action": "sts:AssumeRole"}]}


def events_role_policy(customers) -> dict:
    return {"Version": V, "Statement": [
        {"Sid": "RunParserTasks", "Effect": "Allow", "Action": "ecs:RunTask",
         "Resource": [f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/cc-parser-{c}:*" for c in customers],
         "Condition": {"ArnEquals": {"ecs:cluster": f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/{CLUSTER}"}}},
        {"Sid": "PassParserRoles", "Effect": "Allow", "Action": "iam:PassRole",
         "Resource": [role_arn(names(c)["parser"]) for c in customers],
         "Condition": {"StringEquals": {"iam:PassedToService": "ecs-tasks.amazonaws.com"}}}]}


def success_pattern(c: str) -> dict:
    return {"source": ["aws.s3"], "detail-type": ["Object Created"],
            "detail": {"bucket": {"name": [BUCKET]},
                       "object": {"key": [{"wildcard": f"{c}/raw/received_date=*/_SUCCESS"}]}}}


def lifecycle_rules(customers) -> list[dict]:
    """S3 lifecycle prefixes are literal (no wildcards): one rule per customer layer. Raw has none:
    its versions are under COMPLIANCE retention and are never expired."""
    rules = [{"Prefix": "", "ExpiredObjectDeleteMarker": True}]
    for c in customers:
        rules += [{"Prefix": f"{c}/semantic_layer/", "NoncurrentDays": 1},
                  {"Prefix": f"{c}/staging/", "NoncurrentDays": 1},
                  {"Prefix": f"{c}/parquet/", "NoncurrentDays": 7}]
    return rules


class Deployment:
    """What `terraform apply` produced: ARNs the app and pipeline need."""

    def __init__(self, aws, customers, keys, provider_arn):
        self.aws, self.customers, self.keys, self.provider_arn = aws, list(customers), keys, provider_arn

    def reader_role(self, c):
        return role_arn(names(c)["reader"])


def provision(aws: LocalAWS, customers: list[str], parser_entrypoint, tenant_id: str = "xifin.example",
              app_group: str = "cc-users") -> Deployment:
    provider_arn = aws.create_identity(tenant_id, [app_group])
    keys = {}
    for c in customers:
        keys[c] = aws.kms.create_key(key_policy(c), f"CC data key for {c}", alias=f"alias/cc/{c}")
        n = names(c)
        aws.iam.create_role(n["parser"], trust_ecs_tasks(), max_session_duration=3600)
        aws.iam.put_role_policy(n["parser"], "parser", parser_policy(c))
        aws.iam.create_role(n["reader"], trust_reader(c, provider_arn, aws.cognito.client_id))
        aws.iam.put_role_policy(n["reader"], "reader", reader_policy(c))
        aws.iam.create_role(n["sftp"], trust_role(role_arn("it-sftp-runner")))
        aws.iam.put_role_policy(n["sftp"], "sftp", sftp_policy(c))
    # IT's SFTP host (an EC2 instance profile) may assume each customer's copy role
    aws.iam.create_role("it-sftp-runner", {"Version": V, "Statement": [
        {"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]})
    aws.iam.put_role_policy("it-sftp-runner", "assume-copy-roles", {"Version": V, "Statement": [
        {"Effect": "Allow", "Action": "sts:AssumeRole", "Resource": [role_arn(names(c)["sftp"]) for c in customers]}]})

    b = aws.s3.create_bucket(BUCKET, versioning="Enabled", object_lock=True, eventbridge=True)
    b.encryption = {"SSEAlgorithm": "aws:kms",
                    "KMSMasterKeyID": aws.kms.create_key({"Version": V, "Statement": [
                        {"Effect": "Allow", "Principal": {"AWS": f"arn:aws:iam::{ACCOUNT}:root"}, "Action": "kms:*",
                         "Resource": "*"}]}, "bucket default (never valid for customer prefixes)",
                        alias="alias/cc/platform").arn,
                    "BucketKeyEnabled": False}
    b.policy = bucket_policy({c: keys[c].arn for c in customers})
    b.lifecycle = lifecycle_rules(customers)

    aws.iam.create_role("cc-events-run-parser", {"Version": V, "Statement": [
        {"Effect": "Allow", "Principal": {"Service": "events.amazonaws.com"}, "Action": "sts:AssumeRole",
         "Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT}}}]})
    aws.iam.put_role_policy("cc-events-run-parser", "run", events_role_policy(customers))
    for c in customers:
        td = aws.ecs.register_task_definition(f"cc-parser-{c}", role_arn(names(c)["parser"]), parser_entrypoint,
                                              environment={"CUSTOMER": c, "BUCKET": BUCKET, "KMS_KEY_ARN": keys[c].arn})
        rule = aws.events.put_rule(f"cc-raw-success-{c}", success_pattern(c))

        def invoke(evt, session, family=td.family):
            # InputTransformer equivalent: pass the marker's key to the container
            return aws.ecs.run_task(session, family, cluster=CLUSTER, startedBy="events-rule",
                                    overrides={"containerOverrides": [{"name": "parser", "environment": [
                                        {"name": "SUCCESS_KEY", "value": evt["detail"]["object"]["key"]},
                                        {"name": "EVENT_ID", "value": evt["id"]}]}]})
        aws.events.put_targets(rule.name, Target(f"parser-{c}", invoke, role_arn("cc-events-run-parser")))
    return Deployment(aws, customers, keys, provider_arn)


def sftp_session(aws: LocalAWS, c: str):
    """IT's host credentials (instance profile) -> chained into the customer's copy role."""
    from .aws.sts import service_principal
    host = aws.sts.assume_role(service_principal("ec2.amazonaws.com"), None, role_arn("it-sftp-runner"), "sftp-host")
    return aws.sts.assume_role(host.principal(), host, role_arn(names(c)["sftp"]), f"sftp-copy-{c}"[:64], 900)


def sftp_drop(aws: LocalAWS, dep: Deployment, c: str, local_day_dir, day: str, via_http: bool = True):
    """IT's daily copy: every file for one received day, write-once with retention, then _SUCCESS."""
    s = sftp_session(aws, c)
    s3 = aws.boto3_client("s3", s) if via_http else aws.client("s3", s)
    retain = aws.clock() + RAW_RETENTION_DAYS * 86400
    from datetime import datetime, timezone
    kw = dict(ServerSideEncryption="aws:kms", SSEKMSKeyId=dep.keys[c].arn, IfNoneMatch="*", ObjectLockMode="COMPLIANCE",
              ObjectLockRetainUntilDate=datetime.fromtimestamp(retain, timezone.utc) if via_http else retain)
    for f in sorted(Path(local_day_dir).rglob("*")):
        if f.is_file():
            rel = f.relative_to(local_day_dir).as_posix()
            s3.put_object(Bucket=BUCKET, Key=f"{c}/raw/received_date={day}/{rel}", Body=f.read_bytes(), **kw)
    s3.put_object(Bucket=BUCKET, Key=f"{c}/raw/received_date={day}/_SUCCESS", Body=b"", **kw)


def export(dest: str | Path, customers=("acme_labs", "acme_diagnostics")):
    """Write every policy document as JSON (placeholders for ARNs that only exist after apply)."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    keys = {c: f"arn:aws:kms:{REGION}:{ACCOUNT}:key/<{c}-key-id>" for c in customers}
    provider = f"arn:aws:iam::{ACCOUNT}:oidc-provider/cognito-idp.{REGION}.amazonaws.com/<user-pool-id>"
    docs = {"s3_bucket_policy.json": bucket_policy(keys), "events_run_parser_policy.json": events_role_policy(customers),
            "trust_ecs_tasks.json": trust_ecs_tasks(), "lifecycle_rules.json": lifecycle_rules(customers)}
    for c in customers:
        docs[f"kms_key_policy_{c}.json"] = key_policy(c)
        docs[f"role_{names(c)['parser']}.json"] = parser_policy(c)
        docs[f"role_{names(c)['reader']}.json"] = reader_policy(c)
        docs[f"role_{names(c)['sftp']}.json"] = sftp_policy(c)
        docs[f"trust_{names(c)['reader']}.json"] = trust_reader(c, provider, "<app-client-id>")
        docs[f"events_pattern_{c}.json"] = success_pattern(c)
    for name, d in docs.items():
        (dest / name).write_text(json.dumps(d, indent=2) + "\n")
    return sorted(docs)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="export policy JSON for Terraform")
    ap.add_argument("--export", required=True)
    print("\n".join(export(ap.parse_args().export)))
