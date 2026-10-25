"""Tool layer over a per-request session.

Model-written SQL is executed only by ``run_sql`` and ``sample_rows``, and only
after ``bench.guard.guard``. There is no raw-execute tool. ``request_escalation``
is the only way to widen the session: it records an audit event, asks the opener
for a new escalated session (``ccsim.query.open_session(..., escalate=True)`` in
production), and then the guard is allowed to name ``lossless.*``. Identifier
columns are redacted on the way out even if a view ever grew one.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

from bench.agents import GLOSSARY
from bench.guard import GuardError, guard

from analyzer.checks import PITFALLS, result_warnings, sql_error_message
from analyzer.config import AnalyzerConfig
from analyzer.opener import SessionHandle, SessionOpener

PHI_COLUMN_NAMES = frozenset({
    "name_last_or_org", "name_first", "name_middle", "name_prefix", "name_suffix",
    "dmg_birth_date", "dmg_gender", "addr_line1", "addr_line2", "city", "postal_code",
    "id_value", "member_id", "subscriber_id", "patient_name", "ssn",
    "bpr07_sender_dfi_id", "bpr09_sender_account", "bpr13_receiver_dfi_id", "bpr15_receiver_account",
    "raw_text", "elements", "pat06_death_date", "pat08_weight", "original_filename",
})
_PHI_NAME = re.compile(
    r"(ssn|birth|dob|address|member_id|subscriber_id|patient_name|account_number|name_first|name_last)",
    re.IGNORECASE)
_RELATION = re.compile(r"^(sem|lossless)\.([A-Za-z_][A-Za-z0-9_]*)$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HIGH_CARDINALITY_COLUMNS = frozenset({
    "claim_key", "claim_id", "remit_claim_id", "payer_claim_control_number", "check_or_eft_number",
    "billing_npi", "rendering_npi", "referring_npi",
})


class ToolError(ValueError):
    """A tool failure the model is allowed to see and recover from."""


class QueryTimeout(TimeoutError):
    pass


def is_phi_column(name: str) -> bool:
    return name.lower() in PHI_COLUMN_NAMES or bool(_PHI_NAME.search(name))


def jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    return value


def execute_with_time_limit(connection, sql: str, timeout_seconds: float):
    """Run ``sql`` on the calling thread. A watchdog calls ``connection.interrupt()``."""
    if timeout_seconds is None or timeout_seconds <= 0:
        return connection.execute(sql)
    finished = threading.Event()
    fired = {"value": False}

    def watchdog():
        if not finished.wait(timeout_seconds):
            fired["value"] = True
            interrupt = getattr(connection, "interrupt", None)
            if interrupt is not None:
                interrupt()

    thread = threading.Thread(target=watchdog, name="analyzer-query-timeout", daemon=True)
    thread.start()
    try:
        return connection.execute(sql)
    except Exception as exc:
        if fired["value"]:
            raise QueryTimeout(f"query exceeded the {timeout_seconds:g}s time limit") from exc
        raise
    finally:
        finished.set()


def tool_spec(config: AnalyzerConfig) -> list[dict]:
    """Anthropic tool definitions for the enabled surface. Final answer tools included."""
    spec = [
        {"name": "list_relations", "description": "List the relations this session can read.",
         "input_schema": {"type": "object", "properties": {}}},
        {"name": "describe_relation", "description": "Columns and types for one relation.",
         "input_schema": {"type": "object", "properties": {"relation": {"type": "string"}}, "required": ["relation"]}},
        {"name": "sample_rows", "description": "Return a few rows from one relation. Identifier columns are redacted.",
         "input_schema": {"type": "object", "properties": {
             "relation": {"type": "string"}, "n": {"type": "integer"}}, "required": ["relation"]}},
        {"name": "run_sql",
         "description": "Run one read-only DuckDB SELECT over the listed relations. "
                        "Row and time limits apply. The SQL you submit as the final answer is re-executed.",
         "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}},
        {"name": "request_escalation",
         "description": "Ask for an audited session that can also read PHI-stripped lossless.* relations. "
                        "Does not run SQL. Patient names, member ids, birth dates, addresses and bank accounts "
                        "are still absent. Pass a specific reason.",
         "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}},
    ]
    if config.data_profile:
        spec.append({"name": "profile",
                     "description": "Low-cardinality values and numeric ranges. Identifier columns are not profiled.",
                     "input_schema": {"type": "object", "properties": {"relation": {"type": "string"}}}})
    if config.data_dictionary:
        spec.append({"name": "lookup_dictionary",
                     "description": "Grain, purpose, and column descriptions from the semantic model.",
                     "input_schema": {"type": "object", "properties": {"relation": {"type": "string"}}}})
    if config.glossary != "none":
        spec.append({"name": "lookup_glossary",
                     "description": "Business definitions. Optional term filters the lines.",
                     "input_schema": {"type": "object", "properties": {"term": {"type": "string"}}}})
    if config.enable_metrics:
        spec.append({"name": "query_metrics",
                     "description": "Typed semantic-layer query. The compiler writes the SQL over sem.*.",
                     "input_schema": {"type": "object", "properties": {
                         "metrics": {"type": "array", "items": {"type": "string"}},
                         "group_by": {"type": "array"},
                         "filters": {"type": "array"},
                         "time_range": {"type": "object"},
                         "order_by": {"type": "array"},
                         "limit": {"type": "integer"},
                         "min_group_size": {"type": "integer"}}, "required": ["metrics"]}})
    if config.enable_lookup:
        spec.append({"name": "lookup_values",
                     "description": "Resolve a free-text value to stored dimension values before filtering.",
                     "input_schema": {"type": "object", "properties": {
                         "dimension": {"type": "string"}, "search": {"type": "string"}},
                         "required": ["dimension", "search"]}})
    if config.enable_examples:
        spec.append({"name": "lookup_examples",
                     "description": "Similar solved questions and their SQL. The current question and its family are excluded.",
                     "input_schema": {"type": "object", "properties": {
                         "question": {"type": "string"}, "k": {"type": "integer"}}, "required": ["question"]}})
    spec.append({"name": "final_answer",
                 "description": "Submit the SQL that computes the answer and a one-paragraph answer for the user. "
                                "Every number in the paragraph must come from that result.",
                 "input_schema": {"type": "object", "properties": {
                     "sql": {"type": "string"},
                     "answer": {"type": "string"},
                     "assumptions": {"type": "array", "items": {"type": "string"}}},
                     "required": ["sql", "answer"]}})
    spec.append({"name": "refuse",
                 "description": "Decline a request for patient identifiers, another customer, or a change to the data.",
                 "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}})
    if config.enable_ask:
        spec.append({"name": "ask",
                     "description": "Ask one clarifying question when the request cannot be answered as stated.",
                     "input_schema": {"type": "object", "properties": {"question": {"type": "string"}},
                                      "required": ["question"]}})
    if not config.enable_sql:
        spec = [tool for tool in spec if tool["name"] != "run_sql"]
    return spec


class ToolLayer:
    def __init__(self, opener: SessionOpener, config: AnalyzerConfig | None = None, *,
                 question_id: str | None = None, question_family: str | None = None,
                 example_library: list | None = None, audit_sink: Callable[[dict], None] | None = None,
                 restored_escalation: bool = False):
        self.opener = opener
        self.config = config or AnalyzerConfig()
        self.question_id = question_id
        self.question_family = question_family
        self.example_library = example_library or []
        self.audit_sink = audit_sink
        self.audit: list[dict] = []
        self.calls: list[dict] = []
        self.escalated = False
        self._semantic = None
        self._model = None
        self._tool_calls_used = 0
        self.session = opener.open(restored_escalation)
        self.escalated = restored_escalation
        self._audit("session_open", restored_escalation=restored_escalation)

    def enabled_tool_names(self) -> set[str]:
        return {tool["name"] for tool in tool_spec(self.config) if tool["name"] not in {"final_answer", "refuse", "ask"}}

    def dispatch(self, name: str, arguments: dict | None) -> dict:
        arguments = dict(arguments or {})
        if "charge" in arguments:
            raise ToolError("charge is not a tool argument")
        if name not in self.enabled_tool_names():
            raise ToolError(f"{name} is not enabled in this session")
        try:
            if name == "list_relations":
                return {"relations": self.list_relations()}
            if name == "describe_relation":
                return self.describe_relation(arguments["relation"])
            if name == "sample_rows":
                return self.sample_rows(arguments["relation"], int(arguments.get("n", self.config.sample_row_limit)))
            if name == "profile":
                return self.profile(arguments.get("relation"))
            if name == "lookup_dictionary":
                return self.lookup_dictionary(arguments.get("relation"))
            if name == "lookup_glossary":
                return self.lookup_glossary(arguments.get("term"))
            if name == "run_sql":
                return self.run_sql(arguments["sql"])
            if name == "request_escalation":
                return self.request_escalation(arguments.get("reason", ""))
            if name == "query_metrics":
                return self.query_metrics(arguments)
            if name == "lookup_values":
                return self.lookup_values(arguments["dimension"], arguments["search"])
            if name == "lookup_examples":
                return self.lookup_examples(arguments.get("question", ""), int(arguments.get("k", 3)))
        except KeyError as exc:
            raise ToolError(f"{name} is missing argument {exc}") from None
        raise ToolError(f"unknown tool {name}")

    def list_relations(self, *, charge: bool = True) -> list[str]:
        if charge:
            self._spend("list_relations")
        allowed = {"sem", "lossless"} if self.escalated else {"sem"}
        rows = self.session.con.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema IN ('sem', 'lossless') ORDER BY 1, 2").fetchall()
        return [f"{schema}.{name}" for schema, name in rows if schema in allowed]

    def describe_relation(self, relation: str, *, charge: bool = True) -> dict:
        if charge:
            self._spend("describe_relation")
        relation = self._check_relation(relation)
        described = self.session.con.execute(f"DESCRIBE {relation}").fetchall()
        columns = [{"name": row[0], "type": row[1], "identifier": is_phi_column(row[0])} for row in described]
        return {"relation": relation, "columns": columns}

    def sample_rows(self, relation: str, n: int | None = None, *, charge: bool = True) -> dict:
        if charge:
            self._spend("sample_rows")
        relation = self._check_relation(relation)
        limit = max(1, min(int(n or self.config.sample_row_limit), self.config.rows_shown_to_model))
        return self._execute_guarded(f"SELECT * FROM {relation} LIMIT {limit}", audit_event="sample",
                                     relation=relation)

    def profile(self, relation: str | None = None, *, charge: bool = True) -> dict:
        if charge and not self.config.data_profile:
            raise ToolError("profile is not enabled")
        if charge:
            self._spend("profile")
        relations = [self._check_relation(relation)] if relation else [
            name for name in self.list_relations(charge=False) if name.startswith("sem.")]
        sections = []
        for name in relations:
            sections.append(self._profile_relation(name))
        return {"profiles": sections}

    def lookup_dictionary(self, relation: str | None = None, *, charge: bool = True) -> dict:
        if charge and not self.config.data_dictionary:
            raise ToolError("lookup_dictionary is not enabled")
        if charge:
            self._spend("lookup_dictionary")
        model = self._semantic_model()
        present = set(self.list_relations(charge=False))
        views = model.get("views", {})
        if relation:
            relation = self._check_relation(relation)
            names = [relation]
        else:
            names = [name for name in views if name in present]
        dimensions = {**model.get("dimensions", {}), **model.get("time_dimensions", {})}
        entries = []
        for name in names:
            meta = views.get(name, {})
            described = {row[0]: row[1] for row in self.session.con.execute(f"DESCRIBE {name}").fetchall()}
            columns = []
            for column, column_type in described.items():
                if column in dimensions:
                    columns.append({"name": column, "type": column_type,
                                    "description": dimensions[column].get("description", "")})
            entries.append({"relation": name, "grain": meta.get("grain", ""), "use_for": meta.get("use_for", ""),
                            "default_filter": meta.get("default_filter"), "columns": columns})
        return {"relations": entries}

    def lookup_glossary(self, term: str | None = None, *, charge: bool = True) -> dict:
        if charge and self.config.glossary == "none":
            raise ToolError("lookup_glossary is not enabled")
        if charge:
            self._spend("lookup_glossary")
        text = _glossary_body(self.config)
        if term:
            needle = term.lower()
            lines = [line for line in text.splitlines() if needle in line.lower()]
            text = "\n".join(lines) if lines else f"No glossary line contains {term!r}."
        return {"glossary": text}

    def run_sql(self, sql: str, *, charge: bool = True, include_all_rows: bool = False) -> dict:
        if not self.config.enable_sql:
            raise ToolError("run_sql is not enabled")
        if charge:
            self._spend("run_sql")
        return self._execute_guarded(sql, audit_event="sql", include_all_rows=include_all_rows)

    def request_escalation(self, reason: str, *, charge: bool = True) -> dict:
        reason = (reason or "").strip()
        if not reason:
            raise ToolError("request_escalation requires a reason")
        if charge:
            self._spend("request_escalation")
        self._audit("escalation_requested", reason=reason)
        if self.escalated:
            self._audit("escalation_already_active", reason=reason)
            return {"escalated": True, "already": True, "relations": self.list_relations(charge=False)}
        try:
            opened = self.opener.open(True)
        except Exception as exc:
            self._audit("escalation_failed", reason=reason, error=str(exc)[:300])
            raise ToolError(f"escalation failed: {str(exc)[:300]}") from None
        previous = self.session
        self.session = opened
        self.escalated = True
        self._semantic = None
        self._audit("escalation_granted", reason=reason, version=opened.version)
        if previous.con is not opened.con:
            try:
                previous.con.close()
            except Exception:
                pass
        return {"escalated": True, "already": False, "customer": opened.customer,
                "relations": self.list_relations(charge=False)}

    def query_metrics(self, request: dict, *, charge: bool = True) -> dict:
        if charge and not self.config.enable_metrics:
            raise ToolError("query_metrics is not enabled")
        if charge:
            self._spend("query_metrics")
        api = self._semantic_api()
        try:
            result = api.query_metrics(**{key: value for key, value in request.items() if key != "charge"})
        except Exception as exc:
            raise ToolError(str(exc)[:600]) from None
        result["rows"] = result.get("rows", [])[:self.config.rows_shown_to_model]
        return result

    def lookup_values(self, dimension: str, search: str, *, charge: bool = True) -> dict:
        if charge and not self.config.enable_lookup:
            raise ToolError("lookup_values is not enabled")
        if charge:
            self._spend("lookup_values")
        api = self._semantic_api()
        try:
            return api.lookup_values(dimension, search)
        except Exception as exc:
            raise ToolError(str(exc)[:600]) from None

    def lookup_examples(self, question: str, k: int = 3, *, charge: bool = True) -> dict:
        if charge and not self.config.enable_examples:
            raise ToolError("lookup_examples is not enabled")
        if charge:
            self._spend("lookup_examples")
        library = [example for example in self.example_library
                   if example.get("id") != self.question_id
                   and not (example.get("family") and example.get("family") == self.question_family)]
        words = lambda text: set(re.findall(r"[a-z0-9]+", text.lower()))  # noqa: E731
        target = words(question)
        scored = sorted(library, key=lambda example: -len(target & words(example.get("question", "")))
                        / max(1, len(target | words(example.get("question", "")))))
        return {"examples": [{"question": example["question"], "sql": example.get("gold_sql") or example.get("sql")}
                             for example in scored[: max(0, int(k))]]}

    def _execute_guarded(self, sql: str, *, audit_event: str, relation: str | None = None,
                         include_all_rows: bool = False) -> dict:
        started = time.perf_counter()
        try:
            safe = guard(sql, self.config.row_limit, self.escalated)
            cursor = execute_with_time_limit(self.session.con, safe, self.config.time_limit_seconds)
            columns = [item[0] for item in cursor.description]
            fetched = cursor.fetchall()
        except (GuardError, QueryTimeout, Exception) as exc:  # noqa: BLE001 - the model sees the message
            message = sql_error_message(exc, self.config.sql_error_hints and self.config.self_correct != "none")
            self._audit(audit_event, ok=False, sql=sql[:500], error=message, relation=relation,
                        escalated=self.escalated)
            raise ToolError(message) from None
        columns, fetched, redacted = _redact(columns, fetched)
        warnings = result_warnings(sql, columns, fetched, self.config.result_checks)
        warnings.extend(redacted)
        full_rows = [jsonable(row) for row in fetched]
        shown = full_rows[:self.config.rows_shown_to_model]
        elapsed = round(time.perf_counter() - started, 4)
        self._audit(audit_event, ok=True, sql=sql[:500], row_count=len(fetched), relation=relation,
                    escalated=self.escalated, seconds=elapsed)
        digest = {"columns": columns, "rows": shown, "row_count": len(fetched),
                  "truncated": len(fetched) > self.config.rows_shown_to_model,
                  "row_limit": self.config.row_limit, "time_seconds": elapsed}
        if include_all_rows:
            digest["all_rows"] = full_rows
        if self.config.result_checks:
            digest["warnings"] = warnings
        elif redacted:
            digest["warnings"] = redacted
        return digest

    def _profile_relation(self, relation: str) -> dict:
        described = self.session.con.execute(f"DESCRIBE {relation}").fetchall()
        row_count = self._guarded_profile(f"SELECT count(*) FROM {relation}").fetchone()[0]
        columns = []
        for name, column_type, *_rest in described[:30]:
            if is_phi_column(name) or name in _HIGH_CARDINALITY_COLUMNS:
                columns.append({"name": name, "type": column_type, "profile": "not profiled (identifier)"})
                continue
            if not _IDENT.fullmatch(name):
                columns.append({"name": name, "type": column_type, "profile": "skipped"})
                continue
            quoted = f'"{name}"'
            try:
                if column_type == "VARCHAR" or column_type.startswith("VARCHAR"):
                    distinct = self._guarded_profile(
                        f"SELECT count(DISTINCT {quoted}) FROM {relation}").fetchone()[0]
                    entry = {"name": name, "type": column_type, "distinct": distinct}
                    if distinct is not None and distinct <= 12:
                        top = self._guarded_profile(
                            f"SELECT {quoted}, count(*) AS n FROM {relation} GROUP BY 1 ORDER BY n DESC LIMIT 8").fetchall()
                        entry["values"] = [{"value": jsonable(value), "rows": count} for value, count in top]
                    columns.append(entry)
                elif column_type in {"DOUBLE", "BIGINT", "INTEGER", "FLOAT", "DATE", "HUGEINT"} or column_type.startswith("DECIMAL"):
                    low, high = self._guarded_profile(
                        f"SELECT min({quoted}), max({quoted}) FROM {relation}").fetchone()
                    columns.append({"name": name, "type": column_type, "min": jsonable(low), "max": jsonable(high)})
                else:
                    columns.append({"name": name, "type": column_type})
            except Exception as exc:  # noqa: BLE001 - one column should not hide the rest of the profile
                columns.append({"name": name, "type": column_type, "profile": f"unavailable: {str(exc)[:120]}"})
        return {"relation": relation, "row_count": row_count, "columns": columns}

    def _guarded_profile(self, sql: str):
        """Tool-authored profile SQL still passes the guard and the time limit."""
        safe = guard(sql, self.config.row_limit, self.escalated)
        return execute_with_time_limit(self.session.con, safe, self.config.time_limit_seconds)

    def _check_relation(self, name: str) -> str:
        if not isinstance(name, str) or not _RELATION.fullmatch(name.strip()):
            raise ToolError(f"relation {name!r} must be a schema-qualified identifier such as sem.claims")
        name = name.strip()
        schema = name.split(".", 1)[0]
        allowed = {"sem", "lossless"} if self.escalated else {"sem"}
        if schema not in allowed:
            raise ToolError(
                f"relation {name} is not available in this session. "
                "Call request_escalation with a reason to read lossless.*")
        if name not in self.list_relations(charge=False):
            available = ", ".join(self.list_relations(charge=False)) or "(none)"
            raise ToolError(f"relation {name} is not in this session. Available: {available}")
        return name

    def _semantic_model(self) -> dict:
        if self._model is None:
            import yaml
            from pathlib import Path
            path = Path(__file__).resolve().parent.parent / "claude/schema/03_semantic_model.yaml"
            self._model = yaml.safe_load(path.read_text())
        return self._model

    def _semantic_api(self):
        if self._semantic is None:
            from bench.agent_cli import SEMANTIC_MODEL_PATH, _load_semantic_api_module
            module = _load_semantic_api_module()
            self._semantic = module.SemanticAPI(self.session.con, str(SEMANTIC_MODEL_PATH))
        return self._semantic

    def _spend(self, name: str):
        if self._tool_calls_used >= self.config.max_tool_calls:
            raise ToolError(f"tool-call budget of {self.config.max_tool_calls} is used up: "
                            "submit final_answer, refuse, or ask")
        self._tool_calls_used += 1
        self.calls.append({"tool": name})

    def _audit(self, event: str, **detail):
        detail = {key: value for key, value in detail.items() if value is not None}
        record = {
            "event": event,
            "customer": getattr(self.session, "customer", None),
            "user": getattr(self.session, "user", None),
            "version": getattr(self.session, "version", None),
            "escalated": self.escalated,
            **detail,
        }
        self.audit.append(record)
        if self.audit_sink is not None:
            self.audit_sink(record)


def _glossary_body(config: AnalyzerConfig) -> str:
    if config.glossary == "none":
        return ""
    text = GLOSSARY.strip()
    if config.glossary == "pitfalls":
        text += "\n\n" + PITFALLS.strip()
    return text


def _redact(columns: list[str], rows: list) -> tuple[list[str], list, list[str]]:
    blocked = [index for index, name in enumerate(columns) if is_phi_column(name)]
    if not blocked:
        return columns, rows, []
    cleaned = []
    for row in rows:
        mutable = list(row)
        for index in blocked:
            mutable[index] = None
        cleaned.append(tuple(mutable))
    warnings = [f"column {columns[index]} was redacted (identifier)" for index in blocked]
    return columns, cleaned, warnings
