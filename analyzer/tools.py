"""Tool layer over a per-request session.

Model-written SQL is executed only by ``run_sql`` and ``sample_rows``, and only
after ``bench.guard.guard``. There is no raw-execute tool. ``request_escalation``
is the only way to widen the session: it records an audit event, asks the opener
for a new escalated session (``ccsim.query.open_session(..., escalate=True)`` in
production), and then the guard is allowed to name ``lossless.*``. Identifier
columns are redacted on the way out even if a view ever grew one.
"""

from __future__ import annotations

import difflib
import re
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

from bench.agents import GLOSSARY
from bench.guard import GuardError, check_plan_cost, explain_statement, guard

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
# Model SQL time per session (per question), on top of the per-query time limit. The tool-call budget does not
# bound it: answer and probe are uncharged, and the CLI does not charge failed (timed-out) queries. Past B1 sessions
# spent at most 0.44 s in total.
MAX_TOTAL_QUERY_SECONDS_PER_SESSION = 60.0
_HIGH_CARDINALITY_COLUMNS = frozenset({
    "claim_key", "claim_id", "remit_claim_id", "payer_claim_control_number", "check_or_eft_number",
    "billing_npi", "rendering_npi", "referring_npi",
})


# #19 "descriptions" level: what each tool is for and when to use it, instead of what it returns.
REWRITTEN_TOOL_DESCRIPTIONS = {
    "list_relations": "Start here only if you do not know which sem.* view holds a fact. Lists every readable view.",
    "describe_relation": "Before you write SQL on a view: its exact column names and types. Use these names verbatim.",
    "sample_rows": "See what stored values look like (codes, spellings, flags) before you filter on them.",
    "run_sql": "Check a draft or an intermediate count. Read the row_count and any warning or lint before you "
               "trust a number. The SQL you finally submit is re-run and scored.",
    "request_escalation": "Only when the answer needs a lossless.* relation (raw EDI files, segments). Audited.",
    "profile": "Which values a low-cardinality column holds and each numeric column's range, without writing SQL.",
    "lookup_dictionary": "Grain (one row per what) and the meaning of each column. Read it before choosing a view.",
    "lookup_glossary": "The business definition of a term (denial, current adjudication, primary payer).",
    "query_metrics": "A typed metric query the compiler turns into SQL; use it for a metric it names.",
    "lookup_values": "Turn a name in the question (payer, CPT) into the exact stored value before filtering.",
    "lookup_examples": "Solved questions from the training split with their SQL, for the shape of a query.",
    "final_answer": "Submit the SQL that computes the answer, once. Every number in the paragraph must come from it.",
    "refuse": "Decline patient identifiers, another customer's data, or any change to the data.",
    "ask": "Only when two readings of the question give different numbers, or a required scope is missing: ask "
           "every clarifying question at once (up to 4), each with 2-4 options.",
}

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
    question is asked, which is then a plain question. Ids are q1.. and o1.. so the result is contract-ready.
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
        for option_number, option in enumerate(raw_options, start=1):
            label = _one_line(option.get("label")) if isinstance(option, dict) else ""
            description = _one_line(option.get("description")) if isinstance(option, dict) else ""
            if not label or len(label) > ASK_LABEL_MAX or not description or len(description) > ASK_DESCRIPTION_MAX:
                raise ToolError(f"{where}, option {option_number}: needs a label (at most {ASK_LABEL_MAX} characters) "
                                f"and a description (at most {ASK_DESCRIPTION_MAX})")
            options.append({"id": f"o{option_number}", "label": label, "description": description})
        if len({option["label"].lower() for option in options}) != len(options):
            raise ToolError(f"{where}: option labels must differ")
        questions.append({"id": f"q{question_number}", "header": header, "question": text, "options": options,
                          **({"multiSelect": True} if raw.get("multiSelect") is True else {})})
    if len(lead_in) > ASK_QUESTION_MAX:
        raise ToolError(f"the lead-in is longer than {ASK_QUESTION_MAX} characters")
    text = lead_in or (questions[0]["question"] if len(questions) == 1 else ASK_DEFAULT_LEAD_IN)
    return {"text": text, "questions": questions}


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
        spec.append({"name": "ask", "description": ASK_DESCRIPTION, "input_schema": ASK_INPUT_SCHEMA})
    if not config.enable_sql:
        spec = [tool for tool in spec if tool["name"] != "run_sql"]
    if config.rewritten_tool_descriptions:
        spec = [{**tool, "description": REWRITTEN_TOOL_DESCRIPTIONS[tool["name"]]} for tool in spec]
    return spec


