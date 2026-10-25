"""Hash-chained JSONL. Each record's hash covers its body and the previous hash, so editing,
reordering, or inserting a line breaks verification. Truncating a suffix is not detected: the tip
lives in the same file the writer controls."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

GENESIS = "0" * 64


class AuditTamper(ValueError):
    pass


def _normalize(body: dict) -> dict:
    return json.loads(json.dumps(body, default=str))


def seal(prev_hash: str, body: dict, seq: int) -> dict:
    """Return a copy of `body` with seq, prev_hash, and hash. `body` must not already carry those keys."""
    rec = _normalize(body)
    rec["seq"] = seq
    rec["prev_hash"] = prev_hash
    blob = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    rec["hash"] = hashlib.sha256(blob.encode()).hexdigest()
    return rec


def verify_lines(lines: list[str]) -> int:
    prev = GENESIS
    for i, line in enumerate(lines):
        if not line.strip():
            raise AuditTamper(f"blank audit line at {i}")
        rec = json.loads(line)
        got = rec.pop("hash", None)
        if rec.get("seq") != i or rec.get("prev_hash") != prev or not got:
            raise AuditTamper(f"audit chain broken at record {i}")
        blob = json.dumps(rec, sort_keys=True, separators=(",", ":"))
        if hashlib.sha256(blob.encode()).hexdigest() != got:
            raise AuditTamper(f"audit hash mismatch at record {i}")
        prev = got
    return len(lines)


class HashChainLog:
    def __init__(self, path: Path, clock=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or time.time
        self._lock = threading.Lock()
        self._prev = GENESIS
        self._seq = 0
        if self.path.exists() and self.path.stat().st_size:
            last = json.loads(self.path.read_text().splitlines()[-1])
            self._prev = last["hash"]
            self._seq = last["seq"] + 1
        self._fh = open(self.path, "a", buffering=1)

    def append(self, record: dict) -> dict:
        body = {k: v for k, v in record.items() if k not in ("hash", "prev_hash", "seq")}
        if "eventTime" not in body:
            body["eventTime"] = self.clock()
        with self._lock:
            sealed = seal(self._prev, body, self._seq)
            self._fh.write(json.dumps(sealed, default=str) + "\n")
            self._fh.flush()
            self._prev = sealed["hash"]
            self._seq += 1
            return sealed

    def events(self) -> list[dict]:
        with self._lock:
            self._fh.flush()
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def verify(self) -> int:
        with self._lock:
            self._fh.flush()
        # Keep blank lines. Dropping them would let a blank insertion pass as a valid chain.
        text = self.path.read_text() if self.path.exists() else ""
        if not text:
            return 0
        return verify_lines(text.splitlines())
