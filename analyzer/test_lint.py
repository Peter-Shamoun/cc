"""#22 domain lint and #23 reconciliation: each rule fires on its trap, and never on TRAIN gold SQL."""

from analyzer.lint import domain_lint, filter_literals, reconciliation_findings


def _codes(sql, question="How much was paid?"):
    return [finding.code for finding in domain_lint(sql, question)]


def test_missing_current_adjudication_fires_unless_the_question_is_about_events():
    sql = "SELECT payer_name, sum(paid_amount) FROM sem.remit_claims GROUP BY 1"
    assert "missing_current_adjudication" in _codes(sql)
    assert _codes(sql.replace("GROUP", "WHERE is_current_adjudication GROUP")) == []
    assert "missing_current_adjudication" not in _codes(sql, "How much was paid across every 835 event, reversals included?")
    assert "missing_current_adjudication" not in _codes("SELECT count(*) FROM sem.remit_claims WHERE is_reversal")


def test_missing_latest_submission_fires_unless_claims_are_deduplicated():
    sql = "SELECT payer_name, sum(charge_amount) FROM sem.claims GROUP BY 1"
    assert "missing_latest_submission" in _codes(sql, "What did we bill by payer?")
    assert _codes(sql.replace("GROUP", "WHERE is_latest_submission GROUP"), "What did we bill by payer?") == []
    assert _codes("SELECT count(DISTINCT claim_key) FROM sem.claims", "How many claims did we bill?") == []
    assert _codes(sql, "How many claims were resubmitted?") == []


def test_wrong_date_column_needs_one_named_basis():
    sql = ("SELECT service_month, sum(paid_amount) FROM sem.remit_claims WHERE is_current_adjudication "
           "GROUP BY service_month")
    assert "wrong_date_column" in _codes(sql, "What was paid by payment month?")
    assert _codes(sql.replace("service_month", "payment_month"), "What was paid by payment month?") == []
    assert _codes(sql, "What was paid by service month?") == []
    assert _codes(sql, "Days from service date to payment date?") == []


def test_grain_multiplying_join_and_its_fix():
    fanned = ("SELECT rc.payer_name, sum(rc.paid_amount) FROM sem.remit_claims rc "
              "JOIN sem.remit_lines rl ON rl.remit_claim_id = rc.remit_claim_id "
              "WHERE rc.is_current_adjudication AND rl.procedure_code = '80053' GROUP BY 1")
    assert "grain_multiplying_join" in _codes(fanned)
    fixed = ("WITH lines AS (SELECT DISTINCT remit_claim_id FROM sem.remit_lines WHERE procedure_code = '80053') "
             "SELECT rc.payer_name, sum(rc.paid_amount) FROM sem.remit_claims rc "
             "JOIN lines ON lines.remit_claim_id = rc.remit_claim_id WHERE rc.is_current_adjudication GROUP BY 1")
    assert _codes(fixed) == []
    line_amount = fanned.replace("sum(rc.paid_amount)", "sum(rl.line_paid_amount)")
    assert _codes(line_amount) == []


def test_findings_say_what_to_change():
    finding = domain_lint("SELECT sum(paid_amount) FROM sem.remit_claims", "Total paid?")[0]
    assert "is_current_adjudication" in finding.message and finding.render().startswith(finding.code + ": ")


def test_filter_literals_for_the_mandatory_lookup_gate():
    sql = ("SELECT * FROM sem.remit_claims WHERE payer_name = 'Aetna' AND payer_class IN ('medicare', 'medicaid') "
           "AND carc_code = '45' AND payer_name ILIKE '%blue%'")
    assert set(filter_literals(sql)) == {("payer_name", "Aetna", False), ("payer_class", "medicare", False),
                                         ("payer_class", "medicaid", False), ("payer_name", "%blue%", True)}


def test_rate_parts_and_group_shares():
    assert [f.code for f in reconciliation_findings(
        "SELECT 1", "q", ["payer", "denied_claims", "remitted_claims", "denial_rate"],
        [("A", 2, 10, 0.2), ("B", 1, 4, 0.25)], connection=None)] == []
    wrong = reconciliation_findings("SELECT 1", "q", ["payer", "denied_claims", "remitted_claims", "denial_rate"],
                                    [("A", 2, 10, 0.5)], connection=None)
    assert [f.code for f in wrong] == ["rate_parts"]
    shares = ["payer", "share_of_total"]
    assert reconciliation_findings("SELECT 1", "q", shares, [("A", 60.0), ("B", 40.0)], connection=None) == []
    assert [f.code for f in reconciliation_findings("SELECT 1", "q", shares, [("A", 60.0), ("B", 60.0)],
                                                    connection=None)] == ["group_shares"]
    assert reconciliation_findings("SELECT 1 LIMIT 2", "q", shares, [("A", 60.0), ("B", 60.0)], connection=None) == []


def test_payment_balancing_on_the_snapshot(bench_snapshot):
    from bench.dataset import open_snapshot
    connection = open_snapshot(bench_snapshot)
    claims_paid = "SELECT payer_name, sum(paid_amount) FROM sem.remit_claims WHERE is_current_adjudication GROUP BY 1"
    found = reconciliation_findings(claims_paid, "What were our check totals by payer?", ["payer_name", "paid"],
                                    [("A", 1.0)], connection)
    assert [f.code for f in found] == ["payment_balancing"]
    check_total = "SELECT payer_name, sum(total_payment_amount) FROM sem.payments GROUP BY 1"
    found = reconciliation_findings(check_total, "How much was paid on claims by payer?", ["payer_name", "paid"],
                                    [("A", 1.0)], connection)
    assert [f.code for f in found] == ["payment_balancing"]


def test_no_finding_on_any_train_gold_query(bench_snapshot):
    """False-positive guard, TRAIN split only (validation and test are never read to tune the lint)."""
    from bench.dataset import open_snapshot
    from bench.eval import load_cases
    from bench.splits import select_cases
    connection = open_snapshot(bench_snapshot)
    flagged = []
    for case in select_cases(load_cases(snapshot_path=bench_snapshot), "train"):
        if not case.get("gold_sql") or case.get("expect_refusal"):
            continue
        cursor = connection.execute(case["gold_sql"])
        columns, rows = [item[0] for item in cursor.description], cursor.fetchall()
        findings = domain_lint(case["gold_sql"], case["question"]) + reconciliation_findings(
            case["gold_sql"], case["question"], columns, rows, connection)
        if findings:
            flagged.append((case["id"], [finding.code for finding in findings]))
    assert flagged == []
