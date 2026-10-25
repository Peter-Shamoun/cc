"""The ClaimClarity account, declared the way Terraform will declare it, and applied to LocalAWS.

Every policy below is plain AWS policy JSON. `python -m ccsim.infra --export DIR` writes them out, one file
per resource, ready to paste into aws_iam_role / aws_s3_bucket_policy / aws_kms_key resources.

Layers of protection for customer C's folder s3://claimclarity-prod/C/
  1. IAM roles per customer (parser / reader / sftp-copy), each scoped to C's prefixes and actions and tagged
     customer=C, kms_key_arn=<C's key>.
  2. Bucket policy (ABAC, constant size for any number of customers): object access only under
     ${aws:PrincipalTag/customer}/, listing only with that prefix, PUTs only with that principal's KMS key, TLS
     only, raw/ write-once with >= 7 years COMPLIANCE retention, raw never deleted, and bucket configuration
     (policy, lifecycle, versioning, Object Lock, encryption) changeable only by the Terraform role.
  3. A KMS key per customer. Its key policy allows Decrypt / GenerateDataKey only for C's roles, only through
     S3, and only for objects under C/. Key administration goes to a dedicated key-admin role, which gets
     neither data use nor CreateGrant nor PutKeyPolicy. PutKeyPolicy is for the Terraform role only.
     Consequence: an IAM admin with "*:*" can neither read C's objects nor change the policies that guard them.
     The Terraform role is the trusted path. Protect it with an SCP and CloudTrail alerts, outside this account model.
  4. Raw is write-once: versioning plus Object Lock COMPLIANCE retention on every raw object, If-None-Match
     required on raw PUTs (multipart parts exempt via s3:ObjectCreationOperation), and deletes of raw denied.
  5. Transcripts (Design B) use a second per-customer KMS key and the prefix transcripts/. The lake key's
     principals are still only parser, reader, and sftp. Query-audit objects under audit/queries/ use a
     separate key and are write-once for 6 years, in both transcript designs. See
     docs/data-backend/chat-transcripts.md and ccsim/transcripts.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from .aws import ACCOUNT, REGION, LocalAWS
from .aws.events import Target

BUCKET = "claimclarity-prod"
CLUSTER = "claimclarity"
RAW_RETENTION_DAYS = 2555                        # 7 years (HIPAA documentation retention is >= 6)
SESSION_SECONDS = 900                            # per-request reader credentials
TERRAFORM_ROLE = "cc-terraform"                  # the deploy pipeline
KEY_ADMIN_ROLE = "cc-key-admin"                  # humans who rotate / disable keys; no data-plane use
API_ROLE = "cc-api"                              # the app server. Writes the query audit. Cannot read PHI.
SUPPORT_ROLE = "cc-support"                      # support staff. Can only assume the break-glass role.
BREAKGLASS_ROLE = "cc-support-breakglass"        # read one customer's transcripts, for one ticket
EVAL_TEAM_ROLE = "cc-eval-team"                  # assumes a per-customer eval role. Exports only.
BREAKGLASS_EXTERNAL_ID = "claimclarity-support-breakglass"
BREAKGLASS_SESSION_SECONDS = 900                 # requested duration. AWS floors MaxSessionDuration at 3600.
TRANSCRIPT_RETENTION_DAYS = 90                   # Design B content, metadata, and de-identified exports
AUDIT_RETENTION_DAYS = 2190                      # 6 years. Query audit, both designs. Not the transcript.
TRANSCRIPTS = "transcripts"                      # {customer}/transcripts/...
AUDIT_QUERIES = "audit/queries"                  # {customer}/audit/queries/YYYY-MM-DD/<request_id>.json


def role_arn(name: str) -> str:
    return f"arn:aws:iam::{ACCOUNT}:role/{name}"


def names(c: str) -> dict:
    """Lake roles. Not the transcript roles: those must not be principals on the lake key."""
    return {"parser": f"cc-parser-{c}", "reader": f"cc-reader-{c}", "sftp": f"cc-sftp-copy-{c}"}


def transcript_names(c: str) -> dict:
    return {"user": f"cc-transcript-user-{c}", "admin": f"cc-transcript-admin-{c}",
            "eval": f"cc-eval-{c}", "audit_writer": f"cc-audit-writer-{c}", "audit_reader": f"cc-audit-reader-{c}"}


def obj(prefix: str) -> str:
    return f"arn:aws:s3:::{BUCKET}/{prefix}"


BUCKET_ARN = f"arn:aws:s3:::{BUCKET}"
V = "2012-10-17"
OBJECT_ACTIONS = ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion",
                  "s3:PutObjectRetention", "s3:PutObjectLegalHold", "s3:BypassGovernanceRetention"]
BUCKET_ADMIN_ACTIONS = ["s3:PutBucketPolicy", "s3:DeleteBucketPolicy", "s3:PutLifecycleConfiguration",
                        "s3:PutBucketVersioning", "s3:PutBucketObjectLockConfiguration", "s3:PutEncryptionConfiguration"]


def bucket_policy() -> dict:
    """Same document for 2 or 2,000 customers: isolation comes from each role's customer / kms_key_arn tags."""
    terraform = role_arn(TERRAFORM_ROLE)
    return {"Version": V, "Statement": [
        {"Sid": "DenyInsecureTransport", "Effect": "Deny", "Principal": "*", "Action": "s3:*",
         "Resource": [BUCKET_ARN, obj("*")], "Condition": {"Bool": {"aws:SecureTransport": "false"}}},
        {"Sid": "OnlyTerraformChangesBucketConfig", "Effect": "Deny", "Principal": "*", "Action": BUCKET_ADMIN_ACTIONS,
         "Resource": BUCKET_ARN, "Condition": {"StringNotEquals": {"aws:PrincipalArn": terraform}}},
        {"Sid": "DenyPrincipalsWithoutACustomer", "Effect": "Deny", "Principal": "*", "Action": "s3:*",
         "Resource": [BUCKET_ARN, obj("*")],
         "Condition": {"Null": {"aws:PrincipalTag/customer": "true"}, "StringNotEquals": {"aws:PrincipalArn": terraform}}},
        {"Sid": "ObjectsOnlyUnderOwnCustomer", "Effect": "Deny", "Principal": "*", "Action": OBJECT_ACTIONS,
         "NotResource": obj("${aws:PrincipalTag/customer}/*")},
        {"Sid": "ListOnlyOwnCustomerPrefix", "Effect": "Deny", "Principal": "*",
         "Action": ["s3:ListBucket", "s3:ListBucketVersions"], "Resource": BUCKET_ARN,
         "Condition": {"StringNotLike": {"s3:prefix": "${aws:PrincipalTag/customer}/*"},
                       "StringNotEquals": {"aws:PrincipalArn": terraform}}},
        {"Sid": "PutsUseOwnCustomerKey", "Effect": "Deny", "Principal": "*", "Action": "s3:PutObject", "Resource": obj("*"),
         "Condition": {"StringNotEquals": {"s3:x-amz-server-side-encryption-aws-kms-key-id": "${aws:PrincipalTag/kms_key_arn}"}}},
        {"Sid": "RawIsWriteOnce", "Effect": "Deny", "Principal": "*", "Action": "s3:PutObject", "Resource": obj("*/raw/*"),
         "Condition": {"Null": {"s3:if-none-match": "true"}, "Bool": {"s3:ObjectCreationOperation": "true"}}},
        {"Sid": "RawNeedsComplianceRetention", "Effect": "Deny", "Principal": "*", "Action": "s3:PutObject",
         "Resource": obj("*/raw/*"), "Condition": {"StringNotEquals": {"s3:object-lock-mode": "COMPLIANCE"}}},
        {"Sid": "RawRetentionAtLeastSevenYears", "Effect": "Deny", "Principal": "*", "Action": "s3:PutObject",
         "Resource": obj("*/raw/*"),
         "Condition": {"NumericLessThan": {"s3:object-lock-remaining-retention-days": str(RAW_RETENTION_DAYS - 1)}}},
        {"Sid": "RawIsNeverDeleted", "Effect": "Deny", "Principal": "*",
         "Action": ["s3:DeleteObject", "s3:DeleteObjectVersion"], "Resource": obj("*/raw/*")},
        # Query audit is the application record both transcript designs keep. Same write-once shape as raw,
        # for 6 years rather than 7. Transcript objects are not under this prefix, so a user can still delete chats.
        {"Sid": "AuditIsWriteOnce", "Effect": "Deny", "Principal": "*", "Action": "s3:PutObject",
         "Resource": obj(f"*/{AUDIT_QUERIES}/*"),
         "Condition": {"Null": {"s3:if-none-match": "true"}, "Bool": {"s3:ObjectCreationOperation": "true"}}},
        {"Sid": "AuditNeedsComplianceRetention", "Effect": "Deny", "Principal": "*", "Action": "s3:PutObject",
         "Resource": obj(f"*/{AUDIT_QUERIES}/*"),
         "Condition": {"StringNotEquals": {"s3:object-lock-mode": "COMPLIANCE"}}},
        {"Sid": "AuditRetentionAtLeastSixYears", "Effect": "Deny", "Principal": "*", "Action": "s3:PutObject",
         "Resource": obj(f"*/{AUDIT_QUERIES}/*"),
         "Condition": {"NumericLessThan": {"s3:object-lock-remaining-retention-days": str(AUDIT_RETENTION_DAYS - 1)}}},
        {"Sid": "AuditIsNeverDeleted", "Effect": "Deny", "Principal": "*",
         "Action": ["s3:DeleteObject", "s3:DeleteObjectVersion"], "Resource": obj(f"*/{AUDIT_QUERIES}/*")},
    ]}


