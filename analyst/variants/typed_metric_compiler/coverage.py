"""How much of the benchmark the typed spec can express.

This is a structural reading of each question's gold SQL. It does not look at wrong_sql,
failure write-ups, or the pitfalls text. A question counts as expressible only when the gold
query is a shape the compiler actually emits:

- one sem.* view, catalog metrics (or a documented grain equivalence), dimension filters
- or a claim cohort: only sem.claims, population by min(submission_date) or min(service_date_from),
  and a count of that population

Refusals are a separate lane. They are expressible as intent=refuse, not as a metric.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import sqlglot
from sqlglot import exp
import yaml

ROOT = Path(__file__).resolve().parents[3]
MODEL_PATH = ROOT / "claude" / "schema" / "03_semantic_model.yaml"
QUESTIONS = ROOT / "bench" / "questions.yaml"
AUTHORED = ROOT / "bench" / "authored_questions.json"

_BLOCKED_FUNCS = {"median", "quantile", "quantile_cont", "corr", "regr_avgx", "mode"}
# On sem.claims these columns come from the claim_key outcome join, so they do not change
# across a claim's submissions. Filtering on them plus the compiler's is_latest_submission
# keeps the same set of claim_keys. Payer and charge do change across submissions.
_CLAIM_LEVEL_FILTERS = {"has_remit", "is_denied", "any_payer_denied", "ever_reversed"}


def _norm(sql: str) -> str:
    s = sql.strip().lower()
    changed = True
    while changed:
        changed = False
        for pattern in (
            r"round\s*\((.*),\s*\d+\s*\)",
            r"coalesce\s*\((.*),\s*0(?:\.0+)?\s*\)",
        ):
            match = re.fullmatch(pattern, s, flags=re.S)
            if match:
                s = match.group(1).strip()
                changed = True
                break
        if changed:
            continue
        # sqlglot prints `x::INT` as CAST(x AS INT). Strip only casts with no nested parens.
        stripped = re.sub(r"cast\s*\(([^()]*)\s+as\s+[a-z0-9_ ]+\)", r"\1", s)
        if stripped != s:
            s = stripped
            changed = True
    s = re.sub(r"::\s*[a-z0-9_]+", "", s)
    s = re.sub(r"\s+", "", s)
    return s


def _load_model() -> dict:
    return yaml.safe_load(MODEL_PATH.read_text())


def _dims(model: dict) -> set[str]:
    return set(model.get("dimensions", {})) | set(model.get("time_dimensions", {}))


def _defaults(model: dict) -> dict[str, str | None]:
    return {view: meta.get("default_filter") for view, meta in model["views"].items()}


def _metric_exprs(model: dict, view: str) -> dict[str, str]:
    out = {}
    for name, metric in model["metrics"].items():
        if metric["kind"] == "ratio":
            continue
        body = metric.get("views", {}).get(view)
        if body:
            out[name] = _norm(body)
    return out


def _ratios(model: dict) -> list[tuple[str, str, str]]:
    return [(name, m["numerator"], m["denominator"]) for name, m in model["metrics"].items() if m["kind"] == "ratio"]


def _tables(tree: exp.Expression) -> list[str]:
    names = []
    for table in tree.find_all(exp.Table):
        if table.db:
            names.append(f"{table.db}.{table.name}".lower())
    return names


def _funcs(tree: exp.Expression) -> set[str]:
    found = set()
    for fn in tree.find_all(exp.Func):
        found.add(fn.sql_name().lower() if hasattr(fn, "sql_name") else type(fn).__name__.lower())
        if isinstance(fn, exp.Anonymous):
            found.add(fn.name.lower())
    return found


def _split_and(node) -> list:
    if node is None:
        return []
    if isinstance(node, exp.Where):
        return _split_and(node.this)
    if isinstance(node, exp.Paren):
        return _split_and(node.this)
    if isinstance(node, exp.And):
        return _split_and(node.left) + _split_and(node.right)
    return [node]


def _column_name(node) -> str | None:
    if isinstance(node, exp.Column):
        return node.name.lower()
    return None


def _date_trunc_columns(node) -> list[str] | None:
    name = node.name.lower() if isinstance(node, exp.Anonymous) else ""
    if not name and hasattr(node, "sql_name"):
        name = node.sql_name().lower()
    if name != "date_trunc":
        return None
    return [c.name.lower() for c in node.find_all(exp.Column)]


def _predicate_ok(node, dims: set[str]) -> bool:
    if isinstance(node, exp.Column):
        return node.name.lower() in dims
    if isinstance(node, exp.Not) and isinstance(node.this, exp.Column):
        return node.this.name.lower() in dims
    if isinstance(node, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
        for side in (node.left, node.right):
            cols = _date_trunc_columns(side)
            if cols is not None:
                return bool(cols) and all(c in dims for c in cols)
        return (_column_name(node.left) in dims) or (_column_name(node.this) in dims)
    if isinstance(node, exp.In):
        return _column_name(node.this) in dims
    if isinstance(node, exp.Between):
        return _column_name(node.this) in dims
    if isinstance(node, exp.Is):
        return _column_name(node.this) in dims
    return False


def _default_present(predicates, default: str | None) -> bool:
    if not default:
        return True
    for node in predicates:
        if isinstance(node, exp.Column) and node.name.lower() == default:
            return True
        if isinstance(node, (exp.EQ, exp.Is)) and _column_name(getattr(node, "this", None) or node.left) == default:
            return True
    return False


def _select_exprs(tree: exp.Expression) -> list:
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    return list(select.expressions) if select is not None else []


def _is_agg(node: exp.Expression) -> bool:
    return any(isinstance(n, exp.AggFunc) for n in node.walk())


def _rewrite_count_star(expr_sql: str, view: str) -> str:
    """Under the current-adjudication filter, one row is one claim_payer_key."""
    if view != "sem.remit_claims":
        return expr_sql
    text = _norm(expr_sql)
    text = text.replace("count(*)", "count(distinctclaim_payer_key)")
    return text


def _match_metric(view: str, expr_sql: str, model: dict, current_grain: bool) -> str | None:
    text = _norm(expr_sql)
    if current_grain:
        text = _rewrite_count_star(text, view)
    bodies = _metric_exprs(model, view)
    for name, body in bodies.items():
        if text == body or (current_grain and text == _rewrite_count_star(body, view)):
            return name
    if view == "sem.remit_claims" and text == "avg(is_denied)":
        return "denial_rate"
    if view == "sem.remit_lines" and text == "avg(line_is_denied)":
        return "line_denial_rate"
    if view == "sem.claims" and text in ("count(distinctclaim_key)", "count(*)"):
        # count(*) is not the same as count distinct. Only the distinct form is submitted_claims.
        if text == "count(distinctclaim_key)":
            return "submitted_claims"
    # ratio written as numerator/denominator
    if "/" in text:
        left, _, right = text.partition("/")
        left_name = next((n for n, body in bodies.items() if body == left.strip()), None)
        right_name = next((n for n, body in bodies.items() if body == right.strip()), None)
        for ratio, num, den in _ratios(model):
            if left_name == num and right_name == den:
                return ratio
    return None


def _group_ok(tree: exp.Expression, dims: set[str]) -> bool:
    group = tree.args.get("group")
    if group is None:
        # a wrapping select may not be the one with the group. Check the outermost.
        select = tree if isinstance(tree, exp.Select) else None
        group = select.args.get("group") if select is not None else None
    if group is None:
        return True
    for expression in group.expressions:
        if isinstance(expression, exp.Column) and expression.name.lower() in dims:
            continue
        if isinstance(expression, exp.Anonymous) and expression.name.lower() == "date_trunc":
            cols = [c.name.lower() for c in expression.find_all(exp.Column)]
            if cols and all(c in dims for c in cols):
                continue
        # GROUP BY 1
        if isinstance(expression, exp.Literal):
            continue
        return False
    return True


def _having_ok(tree: exp.Expression) -> bool:
    having = tree.args.get("having")
    if having is None:
        return True
    node = having.this
    if isinstance(node, (exp.GTE, exp.GT, exp.LTE, exp.LT, exp.EQ)) and isinstance(node.left, exp.AggFunc):
        return True
    return False


def _cohort_shape(sql: str, tree: exp.Expression) -> bool:
    if tree.find(exp.Join) or tree.find(exp.Window) or tree.find(exp.Union):
        return False
    tables = set(_tables(tree))
    if tables != {"sem.claims"}:
        return False
    compact = re.sub(r"\s+", "", sql.lower())
    if "min(submission_date)" not in compact and "min(service_date_from)" not in compact:
        return False
    if re.search(r"\b(sum|avg|median|quantile)\s*\(", sql, re.I):
        return False
    if re.search(r"\bcase\b", sql, re.I):
        return False
    return True


def covers(sql: str, model: dict | None = None, *, expect_refusal: bool = False, escalate: bool = False) -> dict:
    model = model or _load_model()
    if expect_refusal:
        return {"expressible": True, "lane": "refuse", "reason": "safety refusal, not a metric"}
    if escalate or (sql and "lossless." in sql.lower()):
        return {"expressible": False, "lane": "fallback", "reason": "not a sem.* question"}
    if not sql:
        return {"expressible": False, "lane": "fallback", "reason": "no gold SQL"}
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except sqlglot.errors.ParseError as e:
        return {"expressible": False, "lane": "fallback", "reason": f"gold SQL did not parse: {e}"}
    if tree.find(exp.Window) or tree.find(exp.Union) or tree.find(exp.Join):
        return {"expressible": False, "lane": "fallback", "reason": "join, union, or window"}
    blocked = _funcs(tree) & _BLOCKED_FUNCS
    if blocked:
        return {"expressible": False, "lane": "fallback", "reason": f"function {sorted(blocked)}"}
    if _cohort_shape(sql, tree):
        return {"expressible": True, "lane": "typed_cohort", "reason": "claim cohort on sem.claims"}
    if tree.find(exp.Subquery) or tree.find(exp.CTE):
        return {"expressible": False, "lane": "fallback", "reason": "subquery that is not a supported cohort"}
    tables = _tables(tree)
    if not tables or any(not t.startswith("sem.") for t in tables):
        return {"expressible": False, "lane": "fallback", "reason": f"tables {tables or 'none'}"}
    if len(set(tables)) != 1:
        return {"expressible": False, "lane": "fallback", "reason": "more than one view"}
    view = tables[0]
    dims = _dims(model)
    predicates = _split_and(tree.args.get("where"))
    # unbalanced_payments is COUNT(*) FILTER (WHERE NOT is_balanced). Gold writes that as a WHERE.
    # is_balanced is not a catalog dimension; the metric is the typed form of this question.
    if (view == "sem.payments" and len(predicates) == 1 and isinstance(predicates[0], exp.Not)
            and isinstance(predicates[0].this, exp.Column) and predicates[0].this.name.lower() == "is_balanced"):
        aggs = [e.this if isinstance(e, exp.Alias) else e for e in _select_exprs(tree) if _is_agg(e)]
        labels = [e for e in _select_exprs(tree) if not _is_agg(e)]
        if len(aggs) == 1 and not labels and _norm(aggs[0].sql(dialect="duckdb")) == "count(*)":
            return {"expressible": True, "lane": "typed", "reason": "catalog metric unbalanced_payments",
                    "metrics": ["unbalanced_payments"]}
    if not all(_predicate_ok(p, dims) for p in predicates):
        return {"expressible": False, "lane": "fallback", "reason": "predicate is not a catalog dimension"}
    if not _group_ok(tree, dims):
        return {"expressible": False, "lane": "fallback", "reason": "group by is not a catalog dimension"}
    if not _having_ok(tree):
        return {"expressible": False, "lane": "fallback", "reason": "having is not a minimum group size"}
    default = _defaults(model).get(view)
    current = _default_present(predicates, default)
    measures = []
    for expression in _select_exprs(tree):
        node = expression.this if isinstance(expression, exp.Alias) else expression
        if _is_agg(expression):
            matched = _match_metric(view, node.sql(dialect="duckdb"), model,
                                    current_grain=current and view == "sem.remit_claims")
            if not matched:
                return {"expressible": False, "lane": "fallback",
                        "reason": f"measure is not a catalog metric: {node.sql(dialect='duckdb')[:140]}"}
            measures.append(matched)
            continue
        if isinstance(node, exp.Column) and node.name.lower() in dims:
            continue
        if _date_trunc_columns(node):
            continue
        return {"expressible": False, "lane": "fallback",
                "reason": f"selected label is not a dimension: {expression.sql(dialect='duckdb')[:80]}"}
    if not measures:
        return {"expressible": False, "lane": "fallback", "reason": "no aggregate measure"}
    if default and not current:
        # count(distinct claim_key) on sem.claims is unchanged by is_latest_submission.
        # A filter is safe only when it is constant on every submission of the claim.
        filter_cols = [_column_name(p.this) if isinstance(p, exp.Not) else _column_name(p) or _column_name(getattr(p, "left", None)) or _column_name(getattr(p, "this", None)) for p in predicates]
        invariant = (view == "sem.claims" and measures == ["submitted_claims"]
                     and all(c in _CLAIM_LEVEL_FILTERS for c in filter_cols if c))
        if not invariant:
            return {"expressible": False, "lane": "fallback",
                    "reason": f"gold omits the default filter {default}; the compiler would add it"}
    return {"expressible": True, "lane": "typed", "reason": "catalog metrics on " + view, "metrics": measures}


def benchmark_items() -> list[dict]:
    items = []
    for case in yaml.safe_load(QUESTIONS.read_text()):
        items.append({"id": case["id"], "source": "handwritten", "sql": case.get("gold_sql"),
                      "expect_refusal": bool(case.get("expect_refusal")), "escalate": bool(case.get("escalate")),
                      "category": case.get("category")})
    if AUTHORED.exists():
        for case in json.loads(AUTHORED.read_text()):
            items.append({"id": case["id"], "source": "authored", "sql": case.get("gold_sql"),
                          "expect_refusal": bool(case.get("expect_refusal")), "escalate": bool(case.get("escalate")),
                          "category": case.get("category")})
    from bench.question_families import FAMILIES
    for family in FAMILIES:
        items.append({"id": "family:" + family.family_id, "source": "family", "sql": family.gold_sql,
                      "expect_refusal": False, "escalate": False, "category": family.category})
    return items


def summarize(items: list[dict] | None = None, model: dict | None = None) -> dict:
    model = model or _load_model()
    items = items or benchmark_items()
    rows = []
    for item in items:
        verdict = covers(item.get("sql") or "", model, expect_refusal=item["expect_refusal"], escalate=item["escalate"])
        rows.append({**item, **verdict})
    def pct(source: str | None, analytic_only: bool) -> dict:
        chosen = [r for r in rows if source is None or r["source"] == source]
        if analytic_only:
            chosen = [r for r in chosen if r["lane"] != "refuse"]
        n = len(chosen)
        hit = sum(1 for r in chosen if r["expressible"])
        return {"n": n, "expressible": hit, "pct": round(100.0 * hit / n, 1) if n else None}
    return {
        "rows": rows,
        "concrete_analytic": pct_sources(rows, ("handwritten", "authored"), analytic_only=True),
        "concrete_all": pct_sources(rows, ("handwritten", "authored"), analytic_only=False),
        "handwritten_analytic": pct("handwritten", True),
        "authored_analytic": pct("authored", True),
        "family_analytic": pct("family", True),
        "all_analytic": pct(None, True),
    }


def pct_sources(rows, sources, analytic_only: bool) -> dict:
    chosen = [r for r in rows if r["source"] in sources and (not analytic_only or r["lane"] != "refuse")]
    n = len(chosen)
    hit = sum(1 for r in chosen if r["expressible"])
    return {"n": n, "expressible": hit, "pct": round(100.0 * hit / n, 1) if n else None}


def main() -> None:
    report = summarize()
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))
    print("\n# expressible")
    for row in report["rows"]:
        if row["expressible"]:
            print(f"{row['lane']:14} {row['source']:12} {row['id']}")
    print("\n# not expressible")
    for row in report["rows"]:
        if not row["expressible"]:
            print(f"{row['source']:12} {row['id']}: {row['reason'][:110]}")


if __name__ == "__main__":
    main()
