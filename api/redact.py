"""PHI-safe text for logs and error reports. It mirrors web/src/lib/redact.ts. The shared vectors are in
web/src/lib/redact.vectors.json.

This is a heuristic last line of defence, not de-identification. The real rule is still never to log user text, SQL
or row values (see AuditLog's allowlist). Quoted strings go first because error messages quote their input
(``invalid literal for int(): 'Jane'``).
"""
from __future__ import annotations

import logging
import re
import traceback

_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\"[^\"]*\"|'[^']*'|\u2018[^\u2019]*\u2019|\u201c[^\u201d]*\u201d|`[^`]*`"), "[text]"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[ssn]"),
    (re.compile(r"(?:\+?1[ .-]?)?(?:\(\d{3}\)\s?|\b\d{3}[ .-])\d{3}[ .-]\d{4}\b"), "[phone]"),
    (re.compile(r"\b(?:\d{1,2}/\d{1,2}/\d{2,4}|(?:19|20)\d{2}-\d{2}-\d{2})\b"), "[date]"),
    (re.compile(r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{8,}\b"), "[id]"),
    (re.compile(r"\b\d{6,}\b"), "[number]"),
]
MAX_LEN = 300


def redact(text: object, max_len: int = MAX_LEN) -> str:
    s = str(text)
    for rx, repl in _RULES:
        s = rx.sub(repl, s)
    return s if len(s) <= max_len else s[:max_len] + "\u2026"


def safe_exc(exc: BaseException) -> str:
    """Exception type and code location only. ``str(exc)`` can carry user input, so it is never included."""
    tb = traceback.extract_tb(exc.__traceback__)[-1:] if exc.__traceback__ else []
    where = f" at {tb[0].filename.rsplit('/', 1)[-1]}:{tb[0].lineno}" if tb else ""
    return f"{type(exc).__name__}{where}"


class PhiLogFilter(logging.Filter):
    """Redacts every record's message. For tracebacks it keeps the frames (file:line in function, with no source
    text) and drops the exception message."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and record.args:
            # Keep the tuple's length: formatters such as uvicorn's access log unpack record.args themselves.
            # The template is code-authored and stays as is (the quote rule would eat its %s placeholders).
            record.args = tuple(a if isinstance(a, (int, float)) else redact(a, 2000) for a in record.args)
        else:
            try:
                msg = record.getMessage()
            except Exception:  # bad format args: keep the template only
                msg = str(record.msg)
            record.msg, record.args = redact(msg, 2000), ()
        if record.exc_info and record.exc_info[0] is not None:
            etype, _exc, tb = record.exc_info
            frames = "\n".join(f"  {f.filename.rsplit('/', 1)[-1]}:{f.lineno} in {f.name}"
                               for f in traceback.extract_tb(tb)[-12:])  # no source lines: they can hold literals
            record.exc_text = f"{etype.__name__} (message redacted)\n{frames}"
            record.exc_info = None
        if record.stack_info:
            record.stack_info = None
        return True


_LOGGERS = ("", "cc.api", "uvicorn", "uvicorn.error", "uvicorn.access", "fastapi", "starlette")
_FILTER = PhiLogFilter()


def install_log_redaction() -> None:
    """Idempotent. It attaches to the known loggers and to their current handlers. Handlers also see records that
    propagate from child loggers (analyzer, ccsim)."""
    for name in _LOGGERS:
        lg = logging.getLogger(name)
        if _FILTER not in lg.filters:
            lg.addFilter(_FILTER)
        for h in lg.handlers:
            if _FILTER not in h.filters:
                h.addFilter(_FILTER)


# ---- access log ----------------------------------------------------------------------------------------------------
# uvicorn logs every request as ``(client_addr, method, full_path, http_version, status)``. Paths carry record
# ids (``/api/conversations/<id>``, ``/api/download/<id>``), and a
# query string can carry anything. The general filter can't catch ids reliably (random tokens), so the
# access log gets the route *shape* instead: a segment is kept only when it's a static segment of a registered route,
# everything else becomes ``:id``, and the query string becomes ``?…``. Client IPs (a HIPAA identifier) are
# truncated to /24 (IPv4) or /48 (IPv6).

def path_shape(path: object, static: frozenset[str] | set[str]) -> str:
    p = str(path)
    p, q = (p.split("?", 1) + [""])[:2] if "?" in p else (p, "")
    segs = [s if (s == "" or s in static) else ":id" for s in p.split("/")]
    out = "/".join(segs)[:200]
    return out + ("?\u2026" if q else "")


def mask_client(client: object) -> str:
    c = str(client)
    if c.count(":") == 1 and c.count(".") == 3:  # ipv4:port
        c = c.rsplit(":", 1)[0]
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", c):
        return ".".join(c.split(".")[:3]) + ".0"
    m = re.fullmatch(r"\[?([0-9A-Fa-f:]+)\]?(?::\d+)?", c)
    if m and ":" in m.group(1):
        return ":".join(m.group(1).split(":")[:3]) + "::"
    return "-"


class AccessLogFilter(logging.Filter):
    """Rewrites uvicorn access records to the route shape (see path_shape). ``static`` grows as apps register."""

    def __init__(self) -> None:
        super().__init__()
        self.static: set[str] = {"api"}

    def filter(self, record: logging.LogRecord) -> bool:
        a = record.args
        if isinstance(a, tuple) and len(a) == 5:
            client, method, path, version, status = a
            method = method if str(method).isalpha() and len(str(method)) <= 10 else "-"
            record.args = (mask_client(client), method, path_shape(path, self.static), version, status)
        elif record.name == "uvicorn.access":  # unknown shape: keep nothing request-derived
            record.msg, record.args = "access (unparsed record dropped)", ()
        return True


ACCESS_FILTER = AccessLogFilter()


def install_access_log_redaction(routes) -> None:
    """Idempotent. ``routes`` is ``app.routes``; their static segments become the allowlist."""
    for r in routes:
        for seg in str(getattr(r, "path", "")).split("/"):
            if seg and "{" not in seg:
                ACCESS_FILTER.static.add(seg)
    lg = logging.getLogger("uvicorn.access")
    if ACCESS_FILTER not in lg.filters:
        lg.addFilter(ACCESS_FILTER)
    for h in lg.handlers:
        if ACCESS_FILTER not in h.filters:
            h.addFilter(ACCESS_FILTER)


# ---- outbound / test-client request log -----------------------------------------------------------------------------
# httpx logs every request it makes as ``HTTP Request: <method> <full url> ...``. That covers the API's own outbound
# calls (model providers, IdP) and Starlette's TestClient, which logs ``http://testserver/api/conversations/<id>``.
# Keep the origin, shape the path the same way as the access log, and drop the query string.

class OutboundUrlFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        a = record.args
        if isinstance(a, tuple) and len(a) >= 2 and isinstance(record.msg, str) and record.msg.startswith("HTTP Request"):
            record.args = (a[0], outbound_shape(a[1]), *a[2:])
        return True


def outbound_shape(url: object) -> str:
    m = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*://[^/?#]*)([^?#]*)", str(url))
    if not m:
        return ":url"
    return m.group(1) + path_shape(m.group(2) or "/", ACCESS_FILTER.static).split("?", 1)[0]


OUTBOUND_FILTER = OutboundUrlFilter()


def install_outbound_url_redaction() -> None:
    """Idempotent."""
    for name in ("httpx", "httpcore"):
        lg = logging.getLogger(name)
        if OUTBOUND_FILTER not in lg.filters:
            lg.addFilter(OUTBOUND_FILTER)