def _key_admin_statements() -> list[dict]:
    """Terraform owns the key policy. Key admins can disable a key (crypto-shred) and cannot decrypt, grant, or
    rewrite the policy. Shared by the lake key, the transcript key, and the audit key."""
    return [
        {"Sid": "TerraformManagesTheKeyPolicy", "Effect": "Allow", "Principal": {"AWS": role_arn(TERRAFORM_ROLE)},
         "Action": ["kms:PutKeyPolicy", "kms:GetKeyPolicy", "kms:DescribeKey", "kms:TagResource", "kms:UntagResource",
                    "kms:EnableKeyRotation", "kms:ScheduleKeyDeletion", "kms:CancelKeyDeletion"], "Resource": "*"},
        {"Sid": "KeyAdministrationWithoutDataAccess", "Effect": "Allow", "Principal": {"AWS": role_arn(KEY_ADMIN_ROLE)},
         "Action": ["kms:Describe*", "kms:Get*", "kms:List*", "kms:EnableKey", "kms:DisableKey", "kms:EnableKeyRotation",
                    "kms:DisableKeyRotation", "kms:RotateKeyOnDemand", "kms:ScheduleKeyDeletion", "kms:CancelKeyDeletion"],
         "Resource": "*"},
    ]


def key_policy(c: str) -> dict:
    """Lake key. Transcript roles are not principals: disabling this key must not be required to shred chats,
    and a transcript role must not be able to decrypt raw, silver, or gold."""
    arns = [role_arn(n) for n in names(c).values()]
    return {"Version": V, "Statement": _key_admin_statements() + [
        {"Sid": f"DataUseBy{c}RolesThroughS3Only", "Effect": "Allow", "Principal": {"AWS": arns},
         "Action": ["kms:Decrypt", "kms:GenerateDataKey"], "Resource": "*",
         "Condition": {"StringEquals": {"kms:ViaService": f"s3.{REGION}.amazonaws.com"},
                       "StringLike": {"kms:EncryptionContext:aws:s3:arn": obj(f"{c}/*")}}},
    ]}


