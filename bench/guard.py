"""SQL guard for agent queries: exactly one read-only SELECT over the agent's schemas, no table functions
that touch files, the network, secrets or settings. Runs before DuckDB, which is itself sandboxed;
this is the layer that turns "the agent tried something dangerous" into a clear, scorable error.
check_plan_cost then reads DuckDB's EXPLAIN of the guarded SQL and refuses a keyless join of large inputs before
it runs, with a message that says what to change."""
from __future__ import annotations

import json

import sqlglot
from sqlglot import exp

ALLOWED_SCHEMAS = {"sem"}
ESCALATION_SCHEMAS = {"sem", "lossless"}
BANNED_FUNCTIONS = {
    "read_parquet", "parquet_scan", "read_csv", "read_csv_auto", "read_json", "read_json_auto", "read_ndjson",
    "read_ndjson_auto", "read_text", "read_blob", "sniff_csv", "glob", "query", "query_table", "iceberg_scan",
    "delta_scan", "parquet_metadata", "parquet_schema", "parquet_file_metadata", "parquet_kv_metadata",
    "duckdb_secrets", "which_secret", "duckdb_settings", "current_setting", "getenv", "duckdb_extensions",
    "duckdb_databases", "pragma_database_list", "load_extension", "install_extension",
    "shell", "system", "st_read", "eval",
}
# Scalar calls are not table functions, so a new reader named read_xlsx('file') would otherwise pass.
# UNNEST / EXPLODE do not match these prefixes and stay available for list columns.
BANNED_PREFIXES = ("read_", "parquet_", "sqlite_", "postgres_", "mysql_", "delta_", "iceberg_", "http_")
MAX_SQL_CHARS = 32_768
MAX_ROW_LIMIT = 10_000


class GuardError(ValueError):
    pass


FORBIDDEN_NODES = {"Insert", "Update", "Delete", "Create", "Drop", "Alter", "Merge", "Command", "Copy", "Set",
                   "Pragma", "Attach", "Detach", "Use", "Summarize", "Describe", "Show", "Install", "Load", "Export",
                   "Transaction", "Commit", "Rollback", "Refresh", "Cache", "Uncache", "LoadData"}
_IDENT = __import__("re").compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _visible_ctes(node) -> set[str]:
    """CTE names in scope for `node`: those of every WITH on the path to the root (lexical scoping)."""
    names = set()
    cur = node
    while cur is not None:
        if isinstance(cur, exp.With):                              # inside a CTE body: its siblings are visible
            names |= {c.alias_or_name.lower() for c in cur.expressions}
        w = (cur.args.get("with_") or cur.args.get("with")) if isinstance(cur, exp.Expression) else None
        if isinstance(w, exp.With):
            names |= {c.alias_or_name.lower() for c in w.expressions}
        cur = cur.parent
    return names


def _function_names(fn) -> set[str]:
    names = set()
    if isinstance(fn, exp.Anonymous) and fn.name:
        names.add(fn.name.lower())
    sql_name = (fn.sql_name() or "").lower()
    if sql_name and sql_name != "anonymous":
        names.add(sql_name)
    return names


def _banned_function(names: set[str]) -> str | None:
    hit = names & BANNED_FUNCTIONS
    if hit:
        return hit.pop()
    for name in names:
        if name.startswith(BANNED_PREFIXES):
            return name
    return None


