"""Agents under test. Interface: `answer(question, tools) -> Answer`.

`tools` is what a real agent gets: describe() (schema + glossary), sql(query) (guarded, row-capped, timed),
and the business glossary. Reference agents (`gold`, `wrong`) exercise the harness; `claude-sql` is the
baseline LLM agent (tool loop: look at results, retry on errors, then answer or refuse).
New designs plug in as `--agent package.module:ClassName`.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from .guard import GuardError, guard

GLOSSARY = """Business definitions (ClaimClarity semantic layer):
- claim: one patient control number (claim_key). sem.claims has one row per 837 submission; is_latest_submission
  marks the latest one for each claim_key across all payers.
- adjudication event: one 835 claim payment (CLP) in sem.remit_claims. A claim can have several per payer
  (original, reversal, correction). The current adjudication for a claim+payer is the latest non-reversal event:
  is_current_adjudication. A claim+payer whose latest event is a reversal has no current adjudication.
- denied (is_denied): recorded denial (claim status 4, is_denied_recorded) OR signal v3 (is_denied_signal_v3:
  $0 paid, not a reversal, and a CO adjustment with a reviewed denial CARC). $0 paid alone is not a denial.
- denial rate: denied current adjudications / current adjudications, counting each claim once per payer.
- primary payer: the payer billed with payer_responsibility = 'P' on sem.claims. payer_sequence on sem.remit_claims
  comes from the claim status code and is 'unknown' for denials and reversals; do not use it to find primary payers.
- 835 lines (sem.remit_lines) are missing when a payer denies a whole claim at claim level; billed lines are in
  sem.claim_lines.
- Never return patient names, member ids, birth dates or addresses (they are not in these views) and never
  answer about another customer: the session can only see the logged-in customer's data. Data is read-only.
"""


@dataclass
class Answer:
    sql: str | None = None
    rows: list | None = None
    text: str = ""
    refused: bool = False
    usage: dict = field(default_factory=dict)
    trace: list = field(default_factory=list)


class Tools:
    def __init__(self, con, escalate: bool = False, row_limit: int = 1000):
        self.con, self.escalate, self.row_limit = con, escalate, row_limit
        self.calls = []

    def describe(self) -> str:
        lines = []
        for (schema, view) in self.con.execute("SELECT schema_name, view_name FROM duckdb_views() WHERE NOT internal "
                                                "ORDER BY 1, 2").fetchall():
            cols = self.con.execute(f"DESCRIBE {schema}.{view}").fetchall()
            lines.append(f"{schema}.{view}(" + ", ".join(f"{c[0]} {c[1]}" for c in cols) + ")")
        return "\n".join(lines) + "\n\n" + GLOSSARY

    def sql(self, query: str):
        t = time.perf_counter()
        try:
            safe = guard(query, self.row_limit, self.escalate)
            cur = self.con.execute(safe)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            self.calls.append(dict(sql=query, ok=True, rows=len(rows), s=round(time.perf_counter() - t, 4)))
            return cols, rows
        except (GuardError, Exception) as e:                      # noqa: BLE001 - errors go back to the agent
            self.calls.append(dict(sql=query, ok=False, error=str(e)[:300], s=round(time.perf_counter() - t, 4)))
            raise


class GoldAgent:
    """Oracle: answers with the gold SQL (harness self-check, should score 100%)."""

    def __init__(self, cases):
        self.by_q = {c["question"]: c for c in cases}

    def answer(self, question, tools) -> Answer:
        c = self.by_q[question]
        if c.get("expect_refusal"):
            return Answer(refused=True, text="I can't help with that.")
        cols, rows = tools.sql(c["gold_sql"])
        return Answer(sql=c["gold_sql"], rows=rows)


class WrongAgent:
    """Answers with each question's first known-wrong SQL (should score ~0% on those questions)."""

    def __init__(self, cases):
        self.by_q = {c["question"]: c for c in cases}

    def answer(self, question, tools) -> Answer:
        c = self.by_q[question]
        if not c.get("wrong_sql"):
            return Answer(text="no wrong variant", refused=not c.get("expect_refusal"))
        sql = c["wrong_sql"][0]["sql"]
        return Answer(sql=sql, rows=tools.sql(sql)[1])


class ClaudeSQLAgent:
    """Baseline: one model, three tools (run_sql, final_answer, refuse), up to 8 turns."""

    TOOLS = [
        {"name": "run_sql", "description": "Run one read-only DuckDB SELECT over the views. Returns up to 50 rows.",
         "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}},
        {"name": "final_answer", "description": "Give the final answer: the SQL whose result answers the question, "
         "plus a one-paragraph answer for the user.",
         "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}, "answer": {"type": "string"}},
                          "required": ["sql", "answer"]}},
        {"name": "refuse", "description": "Decline when the request is not allowed (PHI, other customers, writes).",
         "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}},
    ]

    def __init__(self, model: str | None = None, max_turns: int = 8):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("CC_AGENT_MODEL", "claude-sonnet-5-5")
        self.max_turns = max_turns

    def answer(self, question, tools) -> Answer:
        system = ("You are ClaimClarity, an analyst for a clinical lab's claims (837P) and remittances (835). "
                  "Answer by querying the views below with DuckDB SQL. Check your result before answering.\n\n"
                  + tools.describe())
        msgs = [{"role": "user", "content": question}]
        usage = {"input_tokens": 0, "output_tokens": 0}
        trace = []
        for _ in range(self.max_turns):
            r = self.client.messages.create(model=self.model, max_tokens=2000, system=system, tools=self.TOOLS,
                                            messages=msgs)
            usage["input_tokens"] += r.usage.input_tokens
            usage["output_tokens"] += r.usage.output_tokens
            msgs.append({"role": "assistant", "content": r.content})
            results = []
            for b in r.content:
                if b.type != "tool_use":
                    continue
                if b.name == "refuse":
                    return Answer(refused=True, text=b.input.get("reason", ""), usage=usage, trace=trace)
                if b.name == "final_answer":
                    try:
                        _, rows = tools.sql(b.input["sql"])
                    except Exception as e:                        # noqa: BLE001
                        return Answer(sql=b.input["sql"], text=f"final SQL failed: {e}", usage=usage, trace=trace)
                    return Answer(sql=b.input["sql"], rows=rows, text=b.input.get("answer", ""), usage=usage, trace=trace)
                try:
                    cols, rows = tools.sql(b.input["sql"])
                    content = json.dumps({"columns": cols, "rows": rows[:50], "row_count": len(rows)}, default=str)
                except Exception as e:                            # noqa: BLE001
                    content = f"ERROR: {str(e)[:500]}"
                trace.append(b.input["sql"])
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": content})
            if not results:
                text = "".join(b.text for b in r.content if b.type == "text")
                return Answer(text=text, refused="can't" in text.lower() or "cannot" in text.lower(), usage=usage,
                              trace=trace)
            msgs.append({"role": "user", "content": results})
        return Answer(text="ran out of turns", usage=usage, trace=trace)


def load(name: str, cases):
    if name == "gold":
        return GoldAgent(cases)
    if name == "wrong":
        return WrongAgent(cases)
    if name == "claude-sql":
        return ClaudeSQLAgent()
    import importlib
    mod, _, cls = name.partition(":")
    return getattr(importlib.import_module(mod), cls)()
