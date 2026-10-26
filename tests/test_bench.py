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
    # review round 3: scope-blind CTE names and SUMMARIZE inside a subquery
    'SELECT * FROM (WITH "/snap/truth.json" AS (SELECT 1) SELECT 1) s, "/snap/truth.json"',
    """SELECT 1 WHERE EXISTS (WITH meta AS (SELECT 1) SELECT 1) AND (SELECT count(*) FROM meta) > 0""",
    "SELECT * FROM (SUMMARIZE '/snap/truth.json')", "SELECT * FROM (DESCRIBE sem.claims)",
    "SELECT * FROM \"/snap/x.parquet\"",
])
def test_guard_blocks(sql):
    with pytest.raises(GuardError):
        guard(sql)


def test_guard_allows_analysis_sql():
    guard("WITH x AS (SELECT claim_key FROM sem.claims) SELECT count(*) FROM x JOIN sem.remit_claims USING (claim_key)")
    guard("SELECT * FROM (WITH y AS (SELECT 1 AS a) SELECT a FROM y) z")
    guard("FROM sem.claims SELECT count(*)")
    guard("WITH a AS (SELECT 1 AS v), b AS (SELECT v FROM a) SELECT * FROM b")
    guard("SELECT * FROM lossless.edi_source_file", escalate=True)


def test_matcher_accepts_equivalent_answers():
    from datetime import date
    assert match([("A", 0.5), ("B", 0.2)], [(0.2, "b"), (0.5, "a")])[0]                     # column order, case
    assert match([("A", 0.5), ("B", 0.2)], [("Payer A", "a", 0.5), ("Payer B", "b", 0.2)])[0]  # extra label column
    assert match([(0.25,)], [(25.0,)], {"percent_cols": [0]})[0]
    assert match([("x", 0.25, 10)], [("x", 0.25)], {"optional_cols": [2]})[0]
    assert match([(date(2026, 7, 1), 5.0)], [("2026-07", 5)])[0]
    assert match([(1,)], [(True,)])[0] and match([(0.25,)], [("0.250",)])[0]
    assert match([(-5.0,)], [(5.0,)], {"sign_cols": [0]})[0]
    g = [("a", 10)] + [(f"p{i}", 5) for i in range(13)]
    assert match(g, [("a", 10)] + [(f"p{i}", 5) for i in range(8, 12)], {"order": "topn", "n": 5})[0]
    # a rate with its optional count in another order, plus an extra supporting count (found in screen 1)
    spec = {"percent_cols": [1], "optional_cols": [2], "tol": 0.0005}
    assert match([("Z13220", 0.0465, 86)], [("Z13220", 86, 4, 4.65)], spec)[0]
    assert match([("a", 0.5, 10)], [("a", 10, 0.5)])[0]


@pytest.mark.parametrize("gold,cand,spec", [
    ([(2500,)], [(2624, 2500, 2624)], {}),                                       # several alternative numbers
    ([("A", 0.1)], [("A", 0.3, 0.1)], {}),
    ([(225, 141)], [(141, 225)], {}),                                            # swapped measures
    ([("a", 0.1), ("b", 0.2)], [("a", 0.1), ("b", 20.0)], {"percent_cols": [1]}),  # mixed scaling in a column
    ([("x", 0.25, 25)], [("x", 0.25, 2500)], {"percent_cols": [1], "optional_cols": [2]}),  # x100 only on the rate
    ([(-5.0,)], [(5.0,)], {}),
    ([("a", 1), ("b", 2)], [("b", 2), ("a", 1)], {"order": "strict"}),
    ([("A", 1)], [("A", 1), ("A", 1)], {}),
    ([("x", 10), ("y", 8), ("z", 8), ("w", 5)], [("x", 10), ("w", 5)], {"order": "topn", "n": 2}),
    ([("a", 10)] + [(f"p{i}", 5) for i in range(13)], [(f"p{i}", 5) for i in range(5)], {"order": "topn", "n": 5}),
    ([("a", 10)], [("a", 10, 7)], {}),                                           # extra count beside a count answer
    ([("x", 0.0465, 86)], [("x", 85, 4, 0.0465)], {"optional_cols": [2]}),       # the optional count given, but wrong
])
def test_matcher_rejects(gold, cand, spec):
    assert not match(gold, cand, spec)[0]


def test_selftest_on_a_small_dataset(tmp_path):
    from bench.dataset import snapshot
    from bench.eval import load_cases, run, selftest
    from ccsim.edi_mock import Scenario
    path = snapshot(Scenario(days=20, volume_scale=.3, seed=5), cache=tmp_path)
    rep = selftest(path, strict=False)                           # small data: some questions cannot discriminate
    assert rep["sound"] and rep["oracle_accuracy"] == 1.0, rep
    gold = run("gold", path)
    assert gold["accuracy"] == 1.0, [r for r in gold["results"] if not r["correct"]]
    wrong = run("wrong", path)
    n_wrong = sum(1 for c in load_cases() if c.get("wrong_sql"))
    missed = {w["id"] for w in rep["wrong_accepted"]}
    assert sum(not r["correct"] for r in wrong["results"]) >= n_wrong - len(missed)
