"""The benchmark harness: guard, matcher, and a full self-test on a small dataset."""
import pytest

from bench.compare import match
from bench.guard import GuardError, guard


@pytest.mark.parametrize("sql", [
    "SELECT * FROM read_parquet('s3://x')", "SELECT * FROM 's3://x/y.parquet'", "SELECT * FROM duckdb_secrets()",
    "DROP VIEW sem.claims", "SELECT 1; SELECT 2", "COPY (SELECT 1) TO 'x'", "SELECT getenv('HOME')",
    "SELECT * FROM lossless.edi_entity", "SELECT * FROM memory.sem.claims", "ATTACH 'x.db'", "CREATE SECRET s (TYPE s3)",
    "SELECT * FROM glob('*')", "INSTALL httpfs", "SET threads=1", "SELECT * FROM sem.claims, read_csv('x')",
    "SELECT current_setting('s3_secret_access_key')", "PRAGMA version", "SELECT * FROM (SELECT * FROM read_text('x'))",
    "WITH x AS (SELECT * FROM read_blob('y')) SELECT * FROM x", "SELECT * FROM main.claims",
])
def test_guard_blocks(sql):
    with pytest.raises(GuardError):
        guard(sql)


def test_guard_allows_analysis_sql():
    guard("WITH x AS (SELECT claim_key FROM sem.claims) SELECT count(*) FROM x JOIN sem.remit_claims USING (claim_key)")
    guard("SELECT * FROM lossless.edi_source_file", escalate=True)


def test_matcher():
    assert match([(0.25,)], [(25.0,)], {"allow_percent": True})[0]
    assert not match([(0.25,)], [(25.0,)], {})[0]
    assert match([("A", 0.5), ("B", 0.2)], [(0.2, "b", 99), (0.5, "a", 1)])[0]
    assert not match([("A", 0.5), ("B", 0.2)], [("A", 0.2), ("B", 0.5)])[0]
    assert not match([("A", 1), ("B", 2)], [("B", 2), ("A", 1)], {"order": "strict"})[0]
    g = [("x", 10), ("y", 8), ("z", 8), ("w", 5)]
    assert match(g, [("x", 10), ("z", 8)], {"order": "topn", "n": 2})[0]
    assert not match(g, [("x", 10), ("w", 5)], {"order": "topn", "n": 2})[0]
    assert match([(-12.5,)], [(12.5,)], {"sign_insensitive": True})[0]
    assert not match([("A", 1)], [("A", 1), ("A", 1)])[0]


def test_selftest_on_a_small_dataset(tmp_path):
    from bench.dataset import snapshot
    from bench.eval import load_cases, run, selftest
    from ccsim.edi_mock import Scenario
    path = snapshot(Scenario(days=20, volume_scale=.3, seed=5), cache=tmp_path)
    rep = selftest(path)
    assert rep["sound"], rep
    gold = run("gold", path)
    assert gold["accuracy"] == 1.0, [r for r in gold["results"] if not r["correct"]]
    wrong = run("wrong", path)
    n_wrong = sum(1 for c in load_cases() if c.get("wrong_sql"))
    missed = {w["id"] for w in rep["wrong_accepted"]}
    assert sum(not r["correct"] for r in wrong["results"]) >= n_wrong - len(missed)
