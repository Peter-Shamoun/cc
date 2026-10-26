"""The SQL guard every model-written query passes."""
import pytest

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
