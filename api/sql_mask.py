"""Literal masking for *shown* SQL (contract 1.14.0, CONTRACT.md §10).

The SQL a run executes is never changed. What the UI shows (``step.sql_running.sql``, ``step.repair_attempt.revisedSql``, ``step.candidates`` SQL, the
``code`` part with ``role: query`` and legacy ``tool_call`` input) has PHI-bearing literals replaced by ``?``, and the
event/part carries ``maskedLiterals: <count>``. Policies (``Capabilities.sqlLiteralMasking``):

* ``"phi"`` (default): mask a literal when the column it is compared with is PHI-bearing (patient/member/subscriber
  ids and names, MRN, DOB, SSN, address, phone, email, ...), or when the value itself looks like an identifier
  (SSN, e-mail, phone, a 7+ digit run, an 8+ char mix of letters and digits). ``status = 'denied'`` and date ranges on
  ``service_date`` stay readable.
* ``"all"``: mask every string literal and every number compared with a column (``LIMIT`` and ordinals stay).
* ``"off"``: show SQL verbatim (pre-1.14 behaviour).

A small lexer (strings with ``''`` escapes, quoted identifiers, comments, numbers) finds literals, then walks back
over ``IN (...)``, ``BETWEEN x AND``, ``LIKE``, ``NOT`` and comparison operators to the compared column. Masking is
idempotent (``?`` is not a literal) and fails closed: SQL the lexer cannot tokenize (an unterminated string) is
replaced by a fixed placeholder under ``phi``/``all``.
"""
from __future__ import annotations

import os
import re
from typing import Callable, Iterator

POLICIES = ("phi", "all", "off")
DEFAULT_POLICY = "phi"
ENV = "CC_API_SQL_LITERAL_MASKING"
UNPARSEABLE = "-- SQL hidden: it could not be checked for patient data."

PHI_COLUMN = re.compile(
    r"(?i)(?:^|_)(?:patient|member|subscriber|insured|guarantor|beneficiary|enrollee|person|guardian)(?:_|$)"
    r"|(?:^|_)(?:mrn|dob|ssn|sin|npi|birth|birthdate|birth_date|date_of_birth|first_name|last_name|full_name|"
    r"given_name|family_name|middle_name|maiden_name|address|address1|address2|street|city|zip|zipcode|zip_code|"
    r"postal|postal_code|phone|mobile|fax|email|e_mail|account_number|acct|acct_no|medicare_id|medicaid_id|"
    r"license|license_number|plate|vin|ip_address|device_id|url|claim_id|claim_number|claim_no|encounter_id|"
    r"visit_id|invoice|invoice_number|accession|accession_number|specimen_id|requisition|requisition_id)(?:_|$)")
PHI_VALUE = re.compile(  # searched anywhere in the value, so free text ("call 555-123-4567") is caught too
    r"\b\d{3}-\d{2}-\d{4}\b"                          # SSN
    r"|[^@\s'%]+@[^@\s'%]+\.[A-Za-z]{2,}"              # e-mail
    r"|(?<!\d)\+?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}(?!\d)"    # phone
    r"|(?<![\d-])\d{7,}(?![\d-])"                       # long id (MRN, member, NPI, account)
    r"|\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9][A-Za-z0-9-]{7,}\b")   # mixed id (XYZ123456789)

_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<comment>--[^\n]*|/\*.*?\*/)
  | (?P<string>[EeNn]?'(?:[^']|'')*')
  | (?P<qident>"(?:[^"]|"")*"|`[^`]*`|\[[^\]]*\])
  | (?P<number>(?<![\w.])\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\w.]))
  | (?P<ident>[A-Za-z_][\w$]*(?:\.[A-Za-z_"][\w$"]*)*)
  | (?P<op><>|!=|<=|>=|=|<|>|\|\||::)
  | (?P<punct>[(),;.*+\-/%])
  | (?P<other>.)
