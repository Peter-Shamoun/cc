"""Tool layer over a per-request session.

Model-written SQL is executed only by ``run_sql`` and ``sample_rows``, and only
after ``bench.guard.guard``. There is no raw-execute tool. ``escalate`` (``request_escalation``)
is the only way to widen the session: it records an audit event, asks the opener
for a new escalated session (``ccsim.query.open_session(..., escalate=True)`` in
production), and then the guard is allowed to name ``lossless.*``. Identifier
columns are redacted on the way out even if a view ever grew one.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from bench.guard import GuardError, check_plan_cost, explain_statement, guard

from analyzer.config import AnalyzerConfig
from analyzer.opener import SessionOpener

SCHEMA_DIRECTORY = Path(__file__).resolve().parent.parent / "claude" / "schema"
SEMANTIC_MODEL_PATH = SCHEMA_DIRECTORY / "03_semantic_model.yaml"

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
# Model SQL time per session (per question), on top of the per-query time limit. The tool-call budget does not
# bound it: answer and probe are uncharged, and the CLI does not charge failed (timed-out) queries. Past B1 sessions
# spent at most 0.44 s in total.
MAX_TOTAL_QUERY_SECONDS_PER_SESSION = 60.0
_HIGH_CARDINALITY_COLUMNS = frozenset({
    "claim_key", "claim_id", "remit_claim_id", "payer_claim_control_number", "check_or_eft_number",
    "billing_npi", "rendering_npi", "referring_npi",
})


# ``ask`` (contract 1.23.0 clarify, api/CONTRACT.md §6.1). One plain ``question`` stays valid; ``questions`` holds 1-4
# form questions, each with a chip ``header`` and 2-4 ``options``, and ``question`` is then the form's lead-in. The
# limits mirror api/clarify.py so the form the user sees is exactly what the model asked.
ASK_MAX_QUESTIONS = 4
ASK_MIN_OPTIONS, ASK_MAX_OPTIONS = 2, 4
ASK_HEADER_MAX, ASK_QUESTION_MAX, ASK_LABEL_MAX, ASK_DESCRIPTION_MAX = 12, 500, 60, 200
ASK_DEFAULT_LEAD_IN = "A few quick questions first:"
ASK_DESCRIPTION = ("Ask the user before answering, when the request cannot be answered as stated. Either one plain "
                   "question, or up to 4 questions at once, each with a short header and 2-4 options.")
ASK_INPUT_SCHEMA = {"type": "object", "properties": {
    "question": {"type": "string", "maxLength": ASK_QUESTION_MAX,
                 "description": "One clarifying question; with questions, an optional lead-in sentence."},
    "questions": {"type": "array", "minItems": 1, "maxItems": ASK_MAX_QUESTIONS, "items": {
        "type": "object", "properties": {
            "header": {"type": "string", "maxLength": ASK_HEADER_MAX, "description": "Chip label, e.g. Time basis"},
            "question": {"type": "string", "maxLength": ASK_QUESTION_MAX},
            "options": {"type": "array", "minItems": ASK_MIN_OPTIONS, "maxItems": ASK_MAX_OPTIONS, "items": {
                "type": "object", "properties": {
                    "label": {"type": "string", "maxLength": ASK_LABEL_MAX},
                    "description": {"type": "string", "maxLength": ASK_DESCRIPTION_MAX}},
                "required": ["label", "description"]}},
            "multiSelect": {"type": "boolean"}},
        "required": ["header", "question"]}}}}


def _one_line(value) -> str:
    return " ".join(str(value if value is not None else "").split())


def clarification_request(arguments: dict) -> dict:
    """``ask`` arguments -> ``{"text", "questions"}`` or ToolError (the model repairs and asks again).

    A plain question gives ``questions == []`` and ``text`` = the question. With ``questions``, every question needs a
    header, a question and 2-4 options with a label and a description; options may be left out only when a single
    question is asked, which is then a plain question. Ids are q1.. and o1.. (or distinct simple ids the caller
    gave: the scripted demo names its choices) so the result is contract-ready.
    ``text`` is the lead-in: the given ``question``, else the only question's text, else ASK_DEFAULT_LEAD_IN."""
    lead_in = _one_line(arguments.get("question"))
    raw_questions = arguments.get("questions")
    if raw_questions in (None, "", []):
        if not lead_in:
            raise ToolError("ask needs a question, or questions")
        if len(lead_in) > ASK_QUESTION_MAX:
            raise ToolError(f"the question is longer than {ASK_QUESTION_MAX} characters")
        return {"text": lead_in, "questions": []}
    if not isinstance(raw_questions, list) or not all(isinstance(item, dict) for item in raw_questions):
        raise ToolError("questions must be a list of objects with header, question and options")
    if len(raw_questions) > ASK_MAX_QUESTIONS:
        raise ToolError(f"ask at most {ASK_MAX_QUESTIONS} questions at once")
    if len(raw_questions) == 1 and not raw_questions[0].get("options"):
        text = _one_line(raw_questions[0].get("question"))
        return clarification_request({"question": " ".join(part for part in (lead_in, text) if part)})
    questions = []
    question_ids = _given_ids(raw_questions, "q")
    for question_number, raw in enumerate(raw_questions, start=1):
        header, text = _one_line(raw.get("header")), _one_line(raw.get("question"))
        where = f"question {question_number}"
        if not header or len(header) > ASK_HEADER_MAX:
            raise ToolError(f"{where}: header must be 1-{ASK_HEADER_MAX} characters (a chip label such as 'Time basis')")
        if not text or len(text) > ASK_QUESTION_MAX:
            raise ToolError(f"{where}: question must be 1-{ASK_QUESTION_MAX} characters")
        raw_options = raw.get("options") or []
        if not isinstance(raw_options, list) or not ASK_MIN_OPTIONS <= len(raw_options) <= ASK_MAX_OPTIONS:
            raise ToolError(f"{where}: give {ASK_MIN_OPTIONS}-{ASK_MAX_OPTIONS} options (the user can also type "
                            "their own answer or skip)")
        options = []
        option_ids = _given_ids(raw_options, "o")
        for option_number, option in enumerate(raw_options, start=1):
            label = _one_line(option.get("label")) if isinstance(option, dict) else ""
            description = _one_line(option.get("description")) if isinstance(option, dict) else ""
            if not label or len(label) > ASK_LABEL_MAX or not description or len(description) > ASK_DESCRIPTION_MAX:
                raise ToolError(f"{where}, option {option_number}: needs a label (at most {ASK_LABEL_MAX} characters) "
                                f"and a description (at most {ASK_DESCRIPTION_MAX})")
            options.append({"id": option_ids[option_number - 1], "label": label, "description": description})
        if len({option["label"].lower() for option in options}) != len(options):
            raise ToolError(f"{where}: option labels must differ")
        questions.append({"id": question_ids[question_number - 1], "header": header, "question": text, "options": options,
                          **({"multiSelect": True} if raw.get("multiSelect") is True else {})})
    if len(lead_in) > ASK_QUESTION_MAX:
        raise ToolError(f"the lead-in is longer than {ASK_QUESTION_MAX} characters")
    text = lead_in or (questions[0]["question"] if len(questions) == 1 else ASK_DEFAULT_LEAD_IN)
    return {"text": text, "questions": questions}