def _via_s3(arn_patterns) -> dict:
    return {"StringEquals": {"kms:ViaService": f"s3.{REGION}.amazonaws.com"},
            "StringLike": {"kms:EncryptionContext:aws:s3:arn": arn_patterns}}


def transcript_key_policy(c: str) -> dict:
    """Per-customer key for transcript objects only. SSE-KMS goes through S3. The per-user data key is a direct
    GenerateDataKey/Decrypt (not ViaService) bound to encryption context purpose=transcript-dek, because the
    application encrypts message bodies before they ever reach S3. Break-glass may decrypt, not generate."""
    n = transcript_names(c)
    user, admin, eval_role = role_arn(n["user"]), role_arn(n["admin"]), role_arn(n["eval"])
    breakglass = role_arn(BREAKGLASS_ROLE)
    own_user = obj(f"{c}/{TRANSCRIPTS}/users/${{aws:PrincipalTag/user}}/*")
    any_user = obj(f"{c}/{TRANSCRIPTS}/users/*")
    dek = {"kms:EncryptionContext:customer": "${aws:PrincipalTag/customer}",
           "kms:EncryptionContext:purpose": "transcript-dek"}
    return {"Version": V, "Statement": _key_admin_statements() + [
        {"Sid": "UserS3OwnTranscripts", "Effect": "Allow", "Principal": {"AWS": user},
         "Action": ["kms:Decrypt", "kms:GenerateDataKey"], "Resource": "*", "Condition": _via_s3(own_user)},
        {"Sid": "AdminS3TranscriptPlane", "Effect": "Allow", "Principal": {"AWS": admin},
         "Action": ["kms:Decrypt", "kms:GenerateDataKey"], "Resource": "*",
         "Condition": _via_s3([any_user, obj(f"{c}/{TRANSCRIPTS}/exports/*"), obj(f"{c}/{TRANSCRIPTS}/admin/*")])},
        {"Sid": "EvalS3ExportsOnly", "Effect": "Allow", "Principal": {"AWS": eval_role},
         "Action": "kms:Decrypt", "Resource": "*", "Condition": _via_s3(obj(f"{c}/{TRANSCRIPTS}/exports/*"))},
        {"Sid": "BreakglassS3ReadsUsers", "Effect": "Allow", "Principal": {"AWS": breakglass},
         "Action": "kms:Decrypt", "Resource": "*", "Condition": _via_s3(any_user)},
        {"Sid": "UserUnwrapsOnlyOwnDataKey", "Effect": "Allow", "Principal": {"AWS": user},
         "Action": ["kms:Decrypt", "kms:GenerateDataKey"], "Resource": "*",
         "Condition": {"StringEquals": {**dek, "kms:EncryptionContext:user": "${aws:PrincipalTag/user}"}}},
        {"Sid": "AdminUnwrapsCustomerDataKeys", "Effect": "Allow", "Principal": {"AWS": admin},
         "Action": ["kms:Decrypt", "kms:GenerateDataKey"], "Resource": "*", "Condition": {"StringEquals": dek}},
        {"Sid": "BreakglassUnwrapsCustomerDataKeys", "Effect": "Allow", "Principal": {"AWS": breakglass},
         "Action": "kms:Decrypt", "Resource": "*", "Condition": {"StringEquals": dek}},
    ]}


