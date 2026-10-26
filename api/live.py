"""Live step events from a running analyzer (contract v1 ``step.*``).

``LiveTap`` is the analyzer agent's tool-call observer (``analyzer.strands_agent.ToolCallObserver``): each B1 command
is reported when it starts (-> ``step.sql_running`` / a status) and when it returns (-> ``step.sql_result`` /
``step.explore``), so steps stream *during* the run. A query that follows a failed one is shown as its repair
(``step.repair_attempt``). Model text never becomes an event (reasoning is dropped).

PHI: the tap reads tool payloads only for ``row_count``, column names and ``truncated``. It never
copies row values into an event.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field

from analyzer.strands_agent import RunCancelled

from api.ids import new_id
from api.sql_mask import PHI_VALUE

# USD per million tokens (input, output). Static estimate table; demo mode never bills anything. A prompt-cache read
# costs a tenth of an input token, a cache write a quarter more (Bedrock's 5-minute cache).
PRICES = {"haiku": (1.0, 5.0), "sonnet": (3.0, 15.0), "opus": (15.0, 75.0)}
CACHE_READ_PRICE_FACTOR = 0.1
CACHE_WRITE_PRICE_FACTOR = 1.25
#: B1 command -> the tool name the step events are keyed by
STEP_TOOL_FOR_COMMAND = {
    "list": "list_relations", "describe": "describe_relation", "sample": "sample_rows",
    "dictionary": "lookup_dictionary", "profile": "profile", "glossary": "lookup_glossary", "sql": "run_sql",
    "lookup": "lookup_values", "escalate": "request_escalation", "answer": "final_answer", "refuse": "refuse",
    "ask": "ask"}
_EXPLORE = {"list_relations": "list_views", "describe_relation": "describe_view", "sample_rows": "sample_values",
            "lookup_glossary": "lookup_glossary", "lookup_dictionary": "lookup_dictionary", "profile": "profile",
            "lookup_values": "sample_values", "request_escalation": "request_escalation"}


#: raised when the user stops the run (the analyzer's own exception, so either side can raise it)
Cancelled = RunCancelled


def price(model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0,
          cache_write_tokens: int = 0) -> float:
    m = model.lower()
    pin, pout = next((v for k, v in PRICES.items() if k in m), PRICES["sonnet"])
    return round((input_tokens + cache_read_tokens * CACHE_READ_PRICE_FACTOR
                  + cache_write_tokens * CACHE_WRITE_PRICE_FACTOR) / 1e6 * pin + output_tokens / 1e6 * pout, 6)


def scrub(text: str, limit: int = 300) -> str:
    """Error text without literals: quoted strings, dates, e-mail addresses, IDs mixing letters and digits, and long
    digit runs are masked (adapter spec R2: a database error can echo a value from the data)."""
    text = re.sub(r"'[^']*'", "'…'", str(text or ""))
    text = re.sub(r"\b\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\b", "…", text)
    text = re.sub(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", "…", text)
    text = re.sub(r"\b(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{6,}\b", "…", text)
    text = re.sub(r"\d{4,}", "…", text)
    return text[:limit]


def masked_explore_target(text: str, limit: int = 120) -> str:
    """A model-chosen lookup argument (relation, glossary term, example query) as shown and persisted: quoted strings
    and identifier-like values (SSN, e-mail, phone, long or mixed ids; sql_mask.PHI_VALUE) become "…"."""
    text = re.sub(r"'[^']*'", "'…'", str(text or ""))
    return PHI_VALUE.sub("…", text)[:limit]


def masked_escalation_reason(reason: str, limit: int = 300) -> str:
    """The analyzer's stated reason for reading lossless.*, as shown in the step timeline and audited: on one line,
    with quoted strings, dates, e-mail addresses, mixed or long ids (``scrub``) and SSN/phone-like values
    (sql_mask.PHI_VALUE) masked."""
    return scrub(PHI_VALUE.sub("…", " ".join(str(reason or "").split())), limit).strip()


def _error_code(message: str) -> str:
    m = message.lower()
    for needle, code in (("binder", "binder_error"), ("not found", "binder_error"), ("does not have a column", "binder_error"),
                         ("parser", "syntax_error"), ("syntax", "syntax_error"), ("timeout", "timeout"),
                         ("time limit", "timeout"), ("guard", "guard_blocked"), ("not allowed", "guard_blocked")):
        if needle in m:
            return code
    return "query_failed"


def partition_ms(stages: dict, total_ms: int) -> dict:
    """Whole-ms stages that sum to exactly ``total_ms`` (CONTRACT.md §7), however loaded the machine is.

    The tap's clock starts after the run's (request parsing, history) and is read again after ``totalMs`` was taken,
    so the raw wall-clock stages differ from ``totalMs`` by a few ms, more under load. Time before the tap started is
    planning, so a shortfall goes to ``plan``; an excess is taken from the largest stages. Rounding uses largest
    remainders, so no stage moves by more than the drift plus 1 ms, and none goes negative."""
    raw = {k: max(0.0, float(stages.get(k, 0.0))) for k in STAGES}
    total_ms = max(0, int(total_ms))
    drift = total_ms - sum(raw.values())
    if drift > 0:
        raw["plan"] += drift
    elif drift < 0:
        excess = -drift
        for k in sorted(raw, key=raw.get, reverse=True):
            take = min(raw[k], excess)
            raw[k] -= take
            excess -= take
            if excess <= 0:
                break
    out = {k: int(v) for k, v in raw.items()}
    short = total_ms - sum(out.values())
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[:max(0, short)]:
        out[k] += 1
    return out

@dataclass
class _Pending:
    step_id: str
    name: str
    arguments: dict
    started: float


@dataclass
class TapStats:
    model_ms: float = 0.0
    sql_ms: float = 0.0
    first_step_ms: float | None = None
    queries: int = 0
    models: dict = field(default_factory=dict)  # model -> {inputTokens, outputTokens, cacheRead, cacheWrite, calls}
    #: wall-clock ms per stage (CONTRACT.md §7 ``latency.stages``); each instant belongs to exactly one stage
    stages: dict = field(default_factory=lambda: dict.fromkeys(STAGES, 0.0))


#: per-stage latency buckets, in pipeline order: planning (model turns + schema exploration), running SQL,
#: repairing after a failed query (model + SQL), result checks, and writing the answer.
STAGES = ("plan", "sql", "repair", "checks", "render")


class LiveTap:
    def __init__(self, emit, *, cancelled: threading.Event | None = None, started: float | None = None):
        self.emit = emit
        self.cancelled = cancelled or threading.Event()
        self.started = started or time.perf_counter()
        self.stats = TapStats()
        self.pending: dict[str, _Pending] = {}       # stepId -> step still running
        self.failed: tuple[str, dict] | None = None  # (failed stepId, error) awaiting its repair
        self.final_step: str | None = None           # stepId of the last final_answer SQL
        self.ask_args: dict | None = None
        self.refuse_args: dict | None = None
        self.repairs = 0
        self._lock = threading.Lock()
        self._stage = "plan"
        self._stage_t = self.started

    def enter(self, stage: str) -> None:
        """Close the running stage at now and start ``stage`` (wall clock, so stages sum to the run time)."""
        now = time.perf_counter()
        self.stats.stages[self._stage] += (now - self._stage_t) * 1000
        self._stage, self._stage_t = stage, now

    # -- helpers
    def _ms(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)

    def _step(self, payload: dict):
        if self.stats.first_step_ms is None:
            self.stats.first_step_ms = self._ms()
        self.emit(payload)

    # -- the analyzer's tool-call observer
    def tool_call_started(self, command: str, arguments: dict) -> str | None:
        """A B1 command is about to run: its running step (or status) now. Returns the step id to finish."""
        with self._lock:
            return self._start_call(STEP_TOOL_FOR_COMMAND.get(command, command), dict(arguments))

    def tool_call_finished(self, step_id: str | None, result_text: str) -> None:
        """What the command returned, as the model sees it (``ERROR: ...`` on failure). A recorded final answer stays
        open: ``finish`` closes it with the re-executed rows."""
        with self._lock:
            pending = self.pending.get(step_id) if step_id else None
            if pending is None:
                return
            ok = not result_text.startswith("ERROR:")
            if pending.name in ("ask", "refuse"):
                del self.pending[step_id]
                if ok and pending.name == "ask":
                    # the mapping reads the validated questions from the answer, not the model's raw arguments
                    self.ask_args = {key: value for key, value in pending.arguments.items() if key != "questions"}
                elif ok:
                    self.refuse_args = pending.arguments
                return
            if pending.name == "final_answer" and ok:
                return
            del self.pending[step_id]
            self._finish_call(pending, result_text)

    def record_model_usage(self, model: str, usage: dict) -> None:
        """Token counts (with prompt-cache reads and writes) and model time of the finished run."""
        row = self.stats.models.setdefault(model, {"inputTokens": 0, "outputTokens": 0, "cacheReadTokens": 0,
                                                   "cacheWriteTokens": 0, "calls": 0})
        row["inputTokens"] += int(usage.get("input_tokens") or 0)
        row["outputTokens"] += int(usage.get("output_tokens") or 0)
        row["cacheReadTokens"] += int(usage.get("cache_read_input_tokens") or 0)
        row["cacheWriteTokens"] += int(usage.get("cache_write_input_tokens") or 0)
        row["calls"] += int(usage.get("turns") or 0)
        self.stats.model_ms += float(usage.get("model_ms") or 0)

    # -- turning calls into events
    def _start_call(self, name: str, args: dict) -> str | None:
        step_id = new_id("s_")
        now = time.perf_counter()
        if name in ("ask", "refuse"):
            self.pending[step_id] = _Pending(step_id, name, args, now)
            return step_id
        if name in ("run_sql", "final_answer"):
            sql = str(args.get("sql") or "")
            if not sql:
                return None
            self.stats.queries += 1
            failed, self.failed = self.failed, None
            ev = {"type": "step.sql_running", "stepId": step_id, "sql": sql, "attempt": 1, "purpose": "answer",
                  "label": "Final query" if name == "final_answer" else "Running query"}
            if failed:
                repair_id = new_id("s_")
                fail_step, error = failed
                self.repairs += 1
                self._step({"type": "step.repair_attempt", "stepId": repair_id, "failedStepId": fail_step,
                            "attempt": self.repairs, "maxAttempts": 3,
                            "error": {"code": error["code"], "message": error["message"]},
                            "revisedSql": sql, "explanation": "Revised the query after the database error."})
                ev.update(purpose="repair", repairOf=repair_id, attempt=self.repairs + 1)
            self.enter("repair" if failed else "sql")
            self._step(ev)
            self.emit({"type": "status", "label": "Running query"})
            if name == "final_answer":
                self.final_step = step_id
            self.pending[step_id] = _Pending(step_id, name, args, now)
            return step_id
        self.emit({"type": "status", "label": f"Exploring {args.get('relation') or 'the schema'}"})
        self.pending[step_id] = _Pending(step_id, name, args, now)
        return step_id

    def _finish_call(self, pend: _Pending, content: str, rows: int | None = None, columns: list | None = None):
        elapsed = int((time.perf_counter() - pend.started) * 1000)
        if pend.name in ("run_sql", "final_answer"):
            self.stats.sql_ms += elapsed
            self.enter("plan")  # the model reads the result next; a failure below switches to repair
            if content.startswith("ERROR:"):
                message = content.split(":", 1)[1].strip()
                if message.startswith("final SQL failed:"):
                    message = message.split(":", 1)[1].strip()
                message = scrub(message, 400)
                err = {"code": _error_code(message), "message": message}
                self._step({"type": "step.sql_result", "stepId": pend.step_id, "ok": False, "elapsedMs": elapsed,
                            "error": {"code": err["code"], "message": message}})
                self.failed = (pend.step_id, err)
                self.enter("repair")
                return
            ev = {"type": "step.sql_result", "stepId": pend.step_id, "ok": True, "elapsedMs": elapsed}
            if rows is None:
                try:
                    payload = json.loads(content)
                except (ValueError, TypeError):
                    payload = {}
                rows = payload.get("row_count")
                columns = payload.get("columns")
                if payload.get("truncated"):
                    ev["truncated"] = True
            if rows is not None:
                ev["rowCount"] = int(rows)
            if columns:
                ev["columns"] = [{"name": str(c)} for c in columns]
            self._step(ev)
            return
        probe = _EXPLORE.get(pend.name, pend.name)
        target = pend.arguments.get("relation") or pend.arguments.get("term") or pend.arguments.get("query")
        ok = not content.startswith("ERROR:")
        try:
            payload = json.loads(content) if ok else {}
        except (ValueError, TypeError):
            payload = {}
        if not ok:
            summary = "Failed: " + scrub(content[6:], 200)
        elif pend.name == "request_escalation":
            # data escalation (the web shows it as "Used the full EDI data"); not the model-handoff `escalation` event
            target = None
            summary = "Reason: " + (masked_escalation_reason(pend.arguments.get("reason")) or "not stated")
        elif "columns" in payload and isinstance(payload["columns"], list) and pend.name != "sample_rows":
            summary = f"{len(payload['columns'])} columns"
        elif "relations" in payload and isinstance(payload["relations"], list):
            summary = f"{len(payload['relations'])} views"
        elif "row_count" in payload:
            summary = f"{payload['row_count']} rows sampled"
        else:
            summary = "Done"
        ev = {"type": "step.explore", "stepId": pend.step_id, "probe": probe, "summary": summary, "elapsedMs": elapsed}
        if target:
            ev["target"] = masked_explore_target(target)
        self._step(ev)

    # -- after the analyzer returned
    def finish(self, answer) -> None:
        """Close the final SQL step with the re-executed rows, then the result check."""
        with self._lock:
            for pend in list(self.pending.values()):
                if pend.name == "final_answer":
                    if answer is not None and answer.kind == "answer":
                        self._finish_call(pend, "{}", rows=len(answer.rows or []), columns=list(answer.columns or []))
                    else:
                        self._finish_call(pend, "{}")
            self.pending.clear()
            self.enter("checks")
            if answer is None or answer.kind != "answer":
                return
            warnings = [w for w in (answer.warnings or []) if "redacted" not in str(w)]
            ev = {"type": "step.check", "stepId": new_id("s_"), "check": "verifier",
                  "verdict": "warn" if warnings else "pass",
                  "message": scrub("; ".join(map(str, warnings)), 400) if warnings
                  else f"Result checks passed ({len(answer.rows or [])} rows).",
                  "action": "caveat" if warnings else "none"}
            if self.final_step:
                ev["targetStepId"] = self.final_step
            self._step(ev)

    def abort(self) -> None:
        """The run was cancelled: close every step still in flight so no UI row is left spinning.

        Open SQL steps get ``step.sql_result {ok: false, error: {code: "cancelled"}}``. Explore steps only emit on
        completion, so there is nothing to close (CONTRACT.md §8)."""
        with self._lock:
            for pend in list(self.pending.values()):
                if pend.name in ("run_sql", "final_answer"):
                    self._step({"type": "step.sql_result", "stepId": pend.step_id, "ok": False,
                                "elapsedMs": int((time.perf_counter() - pend.started) * 1000),
                                "error": {"code": "cancelled", "message": "Stopped by the user."}})
            self.pending.clear()

    def usage(self, elapsed_ms: int, first_event_ms: int | None, first_token_ms: int | None) -> dict:
        models = []
        tin = tout = cache_read = cache_write = 0
        cost = 0.0
        for model, row in self.stats.models.items():
            c = price(model, row["inputTokens"], row["outputTokens"], row["cacheReadTokens"], row["cacheWriteTokens"])
            models.append({"model": model, "role": "analyst", "inputTokens": row["inputTokens"],
                           "outputTokens": row["outputTokens"], "costUsd": c, "calls": row["calls"]})
            tin += row["inputTokens"]
            tout += row["outputTokens"]
            cache_read += row["cacheReadTokens"]
            cache_write += row["cacheWriteTokens"]
            cost += c
        self.enter(self._stage)
        latency = {"totalMs": elapsed_ms, "modelMs": int(self.stats.model_ms), "sqlMs": int(self.stats.sql_ms),
                   "stages": partition_ms(self.stats.stages, elapsed_ms)}
        if first_event_ms is not None:
            latency["firstEventMs"] = first_event_ms
        if self.stats.first_step_ms is not None:
            latency["firstStepMs"] = self.stats.first_step_ms
        if first_token_ms is not None:
            latency["firstTokenMs"] = first_token_ms
        out = {"queries": self.stats.queries, "elapsedMs": elapsed_ms, "latency": latency,
               "costIsEstimate": True, "costUsd": round(cost, 6)}
        if models:
            out["tokens"] = {"input": tin, "output": tout, "cacheRead": cache_read, "cacheWrite": cache_write}
            out["models"] = models
        return out
