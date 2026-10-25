"""Per-request DuckDB session: the runnable form of docs/data-backend/duckdb_per_request_connection.py.

    Cognito id token -> app server verifies it -> STS AssumeRoleWithWebIdentity into that customer's reader
    role (the trust policy re-checks the customer tag) -> 15-minute credentials -> CURRENT -> that version's
    manifest -> fresh in-memory DuckDB over s3://cc-prod/<customer>/semantic_layer/,
    with secrets and allowed_directories limited to the prefixes of the relations it may read (sem.*, plus
    lossless.* when escalating), external access otherwise off, and the configuration locked.
    Each view reads exactly the files the manifest lists (no LIST requests, no files of another version).

Four independent layers stop agent SQL from reaching another customer's data or PHI: the IAM role, the
bucket policy, the secret's scope, and DuckDB's allowed_directories. The tests disable them one at a time.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

import duckdb

from .infra import BUCKET, SESSION_SECONDS, Deployment
from .pipeline import APPEND_RELATIONS, SEM_TABLES


@dataclass
class QuerySession:
    customer: str
    user: str
    version: str
    con: duckdb.DuckDBPyConnection
    credentials: object
    gold_uri: str


def login(aws, upn: str) -> str:
    """Browser flow: Entra sign-in, federated into Cognito. Returns the Cognito id token."""
    return aws.cognito.federated_sign_in(aws.entra.sign_in(upn))["IdToken"]


def open_session(aws, dep: Deployment, id_token: str, escalate: bool = False, request_id: str | None = None,
                 enforce_allowed_directories: bool = True, scope_secret: bool = True) -> QuerySession:
    claims = aws.cognito.verify_id_token(id_token)
    customer = claims["custom:customer"]
    rid = request_id or uuid.uuid4().hex[:8]
    creds = aws.sts.assume_role_with_web_identity(RoleArn=dep.reader_role(customer),
                                                  RoleSessionName=f"u-{claims['sub'][:8]}-{rid}",
                                                  WebIdentityToken=id_token, DurationSeconds=SESSION_SECONDS)
    s3 = aws.boto3_client("s3", creds)
    version = s3.get_object(Bucket=BUCKET, Key=f"{customer}/semantic_layer/CURRENT")["Body"].read().decode()
    manifest = json.loads(s3.get_object(Bucket=BUCKET, Key=f"{customer}/semantic_layer/{version}/manifest.json")["Body"].read())
    gold = f"s3://{BUCKET}/{customer}/semantic_layer/"
    # the relation's zero-row schema file first: binding a view reads only the first file, and DuckDB fetches a
    # first file under 2 MB whole (at every session open, for every view)
    files = lambda rel: [f"s3://{BUCKET}/{k}" for part in sorted(manifest["relations"][rel], key=lambda p: (p != "schema", p))
                         for k in manifest["relations"][rel][part]]
    # Only the relations this session may use are reachable: sem.* always, lossless.* only when escalating. Each
    # relation's prefix gets its own secret and allowed directory, so a non-escalated session cannot open a
    # lossless.* file even with SQL that gets past the guard.
    relations = [f"sem.{t}" for t in SEM_TABLES] + (list(APPEND_RELATIONS) if escalate else [])
    prefixes = [f"{gold}{rel}/" for rel in relations]
    con = duckdb.connect(config={"autoinstall_known_extensions": False, "autoload_known_extensions": False})
    con.execute("LOAD httpfs")
    for i, prefix in enumerate(prefixes if scope_secret else ["s3://"]):
        con.execute(aws.duckdb_secret_sql(creds, prefix, name=f"s3_gold_{i}"))
    con.execute("CREATE SCHEMA sem")
    for t in SEM_TABLES:
        con.execute(f"CREATE VIEW sem.{t} AS SELECT * FROM read_parquet({files(f'sem.{t}')!r}, hive_partitioning=false)")
    if escalate:
        con.execute("CREATE SCHEMA lossless")
        for rel in APPEND_RELATIONS:
            con.execute(f"CREATE VIEW {rel} AS SELECT * FROM read_parquet({files(rel)!r}, hive_partitioning=false)")
    if enforce_allowed_directories:
        con.execute("SET allowed_directories = ?", [prefixes])
        con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return QuerySession(customer, claims.get("email", claims["sub"]), version, con, creds, gold)
