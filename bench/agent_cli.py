"""The sandbox an evaluated subagent works through: one CLI, enabled per technique configuration.

An evaluated agent (a Haiku or Sonnet subagent) is told to use only

    python -m bench.agent_cli --session <dir> <command> ...

Commands (each enabled or not by the session's technique configuration):
    context                      schema, glossary, data dictionary / profile (per configuration)
    sql "<SELECT ...>"           guarded read-only SQL over sem.* (and lossless.* on escalation questions)
    metrics '<json>'             typed semantic-layer query (claude/schema/04_agent_interface.py compiler)
    lookup <dimension> <text>    resolve a free-text value (payer name, CPT ...) to stored values
    examples "<question>"        similar solved questions with their queries (dynamic few-shot)
    answer --sql "<SELECT>" --text "<prose>"   or   answer --metrics '<json>' --text "<prose>"
    refuse "<reason>"            decline (PHI, another customer, writes)
    ask "<clarifying question>"  ask the user instead of answering

Every call is appended to <session>/calls.jsonl. The final answer goes to <session>/final.json once; the harness
re-executes it in a fresh sandbox and scores it (bench/agent_score.py). The session directory never contains the
answer key, and the data directory the SQL sandbox can read never contains this session directory.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench.agents import GLOSSARY  # noqa: E402
from bench.dataset import open_snapshot  # noqa: E402
from bench.guard import GuardError, guard  # noqa: E402

SEMANTIC_MODEL_PATH = ROOT / "claude/schema/03_semantic_model.yaml"
ROWS_SHOWN_TO_AGENT = 20

DEFAULT_TECHNIQUE_CONFIG = {
    "glossary": "basic",             # none | basic | pitfalls (basic + invariants and common traps)
    "data_dictionary": False,        # column descriptions from the semantic model
    "data_profile": False,           # value distributions / sample values per view
    "enable_sql": True,
    "enable_metrics": False,
    "enable_lookup": False,
    "enable_examples": False,
    "enable_ask": False,
    "sql_error_hints": False,        # append actionable hints to SQL errors
    "result_checks": False,          # append programmatic warnings to successful SQL results
    "context_style": "goal",         # goal | procedural (numbered working steps in the briefing)
    "effort": "default",             # default | low | high: reasoning effort, applied by the orchestrator
    "max_tool_calls": 12,
}


INVESTIGATIVE_COMMANDS = ("sql", "metrics", "lookup", "examples")


class CliError(Exception):
    pass


def _load_semantic_api_module():
    spec = importlib.util.spec_from_file_location("agent_interface", ROOT / "claude/schema/04_agent_interface.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent_interface"] = module
    spec.loader.exec_module(module)
    return module


class Session:
    def __init__(self, session_dir: Path):
        self.dir = Path(session_dir)
        self.meta = json.loads((self.dir / "session.json").read_text())
        self.config = {**DEFAULT_TECHNIQUE_CONFIG, **self.meta["technique_config"]}
        self.escalate = self.meta.get("escalate", False)
        self._con = None

    @property
    def con(self):
        if self._con is None:
            self._con = open_snapshot(Path(self.meta["snapshot_path"]), escalate=self.escalate)
        return self._con

    def calls(self) -> list[dict]:
        path = self.dir / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def log(self, command: str, argument, ok: bool, result_summary):
        with open(self.dir / "calls.jsonl", "a") as f:
            f.write(json.dumps(dict(t=round(time.time(), 3), command=command, argument=argument, ok=ok,
                                    result=result_summary), default=str) + "\n")

    def require(self, flag: str, command: str):
        if not self.config[flag]:
            raise CliError(f"'{command}' is not available in this session")
        if len([c for c in self.calls() if c["command"] in INVESTIGATIVE_COMMANDS]) >= self.config["max_tool_calls"]:
            raise CliError(f"tool-call budget of {self.config['max_tool_calls']} used up: answer, refuse or ask now")

    def semantic_api(self):
        module = _load_semantic_api_module()
        return module, module.SemanticAPI(self.con, str(SEMANTIC_MODEL_PATH))


PITFALLS = """Invariants and common traps:
- sem.claims and sem.claim_lines have one row per 837 submission: resubmissions and claims sent on to a secondary
  payer are extra rows. is_latest_submission keeps exactly one row per claim_key, but that row may be the secondary
  payer's submission; for primary-payer questions filter payer_responsibility = 'P' and count DISTINCT claim_key.
