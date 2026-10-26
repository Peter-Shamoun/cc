"""The offline demo's model: a scripted Strands model (``analyzer.scripted_model``) that plays B1 command lines.

It is the same path as a real run (Strands agent, the ``cc`` tool, the guarded tool layer over the request's opener,
LiveTap steps, the answer re-executed); only the model is a script. With demo auth the data is DemoAuth's synthetic
``sem.claims`` per customer. A scenario (contract §9; ``ChatRequest.scenario`` in demo mode, else question keywords)
picks the script. B1 has one model and one candidate, so ``candidates`` plays a plain answer and ``escalation`` is the
data escalation to ``lossless.*`` (there is no model handoff). Never calls a network.
"""
from __future__ import annotations

import re
import shlex

from analyzer.scripted_model import ScriptedModel, ScriptedTurn, assistant_turns, first_user_text

_REFUSE = re.compile(r"\b(patient names?|member names?|ssn|social security|date of birth|dob|home address|who is the patient)\b", re.I)

DEMO_QUERIES = [
    (re.compile(r"\b(trend|over time|monthly|by month|per month)\b", re.I),
     "SELECT date_trunc('month', service_date)::DATE AS service_month, SUM(paid_amount) AS paid_amount\n"
     "FROM sem.claims\nGROUP BY 1\nORDER BY 1",
     "Paid amount by service month.",
     ["Month is the claim's service date, not the remittance date.", "Pending and denied claims count as $0 paid."]),
    (re.compile(r"\b(denial|denied)\b.*\bpayer\b|\bpayer\b.*\b(denial|denied)\b", re.I),
     "SELECT payer_name, AVG(CASE WHEN status = 'denied' THEN 1.0 ELSE 0.0 END) AS denial_rate, COUNT(*) AS claims\n"
     "FROM sem.claims\nGROUP BY 1\nORDER BY denial_rate DESC",
     "Denial rate by payer.",
     ["Denial means the whole claim was denied; partial payments are not denials."]),
    (re.compile(r"\bpayer\b", re.I),
     "SELECT payer_name, SUM(paid_amount) AS paid_amount, COUNT(*) AS claims\nFROM sem.claims\nGROUP BY 1\nORDER BY paid_amount DESC",
     "Paid amount by payer.", ["All service dates in the current gold version."]),
    (re.compile(r"\b(list|show|export|all|every|download|csv)\b.*\bclaims?\b", re.I),
     "SELECT claim_key, payer_name, service_date, status, charge_amount, paid_amount, carc_code\n"
     "FROM sem.claims\nORDER BY service_date DESC, claim_key",
     "Here are the claims, newest service date first. The table shows the first rows; download the CSV for all of them.",
     ["Row-level claim list; no patient identifiers are included."]),
    (re.compile(r"\b(name|names)\b.*\bclaims?\b", re.I),
     "SELECT claim_key, name_first, paid_amount FROM sem.claims ORDER BY claim_key LIMIT 20",
     "Claims with the identifier column (it is redacted in this session).", []),
]
DEFAULT_QUERY = ("SELECT COUNT(*) AS claims, SUM(charge_amount) AS charge_amount, SUM(paid_amount) AS paid_amount\n"
                 "FROM sem.claims", "Overall claim volume and dollars.", ["Counts every claim in the current gold version."])



# ---------------------------------------------------------------- demo scenarios (contract v1 §9)

SCENARIOS = ("table", "chart", "repair", "abstain", "clarify", "escalation", "candidates", "error", "slow", "report",
             # 1.16.0 edge cases (CONTRACT.md §9.1): what the UI must survive, each with an example stream
             "empty", "multi_table", "long_sql", "many_repairs", "clarify_nested", "escalation_abstain",
             "cancel_plan", "cancel_sql", "cancel_repair", "cancel_candidates", "cancel_escalation", "cancel_render",
             "clarify_multi")  # 1.23.0: Claude-style multi-question clarification (§6.1); appended like the schema enum
#: 1.16.0 "cancel at every stage": scenario -> (scripted run it rides on, the event that presses Stop).
#: The server presses Stop itself the moment that event is emitted, so the aborted stream is deterministic.
CANCEL_AT = {
    "cancel_plan": ("table", lambda e: e.get("type") == "status" and e.get("label") == "Thinking"),
    "cancel_sql": ("table", lambda e: e.get("type") == "step.sql_running"),
    "cancel_repair": ("repair", lambda e: e.get("type") == "step.repair_attempt"),
    "cancel_candidates": ("candidates", lambda e: e.get("type") == "step.sql_running"),
    "cancel_escalation": ("escalation", lambda e: e.get("probe") == "request_escalation"),
    "cancel_render": ("table", lambda e: e.get("type") in ("text.start", "part")),
}


