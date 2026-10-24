"""The account as Terraform would build it, applied to LocalAWS. Each block maps to a resource type
(aws_kms_key, aws_iam_role, aws_s3_bucket_policy, aws_s3_bucket_object_lock_configuration,
aws_cloudwatch_event_rule + target, aws_ecs_task_definition, aws_cognito_user_pool)."""
from __future__ import annotations

from .aws import IdentityPlane, LocalAWS

BUCKET = "claimclarity-prod"


def _arn(prefix: str) -> str:
    return f"arn:aws:s3:::{BUCKET}/{prefix}"


def provision(aws: LocalAWS, customers: list[str], parser_entrypoint) -> IdentityPlane:
    role_arn = lambda n: f"arn:aws:iam::111122223333:role/{n}"
    allowed_by_prefix = []
    keys = {}
    for c in customers:
        parser, reader, sftp = f"cc-parser-{c}", f"cc-reader-{c}", f"cc-sftp-copy-{c}"
        allowed_by_prefix.append((c, [role_arn(parser), role_arn(reader), role_arn(sftp)]))
        keys[c] = aws.create_key(f"alias/claimclarity/{c}", {"Statement": [
            {"Effect": "Allow", "Principal": [role_arn(parser), role_arn(reader), role_arn(sftp)],
             "Action": ["kms:Decrypt", "kms:GenerateDataKey"], "Resource": "*"},
            {"Effect": "Deny", "NotPrincipal": [role_arn(parser), role_arn(reader), role_arn(sftp)],
             "Action": "kms:*", "Resource": "*"}]})
        # SFTP copy script (IT): may only add raw files
        aws.create_role(sftp, ["it-sftp-script"], [{"Statement": [
            {"Effect": "Allow", "Action": ["s3:PutObject"], "Resource": _arn(f"{c}/raw/*")},
            {"Effect": "Allow", "Action": ["kms:GenerateDataKey"], "Resource": "*"}]}])
        # Fargate parser: read raw, write everything except raw, this customer only
        aws.create_role(parser, ["ecs-tasks.amazonaws.com"], [{"Statement": [
            {"Effect": "Allow", "Action": ["s3:GetObject", "s3:ListBucket"], "Resource": _arn(f"{c}/*")},
            {"Effect": "Allow", "Action": ["s3:PutObject", "s3:DeleteObject"],
             "Resource": [_arn(f"{c}/{p}/*") for p in ("staging", "parquet", "semantic_layer", "quarantine")]},
            {"Effect": "Allow", "Action": ["kms:Decrypt", "kms:GenerateDataKey"], "Resource": "*"}]}])
        # App server, on behalf of a logged-in user of this customer: read-only, gold (+ silver for escalation)
        aws.create_role(reader, ["app-server"], [{"Statement": [
            {"Effect": "Allow", "Action": ["s3:GetObject", "s3:ListBucket"],
             "Resource": [_arn(f"{c}/semantic_layer/*"), _arn(f"{c}/parquet/*")]},
            {"Effect": "Allow", "Action": ["kms:Decrypt"], "Resource": "*"}]}])
    # Bucket policy: each customer prefix refuses every principal except that customer's roles
    stmts = [{"Effect": "Deny", "NotPrincipal": roles, "Action": "s3:*", "Resource": _arn(f"{c}/*")}
             for c, roles in allowed_by_prefix]
    aws.create_bucket(BUCKET, {"Statement": stmts}, object_lock_globs=[f"{c}/raw/*" for c in customers])
    for c in customers:
        aws.set_default_encryption(BUCKET, f"{c}/*", keys[c].key_id)

    # EventBridge: _SUCCESS under <customer>/raw/ -> one parser task for that customer and day
    for c in customers:
        aws.register_task_definition(f"cc-parser-{c}", role_arn(f"cc-parser-{c}"), parser_entrypoint)

        def target(evt, c=c):
            key = evt["detail"]["object"]["key"]
            day = key.split("/")[2].split("=", 1)[1]
            aws.run_task(f"cc-parser-{c}", {"customer": c, "received_date": day, "bucket": BUCKET})
        aws.put_rule(f"cc-raw-success-{c}", {"source": "aws.s3", "detail-type": "Object Created",
                                             "detail": {"bucket": {"name": BUCKET},
                                                        "object": {"key": [{"prefix": f"{c}/raw/"}]}}},
                     lambda evt, t=target: evt["detail"]["object"]["key"].endswith("/_SUCCESS") and t(evt))

    idp = IdentityPlane(aws, entra_tenant="xifin.example", allowed_group="claimclarity-users")
    for c in customers:
        idp.customer_roles[c] = role_arn(f"cc-reader-{c}")
    return idp


def sftp_drop(aws: LocalAWS, customer: str, local_raw_day, day: str):
    """IT's SFTP->S3 copy for one day folder, then the _SUCCESS marker (fires EventBridge)."""
    from pathlib import Path
    creds = aws.assume_role("it-sftp-script", f"arn:aws:iam::111122223333:role/cc-sftp-copy-{customer}", f"sftp-{day}")
    s3 = aws.client("s3", creds)
    for f in sorted(Path(local_raw_day).rglob("*.x12")):
        rel = f.relative_to(local_raw_day)
        s3.put_object(Bucket=BUCKET, Key=f"{customer}/raw/received_date={day}/{rel}", Body=f.read_bytes())
    s3.put_object(Bucket=BUCKET, Key=f"{customer}/raw/received_date={day}/_SUCCESS", Body=b"")
    aws.revoke(creds)
