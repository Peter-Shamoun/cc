"""Stop interrupts the running SQL (API side only; analyzer/ is untouched).

Without this, pressing Stop only takes effect at the analyzer's next model call (``LiveTap`` raises ``Cancelled``), so a
long query keeps a DuckDB thread busy until it finishes or hits the analyzer's own time limit. ``CancellableOpener``
wraps the request's ``SessionOpener``: it remembers every connection/cursor it hands out, and while the run is in
progress a watcher thread calls ``interrupt()`` on them once ``cancelled`` is set (then every ``poll`` seconds until the
run ends, so a query started on the same cursor right after the first interrupt is stopped too; DuckDB ignores an
interrupt on an idle cursor). Only this run's cursors are touched: other users' queries on the shared connection keep
running. ``open()`` after cancellation raises ``Cancelled`` without opening a session.

Backends whose ``con`` has no ``interrupt`` (or ``cancel``) are left alone and fall back to the model-call checkpoint.
"""
from __future__ import annotations

import logging
import threading

from api.live import Cancelled

log = logging.getLogger("cc.api")

DEFAULT_POLL_SECONDS = 0.05


class CancellableOpener:
    def __init__(self, inner, cancelled: threading.Event, poll: float = DEFAULT_POLL_SECONDS):
        self.inner = inner
        self.cancelled = cancelled
        self.poll = poll
        self.interrupts = 0  # interrupt() calls made on a cursor (for audit/tests; no SQL, no data)
        self._cons: list = []
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._thread: threading.Thread | None = None

    def open(self, escalate: bool):
        if self.cancelled.is_set():
            raise Cancelled()
        handle = self.inner.open(escalate)
        con = getattr(handle, "con", None)
        if con is not None:
            with self._lock:
                self._cons.append(con)
        return handle

    def start(self) -> "CancellableOpener":
        return self.__enter__()

    def stop(self) -> None:
        self.__exit__(None, None, None)

    def __enter__(self):
        self._thread = threading.Thread(target=self._watch, name="api-cancel-watch", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._done.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        return False

    def _watch(self) -> None:
        while not self._done.is_set():
            if self.cancelled.wait(self.poll) and not self._done.is_set():
                self.interrupt_all()
                self._done.wait(self.poll)

    def interrupt_all(self) -> int:
        with self._lock:
            cons = list(self._cons)
        n = 0
        for con in cons:
            stop = getattr(con, "interrupt", None) or getattr(con, "cancel", None)
            if stop is None:
                continue
            try:
                stop()
                n += 1
            except Exception as exc:  # noqa: BLE001 - a closed cursor; never log SQL or data
                log.debug("interrupt failed: %s", type(exc).__name__)
        self.interrupts += n
        return n
