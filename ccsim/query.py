"""Per-request DuckDB session (docs/data-backend/duckdb_per_request_connection.py, made runnable).

token -> STS creds for that user's customer -> fresh in-memory DuckDB that can read only that
customer's current semantic_layer version (and, on escalation, PHI-stripped silver views)."""
from __future__ import annotations

import uuid

import duckdb

from .infra import BUCKET
from .pipeline import SEM_TABLES

# silver tables the agent may see on escalation, with PHI columns removed (HIPAA minimum necessary)
ESCALATION_VIEWS = {
    "c837.claim": "SELECT * FROM {t}",
    "c837.service_line": "SELECT * FROM {t}",
    "c837.claim_code": "SELECT * FROM {t}",
    "r835.claim_payment": "SELECT * FROM {t}",
    "r835.service_payment": "SELECT * FROM {t}",
    "r835.remark": "SELECT * FROM {t}",
    "r835.provider_adjustment": "SELECT * FROM {t}",
    "edi.adjustment": "SELECT * FROM {t}",
    "edi.date_time": "SELECT * FROM {t}",
    "r835.payment": "SELECT * EXCLUDE (bpr07_sender_dfi_id, bpr09_sender_account, bpr13_receiver_dfi_id, "
                    "bpr15_receiver_account) FROM {t}",
    "edi.entity": "SELECT * EXCLUDE (name_last_or_org, name_first, name_middle, name_prefix, name_suffix, id_value, "
                  "addr_line1, addr_line2, city, postal_code, dmg_birth_date, dmg_gender), "
                  "CASE WHEN entity_id_code IN ('IL','QC','74','QD') THEN NULL ELSE name_last_or_org END AS org_name, "
                  "CASE WHEN entity_id_code IN ('IL','QC','74','QD') THEN NULL ELSE id_value END AS org_id "
                  "FROM {t}",
}
PHI_COLUMNS = {"name_first", "name_middle", "dmg_birth_date", "bpr09_sender_account", "bpr15_receiver_account",
               "addr_line1", "member_id", "patient_name", "subscriber_id", "raw_text", "elements"}


def open_session(aws, idp, token: str, escalate: bool = False):
    customer, creds = idp.credentials_for(token, uuid.uuid4().hex[:8])
    s3 = aws.client("s3", creds)
    ver = s3.get_object(Bucket=BUCKET, Key=f"{customer}/semantic_layer/CURRENT")["Body"].decode()
    gold = s3.local_prefix(BUCKET, f"{customer}/semantic_layer/{ver}/")
    dirs = [str(gold) + "/"]
    silver = None
    if escalate:
        silver = s3.local_prefix(BUCKET, f"{customer}/parquet/")
        dirs.append(str(silver) + "/")
    con = duckdb.connect(config={"autoinstall_known_extensions": False, "autoload_known_extensions": False})
    con.execute("SET allowed_directories = ?", [dirs])
    con.execute("SET enable_external_access = false")      # read_* now limited to this customer's folders
    con.execute("CREATE SCHEMA sem")
    for t in SEM_TABLES:
        con.execute(f"CREATE VIEW sem.{t} AS SELECT * FROM read_parquet('{gold}/sem.{t}/*.parquet')")
    if escalate:
        con.execute("CREATE SCHEMA lossless")
        for t, sql in ESCALATION_VIEWS.items():
            src = f"read_parquet('{silver}/{t}/**/*.parquet', union_by_name=true, hive_partitioning=false)"
            con.execute(f"CREATE VIEW lossless.{t.replace('.', '_')} AS " + sql.format(t=src))
    con.execute("SET lock_configuration = true")
    aws.audit(creds.role_arn, "duckdb:OpenSession", f"{customer}/semantic_layer/{ver}", True, escalate=escalate)
    return customer, con