_GIVEN_ID = re.compile(r"[a-z][a-z0-9_]{0,31}")


def _given_ids(items: list, prefix: str) -> list[str]:
    """Contract ids for a list of questions or options: the ids the caller gave when every item has a distinct,
    simple one (the scripted demo names its choices), else ``<prefix>1``, ``<prefix>2``, ..."""
    given = [item.get("id") if isinstance(item, dict) else None for item in items]
    if all(isinstance(value, str) and _GIVEN_ID.fullmatch(value) for value in given) and len(set(given)) == len(given):
        return given
    return [f"{prefix}{number}" for number in range(1, len(items) + 1)]


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


class ToolLayer:
    def __init__(self, opener: SessionOpener, config: AnalyzerConfig | None = None, *,
                 audit_sink: Callable[[dict], None] | None = None):
        self.opener = opener
        self.config = config or AnalyzerConfig()
        self.audit_sink = audit_sink
        self.audit: list[dict] = []
        self.calls: list[dict] = []
        self.escalated = False
        self._semantic = None
        self._model = None
        self._tool_calls_used = 0
        self.query_seconds_used = 0.0
        self.session = opener.open(False)
        self._audit("session_open")

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

    def run_sql(self, sql: str, *, charge: bool = True, include_all_rows: bool = False) -> dict:
        if charge:
            self._spend("run_sql")
        return self._execute_guarded(sql, audit_event="sql", include_all_rows=include_all_rows)

    def request_escalation(self, reason: str, *, charge: bool = True) -> dict:
        reason = (reason or "").strip()
        if not reason:
            raise ToolError("escalate requires a reason")
        if charge:
            self._spend("request_escalation")
        self._audit("escalation_requested", reason=reason)
        if self.escalated:
            self._audit("escalation_already_active", reason=reason)
            return {"escalated": True, "already": True, "relations": self.list_relations(charge=False)}
        # an opener that audits the grant itself (the chat API's ReasonedEscalationOpener) takes the reason with it
        open_escalated = getattr(self.opener, "open_escalated", None)
        try:
            opened = open_escalated(reason) if open_escalated is not None else self.opener.open(True)
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

    def lookup_values(self, dimension: str, search: str, *, charge: bool = True) -> dict:
        if charge:
            self._spend("lookup_values")
        api = self._semantic_api()
        try:
            return api.lookup_values(dimension, search)
        except Exception as exc:
            raise ToolError(str(exc)[:600]) from None

    def _execute_guarded(self, sql: str, *, audit_event: str, relation: str | None = None,
                         include_all_rows: bool = False) -> dict:
        remaining_seconds = MAX_TOTAL_QUERY_SECONDS_PER_SESSION - self.query_seconds_used
        if remaining_seconds <= 0:
            message = (f"the {MAX_TOTAL_QUERY_SECONDS_PER_SESSION:g} s of query time for this question are used up "
                       f"({self.query_seconds_used:.1f} s spent), so no more SQL will run. Refuse, saying the "
                       "question needs a narrower query.")
            self._audit(audit_event, ok=False, sql=sql[:500], error=message, relation=relation,
                        escalated=self.escalated, seconds=0.0)
            raise ToolError(message)
        time_limit_seconds = min(self.config.time_limit_seconds, remaining_seconds)
        started = time.perf_counter()
        try:
            safe = guard(sql, self.config.row_limit, self.escalated)
            check_plan_cost(execute_with_time_limit(self.session.con, explain_statement(safe),
                                                    time_limit_seconds).fetchall()[0][1])
            cursor = execute_with_time_limit(self.session.con, safe, time_limit_seconds)
            columns = [item[0] for item in cursor.description]
            fetched = cursor.fetchall()
        except (GuardError, QueryTimeout, Exception) as exc:  # noqa: BLE001 - the model sees the message
            elapsed = self._charge_query_seconds(started)
            message = str(exc).splitlines()[0][:500] if str(exc) else type(exc).__name__
            self._audit(audit_event, ok=False, sql=sql[:500], error=message, relation=relation,
                        escalated=self.escalated, seconds=elapsed)
            raise ToolError(message) from None
        elapsed = self._charge_query_seconds(started)
        columns, fetched, redacted = _redact(columns, fetched)
        full_rows = [jsonable(row) for row in fetched]
        shown = full_rows[:self.config.rows_shown_to_model]
        self._audit(audit_event, ok=True, sql=sql[:500], row_count=len(fetched), relation=relation,
                    escalated=self.escalated, seconds=elapsed)
        digest = {"columns": columns, "rows": shown, "row_count": len(fetched),
                  "truncated": len(fetched) > self.config.rows_shown_to_model,
                  "row_limit": self.config.row_limit, "time_seconds": elapsed}
        if include_all_rows:
            digest["all_rows"] = full_rows
        if redacted:
            digest["warnings"] = redacted
        return digest

    def _charge_query_seconds(self, started: float) -> float:
        elapsed = round(time.perf_counter() - started, 4)
        self.query_seconds_used += elapsed
        return elapsed

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
                "Call escalate with a reason to read lossless.*")
        if name not in self.list_relations(charge=False):
            available = ", ".join(self.list_relations(charge=False)) or "(none)"
            raise ToolError(f"relation {name} is not in this session. Available: {available}")
        return name

    def _semantic_model(self) -> dict:
        if self._model is None:
            import yaml
            self._model = yaml.safe_load(SEMANTIC_MODEL_PATH.read_text())
        return self._model

    def _semantic_api(self):
        """The semantic layer's value lookup (``claude/schema/04_agent_interface.py``) on this session."""
        if self._semantic is None:
            self._semantic = _agent_interface_module().SemanticAPI(self.session.con, str(SEMANTIC_MODEL_PATH))
        return self._semantic

    def _spend(self, name: str):
        if self._tool_calls_used >= self.config.max_tool_calls:
            raise ToolError(f"tool-call budget of {self.config.max_tool_calls} is used up: "
                            "submit answer, refuse, or ask")
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


def _metric_derivations(model: dict, relation: str) -> list[dict]:
    """#3 "+ derivations": how each semantic-model metric is computed on this relation."""
    derivations = []
    for name, metric in (model.get("metrics") or {}).items():
        expression = (metric.get("views") or {}).get(relation)
        if expression:
            derivations.append({"metric": name, "expression": expression, "unit": metric.get("unit", ""),
                                "description": metric.get("description", "")})
        elif metric.get("kind") == "ratio":
            derivations.append({"metric": name, "expression": f"{metric['numerator']} / {metric['denominator']}",
                                "unit": metric.get("unit", ""), "description": metric.get("description", "")})
    return derivations


def _agent_interface_module():
    module = sys.modules.get("agent_interface")
    if module is None:
        spec = importlib.util.spec_from_file_location("agent_interface", SCHEMA_DIRECTORY / "04_agent_interface.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["agent_interface"] = module
        spec.loader.exec_module(module)
    return module


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