def scripted_scenario(scenario: str | None) -> str | None:
    """The script a scenario runs (``report`` and the cancel_* scenarios reuse another one)."""
    if scenario == "report":
        return "chart"
    return CANCEL_AT[scenario][0] if scenario in CANCEL_AT else scenario


_SCENARIO_WORDS = [
    ("error", re.compile(r"\b(simulate (an )?(error|outage)|fail on purpose)\b", re.I)),
    ("clarify_multi", re.compile(r"\b(denial (rate )?breakdown|ask me (a few|some) questions)\b", re.I)),
    ("clarify", re.compile(r"\b(how (bad|big) (are|is)|ambiguous|clarify)\b", re.I)),
    ("abstain", re.compile(r"\b(appeals?|prior auth\w*|forecast|predict\w*|next (month|quarter|year))\b", re.I)),
    ("escalation", re.compile(r"\b(escalat\w*|rejected files?|delivered files?|hard question)\b", re.I)),
    ("candidates", re.compile(r"\b(candidates?|double.?check|cross.?check|average paid)\b", re.I)),
    ("repair", re.compile(r"\b(carc|denial codes?|reason codes?|repair)\b", re.I)),
    ("slow", re.compile(r"\bslow(ly)?\b", re.I)),
    ("report", re.compile(r"\b(report|artifact)\b", re.I)),
]
# 1.16.0 edge cases are reached with ChatRequest.scenario only (no keywords), so real questions never hit them.
CLARIFY_QUESTION = "Do you want the denial rate by **claim count** or by **billed dollars**?"
CLARIFY_CHOICES = [
    {"id": "count", "label": "By claim count", "description": "Share of claims that were fully denied"},
    {"id": "dollars", "label": "By billed dollars", "description": "Share of charges on denied claims"},
]
ABSTAIN_REASON = ("This data doesn't include **appeals, prior authorizations or forecasts**, so I can't answer "
                  "that reliably. I can work with claims, payers, service dates, charges, payments and denial codes.")
PHI_REFUSAL = "I can't return patient-identifying details. I can give counts, rates and dollars instead."
ESCALATION_REASON = "sem.claims has no file parse status; the delivered EDI files are needed"
REJECTED_FILES_SQL = ("SELECT file_kind, COUNT(*) AS rejected_files\nFROM lossless.edi_source_file\n"
                      "WHERE parse_status = 'REJECTED'\nGROUP BY 1\nORDER BY 1")


def detect_scenario(question: str, forced: str | None = None) -> str:
    """``forced`` (ChatRequest.scenario, demo only) wins; otherwise keywords; otherwise ``table``/``chart``."""
    if forced in SCENARIOS:
        return forced
    if _REFUSE.search(question):
        return "abstain"
    for name, pattern in _SCENARIO_WORDS:
        if pattern.search(question) and not (name.startswith("clarify") and "Clarification:" in question):
            return name
    return "chart" if DEMO_QUERIES[0][0].search(question) else "table"


_CANDIDATE_SQL = [
    "SELECT payer_name, AVG(paid_amount) AS avg_paid_amount, COUNT(*) AS claims\nFROM sem.claims\nGROUP BY 1\nORDER BY 2 DESC",
    "SELECT payer_name, AVG(paid_amount) AS avg_paid_amount, COUNT(*) AS claims\nFROM sem.claims\nGROUP BY 1\nORDER BY 2 DESC",
    "SELECT payer_name, AVG(paid_amount) AS avg_paid_amount, COUNT(*) AS claims\nFROM sem.claims\nWHERE status = 'paid'\nGROUP BY 1\nORDER BY 2 DESC",
]
_SQL_CARC_BAD = ("SELECT denial_code, COUNT(*) AS claims\nFROM sem.claims\nWHERE status IN ('denied', 'partial')\n"
             "GROUP BY 1\nORDER BY claims DESC")
# ---- 1.16.0 edge-case scripts (synthetic data; every query runs on the demo DuckDB)
EMPTY_SQL = ("SELECT payer_name, COUNT(*) AS claims, SUM(paid_amount) AS paid_amount\nFROM sem.claims\n"
             "WHERE payer_name = 'Tricare' AND status = 'denied'\nGROUP BY 1")
