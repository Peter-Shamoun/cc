"""SQL guard for agent queries: exactly one read-only SELECT over the agent's schemas, no table functions
that touch files, the network, secrets or settings. Runs before DuckDB, which is itself sandboxed;
this is the layer that turns "the agent tried something dangerous" into a clear, scorable error."""
from __future__ import annotations

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
}


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


def guard(sql: str, row_limit: int = 1000, escalate: bool = False) -> str:
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
        names = {fn.name.lower() if isinstance(fn, exp.Anonymous) else "", fn.sql_name().lower()}
        if names & BANNED_FUNCTIONS:
            raise GuardError(f"function {(names & BANNED_FUNCTIONS).pop()}() is not allowed")
    return f"SELECT * FROM ({tree.sql(dialect='duckdb')}) AS agent_query LIMIT {int(row_limit)}"
