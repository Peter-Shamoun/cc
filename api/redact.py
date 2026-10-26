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
