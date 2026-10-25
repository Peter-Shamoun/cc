"""S3 data plane with the semantics this design leans on.

- Versioning and Object Lock (GOVERNANCE / COMPLIANCE retention, legal hold). Under versioning an
  overwrite makes a new version and a plain DELETE adds a delete marker, both allowed even under
  Object Lock. Raw is made write-once with a bucket-policy Deny on PUTs that lack `If-None-Match: *`
  (condition key s3:if-none-match), plus a Deny on deletes.
- Conditional writes (If-None-Match: *, If-Match: <etag>) are atomic: CURRENT can be flipped
  compare-and-set.
- SSE-KMS with real envelope encryption. The encryption context is the object ARN (or the bucket ARN
  with S3 Bucket Keys). kms:GenerateDataKey / kms:Decrypt are authorized against the key policy, with
  kms:ViaService. SSE-S3 (AES256) uses an S3-owned key.
- Every authorization uses IAM identity, session and bucket policies with S3 condition keys
  (s3:prefix, s3:x-amz-server-side-encryption[-aws-kms-key-id], s3:if-none-match, aws:SecureTransport, ...).
- Ranged GETs, conditional GETs, ListObjectsV2 with delimiter/pagination, ListObjectVersions.
- S3 → EventBridge "Object Created" / "Object Deleted" events. Lifecycle expiry of noncurrent versions.
Bytes on disk are AES-GCM ciphertext. Metadata lives in SQLite.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .errors import (AccessDenied, InvalidArgument, InvalidRange, InvalidRequest, NoSuchBucket, NoSuchKey,
                     NoSuchVersion, NotModified, PreconditionFailed)
from .iam import ACCOUNT, REGION, Principal, decide
from .kms import seal, unseal

VIA_S3 = f"s3.{REGION}.amazonaws.com"
PLAIN_CACHE_MAX_BYTES = 1 << 30                    # decrypted-object cache bound (multi-GB lakes must not OOM)


@dataclass
class RequestContext:
    principal: Principal | None                     # None = anonymous
    session: object | None = None                   # sts.Session for identity / session policies
    secure_transport: bool = True
    source_ip: str = "127.0.0.1"
    auth_type: str = "REST-HEADER"


@dataclass
class Bucket:
    name: str
    versioning: str | None = None                   # None | Enabled | Suspended
    object_lock: bool = False
    default_retention: dict | None = None           # {"Mode": "COMPLIANCE", "Days": 2555}
    policy: dict | None = None
    encryption: dict = field(default_factory=lambda: {"SSEAlgorithm": "AES256"})
    eventbridge: bool = False
    lifecycle: list = field(default_factory=list)   # [{"Prefix": ..., "NoncurrentDays": n, "ExpirationDays": n}]

    @property
    def arn(self):
        return f"arn:aws:s3:::{self.name}"


class _LockedDB:
    """sqlite3 connections are not safe for concurrent use. The endpoint is multi-threaded (DuckDB issues
    parallel range GETs), so every statement runs under one lock and returns materialized rows."""

    class _Result:
        def __init__(self, rows, description):
            self._rows, self.description = rows, description

        def fetchone(self):
            return self._rows[0] if self._rows else None

        def fetchall(self):
            return list(self._rows)

    def __init__(self, path, lock):
        self._con = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._lock = lock

    def execute(self, sql, params=()):
        with self._lock:
            cur = self._con.execute(sql, params)
            return self._Result(cur.fetchall(), cur.description)


class S3:
    def __init__(self, root: Path, clock, iam, kms, events, audit):
        self.root = Path(root)
        (self.root / "s3data").mkdir(parents=True, exist_ok=True)
        self.clock, self.iam, self.kms, self.events, self.audit = clock, iam, kms, events, audit
        self.buckets: dict[str, Bucket] = {}
        self._lock = threading.RLock()
        self._sse_s3_key = AESGCM.generate_key(256)
        self.db = _LockedDB(self.root / "s3.sqlite", self._lock)
        self.db.execute("""CREATE TABLE IF NOT EXISTS objects (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, bucket TEXT, key TEXT, version_id TEXT, is_latest INT,
            delete_marker INT, size INT, etag TEXT, last_modified REAL, sse TEXT, kms_key_arn TEXT, kms_context TEXT,
            data_key BLOB, blob TEXT, content_type TEXT, metadata TEXT, lock_mode TEXT, retain_until REAL,
            legal_hold INT DEFAULT 0, sha256 TEXT)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_latest ON objects(bucket, is_latest, key)")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_key ON objects(bucket, key, seq)")
        self.request_count = 0
        self._plain_cache: dict[str, bytes] = {}                 # blob id -> plaintext (blobs are immutable)
        self._plain_cache_bytes = 0
        self._cols = [d[0] for d in self.db.execute("SELECT * FROM objects LIMIT 0").description]

    # -- control plane ------------------------------------------------------------------------------
    def create_bucket(self, name: str, versioning: str | None = None, object_lock: bool = False,
                      default_retention: dict | None = None, eventbridge: bool = False) -> Bucket:
        if object_lock and versioning != "Enabled":
            raise InvalidRequest("Object Lock requires versioning")
        self.buckets[name] = Bucket(name, versioning, object_lock, default_retention, eventbridge=eventbridge)
        return self.buckets[name]

    def bucket(self, name: str) -> Bucket:
        b = self.buckets.get(name)
        if b is None:
            raise NoSuchBucket(f"The specified bucket does not exist: {name}", BucketName=name)
        return b

    # -- authorization ------------------------------------------------------------------------------
    def _authorize(self, rc: RequestContext, b: Bucket, action: str, resource: str, op: str, req: dict,
                   extra_ctx: dict | None = None):
        p = rc.principal or Principal("anonymous", ("*",), "Anonymous")
        ctx = {"aws:SecureTransport": rc.secure_transport, "aws:SourceIp": rc.source_ip,
               "aws:CurrentTime": self.clock(), "aws:EpochTime": int(self.clock()), "s3:authType": rc.auth_type,
               "s3:signatureversion": "AWS4-HMAC-SHA256", "s3:ResourceAccount": ACCOUNT, **(extra_ctx or {})}
        role = self.iam.role_by_arn(p.role_arn) if p.role_arn else None
        d = decide(p, action, resource, ctx, role.policies if role else [], b.policy,
                   rc.session.session_policies if rc.session else (), role.boundary if role else None)
        if not d:
            self.audit.record("s3.amazonaws.com", op, rc.principal, req, error=("AccessDenied", d.reason),
                              resources=[resource], source_ip=rc.source_ip, decidedBy=d.by)
            raise AccessDenied("Access Denied", RequestedAction=action, Reason=d.reason)
        return d

    def _kms(self, rc: RequestContext, action: str, key, context: dict, op: str):
        p = rc.principal or Principal("anonymous", ("*",), "Anonymous")
        ctx = {"kms:ViaService": VIA_S3, "kms:CallerAccount": ACCOUNT, "aws:CurrentTime": self.clock(),
               **{f"kms:EncryptionContext:{k}": v for k, v in context.items()},
               "kms:EncryptionContextKeys": list(context)}
        role = self.iam.role_by_arn(p.role_arn) if p.role_arn else None
        d = decide(p, action, key.arn, ctx, role.policies if role else [], key.policy,
                   rc.session.session_policies if rc.session else (), role.boundary if role else None,
                   require_resource_allow=True)
        if not d and d.reason != "explicitDeny" and self.kms.grant_allows(key, p, action.split(":")[1], context):
            from .iam import Decision
            d = Decision(True, "allowed", "grant")
        self.audit.record("kms.amazonaws.com", action.split(":")[1], rc.principal,
                          {"keyId": key.arn, "encryptionContext": context}, resources=[key.arn],
                          error=None if d else ("AccessDenied", d.reason), invokedBy=VIA_S3)
        if not d:
            raise AccessDenied(f"User: {p.arn} is not authorized to perform: {action} on resource: {key.arn} "
                               f"because no {'identity-based' if d.reason == 'implicitDeny' else ''} policy allows it",
                               code="AccessDenied", RequestedAction=action)

    def _ok(self, rc, op, req, resource, **resp):
        self.audit.record("s3.amazonaws.com", op, rc.principal, req, resources=[resource], source_ip=rc.source_ip,
                          response=resp or None)

    # -- helpers ------------------------------------------------------------------------------------
    def _latest(self, bucket: str, key: str):
        return self.db.execute("SELECT * FROM objects WHERE bucket=? AND key=? AND is_latest=1", (bucket, key)).fetchone()

    def _row(self, r) -> dict | None:
        if r is None:
            return None
        return dict(zip(self._cols, r))

    def _get_version(self, bucket, key, version_id):
        if version_id:
            r = self.db.execute("SELECT * FROM objects WHERE bucket=? AND key=? AND version_id=?",
                                (bucket, key, version_id)).fetchone()
            if r is None:
                raise NoSuchVersion("The specified version does not exist.", Key=key, VersionId=version_id)
            return self._row(r)
        r = self._row(self._latest(bucket, key))
        if r is None or r["delete_marker"]:
            raise NoSuchKey("The specified key does not exist.", Key=key)
        return r

    def _new_version_id(self, b: Bucket):
        return uuid.uuid4().hex if b.versioning == "Enabled" else "null"

    def _store_version(self, b: Bucket, key: str, row: dict):
        """Insert a version and make it latest. Unversioned / suspended buckets replace the 'null' version."""
        if row["version_id"] == "null":
            old = self.db.execute("SELECT blob FROM objects WHERE bucket=? AND key=? AND version_id='null'",
                                  (b.name, key)).fetchall()
            for (blob,) in old:
                if blob:
                    (self.root / "s3data" / blob).unlink(missing_ok=True)
            self.db.execute("DELETE FROM objects WHERE bucket=? AND key=? AND version_id='null'", (b.name, key))
        self.db.execute("UPDATE objects SET is_latest=0 WHERE bucket=? AND key=?", (b.name, key))
        cols = ", ".join(row)
        self.db.execute(f"INSERT INTO objects (bucket, key, is_latest, {cols}) VALUES (?, ?, 1, {', '.join('?' * len(row))})",
                        (b.name, key, *row.values()))

    def _emit(self, b: Bucket, detail_type: str, key: str, rc: RequestContext, reason: str, **obj):
        if b.eventbridge and self.events is not None:
            self.events.put_event("aws.s3", detail_type, {
                "version": "0", "bucket": {"name": b.name}, "object": {"key": key, **obj},
                "request-id": uuid.uuid4().hex[:16].upper(), "requester": ACCOUNT, "source-ip-address": rc.source_ip,
                "reason": reason}, resources=[b.arn])

    # -- operations ---------------------------------------------------------------------------------
    def put_object(self, rc: RequestContext, Bucket: str, Key: str, Body: bytes, ContentType: str | None = None,
                   ServerSideEncryption: str | None = None, SSEKMSKeyId: str | None = None,
                   BucketKeyEnabled: bool | None = None, IfNoneMatch: str | None = None, IfMatch: str | None = None,
                   Metadata: dict | None = None, ObjectLockMode: str | None = None,
                   ObjectLockRetainUntilDate: float | None = None, ObjectLockLegalHoldStatus: str | None = None,
                   ChecksumSHA256: str | None = None, ContentMD5: str | None = None,
                   _reason: str = "PutObject") -> dict:
        b = self.bucket(Bucket)
        self.request_count += 1
        res = f"{b.arn}/{Key}"
        req = {"bucketName": Bucket, "key": Key}
        if len(Key.encode()) > 1024 or not Key:
            raise InvalidArgument("key length must be 1-1024 bytes")
        extra = {}
        if ServerSideEncryption:
            extra["s3:x-amz-server-side-encryption"] = ServerSideEncryption
        if SSEKMSKeyId:
            extra["s3:x-amz-server-side-encryption-aws-kms-key-id"] = SSEKMSKeyId
        if IfNoneMatch is not None:
            extra["s3:if-none-match"] = IfNoneMatch
        if IfMatch is not None:
            extra["s3:if-match"] = IfMatch
        extra["s3:ObjectCreationOperation"] = True
        if ObjectLockMode:
            extra["s3:object-lock-mode"] = ObjectLockMode
        if ObjectLockRetainUntilDate is not None:
            extra["s3:object-lock-retain-until-date"] = ObjectLockRetainUntilDate
            extra["s3:object-lock-remaining-retention-days"] = int((ObjectLockRetainUntilDate - self.clock()) // 86400)
        if ObjectLockLegalHoldStatus:
            extra["s3:object-lock-legal-hold"] = ObjectLockLegalHoldStatus
        if bool(ObjectLockMode) != (ObjectLockRetainUntilDate is not None):
            raise InvalidArgument("x-amz-object-lock-mode and x-amz-object-lock-retain-until-date must both be supplied")
        if ObjectLockMode and ObjectLockMode not in ("GOVERNANCE", "COMPLIANCE"):
            raise InvalidArgument("Unknown wormMode directive")
        if ObjectLockRetainUntilDate is not None and ObjectLockRetainUntilDate <= self.clock():
            raise InvalidArgument("The retain until date must be in the future")
        if (ObjectLockMode or ObjectLockLegalHoldStatus) and not (ChecksumSHA256 or ContentMD5):
            raise InvalidRequest("Content-MD5 or x-amz-checksum header is required for Put Object requests with "
                                 "Object Lock parameters")
        if ContentMD5 and base64.b64encode(hashlib.md5(Body).digest()).decode() != ContentMD5:
            raise InvalidRequest("The Content-MD5 you specified did not match what we received.", code="BadDigest")
        self._authorize(rc, b, "s3:PutObject", res, "PutObject", req, extra)
        if (ObjectLockMode or ObjectLockRetainUntilDate) and not b.object_lock:
            raise InvalidRequest("Bucket is missing Object Lock Configuration")
        if ObjectLockMode or ObjectLockRetainUntilDate:
            self._authorize(rc, b, "s3:PutObjectRetention", res, "PutObject", req, extra)
        if ObjectLockLegalHoldStatus:
            self._authorize(rc, b, "s3:PutObjectLegalHold", res, "PutObject", req, extra)
        if ChecksumSHA256 and base64.b64encode(hashlib.sha256(Body).digest()).decode() != ChecksumSHA256:
            raise InvalidRequest("The SHA256 you specified did not match the calculated checksum.", code="BadDigest")
        sse = ServerSideEncryption or b.encryption.get("SSEAlgorithm", "AES256")
        key_ref = SSEKMSKeyId or (b.encryption.get("KMSMasterKeyID") if sse == "aws:kms" else None)
        if SSEKMSKeyId and sse != "aws:kms":
            raise InvalidArgument("SSEKMSKeyId requires ServerSideEncryption aws:kms")
        bucket_key = b.encryption.get("BucketKeyEnabled", False) if BucketKeyEnabled is None else BucketKeyEnabled
        with self._lock:                                          # conditional write + version swap are atomic
            cur = self._row(self._latest(Bucket, Key))
            live = cur if cur and not cur["delete_marker"] else None
            if IfNoneMatch is not None:
                if IfNoneMatch != "*":
                    raise InvalidArgument("If-None-Match on PUT only supports '*'")
                if live:
                    self.audit.record("s3.amazonaws.com", "PutObject", rc.principal, req,
                                      error=("PreconditionFailed", "object exists"), resources=[res])
                    raise PreconditionFailed("At least one of the pre-conditions you specified did not hold",
                                             Condition="If-None-Match")
            if IfMatch is not None:
                if live is None:
                    raise NoSuchKey("The specified key does not exist.", Key=Key)
                if live["etag"] != IfMatch.strip('"'):
                    raise PreconditionFailed("At least one of the pre-conditions you specified did not hold",
                                             Condition="If-Match")
            context = {}
            if sse == "aws:kms":
                k = self.kms.resolve(key_ref) if key_ref else None
                if k is None:
                    raise InvalidArgument("no KMS key: set SSEKMSKeyId or bucket default encryption")
                context = {"aws:s3:arn": b.arn if bucket_key else res}
                self._kms(rc, "kms:GenerateDataKey", k, context, "PutObject")
                data_key, blob_key, k = self.kms.generate_data_key(k.key_id, context)
                kms_arn = k.arn
            else:
                data_key = AESGCM.generate_key(256)
                nonce = os.urandom(12)
                blob_key = nonce + AESGCM(self._sse_s3_key).encrypt(nonce, data_key, None)
                kms_arn = None
            blob = uuid.uuid4().hex
            (self.root / "s3data" / blob).write_bytes(seal(data_key, Body))
            etag = hashlib.md5(Body).hexdigest()
            vid = self._new_version_id(b)
            lock_mode = ObjectLockMode
            retain = ObjectLockRetainUntilDate
            if b.object_lock and b.default_retention and not lock_mode:
                lock_mode = b.default_retention["Mode"]
                retain = self.clock() + b.default_retention["Days"] * 86400
            self._store_version(b, Key, dict(version_id=vid, delete_marker=0, size=len(Body), etag=etag,
                                             last_modified=self.clock(), sse=sse, kms_key_arn=kms_arn,
                                             kms_context=json.dumps(context), data_key=blob_key, blob=blob,
                                             content_type=ContentType or "binary/octet-stream",
                                             metadata=json.dumps(Metadata or {}), lock_mode=lock_mode, retain_until=retain,
                                             legal_hold=1 if ObjectLockLegalHoldStatus == "ON" else 0,
                                             sha256=hashlib.sha256(Body).hexdigest()))
            seq = self.db.execute("SELECT max(seq) FROM objects").fetchone()[0]
        out = {"ETag": f'"{etag}"', "ServerSideEncryption": sse}
        if kms_arn:
            out["SSEKMSKeyId"] = kms_arn
            out["BucketKeyEnabled"] = bool(bucket_key)
        if b.versioning:
            out["VersionId"] = vid
        self._ok(rc, "PutObject", req, res, **{k: v for k, v in out.items() if k != "ETag"})
        self._emit(b, "Object Created", Key, rc, _reason, size=len(Body), etag=etag,
                   **({"version-id": vid} if b.versioning else {}), sequencer=f"{seq:016X}")
        return out

    def _decrypt_body(self, rc, row) -> bytes:
        if row["sse"] == "aws:kms":
            ctx = json.loads(row["kms_context"])
            key = self.kms.key_for_blob(row["data_key"])
            self._kms(rc, "kms:Decrypt", key, ctx, "GetObject")          # authorized on every request
            hit = self._plain_cache.get(row["blob"])
            if hit is not None and key.state == "Enabled":
                return hit
            data_key = self.kms.decrypt(row["data_key"], ctx)
        else:
            dk = row["data_key"]
            data_key = AESGCM(self._sse_s3_key).decrypt(dk[:12], dk[12:], None)
        body = unseal(data_key, (self.root / "s3data" / row["blob"]).read_bytes())
        with self._lock:                                          # small LRU: parquet readers issue many ranged GETs
            self._plain_cache[row["blob"]] = body
            self._plain_cache_bytes += len(body)
            while len(self._plain_cache) > 256 or self._plain_cache_bytes > PLAIN_CACHE_MAX_BYTES:
                self._plain_cache_bytes -= len(self._plain_cache.pop(next(iter(self._plain_cache))))
        return body

    def _head(self, rc, Bucket, Key, VersionId, op, IfMatch=None, IfNoneMatch=None):
        b = self.bucket(Bucket)
        self.request_count += 1
        res = f"{b.arn}/{Key}"
        action = "s3:GetObjectVersion" if VersionId else "s3:GetObject"
        extra = {"s3:versionid": VersionId} if VersionId else {}
        self._authorize(rc, b, action, res, op, {"bucketName": Bucket, "key": Key, "versionId": VersionId}, extra)
        try:
            row = self._get_version(Bucket, Key, VersionId)
        except NoSuchKey:
            # S3 returns 403 (not 404) when the caller lacks s3:ListBucket, to avoid leaking existence
            try:
                self._authorize(rc, b, "s3:ListBucket", b.arn, op, {"bucketName": Bucket}, {"s3:prefix": Key})
            except AccessDenied:
                raise AccessDenied("Access Denied") from None
            raise
        if row["delete_marker"]:
            raise InvalidRequest("The specified method is not allowed against a delete marker", code="MethodNotAllowed",
                                 status=405)
        if IfMatch is not None and IfMatch.strip('"') != row["etag"]:
            raise PreconditionFailed("If-Match failed", Condition="If-Match")
        if IfNoneMatch is not None and IfNoneMatch.strip('"') == row["etag"]:
            raise NotModified("Not Modified")
        return b, res, row

    def _meta(self, b, row) -> dict:
        out = {"ContentLength": row["size"], "ETag": f'"{row["etag"]}"', "LastModified": row["last_modified"],
               "ContentType": row["content_type"], "ServerSideEncryption": row["sse"],
               "Metadata": json.loads(row["metadata"] or "{}")}
        if row["kms_key_arn"]:
            out["SSEKMSKeyId"] = row["kms_key_arn"]
        if b.versioning:
            out["VersionId"] = row["version_id"]
        if row["lock_mode"]:
            out["ObjectLockMode"], out["ObjectLockRetainUntilDate"] = row["lock_mode"], row["retain_until"]
        if row["legal_hold"]:
            out["ObjectLockLegalHoldStatus"] = "ON"
        return out

    def head_object(self, rc: RequestContext, Bucket: str, Key: str, VersionId: str | None = None,
                    IfMatch=None, IfNoneMatch=None) -> dict:
        b, res, row = self._head(rc, Bucket, Key, VersionId, "HeadObject", IfMatch, IfNoneMatch)
        self._ok(rc, "HeadObject", {"bucketName": Bucket, "key": Key}, res)
        return self._meta(b, row)

    def get_object(self, rc: RequestContext, Bucket: str, Key: str, VersionId: str | None = None,
                   Range: str | None = None, IfMatch=None, IfNoneMatch=None) -> dict:
        b, res, row = self._head(rc, Bucket, Key, VersionId, "GetObject", IfMatch, IfNoneMatch)
        body = self._decrypt_body(rc, row)
        out = self._meta(b, row)
        if Range and parse_range(Range, len(body)) is not None:
            start, end = parse_range(Range, len(body))
            out["ContentRange"] = f"bytes {start}-{end}/{len(body)}"
            body = body[start:end + 1]
            out["ContentLength"] = len(body)
        out["Body"] = body
        self._ok(rc, "GetObject", {"bucketName": Bucket, "key": Key, **({"Range": Range} if Range else {})}, res,
                 bytesTransferredOut=len(body))
        return out

    def list_objects_v2(self, rc: RequestContext, Bucket: str, Prefix: str = "", Delimiter: str | None = None,
                        MaxKeys: int = 1000, ContinuationToken: str | None = None, StartAfter: str | None = None) -> dict:
        b = self.bucket(Bucket)
        self.request_count += 1
        extra = {"s3:prefix": Prefix, "s3:max-keys": MaxKeys}
        if Delimiter:
            extra["s3:delimiter"] = Delimiter
        self._authorize(rc, b, "s3:ListBucket", b.arn, "ListObjectsV2", {"bucketName": Bucket, "prefix": Prefix}, extra)
        MaxKeys = max(0, min(int(MaxKeys), 1000))
        after = base64.urlsafe_b64decode(ContinuationToken).decode() if ContinuationToken else (StartAfter or "")
        lo = max(Prefix, after) if after else Prefix
        rows = self.db.execute(
            "SELECT key, size, etag, last_modified FROM objects WHERE bucket=? AND is_latest=1 AND delete_marker=0 "
            "AND key >= ? AND key < ? ORDER BY key", (Bucket, lo, Prefix + "\U0010ffff")).fetchall()
        rows.sort(key=lambda r: r[0].encode())                    # S3 orders by UTF-8 bytes
        contents, prefixes, last, truncated = [], [], None, False
        seen_prefixes = set()
        for key, size, etag, lm in rows:
            if after and key.encode() <= after.encode():
                continue
            entry_key = key
            if Delimiter:
                rest = key[len(Prefix):]
                i = rest.find(Delimiter)
                if i >= 0:
                    cp = Prefix + rest[: i + len(Delimiter)]
                    if cp in seen_prefixes:
                        continue
                    if after and cp.encode() <= after.encode():
                        continue
                    if len(contents) + len(prefixes) >= MaxKeys:
                        truncated = True
                        break
                    seen_prefixes.add(cp)
                    prefixes.append({"Prefix": cp})
                    last = cp
                    continue
            if len(contents) + len(prefixes) >= MaxKeys:
                truncated = True
                break
            contents.append({"Key": entry_key, "Size": size, "ETag": f'"{etag}"', "LastModified": lm,
                             "StorageClass": "STANDARD"})
            last = entry_key
        if MaxKeys == 0:
            truncated = False
        out = {"Name": Bucket, "Prefix": Prefix, "MaxKeys": MaxKeys, "KeyCount": len(contents) + len(prefixes),
               "IsTruncated": truncated, "Contents": contents, "CommonPrefixes": prefixes}
        if Delimiter:
            out["Delimiter"] = Delimiter
        if truncated and last is not None:
            out["NextContinuationToken"] = base64.urlsafe_b64encode(last.encode()).decode()
        if ContinuationToken:
            out["ContinuationToken"] = ContinuationToken
        self._ok(rc, "ListObjectsV2", {"bucketName": Bucket, "prefix": Prefix}, b.arn)
        return out

    def list_object_versions(self, rc: RequestContext, Bucket: str, Prefix: str = "") -> dict:
        b = self.bucket(Bucket)
        self.request_count += 1
        self._authorize(rc, b, "s3:ListBucketVersions", b.arn, "ListObjectVersions", {"bucketName": Bucket},
                        {"s3:prefix": Prefix})
        rows = [self._row(r) for r in self.db.execute(
            "SELECT * FROM objects WHERE bucket=? AND substr(key, 1, ?) = ? ORDER BY key, seq DESC",
            (Bucket, len(Prefix), Prefix)).fetchall()]
        out = {"Versions": [], "DeleteMarkers": []}
        for r in rows:
            e = {"Key": r["key"], "VersionId": r["version_id"], "IsLatest": bool(r["is_latest"]),
                 "LastModified": r["last_modified"]}
            if r["delete_marker"]:
                out["DeleteMarkers"].append(e)
            else:
                out["Versions"].append({**e, "Size": r["size"], "ETag": f'"{r["etag"]}"'})
        self._ok(rc, "ListObjectVersions", {"bucketName": Bucket, "prefix": Prefix}, b.arn)
        return out

    def delete_object(self, rc: RequestContext, Bucket: str, Key: str, VersionId: str | None = None,
                      BypassGovernanceRetention: bool = False) -> dict:
        b = self.bucket(Bucket)
        self.request_count += 1
        res = f"{b.arn}/{Key}"
        req = {"bucketName": Bucket, "key": Key, "versionId": VersionId}
        action = "s3:DeleteObjectVersion" if VersionId else "s3:DeleteObject"
        self._authorize(rc, b, action, res, "DeleteObject", req, {"s3:versionid": VersionId} if VersionId else {})
        with self._lock:
            if VersionId is None and b.versioning in ("Enabled", "Suspended"):
                vid = uuid.uuid4().hex if b.versioning == "Enabled" else "null"
                if vid == "null":
                    self._purge_null(b, Key, rc, BypassGovernanceRetention)
                self._store_version(b, Key, dict(version_id=vid, delete_marker=1, size=0, etag="", last_modified=self.clock()))
                self._ok(rc, "DeleteObject", req, res, deleteMarker=True)
                self._emit(b, "Object Deleted", Key, rc, "DeleteObject", **{"version-id": vid},
                           **{"deletion-type": "Delete Marker Created"})
                return {"DeleteMarker": True, "VersionId": vid}
            target = VersionId or "null"
            row = self._row(self.db.execute("SELECT * FROM objects WHERE bucket=? AND key=? AND version_id=?",
                                            (Bucket, Key, target)).fetchone())
            if row is None:
                self._ok(rc, "DeleteObject", req, res)
                return {}                                         # S3 deletes are idempotent
            self._check_lock(rc, b, row, res, BypassGovernanceRetention)
            self.db.execute("DELETE FROM objects WHERE seq=?", (row["seq"],))
            if row["blob"]:
                (self.root / "s3data" / row["blob"]).unlink(missing_ok=True)
            if row["is_latest"]:
                nxt = self.db.execute("SELECT seq FROM objects WHERE bucket=? AND key=? ORDER BY seq DESC LIMIT 1",
                                      (Bucket, Key)).fetchone()
                if nxt:
                    self.db.execute("UPDATE objects SET is_latest=1 WHERE seq=?", nxt)
        self._ok(rc, "DeleteObject", req, res)
        self._emit(b, "Object Deleted", Key, rc, "DeleteObject", **{"deletion-type": "Permanently Deleted"})
        return {"VersionId": target} if b.versioning else {}

    def _purge_null(self, b, Key, rc, bypass):
        row = self._row(self.db.execute("SELECT * FROM objects WHERE bucket=? AND key=? AND version_id='null'",
                                        (b.name, Key)).fetchone())
        if row:
            self._check_lock(rc, b, row, f"{b.arn}/{Key}", bypass)

    def _check_lock(self, rc, b, row, res, bypass):
        if row["legal_hold"]:
            raise AccessDenied("Access Denied because object protected by object lock (legal hold)")
        if row["retain_until"] and row["retain_until"] > self.clock():
            if row["lock_mode"] == "COMPLIANCE":
                raise AccessDenied("Access Denied because object protected by object lock")
            if not bypass:
                raise AccessDenied("Access Denied because object protected by object lock")
            self._authorize(rc, b, "s3:BypassGovernanceRetention", res, "DeleteObject", {"key": row["key"]})

    def copy_object(self, rc: RequestContext, Bucket: str, Key: str, CopySource: dict, **put_kwargs) -> dict:
        put_kwargs = {k: v for k, v in put_kwargs.items() if v not in (None, {})}
        if "IfNoneMatch" in put_kwargs or "IfMatch" in put_kwargs:
            from .errors import NotImplementedOp
            raise NotImplementedOp("A header you provided implies functionality that is not implemented (conditional CopyObject)")
        src = self.get_object(rc, CopySource["Bucket"], CopySource["Key"], CopySource.get("VersionId"))
        put_kwargs.setdefault("ContentType", src.get("ContentType"))
        put_kwargs.setdefault("Metadata", src.get("Metadata"))      # MetadataDirective=COPY
        out = self.put_object(rc, Bucket, Key, src["Body"], _reason="CopyObject", **put_kwargs)
        return {"CopyObjectResult": {"ETag": out["ETag"]}, **{k: v for k, v in out.items() if k != "ETag"}}

    # -- bucket configuration calls that a principal (not Terraform bootstrap) makes -------------------
    def put_bucket_policy(self, rc: RequestContext, Bucket: str, Policy: dict | str):
        from .iam import validate_policy
        b = self.bucket(Bucket)
        doc = json.loads(Policy) if isinstance(Policy, str) else Policy
        self._authorize(rc, b, "s3:PutBucketPolicy", b.arn, "PutBucketPolicy", {"bucketName": Bucket})
        set_bucket_policy(self, b, doc)
        self._ok(rc, "PutBucketPolicy", {"bucketName": Bucket}, b.arn)

    def delete_bucket_policy(self, rc: RequestContext, Bucket: str):
        b = self.bucket(Bucket)
        self._authorize(rc, b, "s3:DeleteBucketPolicy", b.arn, "DeleteBucketPolicy", {"bucketName": Bucket})
        b.policy = None
        self._ok(rc, "DeleteBucketPolicy", {"bucketName": Bucket}, b.arn)

    def put_bucket_lifecycle_configuration(self, rc: RequestContext, Bucket: str, Rules: list):
        b = self.bucket(Bucket)
        self._authorize(rc, b, "s3:PutLifecycleConfiguration", b.arn, "PutBucketLifecycleConfiguration", {"bucketName": Bucket})
        b.lifecycle = list(Rules)

    # -- lifecycle (S3 runs this daily; tests call it) ------------------------------------------------
    def run_lifecycle(self) -> int:
        removed = 0
        now = self.clock()
        for b in self.buckets.values():
            for rule in b.lifecycle:
                pre = rule.get("Prefix", "")
                if rule.get("ExpirationDays"):
                    # Current-version expiry, the way S3 does it on a versioned bucket: a delete marker, not an
                    # immediate byte wipe. NoncurrentDays on the same rule then drops the bytes once they age out.
                    # Object-lock retention and legal hold win, so audit objects are not expired this way.
                    days = rule["ExpirationDays"]
                    current = self.db.execute(
                        "SELECT key, last_modified, retain_until, legal_hold FROM objects WHERE bucket=? "
                        "AND is_latest=1 AND delete_marker=0 AND substr(key,1,?)=?",
                        (b.name, len(pre), pre)).fetchall()
                    for key, modified, retain, hold in current:
                        if hold or (retain and retain > now) or modified is None:
                            continue
                        if now - modified >= days * 86400:
                            self._store_version(b, key, dict(version_id=self._new_version_id(b), delete_marker=1,
                                                             size=0, etag="", last_modified=now))
                            removed += 1
                if "NoncurrentDays" in rule:
                    rows = self.db.execute(
                        "SELECT o.seq, o.blob, o.key, (SELECT min(n.last_modified) FROM objects n WHERE n.bucket=o.bucket "
                        "AND n.key=o.key AND n.seq > o.seq) AS became_noncurrent, o.retain_until, o.legal_hold "
                        "FROM objects o WHERE o.bucket=? AND o.is_latest=0 AND substr(o.key,1,?)=?",
                        (b.name, len(pre), pre)).fetchall()
                    for seq, blob, key, since, retain, hold in rows:
                        if since and now - since >= rule["NoncurrentDays"] * 86400 and not hold and not (retain and retain > now):
                            self.db.execute("DELETE FROM objects WHERE seq=?", (seq,))
                            if blob:
                                (self.root / "s3data" / blob).unlink(missing_ok=True)
                            removed += 1
                if rule.get("ExpiredObjectDeleteMarker"):
                    for seq, key in self.db.execute(
                            "SELECT seq, key FROM objects o WHERE bucket=? AND is_latest=1 AND delete_marker=1 AND "
                            "substr(key,1,?)=? AND NOT EXISTS (SELECT 1 FROM objects n WHERE n.bucket=o.bucket AND "
                            "n.key=o.key AND n.seq<>o.seq)", (b.name, len(pre), pre)).fetchall():
                        self.db.execute("DELETE FROM objects WHERE seq=?", (seq,))
                        removed += 1
        return removed


MAX_BUCKET_POLICY_BYTES = 20 * 1024


def set_bucket_policy(s3: "S3", b: Bucket, doc: dict):
    """S3's write-time checks: valid policy, existing principals, 20 KB size limit."""
    from .errors import AWSError
    from .iam import MalformedPolicy, validate_policy
    try:
        validate_policy(doc, resource_policy=True, principal_exists=s3.iam.role_exists)
    except MalformedPolicy as e:
        raise AWSError(str(e), code="MalformedPolicy", status=400) from None
    size = len(json.dumps(doc, separators=(",", ":")))
    if size > MAX_BUCKET_POLICY_BYTES:
        raise AWSError(f"Policies must be less than 20 KB ({size} bytes)", code="PolicyTooLarge", status=400)
    b.policy = doc


def parse_range(header: str, size: int) -> tuple[int, int] | None:
    """None = ignore the header and return the whole object (what S3 does for malformed or inverted ranges)."""
    if not header.startswith("bytes=") or "," in header:
        return None
    a, _, b = header[6:].partition("-")
    try:
        if a == "":
            n = int(b)
            if n <= 0:
                raise InvalidRange("The requested range is not satisfiable")
            start, end = max(0, size - n), size - 1
        else:
            start = int(a)
            end = int(b) if b else size - 1
    except ValueError:
        return None
    if start > end:
        return None
    if start >= size:
        raise InvalidRange("The requested range is not satisfiable", ActualObjectSize=size)
    return start, min(end, size - 1)
