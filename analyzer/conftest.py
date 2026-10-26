from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def bench_snapshot() -> Path:
    """The cached benchmark snapshot (built on first use, like tests/test_bench.py)."""
    from bench.dataset import snapshot
    return snapshot()


@pytest.fixture
def sessions_root(tmp_path, monkeypatch) -> Path:
    """prepare() and create_session() write under a temporary root."""
    root = tmp_path / "sessions"
    monkeypatch.setattr("bench.agent_session.SESSIONS_ROOT", root)
    monkeypatch.setattr("bench.eval_subagents.SESSIONS_ROOT", root)
    return root
