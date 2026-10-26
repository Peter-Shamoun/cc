"""Load-tolerant time budgets for tests (the box runs several teams' suites at once).

``budget(s)`` stretches an upper bound by the current load: ``s × max(1, loadavg1 / cpus) × CC_TEST_TIME_SCALE``
(env, default 1), capped at 10×. Use it only for *upper* bounds ("finishes within"); lower bounds ("took at least")
are already load-safe. Prefer asserting on events or requested sleeps over wall time where possible."""
from __future__ import annotations

import os
import time
from typing import Callable

MAX_STRETCH = 10.0


def load_factor() -> float:
    try:
        load = os.getloadavg()[0] / (os.cpu_count() or 1)
    except (OSError, AttributeError):
        load = 1.0
    scale = float(os.environ.get("CC_TEST_TIME_SCALE", "1") or 1)
    return min(MAX_STRETCH, max(1.0, load) * max(1.0, scale))


def budget(seconds: float) -> float:
    return seconds * load_factor()


def wait_until(pred: Callable[[], bool], seconds: float, step: float = 0.02) -> bool:
    """Poll ``pred`` until true or the (load-stretched) budget runs out. Returns the last value."""
    deadline = time.monotonic() + budget(seconds)
    while not pred():
        if time.monotonic() >= deadline:
            return pred()
        time.sleep(step)
    return True