MULTI_TABLE_SQL = (
    "SELECT payer_name, COUNT(*) AS claims, SUM(paid_amount) AS paid_amount\nFROM sem.claims\nGROUP BY 1\nORDER BY 3 DESC",
    "SELECT status, COUNT(*) AS claims, SUM(charge_amount) AS charge_amount\nFROM sem.claims\nGROUP BY 1\nORDER BY 2 DESC",
)
MULTI_TABLE_TITLES = ("By payer", "By claim status")


def _long_sql() -> str:
    """A realistic but very long query (~9 KB, 150+ lines): one CTE per payer plus a wide pivot."""
    payers = ["Aetna", "BCBS", "Cigna", "Humana", "Medicare", "Medicaid", "UnitedHealthcare"]
    ctes = []
    for p in payers:
        key = p.lower()
        ctes.append(
            f"{key}_monthly AS (\n"
            f"  SELECT date_trunc('month', service_date)::DATE AS service_month,\n"
            f"         COUNT(*) AS claims,\n"
            f"         SUM(charge_amount) AS charge_amount,\n"
            f"         SUM(paid_amount) AS paid_amount,\n"
            f"         SUM(CASE WHEN status = 'denied' THEN 1 ELSE 0 END) AS denied_claims,\n"
            f"         SUM(CASE WHEN status = 'partial' THEN 1 ELSE 0 END) AS partial_claims\n"
            f"  FROM sem.claims\n"
            f"  WHERE payer_name = '{p}'\n"
            f"  GROUP BY 1\n)")
    cols = ",\n".join(
        f"  COALESCE({p.lower()}_monthly.paid_amount, 0) AS paid_{p.lower()},\n"
        f"  COALESCE({p.lower()}_monthly.denied_claims, 0) AS denied_{p.lower()}" for p in payers)
    joins = "\n".join(f"LEFT JOIN {p.lower()}_monthly USING (service_month)" for p in payers)
    return ("WITH months AS (\n  SELECT DISTINCT date_trunc('month', service_date)::DATE AS service_month\n"
            "  FROM sem.claims\n),\n" + ",\n".join(ctes) +
            "\nSELECT\n  months.service_month,\n" + cols + "\nFROM months\n" + joins + "\nORDER BY 1")


LONG_SQL = _long_sql()
#: many_repairs: four failing attempts, each with a different error, then a working query.
MANY_REPAIRS_BAD = [
    "SELECT denial_reason, COUNT(*) AS claims\nFROM sem.claims\nGROUP BY 1",
    "SELECT carc, COUNT(*) AS claims\nFROM sem.claims\nGROUP BY 1",
    "SELECT carc_code, COUNT(*) AS claims\nFROM sem.claim\nGROUP BY 1",
    "SELECT carc_code, COUNT(*) AS claims\nFROM sem.claims\nGROUP BY carc_code, status ORDER BY claimz",
]
NESTED_CLARIFY = [
    (CLARIFY_QUESTION, CLARIFY_CHOICES),
    ("Over which **time window**?",
     [{"id": "90d", "label": "Last 90 days", "description": "Service dates in the last 90 days of data"},
      {"id": "all", "label": "All time", "description": "Every service date in the data"}]),
]

_SQL_CARC = ("SELECT carc_code, COUNT(*) AS claims, SUM(charge_amount) AS charge_amount\nFROM sem.claims\n"
         "WHERE status IN ('denied', 'partial')\nGROUP BY 1\nORDER BY claims DESC")


#: 1.23.0 demo (§6.1): three questions in one form, the last one multi-select.
MULTI_CLARIFY_LEAD = "A few quick questions so the numbers match what you need:"
MULTI_CLARIFY = [
    {"id": "metric", "header": "Metric", "question": "How should the denial rate be measured?", "options": [
        {"id": "count", "label": "By claim count", "description": "Share of claims that were fully denied"},
        {"id": "dollars", "label": "By billed dollars", "description": "Share of charges on denied claims"}]},
    {"id": "period", "header": "Period", "question": "Which time window?", "options": [
        {"id": "last_90", "label": "Last 90 days", "description": "Service dates in the last 90 days of data"},
        {"id": "last_12m", "label": "Last 12 months", "description": "Service dates in the last 12 months of data"},
        {"id": "all", "label": "All time", "description": "Every service date in the data"}]},
    {"id": "group", "header": "Group by", "question": "How should the results be broken down?", "multiSelect": True,
     "options": [{"id": "payer", "label": "By payer", "description": "One row per payer"},
                 {"id": "month", "label": "By service month", "description": "One row per month of service"}]},
]



# ---------------------------------------------------------------- the scripts, as B1 command lines