def audit_key_policy(customers) -> dict:
    """One platform key. Each statement is one customer, so customer A's writer cannot decrypt customer B's
    audit objects even if an identity policy were wrong. Not the transcript key: shredding chats must leave
    the query audit readable."""
    statements = _key_admin_statements()
    for c in customers:
        n = transcript_names(c)
        pattern = obj(f"{c}/{AUDIT_QUERIES}/*")
        statements.append({"Sid": f"AuditWrite{c}", "Effect": "Allow", "Principal": {"AWS": role_arn(n["audit_writer"])},
                           "Action": ["kms:Decrypt", "kms:GenerateDataKey"], "Resource": "*",
                           "Condition": _via_s3(pattern)})
        statements.append({"Sid": f"AuditRead{c}", "Effect": "Allow", "Principal": {"AWS": role_arn(n["audit_reader"])},
                           "Action": "kms:Decrypt", "Resource": "*", "Condition": _via_s3(pattern)})
    return {"Version": V, "Statement": statements}


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
    into gold for escalation, so no agent SQL can reach a file containing patient identifiers.
    Transcripts are a different role. The analyst session must not be able to read or write them."""
    return {"Version": V, "Statement": [
        {"Sid": "ListGold", "Effect": "Allow", "Action": "s3:ListBucket", "Resource": BUCKET_ARN,
         "Condition": {"StringLike": {"s3:prefix": [f"{c}/semantic_layer/*"]}}},
        {"Sid": "ReadGold", "Effect": "Allow", "Action": "s3:GetObject", "Resource": obj(f"{c}/semantic_layer/*")},
    ]}


def transcript_user_policy(c: str) -> dict:
    """End user: only their own prefix. The user id is the Cognito sub session tag, not a request parameter."""
    prefix = f"{c}/{TRANSCRIPTS}/users/${{aws:PrincipalTag/user}}"
    return {"Version": V, "Statement": [
        {"Sid": "ListOwnTranscripts", "Effect": "Allow", "Action": ["s3:ListBucket", "s3:ListBucketVersions"],
         "Resource": BUCKET_ARN, "Condition": {"StringLike": {"s3:prefix": [f"{prefix}/*", f"{prefix}/"]}}},
        {"Sid": "ReadWriteDeleteOwnTranscripts", "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion"],
         "Resource": obj(f"{prefix}/*")},
    ]}


def transcript_admin_policy(c: str) -> dict:
    """Customer admin: every user under this customer, plus de-identified exports and the export salt.
    No lake prefixes and no query-audit writes."""
    root = f"{c}/{TRANSCRIPTS}"
    return {"Version": V, "Statement": [
        {"Sid": "ListTranscripts", "Effect": "Allow", "Action": ["s3:ListBucket", "s3:ListBucketVersions"],
         "Resource": BUCKET_ARN, "Condition": {"StringLike": {"s3:prefix": [f"{root}/*", f"{root}/"]}}},
        {"Sid": "ReadAndDeleteUserTranscripts", "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:GetObjectVersion", "s3:DeleteObject", "s3:DeleteObjectVersion"],
         "Resource": obj(f"{root}/users/*")},
        {"Sid": "WriteExportsAndSalts", "Effect": "Allow",
         "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:DeleteObjectVersion"],
         "Resource": [obj(f"{root}/exports/*"), obj(f"{root}/admin/*")]},
    ]}


def eval_export_policy(c: str) -> dict:
    """Eval team: de-identified export objects only. Not content, not metadata, not the salt, not the lake."""
    prefix = f"{c}/{TRANSCRIPTS}/exports"
    return {"Version": V, "Statement": [
        {"Sid": "ListExports", "Effect": "Allow", "Action": "s3:ListBucket", "Resource": BUCKET_ARN,
         "Condition": {"StringLike": {"s3:prefix": [f"{prefix}/*", f"{prefix}/"]}}},
        {"Sid": "ReadExports", "Effect": "Allow", "Action": "s3:GetObject", "Resource": obj(f"{prefix}/*")},
    ]}


def audit_writer_policy(c: str) -> dict:
    """Append-only. No Get, no List, no Delete: a writer cannot read back or replace a record (the bucket
    policy also denies a PUT without If-None-Match, and denies deletes)."""
    return {"Version": V, "Statement": [
        {"Sid": "AppendQueryAudit", "Effect": "Allow", "Action": ["s3:PutObject", "s3:PutObjectRetention"],
         "Resource": obj(f"{c}/{AUDIT_QUERIES}/*")},
    ]}


def audit_reader_policy(c: str) -> dict:
    return {"Version": V, "Statement": [
        {"Sid": "ListQueryAudit", "Effect": "Allow", "Action": "s3:ListBucket", "Resource": BUCKET_ARN,
         "Condition": {"StringLike": {"s3:prefix": [f"{c}/{AUDIT_QUERIES}/*", f"{c}/{AUDIT_QUERIES}/"]}}},
        {"Sid": "ReadQueryAudit", "Effect": "Allow", "Action": "s3:GetObject",
         "Resource": obj(f"{c}/{AUDIT_QUERIES}/*")},
    ]}


def breakglass_policy() -> dict:
    """One role for every customer. The session tag picked at AssumeRole chooses the prefix. Read only."""
    prefix = f"${{aws:PrincipalTag/customer}}/{TRANSCRIPTS}/users"
    return {"Version": V, "Statement": [
        {"Sid": "ListThisCustomersTranscripts", "Effect": "Allow",
         "Action": ["s3:ListBucket", "s3:ListBucketVersions"], "Resource": BUCKET_ARN,
         "Condition": {"StringLike": {"s3:prefix": [f"{prefix}/*", f"{prefix}/"]}}},
        {"Sid": "ReadThisCustomersTranscriptObjects", "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:GetObjectVersion"], "Resource": obj(f"{prefix}/*")},
    ]}


def api_policy(customers) -> dict:
    """The server writes query-audit records and nothing else. End-user tokens cannot assume this role."""
    return {"Version": V, "Statement": [
        {"Sid": "AssumeAuditWriters", "Effect": "Allow", "Action": ["sts:AssumeRole", "sts:TagSession"],
         "Resource": [role_arn(transcript_names(c)["audit_writer"]) for c in customers]}]}


def support_policy() -> dict:
    return {"Version": V, "Statement": [
        {"Sid": "AssumeBreakglass", "Effect": "Allow", "Action": ["sts:AssumeRole", "sts:TagSession"],
         "Resource": role_arn(BREAKGLASS_ROLE)}]}


def eval_team_policy(customers) -> dict:
    return {"Version": V, "Statement": [
        {"Sid": "AssumeEvalRoles", "Effect": "Allow", "Action": "sts:AssumeRole",
         "Resource": [role_arn(transcript_names(c)["eval"]) for c in customers]}]}


def trust_ecs_tasks() -> dict:
    # ECS supports aws:SourceArn only as a wildcard over the account's ECS resources (not a specific cluster)
    return {"Version": V, "Statement": [{"Effect": "Allow", "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                                         "Action": "sts:AssumeRole",
                                         "Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT},
                                                       "ArnLike": {"aws:SourceArn": f"arn:aws:ecs:{REGION}:{ACCOUNT}:*"}}}]}


def trust_reader(c: str, provider_arn: str, client_id: str) -> dict:
    hp = provider_arn.split("oidc-provider/", 1)[1]
    return {"Version": V, "Statement": [{"Effect": "Allow", "Principal": {"Federated": provider_arn},
                                         "Action": ["sts:AssumeRoleWithWebIdentity", "sts:TagSession"],
                                         "Condition": {"StringEquals": {f"{hp}:aud": client_id,
                                                                        "aws:RequestTag/customer": c}}}]}


def trust_transcript_admin(c: str, provider_arn: str, client_id: str) -> dict:
    """Same as the reader trust, plus the admin session tag. A user token without it does not match."""
    doc = trust_reader(c, provider_arn, client_id)
    doc["Statement"][0]["Condition"]["StringEquals"]["aws:RequestTag/transcript_admin"] = "true"
    return doc


def trust_audit_writer(c: str) -> dict:
    """The API server, not the end user. The customer tag has to match this role or the assume fails."""
    return {"Version": V, "Statement": [{"Effect": "Allow", "Principal": {"AWS": role_arn(API_ROLE)},
                                         "Action": ["sts:AssumeRole", "sts:TagSession"],
                                         "Condition": {"StringEquals": {"aws:RequestTag/customer": c},
                                                       "Null": {"aws:RequestTag/user": "false"}}}]}


def trust_breakglass() -> dict:
    return {"Version": V, "Statement": [{"Effect": "Allow", "Principal": {"AWS": role_arn(SUPPORT_ROLE)},
                                         "Action": ["sts:AssumeRole", "sts:TagSession"],
                                         "Condition": {"StringEquals": {"sts:ExternalId": BREAKGLASS_EXTERNAL_ID},
                                                       "StringLike": {"aws:RequestTag/reason": "TICKET-*"},
                                                       "Null": {"aws:RequestTag/customer": "false",
                                                                "aws:RequestTag/operator": "false"}}}]}


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
    """Internal form (the sim's lifecycle runner). S3 lifecycle prefixes are literal (no wildcards): one rule per
    customer layer. Raw has none: its versions are under COMPLIANCE retention and are never expired."""
    rules = [{"Prefix": "", "ExpiredObjectDeleteMarker": True}]
    for c in customers:
        rules += [{"Prefix": f"{c}/semantic_layer/", "NoncurrentDays": 1},
                  {"Prefix": f"{c}/staging/", "NoncurrentDays": 1},
                  {"Prefix": f"{c}/parquet/", "NoncurrentDays": 7},
                  # Chats, metadata, and exports. Current version becomes a delete marker at 90 days; the bytes
                  # of noncurrent versions go a day later. Audit is not in this prefix.
                  {"Prefix": f"{c}/{TRANSCRIPTS}/", "ExpirationDays": TRANSCRIPT_RETENTION_DAYS, "NoncurrentDays": 1}]
    return rules


def lifecycle_configuration(customers) -> dict:
    """The same rules in S3's PutBucketLifecycleConfiguration shape (what Terraform's
    aws_s3_bucket_lifecycle_configuration takes)."""
    out = []
    for i, r in enumerate(lifecycle_rules(customers)):
        rule = {"ID": f"rule-{i}-{r['Prefix'].rstrip('/').replace('/', '-') or 'all'}", "Status": "Enabled",
                "Filter": {"Prefix": r["Prefix"]}}
        if "NoncurrentDays" in r:
            rule["NoncurrentVersionExpiration"] = {"NoncurrentDays": r["NoncurrentDays"]}
        if "ExpirationDays" in r:
            rule.setdefault("Expiration", {})["Days"] = r["ExpirationDays"]
        if r.get("ExpiredObjectDeleteMarker"):
            rule.setdefault("Expiration", {})["ExpiredObjectDeleteMarker"] = True
        out.append(rule)
    return {"Rules": out}


class Deployment:
    """What `terraform apply` produced: ARNs the app and pipeline need."""

    def __init__(self, aws, customers, keys, provider_arn, transcript_keys=None, audit_key=None):
        self.aws, self.customers, self.keys, self.provider_arn = aws, list(customers), keys, provider_arn
        self.transcript_keys = transcript_keys or {}
        self.audit_key = audit_key

    def reader_role(self, c):
        return role_arn(names(c)["reader"])


def provision(aws: LocalAWS, customers: list[str], parser_entrypoint, tenant_id: str = "xifin.example",
              app_group: str = "claimclarity-users", pipeline_env: dict | None = None) -> Deployment:
    """Resources in dependency order (IAM, KMS and S3 reject policies that name principals that do not exist).
    pipeline_env: extra parser task-definition environment (pipeline configuration, see ccsim/pipeline.py)."""
    from .aws.s3 import set_bucket_policy
    provider_arn = aws.create_identity(tenant_id, [app_group])
    sso = {"Version": V, "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"},
                                        "Action": "sts:AssumeRole"}]}
    aws.iam.create_role(TERRAFORM_ROLE, sso)
    aws.iam.create_role(KEY_ADMIN_ROLE, sso)
    aws.iam.create_role("it-sftp-runner", sso)                 # IT's SFTP host (EC2 instance profile)
    aws.iam.create_role(API_ROLE, sso)
    aws.iam.create_role(SUPPORT_ROLE, sso)
    aws.iam.create_role(EVAL_TEAM_ROLE, sso)
    aws.iam.create_role(BREAKGLASS_ROLE, trust_breakglass())
    client_id = aws.cognito.client_id
    for c in customers:
        n = names(c)
        t = transcript_names(c)
        aws.iam.create_role(n["parser"], trust_ecs_tasks(), max_session_duration=3600, tags={"customer": c})
        aws.iam.create_role(n["reader"], trust_reader(c, provider_arn, client_id), tags={"customer": c})
        aws.iam.create_role(n["sftp"], trust_role(role_arn("it-sftp-runner")), tags={"customer": c})
        aws.iam.create_role(t["user"], trust_reader(c, provider_arn, client_id), tags={"customer": c})
        aws.iam.create_role(t["admin"], trust_transcript_admin(c, provider_arn, client_id), tags={"customer": c})
        aws.iam.create_role(t["eval"], trust_role(role_arn(EVAL_TEAM_ROLE)), tags={"customer": c})
        aws.iam.create_role(t["audit_writer"], trust_audit_writer(c), tags={"customer": c})
        aws.iam.create_role(t["audit_reader"], trust_transcript_admin(c, provider_arn, client_id), tags={"customer": c})
    aws.iam.put_role_policy(API_ROLE, "api", api_policy(customers))
    aws.iam.put_role_policy(SUPPORT_ROLE, "support", support_policy())
    aws.iam.put_role_policy(EVAL_TEAM_ROLE, "eval-team", eval_team_policy(customers))
    aws.iam.put_role_policy(BREAKGLASS_ROLE, "breakglass", breakglass_policy())
    keys = {}
    for c in customers:
        keys[c] = aws.kms.create_key(key_policy(c), f"ClaimClarity data key for {c}", alias=f"alias/claimclarity/{c}")
        n = names(c)
        for r in n.values():
            aws.iam.tag_role(r, {"kms_key_arn": keys[c].arn})
        aws.iam.put_role_policy(n["parser"], "parser", parser_policy(c))
        aws.iam.put_role_policy(n["reader"], "reader", reader_policy(c))
        aws.iam.put_role_policy(n["sftp"], "sftp", sftp_policy(c))
    transcript_keys = {}
    for c in customers:
        transcript_keys[c] = aws.kms.create_key(transcript_key_policy(c), f"ClaimClarity transcript key for {c}",
                                                alias=f"alias/claimclarity/{c}/transcripts")
        t = transcript_names(c)
        # Audit writer is tagged with the audit key below, not this one: one role, one kms_key_arn tag,
        # because the bucket policy ties PutObject to that tag.
        for r in (t["user"], t["admin"], t["eval"]):
            aws.iam.tag_role(r, {"kms_key_arn": transcript_keys[c].arn})
        aws.iam.put_role_policy(t["user"], "transcript-user", transcript_user_policy(c))
        aws.iam.put_role_policy(t["admin"], "transcript-admin", transcript_admin_policy(c))
        aws.iam.put_role_policy(t["eval"], "eval", eval_export_policy(c))
        aws.iam.put_role_policy(t["audit_reader"], "audit-reader", audit_reader_policy(c))
    audit_key = aws.kms.create_key(audit_key_policy(customers), "ClaimClarity query-audit key",
                                   alias="alias/claimclarity/audit")
    for c in customers:
        t = transcript_names(c)
        aws.iam.tag_role(t["audit_writer"], {"kms_key_arn": audit_key.arn})
        aws.iam.put_role_policy(t["audit_writer"], "audit-writer", audit_writer_policy(c))
    aws.iam.put_role_policy("it-sftp-runner", "assume-copy-roles", {"Version": V, "Statement": [
        {"Effect": "Allow", "Action": "sts:AssumeRole", "Resource": [role_arn(names(c)["sftp"]) for c in customers]}]})

    b = aws.s3.create_bucket(BUCKET, versioning="Enabled", object_lock=True, eventbridge=True)
    b.encryption = {"SSEAlgorithm": "aws:kms",
                    "KMSMasterKeyID": aws.kms.create_key({"Version": V, "Statement": [
                        {"Effect": "Allow", "Principal": {"AWS": f"arn:aws:iam::{ACCOUNT}:root"}, "Action": "kms:*",
                         "Resource": "*"}]}, "bucket default (never valid for customer prefixes)",
                        alias="alias/claimclarity/platform").arn,
                    "BucketKeyEnabled": False}
    set_bucket_policy(aws.s3, b, bucket_policy())
    b.lifecycle = lifecycle_rules(customers)

    aws.iam.create_role("cc-events-run-parser", {"Version": V, "Statement": [
        {"Effect": "Allow", "Principal": {"Service": "events.amazonaws.com"}, "Action": "sts:AssumeRole",
         "Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT}}}]})
    aws.iam.put_role_policy("cc-events-run-parser", "run", events_role_policy(customers))
    for c in customers:
        td = aws.ecs.register_task_definition(f"cc-parser-{c}", role_arn(names(c)["parser"]), parser_entrypoint,
                                              environment={"CUSTOMER": c, "BUCKET": BUCKET, "KMS_KEY_ARN": keys[c].arn,
                                                           **(pipeline_env or {})})
        rule = aws.events.put_rule(f"cc-raw-success-{c}", success_pattern(c))

        def invoke(evt, session, family=td.family):
            # InputTransformer equivalent: pass the marker's key to the container
            return aws.ecs.run_task(session, family, cluster=CLUSTER, startedBy="events-rule",
                                    overrides={"containerOverrides": [{"name": "parser", "environment": [
                                        {"name": "SUCCESS_KEY", "value": evt["detail"]["object"]["key"]},
                                        {"name": "EVENT_ID", "value": evt["id"]}]}]})
        aws.events.put_targets(rule.name, Target(f"parser-{c}", invoke, role_arn("cc-events-run-parser")))
    return Deployment(aws, customers, keys, provider_arn, transcript_keys, audit_key)


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
    import base64
    import hashlib
    checksum = lambda b: base64.b64encode(hashlib.sha256(b).digest()).decode()
    for f in sorted(Path(local_day_dir).rglob("*")):
        if f.is_file():
            rel = f.relative_to(local_day_dir).as_posix()
            body = f.read_bytes()
            s3.put_object(Bucket=BUCKET, Key=f"{c}/raw/received_date={day}/{rel}", Body=body,
                          ChecksumSHA256=checksum(body), **kw)
    s3.put_object(Bucket=BUCKET, Key=f"{c}/raw/received_date={day}/_SUCCESS", Body=b"", ChecksumSHA256=checksum(b""), **kw)


def export(dest: str | Path, customers=("acme_labs", "acme_diagnostics")):
    """Write every policy document as JSON (placeholders for ARNs that only exist after apply)."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    provider = f"arn:aws:iam::{ACCOUNT}:oidc-provider/cognito-idp.{REGION}.amazonaws.com/<user-pool-id>"
    docs = {"s3_bucket_policy.json": bucket_policy(), "events_run_parser_policy.json": events_role_policy(customers),
            "trust_ecs_tasks.json": trust_ecs_tasks(), "s3_lifecycle_configuration.json": lifecycle_configuration(customers),
            "kms_key_policy_audit.json": audit_key_policy(customers),
            "role_cc-api.json": api_policy(customers), "role_cc-support.json": support_policy(),
            "role_cc-eval-team.json": eval_team_policy(customers),
            "role_cc-support-breakglass.json": breakglass_policy(),
            "trust_cc-support-breakglass.json": trust_breakglass()}
    for c in customers:
        t = transcript_names(c)
        docs[f"kms_key_policy_{c}.json"] = key_policy(c)
        docs[f"kms_key_policy_{c}_transcripts.json"] = transcript_key_policy(c)
        docs[f"role_{names(c)['parser']}.json"] = parser_policy(c)
        docs[f"role_{names(c)['reader']}.json"] = reader_policy(c)
        docs[f"role_{names(c)['sftp']}.json"] = sftp_policy(c)
        docs[f"role_{t['user']}.json"] = transcript_user_policy(c)
        docs[f"role_{t['admin']}.json"] = transcript_admin_policy(c)
        docs[f"role_{t['eval']}.json"] = eval_export_policy(c)
        docs[f"role_{t['audit_writer']}.json"] = audit_writer_policy(c)
        docs[f"role_{t['audit_reader']}.json"] = audit_reader_policy(c)
        docs[f"trust_{names(c)['reader']}.json"] = trust_reader(c, provider, "<app-client-id>")
        docs[f"trust_{t['user']}.json"] = trust_reader(c, provider, "<app-client-id>")
        docs[f"trust_{t['admin']}.json"] = trust_transcript_admin(c, provider, "<app-client-id>")
        docs[f"trust_{t['audit_reader']}.json"] = trust_transcript_admin(c, provider, "<app-client-id>")
        docs[f"trust_{t['audit_writer']}.json"] = trust_audit_writer(c)
        docs[f"trust_{t['eval']}.json"] = trust_role(role_arn(EVAL_TEAM_ROLE))
        docs[f"events_pattern_{c}.json"] = success_pattern(c)
        docs[f"role_tags_{c}.json"] = {"customer": c, "kms_key_arn": f"<arn of alias/claimclarity/{c}>"}
        docs[f"role_tags_{c}_transcripts.json"] = {"customer": c,
                                                   "kms_key_arn": f"<arn of alias/claimclarity/{c}/transcripts>"}
    for name, d in docs.items():
        (dest / name).write_text(json.dumps(d, indent=2) + "\n")
    return sorted(docs)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="export policy JSON for Terraform")
    ap.add_argument("--export", required=True)
    print("\n".join(export(ap.parse_args().export)))