def guard(sql: str, row_limit: int = 1000, escalate: bool = False) -> str:
    if not isinstance(sql, str):
        raise GuardError("SQL must be a string")
    if len(sql) > MAX_SQL_CHARS:
        raise GuardError(f"query exceeds {MAX_SQL_CHARS} characters")
    if isinstance(row_limit, bool) or not isinstance(row_limit, int) or not 1 <= row_limit <= MAX_ROW_LIMIT:
        raise GuardError("row limit must be an integer from 1 to 10000")
    allowed = ESCALATION_SCHEMAS if escalate else ALLOWED_SCHEMAS
    try:
        stmts = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise GuardError(f"SQL did not parse: {str(e).splitlines()[0]}") from None
    if len(stmts) != 1:
        raise GuardError("exactly one statement is allowed")
    tree = stmts[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise GuardError("only SELECT queries are allowed")
    for node in tree.walk():
        if type(node).__name__ in FORBIDDEN_NODES:
            raise GuardError(f"{type(node).__name__} is not allowed")
        # sqlglot accepts a bare SELECT (no projection). Wrapping it still yields SELECT with an empty list.
        if isinstance(node, exp.Select) and not node.expressions:
            raise GuardError("SELECT must project at least one expression")
    for with_clause in tree.find_all(exp.With):
        if with_clause.args.get("recursive"):
            # The outer LIMIT does not stop a recursive CTE: count(*) returns one row after the CTE materializes.
            raise GuardError("recursive queries are not allowed")
    for cte in tree.find_all(exp.CTE):
        if not _IDENT.fullmatch(cte.alias_or_name or ""):
            raise GuardError(f"CTE name {cte.alias_or_name!r} must be a plain identifier")
    for t in tree.find_all(exp.Table):
        if isinstance(t.this, (exp.Anonymous, exp.Func)):
            name = t.this.name.lower() if isinstance(t.this, exp.Anonymous) else t.this.sql_name().lower()
            raise GuardError(f"table function {name}() is not allowed")
        if not isinstance(t.this, exp.Identifier) or t.this.quoted and not _IDENT.fullmatch(t.name):
            raise GuardError(f"relation {t.sql()} is not allowed")
        schema, name = (t.db or "").lower(), t.name.lower()
        if t.catalog:
            raise GuardError("catalog-qualified names are not allowed")
        if not schema and name in _visible_ctes(t):
            continue
        if schema not in allowed:
            raise GuardError(f"relation {t.sql()} is not allowed; use {', '.join(sorted(s + '.*' for s in allowed))}")
    for fn in tree.find_all(exp.Func):
        banned = _banned_function(_function_names(fn))
        if banned:
            raise GuardError(f"function {banned}() is not allowed")
    return f"SELECT * FROM ({tree.sql(dialect='duckdb')}) AS agent_query LIMIT {row_limit}"


# Joins DuckDB plans without an equality key: every left row is paired with every right row.
JOINS_WITHOUT_EQUALITY_KEY = frozenset({"CROSS_PRODUCT", "NESTED_LOOP_JOIN", "BLOCKWISE_NL_JOIN",
                                        "PIECEWISE_MERGE_JOIN"})
# About a second of DuckDB work on the sandbox (4 threads); the largest such join any past session ran had 88,663
# pairs. Hash-join estimates are not checked: on the bench data they ran 600,000x over and 1,400x under the real
# row count, so a threshold on them would block correct queries and still miss key fan-out.
MAX_ROW_PAIRS_WITHOUT_JOIN_KEY = 100_000_000


def explain_statement(guarded_sql: str) -> str:
    """The statement whose single result cell is the JSON plan that check_plan_cost reads."""
    return f"EXPLAIN (FORMAT JSON) {guarded_sql}"


def check_plan_cost(plan_json: str) -> None:
    """Refuse, before execution, a plan that joins two multi-row inputs without a key and pairs more than
    MAX_ROW_PAIRS_WITHOUT_JOIN_KEY rows. A one-row side (a totals or bounds CTE) is a scan, not a cartesian join."""
    for root in json.loads(plan_json):
        _estimated_rows(root)


def _estimated_rows(node: dict) -> float:
    child_rows = [_estimated_rows(child) for child in node.get("children", [])]
    if node["name"] in JOINS_WITHOUT_EQUALITY_KEY and len(child_rows) == 2:
        smaller_side, larger_side = sorted(child_rows)
        row_pairs = smaller_side * larger_side
        if smaller_side > 1 and row_pairs > MAX_ROW_PAIRS_WITHOUT_JOIN_KEY:
            raise GuardError(
                f"query is too expensive to run: a join without an equality key pairs ~{smaller_side:,.0f} rows "
                f"with ~{larger_side:,.0f} rows (~{row_pairs:,.0f} row pairs, limit "
                f"{MAX_ROW_PAIRS_WITHOUT_JOIN_KEY:,}). Join on a shared key (ON a.claim_key = b.claim_key), filter "
                "both sides first, or aggregate one side down to a few rows before joining.")
    estimate = (node.get("extra_info") or {}).get("Estimated Cardinality")
    if estimate is not None:
        return float(estimate)
    if node["name"] == "CROSS_PRODUCT":                       # DuckDB leaves the cross product's own estimate out
        return child_rows[0] * child_rows[1]
    return max(child_rows, default=0.0)
