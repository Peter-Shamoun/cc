"""CloudTrail: one JSON record per API call, allowed or denied, in CloudTrail's record schema
(eventSource, eventName, userIdentity, requestParameters, errorCode, resources ...)."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..audit_chain import GENESIS, seal, verify_lines
from .iam import ACCOUNT, REGION, Principal

READ_ONLY = {"GetObject", "HeadObject", "ListObjectsV2", "ListObjectVersions", "GetCallerIdentity", "Decrypt"}


class CloudTrail:
    def __init__(self, path: Path, clock):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._prev, self._seq = GENESIS, 0
        if self.path.exists() and self.path.stat().st_size:
            last = json.loads([ln for ln in self.path.read_text().splitlines() if ln.strip()][-1])
            self._prev, self._seq = last["hash"], last["seq"] + 1
        self._fh = open(self.path, "a", buffering=1 << 16)

    def record(self, event_source: str, event_name: str, principal: Principal | None, request: dict,
               error: tuple | None = None, resources=(), response: dict | None = None, source_ip: str = "127.0.0.1",
               category: str | None = None, **extra):
        p = principal
        ident = {"type": "Anonymous"} if p is None else {
            "type": p.kind, "arn": p.arn, "accountId": p.account,
            **({"sessionContext": {"sessionIssuer": {"type": "Role", "arn": p.role_arn},
                                   "attributes": {"creationDate": p.token_issue_time}},
                "principalTags": p.tags} if p.kind == "AssumedRole" else {}),
            **({"invokedBy": p.service} if p.kind == "Service" else {})}
        rec = {"eventVersion": "1.09", "eventTime": datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(),
               "eventSource": event_source, "eventName": event_name, "awsRegion": REGION, "sourceIPAddress": source_ip,
               "userIdentity": ident, "requestParameters": request, "responseElements": response,
               "requestID": uuid.uuid4().hex[:16].upper(), "eventID": str(uuid.uuid4()),
               "readOnly": event_name in READ_ONLY, "recipientAccountId": ACCOUNT,
               "eventCategory": category or ("Data" if event_source == "s3.amazonaws.com" and event_name.endswith(
                   ("Object", "Objects", "ObjectsV2", "ObjectVersions")) else "Management"),
               "resources": [{"ARN": r} for r in resources], **extra}
        if error:
            rec["errorCode"], rec["errorMessage"] = error
        with self._lock:
            sealed = seal(self._prev, rec, self._seq)
            self._fh.write(json.dumps(sealed, default=str) + "\n")
            self._prev, self._seq = sealed["hash"], self._seq + 1
        return sealed

    def verify(self) -> int:
        """Recompute the hash chain. Raises AuditTamper if a record was edited, reordered, or inserted.
        Blank lines are checked, not skipped: skipping them would hide an inserted blank."""
        with self._lock:
            self._fh.flush()
        if not self.path.exists() or self.path.stat().st_size == 0:
            return 0
        return verify_lines(self.path.read_text().splitlines())

    def events(self, **filters) -> list[dict]:
        with self._lock:
            self._fh.flush()
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            r = json.loads(line)
            if all(_get(r, k) == v for k, v in filters.items()):
                out.append(r)
        return out

    def denied(self) -> list[dict]:
        return [r for r in self.events() if r.get("errorCode")]


def _get(rec, dotted):
    cur = rec
    for part in dotted.split("__"):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur
