"""One customer's lake on S3: key layout, silver file liveness, gold manifests, and the per-customer batch lock.

Silver, s3://<bucket>/<customer>/parquet/<table>/received_month=YYYY-MM/
    part-<run>.parquet          one per table per batch, written straight to silver (no copy step)
    compacted-<run>.parquet     the month's parts merged once the month has closed; covers every part with run <= <run>
  A batch writes edi.source_file last: that part is the batch's commit record. Parts of a run without one (a
  batch that crashed mid-write) are garbage, and so are parts and older compacted files a compacted file covers.
  The next batch deletes garbage while it holds the lock, so a crash never leaves rows visible twice.

Gold, s3://<bucket>/<customer>/semantic_layer/
    CURRENT                                   "v=<run>"; compare-and-set, only ever forward
    v=<run>/manifest.json                     one gold version: every data file of every relation + build state
    <relation>/<partition>/part-<run>.parquet immutable data files, shared by versions until a version replaces them
  Readers open CURRENT, then that version's manifest, and read exactly the files it lists. A data file is deleted
  only after two newer versions stopped referencing it, so a reader that opened the previous version still works.

Batch lock, s3://<bucket>/<customer>/staging/LOCK: a lease changed only by conditional writes (BatchLock). It
serialises one customer's batches; a task re-checks it before every shared write and stops if it was taken over.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field

from botocore.exceptions import ClientError

LOCK_LEASE_SECONDS = 2 * 3600                    # longer than any batch; a crashed holder's lock expires after this
LOCK_WAIT_SECONDS = 15 * 60
COMMIT_TABLE = "edi.source_file"


def run_day(run_id: str) -> str:
    """run_<started %Y%m%dT%H%M%S%f>_<YYYYMMDD received day> -> 'YYYY-MM-DD'."""
    d = run_id.rsplit("_", 1)[1]
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def run_month(run_id: str) -> str:
    return run_day(run_id)[:7]


class Store:
    """The parser's S3 client bound to one customer's prefix and KMS key."""

    def __init__(self, s3, bucket: str, customer: str, kms_key_arn: str):
        self.s3, self.bucket, self.customer, self.kms = s3, bucket, customer, kms_key_arn
        self.root = f"{customer}/"

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    def put(self, key: str, body: bytes, **kw) -> dict:
        return self.s3.put_object(Bucket=self.bucket, Key=key, Body=body, ServerSideEncryption="aws:kms",
                                  SSEKMSKeyId=self.kms, **kw)

    def get(self, key: str) -> tuple[bytes, str] | None:
        try:
            r = self.s3.get_object(Bucket=self.bucket, Key=key)
        except ClientError as err:
            if err.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise
        return r["Body"].read(), r["ETag"]

    def list(self, prefix: str) -> list[tuple[str, int]]:
        out, token = [], None
        while True:
            r = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix, **({"ContinuationToken": token} if token else {}))
            out += [(o["Key"], o["Size"]) for o in r.get("Contents", [])]
            if not r.get("IsTruncated"):
                return out
            token = r["NextContinuationToken"]

    def delete(self, keys):
        keys = sorted(set(keys))
        for i in range(0, len(keys), 1000):                       # DeleteObjects: one request per 1,000 keys
            r = self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]],
                                                                   "Quiet": True})
            if r.get("Errors"):
                raise RuntimeError(f"delete failed: {r['Errors'][:3]}")


# --- batch lock ------------------------------------------------------------------------------------------------
class LockLost(RuntimeError):
    """Another task took the batch lock over (this one overran its lease): stop before writing anything shared."""


class BatchLock:
    """The customer's batch lock: a lease in one S3 object, changed only by conditional writes.

    acquire: create with If-None-Match, or take an expired lease over with If-Match on its ETag.
    renew:   rewrite with If-Match on our own ETag; fails (LockLost) if anyone else has written it since. The task
             renews before every write other batches depend on (the silver commit record, CURRENT), so a task that
             overran its lease never commits after the lease moved on.
    release: overwrite with an already-expired lease, again with If-Match; never removes another holder's lease.
    """

    def __init__(self, store: Store, holder: str, clock):
        self.store, self.holder, self.clock = store, holder, clock
        self.key = f"{store.root}staging/LOCK"
        self.etag: str | None = None

    def _body(self, expires: float) -> bytes:                # the nonce makes every write's ETag unique
        return json.dumps({"holder": self.holder, "expires": expires, "nonce": uuid.uuid4().hex}).encode()

    def acquire(self, wait_seconds: float = LOCK_WAIT_SECONDS) -> "BatchLock":
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                self.etag = self.store.put(self.key, self._body(self.clock() + LOCK_LEASE_SECONDS), IfNoneMatch="*")["ETag"]
                return self
            except ClientError as err:
                if err.response["Error"]["Code"] != "PreconditionFailed":
                    raise
            cur = self.store.get(self.key)
            if cur is not None and json.loads(cur[0])["expires"] < self.clock():
                try:                                              # released, or the holder crashed: take it over
                    self.etag = self.store.put(self.key, self._body(self.clock() + LOCK_LEASE_SECONDS),
                                               IfMatch=cur[1])["ETag"]
                    return self
                except ClientError as err:
                    if err.response["Error"]["Code"] not in ("PreconditionFailed", "NoSuchKey"):
                        raise
            if time.monotonic() > deadline:
                raise TimeoutError(f"batch lock {self.key} is held by {cur and json.loads(cur[0])['holder']}")
            time.sleep(1)

    def renew(self):
        try:
            self.etag = self.store.put(self.key, self._body(self.clock() + LOCK_LEASE_SECONDS), IfMatch=self.etag)["ETag"]
        except ClientError as err:
            if err.response["Error"]["Code"] in ("PreconditionFailed", "NoSuchKey"):
                raise LockLost(f"{self.holder} lost the batch lock {self.key}") from None
            raise

    def release(self):
        try:
            self.store.put(self.key, self._body(0), IfMatch=self.etag)
        except ClientError as err:
            if err.response["Error"]["Code"] not in ("PreconditionFailed", "NoSuchKey"):
                raise                                             # lost it already: someone else holds it now


# --- silver ----------------------------------------------------------------------------------------------------
@dataclass
class Silver:
    """Live silver files, from one listing of parquet/."""
    live: dict = field(default_factory=dict)          # table -> month -> [key]
    sizes: dict = field(default_factory=dict)         # key -> bytes
    committed_runs: set = field(default_factory=set)  # runs with a commit record (part or compacted)
    garbage: list = field(default_factory=list)

    def files(self, table: str, months=None) -> list[str]:
        by_month = self.live.get(table, {})
        return [k for m in sorted(by_month) if months is None or m in months for k in by_month[m]]

    def months(self) -> list[str]:
        return sorted({m for by_month in self.live.values() for m in by_month})

    def parts_of(self, table: str, run_ids) -> list[str]:
        """Part files of these runs (they are uncompacted: compaction only covers runs gold already includes)."""
        names = {f"part-{r}.parquet" for r in run_ids}
        return [k for k in self.files(table) if k.rsplit("/", 1)[1] in names]


def _file_run(name: str) -> tuple[str, str]:
    kind, run = name.removesuffix(".parquet").split("-", 1)
    return kind, run


def silver_state(store: Store) -> Silver:
    """parquet/<table>/received_month=M/{part,compacted}-<run>.parquet -> live files per table and month."""
    entries = {}                                                      # (table, month) -> [(kind, run, key)]
    sizes = {}
    for key, size in store.list(f"{store.root}parquet/"):
        _, _, table, part, name = key.split("/")
        entries.setdefault((table, part.split("=", 1)[1]), []).append((*_file_run(name), key))
        sizes[key] = size
    through = {}                                                      # month -> newest compacted run of the commit table
    committed = set()
    for (table, month), files in entries.items():
        if table == COMMIT_TABLE:
            committed |= {run for kind, run, _ in files if kind == "part"}
            through[month] = max((run for kind, run, _ in files if kind == "compacted"), default="")
    s = Silver(sizes=sizes)
    for (table, month), files in entries.items():
        newest = max((run for kind, run, _ in files if kind == "compacted"), default="")
        for kind, run, key in sorted(files):
            if kind == "compacted":
                live = run == newest
            else:
                live = run > newest and (run in committed or run <= through.get(month, ""))
            if live:
                s.live.setdefault(table, {}).setdefault(month, []).append(key)
            else:
                s.garbage.append(key)
    s.committed_runs = committed
    for month, run in through.items():
        if run:
            s.committed_runs.add(run)
    return s


# --- gold ------------------------------------------------------------------------------------------------------
def gold_key(store: Store, relation: str, partition: str, run_id: str, suffix: str = "") -> str:
    return f"{store.root}semantic_layer/{relation}/{partition}/part-{run_id}{suffix}.parquet"


def manifest_key(store: Store, version: str) -> str:
    return f"{store.root}semantic_layer/{version}/manifest.json"


def read_current(store: Store) -> tuple[str | None, str | None]:
    """(version 'v=<run>' or None, ETag for the compare-and-set)."""
    got = store.get(f"{store.root}semantic_layer/CURRENT")
    return (got[0].decode(), got[1]) if got else (None, None)


def read_manifest(store: Store, version: str) -> dict | None:
    got = store.get(manifest_key(store, version))
    return json.loads(got[0]) if got else None


def manifest_files(manifest: dict | None) -> set[str]:
    if not manifest:
        return set()
    return {k for parts in manifest["relations"].values() for keys in parts.values() for k in keys}


def check_manifest(store: Store, manifest: dict):
    """Fail before publishing a version that lists a file that does not exist, or a file it also retires."""
    live = manifest_files(manifest)
    both = live & {k for k, _ in manifest["retired"]}
    missing = live - {k for k, _ in store.list(f"{store.root}semantic_layer/")}
    if both or missing:
        raise RuntimeError(f"gold manifest {manifest['version']} is inconsistent: retired and live {sorted(both)[:3]}, "
                           f"missing {sorted(missing)[:3]}")


def publish(store: Store, manifest: dict, base_etag: str | None) -> bool:
    """Write the manifest, then move CURRENT to it with a compare-and-set against the version the build started
    from. False if another build published in between (this build's base is stale; its data files are orphans)."""
    store.put(manifest_key(store, manifest["version"]), json.dumps(manifest, indent=0).encode())
    cond = {"IfMatch": base_etag} if base_etag else {"IfNoneMatch": "*"}
    try:
        store.put(f"{store.root}semantic_layer/CURRENT", manifest["version"].encode(), **cond)
    except ClientError as err:
        if err.response["Error"]["Code"] != "PreconditionFailed":
            raise
        return False
    return True
