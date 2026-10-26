"""Demo chart picks the headline measure (rate first, else the first measure), not the last column."""
from api.mapping import chart_for


def _col(key, label, typ):
    return {"key": key, "label": label, "type": typ}


def test_rate_beats_count():
    cols = [_col("payer_name", "Payer name", "string"), _col("denial_rate", "Denial rate", "percent"), _col("claims", "Claims", "integer")]
    rows = [["A", 0.2, 10], ["B", 0.1, 20]]
    ch = chart_for(cols, rows)
    assert ch["spec"]["encoding"]["y"]["field"] == "denial_rate"
    assert ch["title"] == "Denial rate by payer"


def test_first_measure_when_no_rate():
    cols = [_col("service_month", "Service month", "date"), _col("paid_amount", "Paid amount", "currency"), _col("claims", "Claims", "integer")]
    rows = [["2026-01-01", 1.0, 3], ["2026-02-01", 2.0, 4]]
    assert chart_for(cols, rows)["spec"]["encoding"]["y"]["field"] == "paid_amount"
