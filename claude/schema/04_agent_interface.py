# STATUS: CURRENT (design) - agent tools + compiler for the semantic layer; not wired into the app yet (build week Sun). open: run_semantic_sql only allows sem.*, but the plan lets the agent escalate to the lossless schema (lane 2); not reconciled yet | source: chat "835/837p EDI analytics schema" (2026-10-23) | FLAG: 1 later Edit applied; compiles
"""
CC — agent interface to the semantic layer.

The agent gets five tools. Four of them never let the model write SQL:

  describe_semantic_layer   catalog of metrics / dimensions / views (cheap, cached)
  query_metrics             {metrics, group_by, filters, time_range, ...} -> deterministic SQL -> rows
  lookup_values             fuzzy-resolve free text ("aetna", "covid test") to real dimension values
  get_claim_timeline        everything that happened to one claim key, in order
  run_semantic_sql          escape hatch: model-written SELECT, but only over sem.* (parsed + guarded)

The lossless schema (edi / c837 / r835) is unreachable from all five. Escalation to it
is a separate, human-gated path (see LOSSLESS_ESCALATION_TOOL), deliberately not wired here.

Usage:
    import duckdb
    con = duckdb.connect("cc.duckdb", read_only=True)
    api = SemanticAPI(con, "03_semantic_model.yaml")
    api.query_metrics(metrics=["denial_rate","denied_claims","remitted_claims"],
                      group_by=["procedure_code"], min_group_size=20,
                      order_by=[{"field":"denial_rate","direction":"desc"}], limit=15)
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Any

import duckdb
import yaml

MAX_ROWS = 500
ALLOWED_OPS = {"=", "!=", ">", ">=", "<", "<=", "in", "not_in", "between", "contains", "is_null", "is_not_null"}
GRAINS = {"day", "week", "month", "quarter", "year"}


class SemanticError(ValueError):
    """Raised with a message written FOR THE MODEL: what was wrong and what is valid."""


def _suggest(name: str, options: list[str]) -> str:
    close = difflib.get_close_matches(name, options, n=3, cutoff=0.5)
    return f" Did you mean: {', '.join(close)}?" if close else ""


@dataclass
class CompiledQuery:
    sql: str
    params: list[Any]
    view: str
    metric_definitions: dict[str, str]
    applied_filters: list[str]
    notes: list[str] = field(default_factory=list)


class SemanticAPI:
    def __init__(self, con: duckdb.DuckDBPyConnection, model_path: str):
        self.con = con
        with open(model_path) as f:
            self.model = yaml.safe_load(f)
        self.metrics: dict = self.model["metrics"]
        self.dims: dict = {**self.model.get("dimensions", {}), **self.model.get("time_dimensions", {})}
        self.views: dict = self.model["views"]
        self.view_order: list[str] = self.model["view_preference"]
        # Introspect real columns so the catalog can never drift from the views.
        self.columns: dict[str, dict[str, str]] = {}
        for v in self.view_order:
            schema, name = v.split(".")
            rows = con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position", [schema, name]
            ).fetchall()
            if not rows:
                raise RuntimeError(f"semantic view {v} not found - run 02_semantic_layer_views.sql first")
            self.columns[v] = {c: t for c, t in rows}

    # ------------------------------------------------------------------ catalog
    def describe_semantic_layer(self, topic: str | None = None) -> str:
        """Compact, model-facing catalog. ~2-3k tokens; put it in the system prompt and cache it."""
        out = ["# Metrics (name | unit | meaning)"]
        for name, m in self.metrics.items():
            if topic and topic.lower() not in (name + m["description"]).lower():
                continue
            out.append(f"- {name} | {m['unit']} | {m['description']}")
        out.append("\n# Dimensions (group_by / filter). Views that have each are resolved automatically.")
        for name, d in self.dims.items():
            if topic and topic.lower() not in (name + d["description"]).lower():
                continue
            avail = [v.split(".")[1] for v in self.view_order if name in self.columns[v]]
            flag = " [searchable]" if d.get("searchable") else ""
            out.append(f"- {name}{flag}: {d['description']} (in: {', '.join(avail)})")
        if not topic:
            out.append("\n# Grains")
            for v, meta in self.views.items():
                out.append(f"- {v}: {meta['grain']}")
        return "\n".join(out)

    # ------------------------------------------------------------- compilation
    def _leaf_metrics(self, name: str, acc: list[str]) -> None:
        m = self.metrics[name]
        if m["kind"] == "ratio":
            self._leaf_metrics(m["numerator"], acc)
            self._leaf_metrics(m["denominator"], acc)
        elif name not in acc:
            acc.append(name)

    def _views_for_metric(self, name: str) -> set[str]:
        m = self.metrics[name]
        if m["kind"] == "ratio":
            return self._views_for_metric(m["numerator"]) & self._views_for_metric(m["denominator"])
        return set(m["views"])

    @staticmethod
    def _group_field(g: Any) -> tuple[str, str | None]:
        if isinstance(g, str):
            return g, None
        return g["field"], g.get("grain")

    def compile(self, metrics: list[str], group_by: list[Any] | None = None,
                filters: list[dict] | None = None, time_range: dict | None = None,
                order_by: list[dict] | None = None, limit: int = 100,
                min_group_size: int | None = None, include_superseded: bool = False) -> CompiledQuery:
        group_by = group_by or []
        filters = filters or []
        notes: list[str] = []

        if not metrics:
            raise SemanticError("Provide at least one metric. Valid metrics: " + ", ".join(self.metrics))
        for m in metrics:
            if m not in self.metrics:
                raise SemanticError(f"Unknown metric '{m}'.{_suggest(m, list(self.metrics))}")

        # ---- resolve the view: first in preference order that satisfies everything
        needed_cols = [self._group_field(g)[0] for g in group_by] + [f["field"] for f in filters]
        if time_range:
            needed_cols.append(time_range["field"])
        for c in needed_cols:
            if c not in self.dims:
                raise SemanticError(f"Unknown dimension '{c}'.{_suggest(c, list(self.dims))}")
        candidates = set.intersection(*[self._views_for_metric(m) for m in metrics])
        view = next((v for v in self.view_order
                     if v in candidates and all(c in self.columns[v] for c in needed_cols)), None)
        if view is None:
            per_metric = {m: sorted(self._views_for_metric(m)) for m in metrics}
            missing = {v: [c for c in needed_cols if c not in self.columns[v]] for v in candidates}
            raise SemanticError(
                "These metrics and dimensions cannot be computed together. "
                f"Views per metric: {per_metric}. Missing columns per candidate view: {missing}. "
                "Split into two query_metrics calls, or drop a dimension.")

        # ---- leaf aggregates
        leaves: list[str] = []
        for m in metrics:
            self._leaf_metrics(m, leaves)
        defs = {lm: self.metrics[lm]["views"][view] for lm in leaves}

        # ---- WHERE
        where, params, applied = [], [], []
        default_filter = self.views[view].get("default_filter")
        override_flags = {lm: bool(self.metrics[lm].get("override_default_filter")) for lm in leaves}
        if default_filter and len(set(override_flags.values())) > 1:
            hist = [m for m, f in override_flags.items() if f]
            raise SemanticError(
                f"{hist} count history (all 835 events) while the other metrics count current state only. "
                "Request them in a separate query_metrics call.")
        overrides = any(override_flags.values())
        if default_filter and not include_superseded and not overrides:
            where.append(default_filter)
            applied.append(f"default: {default_filter}")
        for f in filters:
            clause, ps = self._compile_filter(f, view)
            where.append(clause)
            params.extend(ps)
            applied.append(f"{f['field']} {f['op']} {f.get('value', '')}".strip())
        if time_range:
            clause, ps, desc = self._compile_time_range(time_range)
            where.append(clause)
            params.extend(ps)
            applied.append(desc)

        # ---- SELECT / GROUP BY
        group_exprs, group_aliases = [], []
        for g in group_by:
            col, grain = self._group_field(g)
            if grain:
                if grain not in GRAINS:
                    raise SemanticError(f"grain must be one of {sorted(GRAINS)}")
                expr, alias = f"date_trunc('{grain}', {col})::DATE", f"{col}_{grain}"
            else:
                expr, alias = col, col
            group_exprs.append(expr)
            group_aliases.append(alias)

        inner_select = [f"{e} AS {a}" for e, a in zip(group_exprs, group_aliases)]
        inner_select += [f"{defs[lm]} AS {lm}" for lm in leaves]
        inner = f"SELECT {', '.join(inner_select)}\nFROM {view}"
        if where:
            inner += "\nWHERE " + "\n  AND ".join(f"({w})" for w in where)
        if group_exprs:
            inner += "\nGROUP BY " + ", ".join(str(i + 1) for i in range(len(group_exprs)))

        outer_cols = list(group_aliases)
        for m in metrics:
            md = self.metrics[m]
            if md["kind"] == "ratio":
                outer_cols.append(f"{md['numerator']}::DOUBLE / NULLIF({md['denominator']}, 0) AS {m}")
            else:
                outer_cols.append(m)
        # leaves that were requested only through a ratio are still useful context: expose them
        for lm in leaves:
            if lm not in metrics:
                outer_cols.append(lm)

        # ---- minimum group size (only meaningful when grouping)
        having = []
        if group_by:
            for m in metrics:
                md = self.metrics[m]
                base = md.get("min_group_size_on")
                if base:
                    n = min_group_size if min_group_size is not None else self.model.get("min_group_size_default", 0)
                    if n:
                        having.append(f"{base} >= {int(n)}")
                        notes.append(f"Groups with {base} < {int(n)} were excluded.")
            if min_group_size is not None and not having:
                # caller asked for a floor but no rate metric declared one; apply to first count metric
                first_count = next((lm for lm in leaves if self.metrics[lm]["kind"] == "count"), None)
                if first_count:
                    having.append(f"{first_count} >= {int(min_group_size)}")
                    notes.append(f"Groups with {first_count} < {int(min_group_size)} were excluded.")

        # ---- ORDER / LIMIT
        valid_order = set(group_aliases) | set(metrics) | set(leaves)
        order_sql = []
        for o in order_by or []:
            fld = o["field"]
            if fld not in valid_order:
                raise SemanticError(f"order_by field '{fld}' must be a requested metric or group_by field: {sorted(valid_order)}")
            direction = "DESC" if o.get("direction", "desc").lower() == "desc" else "ASC"
            order_sql.append(f"{fld} {direction} NULLS LAST")
        limit = max(1, min(int(limit), MAX_ROWS))

        sql = f"WITH agg AS (\n{inner}\n)\nSELECT {', '.join(outer_cols)}\nFROM agg"
        if having:
            sql += "\nWHERE " + " AND ".join(having)
        if order_sql:
            sql += "\nORDER BY " + ", ".join(order_sql)
        sql += f"\nLIMIT {limit}"
        return CompiledQuery(sql, params, view, defs, applied, notes)

    def _compile_filter(self, f: dict, view: str) -> tuple[str, list]:
        col, op = f["field"], f.get("op", "=")
        if op not in ALLOWED_OPS:
            raise SemanticError(f"Unsupported op '{op}'. Use one of {sorted(ALLOWED_OPS)}.")
        if col not in self.columns[view]:
            raise SemanticError(f"Field '{col}' is not available on {view}.")
        is_list = self.columns[view][col].endswith("[]")
        v = f.get("value")
        if op == "is_null":
            return f"{col} IS NULL", []
        if op == "is_not_null":
            return f"{col} IS NOT NULL", []
        if is_list:  # list columns (adjustment_codes, remark codes): membership test
            if op in ("=", "contains"):
                return f"list_contains({col}, ?)", [v]
            raise SemanticError(f"'{col}' is a list column; use op '=' or 'contains'.")
        if op in ("in", "not_in"):
            if not isinstance(v, list) or not v:
                raise SemanticError(f"op '{op}' needs a non-empty list value.")
            ph = ", ".join("?" for _ in v)
            return f"{col} {'IN' if op == 'in' else 'NOT IN'} ({ph})", list(v)
        if op == "between":
            if not isinstance(v, list) or len(v) != 2:
                raise SemanticError("op 'between' needs [low, high].")
            return f"{col} BETWEEN ? AND ?", list(v)
        if op == "contains":
            return f"{col} ILIKE ?", [f"%{v}%"]
        return f"{col} {op} ?", [v]

    @staticmethod
    def _compile_time_range(tr: dict) -> tuple[str, list, str]:
        col = tr["field"]
        if "start" in tr or "end" in tr:
            start, end = tr.get("start", "1900-01-01"), tr.get("end", "2999-12-31")
            return f"{col} >= ?::DATE AND {col} < (?::DATE + INTERVAL 1 DAY)", [start, end], f"{col} in [{start}, {end}]"
        preset = tr.get("preset")
        anchor = "(SELECT max(payment_date) FROM sem.remit_claims)" if tr.get("anchor") == "data" else "current_date"
        presets = {
            "last_30_days":   f"{col} >= {anchor} - INTERVAL 30 DAY",
            "last_90_days":   f"{col} >= {anchor} - INTERVAL 90 DAY",
            "last_12_months": f"{col} >= {anchor} - INTERVAL 12 MONTH",
            "last_month":     f"{col} >= date_trunc('month', {anchor}) - INTERVAL 1 MONTH AND {col} < date_trunc('month', {anchor})",
            "last_quarter":   f"{col} >= date_trunc('quarter', {anchor}) - INTERVAL 3 MONTH AND {col} < date_trunc('quarter', {anchor})",
            "this_year":      f"{col} >= date_trunc('year', {anchor})",
            "last_year":      f"{col} >= date_trunc('year', {anchor}) - INTERVAL 1 YEAR AND {col} < date_trunc('year', {anchor})",
        }
        if preset not in presets:
            raise SemanticError(f"time_range needs start/end or preset in {sorted(presets)}")
        return presets[preset], [], f"{col}: {preset} (anchor={tr.get('anchor', 'today')})"

    # ------------------------------------------------------------------- tools
    def query_metrics(self, **kwargs) -> dict:
        cq = self.compile(**kwargs)
        rel = self.con.execute(cq.sql, cq.params)
        cols = [d[0] for d in rel.description]
        rows = rel.fetchall()
        return {
            "columns": cols,
            "rows": [[_jsonable(v) for v in r] for r in rows],
            "row_count": len(rows),
            "view": cq.view,
            "metric_definitions": cq.metric_definitions,
            "filters_applied": cq.applied_filters,
            "notes": cq.notes + ([f"Truncated to {kwargs.get('limit', 100)} rows."] if len(rows) >= kwargs.get("limit", 100) else []),
            "sql": cq.sql,
        }

    def lookup_values(self, dimension: str, search: str, limit: int = 10) -> dict:
        if dimension not in self.dims:
            raise SemanticError(f"Unknown dimension '{dimension}'.{_suggest(dimension, list(self.dims))}")
        view = next((v for v in self.view_order if dimension in self.columns[v]), None)
        if self.columns[view][dimension].endswith("[]"):
            raise SemanticError(f"'{dimension}' is a list column; look up its scalar counterpart instead.")
        sql = f"""
            SELECT {dimension} AS value, COUNT(*) AS rows,
                   GREATEST(jaro_winkler_similarity(lower({dimension}::VARCHAR), lower(?)),
                            CASE WHEN {dimension}::VARCHAR ILIKE ? THEN 0.95 ELSE 0 END) AS score
            FROM {view} WHERE {dimension} IS NOT NULL
            GROUP BY 1 ORDER BY score DESC, rows DESC LIMIT ?"""
        rows = self.con.execute(sql, [search, f"%{search}%", min(limit, 50)]).fetchall()
        return {"dimension": dimension, "view": view,
                "matches": [{"value": _jsonable(v), "rows": n, "score": round(s, 3)} for v, n, s in rows],
                "note": "Use an exact value from 'matches' in filters. If two matches are plausible, ask the user."}

    def get_claim_timeline(self, claim_key: str) -> dict:
        subs = self.con.execute(
            "SELECT claim_id, submission_date, frequency, payer_name, charge_amount, line_count, is_latest_submission "
            "FROM sem.claims WHERE claim_key = ? ORDER BY submission_date", [claim_key]).fetchall()
        lines = self.con.execute(
            "SELECT line_number, procedure_code, modifier_1, line_charge_amount, units_billed, service_date, "
            "has_line_remit, line_paid_amount, line_is_denied, line_adjustment_codes, line_remark_codes "
            "FROM sem.claim_lines WHERE claim_key = ? AND is_latest_submission ORDER BY line_number", [claim_key]).fetchall()
        remits = self.con.execute(
            "SELECT payment_date, payer_name, claim_status, adjudication_state, charge_amount, paid_amount, "
            "patient_responsibility_amount, is_denied, adjustment_codes, payer_claim_control_number, check_or_eft_number "
            "FROM sem.remit_claims WHERE claim_key = ? ORDER BY payment_date, remit_claim_id", [claim_key]).fetchall()
        if not subs and not remits:
            raise SemanticError(f"No claim with claim_key '{claim_key}'. claim_key is the patient control number.")
        f = lambda rows: [[_jsonable(v) for v in r] for r in rows]  # noqa: E731
        return {"claim_key": claim_key,
                "submissions_837": {"columns": ["claim_id", "submission_date", "frequency", "payer", "charge", "lines", "is_latest"], "rows": f(subs)},
                "latest_submission_lines": {"columns": ["line", "procedure", "mod1", "charge", "units", "service_date", "remitted", "paid", "denied", "cas", "remarks"], "rows": f(lines)},
                "remit_events_835": {"columns": ["payment_date", "payer", "status", "state", "charge", "paid", "patient_resp", "denied", "cas", "payer_icn", "check_eft"], "rows": f(remits)}}

    def run_semantic_sql(self, sql: str, limit: int = 200) -> dict:
        safe_sql = guard_semantic_sql(sql, limit=min(limit, MAX_ROWS))
        rel = self.con.execute(safe_sql)
        cols = [d[0] for d in rel.description]
        rows = rel.fetchall()
        return {"columns": cols, "rows": [[_jsonable(v) for v in r] for r in rows], "row_count": len(rows), "sql": safe_sql}

    # ------------------------------------------------- few-shot retrieval
    def similar_verified_requests(self, question: str, k: int = 3) -> list[dict]:
        def toks(s: str) -> set[str]:
            return set(re.findall(r"[a-z0-9]+", s.lower()))
        q = toks(question)
        scored = sorted(self.model.get("verified_requests", []),
                        key=lambda vr: -len(q & toks(vr["question"])) / (len(q | toks(vr["question"])) or 1))
        return scored[:k]

    # ------------------------------------------------- dispatcher for the tool loop
    def dispatch(self, tool_name: str, tool_input: dict) -> dict:
        try:
            if tool_name == "describe_semantic_layer":
                return {"catalog": self.describe_semantic_layer(tool_input.get("topic"))}
            if tool_name == "query_metrics":
                return self.query_metrics(**tool_input)
            if tool_name == "lookup_values":
                return self.lookup_values(**tool_input)
            if tool_name == "get_claim_timeline":
                return self.get_claim_timeline(**tool_input)
            if tool_name == "run_semantic_sql":
                return self.run_semantic_sql(**tool_input)
            return {"error": f"unknown tool {tool_name}"}
        except SemanticError as e:
            return {"error": str(e), "recoverable": True}
        except duckdb.Error as e:
            return {"error": f"query failed: {e}", "recoverable": True}


# ---------------------------------------------------------------------------
# SQL guard for the escape hatch: one SELECT, only sem.* relations, no file/
# network table functions, row cap enforced. Requires `sqlglot`.
# ---------------------------------------------------------------------------
_BANNED_FUNCS = {"read_parquet", "read_csv", "read_csv_auto", "read_json", "read_json_auto", "parquet_scan",
                 "read_text", "read_blob", "glob", "httpfs", "attach", "copy", "query_table", "query", "sniff_csv"}


def guard_semantic_sql(sql: str, limit: int = 200) -> str:
    import sqlglot
    from sqlglot import exp

    try:
        stmts = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.ParseError as e:
        raise SemanticError(f"SQL did not parse: {e}")
    if len(stmts) != 1 or stmts[0] is None:
        raise SemanticError("Exactly one statement is allowed.")
    tree = stmts[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise SemanticError("Only SELECT queries are allowed.")
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for t in tree.find_all(exp.Table):
        name = t.name.lower()
        schema = (t.db or "").lower()
        if not schema and name in cte_names:
            continue
        if schema != "sem":
            raise SemanticError(f"Table '{t.sql()}' is not allowed. Use only sem.* views "
                                "(remit_claims, remit_lines, claims, claim_lines, adjustments, remarks, payments, "
                                "provider_adjustments, diagnoses, payers).")
    for fn in tree.find_all(exp.Func):
        fname = (fn.sql_name() if hasattr(fn, "sql_name") else type(fn).__name__).lower()
        anon = fn.name.lower() if isinstance(fn, exp.Anonymous) else ""
        if fname in _BANNED_FUNCS or anon in _BANNED_FUNCS:
            raise SemanticError(f"Function '{fname or anon}' is not allowed.")
    return f"SELECT * FROM ({tree.sql(dialect='duckdb')}) AS q LIMIT {int(limit)}"


def _jsonable(v: Any) -> Any:
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
        return float(v)
    if isinstance(v, float):
        return round(v, 6)
    return v


# ---------------------------------------------------------------------------
# Tool definitions (Anthropic Messages API `tools` format)
# ---------------------------------------------------------------------------
_FILTER_SCHEMA = {
    "type": "object",
    "properties": {
        "field": {"type": "string", "description": "A dimension name from the catalog."},
        "op": {"type": "string", "enum": sorted(ALLOWED_OPS)},
        "value": {"description": "Scalar, or list for in/not_in/between. Resolve free text with lookup_values first."},
    },
    "required": ["field", "op"],
}

TOOLS = [
    {
        "name": "query_metrics",
        "description": (
            "Answer aggregate questions about this customer's claims (837P) and remittances (835). "
            "Pick metrics and dimensions BY NAME from the catalog; the server chooses the right view, joins, "
            "denial/reversal rules and writes the SQL. Rate metrics automatically drop groups below the "
            "minimum sample size (default 20) when grouping. Returns rows plus the SQL and metric definitions used. "
            "Prefer this tool for any count / sum / rate / trend / top-N question."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metrics": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                            "description": "Metric names, e.g. ['denial_rate','denied_claims','remitted_claims']."},
                "group_by": {"type": "array", "items": {"anyOf": [
                    {"type": "string"},
                    {"type": "object", "properties": {"field": {"type": "string"},
                                                      "grain": {"type": "string", "enum": sorted(GRAINS)}},
                     "required": ["field", "grain"]}]},
                    "description": "Dimension names, or {field, grain} for dates."},
                "filters": {"type": "array", "items": _FILTER_SCHEMA},
                "time_range": {"type": "object", "properties": {
                    "field": {"type": "string"}, "start": {"type": "string", "format": "date"},
                    "end": {"type": "string", "format": "date"},
                    "preset": {"type": "string", "enum": ["last_30_days", "last_90_days", "last_12_months", "last_month",
                                                         "last_quarter", "this_year", "last_year"]},
                    "anchor": {"type": "string", "enum": ["today", "data"],
                               "description": "'data' anchors presets to the latest payment date in the data (use for historical conversion files)."}},
                    "required": ["field"]},
                "order_by": {"type": "array", "items": {"type": "object", "properties": {
                    "field": {"type": "string"}, "direction": {"type": "string", "enum": ["asc", "desc"]}},
                    "required": ["field"]}},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_ROWS, "default": 100},
                "min_group_size": {"type": "integer", "minimum": 0,
                                   "description": "Override the minimum sample size for rate metrics (0 disables)."},
                "include_superseded": {"type": "boolean", "default": False,
                                       "description": "Include reversed/superseded 835 events (history questions only)."},
            },
            "required": ["metrics"],
        },
    },
    {
        "name": "lookup_values",
        "description": ("Resolve a user's wording to real values of a searchable dimension (payer_name, procedure_code, "
                        "carc_code, carc_description, remark_code, claim_status, ...). Always call before filtering on "
                        "a payer or any free-text value. Returns ranked matches with row counts."),
        "input_schema": {"type": "object", "properties": {
            "dimension": {"type": "string"}, "search": {"type": "string"},
            "limit": {"type": "integer", "default": 10, "maximum": 50}},
            "required": ["dimension", "search"]},
    },
    {
        "name": "get_claim_timeline",
        "description": ("Drill into ONE claim by claim_key (patient control number): every 837 submission, the latest "
                        "submission's lines with their matched 835 outcome, and every 835 event (including reversals) in order."),
        "input_schema": {"type": "object", "properties": {"claim_key": {"type": "string"}}, "required": ["claim_key"]},
    },
    {
        "name": "describe_semantic_layer",
        "description": "Return the catalog of metrics, dimensions and view grains (optionally filtered by a topic word).",
        "input_schema": {"type": "object", "properties": {"topic": {"type": "string"}}},
    },
    {
        "name": "run_semantic_sql",
        "description": ("LAST RESORT for questions query_metrics cannot express (multi-step logic, percentiles, "
                        "window functions, cross-grain comparisons). One DuckDB SELECT over sem.* views ONLY. "
                        "Reuse the metric definitions returned by query_metrics instead of inventing your own; "
                        "for 'current state' questions filter is_current_adjudication / is_latest_submission."),
        "input_schema": {"type": "object", "properties": {
            "sql": {"type": "string"}, "limit": {"type": "integer", "default": 200, "maximum": MAX_ROWS}},
            "required": ["sql"]},
    },
]

# Not given to the agent by default. Wire it only behind a human approval step.
LOSSLESS_ESCALATION_TOOL = {
    "name": "request_lossless_query",
    "description": ("Ask an engineer-approved path to answer a question that needs raw EDI detail not in the "
                    "semantic layer (e.g. a specific segment/element, 2320 COB data, PWK attachments). "
                    "Describe the question and why sem.* cannot answer it. Does not run immediately."),
    "input_schema": {"type": "object", "properties": {
        "question": {"type": "string"}, "why_semantic_layer_is_insufficient": {"type": "string"}},
        "required": ["question", "why_semantic_layer_is_insufficient"]},
}


if __name__ == "__main__":
    print(json.dumps(TOOLS, indent=2)[:2000])
