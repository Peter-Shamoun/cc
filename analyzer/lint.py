"""Domain SQL lint (#22) and reconciliation checks (#23).

The generic result checks (#14, ``analyzer.checks``) look at any result: empty, NULL columns, rates outside
0..100. This module encodes our own traps and needs the question text:

- ``missing_current_adjudication``: money, denials or adjudication counts from an 835 relation with no
  is_current_adjudication filter, when the question does not ask about 835 events or reversals.
- ``missing_latest_submission``: claim or line counts and sums from sem.claims / sem.claim_lines /
  sem.diagnoses with no is_latest_submission filter and no DISTINCT claim_key, when the question is not about
  submissions themselves.
- ``wrong_date_column``: the question names one time basis (payment, service, or submission) and the SQL
  filters or groups on another basis's date column and never on the named one.
- ``grain_multiplying_join``: SUM/AVG of a claim-grain amount in a SELECT that also joins a finer-grain view
  (lines, adjustments, remarks, diagnoses), so the amount repeats once per finer row.

Reconciliation runs on the executed rows:

- ``payment_balancing``: an 835 check total is BPR02 = sum(CLP04) - sum(PLB). total_payment_amount on
  sem.payments is not the claims paid when provider adjustments exist, and the reverse.
- ``rate_parts``: a rate shown next to exactly two counts must equal one count over the other.
- ``group_shares``: shares of a total, over every group, must add up to the whole.

Every finding carries a message that says what to change. Findings feed repair (#13) through analyzer.cli.
A lint lists only the patterns above; it is not proof a query is right.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

CURRENT_ADJUDICATION_RELATIONS = frozenset({"remit_claims", "remit_lines", "adjustments", "remarks"})
SUBMISSION_RELATIONS = frozenset({"claims", "claim_lines", "diagnoses"})
CLAIM_GRAIN_RELATIONS = frozenset({"claims", "remit_claims"})
FINER_THAN_CLAIM_RELATIONS = frozenset({"claim_lines", "remit_lines", "adjustments", "remarks", "diagnoses"})
CLAIM_GRAIN_AMOUNT_COLUMNS = frozenset({
    "charge_amount", "paid_amount", "patient_responsibility_amount", "co_amount", "pr_amount", "oa_amount",
    "pi_amount", "remit_paid_amount", "remit_patient_responsibility_amount", "remit_co_amount",
})

DATE_COLUMNS_BY_BASIS = {
    "payment": frozenset({"payment_date", "payment_month", "first_payment_date", "last_payment_date"}),
    "service": frozenset({"service_date", "service_month", "service_date_from", "service_date_to"}),
    "submission": frozenset({"submission_date", "submission_month"}),
}
_BASIS_IN_QUESTION = {
    "payment": re.compile(r"\bpayment (date|month|week|day)s?\b|\bpaid (in|during|between|on|from|since)\b"
                          r"|\bremitted (in|during|between|on)\b|\b835 (date|month)\b", re.IGNORECASE),
    "service": re.compile(r"\bservice (date|month|week|day)s?\b|\bdates? of service\b|\bDOS\b"
                          r"|\bserviced (in|during|between|on)\b|\bperformed (in|during|between|on)\b",
                          re.IGNORECASE),
    "submission": re.compile(r"\bsubmission (date|month|week|day)s?\b|\bsubmitted (in|during|between|on|from|since)\b"
                             r"|\bbilled (in|during|between|on)\b|\bsent (in|during|between|on)\b", re.IGNORECASE),
}
_QUESTION_ABOUT_835_EVENTS = re.compile(
    r"revers|every 835|each 835|all 835|835 events?|adjudication events?|remit events?|superseded|history"
    r"|no current adjudication|first (remit|payment|835)|earliest (remit|payment|835)|ever (paid|denied|remitted)"
    r"|before the (replacement|correction)|had sent|previous(ly)? (paid|denied)", re.IGNORECASE)
_QUESTION_ABOUT_SUBMISSIONS = re.compile(
    r"submi|frequency|correct(ed|ion)|replacement|void|every 837|each 837|837 rows?|first (bill|837)"
    r"|secondary|sent on|billed (more than|again|twice)", re.IGNORECASE)
_MEASURE_OF_ADJUDICATION = re.compile(
    r"\b(paid_amount|line_paid_amount|patient_responsibility_amount|adjustment_amount|is_denied|claim_is_denied"
    r"|line_is_denied|is_denied_recorded|is_denied_signal_v3|co_amount|pr_amount|line_allowed_amount|units_paid)\b",
    re.IGNORECASE)
_RATE_COLUMN = re.compile(r"rate|pct|percent|ratio", re.IGNORECASE)
_RATE_WORDS = frozenset({"rate", "pct", "percent", "ratio", "of", "share"})
_COUNT_COLUMN = re.compile(r"count|claims|lines|adjudications|submissions|denied|remitted|paid_claims|^n_|_n$|num",
                           re.IGNORECASE)
_NOT_A_COUNT_COLUMN = re.compile(r"_id$|code|days|key|number|npi|month|date", re.IGNORECASE)
_SHARE_COLUMN = re.compile(r"(share|pct|percent)_of_(the_)?(total|all)|_of_total|share_of_|proportion_of_",
                           re.IGNORECASE)
_CHECK_TOTAL_IN_QUESTION = re.compile(
    r"\b(check|eft) (totals?|amounts?)\b|\bdeposits?\b|\bbpr ?02\b|\bpayment totals?\b|\btotal of (the )?checks\b",
    re.IGNORECASE)
_CLAIMS_PAID_IN_QUESTION = re.compile(r"paid on (the )?claims|claim payments?|clp ?04|claims paid", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    code: str
    message: str

    def render(self) -> str:
        return f"{self.code}: {self.message}"


def _parse(sql: str):
    try:
        return sqlglot.parse_one(sql, read="duckdb")
    except sqlglot.errors.ParseError:
        return None


def _sem_relations(tree) -> set[str]:
    return {table.name.lower() for table in tree.find_all(exp.Table) if (table.db or "").lower() == "sem"}


def _column_names(tree) -> set[str]:
    return {column.name.lower() for column in tree.find_all(exp.Column)}


def domain_lint(sql: str, question: str) -> list[Finding]:
    """Static checks over the SQL and the question. No database access, so a finished session re-lints the same."""
    tree = _parse(sql)
    if tree is None:
        return []
    relations = _sem_relations(tree)
    columns = _column_names(tree)
    lowered = " ".join(sql.lower().split())
    findings = []
    adjudication_relations = sorted(relations & CURRENT_ADJUDICATION_RELATIONS)
    if (adjudication_relations and "is_current_adjudication" not in columns
            and not {"is_reversal", "adjudication_state", "claim_status", "claim_status_code"} & columns
            and _MEASURE_OF_ADJUDICATION.search(lowered) is not None
            and not _QUESTION_ABOUT_835_EVENTS.search(question)):
        findings.append(Finding("missing_current_adjudication", (
            f"sem.{adjudication_relations[0]} is aggregated without is_current_adjudication, so reversals and "
            "superseded 835 events are counted. Add WHERE is_current_adjudication (or say in the answer why every "
            "835 event is wanted).")))
    submission_relations = sorted(relations & SUBMISSION_RELATIONS)
    if (submission_relations and "is_latest_submission" not in columns
            and not re.search(r"count\s*\(\s*distinct\s+(\w+\.)?claim_key", lowered)
            and not {"submission_count", "frequency_code", "frequency", "payer_responsibility"} & columns
            and not _QUESTION_ABOUT_SUBMISSIONS.search(question)):
        findings.append(Finding("missing_latest_submission", (
            f"sem.{submission_relations[0]} has one row per 837 submission and is used without "
            "is_latest_submission, so a resubmitted claim counts more than once. Filter is_latest_submission "
            "(or payer_responsibility = 'P' with DISTINCT claim_key for a primary-payer question).")))
    findings.extend(_wrong_date_column(tree, question))
    findings.extend(_grain_multiplying_joins(tree))
    return findings


def _filtered_or_grouped_columns(tree) -> set[str]:
    names = set()
    for clause in list(tree.find_all(exp.Where)) + list(tree.find_all(exp.Group)) + list(tree.find_all(exp.Join)):
        names |= {column.name.lower() for column in clause.find_all(exp.Column)}
    return names


def _wrong_date_column(tree, question: str) -> list[Finding]:
    named = [basis for basis, pattern in _BASIS_IN_QUESTION.items() if pattern.search(question)]
    if len(named) != 1:
        return []
    basis = named[0]
    used = _filtered_or_grouped_columns(tree)
    if used & DATE_COLUMNS_BY_BASIS[basis]:
        return []
    others = sorted(column for other, columns in DATE_COLUMNS_BY_BASIS.items() if other != basis
                    for column in columns & used)
    if not others:
        return []
    wanted = ", ".join(sorted(DATE_COLUMNS_BY_BASIS[basis]))
    return [Finding("wrong_date_column", (
        f"the question is on a {basis} basis but the query filters or groups on {', '.join(others)}. "
        f"Use the {basis} date column ({wanted}) for the window or the grouping."))]


def _scope_tables(select: exp.Select) -> dict[str, str]:
    """alias -> sem relation for tables joined directly in this SELECT (not inside its subqueries)."""
    sources = []
    from_clause = select.args.get("from_") or select.args.get("from")  # sqlglot >= 26 names it from_
    if from_clause is not None:
        sources.append(from_clause.this)
    sources.extend(join.this for join in select.args.get("joins") or [])
    tables = {}
    for source in sources:
        if isinstance(source, exp.Table) and (source.db or "").lower() == "sem":
            tables[(source.alias_or_name or source.name).lower()] = source.name.lower()
    return tables


def _own_aggregates(select: exp.Select):
    """SUM/AVG calls in this SELECT's projections, not in nested SELECTs."""
    for projection in select.expressions:
        for aggregate in projection.find_all(exp.Sum, exp.Avg):
            if aggregate.find_ancestor(exp.Select) is select:
                yield aggregate