class ToolLayer:
    def __init__(self, opener: SessionOpener, config: AnalyzerConfig | None = None, *,
                 question_id: str | None = None, question_family: str | None = None,
                 example_library: list | None = None, audit_sink: Callable[[dict], None] | None = None,
                 restored_escalation: bool = False, restored_query_seconds: float = 0.0):
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
        self.query_seconds_used = restored_query_seconds
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
            entry = {"relation": name, "grain": meta.get("grain", ""), "use_for": meta.get("use_for", ""),
                     "default_filter": meta.get("default_filter"), "columns": columns}
            if self.config.dictionary_detail == "derivations":
                entry["derivations"] = _metric_derivations(model, name)
            entries.append(entry)
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
            message = sql_error_message(exc, self.config.sql_error_hints and self.config.self_correct != "none")
            if self.config.actionable_errors:
                message += self._actionable_error_suffix(message, sql)
            self._audit(audit_event, ok=False, sql=sql[:500], error=message, relation=relation,
                        escalated=self.escalated, seconds=elapsed)
            raise ToolError(message) from None
        elapsed = self._charge_query_seconds(started)
        columns, fetched, redacted = _redact(columns, fetched)
        warnings = result_warnings(sql, columns, fetched, self.config.result_checks)
        warnings.extend(redacted)
        full_rows = [jsonable(row) for row in fetched]
        shown = full_rows[:self.config.rows_shown_to_model]
        self._audit(audit_event, ok=True, sql=sql[:500], row_count=len(fetched), relation=relation,
                    escalated=self.escalated, seconds=elapsed)
        digest = {"columns": columns, "rows": shown, "row_count": len(fetched),
                  "truncated": len(fetched) > self.config.rows_shown_to_model,
                  "row_limit": self.config.row_limit, "time_seconds": elapsed}
        if self.config.digest_results:
            digest["rows"] = full_rows[:min(DIGEST_ROWS_SHOWN, self.config.rows_shown_to_model)]
            digest["truncated"] = len(fetched) > len(digest["rows"])
            digest["column_summary"] = _column_summary(columns, full_rows)
        if include_all_rows:
            digest["all_rows"] = full_rows
        if self.config.result_checks:
            digest["warnings"] = warnings
        elif redacted:
            digest["warnings"] = redacted
        return digest

    def _charge_query_seconds(self, started: float) -> float:
        elapsed = round(time.perf_counter() - started, 4)
        self.query_seconds_used += elapsed
        return elapsed

    def _actionable_error_suffix(self, message: str, sql: str) -> str:
        """#19: name what to use instead. Reads only the session's own catalog."""
        relations = self.list_relations(charge=False)
        named = [name for name in dict.fromkeys(re.findall(r"\b(?:sem|lossless)\.[A-Za-z_][A-Za-z0-9_]*", sql))
                 if name in relations]
        if re.search(r"Referenced column|not found in FROM clause|does not have a column|column .* not found",
                     message, re.IGNORECASE):
            missing = re.search(r'column "?([A-Za-z_][A-Za-z0-9_]*)"? not found|Referenced column "([^"]+)"', message)
            missing_name = next((group for group in (missing.groups() if missing else ()) if group), "")
            parts = []
            for name in named[:3]:
                columns = [row[0] for row in self.session.con.execute(f"DESCRIBE {name}").fetchall()]
                close = difflib.get_close_matches(missing_name, columns, n=3, cutoff=0.5) if missing_name else []
                parts.append(f"{name} columns: {', '.join(columns)}"
                             + (f" (closest to {missing_name}: {', '.join(close)})" if close else ""))
            return " Next step: use a column that exists. " + ("; ".join(parts) if parts else
                                                               f"Describe one of: {', '.join(relations)}")
        if re.search(r"Table with name|does not exist|Catalog Error|not allowed|not available", message, re.IGNORECASE):
            return f" Next step: read only these relations: {', '.join(relations)}."
        if "GROUP BY" in message:
            return (" Next step: put every selected column that is not inside an aggregate into GROUP BY, or wrap it "
                    "in any_value().")
        if re.search(r"Conversion Error|Could not convert|No function matches|Cannot compare", message, re.IGNORECASE):
            return (" Next step: CAST one side so both have the same type; compare dates with DATE 'YYYY-MM-DD' and "
                    "booleans with true/false.")
        if re.search(r"Parser Error|syntax error", message, re.IGNORECASE):
            return " Next step: send one SELECT (a WITH ... SELECT is fine) with balanced parentheses and quotes."
        return " Next step: fix the query and run it again."

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
                elif self.config.profile_detail == "enums":
                    continue
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


DIGEST_ROWS_SHOWN = 5


def _column_summary(columns: list[str], rows: list) -> dict:
    """#19 digest: per column, over every returned row, what a reader checks before trusting a number."""
    summary = {}
    for index, name in enumerate(columns):
        values = [row[index] for row in rows]
        present = [value for value in values if value is not None]
        entry: dict[str, Any] = {"nulls": len(values) - len(present), "distinct": len({str(v) for v in present})}
        numbers = [value for value in present if isinstance(value, (int, float)) and not isinstance(value, bool)]
        if numbers and len(numbers) == len(present):
            entry.update(min=min(numbers), max=max(numbers), sum=round(sum(numbers), 6))
        summary[name] = entry
    return summary


def _glossary_body(config: AnalyzerConfig) -> str:
    if config.glossary == "none":
        return ""
    if config.glossary == "invariants":
        from bench.glossary_versions import render_version
        return render_version("v002_invariants").strip()
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
