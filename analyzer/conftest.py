from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def bench_snapshot() -> Path:
    """The cached benchmark snapshot (built on first use): B1's example library and the acme_labs data."""
    from bench.dataset import snapshot
    return snapshot()