def _grain_multiplying_joins(tree) -> list[Finding]:
    findings = []
    for select in tree.find_all(exp.Select):
        tables = _scope_tables(select)
        coarse = {alias for alias, relation in tables.items() if relation in CLAIM_GRAIN_RELATIONS}
        fine = sorted({relation for relation in tables.values() if relation in FINER_THAN_CLAIM_RELATIONS})
        if not coarse or not fine:
            continue
        for aggregate in _own_aggregates(select):
            if isinstance(aggregate.this, exp.Distinct):
                continue
            for column in aggregate.find_all(exp.Column):
                qualifier = (column.table or "").lower()
                if column.name.lower() in CLAIM_GRAIN_AMOUNT_COLUMNS and qualifier in coarse:
                    findings.append(Finding("grain_multiplying_join", (
                        f"{aggregate.sql(dialect='duckdb')} sums a claim-level amount from sem.{tables[qualifier]} "
                        f"after a join to sem.{fine[0]}, which repeats it once per {fine[0]} row. Aggregate "
                        f"sem.{fine[0]} to the claim grain in a CTE first, or sum the line-level amount.")))
                    break
    return _dedupe(findings)


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen, kept = set(), []
    for finding in findings:
        if finding.render() not in seen:
            seen.add(finding.render())
            kept.append(finding)
    return kept


