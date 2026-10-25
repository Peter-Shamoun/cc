"""A local endpoint that speaks the S3 REST API (path-style) and the STS Query API, authenticated with
real SigV4 (header and presigned-query), so stock boto3 and DuckDB httpfs talk to it exactly as they talk
to AWS. Every request ends in the same S3 / STS code that the in-process clients call.

Latency injection (`latency_ms` per request, `bandwidth_mbps` per response) models S3 round trips, which
matters when the question is how many requests a query makes.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import socket
import threading
import time
from datetime import datetime, timezone
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, quote, quote_plus, unquote, urlsplit
from xml.sax.saxutils import escape

from .errors import AccessDenied, AWSError, InvalidRequest, NotImplementedOp, SignatureDoesNotMatch
from .s3 import RequestContext

S3NS = "http://s3.amazonaws.com/doc/2006-03-01/"
STSNS = "https://sts.amazonaws.com/doc/2011-06-15/"
_AUTH = re.compile(r"AWS4-HMAC-SHA256\s+Credential=([^,]+),\s*SignedHeaders=([^,]+),\s*Signature=([0-9a-f]+)")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _sign_key(secret: str, date: str, region: str, service: str) -> bytes:
    k = hmac.new(("AWS4" + secret).encode(), date.encode(), hashlib.sha256).digest()
    for part in (region, service, "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    return k


def _uri_encode(s: str, safe: str) -> str:
    return quote(s, safe=safe)


def canonical_query(pairs) -> str:
    enc = sorted((_uri_encode(k, "-_.~"), _uri_encode(v, "-_.~")) for k, v in pairs)
    return "&".join(f"{k}={v}" for k, v in enc)


def decode_aws_chunked(body: bytes) -> bytes:
    out, i = bytearray(), 0
    while True:
        j = body.index(b"\r\n", i)
        size = int(body[i:j].split(b";")[0], 16)
        i = j + 2
        if size == 0:
            return bytes(out)
        out += body[i:i + size]
        i += size + 2


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AmazonS3"

    def log_message(self, *a):                                   # quiet
        pass

    def setup(self):
        super().setup()
        # headers and body go out in separate writes; without TCP_NODELAY, Nagle + delayed ACK add ~40 ms
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    # -- plumbing -------------------------------------------------------------------------------------
    @property
    def aws(self):
        return self.server.aws

    def _read_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            buf = bytearray()
            while True:
                size = int(self.rfile.readline().split(b";")[0], 16)
                if size == 0:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                        pass
                    return bytes(buf)
                buf += self.rfile.read(size)
                self.rfile.readline()
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _send(self, status: int, body: bytes = b"", headers: dict | None = None, head: bool = False):
        cfg = self.server.cfg
        delay = cfg["latency_ms"] / 1000
        if cfg["bandwidth_mbps"] and body:
            delay += len(body) / (cfg["bandwidth_mbps"] * 125_000)
        if delay:
            time.sleep(delay)
        self.send_response(status)
        hdrs = {"x-amz-request-id": "LOCAL" + str(self.server.requests), "Date": formatdate(usegmt=True)}
        hdrs.update(headers or {})
        hdrs.setdefault("Content-Length", str(len(body)))
        for k, v in hdrs.items():
            self.send_header(k, str(v))
        self.end_headers()
        if not head and body:
            self.server.bytes_out += len(body)
            self.wfile.write(body)

    def _error(self, e: AWSError, head=False, sts=False):
        if sts:
            xml = (f'<ErrorResponse xmlns="{STSNS}"><Error><Type>Sender</Type><Code>{escape(e.code)}</Code>'
                   f'<Message>{escape(e.message)}</Message></Error><RequestId>local</RequestId></ErrorResponse>')
        else:
            extras = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in e.extra.items() if v is not None)
            xml = (f'<?xml version="1.0" encoding="UTF-8"?><Error><Code>{escape(e.code)}</Code>'
                   f'<Message>{escape(e.message)}</Message>{extras}<RequestId>local</RequestId></Error>')
        self._send(e.status, xml.encode(), {"Content-Type": "application/xml"}, head=head)

    # -- SigV4 ------------------------------------------------------------------------------------------
    def _authenticate(self, raw_path: str, query: list, body: bytes, service_hint: str):
        """Returns (session or None). Raises on a bad signature, unknown key or expired token."""
        auth = self.headers.get("Authorization")
        qd = dict(query)
        presigned = "X-Amz-Signature" in qd
        if not auth and not presigned:
            return None                                          # anonymous
        if presigned:
            cred, signed_headers, signature = qd["X-Amz-Credential"], qd["X-Amz-SignedHeaders"], qd["X-Amz-Signature"]
            amz_date, token = qd.get("X-Amz-Date", ""), qd.get("X-Amz-Security-Token")
            payload_hash = "UNSIGNED-PAYLOAD"
            q_for_sig = [(k, v) for k, v in query if k != "X-Amz-Signature"]
            expires = int(qd.get("X-Amz-Expires", "0"))
            signed_at = datetime.strptime(amz_date, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
            if time.time() > signed_at + expires:
                raise AccessDenied("Request has expired", code="AccessDenied")
        else:
            m = _AUTH.match(auth)
            if not m:
                raise InvalidRequest("Unsupported Authorization header", code="AuthorizationHeaderMalformed")
            cred, signed_headers, signature = m.groups()
            amz_date = self.headers.get("x-amz-date") or ""
            token = self.headers.get("x-amz-security-token")
            payload_hash = self.headers.get("x-amz-content-sha256") or hashlib.sha256(body).hexdigest()
            q_for_sig = query
            try:
                signed_at = datetime.strptime(amz_date, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                raise AccessDenied("missing x-amz-date", code="AccessDenied") from None
            if abs(time.time() - signed_at) > 900:               # real-time skew check (the sim clock may move)
                raise AccessDenied("The difference between the request time and the current time is too large.",
                                   code="RequestTimeTooSkewed")
        akid, date, region, service, term = cred.split("/")
        session = self.aws.sts.lookup(akid, token)
        if service_hint == "s3":
            canon_uri = _uri_encode(unquote(raw_path), "/~-_.")
        else:
            canon_uri = _uri_encode(_uri_encode(unquote(raw_path), "/~-_."), "/~-_.") if raw_path != "/" else "/"
        hdrs = []
        for h in signed_headers.split(";"):
            v = self.headers.get(h, "")
            hdrs.append(f"{h}:{' '.join(v.strip().split())}")
        canonical = "\n".join([self.command, canon_uri, canonical_query(q_for_sig), "\n".join(hdrs) + "\n",
                               signed_headers, payload_hash])
        sts_ = "\n".join(["AWS4-HMAC-SHA256", amz_date, f"{date}/{region}/{service}/{term}",
                          hashlib.sha256(canonical.encode()).hexdigest()])
        expect = hmac.new(_sign_key(session.secret_access_key, date, region, service), sts_.encode(),
                          hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expect, signature):
            raise SignatureDoesNotMatch("The request signature we calculated does not match the signature you provided.")
        if re.fullmatch(r"[0-9a-f]{64}", payload_hash) and hashlib.sha256(body).hexdigest() != payload_hash:
            raise InvalidRequest("x-amz-content-sha256 does not match the payload", code="XAmzContentSHA256Mismatch")
        return session

    # -- dispatch ---------------------------------------------------------------------------------------
    def _handle(self):
        self.server.requests += 1
        u = urlsplit(self.path)
        raw_path = u.path or "/"
        query = parse_qsl(u.query, keep_blank_values=True)
        body = self._read_body() if self.command in ("PUT", "POST") else b""
        self.server.bytes_in += len(body)
        is_sts = self.command == "POST" and raw_path == "/" and (
            "x-www-form-urlencoded" in (self.headers.get("Content-Type") or ""))
        head = self.command == "HEAD"
        try:
            if is_sts:
                form = dict(parse_qsl(body.decode(), keep_blank_values=True))
                session = None if form.get("Action") == "AssumeRoleWithWebIdentity" else \
                    self._authenticate(raw_path, query, body, "sts")
                return self._sts(form, session)
            session = self._authenticate(raw_path, query, body, "s3")
            if self.headers.get("Content-Encoding", "").startswith("aws-chunked") or \
                    (self.headers.get("x-amz-content-sha256") or "").startswith("STREAMING-"):
                body = decode_aws_chunked(body)
            rc = RequestContext(session.principal() if session else None, session,
                                secure_transport=self.server.cfg["secure_transport"], source_ip=self.client_address[0],
                                auth_type="REST-QUERY-STRING" if "X-Amz-Signature" in dict(query) else "REST-HEADER")
            self.server.op_counts[self.command] = self.server.op_counts.get(self.command, 0) + 1
            self._s3(rc, raw_path, dict(query), body, head)
        except AWSError as e:
            self._error(e, head=head, sts=is_sts)
        except Exception as e:                                   # noqa: BLE001
            self._error(AWSError(f"{type(e).__name__}: {e}"), head=head, sts=is_sts)

    do_GET = do_PUT = do_HEAD = do_DELETE = do_POST = _handle

    # -- S3 -------------------------------------------------------------------------------------------
    def _s3(self, rc, raw_path, q, body, head):
        s3 = self.aws.s3
        parts = unquote(raw_path).lstrip("/").split("/", 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ""
        h = self.headers
        if not bucket:
            raise NotImplementedOp("ListBuckets is not supported")
        if not key:
            if self.command == "GET" and q.get("list-type") == "2":
                return self._list_v2(rc, bucket, q)
            if self.command == "GET" and "versions" in q:
                return self._list_versions(rc, bucket, q)
            if self.command == "POST" and "delete" in q:
                return self._delete_objects(rc, bucket, body)
            if self.command == "HEAD":
                s3.bucket(bucket)
                return self._send(200, head=True)
            if self.command == "GET" and "location" in q:
                return self._send(200, f'<LocationConstraint xmlns="{S3NS}">us-west-2</LocationConstraint>'.encode(),
                                  {"Content-Type": "application/xml"})
            raise NotImplementedOp(f"bucket-level {self.command} {sorted(q)} is not supported")
        if self.command in ("GET", "HEAD"):
            fn = s3.get_object if self.command == "GET" else s3.head_object
            kwargs = dict(VersionId=q.get("versionId"), IfMatch=h.get("If-Match"), IfNoneMatch=h.get("If-None-Match"))
            if self.command == "GET" and h.get("Range"):
                kwargs["Range"] = h["Range"]
            r = fn(rc, bucket, key, **kwargs)
            hdrs = {"Content-Type": r["ContentType"], "ETag": r["ETag"], "Accept-Ranges": "bytes",
                    "Last-Modified": formatdate(r["LastModified"], usegmt=True),
                    "x-amz-server-side-encryption": r["ServerSideEncryption"]}
            if "SSEKMSKeyId" in r:
                hdrs["x-amz-server-side-encryption-aws-kms-key-id"] = r["SSEKMSKeyId"]
            if "VersionId" in r:
                hdrs["x-amz-version-id"] = r["VersionId"]
            if "ObjectLockMode" in r:
                hdrs["x-amz-object-lock-mode"] = r["ObjectLockMode"]
                hdrs["x-amz-object-lock-retain-until-date"] = _iso(r["ObjectLockRetainUntilDate"])
            for k, v in r.get("Metadata", {}).items():
                hdrs[f"x-amz-meta-{k}"] = v
            if head:
                hdrs["Content-Length"] = str(r["ContentLength"])
                return self._send(200, b"", hdrs, head=True)
            status = 206 if "ContentRange" in r else 200
            if status == 206:
                hdrs["Content-Range"] = r["ContentRange"]
            return self._send(status, r["Body"], hdrs)
        if self.command == "PUT":
            if "uploadId" in q or "partNumber" in q:
                raise NotImplementedOp("multipart upload is not supported by the local endpoint")
            kw = dict(ContentType=h.get("Content-Type"), ServerSideEncryption=h.get("x-amz-server-side-encryption"),
                      SSEKMSKeyId=h.get("x-amz-server-side-encryption-aws-kms-key-id"),
                      IfNoneMatch=h.get("If-None-Match"), IfMatch=h.get("If-Match"),
                      Metadata={k[11:]: v for k, v in h.items() if k.lower().startswith("x-amz-meta-")},
                      ObjectLockMode=h.get("x-amz-object-lock-mode"),
                      ObjectLockLegalHoldStatus=h.get("x-amz-object-lock-legal-hold"),
                      ChecksumSHA256=h.get("x-amz-checksum-sha256"), ContentMD5=h.get("Content-MD5"))
            if h.get("x-amz-server-side-encryption-bucket-key-enabled"):
                kw["BucketKeyEnabled"] = h["x-amz-server-side-encryption-bucket-key-enabled"].lower() == "true"
            if h.get("x-amz-object-lock-retain-until-date"):
                kw["ObjectLockRetainUntilDate"] = datetime.fromisoformat(
                    h["x-amz-object-lock-retain-until-date"].replace("Z", "+00:00")).timestamp()
            if h.get("x-amz-copy-source"):
                src = unquote(h["x-amz-copy-source"]).lstrip("/")
                src_path, _, src_q = src.partition("?")
                sb, sk = src_path.split("/", 1)
                vid = dict(parse_qsl(src_q)).get("versionId")
                kw.pop("ChecksumSHA256", None)
                kw.pop("ContentMD5", None)
                r = s3.copy_object(rc, bucket, key, {"Bucket": sb, "Key": sk, "VersionId": vid}, **kw)
                xml = (f'<CopyObjectResult xmlns="{S3NS}"><ETag>{escape(r["CopyObjectResult"]["ETag"])}</ETag>'
                       f'<LastModified>{_iso(time.time())}</LastModified></CopyObjectResult>')
                return self._send(200, xml.encode(), {"Content-Type": "application/xml", **self._put_headers(r)})
            r = s3.put_object(rc, bucket, key, body, **{k: v for k, v in kw.items() if v not in (None, {})})
            return self._send(200, b"", {"ETag": r["ETag"], **self._put_headers(r)})
        if self.command == "DELETE":
            r = s3.delete_object(rc, bucket, key, VersionId=q.get("versionId"),
                                 BypassGovernanceRetention=(h.get("x-amz-bypass-governance-retention") or "").lower() == "true")
            hdrs = {}
            if r.get("DeleteMarker"):
                hdrs["x-amz-delete-marker"] = "true"
            if r.get("VersionId"):
                hdrs["x-amz-version-id"] = r["VersionId"]
            return self._send(204, b"", hdrs)
        if self.command == "POST" and "uploads" in q:
            raise NotImplementedOp("multipart upload is not supported by the local endpoint")
        raise NotImplementedOp(f"{self.command} on an object is not supported")

    @staticmethod
    def _put_headers(r):
        out = {"x-amz-server-side-encryption": r["ServerSideEncryption"]}
        if r.get("SSEKMSKeyId"):
            out["x-amz-server-side-encryption-aws-kms-key-id"] = r["SSEKMSKeyId"]
            out["x-amz-server-side-encryption-bucket-key-enabled"] = str(r.get("BucketKeyEnabled", False)).lower()
        if r.get("VersionId"):
            out["x-amz-version-id"] = r["VersionId"]
        return out

    def _list_v2(self, rc, bucket, q):
        url = q.get("encoding-type") == "url"
        enc = (lambda s: quote_plus(s, safe="/")) if url else (lambda s: s)
        r = self.aws.s3.list_objects_v2(rc, bucket, Prefix=q.get("prefix", ""), Delimiter=q.get("delimiter") or None,
                                        MaxKeys=int(q.get("max-keys", 1000)),
                                        ContinuationToken=q.get("continuation-token"), StartAfter=q.get("start-after"))
        x = [f'<?xml version="1.0" encoding="UTF-8"?><ListBucketResult xmlns="{S3NS}">',
             f"<Name>{escape(bucket)}</Name><Prefix>{escape(enc(r['Prefix']))}</Prefix>",
             f"<KeyCount>{r['KeyCount']}</KeyCount><MaxKeys>{r['MaxKeys']}</MaxKeys>",
             f"<IsTruncated>{str(r['IsTruncated']).lower()}</IsTruncated>"]
        if r.get("Delimiter"):
            x.append(f"<Delimiter>{escape(enc(r['Delimiter']))}</Delimiter>")
        if url:
            x.append("<EncodingType>url</EncodingType>")
        if r.get("ContinuationToken"):
            x.append(f"<ContinuationToken>{escape(r['ContinuationToken'])}</ContinuationToken>")
        if r.get("NextContinuationToken"):
            x.append(f"<NextContinuationToken>{escape(r['NextContinuationToken'])}</NextContinuationToken>")
        if q.get("start-after"):
            x.append(f"<StartAfter>{escape(enc(q['start-after']))}</StartAfter>")
        for c in r["Contents"]:
            x.append(f"<Contents><Key>{escape(enc(c['Key']))}</Key><LastModified>{_iso(c['LastModified'])}</LastModified>"
                     f"<ETag>{escape(c['ETag'])}</ETag><Size>{c['Size']}</Size><StorageClass>STANDARD</StorageClass></Contents>")
        for p in r["CommonPrefixes"]:
            x.append(f"<CommonPrefixes><Prefix>{escape(enc(p['Prefix']))}</Prefix></CommonPrefixes>")
        x.append("</ListBucketResult>")
        self._send(200, "".join(x).encode(), {"Content-Type": "application/xml"})

    def _list_versions(self, rc, bucket, q):
        r = self.aws.s3.list_object_versions(rc, bucket, Prefix=q.get("prefix", ""))
        x = [f'<?xml version="1.0" encoding="UTF-8"?><ListVersionsResult xmlns="{S3NS}"><Name>{escape(bucket)}</Name>'
             f"<Prefix>{escape(q.get('prefix', ''))}</Prefix><IsTruncated>false</IsTruncated>"]
        for v in r["Versions"]:
            x.append(f"<Version><Key>{escape(v['Key'])}</Key><VersionId>{v['VersionId']}</VersionId>"
                     f"<IsLatest>{str(v['IsLatest']).lower()}</IsLatest><LastModified>{_iso(v['LastModified'])}</LastModified>"
                     f"<ETag>{escape(v['ETag'])}</ETag><Size>{v['Size']}</Size><StorageClass>STANDARD</StorageClass></Version>")
        for d in r["DeleteMarkers"]:
            x.append(f"<DeleteMarker><Key>{escape(d['Key'])}</Key><VersionId>{d['VersionId']}</VersionId>"
                     f"<IsLatest>{str(d['IsLatest']).lower()}</IsLatest><LastModified>{_iso(d['LastModified'])}</LastModified>"
                     "</DeleteMarker>")
        x.append("</ListVersionsResult>")
        self._send(200, "".join(x).encode(), {"Content-Type": "application/xml"})

    def _delete_objects(self, rc, bucket, body):
        import xml.etree.ElementTree as ET
        root = ET.fromstring(body)
        ns = {"s": S3NS} if root.tag.startswith("{") else {}
        objs = root.findall("s:Object", ns) if ns else root.findall("Object")
        out = [f'<?xml version="1.0" encoding="UTF-8"?><DeleteResult xmlns="{S3NS}">']
        for o in objs:
            k = (o.find("s:Key", ns) if ns else o.find("Key")).text
            v_el = o.find("s:VersionId", ns) if ns else o.find("VersionId")
            try:
                self.aws.s3.delete_object(rc, bucket, k, VersionId=v_el.text if v_el is not None else None)
                out.append(f"<Deleted><Key>{escape(k)}</Key></Deleted>")
            except AWSError as e:
                out.append(f"<Error><Key>{escape(k)}</Key><Code>{e.code}</Code><Message>{escape(e.message)}</Message></Error>")
        out.append("</DeleteResult>")
        self._send(200, "".join(out).encode(), {"Content-Type": "application/xml"})

    # -- STS ------------------------------------------------------------------------------------------
    def _sts(self, form, session):
        sts = self.aws.sts
        action = form.get("Action")
        policy = json.loads(form["Policy"]) if form.get("Policy") else None
        dur = int(form.get("DurationSeconds", 3600))
        if action == "GetCallerIdentity":
            if session is None:
                raise AccessDenied("missing credentials", code="MissingAuthenticationToken")
            r = sts.get_caller_identity(session)
            xml = (f'<GetCallerIdentityResponse xmlns="{STSNS}"><GetCallerIdentityResult><Arn>{r["Arn"]}</Arn>'
                   f'<UserId>{r["UserId"]}</UserId><Account>{r["Account"]}</Account></GetCallerIdentityResult>'
                   "<ResponseMetadata><RequestId>local</RequestId></ResponseMetadata></GetCallerIdentityResponse>")
            return self._send(200, xml.encode(), {"Content-Type": "text/xml"})
        if action == "AssumeRole":
            if session is None:
                raise AccessDenied("missing credentials", code="MissingAuthenticationToken")
            tags, i = [], 1
            while f"Tags.member.{i}.Key" in form:
                tags.append({"Key": form[f"Tags.member.{i}.Key"], "Value": form[f"Tags.member.{i}.Value"]})
                i += 1
            trans = [v for k, v in sorted(form.items()) if k.startswith("TransitiveTagKeys.member.")]
            s = sts.assume_role(session.principal(), session, form["RoleArn"], form["RoleSessionName"], dur, policy,
                                tags, trans, form.get("ExternalId"))
            extra = ""
        elif action == "AssumeRoleWithWebIdentity":
            s = sts.assume_role_with_web_identity(form["RoleArn"], form["RoleSessionName"], form["WebIdentityToken"],
                                                  dur, policy)
            extra = "<Provider>oidc</Provider>"
        else:
            raise NotImplementedOp(f"STS action {action} is not supported")
        xml = (f'<{action}Response xmlns="{STSNS}"><{action}Result><Credentials>'
               f"<AccessKeyId>{s.access_key_id}</AccessKeyId><SecretAccessKey>{s.secret_access_key}</SecretAccessKey>"
               f"<SessionToken>{s.session_token}</SessionToken><Expiration>{_iso(s.expiration)}</Expiration></Credentials>"
               f"<AssumedRoleUser><AssumedRoleId>AROALOCAL:{escape(s.session_name)}</AssumedRoleId><Arn>{escape(s.arn)}</Arn>"
               f"</AssumedRoleUser><PackedPolicySize>1</PackedPolicySize>{extra}</{action}Result>"
               f"<ResponseMetadata><RequestId>local</RequestId></ResponseMetadata></{action}Response>")
        self._send(200, xml.encode(), {"Content-Type": "text/xml"})


class Endpoint:
    def __init__(self, aws, host="127.0.0.1", port=0, latency_ms=0.0, bandwidth_mbps=None, secure_transport=True):
        self.httpd = ThreadingHTTPServer((host, port), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.aws = aws
        self.httpd.cfg = dict(latency_ms=latency_ms, bandwidth_mbps=bandwidth_mbps, secure_transport=secure_transport)
        self.httpd.requests = 0
        self.httpd.op_counts = {}
        self.httpd.bytes_in = self.httpd.bytes_out = 0      # request / response bodies (portable S3 transfer metrics)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        h, p = self.httpd.server_address[:2]
        return f"http://{h}:{p}"

    @property
    def host_port(self):
        h, p = self.httpd.server_address[:2]
        return f"{h}:{p}"

    def set_latency(self, latency_ms: float, bandwidth_mbps=None):
        self.httpd.cfg.update(latency_ms=latency_ms, bandwidth_mbps=bandwidth_mbps)

    @property
    def requests(self):
        return self.httpd.requests

    def counters(self) -> dict:
        """Snapshot of request and transfer counters; subtract two snapshots to measure one step."""
        return dict(requests=self.httpd.requests, bytes_in=self.httpd.bytes_in, bytes_out=self.httpd.bytes_out,
                    **{f"op_{k}": v for k, v in self.httpd.op_counts.items()})

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