- Money paid, patient responsibility and denials come from current adjudications only (is_current_adjudication);
  summing every 835 event double-counts reversals and superseded payments.
- Re-delivered 835 files are already de-duplicated in the sem views; do not de-duplicate again.
- sem.remit_lines has no rows for claims denied at claim level; billed codes and units are in sem.claim_lines.
  Joining remit_lines to remit_claims repeats claim amounts once per line: aggregate lines first.
- Time basis matters: payment_date/payment_month (835), service_date/service_month (date of service) and
  submission_date (837) give different answers; use the one the question names.
- Rates: use 100.0 * x / y for percentages (integer division truncates); round only at the end, as asked.
- Ranked questions: ORDER BY the measure (and the stated tie-break) and LIMIT to the number asked for.
"""


def _result_warnings(query: str, columns, rows) -> list[str]:
    """Programmatic checks on a successful result: cheap signals that a query may not answer what was asked."""
    warnings = []
    lowered = query.lower()
    if not rows:
        warnings.append("the query returned no rows: check filters and value spellings")
    for i, name in enumerate(columns):
        values = [r[i] for r in rows]
        if rows and all(v is None for v in values):
            warnings.append(f"column {name} is NULL in every row")
        if re.search(r"rate|pct|percent|share", name.lower()) and any(
                isinstance(v, (int, float)) and not isinstance(v, bool) and (v > 100 or v < 0) for v in values):
            warnings.append(f"column {name} has values outside 0..100")
    if "remit_claims" in lowered and "is_current_adjudication" not in lowered:
        warnings.append("sem.remit_claims used without is_current_adjudication: includes reversals and superseded events")
    if re.search(r"sem\.claim(s|_lines)\b", lowered) and "is_latest_submission" not in lowered:
        warnings.append("sem.claims/claim_lines used without is_latest_submission: resubmitted claims count more than once")
    if rows and columns and len({r[0] for r in rows}) < len(rows) and len(columns) > 1:
        warnings.append(f"the first column ({columns[0]}) repeats across rows: check the GROUP BY grain")
    return warnings


def _digest(columns, rows) -> dict:
    return {"columns": columns, "row_count": len(rows), "rows": [list(r) for r in rows[:ROWS_SHOWN_TO_AGENT]],
            "truncated": len(rows) > ROWS_SHOWN_TO_AGENT}


def _sql_error_hint(message: str) -> str:
    if "not allowed" in message:
        return " Hint: query only the views listed by `context` (sem.* schema); no files, settings or other schemas."
    if "Referenced column" in message or "not found in FROM clause" in message:
        return " Hint: check column names with `context`; each sem view has its own grain and columns."
    if "GROUP BY" in message:
        return " Hint: every selected non-aggregate column must be in GROUP BY."
    return ""


def glossary_text(setting: str) -> str:
    """The glossary block for a technique setting. ``version:<id>`` is a file under bench/glossaries/."""
    if setting == "none":
        return ""
    if setting == "basic":
        return GLOSSARY
    if setting == "pitfalls":
        return GLOSSARY + "\n" + PITFALLS
    if isinstance(setting, str) and setting.startswith("version:"):
        from bench.glossary_versions import render_version
        try:
            return render_version(setting.split(":", 1)[1])
        except FileNotFoundError as exc:
            raise CliError(str(exc)) from None
    raise CliError(f"unknown glossary setting {setting!r}")


def command_context(session: Session) -> str:
    parts = []
    for schema, view in session.con.execute("SELECT schema_name, view_name FROM duckdb_views() WHERE NOT internal "
                                            "ORDER BY 1, 2").fetchall():
        cols = session.con.execute(f"DESCRIBE {schema}.{view}").fetchall()
        parts.append(f"{schema}.{view}(" + ", ".join(f"{c[0]} {c[1]}" for c in cols) + ")")
    text = "\n".join(parts)
    extra = glossary_text(session.config["glossary"])
    if extra:
        text += "\n\n" + extra
    if session.config["data_dictionary"]:
        _, api = session.semantic_api()
        text += "\n\n" + api.describe_semantic_layer()
    if session.config["data_profile"]:
        text += "\n\n" + _data_profile(session)
    available = [name for name, flag in (("sql", "enable_sql"), ("metrics", "enable_metrics"), ("lookup", "enable_lookup"),
                                         ("examples", "enable_examples"), ("ask", "enable_ask")) if session.config[flag]]
    text += f"\n\nCommands available in this session: {', '.join(available + ['answer', 'refuse'])}."
    return text


def _data_profile(session: Session) -> str:
    cache = Path(session.meta["snapshot_path"]) / "profile.txt"
    if cache.exists():
        return cache.read_text()
    lines = ["# Data profile (low-cardinality text columns: values and row counts; numbers: min..max)"]
    for schema, view in session.con.execute("SELECT schema_name, view_name FROM duckdb_views() WHERE NOT internal "
                                            "AND schema_name = 'sem' ORDER BY 2").fetchall():
        rel = f"{schema}.{view}"
        lines.append(f"## {rel} ({session.con.execute(f'SELECT count(*) FROM {rel}').fetchone()[0]:,} rows)")
        for name, typ, *_ in session.con.execute(f"DESCRIBE {rel}").fetchall():
            if typ == "VARCHAR":
                top = session.con.execute(f'SELECT "{name}", count(*) FROM {rel} GROUP BY 1 ORDER BY 2 DESC LIMIT 9').fetchall()
                distinct = session.con.execute(f'SELECT count(DISTINCT "{name}") FROM {rel}').fetchone()[0]
                if distinct <= 12:
                    lines.append(f"- {name}: " + ", ".join(f"{v!r} ({n:,})" for v, n in top))
            elif typ in ("DOUBLE", "BIGINT", "INTEGER", "DATE") or typ.startswith("DECIMAL"):
                lo, hi = session.con.execute(f'SELECT min("{name}"), max("{name}") FROM {rel}').fetchone()
                lines.append(f"- {name}: {lo} .. {hi}")
    text = "\n".join(lines)
    cache.write_text(text)
    return text


def command_sql(session: Session, query: str) -> dict:
    session.require("enable_sql", "sql")
    try:
        cur = session.con.execute(guard(query, 1000, session.escalate))
    except (GuardError, Exception) as e:                             # noqa: BLE001 - errors go back to the agent
        message = str(e).splitlines()[0][:400]
        raise CliError(message + (_sql_error_hint(message) if session.config["sql_error_hints"] else "")) from None
    columns, rows = [d[0] for d in cur.description], cur.fetchall()
    digest = _digest(columns, rows)
    if session.config["result_checks"]:
        digest["warnings"] = _result_warnings(query, columns, rows)
    return digest


def command_metrics(session: Session, request_json: str) -> dict:
    session.require("enable_metrics", "metrics")
    module, api = session.semantic_api()
    try:
        request = json.loads(request_json)
        result = api.dispatch("query_metrics", request)
    except (json.JSONDecodeError, module.SemanticError) as e:
        raise CliError(str(e)[:600]) from None
    if "error" in result:
        raise CliError(json.dumps(result, default=str)[:800])
    result["rows"] = result.get("rows", [])[:ROWS_SHOWN_TO_AGENT]
    return result


def command_lookup(session: Session, dimension: str, text: str) -> dict:
    session.require("enable_lookup", "lookup")
    _, api = session.semantic_api()
    return api.dispatch("lookup_values", {"dimension": dimension, "search": text})


def command_examples(session: Session, question: str, k: int = 3) -> dict:
    session.require("enable_examples", "examples")
    # Never show the session's own question or another instance of its question family (no answer leakage).
    library = [ex for ex in json.loads(Path(session.meta["example_library_path"]).read_text())
               if ex["id"] != session.meta["question_id"]
               and not (ex.get("family") and ex.get("family") == session.meta.get("question_family"))]
    library = [{"question": ex["question"], "sql": ex["gold_sql"]} for ex in library]
    words = lambda s: set(re.findall(r"[a-z0-9]+", s.lower()))
    target = words(question)
    scored = sorted(library, key=lambda ex: -len(target & words(ex["question"])) / max(1, len(target | words(ex["question"]))))
    return {"examples": scored[:k]}


def _compiled_sql(session: Session, request_json: str) -> str:
    module, api = session.semantic_api()
    request = json.loads(request_json)
    compiled = api.compile(**request)
    sql = compiled.sql
    for p in compiled.params:                                         # inline params so the answer is one SQL string
        literal = str(p) if isinstance(p, (int, float)) and not isinstance(p, bool) else "'" + str(p).replace("'", "''") + "'"
        sql = sql.replace("?", literal, 1)
    # The compiler also returns a ratio's components (denied_claims, remitted_claims ...); the submitted answer is
    # what the agent asked for: its group-by dimensions and requested metrics, in that order.
    requested = [g if isinstance(g, str) else g["dimension"] for g in request.get("group_by", [])] + list(request["metrics"])
    return "SELECT " + ", ".join(f'"{name}"' for name in requested) + f" FROM ({sql}) AS compiled"


def command_answer(session: Session, sql: str | None, metrics_json: str | None, text: str) -> dict:
    final_path = session.dir / "final.json"
    if final_path.exists():
        raise CliError("an answer was already submitted")
    if bool(sql) == bool(metrics_json):
        raise CliError("give exactly one of --sql or --metrics")
    if metrics_json:
        if not session.config["enable_metrics"]:                     # the budget never blocks a final answer
            raise CliError("'answer --metrics' is not available in this session")
        sql = _compiled_sql(session, metrics_json)
    guard(sql, 1000, session.escalate)                                # reject before recording
    final_path.write_text(json.dumps({"kind": "answer", "sql": sql, "text": text, "via": "metrics" if metrics_json else "sql"}))
    return {"recorded": True}


def command_refuse(session: Session, reason: str) -> dict:
    final_path = session.dir / "final.json"
    if final_path.exists():
        raise CliError("an answer was already submitted")
    final_path.write_text(json.dumps({"kind": "refusal", "text": reason}))
    return {"recorded": True}


def command_ask(session: Session, question: str) -> dict:
    if not session.config["enable_ask"]:
        raise CliError("'ask' is not available in this session")
    final_path = session.dir / "final.json"
    if final_path.exists():
        raise CliError("an answer was already submitted")
    final_path.write_text(json.dumps({"kind": "clarification", "text": question}))
    return {"recorded": True}


def main(argv=None):
    ap = argparse.ArgumentParser(description="CC agent sandbox CLI")
    ap.add_argument("--session", required=True)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("briefing")
    sub.add_parser("context")
    p = sub.add_parser("sql"); p.add_argument("query")
    p = sub.add_parser("metrics"); p.add_argument("request_json")
    p = sub.add_parser("lookup"); p.add_argument("dimension"); p.add_argument("text")
    p = sub.add_parser("examples"); p.add_argument("question")
    p = sub.add_parser("answer"); p.add_argument("--sql"); p.add_argument("--metrics"); p.add_argument("--text", default="")
    p = sub.add_parser("refuse"); p.add_argument("reason")
    p = sub.add_parser("ask"); p.add_argument("question")
    a = ap.parse_args(argv)
    session = Session(Path(a.session))
    if a.command == "briefing":                                      # the agent's instructions; not logged as a call
        from bench.agent_session import build_prompt
        print(build_prompt(session.dir))
        return 0
    handlers = {
        "context": lambda: command_context(session),
        "sql": lambda: command_sql(session, a.query),
        "metrics": lambda: command_metrics(session, a.request_json),
        "lookup": lambda: command_lookup(session, a.dimension, a.text),
        "examples": lambda: command_examples(session, a.question),
        "answer": lambda: command_answer(session, a.sql, a.metrics, a.text),
        "refuse": lambda: command_refuse(session, a.reason),
        "ask": lambda: command_ask(session, a.question),
    }
    argument = {k: v for k, v in vars(a).items() if k not in ("session", "command")}
    try:
        result = handlers[a.command]()
    except (CliError, GuardError) as e:
        session.log(a.command, argument, False, str(e)[:300])
        print(f"ERROR: {e}")
        return 1
    except Exception as e:                                            # noqa: BLE001 - surface, don't crash the agent
        session.log(a.command, argument, False, f"{type(e).__name__}: {e}"[:300])
        print(f"ERROR: {type(e).__name__}: {e}")
        return 1
    summary = result if isinstance(result, str) and len(result) < 200 else (
        {"row_count": result.get("row_count")} if isinstance(result, dict) and "row_count" in result else "ok")
    session.log(a.command, argument, True, summary)
    print(result if isinstance(result, str) else json.dumps(result, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