# #5 "mandatory before literals": text dimensions whose stored spelling the question cannot be trusted to match.
# Codes (CPT, CARC, dates, flags) are typed as the question gives them and are not gated.
LOOKUP_GATED_DIMENSIONS = frozenset({
    "payer_name", "payer_class", "billing_provider_name", "claim_status", "frequency", "payment_method",
    "adjudication_state", "carc_category", "carc_description", "remark_description", "plb_reason_description",
})


def filter_literals(sql: str) -> list[tuple[str, str, bool]]:
    """(dimension, literal, is_pattern) for each string literal compared to a gated dimension."""
    tree = _parse(sql)
    if tree is None:
        return []
    found = []
    for node in tree.find_all(exp.EQ, exp.NEQ, exp.In, exp.Like, exp.ILike):
        column = node.this if isinstance(node.this, exp.Column) else None
        if column is None or column.name.lower() not in LOOKUP_GATED_DIMENSIONS:
            continue
        literals = node.expressions if isinstance(node, exp.In) else [node.expression]
        for literal in literals:
            if isinstance(literal, exp.Literal) and literal.is_string:
                found.append((column.name.lower(), literal.this, isinstance(node, (exp.Like, exp.ILike))))
    return list(dict.fromkeys(found))


def reconciliation_findings(sql: str, question: str, columns: list[str], rows: list, connection) -> list[Finding]:
    """Identity checks on an executed result. ``connection`` is the session's guarded DuckDB (read-only sem.*)."""
    findings = []
    findings.extend(_payment_balancing(sql, question, connection))
    findings.extend(_rate_parts(columns, rows))
    findings.extend(_group_shares(sql, columns, rows))
    return findings