def command(*words: str) -> str:
    """One command line, each word quoted as a shell would need it."""
    return " ".join(shlex.quote(word) for word in words)


def _cc(*words: str) -> ScriptedTurn:
    return ScriptedTurn(tool_calls=[("cc", {"command": command(*words)})])


def _sql(sql: str) -> ScriptedTurn:
    return _cc("sql", sql)


def _answer(sql: str, text: str) -> ScriptedTurn:
    return _cc("answer", "--sql", sql, "--text", text)


def _refuse(reason: str) -> ScriptedTurn:
    return _cc("refuse", reason)


def _ask_form(lead_in: str, questions: list) -> ScriptedTurn:
    import json
    return _cc("ask", lead_in, "--questions", json.dumps(questions))


def _one_question(question: str, choices: list, header: str = "Measure") -> list:
    return [{"header": header, "question": question, "options": choices}]


def _query_for(question: str) -> tuple[str, str]:
    for pattern, sql, prose, _assumptions in DEMO_QUERIES:
        if pattern.search(question):
            return sql, prose
    return DEFAULT_QUERY[0], DEFAULT_QUERY[1]


def _question_and_step(messages: list) -> tuple[str, int]:
    text = first_user_text(messages)
    question = text.split("User question: ", 1)[1].rstrip("\n") if "User question: " in text else text
    return question, assistant_turns(messages)


def _turn_table(question, step):
    if _REFUSE.search(question):
        return _refuse(PHI_REFUSAL)
    sql, prose = _query_for(question)
    return _sql(sql) if step == 0 else _answer(sql, prose)


def _turn_abstain(question, step):
    return _refuse(PHI_REFUSAL if _REFUSE.search(question) else ABSTAIN_REASON)


def _turn_error(question, step):
    if step == 0:
        return _sql(DEFAULT_QUERY[0])
    raise RuntimeError("simulated model outage")


def _denial_sql(dollars: bool, where: str = "") -> str:
    return (("SELECT payer_name, SUM(CASE WHEN status = 'denied' THEN charge_amount ELSE 0 END) / SUM(charge_amount)"
             " AS denied_dollar_rate, SUM(charge_amount) AS charge_amount\nFROM sem.claims") if dollars else
            ("SELECT payer_name, AVG(CASE WHEN status = 'denied' THEN 1.0 ELSE 0.0 END) AS denial_rate, COUNT(*) AS claims"
             "\nFROM sem.claims")) + where + "\nGROUP BY 1\nORDER BY 2 DESC"


def _turn_clarify(question, step):
    m = re.search(r"Clarification:\s*(.+)", question)
    if m is None:
        return _ask_form(CLARIFY_QUESTION, _one_question(CLARIFY_QUESTION, CLARIFY_CHOICES))
    dollars = bool(re.search(r"dollar|charge|amount", m.group(1), re.I))
    sql = _denial_sql(dollars)
    return _sql(sql) if step == 0 else _answer(
        sql, "Denial rate by payer, " + ("weighted by billed dollars." if dollars else "by claim count."))


def _turn_escalation(question, step):
    if step == 0:
        return _cc("escalate", ESCALATION_REASON)
    if step == 1:
        return _cc("list")
    if step == 2:
        return _sql(REJECTED_FILES_SQL)
    return _answer(REJECTED_FILES_SQL, "Rejected EDI files by file type, from the delivered files.")


def _turn_candidates(question, step):
    sql = _CANDIDATE_SQL[0]
    return _sql(sql) if step == 0 else _answer(sql, "Average paid amount per claim, by payer.")


def _turn_repair(question, step):
    if step == 0:
        return _sql(_SQL_CARC_BAD)
    if step == 1:
        return _sql(_SQL_CARC)
    return _answer(_SQL_CARC, "Denied and partially paid claims by CARC code.")


def _turn_empty(question, step):
    return _sql(EMPTY_SQL) if step == 0 else _answer(
        EMPTY_SQL, "No denied claims from Tricare in this data, so there is nothing to break down.")


def _turn_multi_table(question, step):
    if step in (0, 1):
        return _sql(MULTI_TABLE_SQL[step])
    return _answer(MULTI_TABLE_SQL[0], "Claims and paid amount by payer, and claims and charges by status.")


def _turn_long_sql(question, step):
    return _sql(LONG_SQL) if step == 0 else _answer(
        LONG_SQL, "Paid amount and denied claims by month, one column pair per payer.")


def _turn_many_repairs(question, step):
    if step < len(MANY_REPAIRS_BAD):
        return _sql(MANY_REPAIRS_BAD[step])
    if step == len(MANY_REPAIRS_BAD):
        return _sql(_SQL_CARC)
    return _answer(_SQL_CARC, "Denied and partially paid claims by CARC code (after four corrections).")


