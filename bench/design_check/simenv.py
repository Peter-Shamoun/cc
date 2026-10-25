"""The simulated account for the design check, plus two bench-only additions to the local endpoint (patched in this
process; ccsim/aws is unchanged on disk):

  - multipart upload (CreateMultipartUpload / UploadPart / Complete / Abort). DuckDB httpfs writes every S3 file as
    a multipart upload, so DuckLake and DuckDB COPY TO s3:// need it. Parts are held in memory; Complete stores the
    object through the normal PutObject path with the SSE headers given at Create, so IAM, the bucket policy and
    KMS judge it exactly like a single PUT. Every part is one request, as S3 bills it.
  - server-side CPU accounting: CPU the endpoint's handler threads spend (SigV4, AES-GCM, SQLite) is summed, so a
    measurement can report client CPU = process CPU - endpoint CPU.
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from xml.sax.saxutils import escape

from ccsim.aws import LocalAWS, reset
from ccsim.aws import http as sim_http
from ccsim.aws.sts import service_principal
from ccsim.infra import BUCKET, SESSION_SECONDS, names, provision, role_arn
from ccsim.pipeline import parse_batch
from ccsim.query import login
from ccsim.world import DEFAULT_USERS, add_user

S3NS = "http://s3.amazonaws.com/doc/2006-03-01/"
_uploads: dict = {}
_cpu = {"s": 0.0}
_catalog = {"requests": 0, "bytes_out": 0, "bytes_in": 0}     # traffic to DuckLake catalog files (/_ducklake/)
_cpu_lock = threading.Lock()


def _patch_endpoint():
    handler = sim_http._Handler
    if getattr(handler, "_design_check_patched", False):
        return
    orig_s3, orig_handle = handler._s3, handler._handle

    def _s3(self, rc, raw_path, q, body, head):
        from urllib.parse import unquote
        bucket, _, key = unquote(raw_path).lstrip("/").partition("/")
        if key and self.command == "POST" and "uploads" in q:
            uid = uuid.uuid4().hex
            h = self.headers
            _uploads[uid] = dict(parts={}, kw={k: v for k, v in dict(
                ContentType=h.get("Content-Type"), ServerSideEncryption=h.get("x-amz-server-side-encryption"),
                SSEKMSKeyId=h.get("x-amz-server-side-encryption-aws-kms-key-id")).items() if v})
            xml = (f'<InitiateMultipartUploadResult xmlns="{S3NS}"><Bucket>{escape(bucket)}</Bucket><Key>{escape(key)}</Key>'
                   f"<UploadId>{uid}</UploadId></InitiateMultipartUploadResult>")
            return self._send(200, xml.encode(), {"Content-Type": "application/xml"})
        if key and self.command == "PUT" and "uploadId" in q:
            _uploads[q["uploadId"]]["parts"][int(q["partNumber"])] = body
            return self._send(200, b"", {"ETag": f'"{uuid.uuid4().hex}"'})
        if key and self.command == "POST" and "uploadId" in q:
            up = _uploads.pop(q["uploadId"])
            data = b"".join(up["parts"][n] for n in sorted(up["parts"]))
            r = self.aws.s3.put_object(rc, bucket, key, data, **up["kw"])
            xml = (f'<CompleteMultipartUploadResult xmlns="{S3NS}"><Bucket>{escape(bucket)}</Bucket><Key>{escape(key)}</Key>'
                   f"<ETag>{escape(r['ETag'])}</ETag></CompleteMultipartUploadResult>")
            return self._send(200, xml.encode(), {"Content-Type": "application/xml", **self._put_headers(r)})
        if key and self.command == "DELETE" and "uploadId" in q:
            _uploads.pop(q["uploadId"], None)
            return self._send(204)
        return orig_s3(self, rc, raw_path, q, body, head)

    orig_send = handler._send

    def _send(self, status, body=b"", headers=None, head=False):
        if "/_ducklake/" in self.path:
            with _cpu_lock:
                _catalog["requests"] += 1
                _catalog["bytes_out"] += 0 if head else len(body)
                _catalog["bytes_in"] += int(self.headers.get("Content-Length") or 0) if self.command == "PUT" else 0
        return orig_send(self, status, body, headers, head)

    handler._send = _send

    def _handle(self):
        t0 = time.thread_time()
        try:
            return orig_handle(self)
        finally:
            with _cpu_lock:
                _cpu["s"] += time.thread_time() - t0

    handler._s3, handler._handle = _s3, _handle
    handler.do_GET = handler.do_PUT = handler.do_HEAD = handler.do_DELETE = handler.do_POST = _handle
    handler._design_check_patched = True


class Meter:
    """Snapshot of endpoint counters + CPU. `Meter.diff(a, b)` -> requests, bytes, ops, CPU split."""

    def __init__(self, ep):
        self.ep = ep

    def snap(self) -> dict:
        return dict(self.ep.counters(), cpu_process=time.process_time(), cpu_endpoint=_cpu["s"],
                    **{f"catalog_{k}": v for k, v in _catalog.items()})

    @staticmethod
    def diff(after: dict, before: dict) -> dict:
        d = {k: after[k] - before.get(k, 0) for k in after}
        return dict(requests=d["requests"], bytes_read=d["bytes_out"], bytes_written=d["bytes_in"],
                    ops={k[3:]: v for k, v in d.items() if k.startswith("op_") and v},
                    catalog_requests=d["catalog_requests"], catalog_bytes_read=d["catalog_bytes_out"],
                    catalog_bytes_written=d["catalog_bytes_in"],
                    cpu_client_s=round(d["cpu_process"] - d["cpu_endpoint"], 3), cpu_endpoint_s=round(d["cpu_endpoint"], 3))


class Sim:
    """One provisioned account with the unchanged infra (IAM, bucket policy, KMS keys), one customer."""

    def __init__(self, root: Path, customer: str = "acme_labs"):
        _patch_endpoint()
        reset(root)
        self.root, self.customer = root, customer
        self.aws = LocalAWS(root / "aws")
        self.ep = self.aws.serve()
        self.dep = provision(self.aws, [customer], parse_batch)
        self.aws.events.auto_drain = False
        for upn, c in DEFAULT_USERS.items():
            if c == customer:
                add_user(self.aws, upn, c)
                self.upn = upn
        self.kms_arn = self.dep.keys[customer].arn
        self.meter = Meter(self.ep)

    def parser_session(self):
        return self.aws.sts.assume_role(service_principal("ecs-tasks.amazonaws.com"), None,
                                        role_arn(names(self.customer)["parser"]), "design-check",
                                        SourceContext={"aws:SourceAccount": "111122223333",
                                                       "aws:SourceArn": "arn:aws:ecs:us-west-2:111122223333:task/cc/dc"})

    def reader_credentials(self):
        """What query.open_session does before DuckDB: Cognito token -> STS web identity -> 15-minute creds."""
        tok = login(self.aws, self.upn)
        sts = self.aws.boto3_client("sts")
        r = sts.assume_role_with_web_identity(RoleArn=self.dep.reader_role(self.customer), RoleSessionName="dc-reader",
                                              WebIdentityToken=tok, DurationSeconds=SESSION_SECONDS)
        c = r["Credentials"]
        return self.aws.sts.lookup(c["AccessKeyId"], c["SessionToken"])

    def secret_sql(self, session, scope: str, name: str, kms: bool = False) -> str:
        sql = self.aws.duckdb_secret_sql(session, scope, name=name)
        return sql[:-1] + f", KMS_KEY_ID '{self.kms_arn}')" if kms else sql

    def inventory(self, prefix: str) -> dict:
        """Live objects and all stored versions (noncurrent bytes are what S3 bills until lifecycle expires them)."""
        db = self.aws.s3.db
        live = db.execute("SELECT count(*), coalesce(sum(size),0) FROM objects WHERE bucket=? AND is_latest=1 AND "
                          "delete_marker=0 AND key LIKE ?", (BUCKET, f"{prefix}%")).fetchone()
        allv = db.execute("SELECT count(*), coalesce(sum(size),0) FROM objects WHERE bucket=? AND key LIKE ?",
                          (BUCKET, f"{prefix}%")).fetchone()
        return dict(live_files=live[0], live_bytes=live[1], stored_versions=allv[0], stored_bytes=allv[1])

    def shutdown(self):
        self.aws.shutdown()