def _provider_adjustment_total(connection) -> float:
    return float(connection.execute(
        "SELECT coalesce(sum(abs(provider_adjustment_amount)), 0) FROM sem.payments").fetchone()[0])


def _payment_balancing(sql: str, question: str, connection) -> list[Finding]:
    tree = _parse(sql)
    if tree is None:
        return []
    columns = _column_names(tree)
    relations = _sem_relations(tree)
    uses_check_total = "payments" in relations and "total_payment_amount" in columns
    sums_claim_payments = "remit_claims" in relations and "paid_amount" in columns
    asks_check_total = bool(_CHECK_TOTAL_IN_QUESTION.search(question)) and not _CLAIMS_PAID_IN_QUESTION.search(question)
    asks_claims_paid = bool(_CLAIMS_PAID_IN_QUESTION.search(question))
    if not ((uses_check_total and asks_claims_paid) or (sums_claim_payments and asks_check_total)):
        return []
    if _provider_adjustment_total(connection) == 0:
        return []
    if uses_check_total and asks_claims_paid:
        return [Finding("payment_balancing", (
            "an 835 check total (BPR02, total_payment_amount) is sum(CLP04) minus provider adjustments (PLB), so it "
            "is not the amount paid on claims here. Use claims_paid_amount on sem.payments or paid_amount on the "
            "current adjudications in sem.remit_claims."))]
    return [Finding("payment_balancing", (
        "the question asks for check or EFT totals, and those are sum(CLP04) minus provider adjustments (PLB). "
        "Summing claim paid_amount leaves out the PLB. Use total_payment_amount on sem.payments."))]


def _numeric(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_count_column(index: int, rows: list) -> bool:
    values = [_numeric(row[index]) for row in rows]
    return bool(values) and all(value is not None and value >= 0 and value == int(value) for value in values)


def _ratio_matches(rate: float, numerator: float, denominator: float) -> bool:
    if denominator == 0:
        return True
    ratio = numerator / denominator
    return any(abs(rate - ratio * scale) <= max(0.0051 * scale, 1e-9) for scale in (1.0, 100.0))


def _rate_parts(columns: list[str], rows: list) -> list[Finding]:
    if not rows:
        return []
    rate_indexes = [i for i, name in enumerate(columns) if _RATE_COLUMN.search(str(name))]
    count_indexes = [i for i, name in enumerate(columns) if i not in rate_indexes and _COUNT_COLUMN.search(str(name))
                     and not _NOT_A_COUNT_COLUMN.search(str(name)) and _is_count_column(i, rows)]
    if len(rate_indexes) != 1 or len(count_indexes) != 2 or "_of_" in str(columns[rate_indexes[0]]).lower():
        return []
    rate_index, (left, right) = rate_indexes[0], count_indexes
    stems = {word[:4] for word in re.split(r"[^a-z0-9]+", str(columns[rate_index]).lower())
             if len(word) >= 4 and word not in _RATE_WORDS}
    part_words = {word[:4] for index in (left, right) for word in re.split(r"[^a-z0-9]+", str(columns[index]).lower())}
    if not stems & part_words:
        return []
    for row in rows:
        rate = _numeric(row[rate_index])
        if rate is None:
            continue
        a, b = _numeric(row[left]), _numeric(row[right])
        if not (_ratio_matches(rate, a, b) or _ratio_matches(rate, b, a)):
            return [Finding("rate_parts", (
                f"{columns[rate_index]} = {row[rate_index]} is neither {columns[left]}/{columns[right]} nor "
                f"{columns[right]}/{columns[left]} in the same row ({row[left]}, {row[right]}). Compute the rate "
                "from the same numerator and denominator the row shows, on the same population."))]
    return []


def _group_shares(sql: str, columns: list[str], rows: list) -> list[Finding]:
    if len(rows) < 2 or re.search(r"\blimit\b", sql, re.IGNORECASE):
        return []
    findings = []
    for index, name in enumerate(columns):
        if not _SHARE_COLUMN.search(str(name)):
            continue
        values = [_numeric(row[index]) for row in rows]
        if any(value is None for value in values):
            continue
        total = sum(values)
        tolerance = 0.006 * len(rows)
        if abs(total - 1.0) <= tolerance or abs(total - 100.0) <= 100 * tolerance:
            continue
        findings.append(Finding("group_shares", (
            f"{name} adds up to {round(total, 4)} over all {len(rows)} groups, not 1 or 100. Shares of a total "
            "must use the same total in every group (one denominator over the whole population).")))
    return findings
