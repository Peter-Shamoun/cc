"""The shipped analyzer configuration (bundle B1) as one question's commands, run in-process.

B1: one Haiku agent at effort low, free SQL over ``sem.*`` through the guarded tool layer, briefed with the playbook,
the invariants glossary, the generated ``sem.*`` schema (columns, meanings, metric derivations, allowed values) and 3
BM25 TRAIN examples (``analyzer.briefing``), with value lookup, multi-question ``ask``, and ``escalate`` to
``lossless.*`` only with a stated reason (then ``list`` / ``describe`` for those relations). No repair loop, no lint, no
candidates: a failing ``answer`` is not recorded and the model sees the error.

``B1Run`` holds one question's state: the tool layer over the request's opener (its session, escalation and query-time
budget live there), the command budget and the final record. Each command returns what the model is shown or raises
``ToolError`` / ``GuardError`` with the message it is shown. A failed command does not use the command budget.
"""
from __future__ import annotations

from dataclasses import dataclass

from bench.guard import GuardError

from analyzer.answer import AnalyzerAnswer
from analyzer.config import HAIKU_MODEL, AnalyzerConfig
from analyzer.tools import ToolError, ToolLayer, clarification_request

#: B1's budgets (research/analyzer_bundles_v1.md section 1.1): 12 commands, 1000-row results, 20 rows shown, 25 s per
#: query, 3 examples. Effort low means no extended thinking.
B1_CONFIG = AnalyzerConfig(model=HAIKU_MODEL)

#: commands that end the run: exactly one of them is recorded
FINAL_COMMANDS = ("answer", "refuse", "ask")
#: commands that use the command budget when they succeed (B1_CONFIG.max_tool_calls of them). The sem.* schema,
#: its allowed values and the glossary are in the system prompt; list and describe are for lossless.* after escalate.
CHARGED_COMMANDS = ("sql", "lookup", "escalate", "list", "describe")


class AnswerAlreadyRecorded(ToolError):
    pass


@dataclass
class FinalRecord:
    """What the run ended with: ``answer`` (sql + text), ``refusal`` (text) or ``clarification`` (text + questions)."""
    kind: str
    text: str
    sql: str | None = None
    questions: list | None = None
    result_warnings: list | None = None


class B1Run:
    def __init__(self, opener, config: AnalyzerConfig = B1_CONFIG):
        self.config = config
        self.tools = ToolLayer(opener, config)
        self.final: FinalRecord | None = None

    # -- the model's commands
    def run_command(self, command: str, arguments: dict) -> dict:
        """One command by name. A charged command that fails gives its budget unit back, as on the CLI route."""
        handler = getattr(self, f"command_{command}", None)
        if handler is None or command not in CHARGED_COMMANDS + FINAL_COMMANDS:
            raise ToolError(f"unknown command {command}")
        calls_used_before = self.tools._tool_calls_used
        try:
            return handler(**arguments)
        except (ToolError, GuardError):
            self.tools._tool_calls_used = calls_used_before
            raise

    def command_list(self) -> dict:
        self._require_escalated("list")
        return {"relations": lossless_relations(self.tools.list_relations())}

    def command_describe(self, relation: str) -> dict:
        self._require_escalated("describe")
        if not str(relation).strip().startswith("lossless."):
            raise ToolError("describe is for lossless.* relations; the sem.* views and their columns are in your "
                            "instructions")
        return self.tools.describe_relation(relation)

    def command_sql(self, sql: str) -> dict:
        return self.tools.run_sql(sql)

    def command_lookup(self, dimension: str, text: str) -> dict:
        return self.tools.lookup_values(dimension, text)

    def command_escalate(self, reason: str) -> dict:
        """The opener (the chat API's ``ReasonedEscalationOpener``) re-opens the same customer with ``lossless.*``
        and audits the reason; the tool layer records it too."""
        result = self.tools.request_escalation(reason)
        return {**result, "relations": lossless_relations(result["relations"])}

    def command_answer(self, sql: str, text: str = "") -> dict:
        self._require_no_final()
        if not (sql or "").strip():
            raise ToolError("answer needs the SQL that computes the answer")
        try:
            digest = self.tools.run_sql(sql, charge=False)
        except (ToolError, GuardError) as exc:
            raise ToolError(f"final SQL failed: {exc}") from None
        warnings = list(digest.get("warnings") or [])
        self.final = FinalRecord("answer", text or "", sql=sql, result_warnings=warnings)
        return {"recorded": True, "row_count": digest["row_count"], "warnings": warnings}

    def command_refuse(self, reason: str) -> dict:
        self._require_no_final()
        self.final = FinalRecord("refusal", reason)
        self.tools._audit("refusal", reason=reason)
        return {"recorded": True}

    def command_ask(self, question: str | None = None, questions: list | None = None) -> dict:
        self._require_no_final()
        request = clarification_request({"question": question, "questions": questions})
        self.final = FinalRecord("clarification", request["text"], questions=request["questions"])
        return {"recorded": True, "questions": len(request["questions"])}

    def _require_escalated(self, command: str) -> None:
        if not self.tools.escalated:
            raise ToolError(f"{command} is for lossless.* relations after escalate; the sem.* views and their "
                            "columns are in your instructions")

    def _require_no_final(self) -> None:
        if self.final is not None:
            raise AnswerAlreadyRecorded("an answer was already submitted")

    # -- after the agent stopped
    def final_answer(self, model_label: str) -> AnalyzerAnswer:
        """The recorded final as the API's answer. An answer's SQL is executed again here: the rows the user sees
        come from that run, never from anything the model wrote."""
        final = self.final
        if final is None:
            raise AnalyzerRunFailed("the agent ended without an answer, refusal or question")
        if final.kind == "refusal":
            return AnalyzerAnswer(kind="refusal", text=final.text, refused=True, model=model_label)
        if final.kind == "clarification":
            return AnalyzerAnswer(kind="clarification", text=final.text, questions=list(final.questions or []),
                                  model=model_label)
        digest = self.tools.run_sql(final.sql, charge=False, include_all_rows=True)
        return AnalyzerAnswer(kind="answer", text=final.text, sql=final.sql, rows=digest["all_rows"],
                              columns=digest["columns"], warnings=list(final.result_warnings or []),
                              lane="lossless" if "lossless." in final.sql.lower() else "sem", model=model_label,
                              audit=list(self.tools.audit))


def lossless_relations(relations: list[str]) -> list[str]:
    return [name for name in relations if name.startswith("lossless.")]


class AnalyzerRunFailed(RuntimeError):
    """The run ended without a usable final. The message is for the server log: no question or data."""