""", re.X | re.S)

_CMP = {"=", "<>", "!=", "<", ">", "<=", ">="}
_SKIP_KW = {"IN", "NOT", "LIKE", "ILIKE", "SIMILAR", "TO", "IS", "ANY", "ALL"}
_TYPED = {"DATE", "TIME", "TIMESTAMP", "TIMESTAMPTZ", "DATETIME"}
_STOP_KW = {"WHERE", "AND", "OR", "ON", "HAVING", "WHEN", "THEN", "ELSE", "SELECT", "SET", "VALUES", "LIMIT", "OFFSET",
            "BY", "FROM", "JOIN", "CASE", "END", "RETURNING", "INTERVAL", "TOP", "FETCH"}
_SAFE_KW = {"LIMIT", "OFFSET", "TOP", "FETCH", "INTERVAL", "BY"}


class _Unparseable(Exception):
    pass


def _lex(sql: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    for m in _TOKEN.finditer(sql):
        if m.start() != pos:
            raise _Unparseable
        kind = m.lastgroup
        if kind == "other" and m.group() == "'":
            raise _Unparseable          # an unterminated string literal
        out.append((kind, m.group()))
        pos = m.end()
    if pos != len(sql):
        raise _Unparseable
    return out


def policy_from_env(env: dict | None = None) -> str:
    value = (os.environ if env is None else env).get(ENV, DEFAULT_POLICY).strip().lower()
    return value if value in POLICIES else DEFAULT_POLICY        # a typo never turns masking off


def _context(toks: list[tuple[str, str]], i: int) -> tuple[str | None, bool, bool]:
    """(compared column, compared with an operator, in a LIMIT/OFFSET/ordinal position) for the literal at ``i``."""
    j, compared, between = i - 1, False, False
    while j >= 0:
        kind, text = toks[j]
        up = text.upper()
        if kind in ("ws", "comment") or (kind == "punct" and text in "(,") or kind in ("string", "number"):
            j -= 1
            continue
        if kind == "op" and text in _CMP:
            compared = True
            j -= 1
            continue
        if kind == "ident" and up in _TYPED:      # DATE '1980-01-02', TIMESTAMP '…'
            j -= 1
            continue
        if kind == "ident" and up in _SKIP_KW:
            compared = True
            j -= 1
            continue
        if kind == "ident" and up == "BETWEEN":
            compared, between = True, False
            j -= 1
            continue
        if kind == "ident" and up == "AND" and not between:
            # ``BETWEEN a AND <literal>``: keep walking only if a BETWEEN precedes the previous literal
            k = j - 1
            while k >= 0 and toks[k][0] in ("ws", "comment", "string", "number"):
                k -= 1
            if k >= 0 and toks[k][1].upper() == "BETWEEN":
                between = True
                j = k
                continue
            return None, compared, False
        if kind == "ident" and up in _STOP_KW:
            return None, compared, up in _SAFE_KW
        if kind in ("ident", "qident"):
            return (_name(text) if compared else None), compared, False
        if kind == "punct" and text == ")" and compared:
            # ``lower(last_name) = 'x'``, ``CAST(dob AS date) = 'x'``: the column is inside the call
            depth, k, names = 0, j, []
            while k >= 0:
                if toks[k][1] == ")":
                    depth += 1
                elif toks[k][1] == "(":
                    depth -= 1
                    if depth == 0:
                        break
                elif toks[k][0] in ("ident", "qident") and toks[k][1].upper() not in _STOP_KW | _SKIP_KW | {"AS"}:
                    names.append(_name(toks[k][1]))
                k -= 1
            return next((n for n in names if PHI_COLUMN.search(n)), names[0] if names else None), compared, False
        return None, compared, False
    return _forward(toks, i), compared, False


def _name(text: str) -> str:
    return text.strip('"`[]').rsplit(".", 1)[-1].strip('"')


def _forward(toks: list[tuple[str, str]], i: int) -> str | None:
    """``'x' = last_name`` (literal first): the column after the operator."""
    rest = [t for t in toks[i + 1:i + 6] if t[0] not in ("ws", "comment")]
    if len(rest) >= 2 and rest[0][0] == "op" and rest[0][1] in _CMP and rest[1][0] in ("ident", "qident"):
        return _name(rest[1][1])
    return None


def mask_sql(sql: str, policy: str = DEFAULT_POLICY) -> tuple[str, int]:
    """``(shown_sql, masked_count)``. ``policy="off"`` returns the SQL unchanged."""
    if policy == "off" or not sql:
        return sql, 0
    try:
        toks = _lex(sql)
    except _Unparseable:
        return UNPARSEABLE, 1
    count = 0
    for i, (kind, text) in enumerate(toks):
        if kind not in ("string", "number"):
            continue
        column, compared, safe = _context(toks, i)
        if column is None and not safe:
            column = _forward(toks, i)
            compared = compared or column is not None
        if safe:
            continue
        value = text[text.index("'") + 1:-1].replace("''", "'") if kind == "string" else text
        if policy == "all":
            hit = kind == "string" or compared
        else:
            hit = bool(column and PHI_COLUMN.search(column)) or bool(PHI_VALUE.search(value))
        if hit:
            toks[i] = (kind, "?")
            count += 1
    return "".join(t for _, t in toks), count


def _mask_field(obj: dict, key: str, policy: str) -> int:
    if isinstance(obj.get(key), str):
        obj[key], n = mask_sql(obj[key], policy)
        return n
    return 0


def mask_part(part: dict, policy: str = DEFAULT_POLICY) -> dict:
    """A copy of ``part`` with shown SQL masked and ``maskedLiterals`` set (only when > 0)."""
    if policy == "off" or not isinstance(part, dict):
        return part
    t = part.get("type")
    if t == "code" and (part.get("role") == "query" or str(part.get("language", "")).lower() == "sql"):
        out = dict(part)
        n = _mask_field(out, "code", policy)
        if n:
            out["maskedLiterals"] = out.get("maskedLiterals", 0) + n
        return out
    if t == "tool_call" and isinstance(part.get("input"), dict) and "sql" in part["input"]:
        out = {**part, "input": dict(part["input"])}
        _mask_field(out["input"], "sql", policy)
        return out
    if t == "artifact" and isinstance(part.get("content"), dict):
        return {**part, "content": mask_part(part["content"], policy)}
    return part


def mask_event(event: dict, policy: str = DEFAULT_POLICY) -> dict:
    """A copy of a stream event with shown SQL masked. Events without SQL are returned as-is."""
    if policy == "off":
        return event
    t = event.get("type")
    if t == "step.sql_running":
        out = dict(event)
        n = _mask_field(out, "sql", policy)
        if n:
            out["maskedLiterals"] = n
        return out
    if t == "step.repair_attempt":
        out = dict(event)
        n = _mask_field(out, "revisedSql", policy)
        if n:
            out["maskedLiterals"] = n
        return out
    if t == "step.candidates" and isinstance(event.get("candidates"), list):
        cands = []
        for c in event["candidates"]:
            c = dict(c)
            n = _mask_field(c, "sql", policy)
            if n:
                c["maskedLiterals"] = n
            cands.append(c)
        return {**event, "candidates": cands}
    if t == "part" and isinstance(event.get("part"), dict):
        return {**event, "part": mask_part(event["part"], policy)}
    if t == "part.patch" and isinstance(event.get("patch"), dict) and isinstance(event["patch"].get("code"), str):
        patch = dict(event["patch"])
        patch["code"], n = mask_sql(patch["code"], policy)
        if n:
            patch["maskedLiterals"] = n
        return {**event, "patch": patch}
    return event


def masking_emit(emit: Callable[[dict], None], policy: str = DEFAULT_POLICY) -> Callable[[dict], None]:
    if policy == "off":
        return emit
    return lambda event: emit(mask_event(event, policy))


def iter_literals(sql: str) -> Iterator[str]:
    """Every literal token in ``sql`` (tests use it to prove nothing PHI-like survived)."""
    for kind, text in _lex(sql):
        if kind in ("string", "number"):
            yield text