def _turn_clarify_nested(question, step):
    answered = len(re.findall(r"^Clarification:", question, re.M))
    if answered < len(NESTED_CLARIFY):
        asked, choices = NESTED_CLARIFY[answered]
        return _ask_form(asked, _one_question(asked, choices, header="Measure" if answered == 0 else "Window"))
    dollars = bool(re.search(r"Clarification:.*dollar", question, re.I))
    recent = re.search(r"Clarification:.*90 days", question, re.I)
    where = "\nWHERE service_date >= (SELECT MAX(service_date) - INTERVAL 90 DAY FROM sem.claims)" if recent else ""
    sql = _denial_sql(dollars, where)
    return _sql(sql) if step == 0 else _answer(
        sql, "Denial rate by payer, " + ("weighted by billed dollars" if dollars else "by claim count")
        + (", last 90 days." if recent else ", all time."))


def _turn_clarify_multi(question, step):
    m = re.search(r"^Clarification:\s*(.*)$", question, re.M)
    if m is None:
        return _ask_form(MULTI_CLARIFY_LEAD, MULTI_CLARIFY)
    picked = dict(kv.split(": ", 1) for kv in m.group(1).split("; ") if ": " in kv)
    dollars = "dollar" in picked.get("Metric", "").lower()
    period = picked.get("Period", "")
    days = 90 if "90 days" in period else 365 if "12 months" in period else None
    groups = picked.get("Group by", "")
    keys = [k for k, on in (("payer_name", "payer" in groups.lower()),
                            ("strftime(service_date, '%Y-%m') AS service_month", "month" in groups.lower())) if on]
    keys = keys or ["payer_name"]
    measure = ("SUM(CASE WHEN status = 'denied' THEN charge_amount ELSE 0 END) / SUM(charge_amount) AS denied_dollar_rate"
               if dollars else "AVG(CASE WHEN status = 'denied' THEN 1.0 ELSE 0.0 END) AS denial_rate")
    where = (f"\nWHERE service_date >= (SELECT MAX(service_date) - INTERVAL {days} DAY FROM sem.claims)" if days else "")
    sql = (f"SELECT {', '.join(keys)}, {measure}, COUNT(*) AS claims\nFROM sem.claims{where}\n"
           f"GROUP BY {', '.join(str(i + 1) for i in range(len(keys)))}\nORDER BY {len(keys) + 1} DESC")
    if step == 0:
        return _sql(sql)
    how = "weighted by billed dollars" if dollars else "by claim count"
    when = {90: "last 90 days", 365: "last 12 months"}.get(days, "all time")
    return _answer(sql, f"Denial rate {how}, {when}. Questions you skipped use the default (claim count, all time, "
                        "by payer).")


def _turn_escalation_abstain(question, step):
    if step == 0:
        return _cc("escalate", "sem.* has no appeal outcomes; checking whether the delivered EDI files record them")
    if step == 1:
        return _cc("list")
    return _refuse(ABSTAIN_REASON)


TURNS = {"table": _turn_table, "chart": _turn_table, "slow": _turn_table, "abstain": _turn_abstain,
         "error": _turn_error, "clarify": _turn_clarify, "escalation": _turn_escalation,
         "candidates": _turn_candidates, "repair": _turn_repair, "empty": _turn_empty,
         "multi_table": _turn_multi_table, "long_sql": _turn_long_sql, "many_repairs": _turn_many_repairs,
         "clarify_nested": _turn_clarify_nested, "clarify_multi": _turn_clarify_multi,
         "escalation_abstain": _turn_escalation_abstain}
#: the slow scenario's pause per model call, at least (it must stay stoppable)
SLOW_MULTIPLIER = 4
SLOW_FLOOR_SECONDS = 0.8


def demo_model(scenario: str | None, seconds_per_turn: float = 0.0) -> ScriptedModel:
    """The scripted model for one run. ``scenario`` None plays the default (table or chart, by the question)."""
    script = TURNS.get(scripted_scenario(scenario) or "table", _turn_table)
    if scenario == "slow":
        seconds_per_turn = max(seconds_per_turn * SLOW_MULTIPLIER, SLOW_FLOOR_SECONDS if seconds_per_turn else 0.0)

    def next_turn(messages, _system):
        question, step = _question_and_step(messages)
        return script(question, step)
    return ScriptedModel(next_turn, seconds_per_turn=seconds_per_turn)
